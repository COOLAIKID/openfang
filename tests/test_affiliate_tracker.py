"""Tests for autoearn/core/affiliate_tracker.py."""
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
# Module-level isolation bootstrap
# ---------------------------------------------------------------------------
# affiliate_tracker uses cfg("affiliate.db_path") not get_db_path, so we
# must patch the cfg function to redirect to our test database.

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_aff_{uuid.uuid4().hex[:8]}.db")


def _fake_cfg(key, fallback=None):
    if "db_path" in key:
        return _DB_PATH
    return fallback


with patch("autoearn.core.affiliate_tracker.cfg", _fake_cfg):
    import autoearn.core.affiliate_tracker as aff

# Permanently redirect cfg in the module after import
aff.cfg = _fake_cfg


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_schema():
    """Drop all tables and reset the global DB connection before every test."""
    # Close and discard the existing connection so _db() will reconnect.
    if aff._conn is not None:
        try:
            aff._conn.close()
        except Exception:
            pass
        aff._conn = None

    # Wipe the database file contents.
    conn = sqlite3.connect(_DB_PATH)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    for (t,) in tables:
        if not t.startswith("sqlite_"):
            conn.execute(f"DROP TABLE IF EXISTS [{t}]")
    conn.commit()
    conn.close()

    yield

    # Teardown — close module connection after each test.
    if aff._conn is not None:
        try:
            aff._conn.close()
        except Exception:
            pass
        aff._conn = None


# ---- helper fixtures -------------------------------------------------------

@pytest.fixture()
def amazon_id():
    """Add and return the rowid of a preconfigured Amazon Associates program."""
    return aff.add_program(
        name="Amazon",
        network="Amazon Associates",
        commission_type="percentage",
        commission_rate=4.0,
        cookie_days=24,
        payout_threshold=10.0,
        affiliate_id="myamzid",
        base_url="https://www.amazon.com",
    )


@pytest.fixture()
def cb_id():
    """Add and return the rowid of a ClickBank program."""
    return aff.add_program(
        name="ClickBank",
        network="ClickBank",
        commission_type="percentage",
        commission_rate=50.0,
        cookie_days=60,
        payout_threshold=50.0,
        affiliate_id="cbid99",
    )


@pytest.fixture()
def flat_id():
    """Flat-rate program: $5 per conversion regardless of sale amount."""
    return aff.add_program(
        name="FlatProgram",
        commission_type="flat",
        commission_rate=5.0,
        payout_threshold=25.0,
        affiliate_id="flatid",
    )


@pytest.fixture()
def cpa_id():
    """CPA program: fixed $12 payout per conversion."""
    return aff.add_program(
        name="CPAProgram",
        commission_type="cpa",
        commission_rate=12.0,
        payout_threshold=100.0,
        affiliate_id="cpaid",
    )


@pytest.fixture()
def amazon_link(amazon_id):
    """Create a tracked link under the Amazon program."""
    return aff.create_link(
        "Amazon",
        "https://www.amazon.com/dp/B000001",
        slug="best-gadget",
        content_id="blog-post-1",
        campaign="summer-sale",
    )


def _db_conn() -> sqlite3.Connection:
    """Return a direct, row-factory-enabled connection to the test DB."""
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# TestProgramManagement
# ---------------------------------------------------------------------------

class TestProgramManagement:
    """add_program, get_program, get_program_by_name, list_programs,
    deactivate_program, and POPULAR_NETWORKS presets."""

    def test_add_program_returns_positive_id(self):
        pid = aff.add_program("TestProg", commission_rate=10.0)
        assert isinstance(pid, int)
        assert pid > 0

    def test_get_program_by_id(self):
        pid = aff.add_program("MyProg", network="TestNet", commission_rate=5.0)
        prog = aff.get_program(pid)
        assert prog is not None
        assert prog["name"] == "MyProg"
        assert prog["network"] == "TestNet"
        assert prog["commission_rate"] == pytest.approx(5.0)

    def test_get_program_unknown_returns_none(self):
        result = aff.get_program(999999)
        assert result is None

    def test_get_program_by_name(self):
        aff.add_program("NamedProg", affiliate_id="x123")
        prog = aff.get_program_by_name("NamedProg")
        assert prog is not None
        assert prog["affiliate_id"] == "x123"

    def test_get_program_by_name_unknown_returns_none(self):
        assert aff.get_program_by_name("DoesNotExist") is None

    def test_list_programs_active_only(self, amazon_id, cb_id):
        programs = aff.list_programs(active_only=True)
        names = [p["name"] for p in programs]
        assert "Amazon" in names
        assert "ClickBank" in names

    def test_list_programs_includes_inactive_when_requested(self, amazon_id):
        aff.deactivate_program(amazon_id)
        all_progs = aff.list_programs(active_only=False)
        names = [p["name"] for p in all_progs]
        assert "Amazon" in names

    def test_list_programs_excludes_inactive_by_default(self, amazon_id):
        aff.deactivate_program(amazon_id)
        active = aff.list_programs(active_only=True)
        names = [p["name"] for p in active]
        assert "Amazon" not in names

    def test_deactivate_program_returns_true(self, amazon_id):
        result = aff.deactivate_program(amazon_id)
        assert result is True

    def test_deactivate_program_sets_active_false(self, amazon_id):
        aff.deactivate_program(amazon_id)
        prog = aff.get_program(amazon_id)
        assert prog is not None
        assert prog["active"] == 0

    def test_add_program_defaults(self):
        pid = aff.add_program("Defaults")
        prog = aff.get_program(pid)
        assert prog["commission_type"] == "percentage"
        assert prog["cookie_days"] == 30
        assert prog["payout_threshold"] == pytest.approx(50.0)
        assert prog["currency"] == "USD"
        assert prog["tracking_param"] == "ref"
        assert prog["active"] == 1

    def test_add_program_upsert_on_duplicate_name(self, amazon_id):
        # Adding the same name should upsert, not raise.
        aff.add_program("Amazon", commission_rate=8.0)
        prog = aff.get_program_by_name("Amazon")
        assert prog["commission_rate"] == pytest.approx(8.0)

    def test_add_program_with_preset_amazon_sets_network(self):
        # Presets apply network name; cookie_days default (30) is truthy so
        # the preset value only applies if caller passes 0 explicitly.
        pid = aff.add_program("AmazonPreset", preset="amazon", affiliate_id="apid")
        prog = aff.get_program(pid)
        assert prog["network"] == "Amazon Associates"

    def test_add_program_with_preset_amazon_sets_commission_rate(self):
        pid = aff.add_program("AmazonRate", preset="amazon", affiliate_id="arid",
                              commission_rate=0.0)
        prog = aff.get_program(pid)
        # When commission_rate=0.0 (falsy), preset fills in 4.0
        assert prog["commission_rate"] == pytest.approx(4.0)

    def test_add_program_with_preset_clickbank_sets_commission_rate(self):
        pid = aff.add_program("CBRate", preset="clickbank", affiliate_id="crid",
                              commission_rate=0.0)
        prog = aff.get_program(pid)
        assert prog["commission_rate"] == pytest.approx(50.0)

    def test_add_program_with_preset_partnerstack_sets_commission_rate(self):
        pid = aff.add_program("PSRate", preset="partnerstack", affiliate_id="psid",
                              commission_rate=0.0)
        prog = aff.get_program(pid)
        assert prog["commission_rate"] == pytest.approx(20.0)

    def test_list_programs_ordered_by_name(self):
        aff.add_program("Zebra")
        aff.add_program("Alpha")
        aff.add_program("Middle")
        progs = aff.list_programs(active_only=False)
        names = [p["name"] for p in progs]
        assert names == sorted(names)

    def test_add_program_stores_notes(self):
        pid = aff.add_program("NoteProg", notes="Important affiliate")
        prog = aff.get_program(pid)
        assert prog["notes"] == "Important affiliate"

    def test_add_program_stores_currency(self):
        pid = aff.add_program("EuroProg", currency="EUR")
        prog = aff.get_program(pid)
        assert prog["currency"] == "EUR"


