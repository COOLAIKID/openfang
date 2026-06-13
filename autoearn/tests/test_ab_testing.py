"""Comprehensive tests for core/ab_testing.py.

Each test class is independent and uses a temporary SQLite database so tests
never touch the real autoearn.db and cannot interfere with one another.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers: redirect the module's DB path to a temp file
# ---------------------------------------------------------------------------

def _make_module(tmp_db_path: str):
    """Import (or re-configure) ab_testing to use an isolated DB."""
    import importlib
    import autoearn.core.ab_testing as mod
    importlib.reload(mod)
    mod._DB_PATH = Path(tmp_db_path)
    mod._schema_ready = False
    return mod


# ---------------------------------------------------------------------------
# TestExperimentCreation
# ---------------------------------------------------------------------------

class TestExperimentCreation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_create_basic_experiment_returns_experiment_object(self):
        exp = self.ab.create_experiment("test_exp", variants=["A", "B"])
        self.assertIsNotNone(exp)
        self.assertEqual(exp.name, "test_exp")

    def test_create_experiment_stores_variants(self):
        exp = self.ab.create_experiment("pricing", variants=["$29", "$49"])
        self.assertEqual(exp.variants, ["$29", "$49"])

    def test_create_experiment_with_three_variants(self):
        exp = self.ab.create_experiment("multi", variants=["v1", "v2", "v3"])
        self.assertEqual(len(exp.variants), 3)
        self.assertIn("v2", exp.variants)

    def test_create_experiment_with_hypothesis(self):
        exp = self.ab.create_experiment(
            "headline", variants=["A", "B"],
            hypothesis="Bold headline converts better"
        )
        self.assertEqual(exp.hypothesis, "Bold headline converts better")

    def test_create_experiment_default_status_is_running(self):
        exp = self.ab.create_experiment("status_test", variants=["A", "B"])
        self.assertEqual(exp.status, "running")

    def test_create_experiment_default_min_sample_size(self):
        exp = self.ab.create_experiment("sample_test", variants=["A", "B"])
        self.assertEqual(exp.min_sample_size, 100)

    def test_create_experiment_custom_min_sample_size(self):
        exp = self.ab.create_experiment(
            "small_test", variants=["A", "B"], min_sample_size=50
        )
        self.assertEqual(exp.min_sample_size, 50)

    def test_create_experiment_default_confidence(self):
        exp = self.ab.create_experiment("conf_test", variants=["A", "B"])
        self.assertAlmostEqual(exp.target_confidence, 0.95)

    def test_create_experiment_custom_confidence(self):
        exp = self.ab.create_experiment(
            "conf90", variants=["A", "B"], target_confidence=0.90
        )
        self.assertAlmostEqual(exp.target_confidence, 0.90)

    def test_create_experiment_with_metadata(self):
        meta = {"team": "growth", "quarter": "Q4"}
        exp = self.ab.create_experiment(
            "meta_test", variants=["A", "B"], metadata=meta
        )
        self.assertEqual(exp.metadata["team"], "growth")
        self.assertEqual(exp.metadata["quarter"], "Q4")

    def test_create_experiment_with_single_variant_raises(self):
        with self.assertRaises(ValueError):
            self.ab.create_experiment("bad", variants=["only_one"])

    def test_create_experiment_with_empty_variants_raises(self):
        with self.assertRaises(ValueError):
            self.ab.create_experiment("empty", variants=[])

    def test_create_experiment_persisted_in_db(self):
        self.ab.create_experiment("persist_me", variants=["X", "Y"])
        fetched = self.ab.get_experiment("persist_me")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "persist_me")
        self.assertEqual(fetched.variants, ["X", "Y"])

    def test_create_experiment_replace_existing(self):
        self.ab.create_experiment("dupe", variants=["A", "B"])
        # INSERT OR REPLACE should succeed
        exp2 = self.ab.create_experiment("dupe", variants=["X", "Y", "Z"])
        self.assertEqual(exp2.variants, ["X", "Y", "Z"])

    def test_create_experiment_winner_initially_empty(self):
        exp = self.ab.create_experiment("winner_check", variants=["A", "B"])
        self.assertEqual(exp.winner, "")

    def test_create_experiment_created_at_is_recent(self):
        before = time.time()
        exp = self.ab.create_experiment("time_test", variants=["A", "B"])
        after = time.time()
        self.assertGreaterEqual(exp.created_at, before)
        self.assertLessEqual(exp.created_at, after)


# ---------------------------------------------------------------------------
# TestGetAndListExperiments
# ---------------------------------------------------------------------------

class TestGetAndListExperiments(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_get_experiment_returns_none_for_missing(self):
        result = self.ab.get_experiment("nonexistent")
        self.assertIsNone(result)

    def test_get_experiment_returns_correct_object(self):
        self.ab.create_experiment("my_exp", variants=["A", "B"],
                                  hypothesis="test hypo")
        exp = self.ab.get_experiment("my_exp")
        self.assertIsNotNone(exp)
        self.assertEqual(exp.hypothesis, "test hypo")

    def test_list_experiments_empty_initially(self):
        result = self.ab.list_experiments()
        self.assertEqual(result, [])

    def test_list_experiments_returns_all(self):
        self.ab.create_experiment("exp1", variants=["A", "B"])
        self.ab.create_experiment("exp2", variants=["X", "Y"])
        self.ab.create_experiment("exp3", variants=["P", "Q"])
        result = self.ab.list_experiments()
        self.assertEqual(len(result), 3)

    def test_list_experiments_returns_dicts(self):
        self.ab.create_experiment("dict_test", variants=["A", "B"])
        result = self.ab.list_experiments()
        self.assertIsInstance(result[0], dict)

    def test_list_experiments_dict_has_required_keys(self):
        self.ab.create_experiment("key_test", variants=["A", "B"])
        result = self.ab.list_experiments()
        entry = result[0]
        for key in ("name", "variants", "status", "winner"):
            self.assertIn(key, entry)

    def test_experiment_to_dict(self):
        exp = self.ab.create_experiment("to_dict", variants=["A", "B"])
        d = exp.to_dict()
        self.assertIn("name", d)
        self.assertIn("variants", d)
        self.assertIn("status", d)
        self.assertIn("hypothesis", d)
        self.assertIn("winner", d)
        self.assertIn("target_confidence", d)

    def test_get_experiment_after_status_change(self):
        self.ab.create_experiment("stat_change", variants=["A", "B"])
        self.ab.pause_experiment("stat_change")
        exp = self.ab.get_experiment("stat_change")
        self.assertEqual(exp.status, "paused")


# ---------------------------------------------------------------------------
# TestAssignment
# ---------------------------------------------------------------------------

class TestAssignment(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)
        self.ab.create_experiment("assign_test", variants=["control", "treatment"])

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_assign_returns_valid_variant(self):
        variant = self.ab.assign("assign_test", "user_001")
        self.assertIn(variant, ["control", "treatment"])

    def test_assign_is_deterministic(self):
        first = self.ab.assign("assign_test", "user_abc")
        second = self.ab.assign("assign_test", "user_abc")
        self.assertEqual(first, second)

    def test_assign_deterministic_across_many_calls(self):
        results = [self.ab.assign("assign_test", "stable_user") for _ in range(10)]
        self.assertEqual(len(set(results)), 1)

    def test_assign_different_participants_may_differ(self):
        """With enough participants, we expect to see both variants."""
        variants_seen = set()
        for i in range(30):
            v = self.ab.assign("assign_test", f"user_{i:04d}")
            variants_seen.add(v)
        self.assertEqual(variants_seen, {"control", "treatment"})

    def test_assign_unknown_experiment_raises(self):
        with self.assertRaises(ValueError):
            self.ab.assign("nonexistent_exp", "user_1")

    def test_assign_records_impression_event(self):
        self.ab.assign("assign_test", "imp_user")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        count = conn.execute(
            "SELECT COUNT(*) as c FROM ab_events WHERE experiment='assign_test' "
            "AND participant='imp_user' AND event_type='impression'"
        ).fetchone()["c"]
        conn.close()
        self.assertEqual(count, 1)

    def test_assign_concluded_experiment_returns_winner(self):
        exp_name = "concluded_exp"
        self.ab.create_experiment(exp_name, variants=["A", "B"])
        # Manually set winner in DB
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE ab_experiments SET status='concluded', winner='B' WHERE name=?",
            (exp_name,)
        )
        conn.commit()
        conn.close()
        self.ab._schema_ready = False  # force reload
        result = self.ab.assign(exp_name, "any_user")
        self.assertEqual(result, "B")

    def test_assign_uses_sha256_hash(self):
        """Verify the hash algorithm matches expected assignment."""
        exp = self.ab.get_experiment("assign_test")
        pid = "hash_verify_user"
        seed = f"assign_test:{pid}"
        digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
        expected = exp.variants[digest % len(exp.variants)]
        actual = self.ab.assign("assign_test", pid)
        self.assertEqual(actual, expected)

    def test_assign_three_variant_experiment(self):
        self.ab.create_experiment("three_var", variants=["A", "B", "C"])
        seen = set()
        for i in range(60):
            v = self.ab.assign("three_var", f"u{i}")
            seen.add(v)
        self.assertEqual(seen, {"A", "B", "C"})


# ---------------------------------------------------------------------------
# TestConversionRecording
# ---------------------------------------------------------------------------

class TestConversionRecording(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)
        self.ab.create_experiment("conv_exp", variants=["A", "B"])

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def _count_events(self, event_type: str, experiment: str = "conv_exp") -> int:
        conn = sqlite3.connect(self.db_path)
        c = conn.execute(
            "SELECT COUNT(*) as c FROM ab_events WHERE experiment=? AND event_type=?",
            (experiment, event_type)
        ).fetchone()[0]
        conn.close()
        return c

    def test_record_conversion_creates_event(self):
        self.ab.assign("conv_exp", "buyer_001")
        self.ab.record_conversion("conv_exp", "buyer_001", revenue=29.0)
        self.assertEqual(self._count_events("conversion"), 1)

    def test_record_conversion_stores_revenue(self):
        self.ab.assign("conv_exp", "buyer_002")
        self.ab.record_conversion("conv_exp", "buyer_002", revenue=49.99)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT revenue FROM ab_events WHERE experiment='conv_exp' AND event_type='conversion'"
        ).fetchone()
        conn.close()
        self.assertAlmostEqual(row[0], 49.99, places=2)

    def test_record_conversion_zero_revenue(self):
        self.ab.assign("conv_exp", "free_user")
        self.ab.record_conversion("conv_exp", "free_user", revenue=0.0)
        self.assertEqual(self._count_events("conversion"), 1)

    def test_record_conversion_multiple_times_same_user(self):
        self.ab.assign("conv_exp", "repeat_buyer")
        self.ab.record_conversion("conv_exp", "repeat_buyer", revenue=10.0)
        self.ab.record_conversion("conv_exp", "repeat_buyer", revenue=20.0)
        self.assertEqual(self._count_events("conversion"), 2)

    def test_record_conversion_unknown_experiment_is_silent(self):
        # Should not raise, just return None
        result = self.ab.record_conversion("ghost_exp", "user_x", revenue=10.0)
        self.assertIsNone(result)

    def test_record_conversion_assigns_to_correct_variant(self):
        """Conversion must go to the same variant as impression."""
        pid = "variant_check_user"
        variant = self.ab.assign("conv_exp", pid)
        self.ab.record_conversion("conv_exp", pid, revenue=100.0)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT variant FROM ab_events WHERE experiment='conv_exp' "
            "AND participant=? AND event_type='conversion'",
            (pid,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], variant)

    def test_record_conversion_with_metadata(self):
        self.ab.assign("conv_exp", "meta_user")
        self.ab.record_conversion(
            "conv_exp", "meta_user", revenue=5.0,
            metadata={"source": "email", "campaign": "spring"}
        )
        self.assertEqual(self._count_events("conversion"), 1)

    def test_multiple_users_multiple_conversions(self):
        for i in range(10):
            self.ab.assign("conv_exp", f"u{i}")
            self.ab.record_conversion("conv_exp", f"u{i}", revenue=float(i * 10))
        self.assertEqual(self._count_events("conversion"), 10)


# ---------------------------------------------------------------------------
# TestVariantStats
# ---------------------------------------------------------------------------

class TestVariantStats(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)
        self.ab.create_experiment("stats_exp", variants=["control", "treatment"])

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_variant_stats_zero_when_no_events(self):
        stats = self.ab._get_variant_stats("stats_exp")
        for v in stats:
            self.assertEqual(v.impressions, 0)
            self.assertEqual(v.conversions, 0)
            self.assertAlmostEqual(v.revenue, 0.0)

    def test_variant_conversion_rate_property(self):
        from autoearn.core.ab_testing import Variant
        v = Variant(name="test", impressions=100, conversions=10)
        self.assertAlmostEqual(v.conversion_rate, 0.10)

    def test_variant_conversion_rate_zero_impressions(self):
        from autoearn.core.ab_testing import Variant
        v = Variant(name="empty")
        self.assertAlmostEqual(v.conversion_rate, 0.0)

    def test_variant_revenue_per_impression(self):
        from autoearn.core.ab_testing import Variant
        v = Variant(name="test", impressions=100, revenue=500.0)
        self.assertAlmostEqual(v.revenue_per_impression, 5.0)

    def test_variant_average_order_value(self):
        from autoearn.core.ab_testing import Variant
        v = Variant(name="test", conversions=5, revenue=250.0)
        self.assertAlmostEqual(v.average_order_value, 50.0)

    def test_variant_average_order_value_zero_conversions(self):
        from autoearn.core.ab_testing import Variant
        v = Variant(name="empty")
        # max(1, 0) protects against division by zero
        self.assertAlmostEqual(v.average_order_value, 0.0)

    def test_get_variant_stats_counts_impressions(self):
        for i in range(5):
            self.ab.assign("stats_exp", f"user_{i}")
        stats = self.ab._get_variant_stats("stats_exp")
        total = sum(v.impressions for v in stats)
        self.assertEqual(total, 5)

    def test_get_variant_stats_counts_conversions(self):
        for i in range(4):
            self.ab.assign("stats_exp", f"cu_{i}")
            self.ab.record_conversion("stats_exp", f"cu_{i}", revenue=10.0)
        stats = self.ab._get_variant_stats("stats_exp")
        total = sum(v.conversions for v in stats)
        self.assertEqual(total, 4)

    def test_get_variant_stats_sums_revenue(self):
        revenues = [10.0, 20.0, 30.0]
        for i, r in enumerate(revenues):
            self.ab.assign("stats_exp", f"rev_u_{i}")
            self.ab.record_conversion("stats_exp", f"rev_u_{i}", revenue=r)
        stats = self.ab._get_variant_stats("stats_exp")
        total_rev = sum(v.revenue for v in stats)
        self.assertAlmostEqual(total_rev, 60.0, places=2)

    def test_get_variant_stats_returns_empty_for_unknown_exp(self):
        stats = self.ab._get_variant_stats("ghost")
        self.assertEqual(stats, [])


# ---------------------------------------------------------------------------
# TestStatisticalAnalysis
# ---------------------------------------------------------------------------

class TestStatisticalAnalysis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_analyze_returns_error_for_unknown_experiment(self):
        result = self.ab.analyze("ghost_exp")
        self.assertIn("error", result)

    def test_analyze_no_data_returns_no_data_status(self):
        self.ab.create_experiment("empty_analysis", variants=["A", "B"])
        result = self.ab.analyze("empty_analysis")
        self.assertEqual(result.get("status"), "no_data")

    def test_analyze_returns_experiment_name(self):
        self.ab.create_experiment("named_exp", variants=["A", "B"])
        for i in range(5):
            self.ab.assign("named_exp", f"u{i}")
        result = self.ab.analyze("named_exp")
        self.assertEqual(result["experiment"], "named_exp")

    def test_analyze_has_required_keys(self):
        self.ab.create_experiment("key_check", variants=["A", "B"])
        for i in range(5):
            self.ab.assign("key_check", f"u{i}")
        result = self.ab.analyze("key_check")
        for key in ("experiment", "status", "has_enough_data", "significant",
                    "variants", "recommendation"):
            self.assertIn(key, result)

    def test_analyze_variants_list_has_correct_length(self):
        self.ab.create_experiment("two_var", variants=["A", "B"])
        for i in range(4):
            self.ab.assign("two_var", f"u{i}")
        result = self.ab.analyze("two_var")
        self.assertEqual(len(result["variants"]), 2)

    def test_analyze_not_enough_data_when_below_min_sample(self):
        self.ab.create_experiment("low_data", variants=["A", "B"], min_sample_size=100)
        for i in range(5):
            self.ab.assign("low_data", f"u{i}")
        result = self.ab.analyze("low_data")
        self.assertFalse(result["has_enough_data"])

    def test_analyze_enough_data_when_above_min_sample(self):
        self.ab.create_experiment("enough_data", variants=["A", "B"], min_sample_size=5)
        for i in range(20):
            self.ab.assign("enough_data", f"u{i}")
        result = self.ab.analyze("enough_data")
        self.assertTrue(result["has_enough_data"])

    def test_z_test_two_proportions_zero_counts_returns_p1(self):
        z, p = self.ab._z_test_two_proportions(0, 0, 100, 10)
        self.assertAlmostEqual(p, 1.0)
        self.assertAlmostEqual(z, 0.0)

    def test_z_test_equal_proportions_not_significant(self):
        z, p = self.ab._z_test_two_proportions(1000, 100, 1000, 100)
        self.assertGreater(p, 0.05)

    def test_z_test_very_different_proportions_is_significant(self):
        # 5% vs 20% with large samples should be significant
        z, p = self.ab._z_test_two_proportions(5000, 250, 5000, 1000)
        self.assertLess(p, 0.05)

    def test_normal_cdf_at_zero_is_half(self):
        result = self.ab._normal_cdf(0)
        self.assertAlmostEqual(result, 0.5, places=3)

    def test_normal_cdf_positive_z_above_half(self):
        result = self.ab._normal_cdf(1.96)
        self.assertGreater(result, 0.97)

    def test_analyze_p_value_present_for_non_control_variants(self):
        self.ab.create_experiment("pval_test", variants=["ctrl", "treat"], min_sample_size=2)
        for i in range(6):
            self.ab.assign("pval_test", f"u{i}")
        result = self.ab.analyze("pval_test")
        non_control = [v for v in result["variants"] if v["variant"] != "ctrl"]
        for v in non_control:
            self.assertIn("p_value", v)

    def test_analyze_lift_vs_control_present_for_treatment(self):
        self.ab.create_experiment("lift_test", variants=["ctrl", "treat"], min_sample_size=2)
        for i in range(6):
            self.ab.assign("lift_test", f"u{i}")
        result = self.ab.analyze("lift_test")
        non_control = [v for v in result["variants"] if v["variant"] != "ctrl"]
        for v in non_control:
            self.assertIn("lift_vs_control", v)

    def test_analyze_recommendation_is_string(self):
        self.ab.create_experiment("rec_test", variants=["A", "B"])
        for i in range(3):
            self.ab.assign("rec_test", f"u{i}")
        result = self.ab.analyze("rec_test")
        self.assertIsInstance(result["recommendation"], str)

    def test_analyze_significant_true_when_statistically_proven(self):
        """Inject enough synthetic data to force significance."""
        exp_name = "sig_test"
        self.ab.create_experiment(exp_name, variants=["ctrl", "treat"],
                                  min_sample_size=50, target_confidence=0.95)
        # Directly insert lopsided events to force significance
        conn = sqlite3.connect(self.db_path)
        ts = time.time()
        # ctrl: 1000 impressions, 50 conversions (5%)
        for i in range(1000):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata) "
                "VALUES (?, 'ctrl', ?, 'impression', 0, ?, '{}')",
                (exp_name, f"ctrl_{i}", ts)
            )
        for i in range(50):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata) "
                "VALUES (?, 'ctrl', ?, 'conversion', 0, ?, '{}')",
                (exp_name, f"ctrl_{i}", ts)
            )
        # treat: 1000 impressions, 200 conversions (20%)
        for i in range(1000):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata) "
                "VALUES (?, 'treat', ?, 'impression', 0, ?, '{}')",
                (exp_name, f"treat_{i}", ts)
            )
        for i in range(200):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata) "
                "VALUES (?, 'treat', ?, 'conversion', 0, ?, '{}')",
                (exp_name, f"treat_{i}", ts)
            )
        conn.commit()
        conn.close()
        result = self.ab.analyze(exp_name)
        self.assertTrue(result["significant"])
        self.assertIsNotNone(result["winner_candidate"])


# ---------------------------------------------------------------------------
# TestAutoSelectWinner
# ---------------------------------------------------------------------------

class TestAutoSelectWinner(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def _inject_lopsided_data(self, exp_name: str,
                               ctrl_imp=1000, ctrl_conv=50,
                               treat_imp=1000, treat_conv=200):
        conn = sqlite3.connect(self.db_path)
        ts = time.time()
        for i in range(ctrl_imp):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata)"
                " VALUES (?, 'ctrl', ?, 'impression', 0, ?, '{}')",
                (exp_name, f"c_{i}", ts)
            )
        for i in range(ctrl_conv):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata)"
                " VALUES (?, 'ctrl', ?, 'conversion', 0, ?, '{}')",
                (exp_name, f"c_{i}", ts)
            )
        for i in range(treat_imp):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata)"
                " VALUES (?, 'treat', ?, 'impression', 0, ?, '{}')",
                (exp_name, f"t_{i}", ts)
            )
        for i in range(treat_conv):
            conn.execute(
                "INSERT INTO ab_events (experiment, variant, participant, event_type, revenue, ts, metadata)"
                " VALUES (?, 'treat', ?, 'conversion', 0, ?, '{}')",
                (exp_name, f"t_{i}", ts)
            )
        conn.commit()
        conn.close()

    def test_auto_select_winner_returns_string(self):
        self.ab.create_experiment("str_test", variants=["ctrl", "treat"])
        result = self.ab.auto_select_winner("str_test")
        self.assertIsInstance(result, str)

    def test_auto_select_winner_not_significant_when_no_data(self):
        self.ab.create_experiment("no_data_win", variants=["ctrl", "treat"])
        result = self.ab.auto_select_winner("no_data_win")
        self.assertIn("Not yet significant", result)

    def test_auto_select_winner_concludes_experiment(self):
        exp_name = "conclude_me"
        self.ab.create_experiment(exp_name, variants=["ctrl", "treat"],
                                  min_sample_size=50, target_confidence=0.95)
        self._inject_lopsided_data(exp_name)
        result = self.ab.auto_select_winner(exp_name)
        self.assertIn("Concluded", result)
        self.assertIn(exp_name, result)

    def test_auto_select_winner_sets_status_concluded(self):
        exp_name = "status_conclude"
        self.ab.create_experiment(exp_name, variants=["ctrl", "treat"],
                                  min_sample_size=50, target_confidence=0.95)
        self._inject_lopsided_data(exp_name)
        self.ab.auto_select_winner(exp_name)
        exp = self.ab.get_experiment(exp_name)
        self.assertEqual(exp.status, "concluded")

    def test_auto_select_winner_sets_winner_field(self):
        exp_name = "winner_field"
        self.ab.create_experiment(exp_name, variants=["ctrl", "treat"],
                                  min_sample_size=50, target_confidence=0.95)
        self._inject_lopsided_data(exp_name)
        self.ab.auto_select_winner(exp_name)
        exp = self.ab.get_experiment(exp_name)
        self.assertNotEqual(exp.winner, "")

    def test_auto_select_winner_assign_returns_winner_after_conclusion(self):
        exp_name = "post_conclude"
        self.ab.create_experiment(exp_name, variants=["ctrl", "treat"],
                                  min_sample_size=50, target_confidence=0.95)
        self._inject_lopsided_data(exp_name)
        self.ab.auto_select_winner(exp_name)
        exp = self.ab.get_experiment(exp_name)
        winner = exp.winner
        for i in range(5):
            result = self.ab.assign(exp_name, f"new_user_{i}")
            self.assertEqual(result, winner)


# ---------------------------------------------------------------------------
# TestPauseResume
# ---------------------------------------------------------------------------

class TestPauseResume(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)
        self.ab.create_experiment("pr_exp", variants=["A", "B"])

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_pause_experiment_returns_string(self):
        result = self.ab.pause_experiment("pr_exp")
        self.assertIsInstance(result, str)

    def test_pause_experiment_mentions_name(self):
        result = self.ab.pause_experiment("pr_exp")
        self.assertIn("pr_exp", result)

    def test_pause_sets_status_paused(self):
        self.ab.pause_experiment("pr_exp")
        exp = self.ab.get_experiment("pr_exp")
        self.assertEqual(exp.status, "paused")

    def test_resume_experiment_returns_string(self):
        self.ab.pause_experiment("pr_exp")
        result = self.ab.resume_experiment("pr_exp")
        self.assertIsInstance(result, str)

    def test_resume_sets_status_running(self):
        self.ab.pause_experiment("pr_exp")
        self.ab.resume_experiment("pr_exp")
        exp = self.ab.get_experiment("pr_exp")
        self.assertEqual(exp.status, "running")

    def test_resume_mentions_name(self):
        self.ab.pause_experiment("pr_exp")
        result = self.ab.resume_experiment("pr_exp")
        self.assertIn("pr_exp", result)

    def test_pause_then_resume_experiment_is_running(self):
        self.ab.pause_experiment("pr_exp")
        self.ab.resume_experiment("pr_exp")
        exp = self.ab.get_experiment("pr_exp")
        self.assertEqual(exp.status, "running")

    def test_paused_experiment_still_assigns(self):
        """Paused experiments still return a variant (just status is paused)."""
        self.ab.pause_experiment("pr_exp")
        variant = self.ab.assign("pr_exp", "user_x")
        self.assertIn(variant, ["A", "B"])


# ---------------------------------------------------------------------------
# TestDeleteExperiment
# ---------------------------------------------------------------------------

class TestDeleteExperiment(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_delete_experiment_returns_string(self):
        self.ab.create_experiment("del_me", variants=["A", "B"])
        result = self.ab.delete_experiment("del_me")
        self.assertIsInstance(result, str)

    def test_delete_experiment_removes_from_db(self):
        self.ab.create_experiment("del_me2", variants=["A", "B"])
        self.ab.delete_experiment("del_me2")
        exp = self.ab.get_experiment("del_me2")
        self.assertIsNone(exp)

    def test_delete_experiment_removes_events(self):
        self.ab.create_experiment("del_events", variants=["A", "B"])
        for i in range(5):
            self.ab.assign("del_events", f"u{i}")
        self.ab.delete_experiment("del_events")
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM ab_events WHERE experiment='del_events'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 0)

    def test_delete_nonexistent_experiment_is_silent(self):
        result = self.ab.delete_experiment("ghost")
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# TestToolWrappers
# ---------------------------------------------------------------------------

class TestToolWrappers(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_create_experiment_tool_returns_string(self):
        result = self.ab.create_experiment_tool("tool_exp", variants=["A", "B"])
        self.assertIsInstance(result, str)

    def test_create_experiment_tool_success_message(self):
        result = self.ab.create_experiment_tool("tool_exp2", variants=["X", "Y"])
        self.assertIn("tool_exp2", result)

    def test_create_experiment_tool_default_variants(self):
        result = self.ab.create_experiment_tool("default_variants")
        self.assertIn("A", result)
        self.assertIn("B", result)

    def test_create_experiment_tool_error_returns_error_string(self):
        result = self.ab.create_experiment_tool("bad", variants=["solo"])
        self.assertIn("ERROR", result)

    def test_assign_tool_returns_string(self):
        self.ab.create_experiment_tool("assign_tool_exp", variants=["A", "B"])
        result = self.ab.assign_tool("assign_tool_exp", "user_1")
        self.assertIsInstance(result, str)

    def test_assign_tool_includes_participant_and_variant(self):
        self.ab.create_experiment_tool("assign_tool_exp2", variants=["A", "B"])
        result = self.ab.assign_tool("assign_tool_exp2", "participant_xyz")
        self.assertIn("participant_xyz", result)

    def test_assign_tool_error_for_missing_experiment(self):
        result = self.ab.assign_tool("ghost_exp", "user_1")
        self.assertIn("ERROR", result)

    def test_analyze_tool_returns_json_string(self):
        self.ab.create_experiment("analyze_tool_exp", variants=["A", "B"])
        for i in range(3):
            self.ab.assign("analyze_tool_exp", f"u{i}")
        result = self.ab.analyze_tool("analyze_tool_exp")
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, dict)

    def test_conclude_experiment_tool_returns_string(self):
        self.ab.create_experiment("conclude_tool", variants=["A", "B"])
        result = self.ab.conclude_experiment_tool("conclude_tool")
        self.assertIsInstance(result, str)

    def test_experiment_summary_returns_json_string(self):
        self.ab.create_experiment("sum_exp1", variants=["A", "B"])
        self.ab.create_experiment("sum_exp2", variants=["X", "Y"])
        result = self.ab.experiment_summary()
        self.assertIsInstance(result, str)
        parsed = json.loads(result)
        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 2)

    def test_experiment_summary_includes_totals(self):
        self.ab.create_experiment("sum_totals", variants=["A", "B"])
        for i in range(3):
            self.ab.assign("sum_totals", f"u{i}")
        result = json.loads(self.ab.experiment_summary())
        entry = next(e for e in result if e["name"] == "sum_totals")
        self.assertIn("total_impressions", entry)
        self.assertIn("total_conversions", entry)
        self.assertIn("total_revenue", entry)


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

    def test_data_persists_across_module_reloads(self):
        ab1 = _make_module(self.db_path)
        ab1.create_experiment("persist_exp", variants=["A", "B"])
        ab1.assign("persist_exp", "user_1")

        # Simulate a fresh module load
        ab2 = _make_module(self.db_path)
        exp = ab2.get_experiment("persist_exp")
        self.assertIsNotNone(exp)
        self.assertEqual(exp.variants, ["A", "B"])

    def test_events_persist_across_reloads(self):
        ab1 = _make_module(self.db_path)
        ab1.create_experiment("event_persist", variants=["A", "B"])
        for i in range(5):
            ab1.assign("event_persist", f"u{i}")

        ab2 = _make_module(self.db_path)
        stats = ab2._get_variant_stats("event_persist")
        total = sum(v.impressions for v in stats)
        self.assertEqual(total, 5)

    def test_schema_created_on_first_use(self):
        ab = _make_module(self.db_path)
        ab._ensure_schema()
        conn = sqlite3.connect(self.db_path)
        tables = {
            row[0] for row in
            conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        conn.close()
        self.assertIn("ab_experiments", tables)
        self.assertIn("ab_events", tables)


# ---------------------------------------------------------------------------
# TestConcurrentAssignments
# ---------------------------------------------------------------------------

class TestConcurrentAssignments(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)
        self.ab.create_experiment("concurrent_exp", variants=["A", "B"])

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_concurrent_assignments_do_not_corrupt_data(self):
        errors = []
        results = []
        lock = threading.Lock()

        def assign_users(start_idx):
            for i in range(start_idx, start_idx + 20):
                try:
                    v = self.ab.assign("concurrent_exp", f"concurrent_user_{i}")
                    with lock:
                        results.append(v)
                except Exception as e:
                    with lock:
                        errors.append(str(e))

        threads = [threading.Thread(target=assign_users, args=(i * 20,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrent errors: {errors}")
        self.assertEqual(len(results), 100)
        for r in results:
            self.assertIn(r, ["A", "B"])

    def test_concurrent_conversions_all_recorded(self):
        # First do all assignments
        for i in range(50):
            self.ab.assign("concurrent_exp", f"conv_thread_{i}")

        errors = []
        lock = threading.Lock()

        def record_convs(indices):
            for i in indices:
                try:
                    self.ab.record_conversion("concurrent_exp", f"conv_thread_{i}", revenue=5.0)
                except Exception as e:
                    with lock:
                        errors.append(str(e))

        chunks = [range(i, i + 10) for i in range(0, 50, 10)]
        threads = [threading.Thread(target=record_convs, args=(c,)) for c in chunks]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM ab_events WHERE experiment='concurrent_exp' "
            "AND event_type='conversion'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(count, 50)


# ---------------------------------------------------------------------------
# TestEdgeCases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.ab = _make_module(self.db_path)

    def tearDown(self):
        Path(self.db_path).unlink(missing_ok=True)

    def test_many_variants_experiment(self):
        variants = [f"v{i}" for i in range(10)]
        exp = self.ab.create_experiment("many_var", variants=variants)
        self.assertEqual(len(exp.variants), 10)

    def test_assign_many_variants_all_seen(self):
        variants = [f"v{i}" for i in range(10)]
        self.ab.create_experiment("big_exp", variants=variants)
        seen = set()
        for i in range(200):
            seen.add(self.ab.assign("big_exp", f"u_{i}"))
        self.assertEqual(seen, set(variants))

    def test_experiment_with_special_chars_in_name(self):
        exp = self.ab.create_experiment("test-exp_2024/Q4", variants=["A", "B"])
        fetched = self.ab.get_experiment("test-exp_2024/Q4")
        self.assertIsNotNone(fetched)

    def test_participant_id_with_special_chars(self):
        self.ab.create_experiment("special_id_exp", variants=["A", "B"])
        pid = "user@example.com|session:abc-123"
        variant = self.ab.assign("special_id_exp", pid)
        self.assertIn(variant, ["A", "B"])
        # Deterministic
        variant2 = self.ab.assign("special_id_exp", pid)
        self.assertEqual(variant, variant2)

    def test_very_high_revenue_value(self):
        self.ab.create_experiment("high_rev", variants=["A", "B"])
        self.ab.assign("high_rev", "whale_user")
        self.ab.record_conversion("high_rev", "whale_user", revenue=999999.99)
        stats = self.ab._get_variant_stats("high_rev")
        total = sum(v.revenue for v in stats)
        self.assertAlmostEqual(total, 999999.99, places=1)

    def test_zero_confidence_threshold(self):
        self.ab.create_experiment("zero_conf", variants=["A", "B"],
                                  target_confidence=0.0)
        exp = self.ab.get_experiment("zero_conf")
        self.assertAlmostEqual(exp.target_confidence, 0.0)

    def test_list_experiments_after_delete(self):
        self.ab.create_experiment("del_list1", variants=["A", "B"])
        self.ab.create_experiment("del_list2", variants=["A", "B"])
        self.ab.delete_experiment("del_list1")
        result = self.ab.list_experiments()
        names = [e["name"] for e in result]
        self.assertNotIn("del_list1", names)
        self.assertIn("del_list2", names)

    def test_analyze_three_variant_experiment_has_all_variants(self):
        self.ab.create_experiment("three_analyze", variants=["A", "B", "C"])
        for i in range(9):
            self.ab.assign("three_analyze", f"u{i}")
        result = self.ab.analyze("three_analyze")
        variant_names = [v["variant"] for v in result["variants"]]
        self.assertIn("A", variant_names)
        self.assertIn("B", variant_names)
        self.assertIn("C", variant_names)

    def test_experiment_from_row_round_trips_metadata(self):
        meta = {"key": "value", "nested": {"a": 1}}
        exp = self.ab.create_experiment("meta_round", variants=["A", "B"], metadata=meta)
        fetched = self.ab.get_experiment("meta_round")
        self.assertEqual(fetched.metadata["key"], "value")
        self.assertEqual(fetched.metadata["nested"]["a"], 1)


if __name__ == "__main__":
    unittest.main()
