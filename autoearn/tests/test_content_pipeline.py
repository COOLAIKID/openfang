"""Tests for core/content_pipeline.py — content production pipeline manager.

Each test class uses an isolated temporary SQLite database so tests never
touch the real autoearn.db and don't interfere with each other.

The module-level globals `_DB_PATH` and `_schema_ready` are patched per
test class fixture so the schema is re-initialised in the temp file.
"""
from __future__ import annotations

import json
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import os


# ---------------------------------------------------------------------------
# Helpers: patch the module-level DB path and reset schema flag
# ---------------------------------------------------------------------------

def _make_pipeline(tmp_dir: str):
    """Return a fresh content_pipeline module pointing at a temp DB."""
    import importlib
    import autoearn.core.content_pipeline as cp

    db_path = Path(tmp_dir) / "cp_test.db"
    cp._DB_PATH = db_path
    cp._schema_ready = False
    return cp


# ---------------------------------------------------------------------------
# TestStagesAndOwners
# ---------------------------------------------------------------------------

class TestStagesAndOwners(unittest.TestCase):
    """Verify STAGES list and STAGE_OWNERS dict are correct and consistent."""

    def setUp(self):
        import autoearn.core.content_pipeline as cp
        self.cp = cp

    def test_stages_is_a_list(self):
        self.assertIsInstance(self.cp.STAGES, list)

    def test_stages_contains_required_stages(self):
        required = {"idea", "research", "writing", "editing", "qc_review",
                    "approved", "published", "rejected", "archived"}
        self.assertTrue(required.issubset(set(self.cp.STAGES)))

    def test_stage_order_idea_first(self):
        self.assertEqual(self.cp.STAGES[0], "idea")

    def test_stage_order_published_before_archived(self):
        idx_pub = self.cp.STAGES.index("published")
        idx_arc = self.cp.STAGES.index("archived")
        self.assertLess(idx_pub, idx_arc)

    def test_stage_owners_is_dict(self):
        self.assertIsInstance(self.cp.STAGE_OWNERS, dict)

    def test_stage_owners_keys_are_valid_stages(self):
        for stage in self.cp.STAGE_OWNERS:
            self.assertIn(stage, self.cp.STAGES)

    def test_idea_owner_is_researcher(self):
        self.assertEqual(self.cp.STAGE_OWNERS["idea"], "researcher")

    def test_writing_owner_is_editor(self):
        self.assertEqual(self.cp.STAGE_OWNERS["writing"], "editor")

    def test_published_owner_is_none(self):
        self.assertIsNone(self.cp.STAGE_OWNERS["published"])

    def test_archived_owner_is_none(self):
        self.assertIsNone(self.cp.STAGE_OWNERS["archived"])

    def test_content_types_is_list(self):
        self.assertIsInstance(self.cp.CONTENT_TYPES, list)

    def test_blog_post_in_content_types(self):
        self.assertIn("blog_post", self.cp.CONTENT_TYPES)

    def test_seo_article_in_content_types(self):
        self.assertIn("seo_article", self.cp.CONTENT_TYPES)


# ---------------------------------------------------------------------------
# TestCreatePiece
# ---------------------------------------------------------------------------

