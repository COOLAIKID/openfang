"""Tests for core/toolkit/pricing.py — pricing strategy toolkit."""
from __future__ import annotations

import json
import math

import pytest

from core.toolkit import pricing


def _parse(result: str) -> dict:
    return json.loads(result)


# ---------------------------------------------------------------------------
# competitive_pricing
# ---------------------------------------------------------------------------

class TestCompetitivePricing:
    def _competitors(self):
        return [
            {"name": "CompA", "price": 49.99},
            {"name": "CompB", "price": 59.99},
            {"name": "CompC", "price": 69.99},
            {"name": "CompD", "price": 79.99},
        ]

    def test_returns_valid_json(self):
        result = pricing.competitive_pricing("Widget", 59.99, self._competitors())
        data = _parse(result)
        assert "product" in data

    def test_no_competitors_returns_error(self):
        result = _parse(pricing.competitive_pricing("Widget", 50.0, []))
        assert "error" in result

    def test_market_stats_computed(self):
        data = _parse(pricing.competitive_pricing("Widget", 59.99, self._competitors()))
        market = data["market"]
        assert "avg" in market
        assert "min" in market
        assert "max" in market
        assert "median" in market

    def test_correct_min_max(self):
        data = _parse(pricing.competitive_pricing("Widget", 60.0, self._competitors()))
        assert data["market"]["min"] == 49.99
        assert data["market"]["max"] == 79.99

    def test_competitive_position_identified(self):
        # Price at average → "competitive"
        avg = (49.99 + 59.99 + 69.99 + 79.99) / 4
        data = _parse(pricing.competitive_pricing("Widget", round(avg, 2), self._competitors()))
        assert data["position"] in ("competitive", "above_average", "below_average")

    def test_premium_outlier_flagged(self):
        data = _parse(pricing.competitive_pricing("Widget", 200.0, self._competitors()))
        assert data["position"] == "premium_outlier"

    def test_underpriced_flagged(self):
        data = _parse(pricing.competitive_pricing("Widget", 5.0, self._competitors()))
        assert data["position"] == "underpriced"

    def test_recommendation_present(self):
        data = _parse(pricing.competitive_pricing("Widget", 60.0, self._competitors()))
        assert isinstance(data["recommendation"], str)
        assert len(data["recommendation"]) > 10

    def test_suggested_price_positive(self):
        data = _parse(pricing.competitive_pricing("Widget", 60.0, self._competitors()))
        assert data["suggested_price"] > 0

    def test_invalid_competitor_prices_filtered(self):
        competitors = [
            {"name": "CompA", "price": 50.0},
            {"name": "CompB", "price": "not_a_price"},  # invalid
        ]
        data = _parse(pricing.competitive_pricing("Widget", 50.0, competitors))
        assert data["market"]["min"] == 50.0

    def test_no_valid_prices_returns_error(self):
        competitors = [{"name": "CompA", "price": "bad"}]
        data = _parse(pricing.competitive_pricing("Widget", 50.0, competitors))
        assert "error" in data


# ---------------------------------------------------------------------------
# psychological_pricing
# ---------------------------------------------------------------------------

class TestPsychologicalPricing:
    def test_returns_valid_json(self):
        result = pricing.psychological_pricing(49.95)
        data = _parse(result)
        assert "suggestions" in data

    def test_target_price_in_result(self):
        data = _parse(pricing.psychological_pricing(99.50))
        assert data["target_price"] == 99.50

    def test_suggestions_non_empty(self):
        data = _parse(pricing.psychological_pricing(49.95))
        assert len(data["suggestions"]) > 0

    def test_all_suggestions_positive(self):
        data = _parse(pricing.psychological_pricing(29.99))
        for s in data["suggestions"]:
            assert s["price"] > 0

    def test_charm_pricing_included(self):
        data = _parse(pricing.psychological_pricing(50.0))
        strategies = {s["strategy"] for s in data["suggestions"]}
        assert "charm_pricing" in strategies

    def test_prestige_pricing_included(self):
        data = _parse(pricing.psychological_pricing(99.0))
        strategies = {s["strategy"] for s in data["suggestions"]}
        assert "prestige_round" in strategies

    def test_suggestions_sorted_by_delta(self):
        data = _parse(pricing.psychological_pricing(49.99))
        deltas = [abs(s["delta_from_target"]) for s in data["suggestions"]]
        assert deltas == sorted(deltas)

    def test_rationale_present(self):
        data = _parse(pricing.psychological_pricing(29.99))
        for s in data["suggestions"]:
            assert "rationale" in s
            assert len(s["rationale"]) > 5


