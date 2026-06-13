"""
core/webhooks.py — Incoming webhook event processor for AutoEarn.

Verifies signatures, parses payloads from major payment providers
(Stripe, Gumroad, PayPal, Shopify, LemonSqueezy), logs events to SQLite,
and routes revenue events to the appropriate internal agents.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    with _lock:
        if _db_conn is not None:
            return _db_conn
        try:
            from core.database import DB_PATH
        except ImportError:
            DB_PATH = "/tmp/autoearn.db"
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _init_schema(conn)
        _db_conn = conn
    return _db_conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS webhook_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            provider    TEXT    NOT NULL,
            event_type  TEXT    NOT NULL,
            payload     TEXT    NOT NULL DEFAULT '{}',
            received_at TEXT    NOT NULL,
            processed   INTEGER NOT NULL DEFAULT 0,
            processed_at TEXT,
            amount_usd  REAL,
            notes       TEXT    DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_wh_provider ON webhook_events (provider, received_at DESC);
        CREATE INDEX IF NOT EXISTS idx_wh_processed ON webhook_events (processed, received_at DESC);
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class WebhookEvent:
    """Represents a single incoming webhook event."""
    provider: str
    event_type: str
    payload: dict[str, Any]
    received_at: str = field(default_factory=_now)
    processed: bool = False
    id: Optional[int] = None
    amount_usd: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "event_type": self.event_type,
            "payload": self.payload,
            "received_at": self.received_at,
            "processed": self.processed,
            "amount_usd": self.amount_usd,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


def _store_event(event: WebhookEvent) -> int:
    """Persist an event to webhook_events and return its id."""
    db = _get_db()
    payload_str = json.dumps(event.payload)
    with _lock:
        cursor = db.execute(
            """
            INSERT INTO webhook_events
                (provider, event_type, payload, received_at, processed, amount_usd, notes)
            VALUES (?,?,?,?,?,?,?)
            """,
            (
                event.provider,
                event.event_type,
                payload_str,
                event.received_at,
                1 if event.processed else 0,
                event.amount_usd,
                event.notes,
            ),
        )
        db.commit()
    return cursor.lastrowid


def mark_processed(event_id: int) -> bool:
    """Mark a webhook event as processed. Returns True if updated."""
    db = _get_db()
    with _lock:
        cursor = db.execute(
            "UPDATE webhook_events SET processed=1, processed_at=? WHERE id=?",
            (_now(), event_id),
        )
        db.commit()
    return cursor.rowcount > 0


def get_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """
    Return the most recent webhook events.

    Parameters
    ----------
    limit: Maximum number of records to return (newest first).
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT id, provider, event_type, payload, received_at,
               processed, processed_at, amount_usd, notes
        FROM webhook_events
        ORDER BY received_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        result.append({
            "id": row["id"],
            "provider": row["provider"],
            "event_type": row["event_type"],
            "payload": payload,
            "received_at": row["received_at"],
            "processed": bool(row["processed"]),
            "processed_at": row["processed_at"],
            "amount_usd": row["amount_usd"],
            "notes": row["notes"],
        })
    return result


# ---------------------------------------------------------------------------
# Signature verification helpers
# ---------------------------------------------------------------------------


def _verify_hmac_sha256(payload_bytes: bytes, secret: str, provided_sig: str) -> bool:
    """
    Verify an HMAC-SHA256 signature.

    Handles both raw hex signatures and 'sha256=<hex>' prefixed signatures.
    """
    if not secret:
        logger.warning("webhooks: HMAC verification skipped — no secret provided")
        return True  # Permissive when no secret configured
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    expected = hmac.new(key, payload_bytes, hashlib.sha256).hexdigest()
    sig = provided_sig
    if sig.startswith("sha256="):
        sig = sig[7:]
    return hmac.compare_digest(expected, sig)


