"""
Tests for autoearn/core/funnel_builder.py

Isolation strategy: funnel_builder.py uses a module-level _conn singleton
initialised via cfg("funnel.db_path", ...).  We permanently replace cfg in the
module's namespace with a stub that always returns a private temp-file path,
then before each test we close + discard the cached connection and wipe all
tables so the schema is recreated fresh on next use.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Module-level isolation — permanently redirect cfg inside funnel_builder
# ---------------------------------------------------------------------------

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_fb_{uuid.uuid4().hex[:8]}.db")


def _fake_cfg(key: str, fallback=None):
    if key == "funnel.db_path":
        return _DB_PATH
    return fallback


# Import under a patch so the very first _db() call (if any) uses the test DB.
with patch("autoearn.core.funnel_builder.cfg", _fake_cfg):
    import autoearn.core.funnel_builder as fb

# Permanently replace cfg in the module namespace so every subsequent call to
# _db() (which calls cfg at connection time) gets the test path.
fb.cfg = _fake_cfg  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Autouse fixture — wipe DB and reset connection before each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_schema():
    # Close and discard the cached connection so the next _db() call
    # reconnects to a freshly wiped database.
    if fb._conn is not None:
        try:
            fb._conn.close()
        except Exception:
            pass
    fb._conn = None

    # Drop all user tables so schema is recreated from scratch.
    # sqlite_sequence is an internal SQLite table (from AUTOINCREMENT) that
    # cannot be dropped with DROP TABLE; skip it and DELETE its rows instead.
    conn = sqlite3.connect(_DB_PATH)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    for (t,) in tables:
        if t == "sqlite_sequence":
            conn.execute("DELETE FROM sqlite_sequence")
        else:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
    conn.commit()
    conn.close()
    yield

    # Teardown: close connection again to avoid leaking handles.
    if fb._conn is not None:
        try:
            fb._conn.close()
        except Exception:
            pass
    fb._conn = None


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_funnel(name="Test Funnel", **kwargs) -> int:
    """Create a funnel and return its ID."""
    return fb.create_funnel(name, **kwargs)


def _make_step(funnel_id: int, name="Step 1", **kwargs) -> int:
    """Add a step to a funnel and return the step ID."""
    return fb.add_step(funnel_id, name, **kwargs)


def _make_visitor(funnel_id: int, step_id: int, session_id: str = "", **kwargs) -> int:
    if not session_id:
        session_id = uuid.uuid4().hex
    return fb.record_visitor(funnel_id, step_id, session_id=session_id, **kwargs)


def _make_conversion(funnel_id: int, step_id: int, session_id: str, **kwargs) -> dict:
    return fb.record_conversion(funnel_id, step_id, session_id, **kwargs)


# ===========================================================================
# 1. TestFunnelCreation
# ===========================================================================

class TestFunnelCreation:

    def test_create_funnel_returns_positive_int(self):
        fid = fb.create_funnel("My Funnel")
        assert isinstance(fid, int)
        assert fid > 0

    def test_create_funnel_name_only(self):
        fid = fb.create_funnel("Simple Funnel")
        funnel = fb.get_funnel(fid)
        assert funnel is not None
        assert funnel["name"] == "Simple Funnel"

    def test_create_funnel_with_description(self):
        fid = fb.create_funnel("Funnel A", description="My description")
        funnel = fb.get_funnel(fid)
        assert funnel["description"] == "My description"

    def test_create_funnel_with_funnel_type(self):
        fid = fb.create_funnel("Launch Funnel", funnel_type="product_launch")
        funnel = fb.get_funnel(fid)
        assert funnel["funnel_type"] == "product_launch"

    def test_create_funnel_default_type_is_generic(self):
        fid = fb.create_funnel("Generic Funnel")
        funnel = fb.get_funnel(fid)
        assert funnel["funnel_type"] == "generic"

    def test_create_funnel_with_goal_revenue(self):
        fid = fb.create_funnel("Revenue Funnel", goal_revenue=5000.0)
        funnel = fb.get_funnel(fid)
        assert funnel["goal_revenue"] == 5000.0

    def test_create_funnel_with_tags(self):
        fid = fb.create_funnel("Tagged Funnel", tags=["email", "launch"])
        funnel = fb.get_funnel(fid)
        assert "email" in funnel["tags"]
        assert "launch" in funnel["tags"]

    def test_create_funnel_default_tags_empty_list(self):
        fid = fb.create_funnel("No Tags Funnel")
        funnel = fb.get_funnel(fid)
        assert funnel["tags"] == []

    def test_create_funnel_duplicate_name_upserts(self):
        fid1 = fb.create_funnel("Same Name", description="first")
        fid2 = fb.create_funnel("Same Name", description="second")
        # ON CONFLICT upsert returns same or a new rowid
        funnel = fb.get_funnel_by_name("Same Name")
        assert funnel is not None
        assert funnel["description"] == "second"

    def test_create_multiple_funnels_unique_ids(self):
        fid1 = fb.create_funnel("Funnel One")
        fid2 = fb.create_funnel("Funnel Two")
        assert fid1 != fid2

    def test_create_funnel_active_defaults_true(self):
        fid = fb.create_funnel("Active Funnel")
        funnel = fb.get_funnel(fid)
        assert funnel["active"] == 1

    def test_create_funnel_initial_visitors_zero(self):
        fid = fb.create_funnel("Fresh Funnel")
        funnel = fb.get_funnel(fid)
        assert funnel["total_visitors"] == 0

    def test_create_funnel_initial_revenue_zero(self):
        fid = fb.create_funnel("Fresh Funnel")
        funnel = fb.get_funnel(fid)
        assert funnel["total_revenue"] == 0.0


# ===========================================================================
# 2. TestFunnelRetrieval
# ===========================================================================

class TestFunnelRetrieval:

    def test_get_funnel_returns_none_for_missing(self):
        result = fb.get_funnel(99999)
        assert result is None

    def test_get_funnel_returns_dict(self):
        fid = _make_funnel("Funnel X")
        result = fb.get_funnel(fid)
        assert isinstance(result, dict)

    def test_get_funnel_contains_steps_key(self):
        fid = _make_funnel("Funnel X")
        result = fb.get_funnel(fid)
        assert "steps" in result

    def test_get_funnel_steps_initially_empty(self):
        fid = _make_funnel("Empty Steps")
        result = fb.get_funnel(fid)
        assert result["steps"] == []

    def test_get_funnel_by_name_returns_correct(self):
        fid = _make_funnel("Named Funnel")
        result = fb.get_funnel_by_name("Named Funnel")
        assert result is not None
        assert result["id"] == fid

    def test_get_funnel_by_name_returns_none_for_missing(self):
        assert fb.get_funnel_by_name("Nonexistent") is None

    def test_list_funnels_returns_list(self):
        _make_funnel("Funnel A")
        _make_funnel("Funnel B")
        funnels = fb.list_funnels()
        assert isinstance(funnels, list)
        assert len(funnels) >= 2

    def test_list_funnels_active_only_filter(self):
        fid = _make_funnel("Active F")
        fb.update_funnel(fid, active=0)
        _make_funnel("Another Active")
        active = fb.list_funnels(active_only=True)
        ids = [f["id"] for f in active]
        assert fid not in ids

    def test_list_funnels_by_funnel_type(self):
        _make_funnel("WF", funnel_type="webinar")
        _make_funnel("LGF", funnel_type="lead_gen")
        webinars = fb.list_funnels(funnel_type="webinar")
        types = {f["funnel_type"] for f in webinars}
        assert types == {"webinar"}

    def test_list_funnels_sorted_by_revenue_desc(self):
        fid1 = _make_funnel("Low Rev")
        fid2 = _make_funnel("High Rev")
        sid1 = _make_step(fid2, "Step")
        sess = uuid.uuid4().hex
        fb.record_visitor(fid2, sid1, session_id=sess)
        fb.record_conversion(fid2, sid1, sess, revenue=100.0)
        funnels = fb.list_funnels()
        revenues = [f["total_revenue"] for f in funnels]
        assert revenues == sorted(revenues, reverse=True)

    def test_get_funnel_includes_tags_as_list(self):
        fid = _make_funnel("Tag Test", tags=["a", "b"])
        funnel = fb.get_funnel(fid)
        assert isinstance(funnel["tags"], list)
        assert set(funnel["tags"]) == {"a", "b"}


# ===========================================================================
# 3. TestFunnelUpdate
# ===========================================================================

class TestFunnelUpdate:

    def test_update_funnel_name(self):
        fid = _make_funnel("Old Name")
        result = fb.update_funnel(fid, name="New Name")
        assert result is True
        funnel = fb.get_funnel(fid)
        assert funnel["name"] == "New Name"

    def test_update_funnel_description(self):
        fid = _make_funnel("F")
        fb.update_funnel(fid, description="Updated desc")
        funnel = fb.get_funnel(fid)
        assert funnel["description"] == "Updated desc"

    def test_update_funnel_type(self):
        fid = _make_funnel("F")
        fb.update_funnel(fid, funnel_type="webinar")
        funnel = fb.get_funnel(fid)
        assert funnel["funnel_type"] == "webinar"

    def test_update_funnel_active_false(self):
        fid = _make_funnel("F")
        fb.update_funnel(fid, active=0)
        funnel = fb.get_funnel(fid)
        assert funnel["active"] == 0

    def test_update_funnel_goal_revenue(self):
        fid = _make_funnel("F")
        fb.update_funnel(fid, goal_revenue=9999.0)
        funnel = fb.get_funnel(fid)
        assert funnel["goal_revenue"] == 9999.0

    def test_update_funnel_ignores_disallowed_fields(self):
        fid = _make_funnel("F")
        result = fb.update_funnel(fid, total_visitors=99999)
        # No allowed fields → returns False
        assert result is False

    def test_update_funnel_returns_false_for_empty_update(self):
        fid = _make_funnel("F")
        result = fb.update_funnel(fid)
        assert result is False

    def test_update_funnel_multiple_fields(self):
        fid = _make_funnel("F", funnel_type="generic")
        fb.update_funnel(fid, name="Updated", funnel_type="lead_gen")
        funnel = fb.get_funnel(fid)
        assert funnel["name"] == "Updated"
        assert funnel["funnel_type"] == "lead_gen"


# ===========================================================================
# 4. TestFunnelDuplicate
# ===========================================================================

class TestFunnelDuplicate:

    def test_duplicate_funnel_returns_new_id(self):
        fid = _make_funnel("Original")
        _make_step(fid, "Step A", step_type="optin", price=0.0)
        new_id = fb.duplicate_funnel(fid, "Clone")
        assert isinstance(new_id, int)
        assert new_id > 0
        assert new_id != fid

    def test_duplicate_funnel_copies_steps(self):
        fid = _make_funnel("Original")
        _make_step(fid, "Step A", step_type="optin")
        _make_step(fid, "Step B", step_type="sales")
        new_id = fb.duplicate_funnel(fid, "Clone")
        clone = fb.get_funnel(new_id)
        assert len(clone["steps"]) == 2

    def test_duplicate_funnel_step_names_match(self):
        fid = _make_funnel("Original")
        _make_step(fid, "Optin Page", step_type="optin")
        new_id = fb.duplicate_funnel(fid, "Clone")
        clone = fb.get_funnel(new_id)
        assert clone["steps"][0]["name"] == "Optin Page"

    def test_duplicate_funnel_inherits_type(self):
        fid = _make_funnel("Original", funnel_type="webinar")
        new_id = fb.duplicate_funnel(fid, "Clone Webinar")
        clone = fb.get_funnel(new_id)
        assert clone["funnel_type"] == "webinar"

    def test_duplicate_funnel_has_copy_description(self):
        fid = _make_funnel("Original")
        new_id = fb.duplicate_funnel(fid, "Clone")
        clone = fb.get_funnel(new_id)
        assert "Original" in clone["description"]

    def test_duplicate_nonexistent_returns_zero(self):
        result = fb.duplicate_funnel(99999, "Ghost Clone")
        assert result == 0

    def test_duplicate_funnel_copies_goal_revenue(self):
        fid = _make_funnel("Original", goal_revenue=1234.0)
        new_id = fb.duplicate_funnel(fid, "Clone")
        clone = fb.get_funnel(new_id)
        assert clone["goal_revenue"] == 1234.0

    def test_duplicate_funnel_step_prices_copied(self):
        fid = _make_funnel("Original")
        _make_step(fid, "Tripwire", step_type="sales", price=7.0)
        new_id = fb.duplicate_funnel(fid, "Clone")
        clone = fb.get_funnel(new_id)
        assert clone["steps"][0]["price"] == 7.0


# ===========================================================================
# 5. TestStepManagement
# ===========================================================================

class TestStepManagement:

    def test_add_step_returns_positive_int(self):
        fid = _make_funnel("F")
        sid = fb.add_step(fid, "Step 1")
        assert isinstance(sid, int)
        assert sid > 0

    def test_add_step_appears_in_funnel(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "My Step")
        funnel = fb.get_funnel(fid)
        assert len(funnel["steps"]) == 1
        assert funnel["steps"][0]["name"] == "My Step"

    def test_add_step_default_type_is_content(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Content Step")
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["step_type"] == "content"

    def test_add_step_with_explicit_type(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Sales Step", step_type="sales")
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["step_type"] == "sales"

    def test_add_step_auto_increments_order(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "First")
        fb.add_step(fid, "Second")
        funnel = fb.get_funnel(fid)
        orders = [s["step_order"] for s in funnel["steps"]]
        assert orders == sorted(orders)
        assert orders[1] > orders[0]

    def test_add_step_with_explicit_order(self):
        fid = _make_funnel("F")
        sid = fb.add_step(fid, "Step at 5", step_order=5)
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["step_order"] == 5

    def test_add_step_with_headline(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Step", headline="Big Headline Here")
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["headline"] == "Big Headline Here"

    def test_add_step_with_cta_text(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Step", cta_text="Click Me")
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["cta_text"] == "Click Me"

    def test_add_step_with_price(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Paid Step", price=97.0)
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["price"] == 97.0

    def test_add_step_with_expected_conv_rate(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Step", expected_conv_rate=0.25)
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["expected_conv_rate"] == 0.25

    def test_add_step_default_conv_rate(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Step")
        funnel = fb.get_funnel(fid)
        assert funnel["steps"][0]["expected_conv_rate"] == 0.10

    def test_add_step_conflict_upserts_by_order(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Original", step_order=1)
        fb.add_step(fid, "Replacement", step_order=1)
        funnel = fb.get_funnel(fid)
        step1 = [s for s in funnel["steps"] if s["step_order"] == 1][0]
        assert step1["name"] == "Replacement"

    def test_add_multiple_steps_ordered(self):
        fid = _make_funnel("F")
        fb.add_step(fid, "Alpha")
        fb.add_step(fid, "Beta")
        fb.add_step(fid, "Gamma")
        funnel = fb.get_funnel(fid)
        assert len(funnel["steps"]) == 3
        names = [s["name"] for s in funnel["steps"]]
        assert names == ["Alpha", "Beta", "Gamma"]


# ===========================================================================
# 6. TestVisitorTracking
# ===========================================================================

class TestVisitorTracking:

    def test_record_visitor_returns_positive_int(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        vid = fb.record_visitor(fid, sid)
        assert isinstance(vid, int)
        assert vid > 0

    def test_record_visitor_auto_generates_session_id(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        vid = fb.record_visitor(fid, sid)
        # Should not raise; session_id generated internally
        assert vid > 0

    def test_record_visitor_with_explicit_session_id(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = "mysession123"
        vid = fb.record_visitor(fid, sid, session_id=session)
        assert vid > 0

    def test_record_visitor_increments_total_visitors(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        fb.record_visitor(fid, sid)
        fb.record_visitor(fid, sid)
        funnel = fb.get_funnel(fid)
        assert funnel["total_visitors"] == 2

    def test_record_visitor_with_source(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        vid = fb.record_visitor(fid, sid, source="google")
        assert vid > 0

    def test_record_visitor_with_channel(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        vid = fb.record_visitor(fid, sid, channel="organic")
        assert vid > 0

    def test_record_exit_marks_exited_at(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        fb.record_exit(session, sid)
        # Verify via raw DB that exited_at is set
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT exited_at FROM funnel_visitors WHERE session_id=?", (session,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] is not None

    def test_record_visitor_does_not_set_converted(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT converted FROM funnel_visitors WHERE session_id=?", (session,)
        ).fetchone()
        conn.close()
        assert row[0] == 0


# ===========================================================================
# 7. TestConversionTracking
# ===========================================================================

class TestConversionTracking:

    def test_record_conversion_returns_dict(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        result = fb.record_conversion(fid, sid, session)
        assert isinstance(result, dict)

    def test_record_conversion_dict_has_expected_keys(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        result = fb.record_conversion(fid, sid, session)
        assert "conversion_id" in result
        assert "funnel_id" in result
        assert "step_id" in result
        assert "session_id" in result
        assert "revenue" in result

    def test_record_conversion_stores_revenue(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step", price=97.0)
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        result = fb.record_conversion(fid, sid, session, revenue=97.0)
        assert result["revenue"] == 97.0

    def test_record_conversion_updates_funnel_total_revenue(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        fb.record_conversion(fid, sid, session, revenue=50.0)
        funnel = fb.get_funnel(fid)
        assert funnel["total_revenue"] == 50.0

    def test_record_conversion_accumulates_revenue(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        s1, s2 = uuid.uuid4().hex, uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=s1)
        fb.record_visitor(fid, sid, session_id=s2)
        fb.record_conversion(fid, sid, s1, revenue=30.0)
        fb.record_conversion(fid, sid, s2, revenue=20.0)
        funnel = fb.get_funnel(fid)
        assert funnel["total_revenue"] == 50.0

    def test_record_conversion_with_order_id(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        result = fb.record_conversion(fid, sid, session, order_id="ORD-001")
        assert result["conversion_id"] > 0

    def test_record_conversion_with_product_name(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        result = fb.record_conversion(fid, sid, session, product_name="Course Pro")
        assert result["conversion_id"] > 0

    def test_record_conversion_marks_visitor_converted(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        fb.record_conversion(fid, sid, session)
        conn = sqlite3.connect(_DB_PATH)
        row = conn.execute(
            "SELECT converted FROM funnel_visitors WHERE session_id=?", (session,)
        ).fetchone()
        conn.close()
        assert row[0] == 1

    def test_record_conversion_zero_revenue(self):
        fid = _make_funnel("F")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        result = fb.record_conversion(fid, sid, session, revenue=0.0)
        assert result["revenue"] == 0.0
        funnel = fb.get_funnel(fid)
        assert funnel["total_revenue"] == 0.0


# ===========================================================================
# 8. TestAnalytics
# ===========================================================================

class TestAnalytics:

    def _setup_funnel_with_data(self):
        """Helper: create funnel + 2 steps, 3 visitors, 1 conversion."""
        fid = _make_funnel("Analytics Funnel", funnel_type="lead_gen")
        s1 = _make_step(fid, "Optin", step_type="optin", expected_conv_rate=0.5)
        s2 = _make_step(fid, "Sales", step_type="sales", price=47.0, expected_conv_rate=0.1)

        sess_a = uuid.uuid4().hex
        sess_b = uuid.uuid4().hex
        sess_c = uuid.uuid4().hex

        fb.record_visitor(fid, s1, session_id=sess_a, source="google")
        fb.record_visitor(fid, s1, session_id=sess_b, source="facebook")
        fb.record_visitor(fid, s1, session_id=sess_c, source="google")
        fb.record_conversion(fid, s1, sess_a)
        fb.record_visitor(fid, s2, session_id=sess_a, source="google")
        fb.record_conversion(fid, s2, sess_a, revenue=47.0)

        return fid, s1, s2

    def test_get_step_stats_returns_dict(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        stats = fb.get_step_stats(fid, s1)
        assert isinstance(stats, dict)

    def test_get_step_stats_visitor_count(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        stats = fb.get_step_stats(fid, s1)
        assert stats["visitors"] == 3

    def test_get_step_stats_conversion_count(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        stats = fb.get_step_stats(fid, s1)
        assert stats["conversions"] == 1

    def test_get_step_stats_conversion_rate(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        stats = fb.get_step_stats(fid, s1)
        # 1/3 * 100 = 33.33
        assert stats["conversion_rate_pct"] == pytest.approx(33.33, abs=0.01)

    def test_get_step_stats_revenue(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        stats = fb.get_step_stats(fid, s2)
        assert stats["revenue"] == pytest.approx(47.0)

    def test_get_step_stats_revenue_per_visitor(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        stats = fb.get_step_stats(fid, s2)
        assert stats["revenue_per_visitor"] == pytest.approx(47.0)

    def test_get_step_stats_no_visitors(self):
        fid = _make_funnel("Empty F")
        sid = _make_step(fid, "Step")
        stats = fb.get_step_stats(fid, sid)
        assert stats["visitors"] == 0
        assert stats["conversion_rate_pct"] == 0.0
        assert stats["revenue_per_visitor"] == 0.0

    def test_funnel_overview_returns_dict(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        assert isinstance(overview, dict)

    def test_funnel_overview_error_for_missing_funnel(self):
        overview = fb.funnel_overview(99999)
        assert "error" in overview

    def test_funnel_overview_has_step_count(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        assert overview["step_count"] == 2

    def test_funnel_overview_total_revenue(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        assert overview["total_revenue"] == pytest.approx(47.0)

    def test_funnel_overview_total_visitors(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        assert overview["total_visitors"] == 4  # 3 on s1 + 1 on s2

    def test_funnel_overview_revenue_per_visitor(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        # 47 / 4 total visitors
        assert overview["revenue_per_visitor"] == pytest.approx(47.0 / 4, abs=0.01)

    def test_funnel_overview_steps_have_conv_rate_vs_expected(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        for step in overview["steps"]:
            assert "conv_rate_vs_expected" in step

    def test_funnel_overview_steps_include_step_name(self):
        fid, s1, s2 = self._setup_funnel_with_data()
        overview = fb.funnel_overview(fid)
        names = [s["name"] for s in overview["steps"]]
        assert "Optin" in names
        assert "Sales" in names

    def test_best_performing_funnels_returns_list(self):
        _make_funnel("Perf Funnel")
        result = fb.best_performing_funnels(days=30, limit=5)
        assert isinstance(result, list)

    def test_best_performing_funnels_includes_period_revenue(self):
        fid = _make_funnel("BPF")
        sid = _make_step(fid, "Step")
        session = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=session)
        fb.record_conversion(fid, sid, session, revenue=200.0)
        result = fb.best_performing_funnels(days=30, limit=5)
        matching = [r for r in result if r["id"] == fid]
        assert len(matching) == 1
        assert matching[0]["period_revenue"] == pytest.approx(200.0)

    def test_best_performing_funnels_respects_limit(self):
        for i in range(5):
            _make_funnel(f"BPF {i}")
        result = fb.best_performing_funnels(days=30, limit=3)
        assert len(result) <= 3


# ===========================================================================
# 9. TestDropOffAnalysis
# ===========================================================================

class TestDropOffAnalysis:

    def _setup_dropoff_funnel(self):
        """Create funnel with 3 steps and decreasing visitor counts."""
        fid = _make_funnel("Dropoff Funnel")
        s1 = _make_step(fid, "Step 1")
        s2 = _make_step(fid, "Step 2")
        s3 = _make_step(fid, "Step 3")

        # 10 visitors on step 1
        for _ in range(10):
            fb.record_visitor(fid, s1)
        # 6 visitors on step 2
        for _ in range(6):
            fb.record_visitor(fid, s2)
        # 2 visitors on step 3
        for _ in range(2):
            fb.record_visitor(fid, s3)

        return fid, s1, s2, s3

    def test_drop_off_analysis_returns_list(self):
        fid, *_ = self._setup_dropoff_funnel()
        result = fb.drop_off_analysis(fid)
        assert isinstance(result, list)

    def test_drop_off_analysis_length_matches_step_count(self):
        fid, *_ = self._setup_dropoff_funnel()
        result = fb.drop_off_analysis(fid)
        assert len(result) == 3

    def test_drop_off_analysis_has_required_keys(self):
        fid, *_ = self._setup_dropoff_funnel()
        result = fb.drop_off_analysis(fid)
        for entry in result:
            assert "step_order" in entry
            assert "step_name" in entry
            assert "visitors" in entry
            assert "prev_visitors" in entry
            assert "drop_rate_pct" in entry
            assert "conversion_rate_pct" in entry
            assert "revenue" in entry

    def test_drop_off_analysis_sorted_by_worst_drop(self):
        fid, *_ = self._setup_dropoff_funnel()
        result = fb.drop_off_analysis(fid)
        drop_rates = [r["drop_rate_pct"] for r in result]
        assert drop_rates == sorted(drop_rates, reverse=True)

    def test_drop_off_analysis_drop_rate_nonnegative(self):
        fid, *_ = self._setup_dropoff_funnel()
        result = fb.drop_off_analysis(fid)
        for entry in result:
            assert entry["drop_rate_pct"] >= 0.0

    def test_drop_off_analysis_first_step_no_drop(self):
        """The first step always uses its own visitor count as prev, so drop=0."""
        fid, s1, *_ = self._setup_dropoff_funnel()
        result = fb.drop_off_analysis(fid)
        # Find the entry for step 1 (drop_rate=0 by definition)
        step1_entry = next((r for r in result if r["step_name"] == "Step 1"), None)
        assert step1_entry is not None
        assert step1_entry["drop_rate_pct"] == 0.0

    def test_drop_off_analysis_empty_funnel_returns_empty(self):
        fid = _make_funnel("No Steps Funnel")
        result = fb.drop_off_analysis(fid)
        assert result == []

    def test_funnel_revenue_by_source_returns_list(self):
        fid = _make_funnel("Src Funnel")
        sid = _make_step(fid, "Step")
        s1, s2 = uuid.uuid4().hex, uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=s1, source="email")
        fb.record_visitor(fid, sid, session_id=s2, source="seo")
        fb.record_conversion(fid, sid, s1, revenue=50.0)
        result = fb.funnel_revenue_by_source(fid, days=7)
        assert isinstance(result, list)
        sources = [r["source"] for r in result]
        assert "email" in sources

    def test_funnel_revenue_by_source_sorted_by_revenue_desc(self):
        fid = _make_funnel("Src Funnel 2")
        sid = _make_step(fid, "Step")
        for source, revenue in [("email", 100.0), ("seo", 30.0), ("ads", 60.0)]:
            sess = uuid.uuid4().hex
            fb.record_visitor(fid, sid, session_id=sess, source=source)
            fb.record_conversion(fid, sid, sess, revenue=revenue)
        result = fb.funnel_revenue_by_source(fid, days=7)
        revenues = [r["revenue"] for r in result]
        assert revenues == sorted(revenues, reverse=True)


# ===========================================================================
# 10. TestTemplates
# ===========================================================================

class TestTemplates:

    def test_funnel_templates_dict_exists(self):
        assert isinstance(fb.FUNNEL_TEMPLATES, dict)

    def test_funnel_templates_contains_lead_gen_basic(self):
        assert "lead_gen_basic" in fb.FUNNEL_TEMPLATES

    def test_funnel_templates_contains_webinar_funnel(self):
        assert "webinar_funnel" in fb.FUNNEL_TEMPLATES

    def test_funnel_templates_contains_tripwire_funnel(self):
        assert "tripwire_funnel" in fb.FUNNEL_TEMPLATES

    def test_lead_gen_basic_has_four_steps(self):
        steps = fb.FUNNEL_TEMPLATES["lead_gen_basic"]["steps"]
        assert len(steps) == 4

    def test_webinar_funnel_has_five_steps(self):
        steps = fb.FUNNEL_TEMPLATES["webinar_funnel"]["steps"]
        assert len(steps) == 5

    def test_tripwire_funnel_has_six_steps(self):
        steps = fb.FUNNEL_TEMPLATES["tripwire_funnel"]["steps"]
        assert len(steps) == 6

    def test_lead_gen_basic_funnel_type(self):
        assert fb.FUNNEL_TEMPLATES["lead_gen_basic"]["funnel_type"] == "lead_gen"

    def test_webinar_funnel_funnel_type(self):
        assert fb.FUNNEL_TEMPLATES["webinar_funnel"]["funnel_type"] == "webinar"

    def test_tripwire_funnel_funnel_type(self):
        assert fb.FUNNEL_TEMPLATES["tripwire_funnel"]["funnel_type"] == "tripwire"

    def test_create_from_template_lead_gen_returns_id(self):
        fid = fb.create_from_template("lead_gen_basic")
        assert isinstance(fid, int)
        assert fid > 0

    def test_create_from_template_webinar_returns_id(self):
        fid = fb.create_from_template("webinar_funnel")
        assert fid > 0

    def test_create_from_template_tripwire_returns_id(self):
        fid = fb.create_from_template("tripwire_funnel")
        assert fid > 0

    def test_create_from_template_invalid_returns_zero(self):
        result = fb.create_from_template("nonexistent_template")
        assert result == 0

    def test_create_from_template_creates_steps(self):
        fid = fb.create_from_template("lead_gen_basic")
        funnel = fb.get_funnel(fid)
        assert len(funnel["steps"]) == 4

    def test_create_from_template_custom_name(self):
        fid = fb.create_from_template("lead_gen_basic", funnel_name="My Custom Lead Gen")
        funnel = fb.get_funnel(fid)
        assert funnel["name"] == "My Custom Lead Gen"

    def test_create_from_template_default_name(self):
        fid = fb.create_from_template("lead_gen_basic")
        funnel = fb.get_funnel(fid)
        assert funnel["name"] == fb.FUNNEL_TEMPLATES["lead_gen_basic"]["name"]

    def test_create_from_template_step_types_preserved(self):
        fid = fb.create_from_template("lead_gen_basic")
        funnel = fb.get_funnel(fid)
        types = [s["step_type"] for s in funnel["steps"]]
        assert "optin" in types
        assert "thankyou" in types
        assert "sales" in types
        assert "upsell" in types

    def test_create_from_template_tripwire_has_downsell_step(self):
        fid = fb.create_from_template("tripwire_funnel")
        funnel = fb.get_funnel(fid)
        types = [s["step_type"] for s in funnel["steps"]]
        assert "downsell" in types

    def test_create_from_template_lead_gen_first_step_is_optin(self):
        fid = fb.create_from_template("lead_gen_basic")
        funnel = fb.get_funnel(fid)
        first = funnel["steps"][0]
        assert first["step_type"] == "optin"

    def test_create_from_template_webinar_has_checkout_step(self):
        fid = fb.create_from_template("webinar_funnel")
        funnel = fb.get_funnel(fid)
        types = [s["step_type"] for s in funnel["steps"]]
        assert "checkout" in types

    def test_template_tool_lists_templates_when_no_name(self):
        result = fb.template_tool()
        assert "lead_gen_basic" in result
        assert "webinar_funnel" in result
        assert "tripwire_funnel" in result

    def test_template_tool_creates_funnel_from_valid_name(self):
        result = fb.template_tool(template_name="lead_gen_basic")
        assert "created" in result.lower() or "id=" in result

    def test_template_tool_error_for_invalid_name(self):
        result = fb.template_tool(template_name="bad_template")
        assert "error" in result.lower() or "not found" in result.lower()


# ===========================================================================
# 11. TestToolWrappers  (bonus — validates the tool-facing API)
# ===========================================================================

class TestToolWrappers:

    def test_create_funnel_tool_success(self):
        result = fb.create_funnel_tool(name="Tool Funnel", funnel_type="generic")
        assert "created" in result
        assert "Tool Funnel" in result

    def test_create_funnel_tool_no_name_returns_error(self):
        result = fb.create_funnel_tool()
        assert "error" in result.lower()

    def test_add_step_tool_success(self):
        fid = _make_funnel("F for Tool")
        result = fb.add_step_tool(funnel_id=fid, name="Tool Step", step_type="optin")
        assert "added" in result
        assert "Tool Step" in result

    def test_add_step_tool_no_funnel_id_returns_error(self):
        result = fb.add_step_tool(name="Orphan Step")
        assert "error" in result.lower()

    def test_add_step_tool_no_name_returns_error(self):
        fid = _make_funnel("F for Tool")
        result = fb.add_step_tool(funnel_id=fid)
        assert "error" in result.lower()

    def test_funnel_stats_tool_success(self):
        fid = _make_funnel("Stats F")
        _make_step(fid, "Step")
        result = fb.funnel_stats_tool(funnel_id=fid)
        # Should return JSON
        data = json.loads(result)
        assert "name" in data
        assert "total_visitors" in data

    def test_funnel_stats_tool_no_id_returns_error(self):
        result = fb.funnel_stats_tool()
        assert "error" in result.lower()

    def test_funnel_stats_tool_missing_funnel_returns_error(self):
        result = fb.funnel_stats_tool(funnel_id=99999)
        assert "not found" in result.lower() or "error" in result.lower()

    def test_list_funnels_tool_returns_string(self):
        _make_funnel("Tool List F")
        result = fb.list_funnels_tool(active_only=False)
        assert isinstance(result, str)

    def test_list_funnels_tool_empty_db(self):
        result = fb.list_funnels_tool(active_only=True)
        assert "no funnels" in result.lower() or "[" in result

    def test_best_funnels_tool_returns_string(self):
        fid = _make_funnel("Best F")
        sid = _make_step(fid, "Step")
        sess = uuid.uuid4().hex
        fb.record_visitor(fid, sid, session_id=sess)
        fb.record_conversion(fid, sid, sess, revenue=500.0)
        result = fb.best_funnels_tool(days=30, limit=5)
        assert isinstance(result, str)

    def test_drop_off_tool_no_id_returns_error(self):
        result = fb.drop_off_tool()
        assert "error" in result.lower()

    def test_drop_off_tool_with_valid_funnel(self):
        fid = _make_funnel("DO Funnel")
        sid = _make_step(fid, "Step 1")
        fb.record_visitor(fid, sid)
        result = fb.drop_off_tool(funnel_id=fid)
        assert isinstance(result, str)
        # Should be JSON or "no funnel data"
        try:
            data = json.loads(result)
            assert isinstance(data, list)
        except json.JSONDecodeError:
            assert "no funnel data" in result.lower()

    def test_drop_off_tool_no_steps_returns_no_data(self):
        fid = _make_funnel("Empty DO Funnel")
        result = fb.drop_off_tool(funnel_id=fid)
        assert "no funnel data" in result.lower()


# ===========================================================================
# 12. TestDataclasses
# ===========================================================================

class TestDataclasses:

    def test_funnel_step_to_dict_has_required_keys(self):
        step = fb.FunnelStep(funnel_id=1, step_order=1, name="Test Step")
        d = step.to_dict()
        for key in ("id", "funnel_id", "step_order", "name", "step_type",
                    "headline", "description", "cta_text", "cta_url",
                    "price", "expected_conv_rate"):
            assert key in d

    def test_funnel_step_default_type(self):
        step = fb.FunnelStep(funnel_id=1, step_order=1, name="S")
        assert step.step_type == "content"

    def test_funnel_step_default_conv_rate(self):
        step = fb.FunnelStep(funnel_id=1, step_order=1, name="S")
        assert step.expected_conv_rate == 0.10

    def test_funnel_dataclass_revenue_progress_zero_when_no_goal(self):
        f = fb.Funnel(name="F", goal_revenue=0.0, total_revenue=100.0)
        assert f.revenue_progress_pct == 0.0

    def test_funnel_dataclass_revenue_progress_pct(self):
        f = fb.Funnel(name="F", goal_revenue=1000.0, total_revenue=250.0)
        assert f.revenue_progress_pct == pytest.approx(25.0)

    def test_funnel_to_dict_has_required_keys(self):
        f = fb.Funnel(name="F")
        d = f.to_dict()
        for key in ("id", "name", "description", "funnel_type", "active",
                    "goal_revenue", "total_visitors", "total_revenue",
                    "revenue_progress_pct", "steps", "tags", "created_at"):
            assert key in d

    def test_funnel_to_dict_total_revenue_rounded(self):
        f = fb.Funnel(name="F", total_revenue=1.23456789)
        d = f.to_dict()
        # total_revenue rounded to 4 decimal places
        assert d["total_revenue"] == round(1.23456789, 4)

    def test_step_types_list_contents(self):
        for t in ("optin", "sales", "upsell", "downsell", "thankyou",
                  "webinar", "content", "quiz", "checkout", "vsl"):
            assert t in fb.STEP_TYPES

    def test_funnel_types_list_contents(self):
        for t in ("lead_gen", "product_launch", "webinar", "tripwire",
                  "high_ticket", "saas_trial", "affiliate", "generic"):
            assert t in fb.FUNNEL_TYPES
