"""Tests for core/monitoring.py — org-wide agent health monitoring."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated monitoring module with temp DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def mon(tmp_path):
    """Fresh monitoring module with isolated SQLite DB."""
    import core.monitoring as m
    m._db_conn = None
    db_path = tmp_path / "mon_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    m._init_schema(conn)
    # Also need agent_status table (normally in core.database)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_status (
            agent_name   TEXT PRIMARY KEY,
            total_runs   INTEGER NOT NULL DEFAULT 0,
            total_errors INTEGER NOT NULL DEFAULT 0,
            last_run_at  TEXT,
            last_result  TEXT,
            status       TEXT NOT NULL DEFAULT 'active'
        );
    """)
    conn.commit()
    m._db_conn = conn
    yield m
    m._db_conn = None


def _insert_agent(mon, name: str, runs: int, errors: int, last_run_offset_minutes: float = 10.0):
    """Helper: insert an agent_status row."""
    last_run_at = (
        datetime.now(timezone.utc) - timedelta(minutes=last_run_offset_minutes)
    ).isoformat()
    mon._db_conn.execute(
        """
        INSERT OR REPLACE INTO agent_status
            (agent_name, total_runs, total_errors, last_run_at, last_result, status)
        VALUES (?, ?, ?, ?, 'ok', 'active')
        """,
        (name, runs, errors, last_run_at),
    )
    mon._db_conn.commit()


# ---------------------------------------------------------------------------
# _compute_status
# ---------------------------------------------------------------------------

class TestComputeStatus:
    def test_healthy_low_error_recent(self, mon):
        assert mon._compute_status(0.0, 5.0) == "healthy"

    def test_degraded_high_error_rate(self, mon):
        assert mon._compute_status(0.6, 10.0) == "degraded"

    def test_degraded_old_run(self, mon):
        # 130 minutes ago → degraded
        assert mon._compute_status(0.0, 130.0) == "degraded"

    def test_dead_very_old_run(self, mon):
        assert mon._compute_status(0.0, 300.0) == "dead"

    def test_dead_at_boundary(self, mon):
        assert mon._compute_status(0.0, 241.0) == "dead"


# ---------------------------------------------------------------------------
# check_all
# ---------------------------------------------------------------------------

class TestCheckAll:
    def test_check_all_empty_returns_empty_list(self, mon):
        result = mon.check_all()
        assert result == []

    def test_check_all_returns_agent_health_objects(self, mon):
        _insert_agent(mon, "ceo", runs=10, errors=1, last_run_offset_minutes=5)
        agents = mon.check_all()
        assert len(agents) == 1
        ah = agents[0]
        assert ah.name == "ceo"
        assert ah.runs == 10
        assert ah.errors == 1

    def test_check_all_computes_error_rate(self, mon):
        _insert_agent(mon, "writer", runs=10, errors=2, last_run_offset_minutes=5)
        agents = mon.check_all()
        assert abs(agents[0].error_rate - 0.2) < 0.001

    def test_check_all_zero_runs_gives_zero_error_rate(self, mon):
        _insert_agent(mon, "idle_agent", runs=0, errors=0, last_run_offset_minutes=5)
        agents = mon.check_all()
        assert agents[0].error_rate == 0.0

    def test_check_all_status_healthy(self, mon):
        _insert_agent(mon, "good_agent", runs=100, errors=0, last_run_offset_minutes=5)
        agents = mon.check_all()
        assert agents[0].status == "healthy"

    def test_check_all_status_dead_for_old_agent(self, mon):
        _insert_agent(mon, "dead_agent", runs=5, errors=0, last_run_offset_minutes=300)
        agents = mon.check_all()
        assert agents[0].status == "dead"

    def test_check_all_multiple_agents(self, mon):
        for name in ["ceo", "writer", "researcher"]:
            _insert_agent(mon, name, runs=20, errors=1, last_run_offset_minutes=10)
        agents = mon.check_all()
        assert len(agents) == 3


# ---------------------------------------------------------------------------
# org_health_score
# ---------------------------------------------------------------------------

class TestOrgHealthScore:
    def test_score_zero_with_no_agents(self, mon):
        assert mon.org_health_score() == 0

    def test_score_between_0_and_100(self, mon):
        for i in range(5):
            _insert_agent(mon, f"agent{i}", runs=50, errors=i, last_run_offset_minutes=10)
        score = mon.org_health_score()
        assert 0 <= score <= 100

    def test_all_healthy_agents_gives_high_score(self, mon):
        for i in range(5):
            _insert_agent(mon, f"agent{i}", runs=100, errors=0, last_run_offset_minutes=5)
        score = mon.org_health_score()
        assert score >= 90

    def test_all_dead_agents_gives_low_score(self, mon):
        for i in range(5):
            _insert_agent(mon, f"agent{i}", runs=5, errors=5, last_run_offset_minutes=500)
        score = mon.org_health_score()
        assert score < 50

    def test_score_is_integer(self, mon):
        _insert_agent(mon, "agent1", runs=10, errors=1, last_run_offset_minutes=10)
        score = mon.org_health_score()
        assert isinstance(score, int)


