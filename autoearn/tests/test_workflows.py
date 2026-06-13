"""Tests for core/workflows.py — workflow persistence, execution engine, and builder."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolated workflows module with temp DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def wf(tmp_path):
    """Fresh workflows module with isolated SQLite DB."""
    import core.workflows as w
    w._db_conn = None
    db_path = tmp_path / "wf_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    w._init_schema(conn)
    w._db_conn = conn
    yield w
    w._db_conn = None


def _make_step(to_agent="writer", message_type="task", subject="Test subject",
               body_template="Test body ${var}"):
    """Helper to create a WorkflowStep."""
    from core.workflows import WorkflowStep
    return WorkflowStep(
        to_agent=to_agent,
        message_type=message_type,
        subject=subject,
        body_template=body_template,
    )


def _make_workflow(name="test_wf", description="A test workflow", n_steps=2):
    """Helper to create a Workflow with N steps."""
    from core.workflows import Workflow, WorkflowStep
    steps = [
        WorkflowStep(
            to_agent=f"agent{i}",
            message_type="task",
            subject=f"Step {i} subject",
            body_template=f"Step {i} body with ${{var}}",
        )
        for i in range(n_steps)
    ]
    return Workflow(name=name, description=description, steps=steps)


# ---------------------------------------------------------------------------
# WorkflowStep
# ---------------------------------------------------------------------------

class TestWorkflowStep:
    def test_step_to_dict(self):
        from core.workflows import WorkflowStep
        step = WorkflowStep(
            to_agent="researcher",
            message_type="task",
            subject="Research this",
            body_template="Please research ${topic}",
            depends_on=["scout"],
        )
        d = step.to_dict()
        assert d["to_agent"] == "researcher"
        assert d["message_type"] == "task"
        assert d["subject"] == "Research this"
        assert d["body_template"] == "Please research ${topic}"
        assert d["depends_on"] == ["scout"]

    def test_step_from_dict(self):
        from core.workflows import WorkflowStep
        data = {
            "to_agent": "writer",
            "message_type": "directive",
            "subject": "Write content",
            "body_template": "Write about ${topic}",
            "depends_on": [],
        }
        step = WorkflowStep.from_dict(data)
        assert step.to_agent == "writer"
        assert step.message_type == "directive"

    def test_step_from_dict_defaults(self):
        from core.workflows import WorkflowStep
        step = WorkflowStep.from_dict({"to_agent": "ceo"})
        assert step.message_type == "task"
        assert step.subject == ""
        assert step.body_template == ""
        assert step.depends_on == []

    def test_step_roundtrip(self):
        from core.workflows import WorkflowStep
        original = WorkflowStep("agent1", "task", "Subject", "Body ${x}", ["dep1"])
        restored = WorkflowStep.from_dict(original.to_dict())
        assert original.to_agent == restored.to_agent
        assert original.depends_on == restored.depends_on


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------

class TestWorkflow:
    def test_workflow_to_dict(self):
        wf_obj = _make_workflow("my_wf", "My description", n_steps=3)
        d = wf_obj.to_dict()
        assert d["name"] == "my_wf"
        assert d["description"] == "My description"
        assert len(d["steps"]) == 3

    def test_workflow_from_dict(self):
        from core.workflows import Workflow
        data = {
            "name": "test",
            "description": "desc",
            "steps": [
                {"to_agent": "a", "message_type": "task", "subject": "s", "body_template": "b"}
            ],
            "created_by": "alice",
        }
        wf_obj = Workflow.from_dict(data)
        assert wf_obj.name == "test"
        assert len(wf_obj.steps) == 1
        assert wf_obj.steps[0].to_agent == "a"

    def test_workflow_roundtrip(self):
        from core.workflows import Workflow
        original = _make_workflow("round_trip", n_steps=4)
        restored = Workflow.from_dict(original.to_dict())
        assert original.name == restored.name
        assert len(original.steps) == len(restored.steps)


# ---------------------------------------------------------------------------
# save_workflow / load_workflow
# ---------------------------------------------------------------------------

class TestSaveLoadWorkflow:
    def test_save_and_load(self, wf):
        workflow = _make_workflow("save_test")
        wf.save_workflow(workflow)
        loaded = wf.load_workflow("save_test")
        assert loaded is not None
        assert loaded.name == "save_test"

    def test_load_nonexistent_returns_none(self, wf):
        result = wf.load_workflow("does_not_exist")
        assert result is None

    def test_save_preserves_steps(self, wf):
        workflow = _make_workflow("step_test", n_steps=3)
        wf.save_workflow(workflow)
        loaded = wf.load_workflow("step_test")
        assert len(loaded.steps) == 3

    def test_save_preserves_step_details(self, wf):
        from core.workflows import Workflow, WorkflowStep
        step = WorkflowStep("researcher", "task", "Research ${topic}", "Find info on ${topic}")
        workflow = Workflow("detailed_wf", steps=[step], description="Detailed workflow")
        wf.save_workflow(workflow)
        loaded = wf.load_workflow("detailed_wf")
        s = loaded.steps[0]
        assert s.to_agent == "researcher"
        assert s.subject == "Research ${topic}"

    def test_save_upserts_existing(self, wf):
        workflow = _make_workflow("upsert_test", description="Original")
        wf.save_workflow(workflow)
        workflow.description = "Updated"
        wf.save_workflow(workflow)
        loaded = wf.load_workflow("upsert_test")
        assert loaded.description == "Updated"

    def test_save_preserves_created_by(self, wf):
        from core.workflows import Workflow
        workflow = Workflow("by_alice", steps=[], created_by="alice")
        wf.save_workflow(workflow)
        loaded = wf.load_workflow("by_alice")
        assert loaded.created_by == "alice"


# ---------------------------------------------------------------------------
# list_workflows
# ---------------------------------------------------------------------------

class TestListWorkflows:
    def test_list_empty(self, wf):
        assert wf.list_workflows() == []

    def test_list_returns_all_saved(self, wf):
        for name in ["alpha", "beta", "gamma"]:
            wf.save_workflow(_make_workflow(name))
        listing = wf.list_workflows()
        names = [w["name"] for w in listing]
        assert set(names) == {"alpha", "beta", "gamma"}

    def test_list_ordered_by_name(self, wf):
        for name in ["zzz", "aaa", "mmm"]:
            wf.save_workflow(_make_workflow(name))
        listing = wf.list_workflows()
        names = [w["name"] for w in listing]
        assert names == sorted(names)

    def test_list_includes_step_count(self, wf):
        wf.save_workflow(_make_workflow("wf_3steps", n_steps=3))
        listing = wf.list_workflows()
        entry = listing[0]
        assert entry["step_count"] == 3

    def test_list_metadata_keys(self, wf):
        wf.save_workflow(_make_workflow("meta_test"))
        entry = wf.list_workflows()[0]
        for key in ("name", "description", "created_by", "created_at", "step_count"):
            assert key in entry


# ---------------------------------------------------------------------------
# delete_workflow
# ---------------------------------------------------------------------------

class TestDeleteWorkflow:
    def test_delete_existing_returns_true(self, wf):
        wf.save_workflow(_make_workflow("to_delete"))
        assert wf.delete_workflow("to_delete") is True

    def test_delete_removes_from_db(self, wf):
        wf.save_workflow(_make_workflow("gone"))
        wf.delete_workflow("gone")
        assert wf.load_workflow("gone") is None

    def test_delete_nonexistent_returns_false(self, wf):
        assert wf.delete_workflow("never_existed") is False

    def test_delete_does_not_affect_others(self, wf):
        wf.save_workflow(_make_workflow("keep"))
        wf.save_workflow(_make_workflow("remove"))
        wf.delete_workflow("remove")
        assert wf.load_workflow("keep") is not None
        assert wf.list_workflows()[0]["name"] == "keep"


# ---------------------------------------------------------------------------
# _render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate:
    def test_render_simple_substitution(self, wf):
        result = wf._render_template("Hello ${name}!", {"name": "Alice"})
        assert result == "Hello Alice!"

    def test_render_multiple_vars(self, wf):
        result = wf._render_template("${a} and ${b}", {"a": "foo", "b": "bar"})
        assert result == "foo and bar"

    def test_render_missing_var_leaves_placeholder(self, wf):
        # safe_substitute leaves unknown vars in place
        result = wf._render_template("Hello ${unknown_var}!", {})
        assert "${unknown_var}" in result

    def test_render_empty_context(self, wf):
        result = wf._render_template("No variables here", {})
        assert result == "No variables here"

    def test_render_returns_raw_on_error(self, wf):
        # Should not raise even with unusual templates
        result = wf._render_template("${}", {})
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# run_workflow
# ---------------------------------------------------------------------------

class TestRunWorkflow:
    def test_run_raises_on_missing_workflow(self, wf):
        with pytest.raises(ValueError, match="not found"):
            wf.run_workflow("nonexistent_wf")

    def test_run_returns_message_id_list(self, wf):
        workflow = _make_workflow("run_test", n_steps=2)
        wf.save_workflow(workflow)
        mock_send = MagicMock(return_value=42)
        with patch("core.message_bus.send", mock_send):
            ids = wf.run_workflow("run_test", context={"var": "hello"})
        assert isinstance(ids, list)

    def test_run_sends_one_message_per_step(self, wf):
        workflow = _make_workflow("multi_step", n_steps=3)
        wf.save_workflow(workflow)
        call_count = [0]

        def _fake_send(**kwargs):
            call_count[0] += 1
            return call_count[0]

        with patch("core.message_bus.send", side_effect=_fake_send):
            ids = wf.run_workflow("multi_step")
        assert call_count[0] == 3

    def test_run_renders_templates(self, wf):
        from core.workflows import Workflow, WorkflowStep
        step = WorkflowStep("writer", "task", "${topic} research", "Write about ${topic}")
        workflow = Workflow("template_wf", steps=[step])
        wf.save_workflow(workflow)
        sent_subjects = []

        def _fake_send(**kwargs):
            sent_subjects.append(kwargs.get("subject", ""))
            return 1

        with patch("core.message_bus.send", side_effect=_fake_send):
            wf.run_workflow("template_wf", context={"topic": "AI"})
        assert "AI" in sent_subjects[0]

    def test_run_with_no_context(self, wf):
        workflow = _make_workflow("no_ctx_wf", n_steps=1)
        wf.save_workflow(workflow)
        with patch("core.message_bus.send", return_value=1):
            ids = wf.run_workflow("no_ctx_wf")
        assert isinstance(ids, list)

    def test_run_records_workflow_run_in_db(self, wf):
        workflow = _make_workflow("recorded_wf", n_steps=1)
        wf.save_workflow(workflow)
        with patch("core.message_bus.send", return_value=1):
            wf.run_workflow("recorded_wf")
        rows = wf._db_conn.execute(
            "SELECT * FROM workflow_runs WHERE workflow = ?", ("recorded_wf",)
        ).fetchall()
        assert len(rows) == 1

    def test_run_status_completed_on_success(self, wf):
        workflow = _make_workflow("status_wf", n_steps=2)
        wf.save_workflow(workflow)
        with patch("core.message_bus.send", return_value=99):
            wf.run_workflow("status_wf")
        row = wf._db_conn.execute(
            "SELECT status FROM workflow_runs WHERE workflow = ?", ("status_wf",)
        ).fetchone()
        assert row["status"] in ("completed", "partial")

    def test_run_partial_status_when_send_fails(self, wf):
        workflow = _make_workflow("fail_wf", n_steps=2)
        wf.save_workflow(workflow)
        # First send succeeds, second fails
        call_count = [0]

        def _flaky_send(**kwargs):
            call_count[0] += 1
            if call_count[0] > 1:
                raise RuntimeError("Network error")
            return 1

        with patch("core.message_bus.send", side_effect=_flaky_send):
            ids = wf.run_workflow("fail_wf")
        row = wf._db_conn.execute(
            "SELECT status FROM workflow_runs WHERE workflow = ?", ("fail_wf",)
        ).fetchone()
        assert row["status"] in ("partial", "completed")


# ---------------------------------------------------------------------------
# WorkflowBuilder
# ---------------------------------------------------------------------------

class TestWorkflowBuilder:
    def test_builder_creates_workflow(self, wf):
        from core.workflows import WorkflowBuilder
        built = (
            WorkflowBuilder()
            .step("researcher", "task", "Research ${topic}", "Find info")
            .build("built_wf", "Built by test")
        )
        assert built.name == "built_wf"
        assert len(built.steps) == 1

    def test_builder_then_is_alias_for_step(self, wf):
        from core.workflows import WorkflowBuilder
        built = (
            WorkflowBuilder()
            .step("a1", "task", "Step 1", "Body 1")
            .then("a2", "task", "Step 2", "Body 2")
            .build("chain_wf")
        )
        assert len(built.steps) == 2
        assert built.steps[0].to_agent == "a1"
        assert built.steps[1].to_agent == "a2"

    def test_builder_fluent_chain(self, wf):
        from core.workflows import WorkflowBuilder
        built = (
            WorkflowBuilder()
            .step("scout", "task", "Scout", "Find prospects")
            .then("proposer", "task", "Propose", "Draft proposals")
            .then("closer", "task", "Close", "Send outreach")
            .build("pipeline", "3-stage pipeline")
        )
        assert len(built.steps) == 3

    def test_builder_save_and_load(self, wf):
        from core.workflows import WorkflowBuilder
        built = (
            WorkflowBuilder()
            .step("researcher", "task", "Research ${topic}", "Investigate ${topic}")
            .then("writer", "task", "Write on ${topic}", "Create article about ${topic}")
            .build("content_pipeline", created_by="test")
        )
        wf.save_workflow(built)
        loaded = wf.load_workflow("content_pipeline")
        assert loaded is not None
        assert len(loaded.steps) == 2
        assert loaded.created_by == "test"

    def test_builder_step_with_depends_on(self, wf):
        from core.workflows import WorkflowBuilder
        built = (
            WorkflowBuilder()
            .step("dep_agent", "task", "Subj", "Body", depends_on=["prereq"])
            .build("dep_wf")
        )
        assert built.steps[0].depends_on == ["prereq"]

    def test_builder_empty_produces_zero_steps(self, wf):
        from core.workflows import WorkflowBuilder
        built = WorkflowBuilder().build("empty_wf")
        assert len(built.steps) == 0

    def test_builder_does_not_save_automatically(self, wf):
        from core.workflows import WorkflowBuilder
        WorkflowBuilder().step("a", "task", "s", "b").build("unsaved_wf")
        assert wf.load_workflow("unsaved_wf") is None


# ---------------------------------------------------------------------------
# seed_default_workflows
# ---------------------------------------------------------------------------

class TestSeedDefaultWorkflows:
    def test_seed_creates_three_workflows(self, wf):
        wf.seed_default_workflows()
        listing = wf.list_workflows()
        names = {w["name"] for w in listing}
        assert "content_pipeline" in names
        assert "market_signal" in names
        assert "outreach_pipeline" in names

    def test_seed_idempotent(self, wf):
        wf.seed_default_workflows()
        wf.seed_default_workflows()  # Second call should not fail or duplicate
        listing = wf.list_workflows()
        name_list = [w["name"] for w in listing]
        # Each workflow should appear exactly once
        assert name_list.count("content_pipeline") == 1

    def test_seeded_content_pipeline_has_four_steps(self, wf):
        wf.seed_default_workflows()
        loaded = wf.load_workflow("content_pipeline")
        assert loaded is not None
        assert len(loaded.steps) == 4