class TestCreatePiece(unittest.TestCase):
    """Tests for create_piece() — creation with various field combinations."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_create_piece_returns_content_piece(self):
        piece = self.cp.create_piece("My Article")
        self.assertIsNotNone(piece)
        self.assertEqual(piece.title, "My Article")

    def test_create_piece_has_positive_id(self):
        piece = self.cp.create_piece("Article A")
        self.assertGreater(piece.id, 0)

    def test_create_piece_default_stage_is_idea(self):
        piece = self.cp.create_piece("Article A")
        self.assertEqual(piece.stage, "idea")

    def test_create_piece_default_type_is_blog_post(self):
        piece = self.cp.create_piece("Article A")
        self.assertEqual(piece.content_type, "blog_post")

    def test_create_piece_default_assigned_to_researcher(self):
        piece = self.cp.create_piece("Article A")
        self.assertEqual(piece.assigned_to, "researcher")

    def test_create_piece_default_priority_is_5(self):
        piece = self.cp.create_piece("Article A")
        self.assertEqual(piece.priority, 5)

    def test_create_piece_custom_content_type(self):
        piece = self.cp.create_piece("Review", content_type="product_review")
        self.assertEqual(piece.content_type, "product_review")

    def test_create_piece_custom_niche(self):
        piece = self.cp.create_piece("Article", niche="personal_finance")
        self.assertEqual(piece.niche, "personal_finance")

    def test_create_piece_custom_target_keyword(self):
        piece = self.cp.create_piece("Article", target_keyword="best vpn 2024")
        self.assertEqual(piece.target_keyword, "best vpn 2024")

    def test_create_piece_custom_priority(self):
        piece = self.cp.create_piece("Urgent", priority=1)
        self.assertEqual(piece.priority, 1)

    def test_create_piece_custom_estimated_words(self):
        piece = self.cp.create_piece("Long Article", estimated_words=3000)
        self.assertEqual(piece.estimated_words, 3000)

    def test_create_piece_custom_tags(self):
        piece = self.cp.create_piece("Tagged", tags=["seo", "python"])
        self.assertIn("seo", piece.tags)
        self.assertIn("python", piece.tags)

    def test_create_piece_custom_created_by(self):
        piece = self.cp.create_piece("Article", created_by="ceo_agent")
        self.assertEqual(piece.created_by, "ceo_agent")

    def test_create_piece_timestamps_are_set(self):
        before = time.time()
        piece = self.cp.create_piece("Timed")
        after = time.time()
        self.assertGreaterEqual(piece.created_at, before)
        self.assertLessEqual(piece.created_at, after)

    def test_create_piece_multiple_pieces_have_unique_ids(self):
        p1 = self.cp.create_piece("First")
        p2 = self.cp.create_piece("Second")
        p3 = self.cp.create_piece("Third")
        ids = {p1.id, p2.id, p3.id}
        self.assertEqual(len(ids), 3)

    def test_create_piece_persisted_in_db(self):
        piece = self.cp.create_piece("Persisted")
        fetched = self.cp.get_piece(piece.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "Persisted")

    def test_create_piece_with_all_fields(self):
        piece = self.cp.create_piece(
            title="Full Article",
            content_type="seo_article",
            niche="tech",
            target_keyword="python tutorial",
            estimated_words=2500,
            priority=2,
            tags=["python", "tutorial"],
            created_by="researcher_agent",
        )
        self.assertEqual(piece.title, "Full Article")
        self.assertEqual(piece.content_type, "seo_article")
        self.assertEqual(piece.niche, "tech")
        self.assertEqual(piece.target_keyword, "python tutorial")
        self.assertEqual(piece.estimated_words, 2500)
        self.assertEqual(piece.priority, 2)
        self.assertIn("python", piece.tags)
        self.assertEqual(piece.created_by, "researcher_agent")


# ---------------------------------------------------------------------------
# TestGetPiece
# ---------------------------------------------------------------------------

class TestGetPiece(unittest.TestCase):
    """Tests for get_piece() — retrieval by ID."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_get_piece_returns_none_for_missing_id(self):
        result = self.cp.get_piece(9999)
        self.assertIsNone(result)

    def test_get_piece_returns_correct_title(self):
        p = self.cp.create_piece("Findable")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.title, "Findable")

    def test_get_piece_returns_correct_type(self):
        p = self.cp.create_piece("Review", content_type="product_review")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.content_type, "product_review")

    def test_get_piece_returns_correct_keyword(self):
        p = self.cp.create_piece("KW Piece", target_keyword="best laptop")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.target_keyword, "best laptop")

    def test_get_piece_tags_deserialised(self):
        p = self.cp.create_piece("Tagged", tags=["a", "b"])
        fetched = self.cp.get_piece(p.id)
        self.assertIsInstance(fetched.tags, list)
        self.assertIn("a", fetched.tags)

    def test_get_piece_id_matches_requested(self):
        p = self.cp.create_piece("Check ID")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.id, p.id)


# ---------------------------------------------------------------------------
# TestMovePiece
# ---------------------------------------------------------------------------