def _verify_stripe_sig(payload_bytes: bytes, sig_header: str, secret: str) -> bool:
    """
    Verify a Stripe webhook signature (v1 scheme).

    Stripe sends: 'Stripe-Signature: t=<ts>,v1=<sig>'
    """
    try:
        parts = {p.split("=", 1)[0]: p.split("=", 1)[1]
                 for p in sig_header.split(",") if "=" in p}
        timestamp = parts.get("t", "")
        v1_sig = parts.get("v1", "")
        signed_payload = f"{timestamp}.".encode() + payload_bytes
        key = secret.encode("utf-8")
        expected = hmac.new(key, signed_payload, hashlib.sha256).hexdigest()
        # Replay attack check: reject if older than 5 minutes
        try:
            ts = int(timestamp)
            if abs(time.time() - ts) > 300:
                logger.warning("webhooks: Stripe timestamp too old (%s)", timestamp)
                return False
        except ValueError:
            pass
        return hmac.compare_digest(expected, v1_sig)
    except Exception as exc:
        logger.warning("webhooks: Stripe sig verification error: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Revenue routing
# ---------------------------------------------------------------------------


def route_event(provider: str, event_type: str, data: dict[str, Any]) -> None:
    """
    Log revenue and send a notification message to the relevant internal agent.

    Revenue events go to the 'cfo' and 'closer' agents.
    """
    amount = data.get("amount_usd")
    source = data.get("source", f"{provider}/{event_type}")

    # Log revenue via monitoring if amount is present
    if amount is not None:
        try:
            from core.monitoring import log_metric  # type: ignore
            log_metric("cfo", "revenue_usd", float(amount))
        except Exception as exc:
            logger.debug("route_event: could not log metric: %s", exc)

    # Send notification
    try:
        from core.notifications import notify_revenue  # type: ignore
        notify_revenue("webhook", float(amount or 0), source)
    except Exception as exc:
        logger.debug("route_event: could not send notification: %s", exc)

    # Message the CFO agent
    try:
        from core.message_bus import send  # type: ignore
        body = (
            f"Payment received via {provider}\n"
            f"Event type : {event_type}\n"
            f"Amount     : ${amount or 'N/A'}\n"
            f"Source     : {source}\n"
            f"Raw data   : {json.dumps(data, indent=2)[:500]}"
        )
        send(
            to="cfo",
            subject=f"Revenue event: {provider}/{event_type}",
            body=body,
            message_type="revenue",
            from_agent="webhook_processor",
        )
    except Exception as exc:
        logger.debug("route_event: could not message cfo: %s", exc)


# ---------------------------------------------------------------------------
# Provider-specific processors
# ---------------------------------------------------------------------------


def process_stripe_event(
    payload_str: str,
    sig_header: str,
    secret: str,
) -> Optional[WebhookEvent]:
    """
    Verify a Stripe webhook signature and parse the event.

    Parameters
    ----------
    payload_str: Raw request body as a string.
    sig_header:  Value of the 'Stripe-Signature' header.
    secret:      Stripe webhook signing secret (whsec_...).

    Returns
    -------
    WebhookEvent on success, None on signature failure or parse error.
    """
    payload_bytes = payload_str.encode("utf-8") if isinstance(payload_str, str) else payload_str

    if sig_header and secret:
        if not _verify_stripe_sig(payload_bytes, sig_header, secret):
            logger.error("webhooks: Stripe signature verification FAILED")
            return None

    try:
        payload = json.loads(payload_str)
    except json.JSONDecodeError as exc:
        logger.error("webhooks: could not parse Stripe payload: %s", exc)
        return None

    event_type = payload.get("type", "unknown")
    amount_usd: Optional[float] = None

    # Extract amount from common Stripe event types
    data_obj = payload.get("data", {}).get("object", {})
    if event_type in ("charge.succeeded", "payment_intent.succeeded"):
        raw_amount = data_obj.get("amount", 0)
        currency = data_obj.get("currency", "usd").lower()
        if currency == "usd":
            amount_usd = raw_amount / 100.0
    elif event_type == "checkout.session.completed":
        raw_amount = data_obj.get("amount_total", 0)
        currency = data_obj.get("currency", "usd").lower()
        if currency == "usd":
            amount_usd = raw_amount / 100.0

    event = WebhookEvent(
        provider="stripe",
        event_type=event_type,
        payload=payload,
        amount_usd=amount_usd,
        notes=f"stripe_event_id={payload.get('id', '')}",
    )
    event_id = _store_event(event)
    event.id = event_id

    if amount_usd is not None:
        route_event("stripe", event_type, {
            "amount_usd": amount_usd,
            "source": f"Stripe {event_type}",
            "event_id": payload.get("id"),
        })

    logger.info("stripe event processed: type=%s amount=$%s id=%s",
                event_type, amount_usd, event_id)
    return event


def process_gumroad_event(payload: dict[str, Any]) -> Optional[WebhookEvent]:
    """
    Parse a Gumroad seller ping webhook.

    Gumroad sends a form-encoded POST; the caller should parse it into a dict.

    Parameters
    ----------
    payload: Dict of Gumroad ping fields.

    Returns
    -------
    WebhookEvent with the sale amount populated.
    """
    try:
        sale_id = payload.get("sale_id", "")
        product_name = payload.get("product_name", "")
        price_str = payload.get("price", "0")
        # Gumroad price is in cents
        try:
            amount_usd = float(price_str) / 100.0
        except (ValueError, TypeError):
            amount_usd = 0.0

        event = WebhookEvent(
            provider="gumroad",
            event_type="sale",
            payload=payload,
            amount_usd=amount_usd,
            notes=f"sale_id={sale_id} product={product_name}",
        )
        event_id = _store_event(event)
        event.id = event_id

        route_event("gumroad", "sale", {
            "amount_usd": amount_usd,
            "source": f"Gumroad sale: {product_name}",
            "sale_id": sale_id,
        })
        logger.info("gumroad event processed: sale_id=%s amount=$%.2f id=%s",
                    sale_id, amount_usd, event_id)
        return event
    except Exception as exc:
        logger.error("webhooks: process_gumroad_event error: %s", exc)
        return None


def process_paypal_event(payload: dict[str, Any]) -> Optional[WebhookEvent]:
    """
    Parse a PayPal IPN or REST webhook payload.

    Handles both IPN (form-encoded, passed as dict) and REST webhook JSON.

    Parameters
    ----------
    payload: Dict of PayPal event fields.
    """
    try:
        # Detect REST webhook vs IPN
        event_type = payload.get("event_type", payload.get("txn_type", "payment"))
        amount_usd: Optional[float] = None

        # REST: payment_capture.completed
        resource = payload.get("resource", {})
        if resource:
            amount_obj = resource.get("amount", resource.get("seller_receivable_breakdown", {}))
            gross = amount_obj.get("gross_amount", amount_obj.get("value"))
            if gross:
                try:
                    amount_usd = float(gross.get("value", gross) if isinstance(gross, dict) else gross)
                except (ValueError, TypeError):
                    pass
        # IPN: mc_gross
        if amount_usd is None and "mc_gross" in payload:
            try:
                amount_usd = float(payload["mc_gross"])
            except (ValueError, TypeError):
                pass

        event = WebhookEvent(
            provider="paypal",
            event_type=event_type,
            payload=payload,
            amount_usd=amount_usd,
            notes=f"paypal_txn={payload.get('txn_id', resource.get('id', ''))}",
        )
        event_id = _store_event(event)
        event.id = event_id

        if amount_usd and amount_usd > 0:
            route_event("paypal", event_type, {
                "amount_usd": amount_usd,
                "source": f"PayPal {event_type}",
            })
        logger.info("paypal event processed: type=%s amount=$%s id=%s",
                    event_type, amount_usd, event_id)
        return event
    except Exception as exc:
        logger.error("webhooks: process_paypal_event error: %s", exc)
        return None


def process_shopify_event(
    payload: dict[str, Any],
    hmac_header: str,
    secret: str,
) -> Optional[WebhookEvent]:
    """
    Verify Shopify webhook HMAC and parse an order/payment event.

    Parameters
    ----------
    payload:     Parsed JSON payload dict.
    hmac_header: Value of the 'X-Shopify-Hmac-Sha256' header (base64).
    secret:      Shopify webhook secret.
    """
    import base64

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    if hmac_header and secret:
        key = secret.encode("utf-8")
        expected_bytes = hmac.new(key, payload_bytes, hashlib.sha256).digest()
        expected_b64 = base64.b64encode(expected_bytes).decode()
        if not hmac.compare_digest(expected_b64, hmac_header):
            logger.error("webhooks: Shopify HMAC verification FAILED")
            return None

    try:
        order_id = payload.get("id", "")
        financial_status = payload.get("financial_status", "")
        total_price = payload.get("total_price", "0.00")
        try:
            amount_usd = float(total_price)
        except (ValueError, TypeError):
            amount_usd = 0.0

        event_type = f"order/{financial_status}" if financial_status else "order"
        event = WebhookEvent(
            provider="shopify",
            event_type=event_type,
            payload=payload,
            amount_usd=amount_usd if amount_usd > 0 else None,
            notes=f"shopify_order_id={order_id} status={financial_status}",
        )
        event_id = _store_event(event)
        event.id = event_id

        if financial_status == "paid" and amount_usd > 0:
            route_event("shopify", event_type, {
                "amount_usd": amount_usd,
                "source": f"Shopify order #{order_id}",
            })
        logger.info("shopify event processed: order=%s status=%s amount=$%.2f id=%s",
                    order_id, financial_status, amount_usd, event_id)
        return event
    except Exception as exc:
        logger.error("webhooks: process_shopify_event error: %s", exc)
        return None


def process_lemonsqueezy_event(
    payload: dict[str, Any],
    sig: str,
    secret: str,
) -> Optional[WebhookEvent]:
    """
    Verify a LemonSqueezy webhook signature and parse the event.

    LemonSqueezy uses HMAC-SHA256 with the raw JSON body.

    Parameters
    ----------
    payload: Parsed JSON payload.
    sig:     Value of the 'X-Signature' header.
    secret:  Webhook signing secret from LemonSqueezy dashboard.
    """
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    if sig and secret:
        if not _verify_hmac_sha256(payload_bytes, secret, sig):
            logger.error("webhooks: LemonSqueezy signature verification FAILED")
            return None

    try:
        meta = payload.get("meta", {})
        event_name = meta.get("event_name", "unknown")
        data_obj = payload.get("data", {})
        attrs = data_obj.get("attributes", {})

        # Extract amount: LemonSqueezy provides total in cents
        total = attrs.get("total", attrs.get("subtotal", 0))
        try:
            amount_usd = float(total) / 100.0
        except (ValueError, TypeError):
            amount_usd = 0.0

        order_id = data_obj.get("id", "")
        product_name = attrs.get("first_order_item", {}).get("product_name", "")

        event = WebhookEvent(
            provider="lemonsqueezy",
            event_type=event_name,
            payload=payload,
            amount_usd=amount_usd if amount_usd > 0 else None,
            notes=f"ls_order={order_id} product={product_name}",
        )
        event_id = _store_event(event)
        event.id = event_id

        if event_name in ("order_created", "subscription_created") and amount_usd > 0:
            route_event("lemonsqueezy", event_name, {
                "amount_usd": amount_usd,
                "source": f"LemonSqueezy: {product_name or event_name}",
                "order_id": order_id,
            })
        logger.info("lemonsqueezy event processed: %s amount=$%.2f id=%s",
                    event_name, amount_usd, event_id)
        return event
    except Exception as exc:
        logger.error("webhooks: process_lemonsqueezy_event error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gumroad_payload = {
        "sale_id": "abc123",
        "product_name": "AutoEarn Guide",
        "price": "4999",  # $49.99 in cents
        "email": "buyer@example.com",
    }
    evt = process_gumroad_event(gumroad_payload)
    if evt:
        print("Gumroad event:", evt.to_dict())

    recent = get_recent_events(limit=5)
    print(f"Recent events ({len(recent)}):", [e["provider"] for e in recent])