# ---------------------------------------------------------------------------
# stale_agents
# ---------------------------------------------------------------------------

class TestStaleAgents:
    def test_stale_agents_empty_when_all_recent(self, mon):
        _insert_agent(mon, "fresh_agent", runs=10, errors=0, last_run_offset_minutes=5)
        stale = mon.stale_agents(threshold_minutes=120)
        assert stale == []

    def test_stale_agents_detects_old_agents(self, mon):
        _insert_agent(mon, "old_agent", runs=5, errors=0, last_run_offset_minutes=200)
        stale = mon.stale_agents(threshold_minutes=120)
        names = [a.name for a in stale]
        assert "old_agent" in names

    def test_stale_agents_threshold_respected(self, mon):
        _insert_agent(mon, "borderline", runs=5, errors=0, last_run_offset_minutes=61)
        stale_60 = mon.stale_agents(threshold_minutes=60)
        stale_120 = mon.stale_agents(threshold_minutes=120)
        assert len(stale_60) == 1
        assert len(stale_120) == 0

    def test_stale_agents_returns_agent_health_list(self, mon):
        _insert_agent(mon, "stale", runs=1, errors=0, last_run_offset_minutes=200)
        stale = mon.stale_agents()
        assert stale
        assert hasattr(stale[0], "name")
        assert hasattr(stale[0], "last_run_age_minutes")


# ---------------------------------------------------------------------------
# erroring_agents
# ---------------------------------------------------------------------------

class TestErroringAgents:
    def test_no_erroring_when_all_clean(self, mon):
        _insert_agent(mon, "clean_agent", runs=100, errors=0, last_run_offset_minutes=5)
        assert mon.erroring_agents() == []

    def test_detects_high_error_rate(self, mon):
        _insert_agent(mon, "bad_agent", runs=10, errors=6, last_run_offset_minutes=5)
        erroring = mon.erroring_agents(threshold_rate=0.5)
        names = [a.name for a in erroring]
        assert "bad_agent" in names

    def test_threshold_boundary(self, mon):
        # Exactly at threshold should be included
        _insert_agent(mon, "fifty_pct", runs=10, errors=5, last_run_offset_minutes=5)
        erroring = mon.erroring_agents(threshold_rate=0.5)
        assert any(a.name == "fifty_pct" for a in erroring)

    def test_custom_threshold(self, mon):
        _insert_agent(mon, "moderate_error", runs=10, errors=2, last_run_offset_minutes=5)
        # 20% error rate — only erroring at 0.15 threshold
        e15 = mon.erroring_agents(threshold_rate=0.15)
        e25 = mon.erroring_agents(threshold_rate=0.25)
        assert any(a.name == "moderate_error" for a in e15)
        assert not any(a.name == "moderate_error" for a in e25)


# ---------------------------------------------------------------------------
# log_metric / get_metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_log_and_get_metric(self, mon):
        mon.log_metric("cfo", "revenue_usd", 150.0)
        mon.log_metric("cfo", "revenue_usd", 300.0)
        metrics = mon.get_metrics("cfo", "revenue_usd")
        assert len(metrics) == 2
        values = [m["value"] for m in metrics]
        assert 150.0 in values
        assert 300.0 in values

    def test_get_metrics_returns_ordered_oldest_first(self, mon):
        mon.log_metric("cfo", "rev", 10.0)
        mon.log_metric("cfo", "rev", 20.0)
        mon.log_metric("cfo", "rev", 30.0)
        metrics = mon.get_metrics("cfo", "rev")
        # oldest first
        assert metrics[0]["value"] <= metrics[-1]["value"]

    def test_get_metrics_empty_for_unknown(self, mon):
        metrics = mon.get_metrics("unknown_agent", "nonexistent_metric")
        assert metrics == []

    def test_get_metrics_respects_last_n(self, mon):
        for v in range(20):
            mon.log_metric("agent1", "metric", float(v))
        metrics = mon.get_metrics("agent1", "metric", last_n=5)
        assert len(metrics) == 5

    def test_metrics_isolate_by_agent(self, mon):
        mon.log_metric("agentA", "cpu", 80.0)
        mon.log_metric("agentB", "cpu", 40.0)
        a_metrics = mon.get_metrics("agentA", "cpu")
        assert len(a_metrics) == 1
        assert a_metrics[0]["value"] == 80.0

    def test_metrics_dict_has_ts_and_value(self, mon):
        mon.log_metric("agent1", "score", 99.9)
        metrics = mon.get_metrics("agent1", "score")
        assert "ts" in metrics[0]
        assert "value" in metrics[0]


