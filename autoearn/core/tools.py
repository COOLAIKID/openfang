"""External-action tools available to agents.

Each tool is a plain function registered in :data:`REGISTRY`. Agents invoke tools
by name with a JSON argument dict; :func:`dispatch` looks the tool up, calls it,
and always returns a string (tools that lack credentials return a readable error
string rather than raising, so the agent can simply choose something else).

A tool's signature is ``fn(agent: str, **kwargs) -> str`` — ``agent`` is the
caller's name, injected automatically so tools can attribute logs/revenue.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from . import config, database as db, message_bus

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"

REGISTRY: dict[str, Callable[..., str]] = {}
SCHEMAS: dict[str, str] = {}


def tool(name: str, description: str) -> Callable:
    """Decorator registering a function as an agent tool."""

    def wrap(fn: Callable[..., str]) -> Callable[..., str]:
        REGISTRY[name] = fn
        SCHEMAS[name] = description
        return fn

    return wrap


def dispatch(name: str, agent: str, args: dict[str, Any] | None = None) -> str:
    """Run a tool by name. Never raises — returns an error string on failure."""
    fn = REGISTRY.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'. Available: {', '.join(sorted(REGISTRY))}"
    try:
        result = fn(agent=agent, **(args or {}))
        return result if isinstance(result, str) else json.dumps(result)
    except TypeError as exc:
        return f"ERROR: bad arguments for '{name}': {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: tool '{name}' failed: {exc}"


def describe(tool_names: list[str]) -> str:
    """Human/LLM-readable catalog of the given tools."""
    lines = []
    for name in tool_names:
        if name in SCHEMAS:
            lines.append(f"- {name}: {SCHEMAS[name]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Information tools
# --------------------------------------------------------------------------
@tool("web_search", "web_search(query) — search the web, returns top result snippets.")
def _web_search(agent: str, query: str = "", **_: Any) -> str:
    import requests

    resp = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": query},
        headers={"User-Agent": "Mozilla/5.0 autoearn"},
        timeout=30,
    )
    resp.raise_for_status()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(resp.text, "html.parser")
    out = []
    for res in soup.select(".result__body")[:8]:
        title = res.select_one(".result__a")
        snippet = res.select_one(".result__snippet")
        if title:
            out.append(f"{title.get_text(strip=True)} — {snippet.get_text(strip=True) if snippet else ''}")
    return "\n".join(out) if out else "No results."


@tool("fetch_url", "fetch_url(url) — fetch a web page and return its readable text.")
def _fetch_url(agent: str, url: str = "", **_: Any) -> str:
    import requests
    from bs4 import BeautifulSoup

    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 autoearn"}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer"]):
        tag.decompose()
    text = " ".join(soup.get_text(" ").split())
    return text[:6000]


@tool("fetch_prices", "fetch_prices(symbols) — CoinGecko prices for comma-separated coin ids (e.g. 'bitcoin,ethereum').")
def _fetch_prices(agent: str, symbols: str = "bitcoin,ethereum", **_: Any) -> str:
    import requests

    ids = ",".join(s.strip().lower() for s in symbols.split(","))
    resp = requests.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": ids, "vs_currencies": "usd", "include_24hr_change": "true"},
        timeout=30,
    )
    resp.raise_for_status()
    return json.dumps(resp.json())


@tool("http_request", "http_request(method, url, headers?, body?) — arbitrary HTTP call, returns status + truncated body.")
def _http_request(agent: str, method: str = "GET", url: str = "", headers: dict | None = None, body: Any = None, **_: Any) -> str:
    import requests

    resp = requests.request(
        method.upper(),
        url,
        headers=headers or {},
        json=body if isinstance(body, (dict, list)) else None,
        data=body if isinstance(body, str) else None,
        timeout=60,
    )
    return f"HTTP {resp.status_code}\n{resp.text[:4000]}"


# --------------------------------------------------------------------------
# Output / persistence tools
# --------------------------------------------------------------------------
@tool("save_output", "save_output(category, filename, content) — save a file under output/<category>/.")
def _save_output(agent: str, category: str = "misc", filename: str = "untitled.txt", content: str = "", **_: Any) -> str:
    safe_cat = "".join(c for c in category if c.isalnum() or c in "-_") or "misc"
    safe_name = "".join(c for c in filename if c.isalnum() or c in "-_. ") or "untitled.txt"
    target_dir = OUTPUT_DIR / safe_cat
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / safe_name
    path.write_text(content, encoding="utf-8")
    db.log_activity(agent, "save_output", str(path.relative_to(ROOT)))
    return f"Saved {len(content)} chars to {path.relative_to(ROOT)}"


@tool("log_revenue", "log_revenue(amount, source, note?) — record a revenue event in USD.")
def _log_revenue(agent: str, amount: float = 0.0, source: str = "", note: str = "", **_: Any) -> str:
    db.log_revenue(float(amount), source=source, agent=agent, note=note)
    return f"Logged ${float(amount):.2f} from {source}"


@tool("get_recent_activity", "get_recent_activity(n?) — recent org-wide activity log entries.")
def _get_recent_activity(agent: str, n: int = 20, **_: Any) -> str:
    rows = db.recent_activity(limit=int(n))
    return json.dumps([{"agent": r["agent"], "action": r["action"], "detail": r["detail"]} for r in rows])


@tool("get_revenue_summary", "get_revenue_summary() — total revenue and breakdown by source/agent.")
def _get_revenue_summary(agent: str, **_: Any) -> str:
    return json.dumps(db.revenue_summary())


# --------------------------------------------------------------------------
# Messaging tools
# --------------------------------------------------------------------------
@tool("send_message", "send_message(to, type, subject, body) — send a message to another agent or team.")
def _send_message(agent: str, to: str = "", type: str = "work_item", subject: str = "", body: str = "", **_: Any) -> str:
    if not to:
        return "ERROR: 'to' is required."
    mid = message_bus.send(agent, to, type, subject=subject, body=body)
    return f"Sent message #{mid} ({type}) to {to}."


@tool("get_messages", "get_messages() — read unread messages addressed to you.")
def _get_messages(agent: str, **_: Any) -> str:
    msgs = message_bus.inbox(agent, include_read=False)
    out = [{"id": m["id"], "from": m["from_agent"], "type": m["type"], "subject": m["subject"], "body": m["body"]} for m in msgs]
    return json.dumps(out)


# --------------------------------------------------------------------------
# Publishing tools (no-op with helpful message when unconfigured)
# --------------------------------------------------------------------------
@tool("publish_wordpress", "publish_wordpress(title, body) — publish a post to WordPress.")
def _publish_wordpress(agent: str, title: str = "", body: str = "", **_: Any) -> str:
    cfg = config.section("wordpress")
    if not cfg.get("url") or not cfg.get("username"):
        return "ERROR: WordPress not configured (set [wordpress] url/username/password)."
    import requests

    resp = requests.post(
        cfg["url"].rstrip("/") + "/wp-json/wp/v2/posts",
        auth=(cfg["username"], cfg.get("password", "")),
        json={"title": title, "content": body, "status": "publish"},
        timeout=60,
    )
    resp.raise_for_status()
    link = resp.json().get("link", "(unknown)")
    db.log_activity(agent, "publish_wordpress", link)
    return f"Published to WordPress: {link}"


@tool("publish_medium", "publish_medium(title, body) — publish a post to Medium.")
def _publish_medium(agent: str, title: str = "", body: str = "", **_: Any) -> str:
    token = config.get("medium", "token")
    if not token:
        return "ERROR: Medium not configured (set [medium] token)."
    import requests

    me = requests.get(
        "https://api.medium.com/v1/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    me.raise_for_status()
    user_id = me.json()["data"]["id"]
    resp = requests.post(
        f"https://api.medium.com/v1/users/{user_id}/posts",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": title, "contentFormat": "markdown", "content": body, "publishStatus": "public"},
        timeout=60,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    db.log_activity(agent, "publish_medium", url)
    return f"Published to Medium: {url}"


@tool("post_telegram", "post_telegram(message) — post a message to the configured Telegram channel.")
def _post_telegram(agent: str, message: str = "", **_: Any) -> str:
    cfg = config.section("telegram")
    if not cfg.get("bot_token") or not cfg.get("channel_id"):
        return "ERROR: Telegram not configured (set [telegram] bot_token/channel_id)."
    import requests

    resp = requests.post(
        f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage",
        json={"chat_id": cfg["channel_id"], "text": message},
        timeout=30,
    )
    resp.raise_for_status()
    db.log_activity(agent, "post_telegram", message[:80])
    return "Posted to Telegram."


@tool("list_skills", "list_skills() — list installed Claude skills you can invoke.")
def _list_skills(agent: str, **_: Any) -> str:
    from . import skills

    items = [{"name": s["name"], "description": s["description"]} for s in skills.discover()]
    return json.dumps(items) if items else "No skills installed."


@tool("use_skill", "use_skill(name, input) — run an installed Claude skill on the given input and return its output.")
def _use_skill(agent: str, name: str = "", input: str = "", **_: Any) -> str:
    from . import skills

    out = skills.run(name, input)
    db.log_activity(agent, "use_skill", f"{name}: {input[:60]}")
    return out


@tool("install_skill", "install_skill(source) — install a Claude skill from a local path, git URL, or .zip URL.")
def _install_skill(agent: str, source: str = "", **_: Any) -> str:
    from . import skills

    try:
        skill = skills.install(source)
        return f"Installed skill '{skill['name']}': {skill['description']}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: could not install skill: {exc}"


@tool("post_reddit", "post_reddit(subreddit, title, body) — submit a self-post to a subreddit.")
def _post_reddit(agent: str, subreddit: str = "", title: str = "", body: str = "", **_: Any) -> str:
    cfg = config.section("reddit")
    if not cfg.get("client_id") or not cfg.get("username"):
        return "ERROR: Reddit not configured (set [reddit] credentials)."
    import praw

    reddit = praw.Reddit(
        client_id=cfg["client_id"],
        client_secret=cfg.get("client_secret", ""),
        username=cfg["username"],
        password=cfg.get("password", ""),
        user_agent=cfg.get("user_agent", "autoearn/0.1"),
    )
    submission = reddit.subreddit(subreddit).submit(title=title, selftext=body)
    db.log_activity(agent, "post_reddit", submission.url)
    return f"Posted to r/{subreddit}: {submission.url}"
