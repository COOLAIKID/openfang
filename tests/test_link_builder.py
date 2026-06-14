"""Tests for autoearn/core/link_builder.py."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

_tmp_dir = tempfile.mkdtemp()
_DB_PATH = os.path.join(_tmp_dir, f"test_lb_{uuid.uuid4().hex[:8]}.db")


def _get_db_path():
    return _DB_PATH


with patch("autoearn.core.database.get_db_path", _get_db_path):
    import autoearn.core.link_builder as lb


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_schema():
    lb._schema_ready = False
    conn = sqlite3.connect(_DB_PATH)
    conn.executescript("""
        DROP TABLE IF EXISTS link_clicks;
        DROP TABLE IF EXISTS link_in_bio_items;
        DROP TABLE IF EXISTS link_in_bio_pages;
        DROP TABLE IF EXISTS short_links;
        DROP TABLE IF EXISTS tracked_links;
        DROP TABLE IF EXISTS campaign_groups;
    """)
    conn.close()
    yield


@pytest.fixture()
def sample_link():
    with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
        return lb.create_link(
            destination="https://example.com/product",
            utm_source="google",
            utm_medium="cpc",
            utm_campaign="spring_sale",
            title="Spring Sale Link",
        )


@pytest.fixture()
def sample_bio_page():
    with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
        return lb.create_bio_page(
            handle="testuser",
            title="Test User",
            bio="Building cool things",
        )


# ── Tracked links ─────────────────────────────────────────────────────────

class TestCreateLink:
    def test_basic_creation(self, sample_link):
        assert sample_link.id is not None
        assert sample_link.destination == "https://example.com/product"
        assert sample_link.utm_source == "google"
        assert sample_link.utm_medium == "cpc"
        assert sample_link.utm_campaign == "spring_sale"

    def test_slug_auto_generated(self, sample_link):
        assert len(sample_link.slug) == 8

    def test_custom_slug(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lnk = lb.create_link("https://example.com", slug="my-custom")
        assert lnk.slug == "my-custom"

    def test_status_defaults_to_active(self, sample_link):
        assert sample_link.status == "active"

    def test_full_url_includes_utm_params(self, sample_link):
        url = sample_link.full_url
        assert "utm_source=google" in url
        assert "utm_medium=cpc" in url
        assert "utm_campaign=spring_sale" in url

    def test_full_url_base_included(self, sample_link):
        url = sample_link.full_url
        assert "example.com" in url

    def test_full_url_no_params_returns_destination(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lnk = lb.create_link("https://example.com/page")
        assert lnk.full_url == "https://example.com/page"

    def test_full_url_with_custom_params(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lnk = lb.create_link(
                "https://example.com",
                custom_params={"ref": "banner", "variant": "A"},
            )
        url = lnk.full_url
        assert "ref=banner" in url
        assert "variant=A" in url

    def test_with_expiry(self):
        future = (datetime.utcnow() + timedelta(days=7)).isoformat()
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lnk = lb.create_link("https://example.com", expires_at=future)
        assert lnk.expires_at == future

    def test_with_tags(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lnk = lb.create_link("https://example.com", tags=["promo", "summer"])
        assert "promo" in lnk.tags
        assert "summer" in lnk.tags

    def test_created_at_set(self, sample_link):
        assert sample_link.created_at
        datetime.fromisoformat(sample_link.created_at)


class TestGetLink:
    def test_get_by_id(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            fetched = lb.get_link(sample_link.id)
        assert fetched is not None
        assert fetched.id == sample_link.id

    def test_get_nonexistent_returns_none(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = lb.get_link(99999)
        assert result is None

    def test_get_by_slug(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            fetched = lb.get_link_by_slug(sample_link.slug)
        assert fetched is not None
        assert fetched.slug == sample_link.slug

    def test_get_by_unknown_slug_returns_none(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = lb.get_link_by_slug("nosuchslug")
        assert result is None


class TestListLinks:
    def test_list_active_links(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            links = lb.list_links(status="active")
        assert len(links) >= 1

    def test_filter_by_campaign(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            links = lb.list_links(campaign_name="spring_sale")
        assert all(lnk.campaign_name == "spring_sale" for lnk in links)
        assert len(links) >= 1

    def test_filter_by_source(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            links = lb.list_links(utm_source="google")
        assert all(lnk.utm_source == "google" for lnk in links)

    def test_limit_respected(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            for i in range(5):
                lb.create_link(f"https://example.com/{i}")
            links = lb.list_links(status="active", limit=2)
        assert len(links) <= 2


class TestRecordClick:
    def test_record_click_returns_url(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            url = lb.record_click(sample_link.slug, ip="1.2.3.4")
        assert url is not None
        assert "example.com" in url

    def test_record_click_increments_count(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, ip="1.2.3.4")
            lb.record_click(sample_link.slug, ip="5.6.7.8")
            fetched = lb.get_link(sample_link.id)
        assert fetched.click_count >= 2

    def test_unique_clicks_count(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, ip="1.2.3.4")
            lb.record_click(sample_link.slug, ip="1.2.3.4")  # same IP
            lb.record_click(sample_link.slug, ip="9.9.9.9")
            fetched = lb.get_link(sample_link.id)
        assert fetched.unique_clicks == 2  # two unique IPs

    def test_unknown_slug_returns_none(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = lb.record_click("nosuchslug")
        assert result is None

    def test_paused_link_returns_none(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.update_link_status(sample_link.slug, "paused")
            result = lb.record_click(sample_link.slug)
        assert result is None

    def test_expired_link_returns_none(self):
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lnk = lb.create_link("https://example.com", expires_at=past)
            result = lb.record_click(lnk.slug)
        assert result is None

    def test_click_updates_last_clicked(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug)
            fetched = lb.get_link(sample_link.id)
        assert fetched.last_clicked is not None


class TestUpdateLinkStatus:
    def test_pause_link(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.update_link_status(sample_link.slug, "paused")
            fetched = lb.get_link(sample_link.id)
        assert fetched.status == "paused"

    def test_archive_link(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.update_link_status(sample_link.slug, "archived")
            fetched = lb.get_link(sample_link.id)
        assert fetched.status == "archived"


class TestDeleteLink:
    def test_delete_removes_link(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.delete_link(sample_link.id)
            result = lb.get_link(sample_link.id)
        assert result is None


# ── Short links ───────────────────────────────────────────────────────────

class TestShortLinks:
    def test_create_short_link(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com/very-long-url", title="Test")
        assert sl.id is not None
        assert sl.short_code
        assert sl.long_url == "https://example.com/very-long-url"

    def test_custom_short_code(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com", short_code="abc123")
        assert sl.short_code == "abc123"

    def test_resolve_short_link(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com/target")
            url = lb.resolve_short_link(sl.short_code)
        assert url == "https://example.com/target"

    def test_resolve_increments_clicks(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com")
            lb.resolve_short_link(sl.short_code)
            lb.resolve_short_link(sl.short_code)
            links = lb.list_short_links()
        matching = [l for l in links if l.short_code == sl.short_code]
        assert matching[0].click_count == 2

    def test_resolve_unknown_code_returns_none(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = lb.resolve_short_link("nosuchcode")
        assert result is None

    def test_deactivate_short_link(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com")
            lb.deactivate_short_link(sl.short_code)
            result = lb.resolve_short_link(sl.short_code)
        assert result is None

    def test_expired_short_link_returns_none(self):
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com", expires_at=past)
            result = lb.resolve_short_link(sl.short_code)
        assert result is None

    def test_list_short_links(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.create_short_link("https://a.com")
            lb.create_short_link("https://b.com")
            links = lb.list_short_links()
        assert len(links) >= 2


# ── Link-in-bio pages ─────────────────────────────────────────────────────

class TestLinkInBioPages:
    def test_create_bio_page(self, sample_bio_page):
        assert sample_bio_page.id is not None
        assert sample_bio_page.handle == "testuser"
        assert sample_bio_page.title == "Test User"

    def test_duplicate_handle_raises(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="already taken"):
                lb.create_bio_page("testuser")

    def test_get_bio_page(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            fetched = lb.get_bio_page("testuser")
        assert fetched is not None
        assert fetched.handle == "testuser"

    def test_get_unknown_bio_page(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = lb.get_bio_page("nosuchhandle")
        assert result is None

    def test_add_bio_link(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            item = lb.add_bio_link(
                "testuser",
                title="My Website",
                url="https://mysite.com",
                description="Personal site",
            )
        assert item["id"] is not None
        assert item["title"] == "My Website"

    def test_add_bio_link_unknown_handle_raises(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            with pytest.raises(ValueError, match="not found"):
                lb.add_bio_link("nosuchhandle", "Title", "https://example.com")

    def test_bio_page_includes_items(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.add_bio_link("testuser", "Link 1", "https://link1.com")
            lb.add_bio_link("testuser", "Link 2", "https://link2.com")
            page = lb.get_bio_page("testuser")
        assert len(page.items) >= 2

    def test_record_bio_view(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_bio_view("testuser")
            lb.record_bio_view("testuser")
            page = lb.get_bio_page("testuser")
        assert page.view_count >= 2

    def test_update_bio_page(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.update_bio_page("testuser", title="New Title", bio="New Bio")
            page = lb.get_bio_page("testuser")
        assert page.title == "New Title"
        assert page.bio == "New Bio"

    def test_list_bio_pages(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            pages = lb.list_bio_pages()
        assert len(pages) >= 1

    def test_record_bio_item_click(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            item = lb.add_bio_link("testuser", "Click Me", "https://example.com")
            lb.record_bio_item_click(item["id"])
            lb.record_bio_item_click(item["id"])
            page = lb.get_bio_page("testuser")
        for i in page.items:
            if i["id"] == item["id"]:
                assert i["click_count"] >= 2


# ── Campaign management ───────────────────────────────────────────────────

class TestCampaignGroups:
    def test_create_campaign_group(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            grp = lb.create_campaign_group("Summer 2024", budget_usd=500.0)
        assert grp["id"] is not None
        assert grp["name"] == "Summer 2024"
        assert grp["budget_usd"] == 500.0

    def test_duplicate_campaign_raises(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.create_campaign_group("Unique Camp")
            with pytest.raises(ValueError, match="already exists"):
                lb.create_campaign_group("Unique Camp")

    def test_campaign_performance_returns_stats(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, ip="1.2.3.4")
            perf = lb.campaign_performance("spring_sale")
        assert perf["campaign"] == "spring_sale"
        assert perf["link_count"] >= 1
        assert perf["total_clicks"] >= 1

    def test_campaign_performance_unknown(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            perf = lb.campaign_performance("no-such-campaign")
        assert perf["link_count"] == 0
        assert perf["total_clicks"] == 0


# ── Analytics ─────────────────────────────────────────────────────────────

class TestAnalytics:
    def test_link_click_history_returns_list(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, ip="10.0.0.1")
            history = lb.link_click_history(sample_link.slug, days=30)
        assert isinstance(history, list)
        if history:
            assert "day" in history[0]
            assert "clicks" in history[0]

    def test_link_click_history_unknown_slug(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            history = lb.link_click_history("nosuchslug")
        assert history == []

    def test_top_links_returns_list(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug)
            links = lb.top_links(limit=10)
        assert isinstance(links, list)

    def test_device_breakdown(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, device="mobile")
            lb.record_click(sample_link.slug, device="desktop")
            breakdown = lb.device_breakdown(sample_link.slug)
        assert isinstance(breakdown, list)

    def test_geo_breakdown(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, country="US")
            lb.record_click(sample_link.slug, country="UK")
            breakdown = lb.geo_breakdown(sample_link.slug)
        assert isinstance(breakdown, list)
        countries = [r["country"] for r in breakdown]
        assert "US" in countries

    def test_referrer_breakdown(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug, referrer="https://google.com")
            breakdown = lb.referrer_breakdown(sample_link.slug)
        assert isinstance(breakdown, list)

    def test_link_summary(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.record_click(sample_link.slug)
            summary = lb.link_summary()
        assert "tracked_links" in summary
        assert "short_links" in summary
        assert "link_in_bio" in summary
        assert summary["tracked_links"]["total"] >= 1


# ── Bulk link generation ──────────────────────────────────────────────────

class TestBulkLinkGeneration:
    def test_bulk_create_links(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            links = lb.bulk_create_links(
                destination="https://example.com",
                sources=["google", "facebook", "instagram"],
                medium="social",
                campaign="winter_promo",
            )
        assert len(links) == 3
        sources = [l.utm_source for l in links]
        assert "google" in sources
        assert "facebook" in sources

    def test_generate_utm_url_no_db(self):
        url = lb.generate_utm_url(
            "https://example.com",
            utm_source="email",
            utm_medium="newsletter",
            utm_campaign="weekly",
        )
        assert "utm_source=email" in url
        assert "utm_medium=newsletter" in url
        assert "utm_campaign=weekly" in url

    def test_generate_utm_url_preserves_path(self):
        url = lb.generate_utm_url(
            "https://example.com/page?existing=1",
            utm_source="test",
            utm_medium="test",
            utm_campaign="test",
        )
        assert "existing=1" in url
        assert "utm_source=test" in url

    def test_generate_utm_with_content(self):
        url = lb.generate_utm_url(
            "https://example.com",
            utm_source="fb",
            utm_medium="social",
            utm_campaign="camp",
            utm_content="banner_a",
        )
        assert "utm_content=banner_a" in url


# ── to_dict ───────────────────────────────────────────────────────────────

class TestToDict:
    def test_tracked_link_to_dict(self, sample_link):
        d = sample_link.to_dict()
        required = ["id", "slug", "destination", "full_url", "status", "click_count"]
        for k in required:
            assert k in d

    def test_tracked_link_to_dict_is_json_serializable(self, sample_link):
        d = sample_link.to_dict()
        json.dumps(d)  # should not raise

    def test_short_link_to_dict(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            sl = lb.create_short_link("https://example.com")
        d = sl.to_dict()
        required = ["id", "short_code", "long_url", "click_count"]
        for k in required:
            assert k in d

    def test_bio_page_to_dict(self, sample_bio_page):
        d = sample_bio_page.to_dict()
        required = ["id", "handle", "title", "bio", "view_count", "items"]
        for k in required:
            assert k in d


# ── Tool wrappers ─────────────────────────────────────────────────────────

class TestToolWrappers:
    def test_create_link_tool(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.create_link_tool(
                destination="https://example.com",
                utm_source="google",
                utm_medium="cpc",
                utm_campaign="test",
            ))
        assert result["ok"] is True
        assert "link" in result

    def test_campaign_performance_tool(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.campaign_performance_tool("spring_sale"))
        assert result["campaign"] == "spring_sale"

    def test_top_links_tool(self, sample_link):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.top_links_tool())
        assert isinstance(result, list)

    def test_create_short_link_tool(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.create_short_link_tool("https://example.com", "Test"))
        assert result["ok"] is True
        assert "short_link" in result

    def test_create_bio_page_tool(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.create_bio_page_tool("myhandle", "My Page", "My bio"))
        assert result["ok"] is True

    def test_create_bio_page_tool_duplicate(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            lb.create_bio_page_tool("duphandle")
            result = json.loads(lb.create_bio_page_tool("duphandle"))
        assert result["ok"] is False

    def test_add_bio_link_tool(self, sample_bio_page):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.add_bio_link_tool(
                "testuser", "GitHub", "https://github.com/user"
            ))
        assert result["ok"] is True

    def test_link_summary_tool(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.link_summary_tool())
        assert "tracked_links" in result

    def test_generate_utm_tool(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.generate_utm_tool(
                "https://example.com", "google", "cpc", "launch"
            ))
        assert result["ok"] is True
        assert "utm_source=google" in result["url"]

    def test_bulk_create_links_tool(self):
        with patch("autoearn.core.link_builder.get_db_path", _get_db_path):
            result = json.loads(lb.bulk_create_links_tool(
                "https://example.com",
                "google,facebook,twitter",
                "social",
                "bulk_camp",
            ))
        assert result["ok"] is True
        assert result["count"] == 3