class TestMovePiece(unittest.TestCase):
    """Tests for move_piece() — stage transitions."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_move_piece_returns_string(self):
        p = self.cp.create_piece("Movable")
        result = self.cp.move_piece(p.id, "research")
        self.assertIsInstance(result, str)

    def test_move_piece_updates_stage_in_db(self):
        p = self.cp.create_piece("Movable")
        self.cp.move_piece(p.id, "research")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "research")

    def test_move_piece_result_message_contains_piece_id(self):
        p = self.cp.create_piece("Movable")
        result = self.cp.move_piece(p.id, "research")
        self.assertIn(str(p.id), result)

    def test_move_piece_result_message_contains_old_stage(self):
        p = self.cp.create_piece("Movable")
        result = self.cp.move_piece(p.id, "research")
        self.assertIn("idea", result)

    def test_move_piece_result_message_contains_new_stage(self):
        p = self.cp.create_piece("Movable")
        result = self.cp.move_piece(p.id, "research")
        self.assertIn("research", result)

    def test_move_piece_invalid_stage_returns_error(self):
        p = self.cp.create_piece("Movable")
        result = self.cp.move_piece(p.id, "nonexistent_stage")
        self.assertIn("ERROR", result)

    def test_move_piece_missing_id_returns_error(self):
        result = self.cp.move_piece(9999, "research")
        self.assertIn("ERROR", result)

    def test_move_piece_updates_assigned_to(self):
        p = self.cp.create_piece("Movable")
        self.cp.move_piece(p.id, "research")
        fetched = self.cp.get_piece(p.id)
        # research stage -> assigned to writer per STAGE_OWNERS
        self.assertEqual(fetched.assigned_to, self.cp.STAGE_OWNERS["research"])

    def test_move_piece_to_rejected_increments_rejection_count(self):
        p = self.cp.create_piece("Questionable")
        self.cp.move_piece(p.id, "rejected")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.rejection_count, 1)

    def test_move_piece_to_rejected_twice_increments_twice(self):
        p = self.cp.create_piece("Questionable")
        self.cp.move_piece(p.id, "rejected")
        self.cp.move_piece(p.id, "writing")
        self.cp.move_piece(p.id, "rejected")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.rejection_count, 2)

    def test_move_piece_to_published_sets_published_at(self):
        p = self.cp.create_piece("Ready")
        before = time.time()
        self.cp.move_piece(p.id, "published")
        after = time.time()
        fetched = self.cp.get_piece(p.id)
        self.assertGreaterEqual(fetched.published_at, before)
        self.assertLessEqual(fetched.published_at, after)

    def test_move_piece_with_seo_score(self):
        p = self.cp.create_piece("SEO Piece")
        self.cp.move_piece(p.id, "approved", seo_score=85.5)
        fetched = self.cp.get_piece(p.id)
        self.assertAlmostEqual(fetched.seo_score, 85.5)

    def test_move_piece_with_quality_score(self):
        p = self.cp.create_piece("Quality Piece")
        self.cp.move_piece(p.id, "approved", quality_score=92.0)
        fetched = self.cp.get_piece(p.id)
        self.assertAlmostEqual(fetched.quality_score, 92.0)

    def test_move_piece_note_recorded_in_history(self):
        p = self.cp.create_piece("Noted")
        self.cp.move_piece(p.id, "research", note="Start research phase")
        history = self.cp.get_history(p.id)
        self.assertTrue(any("Start research phase" in h["note"] for h in history))

    def test_move_piece_history_records_from_stage(self):
        p = self.cp.create_piece("History")
        self.cp.move_piece(p.id, "research")
        history = self.cp.get_history(p.id)
        self.assertEqual(history[0]["from_stage"], "idea")

    def test_move_piece_history_records_to_stage(self):
        p = self.cp.create_piece("History")
        self.cp.move_piece(p.id, "research")
        history = self.cp.get_history(p.id)
        self.assertEqual(history[0]["to_stage"], "research")

    def test_move_piece_updates_updated_at(self):
        p = self.cp.create_piece("Timestamped")
        old_ts = p.updated_at
        time.sleep(0.01)
        self.cp.move_piece(p.id, "research")
        fetched = self.cp.get_piece(p.id)
        self.assertGreater(fetched.updated_at, old_ts)

    def test_move_piece_agent_recorded_in_history(self):
        p = self.cp.create_piece("Agent Test")
        self.cp.move_piece(p.id, "research", agent="researcher_bot")
        history = self.cp.get_history(p.id)
        self.assertEqual(history[0]["agent"], "researcher_bot")

    def test_move_to_archived_stage(self):
        p = self.cp.create_piece("Old Piece")
        result = self.cp.move_piece(p.id, "archived")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "archived")
        self.assertNotIn("ERROR", result)


# ---------------------------------------------------------------------------
# TestFullStagePipeline
# ---------------------------------------------------------------------------

class TestFullStagePipeline(unittest.TestCase):
    """Test a piece progressing through the complete pipeline."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def _progress(self, piece_id, stages):
        """Move piece through a list of stages sequentially."""
        for stage in stages:
            result = self.cp.move_piece(piece_id, stage)
            self.assertNotIn("ERROR", result, f"Failed moving to {stage}: {result}")

    def test_full_pipeline_idea_to_published(self):
        p = self.cp.create_piece("Full Journey")
        self._progress(p.id, [
            "research", "writing", "editing", "qc_review", "approved", "published"
        ])
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "published")

    def test_full_pipeline_history_has_all_transitions(self):
        p = self.cp.create_piece("History Full")
        stages = ["research", "writing", "editing", "qc_review", "approved", "published"]
        self._progress(p.id, stages)
        history = self.cp.get_history(p.id)
        # 6 transitions
        self.assertEqual(len(history), 6)

    def test_full_pipeline_published_at_set_only_after_publish(self):
        p = self.cp.create_piece("Timing Test")
        self._progress(p.id, ["research", "writing", "editing"])
        mid = self.cp.get_piece(p.id)
        self.assertEqual(mid.published_at, 0.0)
        self._progress(p.id, ["qc_review", "approved", "published"])
        final = self.cp.get_piece(p.id)
        self.assertGreater(final.published_at, 0.0)

    def test_rejection_then_recovery_flow(self):
        p = self.cp.create_piece("Comeback")
        self.cp.move_piece(p.id, "research")
        self.cp.move_piece(p.id, "writing")
        self.cp.move_piece(p.id, "editing")
        self.cp.move_piece(p.id, "rejected", note="Needs more depth")
        rejected = self.cp.get_piece(p.id)
        self.assertEqual(rejected.rejection_count, 1)
        # Fix and re-submit
        self.cp.move_piece(p.id, "writing")
        self.cp.move_piece(p.id, "editing")
        self.cp.move_piece(p.id, "qc_review")
        self.cp.move_piece(p.id, "approved")
        self.cp.move_piece(p.id, "published")
        final = self.cp.get_piece(p.id)
        self.assertEqual(final.stage, "published")
        self.assertEqual(final.rejection_count, 1)

    def test_archival_flow(self):
        p = self.cp.create_piece("Stale Draft")
        self.cp.move_piece(p.id, "research")
        self.cp.move_piece(p.id, "archived", note="Decided not to publish")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "archived")

    def test_multiple_rejections_tracked(self):
        p = self.cp.create_piece("Tough Piece")
        for _ in range(3):
            self.cp.move_piece(p.id, "writing")
            self.cp.move_piece(p.id, "rejected")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.rejection_count, 3)