# ---------------------------------------------------------------------------
# TestLinkCreation
# ---------------------------------------------------------------------------

class TestLinkCreation:
    """create_link and the returned dict structure."""

    def test_create_link_ok(self, amazon_id, amazon_link):
        assert amazon_link.get("ok") is True

    def test_create_link_returns_link_id(self, amazon_id, amazon_link):
        assert isinstance(amazon_link["link_id"], int)
        assert amazon_link["link_id"] > 0

    def test_create_link_slug_used_when_no_collision(self, amazon_id):
        result = aff.create_link("Amazon", "https://www.amazon.com/dp/B001",
                                 slug="unique-slug")
        assert result["slug"] == "unique-slug"

    def test_create_link_returns_short_code_of_length_8(self, amazon_id, amazon_link):
        assert len(amazon_link["short_code"]) == 8

    def test_create_link_tracking_url_has_affiliate_id(self, amazon_id, amazon_link):
        assert "myamzid" in amazon_link["tracking_url"]

    def test_create_link_tracking_url_has_tracking_param(self, amazon_id, amazon_link):
        assert "ref=" in amazon_link["tracking_url"]

    def test_create_link_unknown_program_returns_error(self):
        result = aff.create_link("NoSuchProgram", "https://example.com")
        assert "error" in result
        assert "not found" in result["error"]

    def test_create_link_auto_slug_when_not_provided(self, amazon_id):
        result = aff.create_link("Amazon", "https://www.amazon.com/dp/B999")
        assert result.get("ok") is True
        assert result["slug"]  # non-empty

    def test_create_link_duplicate_slug_gets_suffix(self, amazon_id):
        aff.create_link("Amazon", "https://www.amazon.com/dp/A1", slug="dup-slug")
        result2 = aff.create_link("Amazon", "https://www.amazon.com/dp/A2",
                                  slug="dup-slug")
        assert result2.get("ok") is True
        assert result2["slug"] != "dup-slug"

    def test_create_link_with_query_string_appends_correctly(self, amazon_id):
        result = aff.create_link("Amazon", "https://www.amazon.com/dp/B1?color=red")
        assert "&ref=" in result["tracking_url"]

    def test_create_link_stores_content_id(self, amazon_id, amazon_link):
        link = aff.get_link(amazon_link["link_id"])
        assert link["content_id"] == "blog-post-1"

    def test_create_link_stores_campaign(self, amazon_id, amazon_link):
        link = aff.get_link(amazon_link["link_id"])
        assert link["campaign"] == "summer-sale"

    def test_create_link_returns_program_name(self, amazon_id, amazon_link):
        assert amazon_link["program"] == "Amazon"


# ---------------------------------------------------------------------------
# TestLinkRetrieval
# ---------------------------------------------------------------------------

