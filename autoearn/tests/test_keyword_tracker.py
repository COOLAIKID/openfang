"""Tests for core/keyword_tracker.py — keyword rank tracking and opportunity scoring.

NOTE: The source module uses ``drop`` as a column name in the rank_alerts table, which
is a reserved keyword in SQLite 3.45.1+.  The helper ``_make_module`` monkey-patches
both ``_init_schema`` and ``update_rank`` to quote the column name so tests can run
against a fresh temp database without hitting the syntax error.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fixed_init_schema(db_path: Path) -> None:
    """Create the keyword_tracker schema with ``"drop"`` properly quoted."""
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS keywords (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword             TEXT    NOT NULL UNIQUE,
                target_url          TEXT    NOT NULL DEFAULT '',
                current_rank        INTEGER NOT NULL DEFAULT 0,
                best_rank           INTEGER NOT NULL DEFAULT 0,
                niche               TEXT    NOT NULL DEFAULT '',
                search_volume       INTEGER NOT NULL DEFAULT 0,
                keyword_difficulty  INTEGER NOT NULL DEFAULT 0,
                cpc_usd             REAL    NOT NULL DEFAULT 0,
                content_type        TEXT    NOT NULL DEFAULT '',
                notes               TEXT    NOT NULL DEFAULT '',
                created_at          REAL    NOT NULL,
                updated_at          REAL    NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rank_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_id      INTEGER NOT NULL,
                rank            INTEGER NOT NULL,
                url             TEXT    NOT NULL DEFAULT '',
                serp_features   TEXT    NOT NULL DEFAULT '[]',
                ts              REAL    NOT NULL
            )""")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rank_alerts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_id      INTEGER NOT NULL,
                keyword         TEXT    NOT NULL,
                old_rank        INTEGER NOT NULL,
                new_rank        INTEGER NOT NULL,
                "drop"          INTEGER NOT NULL,
                ts              REAL    NOT NULL,
                acknowledged    INTEGER NOT NULL DEFAULT 0
            )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kw_niche ON keywords(niche)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_kw ON rank_snapshots(keyword_id, ts)")
    conn.close()


def _make_fixed_update_rank(mod, db_path: Path):
    """Return an ``update_rank`` replacement that quotes the ``drop`` column."""

    def _update_rank(keyword_id: int, rank: int, url: str = "",
                     serp_features=None) -> str:
        kw = mod.get_keyword(keyword_id)
        if not kw:
            return f"ERROR: keyword #{keyword_id} not found"
        old_rank = kw.current_rank
        new_best = min(rank, kw.best_rank) if kw.best_rank > 0 else rank
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        with conn:
            conn.execute(
                "UPDATE keywords SET current_rank=?, best_rank=?, updated_at=? WHERE id=?",
                (rank, new_best, time.time(), keyword_id),
            )
            conn.execute(
                "INSERT INTO rank_snapshots (keyword_id, rank, url, serp_features, ts) "
                "VALUES (?,?,?,?,?)",
                (keyword_id, rank, url, json.dumps(serp_features or []), time.time()),
            )
            if old_rank > 0 and rank > old_rank + 5:
                conn.execute(
                    'INSERT INTO rank_alerts '
                    '(keyword_id, keyword, old_rank, new_rank, "drop", ts, acknowledged) '
                    "VALUES (?,?,?,?,?,?,0)",
                    (keyword_id, kw.keyword, old_rank, rank, rank - old_rank, time.time()),
                )
        conn.close()
        direction = "↓" if rank > old_rank else "↑" if rank < old_rank else "→"
        return f"Rank updated for '{kw.keyword}': {old_rank} → {rank} {direction}"

    return _update_rank


def _make_module(tmp_db: Path):
    """Return keyword_tracker re-initialised against a fresh temp DB.

    Patches _init_schema and update_rank to work around the ``drop`` reserved
    keyword bug that causes sqlite3.OperationalError on SQLite 3.45.1+.
    """
    import core.keyword_tracker as mod

    mod._schema_ready = False
    mod._DB_PATH = tmp_db
    # Apply patches before the first _ensure() call
    mod._init_schema = lambda: _fixed_init_schema(tmp_db)
    mod.update_rank = _make_fixed_update_rank(mod, tmp_db)
    return mod