# ---------------------------------------------------------------------------
# price_elasticity
# ---------------------------------------------------------------------------

class TestPriceElasticity:
    def test_elastic_demand(self):
        # Price goes up 10%, quantity drops 20% → elastic
        prices = [10.0, 11.0]
        quantities = [100.0, 80.0]
        data = _parse(pricing.price_elasticity(prices, quantities))
        assert data["interpretation"] == "elastic"
        assert data["avg_elasticity"] < -1

    def test_inelastic_demand(self):
        # Price goes up 10%, quantity drops 2% → inelastic
        prices = [10.0, 11.0]
        quantities = [100.0, 98.0]
        data = _parse(pricing.price_elasticity(prices, quantities))
        assert data["interpretation"] == "inelastic"

    def test_unequal_lengths_error(self):
        data = _parse(pricing.price_elasticity([10.0], [100.0, 90.0]))
        assert "error" in data

    def test_too_few_points_error(self):
        data = _parse(pricing.price_elasticity([10.0], [100.0]))
        assert "error" in data

    def test_returns_periods(self):
        prices = [10.0, 12.0, 11.0]
        quantities = [100.0, 80.0, 90.0]
        data = _parse(pricing.price_elasticity(prices, quantities))
        assert "periods" in data
        assert len(data["periods"]) >= 1

    def test_no_price_change_skipped(self):
        prices = [10.0, 10.0, 12.0]
        quantities = [100.0, 95.0, 80.0]
        data = _parse(pricing.price_elasticity(prices, quantities))
        # Only the period with a price change should be included
        assert "avg_elasticity" in data

    def test_advice_present(self):
        prices = [10.0, 12.0]
        quantities = [100.0, 80.0]
        data = _parse(pricing.price_elasticity(prices, quantities))
        assert "advice" in data


# ---------------------------------------------------------------------------
# value_based_pricing
# ---------------------------------------------------------------------------

class TestValueBasedPricing:
    def test_returns_valid_json(self):
        result = pricing.value_based_pricing(10.0, 0.6, 50.0, 0.5)
        data = _parse(result)
        assert "recommended_price" in data

    def test_invalid_margin_returns_error(self):
        data = _parse(pricing.value_based_pricing(10.0, 1.5, 50.0, 0.5))
        assert "error" in data

    def test_negative_margin_returns_error(self):
        data = _parse(pricing.value_based_pricing(10.0, -0.1, 50.0, 0.5))
        assert "error" in data

    def test_recommended_price_above_cost(self):
        data = _parse(pricing.value_based_pricing(10.0, 0.6, 50.0, 0.5))
        assert data["recommended_price"] > 10.0

    def test_higher_unique_value_increases_price(self):
        low_val = _parse(pricing.value_based_pricing(10.0, 0.6, 50.0, 0.0))
        high_val = _parse(pricing.value_based_pricing(10.0, 0.6, 50.0, 1.0))
        assert high_val["recommended_price"] > low_val["recommended_price"]

    def test_achieved_margin_between_0_and_1(self):
        data = _parse(pricing.value_based_pricing(20.0, 0.5, 100.0, 0.3))
        assert 0.0 <= data["achieved_margin"] <= 1.0

    def test_cost_plus_price_computed(self):
        # cost=10, margin=0.5 → cost_plus = 10 / 0.5 = 20
        data = _parse(pricing.value_based_pricing(10.0, 0.5, 100.0, 0.0))
        assert abs(data["cost_plus_price"] - 20.0) < 0.01


# ---------------------------------------------------------------------------
# lifetime_value
# ---------------------------------------------------------------------------

class TestLifetimeValue:
    def test_basic_ltv(self):
        # MRR=100, churn=0.05, margin=0.70 → LTV = 100*0.70/0.05 = 1400
        data = _parse(pricing.lifetime_value(100.0, 0.05, 0.70))
        assert abs(data["ltv"] - 1400.0) < 0.01

    def test_zero_churn_returns_error(self):
        data = _parse(pricing.lifetime_value(100.0, 0.0, 0.70))
        assert "error" in data

    def test_invalid_margin_returns_error(self):
        data = _parse(pricing.lifetime_value(100.0, 0.05, 1.5))
        assert "error" in data

    def test_negative_margin_returns_error(self):
        data = _parse(pricing.lifetime_value(100.0, 0.05, -0.1))
        assert "error" in data

    def test_avg_lifespan_computed(self):
        # churn=0.1 → lifespan = 10 months
        data = _parse(pricing.lifetime_value(50.0, 0.1, 0.8))
        assert abs(data["avg_lifespan_months"] - 10.0) < 0.01

    def test_returns_required_fields(self):
        data = _parse(pricing.lifetime_value(100.0, 0.05, 0.70))
        for key in ("ltv", "avg_lifespan_months", "avg_lifespan_years", "inputs", "ltv_cac_guidance"):
            assert key in data

    def test_ltv_increases_with_lower_churn(self):
        high_churn = _parse(pricing.lifetime_value(100.0, 0.2, 0.7))
        low_churn = _parse(pricing.lifetime_value(100.0, 0.05, 0.7))
        assert low_churn["ltv"] > high_churn["ltv"]


