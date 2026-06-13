"""Toolkit package — registers a broad library of agent tools.

The modules here hold plain functions. :func:`register_all` wraps them as
agent tools in the main :mod:`core.tools` registry. ``core.tools`` calls
:func:`register_all` at import time, so every agent that lists one of these
tool names can use it.
"""
from __future__ import annotations

import json
from typing import Any

from . import (
    affiliate,
    calendar_tools,
    code_tools,
    computer,
    content,
    crypto_trading,
    data_analysis,
    domain_tools,
    email_compose,
    files,
    finance,
    images,
    nlp,
    pdf_tools,
    pricing,
    research,
    scraper,
    seo,
    stock_tools,
    video,
)


def register_all(tool_decorator) -> None:  # noqa: C901
    """Register every toolkit function as an agent tool."""

    # =========================================================================
    # research
    # =========================================================================
    @tool_decorator("wikipedia", "wikipedia(topic) — Wikipedia lead summary.")
    def _wikipedia(agent: str, topic: str = "", **_: Any) -> str:
        return research.wikipedia_summary(topic)

    @tool_decorator("read_rss", "read_rss(feed_url) — recent items from an RSS/Atom feed.")
    def _read_rss(agent: str, feed_url: str = "", **_: Any) -> str:
        return research.read_rss(feed_url)

    @tool_decorator("hacker_news", "hacker_news() — top Hacker News stories.")
    def _hn(agent: str, **_: Any) -> str:
        return research.hacker_news_top()

    @tool_decorator("google_trends", "google_trends(geo?) — daily trending search topics.")
    def _trends(agent: str, geo: str = "US", **_: Any) -> str:
        return research.google_trends_rss(geo)

    @tool_decorator("reddit_top", "reddit_top(subreddit) — top posts of the day.")
    def _reddit_top(agent: str, subreddit: str = "all", **_: Any) -> str:
        return research.reddit_top(subreddit)

    # =========================================================================
    # finance
    # =========================================================================
    @tool_decorator("fx_rate", "fx_rate(base, quote) — fiat exchange rate.")
    def _fx(agent: str, base: str = "USD", quote: str = "EUR", **_: Any) -> str:
        return finance.fx_rate(base, quote)

    @tool_decorator("crypto_signal", "crypto_signal(coin, days?) — SMA+RSI buy/sell/hold signal.")
    def _signal(agent: str, coin: str = "bitcoin", days: int = 14, **_: Any) -> str:
        return finance.trend_signal(coin, int(days))

    # =========================================================================
    # content
    # =========================================================================
    @tool_decorator("analyze_text", "analyze_text(text) — readability, reading time, word count.")
    def _analyze(agent: str, text: str = "", **_: Any) -> str:
        return json.dumps({
            "word_count": content.word_count(text),
            "reading_time": content.reading_time(text),
            "readability": content.flesch_reading_ease(text),
        })

    @tool_decorator("keyword_density", "keyword_density(text) — top keywords and their density.")
    def _density(agent: str, text: str = "", **_: Any) -> str:
        return content.keyword_density(text)

    @tool_decorator("make_slug", "make_slug(title) — URL slug for a title.")
    def _slug(agent: str, title: str = "", **_: Any) -> str:
        return content.slugify(title)

    @tool_decorator("meta_description", "meta_description(text) — SEO meta description from body.")
    def _meta(agent: str, text: str = "", **_: Any) -> str:
        return content.meta_description(text)

    # =========================================================================
    # files
    # =========================================================================
    @tool_decorator("read_file", "read_file(rel_path) — read a file under output/.")
    def _read_file(agent: str, rel_path: str = "", **_: Any) -> str:
        return files.read_text(rel_path)

    @tool_decorator("list_files", "list_files(subdir?) — list files under output/.")
    def _list_files(agent: str, subdir: str = "", **_: Any) -> str:
        return files.list_output(subdir)

    @tool_decorator("append_csv", "append_csv(rel_path, row) — append a row to a CSV under output/.")
    def _append_csv(agent: str, rel_path: str = "", row: list | None = None, **_: Any) -> str:
        return files.append_csv_row(rel_path, row or [])

    # =========================================================================
    # connectors (publishing / selling)
    # =========================================================================
    @tool_decorator("connectors", "connectors() — list integration connectors and their status.")
    def _connectors(agent: str, **_: Any) -> str:
        from .. import connectors as conn
        return json.dumps(conn.configured())

    @tool_decorator("publish", "publish(connector, title, body) — publish via a blogging connector.")
    def _publish(agent: str, connector: str = "", title: str = "", body: str = "", **_: Any) -> str:
        from .. import connectors as conn
        from .. import database as db
        c = conn.get(connector)
        if c is None or "publish" not in c.capabilities:
            return f"ERROR: '{connector}' is not a publishing connector."
        result = c.publish(title, body)  # type: ignore[attr-defined]
        db.log_activity(agent, f"publish:{connector}", result.message[:120])
        return result.message

    @tool_decorator("post_social", "post_social(connector, message, subreddit?, title?) — post to social.")
    def _post_social(agent: str, connector: str = "", message: str = "", subreddit: str = "", title: str = "", **_: Any) -> str:
        from .. import connectors as conn
        from .. import database as db
        c = conn.get(connector)
        if c is None or "post" not in c.capabilities:
            return f"ERROR: '{connector}' is not a social connector."
        if connector == "reddit":
            result = c.post(subreddit or "test", title or message[:80], message)  # type: ignore[attr-defined]
        else:
            result = c.post(message)  # type: ignore[attr-defined]
        db.log_activity(agent, f"post:{connector}", result.message[:120])
        return result.message

    @tool_decorator("check_sales", "check_sales(connector) — read revenue from a commerce connector.")
    def _check_sales(agent: str, connector: str = "", **_: Any) -> str:
        from .. import connectors as conn
        c = conn.get(connector)
        if c is None:
            return f"ERROR: no connector '{connector}'."
        for method in ("sales_total", "revenue_today", "balance", "revenue"):
            fn = getattr(c, method, None)
            if fn:
                return fn().message
        return f"ERROR: '{connector}' has no sales capability."

    # =========================================================================
    # computer use (desktop)
    # =========================================================================
    @tool_decorator("screenshot", "screenshot(save_path?) — full-desktop screenshot. Requires DISPLAY.")
    def _screenshot(agent: str, save_path: str = "", **_: Any) -> str:
        try:
            return computer.screenshot(save_path)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("screen_size", "screen_size() — return screen dimensions {width, height}.")
    def _screen_size(agent: str, **_: Any) -> str:
        try:
            return computer.screen_size()
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("mouse_move", "mouse_move(x, y, duration?) — move mouse cursor to (x, y).")
    def _mouse_move(agent: str, x: int = 0, y: int = 0, duration: float = 0.3, **_: Any) -> str:
        try:
            return computer.mouse_move(x, y, duration)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("mouse_click", "mouse_click(x, y, button?, clicks?) — click at (x,y).")
    def _mouse_click(agent: str, x: int = 0, y: int = 0, button: str = "left", clicks: int = 1, **_: Any) -> str:
        try:
            return computer.mouse_click(x, y, button, clicks)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("mouse_double_click", "mouse_double_click(x, y) — double-click at (x, y).")
    def _mouse_dbl(agent: str, x: int = 0, y: int = 0, **_: Any) -> str:
        try:
            return computer.mouse_double_click(x, y)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("mouse_right_click", "mouse_right_click(x, y) — right-click at (x, y).")
    def _mouse_right(agent: str, x: int = 0, y: int = 0, **_: Any) -> str:
        try:
            return computer.mouse_right_click(x, y)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("mouse_drag", "mouse_drag(x1, y1, x2, y2, duration?) — click-and-drag.")
    def _mouse_drag(agent: str, x1: int = 0, y1: int = 0, x2: int = 0, y2: int = 0, duration: float = 0.5, **_: Any) -> str:
        try:
            return computer.mouse_drag(x1, y1, x2, y2, duration)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("mouse_scroll", "mouse_scroll(x, y, amount?, direction?) — scroll at (x,y).")
    def _mouse_scroll(agent: str, x: int = 0, y: int = 0, amount: int = 3, direction: str = "down", **_: Any) -> str:
        try:
            return computer.mouse_scroll(x, y, amount, direction)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("get_mouse_pos", "get_mouse_pos() — current mouse cursor position {x, y}.")
    def _mouse_pos(agent: str, **_: Any) -> str:
        try:
            return computer.get_mouse_pos()
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("keyboard_type", "keyboard_type(text, interval?) — type a string via keyboard.")
    def _kb_type(agent: str, text: str = "", interval: float = 0.01, **_: Any) -> str:
        try:
            return computer.keyboard_type(text, interval)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("keyboard_press", "keyboard_press(key) — press a single key (enter, escape, tab…).")
    def _kb_press(agent: str, key: str = "", **_: Any) -> str:
        try:
            return computer.keyboard_press(key)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("keyboard_shortcut", "keyboard_shortcut(keys) — press a combo e.g. 'ctrl+c'.")
    def _kb_shortcut(agent: str, keys: str = "", **_: Any) -> str:
        try:
            return computer.keyboard_shortcut(keys)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("keyboard_write_line", "keyboard_write_line(text) — type text and press Enter.")
    def _kb_writeline(agent: str, text: str = "", **_: Any) -> str:
        try:
            return computer.keyboard_write_line(text)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    # =========================================================================
    # browser (headless Playwright, per-agent sessions)
    # =========================================================================
    @tool_decorator("browser_open", "browser_open(url) — open URL in agent's headless browser.")
    def _br_open(agent: str, url: str = "", **_: Any) -> str:
        try:
            return computer.browser_open(agent, url)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_url", "browser_url() — current URL in the agent's browser.")
    def _br_url(agent: str, **_: Any) -> str:
        try:
            return computer.browser_url(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_title", "browser_title() — current page title.")
    def _br_title(agent: str, **_: Any) -> str:
        try:
            return computer.browser_title(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_click", "browser_click(selector) — click element by CSS selector.")
    def _br_click(agent: str, selector: str = "", **_: Any) -> str:
        try:
            return computer.browser_click(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_type", "browser_type(selector, text) — fill an input field.")
    def _br_type(agent: str, selector: str = "", text: str = "", **_: Any) -> str:
        try:
            return computer.browser_type(agent, selector, text)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_press", "browser_press(key) — send key to browser (Enter, Tab…).")
    def _br_press(agent: str, key: str = "Enter", **_: Any) -> str:
        try:
            return computer.browser_press(agent, key)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_scroll", "browser_scroll(direction?, px?) — scroll the browser page.")
    def _br_scroll(agent: str, direction: str = "down", px: int = 500, **_: Any) -> str:
        try:
            return computer.browser_scroll(agent, direction, px)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_text", "browser_text(selector?) — extract visible text from page.")
    def _br_text(agent: str, selector: str = "body", **_: Any) -> str:
        try:
            return computer.browser_text(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_html", "browser_html(selector?) — inner HTML of matching element.")
    def _br_html(agent: str, selector: str = "body", **_: Any) -> str:
        try:
            return computer.browser_html(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_links", "browser_links() — all external links on current page.")
    def _br_links(agent: str, **_: Any) -> str:
        try:
            return computer.browser_links(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_screenshot", "browser_screenshot(path?) — screenshot the browser page.")
    def _br_shot(agent: str, path: str = "", **_: Any) -> str:
        try:
            return computer.browser_screenshot(agent, path)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_js", "browser_js(script) — run JavaScript in browser, return JSON.")
    def _br_js(agent: str, script: str = "", **_: Any) -> str:
        try:
            return computer.browser_js(agent, script)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_wait", "browser_wait(selector, timeout_ms?) — wait for CSS element.")
    def _br_wait(agent: str, selector: str = "", timeout_ms: int = 10000, **_: Any) -> str:
        try:
            return computer.browser_wait(agent, selector, timeout_ms)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_fill_form", "browser_fill_form(fields) — fill multiple form fields {selector: value}.")
    def _br_form(agent: str, fields: dict | None = None, **_: Any) -> str:
        try:
            return computer.browser_fill_form(agent, fields)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_select", "browser_select(selector, value) — choose a <select> option.")
    def _br_select(agent: str, selector: str = "", value: str = "", **_: Any) -> str:
        try:
            return computer.browser_select(agent, selector, value)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_hover", "browser_hover(selector) — hover over an element.")
    def _br_hover(agent: str, selector: str = "", **_: Any) -> str:
        try:
            return computer.browser_hover(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_get_attribute", "browser_get_attribute(selector, attribute) — get element attributes as JSON list.")
    def _br_attr(agent: str, selector: str = "", attribute: str = "href", **_: Any) -> str:
        try:
            return computer.browser_get_attribute(agent, selector, attribute)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_close", "browser_close() — close the agent's browser session.")
    def _br_close(agent: str, **_: Any) -> str:
        try:
            return computer.browser_close(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_new_tab", "browser_new_tab() — open a new tab in the agent's browser.")
    def _br_new_tab(agent: str, **_: Any) -> str:
        try:
            return computer.browser_new_tab(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("active_browsers", "active_browsers() — list agents with open browser sessions.")
    def _active_browsers(agent: str, **_: Any) -> str:
        return computer.active_browsers()

    # =========================================================================
    # SEO toolkit
    # =========================================================================
    @tool_decorator("seo_analyze", "seo_analyze(url, keyword) — on-page SEO audit for a URL and target keyword.")
    def _seo_analyze(agent: str, url: str = "", keyword: str = "", **_: Any) -> str:
        return seo.analyze_on_page(url, keyword)

    @tool_decorator("keyword_ideas", "keyword_ideas(seed_keyword, num?) — generate keyword variations and long-tail ideas.")
    def _keyword_ideas(agent: str, seed_keyword: str = "", num: int = 20, **_: Any) -> str:
        return seo.keyword_ideas(seed_keyword, int(num))

    @tool_decorator("serp_rank", "serp_rank(keyword, domain) — check where a domain ranks for a keyword.")
    def _serp_rank(agent: str, keyword: str = "", domain: str = "", **_: Any) -> str:
        return seo.check_serp_rank(keyword, domain)

    @tool_decorator("competitor_seo", "competitor_seo(keyword, num_competitors?) — analyse top competitors for a keyword.")
    def _competitor_seo(agent: str, keyword: str = "", num_competitors: int = 5, **_: Any) -> str:
        return seo.competitor_analysis(keyword, int(num_competitors))

    @tool_decorator("generate_sitemap", "generate_sitemap(base_url, urls) — generate an XML sitemap.")
    def _gen_sitemap(agent: str, base_url: str = "", urls: list | None = None, **_: Any) -> str:
        return seo.generate_sitemap(base_url, urls or [])

    @tool_decorator("generate_robots", "generate_robots(allow?, disallow?) — generate a robots.txt.")
    def _gen_robots(agent: str, allow: list | None = None, disallow: list | None = None, **_: Any) -> str:
        return seo.generate_robots_txt(allow, disallow)

    @tool_decorator("json_ld_article", "json_ld_article(title, author, url, date_published?, description?) — JSON-LD schema for an article.")
    def _json_ld_article(agent: str, title: str = "", author: str = "", url: str = "", date_published: str = "", description: str = "", **_: Any) -> str:
        return seo.json_ld_article(title, author, url, date_published, description)

    @tool_decorator("json_ld_product", "json_ld_product(name, description, price, currency?, image?, url?) — JSON-LD for a product.")
    def _json_ld_product(agent: str, name: str = "", description: str = "", price: float = 0.0, currency: str = "USD", image: str = "", url: str = "", **_: Any) -> str:
        return seo.json_ld_product(name, description, float(price), currency, image, url)

    @tool_decorator("json_ld_faq", "json_ld_faq(questions_answers) — JSON-LD FAQ schema from [{question, answer}] list.")
    def _json_ld_faq(agent: str, questions_answers: list | None = None, **_: Any) -> str:
        return seo.json_ld_faq(questions_answers or [])

    @tool_decorator("extract_meta_tags", "extract_meta_tags(url) — extract title, meta description, OG tags from a URL.")
    def _extract_meta(agent: str, url: str = "", **_: Any) -> str:
        return seo.extract_meta_tags(url)

    @tool_decorator("internal_link_suggestions", "internal_link_suggestions(content, existing_urls) — suggest internal links for content.")
    def _internal_links(agent: str, content: str = "", existing_urls: list | None = None, **_: Any) -> str:
        return seo.internal_link_suggestions(content, existing_urls or [])

    @tool_decorator("page_speed_score", "page_speed_score(url) — measure page load metrics and performance score.")
    def _page_speed(agent: str, url: str = "", **_: Any) -> str:
        return seo.page_speed_score(url)

    @tool_decorator("backlink_opportunities", "backlink_opportunities(niche) — find guest post and link-building opportunities.")
    def _backlinks(agent: str, niche: str = "", **_: Any) -> str:
        return seo.backlink_opportunities(niche)

    @tool_decorator("heading_structure", "heading_structure(html) — extract H1–H6 heading hierarchy from HTML.")
    def _headings(agent: str, html: str = "", **_: Any) -> str:
        return seo.heading_structure(html)

    @tool_decorator("lsi_keywords", "lsi_keywords(keyword) — generate LSI/semantic keyword suggestions.")
    def _lsi(agent: str, keyword: str = "", **_: Any) -> str:
        return seo.lsi_keywords(keyword)

    # =========================================================================
    # Image toolkit
    # =========================================================================
    @tool_decorator("generate_image_dalle", "generate_image_dalle(prompt, size?, quality?) — generate image via DALL-E 3.")
    def _gen_dalle(agent: str, prompt: str = "", size: str = "1024x1024", quality: str = "standard", **_: Any) -> str:
        return images.generate_dalle(prompt, size, quality)

    @tool_decorator("generate_image_stability", "generate_image_stability(prompt, negative_prompt?, steps?, cfg_scale?) — Stability AI image.")
    def _gen_stability(agent: str, prompt: str = "", negative_prompt: str = "", steps: int = 30, cfg_scale: float = 7.0, **_: Any) -> str:
        return images.generate_stability(prompt, negative_prompt, int(steps), float(cfg_scale))

    @tool_decorator("fetch_unsplash", "fetch_unsplash(query, count?) — search Unsplash for free stock photos.")
    def _unsplash(agent: str, query: str = "", count: int = 3, **_: Any) -> str:
        return images.fetch_unsplash(query, int(count))

    @tool_decorator("resize_image", "resize_image(path, width, height, output_path?) — resize an image file.")
    def _resize_img(agent: str, path: str = "", width: int = 800, height: int = 600, output_path: str = "", **_: Any) -> str:
        return images.resize_image(path, int(width), int(height), output_path)

    @tool_decorator("create_thumbnail", "create_thumbnail(path, max_size?) — create a square thumbnail.")
    def _thumbnail(agent: str, path: str = "", max_size: int = 300, **_: Any) -> str:
        return images.create_thumbnail(path, int(max_size))

    @tool_decorator("add_text_overlay", "add_text_overlay(path, text, position?, font_size?, color?, bg_color?) — add text to image.")
    def _text_overlay(agent: str, path: str = "", text: str = "", position: str = "bottom", font_size: int = 36, color: str = "#FFFFFF", bg_color: str = "", **_: Any) -> str:
        return images.add_text_overlay(path, text, position, int(font_size), color, bg_color)

    @tool_decorator("convert_image_format", "convert_image_format(path, to_format?) — convert image format (webp, png, jpg…).")
    def _convert_img(agent: str, path: str = "", to_format: str = "webp", **_: Any) -> str:
        return images.convert_format(path, to_format)

    @tool_decorator("compress_image", "compress_image(path, quality?) — compress image to reduce file size.")
    def _compress_img(agent: str, path: str = "", quality: int = 85, **_: Any) -> str:
        return images.compress_image(path, int(quality))

    @tool_decorator("get_image_info", "get_image_info(path) — get image dimensions, format, mode and file size.")
    def _img_info(agent: str, path: str = "", **_: Any) -> str:
        return images.get_image_info(path)

    @tool_decorator("create_og_image", "create_og_image(title, subtitle?, bg_color?, width?, height?) — generate Open Graph social card image.")
    def _og_image(agent: str, title: str = "", subtitle: str = "", bg_color: str = "#1a1a2e", width: int = 1200, height: int = 630, **_: Any) -> str:
        return images.create_og_image(title, subtitle, bg_color, int(width), int(height))

    @tool_decorator("image_to_base64", "image_to_base64(path) — encode image as base64 data URI.")
    def _img_b64(agent: str, path: str = "", **_: Any) -> str:
        return images.image_to_base64(path)

    @tool_decorator("image_grid", "image_grid(paths, cols?, output_path?) — combine images into a grid collage.")
    def _img_grid(agent: str, paths: list | None = None, cols: int = 3, output_path: str = "", **_: Any) -> str:
        return images.image_grid(paths or [], int(cols), output_path)

    # =========================================================================
    # NLP toolkit
    # =========================================================================
    @tool_decorator("summarize_text", "summarize_text(text, max_sentences?) — extractive text summarization.")
    def _summarize(agent: str, text: str = "", max_sentences: int = 5, **_: Any) -> str:
        return nlp.summarize_text(text, int(max_sentences))

    @tool_decorator("extract_entities", "extract_entities(text) — extract named entities (people, places, orgs, dates).")
    def _entities(agent: str, text: str = "", **_: Any) -> str:
        return nlp.extract_entities(text)

    @tool_decorator("sentiment_analysis", "sentiment_analysis(text) — positive/negative/neutral sentiment score.")
    def _sentiment(agent: str, text: str = "", **_: Any) -> str:
        return nlp.sentiment_analysis(text)

    @tool_decorator("detect_language", "detect_language(text) — identify the language of text.")
    def _detect_lang(agent: str, text: str = "", **_: Any) -> str:
        return nlp.detect_language(text)

    @tool_decorator("translate_text", "translate_text(text, target_lang, source_lang?) — translate text to a target language.")
    def _translate(agent: str, text: str = "", target_lang: str = "en", source_lang: str = "auto", **_: Any) -> str:
        return nlp.translate_text(text, target_lang, source_lang)

    @tool_decorator("extract_keywords", "extract_keywords(text, num?) — extract top keywords using TF-IDF.")
    def _keywords(agent: str, text: str = "", num: int = 10, **_: Any) -> str:
        return nlp.extract_keywords(text, int(num))

    @tool_decorator("text_similarity", "text_similarity(text1, text2) — cosine similarity between two texts (0–1).")
    def _text_sim(agent: str, text1: str = "", text2: str = "", **_: Any) -> str:
        return nlp.text_similarity(text1, text2)

    @tool_decorator("classify_intent", "classify_intent(text) — classify user intent (question, complaint, purchase, etc.).")
    def _classify_intent(agent: str, text: str = "", **_: Any) -> str:
        return nlp.classify_intent(text)

    @tool_decorator("extract_action_items", "extract_action_items(text) — pull action items and tasks from meeting notes or emails.")
    def _action_items(agent: str, text: str = "", **_: Any) -> str:
        return nlp.extract_action_items(text)

    @tool_decorator("reading_grade_level", "reading_grade_level(text) — Flesch-Kincaid grade level and readability stats.")
    def _grade_level(agent: str, text: str = "", **_: Any) -> str:
        return nlp.reading_grade_level(text)

    @tool_decorator("detect_spam", "detect_spam(text) — spam probability score and trigger word analysis.")
    def _detect_spam(agent: str, text: str = "", **_: Any) -> str:
        return nlp.detect_spam(text)

    @tool_decorator("anonymize_text", "anonymize_text(text) — redact PII (names, emails, phones, SSNs) from text.")
    def _anonymize(agent: str, text: str = "", **_: Any) -> str:
        return nlp.anonymize_text(text)

    @tool_decorator("topic_classifier", "topic_classifier(text) — classify text into a topic category (tech, finance, health…).")
    def _topic(agent: str, text: str = "", **_: Any) -> str:
        return nlp.topic_classifier(text)

    @tool_decorator("word_frequency", "word_frequency(text, top_n?) — top N most frequent words in text.")
    def _word_freq(agent: str, text: str = "", top_n: int = 20, **_: Any) -> str:
        return nlp.word_frequency(text, int(top_n))

    # =========================================================================
    # Email compose toolkit
    # =========================================================================
    @tool_decorator("compose_email", "compose_email(subject, markdown_body, to, from_email?, unsubscribe_url?) — compose HTML email from markdown.")
    def _compose_email(agent: str, subject: str = "", markdown_body: str = "", to: str = "", from_email: str = "", unsubscribe_url: str = "", **_: Any) -> str:
        return email_compose.compose_html_email(subject, markdown_body, to, from_email, unsubscribe_url)

    @tool_decorator("send_email_smtp", "send_email_smtp(to, subject, html_body, from_email?, smtp_host?, smtp_port?, username?, password?) — send via SMTP.")
    def _send_smtp(agent: str, to: str = "", subject: str = "", html_body: str = "", from_email: str = "", smtp_host: str = "", smtp_port: int = 587, username: str = "", password: str = "", **_: Any) -> str:
        return email_compose.send_smtp(to, subject, html_body, from_email, smtp_host, int(smtp_port), username, password)

    @tool_decorator("send_email_sendgrid", "send_email_sendgrid(to, subject, html_body, from_email?, api_key?) — send via SendGrid.")
    def _send_sg(agent: str, to: str = "", subject: str = "", html_body: str = "", from_email: str = "", api_key: str = "", **_: Any) -> str:
        return email_compose.send_sendgrid(to, subject, html_body, from_email, api_key)

    @tool_decorator("send_email_mailgun", "send_email_mailgun(to, subject, html_body, from_email?, api_key?, domain?) — send via Mailgun.")
    def _send_mg(agent: str, to: str = "", subject: str = "", html_body: str = "", from_email: str = "", api_key: str = "", domain: str = "", **_: Any) -> str:
        return email_compose.send_mailgun(to, subject, html_body, from_email, api_key, domain)

    @tool_decorator("email_template_newsletter", "email_template_newsletter(title, intro, articles, cta_url?, cta_text?) — newsletter email template.")
    def _email_newsletter(agent: str, title: str = "", intro: str = "", articles: list | None = None, cta_url: str = "", cta_text: str = "", **_: Any) -> str:
        return email_compose.email_template_newsletter(title, intro, articles or [], cta_url, cta_text)

    @tool_decorator("email_template_welcome", "email_template_welcome(name, product_name, getting_started_steps?, cta_url?) — welcome email template.")
    def _email_welcome(agent: str, name: str = "", product_name: str = "", getting_started_steps: list | None = None, cta_url: str = "", **_: Any) -> str:
        return email_compose.email_template_welcome(name, product_name, getting_started_steps or [], cta_url)

    @tool_decorator("email_template_invoice", "email_template_invoice(invoice_data) — professional invoice email with line items.")
    def _email_invoice(agent: str, invoice_data: dict | None = None, **_: Any) -> str:
        return email_compose.email_template_invoice(invoice_data or {})

    @tool_decorator("validate_email_address", "validate_email_address(address) — validate email format and domain MX records.")
    def _validate_email(agent: str, address: str = "", **_: Any) -> str:
        result = email_compose.validate_email(address)
        return json.dumps(result)

    # =========================================================================
    # Video toolkit
    # =========================================================================
    @tool_decorator("youtube_search", "youtube_search(query, max_results?) — search YouTube videos.")
    def _yt_search(agent: str, query: str = "", max_results: int = 10, **_: Any) -> str:
        return video.youtube_search(query, int(max_results))

    @tool_decorator("youtube_video_details", "youtube_video_details(video_id) — get title, views, likes, description for a video.")
    def _yt_details(agent: str, video_id: str = "", **_: Any) -> str:
        return video.youtube_video_details(video_id)

    @tool_decorator("youtube_channel_stats", "youtube_channel_stats(channel_id) — subscriber count, total views, video count.")
    def _yt_channel(agent: str, channel_id: str = "", **_: Any) -> str:
        return video.youtube_channel_stats(channel_id)

    @tool_decorator("youtube_trending", "youtube_trending(region_code?, category_id?) — trending YouTube videos.")
    def _yt_trending(agent: str, region_code: str = "US", category_id: str = "0", **_: Any) -> str:
        return video.youtube_trending(region_code, category_id)

    @tool_decorator("youtube_transcript", "youtube_transcript(video_id) — extract auto-generated transcript from a YouTube video.")
    def _yt_transcript(agent: str, video_id: str = "", **_: Any) -> str:
        return video.extract_youtube_transcript(video_id)

    @tool_decorator("youtube_comments", "youtube_comments(video_id, max_results?) — top comments from a YouTube video.")
    def _yt_comments(agent: str, video_id: str = "", max_results: int = 50, **_: Any) -> str:
        return video.youtube_comments(video_id, int(max_results))

    @tool_decorator("youtube_cpm_estimate", "youtube_cpm_estimate(niche) — estimate CPM rates and ad revenue for a YouTube niche.")
    def _yt_cpm(agent: str, niche: str = "", **_: Any) -> str:
        return video.estimate_youtube_cpm(niche)

    @tool_decorator("video_script_outline", "video_script_outline(topic, duration_minutes?) — generate video script outline with hooks and CTAs.")
    def _video_script(agent: str, topic: str = "", duration_minutes: int = 10, **_: Any) -> str:
        return video.video_script_outline(topic, int(duration_minutes))

    @tool_decorator("youtube_monetization_check", "youtube_monetization_check(subscribers, watch_hours) — check YPP eligibility.")
    def _yt_monetize(agent: str, subscribers: int = 0, watch_hours: int = 0, **_: Any) -> str:
        return video.youtube_monetization_eligibility(int(subscribers), int(watch_hours))

    @tool_decorator("tiktok_trending_hashtags", "tiktok_trending_hashtags(region?) — trending TikTok hashtags by region.")
    def _tiktok_tags(agent: str, region: str = "US", **_: Any) -> str:
        return video.tiktok_trending_hashtags(region)

    @tool_decorator("shorts_ideas", "shorts_ideas(niche, num?) — generate YouTube Shorts / TikTok video ideas.")
    def _shorts(agent: str, niche: str = "", num: int = 10, **_: Any) -> str:
        return video.shorts_ideas(niche, int(num))

    @tool_decorator("calculate_ad_revenue", "calculate_ad_revenue(monthly_views, cpm?, ad_type?) — estimate channel ad revenue.")
    def _ad_revenue(agent: str, monthly_views: int = 0, cpm: float = 3.0, ad_type: str = "display", **_: Any) -> str:
        return video.calculate_ad_revenue(int(monthly_views), float(cpm), ad_type)

    # =========================================================================
    # Affiliate toolkit
    # =========================================================================
    @tool_decorator("amazon_search", "amazon_search(query, max_results?, category?) — search Amazon products for affiliate review.")
    def _amz_search(agent: str, query: str = "", max_results: int = 10, category: str = "", **_: Any) -> str:
        return affiliate.amazon_search_products(query, int(max_results), category)

    @tool_decorator("amazon_affiliate_link", "amazon_affiliate_link(asin, tag) — build Amazon affiliate link from ASIN.")
    def _amz_link(agent: str, asin: str = "", tag: str = "", **_: Any) -> str:
        return json.dumps(affiliate.amazon_affiliate_link(asin, tag))

    @tool_decorator("clickbank_marketplace", "clickbank_marketplace(category?, min_gravity?, sort_by?) — search ClickBank products.")
    def _clickbank(agent: str, category: str = "", min_gravity: float = 20.0, sort_by: str = "gravity", **_: Any) -> str:
        return affiliate.clickbank_marketplace(category, float(min_gravity), sort_by)

    @tool_decorator("affiliate_opportunity_score", "affiliate_opportunity_score(product_url, commission_rate?, niche?) — score an affiliate opportunity.")
    def _aff_score(agent: str, product_url: str = "", commission_rate: float = 0.0, niche: str = "", **_: Any) -> str:
        return affiliate.affiliate_opportunity_score(product_url, float(commission_rate), niche)

    @tool_decorator("affiliate_review_outline", "affiliate_review_outline(product_name, product_url?, niche?) — generate a product review outline.")
    def _review_outline(agent: str, product_name: str = "", product_url: str = "", niche: str = "", **_: Any) -> str:
        return affiliate.generate_review_outline(product_name, product_url, niche)

    @tool_decorator("comparison_table", "comparison_table(products) — generate an HTML product comparison table.")
    def _compare_table(agent: str, products: list | None = None, **_: Any) -> str:
        return affiliate.generate_comparison_table(products or [])

    @tool_decorator("estimate_affiliate_revenue", "estimate_affiliate_revenue(monthly_visitors, conversion_rate?, avg_commission?) — project affiliate income.")
    def _aff_revenue(agent: str, monthly_visitors: int = 0, conversion_rate: float = 0.02, avg_commission: float = 30.0, **_: Any) -> str:
        return affiliate.estimate_affiliate_revenue(int(monthly_visitors), float(conversion_rate), float(avg_commission))

    @tool_decorator("top_affiliate_niches", "top_affiliate_niches() — ranked list of highest-paying affiliate niches.")
    def _top_niches(agent: str, **_: Any) -> str:
        return json.dumps(affiliate.top_niches_for_affiliate())

    @tool_decorator("etsy_bestsellers", "etsy_bestsellers(category, num?) — scrape Etsy bestsellers in a category.")
    def _etsy(agent: str, category: str = "", num: int = 20, **_: Any) -> str:
        return json.dumps(affiliate.etsy_bestsellers(category, int(num)))

    # =========================================================================
    # Domain tools
    # =========================================================================
    @tool_decorator("domain_available", "domain_available(domain) — check if a domain name is available for registration.")
    def _domain_avail(agent: str, domain: str = "", **_: Any) -> str:
        return json.dumps(domain_tools.check_domain_available(domain))

    @tool_decorator("domain_suggestions", "domain_suggestions(keyword, extensions?) — suggest available domain names for a keyword.")
    def _domain_suggest(agent: str, keyword: str = "", extensions: list | None = None, **_: Any) -> str:
        return json.dumps(domain_tools.suggest_domain_names(keyword, extensions or []))

    @tool_decorator("whois_lookup", "whois_lookup(domain) — WHOIS registration data for a domain.")
    def _whois(agent: str, domain: str = "", **_: Any) -> str:
        return json.dumps(domain_tools.whois_lookup(domain))

    @tool_decorator("dns_lookup", "dns_lookup(domain, record_type?) — DNS records (A/CNAME/MX/TXT/NS).")
    def _dns(agent: str, domain: str = "", record_type: str = "A", **_: Any) -> str:
        return json.dumps(domain_tools.dns_lookup(domain, record_type))

    @tool_decorator("ssl_cert_check", "ssl_cert_check(domain) — SSL certificate validity, expiry, and issuer.")
    def _ssl(agent: str, domain: str = "", **_: Any) -> str:
        return json.dumps(domain_tools.check_ssl_cert(domain))

    @tool_decorator("page_load_time", "page_load_time(url) — measure actual page load time and size.")
    def _page_load(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(domain_tools.measure_page_load(url))

    @tool_decorator("check_broken_links", "check_broken_links(url, max_links?) — find broken links on a page.")
    def _broken_links(agent: str, url: str = "", max_links: int = 50, **_: Any) -> str:
        return json.dumps(domain_tools.check_broken_links(url, int(max_links)))

    @tool_decorator("estimate_domain_value", "estimate_domain_value(domain) — estimate a domain's resale value.")
    def _domain_value(agent: str, domain: str = "", **_: Any) -> str:
        return json.dumps(domain_tools.estimate_domain_value(domain))

    @tool_decorator("generate_business_names", "generate_business_names(keyword, industry?, style?) — brainstorm brandable business names.")
    def _biz_names(agent: str, keyword: str = "", industry: str = "", style: str = "modern", **_: Any) -> str:
        return json.dumps(domain_tools.generate_business_names(keyword, industry, style))

    @tool_decorator("website_tech_stack", "website_tech_stack(url) — detect CMS, frameworks, CDN, analytics on a website.")
    def _tech_stack(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(domain_tools.website_technology_stack(url))

    # =========================================================================
    # Stock tools
    # =========================================================================
    @tool_decorator("stock_price", "stock_price(symbol) — current price, market cap, P/E ratio for a stock ticker.")
    def _stock_price(agent: str, symbol: str = "", **_: Any) -> str:
        return json.dumps(stock_tools.stock_price(symbol))

    @tool_decorator("stock_history", "stock_history(symbol, days?) — historical OHLCV price data.")
    def _stock_hist(agent: str, symbol: str = "", days: int = 30, **_: Any) -> str:
        return json.dumps(stock_tools.stock_history(symbol, int(days)))

    @tool_decorator("stock_fundamentals", "stock_fundamentals(symbol) — EPS, revenue, debt, dividend yield.")
    def _stock_fund(agent: str, symbol: str = "", **_: Any) -> str:
        return json.dumps(stock_tools.fundamental_data(symbol))

    @tool_decorator("earnings_calendar", "earnings_calendar(days_ahead?) — upcoming earnings report dates.")
    def _earnings_cal(agent: str, days_ahead: int = 14, **_: Any) -> str:
        return json.dumps(stock_tools.earnings_calendar(int(days_ahead)))

    @tool_decorator("sector_performance", "sector_performance() — today's S&P 500 sector performance.")
    def _sector_perf(agent: str, **_: Any) -> str:
        return json.dumps(stock_tools.sector_performance())

    @tool_decorator("stock_screener", "stock_screener(criteria) — filter stocks by criteria {market_cap_min, pe_max, dividend_min…}.")
    def _screener(agent: str, criteria: dict | None = None, **_: Any) -> str:
        return json.dumps(stock_tools.stock_screener(criteria or {}))

    @tool_decorator("technical_indicators", "technical_indicators(symbol, period?) — SMA/EMA/RSI/MACD/Bollinger Bands.")
    def _tech_ind(agent: str, symbol: str = "", period: int = 14, **_: Any) -> str:
        return json.dumps(stock_tools.compute_technical_indicators(symbol, int(period)))

    @tool_decorator("analyst_ratings", "analyst_ratings(symbol) — Wall Street analyst buy/hold/sell consensus.")
    def _analyst(agent: str, symbol: str = "", **_: Any) -> str:
        return json.dumps(stock_tools.analyst_ratings(symbol))

    @tool_decorator("stock_news", "stock_news(symbol, limit?) — latest news articles for a stock.")
    def _stock_news(agent: str, symbol: str = "", limit: int = 10, **_: Any) -> str:
        return json.dumps(stock_tools.stock_news(symbol, int(limit)))

    # =========================================================================
    # Crypto trading toolkit
    # =========================================================================
    @tool_decorator("top_coins", "top_coins(limit?) — top N coins by market cap with price and 24h change.")
    def _top_coins(agent: str, limit: int = 50, **_: Any) -> str:
        return json.dumps(crypto_trading.top_coins(int(limit)))

    @tool_decorator("coin_detail", "coin_detail(coin_id) — detailed metrics for a single coin (market cap, ATH, links).")
    def _coin_detail(agent: str, coin_id: str = "", **_: Any) -> str:
        return json.dumps(crypto_trading.coin_detail(coin_id))

    @tool_decorator("fear_greed_index", "fear_greed_index() — Crypto Fear & Greed Index (0=extreme fear, 100=extreme greed).")
    def _fear_greed(agent: str, **_: Any) -> str:
        return json.dumps(crypto_trading.fear_greed_index())

    @tool_decorator("defi_protocols", "defi_protocols(limit?) — top DeFi protocols by TVL.")
    def _defi(agent: str, limit: int = 30, **_: Any) -> str:
        return json.dumps(crypto_trading.defi_protocols(int(limit)))

    @tool_decorator("yield_opportunities", "yield_opportunities(min_apy?, chain?) — DeFi yield farming opportunities above min APY.")
    def _yield_opps(agent: str, min_apy: float = 10.0, chain: str = "", **_: Any) -> str:
        return json.dumps(crypto_trading.yield_opportunities(float(min_apy), chain))

    @tool_decorator("nft_collection_stats", "nft_collection_stats(collection_slug) — floor price, volume, owners for an NFT collection.")
    def _nft_stats(agent: str, collection_slug: str = "", **_: Any) -> str:
        return json.dumps(crypto_trading.nft_collection_stats(collection_slug))

    @tool_decorator("whale_alerts", "whale_alerts(min_usd?) — large crypto transactions above threshold.")
    def _whales(agent: str, min_usd: float = 1_000_000, **_: Any) -> str:
        return json.dumps(crypto_trading.whale_alerts(float(min_usd)))

    @tool_decorator("gas_prices", "gas_prices() — current Ethereum gas prices (slow/average/fast).")
    def _gas(agent: str, **_: Any) -> str:
        return json.dumps(crypto_trading.gas_prices())

    @tool_decorator("dex_volume", "dex_volume(chain?) — top DEX trading volume on a chain.")
    def _dex_vol(agent: str, chain: str = "ethereum", **_: Any) -> str:
        return json.dumps(crypto_trading.dex_volume(chain))

    @tool_decorator("crypto_correlation", "crypto_correlation(coins) — price correlation matrix for a list of coins.")
    def _crypto_corr(agent: str, coins: list | None = None, **_: Any) -> str:
        return json.dumps(crypto_trading.crypto_correlation_matrix(coins or ["bitcoin", "ethereum"]))

    @tool_decorator("arbitrage_opportunities", "arbitrage_opportunities(coins?, min_spread_pct?) — spot price discrepancies across exchanges.")
    def _arb(agent: str, coins: list | None = None, min_spread_pct: float = 0.5, **_: Any) -> str:
        return json.dumps(crypto_trading.arbitrage_opportunities(coins or [], float(min_spread_pct)))

    # =========================================================================
    # PDF toolkit
    # =========================================================================
    @tool_decorator("create_pdf_report", "create_pdf_report(title, sections, output_path?) — generate a styled PDF report.")
    def _pdf_report(agent: str, title: str = "", sections: list | None = None, output_path: str = "", **_: Any) -> str:
        return pdf_tools.create_pdf_report(title, sections or [], output_path)

    @tool_decorator("create_pdf_invoice", "create_pdf_invoice(invoice_data, output_path?) — generate a professional PDF invoice.")
    def _pdf_invoice(agent: str, invoice_data: dict | None = None, output_path: str = "", **_: Any) -> str:
        return pdf_tools.create_pdf_invoice(invoice_data or {}, output_path)

    @tool_decorator("create_pdf_ebook", "create_pdf_ebook(title, chapters, author?, output_path?) — compile chapters into a PDF ebook.")
    def _pdf_ebook(agent: str, title: str = "", chapters: list | None = None, author: str = "", output_path: str = "", **_: Any) -> str:
        return pdf_tools.create_pdf_ebook(title, chapters or [], author, output_path)

    @tool_decorator("parse_pdf_text", "parse_pdf_text(path) — extract text from a PDF file.")
    def _pdf_parse(agent: str, path: str = "", **_: Any) -> str:
        return pdf_tools.parse_pdf_text(path)

    @tool_decorator("merge_pdfs", "merge_pdfs(input_paths, output_path) — merge multiple PDFs into one.")
    def _pdf_merge(agent: str, input_paths: list | None = None, output_path: str = "", **_: Any) -> str:
        return pdf_tools.merge_pdfs(input_paths or [], output_path)

    @tool_decorator("html_to_pdf", "html_to_pdf(html_content, output_path?) — convert HTML content to PDF.")
    def _html_pdf(agent: str, html_content: str = "", output_path: str = "", **_: Any) -> str:
        return pdf_tools.html_to_pdf(html_content, output_path)

    @tool_decorator("generate_qr_code", "generate_qr_code(data, output_path?) — generate a QR code image.")
    def _qr_code(agent: str, data: str = "", output_path: str = "", **_: Any) -> str:
        return pdf_tools.generate_qr_code(data, output_path)

    @tool_decorator("create_simple_pdf", "create_simple_pdf(title, body, output_path?) — create a simple single-page PDF from text.")
    def _simple_pdf(agent: str, title: str = "", body: str = "", output_path: str = "", **_: Any) -> str:
        return pdf_tools.create_simple_pdf(title, body, output_path)

    # =========================================================================
    # Code tools
    # =========================================================================
    @tool_decorator("analyze_python_file", "analyze_python_file(path) — AST analysis: functions, classes, imports, complexity.")
    def _analyze_py(agent: str, path: str = "", **_: Any) -> str:
        return code_tools.analyze_python_file(path)

    @tool_decorator("find_todos", "find_todos(path_or_dir) — find TODO/FIXME/HACK comments in code.")
    def _find_todos(agent: str, path_or_dir: str = ".", **_: Any) -> str:
        return code_tools.find_todos(path_or_dir)

    @tool_decorator("count_lines_of_code", "count_lines_of_code(path_or_dir, extensions?) — count code/blank/comment lines.")
    def _cloc(agent: str, path_or_dir: str = ".", extensions: list | None = None, **_: Any) -> str:
        return code_tools.count_lines_of_code(path_or_dir, extensions)

    @tool_decorator("check_syntax", "check_syntax(code, language?) — check Python or JS syntax for errors.")
    def _check_syntax(agent: str, code: str = "", language: str = "python", **_: Any) -> str:
        return code_tools.check_syntax(code, language)

    @tool_decorator("generate_docstring", "generate_docstring(function_code) — auto-generate Google-style docstring for a function.")
    def _gen_docstring(agent: str, function_code: str = "", **_: Any) -> str:
        return code_tools.generate_docstring(function_code)

    @tool_decorator("extract_api_endpoints", "extract_api_endpoints(path_or_dir) — find FastAPI/Flask/Django endpoints in code.")
    def _api_endpoints(agent: str, path_or_dir: str = ".", **_: Any) -> str:
        return code_tools.extract_api_endpoints(path_or_dir)

    @tool_decorator("github_repo_stats", "github_repo_stats(owner, repo, token?) — stars, forks, issues, license.")
    def _gh_stats(agent: str, owner: str = "", repo: str = "", token: str = "", **_: Any) -> str:
        return code_tools.github_repo_stats(owner, repo, token)

    @tool_decorator("github_search_repos", "github_search_repos(query, sort?, limit?) — search GitHub repositories.")
    def _gh_search(agent: str, query: str = "", sort: str = "stars", limit: int = 10, **_: Any) -> str:
        return code_tools.github_search_repos(query, sort, int(limit))

    @tool_decorator("github_trending", "github_trending(language?, since?) — trending GitHub repos today/weekly.")
    def _gh_trending(agent: str, language: str = "python", since: str = "daily", **_: Any) -> str:
        return code_tools.github_trending(language, since)

    @tool_decorator("npm_package_info", "npm_package_info(package_name) — npm package stats, version, weekly downloads.")
    def _npm_info(agent: str, package_name: str = "", **_: Any) -> str:
        return code_tools.npm_package_info(package_name)

    @tool_decorator("pypi_package_info", "pypi_package_info(package_name) — PyPI package stats and download counts.")
    def _pypi_info(agent: str, package_name: str = "", **_: Any) -> str:
        return code_tools.pypi_package_info(package_name)

    @tool_decorator("detect_secrets", "detect_secrets(path) — scan code for leaked API keys, tokens, credentials.")
    def _detect_secrets(agent: str, path: str = ".", **_: Any) -> str:
        return code_tools.detect_secrets(path)

    @tool_decorator("generate_readme", "generate_readme(project_name, description, features?, install_steps?, usage_example?) — generate README.md template.")
    def _gen_readme(agent: str, project_name: str = "", description: str = "", features: list | None = None, install_steps: list | None = None, usage_example: str = "", **_: Any) -> str:
        return code_tools.generate_readme_template(project_name, description, features or [], install_steps or [], usage_example)

    @tool_decorator("lint_python", "lint_python(code) — AST-based Python linting for common issues.")
    def _lint_py(agent: str, code: str = "", **_: Any) -> str:
        return code_tools.lint_python(code)

    # =========================================================================
    # Calendar / scheduling tools
    # =========================================================================
    @tool_decorator("optimal_posting_times", "optimal_posting_times(platform, timezone?) — best times to post on a platform.")
    def _post_times(agent: str, platform: str = "", timezone: str = "UTC", **_: Any) -> str:
        return json.dumps(calendar_tools.optimal_posting_times(platform, timezone))

    @tool_decorator("content_calendar_week", "content_calendar_week(team, topics, start_date?) — weekly content calendar with assignments.")
    def _cal_week(agent: str, team: list | None = None, topics: list | None = None, start_date: str = "", **_: Any) -> str:
        return json.dumps(calendar_tools.content_calendar_week(team or [], topics or [], start_date))

    @tool_decorator("content_calendar_month", "content_calendar_month(team, strategy_notes?) — full month content calendar.")
    def _cal_month(agent: str, team: list | None = None, strategy_notes: str = "", **_: Any) -> str:
        return json.dumps(calendar_tools.content_calendar_month(team or [], strategy_notes))

    @tool_decorator("calculate_posting_schedule", "calculate_posting_schedule(platforms, start_date?, posts_per_week?) — posting schedule across platforms.")
    def _post_schedule(agent: str, platforms: list | None = None, start_date: str = "", posts_per_week: int = 5, **_: Any) -> str:
        return json.dumps(calendar_tools.calculate_posting_schedule(platforms or [], start_date, int(posts_per_week)))

    @tool_decorator("marketing_seasons", "marketing_seasons() — upcoming holidays and marketing opportunities with dates.")
    def _mkt_seasons(agent: str, **_: Any) -> str:
        return json.dumps(calendar_tools.marketing_seasons())

    @tool_decorator("hashtag_strategy", "hashtag_strategy(topic, platform, count?) — recommended hashtags for a topic/platform.")
    def _hashtags(agent: str, topic: str = "", platform: str = "", count: int = 10, **_: Any) -> str:
        return json.dumps(calendar_tools.hashtag_strategy(topic, platform, int(count)))

    @tool_decorator("editorial_themes", "editorial_themes(month, niche) — monthly editorial content themes and ideas.")
    def _ed_themes(agent: str, month: str = "", niche: str = "", **_: Any) -> str:
        return json.dumps(calendar_tools.editorial_themes(month, niche))

    @tool_decorator("content_repurposing_plan", "content_repurposing_plan(original_content_type, platform) — plan to repurpose content across channels.")
    def _repurpose(agent: str, original_content_type: str = "", platform: str = "", **_: Any) -> str:
        return json.dumps(calendar_tools.content_repurposing_plan(original_content_type, platform))

    @tool_decorator("publishing_checklist", "publishing_checklist(content_type) — pre-publish QA checklist for a content type.")
    def _pub_checklist(agent: str, content_type: str = "blog", **_: Any) -> str:
        return json.dumps(calendar_tools.publishing_checklist(content_type))

    # =========================================================================
    # Data analysis toolkit
    # =========================================================================
    @tool_decorator("load_csv", "load_csv(path) — load a CSV file and show preview with column types.")
    def _load_csv(agent: str, path: str = "", **_: Any) -> str:
        return data_analysis.load_csv(path)

    @tool_decorator("summarize_csv", "summarize_csv(path) — statistical summary (mean, min, max, nulls) for each column.")
    def _summarize_csv(agent: str, path: str = "", **_: Any) -> str:
        return data_analysis.summarize_csv(path)

    @tool_decorator("filter_csv", "filter_csv(path, column, operator, value) — filter CSV rows. operator: eq|ne|gt|lt|contains.")
    def _filter_csv(agent: str, path: str = "", column: str = "", operator: str = "eq", value: str = "", **_: Any) -> str:
        return data_analysis.filter_csv(path, column, operator, value)

    @tool_decorator("sort_csv", "sort_csv(path, column, ascending?) — sort a CSV by column.")
    def _sort_csv(agent: str, path: str = "", column: str = "", ascending: bool = True, **_: Any) -> str:
        return data_analysis.sort_csv(path, column, bool(ascending))

    @tool_decorator("group_and_count", "group_and_count(path, column) — count rows grouped by a column value.")
    def _group_count(agent: str, path: str = "", column: str = "", **_: Any) -> str:
        return data_analysis.group_and_count(path, column)

    @tool_decorator("compute_stats", "compute_stats(numbers) — mean, median, std, variance, percentiles for a list.")
    def _stats(agent: str, numbers: list | None = None, **_: Any) -> str:
        return data_analysis.compute_stats([float(x) for x in (numbers or [])])

    @tool_decorator("detect_trend", "detect_trend(values) — linear regression trend direction and strength for a time series.")
    def _detect_trend(agent: str, values: list | None = None, **_: Any) -> str:
        return data_analysis.detect_trend([float(x) for x in (values or [])])

    @tool_decorator("json_to_csv", "json_to_csv(json_str, output_path) — convert JSON array to CSV file.")
    def _json_csv(agent: str, json_str: str = "", output_path: str = "", **_: Any) -> str:
        return data_analysis.json_to_csv(json_str, output_path)

    @tool_decorator("moving_average", "moving_average(values, window) — calculate moving average over a rolling window.")
    def _moving_avg(agent: str, values: list | None = None, window: int = 7, **_: Any) -> str:
        return data_analysis.moving_average([float(x) for x in (values or [])], int(window))

    @tool_decorator("find_outliers", "find_outliers(values, method?) — detect statistical outliers (iqr or zscore).")
    def _outliers(agent: str, values: list | None = None, method: str = "iqr", **_: Any) -> str:
        return data_analysis.find_outliers([float(x) for x in (values or [])], method)

    @tool_decorator("generate_chart_data", "generate_chart_data(labels, datasets, chart_type?) — Chart.js-compatible data for line/bar/pie.")
    def _chart_data(agent: str, labels: list | None = None, datasets: list | None = None, chart_type: str = "line", **_: Any) -> str:
        return data_analysis.generate_chart_data(labels or [], datasets or [], chart_type)

    # =========================================================================
    # Scraper toolkit
    # =========================================================================
    @tool_decorator("scrape_paginated", "scrape_paginated(base_url, item_selector, max_pages?) — scrape multiple pages of listings.")
    def _scrape_pages(agent: str, base_url: str = "", item_selector: str = "", max_pages: int = 5, **_: Any) -> str:
        return json.dumps(scraper.scrape_paginated(base_url, item_selector, int(max_pages)))

    @tool_decorator("scrape_table", "scrape_table(url, table_index?) — extract HTML table as JSON.")
    def _scrape_table(agent: str, url: str = "", table_index: int = 0, **_: Any) -> str:
        return json.dumps(scraper.scrape_table(url, int(table_index)))

    @tool_decorator("scrape_product_price", "scrape_product_price(url) — extract product name, price, and currency from a product page.")
    def _scrape_price(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(scraper.scrape_product_price(url))

    @tool_decorator("scrape_emails", "scrape_emails(url) — extract email addresses from a page.")
    def _scrape_emails(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(scraper.scrape_emails(url))

    @tool_decorator("scrape_social_links", "scrape_social_links(url) — extract social media profile links from a page.")
    def _scrape_social(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(scraper.scrape_social_links(url))

    @tool_decorator("scrape_news_articles", "scrape_news_articles(url) — extract article headlines and links from a news page.")
    def _scrape_news(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(scraper.scrape_news_articles(url))

    @tool_decorator("crawl_sitemap", "crawl_sitemap(sitemap_url, max_urls?) — fetch and parse a sitemap.xml.")
    def _crawl_sitemap(agent: str, sitemap_url: str = "", max_urls: int = 100, **_: Any) -> str:
        return json.dumps(scraper.crawl_sitemap(sitemap_url, int(max_urls)))

    @tool_decorator("check_url_status", "check_url_status(urls) — batch HTTP status check for a list of URLs.")
    def _url_status(agent: str, urls: list | None = None, **_: Any) -> str:
        return json.dumps(scraper.check_url_status(urls or []))

    @tool_decorator("scrape_job_posting", "scrape_job_posting(url) — extract title, company, location, salary from a job listing.")
    def _scrape_job(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(scraper.scrape_job_posting(url))

    @tool_decorator("monitor_page_change", "monitor_page_change(url, previous_hash?) — detect if a page has changed since last check.")
    def _monitor_page(agent: str, url: str = "", previous_hash: str = "", **_: Any) -> str:
        return json.dumps(scraper.monitor_page_change(url, previous_hash))

    @tool_decorator("extract_contact_info", "extract_contact_info(url) — extract emails, phones, addresses, social from a business page.")
    def _contact_info(agent: str, url: str = "", **_: Any) -> str:
        return json.dumps(scraper.extract_contact_info(url))

    # =========================================================================
    # Pricing toolkit
    # =========================================================================
    @tool_decorator("competitive_pricing", "competitive_pricing(competitor_prices, target_position?) — suggest price based on market analysis.")
    def _comp_pricing(agent: str, competitor_prices: list | None = None, target_position: str = "mid", **_: Any) -> str:
        return pricing.competitive_pricing(competitor_prices or [], target_position)

    @tool_decorator("psychological_pricing", "psychological_pricing(target_price) — charm pricing, prestige pricing, five-ending variants.")
    def _psych_price(agent: str, target_price: float = 0.0, **_: Any) -> str:
        return pricing.psychological_pricing(float(target_price))

    @tool_decorator("price_elasticity", "price_elasticity(price_history, quantity_history) — calculate demand elasticity.")
    def _elasticity(agent: str, price_history: list | None = None, quantity_history: list | None = None, **_: Any) -> str:
        return pricing.price_elasticity(price_history or [], quantity_history or [])

    @tool_decorator("value_based_pricing", "value_based_pricing(cost, competitor_avg, unique_value_score?) — value-anchored pricing recommendation.")
    def _value_price(agent: str, cost: float = 0.0, competitor_avg: float = 0.0, unique_value_score: float = 5.0, **_: Any) -> str:
        return pricing.value_based_pricing(float(cost), float(competitor_avg), float(unique_value_score))

    @tool_decorator("dynamic_pricing_signal", "dynamic_pricing_signal(current_price, inventory_level, demand_score?, competitor_price?) — raise/lower/hold signal.")
    def _dyn_price(agent: str, current_price: float = 0.0, inventory_level: int = 100, demand_score: float = 5.0, competitor_price: float = 0.0, **_: Any) -> str:
        return pricing.dynamic_pricing_signal(float(current_price), int(inventory_level), float(demand_score), float(competitor_price))

    @tool_decorator("lifetime_value", "lifetime_value(mrr, churn_rate, gross_margin?) — calculate customer LTV.")
    def _ltv(agent: str, mrr: float = 0.0, churn_rate: float = 0.05, gross_margin: float = 0.7, **_: Any) -> str:
        return pricing.lifetime_value(float(mrr), float(churn_rate), float(gross_margin))

    @tool_decorator("break_even_analysis", "break_even_analysis(fixed_costs, variable_cost_per_unit, price_per_unit) — break-even units and revenue.")
    def _break_even(agent: str, fixed_costs: float = 0.0, variable_cost_per_unit: float = 0.0, price_per_unit: float = 0.0, **_: Any) -> str:
        return pricing.break_even_analysis(float(fixed_costs), float(variable_cost_per_unit), float(price_per_unit))

    @tool_decorator("pricing_page_html", "pricing_page_html(plans) — generate a self-contained HTML pricing table.")
    def _pricing_page(agent: str, plans: list | None = None, **_: Any) -> str:
        return pricing.pricing_page_templates(plans or [])

    @tool_decorator("subscription_metrics", "subscription_metrics(mrr_history, churn_events?) — MRR growth, quick ratio, churn health.")
    def _sub_metrics(agent: str, mrr_history: list | None = None, churn_events: list | None = None, **_: Any) -> str:
        return pricing.subscription_metrics(mrr_history or [], churn_events or [])

    @tool_decorator("anchor_pricing", "anchor_pricing(premium_price, target_price) — anchor pricing analysis and copy suggestions.")
    def _anchor(agent: str, premium_price: float = 0.0, target_price: float = 0.0, **_: Any) -> str:
        return pricing.anchor_pricing(float(premium_price), float(target_price))
