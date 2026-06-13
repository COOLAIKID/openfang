"""Comprehensive tests for core/lead_tracker.py.

Each test class is independent and uses a temporary SQLite database so tests
never touch the real autoearn.db and cannot interfere with one another.
"""
from __future__ import annotations

import datetime
import importlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers: redirect the module's DB path to a temp file
# ---------------------------------------------------------------------------

def _make_module(tmp_db_path: str):
    """Import (or re-configure) lead_tracker to use an isolated DB."""
    import autoearn.core.lead_tracker as mod
    importlib.reload(mod)
    mod._DB_PATH = Path(tmp_db_path)
    mod._schema_ready = False
    return mod


# ---------------------------------------------------------------------------
# TestLeadCreation
# ---------------------------------------------------------------------------

class TestLeadCreation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_create_lead_returns_lead_object(self):
        lead = self.lt.create_lead("Acme Corp")
        self.assertIsNotNone(lead)
        self.assertEqual(lead.name, "Acme Corp")

    def test_create_lead_assigns_positive_id(self):
        lead = self.lt.create_lead("Test Co")
        self.assertGreater(lead.id, 0)

    def test_create_lead_default_stage_is_new(self):
        lead = self.lt.create_lead("Startup Inc")
        self.assertEqual(lead.stage, "new")

    def test_create_lead_with_all_fields(self):
        lead = self.lt.create_lead(
            name="BigCorp",
            source="linkedin",
            contact_email="ceo@bigcorp.com",
            company="BigCorp LLC",
            niche="SaaS",
            estimated_value=50000.0,
            notes="Very promising lead",
            assigned_to="closer",
            tags=["hot", "enterprise"],
        )
        self.assertEqual(lead.name, "BigCorp")
        self.assertEqual(lead.source, "linkedin")
        self.assertEqual(lead.contact_email, "ceo@bigcorp.com")
        self.assertEqual(lead.company, "BigCorp LLC")
        self.assertEqual(lead.niche, "SaaS")
        self.assertAlmostEqual(lead.estimated_value, 50000.0)
        self.assertEqual(lead.notes, "Very promising lead")
        self.assertEqual(lead.assigned_to, "closer")
        self.assertIn("hot", lead.tags)
        self.assertIn("enterprise", lead.tags)

    def test_create_lead_default_assigned_to_is_scout(self):
        lead = self.lt.create_lead("Default Agent")
        self.assertEqual(lead.assigned_to, "scout")

    def test_create_lead_default_estimated_value_is_zero(self):
        lead = self.lt.create_lead("Cheap Lead")
        self.assertAlmostEqual(lead.estimated_value, 0.0)

    def test_create_lead_empty_tags_by_default(self):
        lead = self.lt.create_lead("No Tags")
        self.assertEqual(lead.tags, [])

    def test_create_lead_created_at_is_recent(self):
        before = time.time()
        lead = self.lt.create_lead("Time Test")
        after = time.time()
        self.assertGreaterEqual(lead.created_at, before)
        self.assertLessEqual(lead.created_at, after)

    def test_create_lead_updated_at_equals_created_at(self):
        lead = self.lt.create_lead("Timestamp Check")
        self.assertAlmostEqual(lead.created_at, lead.updated_at, delta=1.0)

    def test_create_lead_logs_activity(self):
        lead = self.lt.create_lead("Activity Lead", source="cold_email")
        activities = self.lt.get_activities(lead.id)
        self.assertGreater(len(activities), 0)
        self.assertEqual(activities[0]["activity"], "created")

    def test_create_multiple_leads_unique_ids(self):
        lead1 = self.lt.create_lead("First")
        lead2 = self.lt.create_lead("Second")
        lead3 = self.lt.create_lead("Third")
        ids = {lead1.id, lead2.id, lead3.id}
        self.assertEqual(len(ids), 3)

    def test_create_lead_with_tags_list_stored_correctly(self):
        tags = ["urgent", "big_deal", "q4"]
        lead = self.lt.create_lead("Tagged Lead", tags=tags)
        fetched = self.lt.get_lead(lead.id)
        self.assertEqual(sorted(fetched.tags), sorted(tags))


# ---------------------------------------------------------------------------
# TestGetLead
# ---------------------------------------------------------------------------

class TestGetLead(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_lead_returns_none_for_missing_id(self):
        result = self.lt.get_lead(99999)
        self.assertIsNone(result)

    def test_get_lead_returns_correct_lead(self):
        lead = self.lt.create_lead("Findable", company="SearchCo")
        fetched = self.lt.get_lead(lead.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Findable")
        self.assertEqual(fetched.company, "SearchCo")

    def test_get_lead_returns_lead_dataclass(self):
        from autoearn.core.lead_tracker import Lead
        lead = self.lt.create_lead("Type Check")
        fetched = self.lt.get_lead(lead.id)
        self.assertIsInstance(fetched, Lead)

    def test_get_lead_correct_id_field(self):
        lead = self.lt.create_lead("ID Check")
        fetched = self.lt.get_lead(lead.id)
        self.assertEqual(fetched.id, lead.id)

    def test_find_lead_by_name(self):
        lead = self.lt.create_lead("Unique Company XYZ")
        found = self.lt.find_lead("Unique Company XYZ")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, lead.id)

    def test_find_lead_partial_name_match(self):
        lead = self.lt.create_lead("The Innovative Company")
        found = self.lt.find_lead("Innovative")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, lead.id)

    def test_find_lead_returns_none_for_no_match(self):
        self.lt.create_lead("Some Company")
        result = self.lt.find_lead("ZZZ_No_Match_XYZ")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TestStageTransitions
