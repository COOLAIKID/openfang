"""
Tests for autoearn/core/newsletter.py

Isolation: we redirect the sqlite database to a temp file by patching
autoearn.core.newsletter._conn and autoearn.core.config.cfg so the
module never touches the real autoearn.db.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Isolation — redirect DB before the module is imported / used
# ---------------------------------------------------------------------------

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_nl_{uuid.uuid4().hex[:8]}.db")


def _fake_cfg(key: str, fallback=None):
    """Replace cfg() so newsletter always uses our temp DB."""
    if key == "newsletter.db_path":
        return _DB_PATH
    return fallback


# Import module (may already be cached if test_integration.py ran first).
# Unconditionally override the module-level cfg binding so our tests always
# use _DB_PATH regardless of import order.
import autoearn.core.newsletter as nl  # noqa: E402

nl.cfg = _fake_cfg


# ---------------------------------------------------------------------------
# Autouse fixture – drop all tables and reset the connection between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_db(monkeypatch):
    """Wipe all nl_* tables and force _db() to recreate the schema."""
    # Pin nl.cfg for this test so it survives any monkeypatch restoration.
    monkeypatch.setattr(nl, "cfg", _fake_cfg)

    # Close any open connection so we start fresh
    if nl._conn is not None:
        try:
            nl._conn.close()
        except Exception:
            pass
        nl._conn = None

    # Drop every non-system table that was created
    conn = sqlite3.connect(_DB_PATH)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    for (t,) in tables:
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.close()

    yield

    # Cleanup: close connection again after the test
    if nl._conn is not None:
        try:
            nl._conn.close()
        except Exception:
            pass
        nl._conn = None


# ===========================================================================
# 1. TestSubscriberManagement
# ===========================================================================

class TestSubscriberManagement:
    """Basic subscribe / get / list / count lifecycle."""

    def test_subscribe_new_with_double_optin(self):
        result = nl.subscribe("alice@example.com", double_optin=True)
        assert result["ok"] is True
        assert result["action"] == "subscribed"
        assert result["status"] == "pending"
        assert result["requires_confirmation"] is True
        assert "token" in result

    def test_subscribe_new_without_double_optin(self):
        result = nl.subscribe("bob@example.com", double_optin=False)
        assert result["ok"] is True
        assert result["status"] == "confirmed"

    def test_subscribe_invalid_email(self):
        result = nl.subscribe("not-an-email")
        assert result["ok"] is False
        assert "invalid email" in result["error"]

    def test_subscribe_invalid_email_no_tld(self):
        result = nl.subscribe("user@nodot")
        assert result["ok"] is False

    def test_subscribe_email_normalised_to_lowercase(self):
        result = nl.subscribe("Alice@Example.COM", double_optin=False)
        assert result["ok"] is True
        sub = nl.get_subscriber("alice@example.com")
        assert sub is not None

    def test_subscribe_returns_token_for_pending(self):
        result = nl.subscribe("carol@example.com", double_optin=True)
        assert len(result["token"]) == 32

    def test_subscribe_sets_names(self):
        nl.subscribe("dave@example.com", first_name="Dave", last_name="Smith", double_optin=False)
        sub = nl.get_subscriber("dave@example.com")
        assert sub["first_name"] == "Dave"
        assert sub["last_name"] == "Smith"

    def test_subscribe_sets_source_and_tags(self):
        nl.subscribe("eve@example.com", source="blog", tags=["vip", "early"],
                     double_optin=False)
        sub = nl.get_subscriber("eve@example.com")
        assert sub["source"] == "blog"
        assert "vip" in sub["tags"]
        assert "early" in sub["tags"]

    def test_subscribe_sets_custom_fields(self):
        nl.subscribe("frank@example.com", custom_fields={"plan": "pro"},
                     double_optin=False)
        sub = nl.get_subscriber("frank@example.com")
        assert sub["custom_fields"]["plan"] == "pro"

    def test_subscribe_duplicate_updates_existing(self):
        nl.subscribe("dup@example.com", double_optin=False)
        result = nl.subscribe("dup@example.com", first_name="Updated", double_optin=False)
        assert result["ok"] is True
        assert result["action"] == "updated"

    def test_subscribe_previously_unsubscribed_blocked(self):
        nl.subscribe("gone@example.com", double_optin=False)
        nl.unsubscribe("gone@example.com")
        result = nl.subscribe("gone@example.com")
        assert result["ok"] is False
        assert "unsubscribed" in result["error"]

    def test_get_subscriber_returns_none_for_missing(self):
        assert nl.get_subscriber("nobody@example.com") is None

    def test_list_subscribers_default_confirmed_only(self):
        nl.subscribe("p@example.com", double_optin=True)   # pending
        nl.subscribe("c@example.com", double_optin=False)  # confirmed
        subs = nl.list_subscribers()
        emails = [s["email"] for s in subs]
        assert "c@example.com" in emails
        assert "p@example.com" not in emails

    def test_list_subscribers_pending_filter(self):
        nl.subscribe("p2@example.com", double_optin=True)
        subs = nl.list_subscribers(status="pending")
        assert any(s["email"] == "p2@example.com" for s in subs)

    def test_list_subscribers_tag_filter(self):
        nl.subscribe("tagged@example.com", tags=["newsletter"], double_optin=False)
        nl.subscribe("plain@example.com", double_optin=False)
        subs = nl.list_subscribers(tag="newsletter")
        emails = [s["email"] for s in subs]
        assert "tagged@example.com" in emails
        assert "plain@example.com" not in emails

    def test_subscriber_count(self):
        nl.subscribe("a1@example.com", double_optin=False)
        nl.subscribe("a2@example.com", double_optin=False)
        assert nl.subscriber_count() >= 2

    def test_subscriber_count_by_status(self):
        nl.subscribe("pending_one@example.com", double_optin=True)
        count = nl.subscriber_count(status="pending")
        assert count >= 1

    def test_subscriber_count_by_list(self):
        nl.subscribe("list1@example.com", list_name="vip", double_optin=False)
        nl.subscribe("list2@example.com", list_name="free", double_optin=False)
        assert nl.subscriber_count(list_name="vip") == 1
        assert nl.subscriber_count(list_name="free") == 1


# ===========================================================================
# 2. TestConfirmAndUnsubscribe
# ===========================================================================

class TestConfirmAndUnsubscribe:
    """Double-optin confirmation and unsubscribe flows."""

    def test_confirm_subscriber_valid_token(self):
        result = nl.subscribe("confirm_me@example.com", double_optin=True)
        token = result["token"]
        conf = nl.confirm_subscriber(token)
        assert conf["ok"] is True
        assert conf["action"] == "confirmed"

    def test_confirm_subscriber_already_confirmed(self):
        result = nl.subscribe("already@example.com", double_optin=True)
        token = result["token"]
        nl.confirm_subscriber(token)
        conf2 = nl.confirm_subscriber(token)
        assert conf2["ok"] is True
        assert conf2["action"] == "already_confirmed"

    def test_confirm_subscriber_invalid_token(self):
        result = nl.confirm_subscriber("badtoken1234")
        assert result["ok"] is False
        assert "invalid token" in result["error"]

    def test_confirm_sets_status_to_confirmed(self):
        result = nl.subscribe("will_confirm@example.com", double_optin=True)
        nl.confirm_subscriber(result["token"])
        sub = nl.get_subscriber("will_confirm@example.com")
        assert sub["status"] == "confirmed"

    def test_unsubscribe_existing(self):
        nl.subscribe("unsub@example.com", double_optin=False)
        result = nl.unsubscribe("unsub@example.com")
        assert result["ok"] is True
        assert result["action"] == "unsubscribed"

    def test_unsubscribe_sets_status(self):
        nl.subscribe("unsub2@example.com", double_optin=False)
        nl.unsubscribe("unsub2@example.com")
        sub = nl.get_subscriber("unsub2@example.com")
        assert sub["status"] == "unsubscribed"

    def test_unsubscribe_nonexistent(self):
        result = nl.unsubscribe("ghost@example.com")
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_unsubscribe_sets_timestamp(self):
        nl.subscribe("timestamped@example.com", double_optin=False)
        before = time.time()
        nl.unsubscribe("timestamped@example.com")
        after = time.time()
        sub = nl.get_subscriber("timestamped@example.com")
        assert sub["unsubscribed_at"] is not None
        assert before <= sub["unsubscribed_at"] <= after


# ===========================================================================
# 3. TestBounceHandling
# ===========================================================================

class TestBounceHandling:
    """record_bounce for hard and soft bounces."""

    def test_hard_bounce_marks_status_bounced(self):
        nl.subscribe("hard@example.com", double_optin=False)
        result = nl.record_bounce("hard@example.com", bounce_type="hard")
        assert result["ok"] is True
        sub = nl.get_subscriber("hard@example.com")
        assert sub["status"] == "bounced"

    def test_soft_bounce_keeps_status_confirmed(self):
        nl.subscribe("soft@example.com", double_optin=False)
        result = nl.record_bounce("soft@example.com", bounce_type="soft")
        assert result["ok"] is True
        sub = nl.get_subscriber("soft@example.com")
        assert sub["status"] == "confirmed"

    def test_bounce_nonexistent_subscriber(self):
        result = nl.record_bounce("nobody@example.com")
        assert result["ok"] is False

    def test_bounce_sets_bounced_at(self):
        nl.subscribe("bounce_time@example.com", double_optin=False)
        before = time.time()
        nl.record_bounce("bounce_time@example.com", bounce_type="hard")
        after = time.time()
        sub = nl.get_subscriber("bounce_time@example.com")
        assert sub["bounced_at"] is not None
        assert before <= sub["bounced_at"] <= after

    def test_bounce_returns_bounce_type_in_result(self):
        nl.subscribe("bt@example.com", double_optin=False)
        result = nl.record_bounce("bt@example.com", bounce_type="hard")
        assert result["bounce_type"] == "hard"


# ===========================================================================
# 4. TestTagManagement
# ===========================================================================

class TestTagManagement:
    """add_tag and remove_tag behaviour."""

    def test_add_tag_to_subscriber(self):
        nl.subscribe("tag1@example.com", double_optin=False)
        ok = nl.add_tag("tag1@example.com", "premium")
        assert ok is True
        sub = nl.get_subscriber("tag1@example.com")
        assert "premium" in sub["tags"]

    def test_add_tag_idempotent(self):
        nl.subscribe("tag2@example.com", double_optin=False)
        nl.add_tag("tag2@example.com", "sale")
        nl.add_tag("tag2@example.com", "sale")
        sub = nl.get_subscriber("tag2@example.com")
        assert sub["tags"].count("sale") == 1

    def test_add_tag_to_missing_subscriber(self):
        ok = nl.add_tag("missing@example.com", "x")
        assert ok is False

    def test_remove_tag_from_subscriber(self):
        nl.subscribe("tag3@example.com", tags=["old"], double_optin=False)
        nl.remove_tag("tag3@example.com", "old")
        sub = nl.get_subscriber("tag3@example.com")
        assert "old" not in sub["tags"]

    def test_remove_tag_not_present_is_noop(self):
        nl.subscribe("tag4@example.com", double_optin=False)
        ok = nl.remove_tag("tag4@example.com", "nonexistent")
        assert ok is True  # subscriber found, tag just wasn't there

    def test_remove_tag_from_missing_subscriber(self):
        ok = nl.remove_tag("nobody@example.com", "tag")
        assert ok is False

    def test_multiple_tags_persisted(self):
        nl.subscribe("multitag@example.com", double_optin=False)
        nl.add_tag("multitag@example.com", "a")
        nl.add_tag("multitag@example.com", "b")
        nl.add_tag("multitag@example.com", "c")
        sub = nl.get_subscriber("multitag@example.com")
        assert set(sub["tags"]) == {"a", "b", "c"}


# ===========================================================================
# 5. TestListManagement
# ===========================================================================

class TestListManagement:
    """create_list and get_list_stats."""

    def test_create_list_basic(self):
        result = nl.create_list("weekly", description="Weekly digest")
        assert result["ok"] is True
        assert result["name"] == "weekly"

    def test_create_list_double_optin_flag(self):
        result = nl.create_list("optin_list", double_optin=True)
        assert result["double_optin"] is True

    def test_create_list_upsert_on_duplicate(self):
        nl.create_list("updates", description="Old description")
        result = nl.create_list("updates", description="New description")
        assert result["ok"] is True

    def test_get_list_stats_returns_counts(self):
        nl.create_list("mylist")
        nl.subscribe("s1@example.com", list_name="mylist", double_optin=False)
        nl.subscribe("s2@example.com", list_name="mylist", double_optin=True)
        stats = nl.get_list_stats("mylist")
        assert stats["confirmed_count"] == 1
        assert stats["pending_count"] == 1
        assert stats["total"] >= 2

    def test_get_list_stats_missing_list(self):
        stats = nl.get_list_stats("does_not_exist")
        assert "error" in stats

    def test_get_list_stats_bounced_count(self):
        nl.create_list("bouncelist")
        nl.subscribe("b1@example.com", list_name="bouncelist", double_optin=False)
        nl.record_bounce("b1@example.com", bounce_type="hard")
        stats = nl.get_list_stats("bouncelist")
        assert stats["bounced_count"] == 1

    def test_get_list_stats_unsubscribed_count(self):
        nl.create_list("unsub_list")
        nl.subscribe("u1@example.com", list_name="unsub_list", double_optin=False)
        nl.unsubscribe("u1@example.com")
        stats = nl.get_list_stats("unsub_list")
        assert stats["unsubscribed_count"] == 1

    def test_create_list_stores_from_fields(self):
        nl.create_list(
            "branded",
            from_name="Acme",
            from_email="news@acme.com",
            reply_to="help@acme.com",
        )
        stats = nl.get_list_stats("branded")
        assert stats["from_name"] == "Acme"
        assert stats["from_email"] == "news@acme.com"
        assert stats["reply_to"] == "help@acme.com"


# ===========================================================================
# 6. TestCampaignManagement
# ===========================================================================

class TestCampaignManagement:
    """create_campaign, get_campaign, list_campaigns, schedule, mark sent."""

    def test_create_campaign_returns_int_id(self):
        cid = nl.create_campaign("Welcome", "Welcome aboard!")
        assert isinstance(cid, int)
        assert cid > 0

    def test_get_campaign_returns_dict(self):
        cid = nl.create_campaign("Test Campaign", "Subject")
        camp = nl.get_campaign(cid)
        assert camp is not None
        assert camp["name"] == "Test Campaign"
        assert camp["subject"] == "Subject"

    def test_get_campaign_missing_returns_none(self):
        assert nl.get_campaign(999999) is None

    def test_campaign_default_status_is_draft(self):
        cid = nl.create_campaign("Draft", "A draft")
        camp = nl.get_campaign(cid)
        assert camp["status"] == "draft"

    def test_campaign_stores_html_and_text(self):
        cid = nl.create_campaign(
            "Rich", "Rich email",
            html_body="<b>hello</b>",
            text_body="hello",
        )
        camp = nl.get_campaign(cid)
        assert camp["html_body"] == "<b>hello</b>"
        assert camp["text_body"] == "hello"

    def test_campaign_stores_preview_text(self):
        cid = nl.create_campaign("Preview", "Sub", preview_text="Sneak peek")
        camp = nl.get_campaign(cid)
        assert camp["preview_text"] == "Sneak peek"

    def test_campaign_stores_tags_filter(self):
        cid = nl.create_campaign("Segmented", "Sub", tags_filter=["vip", "paid"])
        camp = nl.get_campaign(cid)
        assert "vip" in camp["tags_filter"]

    def test_schedule_campaign(self):
        cid = nl.create_campaign("Schedule Me", "Scheduled subject")
        send_at = time.time() + 3600
        ok = nl.schedule_campaign(cid, send_at)
        assert ok is True
        camp = nl.get_campaign(cid)
        assert camp["status"] == "scheduled"
        assert camp["scheduled_at"] == send_at

    def test_schedule_campaign_only_from_draft(self):
        cid = nl.create_campaign("Already Sent", "Sub")
        nl.mark_campaign_sent(cid, recipients=10)
        ok = nl.schedule_campaign(cid, time.time() + 3600)
        # Cannot reschedule a sent campaign — status stays 'sent'
        assert ok is False

    def test_mark_campaign_sent(self):
        cid = nl.create_campaign("Sent Camp", "Final subject")
        nl.mark_campaign_sent(cid, recipients=42)
        camp = nl.get_campaign(cid)
        assert camp["status"] == "sent"
        assert camp["recipients"] == 42
        assert camp["sent_at"] is not None

    def test_list_campaigns_all(self):
        nl.create_campaign("C1", "Sub1")
        nl.create_campaign("C2", "Sub2")
        camps = nl.list_campaigns()
        assert len(camps) >= 2

    def test_list_campaigns_by_status(self):
        cid = nl.create_campaign("Draft Only", "Sub")
        camps = nl.list_campaigns(status="draft")
        assert any(c["id"] == cid for c in camps)

    def test_list_campaigns_limit(self):
        for i in range(5):
            nl.create_campaign(f"Camp{i}", "Sub")
        camps = nl.list_campaigns(limit=3)
        assert len(camps) <= 3

    def test_due_campaigns_empty_when_nothing_scheduled(self):
        assert nl.due_campaigns() == []

    def test_due_campaigns_returns_past_scheduled(self):
        cid = nl.create_campaign("DueCamp", "Due sub")
        nl.schedule_campaign(cid, time.time() - 10)  # 10 seconds ago
        due = nl.due_campaigns()
        assert any(c["id"] == cid for c in due)

    def test_due_campaigns_ignores_future(self):
        cid = nl.create_campaign("FutureCamp", "Future sub")
        nl.schedule_campaign(cid, time.time() + 9999)
        due = nl.due_campaigns()
        assert not any(c["id"] == cid for c in due)


# ===========================================================================
# 7. TestCampaignOpenAndClickTracking
# ===========================================================================

class TestCampaignOpenAndClickTracking:
    """record_campaign_open, record_campaign_click, record_campaign_revenue."""

    def _make_confirmed_sub(self, email: str) -> int:
        nl.subscribe(email, double_optin=False)
        sub = nl.get_subscriber(email)
        return sub["id"]

    def test_record_open_increments_opens(self):
        cid = nl.create_campaign("OpenCamp", "Open sub")
        sub_id = self._make_confirmed_sub("opener@example.com")
        nl.record_campaign_open(cid, sub_id, unique=True)
        camp = nl.get_campaign(cid)
        assert camp["opens"] == 1
        assert camp["unique_opens"] == 1

    def test_record_open_non_unique_increments_opens_only(self):
        cid = nl.create_campaign("OpenCamp2", "Sub")
        sub_id = self._make_confirmed_sub("opener2@example.com")
        nl.record_campaign_open(cid, sub_id, unique=True)
        nl.record_campaign_open(cid, sub_id, unique=False)
        camp = nl.get_campaign(cid)
        assert camp["opens"] == 2
        assert camp["unique_opens"] == 1

    def test_record_open_increments_subscriber_total_opens(self):
        cid = nl.create_campaign("SubOpen", "Sub")
        sub_id = self._make_confirmed_sub("subopener@example.com")
        nl.record_campaign_open(cid, sub_id, unique=True)
        sub = nl.get_subscriber("subopener@example.com")
        assert sub["total_opens"] == 1

    def test_record_click_increments_clicks(self):
        cid = nl.create_campaign("ClickCamp", "Click sub")
        sub_id = self._make_confirmed_sub("clicker@example.com")
        nl.record_campaign_click(cid, sub_id, url="https://example.com", unique=True)
        camp = nl.get_campaign(cid)
        assert camp["clicks"] == 1
        assert camp["unique_clicks"] == 1

    def test_record_click_non_unique(self):
        cid = nl.create_campaign("ClickCamp2", "Click sub")
        sub_id = self._make_confirmed_sub("clicker2@example.com")
        nl.record_campaign_click(cid, sub_id, url="https://a.com", unique=True)
        nl.record_campaign_click(cid, sub_id, url="https://a.com", unique=False)
        camp = nl.get_campaign(cid)
        assert camp["clicks"] == 2
        assert camp["unique_clicks"] == 1

    def test_record_click_increments_subscriber_total_clicks(self):
        cid = nl.create_campaign("SubClick", "Sub")
        sub_id = self._make_confirmed_sub("subclicker@example.com")
        nl.record_campaign_click(cid, sub_id, url="https://b.com", unique=True)
        sub = nl.get_subscriber("subclicker@example.com")
        assert sub["total_clicks"] == 1

    def test_record_campaign_revenue(self):
        cid = nl.create_campaign("RevCamp", "Rev sub")
        nl.record_campaign_revenue(cid, 49.99)
        camp = nl.get_campaign(cid)
        assert abs(camp["revenue"] - 49.99) < 0.001

    def test_record_campaign_revenue_accumulates(self):
        cid = nl.create_campaign("RevCamp2", "Rev sub")
        nl.record_campaign_revenue(cid, 10.0)
        nl.record_campaign_revenue(cid, 20.0)
        camp = nl.get_campaign(cid)
        assert abs(camp["revenue"] - 30.0) < 0.001

    def test_campaign_open_rate_property(self):
        cid = nl.create_campaign("RateCamp", "Sub")
        nl.mark_campaign_sent(cid, recipients=100)
        # Manually set delivered and unique_opens via direct DB manipulation
        nl._db().execute(
            "UPDATE nl_campaigns SET delivered=100, unique_opens=25 WHERE id=?", (cid,)
        )
        nl._db().commit()
        camp = nl.get_campaign(cid)
        # open_rate_pct = 25/100 * 100 = 25.0
        assert camp["open_rate_pct"] == 25.0

    def test_campaign_click_rate_property(self):
        cid = nl.create_campaign("ClickRateCamp", "Sub")
        nl._db().execute(
            "UPDATE nl_campaigns SET delivered=100, unique_opens=50, unique_clicks=10 WHERE id=?",
            (cid,),
        )
        nl._db().commit()
        camp = nl.get_campaign(cid)
        # click_rate_pct = 10/50 * 100 = 20.0
        assert camp["click_rate_pct"] == 20.0

    def test_campaign_bounce_rate_zero_when_no_recipients(self):
        cid = nl.create_campaign("NoBounce", "Sub")
        camp = nl.get_campaign(cid)
        assert camp["bounce_rate_pct"] == 0.0

    def test_campaign_revenue_per_email_zero_when_no_delivered(self):
        cid = nl.create_campaign("NoRev", "Sub")
        nl.record_campaign_revenue(cid, 100.0)
        camp = nl.get_campaign(cid)
        assert camp["revenue_per_email"] == 0.0


# ===========================================================================
# 8. TestEmailSequences
# ===========================================================================

class TestEmailSequences:
    """create_sequence, add_sequence_email, enroll_in_sequence, advance_enrollment."""

    def _make_confirmed_sub(self, email: str) -> int:
        nl.subscribe(email, double_optin=False)
        sub = nl.get_subscriber(email)
        return sub["id"]

    def test_create_sequence_returns_int_id(self):
        sid = nl.create_sequence("Welcome Series")
        assert isinstance(sid, int)
        assert sid > 0

    def test_create_sequence_with_trigger(self):
        sid = nl.create_sequence("Onboarding", trigger="signup")
        assert sid > 0

    def test_create_sequence_upsert_on_duplicate_name(self):
        sid1 = nl.create_sequence("Dupe Seq", description="first")
        sid2 = nl.create_sequence("Dupe Seq", description="second")
        # Both should succeed (upsert); the IDs may differ on conflict behaviour
        assert sid1 > 0
        assert sid2 >= 0

    def test_add_sequence_email_returns_id(self):
        sid = nl.create_sequence("Drip")
        eid = nl.add_sequence_email(sid, step_number=1, subject="Day 1",
                                    html_body="<p>Hi</p>", delay_hours=0)
        assert isinstance(eid, int)
        assert eid > 0

    def test_add_multiple_steps(self):
        sid = nl.create_sequence("Multi-step")
        e1 = nl.add_sequence_email(sid, 1, "Step 1", delay_hours=0)
        e2 = nl.add_sequence_email(sid, 2, "Step 2", delay_hours=24)
        e3 = nl.add_sequence_email(sid, 3, "Step 3", delay_hours=48)
        assert e1 > 0
        assert e2 > 0
        assert e3 > 0

    def test_add_sequence_email_upsert_on_duplicate_step(self):
        sid = nl.create_sequence("Upsert Steps")
        nl.add_sequence_email(sid, 1, "Original")
        step_id = nl.add_sequence_email(sid, 1, "Updated")
        # Should not raise; verify updated subject by checking DB directly
        row = nl._db().execute(
            "SELECT subject FROM nl_sequence_emails WHERE sequence_id=? AND step_number=1",
            (sid,),
        ).fetchone()
        assert row["subject"] == "Updated"

    def test_enroll_subscriber(self):
        sid = nl.create_sequence("Enroll Test")
        nl.add_sequence_email(sid, 1, "First email", delay_hours=0)
        sub_id = self._make_confirmed_sub("enroll@example.com")
        result = nl.enroll_in_sequence(sid, sub_id)
        assert result["ok"] is True
        assert "enrollment_id" in result

    def test_enroll_twice_blocked(self):
        sid = nl.create_sequence("Double Enroll")
        nl.add_sequence_email(sid, 1, "First", delay_hours=0)
        sub_id = self._make_confirmed_sub("double@example.com")
        nl.enroll_in_sequence(sid, sub_id)
        result2 = nl.enroll_in_sequence(sid, sub_id)
        assert result2["ok"] is False
        assert "already enrolled" in result2["error"]

    def test_advance_enrollment_to_next_step(self):
        sid = nl.create_sequence("Advance Test")
        nl.add_sequence_email(sid, 1, "Step 1", delay_hours=0)
        nl.add_sequence_email(sid, 2, "Step 2", delay_hours=1)
        sub_id = self._make_confirmed_sub("advance@example.com")
        enroll = nl.enroll_in_sequence(sid, sub_id)
        result = nl.advance_enrollment(enroll["enrollment_id"])
        assert result["status"] == "advanced"
        assert result["current_step"] == 1
        assert result["subject"] == "Step 1"

    def test_advance_enrollment_completes_after_last_step(self):
        sid = nl.create_sequence("One-step")
        nl.add_sequence_email(sid, 1, "Only email", delay_hours=0)
        sub_id = self._make_confirmed_sub("complete@example.com")
        enroll = nl.enroll_in_sequence(sid, sub_id)
        nl.advance_enrollment(enroll["enrollment_id"])   # moves to step 1
        result = nl.advance_enrollment(enroll["enrollment_id"])  # no step 2 => complete
        assert result["status"] == "completed"

    def test_advance_enrollment_missing(self):
        result = nl.advance_enrollment(999999)
        assert "error" in result

    def test_due_sequence_emails_empty_when_none_enrolled(self):
        due = nl.due_sequence_emails()
        assert isinstance(due, list)
        assert len(due) == 0

    def test_due_sequence_emails_returns_overdue_step(self):
        sid = nl.create_sequence("Due Seq")
        nl.add_sequence_email(sid, 1, "Now!", delay_hours=0)
        sub_id = self._make_confirmed_sub("dueseq@example.com")
        enroll = nl.enroll_in_sequence(sid, sub_id)
        # Force next_send_at to be in the past
        nl._db().execute(
            "UPDATE nl_sequence_enrollments SET next_send_at=? WHERE id=?",
            (time.time() - 60, enroll["enrollment_id"]),
        )
        nl._db().commit()
        due = nl.due_sequence_emails()
        assert any(d["email"] == "dueseq@example.com" for d in due)

    def test_due_sequence_emails_excludes_future(self):
        sid = nl.create_sequence("Future Seq")
        nl.add_sequence_email(sid, 1, "Later", delay_hours=24)
        sub_id = self._make_confirmed_sub("future_seq@example.com")
        nl.enroll_in_sequence(sid, sub_id)
        # Enrollment next_send_at is in 24 hours → should not appear
        due = nl.due_sequence_emails()
        assert not any(d["email"] == "future_seq@example.com" for d in due)

    def test_due_sequence_emails_excludes_unconfirmed_subscribers(self):
        sid = nl.create_sequence("Pending Sub Seq")
        nl.add_sequence_email(sid, 1, "Pending step", delay_hours=0)
        nl.subscribe("pending_seq@example.com", double_optin=True)  # stays pending
        sub = nl.get_subscriber("pending_seq@example.com")
        enroll = nl.enroll_in_sequence(sid, sub["id"])
        nl._db().execute(
            "UPDATE nl_sequence_enrollments SET next_send_at=? WHERE id=?",
            (time.time() - 60, enroll["enrollment_id"]),
        )
        nl._db().commit()
        due = nl.due_sequence_emails()
        assert not any(d["email"] == "pending_seq@example.com" for d in due)


# ===========================================================================
# 9. TestAnalyticsAndReporting
# ===========================================================================

class TestAnalyticsAndReporting:
    """newsletter_summary and growth_trend."""

    def test_newsletter_summary_empty_db(self):
        summary = nl.newsletter_summary()
        assert summary["confirmed_subscribers"] == 0
        assert summary["total_subscribers"] == 0
        assert summary["campaigns_sent"] == 0
        assert summary["total_revenue"] == 0.0
        assert summary["list_health_pct"] == 0.0

    def test_newsletter_summary_confirmed_count(self):
        nl.subscribe("s1@example.com", double_optin=False)
        nl.subscribe("s2@example.com", double_optin=False)
        nl.subscribe("s3@example.com", double_optin=True)  # pending
        summary = nl.newsletter_summary()
        assert summary["confirmed_subscribers"] == 2
        assert summary["pending_subscribers"] == 1
        assert summary["total_subscribers"] == 3

    def test_newsletter_summary_list_health(self):
        nl.subscribe("h1@example.com", double_optin=False)
        nl.subscribe("h2@example.com", double_optin=False)
        summary = nl.newsletter_summary()
        assert summary["list_health_pct"] == 100.0

    def test_newsletter_summary_bounced_count(self):
        nl.subscribe("b@example.com", double_optin=False)
        nl.record_bounce("b@example.com", bounce_type="hard")
        summary = nl.newsletter_summary()
        assert summary["bounced_subscribers"] >= 1

    def test_newsletter_summary_unsubscribed_count(self):
        nl.subscribe("u@example.com", double_optin=False)
        nl.unsubscribe("u@example.com")
        summary = nl.newsletter_summary()
        assert summary["unsubscribed"] >= 1

    def test_newsletter_summary_campaigns_sent(self):
        cid = nl.create_campaign("Report Camp", "Sub")
        nl.mark_campaign_sent(cid, recipients=10)
        summary = nl.newsletter_summary()
        assert summary["campaigns_sent"] >= 1

    def test_newsletter_summary_total_revenue(self):
        cid = nl.create_campaign("Rev Camp", "Sub")
        nl.mark_campaign_sent(cid, recipients=5)
        nl.record_campaign_revenue(cid, 99.50)
        summary = nl.newsletter_summary()
        assert summary["total_revenue"] >= 99.50

    def test_newsletter_summary_avg_open_rate(self):
        cid = nl.create_campaign("OpenRate Camp", "Sub")
        nl.mark_campaign_sent(cid, recipients=100)
        nl._db().execute(
            "UPDATE nl_campaigns SET delivered=100, unique_opens=20 WHERE id=?", (cid,)
        )
        nl._db().commit()
        summary = nl.newsletter_summary()
        # avg_open_rate should be 20/100*100 = 20 %
        assert abs(summary["avg_open_rate_pct"] - 20.0) < 0.1

    def test_growth_trend_empty(self):
        trend = nl.growth_trend(days=7)
        assert isinstance(trend, list)

    def test_growth_trend_contains_today(self):
        nl.subscribe("trend@example.com", double_optin=False)
        trend = nl.growth_trend(days=1)
        total_new = sum(r["new"] for r in trend)
        assert total_new >= 1

    def test_growth_trend_net_calculation(self):
        nl.subscribe("net1@example.com", double_optin=False)
        nl.subscribe("net2@example.com", double_optin=False)
        nl.unsubscribe("net1@example.com")
        trend = nl.growth_trend(days=1)
        total_net = sum(r["net"] for r in trend)
        # 2 new - 1 unsub = 1 net (within today)
        assert total_net >= 1

    def test_growth_trend_keys_present(self):
        nl.subscribe("keys@example.com", double_optin=False)
        trend = nl.growth_trend(days=1)
        if trend:
            row = trend[0]
            assert "day" in row
            assert "new" in row
            assert "unsubscribed" in row
            assert "net" in row


# ===========================================================================
# 10. TestToolWrappers
# ===========================================================================

class TestToolWrappers:
    """Agent-callable tool wrapper functions."""

    def test_subscribe_tool_basic(self):
        result_json = nl.subscribe_tool(email="tool@example.com")
        result = json.loads(result_json)
        assert result["ok"] is True

    def test_subscribe_tool_empty_email(self):
        result = nl.subscribe_tool(email="")
        assert result == "error: email required"

    def test_subscribe_tool_with_tags_string(self):
        result_json = nl.subscribe_tool(
            email="tagged_tool@example.com", tags="a, b, c"
        )
        result = json.loads(result_json)
        assert result["ok"] is True

    def test_unsubscribe_tool_success(self):
        nl.subscribe("unsub_tool@example.com", double_optin=False)
        result_json = nl.unsubscribe_tool(email="unsub_tool@example.com")
        result = json.loads(result_json)
        assert result["ok"] is True

    def test_unsubscribe_tool_empty_email(self):
        result = nl.unsubscribe_tool(email="")
        assert result == "error: email required"

    def test_subscriber_count_tool_returns_string(self):
        nl.subscribe("ct1@example.com", double_optin=False)
        result = nl.subscriber_count_tool()
        assert "confirmed" in result
        assert "1" in result or int(result.split()[0]) >= 1

    def test_subscriber_count_tool_with_list(self):
        nl.subscribe("listed@example.com", list_name="mylist", double_optin=False)
        result = nl.subscriber_count_tool(list_name="mylist")
        assert "mylist" in result

    def test_create_campaign_tool_success(self):
        result = nl.create_campaign_tool(
            name="Tool Campaign", subject="Hello", body="<p>Hi</p>"
        )
        assert "Tool Campaign" in result
        assert "id=" in result

    def test_create_campaign_tool_missing_name(self):
        result = nl.create_campaign_tool(subject="No name")
        assert result.startswith("error:")

    def test_create_campaign_tool_missing_subject(self):
        result = nl.create_campaign_tool(name="No subject")
        assert result.startswith("error:")

    def test_newsletter_summary_tool_returns_json(self):
        result = nl.newsletter_summary_tool()
        parsed = json.loads(result)
        assert "confirmed_subscribers" in parsed

    def test_due_campaigns_tool_no_campaigns(self):
        result = nl.due_campaigns_tool()
        assert "no campaigns" in result

    def test_due_campaigns_tool_with_due_campaign(self):
        cid = nl.create_campaign("Tool Due Camp", "Due!")
        nl.schedule_campaign(cid, time.time() - 5)
        result = nl.due_campaigns_tool()
        parsed = json.loads(result)
        assert any(c["id"] == cid for c in parsed)

    def test_create_sequence_tool_success(self):
        result = nl.create_sequence_tool(
            name="Tool Seq", description="desc", trigger="signup"
        )
        assert "Tool Seq" in result
        assert "id=" in result

    def test_create_sequence_tool_missing_name(self):
        result = nl.create_sequence_tool(name="")
        assert result.startswith("error:")

    def test_growth_trend_tool_empty(self):
        result = nl.growth_trend_tool(days=1)
        assert "no subscriber data" in result or isinstance(json.loads(result), list)

    def test_growth_trend_tool_with_data(self):
        nl.subscribe("trend_tool@example.com", double_optin=False)
        result = nl.growth_trend_tool(days=1)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1


# ===========================================================================
# 11. TestDataclassProperties  (Campaign / Subscriber computed fields)
# ===========================================================================

class TestDataclassProperties:
    """Verify computed properties on Campaign and Subscriber dataclasses."""

    def test_subscriber_full_name_both_parts(self):
        sub = nl.Subscriber(email="e@x.com", first_name="Ada", last_name="Lovelace")
        assert sub.full_name == "Ada Lovelace"

    def test_subscriber_full_name_first_only(self):
        sub = nl.Subscriber(email="e@x.com", first_name="Ada")
        assert sub.full_name == "Ada"

    def test_subscriber_full_name_fallback_to_email(self):
        sub = nl.Subscriber(email="e@x.com")
        assert sub.full_name == "e@x.com"

    def test_subscriber_is_active_when_confirmed(self):
        sub = nl.Subscriber(email="e@x.com", status="confirmed")
        assert sub.is_active is True

    def test_subscriber_is_not_active_when_pending(self):
        sub = nl.Subscriber(email="e@x.com", status="pending")
        assert sub.is_active is False

    def test_subscriber_to_dict_keys(self):
        sub = nl.Subscriber(email="e@x.com")
        d = sub.to_dict()
        for key in ["id", "email", "full_name", "status", "tags", "custom_fields",
                    "open_rate", "click_rate", "is_active"]:
            assert key in d, f"missing key: {key}"

    def test_campaign_open_rate_zero_when_no_delivered(self):
        camp = nl.Campaign(name="c", subject="s")
        assert camp.open_rate == 0.0

    def test_campaign_open_rate_calculated(self):
        camp = nl.Campaign(name="c", subject="s", delivered=200, unique_opens=40)
        assert camp.open_rate == 20.0

    def test_campaign_click_rate_zero_when_no_unique_opens(self):
        camp = nl.Campaign(name="c", subject="s")
        assert camp.click_rate == 0.0

    def test_campaign_click_rate_calculated(self):
        camp = nl.Campaign(name="c", subject="s", unique_opens=50, unique_clicks=10)
        assert camp.click_rate == 20.0

    def test_campaign_click_to_open_rate(self):
        camp = nl.Campaign(name="c", subject="s", opens=100, clicks=15)
        assert camp.click_to_open_rate == 15.0

    def test_campaign_bounce_rate(self):
        camp = nl.Campaign(name="c", subject="s", recipients=500, bounces=5)
        assert camp.bounce_rate == 1.0

    def test_campaign_unsubscribe_rate(self):
        camp = nl.Campaign(name="c", subject="s", delivered=1000, unsubscribes=2)
        assert camp.unsubscribe_rate == 0.2

    def test_campaign_revenue_per_email(self):
        camp = nl.Campaign(name="c", subject="s", delivered=100, revenue=500.0)
        assert camp.revenue_per_email == 5.0

    def test_campaign_to_dict_contains_all_rate_keys(self):
        camp = nl.Campaign(name="c", subject="s")
        d = camp.to_dict()
        for key in ["open_rate_pct", "click_rate_pct", "click_to_open_rate_pct",
                    "bounce_rate_pct", "unsubscribe_rate_pct", "revenue_per_email"]:
            assert key in d, f"missing key: {key}"

    def test_subscriber_token_generated_by_default(self):
        sub = nl.Subscriber(email="tok@x.com")
        assert len(sub.token) == 32  # uuid4().hex is 32 chars
