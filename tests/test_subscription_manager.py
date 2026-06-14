"""
Tests for autoearn/core/subscription_manager.py

Uses an isolated SQLite database per test run so tests never touch the real DB.
The _schema_ready flag is reset before each test so the schema is re-initialised
against the clean database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Isolation bootstrap
# ---------------------------------------------------------------------------

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_sm_{uuid.uuid4().hex[:8]}.db")


def _get_db_path():
    return _DB_PATH


# Import module (may already be cached if test_integration.py ran first).
# After import, unconditionally override the module-level get_db_path binding
# so our tests always use _DB_PATH regardless of import order.
import autoearn.core.subscription_manager as sm  # noqa: E402

sm.get_db_path = _get_db_path


@pytest.fixture(autouse=True)
def reset_schema(monkeypatch):
    """Drop all tables and reset the schema-ready flag before each test."""
    # Pin get_db_path for this test and restore to our _get_db_path after.
    monkeypatch.setattr(sm, "get_db_path", _get_db_path)
    sm._schema_ready = False
    current_db = sm.get_db_path()
    conn = sqlite3.connect(current_db)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    for (t,) in tables:
        # sqlite_sequence is an internal SQLite table that cannot be dropped
        if t == "sqlite_sequence":
            continue
        conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.close()
    yield
    sm._schema_ready = False


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_plan(
    name="Starter",
    price_monthly=9.99,
    price_annual=None,
    trial_days=0,
    features_list=None,
    max_users=1,
    description="",
):
    return sm.create_plan(
        name=name,
        price_monthly=price_monthly,
        price_annual=price_annual,
        trial_days=trial_days,
        features_list=features_list,
        max_users=max_users,
        description=description,
    )


def _make_subscriber(
    email="user@example.com",
    name="Test User",
    plan_slug="starter",
    billing_cycle="monthly",
    coupon_code=None,
    metadata=None,
):
    return sm.subscribe(
        email=email,
        name=name,
        plan_slug=plan_slug,
        billing_cycle=billing_cycle,
        coupon_code=coupon_code,
        metadata=metadata,
    )


# ===========================================================================
# 1. Plan creation and listing
# ===========================================================================

class TestPlanCreationAndListing:
    """Tests for create_plan, get_plan, list_plans, update_plan, archive_plan."""

    def test_create_plan_returns_subscription_plan(self):
        plan = _make_plan()
        assert isinstance(plan, sm.SubscriptionPlan)
        assert plan.id is not None
        assert plan.name == "Starter"
        assert plan.slug == "starter"
        assert plan.price_monthly == 9.99

    def test_create_plan_default_annual_is_ten_times_monthly(self):
        plan = _make_plan(price_monthly=10.0)
        assert plan.price_annual == 100.0

    def test_create_plan_explicit_annual_price(self):
        plan = _make_plan(price_monthly=10.0, price_annual=80.0)
        assert plan.price_annual == 80.0

    def test_create_plan_with_trial_days(self):
        plan = _make_plan(trial_days=14)
        assert plan.trial_days == 14

    def test_create_plan_with_features_list(self):
        features = ["feature_a", "feature_b"]
        plan = _make_plan(features_list=features)
        assert plan.features == features

    def test_create_plan_status_is_active(self):
        plan = _make_plan()
        assert plan.status == "active"

    def test_create_plan_slug_is_slugified(self):
        plan = _make_plan(name="Pro Plan")
        assert plan.slug == "pro-plan"

    def test_create_plan_description_stored(self):
        plan = _make_plan(description="A great plan")
        assert plan.description == "A great plan"

    def test_create_plan_max_users(self):
        plan = _make_plan(max_users=5)
        assert plan.max_users == 5

    def test_get_plan_by_slug(self):
        _make_plan(name="Starter")
        fetched = sm.get_plan("starter")
        assert fetched is not None
        assert fetched.slug == "starter"

    def test_get_plan_by_name(self):
        _make_plan(name="Starter")
        fetched = sm.get_plan("Starter")
        assert fetched is not None
        assert fetched.name == "Starter"

    def test_get_plan_returns_none_for_unknown(self):
        result = sm.get_plan("nonexistent-plan")
        assert result is None

    def test_list_plans_returns_active_by_default(self):
        _make_plan(name="Plan A", price_monthly=5.0)
        _make_plan(name="Plan B", price_monthly=15.0)
        plans = sm.list_plans()
        assert len(plans) == 2
        assert all(p.status == "active" for p in plans)

    def test_list_plans_sorted_by_price_ascending(self):
        _make_plan(name="Cheap", price_monthly=5.0)
        _make_plan(name="Expensive", price_monthly=50.0)
        plans = sm.list_plans()
        assert plans[0].price_monthly <= plans[-1].price_monthly

    def test_list_plans_active_only_excludes_archived(self):
        plan = _make_plan(name="Old Plan")
        sm.archive_plan(plan.id)
        active_plans = sm.list_plans(active_only=True)
        assert all(p.id != plan.id for p in active_plans)

    def test_list_plans_active_only_false_includes_archived(self):
        plan = _make_plan(name="Old Plan")
        sm.archive_plan(plan.id)
        all_plans = sm.list_plans(active_only=False)
        ids = [p.id for p in all_plans]
        assert plan.id in ids

    def test_update_plan_price(self):
        plan = _make_plan(price_monthly=9.99)
        result = sm.update_plan(plan.id, price_monthly=19.99)
        assert result is True
        updated = sm.get_plan(plan.slug)
        assert updated.price_monthly == 19.99

    def test_update_plan_features_list(self):
        plan = _make_plan()
        result = sm.update_plan(plan.id, features=["x", "y", "z"])
        assert result is True
        updated = sm.get_plan(plan.slug)
        assert "x" in updated.features

    def test_update_plan_ignores_unknown_fields(self):
        plan = _make_plan()
        result = sm.update_plan(plan.id, totally_fake_field="value")
        assert result is False

    def test_archive_plan_changes_status(self):
        plan = _make_plan()
        sm.archive_plan(plan.id)
        all_plans = sm.list_plans(active_only=False)
        archived = next((p for p in all_plans if p.id == plan.id), None)
        assert archived is not None
        assert archived.status == "archived"

    def test_annual_savings_percent_calculation(self):
        # monthly=10, annual=120 → (120-120)/120 = 0% savings
        plan = _make_plan(price_monthly=10.0, price_annual=120.0)
        assert plan.annual_savings_percent == 0.0

        # monthly=10, annual=80 → (120-80)/120 = 33.33% savings
        plan2 = _make_plan(name="Plan2", price_monthly=10.0, price_annual=80.0)
        assert plan2.annual_savings_percent == pytest.approx(33.33, abs=0.01)

    def test_plan_to_dict_contains_expected_keys(self):
        plan = _make_plan()
        d = plan.to_dict()
        for key in ("id", "name", "slug", "price_monthly", "price_annual",
                    "trial_days", "features", "max_users", "status",
                    "annual_savings_percent", "created_at", "updated_at"):
            assert key in d


# ===========================================================================
# 2. Subscriber signup with different billing cycles
# ===========================================================================

class TestSubscriberSignup:
    """Tests for subscribe() with monthly, annual, and lifetime billing cycles."""

    def test_subscribe_monthly_creates_subscriber(self):
        _make_plan()
        sub = _make_subscriber()
        assert isinstance(sub, sm.Subscriber)
        assert sub.email == "user@example.com"
        assert sub.billing_cycle == "monthly"

    def test_subscribe_sets_status_active_when_no_trial(self):
        _make_plan(trial_days=0)
        sub = _make_subscriber()
        assert sub.status == "active"

    def test_subscribe_sets_status_trialing_with_trial_days(self):
        _make_plan(name="Trial Plan", trial_days=14)
        sub = _make_subscriber(plan_slug="trial-plan")
        assert sub.status == "trialing"
        assert sub.trial_ends_at is not None

    def test_subscribe_annual_billing_cycle(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="annual")
        assert sub.billing_cycle == "annual"

    def test_subscribe_lifetime_billing_cycle(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="lifetime")
        assert sub.billing_cycle == "lifetime"

    def test_subscribe_invalid_billing_cycle_defaults_to_monthly(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="weekly")
        assert sub.billing_cycle == "monthly"

    def test_subscribe_period_end_is_30_days_for_monthly(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="monthly")
        start = datetime.fromisoformat(sub.current_period_start)
        end = datetime.fromisoformat(sub.current_period_end)
        delta = (end - start).days
        assert delta == 30

    def test_subscribe_period_end_is_365_days_for_annual(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="annual")
        start = datetime.fromisoformat(sub.current_period_start)
        end = datetime.fromisoformat(sub.current_period_end)
        delta = (end - start).days
        assert delta == 365

    def test_subscribe_period_end_is_far_future_for_lifetime(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="lifetime")
        start = datetime.fromisoformat(sub.current_period_start)
        end = datetime.fromisoformat(sub.current_period_end)
        delta = (end - start).days
        assert delta == 36500

    def test_subscribe_plan_is_populated(self):
        _make_plan()
        sub = _make_subscriber()
        assert sub.plan is not None
        assert sub.plan.slug == "starter"

    def test_subscribe_metadata_stored(self):
        _make_plan()
        sub = _make_subscriber(metadata={"source": "landing_page"})
        assert sub.metadata.get("source") == "landing_page"

    def test_subscribe_raises_for_unknown_plan(self):
        with pytest.raises(ValueError, match="not found"):
            _make_subscriber(plan_slug="does-not-exist")

    def test_get_subscriber_returns_subscriber(self):
        _make_plan()
        _make_subscriber()
        fetched = sm.get_subscriber("user@example.com")
        assert fetched is not None
        assert fetched.email == "user@example.com"

    def test_get_subscriber_returns_none_for_unknown(self):
        result = sm.get_subscriber("nobody@example.com")
        assert result is None

    def test_list_subscribers_returns_all(self):
        _make_plan()
        _make_subscriber(email="a@example.com")
        _make_subscriber(email="b@example.com")
        subs = sm.list_subscribers()
        emails = [s.email for s in subs]
        assert "a@example.com" in emails
        assert "b@example.com" in emails

    def test_list_subscribers_filter_by_status(self):
        _make_plan(name="Trial Plan", trial_days=7)
        _make_plan(name="No Trial", price_monthly=5.0, trial_days=0)
        _make_subscriber(email="trial@example.com", plan_slug="trial-plan")
        _make_subscriber(email="active@example.com", plan_slug="no-trial")
        trialing = sm.list_subscribers(status="trialing")
        assert all(s.status == "trialing" for s in trialing)

    def test_list_subscribers_filter_by_plan_slug(self):
        _make_plan(name="Plan A", price_monthly=5.0)
        _make_plan(name="Plan B", price_monthly=10.0)
        _make_subscriber(email="a@example.com", plan_slug="plan-a")
        _make_subscriber(email="b@example.com", plan_slug="plan-b")
        result = sm.list_subscribers(plan_slug="plan-a")
        assert all(s.plan.slug == "plan-a" for s in result)

    def test_subscriber_is_active_property(self):
        _make_plan()
        sub = _make_subscriber()
        assert sub.is_active is True

    def test_subscriber_monthly_value_for_monthly_cycle(self):
        _make_plan(price_monthly=9.99)
        sub = _make_subscriber()
        assert sub.monthly_value == pytest.approx(9.99)

    def test_subscriber_monthly_value_for_annual_cycle(self):
        _make_plan(price_monthly=10.0, price_annual=96.0)
        sub = _make_subscriber(billing_cycle="annual")
        assert sub.monthly_value == pytest.approx(8.0)

    def test_subscriber_monthly_value_for_lifetime_is_zero(self):
        _make_plan()
        sub = _make_subscriber(billing_cycle="lifetime")
        assert sub.monthly_value == 0.0

    def test_subscriber_to_dict_has_expected_keys(self):
        _make_plan()
        sub = _make_subscriber()
        d = sub.to_dict()
        for key in ("id", "email", "name", "plan_id", "plan", "status",
                    "billing_cycle", "is_active", "monthly_value",
                    "days_since_signup", "created_at", "updated_at"):
            assert key in d


# ===========================================================================
# 3. Plan upgrades and downgrades
# ===========================================================================

class TestPlanChanges:
    """Tests for upgrade_plan() and downgrade_plan()."""

    def test_upgrade_plan_changes_plan_id(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_plan(name="Pro", price_monthly=29.0)
        _make_subscriber(plan_slug="starter")
        sub = sm.upgrade_plan("user@example.com", "pro")
        assert sub.plan.slug == "pro"

    def test_upgrade_plan_returns_subscriber(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_plan(name="Pro", price_monthly=29.0)
        _make_subscriber(plan_slug="starter")
        result = sm.upgrade_plan("user@example.com", "pro")
        assert isinstance(result, sm.Subscriber)

    def test_downgrade_plan_changes_plan_id(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_plan(name="Pro", price_monthly=29.0)
        _make_subscriber(plan_slug="pro")
        sub = sm.downgrade_plan("user@example.com", "starter")
        assert sub.plan.slug == "starter"

    def test_downgrade_plan_returns_subscriber(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_plan(name="Pro", price_monthly=29.0)
        _make_subscriber(plan_slug="pro")
        result = sm.downgrade_plan("user@example.com", "starter")
        assert isinstance(result, sm.Subscriber)

    def test_upgrade_raises_for_unknown_subscriber(self):
        _make_plan(name="Pro", price_monthly=29.0)
        with pytest.raises(ValueError, match="not found"):
            sm.upgrade_plan("ghost@example.com", "pro")

    def test_upgrade_raises_for_unknown_new_plan(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_subscriber(plan_slug="starter")
        with pytest.raises(ValueError, match="not found"):
            sm.upgrade_plan("user@example.com", "nonexistent-plan")

    def test_downgrade_raises_for_unknown_subscriber(self):
        _make_plan(name="Starter", price_monthly=9.0)
        with pytest.raises(ValueError, match="not found"):
            sm.downgrade_plan("ghost@example.com", "starter")

    def test_multiple_plan_changes_are_reflected(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_plan(name="Pro", price_monthly=29.0)
        _make_plan(name="Enterprise", price_monthly=99.0)
        _make_subscriber(plan_slug="starter")
        sm.upgrade_plan("user@example.com", "pro")
        final = sm.upgrade_plan("user@example.com", "enterprise")
        assert final.plan.slug == "enterprise"

    def test_upgrade_reflects_in_get_subscriber(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_plan(name="Pro", price_monthly=29.0)
        _make_subscriber(plan_slug="starter")
        sm.upgrade_plan("user@example.com", "pro")
        fetched = sm.get_subscriber("user@example.com")
        assert fetched.plan.slug == "pro"


# ===========================================================================
# 4. Cancellation and reactivation
# ===========================================================================

class TestCancellationAndReactivation:
    """Tests for cancel_subscription(), reactivate_subscription(),
    pause_subscription(), and resume_subscription()."""

    def test_cancel_immediately_sets_status_cancelled(self):
        _make_plan()
        _make_subscriber()
        result = sm.cancel_subscription("user@example.com", immediately=True)
        assert result is True
        sub = sm.get_subscriber("user@example.com")
        assert sub.status == "cancelled"

    def test_cancel_at_period_end_sets_flag(self):
        _make_plan()
        _make_subscriber()
        result = sm.cancel_subscription("user@example.com", immediately=False)
        assert result is True
        sub = sm.get_subscriber("user@example.com")
        assert sub.cancel_at_period_end is True
        # Status should NOT be 'cancelled' yet
        assert sub.status != "cancelled"

    def test_cancel_returns_false_for_unknown_subscriber(self):
        result = sm.cancel_subscription("ghost@example.com", immediately=True)
        assert result is False

    def test_reactivate_sets_status_active(self):
        _make_plan()
        _make_subscriber()
        sm.cancel_subscription("user@example.com", immediately=True)
        result = sm.reactivate_subscription("user@example.com")
        assert result is True
        sub = sm.get_subscriber("user@example.com")
        assert sub.status == "active"

    def test_reactivate_resets_cancel_at_period_end(self):
        _make_plan()
        _make_subscriber()
        sm.cancel_subscription("user@example.com", immediately=False)
        sm.reactivate_subscription("user@example.com")
        sub = sm.get_subscriber("user@example.com")
        assert sub.cancel_at_period_end is False

    def test_reactivate_returns_false_for_unknown_subscriber(self):
        result = sm.reactivate_subscription("ghost@example.com")
        assert result is False

    def test_pause_active_subscriber(self):
        _make_plan()
        _make_subscriber()
        result = sm.pause_subscription("user@example.com")
        assert result is True
        sub = sm.get_subscriber("user@example.com")
        assert sub.status == "paused"

    def test_pause_already_paused_returns_false(self):
        _make_plan()
        _make_subscriber()
        sm.pause_subscription("user@example.com")
        result = sm.pause_subscription("user@example.com")
        assert result is False

    def test_pause_cancelled_subscriber_returns_false(self):
        _make_plan()
        _make_subscriber()
        sm.cancel_subscription("user@example.com", immediately=True)
        result = sm.pause_subscription("user@example.com")
        assert result is False

    def test_resume_paused_subscriber(self):
        _make_plan()
        _make_subscriber()
        sm.pause_subscription("user@example.com")
        result = sm.resume_subscription("user@example.com")
        assert result is True
        sub = sm.get_subscriber("user@example.com")
        assert sub.status == "active"

    def test_resume_active_subscriber_returns_false(self):
        _make_plan()
        _make_subscriber()
        result = sm.resume_subscription("user@example.com")
        assert result is False

    def test_resume_unknown_subscriber_returns_false(self):
        result = sm.resume_subscription("ghost@example.com")
        assert result is False

    def test_is_active_false_when_cancelled(self):
        _make_plan()
        _make_subscriber()
        sm.cancel_subscription("user@example.com", immediately=True)
        sub = sm.get_subscriber("user@example.com")
        assert sub.is_active is False


# ===========================================================================
# 5. Invoice creation and payment
# ===========================================================================

class TestInvoices:
    """Tests for create_invoice(), mark_invoice_paid(), list_invoices()."""

    def test_create_invoice_returns_dict(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=9.99)
        assert isinstance(invoice, dict)
        assert "id" in invoice

    def test_create_invoice_amount_stored(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=49.99)
        assert invoice["amount"] == 49.99

    def test_create_invoice_default_status_is_open(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=9.99)
        assert invoice["status"] == "open"

    def test_create_invoice_default_currency_is_usd(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=9.99)
        assert invoice["currency"] == "USD"

    def test_create_invoice_with_billing_reason(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice(
            "user@example.com", amount=9.99, billing_reason="renewal"
        )
        assert invoice["billing_reason"] == "renewal"

    def test_create_invoice_with_period(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice(
            "user@example.com",
            amount=9.99,
            period_start="2025-01-01T00:00:00",
            period_end="2025-01-31T00:00:00",
        )
        assert invoice["period_start"] == "2025-01-01T00:00:00"
        assert invoice["period_end"] == "2025-01-31T00:00:00"

    def test_create_invoice_error_for_unknown_subscriber(self):
        result = sm.create_invoice("ghost@example.com", amount=9.99)
        assert "error" in result

    def test_mark_invoice_paid_returns_true(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=9.99)
        result = sm.mark_invoice_paid(invoice["id"])
        assert result is True

    def test_mark_invoice_paid_updates_status(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=9.99)
        sm.mark_invoice_paid(invoice["id"])
        paid_invoices = sm.list_invoices(
            subscriber_email="user@example.com", status="paid"
        )
        assert any(i["id"] == invoice["id"] for i in paid_invoices)

    def test_mark_invoice_paid_with_explicit_timestamp(self):
        _make_plan()
        _make_subscriber()
        invoice = sm.create_invoice("user@example.com", amount=9.99)
        ts = "2025-06-01T12:00:00"
        sm.mark_invoice_paid(invoice["id"], paid_at=ts)
        invoices = sm.list_invoices(subscriber_email="user@example.com")
        paid = next((i for i in invoices if i["id"] == invoice["id"]), None)
        assert paid is not None
        assert paid["paid_at"] == ts

    def test_mark_invoice_paid_unknown_id_returns_false(self):
        result = sm.mark_invoice_paid(999999)
        assert result is False

    def test_list_invoices_without_filter_returns_all(self):
        _make_plan()
        _make_subscriber(email="a@example.com")
        _make_subscriber(email="b@example.com")
        sm.create_invoice("a@example.com", amount=10.0)
        sm.create_invoice("b@example.com", amount=20.0)
        invoices = sm.list_invoices()
        assert len(invoices) >= 2

    def test_list_invoices_filtered_by_email(self):
        _make_plan()
        _make_subscriber(email="a@example.com")
        _make_subscriber(email="b@example.com")
        sm.create_invoice("a@example.com", amount=10.0)
        sm.create_invoice("b@example.com", amount=20.0)
        invoices = sm.list_invoices(subscriber_email="a@example.com")
        assert all(i["subscriber_email"] == "a@example.com" for i in invoices)

    def test_list_invoices_filtered_by_status(self):
        _make_plan()
        _make_subscriber()
        i1 = sm.create_invoice("user@example.com", amount=10.0)
        sm.create_invoice("user@example.com", amount=20.0)
        sm.mark_invoice_paid(i1["id"])
        paid = sm.list_invoices(status="paid")
        assert all(i["status"] == "paid" for i in paid)

    def test_list_invoices_unknown_subscriber_returns_empty(self):
        result = sm.list_invoices(subscriber_email="ghost@example.com")
        assert result == []


# ===========================================================================
# 6. Coupon creation and validation
# ===========================================================================

class TestCoupons:
    """Tests for create_coupon(), validate_coupon(), apply_coupon()."""

    def test_create_percent_coupon(self):
        result = sm.create_coupon("SAVE10", "percent", 10.0)
        assert "error" not in result
        assert result["code"] == "SAVE10"
        assert result["discount_type"] == "percent"
        assert result["discount_value"] == 10.0

    def test_create_flat_coupon(self):
        result = sm.create_coupon("FLAT5", "flat", 5.0)
        assert "error" not in result
        assert result["discount_type"] == "flat"

    def test_create_coupon_code_uppercased(self):
        result = sm.create_coupon("lowercase", "percent", 10.0)
        assert result["code"] == "LOWERCASE"

    def test_create_coupon_invalid_discount_type(self):
        result = sm.create_coupon("BAD", "daily", 10.0)
        assert "error" in result

    def test_create_coupon_duplicate_code_returns_error(self):
        sm.create_coupon("DUPE", "percent", 10.0)
        result = sm.create_coupon("DUPE", "flat", 5.0)
        assert "error" in result

    def test_create_coupon_with_max_uses(self):
        result = sm.create_coupon("LIMITED", "percent", 20.0, max_uses=5)
        assert result["max_uses"] == 5
        assert result["uses"] == 0

    def test_create_coupon_with_expiry(self):
        future = (datetime.utcnow() + timedelta(days=30)).isoformat()
        result = sm.create_coupon("FUTURE", "percent", 10.0, expires_at=future)
        assert result["expires_at"] == future

    def test_validate_coupon_valid(self):
        sm.create_coupon("VALID10", "percent", 10.0)
        result = sm.validate_coupon("VALID10")
        assert result["valid"] is True

    def test_validate_coupon_case_insensitive(self):
        sm.create_coupon("MYCODE", "percent", 10.0)
        result = sm.validate_coupon("mycode")
        assert result["valid"] is True

    def test_validate_coupon_not_found(self):
        result = sm.validate_coupon("NOSUCHCODE")
        assert result["valid"] is False
        assert "reason" in result

    def test_validate_coupon_expired(self):
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        sm.create_coupon("EXPIRED", "percent", 10.0, expires_at=past)
        result = sm.validate_coupon("EXPIRED")
        assert result["valid"] is False
        assert "expired" in result["reason"].lower()

    def test_validate_coupon_exhausted_uses(self):
        sm.create_coupon("MAXED", "percent", 10.0, max_uses=1)
        _make_plan()
        _make_subscriber()
        sm.apply_coupon("MAXED", "user@example.com")
        result = sm.validate_coupon("MAXED")
        assert result["valid"] is False
        assert "maximum" in result["reason"].lower()

    def test_apply_percent_coupon_reduces_price(self):
        sm.create_coupon("PERCENT20", "percent", 20.0)
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        discounted = sm.apply_coupon("PERCENT20", "user@example.com")
        assert discounted == pytest.approx(8.0)

    def test_apply_flat_coupon_reduces_price(self):
        sm.create_coupon("FLAT3", "flat", 3.0)
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        discounted = sm.apply_coupon("FLAT3", "user@example.com")
        assert discounted == pytest.approx(7.0)

    def test_apply_coupon_increments_use_count(self):
        sm.create_coupon("USECOUNT", "percent", 10.0)
        _make_plan()
        _make_subscriber()
        sm.apply_coupon("USECOUNT", "user@example.com")
        validation = sm.validate_coupon("USECOUNT")
        assert validation["uses"] == 1

    def test_apply_coupon_100_percent_results_in_zero(self):
        sm.create_coupon("FREE100", "percent", 100.0)
        _make_plan(price_monthly=9.99)
        _make_subscriber()
        discounted = sm.apply_coupon("FREE100", "user@example.com")
        assert discounted == 0.0

    def test_apply_flat_coupon_does_not_go_negative(self):
        sm.create_coupon("TOOLARGE", "flat", 999.0)
        _make_plan(price_monthly=5.0)
        _make_subscriber()
        discounted = sm.apply_coupon("TOOLARGE", "user@example.com")
        assert discounted >= 0.0

    def test_apply_invalid_coupon_raises(self):
        _make_plan()
        _make_subscriber()
        with pytest.raises(ValueError):
            sm.apply_coupon("FAKECODE", "user@example.com")

    def test_apply_coupon_unknown_subscriber_raises(self):
        sm.create_coupon("CODE", "percent", 10.0)
        with pytest.raises(ValueError, match="not found"):
            sm.apply_coupon("CODE", "ghost@example.com")


# ===========================================================================
# 7. MRR / ARR calculations
# ===========================================================================

class TestMRRAndARR:
    """Tests for mrr() and arr()."""

    def test_mrr_zero_with_no_subscribers(self):
        assert sm.mrr() == 0.0

    def test_arr_zero_with_no_subscribers(self):
        assert sm.arr() == 0.0

    def test_mrr_single_monthly_subscriber(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        assert sm.mrr() == pytest.approx(10.0)

    def test_mrr_multiple_monthly_subscribers(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber(email="a@example.com")
        _make_subscriber(email="b@example.com")
        assert sm.mrr() == pytest.approx(20.0)

    def test_mrr_annual_subscriber_contributes_monthly_share(self):
        _make_plan(price_monthly=10.0, price_annual=120.0)
        _make_subscriber(billing_cycle="annual")
        assert sm.mrr() == pytest.approx(10.0)

    def test_mrr_lifetime_subscriber_contributes_zero(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber(billing_cycle="lifetime")
        assert sm.mrr() == 0.0

    def test_mrr_excludes_cancelled_subscribers(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        sm.cancel_subscription("user@example.com", immediately=True)
        assert sm.mrr() == 0.0

    def test_mrr_includes_trialing_subscribers(self):
        _make_plan(name="Trial Plan", price_monthly=10.0, trial_days=7)
        _make_subscriber(plan_slug="trial-plan")
        assert sm.mrr() == pytest.approx(10.0)

    def test_arr_equals_mrr_times_twelve(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        assert sm.arr() == pytest.approx(sm.mrr() * 12)

    def test_mrr_mixed_cycles(self):
        _make_plan(price_monthly=10.0, price_annual=96.0)
        _make_subscriber(email="monthly@example.com", billing_cycle="monthly")
        _make_subscriber(email="annual@example.com", billing_cycle="annual")
        # 10.0 + 96/12 = 10 + 8 = 18
        assert sm.mrr() == pytest.approx(18.0)


# ===========================================================================
# 8. Churn rate and growth metrics
# ===========================================================================

class TestChurnAndGrowth:
    """Tests for churn_rate() and subscriber_growth()."""

    def test_churn_rate_zero_with_no_data(self):
        assert sm.churn_rate() == 0.0

    def test_churn_rate_zero_with_no_cancellations(self):
        _make_plan()
        _make_subscriber()
        assert sm.churn_rate() == 0.0

    def test_churn_rate_positive_after_cancellation(self):
        _make_plan()
        # Create subscriber in the "past" by inserting directly
        _make_subscriber()
        # Need a subscriber created before the cutoff
        cutoff = (datetime.utcnow() - timedelta(days=31)).isoformat()
        conn = sqlite3.connect(_DB_PATH)
        sub_id = conn.execute(
            "SELECT id FROM subscribers WHERE email = 'user@example.com'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE subscribers SET created_at = ? WHERE id = ?",
            (cutoff, sub_id),
        )
        conn.commit()
        conn.close()
        # Now cancel
        sm.cancel_subscription("user@example.com", immediately=True)
        rate = sm.churn_rate(30)
        assert rate > 0.0

    def test_churn_rate_returns_float(self):
        result = sm.churn_rate(days=30)
        assert isinstance(result, float)

    def test_subscriber_growth_empty_when_no_subscribers(self):
        result = sm.subscriber_growth(days=7)
        assert isinstance(result, list)
        assert result == []

    def test_subscriber_growth_returns_list_of_dicts(self):
        _make_plan()
        _make_subscriber()
        result = sm.subscriber_growth(days=7)
        assert isinstance(result, list)
        for entry in result:
            assert "date" in entry
            assert "new_subscribers" in entry

    def test_subscriber_growth_includes_today(self):
        _make_plan()
        _make_subscriber()
        result = sm.subscriber_growth(days=1)
        total = sum(e["new_subscribers"] for e in result)
        assert total >= 1

    def test_subscriber_growth_custom_window(self):
        result = sm.subscriber_growth(days=90)
        assert isinstance(result, list)

    def test_expire_trials_transitions_without_stripe(self):
        _make_plan(name="Trial Plan", trial_days=1)
        _make_subscriber(plan_slug="trial-plan")
        # Backdate trial_ends_at to the past
        conn = sqlite3.connect(_DB_PATH)
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        conn.execute(
            "UPDATE subscribers SET trial_ends_at = ? WHERE email = 'user@example.com'",
            (past,),
        )
        conn.commit()
        conn.close()
        count = sm.expire_trials()
        assert count >= 1
        sub = sm.get_subscriber("user@example.com")
        assert sub.status == "expired"

    def test_expire_trials_activates_with_stripe_id(self):
        _make_plan(name="Trial Plan 2", trial_days=1)
        _make_subscriber(email="stripe@example.com", plan_slug="trial-plan-2")
        conn = sqlite3.connect(_DB_PATH)
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        conn.execute(
            """UPDATE subscribers SET trial_ends_at = ?, stripe_customer_id = 'cus_test'
               WHERE email = 'stripe@example.com'""",
            (past,),
        )
        conn.commit()
        conn.close()
        sm.expire_trials()
        sub = sm.get_subscriber("stripe@example.com")
        assert sub.status == "active"


# ===========================================================================
# 9. Plan distribution analytics
# ===========================================================================

class TestPlanDistributionAnalytics:
    """Tests for plan_distribution(), ltv_estimate(), cohort_retention(),
    subscription_summary()."""

    def test_plan_distribution_empty_with_no_data(self):
        result = sm.plan_distribution()
        assert isinstance(result, list)

    def test_plan_distribution_includes_plan_entries(self):
        _make_plan(name="Starter", price_monthly=9.0)
        _make_subscriber()
        result = sm.plan_distribution()
        assert len(result) >= 1
        assert any(r["plan_slug"] == "starter" for r in result)

    def test_plan_distribution_subscriber_count_correct(self):
        _make_plan(price_monthly=9.0)
        _make_subscriber(email="a@example.com")
        _make_subscriber(email="b@example.com")
        result = sm.plan_distribution()
        starter_entry = next(r for r in result if r["plan_slug"] == "starter")
        assert starter_entry["subscriber_count"] == 2

    def test_plan_distribution_mrr_correct_for_monthly(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        result = sm.plan_distribution()
        starter = next(r for r in result if r["plan_slug"] == "starter")
        assert starter["mrr"] == pytest.approx(10.0)

    def test_plan_distribution_sorted_by_mrr_descending(self):
        _make_plan(name="Cheap", price_monthly=5.0)
        _make_plan(name="Expensive", price_monthly=50.0)
        _make_subscriber(email="c@example.com", plan_slug="cheap")
        _make_subscriber(email="e@example.com", plan_slug="expensive")
        result = sm.plan_distribution()
        mrrs = [r["mrr"] for r in result]
        assert mrrs == sorted(mrrs, reverse=True)

    def test_plan_distribution_excludes_cancelled(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        sm.cancel_subscription("user@example.com", immediately=True)
        result = sm.plan_distribution()
        starter = next((r for r in result if r["plan_slug"] == "starter"), None)
        if starter:
            assert starter["subscriber_count"] == 0

    def test_ltv_estimate_zero_with_no_subscribers(self):
        result = sm.ltv_estimate()
        assert result == 0.0

    def test_ltv_estimate_positive_with_subscriber(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        result = sm.ltv_estimate()
        assert result >= 0.0

    def test_ltv_estimate_by_plan_slug(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        result = sm.ltv_estimate(plan_slug="starter")
        assert result >= 0.0

    def test_ltv_estimate_unknown_plan_returns_zero(self):
        result = sm.ltv_estimate(plan_slug="nonexistent")
        assert result == 0.0

    def test_cohort_retention_returns_list(self):
        result = sm.cohort_retention(months=3)
        assert isinstance(result, list)
        assert len(result) == 3

    def test_cohort_retention_has_expected_keys(self):
        result = sm.cohort_retention(months=2)
        for entry in result:
            assert "cohort_month" in entry
            assert "cohort_size" in entry
            assert "retained" in entry
            assert "retention_rate" in entry

    def test_cohort_retention_rate_between_0_and_100(self):
        _make_plan()
        _make_subscriber()
        result = sm.cohort_retention(months=3)
        for entry in result:
            assert 0.0 <= entry["retention_rate"] <= 100.0

    def test_subscription_summary_keys_present(self):
        result = sm.subscription_summary()
        for key in ("mrr", "arr", "total_active", "total_trialing",
                    "total_cancelled", "total_subscribers",
                    "churn_rate_30d", "avg_ltv", "top_plan",
                    "plan_distribution"):
            assert key in result

    def test_subscription_summary_mrr_matches_mrr_function(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        summary = sm.subscription_summary()
        assert summary["mrr"] == pytest.approx(sm.mrr())

    def test_subscription_summary_arr_is_twelve_times_mrr(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        summary = sm.subscription_summary()
        assert summary["arr"] == pytest.approx(summary["mrr"] * 12)

    def test_subscription_summary_top_plan_with_data(self):
        _make_plan(price_monthly=10.0)
        _make_subscriber()
        summary = sm.subscription_summary()
        assert summary["top_plan"] == "Starter"

    def test_subscription_summary_top_plan_none_when_empty(self):
        summary = sm.subscription_summary()
        assert summary["top_plan"] is None


# ===========================================================================
# 10. Tool function wrappers
# ===========================================================================

class TestToolFunctions:
    """Tests for the @tool-decorated wrapper functions."""

    def test_sm_create_plan_tool_success(self):
        result_str = sm.sm_create_plan_tool(name="Tool Plan", price_monthly=9.99)
        result = json.loads(result_str)
        assert result["ok"] is True
        assert result["plan"]["name"] == "Tool Plan"

    def test_sm_create_plan_tool_has_plan_dict(self):
        result_str = sm.sm_create_plan_tool(name="Tool Plan 2", price_monthly=5.0)
        result = json.loads(result_str)
        assert "plan" in result
        assert "id" in result["plan"]

    def test_sm_subscribe_tool_success(self):
        sm.sm_create_plan_tool(name="Sub Plan", price_monthly=9.99)
        result_str = sm.sm_subscribe_tool(
            email="tool@example.com", name="Tool User", plan_slug="sub-plan"
        )
        result = json.loads(result_str)
        assert result["ok"] is True
        assert result["subscriber"]["email"] == "tool@example.com"

    def test_sm_subscribe_tool_with_billing_cycle(self):
        sm.sm_create_plan_tool(name="Sub Plan 2", price_monthly=9.99)
        result_str = sm.sm_subscribe_tool(
            email="annual@example.com",
            name="Annual User",
            plan_slug="sub-plan-2",
            billing_cycle="annual",
        )
        result = json.loads(result_str)
        assert result["ok"] is True
        assert result["subscriber"]["billing_cycle"] == "annual"

    def test_sm_subscribe_tool_unknown_plan_returns_error(self):
        result_str = sm.sm_subscribe_tool(
            email="x@example.com", name="X", plan_slug="no-such-plan"
        )
        result = json.loads(result_str)
        assert result["ok"] is False
        assert "error" in result

    def test_sm_cancel_tool_success(self):
        sm.sm_create_plan_tool(name="Cancel Plan", price_monthly=9.99)
        sm.sm_subscribe_tool(
            email="cancel@example.com", name="Cancel Me", plan_slug="cancel-plan"
        )
        result_str = sm.sm_cancel_tool(email="cancel@example.com", immediately=True)
        result = json.loads(result_str)
        assert result["ok"] is True

    def test_sm_cancel_tool_unknown_returns_false(self):
        result_str = sm.sm_cancel_tool(email="ghost@example.com")
        result = json.loads(result_str)
        assert result["ok"] is False

    def test_sm_upgrade_tool_success(self):
        sm.sm_create_plan_tool(name="Basic", price_monthly=5.0)
        sm.sm_create_plan_tool(name="Premium", price_monthly=20.0)
        sm.sm_subscribe_tool(email="up@example.com", name="Up User", plan_slug="basic")
        result_str = sm.sm_upgrade_tool(email="up@example.com", new_plan_slug="premium")
        result = json.loads(result_str)
        assert result["ok"] is True
        assert result["subscriber"]["plan"]["slug"] == "premium"

    def test_sm_upgrade_tool_unknown_subscriber_returns_error(self):
        sm.sm_create_plan_tool(name="Premium2", price_monthly=20.0)
        result_str = sm.sm_upgrade_tool(
            email="nobody@example.com", new_plan_slug="premium2"
        )
        result = json.loads(result_str)
        assert result["ok"] is False

    def test_sm_mrr_tool_returns_values(self):
        result_str = sm.sm_mrr_tool()
        result = json.loads(result_str)
        assert result["ok"] is True
        assert "mrr" in result
        assert "arr" in result
        assert "total_active_subscribers" in result
        assert "total_subscribers" in result

    def test_sm_mrr_tool_mrr_correct(self):
        sm.sm_create_plan_tool(name="MRR Plan", price_monthly=15.0)
        sm.sm_subscribe_tool(
            email="mrr@example.com", name="MRR User", plan_slug="mrr-plan"
        )
        result = json.loads(sm.sm_mrr_tool())
        assert result["mrr"] == pytest.approx(15.0)

    def test_sm_plan_distribution_tool_success(self):
        sm.sm_create_plan_tool(name="Dist Plan", price_monthly=9.99)
        result_str = sm.sm_plan_distribution_tool()
        result = json.loads(result_str)
        assert result["ok"] is True
        assert "plans" in result

    def test_sm_subscription_summary_tool_success(self):
        result_str = sm.sm_subscription_summary_tool()
        result = json.loads(result_str)
        assert result["ok"] is True
        assert "mrr" in result
        assert "arr" in result

    def test_sm_churn_rate_tool_success(self):
        result_str = sm.sm_churn_rate_tool(days=30)
        result = json.loads(result_str)
        assert result["ok"] is True
        assert "churn_rate_percent" in result
        assert result["period_days"] == 30

    def test_sm_churn_rate_tool_custom_days(self):
        result_str = sm.sm_churn_rate_tool(days=90)
        result = json.loads(result_str)
        assert result["period_days"] == 90

    def test_sm_create_coupon_tool_success(self):
        result_str = sm.sm_create_coupon_tool(
            code="TOOL20", discount_type="percent", discount_value=20.0
        )
        result = json.loads(result_str)
        assert result["ok"] is True
        assert result["code"] == "TOOL20"

    def test_sm_create_coupon_tool_invalid_type_returns_error(self):
        result_str = sm.sm_create_coupon_tool(
            code="BAD", discount_type="bogus", discount_value=10.0
        )
        result = json.loads(result_str)
        assert result["ok"] is False

    def test_sm_create_coupon_tool_with_max_uses(self):
        result_str = sm.sm_create_coupon_tool(
            code="LIMITED2", discount_type="flat", discount_value=5.0, max_uses=3
        )
        result = json.loads(result_str)
        assert result["max_uses"] == 3

    def test_sm_subscribe_tool_with_coupon(self):
        sm.sm_create_plan_tool(name="Coupon Plan", price_monthly=10.0)
        sm.create_coupon("TOOLCOUPON", "percent", 50.0)
        result_str = sm.sm_subscribe_tool(
            email="coupon@example.com",
            name="Coupon User",
            plan_slug="coupon-plan",
            coupon_code="TOOLCOUPON",
        )
        result = json.loads(result_str)
        assert result["ok"] is True