# ---------------------------------------------------------------------------

class TestStageTransitions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)
        self.lead = self.lt.create_lead("Pipeline Lead", estimated_value=10000.0)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_update_stage_returns_string(self):
        result = self.lt.update_stage(self.lead.id, "contacted")
        self.assertIsInstance(result, str)

    def test_update_stage_success_message_contains_id(self):
        result = self.lt.update_stage(self.lead.id, "contacted")
        self.assertIn(str(self.lead.id), result)

    def test_update_stage_to_contacted(self):
        self.lt.update_stage(self.lead.id, "contacted")
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "contacted")

    def test_update_stage_to_proposal_sent(self):
        self.lt.update_stage(self.lead.id, "proposal_sent")
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "proposal_sent")

    def test_update_stage_to_negotiating(self):
        self.lt.update_stage(self.lead.id, "negotiating")
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "negotiating")

    def test_update_stage_to_won(self):
        self.lt.update_stage(self.lead.id, "won")
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "won")

    def test_update_stage_to_lost(self):
        self.lt.update_stage(self.lead.id, "lost")
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "lost")

    def test_update_stage_invalid_returns_error(self):
        result = self.lt.update_stage(self.lead.id, "invalid_stage")
        self.assertIn("ERROR", result)

    def test_update_stage_invalid_stage_listed_in_error(self):
        result = self.lt.update_stage(self.lead.id, "flying")
        self.assertIn("flying", result)

    def test_update_stage_does_not_change_on_invalid(self):
        self.lt.update_stage(self.lead.id, "invalid_stage_xyz")
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "new")

    def test_update_stage_full_pipeline_journey(self):
        stages = ["contacted", "proposal_sent", "negotiating", "won"]
        for stage in stages:
            self.lt.update_stage(self.lead.id, stage)
            lead = self.lt.get_lead(self.lead.id)
            self.assertEqual(lead.stage, stage)

    def test_update_stage_logs_stage_change_activity(self):
        self.lt.update_stage(self.lead.id, "contacted", note="Called them today")
        activities = self.lt.get_activities(self.lead.id)
        stage_changes = [a for a in activities if a["activity"] == "stage_change"]
        self.assertGreater(len(stage_changes), 0)

    def test_update_stage_updates_updated_at(self):
        original = self.lt.get_lead(self.lead.id).updated_at
        time.sleep(0.01)
        self.lt.update_stage(self.lead.id, "contacted")
        updated = self.lt.get_lead(self.lead.id).updated_at
        self.assertGreater(updated, original)

    def test_move_lead_stage_wrapper(self):
        result = self.lt.move_lead_stage(self.lead.id, "contacted", agent="closer")
        self.assertIsInstance(result, str)
        lead = self.lt.get_lead(self.lead.id)
        self.assertEqual(lead.stage, "contacted")

    def test_all_valid_stages_are_accepted(self):
        from autoearn.core.lead_tracker import STAGES
        for stage in STAGES:
            lead = self.lt.create_lead(f"Stage {stage} Lead")
            result = self.lt.update_stage(lead.id, stage)
            self.assertNotIn("ERROR", result)


# ---------------------------------------------------------------------------
# TestWeightedValue
# ---------------------------------------------------------------------------

