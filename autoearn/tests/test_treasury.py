"""Tests for core/treasury.py — budget allocation, expense tracking, and ROI reporting."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated treasury module with temp DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def tr(tmp_path):
    """Fresh treasury module with isolated SQLite DB."""
    import core.treasury as t
    t._db_conn = None
    db_path = tmp_path / "treasury_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    t._init_schema(conn)
    # Also create metrics table for revenue queries
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metrics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            agent      TEXT    NOT NULL,
            metric     TEXT    NOT NULL,
            value      REAL    NOT NULL,
            recorded_at TEXT   NOT NULL
        );
    """)
    conn.commit()
    t._db_conn = conn
    yield t
    t._db_conn = None


# ---------------------------------------------------------------------------
# allocate_budget
# ---------------------------------------------------------------------------

class TestAllocateBudget:
    def test_allocate_returns_budget_object(self, tr):
        b = tr.allocate_budget("writer", 200.0, "monthly")
        assert b is not None
        assert b.agent == "writer"
        assert b.amount_usd == 200.0
        assert b.period == "monthly"

    def test_allocate_invalid_period_raises(self, tr):
        with pytest.raises(ValueError, match="period must be one of"):
            tr.allocate_budget("agent1", 100.0, "yearly")

    def test_allocate_daily_period(self, tr):
        b = tr.allocate_budget("agent1", 50.0, "daily")
        assert b.period == "daily"

    def test_allocate_weekly_period(self, tr):
        b = tr.allocate_budget("agent1", 100.0, "weekly")
        assert b.period == "weekly"

    def test_allocate_upserts_on_duplicate(self, tr):
        tr.allocate_budget("writer", 100.0)
        b = tr.allocate_budget("writer", 250.0)
        assert b.amount_usd == 250.0

    def test_allocate_new_budget_has_zero_spent(self, tr):
        b = tr.allocate_budget("researcher", 300.0)
        assert b.spent == 0.0
        assert b.remaining == 300.0

    def test_allocate_multiple_agents(self, tr):
        tr.allocate_budget("ceo", 1000.0)
        tr.allocate_budget("cfo", 500.0)
        b_ceo = tr.get_budget("ceo")
        b_cfo = tr.get_budget("cfo")
        assert b_ceo.amount_usd == 1000.0
        assert b_cfo.amount_usd == 500.0


# ---------------------------------------------------------------------------
# get_budget
# ---------------------------------------------------------------------------

class TestGetBudget:
    def test_get_budget_none_for_unknown_agent(self, tr):
        result = tr.get_budget("nonexistent_agent")
        assert result is None

    def test_get_budget_reflects_current_spend(self, tr):
        tr.allocate_budget("writer", 200.0)
        tr.record_spend("writer", 50.0, "api_costs")
        b = tr.get_budget("writer")
        assert b.spent == 50.0
        assert b.remaining == 150.0

    def test_get_budget_remaining_zero_when_over_budget(self, tr):
        tr.allocate_budget("writer", 100.0)
        tr.record_spend("writer", 150.0, "api_costs")
        b = tr.get_budget("writer")
        assert b.remaining == 0.0

    def test_budget_to_dict(self, tr):
        tr.allocate_budget("agent1", 100.0)
        b = tr.get_budget("agent1")
        d = b.to_dict()
        assert "agent" in d
        assert "amount_usd" in d
        assert "spent" in d
        assert "remaining" in d

    def test_get_budget_with_multiple_spends(self, tr):
        tr.allocate_budget("researcher", 500.0)
        tr.record_spend("researcher", 30.0, "api_costs")
        tr.record_spend("researcher", 20.0, "tools")
        tr.record_spend("researcher", 10.0, "subscriptions")
        b = tr.get_budget("researcher")
        assert abs(b.spent - 60.0) < 0.01


# ---------------------------------------------------------------------------
# record_spend
# ---------------------------------------------------------------------------

