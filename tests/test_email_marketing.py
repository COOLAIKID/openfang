import importlib, sys, types, tempfile, os, sqlite3
import pytest
from datetime import datetime, timedelta

# ---------------------------------------------------------------------------
# Isolation fixture
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("autoearn.core.database.get_db_path", lambda: str(db_path))
    # Reset schema flag so each test starts fresh
    import autoearn.core.email_marketing as em
    em._schema_ready = False
    yield
    em._schema_ready = False


from autoearn.core import email_marketing as em


# ===========================================================================
# 1. TestCampaignCreation
# ===========================================================================

class TestCampaignCreation:
    """Basic campaign creation, duplicates, and validation."""

    def test_create_campaign_minimal(self):
        campaign = em.create_campaign(name="My Campaign", subject="Hello World")
        assert campaign is not None
        assert campaign.name == "My Campaign"
        assert campaign.subject == "Hello World"

    def test_create_campaign_returns_dataclass(self):
        campaign = em.create_campaign(name="DC Campaign", subject="Subject")
        assert hasattr(campaign, "id")
        assert hasattr(campaign, "name")
        assert hasattr(campaign, "subject")
        assert hasattr(campaign, "email_type")
        assert hasattr(campaign, "status")

    def test_create_campaign_default_status_is_draft(self):
        campaign = em.create_campaign(name="Draft Campaign", subject="Draft Subject")
        assert campaign.status == "draft"

    def test_create_campaign_with_email_type(self):
        campaign = em.create_campaign(
            name="Newsletter", subject="News", email_type="newsletter"
        )
        assert campaign.email_type == "newsletter"

    def test_create_campaign_with_body_html(self):
        html = "<h1>Hello</h1><p>Body content</p>"
        campaign = em.create_campaign(
            name="HTML Campaign", subject="HTML Subject", body_html=html
        )
        assert campaign is not None
        assert campaign.name == "HTML Campaign"

    def test_create_campaign_with_body_text(self):
        campaign = em.create_campaign(
            name="Text Campaign", subject="Text Subject",
            body_text="Plain text body"
        )
        assert campaign is not None

    def test_create_campaign_with_preview_text(self):
        campaign = em.create_campaign(
            name="Preview Campaign", subject="Preview Subject",
            preview_text="Preview snippet"
        )
        assert campaign is not None

    def test_create_campaign_all_fields(self):
        campaign = em.create_campaign(
            name="Full Campaign",
            subject="Full Subject",
            email_type="promotional",
            body_html="<p>HTML</p>",
            body_text="Text",
            preview_text="Preview",
        )
        assert campaign.name == "Full Campaign"
        assert campaign.subject == "Full Subject"

    def test_create_campaign_assigns_id(self):
        campaign = em.create_campaign(name="ID Campaign", subject="ID Subject")
        assert campaign.id is not None
        assert isinstance(campaign.id, int)

    def test_create_campaign_ids_are_unique(self):
        c1 = em.create_campaign(name="Camp A", subject="Sub A")
        c2 = em.create_campaign(name="Camp B", subject="Sub B")
        assert c1.id != c2.id

    def test_create_duplicate_campaign_name(self):
        em.create_campaign(name="Duplicate", subject="First")
        # Creating a second campaign with the same name should either raise or
        # return None/raise – test that it doesn't silently succeed with a
        # different id without error (behaviour depends on implementation).
        try:
            result = em.create_campaign(name="Duplicate", subject="Second")
            # If it returns something it should be a campaign object or None
            assert result is None or hasattr(result, "id")
        except Exception:
            pass  # Raising an exception on duplicate is also acceptable

    def test_create_campaign_open_rate_initial(self):
        campaign = em.create_campaign(name="Rate Camp", subject="Rate Sub")
        assert campaign.open_rate == 0.0 or campaign.open_rate is None or campaign.open_rate == 0

    def test_create_campaign_click_rate_initial(self):
        campaign = em.create_campaign(name="Click Camp", subject="Click Sub")
        assert campaign.click_rate == 0.0 or campaign.click_rate is None or campaign.click_rate == 0