class TestWeightedValue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_weighted_value_new_stage(self):
        from autoearn.core.lead_tracker import Lead, STAGE_WIN_PROBABILITIES
        lead = Lead(name="test", stage="new", estimated_value=10000.0)
        expected = 10000.0 * STAGE_WIN_PROBABILITIES["new"]
        self.assertAlmostEqual(lead.weighted_value, expected)

    def test_weighted_value_won_stage(self):
        from autoearn.core.lead_tracker import Lead
        lead = Lead(name="test", stage="won", estimated_value=5000.0)
        self.assertAlmostEqual(lead.weighted_value, 5000.0)

    def test_weighted_value_lost_stage(self):
        from autoearn.core.lead_tracker import Lead
        lead = Lead(name="test", stage="lost", estimated_value=9999.0)
        self.assertAlmostEqual(lead.weighted_value, 0.0)

    def test_weighted_value_negotiating_stage(self):
        from autoearn.core.lead_tracker import Lead, STAGE_WIN_PROBABILITIES
        lead = Lead(name="test", stage="negotiating", estimated_value=20000.0)
        expected = 20000.0 * STAGE_WIN_PROBABILITIES["negotiating"]
        self.assertAlmostEqual(lead.weighted_value, expected)

    def test_weighted_value_uses_custom_probability_over_stage(self):
        from autoearn.core.lead_tracker import Lead
        lead = Lead(name="test", stage="new", estimated_value=10000.0, probability=0.75)
        self.assertAlmostEqual(lead.weighted_value, 7500.0)

    def test_weighted_value_increases_through_pipeline(self):
        from autoearn.core.lead_tracker import Lead, STAGES, STAGE_WIN_PROBABILITIES
        value = 100000.0
        prev_wv = 0.0
        active_stages = ["new", "contacted", "proposal_sent", "negotiating"]
        for stage in active_stages:
            lead = Lead(name="test", stage=stage, estimated_value=value)
            self.assertGreaterEqual(lead.weighted_value, prev_wv)
            prev_wv = lead.weighted_value

    def test_stage_win_probabilities_constants(self):
        from autoearn.core.lead_tracker import STAGE_WIN_PROBABILITIES
        self.assertAlmostEqual(STAGE_WIN_PROBABILITIES["won"], 1.0)
        self.assertAlmostEqual(STAGE_WIN_PROBABILITIES["lost"], 0.0)
        self.assertLess(STAGE_WIN_PROBABILITIES["new"], STAGE_WIN_PROBABILITIES["contacted"])
        self.assertLess(STAGE_WIN_PROBABILITIES["contacted"], STAGE_WIN_PROBABILITIES["proposal_sent"])


# ---------------------------------------------------------------------------
# TestActivityLogging
# ---------------------------------------------------------------------------

class TestActivityLogging(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)
        self.lead = self.lt.create_lead("Activity Test Lead")

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_activities_returns_list(self):
        activities = self.lt.get_activities(self.lead.id)
        self.assertIsInstance(activities, list)

    def test_create_lead_creates_initial_activity(self):
        activities = self.lt.get_activities(self.lead.id)
        self.assertGreater(len(activities), 0)

    def test_get_activities_returns_dicts(self):
        activities = self.lt.get_activities(self.lead.id)
        self.assertIsInstance(activities[0], dict)

    def test_activity_has_required_fields(self):
        activities = self.lt.get_activities(self.lead.id)
        a = activities[0]
        for field in ("lead_id", "agent", "activity", "note", "ts"):
            self.assertIn(field, a)

    def test_add_note_creates_activity(self):
        initial_count = len(self.lt.get_activities(self.lead.id))
        self.lt.add_note(self.lead.id, "scout", "Follow up on proposal")
        activities = self.lt.get_activities(self.lead.id)
        self.assertEqual(len(activities), initial_count + 1)

    def test_add_note_returns_confirmation_string(self):
        result = self.lt.add_note(self.lead.id, "scout", "Test note")
        self.assertIsInstance(result, str)
        self.assertIn(str(self.lead.id), result)

    def test_add_note_activity_type_is_note(self):
        self.lt.add_note(self.lead.id, "scout", "Important note")
        activities = self.lt.get_activities(self.lead.id)
        notes = [a for a in activities if a["activity"] == "note"]
        self.assertGreater(len(notes), 0)

    def test_add_note_stores_text(self):
        self.lt.add_note(self.lead.id, "writer", "Call back Monday")
        activities = self.lt.get_activities(self.lead.id)
        notes = [a for a in activities if a["activity"] == "note"]
        self.assertTrue(any("Call back Monday" in a["note"] for a in notes))

    def test_get_activities_respects_limit(self):
        for i in range(25):
            self.lt.add_note(self.lead.id, "scout", f"Note {i}")
        activities = self.lt.get_activities(self.lead.id, limit=10)
        self.assertLessEqual(len(activities), 10)

    def test_stage_change_logged_as_activity(self):
        self.lt.update_stage(self.lead.id, "contacted", note="Sent intro email")
        activities = self.lt.get_activities(self.lead.id)
        stage_changes = [a for a in activities if a["activity"] == "stage_change"]
        self.assertGreater(len(stage_changes), 0)

    def test_activities_ordered_by_most_recent_first(self):
        self.lt.add_note(self.lead.id, "scout", "First note")
        time.sleep(0.01)
        self.lt.add_note(self.lead.id, "closer", "Second note")
        activities = self.lt.get_activities(self.lead.id)
        # Most recent (Second note) should appear first
        notes = [a for a in activities if a["activity"] == "note"]
        if len(notes) >= 2:
            self.assertGreaterEqual(notes[0]["ts"], notes[1]["ts"])

    def test_activities_for_nonexistent_lead_empty(self):
        activities = self.lt.get_activities(99999)
        self.assertEqual(activities, [])


# ---------------------------------------------------------------------------
# TestGetPipeline
# ---------------------------------------------------------------------------

