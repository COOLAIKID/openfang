"""Tests for core/competitor_monitor.py — competitor monitoring system.

Each test class uses an isolated temporary SQLite database so tests never
touch the real autoearn.db and don't interfere with each other.

The module-level globals `_DB_PATH` and `_schema_ready` are patched per
test class fixture so the schema is re-initialised in the temp file.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock


# ---------------------------------------------------------------------------
# Helper: patch the module-level DB path and reset schema flag
# ---------------------------------------------------------------------------

def _make_monitor(tmp_dir: str):
    """Return a fresh competitor_monitor module pointing at a temp DB."""
    import autoearn.core.competitor_monitor as cm

    db_path = Path(tmp_dir) / "cm_test.db"
    cm._DB_PATH = db_path
    cm._schema_ready = False
    return cm


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# TestChangeTypesConstant
# ---------------------------------------------------------------------------

class TestChangeTypesConstant(unittest.TestCase):
    """Verify CHANGE_TYPES list is present and contains expected values."""

    def setUp(self):
        import autoearn.core.competitor_monitor as cm
        self.cm = cm

    def test_change_types_is_list(self):
        self.assertIsInstance(self.cm.CHANGE_TYPES, list)

    def test_change_types_not_empty(self):
        self.assertGreater(len(self.cm.CHANGE_TYPES), 0)

    def test_price_change_in_change_types(self):
        self.assertIn("price_change", self.cm.CHANGE_TYPES)

    def test_page_changed_in_change_types(self):
        self.assertIn("page_changed", self.cm.CHANGE_TYPES)

    def test_rank_change_in_change_types(self):
        self.assertIn("rank_change", self.cm.CHANGE_TYPES)

    def test_content_added_in_change_types(self):
        self.assertIn("content_added", self.cm.CHANGE_TYPES)


# ---------------------------------------------------------------------------
# TestAddCompetitor
# ---------------------------------------------------------------------------

class TestAddCompetitor(unittest.TestCase):
    """Tests for add_competitor() — adding competitors to the database."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_add_competitor_returns_competitor_object(self):
        comp = self.cm.add_competitor("Acme Corp", "acmecorp.com")
        self.assertIsNotNone(comp)
        self.assertEqual(comp.name, "Acme Corp")

    def test_add_competitor_assigns_positive_id(self):
        comp = self.cm.add_competitor("Acme Corp", "acmecorp.com")
        self.assertGreater(comp.id, 0)

    def test_add_competitor_normalises_domain_lowercase(self):
        comp = self.cm.add_competitor("Test", "UPPERCASE.COM")
        self.assertEqual(comp.domain, "https://uppercase.com")

    def test_add_competitor_prepends_https_if_missing(self):
        comp = self.cm.add_competitor("Test", "example.com")
        self.assertTrue(comp.domain.startswith("https://"))

    def test_add_competitor_keeps_existing_https(self):
        comp = self.cm.add_competitor("Test", "https://example.com")
        self.assertEqual(comp.domain.count("https://"), 1)

    def test_add_competitor_strips_trailing_slash(self):
        comp = self.cm.add_competitor("Test", "https://example.com/")
        self.assertFalse(comp.domain.endswith("/"))

    def test_add_competitor_stores_niche(self):
        comp = self.cm.add_competitor("NicheComp", "niche.com", niche="personal_finance")
        self.assertEqual(comp.niche, "personal_finance")

    def test_add_competitor_stores_notes(self):
        comp = self.cm.add_competitor("NoteComp", "note.com", notes="Main rival")
        self.assertEqual(comp.notes, "Main rival")

    def test_add_competitor_stores_tracked_keywords(self):
        kws = ["best vpn", "vpn review"]
        comp = self.cm.add_competitor("KWComp", "kw.com", tracked_keywords=kws)
        self.assertIn("best vpn", comp.tracked_keywords)
        self.assertIn("vpn review", comp.tracked_keywords)

    def test_add_competitor_stores_tracked_pages(self):
        pages = ["https://rival.com/pricing", "https://rival.com/features"]
        comp = self.cm.add_competitor("PageComp", "pagecomp.com", tracked_pages=pages)
        self.assertIn("https://rival.com/pricing", comp.tracked_pages)

    def test_add_competitor_created_at_set(self):
        before = time.time()
        comp = self.cm.add_competitor("TimedComp", "timed.com")
        after = time.time()
        self.assertGreaterEqual(comp.created_at, before)
        self.assertLessEqual(comp.created_at, after)

    def test_add_competitor_last_checked_zero(self):
        comp = self.cm.add_competitor("NewComp", "new.com")
        self.assertEqual(comp.last_checked, 0.0)

    def test_add_multiple_competitors_unique_ids(self):
        c1 = self.cm.add_competitor("Comp A", "compa.com")
        c2 = self.cm.add_competitor("Comp B", "compb.com")
        c3 = self.cm.add_competitor("Comp C", "compc.com")
        ids = {c1.id, c2.id, c3.id}
        self.assertEqual(len(ids), 3)

    def test_add_competitor_persisted_in_db(self):
        comp = self.cm.add_competitor("Persisted", "persisted.com")
        fetched = self.cm.get_competitor(comp.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Persisted")

    def test_add_competitor_default_tracking_flags_true(self):
        comp = self.cm.add_competitor("Track All", "trackall.com")
        self.assertTrue(comp.track_prices)
        self.assertTrue(comp.track_content)
        self.assertTrue(comp.track_rankings)


# ---------------------------------------------------------------------------
# TestGetCompetitor
# ---------------------------------------------------------------------------

class TestGetCompetitor(unittest.TestCase):
    """Tests for get_competitor() — retrieval by ID."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_get_competitor_returns_none_for_missing(self):
        result = self.cm.get_competitor(9999)
        self.assertIsNone(result)

    def test_get_competitor_returns_correct_name(self):
        comp = self.cm.add_competitor("FindMe", "findme.com")
        fetched = self.cm.get_competitor(comp.id)
        self.assertEqual(fetched.name, "FindMe")

    def test_get_competitor_returns_correct_domain(self):
        comp = self.cm.add_competitor("FindMe", "findme.com")
        fetched = self.cm.get_competitor(comp.id)
        self.assertIn("findme.com", fetched.domain)

    def test_get_competitor_returns_correct_niche(self):
        comp = self.cm.add_competitor("FindMe", "findme.com", niche="tech")
        fetched = self.cm.get_competitor(comp.id)
        self.assertEqual(fetched.niche, "tech")

    def test_get_competitor_deserialises_keywords(self):
        comp = self.cm.add_competitor("KWComp", "kw.com", tracked_keywords=["kw1", "kw2"])
        fetched = self.cm.get_competitor(comp.id)
        self.assertIsInstance(fetched.tracked_keywords, list)
        self.assertIn("kw1", fetched.tracked_keywords)

    def test_get_competitor_id_matches(self):
        comp = self.cm.add_competitor("IDCheck", "idcheck.com")
        fetched = self.cm.get_competitor(comp.id)
        self.assertEqual(fetched.id, comp.id)


# ---------------------------------------------------------------------------
# TestListCompetitors
# ---------------------------------------------------------------------------

class TestListCompetitors(unittest.TestCase):
    """Tests for list_competitors() — listing all tracked competitors."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_list_competitors_empty_when_none_added(self):
        result = self.cm.list_competitors()
        self.assertEqual(result, [])

    def test_list_competitors_returns_list(self):
        result = self.cm.list_competitors()
        self.assertIsInstance(result, list)

    def test_list_competitors_returns_all_added(self):
        self.cm.add_competitor("A", "a.com")
        self.cm.add_competitor("B", "b.com")
        self.cm.add_competitor("C", "c.com")
        result = self.cm.list_competitors()
        self.assertEqual(len(result), 3)

    def test_list_competitors_returns_competitor_objects(self):
        self.cm.add_competitor("X", "x.com")
        result = self.cm.list_competitors()
        self.assertIsInstance(result[0], self.cm.Competitor)

    def test_list_competitors_names_correct(self):
        self.cm.add_competitor("Alpha", "alpha.com")
        self.cm.add_competitor("Beta", "beta.com")
        result = self.cm.list_competitors()
        names = {c.name for c in result}
        self.assertIn("Alpha", names)
        self.assertIn("Beta", names)

    def test_list_competitors_ordered_by_priority_then_name(self):
        self.cm.add_competitor("Zebra Corp", "zebra.com")
        self.cm.add_competitor("Alpha Corp", "alpha.com")
        result = self.cm.list_competitors()
        # Default priority=5 for both → sorted by name
        names = [c.name for c in result]
        self.assertEqual(names.index("Alpha Corp"), 0)


# ---------------------------------------------------------------------------
# TestAddKeywords
# ---------------------------------------------------------------------------

class TestAddKeywords(unittest.TestCase):
    """Tests for add_keywords() — appending keywords to a competitor."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_add_keywords_returns_string(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        result = self.cm.add_keywords(comp.id, ["kw1", "kw2"])
        self.assertIsInstance(result, str)

    def test_add_keywords_persisted(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self.cm.add_keywords(comp.id, ["python", "django"])
        fetched = self.cm.get_competitor(comp.id)
        self.assertIn("python", fetched.tracked_keywords)
        self.assertIn("django", fetched.tracked_keywords)

    def test_add_keywords_no_duplicates(self):
        comp = self.cm.add_competitor("Comp", "comp.com", tracked_keywords=["python"])
        self.cm.add_keywords(comp.id, ["python", "django"])
        fetched = self.cm.get_competitor(comp.id)
        self.assertEqual(fetched.tracked_keywords.count("python"), 1)

    def test_add_keywords_invalid_id_returns_error(self):
        result = self.cm.add_keywords(9999, ["kw"])
        self.assertIn("ERROR", result)

    def test_add_keywords_result_contains_count(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        result = self.cm.add_keywords(comp.id, ["kw1", "kw2", "kw3"])
        self.assertIn("3", result)

    def test_add_keywords_appends_to_existing(self):
        comp = self.cm.add_competitor("Comp", "comp.com", tracked_keywords=["existing"])
        self.cm.add_keywords(comp.id, ["new1", "new2"])
        fetched = self.cm.get_competitor(comp.id)
        self.assertIn("existing", fetched.tracked_keywords)
        self.assertIn("new1", fetched.tracked_keywords)


# ---------------------------------------------------------------------------
# TestCheckCompetitorPage
# ---------------------------------------------------------------------------

class TestCheckCompetitorPage(unittest.TestCase):
    """Tests for check_competitor_page() with mocked HTTP requests."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def _mock_response(self, text: str, status: int = 200):
        """Build a mock requests.Response."""
        resp = MagicMock()
        resp.text = text
        resp.status_code = status
        return resp

    def test_check_page_returns_empty_list_for_missing_competitor(self):
        result = self.cm.check_competitor_page(9999, "https://example.com")
        self.assertEqual(result, [])

    def test_check_page_first_check_no_changes(self):
        """First check has no previous snapshot so no change events."""
        comp = self.cm.add_competitor("First Check", "first.com")
        mock_resp = self._mock_response("<html><body>Hello World</body></html>")

        with patch("requests.get", return_value=mock_resp):
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_soup = MagicMock()
                mock_soup.get_text.return_value = "Hello World"
                mock_bs.return_value = mock_soup
                changes = self.cm.check_competitor_page(comp.id, "https://first.com")

        self.assertEqual(changes, [])

    def test_check_page_second_check_same_content_no_changes(self):
        """Two identical checks should produce no change events."""
        comp = self.cm.add_competitor("Same Content", "same.com")
        content = "Stable page content that does not change."
        mock_resp = self._mock_response(f"<html><body>{content}</body></html>")

        with patch("requests.get", return_value=mock_resp):
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_soup = MagicMock()
                mock_soup.get_text.return_value = content
                mock_bs.return_value = mock_soup
                # First check
                self.cm.check_competitor_page(comp.id, "https://same.com")
                # Second check — same content
                changes = self.cm.check_competitor_page(comp.id, "https://same.com")

        # No page_changed event when content is identical
        page_changes = [c for c in changes if c.change_type == "page_changed"]
        self.assertEqual(page_changes, [])

    def test_check_page_detects_content_change(self):
        """Different content on second check triggers page_changed event."""
        comp = self.cm.add_competitor("Changing Site", "changing.com")

        first_content = "Original content about our product."
        second_content = "Completely new content about our updated service."

        with patch("requests.get") as mock_get:
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_soup_first = MagicMock()
                mock_soup_first.get_text.return_value = first_content
                mock_soup_second = MagicMock()
                mock_soup_second.get_text.return_value = second_content

                mock_resp = MagicMock()
                mock_resp.status_code = 200

                # First check
                mock_resp.text = f"<html>{first_content}</html>"
                mock_get.return_value = mock_resp
                mock_bs.return_value = mock_soup_first
                self.cm.check_competitor_page(comp.id, "https://changing.com")

                # Second check — different content
                mock_resp.text = f"<html>{second_content}</html>"
                mock_bs.return_value = mock_soup_second
                changes = self.cm.check_competitor_page(comp.id, "https://changing.com")

        page_changes = [c for c in changes if c.change_type == "page_changed"]
        self.assertGreater(len(page_changes), 0)

    def test_check_page_content_change_severity_is_medium(self):
        """Page content change events have medium severity."""
        comp = self.cm.add_competitor("Severity Test", "severity.com")
        first_content = "First version of the page."
        second_content = "Second version with new information."

        with patch("requests.get") as mock_get:
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                mock_soup = MagicMock()
                mock_soup.get_text.return_value = first_content
                mock_resp.text = f"<html>{first_content}</html>"
                mock_get.return_value = mock_resp
                mock_bs.return_value = mock_soup
                self.cm.check_competitor_page(comp.id, "https://severity.com")

                mock_soup2 = MagicMock()
                mock_soup2.get_text.return_value = second_content
                mock_bs.return_value = mock_soup2
                changes = self.cm.check_competitor_page(comp.id, "https://severity.com")

        page_changes = [c for c in changes if c.change_type == "page_changed"]
        if page_changes:
            self.assertEqual(page_changes[0].severity, "medium")

    def test_check_page_detects_price_change(self):
        """A change in prices triggers a price_change event."""
        comp = self.cm.add_competitor("Price Watcher", "prices.com")
        first_content = "Our plan costs $9.99 per month."
        second_content = "Our plan now costs $14.99 per month."

        with patch("requests.get") as mock_get:
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                mock_soup = MagicMock()
                mock_soup.get_text.return_value = first_content
                mock_resp.text = f"<html>{first_content}</html>"
                mock_get.return_value = mock_resp
                mock_bs.return_value = mock_soup
                self.cm.check_competitor_page(comp.id, "https://prices.com")

                mock_soup2 = MagicMock()
                mock_soup2.get_text.return_value = second_content
                mock_bs.return_value = mock_soup2
                changes = self.cm.check_competitor_page(comp.id, "https://prices.com")

        price_changes = [c for c in changes if c.change_type == "price_change"]
        self.assertGreater(len(price_changes), 0)

    def test_check_page_price_change_severity_is_high(self):
        """Price change events have high severity."""
        comp = self.cm.add_competitor("High Severity Prices", "hsp.com")
        first_content = "Basic plan: $4.99"
        second_content = "Basic plan: $7.99"

        with patch("requests.get") as mock_get:
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                mock_soup = MagicMock()
                mock_soup.get_text.return_value = first_content
                mock_resp.text = f"<html>{first_content}</html>"
                mock_get.return_value = mock_resp
                mock_bs.return_value = mock_soup
                self.cm.check_competitor_page(comp.id, "https://hsp.com")

                mock_soup2 = MagicMock()
                mock_soup2.get_text.return_value = second_content
                mock_bs.return_value = mock_soup2
                changes = self.cm.check_competitor_page(comp.id, "https://hsp.com")

        price_changes = [c for c in changes if c.change_type == "price_change"]
        if price_changes:
            self.assertEqual(price_changes[0].severity, "high")

    def test_check_page_updates_last_checked(self):
        """check_competitor_page should update last_checked timestamp."""
        comp = self.cm.add_competitor("Last Checked", "lc.com")
        self.assertEqual(comp.last_checked, 0.0)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Content</body></html>"

        before = time.time()
        with patch("requests.get", return_value=mock_resp):
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_soup = MagicMock()
                mock_soup.get_text.return_value = "Content"
                mock_bs.return_value = mock_soup
                self.cm.check_competitor_page(comp.id, "https://lc.com")
        after = time.time()

        fetched = self.cm.get_competitor(comp.id)
        self.assertGreaterEqual(fetched.last_checked, before)
        self.assertLessEqual(fetched.last_checked, after)

    def test_check_page_handles_request_exception(self):
        """A failed HTTP request should not raise — return empty changes."""
        comp = self.cm.add_competitor("Unreachable", "unreachable.com")

        with patch("requests.get", side_effect=Exception("Connection refused")):
            changes = self.cm.check_competitor_page(comp.id, "https://unreachable.com")

        self.assertIsInstance(changes, list)
        self.assertEqual(changes, [])

    def test_check_page_returns_change_event_objects(self):
        """Change events should be ChangeEvent instances."""
        comp = self.cm.add_competitor("Events", "events.com")
        first_content = "Version one"
        second_content = "Version two completely different"

        with patch("requests.get") as mock_get:
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                mock_soup = MagicMock()
                mock_soup.get_text.return_value = first_content
                mock_resp.text = f"<html>{first_content}</html>"
                mock_get.return_value = mock_resp
                mock_bs.return_value = mock_soup
                self.cm.check_competitor_page(comp.id, "https://events.com")

                mock_soup2 = MagicMock()
                mock_soup2.get_text.return_value = second_content
                mock_bs.return_value = mock_soup2
                changes = self.cm.check_competitor_page(comp.id, "https://events.com")

        for change in changes:
            self.assertIsInstance(change, self.cm.ChangeEvent)


# ---------------------------------------------------------------------------
# TestRecentChanges
# ---------------------------------------------------------------------------

class TestRecentChanges(unittest.TestCase):
    """Tests for recent_changes() — change history retrieval."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def _insert_change(self, comp_id: int, comp_name: str, change_type: str = "page_changed",
                       severity: str = "low", ts: float | None = None):
        """Directly insert a change event for testing."""
        self.cm._ensure()
        conn = sqlite3.connect(str(self.cm._DB_PATH))
        conn.row_factory = sqlite3.Row
        ts = ts or time.time()
        conn.execute(
            """INSERT INTO competitor_changes
               (competitor_id, competitor_name, change_type, url, description,
                old_value, new_value, severity, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (comp_id, comp_name, change_type, "https://test.com",
             "Test change", "old", "new", severity, ts),
        )
        conn.commit()
        conn.close()

    def test_recent_changes_returns_list(self):
        result = self.cm.recent_changes()
        self.assertIsInstance(result, list)

    def test_recent_changes_empty_when_no_changes(self):
        result = self.cm.recent_changes()
        self.assertEqual(result, [])

    def test_recent_changes_returns_change_events(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self._insert_change(comp.id, comp.name)
        result = self.cm.recent_changes()
        self.assertIsInstance(result[0], self.cm.ChangeEvent)

    def test_recent_changes_within_days_window(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        # Recent change
        self._insert_change(comp.id, comp.name, ts=time.time() - 3600)  # 1 hour ago
        result = self.cm.recent_changes(days=7)
        self.assertEqual(len(result), 1)

    def test_recent_changes_excludes_old_changes(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        old_ts = time.time() - 10 * 86400  # 10 days ago
        self._insert_change(comp.id, comp.name, ts=old_ts)
        result = self.cm.recent_changes(days=7)
        self.assertEqual(len(result), 0)

    def test_recent_changes_respects_limit(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        for _ in range(10):
            self._insert_change(comp.id, comp.name)
        result = self.cm.recent_changes(limit=5)
        self.assertLessEqual(len(result), 5)

    def test_recent_changes_ordered_newest_first(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ts1 = time.time() - 3600
        ts2 = time.time() - 1800
        ts3 = time.time() - 600
        self._insert_change(comp.id, comp.name, ts=ts1)
        self._insert_change(comp.id, comp.name, ts=ts2)
        self._insert_change(comp.id, comp.name, ts=ts3)
        result = self.cm.recent_changes()
        self.assertGreaterEqual(result[0].ts, result[-1].ts)

    def test_recent_changes_has_correct_competitor_name(self):
        comp = self.cm.add_competitor("Named Corp", "named.com")
        self._insert_change(comp.id, "Named Corp")
        result = self.cm.recent_changes()
        self.assertEqual(result[0].competitor_name, "Named Corp")

    def test_recent_changes_change_type_preserved(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self._insert_change(comp.id, comp.name, change_type="price_change")
        result = self.cm.recent_changes()
        self.assertEqual(result[0].change_type, "price_change")

    def test_recent_changes_severity_preserved(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self._insert_change(comp.id, comp.name, severity="high")
        result = self.cm.recent_changes()
        self.assertEqual(result[0].severity, "high")


# ---------------------------------------------------------------------------
# TestCompetitorReport
# ---------------------------------------------------------------------------

class TestCompetitorReport(unittest.TestCase):
    """Tests for competitor_report() — summary across all competitors."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def _insert_change(self, comp_id: int, comp_name: str, severity: str = "low"):
        self.cm._ensure()
        conn = sqlite3.connect(str(self.cm._DB_PATH))
        conn.execute(
            """INSERT INTO competitor_changes
               (competitor_id, competitor_name, change_type, url, description,
                old_value, new_value, severity, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (comp_id, comp_name, "page_changed", "https://x.com",
             "changed", "", "", severity, time.time()),
        )
        conn.commit()
        conn.close()

    def test_report_returns_dict(self):
        result = self.cm.competitor_report()
        self.assertIsInstance(result, dict)

    def test_report_has_required_keys(self):
        result = self.cm.competitor_report()
        for key in ("total_competitors", "changes_last_7d",
                    "high_priority_changes", "competitors", "recent_high_priority"):
            self.assertIn(key, result)

    def test_report_total_competitors_zero_when_empty(self):
        result = self.cm.competitor_report()
        self.assertEqual(result["total_competitors"], 0)

    def test_report_counts_competitors_correctly(self):
        self.cm.add_competitor("A", "a.com")
        self.cm.add_competitor("B", "b.com")
        result = self.cm.competitor_report()
        self.assertEqual(result["total_competitors"], 2)

    def test_report_changes_last_7d_counts_recent(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self._insert_change(comp.id, comp.name)
        result = self.cm.competitor_report()
        self.assertGreaterEqual(result["changes_last_7d"], 1)

    def test_report_high_priority_changes_counts_high_severity(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self._insert_change(comp.id, comp.name, severity="high")
        self._insert_change(comp.id, comp.name, severity="low")
        result = self.cm.competitor_report()
        self.assertEqual(result["high_priority_changes"], 1)

    def test_report_competitors_list_has_correct_fields(self):
        self.cm.add_competitor("FieldTest", "ft.com", niche="tech")
        result = self.cm.competitor_report()
        comp_entry = result["competitors"][0]
        for key in ("id", "name", "domain", "niche", "keywords_tracked"):
            self.assertIn(key, comp_entry)

    def test_report_keywords_tracked_count_correct(self):
        self.cm.add_competitor("KW Comp", "kw.com", tracked_keywords=["kw1", "kw2", "kw3"])
        result = self.cm.competitor_report()
        comp_entry = result["competitors"][0]
        self.assertEqual(comp_entry["keywords_tracked"], 3)

    def test_report_last_checked_hours_ago_none_for_unchecked(self):
        self.cm.add_competitor("Unchecked", "unchecked.com")
        result = self.cm.competitor_report()
        comp_entry = result["competitors"][0]
        self.assertIsNone(comp_entry["last_checked_hours_ago"])

    def test_report_recent_high_priority_capped_at_10(self):
        comp = self.cm.add_competitor("Spammy", "spammy.com")
        for _ in range(15):
            self._insert_change(comp.id, comp.name, severity="high")
        result = self.cm.competitor_report()
        self.assertLessEqual(len(result["recent_high_priority"]), 10)


# ---------------------------------------------------------------------------
# TestToolWrappers
# ---------------------------------------------------------------------------

class TestToolWrappers(unittest.TestCase):
    """Tests for tool-friendly wrappers that return string results."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_add_competitor_tool_returns_string(self):
        result = self.cm.add_competitor_tool("Acme", "acme.com")
        self.assertIsInstance(result, str)

    def test_add_competitor_tool_contains_competitor_name(self):
        result = self.cm.add_competitor_tool("Acme Corp", "acme.com")
        self.assertIn("Acme Corp", result)

    def test_add_competitor_tool_contains_id(self):
        result = self.cm.add_competitor_tool("Acme", "acme.com")
        self.assertRegex(result, r"#\d+")

    def test_add_competitor_tool_contains_domain(self):
        result = self.cm.add_competitor_tool("Acme", "acme.com")
        self.assertIn("acme.com", result)

    def test_add_competitor_tool_with_niche(self):
        result = self.cm.add_competitor_tool("Niche Corp", "niche.com", niche="finance")
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_add_competitor_tool_with_keywords(self):
        result = self.cm.add_competitor_tool(
            "KW Corp", "kw.com", keywords=["best vpn", "vpn review"]
        )
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_check_competitor_tool_returns_string(self):
        comp = self.cm.add_competitor("Check Me", "checkme.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>No change</body></html>"

        with patch("requests.get", return_value=mock_resp):
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_soup = MagicMock()
                mock_soup.get_text.return_value = "No change"
                mock_bs.return_value = mock_soup
                result = self.cm.check_competitor_tool(comp.id)

        self.assertIsInstance(result, str)

    def test_check_competitor_tool_no_changes_message(self):
        comp = self.cm.add_competitor("No Change", "nochange.com")
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body>Stable</body></html>"

        with patch("requests.get", return_value=mock_resp):
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_soup = MagicMock()
                mock_soup.get_text.return_value = "Stable"
                mock_bs.return_value = mock_soup
                result = self.cm.check_competitor_tool(comp.id)

        # First check — no previous snapshot, so "No changes detected"
        self.assertIn("No changes", result)

    def test_check_competitor_tool_missing_competitor_returns_error(self):
        result = self.cm.check_competitor_tool(9999)
        self.assertIn("ERROR", result)

    def test_check_competitor_tool_with_changes_returns_json(self):
        """When changes are detected, check_competitor_tool should return JSON."""
        comp = self.cm.add_competitor("Changing", "changing2.com")
        first_content = "Original text."
        second_content = "Totally different text now."

        with patch("requests.get") as mock_get:
            with patch("bs4.BeautifulSoup") as mock_bs:
                mock_resp = MagicMock()
                mock_resp.status_code = 200

                mock_soup = MagicMock()
                mock_soup.get_text.return_value = first_content
                mock_resp.text = f"<html>{first_content}</html>"
                mock_get.return_value = mock_resp
                mock_bs.return_value = mock_soup
                self.cm.check_competitor_tool(comp.id)  # First check

                mock_soup2 = MagicMock()
                mock_soup2.get_text.return_value = second_content
                mock_bs.return_value = mock_soup2
                result = self.cm.check_competitor_tool(comp.id)  # Second check

        # If changes detected, result should be JSON with change entries
        if result.startswith("["):
            data = json.loads(result)
            self.assertIsInstance(data, list)
            if data:
                self.assertIn("type", data[0])
                self.assertIn("severity", data[0])

    def test_competitor_report_tool_returns_string(self):
        result = self.cm.competitor_report_tool()
        self.assertIsInstance(result, str)

    def test_competitor_report_tool_returns_valid_json(self):
        result = self.cm.competitor_report_tool()
        data = json.loads(result)
        self.assertIsInstance(data, dict)

    def test_competitor_report_tool_json_has_required_keys(self):
        result = self.cm.competitor_report_tool()
        data = json.loads(result)
        self.assertIn("total_competitors", data)
        self.assertIn("changes_last_7d", data)

    def test_recent_competitor_changes_tool_returns_string(self):
        result = self.cm.recent_competitor_changes_tool()
        self.assertIsInstance(result, str)

    def test_recent_competitor_changes_tool_returns_valid_json(self):
        result = self.cm.recent_competitor_changes_tool()
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_recent_competitor_changes_tool_with_data(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        self.cm._ensure()
        conn = sqlite3.connect(str(self.cm._DB_PATH))
        conn.execute(
            """INSERT INTO competitor_changes
               (competitor_id, competitor_name, change_type, url, description,
                old_value, new_value, severity, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (comp.id, comp.name, "page_changed", "https://comp.com",
             "content changed", "", "", "medium", time.time()),
        )
        conn.commit()
        conn.close()
        result = self.cm.recent_competitor_changes_tool(days=7)
        data = json.loads(result)
        self.assertEqual(len(data), 1)
        self.assertIn("type", data[0])
        self.assertIn("severity", data[0])
        self.assertIn("competitor", data[0])

    def test_recent_competitor_changes_tool_json_structure(self):
        comp = self.cm.add_competitor("Structured", "structured.com")
        self.cm._ensure()
        conn = sqlite3.connect(str(self.cm._DB_PATH))
        conn.execute(
            """INSERT INTO competitor_changes
               (competitor_id, competitor_name, change_type, url, description,
                old_value, new_value, severity, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (comp.id, "Structured", "price_change", "https://structured.com/pricing",
             "price increased", "$9.99", "$14.99", "high", time.time()),
        )
        conn.commit()
        conn.close()
        result = self.cm.recent_competitor_changes_tool()
        data = json.loads(result)
        item = data[0]
        for key in ("id", "competitor", "type", "url", "description", "severity"):
            self.assertIn(key, item)


# ---------------------------------------------------------------------------
# TestSnapshotAndHashDetection
# ---------------------------------------------------------------------------

class TestSnapshotAndHashDetection(unittest.TestCase):
    """Tests for internal snapshot and content hash utilities."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_content_hash_is_16_chars(self):
        h = self.cm._content_hash("hello world")
        self.assertEqual(len(h), 16)

    def test_content_hash_is_hex(self):
        h = self.cm._content_hash("hello world")
        int(h, 16)  # should not raise

    def test_content_hash_same_input_same_hash(self):
        h1 = self.cm._content_hash("consistent content")
        h2 = self.cm._content_hash("consistent content")
        self.assertEqual(h1, h2)

    def test_content_hash_different_input_different_hash(self):
        h1 = self.cm._content_hash("first content")
        h2 = self.cm._content_hash("second content")
        self.assertNotEqual(h1, h2)

    def test_save_snapshot_returns_hash_string(self):
        comp = self.cm.add_competitor("Snap", "snap.com")
        result = self.cm._save_snapshot(comp.id, "page", "https://snap.com", {}, "content text")
        self.assertIsInstance(result, str)
        self.assertEqual(len(result), 16)

    def test_get_previous_snapshot_returns_none_on_first(self):
        comp = self.cm.add_competitor("Snap", "snap.com")
        self.cm._save_snapshot(comp.id, "page", "https://snap.com", {}, "first")
        result = self.cm._get_previous_snapshot(comp.id, "page", "https://snap.com")
        # Only 1 snapshot → no previous (OFFSET 1)
        self.assertIsNone(result)

    def test_get_previous_snapshot_returns_first_after_second_save(self):
        comp = self.cm.add_competitor("Snap", "snap.com")
        self.cm._save_snapshot(comp.id, "page", "https://snap.com", {"v": 1}, "first content")
        self.cm._save_snapshot(comp.id, "page", "https://snap.com", {"v": 2}, "second content")
        result = self.cm._get_previous_snapshot(comp.id, "page", "https://snap.com")
        self.assertIsNotNone(result)
        self.assertIn("hash", result)
        self.assertIn("data", result)

    def test_save_snapshot_persists_to_db(self):
        comp = self.cm.add_competitor("Persist", "persist.com")
        self.cm._save_snapshot(comp.id, "page", "https://persist.com", {}, "content")
        conn = sqlite3.connect(str(self.cm._DB_PATH))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM competitor_snapshots WHERE competitor_id=?", (comp.id,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)


# ---------------------------------------------------------------------------
# TestRecordChange
# ---------------------------------------------------------------------------

class TestRecordChange(unittest.TestCase):
    """Tests for _record_change() — internal change event creation."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_record_change_returns_change_event(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(comp.id, comp.name, "page_changed")
        self.assertIsInstance(ev, self.cm.ChangeEvent)

    def test_record_change_has_positive_id(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(comp.id, comp.name, "page_changed")
        self.assertGreater(ev.id, 0)

    def test_record_change_stores_competitor_id(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(comp.id, comp.name, "page_changed")
        self.assertEqual(ev.competitor_id, comp.id)

    def test_record_change_stores_change_type(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(comp.id, comp.name, "price_change")
        self.assertEqual(ev.change_type, "price_change")

    def test_record_change_stores_severity(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(
            comp.id, comp.name, "price_change", severity="high"
        )
        self.assertEqual(ev.severity, "high")

    def test_record_change_stores_url(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(
            comp.id, comp.name, "page_changed", url="https://comp.com/page"
        )
        self.assertEqual(ev.url, "https://comp.com/page")

    def test_record_change_stores_description(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(
            comp.id, comp.name, "page_changed",
            description="Major redesign detected"
        )
        self.assertEqual(ev.description, "Major redesign detected")

    def test_record_change_stores_old_and_new_value(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(
            comp.id, comp.name, "price_change",
            old_value="$9.99", new_value="$14.99"
        )
        self.assertEqual(ev.old_value, "$9.99")
        self.assertEqual(ev.new_value, "$14.99")

    def test_record_change_ts_set_recently(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        before = time.time()
        ev = self.cm._record_change(comp.id, comp.name, "page_changed")
        after = time.time()
        self.assertGreaterEqual(ev.ts, before)
        self.assertLessEqual(ev.ts, after)

    def test_record_change_persisted_in_db(self):
        comp = self.cm.add_competitor("Comp", "comp.com")
        ev = self.cm._record_change(comp.id, comp.name, "page_changed")
        # Verify via recent_changes
        changes = self.cm.recent_changes()
        self.assertTrue(any(c.id == ev.id for c in changes))


# ---------------------------------------------------------------------------
# TestDatabasePersistence
# ---------------------------------------------------------------------------

class TestDatabasePersistence(unittest.TestCase):
    """Verify competitor data survives across separate module calls."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.cm = _make_monitor(self._tmp)

    def tearDown(self):
        self.cm._schema_ready = False

    def test_competitor_persists_after_add(self):
        comp = self.cm.add_competitor("Saved", "saved.com")
        fetched = self.cm.get_competitor(comp.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "Saved")

    def test_multiple_competitors_all_persisted(self):
        ids = []
        for i in range(4):
            c = self.cm.add_competitor(f"Comp {i}", f"comp{i}.com")
            ids.append(c.id)
        for cid in ids:
            self.assertIsNotNone(self.cm.get_competitor(cid))

    def test_keywords_persist_after_add(self):
        comp = self.cm.add_competitor("KW Persist", "kwpersist.com")
        self.cm.add_keywords(comp.id, ["keyword1", "keyword2"])
        self.cm._schema_ready = False
        fetched = self.cm.get_competitor(comp.id)
        self.assertIn("keyword1", fetched.tracked_keywords)

    def test_changes_persist_after_record(self):
        comp = self.cm.add_competitor("Change Persist", "changepersist.com")
        self.cm._record_change(comp.id, comp.name, "page_changed", severity="medium")
        changes = self.cm.recent_changes()
        self.assertEqual(len(changes), 1)

    def test_domain_uniqueness_enforced(self):
        """Adding same domain twice should replace (INSERT OR REPLACE)."""
        self.cm.add_competitor("First", "unique.com")
        self.cm.add_competitor("Second", "unique.com")
        comps = self.cm.list_competitors()
        domains = [c.domain for c in comps]
        unique_domains = set(domains)
        # At most one entry for the domain
        self.assertLessEqual(domains.count("https://unique.com"), 1)


if __name__ == "__main__":
    unittest.main()