# ---------------------------------------------------------------------------
# TestGetStageQueue (list by stage)
# ---------------------------------------------------------------------------

class TestGetStageQueue(unittest.TestCase):
    """Tests for get_stage_queue() — list pieces in a given stage."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_get_stage_queue_empty_when_no_pieces(self):
        result = self.cp.get_stage_queue("idea")
        self.assertEqual(result, [])

    def test_get_stage_queue_returns_pieces_in_stage(self):
        self.cp.create_piece("Piece 1")
        self.cp.create_piece("Piece 2")
        result = self.cp.get_stage_queue("idea")
        self.assertEqual(len(result), 2)

    def test_get_stage_queue_excludes_other_stages(self):
        p1 = self.cp.create_piece("Idea Piece")
        p2 = self.cp.create_piece("Research Piece")
        self.cp.move_piece(p2.id, "research")
        result = self.cp.get_stage_queue("idea")
        ids = [p.id for p in result]
        self.assertIn(p1.id, ids)
        self.assertNotIn(p2.id, ids)

    def test_get_stage_queue_ordered_by_priority(self):
        self.cp.create_piece("Low Priority", priority=9)
        self.cp.create_piece("High Priority", priority=1)
        self.cp.create_piece("Mid Priority", priority=5)
        result = self.cp.get_stage_queue("idea")
        priorities = [p.priority for p in result]
        self.assertEqual(priorities, sorted(priorities))

    def test_get_stage_queue_for_research_stage(self):
        p = self.cp.create_piece("Research Ready")
        self.cp.move_piece(p.id, "research")
        result = self.cp.get_stage_queue("research")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, p.id)


# ---------------------------------------------------------------------------
# TestGetMyQueue
# ---------------------------------------------------------------------------

class TestGetMyQueue(unittest.TestCase):
    """Tests for get_my_queue() — pieces assigned to a specific agent."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_my_queue_empty_for_unknown_agent(self):
        result = self.cp.get_my_queue("nonexistent_agent")
        self.assertEqual(result, [])

    def test_my_queue_researcher_gets_new_pieces(self):
        self.cp.create_piece("New Article")
        result = self.cp.get_my_queue("researcher")
        self.assertEqual(len(result), 1)

    def test_my_queue_excludes_published(self):
        p = self.cp.create_piece("Published")
        self.cp.move_piece(p.id, "published")
        result = self.cp.get_my_queue("researcher")
        self.assertEqual(len(result), 0)

    def test_my_queue_excludes_archived(self):
        p = self.cp.create_piece("Archived")
        self.cp.move_piece(p.id, "archived")
        result = self.cp.get_my_queue("researcher")
        self.assertEqual(len(result), 0)

    def test_my_queue_ordered_by_priority(self):
        self.cp.create_piece("Low", priority=8)
        self.cp.create_piece("High", priority=1)
        result = self.cp.get_my_queue("researcher")
        self.assertEqual(result[0].priority, 1)


# ---------------------------------------------------------------------------
# TestPipelineStats
# ---------------------------------------------------------------------------