class TestGetPipeline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_pipeline_empty_returns_empty_list(self):
        result = self.lt.get_pipeline()
        self.assertEqual(result, [])

    def test_get_pipeline_returns_all_leads(self):
        for i in range(5):
            self.lt.create_lead(f"Lead {i}")
        result = self.lt.get_pipeline()
        self.assertEqual(len(result), 5)

    def test_get_pipeline_filter_by_stage(self):
        self.lt.create_lead("New Lead 1")
        self.lt.create_lead("New Lead 2")
        contacted = self.lt.create_lead("Contacted Lead")
        self.lt.update_stage(contacted.id, "contacted")

        new_leads = self.lt.get_pipeline(stage="new")
        self.assertEqual(len(new_leads), 2)

        contacted_leads = self.lt.get_pipeline(stage="contacted")
        self.assertEqual(len(contacted_leads), 1)

    def test_get_pipeline_filter_by_assigned_to(self):
        self.lt.create_lead("Scout Lead", assigned_to="scout")
        self.lt.create_lead("Closer Lead", assigned_to="closer")
        scout_leads = self.lt.get_pipeline(assigned_to="scout")
        self.assertTrue(all(l.assigned_to == "scout" for l in scout_leads))

    def test_get_pipeline_combined_filters(self):
        l1 = self.lt.create_lead("Match", assigned_to="closer")
        self.lt.update_stage(l1.id, "negotiating")
        self.lt.create_lead("No Match", assigned_to="closer")
        result = self.lt.get_pipeline(stage="negotiating", assigned_to="closer")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, l1.id)


# ---------------------------------------------------------------------------
# TestPipelineSummary
# ---------------------------------------------------------------------------

class TestPipelineSummary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_pipeline_summary_returns_dict(self):
        result = self.lt.pipeline_summary()
        self.assertIsInstance(result, dict)

    def test_pipeline_summary_required_keys(self):
        result = self.lt.pipeline_summary()
        for key in ("pipeline", "total_leads", "total_pipeline_value",
                    "weighted_pipeline_value", "won_revenue", "win_rate"):
            self.assertIn(key, result)

    def test_pipeline_summary_empty_db(self):
        result = self.lt.pipeline_summary()
        self.assertEqual(result["total_leads"], 0)
        self.assertAlmostEqual(result["total_pipeline_value"], 0.0)

    def test_pipeline_summary_total_leads_count(self):
        for i in range(4):
            self.lt.create_lead(f"Lead {i}")
        result = self.lt.pipeline_summary()
        self.assertEqual(result["total_leads"], 4)

    def test_pipeline_summary_total_value(self):
        self.lt.create_lead("L1", estimated_value=10000.0)
        self.lt.create_lead("L2", estimated_value=20000.0)
        result = self.lt.pipeline_summary()
        self.assertAlmostEqual(result["total_pipeline_value"], 30000.0, places=1)

    def test_pipeline_summary_stage_breakdown(self):
        self.lt.create_lead("New 1")
        self.lt.create_lead("New 2")
        result = self.lt.pipeline_summary()
        self.assertIn("new", result["pipeline"])
        self.assertEqual(result["pipeline"]["new"]["count"], 2)

    def test_pipeline_summary_weighted_value_calculation(self):
        from autoearn.core.lead_tracker import STAGE_WIN_PROBABILITIES
        value = 10000.0
        lead = self.lt.create_lead("Negotiating Lead", estimated_value=value)
        self.lt.update_stage(lead.id, "negotiating")
        result = self.lt.pipeline_summary()
        expected_weighted = value * STAGE_WIN_PROBABILITIES["negotiating"]
        self.assertAlmostEqual(
            result["pipeline"]["negotiating"]["weighted_value"],
            round(expected_weighted, 2),
            places=1
        )

    def test_pipeline_summary_won_revenue(self):
        lead = self.lt.create_lead("Won Deal", estimated_value=15000.0)
        self.lt.update_stage(lead.id, "won")
        result = self.lt.pipeline_summary()
        self.assertAlmostEqual(result["won_revenue"], 15000.0, places=1)

    def test_pipeline_summary_win_rate(self):
        # 2 leads: 1 won, 1 new → 50% win rate
        l1 = self.lt.create_lead("Won Lead", estimated_value=5000.0)
        self.lt.update_stage(l1.id, "won")
        self.lt.create_lead("New Lead")
        result = self.lt.pipeline_summary()
        self.assertAlmostEqual(result["win_rate"], 0.5, places=2)

    def test_pipeline_summary_zero_win_rate_when_no_won(self):
        self.lt.create_lead("Pending")
        result = self.lt.pipeline_summary()
        self.assertAlmostEqual(result["win_rate"], 0.0, places=3)

    def test_get_pipeline_report_returns_json_string(self):
        result = self.lt.get_pipeline_report()
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, dict)

    def test_pipeline_summary_stage_entry_has_required_keys(self):
        self.lt.create_lead("L1", estimated_value=1000.0)
        result = self.lt.pipeline_summary()
        stage_entry = result["pipeline"]["new"]
        for key in ("count", "total_value", "avg_value", "weighted_value"):
            self.assertIn(key, stage_entry)


# ---------------------------------------------------------------------------
# TestFollowupDetection
# ---------------------------------------------------------------------------

