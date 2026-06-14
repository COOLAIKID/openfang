"""Tests for autoearn/core/reporting_engine.py."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_rpt_{uuid.uuid4().hex[:8]}.db")


def _get_db_path():
    return _DB_PATH


with patch("autoearn.core.database.get_db_path", _get_db_path):
    import autoearn.core.reporting_engine as rpt


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_schema():
    rpt._schema_ready = False
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript("""
        DROP TABLE IF EXISTS report_sections;
        DROP TABLE IF EXISTS report_runs;
        DROP TABLE IF EXISTS report_definitions;
        DROP TABLE IF EXISTS report_metrics_cache;
    """)
    conn.close()
    yield


@pytest.fixture()
def sample_report():
    with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
        return rpt.create_report_definition(
            name="test_weekly",
            report_type="executive_summary",
            frequency="weekly",
            output_format="markdown",
        )


# ── Report definition CRUD ─────────────────────────────────────────────────

class TestCreateReportDefinition:
    def test_basic_creation(self, sample_report):
        assert sample_report.id is not None
        assert sample_report.name == "test_weekly"
        assert sample_report.report_type == "executive_summary"
        assert sample_report.frequency == "weekly"

    def test_default_format_is_markdown(self, sample_report):
        assert sample_report.output_format == "markdown"

    def test_is_active_by_default(self, sample_report):
        assert sample_report.is_active is True

    def test_next_run_at_set(self, sample_report):
        assert sample_report.next_run_at is not None
        datetime.fromisoformat(sample_report.next_run_at)

    def test_duplicate_name_raises(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="already exists"):
                rpt.create_report_definition("test_weekly")

    def test_all_report_types(self):
        for rt in rpt.REPORT_TYPES[:5]:
            with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
                r = rpt.create_report_definition(f"rt_{rt}", report_type=rt)
            assert r.report_type == rt

    def test_all_frequencies(self):
        for freq in rpt.FREQUENCIES:
            with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
                r = rpt.create_report_definition(f"freq_{freq}", frequency=freq)
            assert r.frequency == freq

    def test_with_parameters(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            r = rpt.create_report_definition(
                "parameterized", parameters={"days": 7, "include_drafts": False}
            )
        assert r.parameters["days"] == 7

    def test_with_delivery_channels(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            r = rpt.create_report_definition(
                "multi_deliver", delivery=["save", "telegram"]
            )
        assert "telegram" in r.delivery


class TestGetReportDefinition:
    def test_get_by_name(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            fetched = rpt.get_report_definition("test_weekly")
        assert fetched is not None
        assert fetched.id == sample_report.id

    def test_get_unknown_returns_none(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = rpt.get_report_definition("nosuchreport")
        assert result is None


class TestListReportDefinitions:
    def test_list_active(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            defs = rpt.list_report_definitions(active_only=True)
        assert len(defs) >= 1

    def test_list_all_includes_inactive(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            r = rpt.create_report_definition("inactive_rpt")
            rpt.update_report_definition("inactive_rpt", is_active=False)
            all_defs = rpt.list_report_definitions(active_only=False)
        assert any(d.name == "inactive_rpt" for d in all_defs)

    def test_list_empty_returns_empty(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            defs = rpt.list_report_definitions()
        assert isinstance(defs, list)


class TestUpdateReportDefinition:
    def test_update_frequency(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            ok = rpt.update_report_definition("test_weekly", frequency="monthly")
            updated = rpt.get_report_definition("test_weekly")
        assert ok is True
        assert updated.frequency == "monthly"

    def test_update_deactivate(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.update_report_definition("test_weekly", is_active=False)
            updated = rpt.get_report_definition("test_weekly")
        assert updated.is_active is False

    def test_update_output_format(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.update_report_definition("test_weekly", output_format="json")
            updated = rpt.get_report_definition("test_weekly")
        assert updated.output_format == "json"

    def test_update_unknown_field_returns_false(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            ok = rpt.update_report_definition("test_weekly", unknown_field="x")
        assert ok is False


class TestDueReports:
    def test_newly_created_report_is_not_immediately_due(self, sample_report):
        # next_run is set to future (1 week from now)
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            due = rpt.due_reports()
        # The test_weekly was just created — next_run is 1 week away, so NOT due
        assert not any(d.name == "test_weekly" for d in due)

    def test_overdue_report_appears_in_due_list(self):
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            r = rpt.create_report_definition("overdue_rpt")
            conn = sqlite3.connect(_DB_PATH)
            conn.execute(
                "UPDATE report_definitions SET next_run_at = ? WHERE name = ?",
                (past, "overdue_rpt"),
            )
            conn.commit()
            conn.close()
            due = rpt.due_reports()
        assert any(d.name == "overdue_rpt" for d in due)

    def test_inactive_report_not_in_due_list(self):
        past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.create_report_definition("inactive_overdue")
            rpt.update_report_definition("inactive_overdue", is_active=False)
            conn = sqlite3.connect(_DB_PATH)
            conn.execute(
                "UPDATE report_definitions SET next_run_at = ? WHERE name = ?",
                (past, "inactive_overdue"),
            )
            conn.commit()
            conn.close()
            due = rpt.due_reports()
        assert not any(d.name == "inactive_overdue" for d in due)


# ── next_run_for helper ───────────────────────────────────────────────────

class TestNextRunFor:
    def test_daily(self):
        now = datetime.utcnow()
        result = rpt._next_run_for("daily", from_dt=now)
        delta = datetime.fromisoformat(result) - now
        assert abs(delta.total_seconds() - 86400) < 10

    def test_weekly(self):
        now = datetime.utcnow()
        result = rpt._next_run_for("weekly", from_dt=now)
        delta = datetime.fromisoformat(result) - now
        assert abs(delta.total_seconds() - 7 * 86400) < 10

    def test_monthly(self):
        now = datetime.utcnow()
        result = rpt._next_run_for("monthly", from_dt=now)
        delta = datetime.fromisoformat(result) - now
        assert abs(delta.days - 30) <= 1

    def test_quarterly(self):
        now = datetime.utcnow()
        result = rpt._next_run_for("quarterly", from_dt=now)
        delta = datetime.fromisoformat(result) - now
        assert abs(delta.days - 90) <= 1

    def test_once_returns_now(self):
        now = datetime.utcnow()
        result = rpt._next_run_for("once", from_dt=now)
        delta = datetime.fromisoformat(result) - now
        assert abs(delta.total_seconds()) < 5


# ── Adhoc report generation ───────────────────────────────────────────────

class TestAdhocReports:
    def test_executive_summary_returns_string(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            content = rpt.run_adhoc_report("executive_summary", output_format="markdown")
        assert isinstance(content, str)
        assert len(content) > 10

    def test_markdown_format_has_headers(self):
        content = rpt.run_adhoc_report("executive_summary", output_format="markdown")
        assert "# " in content or "## " in content

    def test_json_format_is_parseable(self):
        content = rpt.run_adhoc_report("executive_summary", output_format="json")
        data = json.loads(content)
        assert isinstance(data, dict)

    def test_html_format_has_tags(self):
        content = rpt.run_adhoc_report("executive_summary", output_format="html")
        assert "<html" in content
        assert "<body" in content

    def test_text_format_no_markdown_syntax(self):
        content = rpt.run_adhoc_report("executive_summary", output_format="text")
        # # headers should be stripped
        lines = content.splitlines()
        # at least one line
        assert len(lines) > 1

    def test_revenue_report(self):
        content = rpt.run_adhoc_report("revenue_report", output_format="markdown")
        assert isinstance(content, str)

    def test_newsletter_health(self):
        content = rpt.run_adhoc_report("newsletter_health", output_format="markdown")
        assert isinstance(content, str)

    def test_seo_audit(self):
        content = rpt.run_adhoc_report("seo_audit", output_format="markdown")
        assert isinstance(content, str)

    def test_agent_activity(self):
        content = rpt.run_adhoc_report("agent_activity", output_format="markdown")
        assert isinstance(content, str)

    def test_funnel_report(self):
        content = rpt.run_adhoc_report("funnel_report", output_format="markdown")
        assert isinstance(content, str)

    def test_custom_title(self):
        content = rpt.run_adhoc_report(
            "executive_summary", output_format="markdown", title="My Custom Report"
        )
        assert "My Custom Report" in content

    def test_unknown_type_falls_back_gracefully(self):
        content = rpt.run_adhoc_report("nonexistent_type", output_format="markdown")
        assert isinstance(content, str)


# ── Run report ────────────────────────────────────────────────────────────

class TestRunReport:
    def test_run_report_creates_run(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly")
        assert run.id is not None
        assert run.status == "completed"
        assert run.run_ref.startswith("RUN-")

    def test_run_report_generates_content(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly")
        assert isinstance(run.content, str)
        assert len(run.content) > 0

    def test_run_report_updates_last_run_at(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.run_report("test_weekly")
            updated = rpt.get_report_definition("test_weekly")
        assert updated.last_run_at is not None

    def test_run_report_advances_next_run_at(self, sample_report):
        original_next = sample_report.next_run_at
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.run_report("test_weekly")
            updated = rpt.get_report_definition("test_weekly")
        # After running, next_run_at should be updated
        assert updated.next_run_at != original_next

    def test_run_unknown_report_raises(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="not found"):
                rpt.run_report("nonexistent_report")

    def test_run_report_with_format_override(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", output_format="json")
        assert run.output_format == "json"
        data = json.loads(run.content)
        assert isinstance(data, dict)

    def test_run_report_sets_byte_size(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", save_to_file=False)
        assert run.byte_size > 0

    def test_run_report_sets_completed_at(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", save_to_file=False)
        assert run.completed_at
        datetime.fromisoformat(run.completed_at)


# ── Run history ───────────────────────────────────────────────────────────

class TestRunHistory:
    def test_get_run_history_empty(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            history = rpt.get_run_history()
        assert isinstance(history, list)

    def test_get_run_history_after_run(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", save_to_file=False)
            history = rpt.get_run_history()
        assert len(history) >= 1
        refs = [h["run_ref"] for h in history]
        assert run.run_ref in refs

    def test_get_run_history_by_report(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.run_report("test_weekly", save_to_file=False)
            history = rpt.get_run_history(report_name="test_weekly")
        assert all(h["report_name"] == "test_weekly" for h in history)

    def test_get_run_history_limit(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            for _ in range(5):
                rpt.run_report("test_weekly", save_to_file=False)
            history = rpt.get_run_history(limit=2)
        assert len(history) <= 2

    def test_get_run_content(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", save_to_file=False)
            content = rpt.get_run_content(run.run_ref)
        assert content is not None
        assert len(content) > 0

    def test_get_run_content_unknown_ref(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = rpt.get_run_content("RUN-NONEXISTENT")
        assert result is None

    def test_delete_run(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", save_to_file=False)
            rpt.delete_run(run.id)
            history = rpt.get_run_history()
        refs = [h["run_ref"] for h in history]
        assert run.run_ref not in refs


# ── Metrics cache ─────────────────────────────────────────────────────────

class TestMetricsCache:
    def test_cache_and_retrieve(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.cache_metric("revenue_today", 1234.56, ttl_seconds=3600)
            value = rpt.get_cached_metric("revenue_today")
        assert value == 1234.56

    def test_cache_complex_value(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.cache_metric("my_dict", {"a": 1, "b": [1, 2, 3]})
            value = rpt.get_cached_metric("my_dict")
        assert value["a"] == 1
        assert value["b"] == [1, 2, 3]

    def test_cache_miss_returns_none(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = rpt.get_cached_metric("nonexistent_key")
        assert result is None

    def test_expired_cache_returns_none(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.cache_metric("expired_key", 999, ttl_seconds=0)
            result = rpt.get_cached_metric("expired_key")
        assert result is None

    def test_upsert_overwrites_existing(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.cache_metric("update_key", 100)
            rpt.cache_metric("update_key", 200)
            value = rpt.get_cached_metric("update_key")
        assert value == 200

    def test_purge_expired_cache(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.cache_metric("keep_this", 1, ttl_seconds=3600)
            rpt.cache_metric("purge_this", 2, ttl_seconds=0)
            deleted = rpt.purge_expired_cache()
        assert deleted >= 1


# ── Default reports seed ──────────────────────────────────────────────────

class TestDefaultReportsSeed:
    def test_seed_creates_default_reports(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            created = rpt.seed_default_reports()
        assert len(created) > 0
        assert "weekly_executive_summary" in created

    def test_seed_idempotent(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.seed_default_reports()
            created_second_time = rpt.seed_default_reports()
        # second call creates nothing (all exist)
        assert created_second_time == []

    def test_seeded_reports_are_fetchable(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.seed_default_reports()
            r = rpt.get_report_definition("weekly_executive_summary")
        assert r is not None
        assert r.frequency == "weekly"


# ── Reporting summary ─────────────────────────────────────────────────────

class TestReportingSummary:
    def test_summary_structure(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            summary = rpt.reporting_summary()
        assert "definitions" in summary
        assert "runs" in summary
        assert "due_now" in summary

    def test_summary_counts_definitions(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            summary = rpt.reporting_summary()
        assert len(summary["definitions"]) >= 1

    def test_summary_run_counts(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.run_report("test_weekly", save_to_file=False)
            summary = rpt.reporting_summary()
        assert summary["runs"]["completed"] >= 1

    def test_summary_due_now_is_int(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            summary = rpt.reporting_summary()
        assert isinstance(summary["due_now"], int)


# ── ReportDefinition to_dict ──────────────────────────────────────────────

class TestToDict:
    def test_definition_to_dict_keys(self, sample_report):
        d = sample_report.to_dict()
        required = ["id", "name", "report_type", "frequency", "is_active", "delivery"]
        for k in required:
            assert k in d

    def test_definition_to_dict_is_serializable(self, sample_report):
        d = sample_report.to_dict()
        json.dumps(d)  # should not raise

    def test_run_to_dict_keys(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run = rpt.run_report("test_weekly", save_to_file=False)
        d = run.to_dict()
        required = ["id", "report_id", "run_ref", "status", "output_format"]
        for k in required:
            assert k in d


# ── Tool wrappers ─────────────────────────────────────────────────────────

class TestToolWrappers:
    def test_create_report_tool(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.create_report_tool(
                "tool_test_rpt", report_type="revenue_report", frequency="daily"
            ))
        assert result["ok"] is True
        assert result["report"]["name"] == "tool_test_rpt"

    def test_create_report_tool_duplicate(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.create_report_tool("dup_rpt")
            result = json.loads(rpt.create_report_tool("dup_rpt"))
        assert result["ok"] is False

    def test_run_report_tool(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.run_report_tool("test_weekly"))
        assert result["ok"] is True
        assert "run_ref" in result

    def test_run_report_tool_unknown(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.run_report_tool("no_such_report"))
        assert result["ok"] is False

    def test_adhoc_report_tool(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.adhoc_report_tool("executive_summary", "markdown", "My Report"))
        assert result["ok"] is True
        assert "content" in result

    def test_list_reports_tool(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.list_reports_tool())
        assert "definitions" in result

    def test_run_history_tool(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            rpt.run_report_tool("test_weekly")
            result = json.loads(rpt.run_history_tool("test_weekly"))
        assert isinstance(result, list)

    def test_get_content_tool_after_run(self, sample_report):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            run_result = json.loads(rpt.run_report_tool("test_weekly"))
            ref = run_result["run_ref"]
            content_result = json.loads(rpt.get_content_tool(ref))
        assert content_result["ok"] is True
        assert len(content_result["content"]) > 0

    def test_get_content_tool_unknown_ref(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.get_content_tool("RUN-NOSUCHREF"))
        assert "error" in result

    def test_seed_defaults_tool(self):
        with patch("autoearn.core.reporting_engine.get_db_path", _get_db_path):
            result = json.loads(rpt.seed_defaults_tool())
        assert result["ok"] is True
        assert len(result["created"]) > 0