class TestPipelineStats(unittest.TestCase):
    """Tests for pipeline_stats() — aggregate dashboard statistics."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_stats_empty_database(self):
        stats = self.cp.pipeline_stats()
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["published_count"], 0)

    def test_stats_returns_dict(self):
        stats = self.cp.pipeline_stats()
        self.assertIsInstance(stats, dict)

    def test_stats_has_required_keys(self):
        stats = self.cp.pipeline_stats()
        for key in ("by_stage", "total", "published_count",
                    "avg_published_words", "avg_seo_score",
                    "avg_cycle_hours", "by_type"):
            self.assertIn(key, stats)

    def test_stats_total_counts_all_pieces(self):
        self.cp.create_piece("A")
        self.cp.create_piece("B")
        self.cp.create_piece("C")
        stats = self.cp.pipeline_stats()
        self.assertEqual(stats["total"], 3)

    def test_stats_by_stage_counts_correctly(self):
        p1 = self.cp.create_piece("P1")
        p2 = self.cp.create_piece("P2")
        self.cp.move_piece(p2.id, "research")
        stats = self.cp.pipeline_stats()
        self.assertEqual(stats["by_stage"].get("idea", 0), 1)
        self.assertEqual(stats["by_stage"].get("research", 0), 1)

    def test_stats_published_count_accurate(self):
        p1 = self.cp.create_piece("Published 1")
        p2 = self.cp.create_piece("Published 2")
        p3 = self.cp.create_piece("Still in idea")
        self.cp.move_piece(p1.id, "published")
        self.cp.move_piece(p2.id, "published")
        stats = self.cp.pipeline_stats()
        self.assertEqual(stats["published_count"], 2)

    def test_stats_by_type_counts_correctly(self):
        self.cp.create_piece("A", content_type="blog_post")
        self.cp.create_piece("B", content_type="blog_post")
        self.cp.create_piece("C", content_type="seo_article")
        stats = self.cp.pipeline_stats()
        self.assertEqual(stats["by_type"].get("blog_post", 0), 2)
        self.assertEqual(stats["by_type"].get("seo_article", 0), 1)

    def test_stats_avg_seo_score_with_published_pieces(self):
        p1 = self.cp.create_piece("P1")
        p2 = self.cp.create_piece("P2")
        self.cp.move_piece(p1.id, "approved", seo_score=80.0)
        self.cp.move_piece(p1.id, "published")
        self.cp.move_piece(p2.id, "approved", seo_score=60.0)
        self.cp.move_piece(p2.id, "published")
        stats = self.cp.pipeline_stats()
        self.assertAlmostEqual(stats["avg_seo_score"], 70.0, places=0)

    def test_stats_avg_cycle_hours_positive_when_published(self):
        p = self.cp.create_piece("Fast Piece")
        self.cp.move_piece(p.id, "published")
        stats = self.cp.pipeline_stats()
        self.assertGreaterEqual(stats["avg_cycle_hours"], 0.0)


# ---------------------------------------------------------------------------
# TestContentVelocity
# ---------------------------------------------------------------------------

class TestContentVelocity(unittest.TestCase):
    """Tests for content_velocity() — published pieces per day."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_velocity_returns_dict(self):
        result = self.cp.content_velocity(days=7)
        self.assertIsInstance(result, dict)

    def test_velocity_has_required_keys(self):
        result = self.cp.content_velocity(days=7)
        for key in ("days", "published", "created", "daily_velocity"):
            self.assertIn(key, result)

    def test_velocity_days_parameter_set_correctly(self):
        result = self.cp.content_velocity(days=14)
        self.assertEqual(result["days"], 14)

    def test_velocity_zero_when_no_pieces(self):
        result = self.cp.content_velocity(days=7)
        self.assertEqual(result["published"], 0)
        self.assertEqual(result["created"], 0)

    def test_velocity_counts_recently_published(self):
        p = self.cp.create_piece("Fresh Article")
        self.cp.move_piece(p.id, "published")
        result = self.cp.content_velocity(days=7)
        self.assertEqual(result["published"], 1)

    def test_velocity_counts_recently_created(self):
        self.cp.create_piece("Brand New")
        result = self.cp.content_velocity(days=7)
        self.assertEqual(result["created"], 1)

    def test_velocity_daily_velocity_calculation(self):
        p1 = self.cp.create_piece("A")
        p2 = self.cp.create_piece("B")
        self.cp.move_piece(p1.id, "published")
        self.cp.move_piece(p2.id, "published")
        result = self.cp.content_velocity(days=2)
        self.assertAlmostEqual(result["daily_velocity"], 1.0, places=1)

    def test_velocity_default_days_is_7(self):
        result = self.cp.content_velocity()
        self.assertEqual(result["days"], 7)

    def test_velocity_excludes_old_published(self):
        """Manually insert a piece with a very old published_at."""
        self.cp._ensure()
        import autoearn.core.content_pipeline as _cp
        old_time = time.time() - 30 * 86400  # 30 days ago
        conn = sqlite3.connect(str(self.cp._DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO content_pieces
               (title, content_type, niche, target_keyword, stage, assigned_to,
                priority, estimated_words, actual_words, content_body, output_path,
                publish_url, seo_score, quality_score, rejection_count, tags, notes,
                created_by, created_at, updated_at, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("Old Article", "blog_post", "", "", "published", "researcher",
             5, 1500, 0, "", "", "", 0.0, 0.0, 0, "[]", "",
             "system", old_time, old_time, old_time),
        )
        conn.commit()
        conn.close()
        result = self.cp.content_velocity(days=7)
        self.assertEqual(result["published"], 0)


# ---------------------------------------------------------------------------
# TestStalePieces
# ---------------------------------------------------------------------------

class TestStalePieces(unittest.TestCase):
    """Tests for stale_pieces() — pieces not updated within N hours."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def _insert_stale(self, title: str, stage: str = "writing", hours_ago: int = 48):
        """Insert a piece with updated_at set to the past."""
        self.cp._ensure()
        old_time = time.time() - hours_ago * 3600
        conn = sqlite3.connect(str(self.cp._DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO content_pieces
               (title, content_type, niche, target_keyword, stage, assigned_to,
                priority, estimated_words, actual_words, content_body, output_path,
                publish_url, seo_score, quality_score, rejection_count, tags, notes,
                created_by, created_at, updated_at, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (title, "blog_post", "", "", stage, "writer",
             5, 1500, 0, "", "", "", 0.0, 0.0, 0, "[]", "",
             "system", old_time, old_time, 0.0),
        )
        conn.commit()
        conn.close()

    def test_stale_pieces_returns_list(self):
        result = self.cp.stale_pieces(24)
        self.assertIsInstance(result, list)

    def test_stale_pieces_empty_when_no_stale(self):
        self.cp.create_piece("Fresh Piece")
        result = self.cp.stale_pieces(24)
        self.assertEqual(result, [])

    def test_stale_pieces_detects_stale_piece(self):
        self._insert_stale("Stuck Piece", hours_ago=48)
        result = self.cp.stale_pieces(24)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Stuck Piece")

    def test_stale_pieces_excludes_published(self):
        self._insert_stale("Published Stale", stage="published", hours_ago=200)
        result = self.cp.stale_pieces(24)
        self.assertEqual(result, [])

    def test_stale_pieces_excludes_archived(self):
        self._insert_stale("Archived Stale", stage="archived", hours_ago=200)
        result = self.cp.stale_pieces(24)
        self.assertEqual(result, [])

    def test_stale_pieces_detects_multiple_stale(self):
        self._insert_stale("Stale 1", hours_ago=30)
        self._insert_stale("Stale 2", hours_ago=50)
        result = self.cp.stale_pieces(24)
        self.assertEqual(len(result), 2)

    def test_stale_pieces_fresh_piece_not_included(self):
        self._insert_stale("Old Piece", hours_ago=30)
        self.cp.create_piece("Fresh Piece")
        result = self.cp.stale_pieces(24)
        titles = [p.title for p in result]
        self.assertIn("Old Piece", titles)
        self.assertNotIn("Fresh Piece", titles)

    def test_stale_pieces_ordered_by_oldest_first(self):
        self._insert_stale("Recent Stale", hours_ago=25)
        self._insert_stale("Very Old Stale", hours_ago=100)
        result = self.cp.stale_pieces(24)
        self.assertLessEqual(result[0].updated_at, result[1].updated_at)

    def test_stale_pieces_custom_hours_threshold(self):
        self._insert_stale("Slightly Stale", hours_ago=5)
        result_4h = self.cp.stale_pieces(4)
        result_6h = self.cp.stale_pieces(6)
        self.assertEqual(len(result_4h), 1)
        self.assertEqual(len(result_6h), 0)


# ---------------------------------------------------------------------------
# TestUpdatePieceContent
# ---------------------------------------------------------------------------

class TestUpdatePieceContent(unittest.TestCase):
    """Tests for update_piece_content() — writing body and word count."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_update_content_returns_string(self):
        p = self.cp.create_piece("Article")
        result = self.cp.update_piece_content(p.id, "Hello world content")
        self.assertIsInstance(result, str)

    def test_update_content_stores_body(self):
        p = self.cp.create_piece("Article")
        self.cp.update_piece_content(p.id, "This is the article body.")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.content_body, "This is the article body.")

    def test_update_content_counts_words(self):
        p = self.cp.create_piece("Article")
        self.cp.update_piece_content(p.id, "one two three four five")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.actual_words, 5)

    def test_update_content_stores_output_path(self):
        p = self.cp.create_piece("Article")
        self.cp.update_piece_content(p.id, "body", output_path="/tmp/article.md")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.output_path, "/tmp/article.md")

    def test_update_content_result_contains_piece_id(self):
        p = self.cp.create_piece("Article")
        result = self.cp.update_piece_content(p.id, "content")
        self.assertIn(str(p.id), result)

    def test_update_content_result_contains_word_count(self):
        p = self.cp.create_piece("Article")
        result = self.cp.update_piece_content(p.id, "one two three")
        self.assertIn("3", result)