class TestFollowupDetection(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def _set_lead_updated_at(self, lead_id: int, days_ago: float):
        """Backdates a lead's updated_at to simulate aging."""
        past_ts = time.time() - days_ago * 86400
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE leads SET updated_at=? WHERE id=?", (past_ts, lead_id))
        conn.commit()
        conn.close()

    def test_leads_due_for_followup_returns_list(self):
        result = self.lt.leads_due_for_followup()
        self.assertIsInstance(result, list)

    def test_new_stage_lead_not_in_followup(self):
        """'new' stage leads are not included in follow-up (only active stages)."""
        self.lt.create_lead("Brand New Lead")
        result = self.lt.leads_due_for_followup(days_since_update=0)
        self.assertEqual(len(result), 0)

    def test_recently_updated_contacted_lead_not_overdue(self):
        lead = self.lt.create_lead("Fresh Contact")
        self.lt.update_stage(lead.id, "contacted")
        result = self.lt.leads_due_for_followup(days_since_update=3)
        self.assertEqual(len(result), 0)

    def test_old_contacted_lead_is_overdue(self):
        lead = self.lt.create_lead("Stale Contact")
        self.lt.update_stage(lead.id, "contacted")
        self._set_lead_updated_at(lead.id, days_ago=5)
        result = self.lt.leads_due_for_followup(days_since_update=3)
        ids = [l.id for l in result]
        self.assertIn(lead.id, ids)

    def test_old_proposal_sent_lead_is_overdue(self):
        lead = self.lt.create_lead("Old Proposal")
        self.lt.update_stage(lead.id, "proposal_sent")
        self._set_lead_updated_at(lead.id, days_ago=10)
        result = self.lt.leads_due_for_followup(days_since_update=7)
        ids = [l.id for l in result]
        self.assertIn(lead.id, ids)

    def test_old_negotiating_lead_is_overdue(self):
        lead = self.lt.create_lead("Old Negotiation")
        self.lt.update_stage(lead.id, "negotiating")
        self._set_lead_updated_at(lead.id, days_ago=4)
        result = self.lt.leads_due_for_followup(days_since_update=2)
        ids = [l.id for l in result]
        self.assertIn(lead.id, ids)

    def test_won_lead_not_included_in_followup(self):
        lead = self.lt.create_lead("Won Deal")
        self.lt.update_stage(lead.id, "won")
        self._set_lead_updated_at(lead.id, days_ago=30)
        result = self.lt.leads_due_for_followup(days_since_update=1)
        ids = [l.id for l in result]
        self.assertNotIn(lead.id, ids)

    def test_lost_lead_not_included_in_followup(self):
        lead = self.lt.create_lead("Lost Deal")
        self.lt.update_stage(lead.id, "lost")
        self._set_lead_updated_at(lead.id, days_ago=30)
        result = self.lt.leads_due_for_followup(days_since_update=1)
        ids = [l.id for l in result]
        self.assertNotIn(lead.id, ids)

    def test_get_followup_leads_returns_json_string(self):
        result = self.lt.get_followup_leads()
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)

    def test_followup_json_includes_required_fields(self):
        lead = self.lt.create_lead("Followup JSON")
        self.lt.update_stage(lead.id, "contacted")
        self._set_lead_updated_at(lead.id, days_ago=5)
        result = json.loads(self.lt.get_followup_leads())
        if result:
            for field in ("id", "name", "stage", "company", "assigned_to", "estimated_value"):
                self.assertIn(field, result[0])

    def test_multiple_overdue_leads_all_returned(self):
        overdue_ids = []
        for i in range(3):
            lead = self.lt.create_lead(f"Overdue {i}")
            self.lt.update_stage(lead.id, "contacted")
            self._set_lead_updated_at(lead.id, days_ago=7)
            overdue_ids.append(lead.id)
        result = self.lt.leads_due_for_followup(days_since_update=3)
        returned_ids = [l.id for l in result]
        for oid in overdue_ids:
            self.assertIn(oid, returned_ids)


# ---------------------------------------------------------------------------
# TestWonDealsAndRevenue
# ---------------------------------------------------------------------------

