"""Toolkit package — registers a broad library of agent tools.

The modules here (research, finance, content, files, computer) hold plain
functions. :func:`register_all` wraps them as agent tools in the main
:mod:`core.tools` registry, plus adds connector-backed publishing/selling tools.
``core.tools`` calls :func:`register_all` at import time, so every agent that
lists one of these tool names can use it.
"""
from __future__ import annotations

import json
from typing import Any

from . import computer, content, files, finance, research


def register_all(tool_decorator) -> None:
    """Register every toolkit function as an agent tool.

    ``tool_decorator`` is :func:`core.tools.tool` — passed in to avoid a circular
    import (tools imports toolkit, toolkit registers into tools).
    """

    # -- research ----------------------------------------------------------
    @tool_decorator("wikipedia", "wikipedia(topic) — lead summary of a Wikipedia article.")
    def _wikipedia(agent: str, topic: str = "", **_: Any) -> str:
        return research.wikipedia_summary(topic)

    @tool_decorator("read_rss", "read_rss(feed_url) — recent items from an RSS/Atom feed.")
    def _read_rss(agent: str, feed_url: str = "", **_: Any) -> str:
        return research.read_rss(feed_url)

    @tool_decorator("hacker_news", "hacker_news() — top Hacker News stories (tech/startup pulse).")
    def _hn(agent: str, **_: Any) -> str:
        return research.hacker_news_top()

    @tool_decorator("google_trends", "google_trends(geo) — daily trending search topics.")
    def _trends(agent: str, geo: str = "US", **_: Any) -> str:
        return research.google_trends_rss(geo)

    @tool_decorator("reddit_top", "reddit_top(subreddit) — top posts of the day from a subreddit (no auth).")
    def _reddit_top(agent: str, subreddit: str = "all", **_: Any) -> str:
        return research.reddit_top(subreddit)

    # -- finance -----------------------------------------------------------
    @tool_decorator("fx_rate", "fx_rate(base, quote) — fiat exchange rate.")
    def _fx(agent: str, base: str = "USD", quote: str = "EUR", **_: Any) -> str:
        return finance.fx_rate(base, quote)

    @tool_decorator("crypto_signal", "crypto_signal(coin, days) — SMA+RSI buy/sell/hold signal for a coin.")
    def _signal(agent: str, coin: str = "bitcoin", days: int = 14, **_: Any) -> str:
        return finance.trend_signal(coin, int(days))

    # -- content -----------------------------------------------------------
    @tool_decorator("analyze_text", "analyze_text(text) — readability, reading time and word count.")
    def _analyze(agent: str, text: str = "", **_: Any) -> str:
        return json.dumps(
            {
                "word_count": content.word_count(text),
                "reading_time": content.reading_time(text),
                "readability": content.flesch_reading_ease(text),
            }
        )

    @tool_decorator("keyword_density", "keyword_density(text) — top keywords and their density.")
    def _density(agent: str, text: str = "", **_: Any) -> str:
        return content.keyword_density(text)

    @tool_decorator("make_slug", "make_slug(title) — URL slug for a title.")
    def _slug(agent: str, title: str = "", **_: Any) -> str:
        return content.slugify(title)

    @tool_decorator("meta_description", "meta_description(text) — SEO meta description from body text.")
    def _meta(agent: str, text: str = "", **_: Any) -> str:
        return content.meta_description(text)

    # -- files -------------------------------------------------------------
    @tool_decorator("read_file", "read_file(rel_path) — read a file under output/.")
    def _read_file(agent: str, rel_path: str = "", **_: Any) -> str:
        return files.read_text(rel_path)

    @tool_decorator("list_files", "list_files(subdir?) — list files under output/.")
    def _list_files(agent: str, subdir: str = "", **_: Any) -> str:
        return files.list_output(subdir)

    @tool_decorator("append_csv", "append_csv(rel_path, row) — append a row (list) to a CSV under output/.")
    def _append_csv(agent: str, rel_path: str = "", row: list | None = None, **_: Any) -> str:
        return files.append_csv_row(rel_path, row or [])

    # -- connectors (publishing / selling) --------------------------------
    @tool_decorator("connectors", "connectors() — list integration connectors and whether each is configured.")
    def _connectors(agent: str, **_: Any) -> str:
        from .. import connectors as conn

        return json.dumps(conn.configured())

    @tool_decorator("publish", "publish(connector, title, body) — publish content via a blogging connector (wordpress/ghost/devto/hashnode/medium).")
    def _publish(agent: str, connector: str = "", title: str = "", body: str = "", **_: Any) -> str:
        from .. import connectors as conn
        from .. import database as db

        c = conn.get(connector)
        if c is None or "publish" not in c.capabilities:
            return f"ERROR: '{connector}' is not a publishing connector. Try /connectors."
        result = c.publish(title, body)  # type: ignore[attr-defined]
        db.log_activity(agent, f"publish:{connector}", result.message[:120])
        return result.message

    @tool_decorator("post_social", "post_social(connector, message, subreddit?, title?) — post via a social connector (telegram/discord/slack/mastodon/reddit/twitter).")
    def _post_social(agent: str, connector: str = "", message: str = "", subreddit: str = "", title: str = "", **_: Any) -> str:
        from .. import connectors as conn
        from .. import database as db

        c = conn.get(connector)
        if c is None or "post" not in c.capabilities:
            return f"ERROR: '{connector}' is not a social connector. Try /connectors."
        if connector == "reddit":
            result = c.post(subreddit or "test", title or message[:80], message)  # type: ignore[attr-defined]
        else:
            result = c.post(message)  # type: ignore[attr-defined]
        db.log_activity(agent, f"post:{connector}", result.message[:120])
        return result.message

    @tool_decorator("check_sales", "check_sales(connector) — read sales/revenue from a commerce connector (gumroad/shopify/stripe/lemonsqueezy).")
    def _check_sales(agent: str, connector: str = "", **_: Any) -> str:
        from .. import connectors as conn

        c = conn.get(connector)
        if c is None:
            return f"ERROR: no connector '{connector}'."
        for method in ("sales_total", "revenue_today", "balance", "revenue"):
            fn = getattr(c, method, None)
            if fn:
                return fn().message
        return f"ERROR: '{connector}' has no sales-reading capability."

    # -- computer use (desktop + browser) ----------------------------------
    @tool_decorator(
        "screenshot",
        "screenshot(save_path?) — take a full-desktop screenshot, return the file path. "
        "Requires a display (DISPLAY env var). Use browser_screenshot for headless servers.",
    )
    def _screenshot(agent: str, save_path: str = "", **_: Any) -> str:
        try:
            return computer.screenshot(save_path)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "screen_size",
        "screen_size() — return screen dimensions {width, height}.",
    )
    def _screen_size(agent: str, **_: Any) -> str:
        try:
            return computer.screen_size()
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "mouse_move",
        "mouse_move(x, y, duration?) — move the mouse cursor to (x, y).",
    )
    def _mouse_move(agent: str, x: int = 0, y: int = 0, duration: float = 0.3, **_: Any) -> str:
        try:
            return computer.mouse_move(x, y, duration)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "mouse_click",
        "mouse_click(x, y, button?, clicks?) — click at (x,y). button: left|right|middle.",
    )
    def _mouse_click(agent: str, x: int = 0, y: int = 0, button: str = "left", clicks: int = 1, **_: Any) -> str:
        try:
            return computer.mouse_click(x, y, button, clicks)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "mouse_double_click",
        "mouse_double_click(x, y) — double-click at (x, y).",
    )
    def _mouse_dbl(agent: str, x: int = 0, y: int = 0, **_: Any) -> str:
        try:
            return computer.mouse_double_click(x, y)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "mouse_right_click",
        "mouse_right_click(x, y) — right-click at (x, y).",
    )
    def _mouse_right(agent: str, x: int = 0, y: int = 0, **_: Any) -> str:
        try:
            return computer.mouse_right_click(x, y)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "mouse_drag",
        "mouse_drag(x1, y1, x2, y2, duration?) — click-and-drag from (x1,y1) to (x2,y2).",
    )
    def _mouse_drag(agent: str, x1: int = 0, y1: int = 0, x2: int = 0, y2: int = 0, duration: float = 0.5, **_: Any) -> str:
        try:
            return computer.mouse_drag(x1, y1, x2, y2, duration)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "mouse_scroll",
        "mouse_scroll(x, y, amount?, direction?) — scroll mouse wheel at (x,y). direction: up|down.",
    )
    def _mouse_scroll(agent: str, x: int = 0, y: int = 0, amount: int = 3, direction: str = "down", **_: Any) -> str:
        try:
            return computer.mouse_scroll(x, y, amount, direction)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "get_mouse_pos",
        "get_mouse_pos() — return current mouse cursor position {x, y}.",
    )
    def _mouse_pos(agent: str, **_: Any) -> str:
        try:
            return computer.get_mouse_pos()
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "keyboard_type",
        "keyboard_type(text, interval?) — type a string using the keyboard.",
    )
    def _kb_type(agent: str, text: str = "", interval: float = 0.01, **_: Any) -> str:
        try:
            return computer.keyboard_type(text, interval)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "keyboard_press",
        "keyboard_press(key) — press a single key: 'enter','escape','tab','backspace', etc.",
    )
    def _kb_press(agent: str, key: str = "", **_: Any) -> str:
        try:
            return computer.keyboard_press(key)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "keyboard_shortcut",
        "keyboard_shortcut(keys) — press a key combo, '+'-separated. E.g. 'ctrl+c', 'alt+tab'.",
    )
    def _kb_shortcut(agent: str, keys: str = "", **_: Any) -> str:
        try:
            return computer.keyboard_shortcut(keys)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "keyboard_write_line",
        "keyboard_write_line(text) — type text and press Enter.",
    )
    def _kb_writeline(agent: str, text: str = "", **_: Any) -> str:
        try:
            return computer.keyboard_write_line(text)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    # -- browser (headless Playwright, per-agent sessions) ----------------
    @tool_decorator(
        "browser_open",
        "browser_open(url) — open a URL in the agent's headless browser. Works on any server.",
    )
    def _br_open(agent: str, url: str = "", **_: Any) -> str:
        try:
            return computer.browser_open(agent, url)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_url", "browser_url() — return the current URL in the agent's browser.")
    def _br_url(agent: str, **_: Any) -> str:
        try:
            return computer.browser_url(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator("browser_title", "browser_title() — return the current page title.")
    def _br_title(agent: str, **_: Any) -> str:
        try:
            return computer.browser_title(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_click",
        "browser_click(selector) — click an element by CSS selector in the browser.",
    )
    def _br_click(agent: str, selector: str = "", **_: Any) -> str:
        try:
            return computer.browser_click(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_type",
        "browser_type(selector, text) — fill an input field by CSS selector.",
    )
    def _br_type(agent: str, selector: str = "", text: str = "", **_: Any) -> str:
        try:
            return computer.browser_type(agent, selector, text)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_press",
        "browser_press(key) — send a keyboard key to the browser (e.g. 'Enter', 'Tab').",
    )
    def _br_press(agent: str, key: str = "Enter", **_: Any) -> str:
        try:
            return computer.browser_press(agent, key)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_scroll",
        "browser_scroll(direction?, px?) — scroll the browser page. direction: up|down.",
    )
    def _br_scroll(agent: str, direction: str = "down", px: int = 500, **_: Any) -> str:
        try:
            return computer.browser_scroll(agent, direction, px)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_text",
        "browser_text(selector?) — extract visible text. selector defaults to 'body'.",
    )
    def _br_text(agent: str, selector: str = "body", **_: Any) -> str:
        try:
            return computer.browser_text(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_html",
        "browser_html(selector?) — get inner HTML of the first matching element.",
    )
    def _br_html(agent: str, selector: str = "body", **_: Any) -> str:
        try:
            return computer.browser_html(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_links",
        "browser_links() — get all external links on the current page as JSON [{text, href}].",
    )
    def _br_links(agent: str, **_: Any) -> str:
        try:
            return computer.browser_links(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_screenshot",
        "browser_screenshot(path?) — screenshot the current browser page, return file path.",
    )
    def _br_shot(agent: str, path: str = "", **_: Any) -> str:
        try:
            return computer.browser_screenshot(agent, path)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_js",
        "browser_js(script) — run JavaScript in the browser, return result as JSON.",
    )
    def _br_js(agent: str, script: str = "", **_: Any) -> str:
        try:
            return computer.browser_js(agent, script)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_wait",
        "browser_wait(selector, timeout_ms?) — wait for a CSS element to appear.",
    )
    def _br_wait(agent: str, selector: str = "", timeout_ms: int = 10000, **_: Any) -> str:
        try:
            return computer.browser_wait(agent, selector, timeout_ms)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_fill_form",
        "browser_fill_form(fields) — fill multiple form fields at once. fields: {selector: value}.",
    )
    def _br_form(agent: str, fields: dict | None = None, **_: Any) -> str:
        try:
            return computer.browser_fill_form(agent, fields)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_select",
        "browser_select(selector, value) — choose a <select> option by value.",
    )
    def _br_select(agent: str, selector: str = "", value: str = "", **_: Any) -> str:
        try:
            return computer.browser_select(agent, selector, value)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_hover",
        "browser_hover(selector) — hover over an element by CSS selector.",
    )
    def _br_hover(agent: str, selector: str = "", **_: Any) -> str:
        try:
            return computer.browser_hover(agent, selector)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_get_attribute",
        "browser_get_attribute(selector, attribute) — get an attribute from matching elements, returns JSON list.",
    )
    def _br_attr(agent: str, selector: str = "", attribute: str = "href", **_: Any) -> str:
        try:
            return computer.browser_get_attribute(agent, selector, attribute)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_close",
        "browser_close() — close and clean up the agent's browser session.",
    )
    def _br_close(agent: str, **_: Any) -> str:
        try:
            return computer.browser_close(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "browser_new_tab",
        "browser_new_tab() — open a new tab in the agent's browser.",
    )
    def _br_new_tab(agent: str, **_: Any) -> str:
        try:
            return computer.browser_new_tab(agent)
        except RuntimeError as exc:
            return f"ERROR: {exc}"

    @tool_decorator(
        "active_browsers",
        "active_browsers() — list agents that currently have browser sessions open.",
    )
    def _active_browsers(agent: str, **_: Any) -> str:
        return computer.active_browsers()