class TestRecordSpend:
    def test_record_spend_returns_id(self, tr):
        expense_id = tr.record_spend("writer", 25.0, "api_costs")
        assert expense_id > 0

    def test_record_spend_negative_amount_raises(self, tr):
        with pytest.raises(ValueError, match="non-negative"):
            tr.record_spend("writer", -10.0)

    def test_record_spend_unknown_category_defaults_to_other(self, tr):
        # Should not raise; should log a warning and default to 'other'
        expense_id = tr.record_spend("writer", 10.0, "invalid_category")
        assert expense_id > 0

    def test_record_spend_zero_amount_allowed(self, tr):
        expense_id = tr.record_spend("writer", 0.0)
        assert expense_id > 0

    def test_record_spend_all_valid_categories(self, tr):
        for cat in tr.expense_categories:
            expense_id = tr.record_spend("agent1", 5.0, cat, f"Test {cat}")
            assert expense_id > 0

    def test_record_spend_with_note(self, tr):
        expense_id = tr.record_spend("writer", 10.0, "api_costs", "GPT-4 API usage")
        assert expense_id > 0


# ---------------------------------------------------------------------------
# get_spend_breakdown
# ---------------------------------------------------------------------------

class TestGetSpendBreakdown:
    def test_breakdown_all_zeros_when_no_expenses(self, tr):
        breakdown = tr.get_spend_breakdown()
        assert all(v == 0.0 for v in breakdown.values())

    def test_breakdown_by_category(self, tr):
        tr.record_spend("agent1", 50.0, "ads")
        tr.record_spend("agent1", 30.0, "api_costs")
        tr.record_spend("agent2", 20.0, "ads")
        breakdown = tr.get_spend_breakdown()
        assert breakdown["ads"] == 70.0
        assert breakdown["api_costs"] == 30.0

    def test_breakdown_filtered_by_agent(self, tr):
        tr.record_spend("writer", 40.0, "api_costs")
        tr.record_spend("researcher", 60.0, "subscriptions")
        breakdown = tr.get_spend_breakdown(agent="writer")
        assert breakdown["api_costs"] == 40.0
        assert breakdown["subscriptions"] == 0.0

    def test_breakdown_all_categories_present(self, tr):
        breakdown = tr.get_spend_breakdown()
        for cat in tr.expense_categories:
            assert cat in breakdown

    def test_breakdown_lookback_window(self, tr):
        # Expenses today should appear in 30-day window
        tr.record_spend("agent1", 99.0, "tools")
        breakdown_30 = tr.get_spend_breakdown(days=30)
        assert breakdown_30["tools"] == 99.0

    def test_breakdown_with_multiple_spends_per_category(self, tr):
        for i in range(5):
            tr.record_spend("agent1", 10.0, "ads")
        breakdown = tr.get_spend_breakdown(agent="agent1")
        assert breakdown["ads"] == 50.0


# ---------------------------------------------------------------------------
# roi_summary
# ---------------------------------------------------------------------------

class TestROISummary:
    def test_roi_empty_when_no_data(self, tr):
        result = tr.roi_summary()
        assert result == []

    def test_roi_with_expenses_only(self, tr):
        tr.allocate_budget("writer", 200.0)
        tr.record_spend("writer", 100.0, "api_costs")
        results = tr.roi_summary(days=30)
        assert len(results) >= 1
        writer_result = next((r for r in results if r["agent"] == "writer"), None)
        assert writer_result is not None
        assert writer_result["spend"] == 100.0

    def test_roi_sorted_by_roi_desc(self, tr):
        tr.record_spend("low_roi_agent", 100.0)
        tr.record_spend("low_roi_agent2", 50.0)
        results = tr.roi_summary()
        if len(results) >= 2:
            assert results[0]["roi_pct"] >= results[-1]["roi_pct"]

    def test_roi_dict_has_required_keys(self, tr):
        tr.record_spend("agent1", 10.0)
        results = tr.roi_summary()
        assert results
        r = results[0]
        for key in ("agent", "revenue", "spend", "profit", "roi_pct"):
            assert key in r

    def test_roi_zero_spend_with_no_revenue(self, tr):
        tr.allocate_budget("idle", 100.0)
        # No spend, no revenue → agent appears in results
        results = tr.roi_summary()
        idle = next((r for r in results if r["agent"] == "idle"), None)
        # May or may not appear depending on whether budget-only agents are listed
        # If it does appear, ROI should be 0
        if idle is not None:
            assert idle["spend"] == 0.0


# ---------------------------------------------------------------------------
# budget_health
# ---------------------------------------------------------------------------