class TestLinkRetrieval:
    """get_link, get_link_by_slug, list_links."""

    def test_get_link_by_id(self, amazon_id, amazon_link):
        link = aff.get_link(amazon_link["link_id"])
        assert link is not None
        assert link["id"] == amazon_link["link_id"]

    def test_get_link_contains_expected_keys(self, amazon_id, amazon_link):
        link = aff.get_link(amazon_link["link_id"])
        expected_keys = [
            "id", "program_id", "slug", "destination_url", "short_code",
            "content_id", "campaign", "active", "clicks", "unique_clicks",
            "conversions", "revenue", "commissions", "conversion_rate_pct",
            "epc", "avg_order_value",
        ]
        for key in expected_keys:
            assert key in link, f"missing key: {key}"

    def test_get_link_unknown_returns_none(self):
        assert aff.get_link(999999) is None

    def test_get_link_by_slug_returns_row(self, amazon_id, amazon_link):
        slug = amazon_link["slug"]
        link = aff.get_link_by_slug(slug)
        assert link is not None
        assert link["slug"] == slug

    def test_get_link_by_slug_unknown_returns_none(self):
        assert aff.get_link_by_slug("no-such-slug") is None

    def test_list_links_returns_all_for_program(self, amazon_id):
        aff.create_link("Amazon", "https://www.amazon.com/dp/B001", slug="link-a")
        aff.create_link("Amazon", "https://www.amazon.com/dp/B002", slug="link-b")
        links = aff.list_links(program_name="Amazon")
        assert len(links) >= 2

    def test_list_links_filters_by_campaign(self, amazon_id):
        aff.create_link("Amazon", "https://www.amazon.com/dp/C001", slug="camp-a",
                        campaign="promo-q4")
        aff.create_link("Amazon", "https://www.amazon.com/dp/C002", slug="camp-b",
                        campaign="promo-q1")
        links = aff.list_links(campaign="promo-q4")
        assert all(l["campaign"] == "promo-q4" for l in links)
        assert len(links) >= 1

    def test_list_links_filters_by_content_id(self, amazon_id):
        aff.create_link("Amazon", "https://www.amazon.com/dp/D1", slug="cont-a",
                        content_id="article-42")
        aff.create_link("Amazon", "https://www.amazon.com/dp/D2", slug="cont-b",
                        content_id="article-99")
        links = aff.list_links(content_id="article-42")
        assert all(l["content_id"] == "article-42" for l in links)
        assert len(links) >= 1

    def test_list_links_respects_limit(self, amazon_id):
        for i in range(10):
            aff.create_link("Amazon", f"https://www.amazon.com/dp/E{i}",
                            slug=f"limit-link-{i}")
        links = aff.list_links(limit=3)
        assert len(links) <= 3

    def test_list_links_includes_program_name(self, amazon_id, amazon_link):
        links = aff.list_links(program_name="Amazon")
        assert all(l["program_name"] == "Amazon" for l in links)

    def test_list_links_includes_commission_rate(self, amazon_id, amazon_link):
        links = aff.list_links(program_name="Amazon")
        for link in links:
            assert "commission_rate" in link
            assert link["commission_rate"] == pytest.approx(4.0)

    def test_list_links_active_only_by_default(self, amazon_id, amazon_link):
        # All created links are active=1 by default, so active_only=True
        # should return them.
        links = aff.list_links(active_only=True)
        assert len(links) >= 1


# ---------------------------------------------------------------------------
# TestClickTracking
# ---------------------------------------------------------------------------