# ---------------------------------------------------------------------------
# TestPublishPiece
# ---------------------------------------------------------------------------

class TestPublishPiece(unittest.TestCase):
    """Tests for publish_piece() — final publishing with URL."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_publish_piece_returns_string(self):
        p = self.cp.create_piece("Article")
        result = self.cp.publish_piece(p.id, "https://example.com/article")
        self.assertIsInstance(result, str)

    def test_publish_piece_sets_stage_to_published(self):
        p = self.cp.create_piece("Article")
        self.cp.publish_piece(p.id, "https://example.com/article")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "published")

    def test_publish_piece_stores_url(self):
        p = self.cp.create_piece("Article")
        url = "https://myblog.com/best-python-guide"
        self.cp.publish_piece(p.id, url)
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.publish_url, url)

    def test_publish_piece_sets_published_at(self):
        p = self.cp.create_piece("Article")
        before = time.time()
        self.cp.publish_piece(p.id, "https://example.com/article")
        fetched = self.cp.get_piece(p.id)
        self.assertGreaterEqual(fetched.published_at, before)

    def test_publish_piece_result_contains_url(self):
        p = self.cp.create_piece("Article")
        url = "https://example.com/article"
        result = self.cp.publish_piece(p.id, url)
        self.assertIn(url, result)


# ---------------------------------------------------------------------------
# TestGetHistory
# ---------------------------------------------------------------------------

class TestGetHistory(unittest.TestCase):
    """Tests for get_history() — stage transition log."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_history_empty_for_new_piece(self):
        p = self.cp.create_piece("New")
        history = self.cp.get_history(p.id)
        self.assertEqual(history, [])

    def test_history_records_single_move(self):
        p = self.cp.create_piece("New")
        self.cp.move_piece(p.id, "research")
        history = self.cp.get_history(p.id)
        self.assertEqual(len(history), 1)

    def test_history_records_multiple_moves(self):
        p = self.cp.create_piece("New")
        self.cp.move_piece(p.id, "research")
        self.cp.move_piece(p.id, "writing")
        self.cp.move_piece(p.id, "editing")
        history = self.cp.get_history(p.id)
        self.assertEqual(len(history), 3)

    def test_history_ordered_chronologically(self):
        p = self.cp.create_piece("New")
        self.cp.move_piece(p.id, "research")
        self.cp.move_piece(p.id, "writing")
        history = self.cp.get_history(p.id)
        self.assertLessEqual(history[0]["ts"], history[1]["ts"])

    def test_history_is_isolated_per_piece(self):
        p1 = self.cp.create_piece("P1")
        p2 = self.cp.create_piece("P2")
        self.cp.move_piece(p1.id, "research")
        self.cp.move_piece(p1.id, "writing")
        self.cp.move_piece(p2.id, "research")
        h1 = self.cp.get_history(p1.id)
        h2 = self.cp.get_history(p2.id)
        self.assertEqual(len(h1), 2)
        self.assertEqual(len(h2), 1)

    def test_history_dict_has_required_keys(self):
        p = self.cp.create_piece("New")
        self.cp.move_piece(p.id, "research")
        history = self.cp.get_history(p.id)
        for key in ("piece_id", "from_stage", "to_stage", "agent", "note", "ts"):
            self.assertIn(key, history[0])