class TestWonDealsAndRevenue(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def _set_lead_updated_at(self, lead_id: int, days_ago: float):
        past_ts = time.time() - days_ago * 86400
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE leads SET updated_at=? WHERE id=?", (past_ts, lead_id))
        conn.commit()
        conn.close()

    def test_won_deals_returns_list(self):
        result = self.lt.won_deals()
        self.assertIsInstance(result, list)

    def test_won_deals_empty_when_no_won_leads(self):
        self.lt.create_lead("Not Won Yet")
        result = self.lt.won_deals()
        self.assertEqual(result, [])

    def test_won_deals_includes_recently_won(self):
        lead = self.lt.create_lead("Won Recently", estimated_value=5000.0)
        self.lt.update_stage(lead.id, "won")
        result = self.lt.won_deals(days=30)
        ids = [l.id for l in result]
        self.assertIn(lead.id, ids)

    def test_won_deals_excludes_old_wins(self):
        lead = self.lt.create_lead("Old Win", estimated_value=5000.0)
        self.lt.update_stage(lead.id, "won")
        self._set_lead_updated_at(lead.id, days_ago=45)
        result = self.lt.won_deals(days=30)
        ids = [l.id for l in result]
        self.assertNotIn(lead.id, ids)

    def test_won_deals_does_not_include_lost(self):
        lead = self.lt.create_lead("Lost Lead")
        self.lt.update_stage(lead.id, "lost")
        result = self.lt.won_deals()
        ids = [l.id for l in result]
        self.assertNotIn(lead.id, ids)

    def test_revenue_from_leads_zero_when_none(self):
        result = self.lt.revenue_from_leads()
        self.assertAlmostEqual(result, 0.0)

    def test_revenue_from_leads_sums_won_values(self):
        values = [10000.0, 25000.0, 5000.0]
        for i, v in enumerate(values):
            lead = self.lt.create_lead(f"Won Lead {i}", estimated_value=v)
            self.lt.update_stage(lead.id, "won")
        result = self.lt.revenue_from_leads(days=30)
        self.assertAlmostEqual(result, sum(values), places=1)

    def test_revenue_from_leads_excludes_non_won(self):
        won_lead = self.lt.create_lead("Won", estimated_value=10000.0)
        self.lt.update_stage(won_lead.id, "won")
        self.lt.create_lead("Pending", estimated_value=50000.0)
        lost_lead = self.lt.create_lead("Lost", estimated_value=20000.0)
        self.lt.update_stage(lost_lead.id, "lost")
        result = self.lt.revenue_from_leads(days=30)
        self.assertAlmostEqual(result, 10000.0, places=1)

    def test_revenue_from_leads_custom_days_window(self):
        lead = self.lt.create_lead("Recent Win", estimated_value=8000.0)
        self.lt.update_stage(lead.id, "won")
        result_7 = self.lt.revenue_from_leads(days=7)
        result_90 = self.lt.revenue_from_leads(days=90)
        self.assertAlmostEqual(result_7, result_90, places=1)

    def test_multiple_won_deals_all_counted(self):
        n = 5
        for i in range(n):
            lead = self.lt.create_lead(f"Deal {i}", estimated_value=1000.0)
            self.lt.update_stage(lead.id, "won")
        result = self.lt.won_deals(days=30)
        self.assertEqual(len(result), n)


# ---------------------------------------------------------------------------
# TestSearchLeads
# ---------------------------------------------------------------------------

class TestSearchLeads(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)
        # Seed some leads
        self.lt.create_lead("Alpha Corp", company="Alpha Inc", niche="SaaS")
        self.lt.create_lead("Beta Solutions", company="Beta Ltd", niche="Consulting")
        self.lt.create_lead("Gamma Dynamics", company="Gamma Corp", niche="SaaS")

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_search_leads_returns_json_string(self):
        result = self.lt.search_leads("Alpha")
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)

    def test_search_leads_by_name(self):
        result = json.loads(self.lt.search_leads("Alpha"))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Alpha Corp")

    def test_search_leads_by_company(self):
        result = json.loads(self.lt.search_leads("Beta Ltd"))
        self.assertEqual(len(result), 1)
        self.assertIn("Beta", result[0]["name"])

    def test_search_leads_by_niche(self):
        result = json.loads(self.lt.search_leads("SaaS"))
        self.assertEqual(len(result), 2)

    def test_search_leads_case_insensitive_via_like(self):
        # SQLite LIKE is case-insensitive for ASCII
        result_upper = json.loads(self.lt.search_leads("ALPHA"))
        result_lower = json.loads(self.lt.search_leads("alpha"))
        # Both should return the same leads
        ids_upper = {r["id"] for r in result_upper}
        ids_lower = {r["id"] for r in result_lower}
        self.assertEqual(ids_upper, ids_lower)

    def test_search_leads_no_match_returns_empty_list(self):
        result = json.loads(self.lt.search_leads("ZZZ_No_Match_9999"))
        self.assertEqual(result, [])

    def test_search_leads_result_has_required_fields(self):
        result = json.loads(self.lt.search_leads("Alpha"))
        self.assertGreater(len(result), 0)
        entry = result[0]
        for field in ("id", "name", "company", "stage", "estimated_value", "assigned_to"):
            self.assertIn(field, entry)

    def test_search_leads_partial_match(self):
        result = json.loads(self.lt.search_leads("Corp"))
        # "Alpha Corp" name + "Gamma Corp" company both match
        self.assertGreater(len(result), 0)

    def test_search_leads_respects_limit_of_20(self):
        # Add 25 leads all matching the same query
        for i in range(25):
            self.lt.create_lead(f"Searchable Lead {i}", niche="searchable_niche")
        result = json.loads(self.lt.search_leads("searchable_niche"))
        self.assertLessEqual(len(result), 20)


# ---------------------------------------------------------------------------
# TestUpdateLead
# ---------------------------------------------------------------------------