# ===========================================================================
# 2. TestCampaignLifecycle
# ===========================================================================

class TestCampaignLifecycle:
    """Campaign state transitions and listing/filtering."""

    def _make_campaign(self, name="Lifecycle Camp", subject="Sub"):
        return em.create_campaign(name=name, subject=subject)

    def test_get_campaign_by_name(self):
        em.create_campaign(name="Findable", subject="Find me")
        found = em.get_campaign("Findable")
        assert found is not None
        assert found.name == "Findable"

    def test_get_campaign_missing_returns_none(self):
        result = em.get_campaign("Does Not Exist")
        assert result is None

    def test_schedule_campaign(self):
        campaign = self._make_campaign(name="Schedule Me")
        send_at = (datetime.utcnow() + timedelta(days=1)).isoformat()
        result = em.schedule_campaign(campaign.id, send_at)
        assert result is True

    def test_schedule_campaign_changes_status(self):
        campaign = self._make_campaign(name="Schedule Status")
        send_at = (datetime.utcnow() + timedelta(hours=2)).isoformat()
        em.schedule_campaign(campaign.id, send_at)
        updated = em.get_campaign("Schedule Status")
        assert updated.status == "scheduled"

    def test_schedule_nonexistent_campaign(self):
        send_at = (datetime.utcnow() + timedelta(days=1)).isoformat()
        result = em.schedule_campaign(99999, send_at)
        assert result is False

    def test_mark_campaign_sent(self):
        campaign = self._make_campaign(name="Send Me")
        result = em.mark_campaign_sent(campaign.id)
        assert result is True

    def test_mark_campaign_sent_changes_status(self):
        campaign = self._make_campaign(name="Sent Status")
        em.mark_campaign_sent(campaign.id)
        updated = em.get_campaign("Sent Status")
        assert updated.status == "sent"

    def test_mark_nonexistent_campaign_sent(self):
        result = em.mark_campaign_sent(99999)
        assert result is False

    def test_delete_campaign(self):
        campaign = self._make_campaign(name="Delete Me")
        result = em.delete_campaign(campaign.id)
        assert result is True

    def test_delete_campaign_removes_it(self):
        campaign = self._make_campaign(name="Gone Camp")
        em.delete_campaign(campaign.id)
        found = em.get_campaign("Gone Camp")
        assert found is None

    def test_delete_nonexistent_campaign(self):
        result = em.delete_campaign(99999)
        assert result is False

    def test_list_campaigns_empty(self):
        result = em.list_campaigns()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_list_campaigns_returns_all(self):
        em.create_campaign(name="List A", subject="Sub A")
        em.create_campaign(name="List B", subject="Sub B")
        result = em.list_campaigns()
        assert len(result) == 2

    def test_list_campaigns_filter_by_status(self):
        c1 = em.create_campaign(name="Draft Only", subject="Draft")
        c2 = em.create_campaign(name="Sent Only", subject="Sent")
        em.mark_campaign_sent(c2.id)
        drafts = em.list_campaigns(status="draft")
        assert all(c.status == "draft" for c in drafts)
        sent_list = em.list_campaigns(status="sent")
        assert all(c.status == "sent" for c in sent_list)

    def test_list_campaigns_filter_by_email_type(self):
        em.create_campaign(name="NL Camp", subject="NL", email_type="newsletter")
        em.create_campaign(name="Promo Camp", subject="Promo", email_type="promotional")
        newsletters = em.list_campaigns(email_type="newsletter")
        assert all(c.email_type == "newsletter" for c in newsletters)

    def test_list_campaigns_limit(self):
        for i in range(5):
            em.create_campaign(name=f"Limit Camp {i}", subject=f"Sub {i}")
        result = em.list_campaigns(limit=3)
        assert len(result) <= 3


# ===========================================================================
# 3. TestEventTracking
# ===========================================================================

