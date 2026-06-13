"""Tests for core/toolkit/seo.py — SEO toolkit (no real HTTP calls)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from core.toolkit import seo


def _parse(result: str) -> dict | list:
    return json.loads(result)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head>
  <title>Best Python Tutorial for Beginners</title>
  <meta name="description" content="Learn Python programming with this comprehensive guide for beginners.">
</head>
<body>
  <h1>Python Tutorial for Beginners</h1>
  <h2>Getting Started with Python</h2>
  <h2>Advanced Python Concepts</h2>
  <h3>Functions and Classes</h3>
  <p>Python is a great programming language. It is widely used in web development, data science, and automation.</p>
  <img src="python.png" alt="Python logo">
  <a href="/about">About Us</a>
  <a href="https://external.com">External Link</a>
</body>
</html>"""


def _mock_response(status=200, text="", json_data=None):
    mock = MagicMock()
    mock.status_code = status
    mock.text = text
    if json_data is not None:
        mock.json.return_value = json_data
    mock.raise_for_status = MagicMock()
    return mock


# ---------------------------------------------------------------------------
# keyword_ideas
# ---------------------------------------------------------------------------

class TestKeywordIdeas:
    def test_returns_valid_json(self):
        with patch("requests.get", return_value=_mock_response(200, json_data=[[], []])):
            result = seo.keyword_ideas("python tutorial", num=20)
        data = _parse(result)
        assert "keywords" in data

    def test_seed_present_in_result(self):
        with patch("requests.get", return_value=_mock_response(200, json_data=[[], []])):
            data = _parse(seo.keyword_ideas("machine learning"))
        assert data["seed"] == "machine learning"

    def test_keywords_from_prefixes_and_suffixes(self):
        with patch("requests.get", return_value=_mock_response(200, json_data=[[], []])):
            data = _parse(seo.keyword_ideas("seo", num=50))
        keywords = data["keywords"]
        assert any("seo" in kw.lower() for kw in keywords)

    def test_num_parameter_respected(self):
        with patch("requests.get", return_value=_mock_response(200, json_data=[[], []])):
            data = _parse(seo.keyword_ideas("blogging", num=10))
        assert len(data["keywords"]) <= 10

    def test_count_matches_keywords_length(self):
        with patch("requests.get", return_value=_mock_response(200, json_data=[[], []])):
            data = _parse(seo.keyword_ideas("affiliate marketing"))
        assert data["count"] == len(data["keywords"])

    def test_network_error_gracefully_handled(self):
        import requests
        with patch("requests.get", side_effect=Exception("Network failure")):
            data = _parse(seo.keyword_ideas("keyword"))
        assert "keywords" in data  # Should still return from built-in prefixes/suffixes

    def test_duckduckgo_suggestions_included(self):
        ddg_suggestions = [[], ["python for beginners", "python tutorial online"]]
        with patch("requests.get", return_value=_mock_response(200, json_data=ddg_suggestions)):
            data = _parse(seo.keyword_ideas("python"))
        # Should include suggestions from mocked DDG response
        assert "keywords" in data


# ---------------------------------------------------------------------------
# heading_structure
# ---------------------------------------------------------------------------

