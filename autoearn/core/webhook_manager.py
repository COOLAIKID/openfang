"""
Webhook Manager — register endpoints, fire events, deliver payloads, and
track retries for the AutoEarn self-running organization.

Supports HMAC signatures (sha256/sha512), exponential-backoff retries,
per-webhook delivery logs, and health-check pings. All data stored in
SQLite via the shared database module. No external HTTP dependencies —
delivery uses urllib.request from the standard library only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WEBHOOK_EVENTS: List[str] = [
    "agent.started",
    "agent.completed",
    "agent.error",
    "revenue.recorded",
    "content.published",
    "product.sold",
    "campaign.sent",
    "qc.approved",
    "qc.rejected",
    "funnel.conversion",
    "course.enrolled",
    "email.bounced",
    "report.generated",
    "price.changed",
]

DELIVERY_STATUSES: List[str] = [
    "pending",
    "delivered",
    "failed",
    "retrying",
    "abandoned",
]

ALGORITHMS: List[str] = [
    "sha256",
    "sha512",
]

DEFAULT_RETRY_MAX = 5
DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_ALGORITHM = "sha256"
DEFAULT_PURGE_DAYS = 30

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_schema_ready = False


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


def _init_schema() -> None:
    conn = _db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS webhooks (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                name             TEXT    NOT NULL UNIQUE,
                url              TEXT    NOT NULL,
                secret           TEXT    NOT NULL DEFAULT '',
                events           TEXT    NOT NULL DEFAULT '[]',
                status           TEXT    NOT NULL DEFAULT 'active',
                retry_max        INTEGER NOT NULL DEFAULT 5,
                timeout_seconds  INTEGER NOT NULL DEFAULT 10,
                created_at       TEXT    NOT NULL,
                updated_at       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS webhook_deliveries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                webhook_id      INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
                event_type      TEXT    NOT NULL,
                payload         TEXT    NOT NULL DEFAULT '{}',
                status          TEXT    NOT NULL DEFAULT 'pending',
                attempts        INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                next_retry_at   TEXT,
                response_code   INTEGER,
                response_body   TEXT,
                error           TEXT,
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS webhook_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT    NOT NULL,
                source      TEXT    NOT NULL DEFAULT '',
                payload     TEXT    NOT NULL DEFAULT '{}',
                processed   INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS webhook_signatures (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                webhook_id  INTEGER NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
                algorithm   TEXT    NOT NULL DEFAULT 'sha256',
                secret_hash TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_wh_deliveries_webhook
                ON webhook_deliveries(webhook_id, status);
            CREATE INDEX IF NOT EXISTS idx_wh_deliveries_retry
                ON webhook_deliveries(status, next_retry_at);
            CREATE INDEX IF NOT EXISTS idx_wh_events_processed
                ON webhook_events(processed, created_at);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Webhook:
    id: int
    name: str
    url: str
    secret: str
    events: List[str]
    status: str
    retry_max: int
    timeout_seconds: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "secret": "***" if self.secret else "",
            "events": self.events,
            "status": self.status,
            "retry_max": self.retry_max,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
        }


@dataclass
class WebhookDelivery:
    id: int
    webhook_id: int
    event_type: str
    payload: Dict[str, Any]
    status: str
    attempts: int
    response_code: Optional[int]
    error: Optional[str]
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "webhook_id": self.webhook_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "status": self.status,
            "attempts": self.attempts,
            "response_code": self.response_code,
            "error": self.error,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.utcnow().isoformat()


def _row_to_webhook(row: sqlite3.Row) -> Webhook:
    events_raw = row["events"]
    try:
        events = json.loads(events_raw) if events_raw else []
    except (json.JSONDecodeError, TypeError):
        events = []
    return Webhook(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        secret=row["secret"] or "",
        events=events,
        status=row["status"],
        retry_max=row["retry_max"],
        timeout_seconds=row["timeout_seconds"],
        created_at=row["created_at"],
    )


def _row_to_delivery(row: sqlite3.Row) -> WebhookDelivery:
    payload_raw = row["payload"]
    try:
        payload = json.loads(payload_raw) if payload_raw else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return WebhookDelivery(
        id=row["id"],
        webhook_id=row["webhook_id"],
        event_type=row["event_type"],
        payload=payload,
        status=row["status"],
        attempts=row["attempts"],
        response_code=row["response_code"],
        error=row["error"],
        created_at=row["created_at"],
    )


def _next_retry_at(attempts: int) -> str:
    """Exponential back-off: 2^attempts minutes from now."""
    delay_minutes = 2 ** attempts
    next_dt = datetime.utcnow() + timedelta(minutes=delay_minutes)
    return next_dt.isoformat()


def _compute_hmac(secret: str, payload_bytes: bytes, algorithm: str = DEFAULT_ALGORITHM) -> str:
    """Return hex-encoded HMAC digest using the requested algorithm."""
    if algorithm not in ALGORITHMS:
        algorithm = DEFAULT_ALGORITHM
    digest_mod = hashlib.sha256 if algorithm == "sha256" else hashlib.sha512
    mac = hmac.new(secret.encode("utf-8"), payload_bytes, digest_mod)
    return mac.hexdigest()


def _build_headers(webhook: Webhook, payload_bytes: bytes) -> Dict[str, str]:
    """Build HTTP headers for a webhook delivery including HMAC signature."""
    delivery_id = str(uuid.uuid4())
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "X-AutoEarn-Delivery": delivery_id,
        "X-AutoEarn-Event": "webhook.delivery",
        "User-Agent": "AutoEarn-Webhook/1.0",
    }
    if webhook.secret:
        sig = _compute_hmac(webhook.secret, payload_bytes, DEFAULT_ALGORITHM)
        headers["X-AutoEarn-Signature"] = f"{DEFAULT_ALGORITHM}={sig}"
    return headers


# ---------------------------------------------------------------------------
# Core CRUD — webhooks
# ---------------------------------------------------------------------------

def register_webhook(
    name: str,
    url: str,
    events: List[str],
    secret: str = "",
    retry_max: int = DEFAULT_RETRY_MAX,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Webhook:
    """Register a new webhook endpoint and return the created Webhook object."""
    _ensure()
    # Validate events
    unknown = [e for e in events if e not in WEBHOOK_EVENTS]
    if unknown:
        raise ValueError(f"Unknown event types: {unknown}. Valid: {WEBHOOK_EVENTS}")
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO webhooks
               (name, url, secret, events, status, retry_max, timeout_seconds, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, url, secret, json.dumps(events), "active", retry_max, timeout, now, now),
        )
        conn.commit()
        wh_id = cur.lastrowid
        # If a secret is provided, store a signature record
        if secret:
            secret_hash = hashlib.sha256(secret.encode()).hexdigest()
            conn.execute(
                """INSERT INTO webhook_signatures (webhook_id, algorithm, secret_hash, created_at)
                   VALUES (?,?,?,?)""",
                (wh_id, DEFAULT_ALGORITHM, secret_hash, now),
            )
            conn.commit()
        row = conn.execute("SELECT * FROM webhooks WHERE id=?", (wh_id,)).fetchone()
        return _row_to_webhook(row)
    finally:
        conn.close()


def get_webhook(name_or_id: Any) -> Optional[Webhook]:
    """Retrieve a webhook by name (str) or id (int). Returns None if not found."""
    _ensure()
    conn = _db()
    try:
        if isinstance(name_or_id, int) or (isinstance(name_or_id, str) and name_or_id.isdigit()):
            row = conn.execute(
                "SELECT * FROM webhooks WHERE id=?", (int(name_or_id),)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM webhooks WHERE name=?", (name_or_id,)
            ).fetchone()
        return _row_to_webhook(row) if row else None
    finally:
        conn.close()


def list_webhooks(status: Optional[str] = None) -> List[Webhook]:
    """List all webhooks, optionally filtered by status."""
    _ensure()
    conn = _db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE status=? ORDER BY id ASC", (status,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM webhooks ORDER BY id ASC"
            ).fetchall()
        return [_row_to_webhook(r) for r in rows]
    finally:
        conn.close()


def update_webhook(webhook_id: int, **fields: Any) -> bool:
    """Update arbitrary fields on a webhook row. Returns True on success."""
    _ensure()
    allowed = {"name", "url", "secret", "events", "status", "retry_max", "timeout_seconds"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    if "events" in updates and isinstance(updates["events"], list):
        updates["events"] = json.dumps(updates["events"])
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [webhook_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE webhooks SET {set_clause} WHERE id=?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def enable_webhook(webhook_id: int) -> bool:
    """Set a webhook status to 'active'."""
    return update_webhook(webhook_id, status="active")


def disable_webhook(webhook_id: int) -> bool:
    """Set a webhook status to 'inactive'."""
    return update_webhook(webhook_id, status="inactive")


def delete_webhook(webhook_id: int) -> bool:
    """Permanently delete a webhook and all its delivery records."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM webhooks WHERE id=?", (webhook_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Event firing
# ---------------------------------------------------------------------------

def fire_event(
    event_type: str,
    payload: Dict[str, Any],
    source: str = "system",
) -> int:
    """
    Record a system event in webhook_events and return the new event_id.

    The event is not delivered here — call process_event(event_id) to fan out
    to registered webhooks, or let the scheduler handle it.
    """
    _ensure()
    if event_type not in WEBHOOK_EVENTS:
        raise ValueError(
            f"Unknown event type '{event_type}'. Valid: {WEBHOOK_EVENTS}"
        )
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO webhook_events (event_type, source, payload, processed, created_at)
               VALUES (?,?,?,0,?)""",
            (event_type, source, json.dumps(payload), now),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# HTTP delivery
# ---------------------------------------------------------------------------

def deliver_webhook(
    webhook_id: int,
    event_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Deliver a payload to a single webhook endpoint.

    Creates a delivery record, performs the HTTP POST, updates the record with
    the result, and schedules a retry if needed.

    Returns a dict: {ok, status_code, attempts, delivery_id}.
    """
    _ensure()
    wh = get_webhook(webhook_id)
    if not wh:
        return {"ok": False, "error": f"Webhook {webhook_id} not found", "delivery_id": None}

    now = _now()
    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = _build_headers(wh, payload_bytes)
    # The event name belongs in the header, not the delivery id
    headers["X-AutoEarn-Event"] = event_type

    # Create the pending delivery record
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO webhook_deliveries
               (webhook_id, event_type, payload, status, attempts, created_at)
               VALUES (?,?,?,?,0,?)""",
            (webhook_id, event_type, json.dumps(payload), "pending", now),
        )
        conn.commit()
        delivery_id = int(cur.lastrowid or 0)
    finally:
        conn.close()

    # Perform HTTP POST
    status_code: Optional[int] = None
    response_body: str = ""
    error_msg: str = ""
    success = False

    try:
        req = urllib.request.Request(
            wh.url,
            data=payload_bytes,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=wh.timeout_seconds) as resp:
            status_code = resp.status
            try:
                response_body = resp.read(4096).decode("utf-8", errors="replace")
            except Exception:
                response_body = ""
            success = 200 <= status_code < 300
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        try:
            response_body = exc.read(4096).decode("utf-8", errors="replace")
        except Exception:
            response_body = ""
        error_msg = f"HTTP {exc.code}: {exc.reason}"
    except urllib.error.URLError as exc:
        error_msg = f"URLError: {exc.reason}"
    except TimeoutError:
        error_msg = "Request timed out"
    except Exception as exc:  # noqa: BLE001
        error_msg = str(exc)

    # Determine final delivery status
    attempts = 1
    if success:
        delivery_status = "delivered"
        next_retry: Optional[str] = None
    else:
        # Determine whether to retry or abandon
        conn = _db()
        try:
            row = conn.execute(
                "SELECT attempts FROM webhook_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
        finally:
            conn.close()

        if attempts < wh.retry_max:
            delivery_status = "retrying"
            next_retry = _next_retry_at(attempts)
        else:
            delivery_status = "abandoned"
            next_retry = None

    # Update the delivery record
    conn = _db()
    try:
        conn.execute(
            """UPDATE webhook_deliveries
               SET status=?, attempts=?, last_attempt_at=?, next_retry_at=?,
                   response_code=?, response_body=?, error=?
               WHERE id=?""",
            (
                delivery_status,
                attempts,
                _now(),
                next_retry,
                status_code,
                response_body[:4096] if response_body else None,
                error_msg or None,
                delivery_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": success,
        "status_code": status_code,
        "attempts": attempts,
        "delivery_id": delivery_id,
        "delivery_status": delivery_status,
        "error": error_msg or None,
    }


# ---------------------------------------------------------------------------
# Event processing
# ---------------------------------------------------------------------------

def process_event(event_id: int) -> Dict[str, Any]:
    """
    Find all active webhooks subscribed to this event's type and deliver to each.

    Marks the event as processed in webhook_events. Returns a summary dict.
    """
    _ensure()
    conn = _db()
    try:
        ev_row = conn.execute(
            "SELECT * FROM webhook_events WHERE id=?", (event_id,)
        ).fetchone()
    finally:
        conn.close()

    if not ev_row:
        return {"ok": False, "error": f"Event {event_id} not found"}

    event_type = ev_row["event_type"]
    try:
        payload = json.loads(ev_row["payload"])
    except (json.JSONDecodeError, TypeError):
        payload = {}

    # Find active webhooks subscribed to this event type
    webhooks = list_webhooks(status="active")
    matching = [wh for wh in webhooks if event_type in wh.events]

    results = []
    succeeded = 0
    failed = 0
    for wh in matching:
        result = deliver_webhook(wh.id, event_type, payload)
        results.append({"webhook_id": wh.id, "webhook_name": wh.name, **result})
        if result.get("ok"):
            succeeded += 1
        else:
            failed += 1

    # Mark event as processed
    conn = _db()
    try:
        conn.execute(
            "UPDATE webhook_events SET processed=1 WHERE id=?", (event_id,)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "event_id": event_id,
        "event_type": event_type,
        "webhooks_notified": len(matching),
        "succeeded": succeeded,
        "failed": failed,
        "deliveries": results,
    }


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------

def retry_failed_deliveries() -> Dict[str, Any]:
    """
    Find all deliveries in 'retrying' status where next_retry_at <= now.

    Attempts re-delivery for each and returns a summary.
    """
    _ensure()
    now = _now()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT * FROM webhook_deliveries
               WHERE status='retrying' AND next_retry_at <= ?
               ORDER BY next_retry_at ASC""",
            (now,),
        ).fetchall()
    finally:
        conn.close()

    attempted = 0
    succeeded = 0
    failed = 0

    for row in rows:
        webhook_id = row["webhook_id"]
        event_type = row["event_type"]
        delivery_id = row["id"]
        prev_attempts = row["attempts"]

        try:
            payload = json.loads(row["payload"])
        except (json.JSONDecodeError, TypeError):
            payload = {}

        wh = get_webhook(webhook_id)
        if not wh or wh.status != "active":
            # Abandon delivery — webhook gone or disabled
            conn = _db()
            try:
                conn.execute(
                    "UPDATE webhook_deliveries SET status='abandoned' WHERE id=?",
                    (delivery_id,),
                )
                conn.commit()
            finally:
                conn.close()
            failed += 1
            attempted += 1
            continue

        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = _build_headers(wh, payload_bytes)
        headers["X-AutoEarn-Event"] = event_type

        status_code: Optional[int] = None
        response_body: str = ""
        error_msg: str = ""
        success = False

        try:
            req = urllib.request.Request(
                wh.url,
                data=payload_bytes,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=wh.timeout_seconds) as resp:
                status_code = resp.status
                try:
                    response_body = resp.read(4096).decode("utf-8", errors="replace")
                except Exception:
                    response_body = ""
                success = 200 <= status_code < 300
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            try:
                response_body = exc.read(4096).decode("utf-8", errors="replace")
            except Exception:
                response_body = ""
            error_msg = f"HTTP {exc.code}: {exc.reason}"
        except urllib.error.URLError as exc:
            error_msg = f"URLError: {exc.reason}"
        except TimeoutError:
            error_msg = "Request timed out"
        except Exception as exc:  # noqa: BLE001
            error_msg = str(exc)

        new_attempts = prev_attempts + 1
        if success:
            new_status = "delivered"
            next_retry = None
            succeeded += 1
        elif new_attempts >= wh.retry_max:
            new_status = "abandoned"
            next_retry = None
            failed += 1
        else:
            new_status = "retrying"
            next_retry = _next_retry_at(new_attempts)
            failed += 1

        conn = _db()
        try:
            conn.execute(
                """UPDATE webhook_deliveries
                   SET status=?, attempts=?, last_attempt_at=?, next_retry_at=?,
                       response_code=?, response_body=?, error=?
                   WHERE id=?""",
                (
                    new_status,
                    new_attempts,
                    _now(),
                    next_retry,
                    status_code,
                    response_body[:4096] if response_body else None,
                    error_msg or None,
                    delivery_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        attempted += 1

    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
    }


# ---------------------------------------------------------------------------
# History & stats
# ---------------------------------------------------------------------------

def get_delivery_history(
    webhook_id: Optional[int] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return recent delivery records as dicts, optionally scoped to one webhook."""
    _ensure()
    conn = _db()
    try:
        if webhook_id is not None:
            rows = conn.execute(
                """SELECT * FROM webhook_deliveries
                   WHERE webhook_id=?
                   ORDER BY id DESC LIMIT ?""",
                (webhook_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM webhook_deliveries
                   ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (json.JSONDecodeError, TypeError):
                payload = {}
            result.append({
                "id": row["id"],
                "webhook_id": row["webhook_id"],
                "event_type": row["event_type"],
                "payload": payload,
                "status": row["status"],
                "attempts": row["attempts"],
                "last_attempt_at": row["last_attempt_at"],
                "next_retry_at": row["next_retry_at"],
                "response_code": row["response_code"],
                "response_body": row["response_body"],
                "error": row["error"],
                "created_at": row["created_at"],
            })
        return result
    finally:
        conn.close()


def delivery_stats(webhook_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Aggregate delivery statistics.

    Returns totals by status, success rate, and average response-code distribution.
    Optionally scoped to a single webhook.
    """
    _ensure()
    conn = _db()
    try:
        where = "WHERE webhook_id=?" if webhook_id is not None else ""
        params = (webhook_id,) if webhook_id is not None else ()

        total_row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM webhook_deliveries {where}", params
        ).fetchone()
        total = int(total_row["cnt"]) if total_row else 0

        status_rows = conn.execute(
            f"""SELECT status, COUNT(*) AS cnt
                FROM webhook_deliveries {where}
                GROUP BY status""",
            params,
        ).fetchall()
        by_status = {r["status"]: int(r["cnt"]) for r in status_rows}

        delivered = by_status.get("delivered", 0)
        success_rate = round(delivered / total * 100, 2) if total > 0 else 0.0

        code_rows = conn.execute(
            f"""SELECT response_code, COUNT(*) AS cnt
                FROM webhook_deliveries
                {where}
                AND response_code IS NOT NULL
                GROUP BY response_code
                ORDER BY cnt DESC""".replace(
                "AND", "WHERE" if not where else "AND"
            ),
            params,
        ).fetchall()
        by_code = {str(r["response_code"]): int(r["cnt"]) for r in code_rows}

        event_rows = conn.execute(
            f"""SELECT event_type, COUNT(*) AS cnt
                FROM webhook_deliveries {where}
                GROUP BY event_type
                ORDER BY cnt DESC""",
            params,
        ).fetchall()
        by_event = {r["event_type"]: int(r["cnt"]) for r in event_rows}

        return {
            "total": total,
            "by_status": by_status,
            "by_response_code": by_code,
            "by_event_type": by_event,
            "success_rate_pct": success_rate,
            "webhook_id": webhook_id,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def webhook_health_check(webhook_id: int) -> Dict[str, Any]:
    """
    Fire a test ping to the webhook URL and return the result.

    The ping payload includes a 'test': True marker and is NOT logged as a
    real delivery (it uses deliver_webhook which does create a delivery record,
    but with event_type 'webhook.ping').
    """
    _ensure()
    wh = get_webhook(webhook_id)
    if not wh:
        return {"ok": False, "error": f"Webhook {webhook_id} not found"}

    ping_payload = {
        "test": True,
        "webhook_id": webhook_id,
        "webhook_name": wh.name,
        "ping_at": _now(),
    }
    result = deliver_webhook(webhook_id, "webhook.ping", ping_payload)
    return {
        "webhook_id": webhook_id,
        "webhook_name": wh.name,
        "url": wh.url,
        "healthy": result.get("ok", False),
        "status_code": result.get("status_code"),
        "delivery_id": result.get("delivery_id"),
        "error": result.get("error"),
        "checked_at": _now(),
    }


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def purge_old_deliveries(days: int = DEFAULT_PURGE_DAYS) -> int:
    """
    Delete delivery records older than `days` days.

    Returns the number of rows deleted.
    """
    _ensure()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM webhook_deliveries WHERE created_at < ?", (cutoff,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def webhook_summary() -> Dict[str, Any]:
    """
    High-level overview: webhook counts, delivery stats, recent events.
    """
    _ensure()
    all_wh = list_webhooks()
    active_wh = [w for w in all_wh if w.status == "active"]

    stats = delivery_stats()

    conn = _db()
    try:
        recent_events = conn.execute(
            """SELECT event_type, source, processed, created_at
               FROM webhook_events
               ORDER BY id DESC LIMIT 10"""
        ).fetchall()
        recent_events_list = [
            {
                "event_type": r["event_type"],
                "source": r["source"],
                "processed": bool(r["processed"]),
                "created_at": r["created_at"],
            }
            for r in recent_events
        ]

        unprocessed_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM webhook_events WHERE processed=0"
        ).fetchone()["cnt"]

        retrying_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM webhook_deliveries WHERE status='retrying'"
        ).fetchone()["cnt"]
    finally:
        conn.close()

    return {
        "total_webhooks": len(all_wh),
        "active_webhooks": len(active_wh),
        "inactive_webhooks": len(all_wh) - len(active_wh),
        "delivery_stats": stats,
        "unprocessed_events": int(unprocessed_count),
        "retrying_deliveries": int(retrying_count),
        "recent_events": recent_events_list,
        "known_event_types": WEBHOOK_EVENTS,
        "generated_at": _now(),
    }


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