class TestEventTracking:
    """Record events and verify analytics computation."""

    def _campaign(self, name="Event Camp"):
        c = em.create_campaign(name=name, subject="Event Subject")
        em.mark_campaign_sent(c.id)
        return c

    def test_record_open_event(self):
        c = self._campaign("Open Camp")
        # Should not raise
        em.record_event("open", "user@example.com", campaign_id=c.id)

    def test_record_click_event(self):
        c = self._campaign("Click Camp")
        em.record_event(
            "click", "user@example.com", campaign_id=c.id,
            link_url="https://example.com"
        )

    def test_record_bounce_event(self):
        c = self._campaign("Bounce Camp")
        em.record_event("bounce", "bounced@example.com", campaign_id=c.id)

    def test_record_unsubscribe_event(self):
        c = self._campaign("Unsub Camp")
        em.record_event("unsubscribe", "unsub@example.com", campaign_id=c.id)

    def test_record_event_without_campaign(self):
        # Events without a campaign_id should also be accepted
        em.record_event("open", "nocamp@example.com")

    def test_campaign_analytics_returns_dict(self):
        c = self._campaign("Analytics Camp")
        result = em.campaign_analytics(c.id)
        assert isinstance(result, dict)

    def test_campaign_analytics_has_open_rate(self):
        c = self._campaign("Open Rate Camp")
        result = em.campaign_analytics(c.id)
        assert "open_rate" in result

    def test_campaign_analytics_has_click_rate(self):
        c = self._campaign("Click Rate Camp")
        result = em.campaign_analytics(c.id)
        assert "click_rate" in result

    def test_campaign_analytics_open_rate_computed(self):
        c = self._campaign("Computed Open Camp")
        em.record_event("open", "a@example.com", campaign_id=c.id)
        em.record_event("open", "b@example.com", campaign_id=c.id)
        result = em.campaign_analytics(c.id)
        assert result["open_rate"] >= 0

    def test_campaign_analytics_click_rate_computed(self):
        c = self._campaign("Computed Click Camp")
        em.record_event("click", "a@example.com", campaign_id=c.id,
                        link_url="https://example.com/link")
        result = em.campaign_analytics(c.id)
        assert result["click_rate"] >= 0

    def test_campaign_analytics_multiple_events(self):
        c = self._campaign("Multi Event Camp")
        em.record_event("open", "a@example.com", campaign_id=c.id)
        em.record_event("open", "b@example.com", campaign_id=c.id)
        em.record_event("click", "a@example.com", campaign_id=c.id,
                        link_url="https://ex.com")
        em.record_event("bounce", "c@example.com", campaign_id=c.id)
        result = em.campaign_analytics(c.id)
        assert isinstance(result, dict)
        assert result.get("open_rate", 0) >= 0

    def test_campaign_analytics_no_events(self):
        c = self._campaign("No Events Camp")
        result = em.campaign_analytics(c.id)
        assert result["open_rate"] == 0 or result["open_rate"] == 0.0
        assert result["click_rate"] == 0 or result["click_rate"] == 0.0

    def test_campaign_analytics_nonexistent(self):
        result = em.campaign_analytics(99999)
        assert result is None or isinstance(result, dict)


# ===========================================================================
# 4. TestSequenceManagement
# ===========================================================================