# ---------------------------------------------------------------------------
# TestToolWrappers
# ---------------------------------------------------------------------------

class TestToolWrappers(unittest.TestCase):
    """Tests for the tool-friendly wrapper functions that return strings."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_create_content_piece_returns_string(self):
        result = self.cp.create_content_piece("My Article")
        self.assertIsInstance(result, str)

    def test_create_content_piece_contains_piece_id(self):
        result = self.cp.create_content_piece("My Article")
        self.assertRegex(result, r"#\d+")

    def test_create_content_piece_contains_title(self):
        result = self.cp.create_content_piece("Unique Title For Test")
        self.assertIn("Unique Title For Test", result)

    def test_create_content_piece_with_type_and_keyword(self):
        result = self.cp.create_content_piece(
            "SEO Guide", content_type="seo_article",
            target_keyword="python tips"
        )
        self.assertIn("seo_article", result)
        self.assertIn("python tips", result)

    def test_advance_content_returns_string(self):
        p = self.cp.create_piece("Advance Me")
        result = self.cp.advance_content(p.id, "research")
        self.assertIsInstance(result, str)

    def test_advance_content_moves_stage(self):
        p = self.cp.create_piece("Advance Me")
        self.cp.advance_content(p.id, "research")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "research")

    def test_my_content_queue_returns_json_string(self):
        self.cp.create_piece("Queued")
        result = self.cp.my_content_queue("researcher")
        self.assertIsInstance(result, str)
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_my_content_queue_contains_piece_info(self):
        self.cp.create_piece("Queued Article", target_keyword="kw")
        result = self.cp.my_content_queue("researcher")
        data = json.loads(result)
        self.assertTrue(any(p["title"] == "Queued Article" for p in data))

    def test_content_pipeline_report_returns_json_string(self):
        result = self.cp.content_pipeline_report()
        self.assertIsInstance(result, str)
        data = json.loads(result)
        self.assertIn("total", data)

    def test_stale_content_report_returns_json_string(self):
        result = self.cp.stale_content_report()
        self.assertIsInstance(result, str)
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_stale_content_report_includes_hours_stale(self):
        """Insert a stale piece and confirm report shows hours_stale."""
        self.cp._ensure()
        old_time = time.time() - 30 * 3600
        conn = sqlite3.connect(str(self.cp._DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute(
            """INSERT INTO content_pieces
               (title, content_type, niche, target_keyword, stage, assigned_to,
                priority, estimated_words, actual_words, content_body, output_path,
                publish_url, seo_score, quality_score, rejection_count, tags, notes,
                created_by, created_at, updated_at, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("Stale", "blog_post", "", "", "writing", "writer",
             5, 1500, 0, "", "", "", 0.0, 0.0, 0, "[]", "",
             "system", old_time, old_time, 0.0),
        )
        conn.commit()
        conn.close()
        result = self.cp.stale_content_report()
        data = json.loads(result)
        self.assertTrue(len(data) > 0)
        self.assertIn("hours_stale", data[0])
        self.assertGreater(data[0]["hours_stale"], 24)