# ---------------------------------------------------------------------------
# break_even_analysis
# ---------------------------------------------------------------------------

class TestBreakEvenAnalysis:
    def test_basic_break_even(self):
        # fixed=1000, var=10, price=20 → cm=10, BEU=100
        data = _parse(pricing.break_even_analysis(1000.0, 10.0, 20.0))
        assert abs(data["break_even_units"] - 100.0) < 0.01

    def test_break_even_revenue(self):
        # BEU=100, price=20 → BER=2000
        data = _parse(pricing.break_even_analysis(1000.0, 10.0, 20.0))
        assert abs(data["break_even_revenue"] - 2000.0) < 0.01

    def test_price_below_variable_cost_returns_error(self):
        data = _parse(pricing.break_even_analysis(1000.0, 30.0, 20.0))
        assert "error" in data

    def test_equal_price_and_var_cost_returns_error(self):
        data = _parse(pricing.break_even_analysis(1000.0, 20.0, 20.0))
        assert "error" in data

    def test_sensitivity_analysis_present(self):
        data = _parse(pricing.break_even_analysis(1000.0, 10.0, 30.0))
        assert "sensitivity" in data

    def test_contribution_margin_ratio(self):
        # CM=10, price=20 → CMR=0.5
        data = _parse(pricing.break_even_analysis(1000.0, 10.0, 20.0))
        assert abs(data["contribution_margin_ratio"] - 0.5) < 0.001

    def test_returns_required_fields(self):
        data = _parse(pricing.break_even_analysis(500.0, 5.0, 15.0))
        for key in ("break_even_units", "break_even_revenue", "contribution_margin", "inputs"):
            assert key in data


# ---------------------------------------------------------------------------
# subscription_metrics
# ---------------------------------------------------------------------------

class TestSubscriptionMetrics:
    def test_basic_metrics(self):
        data = _parse(pricing.subscription_metrics(
            mrr=10000.0, new_mrr=2000.0, churned_mrr=500.0, expansion_mrr=500.0
        ))
        assert "mrr" in data
        assert data["mrr"] == 10000.0

    def test_zero_mrr_returns_error(self):
        data = _parse(pricing.subscription_metrics(0.0, 1000.0, 100.0, 200.0))
        assert "error" in data

    def test_net_new_mrr(self):
        # net_new = 2000 + 500 - 500 = 2000
        data = _parse(pricing.subscription_metrics(10000.0, 2000.0, 500.0, 500.0))
        assert abs(data["net_new_mrr"] - 2000.0) < 0.01

    def test_arr_is_12x_eom_mrr(self):
        data = _parse(pricing.subscription_metrics(10000.0, 2000.0, 500.0, 500.0))
        eom_mrr = 10000.0 + 2000.0
        expected_arr = eom_mrr * 12
        assert abs(data["arr_projection"] - expected_arr) < 0.01

    def test_quick_ratio_computed(self):
        # (new + expansion) / churned = (2000+500)/500 = 5.0
        data = _parse(pricing.subscription_metrics(10000.0, 2000.0, 500.0, 500.0))
        assert abs(data["quick_ratio"] - 5.0) < 0.01

    def test_no_churn_quick_ratio_is_inf(self):
        data = _parse(pricing.subscription_metrics(10000.0, 2000.0, 0.0, 500.0))
        assert data["quick_ratio"] == "inf"

    def test_health_excellent_with_high_quick_ratio(self):
        data = _parse(pricing.subscription_metrics(10000.0, 5000.0, 100.0, 500.0))
        assert data["health"] == "excellent"

    def test_health_at_risk_with_low_quick_ratio(self):
        # (new + expansion) < churned → quick_ratio < 1 → at_risk
        data = _parse(pricing.subscription_metrics(10000.0, 100.0, 5000.0, 100.0))
        assert data["health"] == "at_risk"


# ---------------------------------------------------------------------------
# anchor_pricing
# ---------------------------------------------------------------------------