@tool("wh_register", "Register a new webhook endpoint to receive AutoEarn events")
def register_webhook_tool(
    name: str,
    url: str,
    events_csv: str,
    secret: str = "",
    retry_max: int = DEFAULT_RETRY_MAX,
) -> str:
    """
    Register a webhook.

    events_csv: comma-separated event types, e.g. 'agent.started,revenue.recorded'
    """
    try:
        events = [e.strip() for e in events_csv.split(",") if e.strip()]
        wh = register_webhook(
            name=name,
            url=url,
            events=events,
            secret=secret,
            retry_max=retry_max,
        )
        return json.dumps({"ok": True, "webhook": wh.to_dict()})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool("wh_list", "List all registered webhooks, optionally filtered by status")
def list_webhooks_tool(status: str = "") -> str:
    """
    List webhooks.

    status: optional filter — 'active', 'inactive', or '' for all
    """
    try:
        webhooks = list_webhooks(status=status or None)
        return json.dumps({
            "ok": True,
            "count": len(webhooks),
            "webhooks": [w.to_dict() for w in webhooks],
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool("wh_fire_event", "Fire a system event to be delivered to subscribed webhooks")
def fire_event_tool(event_type: str, payload_json: str = "{}") -> str:
    """
    Fire an event.

    event_type: must be one of WEBHOOK_EVENTS
    payload_json: JSON string with event data (optional)
    """
    try:
        try:
            payload = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            payload = {"raw": payload_json}

        event_id = fire_event(event_type=event_type, payload=payload, source="tool")
        result = process_event(event_id)
        return json.dumps({"ok": True, "event_id": event_id, **result})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool("wh_delivery_stats", "Get delivery statistics for all or a specific webhook")
def delivery_stats_tool(webhook_name: str = "") -> str:
    """
    Delivery statistics.

    webhook_name: optional — if given, scopes stats to that webhook
    """
    try:
        webhook_id: Optional[int] = None
        if webhook_name:
            wh = get_webhook(webhook_name)
            if not wh:
                return json.dumps({"ok": False, "error": f"Webhook '{webhook_name}' not found"})
            webhook_id = wh.id
        stats = delivery_stats(webhook_id=webhook_id)
        return json.dumps({"ok": True, **stats})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool("wh_retry_failed", "Retry all webhook deliveries that are due for retry")
def retry_failed_tool() -> str:
    """Retry failed/retrying webhook deliveries whose back-off window has elapsed."""
    try:
        result = retry_failed_deliveries()
        return json.dumps({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool("wh_summary", "High-level summary of webhook system health and activity")
def webhook_summary_tool() -> str:
    """Return a dashboard-style summary of the webhook subsystem."""
    try:
        summary = webhook_summary()
        return json.dumps({"ok": True, **summary})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool("wh_health_check", "Ping a webhook endpoint to verify it is reachable")
def webhook_health_check_tool(webhook_name: str) -> str:
    """
    Send a test ping to the named webhook.

    webhook_name: the registered name of the webhook to check
    """
    try:
        wh = get_webhook(webhook_name)
        if not wh:
            return json.dumps({"ok": False, "error": f"Webhook '{webhook_name}' not found"})
        result = webhook_health_check(wh.id)
        return json.dumps({"ok": True, **result})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})