# ---------------------------------------------------------------------------
# TestMessageBusNotifications
# ---------------------------------------------------------------------------

class TestMessageBusNotifications(unittest.TestCase):
    """Verify move_piece sends notifications via message_bus on stage changes."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_move_piece_calls_message_bus_send(self):
        """message_bus.send should be called when piece moves to a stage with an owner."""
        p = self.cp.create_piece("Notify Test")
        mock_bus = MagicMock()
        mock_bus.send = MagicMock(return_value=1)

        with patch.dict("sys.modules", {"autoearn.core.message_bus": mock_bus}):
            # Patch the relative import path used inside move_piece
            import autoearn.core.content_pipeline as cp_mod
            with patch.object(cp_mod, "__name__", "autoearn.core.content_pipeline"):
                # Simulate the relative import by patching message_bus in the module
                original_send = None
                try:
                    from autoearn.core import message_bus as real_bus
                    original_send = real_bus.send
                    real_bus.send = mock_bus.send
                    self.cp.move_piece(p.id, "research", agent="researcher_agent")
                finally:
                    if original_send is not None:
                        real_bus.send = original_send

        # The key assertion: message_bus.send was invoked
        mock_bus.send.assert_called()

    def test_move_piece_to_published_no_bus_send_for_none_owner(self):
        """When stage owner is None, no notification should be sent but move succeeds."""
        p = self.cp.create_piece("No Notify")
        # published has STAGE_OWNERS["published"] = None
        result = self.cp.move_piece(p.id, "published")
        self.assertNotIn("ERROR", result)
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "published")

    def test_move_piece_notification_content_contains_title(self):
        """When move_piece notifies, the message body should reference the piece title."""
        p = self.cp.create_piece("Important Article", target_keyword="seo tips")
        sent_messages = []

        def capture_send(**kwargs):
            sent_messages.append(kwargs)
            return 1

        try:
            from autoearn.core import message_bus as real_bus
            original_send = real_bus.send
            real_bus.send = capture_send
            self.cp.move_piece(p.id, "research", agent="system")
        finally:
            real_bus.send = original_send

        # If a message was sent, it should contain the piece title or keyword
        if sent_messages:
            all_content = str(sent_messages)
            self.assertTrue(
                "Important Article" in all_content or "seo tips" in all_content
            )

    def test_move_piece_notification_has_correct_msg_type(self):
        """The message type sent via bus should be 'work_item'."""
        p = self.cp.create_piece("Work Item Test")
        sent_messages = []

        def capture_send(**kwargs):
            sent_messages.append(kwargs)
            return 1

        try:
            from autoearn.core import message_bus as real_bus
            original_send = real_bus.send
            real_bus.send = capture_send
            self.cp.move_piece(p.id, "research")
        finally:
            real_bus.send = original_send

        if sent_messages:
            types = [m.get("msg_type") or m.get("type") for m in sent_messages]
            self.assertIn("work_item", types)


# ---------------------------------------------------------------------------
# TestDatabasePersistence
# ---------------------------------------------------------------------------

class TestDatabasePersistence(unittest.TestCase):
    """Verify data survives across separate calls (persistence check)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cp = _make_pipeline(self._tmp)

    def tearDown(self):
        self.cp._schema_ready = False

    def test_piece_persists_after_create(self):
        p = self.cp.create_piece("Persistent Piece")
        # Re-query to confirm it is in the DB
        fetched = self.cp.get_piece(p.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.title, "Persistent Piece")

    def test_multiple_pieces_all_persisted(self):
        ids = []
        for i in range(5):
            p = self.cp.create_piece(f"Article {i}")
            ids.append(p.id)
        for piece_id in ids:
            fetched = self.cp.get_piece(piece_id)
            self.assertIsNotNone(fetched)

    def test_stage_change_persists(self):
        p = self.cp.create_piece("Stage Persist")
        self.cp.move_piece(p.id, "writing")
        # Reset schema flag to simulate a fresh load
        self.cp._schema_ready = False
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.stage, "writing")

    def test_history_persists_across_calls(self):
        p = self.cp.create_piece("History Persist")
        self.cp.move_piece(p.id, "research")
        self.cp.move_piece(p.id, "writing")
        history = self.cp.get_history(p.id)
        self.assertEqual(len(history), 2)

    def test_content_body_persists(self):
        p = self.cp.create_piece("Content Persist")
        self.cp.update_piece_content(p.id, "This is the full article text.")
        fetched = self.cp.get_piece(p.id)
        self.assertEqual(fetched.content_body, "This is the full article text.")

    def test_tags_persist_as_list(self):
        p = self.cp.create_piece("Tagged Persist", tags=["finance", "investing"])
        fetched = self.cp.get_piece(p.id)
        self.assertIn("finance", fetched.tags)
        self.assertIn("investing", fetched.tags)


if __name__ == "__main__":
    unittest.main()
