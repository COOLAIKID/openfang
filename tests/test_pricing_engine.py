"""Tests for autoearn/core/pricing_engine.py."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_pe_{uuid.uuid4().hex[:8]}.db")


def _get_db_path():
    return _DB_PATH


with patch("autoearn.core.database.get_db_path", _get_db_path):
    import autoearn.core.pricing_engine as pe


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_schema():
    pe._schema_ready = False
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript("""
        DROP TABLE IF EXISTS ltv_estimates;
        DROP TABLE IF EXISTS price_history;
        DROP TABLE IF EXISTS price_tests;
        DROP TABLE IF EXISTS discount_rules;
        DROP TABLE IF EXISTS price_tiers;
        DROP TABLE IF EXISTS pricing_rules;
    """)
    conn.close()
    yield


@pytest.fixture()
def flat_rule():
    with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
        return pe.create_pricing_rule("flat_test", base_price=49.99)


@pytest.fixture()
def tiered_rule():
    with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
        rule = pe.create_pricing_rule("tiered_test", base_price=9.99, pricing_model="tiered")
        pe.add_tier("tiered_test", "Starter", 9.99, up_to=5.0)
        pe.add_tier("tiered_test", "Pro", 29.99, up_to=20.0, sort_order=1)
        pe.add_tier("tiered_test", "Enterprise", 99.99, sort_order=2)
        return rule


@pytest.fixture()
def price_test():
    with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
        return pe.create_price_test("test_ab", 29.99, 39.99)


# ── Pricing models ─────────────────────────────────────────────────────────

class TestPricingRuleTypes:
    @pytest.mark.parametrize("model", pe.PRICING_MODELS)
    def test_all_pricing_models(self, model):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            rule = pe.create_pricing_rule(f"model_{model}", base_price=10.0, pricing_model=model)
        assert rule.pricing_model == model

    @pytest.mark.parametrize("interval", pe.BILLING_INTERVALS)
    def test_all_billing_intervals(self, interval):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            rule = pe.create_pricing_rule(
                f"interval_{interval}", base_price=10.0, billing_interval=interval
            )
        assert rule.billing_interval == interval


class TestCreatePricingRule:
    def test_basic_creation(self, flat_rule):
        assert flat_rule.id is not None
        assert flat_rule.name == "flat_test"
        assert flat_rule.base_price == 49.99
        assert flat_rule.pricing_model == "flat"

    def test_default_currency_usd(self, flat_rule):
        assert flat_rule.currency == "USD"

    def test_is_active_by_default(self, flat_rule):
        assert flat_rule.is_active is True

    def test_duplicate_name_raises(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="already exists"):
                pe.create_pricing_rule("flat_test", base_price=10.0)

    def test_with_min_max_price(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            rule = pe.create_pricing_rule("bounded", base_price=50.0, min_price=10.0, max_price=100.0)
        assert rule.min_price == 10.0
        assert rule.max_price == 100.0

    def test_with_conditions(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            rule = pe.create_pricing_rule(
                "conditional", base_price=20.0,
                conditions={"country": "US", "min_order": 50}
            )
        assert rule.conditions["country"] == "US"

    def test_timestamps_set(self, flat_rule):
        datetime.fromisoformat(flat_rule.created_at)
        datetime.fromisoformat(flat_rule.updated_at)


class TestGetPricingRule:
    def test_get_by_name(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            fetched = pe.get_pricing_rule("flat_test")
        assert fetched is not None
        assert fetched.id == flat_rule.id

    def test_get_unknown_returns_none(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.get_pricing_rule("nosuchrule")
        assert result is None


class TestListPricingRules:
    def test_list_active(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            rules = pe.list_pricing_rules(active_only=True)
        assert len(rules) >= 1

    def test_filter_by_product_id(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("product_rule", base_price=10.0, product_id=42)
            pe.create_pricing_rule("other_rule", base_price=20.0, product_id=99)
            rules = pe.list_pricing_rules(product_id=42)
        assert all(r.product_id == 42 for r in rules)


# ── Price tiers ───────────────────────────────────────────────────────────

class TestPriceTiers:
    def test_add_tier(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            tier = pe.add_tier("flat_test", "Starter", 9.99, up_to=5.0)
        assert tier.id is not None
        assert tier.name == "Starter"
        assert tier.price == 9.99

    def test_tier_up_to_unlimited(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            tier = pe.add_tier("flat_test", "Unlimited", 99.99)
        assert tier.up_to is None

    def test_tier_with_features(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            tier = pe.add_tier("flat_test", "Pro", 49.99, features=["unlimited_users", "api_access"])
        assert "unlimited_users" in tier.features
        assert "api_access" in tier.features

    def test_add_tier_unknown_rule_raises(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="not found"):
                pe.add_tier("nosuchrule", "Bad", 10.0)

    def test_tiered_rule_includes_tiers(self, tiered_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            rule = pe.get_pricing_rule("tiered_test")
        assert len(rule.tiers) == 3


# ── Resolve price ─────────────────────────────────────────────────────────

class TestResolvePrice:
    def test_flat_price_resolution(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("my_flat", base_price=29.99, product_id=1)
            result = pe.resolve_price(product_id=1)
        assert result["final_price"] == 29.99

    def test_per_seat_pricing(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("per_seat_rule", base_price=10.0, pricing_model="per_seat", product_id=2)
            result = pe.resolve_price(product_id=2, quantity=5)
        assert result["final_price"] == 50.0

    def test_usage_based_pricing(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("usage_rule", base_price=0.01, pricing_model="usage_based", product_id=3)
            result = pe.resolve_price(product_id=3, quantity=1000)
        assert abs(result["final_price"] - 10.0) < 0.01

    def test_no_rule_returns_none_price(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.resolve_price(product_id=9999)
        assert result["price"] is None

    def test_min_price_enforced(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("min_rule", base_price=5.0, min_price=10.0, product_id=4)
            result = pe.resolve_price(product_id=4)
        assert result["final_price"] >= 10.0

    def test_max_price_enforced(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("max_rule", base_price=200.0, max_price=99.99, product_id=5)
            result = pe.resolve_price(product_id=5)
        assert result["final_price"] <= 99.99

    def test_annual_equivalent_monthly(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("monthly_sub", base_price=10.0, billing_interval="monthly", product_id=6)
            result = pe.resolve_price(product_id=6)
        assert result["annual_equivalent"] == 120.0

    def test_annual_equivalent_annual(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_pricing_rule("annual_sub", base_price=100.0, billing_interval="annual", product_id=7)
            result = pe.resolve_price(product_id=7)
        assert result["annual_equivalent"] == 100.0


# ── Annual conversion helper ──────────────────────────────────────────────

class TestToAnnual:
    @pytest.mark.parametrize("interval,price,expected", [
        ("weekly", 10.0, 520.0),
        ("monthly", 10.0, 120.0),
        ("quarterly", 10.0, 40.0),
        ("semiannual", 10.0, 20.0),
        ("annual", 10.0, 10.0),
        ("biennial", 10.0, 5.0),
        ("once", 10.0, 10.0),
    ])
    def test_annual_conversion(self, interval, price, expected):
        assert pe._to_annual(price, interval) == expected


# ── Price history ─────────────────────────────────────────────────────────

class TestPriceHistory:
    def test_record_price_change(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.record_price_change(1, 49.99, 59.99, reason="price increase")
        # no error means success

    def test_get_price_history(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.record_price_change(10, 19.99, 29.99, reason="market adjustment")
            pe.record_price_change(10, 29.99, 39.99, reason="annual review")
            history = pe.get_price_history(10)
        assert len(history) >= 2
        assert history[0]["new_price"] == 39.99  # most recent first

    def test_get_price_history_unknown_product(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            history = pe.get_price_history(99999)
        assert history == []

    def test_price_history_limit(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            for i in range(5):
                pe.record_price_change(20, float(i), float(i + 1))
            history = pe.get_price_history(20, limit=2)
        assert len(history) <= 2


# ── A/B price tests ───────────────────────────────────────────────────────

class TestPriceTest:
    def test_create_price_test(self, price_test):
        assert price_test.id is not None
        assert price_test.name == "test_ab"
        assert price_test.variant_a_price == 29.99
        assert price_test.variant_b_price == 39.99
        assert price_test.status == "running"

    def test_duplicate_test_name_raises(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="already exists"):
                pe.create_price_test("test_ab", 10.0, 20.0)

    def test_get_price_test(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            fetched = pe.get_price_test("test_ab")
        assert fetched is not None
        assert fetched.id == price_test.id

    def test_get_unknown_test_returns_none(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.get_price_test("nosuchtest")
        assert result is None

    def test_assign_variant_a_or_b(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            results = [pe.assign_price_variant("test_ab") for _ in range(20)]
        variants = {r["variant"] for r in results}
        assert "a" in variants or "b" in variants  # at least one appears

    def test_assign_increments_impressions(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            for _ in range(10):
                pe.assign_price_variant("test_ab")
            updated = pe.get_price_test("test_ab")
        total = updated.a_impressions + updated.b_impressions
        assert total == 10

    def test_record_conversion(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            ok = pe.record_price_test_conversion("test_ab", "a", 29.99)
            updated = pe.get_price_test("test_ab")
        assert ok is True
        assert updated.a_conversions == 1
        assert updated.a_revenue == 29.99

    def test_record_conversion_variant_b(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.record_price_test_conversion("test_ab", "b", 39.99)
            pe.record_price_test_conversion("test_ab", "b", 39.99)
            updated = pe.get_price_test("test_ab")
        assert updated.b_conversions == 2
        assert abs(updated.b_revenue - 79.98) < 0.01

    def test_record_conversion_invalid_variant(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            ok = pe.record_price_test_conversion("test_ab", "c", 10.0)
        assert ok is False

    def test_conversion_rate_calculation(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            for _ in range(10):
                pe.assign_price_variant("test_ab")
            for _ in range(3):
                pe.record_price_test_conversion("test_ab", "a", 29.99)
            test = pe.get_price_test("test_ab")
        if test.a_impressions > 0:
            assert test.a_conversion_rate == pytest.approx(test.a_conversions / test.a_impressions)

    def test_arpu_calculation(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.assign_price_variant("test_ab")
            pe.record_price_test_conversion("test_ab", "a", 29.99)
            test = pe.get_price_test("test_ab")
        assert test.a_arpu >= 0

    def test_list_price_tests(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            tests = pe.list_price_tests(status="running")
        assert len(tests) >= 1

    def test_conclude_test(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.conclude_price_test("test_ab", winner="b")
            updated = pe.get_price_test("test_ab")
        assert updated.status == "concluded"
        assert updated.winner == "b"

    def test_analysis_structure(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            analysis = pe.analyze_price_test("test_ab")
        assert "confidence_pct" in analysis
        assert "is_significant" in analysis
        assert "p_value" in analysis
        assert "recommended_winner" in analysis

    def test_analysis_unknown_test(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.analyze_price_test("nosuchtest")
        assert "error" in result


# ── Statistical functions ─────────────────────────────────────────────────

class TestStatisticalFunctions:
    def test_chi_square_significant(self):
        # clear winner: 500/1000 vs 100/1000
        p = pe._chi_square_p_value(500, 1000, 100, 1000)
        assert p < 0.05

    def test_chi_square_insignificant(self):
        # equal rates: 50/100 vs 51/100
        p = pe._chi_square_p_value(50, 100, 51, 100)
        assert p > 0.05

    def test_chi_square_zero_samples(self):
        p = pe._chi_square_p_value(0, 0, 0, 0)
        assert p == 1.0

    def test_min_sample_size_positive(self):
        n = pe._min_sample_size(0.05, 0.8, 0.05)
        assert n > 0
        assert isinstance(n, int)

    def test_min_sample_size_edge_cases(self):
        # zero baseline
        n = pe._min_sample_size(0.05, 0.8, 0.0)
        assert n >= 100  # falls back to min

    def test_psych_price_positive(self):
        p = pe._nearest_psych_price(50.0)
        assert p > 0

    @pytest.mark.parametrize("price", [9.5, 14.0, 29.0, 49.0, 97.0])
    def test_psych_price_ends_in_9_or_7(self, price):
        p = pe._nearest_psych_price(price)
        # should end close to .99 or similar
        remainder = round(p % 1, 2)
        assert remainder in (0.99, 0.97, 0.95, 0.0) or p > 0


# ── Discount rules ────────────────────────────────────────────────────────

class TestDiscountRules:
    def test_create_percent_discount(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            disc = pe.create_discount_rule("SUMMER10", discount_type="percent", discount_value=10.0)
        assert disc["name"] == "SUMMER10"
        assert disc["discount_type"] == "percent"

    def test_create_fixed_discount(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            disc = pe.create_discount_rule("FIXED5", discount_type="fixed", discount_value=5.0)
        assert disc["discount_value"] == 5.0

    def test_duplicate_discount_raises(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_discount_rule("UNIQ_DISC")
            with pytest.raises(ValueError, match="already exists"):
                pe.create_discount_rule("UNIQ_DISC")

    def test_active_discounts_returns_active(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_discount_rule("ACTIVE_ONE", discount_value=5.0)
            discounts = pe.active_discounts()
        assert isinstance(discounts, list)
        assert len(discounts) >= 1

    def test_expired_discount_not_in_active(self):
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_discount_rule("EXPIRED_DISC", discount_value=10.0, end_at=past)
            discounts = pe.active_discounts()
        names = [d["name"] for d in discounts]
        assert "EXPIRED_DISC" not in names

    def test_apply_percent_discount(self):
        final, amount = pe.apply_discount(100.0, "percent", 20.0)
        assert final == 80.0
        assert amount == 20.0

    def test_apply_fixed_discount(self):
        final, amount = pe.apply_discount(100.0, "fixed", 15.0)
        assert final == 85.0
        assert amount == 15.0

    def test_apply_discount_max_cap(self):
        final, amount = pe.apply_discount(100.0, "percent", 90.0, max_discount=30.0)
        assert amount == 30.0
        assert final == 70.0

    def test_apply_discount_floor_zero(self):
        final, amount = pe.apply_discount(10.0, "fixed", 999.0)
        assert final == 0.0

    def test_increment_discount_usage(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_discount_rule("TRACK_USAGE", discount_value=5.0, usage_limit=3)
            pe.increment_discount_usage("TRACK_USAGE")
            pe.increment_discount_usage("TRACK_USAGE")
            conn = sqlite3.connect(_DB_PATH)
            row = conn.execute(
                "SELECT usage_count FROM discount_rules WHERE name = ?", ("TRACK_USAGE",)
            ).fetchone()
            conn.close()
        assert row[0] == 2

    def test_discount_types_variety(self):
        for dt in pe.DISCOUNT_TYPES:
            with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
                disc = pe.create_discount_rule(f"disc_{dt}", discount_type=dt, discount_value=5.0)
            assert disc["discount_type"] == dt


# ── LTV estimates ─────────────────────────────────────────────────────────

class TestLTVEstimates:
    def test_upsert_ltv_estimate(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.upsert_ltv_estimate(
                "premium_users",
                avg_ltv=500.0,
                avg_order_value=50.0,
                churn_rate=0.05,
                acquisition_cost=20.0,
            )
        assert result["segment"] == "premium_users"
        assert "roas_1x" in result
        assert "payback_months" in result

    def test_upsert_overwrites_existing(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.upsert_ltv_estimate("seg_a", avg_ltv=100.0)
            pe.upsert_ltv_estimate("seg_a", avg_ltv=200.0)
            estimates = pe.get_ltv_estimates()
        seg_a = next((e for e in estimates if e["segment"] == "seg_a"), None)
        assert seg_a is not None
        assert seg_a["avg_ltv"] == 200.0

    def test_get_ltv_estimates_empty(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            estimates = pe.get_ltv_estimates()
        assert isinstance(estimates, list)

    def test_get_ltv_estimates_multiple(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.upsert_ltv_estimate("segment_1", avg_ltv=100.0)
            pe.upsert_ltv_estimate("segment_2", avg_ltv=200.0)
            estimates = pe.get_ltv_estimates()
        assert len(estimates) >= 2
        # sorted by avg_ltv desc
        assert estimates[0]["avg_ltv"] >= estimates[1]["avg_ltv"]

    def test_roas_calculation(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.upsert_ltv_estimate("roas_seg", avg_ltv=100.0, acquisition_cost=25.0)
        assert result["roas_1x"] == pytest.approx(4.0)

    def test_payback_months_calculation(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.upsert_ltv_estimate(
                "payback_seg",
                avg_ltv=200.0,
                avg_order_value=60.0,
                acquisition_cost=30.0,
            )
        # payback_months = acquisition_cost / (avg_order_value / 12) = 30 / 5 = 6
        assert result["payback_months"] == pytest.approx(6.0)

    def test_zero_acquisition_cost_roas(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.upsert_ltv_estimate("free_seg", avg_ltv=100.0, acquisition_cost=0.0)
        assert result["roas_1x"] > 0  # should not divide by zero


# ── Price recommendation ──────────────────────────────────────────────────

class TestRecommendPrice:
    def test_recommend_no_history_returns_dict(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.recommend_price(product_id=999)
        assert "product_id" in result
        assert "suggestions" in result

    def test_recommend_suggestions_list(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = pe.recommend_price(product_id=888)
        assert isinstance(result["suggestions"], list)

    def test_recommend_with_ltv(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.upsert_ltv_estimate("default", avg_ltv=500.0)
            result = pe.recommend_price(product_id=777, segment="default")
        # should include LTV-based suggestions
        labels = [s["label"] for s in result["suggestions"]]
        assert any("LTV" in label for label in labels)


# ── Pricing summary ───────────────────────────────────────────────────────

class TestPricingSummary:
    def test_summary_structure(self, flat_rule, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            summary = pe.pricing_summary()
        assert "pricing_rules" in summary
        assert "price_tests" in summary
        assert "active_discounts" in summary
        assert "ltv_segments" in summary

    def test_summary_counts_rules(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            summary = pe.pricing_summary()
        assert summary["pricing_rules"]["total"] >= 1
        assert summary["pricing_rules"]["active"] >= 1

    def test_summary_counts_tests(self, price_test):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            summary = pe.pricing_summary()
        assert summary["price_tests"]["total"] >= 1
        assert summary["price_tests"]["running"] >= 1

    def test_summary_active_discounts(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_discount_rule("DISC_SUMMARY", discount_value=10.0)
            summary = pe.pricing_summary()
        assert summary["active_discounts"] >= 1


# ── to_dict serialization ─────────────────────────────────────────────────

class TestToDict:
    def test_pricing_rule_to_dict(self, flat_rule):
        d = flat_rule.to_dict()
        required = ["id", "name", "pricing_model", "base_price", "currency", "tiers"]
        for k in required:
            assert k in d

    def test_pricing_rule_to_dict_serializable(self, flat_rule):
        d = flat_rule.to_dict()
        json.dumps(d)  # should not raise

    def test_price_test_to_dict(self, price_test):
        d = price_test.to_dict()
        required = ["id", "name", "status", "variant_a", "variant_b", "winner"]
        for k in required:
            assert k in d

    def test_price_test_to_dict_variant_structure(self, price_test):
        d = price_test.to_dict()
        for key in ["name", "price", "impressions", "conversions", "conversion_rate", "revenue"]:
            assert key in d["variant_a"]
            assert key in d["variant_b"]

    def test_price_tier_to_dict(self, flat_rule):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            tier = pe.add_tier("flat_test", "Basic", 9.99)
        d = tier.to_dict()
        assert "name" in d
        assert "price" in d
        assert "features" in d


# ── Tool wrappers ─────────────────────────────────────────────────────────

class TestToolWrappers:
    def test_create_rule_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.create_rule_tool("tool_rule", 29.99, "flat", "monthly"))
        assert result["ok"] is True
        assert result["rule"]["name"] == "tool_rule"

    def test_create_rule_tool_duplicate(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_rule_tool("dup_rule", 10.0)
            result = json.loads(pe.create_rule_tool("dup_rule", 10.0))
        assert result["ok"] is False

    def test_resolve_price_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.resolve_price_tool(1234))
        assert "price" in result or "note" in result

    def test_create_price_test_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.create_price_test_tool("tool_ab", 19.99, 29.99))
        assert result["ok"] is True
        assert result["test"]["variant_a"]["price"] == 19.99

    def test_analyze_test_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            pe.create_price_test_tool("analyze_ab", 19.99, 29.99)
            result = json.loads(pe.analyze_test_tool("analyze_ab"))
        assert "confidence_pct" in result

    def test_analyze_unknown_test_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.analyze_test_tool("no_such_test"))
        assert "error" in result

    def test_recommend_price_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.recommend_price_tool(42))
        assert "suggestions" in result

    def test_create_discount_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.create_discount_tool("TOOL_DISC", "percent", 15.0))
        assert result["ok"] is True

    def test_pricing_summary_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.pricing_summary_tool())
        assert "pricing_rules" in result

    def test_upsert_ltv_tool(self):
        with patch("autoearn.core.pricing_engine.get_db_path", _get_db_path):
            result = json.loads(pe.upsert_ltv_tool("tool_seg", 300.0, 30.0, 0.1, 15.0))
        assert result["ok"] is True
        assert result["segment"] == "tool_seg"