class TestAnchorPricing:
    def test_basic_anchor(self):
        data = _parse(pricing.anchor_pricing(299.0, 99.0))
        assert data["anchor_price"] == 299.0
        assert data["target_price"] == 99.0

    def test_premium_not_greater_than_target_returns_error(self):
        data = _parse(pricing.anchor_pricing(99.0, 99.0))
        assert "error" in data

    def test_premium_less_than_target_returns_error(self):
        data = _parse(pricing.anchor_pricing(50.0, 100.0))
        assert "error" in data

    def test_savings_computed(self):
        data = _parse(pricing.anchor_pricing(200.0, 80.0))
        assert abs(data["savings"] - 120.0) < 0.01

    def test_savings_pct_computed(self):
        data = _parse(pricing.anchor_pricing(200.0, 100.0))
        assert abs(data["savings_pct"] - 50.0) < 0.01

    def test_anchor_strength_strong(self):
        # ratio ~2x → strong
        data = _parse(pricing.anchor_pricing(200.0, 99.0))
        assert data["anchor_strength"] in ("strong", "very_strong")

    def test_anchor_strength_weak_close_prices(self):
        # ratio 1.1 → weak
        data = _parse(pricing.anchor_pricing(110.0, 100.0))
        assert data["anchor_strength"] == "weak"

    def test_copy_suggestions_present(self):
        data = _parse(pricing.anchor_pricing(300.0, 99.0))
        assert "copy_suggestions" in data
        assert len(data["copy_suggestions"]) > 0

    def test_display_order_has_three_positions(self):
        data = _parse(pricing.anchor_pricing(300.0, 99.0))
        assert len(data["recommended_display_order"]) == 3

    def test_returns_strategy_string(self):
        data = _parse(pricing.anchor_pricing(300.0, 99.0))
        assert "strategy" in data
        assert isinstance(data["strategy"], str)


# ---------------------------------------------------------------------------
# discount_strategy
# ---------------------------------------------------------------------------

class TestDiscountStrategy:
    def test_fresh_inventory_no_discount(self):
        # < 30 days old + target_sellthrough < 0.60 → 0% base + (-5%) adder → 0% (clamped)
        data = _parse(pricing.discount_strategy(100.0, 15, 0.4))
        assert data["recommended_discount_pct"] == 0.0

    def test_old_inventory_higher_discount(self):
        fresh = _parse(pricing.discount_strategy(100.0, 10, 0.8))
        old = _parse(pricing.discount_strategy(100.0, 200, 0.8))
        assert old["recommended_discount_pct"] > fresh["recommended_discount_pct"]

    def test_discount_capped_at_70_pct(self):
        data = _parse(pricing.discount_strategy(100.0, 999, 1.0))
        assert data["recommended_discount_pct"] <= 70.0

    def test_discounted_price_less_than_original(self):
        data = _parse(pricing.discount_strategy(200.0, 120, 0.9))
        assert data["discounted_price"] < data["original_price"]

    def test_returns_required_fields(self):
        data = _parse(pricing.discount_strategy(100.0, 45, 0.8))
        for key in ("original_price", "recommended_discount_pct", "discounted_price", "savings"):
            assert key in data

    def test_high_sellthrough_target_increases_discount(self):
        low_target = _parse(pricing.discount_strategy(100.0, 60, 0.5))
        high_target = _parse(pricing.discount_strategy(100.0, 60, 0.95))
        assert high_target["recommended_discount_pct"] >= low_target["recommended_discount_pct"]


# ---------------------------------------------------------------------------
# freemium_conversion_estimate
# ---------------------------------------------------------------------------

class TestFreemiumConversionEstimate:
    def test_returns_valid_json(self):
        result = pricing.freemium_conversion_estimate(1000, 0.5, 0.3)
        data = _parse(result)
        assert "estimated_conversion_rate" in data

    def test_rate_in_reasonable_range(self):
        data = _parse(pricing.freemium_conversion_estimate(5000, 0.7, 0.4))
        assert 0.0 < data["estimated_conversion_rate"] <= 0.25

    def test_expected_paid_computed(self):
        data = _parse(pricing.freemium_conversion_estimate(1000, 0.5, 0.3))
        expected = int(1000 * data["estimated_conversion_rate"])
        assert data["expected_paid_users"] == expected

    def test_higher_gate_score_higher_conversion(self):
        low = _parse(pricing.freemium_conversion_estimate(1000, 0.0, 0.3))
        high = _parse(pricing.freemium_conversion_estimate(1000, 1.0, 0.3))
        assert high["estimated_conversion_rate"] > low["estimated_conversion_rate"]

    def test_advice_present(self):
        data = _parse(pricing.freemium_conversion_estimate(500, 0.5, 0.5))
        assert "advice" in data
        assert isinstance(data["advice"], str)