class TestUpdateLead(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)
        self.lead = self.lt.create_lead("Update Test", estimated_value=1000.0)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_update_lead_email(self):
        self.lt.update_lead(self.lead.id, contact_email="new@email.com")
        fetched = self.lt.get_lead(self.lead.id)
        self.assertEqual(fetched.contact_email, "new@email.com")

    def test_update_lead_estimated_value(self):
        self.lt.update_lead(self.lead.id, estimated_value=99999.0)
        fetched = self.lt.get_lead(self.lead.id)
        self.assertAlmostEqual(fetched.estimated_value, 99999.0)

    def test_update_lead_notes(self):
        self.lt.update_lead(self.lead.id, notes="Updated notes here")
        fetched = self.lt.get_lead(self.lead.id)
        self.assertEqual(fetched.notes, "Updated notes here")

    def test_update_lead_tags(self):
        self.lt.update_lead(self.lead.id, tags=["vip", "urgent"])
        fetched = self.lt.get_lead(self.lead.id)
        self.assertIn("vip", fetched.tags)
        self.assertIn("urgent", fetched.tags)

    def test_update_lead_no_valid_fields_returns_message(self):
        result = self.lt.update_lead(self.lead.id, invalid_field="foo")
        self.assertIn("No valid fields", result)

    def test_update_lead_returns_string(self):
        result = self.lt.update_lead(self.lead.id, notes="test")
        self.assertIsInstance(result, str)

    def test_update_lead_multiple_fields(self):
        self.lt.update_lead(
            self.lead.id,
            contact_email="multi@test.com",
            notes="Multiple fields",
            estimated_value=55000.0
        )
        fetched = self.lt.get_lead(self.lead.id)
        self.assertEqual(fetched.contact_email, "multi@test.com")
        self.assertEqual(fetched.notes, "Multiple fields")
        self.assertAlmostEqual(fetched.estimated_value, 55000.0)


# ---------------------------------------------------------------------------
# TestToolWrappers
# ---------------------------------------------------------------------------

class TestToolWrappers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_track_lead_returns_string(self):
        result = self.lt.track_lead("Tool Lead")
        self.assertIsInstance(result, str)

    def test_track_lead_includes_lead_id(self):
        result = self.lt.track_lead("Tool Lead 2")
        self.assertIn("#", result)

    def test_track_lead_includes_name(self):
        result = self.lt.track_lead("Special Corp")
        self.assertIn("Special Corp", result)

    def test_track_lead_includes_value(self):
        result = self.lt.track_lead("Value Lead", estimated_value=5000.0)
        self.assertIn("5000", result)

    def test_track_lead_creates_lead_in_db(self):
        self.lt.track_lead("DB Lead", company="DB Corp")
        found = self.lt.find_lead("DB Lead")
        self.assertIsNotNone(found)

    def test_move_lead_stage_wrapper_returns_string(self):
        lead = self.lt.create_lead("Stage Lead")
        result = self.lt.move_lead_stage(lead.id, "contacted")
        self.assertIsInstance(result, str)

    def test_get_pipeline_report_returns_json_string(self):
        self.lt.create_lead("Report Lead", estimated_value=10000.0)
        result = self.lt.get_pipeline_report()
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIn("total_leads", parsed)

    def test_get_followup_leads_returns_json_string(self):
        result = self.lt.get_followup_leads()
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)

    def test_search_leads_tool_returns_json_string(self):
        self.lt.create_lead("Searchable", company="SearchCo")
        result = self.lt.search_leads("SearchCo")
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)

    def test_track_lead_with_all_params(self):
        result = self.lt.track_lead(
            name="Full Lead",
            source="referral",
            contact_email="contact@full.com",
            company="Full Corp",
            estimated_value=30000.0,
            notes="Full test",
            assigned_to="closer",
        )
        self.assertIn("Full Lead", result)
        found = self.lt.find_lead("Full Lead")
        self.assertIsNotNone(found)
        self.assertEqual(found.assigned_to, "closer")


# ---------------------------------------------------------------------------
# TestDatabasePersistence
# ---------------------------------------------------------------------------