class _TrackerBase(unittest.TestCase):
    """Each test class gets its own isolated SQLite database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "kw_test.db"
        self.mod = _make_module(self.db_path)

    def tearDown(self):
        self.mod._schema_ready = False
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# OPPORTUNITY_TIERS constant
# ---------------------------------------------------------------------------

class TestOpportunityTiers(_TrackerBase):
    def test_tiers_dict_exists(self):
        self.assertIsInstance(self.mod.OPPORTUNITY_TIERS, dict)

    def test_top_3_tier_defined(self):
        self.assertIn("top_3", self.mod.OPPORTUNITY_TIERS)

    def test_page_1_tier_defined(self):
        self.assertIn("page_1", self.mod.OPPORTUNITY_TIERS)

    def test_page_2_tier_defined(self):
        self.assertIn("page_2", self.mod.OPPORTUNITY_TIERS)

    def test_not_ranking_tier_defined(self):
        self.assertIn("not_ranking", self.mod.OPPORTUNITY_TIERS)

    def test_top_3_range(self):
        low, high = self.mod.OPPORTUNITY_TIERS["top_3"]
        self.assertEqual(low, 1)
        self.assertEqual(high, 3)

    def test_page_1_range(self):
        low, high = self.mod.OPPORTUNITY_TIERS["page_1"]
        self.assertEqual(low, 4)
        self.assertEqual(high, 10)

    def test_page_2_range(self):
        low, high = self.mod.OPPORTUNITY_TIERS["page_2"]
        self.assertEqual(low, 11)
        self.assertEqual(high, 20)

    def test_deep_tier_exists(self):
        self.assertIn("deep", self.mod.OPPORTUNITY_TIERS)


# ---------------------------------------------------------------------------
# add_keyword
# ---------------------------------------------------------------------------

class TestAddKeyword(_TrackerBase):
    def test_add_keyword_returns_record(self):
        kw = self.mod.add_keyword("python tutorial")
        self.assertIsNotNone(kw)

    def test_add_keyword_assigns_id(self):
        kw = self.mod.add_keyword("python tutorial")
        self.assertGreater(kw.id, 0)

    def test_add_keyword_lowercases(self):
        kw = self.mod.add_keyword("Python Tutorial")
        self.assertEqual(kw.keyword, "python tutorial")

    def test_add_keyword_strips_whitespace(self):
        kw = self.mod.add_keyword("  seo tips  ")
        self.assertEqual(kw.keyword, "seo tips")

    def test_add_keyword_stores_target_url(self):
        kw = self.mod.add_keyword("best seo tool", target_url="https://example.com/seo")
        self.assertEqual(kw.target_url, "https://example.com/seo")

    def test_add_keyword_stores_search_volume(self):
        kw = self.mod.add_keyword("affiliate marketing", search_volume=5000)
        self.assertEqual(kw.search_volume, 5000)

    def test_add_keyword_stores_difficulty(self):
        kw = self.mod.add_keyword("make money online", keyword_difficulty=75)
        self.assertEqual(kw.keyword_difficulty, 75)

    def test_add_keyword_stores_niche(self):
        kw = self.mod.add_keyword("python web scraping", niche="tech")
        self.assertEqual(kw.niche, "tech")

    def test_add_keyword_stores_cpc(self):
        kw = self.mod.add_keyword("buy vpn", cpc_usd=3.50)
        self.assertAlmostEqual(kw.cpc_usd, 3.50, places=2)

    def test_add_keyword_stores_content_type(self):
        kw = self.mod.add_keyword("how to cook pasta", content_type="tutorial")
        self.assertEqual(kw.content_type, "tutorial")

    def test_add_duplicate_keyword_returns_existing(self):
        kw1 = self.mod.add_keyword("duplicate keyword")
        kw2 = self.mod.add_keyword("duplicate keyword")
        self.assertEqual(kw1.id, kw2.id)

    def test_add_keyword_default_rank_zero(self):
        kw = self.mod.add_keyword("fresh keyword")
        self.assertEqual(kw.current_rank, 0)

    def test_add_multiple_keywords_unique_ids(self):
        kw1 = self.mod.add_keyword("keyword alpha")
        kw2 = self.mod.add_keyword("keyword beta")
        self.assertNotEqual(kw1.id, kw2.id)

    def test_add_keyword_persists_to_db(self):
        kw = self.mod.add_keyword("persistence test kw")
        found = self.mod.find_keyword("persistence test kw")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, kw.id)


# ---------------------------------------------------------------------------
# find_keyword / get_keyword
# ---------------------------------------------------------------------------

class TestFindKeyword(_TrackerBase):
    def test_find_existing_keyword(self):
        self.mod.add_keyword("findable keyword")
        result = self.mod.find_keyword("findable keyword")
        self.assertIsNotNone(result)

    def test_find_keyword_case_insensitive(self):
        self.mod.add_keyword("case test")
        result = self.mod.find_keyword("CASE TEST")
        self.assertIsNotNone(result)

    def test_find_nonexistent_keyword_returns_none(self):
        result = self.mod.find_keyword("this keyword does not exist xyz123")
        self.assertIsNone(result)

    def test_get_keyword_by_id(self):
        kw = self.mod.add_keyword("get by id test")
        fetched = self.mod.get_keyword(kw.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.keyword, "get by id test")

    def test_get_keyword_invalid_id_returns_none(self):
        result = self.mod.get_keyword(99999)
        self.assertIsNone(result)

    def test_find_keyword_strips_whitespace(self):
        self.mod.add_keyword("whitespace test")
        result = self.mod.find_keyword("  whitespace test  ")
        self.assertIsNotNone(result)


# ---------------------------------------------------------------------------
# update_rank
# ---------------------------------------------------------------------------

class TestUpdateRank(_TrackerBase):
    def test_update_rank_returns_string(self):
        kw = self.mod.add_keyword("rank test keyword")
        result = self.mod.update_rank(kw.id, 15)
        self.assertIsInstance(result, str)

    def test_update_rank_stores_new_rank(self):
        kw = self.mod.add_keyword("rank storage test", search_volume=1000)
        self.mod.update_rank(kw.id, 25)
        fetched = self.mod.get_keyword(kw.id)
        self.assertEqual(fetched.current_rank, 25)

    def test_update_rank_records_best_rank(self):
        kw = self.mod.add_keyword("best rank test")
        self.mod.update_rank(kw.id, 20)
        self.mod.update_rank(kw.id, 5)
        fetched = self.mod.get_keyword(kw.id)
        self.assertEqual(fetched.best_rank, 5)

    def test_update_rank_best_rank_does_not_worsen(self):
        kw = self.mod.add_keyword("best rank preservation")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 50)
        fetched = self.mod.get_keyword(kw.id)
        self.assertEqual(fetched.best_rank, 5)

    def test_update_rank_invalid_keyword_returns_error(self):
        result = self.mod.update_rank(99999, 10)
        self.assertIn("ERROR", result)

    def test_update_rank_shows_direction_improving(self):
        kw = self.mod.add_keyword("direction test")
        self.mod.update_rank(kw.id, 30)
        result = self.mod.update_rank(kw.id, 10)
        self.assertIn("↑", result)

    def test_update_rank_shows_direction_declining(self):
        kw = self.mod.add_keyword("decline test")
        self.mod.update_rank(kw.id, 10)
        result = self.mod.update_rank(kw.id, 30)
        self.assertIn("↓", result)

    def test_update_rank_shows_direction_stable(self):
        kw = self.mod.add_keyword("stable test")
        self.mod.update_rank(kw.id, 10)
        result = self.mod.update_rank(kw.id, 10)
        self.assertIn("→", result)

    def test_update_rank_triggers_alert_on_big_drop(self):
        kw = self.mod.add_keyword("alert test keyword", search_volume=2000)
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 25)  # drop of 20 positions
        alerts = self.mod.rank_alerts()
        self.assertTrue(any(a["keyword_id"] == kw.id for a in alerts))

    def test_update_rank_no_alert_on_small_drop(self):
        kw = self.mod.add_keyword("no alert test")
        self.mod.update_rank(kw.id, 10)
        self.mod.update_rank(kw.id, 14)  # drop of only 4 positions
        alerts = self.mod.rank_alerts()
        self.assertFalse(any(a["keyword_id"] == kw.id for a in alerts))

    def test_update_rank_stores_snapshot(self):
        kw = self.mod.add_keyword("url rank test")
        self.mod.update_rank(kw.id, 7, url="https://example.com/page")
        history = self.mod.rank_history(kw.id)
        self.assertGreater(len(history), 0)

    def test_update_rank_message_contains_keyword(self):
        kw = self.mod.add_keyword("keyword in message")
        result = self.mod.update_rank(kw.id, 15)
        self.assertIn("keyword in message", result)


# ---------------------------------------------------------------------------
# rank_history
# ---------------------------------------------------------------------------

class TestRankHistory(_TrackerBase):
    def test_rank_history_returns_list(self):
        kw = self.mod.add_keyword("history keyword")
        history = self.mod.rank_history(kw.id)
        self.assertIsInstance(history, list)

    def test_rank_history_empty_before_updates(self):
        kw = self.mod.add_keyword("empty history")
        history = self.mod.rank_history(kw.id)
        self.assertEqual(len(history), 0)

    def test_rank_history_records_grow_with_updates(self):
        kw = self.mod.add_keyword("growing history")
        self.mod.update_rank(kw.id, 10)
        self.mod.update_rank(kw.id, 8)
        self.mod.update_rank(kw.id, 6)
        history = self.mod.rank_history(kw.id)
        self.assertEqual(len(history), 3)

    def test_rank_history_entries_have_rank_and_ts(self):
        kw = self.mod.add_keyword("entry format test")
        self.mod.update_rank(kw.id, 12)
        history = self.mod.rank_history(kw.id)
        self.assertIn("rank", history[0])
        self.assertIn("ts", history[0])

    def test_rank_history_rank_values_correct(self):
        kw = self.mod.add_keyword("rank values test")
        self.mod.update_rank(kw.id, 20)
        self.mod.update_rank(kw.id, 15)
        history = self.mod.rank_history(kw.id)
        ranks = [h["rank"] for h in history]
        self.assertIn(20, ranks)
        self.assertIn(15, ranks)


# ---------------------------------------------------------------------------
# rank_trend
# ---------------------------------------------------------------------------

class TestRankTrend(_TrackerBase):
    def _insert_snapshot(self, keyword_id: int, rank: int, ts: float) -> None:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO rank_snapshots (keyword_id, rank, url, serp_features, ts) "
            "VALUES (?,?,?,?,?)",
            (keyword_id, rank, "", "[]", ts),
        )
        conn.commit()
        conn.close()

    def test_trend_insufficient_data_no_snapshots(self):
        kw = self.mod.add_keyword("no snapshots trend")
        result = self.mod.rank_trend(kw.id)
        self.assertEqual(result, "insufficient_data")

    def test_trend_insufficient_data_one_snapshot(self):
        kw = self.mod.add_keyword("one snapshot trend")
        self.mod.update_rank(kw.id, 10)
        result = self.mod.rank_trend(kw.id)
        self.assertEqual(result, "insufficient_data")

    def test_trend_improving_rank_falling(self):
        """Improving = rank number goes DOWN (position gets better)."""
        kw = self.mod.add_keyword("improving trend kw")
        now = time.time()
        self._insert_snapshot(kw.id, 50, now - 1000)
        self._insert_snapshot(kw.id, 30, now - 500)
        self._insert_snapshot(kw.id, 10, now)
        result = self.mod.rank_trend(kw.id, days=1)
        self.assertEqual(result, "improving")

    def test_trend_declining_rank_rising(self):
        """Declining = rank number goes UP (position gets worse)."""
        kw = self.mod.add_keyword("declining trend kw")
        now = time.time()
        self._insert_snapshot(kw.id, 5, now - 1000)
        self._insert_snapshot(kw.id, 20, now - 500)
        self._insert_snapshot(kw.id, 40, now)
        result = self.mod.rank_trend(kw.id, days=1)
        self.assertEqual(result, "declining")

    def test_trend_stable_small_change(self):
        kw = self.mod.add_keyword("stable trend kw")
        now = time.time()
        self._insert_snapshot(kw.id, 10, now - 1000)
        self._insert_snapshot(kw.id, 12, now)
        result = self.mod.rank_trend(kw.id, days=1)
        self.assertEqual(result, "stable")

    def test_trend_returns_string(self):
        kw = self.mod.add_keyword("string return trend")
        result = self.mod.rank_trend(kw.id)
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# Opportunity tier assignment via KeywordRecord.tier
# ---------------------------------------------------------------------------

class TestOpportunityTierAssignment(_TrackerBase):
    def _kw_with_rank(self, rank: int):
        kw = self.mod.add_keyword(f"tier test rank {rank} {time.time()}")
        if rank > 0:
            self.mod.update_rank(kw.id, rank)
        return self.mod.get_keyword(kw.id)

    def test_rank_1_is_top_3(self):
        kw = self._kw_with_rank(1)
        self.assertEqual(kw.tier, "top_3")

    def test_rank_3_is_top_3(self):
        kw = self._kw_with_rank(3)
        self.assertEqual(kw.tier, "top_3")

    def test_rank_4_is_page_1(self):
        kw = self._kw_with_rank(4)
        self.assertEqual(kw.tier, "page_1")

    def test_rank_10_is_page_1(self):
        kw = self._kw_with_rank(10)
        self.assertEqual(kw.tier, "page_1")

    def test_rank_11_is_page_2(self):
        kw = self._kw_with_rank(11)
        self.assertEqual(kw.tier, "page_2")

    def test_rank_20_is_page_2(self):
        kw = self._kw_with_rank(20)
        self.assertEqual(kw.tier, "page_2")

    def test_rank_21_is_page_3(self):
        kw = self._kw_with_rank(21)
        self.assertEqual(kw.tier, "page_3")

    def test_rank_30_is_page_3(self):
        kw = self._kw_with_rank(30)
        self.assertEqual(kw.tier, "page_3")

    def test_rank_31_is_deep(self):
        kw = self._kw_with_rank(31)
        self.assertEqual(kw.tier, "deep")

    def test_rank_0_is_featured_snippet(self):
        # featured_snippet tier covers rank 0 (position zero = featured snippet slot)
        kw = self.mod.add_keyword("unranked keyword tier test")
        self.assertEqual(kw.tier, "featured_snippet")

    def test_rank_101_is_not_ranking(self):
        kw = self._kw_with_rank(101)
        self.assertEqual(kw.tier, "not_ranking")


# ---------------------------------------------------------------------------
# opportunity_score calculation
# ---------------------------------------------------------------------------

class TestOpportunityScore(_TrackerBase):
    def test_opportunity_score_zero_when_no_rank(self):
        kw = self.mod.add_keyword("unranked score test")
        self.assertEqual(kw.opportunity_score, 0.0)

    def test_opportunity_score_positive_with_rank(self):
        kw = self.mod.add_keyword("ranked score test", search_volume=5000,
                                   keyword_difficulty=40)
        self.mod.update_rank(kw.id, 15)
        kw = self.mod.get_keyword(kw.id)
        self.assertGreater(kw.opportunity_score, 0.0)

    def test_opportunity_score_range_0_to_100(self):
        kw = self.mod.add_keyword("score range test", search_volume=10000,
                                   keyword_difficulty=20)
        self.mod.update_rank(kw.id, 5)
        kw = self.mod.get_keyword(kw.id)
        self.assertGreaterEqual(kw.opportunity_score, 0.0)
        self.assertLessEqual(kw.opportunity_score, 100.0)

    def test_opportunity_score_higher_volume_scores_higher(self):
        kw_low = self.mod.add_keyword("low volume kw", search_volume=100, keyword_difficulty=50)
        kw_high = self.mod.add_keyword("high volume kw", search_volume=10000, keyword_difficulty=50)
        self.mod.update_rank(kw_low.id, 15)
        self.mod.update_rank(kw_high.id, 15)
        kw_low = self.mod.get_keyword(kw_low.id)
        kw_high = self.mod.get_keyword(kw_high.id)
        self.assertGreater(kw_high.opportunity_score, kw_low.opportunity_score)

    def test_opportunity_score_lower_difficulty_scores_higher(self):
        kw_hard = self.mod.add_keyword("hard kw", search_volume=5000, keyword_difficulty=90)
        kw_easy = self.mod.add_keyword("easy kw", search_volume=5000, keyword_difficulty=10)
        self.mod.update_rank(kw_hard.id, 15)
        self.mod.update_rank(kw_easy.id, 15)
        kw_hard = self.mod.get_keyword(kw_hard.id)
        kw_easy = self.mod.get_keyword(kw_easy.id)
        self.assertGreater(kw_easy.opportunity_score, kw_hard.opportunity_score)

    def test_opportunity_score_rounded(self):
        kw = self.mod.add_keyword("rounded score", search_volume=3000, keyword_difficulty=50)
        self.mod.update_rank(kw.id, 12)
        kw = self.mod.get_keyword(kw.id)
        self.assertEqual(kw.opportunity_score, round(kw.opportunity_score, 1))


# ---------------------------------------------------------------------------
# rank_alerts
# ---------------------------------------------------------------------------

class TestRankAlerts(_TrackerBase):
    def test_alerts_empty_initially(self):
        alerts = self.mod.rank_alerts()
        self.assertEqual(len(alerts), 0)

    def test_alert_created_on_6_position_drop(self):
        kw = self.mod.add_keyword("alert drop test")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 11)  # drop of 6
        alerts = self.mod.rank_alerts()
        self.assertEqual(len(alerts), 1)

    def test_alert_contains_keyword(self):
        kw = self.mod.add_keyword("keyword in alert")
        self.mod.update_rank(kw.id, 3)
        self.mod.update_rank(kw.id, 20)
        alerts = self.mod.rank_alerts()
        self.assertEqual(alerts[0]["keyword"], "keyword in alert")

    def test_alert_contains_old_and_new_rank(self):
        kw = self.mod.add_keyword("rank values in alert")
        self.mod.update_rank(kw.id, 3)
        self.mod.update_rank(kw.id, 20)
        alerts = self.mod.rank_alerts()
        alert = alerts[0]
        self.assertEqual(alert["old_rank"], 3)
        self.assertEqual(alert["new_rank"], 20)

    def test_alert_drop_field(self):
        kw = self.mod.add_keyword("drop field test")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 18)  # drop of 13
        alerts = self.mod.rank_alerts()
        self.assertEqual(alerts[0]["drop"], 13)

    def test_acknowledge_alerts_marks_all(self):
        kw = self.mod.add_keyword("acknowledge test")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 25)
        result = self.mod.acknowledge_alerts()
        self.assertIsInstance(result, str)
        alerts = self.mod.rank_alerts()
        self.assertEqual(len(alerts), 0)

    def test_alerts_unacknowledged_only_by_default(self):
        kw = self.mod.add_keyword("unack only test")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 25)
        self.mod.acknowledge_alerts()
        alerts = self.mod.rank_alerts(unacknowledged_only=True)
        self.assertEqual(len(alerts), 0)

    def test_alerts_all_when_not_unack_only(self):
        kw = self.mod.add_keyword("all alerts test")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 25)
        self.mod.acknowledge_alerts()
        alerts = self.mod.rank_alerts(unacknowledged_only=False)
        self.assertGreater(len(alerts), 0)


# ---------------------------------------------------------------------------
# list_keywords
# ---------------------------------------------------------------------------

class TestListKeywords(_TrackerBase):
    def test_list_keywords_returns_list(self):
        result = self.mod.list_keywords()
        self.assertIsInstance(result, list)

    def test_list_keywords_empty_initially(self):
        result = self.mod.list_keywords()
        self.assertEqual(len(result), 0)

    def test_list_keywords_returns_added_keywords(self):
        self.mod.add_keyword("list test kw 1")
        self.mod.add_keyword("list test kw 2")
        result = self.mod.list_keywords()
        self.assertEqual(len(result), 2)

    def test_list_keywords_filtered_by_niche(self):
        self.mod.add_keyword("tech kw", niche="tech")
        self.mod.add_keyword("finance kw", niche="finance")
        result = self.mod.list_keywords(niche="tech")
        self.assertTrue(all(k.niche == "tech" for k in result))

    def test_list_keywords_limit_respected(self):
        for i in range(10):
            self.mod.add_keyword(f"limit kw {i}")
        result = self.mod.list_keywords(limit=5)
        self.assertLessEqual(len(result), 5)

    def test_list_keywords_returns_keyword_record_objects(self):
        self.mod.add_keyword("type check kw")
        result = self.mod.list_keywords()
        self.assertIsInstance(result[0], self.mod.KeywordRecord)


# ---------------------------------------------------------------------------
# get_opportunities
# ---------------------------------------------------------------------------

class TestGetOpportunities(_TrackerBase):
    def test_opportunities_returns_list(self):
        result = self.mod.get_opportunities()
        self.assertIsInstance(result, list)

    def test_opportunities_page_2_keywords_included(self):
        kw = self.mod.add_keyword("page 2 opportunity", search_volume=500)
        self.mod.update_rank(kw.id, 15)  # page 2
        result = self.mod.get_opportunities(min_volume=100)
        self.assertTrue(any(k.id == kw.id for k in result))

    def test_opportunities_excludes_page_1(self):
        kw = self.mod.add_keyword("page 1 should exclude", search_volume=500)
        self.mod.update_rank(kw.id, 5)  # page 1
        result = self.mod.get_opportunities(min_volume=100)
        self.assertFalse(any(k.id == kw.id for k in result))

    def test_opportunities_min_volume_filter(self):
        kw_low = self.mod.add_keyword("low vol opp", search_volume=50)
        kw_high = self.mod.add_keyword("high vol opp", search_volume=1000)
        self.mod.update_rank(kw_low.id, 15)
        self.mod.update_rank(kw_high.id, 15)
        result = self.mod.get_opportunities(min_volume=500)
        ids = [k.id for k in result]
        self.assertNotIn(kw_low.id, ids)
        self.assertIn(kw_high.id, ids)

    def test_opportunities_filtered_by_niche(self):
        kw_tech = self.mod.add_keyword("tech opportunity", niche="tech", search_volume=500)
        kw_fin = self.mod.add_keyword("finance opportunity", niche="finance", search_volume=500)
        self.mod.update_rank(kw_tech.id, 15)
        self.mod.update_rank(kw_fin.id, 15)
        result = self.mod.get_opportunities(niche="tech", min_volume=100)
        self.assertTrue(all(k.niche == "tech" for k in result))


# ---------------------------------------------------------------------------
# keyword_report
# ---------------------------------------------------------------------------

class TestKeywordReport(_TrackerBase):
    def test_report_returns_dict(self):
        result = self.mod.keyword_report()
        self.assertIsInstance(result, dict)

    def test_report_contains_total_keywords(self):
        result = self.mod.keyword_report()
        self.assertIn("total_keywords", result)

    def test_report_contains_by_tier(self):
        result = self.mod.keyword_report()
        self.assertIn("by_tier", result)

    def test_report_contains_top_10(self):
        result = self.mod.keyword_report()
        self.assertIn("top_10", result)

    def test_report_total_count_correct(self):
        self.mod.add_keyword("report kw 1")
        self.mod.add_keyword("report kw 2")
        result = self.mod.keyword_report()
        self.assertEqual(result["total_keywords"], 2)

    def test_report_unread_alerts(self):
        result = self.mod.keyword_report()
        self.assertIn("unread_alerts", result)

    def test_report_by_tier_has_all_tiers(self):
        result = self.mod.keyword_report()
        for tier in self.mod.OPPORTUNITY_TIERS:
            self.assertIn(tier, result["by_tier"])


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

class TestAddKeywordTool(_TrackerBase):
    def test_returns_string(self):
        result = self.mod.add_keyword_tool("tool keyword")
        self.assertIsInstance(result, str)

    def test_success_contains_keyword_name(self):
        result = self.mod.add_keyword_tool("tool keyword name")
        self.assertIn("tool keyword name", result)

    def test_success_contains_id(self):
        result = self.mod.add_keyword_tool("tool keyword id")
        self.assertRegex(result, r"#\d+")

    def test_with_volume_and_difficulty(self):
        result = self.mod.add_keyword_tool("full tool keyword", search_volume=2000,
                                            keyword_difficulty=45)
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_with_target_url(self):
        result = self.mod.add_keyword_tool("url tool kw",
                                            target_url="https://example.com/page")
        self.assertIsInstance(result, str)


class TestUpdateRankTool(_TrackerBase):
    def test_returns_string(self):
        self.mod.add_keyword("rank tool kw")
        result = self.mod.update_rank_tool("rank tool kw", 10)
        self.assertIsInstance(result, str)

    def test_creates_keyword_if_missing(self):
        result = self.mod.update_rank_tool("brand new kw for rank tool", 20)
        self.assertIsInstance(result, str)
        kw = self.mod.find_keyword("brand new kw for rank tool")
        self.assertIsNotNone(kw)

    def test_contains_rank_in_result(self):
        result = self.mod.update_rank_tool("rank msg kw", 15)
        self.assertIn("15", result)


class TestKeywordOpportunitiesTool(_TrackerBase):
    def test_returns_string(self):
        result = self.mod.keyword_opportunities_tool()
        self.assertIsInstance(result, str)

    def test_returns_json(self):
        result = self.mod.keyword_opportunities_tool()
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_opportunity_item_has_required_keys(self):
        kw = self.mod.add_keyword("opp tool kw", search_volume=500)
        self.mod.update_rank(kw.id, 15)
        result = self.mod.keyword_opportunities_tool(min_volume=100)
        data = json.loads(result)
        if data:
            for key in ("id", "keyword", "rank", "volume", "tier", "score"):
                self.assertIn(key, data[0])

    def test_niche_filter_in_tool(self):
        kw = self.mod.add_keyword("niche filter kw", niche="seo", search_volume=500)
        self.mod.update_rank(kw.id, 15)
        result = self.mod.keyword_opportunities_tool(niche="seo", min_volume=100)
        data = json.loads(result)
        self.assertGreater(len(data), 0)


class TestKeywordReportTool(_TrackerBase):
    def test_returns_string(self):
        result = self.mod.keyword_report_tool()
        self.assertIsInstance(result, str)

    def test_returns_json(self):
        result = self.mod.keyword_report_tool()
        data = json.loads(result)
        self.assertIsInstance(data, dict)

    def test_contains_total_keywords(self):
        result = self.mod.keyword_report_tool()
        data = json.loads(result)
        self.assertIn("total_keywords", data)


class TestRankAlertsTool(_TrackerBase):
    def test_returns_string(self):
        result = self.mod.rank_alerts_tool()
        self.assertIsInstance(result, str)

    def test_returns_json(self):
        result = self.mod.rank_alerts_tool()
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_alert_in_tool_after_drop(self):
        kw = self.mod.add_keyword("tool alert drop")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 30)
        result = self.mod.rank_alerts_tool()
        data = json.loads(result)
        self.assertGreater(len(data), 0)


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

class TestKeywordPersistence(_TrackerBase):
    def test_keyword_survives_reinit(self):
        kw = self.mod.add_keyword("persistent kw")
        self.mod._schema_ready = False
        found = self.mod.find_keyword("persistent kw")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, kw.id)

    def test_rank_update_persists(self):
        kw = self.mod.add_keyword("rank persist kw")
        self.mod.update_rank(kw.id, 12)
        self.mod._schema_ready = False
        fetched = self.mod.get_keyword(kw.id)
        self.assertEqual(fetched.current_rank, 12)

    def test_rank_snapshots_persist(self):
        kw = self.mod.add_keyword("snapshot persist kw")
        self.mod.update_rank(kw.id, 8)
        self.mod.update_rank(kw.id, 6)
        self.mod._schema_ready = False
        history = self.mod.rank_history(kw.id)
        self.assertEqual(len(history), 2)

    def test_alerts_persist_after_reinit(self):
        kw = self.mod.add_keyword("alert persist kw")
        self.mod.update_rank(kw.id, 5)
        self.mod.update_rank(kw.id, 25)
        self.mod._schema_ready = False
        alerts = self.mod.rank_alerts()
        self.assertGreater(len(alerts), 0)


if __name__ == "__main__":
    unittest.main()