# ---------------------------------------------------------------------------
# record_latency / p95_latency
# ---------------------------------------------------------------------------

class TestLatency:
    def test_p95_none_with_no_data(self, mon):
        result = mon.p95_latency("no_such_agent")
        assert result is None

    def test_p95_single_value(self, mon):
        mon.record_latency("agent1", 100.0)
        p95 = mon.p95_latency("agent1")
        assert p95 == 100.0

    def test_p95_correct_value(self, mon):
        # 20 values: 1–20. P95 index = floor(20 * 0.95) = 19 → value 20
        for v in range(1, 21):
            mon.record_latency("agent1", float(v))
        p95 = mon.p95_latency("agent1")
        assert p95 == 20.0

    def test_record_latency_also_logs_metric(self, mon):
        mon.record_latency("agent1", 500.0)
        metrics = mon.get_metrics("agent1", "latency_ms")
        assert any(m["value"] == 500.0 for m in metrics)

    def test_p95_with_outlier(self, mon):
        # 19 low values and 1 very high outlier
        for _ in range(19):
            mon.record_latency("agent2", 50.0)
        mon.record_latency("agent2", 9999.0)
        p95 = mon.p95_latency("agent2")
        assert p95 == 9999.0

    def test_latency_isolates_by_agent(self, mon):
        mon.record_latency("agentA", 100.0)
        mon.record_latency("agentB", 500.0)
        assert mon.p95_latency("agentA") == 100.0
        assert mon.p95_latency("agentB") == 500.0


# ---------------------------------------------------------------------------
# record_run
# ---------------------------------------------------------------------------

class TestRecordRun:
    def test_record_run_creates_row(self, mon):
        mon.record_run("writer", True, "Success: article published")
        agents = mon.check_all()
        names = [a.name for a in agents]
        assert "writer" in names

    def test_record_run_increments_runs(self, mon):
        for _ in range(3):
            mon.record_run("ceo", True)
        agents = mon.check_all()
        agent = next(a for a in agents if a.name == "ceo")
        assert agent.runs == 3

    def test_record_run_increments_errors_on_failure(self, mon):
        mon.record_run("cfo", True)
        mon.record_run("cfo", False)
        mon.record_run("cfo", False)
        agents = mon.check_all()
        agent = next(a for a in agents if a.name == "cfo")
        assert agent.errors == 2

    def test_record_run_success_does_not_increment_errors(self, mon):
        mon.record_run("qc", True)
        agents = mon.check_all()
        agent = next(a for a in agents if a.name == "qc")
        assert agent.errors == 0


# ---------------------------------------------------------------------------
# AgentHealth.to_dict
# ---------------------------------------------------------------------------

class TestAgentHealthToDict:
    def test_to_dict_contains_expected_keys(self, mon):
        _insert_agent(mon, "test_agent", runs=10, errors=1, last_run_offset_minutes=5)
        agents = mon.check_all()
        d = agents[0].to_dict()
        for key in ("name", "runs", "errors", "error_rate", "last_run_age_minutes", "status"):
            assert key in d

    def test_to_dict_error_rate_rounded(self, mon):
        _insert_agent(mon, "test_agent", runs=3, errors=1, last_run_offset_minutes=5)
        agents = mon.check_all()
        d = agents[0].to_dict()
        # Should be rounded to 4 decimal places
        assert isinstance(d["error_rate"], float)


# ---------------------------------------------------------------------------
# health_summary
# ---------------------------------------------------------------------------

class TestHealthSummary:
    def test_health_summary_returns_string(self, mon):
        _insert_agent(mon, "agent1", runs=10, errors=1, last_run_offset_minutes=5)
        summary = mon.health_summary()
        assert isinstance(summary, str)

    def test_health_summary_contains_score(self, mon):
        _insert_agent(mon, "agent1", runs=10, errors=0, last_run_offset_minutes=5)
        summary = mon.health_summary()
        assert "score" in summary.lower() or "Overall" in summary

    def test_health_summary_empty_agents(self, mon):
        summary = mon.health_summary()
        assert isinstance(summary, str)
        assert "0" in summary  # Zero agents should appear somewhere