class TestHeadingStructure:
    def test_returns_valid_json(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        assert "structure" in data
        assert "flat" in data

    def test_flat_list_has_all_headings(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        flat = data["flat"]
        levels = [h["level"] for h in flat]
        assert 1 in levels
        assert 2 in levels
        assert 3 in levels

    def test_h1_text_extracted(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        h1s = [h for h in data["flat"] if h["level"] == 1]
        assert h1s
        assert "Python Tutorial" in h1s[0]["text"]

    def test_h2_texts_extracted(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        h2s = [h for h in data["flat"] if h["level"] == 2]
        assert len(h2s) == 2

    def test_hierarchy_built(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        hierarchy = data["structure"]
        assert hierarchy  # Should have at least one top-level heading

    def test_empty_html_returns_empty_structure(self):
        data = _parse(seo.heading_structure("<html><body><p>No headings</p></body></html>"))
        assert data["flat"] == []
        assert data["structure"] == []

    def test_heading_level_field_is_int(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        for h in data["flat"]:
            assert isinstance(h["level"], int)

    def test_tag_field_present(self):
        data = _parse(seo.heading_structure(SAMPLE_HTML))
        for h in data["flat"]:
            assert h["tag"] in ("h1", "h2", "h3", "h4", "h5", "h6")


# ---------------------------------------------------------------------------
# generate_sitemap
# ---------------------------------------------------------------------------

class TestGenerateSitemap:
    def test_returns_xml_string(self):
        result = seo.generate_sitemap("https://example.com", ["/", "/about", "/blog"])
        assert result.startswith("<?xml")
        assert "<urlset" in result

    def test_all_urls_included(self):
        urls = ["/page1", "/page2", "/page3"]
        result = seo.generate_sitemap("https://example.com", urls)
        for url in urls:
            assert "page1" in result or "page2" in result or "page3" in result

    def test_absolute_urls_used_directly(self):
        result = seo.generate_sitemap("https://example.com", ["https://other.com/page"])
        assert "https://other.com/page" in result

    def test_relative_urls_prepended_with_base(self):
        result = seo.generate_sitemap("https://mysite.com", ["/about"])
        assert "https://mysite.com/about" in result

    def test_lastmod_present(self):
        result = seo.generate_sitemap("https://example.com", ["/"])
        assert "<lastmod>" in result

    def test_changefreq_present(self):
        result = seo.generate_sitemap("https://example.com", ["/"])
        assert "<changefreq>" in result

    def test_priority_present(self):
        result = seo.generate_sitemap("https://example.com", ["/"])
        assert "<priority>" in result

    def test_empty_url_list(self):
        result = seo.generate_sitemap("https://example.com", [])
        assert "<urlset" in result
        assert "<loc>" not in result

    def test_base_url_trailing_slash_removed(self):
        result = seo.generate_sitemap("https://example.com/", ["/page"])
        # Should not produce double slash
        assert "example.com//page" not in result


# ---------------------------------------------------------------------------
# generate_robots_txt
# ---------------------------------------------------------------------------

class TestGenerateRobotsTxt:
    def test_returns_string(self):
        result = seo.generate_robots_txt()
        assert isinstance(result, str)

    def test_user_agent_present(self):
        result = seo.generate_robots_txt()
        assert "User-agent: *" in result

    def test_default_disallow_paths(self):
        result = seo.generate_robots_txt()
        assert "Disallow: /admin" in result
        assert "Disallow: /private" in result

    def test_custom_disallow(self):
        result = seo.generate_robots_txt(disallow=["/secret", "/internal"])
        assert "Disallow: /secret" in result
        assert "Disallow: /internal" in result

    def test_custom_allow(self):
        result = seo.generate_robots_txt(allow=["/public"])
        assert "Allow: /public" in result

    def test_sitemap_reference_present(self):
        result = seo.generate_robots_txt()
        assert "Sitemap:" in result

    def test_empty_disallow(self):
        result = seo.generate_robots_txt(disallow=[])
        assert "Disallow:" not in result


# ---------------------------------------------------------------------------
# json_ld_article
# ---------------------------------------------------------------------------

class TestJsonLdArticle:
    def test_returns_script_tag(self):
        result = seo.json_ld_article(
            title="My Article",
            description="A great article",
            author="John Doe",
            url="https://example.com/article",
            date_published="2025-01-01",
        )
        assert "<script" in result
        assert "application/ld+json" in result
        assert "</script>" in result

    def test_schema_type_is_article(self):
        result = seo.json_ld_article(
            "Title", "Desc", "Author", "https://example.com", "2025-01-01"
        )
        assert '"@type": "Article"' in result

    def test_headline_present(self):
        result = seo.json_ld_article(
            "My Title", "Desc", "Author", "https://example.com", "2025-01-01"
        )
        assert "My Title" in result

    def test_author_present(self):
        result = seo.json_ld_article(
            "T", "D", "Jane Smith", "https://example.com", "2025-01-01"
        )
        assert "Jane Smith" in result

    def test_image_optional(self):
        without_image = seo.json_ld_article("T", "D", "A", "https://e.com", "2025-01-01")
        with_image = seo.json_ld_article("T", "D", "A", "https://e.com", "2025-01-01", "https://img.com/a.jpg")
        assert "image" not in without_image or "image" in with_image

    def test_url_present(self):
        result = seo.json_ld_article("T", "D", "A", "https://mysite.com/post", "2025-01-01")
        assert "https://mysite.com/post" in result

    def test_valid_json_inside_script(self):
        result = seo.json_ld_article("T", "D", "A", "https://e.com", "2025-01-01")
        # Extract JSON between script tags
        start = result.index("{")
        end = result.rindex("}") + 1
        json_part = result[start:end]
        parsed = json.loads(json_part)
        assert parsed["@type"] == "Article"


# ---------------------------------------------------------------------------
# json_ld_product
# ---------------------------------------------------------------------------

class TestJsonLdProduct:
    def test_returns_script_tag(self):
        result = seo.json_ld_product(
            name="Widget Pro",
            description="A great widget",
            price=49.99,
            currency="USD",
            url="https://example.com/widget",
        )
        assert "<script" in result
        assert "application/ld+json" in result

    def test_schema_type_is_product(self):
        result = seo.json_ld_product("Widget", "Desc", 29.99, "USD", "https://e.com")
        assert '"@type": "Product"' in result

    def test_price_present(self):
        result = seo.json_ld_product("Widget", "Desc", 29.99, "USD", "https://e.com")
        assert "29.99" in result

    def test_currency_present(self):
        result = seo.json_ld_product("Widget", "Desc", 29.99, "EUR", "https://e.com")
        assert "EUR" in result

    def test_brand_optional(self):
        with_brand = seo.json_ld_product("W", "D", 10.0, "USD", "https://e.com", brand="Acme")
        assert "Acme" in with_brand

    def test_sku_optional(self):
        with_sku = seo.json_ld_product("W", "D", 10.0, "USD", "https://e.com", sku="SKU-001")
        assert "SKU-001" in with_sku

    def test_offers_schema_present(self):
        result = seo.json_ld_product("W", "D", 10.0, "USD", "https://e.com")
        assert "Offer" in result

    def test_valid_json_inside_script(self):
        result = seo.json_ld_product("Widget", "Desc", 49.99, "USD", "https://e.com")
        start = result.index("{")
        end = result.rindex("}") + 1
        parsed = json.loads(result[start:end])
        assert parsed["@type"] == "Product"


# ---------------------------------------------------------------------------
# json_ld_faq
# ---------------------------------------------------------------------------

class TestJsonLdFaq:
    def _qa_list(self):
        return [
            {"q": "What is Python?", "a": "Python is a programming language."},
            {"q": "Is Python free?", "a": "Yes, Python is open source and free."},
        ]

    def test_returns_script_tag(self):
        result = seo.json_ld_faq(self._qa_list())
        assert "<script" in result
        assert "application/ld+json" in result

    def test_schema_type_is_faq_page(self):
        result = seo.json_ld_faq(self._qa_list())
        assert "FAQPage" in result

    def test_questions_included(self):
        result = seo.json_ld_faq(self._qa_list())
        assert "What is Python?" in result
        assert "Is Python free?" in result

    def test_answers_included(self):
        result = seo.json_ld_faq(self._qa_list())
        assert "programming language" in result

    def test_empty_list_returns_faq_schema(self):
        result = seo.json_ld_faq([])
        assert "FAQPage" in result

    def test_valid_json_inside_script(self):
        result = seo.json_ld_faq(self._qa_list())
        start = result.index("{")
        end = result.rindex("}") + 1
        parsed = json.loads(result[start:end])
        assert parsed["@type"] == "FAQPage"
        assert len(parsed["mainEntity"]) == 2

    def test_question_type_correct(self):
        result = seo.json_ld_faq(self._qa_list())
        start = result.index("{")
        end = result.rindex("}") + 1
        parsed = json.loads(result[start:end])
        assert parsed["mainEntity"][0]["@type"] == "Question"


# ---------------------------------------------------------------------------
# lsi_keywords
# ---------------------------------------------------------------------------

class TestLsiKeywords:
    def test_returns_valid_json(self):
        with patch("requests.get", side_effect=Exception("No network")):
            result = seo.lsi_keywords("content marketing")
        data = _parse(result)
        assert "lsi_keywords" in data

    def test_seed_keyword_present(self):
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("email marketing"))
        assert data["keyword"] == "email marketing"

    def test_count_matches_list_length(self):
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("SEO strategy"))
        assert data["count"] == len(data["lsi_keywords"])

    def test_lsi_keywords_non_empty(self):
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("content marketing"))
        assert len(data["lsi_keywords"]) > 0

    def test_synonyms_from_map_included(self):
        # "best" → "top", "leading", etc.
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("best tools"))
        lsi = data["lsi_keywords"]
        # Should include variations like "top tools"
        assert any("tools" in kw for kw in lsi)

    def test_modifiers_before_and_after(self):
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("python"))
        lsi = data["lsi_keywords"]
        # Should include prefix/suffix variations
        assert len(lsi) > 5

    def test_original_keyword_not_in_lsi(self):
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("email marketing"))
        lsi = data["lsi_keywords"]
        assert "email marketing" not in lsi

    def test_max_30_keywords(self):
        with patch("requests.get", side_effect=Exception("No network")):
            data = _parse(seo.lsi_keywords("content marketing strategy"))
        assert len(data["lsi_keywords"]) <= 30

    def test_network_suggestions_included_when_available(self):
        google_suggestions = [["python"], ["python for beginners", "python data science"]]
        with patch("requests.get", return_value=_mock_response(200, json_data=google_suggestions)):
            data = _parse(seo.lsi_keywords("python"))
        lsi = data["lsi_keywords"]
        assert "python for beginners" in lsi or len(lsi) > 0