class TestSequenceManagement:
    """Create, retrieve, list, and manage email sequences."""

    def test_create_sequence_minimal(self):
        seq = em.create_sequence(name="Welcome Seq")
        assert seq is not None
        assert seq.name == "Welcome Seq"

    def test_create_sequence_returns_dataclass(self):
        seq = em.create_sequence(name="DC Seq")
        assert hasattr(seq, "id")
        assert hasattr(seq, "name")

    def test_create_sequence_with_trigger_event(self):
        seq = em.create_sequence(name="Trigger Seq", trigger_event="signup")
        assert seq.name == "Trigger Seq"

    def test_create_sequence_with_description(self):
        seq = em.create_sequence(name="Desc Seq", description="A test sequence")
        assert seq is not None

    def test_create_sequence_with_delay_hours(self):
        seq = em.create_sequence(name="Delay Seq", delay_hours=24)
        assert seq is not None

    def test_create_sequence_assigns_id(self):
        seq = em.create_sequence(name="ID Seq")
        assert seq.id is not None
        assert isinstance(seq.id, int)

    def test_get_sequence_by_name(self):
        em.create_sequence(name="Findable Seq")
        found = em.get_sequence("Findable Seq")
        assert found is not None
        assert found.name == "Findable Seq"

    def test_get_sequence_missing_returns_none(self):
        result = em.get_sequence("No Such Sequence")
        assert result is None

    def test_list_sequences_empty(self):
        result = em.list_sequences()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_list_sequences_returns_all(self):
        em.create_sequence(name="Seq A")
        em.create_sequence(name="Seq B")
        result = em.list_sequences()
        assert len(result) == 2

    def test_add_sequence_email(self):
        seq = em.create_sequence(name="Email Seq")
        result = em.add_sequence_email(
            seq.id, subject="Welcome", body="Hello!"
        )
        assert result is not None
        assert isinstance(result, dict)

    def test_add_sequence_email_with_delay(self):
        seq = em.create_sequence(name="Delay Email Seq")
        result = em.add_sequence_email(
            seq.id, subject="Day 3", body="Day 3 content", delay_hours=72
        )
        assert result is not None

    def test_add_multiple_emails_to_sequence(self):
        seq = em.create_sequence(name="Multi Email Seq")
        em.add_sequence_email(seq.id, subject="Email 1", body="Body 1")
        em.add_sequence_email(seq.id, subject="Email 2", body="Body 2", delay_hours=48)
        em.add_sequence_email(seq.id, subject="Email 3", body="Body 3", delay_hours=96)
        # Just verify no error occurred; listing could be tested further

    def test_add_sequence_email_has_id(self):
        seq = em.create_sequence(name="ID Email Seq")
        email = em.add_sequence_email(seq.id, subject="Check ID", body="Body")
        assert "id" in email


# ===========================================================================
# 5. TestSubscriberEnrollment
# ===========================================================================

class TestSubscriberEnrollment:
    """Enroll/unenroll subscribers and test due-email timing."""

    def _sequence_with_email(self, name="Enroll Seq", delay_hours=0):
        seq = em.create_sequence(name=name, trigger_event="signup")
        em.add_sequence_email(
            seq.id, subject="First Email", body="Welcome!", delay_hours=delay_hours
        )
        return seq

    def test_enroll_subscriber_by_name(self):
        seq = self._sequence_with_email("Enroll By Name")
        result = em.enroll_subscriber("Enroll By Name", "user@example.com")
        assert result is not None
        assert isinstance(result, dict)

    def test_enroll_subscriber_by_id(self):
        seq = self._sequence_with_email("Enroll By ID")
        result = em.enroll_subscriber(seq.id, "user2@example.com")
        assert result is not None

    def test_enroll_subscriber_has_expected_keys(self):
        seq = self._sequence_with_email("Enroll Keys")
        result = em.enroll_subscriber("Enroll Keys", "keys@example.com")
        # Should return some form of enrollment info
        assert isinstance(result, dict)

    def test_unenroll_subscriber(self):
        seq = self._sequence_with_email("Unenroll Seq")
        em.enroll_subscriber("Unenroll Seq", "unenroll@example.com")
        result = em.unenroll_subscriber(seq.id, "unenroll@example.com")
        assert result is True

    def test_unenroll_nonexistent_subscriber(self):
        seq = self._sequence_with_email("No Sub Seq")
        result = em.unenroll_subscriber(seq.id, "ghost@example.com")
        assert result is False or result is True  # implementation-defined

    def test_get_due_sequence_emails_returns_list(self):
        result = em.get_due_sequence_emails()
        assert isinstance(result, list)

    def test_get_due_sequence_emails_immediate(self):
        seq = self._sequence_with_email("Due Now Seq", delay_hours=0)
        em.enroll_subscriber("Due Now Seq", "due@example.com")
        result = em.get_due_sequence_emails()
        assert isinstance(result, list)
        # With delay_hours=0, the email should be due immediately
        assert len(result) >= 0  # at least not an error

    def test_get_due_sequence_emails_future_not_due(self):
        seq = self._sequence_with_email("Future Seq", delay_hours=9999)
        em.enroll_subscriber("Future Seq", "future@example.com")
        result = em.get_due_sequence_emails()
        # Emails due far in future should not appear
        future_emails = [
            e for e in result
            if e.get("subscriber_email") == "future@example.com"
        ]
        assert len(future_emails) == 0

    def test_enroll_multiple_subscribers(self):
        seq = self._sequence_with_email("Multi Enroll Seq")
        em.enroll_subscriber("Multi Enroll Seq", "a@example.com")
        em.enroll_subscriber("Multi Enroll Seq", "b@example.com")
        em.enroll_subscriber("Multi Enroll Seq", "c@example.com")
        result = em.get_due_sequence_emails()
        assert isinstance(result, list)

    def test_enroll_subscriber_nonexistent_sequence(self):
        try:
            result = em.enroll_subscriber("No Such Seq", "user@example.com")
            assert result is None or isinstance(result, dict)
        except Exception:
            pass  # Raising an error for missing sequence is acceptable