class TestClickTracking:
    """record_click — counts, uniqueness, stored metadata."""

    def test_record_click_returns_click_id(self, amazon_id, amazon_link):
        result = aff.record_click(amazon_link["link_id"])
        assert isinstance(result["click_id"], int)
        assert result["click_id"] > 0

    def test_record_click_returns_link_id(self, amazon_id, amazon_link):
        result = aff.record_click(amazon_link["link_id"])
        assert result["link_id"] == amazon_link["link_id"]

    def test_first_click_is_unique(self, amazon_id, amazon_link):
        result = aff.record_click(amazon_link["link_id"], ip_hash="abc123")
        assert result["unique"] is True

    def test_same_ip_within_24h_not_unique(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"], ip_hash="repeatip")
        result2 = aff.record_click(amazon_link["link_id"], ip_hash="repeatip")
        assert result2["unique"] is False

    def test_different_ips_both_unique(self, amazon_id, amazon_link):
        r1 = aff.record_click(amazon_link["link_id"], ip_hash="ip-one")
        r2 = aff.record_click(amazon_link["link_id"], ip_hash="ip-two")
        assert r1["unique"] is True
        assert r2["unique"] is True

    def test_click_without_ip_is_unique(self, amazon_id, amazon_link):
        result = aff.record_click(amazon_link["link_id"])
        assert result["unique"] is True

    def test_clicks_count_increments(self, amazon_id, amazon_link):
        for _ in range(3):
            aff.record_click(amazon_link["link_id"])
        link = aff.get_link(amazon_link["link_id"])
        assert link["clicks"] == 3

    def test_unique_clicks_count_increments_only_for_unique(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"], ip_hash="u1")
        aff.record_click(amazon_link["link_id"], ip_hash="u1")  # duplicate
        aff.record_click(amazon_link["link_id"], ip_hash="u2")
        link = aff.get_link(amazon_link["link_id"])
        assert link["unique_clicks"] == 2
        assert link["clicks"] == 3

    def test_record_click_stores_referrer(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"], referrer="https://google.com")
        conn = _db_conn()
        row = conn.execute(
            "SELECT referrer FROM affiliate_clicks WHERE link_id=?",
            (amazon_link["link_id"],)
        ).fetchone()
        conn.close()
        assert row["referrer"] == "https://google.com"

    def test_record_click_stores_country(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"], country="DE")
        conn = _db_conn()
        row = conn.execute(
            "SELECT country FROM affiliate_clicks WHERE link_id=?",
            (amazon_link["link_id"],)
        ).fetchone()
        conn.close()
        assert row["country"] == "DE"

    def test_record_click_stores_user_agent(self, amazon_id, amazon_link):
        ua = "Mozilla/5.0 (compatible)"
        aff.record_click(amazon_link["link_id"], user_agent=ua)
        conn = _db_conn()
        row = conn.execute(
            "SELECT user_agent FROM affiliate_clicks WHERE link_id=?",
            (amazon_link["link_id"],)
        ).fetchone()
        conn.close()
        assert row["user_agent"] == ua

    def test_record_click_stores_ip_hash(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"], ip_hash="hashed-ip")
        conn = _db_conn()
        row = conn.execute(
            "SELECT ip_hash FROM affiliate_clicks WHERE link_id=?",
            (amazon_link["link_id"],)
        ).fetchone()
        conn.close()
        assert row["ip_hash"] == "hashed-ip"


# ---------------------------------------------------------------------------
# TestConversionTracking
# ---------------------------------------------------------------------------

class TestConversionTracking:
    """record_conversion, approve_conversion, reject_conversion."""

    def test_record_conversion_returns_conversion_id(self, amazon_id, amazon_link):
        result = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        assert isinstance(result["conversion_id"], int)
        assert result["conversion_id"] > 0

    def test_record_conversion_calculates_percentage_commission(self, amazon_id, amazon_link):
        # Amazon is 4% — 100.0 * 0.04 = 4.0
        result = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        assert result["commission"] == pytest.approx(4.0)

    def test_record_conversion_calculates_flat_commission(self, flat_id):
        link = aff.create_link("FlatProgram", "https://flat.example.com/p1")
        result = aff.record_conversion(link["link_id"], sale_amount=200.0)
        # Flat rate is 5.0 regardless of sale amount.
        assert result["commission"] == pytest.approx(5.0)

    def test_record_conversion_calculates_cpa_commission(self, cpa_id):
        link = aff.create_link("CPAProgram", "https://cpa.example.com/p1")
        result = aff.record_conversion(link["link_id"], sale_amount=0.0)
        # CPA = fixed rate = 12.0
        assert result["commission"] == pytest.approx(12.0)

    def test_record_conversion_returns_sale_amount(self, amazon_id, amazon_link):
        result = aff.record_conversion(amazon_link["link_id"], sale_amount=49.99)
        assert result["sale_amount"] == pytest.approx(49.99, abs=0.001)

    def test_record_conversion_returns_commission_rate(self, amazon_id, amazon_link):
        result = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        assert result["commission_rate"] == pytest.approx(4.0)

    def test_record_conversion_default_status_pending(self, amazon_id, amazon_link):
        result = aff.record_conversion(amazon_link["link_id"], sale_amount=50.0)
        assert result["status"] == "pending"

    def test_record_conversion_with_explicit_status(self, amazon_id, amazon_link):
        result = aff.record_conversion(
            amazon_link["link_id"], sale_amount=50.0, status="approved"
        )
        assert result["status"] == "approved"

    def test_record_conversion_with_order_id(self, amazon_id, amazon_link):
        result = aff.record_conversion(
            amazon_link["link_id"], sale_amount=75.0, order_id="ORD-9999"
        )
        conn = _db_conn()
        row = conn.execute(
            "SELECT order_id FROM affiliate_conversions WHERE id=?",
            (result["conversion_id"],)
        ).fetchone()
        conn.close()
        assert row["order_id"] == "ORD-9999"

    def test_record_conversion_with_meta(self, amazon_id, amazon_link):
        meta = {"product": "widget", "color": "blue"}
        result = aff.record_conversion(
            amazon_link["link_id"], sale_amount=30.0, meta=meta
        )
        conn = _db_conn()
        row = conn.execute(
            "SELECT meta FROM affiliate_conversions WHERE id=?",
            (result["conversion_id"],)
        ).fetchone()
        conn.close()
        stored = json.loads(row["meta"])
        assert stored["product"] == "widget"

    def test_record_conversion_updates_link_conversions_count(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        link = aff.get_link(amazon_link["link_id"])
        assert link["conversions"] == 1

    def test_record_conversion_updates_link_revenue(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        link = aff.get_link(amazon_link["link_id"])
        assert link["revenue"] == pytest.approx(100.0)

    def test_record_conversion_updates_link_commissions(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        link = aff.get_link(amazon_link["link_id"])
        assert link["commissions"] == pytest.approx(4.0)

    def test_record_conversion_unknown_link_returns_error(self):
        result = aff.record_conversion(99999, sale_amount=100.0)
        assert "error" in result

    def test_approve_conversion_returns_true(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.approve_conversion(conv["conversion_id"])
        assert result is True

    def test_approve_conversion_sets_approved_status(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.approve_conversion(conv["conversion_id"])
        conn = _db_conn()
        row = conn.execute(
            "SELECT status FROM affiliate_conversions WHERE id=?",
            (conv["conversion_id"],)
        ).fetchone()
        conn.close()
        assert row["status"] == "approved"

    def test_approve_already_approved_is_noop(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=50.0,
                                     status="approved")
        result = aff.approve_conversion(conv["conversion_id"])
        assert result is True

    def test_reject_conversion_returns_true(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.reject_conversion(conv["conversion_id"])
        assert result is True

    def test_reject_conversion_reverses_conversions_count(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.reject_conversion(conv["conversion_id"])
        link = aff.get_link(amazon_link["link_id"])
        assert link["conversions"] == 0

    def test_reject_conversion_reverses_revenue(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.reject_conversion(conv["conversion_id"])
        link = aff.get_link(amazon_link["link_id"])
        assert link["revenue"] == pytest.approx(0.0, abs=0.001)

    def test_reject_conversion_reverses_commissions(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.reject_conversion(conv["conversion_id"])
        link = aff.get_link(amazon_link["link_id"])
        assert link["commissions"] == pytest.approx(0.0, abs=0.001)

    def test_reject_conversion_sets_rejected_status(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.reject_conversion(conv["conversion_id"])
        conn = _db_conn()
        row = conn.execute(
            "SELECT status FROM affiliate_conversions WHERE id=?",
            (conv["conversion_id"],)
        ).fetchone()
        conn.close()
        assert row["status"] == "rejected"

    def test_reject_unknown_conversion_returns_false(self):
        result = aff.reject_conversion(99999)
        assert result is False

    def test_multiple_conversions_accumulate(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.record_conversion(amazon_link["link_id"], sale_amount=200.0)
        link = aff.get_link(amazon_link["link_id"])
        assert link["conversions"] == 2
        assert link["revenue"] == pytest.approx(300.0)
        assert link["commissions"] == pytest.approx(12.0)  # 4% of 300


# ---------------------------------------------------------------------------
# TestAffiliateLink dataclass
# ---------------------------------------------------------------------------

class TestAffiliateLinkDataclass:
    """AffiliateLink computed properties and to_dict."""

    def _make_link(self, **kwargs) -> aff.AffiliateLink:
        defaults = dict(
            program_id=1,
            slug="test-slug",
            destination_url="https://example.com",
            short_code="abcd1234",
            clicks=0,
            unique_clicks=0,
            conversions=0,
            revenue=0.0,
            commissions=0.0,
            id=1,
        )
        defaults.update(kwargs)
        return aff.AffiliateLink(**defaults)

    def test_conversion_rate_zero_when_no_unique_clicks(self):
        link = self._make_link(unique_clicks=0, conversions=0)
        assert link.conversion_rate == 0.0

    def test_conversion_rate_calculated_correctly(self):
        link = self._make_link(unique_clicks=100, conversions=5)
        assert link.conversion_rate == pytest.approx(5.0)

    def test_epc_zero_when_no_clicks(self):
        link = self._make_link(clicks=0, commissions=0.0)
        assert link.epc == 0.0

    def test_epc_calculated_correctly(self):
        link = self._make_link(clicks=200, commissions=20.0)
        assert link.epc == pytest.approx(0.1)

    def test_avg_order_value_zero_when_no_conversions(self):
        link = self._make_link(conversions=0, revenue=0.0)
        assert link.avg_order_value == 0.0

    def test_avg_order_value_calculated_correctly(self):
        link = self._make_link(conversions=4, revenue=400.0)
        assert link.avg_order_value == pytest.approx(100.0)

    def test_to_dict_contains_all_keys(self):
        link = self._make_link(clicks=10, unique_clicks=8, conversions=2,
                               revenue=200.0, commissions=8.0)
        d = link.to_dict()
        required = [
            "id", "program_id", "slug", "destination_url", "short_code",
            "content_id", "campaign", "active", "clicks", "unique_clicks",
            "conversions", "revenue", "commissions",
            "conversion_rate_pct", "epc", "avg_order_value",
        ]
        for key in required:
            assert key in d, f"missing key: {key}"

    def test_to_dict_is_json_serializable(self):
        link = self._make_link(clicks=5, commissions=2.5, revenue=50.0, conversions=1)
        json.dumps(link.to_dict())  # should not raise

    def test_to_dict_revenue_rounded_to_4dp(self):
        link = self._make_link(conversions=1, revenue=1.23456789)
        d = link.to_dict()
        # The implementation rounds to 4 decimal places.
        assert abs(d["revenue"] - round(1.23456789, 4)) < 1e-9

    def test_to_dict_commissions_rounded_to_4dp(self):
        link = self._make_link(commissions=3.14159265)
        d = link.to_dict()
        assert abs(d["commissions"] - round(3.14159265, 4)) < 1e-9

    def test_conversion_rate_pct_in_to_dict_matches_property(self):
        link = self._make_link(unique_clicks=50, conversions=5)
        d = link.to_dict()
        assert d["conversion_rate_pct"] == link.conversion_rate

    def test_epc_in_to_dict_matches_property(self):
        link = self._make_link(clicks=10, commissions=5.0)
        d = link.to_dict()
        assert d["epc"] == link.epc


# ---------------------------------------------------------------------------
# TestPayouts
# ---------------------------------------------------------------------------

class TestPayouts:
    """record_payout and pending_payout_estimate."""

    def test_pending_payout_estimate_unknown_program(self):
        result = aff.pending_payout_estimate(99999)
        assert "error" in result

    def test_pending_payout_estimate_zero_when_no_approved(self, amazon_id):
        result = aff.pending_payout_estimate(amazon_id)
        assert result["pending_commission"] == pytest.approx(0.0)
        assert result["pending_conversions"] == 0
        assert result["above_threshold"] is False

    def test_pending_payout_estimate_includes_approved_only(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=1000.0)
        aff.approve_conversion(conv["conversion_id"])
        # Also add a pending one — should NOT be counted.
        aff.record_conversion(amazon_link["link_id"], sale_amount=500.0)
        result = aff.pending_payout_estimate(amazon_id)
        # 1000 * 4% = 40 commission approved
        assert result["pending_conversions"] == 1
        assert result["pending_commission"] == pytest.approx(40.0)

    def test_pending_payout_estimate_above_threshold(self, amazon_id, amazon_link):
        # threshold is 10.0 for amazon_id fixture
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=500.0)
        aff.approve_conversion(conv["conversion_id"])
        result = aff.pending_payout_estimate(amazon_id)
        assert result["above_threshold"] is True

    def test_pending_payout_estimate_below_threshold(self, amazon_id, amazon_link):
        # 1.0 * 4% = 0.04 commission, threshold = 10.0 → below
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=1.0)
        aff.approve_conversion(conv["conversion_id"])
        result = aff.pending_payout_estimate(amazon_id)
        assert result["above_threshold"] is False

    def test_pending_payout_estimate_contains_program_name(self, amazon_id):
        result = aff.pending_payout_estimate(amazon_id)
        assert result["program"] == "Amazon"

    def test_pending_payout_estimate_contains_currency(self, amazon_id):
        result = aff.pending_payout_estimate(amazon_id)
        assert result["currency"] == "USD"

    def test_pending_payout_estimate_contains_threshold(self, amazon_id):
        result = aff.pending_payout_estimate(amazon_id)
        assert result["payout_threshold"] == pytest.approx(10.0)

    def test_record_payout_returns_positive_id(self, amazon_id):
        payout_id = aff.record_payout(amazon_id, amount=50.0, method="paypal")
        assert isinstance(payout_id, int)
        assert payout_id > 0

    def test_record_payout_marks_approved_conversions_as_paid(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=500.0)
        aff.approve_conversion(conv["conversion_id"])
        aff.record_payout(amazon_id, amount=20.0)
        conn = _db_conn()
        row = conn.execute(
            "SELECT status FROM affiliate_conversions WHERE id=?",
            (conv["conversion_id"],)
        ).fetchone()
        conn.close()
        assert row["status"] == "paid"

    def test_record_payout_does_not_mark_pending_as_paid(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=500.0)
        # Not approved — stays pending.
        aff.record_payout(amazon_id, amount=20.0)
        conn = _db_conn()
        row = conn.execute(
            "SELECT status FROM affiliate_conversions WHERE id=?",
            (conv["conversion_id"],)
        ).fetchone()
        conn.close()
        assert row["status"] == "pending"

    def test_pending_commission_zeroed_after_payout(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=500.0)
        aff.approve_conversion(conv["conversion_id"])
        aff.record_payout(amazon_id, amount=20.0)
        result = aff.pending_payout_estimate(amazon_id)
        assert result["pending_commission"] == pytest.approx(0.0)
        assert result["pending_conversions"] == 0

    def test_record_payout_stores_conversion_ids(self, amazon_id, amazon_link):
        conv1 = aff.record_conversion(amazon_link["link_id"], sale_amount=200.0)
        conv2 = aff.record_conversion(amazon_link["link_id"], sale_amount=300.0)
        aff.approve_conversion(conv1["conversion_id"])
        aff.approve_conversion(conv2["conversion_id"])
        payout_id = aff.record_payout(amazon_id, amount=20.0)
        conn = _db_conn()
        row = conn.execute(
            "SELECT conversion_ids FROM affiliate_payouts WHERE id=?", (payout_id,)
        ).fetchone()
        conn.close()
        ids = json.loads(row["conversion_ids"])
        assert conv1["conversion_id"] in ids
        assert conv2["conversion_id"] in ids

    def test_record_payout_with_reference_stored(self, amazon_id):
        payout_id = aff.record_payout(amazon_id, amount=100.0, reference="REF-ABC")
        conn = _db_conn()
        row = conn.execute(
            "SELECT reference FROM affiliate_payouts WHERE id=?", (payout_id,)
        ).fetchone()
        conn.close()
        assert row["reference"] == "REF-ABC"


# ---------------------------------------------------------------------------
# TestReporting
# ---------------------------------------------------------------------------

class TestReporting:
    """program_performance, top_links, affiliate_summary, content_performance."""

    def test_program_performance_empty_returns_empty_list(self):
        # No programs at all.
        result = aff.program_performance(days=30)
        assert isinstance(result, list)
        assert result == []

    def test_program_performance_returns_list_of_dicts(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.program_performance(days=30)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_program_performance_contains_expected_keys(self, amazon_id, amazon_link):
        result = aff.program_performance(days=30)
        row = result[0]
        for key in ["program", "network", "links", "revenue", "commissions",
                    "conversions", "clicks", "epc"]:
            assert key in row, f"missing key: {key}"

    def test_program_performance_aggregates_commissions(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        aff.record_conversion(amazon_link["link_id"], sale_amount=200.0)
        result = aff.program_performance(days=30)
        amazon_row = next(r for r in result if r["program"] == "Amazon")
        assert amazon_row["commissions"] == pytest.approx(12.0)  # 4% of 300

    def test_program_performance_aggregates_revenue(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=50.0)
        aff.record_conversion(amazon_link["link_id"], sale_amount=150.0)
        result = aff.program_performance(days=30)
        amazon_row = next(r for r in result if r["program"] == "Amazon")
        assert amazon_row["revenue"] == pytest.approx(200.0)

    def test_program_performance_epc_calculated(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"])
        aff.record_click(amazon_link["link_id"])
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.program_performance(days=30)
        amazon_row = next(r for r in result if r["program"] == "Amazon")
        # 2 clicks, commission = 4.0 -> epc = 2.0
        assert amazon_row["epc"] == pytest.approx(2.0)

    def test_program_performance_epc_zero_when_no_clicks(self, amazon_id):
        result = aff.program_performance(days=30)
        amazon_row = next(r for r in result if r["program"] == "Amazon")
        assert amazon_row["epc"] == pytest.approx(0.0)

    def test_top_links_empty_returns_empty_list(self):
        result = aff.top_links(days=30, limit=20)
        assert isinstance(result, list)
        assert result == []

    def test_top_links_returns_list_of_dicts(self, amazon_id, amazon_link):
        result = aff.top_links(days=30, limit=20)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_top_links_contains_expected_keys(self, amazon_id, amazon_link):
        result = aff.top_links(days=30, limit=20)
        row = result[0]
        for key in ["id", "slug", "content_id", "campaign", "program",
                    "clicks", "unique_clicks", "conversions",
                    "period_revenue", "period_commissions"]:
            assert key in row, f"missing key: {key}"

    def test_top_links_limit_respected(self, amazon_id):
        for i in range(15):
            aff.create_link("Amazon", f"https://www.amazon.com/dp/R{i}",
                            slug=f"top-{i}")
        result = aff.top_links(days=30, limit=5)
        assert len(result) <= 5

    def test_top_links_ordered_by_commissions_desc(self, amazon_id):
        r1 = aff.create_link("Amazon", "https://www.amazon.com/dp/X1", slug="low-rev")
        r2 = aff.create_link("Amazon", "https://www.amazon.com/dp/X2", slug="high-rev")
        aff.record_conversion(r1["link_id"], sale_amount=10.0)
        aff.record_conversion(r2["link_id"], sale_amount=1000.0)
        result = aff.top_links(days=30, limit=10)
        commissions = [r["period_commissions"] for r in result]
        assert commissions == sorted(commissions, reverse=True)

    def test_content_performance_empty_returns_empty_list(self):
        result = aff.content_performance(days=30)
        assert isinstance(result, list)
        assert result == []

    def test_content_performance_groups_by_content_id(self, amazon_id):
        r1 = aff.create_link("Amazon", "https://www.amazon.com/dp/Y1", slug="cp-a",
                              content_id="article-1")
        r2 = aff.create_link("Amazon", "https://www.amazon.com/dp/Y2", slug="cp-b",
                              content_id="article-2")
        aff.record_conversion(r1["link_id"], sale_amount=100.0)
        aff.record_conversion(r2["link_id"], sale_amount=200.0)
        result = aff.content_performance(days=30)
        content_ids = [r["content_id"] for r in result]
        assert "article-1" in content_ids
        assert "article-2" in content_ids

    def test_content_performance_excludes_blank_content_id(self, amazon_id):
        aff.create_link("Amazon", "https://www.amazon.com/dp/Z1", slug="no-cid")
        result = aff.content_performance(days=30)
        content_ids = [row["content_id"] for row in result]
        assert "" not in content_ids

    def test_content_performance_contains_expected_keys(self, amazon_id):
        r = aff.create_link("Amazon", "https://www.amazon.com/dp/K1", slug="kp-a",
                            content_id="article-x")
        aff.record_conversion(r["link_id"], sale_amount=50.0)
        result = aff.content_performance(days=30)
        row = next(r for r in result if r["content_id"] == "article-x")
        for key in ["content_id", "links", "clicks", "conversions", "commissions"]:
            assert key in row, f"missing key: {key}"


# ---------------------------------------------------------------------------
# TestAffiliateSummary
# ---------------------------------------------------------------------------

class TestAffiliateSummary:
    """Overall affiliate_summary report."""

    def test_summary_returns_dict(self):
        result = aff.affiliate_summary()
        assert isinstance(result, dict)

    def test_summary_contains_expected_keys(self):
        result = aff.affiliate_summary()
        expected = [
            "active_programs", "active_links", "total_clicks",
            "total_conversions", "total_revenue", "total_commissions_earned",
            "total_commissions_paid", "total_commissions_pending",
            "overall_conversion_rate_pct", "overall_epc",
        ]
        for key in expected:
            assert key in result, f"missing key: {key}"

    def test_summary_zero_state(self):
        result = aff.affiliate_summary()
        assert result["active_programs"] == 0
        assert result["active_links"] == 0
        assert result["total_clicks"] == 0
        assert result["total_conversions"] == 0
        assert result["total_revenue"] == pytest.approx(0.0)
        assert result["overall_conversion_rate_pct"] == pytest.approx(0.0)
        assert result["overall_epc"] == pytest.approx(0.0)

    def test_summary_active_programs_count(self, amazon_id, cb_id):
        result = aff.affiliate_summary()
        assert result["active_programs"] == 2

    def test_summary_active_links_count(self, amazon_id, amazon_link):
        result = aff.affiliate_summary()
        assert result["active_links"] >= 1

    def test_summary_total_clicks(self, amazon_id, amazon_link):
        aff.record_click(amazon_link["link_id"])
        aff.record_click(amazon_link["link_id"])
        result = aff.affiliate_summary()
        assert result["total_clicks"] == 2

    def test_summary_total_conversions_excludes_rejected(self, amazon_id, amazon_link):
        c1 = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        c2 = aff.record_conversion(amazon_link["link_id"], sale_amount=50.0)
        aff.reject_conversion(c2["conversion_id"])
        result = aff.affiliate_summary()
        assert result["total_conversions"] == 1

    def test_summary_total_revenue_excludes_rejected(self, amazon_id, amazon_link):
        c1 = aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        c2 = aff.record_conversion(amazon_link["link_id"], sale_amount=200.0)
        aff.reject_conversion(c2["conversion_id"])
        result = aff.affiliate_summary()
        assert result["total_revenue"] == pytest.approx(100.0)

    def test_summary_commissions_pending_calculation(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.affiliate_summary()
        assert result["total_commissions_pending"] == pytest.approx(
            result["total_commissions_earned"] - result["total_commissions_paid"]
        )

    def test_summary_commissions_paid_after_payout(self, amazon_id, amazon_link):
        conv = aff.record_conversion(amazon_link["link_id"], sale_amount=500.0)
        aff.approve_conversion(conv["conversion_id"])
        aff.record_payout(amazon_id, amount=20.0)
        result = aff.affiliate_summary()
        assert result["total_commissions_paid"] == pytest.approx(20.0)

    def test_summary_overall_conversion_rate(self, amazon_id, amazon_link):
        # 4 clicks, 1 conversion = 25%
        for _ in range(4):
            aff.record_click(amazon_link["link_id"])
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.affiliate_summary()
        assert result["overall_conversion_rate_pct"] == pytest.approx(25.0)

    def test_summary_overall_epc(self, amazon_id, amazon_link):
        # 2 clicks, 1 conversion of 100 at 4% = 4.0 commission → epc = 2.0
        aff.record_click(amazon_link["link_id"])
        aff.record_click(amazon_link["link_id"])
        aff.record_conversion(amazon_link["link_id"], sale_amount=100.0)
        result = aff.affiliate_summary()
        assert result["overall_epc"] == pytest.approx(2.0)

    def test_summary_commissions_earned_correct(self, amazon_id, amazon_link):
        aff.record_conversion(amazon_link["link_id"], sale_amount=200.0)
        result = aff.affiliate_summary()
        # 200 * 4% = 8.0
        assert result["total_commissions_earned"] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# TestToolWrappers
# ---------------------------------------------------------------------------

class TestToolWrappers:
    """Agent-callable tool wrapper functions."""

    def test_add_program_tool_success(self):
        result = aff.add_program_tool(name="ToolProg", commission_rate=8.0)
        assert "added" in result
        assert "ToolProg" in result

    def test_add_program_tool_includes_id(self):
        result = aff.add_program_tool(name="IdProg", commission_rate=5.0)
        assert "id=" in result

    def test_add_program_tool_no_name_returns_error(self):
        result = aff.add_program_tool(name="")
        assert "error" in result.lower()

    def test_add_program_tool_with_preset(self):
        result = aff.add_program_tool(name="AmazonTool", preset="amazon")
        assert "AmazonTool" in result

    def test_create_link_tool_success(self):
        aff.add_program("ToolLinkProg", commission_rate=5.0, affiliate_id="tlp123")
        result_str = aff.create_link_tool(
            program_name="ToolLinkProg",
            destination_url="https://example.com/tool-target",
        )
        result = json.loads(result_str)
        assert result["ok"] is True
        assert "link_id" in result
        assert "slug" in result

    def test_create_link_tool_missing_program_returns_error(self):
        result = aff.create_link_tool(program_name="", destination_url="https://x.com")
        assert result.startswith("error")

    def test_create_link_tool_missing_url_returns_error(self):
        result = aff.create_link_tool(program_name="SomeProg", destination_url="")
        assert result.startswith("error")

    def test_create_link_tool_unknown_program_returns_error(self):
        result = aff.create_link_tool(
            program_name="NoSuchProg", destination_url="https://x.com"
        )
        assert result.startswith("error")

    def test_record_conversion_tool_success(self):
        aff.add_program("ConvToolProg", commission_rate=10.0, affiliate_id="ctp")
        link = aff.create_link("ConvToolProg", "https://example.com/ct")
        result_str = aff.record_conversion_tool(
            link_id=link["link_id"], sale_amount=100.0, order_id="T-123"
        )
        result = json.loads(result_str)
        assert "conversion_id" in result
        assert result["commission"] == pytest.approx(10.0)

    def test_record_conversion_tool_no_link_id_returns_error(self):
        result = aff.record_conversion_tool(link_id=0, sale_amount=100.0)
        assert result.startswith("error")

    def test_affiliate_summary_tool_returns_json(self):
        result_str = aff.affiliate_summary_tool()
        result = json.loads(result_str)
        assert "active_programs" in result

    def test_program_performance_tool_no_programs_returns_sentinel(self):
        result = aff.program_performance_tool(days=30)
        assert result == "no affiliate program data"

    def test_program_performance_tool_returns_json_with_programs(self):
        aff.add_program("PerfToolProg", commission_rate=5.0, affiliate_id="ptp")
        result_str = aff.program_performance_tool(days=30)
        result = json.loads(result_str)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_top_links_tool_no_data_returns_sentinel(self):
        result = aff.top_links_tool(days=30, limit=10)
        assert result == "no affiliate link data"

    def test_top_links_tool_returns_json_with_links(self):
        aff.add_program("TopProg", commission_rate=5.0, affiliate_id="tpid")
        aff.create_link("TopProg", "https://example.com/top")
        result_str = aff.top_links_tool(days=30, limit=10)
        result = json.loads(result_str)
        assert isinstance(result, list)

    def test_list_programs_tool_no_programs_returns_sentinel(self):
        result = aff.list_programs_tool()
        assert result == "no affiliate programs configured"

    def test_list_programs_tool_returns_json_with_programs(self):
        aff.add_program("ListProg", commission_rate=3.0, affiliate_id="lpid")
        result_str = aff.list_programs_tool()
        result = json.loads(result_str)
        assert isinstance(result, list)
        assert any(p["name"] == "ListProg" for p in result)

    def test_list_programs_tool_contains_expected_keys(self):
        aff.add_program("KeyProg", commission_rate=6.0, affiliate_id="kpid")
        result_str = aff.list_programs_tool()
        result = json.loads(result_str)
        prog = result[0]
        for key in ["id", "name", "network", "commission_rate", "commission_type"]:
            assert key in prog, f"missing key: {key}"

    def test_create_link_tool_with_content_id_and_campaign(self):
        aff.add_program("ContentProg", commission_rate=5.0, affiliate_id="cpid")
        result_str = aff.create_link_tool(
            program_name="ContentProg",
            destination_url="https://example.com/p",
            content_id="blog-99",
            campaign="spring",
        )
        result = json.loads(result_str)
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# TestPopularNetworksConstants
# ---------------------------------------------------------------------------

class TestPopularNetworksConstants:
    """POPULAR_NETWORKS dictionary structure and values."""

    def test_popular_networks_not_empty(self):
        assert len(aff.POPULAR_NETWORKS) > 0

    def test_amazon_preset_exists(self):
        assert "amazon" in aff.POPULAR_NETWORKS

    def test_clickbank_preset_exists(self):
        assert "clickbank" in aff.POPULAR_NETWORKS

    def test_shareasale_preset_exists(self):
        assert "shareasale" in aff.POPULAR_NETWORKS

    def test_impact_preset_exists(self):
        assert "impact" in aff.POPULAR_NETWORKS

    def test_cj_preset_exists(self):
        assert "cj" in aff.POPULAR_NETWORKS

    def test_each_preset_has_required_keys(self):
        required_keys = {"network", "commission_type", "commission_rate", "cookie_days"}
        for preset_name, preset_data in aff.POPULAR_NETWORKS.items():
            assert required_keys.issubset(
                set(preset_data.keys())
            ), f"preset '{preset_name}' missing keys"

    def test_amazon_commission_rate(self):
        assert aff.POPULAR_NETWORKS["amazon"]["commission_rate"] == pytest.approx(4.0)

    def test_clickbank_commission_rate(self):
        assert aff.POPULAR_NETWORKS["clickbank"]["commission_rate"] == pytest.approx(50.0)

    def test_amazon_cookie_days(self):
        assert aff.POPULAR_NETWORKS["amazon"]["cookie_days"] == 24

    def test_clickbank_cookie_days(self):
        assert aff.POPULAR_NETWORKS["clickbank"]["cookie_days"] == 60

    def test_partnerstack_cookie_days(self):
        assert aff.POPULAR_NETWORKS["partnerstack"]["cookie_days"] == 90

    def test_commission_types_list_not_empty(self):
        assert len(aff.COMMISSION_TYPES) > 0
        assert "percentage" in aff.COMMISSION_TYPES
        assert "flat" in aff.COMMISSION_TYPES
        assert "cpa" in aff.COMMISSION_TYPES

    def test_conversion_statuses_list_correct(self):
        for status in ["pending", "approved", "rejected", "paid"]:
            assert status in aff.CONVERSION_STATUSES

    def test_all_presets_have_percentage_type(self):
        for name, data in aff.POPULAR_NETWORKS.items():
            assert data["commission_type"] == "percentage", (
                f"preset '{name}' unexpected commission_type"
            )
