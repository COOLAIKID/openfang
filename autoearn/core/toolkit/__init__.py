"""Toolkit package — registers a broad library of agent tools.

The modules here (``research``, ``finance``, ``content``, ``files``) hold plain
functions. :func:`register_all` wraps them as agent tools in the main
:mod:`core.tools` registry, plus adds connector-backed publishing/selling tools.
``core.tools`` calls :func:`register_all` at import time, so every agent that
lists one of these tool names can use it.
"""
from __future__ import annotations

import json
from typing import Any

from . import content, files, finance, research


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