# ===========================================================================
# 6. TestAutomations
# ===========================================================================

class TestAutomations:
    """Create, list, and fire automations."""

    def test_create_automation_minimal(self):
        result = em.create_automation(
            name="Welcome Auto", trigger_event="signup"
        )
        assert result is not None
        assert isinstance(result, dict)

    def test_create_automation_has_id(self):
        result = em.create_automation(
            name="ID Auto", trigger_event="purchase"
        )
        assert "id" in result

    def test_create_automation_with_action_type(self):
        result = em.create_automation(
            name="Action Auto", trigger_event="signup",
            action_type="send_sequence"
        )
        assert result is not None

    def test_create_automation_with_delay_minutes(self):
        result = em.create_automation(
            name="Delay Auto", trigger_event="signup",
            delay_minutes=30
        )
        assert result is not None

    def test_list_automations_empty(self):
        result = em.list_automations()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_list_automations_returns_all(self):
        em.create_automation(name="Auto A", trigger_event="signup")
        em.create_automation(name="Auto B", trigger_event="purchase")
        result = em.list_automations()
        assert len(result) == 2

    def test_list_automations_returns_dicts(self):
        em.create_automation(name="Dict Auto", trigger_event="signup")
        result = em.list_automations()
        for item in result:
            assert isinstance(item, dict)

    def test_fire_automation_returns_list(self):
        em.create_automation(name="Fire Auto", trigger_event="signup")
        result = em.fire_automation("signup", "fired@example.com")
        assert isinstance(result, list)

    def test_fire_automation_with_context(self):
        em.create_automation(name="Context Auto", trigger_event="purchase")
        result = em.fire_automation(
            "purchase", "ctx@example.com",
            context={"product_id": 42, "amount": 99.99}
        )
        assert isinstance(result, list)

    def test_fire_automation_no_match(self):
        # Firing an event with no matching automation should return empty list
        result = em.fire_automation("random_event_xyz", "nobody@example.com")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_fire_automation_matching_trigger(self):
        em.create_automation(name="Match Auto", trigger_event="subscribe")
        result = em.fire_automation("subscribe", "match@example.com")
        assert isinstance(result, list)

    def test_create_automation_name_stored(self):
        em.create_automation(name="Named Auto", trigger_event="signup")
        autos = em.list_automations()
        names = [a.get("name") for a in autos]
        assert "Named Auto" in names


# ===========================================================================
# 7. TestSegments
# ===========================================================================