class TestDatabasePersistence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_lead_persists_across_module_reloads(self):
        lt1 = _make_module(self.db_path)
        lead = lt1.create_lead("Persistent Lead", estimated_value=12345.0)
        lead_id = lead.id

        lt2 = _make_module(self.db_path)
        fetched = lt2.get_lead(lead_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Persistent Lead")
        self.assertAlmostEqual(fetched.estimated_value, 12345.0)

    def test_stage_update_persists(self):
        lt1 = _make_module(self.db_path)
        lead = lt1.create_lead("Stage Persist")
        lt1.update_stage(lead.id, "negotiating")

        lt2 = _make_module(self.db_path)
        fetched = lt2.get_lead(lead.id)
        self.assertEqual(fetched.stage, "negotiating")

    def test_activities_persist(self):
        lt1 = _make_module(self.db_path)
        lead = lt1.create_lead("Activity Persist")
        lt1.add_note(lead.id, "scout", "First note")
        lt1.add_note(lead.id, "scout", "Second note")

        lt2 = _make_module(self.db_path)
        activities = lt2.get_activities(lead.id)
        self.assertGreaterEqual(len(activities), 3)  # created + 2 notes

    def test_schema_tables_created(self):
        lt = _make_module(self.db_path)
        lt._ensure()
        conn = sqlite3.connect(self.db_path)
        tables = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        self.assertIn("leads", tables)
        self.assertIn("lead_activities", tables)


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.lt = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_create_lead_with_zero_value(self):
        lead = self.lt.create_lead("Zero Value", estimated_value=0.0)
        self.assertAlmostEqual(lead.estimated_value, 0.0)

    def test_create_lead_with_very_large_value(self):
        lead = self.lt.create_lead("Big Deal", estimated_value=10_000_000.0)
        fetched = self.lt.get_lead(lead.id)
        self.assertAlmostEqual(fetched.estimated_value, 10_000_000.0, places=0)

    def test_create_multiple_leads_same_email(self):
        # Should not raise — no unique constraint on email
        l1 = self.lt.create_lead("Dup Email 1", contact_email="same@email.com")
        l2 = self.lt.create_lead("Dup Email 2", contact_email="same@email.com")
        self.assertNotEqual(l1.id, l2.id)

    def test_create_lead_empty_name_allowed(self):
        # The DB allows empty strings; no uniqueness/required constraint
        lead = self.lt.create_lead("")
        self.assertIsNotNone(lead)
        self.assertGreater(lead.id, 0)

    def test_update_stage_with_agent_and_note(self):
        lead = self.lt.create_lead("Agent Note Lead")
        result = self.lt.update_stage(lead.id, "contacted", agent="closer", note="Made contact via phone")
        self.assertNotIn("ERROR", result)
        activities = self.lt.get_activities(lead.id)
        stage_change = next((a for a in activities if a["activity"] == "stage_change"), None)
        self.assertIsNotNone(stage_change)
        self.assertIn("Made contact via phone", stage_change["note"])

    def test_lead_with_unicode_content(self):
        lead = self.lt.create_lead(
            name="Müller GmbH",
            company="Société Générale",
            notes="héros façon être"
        )
        fetched = self.lt.get_lead(lead.id)
        self.assertEqual(fetched.name, "Müller GmbH")
        self.assertEqual(fetched.company, "Société Générale")

    def test_pipeline_summary_with_mixed_stages(self):
        stages = ["new", "contacted", "proposal_sent", "negotiating", "won", "lost"]
        for stage in stages:
            lead = self.lt.create_lead(f"Stage {stage}", estimated_value=1000.0)
            self.lt.update_stage(lead.id, stage)
        summary = self.lt.pipeline_summary()
        self.assertEqual(summary["total_leads"], 6)
        for stage in stages:
            self.assertIn(stage, summary["pipeline"])

    def test_search_leads_empty_query_returns_all_up_to_limit(self):
        for i in range(5):
            self.lt.create_lead(f"Lead {i}")
        result = json.loads(self.lt.search_leads(""))
        # Empty string LIKE %% matches everything
        self.assertGreaterEqual(len(result), 5)

    def test_leads_due_for_followup_zero_days(self):
        """A lead updated now with 0-day threshold: only those not updated in 0 days are due."""
        lead = self.lt.create_lead("Now Lead")
        self.lt.update_stage(lead.id, "contacted")
        result = self.lt.leads_due_for_followup(days_since_update=0)
        # A just-updated lead should NOT be overdue for 0-day threshold
        # (updated_at is greater than cutoff = now - 0)
        ids = [l.id for l in result]
        self.assertNotIn(lead.id, ids)

    def test_revenue_from_leads_excludes_very_old_wins(self):
        lead = self.lt.create_lead("Old Win", estimated_value=9999.0)
        self.lt.update_stage(lead.id, "won")
        past_ts = time.time() - 100 * 86400  # 100 days ago
        conn = sqlite3.connect(self.db_path)
        conn.execute("UPDATE leads SET updated_at=? WHERE id=?", (past_ts, lead.id))
        conn.commit()
        conn.close()
        revenue = self.lt.revenue_from_leads(days=30)
        self.assertAlmostEqual(revenue, 0.0)

    def test_lead_to_row_and_from_row_round_trip(self):
        """Verify Lead.to_row() and Lead.from_row() are consistent."""
        original = self.lt.create_lead(
            "Round Trip", source="web", company="RT Corp",
            estimated_value=7500.0, tags=["a", "b"]
        )
        fetched = self.lt.get_lead(original.id)
        self.assertEqual(fetched.name, original.name)
        self.assertEqual(fetched.source, original.source)
        self.assertEqual(fetched.company, original.company)
        self.assertAlmostEqual(fetched.estimated_value, original.estimated_value)
        self.assertEqual(sorted(fetched.tags), sorted(original.tags))


if __name__ == "__main__":
    unittest.main()
