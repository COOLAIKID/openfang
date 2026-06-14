"""Tests for core/webhooks.py — signature verification and event routing."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated webhooks module with temp DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def wh(tmp_path):
    """Fresh webhooks module with isolated SQLite DB."""
    import core.webhooks as w
    w._db_conn = None
    db_path = tmp_path / "wh_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    w._init_schema(conn)
    # Also create metrics and notification tables to avoid import errors
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL, metric TEXT NOT NULL,
            value REAL NOT NULL, recorded_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL, level TEXT NOT NULL,
            message TEXT NOT NULL, channels TEXT NOT NULL DEFAULT '[]',
            sent_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_rate (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bucket TEXT NOT NULL UNIQUE, count INTEGER NOT NULL DEFAULT 0,
            window_start TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL, to_agent TEXT NOT NULL,
            subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
            message_type TEXT NOT NULL DEFAULT 'info',
            sent_at TEXT NOT NULL, read_at TEXT
        );
    """)
    conn.commit()
    w._db_conn = conn
    yield w
    w._db_conn = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stripe_sig(payload_bytes: bytes, secret: str) -> str:
    """Build a valid Stripe-Signature header."""
    ts = str(int(time.time()))
    signed_payload = ts.encode() + b"." + payload_bytes
    sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def _make_shopify_hmac(payload_bytes: bytes, secret: str) -> str:
    """Build a valid Shopify X-Shopify-Hmac-Sha256 header (base64)."""
    sig_bytes = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).digest()
    return base64.b64encode(sig_bytes).decode()


def _make_ls_sig(payload_bytes: bytes, secret: str) -> str:
    """Build a valid LemonSqueezy X-Signature header (hex)."""
    return hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# _verify_hmac_sha256
# ---------------------------------------------------------------------------

class TestVerifyHmacSha256:
    def test_valid_signature_returns_true(self, wh):
        payload = b"hello world"
        secret = "my_secret"
        sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        assert wh._verify_hmac_sha256(payload, secret, sig) is True

    def test_invalid_signature_returns_false(self, wh):
        payload = b"hello world"
        secret = "my_secret"
        assert wh._verify_hmac_sha256(payload, secret, "baddeadbeef") is False

    def test_sha256_prefix_handled(self, wh):
        payload = b"test payload"
        secret = "secret_key"
        sig_hex = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        sig_with_prefix = f"sha256={sig_hex}"
        assert wh._verify_hmac_sha256(payload, secret, sig_with_prefix) is True

    def test_empty_secret_returns_true_permissive(self, wh):
        # Permissive behavior when no secret configured
        assert wh._verify_hmac_sha256(b"any", "", "anysig") is True

    def test_tampered_payload_fails(self, wh):
        original = b"original payload"
        tampered = b"tampered payload"
        secret = "webhook_secret"
        sig = hmac.new(secret.encode(), original, hashlib.sha256).hexdigest()
        assert wh._verify_hmac_sha256(tampered, secret, sig) is False


# ---------------------------------------------------------------------------
# _verify_stripe_sig
# ---------------------------------------------------------------------------

class TestVerifyStripeSig:
    def test_valid_stripe_signature_returns_true(self, wh):
        payload = b'{"type":"payment_intent.succeeded","data":{}}'
        secret = "whsec_test"
        sig_header = _make_stripe_sig(payload, secret)
        assert wh._verify_stripe_sig(payload, sig_header, secret) is True

    def test_invalid_stripe_signature_returns_false(self, wh):
        payload = b'{"type":"charge.succeeded"}'
        secret = "whsec_real"
        fake_header = "t=9999999999,v1=deadbeefdeadbeef"
        assert wh._verify_stripe_sig(payload, fake_header, secret) is False

    def test_old_timestamp_rejected(self, wh):
        payload = b'{"type":"charge.succeeded"}'
        secret = "whsec_test"
        old_ts = str(int(time.time()) - 400)  # 400 seconds ago > 5min limit
        signed_payload = old_ts.encode() + b"." + payload
        sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        old_header = f"t={old_ts},v1={sig}"
        assert wh._verify_stripe_sig(payload, old_header, secret) is False

    def test_malformed_sig_header_returns_false(self, wh):
        payload = b'{"type":"test"}'
        assert wh._verify_stripe_sig(payload, "not_a_valid_header", "secret") is False

    def test_empty_sig_header_returns_false(self, wh):
        # With a secret set, empty header should fail
        payload = b'{"type":"test"}'
        result = wh._verify_stripe_sig(payload, "", "mysecret")
        # Empty v1 sig won't match expected
        assert result is False


# ---------------------------------------------------------------------------
# process_stripe_event
# ---------------------------------------------------------------------------