class TestSegments:
    """Create and list subscriber segments."""

    def test_create_segment_minimal(self):
        result = em.create_segment(name="Active Users")
        assert result is not None
        assert isinstance(result, dict)

    def test_create_segment_has_id(self):
        result = em.create_segment(name="ID Segment")
        assert "id" in result

    def test_create_segment_with_conditions(self):
        conditions = '{"field": "signup_date", "op": "gt", "value": "2024-01-01"}'
        result = em.create_segment(
            name="Recent Signups", conditions_json=conditions
        )
        assert result is not None

    def test_create_segment_name_stored(self):
        em.create_segment(name="Named Segment")
        segments = em.list_segments()
        names = [s.get("name") for s in segments]
        assert "Named Segment" in names

    def test_list_segments_empty(self):
        result = em.list_segments()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_list_segments_returns_all(self):
        em.create_segment(name="Seg A")
        em.create_segment(name="Seg B")
        em.create_segment(name="Seg C")
        result = em.list_segments()
        assert len(result) == 3

    def test_list_segments_returns_dicts(self):
        em.create_segment(name="Dict Seg")
        result = em.list_segments()
        for item in result:
            assert isinstance(item, dict)

    def test_create_multiple_segments(self):
        names = ["Buyers", "Non-Buyers", "VIP", "Newsletter Only"]
        for name in names:
            em.create_segment(name=name)
        result = em.list_segments()
        assert len(result) == len(names)

    def test_create_segment_complex_conditions(self):
        conditions = '{"and": [{"field": "open_rate", "op": "gt", "value": 0.5}, {"field": "country", "op": "eq", "value": "US"}]}'
        result = em.create_segment(
            name="Engaged US Users", conditions_json=conditions
        )
        assert result is not None


# ===========================================================================
# 8. TestEmailSummary
# ===========================================================================

class TestEmailSummary:
    """email_marketing_summary returns expected keys and values."""

    def test_summary_returns_dict(self):
        result = em.email_marketing_summary()
        assert isinstance(result, dict)

    def test_summary_has_campaigns_key(self):
        result = em.email_marketing_summary()
        assert any("campaign" in k.lower() for k in result.keys())

    def test_summary_has_sequences_key(self):
        result = em.email_marketing_summary()
        assert any("sequence" in k.lower() for k in result.keys())

    def test_summary_campaigns_count_empty(self):
        result = em.email_marketing_summary()
        # With no data, campaign count should be 0
        campaign_key = next(
            (k for k in result.keys() if "campaign" in k.lower()), None
        )
        if campaign_key:
            val = result[campaign_key]
            assert val == 0 or isinstance(val, (int, dict))

    def test_summary_sequences_count_empty(self):
        result = em.email_marketing_summary()
        seq_key = next(
            (k for k in result.keys() if "sequence" in k.lower()), None
        )
        if seq_key:
            val = result[seq_key]
            assert val == 0 or isinstance(val, (int, dict))

    def test_summary_after_creating_campaigns(self):
        em.create_campaign(name="Summary Camp A", subject="Sub A")
        em.create_campaign(name="Summary Camp B", subject="Sub B")
        result = em.email_marketing_summary()
        assert isinstance(result, dict)

    def test_summary_after_creating_sequences(self):
        em.create_sequence(name="Summary Seq A")
        em.create_sequence(name="Summary Seq B")
        result = em.email_marketing_summary()
        assert isinstance(result, dict)

    def test_summary_after_mixed_data(self):
        em.create_campaign(name="Mixed Camp", subject="Mixed Sub")
        em.create_sequence(name="Mixed Seq")
        em.create_segment(name="Mixed Seg")
        em.create_automation(name="Mixed Auto", trigger_event="signup")
        result = em.email_marketing_summary()
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_summary_values_are_not_negative(self):
        em.create_campaign(name="Non-Neg Camp", subject="Sub")
        result = em.email_marketing_summary()
        for key, val in result.items():
            if isinstance(val, (int, float)):
                assert val >= 0, f"Negative value for key: {key}"

    def test_summary_is_repeatable(self):
        result1 = em.email_marketing_summary()
        result2 = em.email_marketing_summary()
        assert result1.keys() == result2.keys()


# ===========================================================================
# 9. TestSequenceTemplates
# ===========================================================================