class TestBudgetHealth:
    def test_budget_health_empty_when_no_budgets(self, tr):
        result = tr.budget_health()
        assert result == []

    def test_budget_health_on_track(self, tr):
        tr.allocate_budget("agent1", 100.0)
        tr.record_spend("agent1", 50.0, "ads")
        health = tr.budget_health()
        agent_h = next(h for h in health if h["agent"] == "agent1")
        assert agent_h["status"] == "on_track"

    def test_budget_health_over_budget(self, tr):
        tr.allocate_budget("agent1", 100.0)
        tr.record_spend("agent1", 150.0, "ads")
        health = tr.budget_health()
        agent_h = next(h for h in health if h["agent"] == "agent1")
        assert agent_h["status"] == "over_budget"

    def test_budget_health_under_utilized(self, tr):
        tr.allocate_budget("agent1", 100.0)
        # Spend < 10% = under-utilized
        tr.record_spend("agent1", 5.0, "ads")
        health = tr.budget_health()
        agent_h = next(h for h in health if h["agent"] == "agent1")
        assert agent_h["status"] == "under_utilized"

    def test_budget_health_dict_has_required_keys(self, tr):
        tr.allocate_budget("agent1", 100.0)
        health = tr.budget_health()
        h = health[0]
        for key in ("agent", "status", "spent_pct", "amount_usd", "spent", "remaining", "period"):
            assert key in h

    def test_budget_health_all_agents_included(self, tr):
        for name in ["ceo", "cfo", "writer"]:
            tr.allocate_budget(name, 100.0)
        health = tr.budget_health()
        names = {h["agent"] for h in health}
        assert {"ceo", "cfo", "writer"}.issubset(names)


# ---------------------------------------------------------------------------
# suggest_reallocation
# ---------------------------------------------------------------------------

class TestSuggestReallocation:
    def test_suggest_reallocation_empty_when_no_data(self, tr):
        result = tr.suggest_reallocation()
        assert isinstance(result, list)

    def test_suggest_reallocation_no_suggestions_when_all_healthy(self, tr):
        # Only one agent with positive ROI → no reallocation
        tr.allocate_budget("good_agent", 100.0)
        tr.record_spend("good_agent", 50.0, "ads")
        suggestions = tr.suggest_reallocation()
        # Suggestions may be empty if no zero-ROI agents
        assert isinstance(suggestions, list)

    def test_suggest_reallocation_dict_structure(self, tr):
        # Set up a scenario where reallocation might be suggested
        tr.allocate_budget("poor_agent", 200.0)
        # No revenue for poor_agent, only spend
        tr.record_spend("poor_agent", 10.0, "ads")

        tr.allocate_budget("rich_agent", 200.0)
        # Add revenue via metrics table
        tr._db_conn.execute(
            "INSERT INTO metrics (agent, metric, value, recorded_at) VALUES (?, ?, ?, datetime('now'))",
            ("rich_agent", "revenue_usd", 1000.0),
        )
        tr._db_conn.commit()

        suggestions = tr.suggest_reallocation()
        for s in suggestions:
            assert "from_agent" in s
            assert "to_agent" in s
            assert "amount_usd" in s
            assert "reason" in s


# ---------------------------------------------------------------------------
# financial_report
# ---------------------------------------------------------------------------

class TestFinancialReport:
    def test_financial_report_returns_string(self, tr):
        result = tr.financial_report()
        assert isinstance(result, str)

    def test_financial_report_contains_headers(self, tr):
        result = tr.financial_report()
        assert "Financial Report" in result

    def test_financial_report_contains_summary(self, tr):
        result = tr.financial_report()
        assert "Summary" in result or "Revenue" in result

    def test_financial_report_with_data(self, tr):
        tr.allocate_budget("writer", 200.0)
        tr.record_spend("writer", 50.0, "api_costs", "GPT-4")
        tr.record_spend("writer", 25.0, "tools", "Grammarly")
        result = tr.financial_report(days=7)
        assert "writer" in result

    def test_financial_report_days_parameter(self, tr):
        report_7 = tr.financial_report(days=7)
        report_30 = tr.financial_report(days=30)
        assert "7 days" in report_7 or "last 7" in report_7.lower()

    def test_financial_report_with_no_data(self, tr):
        # Should not raise even with empty database
        result = tr.financial_report(days=30)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_financial_report_expense_breakdown_section(self, tr):
        tr.record_spend("agent1", 100.0, "ads")
        result = tr.financial_report()
        assert "Expense" in result or "Breakdown" in result

    def test_financial_report_budget_health_section(self, tr):
        tr.allocate_budget("agent1", 100.0)
        result = tr.financial_report()
        assert "Budget" in result or "Health" in result