class TestProcessStripeEvent:
    def _stripe_payload(self, event_type: str, amount_cents: int = 4999,
                        currency: str = "usd") -> dict:
        return {
            "id": "evt_test_123",
            "type": event_type,
            "data": {
                "object": {
                    "amount": amount_cents,
                    "currency": currency,
                }
            }
        }

    def test_valid_charge_succeeded(self, wh):
        payload = self._stripe_payload("charge.succeeded", 4999)
        payload_str = json.dumps(payload)
        payload_bytes = payload_str.encode()
        secret = "whsec_test"
        sig = _make_stripe_sig(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            event = wh.process_stripe_event(payload_str, sig, secret)
        assert event is not None
        assert event.provider == "stripe"
        assert event.event_type == "charge.succeeded"
        assert abs(event.amount_usd - 49.99) < 0.01

    def test_invalid_sig_returns_none(self, wh):
        payload_str = json.dumps({"type": "charge.succeeded", "data": {"object": {}}})
        event = wh.process_stripe_event(payload_str, "t=1,v1=bad", "real_secret")
        assert event is None

    def test_payment_intent_succeeded_extracts_amount(self, wh):
        payload = self._stripe_payload("payment_intent.succeeded", 10000)
        payload_str = json.dumps(payload)
        payload_bytes = payload_str.encode()
        secret = "whsec_test"
        sig = _make_stripe_sig(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            event = wh.process_stripe_event(payload_str, sig, secret)
        assert event is not None
        assert abs(event.amount_usd - 100.0) < 0.01

    def test_checkout_session_completed(self, wh):
        payload = {
            "id": "evt_checkout",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "amount_total": 2999,
                    "currency": "usd",
                }
            }
        }
        payload_str = json.dumps(payload)
        payload_bytes = payload_str.encode()
        secret = "test"
        sig = _make_stripe_sig(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            event = wh.process_stripe_event(payload_str, sig, secret)
        assert event is not None
        assert abs(event.amount_usd - 29.99) < 0.01

    def test_invalid_json_returns_none(self, wh):
        event = wh.process_stripe_event("not valid json", "", "")
        assert event is None

    def test_non_usd_currency_no_amount(self, wh):
        payload = self._stripe_payload("charge.succeeded", 5000, currency="eur")
        payload_str = json.dumps(payload)
        payload_bytes = payload_str.encode()
        secret = "test"
        sig = _make_stripe_sig(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            event = wh.process_stripe_event(payload_str, sig, secret)
        assert event is not None
        assert event.amount_usd is None


# ---------------------------------------------------------------------------
# process_shopify_event
# ---------------------------------------------------------------------------

class TestProcessShopifyEvent:
    def _shopify_payload(self, status="paid", total="99.00") -> dict:
        return {
            "id": 123456,
            "financial_status": status,
            "total_price": total,
            "currency": "USD",
        }

    def test_valid_paid_order(self, wh):
        payload = self._shopify_payload("paid", "49.99")
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = "shopify_secret"
        hmac_header = _make_shopify_hmac(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            event = wh.process_shopify_event(payload, hmac_header, secret)
        assert event is not None
        assert event.provider == "shopify"
        assert abs(event.amount_usd - 49.99) < 0.01

    def test_invalid_hmac_returns_none(self, wh):
        payload = self._shopify_payload("paid", "99.00")
        result = wh.process_shopify_event(payload, "invalid_hmac", "real_secret")
        assert result is None

    def test_unpaid_order_no_revenue_route(self, wh):
        payload = self._shopify_payload("pending", "50.00")
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = "test"
        hmac_header = _make_shopify_hmac(payload_bytes, secret)
        route_calls = []
        with patch.object(wh, "route_event", side_effect=lambda *a, **kw: route_calls.append(a)):
            wh.process_shopify_event(payload, hmac_header, secret)
        # route_event should NOT be called for non-paid orders
        assert len(route_calls) == 0

    def test_event_stored_in_db(self, wh):
        payload = self._shopify_payload("paid", "25.00")
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = "test"
        hmac_header = _make_shopify_hmac(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            wh.process_shopify_event(payload, hmac_header, secret)
        events = wh.get_recent_events()
        assert any(e["provider"] == "shopify" for e in events)


# ---------------------------------------------------------------------------
# process_lemonsqueezy_event
# ---------------------------------------------------------------------------

class TestProcessLemonSqueezyEvent:
    def _ls_payload(self, event_name="order_created", total_cents=4999) -> dict:
        return {
            "meta": {"event_name": event_name},
            "data": {
                "id": "ls_order_456",
                "attributes": {
                    "total": total_cents,
                    "first_order_item": {"product_name": "AutoEarn Guide"},
                }
            }
        }

    def test_valid_order_created(self, wh):
        payload = self._ls_payload("order_created", 9999)
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = "ls_secret"
        sig = _make_ls_sig(payload_bytes, secret)
        with patch.object(wh, "route_event"):
            event = wh.process_lemonsqueezy_event(payload, sig, secret)
        assert event is not None
        assert event.provider == "lemonsqueezy"
        assert abs(event.amount_usd - 99.99) < 0.01

    def test_invalid_signature_returns_none(self, wh):
        payload = self._ls_payload("order_created", 5000)
        result = wh.process_lemonsqueezy_event(payload, "invalidsig", "real_secret")
        assert result is None

    def test_subscription_created_triggers_routing(self, wh):
        payload = self._ls_payload("subscription_created", 1999)
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = "test"
        sig = _make_ls_sig(payload_bytes, secret)
        route_calls = []
        with patch.object(wh, "route_event", side_effect=lambda *a, **kw: route_calls.append(a)):
            wh.process_lemonsqueezy_event(payload, sig, secret)
        assert len(route_calls) == 1

    def test_zero_amount_not_routed(self, wh):
        payload = self._ls_payload("order_refunded", 0)
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        secret = "test"
        sig = _make_ls_sig(payload_bytes, secret)
        route_calls = []
        with patch.object(wh, "route_event", side_effect=lambda *a, **kw: route_calls.append(a)):
            wh.process_lemonsqueezy_event(payload, sig, secret)
        assert len(route_calls) == 0


# ---------------------------------------------------------------------------
# process_gumroad_event
# ---------------------------------------------------------------------------

class TestProcessGumroadEvent:
    def test_basic_gumroad_sale(self, wh):
        payload = {
            "sale_id": "abc123",
            "product_name": "AutoEarn Guide",
            "price": "4999",
        }
        with patch.object(wh, "route_event"):
            event = wh.process_gumroad_event(payload)
        assert event is not None
        assert event.provider == "gumroad"
        assert abs(event.amount_usd - 49.99) < 0.01

    def test_gumroad_missing_price_defaults_zero(self, wh):
        payload = {"sale_id": "xyz", "product_name": "Test Product", "price": "invalid"}
        with patch.object(wh, "route_event"):
            event = wh.process_gumroad_event(payload)
        assert event is not None
        assert event.amount_usd == 0.0

    def test_gumroad_stores_event_in_db(self, wh):
        payload = {"sale_id": "g123", "product_name": "Product", "price": "999"}
        with patch.object(wh, "route_event"):
            wh.process_gumroad_event(payload)
        events = wh.get_recent_events()
        assert any(e["provider"] == "gumroad" for e in events)


# ---------------------------------------------------------------------------
# route_event
# ---------------------------------------------------------------------------

class TestRouteEvent:
    def test_route_event_does_not_raise(self, wh):
        # All internal imports may fail gracefully; should not raise
        with patch("core.monitoring.log_metric", MagicMock()):
            with patch("core.notifications.notify_revenue", MagicMock()):
                with patch("core.message_bus.send", MagicMock(return_value=1)):
                    wh.route_event("stripe", "charge.succeeded", {
                        "amount_usd": 49.99,
                        "source": "Stripe charge.succeeded"
                    })

    def test_route_event_with_no_amount(self, wh):
        # No amount_usd in data → should not crash
        with patch("core.monitoring.log_metric", MagicMock()):
            with patch("core.notifications.notify_revenue", MagicMock()):
                with patch("core.message_bus.send", MagicMock(return_value=1)):
                    wh.route_event("paypal", "payment", {})


# ---------------------------------------------------------------------------
# get_recent_events / mark_processed
# ---------------------------------------------------------------------------

class TestGetRecentEvents:
    def test_get_recent_events_empty(self, wh):
        events = wh.get_recent_events()
        assert events == []

    def test_get_recent_events_after_gumroad(self, wh):
        payload = {"sale_id": "s1", "product_name": "Prod", "price": "1000"}
        with patch.object(wh, "route_event"):
            wh.process_gumroad_event(payload)
        events = wh.get_recent_events()
        assert len(events) == 1
        assert events[0]["provider"] == "gumroad"

    def test_get_recent_events_limit(self, wh):
        for i in range(10):
            payload = {"sale_id": f"s{i}", "product_name": f"Prod{i}", "price": "100"}
            with patch.object(wh, "route_event"):
                wh.process_gumroad_event(payload)
        events = wh.get_recent_events(limit=5)
        assert len(events) == 5

    def test_mark_processed_returns_true(self, wh):
        payload = {"sale_id": "s1", "product_name": "Prod", "price": "100"}
        with patch.object(wh, "route_event"):
            event = wh.process_gumroad_event(payload)
        assert event is not None
        result = wh.mark_processed(event.id)
        assert result is True

    def test_mark_processed_nonexistent_returns_false(self, wh):
        result = wh.mark_processed(99999)
        assert result is False

    def test_recent_events_include_processed_flag(self, wh):
        payload = {"sale_id": "s1", "product_name": "P", "price": "500"}
        with patch.object(wh, "route_event"):
            event = wh.process_gumroad_event(payload)
        events = wh.get_recent_events()
        assert "processed" in events[0]

    def test_webhook_event_to_dict(self, wh):
        from core.webhooks import WebhookEvent
        ev = WebhookEvent(
            provider="stripe",
            event_type="charge.succeeded",
            payload={"foo": "bar"},
            amount_usd=29.99,
        )
        d = ev.to_dict()
        for key in ("provider", "event_type", "payload", "amount_usd", "processed"):
            assert key in d
