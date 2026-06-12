"""Research toolkit — gathering information from the open web.

These functions are thin, dependency-light helpers wrapped as agent tools in
:mod:`core.toolkit.register_all`. They focus on free, key-less data sources so
they work out of the box: DuckDuckGo, Wikipedia, RSS feeds, Hacker News, and
generic page fetching.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; AutoEarn/1.0)"}


def web_search(query: str, limit: int = 8) -> str:
    """DuckDuckGo HTML search; returns title — snippet lines."""
    resp = requests.get(
        "https://duckduckgo.com/html/", params={"q": query}, headers=UA, timeout=30
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for res in soup.select(".result__body")[:limit]:
        title = res.select_one(".result__a")
        snippet = res.select_one(".result__snippet")
        if title:
            out.append(f"{title.get_text(strip=True)} — {snippet.get_text(strip=True) if snippet else ''}")
    return "\n".join(out) if out else "No results."


def fetch_url(url: str, max_chars: int = 6000) -> str:
    """Fetch a page and return its readable text content."""
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "noscript"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:max_chars]


def wikipedia_summary(topic: str) -> str:
    """Return the lead summary of a Wikipedia article."""
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote(topic)}"
    resp = requests.get(url, headers=UA, timeout=30)
    if resp.status_code == 404:
        return f"No Wikipedia article for '{topic}'."
    resp.raise_for_status()
    data = resp.json()
    return data.get("extract", "(no extract)")


def read_rss(feed_url: str, limit: int = 10) -> str:
    """Parse an RSS/Atom feed; return recent item titles and links."""
    resp = requests.get(feed_url, headers=UA, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    out = []
    for item in items[:limit]:
        title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "(no title)"
        link_el = item.find("link")
        link = (link_el.text if link_el is not None else "") or ""
        out.append(f"{title.strip()} — {link.strip()}")
    return "\n".join(out) if out else "No items."


def hacker_news_top(limit: int = 10) -> str:
    """Top Hacker News stories — a good pulse on tech/startup trends."""
    ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=30).json()[:limit]
    out = []
    for sid in ids:
        item = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json", timeout=30).json()
        if item:
            out.append(f"[{item.get('score', 0)}pts] {item.get('title', '')} — {item.get('url', '')}")
    return "\n".join(out)


def google_trends_rss(geo: str = "US") -> str:
    """Daily trending search topics via the public Google Trends RSS feed."""
    url = f"https://trends.google.com/trends/trendingsearches/daily/rss?geo={geo}"
    try:
        return read_rss(url, limit=15)
    except Exception as exc:  # noqa: BLE001
        return f"Could not load trends: {exc}"


def reddit_top(subreddit: str, limit: int = 10) -> str:
    """Top posts from a subreddit via the public JSON endpoint (no auth)."""
    resp = requests.get(
        f"https://www.reddit.com/r/{subreddit}/top.json",
        params={"limit": limit, "t": "day"},
        headers=UA,
        timeout=30,
    )
    resp.raise_for_status()
    children = resp.json().get("data", {}).get("children", [])
    out = []
    for c in children:
        d = c.get("data", {})
        out.append(f"[{d.get('score', 0)}] {d.get('title', '')} (r/{subreddit})")
    return "\n".join(out) if out else "No posts."


def http_request(method: str, url: str, headers: dict | None = None, body=None) -> str:
    """Arbitrary HTTP call; returns status + truncated body."""
    resp = requests.request(
        method.upper(),
        url,
        headers=headers or UA,
        json=body if isinstance(body, (dict, list)) else None,
        data=body if isinstance(body, str) else None,
        timeout=60,
    )
    return f"HTTP {resp.status_code}\n{resp.text[:4000]}"