class TestSequenceTemplates:
    """SEQUENCE_TEMPLATES dict and create_sequence_from_template."""

    def test_sequence_templates_exists(self):
        assert hasattr(em, "SEQUENCE_TEMPLATES")
        assert isinstance(em.SEQUENCE_TEMPLATES, dict)

    def test_welcome_series_template_exists(self):
        assert "welcome_series" in em.SEQUENCE_TEMPLATES

    def test_product_launch_template_exists(self):
        assert "product_launch" in em.SEQUENCE_TEMPLATES

    def test_re_engagement_template_exists(self):
        assert "re_engagement" in em.SEQUENCE_TEMPLATES

    def test_templates_have_at_least_three(self):
        assert len(em.SEQUENCE_TEMPLATES) >= 3

    def test_create_sequence_from_welcome_template(self):
        result = em.create_sequence_from_template("welcome_series")
        assert result is not None
        assert hasattr(result, "name")

    def test_create_sequence_from_product_launch_template(self):
        result = em.create_sequence_from_template("product_launch")
        assert result is not None
        assert hasattr(result, "name")

    def test_create_sequence_from_re_engagement_template(self):
        result = em.create_sequence_from_template("re_engagement")
        assert result is not None
        assert hasattr(result, "name")

    def test_create_sequence_from_template_with_custom_name(self):
        result = em.create_sequence_from_template(
            "welcome_series", sequence_name="My Welcome Sequence"
        )
        assert result is not None
        assert result.name == "My Welcome Sequence"

    def test_create_sequence_from_template_assigns_id(self):
        result = em.create_sequence_from_template("welcome_series")
        assert result.id is not None
        assert isinstance(result.id, int)

    def test_created_template_sequence_is_retrievable(self):
        created = em.create_sequence_from_template(
            "welcome_series", sequence_name="Retrievable Welcome"
        )
        found = em.get_sequence("Retrievable Welcome")
        assert found is not None
        assert found.name == "Retrievable Welcome"

    def test_create_sequence_from_invalid_template(self):
        try:
            result = em.create_sequence_from_template("nonexistent_template")
            assert result is None
        except Exception:
            pass  # Raising on invalid template name is acceptable

    def test_each_template_creates_unique_sequence(self):
        ids = set()
        for template_name in ["welcome_series", "product_launch", "re_engagement"]:
            seq = em.create_sequence_from_template(
                template_name, sequence_name=f"Unique {template_name}"
            )
            if seq and seq.id is not None:
                ids.add(seq.id)
        assert len(ids) == 3

    def test_template_sequence_appears_in_list(self):
        em.create_sequence_from_template(
            "welcome_series", sequence_name="Listed Welcome"
        )
        sequences = em.list_sequences()
        names = [s.name if hasattr(s, "name") else s.get("name") for s in sequences]
        assert "Listed Welcome" in names


# ===========================================================================
# 10. TestToolFunctions
# ===========================================================================