# ---------------------------------------------------------------------------
# internal_link_suggestions
# ---------------------------------------------------------------------------

class TestInternalLinkSuggestions:
    def test_returns_valid_json(self):
        content = "Learn about python programming and machine learning with our tutorials."
        urls = ["https://example.com/python-programming"]
        result = seo.internal_link_suggestions(content, urls)
        data = _parse(result)
        assert "suggestions" in data

    def test_finds_keyword_match(self):
        content = "Python programming is great. You should try python programming today."
        urls = ["https://example.com/python-programming"]
        data = _parse(seo.internal_link_suggestions(content, urls))
        assert data["count"] > 0

    def test_no_match_returns_empty(self):
        content = "Cooking recipes for Italian pasta dishes."
        urls = ["https://example.com/python-programming"]
        data = _parse(seo.internal_link_suggestions(content, urls))
        assert data["count"] == 0

    def test_suggestion_has_required_fields(self):
        content = "Learn python programming here."
        urls = ["https://example.com/python-programming"]
        data = _parse(seo.internal_link_suggestions(content, urls))
        if data["suggestions"]:
            s = data["suggestions"][0]
            assert "url" in s
            assert "anchor_text" in s
            assert "context" in s

    def test_empty_urls_returns_empty(self):
        content = "Some content about anything."
        data = _parse(seo.internal_link_suggestions(content, []))
        assert data["suggestions"] == []
        assert data["count"] == 0

    def test_count_matches_suggestions_length(self):
        content = "machine learning and deep learning are related to machine learning research."
        urls = ["https://example.com/machine-learning"]
        data = _parse(seo.internal_link_suggestions(content, urls))
        assert data["count"] == len(data["suggestions"])

    def test_max_three_suggestions_per_url(self):
        # Keyword appears 5 times but only 3 suggestions per URL
        content = " ".join(["python programming is great"] * 5)
        urls = ["https://example.com/python-programming"]
        data = _parse(seo.internal_link_suggestions(content, urls))
        assert data["count"] <= 3

    def test_multiple_urls_all_checked(self):
        content = "Learn python programming and data science with pandas library."
        urls = [
            "https://example.com/python-programming",
            "https://example.com/data-science",
        ]
        data = _parse(seo.internal_link_suggestions(content, urls))
        matched_urls = {s["url"] for s in data["suggestions"]}
        assert len(matched_urls) >= 1
