"""Tests for core/notifications.py — multi-channel notification system."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated notifications module with temp DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def notif(tmp_path):
    """Fresh notifications module with isolated SQLite DB."""
    import core.notifications as n
    n._db_conn = None
    db_path = tmp_path / "notif_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    n._init_schema(conn)
    n._db_conn = conn
    yield n
    n._db_conn = None


@pytest.fixture()
def notif_fresh(notif):
    """Reset rate-limit table so every test starts clean."""
    notif._db_conn.execute("DELETE FROM notification_rate")
    notif._db_conn.commit()
    return notif


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SuccessChannel:
    """A mock channel that always succeeds."""
    name = "mock_success"

    def is_configured(self):
        return True

    def send(self, message, level="info", agent="system"):
        return True


class _FailChannel:
    """A mock channel that always fails."""
    name = "mock_fail"

    def is_configured(self):
        return True

    def send(self, message, level="info", agent="system"):
        return False


class _UnconfiguredChannel:
    """A mock channel that is not configured."""
    name = "mock_unconfigured"

    def is_configured(self):
        return False

    def send(self, message, level="info", agent="system"):
        raise AssertionError("Should not be called")


# ---------------------------------------------------------------------------
# notify (core function)
# ---------------------------------------------------------------------------

class TestNotify:
    def test_notify_with_no_channels_returns_empty(self, notif_fresh):
        # Clear all channels temporarily
        original_channels = dict(notif_fresh._channels)
        notif_fresh._channels.clear()
        try:
            result = notif_fresh.notify("Test message", level="info", agent="test_agent")
            assert result == {}
        finally:
            notif_fresh._channels.update(original_channels)

    def test_notify_custom_channel_returns_result(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("mock_success", ch)
        result = notif_fresh.notify(
            "Test notification",
            level="info",
            channels=["mock_success"],
            agent="test_agent",
        )
        assert result.get("mock_success") is True

    def test_notify_failed_channel_returns_false(self, notif_fresh):
        ch = _FailChannel()
        notif_fresh.register_channel("mock_fail", ch)
        result = notif_fresh.notify(
            "Test notification",
            level="info",
            channels=["mock_fail"],
            agent="test_agent",
        )
        assert result.get("mock_fail") is False

    def test_notify_unconfigured_channel_skipped(self, notif_fresh):
        ch = _UnconfiguredChannel()
        notif_fresh.register_channel("mock_unconfigured", ch)
        result = notif_fresh.notify(
            "Test",
            channels=["mock_unconfigured"],
            agent="test_agent",
        )
        # Unconfigured channels are skipped (not in result)
        assert "mock_unconfigured" not in result

    def test_notify_unknown_channel_skipped(self, notif_fresh):
        result = notif_fresh.notify(
            "Test",
            channels=["nonexistent_channel"],
            agent="test_agent",
        )
        assert "nonexistent_channel" not in result

    def test_notify_logs_to_db(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("mock_success", ch)
        notif_fresh.notify(
            "Log this message",
            channels=["mock_success"],
            agent="logger_agent",
        )
        history = notif_fresh.notification_history()
        assert any(
            h["agent"] == "logger_agent" and "Log this message" in h["message"]
            for h in history
        )

    def test_notify_returns_dict(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        result = notif_fresh.notify("msg", channels=["success_ch"], agent="a")
        assert isinstance(result, dict)

    def test_notify_channel_exception_handled(self, notif_fresh):
        class _ExplodingChannel:
            name = "exploder"

            def is_configured(self):
                return True

            def send(self, message, level="info", agent="system"):
                raise RuntimeError("Boom!")

        notif_fresh.register_channel("exploder", _ExplodingChannel())
        # Should not raise; result should be False
        result = notif_fresh.notify("Test", channels=["exploder"], agent="test")
        assert result.get("exploder") is False


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimiting:
    def test_first_notification_allowed(self, notif_fresh):
        allowed = notif_fresh._check_rate_limit("test_agent", "info")
        assert allowed is True

    def test_notifications_below_limit_allowed(self, notif_fresh):
        for _ in range(notif_fresh.RATE_LIMIT_MAX - 1):
            result = notif_fresh._check_rate_limit("rate_agent", "info")
        assert result is True

    def test_notification_at_limit_rejected(self, notif_fresh):
        for _ in range(notif_fresh.RATE_LIMIT_MAX):
            notif_fresh._check_rate_limit("limit_agent", "warning")
        # Next one should be rejected
        allowed = notif_fresh._check_rate_limit("limit_agent", "warning")
        assert allowed is False

    def test_rate_limit_per_agent_level_bucket(self, notif_fresh):
        # Fill up bucket for "info" level
        for _ in range(notif_fresh.RATE_LIMIT_MAX):
            notif_fresh._check_rate_limit("bucket_agent", "info")
        # "error" level should still be allowed
        allowed = notif_fresh._check_rate_limit("bucket_agent", "error")
        assert allowed is True

    def test_rate_limit_different_agents_independent(self, notif_fresh):
        for _ in range(notif_fresh.RATE_LIMIT_MAX):
            notif_fresh._check_rate_limit("agent_a", "info")
        # Agent B should still be allowed
        assert notif_fresh._check_rate_limit("agent_b", "info") is True

    def test_rate_limited_notify_returns_empty_dict(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        # Exhaust rate limit
        for _ in range(notif_fresh.RATE_LIMIT_MAX + 1):
            notif_fresh._check_rate_limit("rl_agent", "info")
        # Now notify should be blocked
        result = notif_fresh.notify("blocked", channels=["success_ch"], agent="rl_agent")
        assert result == {}


# ---------------------------------------------------------------------------
# notify_error
# ---------------------------------------------------------------------------

class TestNotifyError:
    def test_notify_error_logged_with_error_level(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_error("cfo", "ValueError: bad amount", context="Line 42")
        history = notif_fresh.notification_history()
        error_entries = [h for h in history if h["level"] == "error"]
        assert error_entries

    def test_notify_error_message_contains_agent(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_error("writer_agent", "Import error")
        history = notif_fresh.notification_history()
        assert any("writer_agent" in h["message"] for h in history)

    def test_notify_error_message_contains_error_text(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_error("ceo", "PermissionError: access denied")
        history = notif_fresh.notification_history()
        assert any("PermissionError" in h["message"] for h in history)

    def test_notify_error_returns_dict(self, notif_fresh):
        result = notif_fresh.notify_error("agent1", "Test error")
        assert isinstance(result, dict)

    def test_notify_error_with_context(self, notif_fresh):
        result = notif_fresh.notify_error("agent1", "Error msg", context="Some context")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# notify_revenue
# ---------------------------------------------------------------------------

class TestNotifyRevenue:
    def test_notify_revenue_level_is_revenue(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_revenue("affiliate", 99.99, "Gumroad sale")
        history = notif_fresh.notification_history()
        revenue_entries = [h for h in history if h["level"] == "revenue"]
        assert revenue_entries

    def test_notify_revenue_message_has_amount(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_revenue("closer", 49.99, "Product sale")
        history = notif_fresh.notification_history()
        assert any("49.99" in h["message"] for h in history)

    def test_notify_revenue_message_has_source(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_revenue("cfo", 100.0, "Affiliate commission XYZ")
        history = notif_fresh.notification_history()
        assert any("Affiliate commission XYZ" in h["message"] for h in history)

    def test_notify_revenue_returns_dict(self, notif_fresh):
        result = notif_fresh.notify_revenue("agent1", 25.0, "Source")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# notify_milestone
# ---------------------------------------------------------------------------

class TestNotifyMilestone:
    def test_notify_milestone_level_is_milestone(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_milestone("First $1000 revenue reached!")
        history = notif_fresh.notification_history()
        milestone_entries = [h for h in history if h["level"] == "milestone"]
        assert milestone_entries

    def test_notify_milestone_message_contains_text(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_milestone("100 customers milestone!")
        history = notif_fresh.notification_history()
        assert any("100 customers" in h["message"] for h in history)

    def test_notify_milestone_agent_is_system(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify_milestone("Test milestone")
        history = notif_fresh.notification_history()
        assert any(h["agent"] == "system" for h in history)

    def test_notify_milestone_returns_dict(self, notif_fresh):
        result = notif_fresh.notify_milestone("Reached 50 users")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# notification_history
# ---------------------------------------------------------------------------

class TestNotificationHistory:
    def test_history_empty_initially(self, notif_fresh):
        history = notif_fresh.notification_history()
        assert history == []

    def test_history_grows_with_notifications(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        for i in range(3):
            notif_fresh.notify(f"Message {i}", channels=["success_ch"], agent="hist_agent")
        history = notif_fresh.notification_history()
        assert len(history) == 3

    def test_history_ordered_newest_first(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        for i in range(3):
            notif_fresh.notify(f"Message {i}", channels=["success_ch"], agent="order_agent")
        history = notif_fresh.notification_history()
        # sent_at should be descending
        times = [h["sent_at"] for h in history]
        assert times == sorted(times, reverse=True)

    def test_history_respects_limit(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        for i in range(20):
            notif_fresh.notify(f"Msg {i}", channels=["success_ch"], agent="limit_agent")
        history = notif_fresh.notification_history(limit=5)
        assert len(history) == 5

    def test_history_contains_expected_keys(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify("Test", channels=["success_ch"], agent="key_agent")
        history = notif_fresh.notification_history()
        h = history[0]
        for key in ("id", "agent", "level", "message", "channels", "sent_at"):
            assert key in h

    def test_history_channels_is_list(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("success_ch", ch)
        notif_fresh.notify("Test", channels=["success_ch"], agent="ch_agent")
        history = notif_fresh.notification_history()
        assert isinstance(history[0]["channels"], list)


# ---------------------------------------------------------------------------
# register_channel / get_channels
# ---------------------------------------------------------------------------

class TestChannelRegistry:
    def test_register_new_channel(self, notif_fresh):
        ch = _SuccessChannel()
        notif_fresh.register_channel("custom_ch", ch)
        channels = notif_fresh.get_channels()
        assert "custom_ch" in channels

    def test_get_channels_returns_dict(self, notif_fresh):
        channels = notif_fresh.get_channels()
        assert isinstance(channels, dict)

    def test_get_channels_returns_copy(self, notif_fresh):
        channels = notif_fresh.get_channels()
        channels["injected"] = _SuccessChannel()
        # The internal registry should not be affected
        original = notif_fresh.get_channels()
        assert "injected" not in original

    def test_register_overwrites_existing(self, notif_fresh):
        ch1 = _SuccessChannel()
        ch2 = _FailChannel()
        notif_fresh.register_channel("overwrite_ch", ch1)
        notif_fresh.register_channel("overwrite_ch", ch2)
        channels = notif_fresh.get_channels()
        assert channels["overwrite_ch"] is ch2


# ---------------------------------------------------------------------------
# Specific channel: SlackChannel
# ---------------------------------------------------------------------------

class TestSlackChannel:
    def test_slack_not_configured_without_env(self, notif_fresh):
        from core.notifications import SlackChannel
        ch = SlackChannel()
        # Without SLACK_WEBHOOK_URL env var, should not be configured
        with patch("core.notifications._cfg", return_value=""):
            assert ch.is_configured() is False

    def test_slack_send_posts_json(self, notif_fresh):
        from core.notifications import SlackChannel, _post_json
        ch = SlackChannel()
        called_with = {}

        def _mock_post_json(url, payload, name):
            called_with["url"] = url
            called_with["payload"] = payload
            return True

        with patch("core.notifications._cfg", side_effect=lambda k, d="": "https://hooks.slack.com/test" if k == "SLACK_WEBHOOK_URL" else d):
            with patch("core.notifications._post_json", side_effect=_mock_post_json):
                result = ch.send("Test message", level="info", agent="test")
        assert result is True
        assert "text" in called_with.get("payload", {})


# ---------------------------------------------------------------------------
# _post_json HTTP helper
# ---------------------------------------------------------------------------

class TestPostJson:
    def test_post_json_returns_true_on_200(self, notif_fresh):
        from core.notifications import _post_json
        import urllib.request

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch.object(urllib.request, "urlopen", return_value=mock_resp):
            result = _post_json("https://example.com/hook", {"text": "hi"}, "TestCh")
        assert result is True

    def test_post_json_returns_false_on_network_error(self, notif_fresh):
        from core.notifications import _post_json
        import urllib.request

        with patch.object(urllib.request, "urlopen", side_effect=Exception("Network error")):
            result = _post_json("https://example.com/hook", {"text": "hi"}, "TestCh")
        assert result is False