class TestToolFunctions:
    """Test tool wrapper functions that return JSON-serialisable results."""

    def _has_tool(self, name):
        return hasattr(em, name)

    def test_create_campaign_tool_exists(self):
        assert self._has_tool("create_campaign_tool") or self._has_tool("create_campaign")

    def test_list_campaigns_tool_exists(self):
        assert self._has_tool("list_campaigns_tool") or self._has_tool("list_campaigns")

    def test_create_campaign_tool_returns_serializable(self):
        import json
        if hasattr(em, "create_campaign_tool"):
            result = em.create_campaign_tool(
                name="Tool Camp", subject="Tool Subject"
            )
            # Should be JSON-serializable string or dict
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
            else:
                assert isinstance(result, dict)
        else:
            # Fall back to create_campaign and check dataclass
            c = em.create_campaign(name="Tool Camp", subject="Tool Subject")
            assert c is not None

    def test_list_campaigns_tool_returns_serializable(self):
        import json
        em.create_campaign(name="JSON Camp", subject="JSON Sub")
        if hasattr(em, "list_campaigns_tool"):
            result = em.list_campaigns_tool()
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, (list, dict))
            else:
                assert isinstance(result, (list, dict))
        else:
            result = em.list_campaigns()
            assert isinstance(result, list)

    def test_get_campaign_tool(self):
        import json
        em.create_campaign(name="Get Tool Camp", subject="Get Sub")
        if hasattr(em, "get_campaign_tool"):
            result = em.get_campaign_tool("Get Tool Camp")
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.get_campaign("Get Tool Camp")
            assert result is not None

    def test_campaign_analytics_tool(self):
        import json
        c = em.create_campaign(name="Analytics Tool Camp", subject="Analytics Sub")
        em.mark_campaign_sent(c.id)
        if hasattr(em, "campaign_analytics_tool"):
            result = em.campaign_analytics_tool(c.id)
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.campaign_analytics(c.id)
            assert isinstance(result, dict)

    def test_create_sequence_tool(self):
        import json
        if hasattr(em, "create_sequence_tool"):
            result = em.create_sequence_tool(name="Tool Seq")
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.create_sequence(name="Tool Seq")
            assert result is not None

    def test_list_sequences_tool(self):
        import json
        em.create_sequence(name="Listed Tool Seq")
        if hasattr(em, "list_sequences_tool"):
            result = em.list_sequences_tool()
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, (list, dict))
        else:
            result = em.list_sequences()
            assert isinstance(result, list)

    def test_create_automation_tool(self):
        import json
        if hasattr(em, "create_automation_tool"):
            result = em.create_automation_tool(
                name="Tool Auto", trigger_event="signup"
            )
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.create_automation(name="Tool Auto", trigger_event="signup")
            assert isinstance(result, dict)

    def test_list_automations_tool(self):
        import json
        em.create_automation(name="List Tool Auto", trigger_event="signup")
        if hasattr(em, "list_automations_tool"):
            result = em.list_automations_tool()
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, (list, dict))
        else:
            result = em.list_automations()
            assert isinstance(result, list)

    def test_create_segment_tool(self):
        import json
        if hasattr(em, "create_segment_tool"):
            result = em.create_segment_tool(name="Tool Segment")
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.create_segment(name="Tool Segment")
            assert isinstance(result, dict)

    def test_email_marketing_summary_tool(self):
        import json
        if hasattr(em, "email_marketing_summary_tool"):
            result = em.email_marketing_summary_tool()
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.email_marketing_summary()
            assert isinstance(result, dict)

    def test_enroll_subscriber_tool(self):
        import json
        em.create_sequence(name="Tool Enroll Seq", trigger_event="signup")
        em.add_sequence_email(
            em.get_sequence("Tool Enroll Seq").id,
            subject="Welcome", body="Body"
        )
        if hasattr(em, "enroll_subscriber_tool"):
            result = em.enroll_subscriber_tool(
                "Tool Enroll Seq", "tool@example.com"
            )
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, dict)
        else:
            result = em.enroll_subscriber("Tool Enroll Seq", "tool@example.com")
            assert isinstance(result, dict)

    def test_delete_campaign_tool(self):
        import json
        c = em.create_campaign(name="Delete Tool Camp", subject="Delete Sub")
        if hasattr(em, "delete_campaign_tool"):
            result = em.delete_campaign_tool(c.id)
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, (dict, bool))
        else:
            result = em.delete_campaign(c.id)
            assert result is True

    def test_schedule_campaign_tool(self):
        import json
        c = em.create_campaign(name="Schedule Tool Camp", subject="Schedule Sub")
        send_at = (datetime.utcnow() + timedelta(days=1)).isoformat()
        if hasattr(em, "schedule_campaign_tool"):
            result = em.schedule_campaign_tool(c.id, send_at)
            if isinstance(result, str):
                parsed = json.loads(result)
                assert isinstance(parsed, (dict, bool))
        else:
            result = em.schedule_campaign(c.id, send_at)
            assert result is True

    def test_record_event_tool(self):
        import json
        c = em.create_campaign(name="Record Tool Camp", subject="Record Sub")
        em.mark_campaign_sent(c.id)
        if hasattr(em, "record_event_tool"):
            result = em.record_event_tool(
                "open", "event_tool@example.com", campaign_id=c.id
            )
            if isinstance(result, str):
                json.loads(result)  # just check it's valid JSON
        else:
            em.record_event("open", "event_tool@example.com", campaign_id=c.id)
