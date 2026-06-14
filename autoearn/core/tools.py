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


# --------------------------------------------------------------------------
# Expanded toolkit (research / finance / content / files / connectors /
#                   computer use / browser)
# Registered here so every tool lives in one registry.
# --------------------------------------------------------------------------
from . import toolkit  # noqa: E402

toolkit.register_all(tool)

# --------------------------------------------------------------------------
# Per-agent Docker sandbox tools
# --------------------------------------------------------------------------
from . import sandbox as _sandbox  # noqa: E402


@tool("sandbox_exec", "sandbox_exec(command, timeout?) — run a shell command in the agent's isolated Linux container.")
def _sandbox_exec(agent: str, command: str = "", timeout: int = 30, **_: Any) -> str:
    return _sandbox.exec_cmd(agent, command, int(timeout))


@tool("sandbox_install", "sandbox_install(package, manager?) — install a package in the agent's container. manager: pip (default) | apt.")
def _sandbox_install(agent: str, package: str = "", manager: str = "pip", **_: Any) -> str:
    return _sandbox.install_package(agent, package, manager)


@tool("sandbox_browse", "sandbox_browse(url) — fetch a URL inside the agent's sandbox container and return readable text.")
def _sandbox_browse(agent: str, url: str = "", **_: Any) -> str:
    return _sandbox.browse_url(agent, url)


@tool("sandbox_write", "sandbox_write(path, content) — write a file inside the agent's container.")
def _sandbox_write(agent: str, path: str = "", content: str = "", **_: Any) -> str:
    return _sandbox.write_file(agent, path, content)


@tool("sandbox_read", "sandbox_read(path) — read a file from inside the agent's container.")
def _sandbox_read(agent: str, path: str = "", **_: Any) -> str:
    return _sandbox.read_file(agent, path)


@tool("sandbox_list", "sandbox_list(path?) — list files in the agent's container (default: /workspace).")
def _sandbox_list(agent: str, path: str = "/workspace", **_: Any) -> str:
    return _sandbox.list_files(agent, path)


@tool("sandbox_status", "sandbox_status() — list all running AutoEarn sandbox containers.")
def _sandbox_status(agent: str, **_: Any) -> str:
    return _sandbox.sandbox_status()


@tool("sandbox_destroy", "sandbox_destroy(agent_name?) — stop and remove a sandbox container.")
def _sandbox_destroy(agent: str, agent_name: str = "", **_: Any) -> str:
    return _sandbox.destroy_sandbox(agent_name or agent)


@tool("sandbox_rebuild", "sandbox_rebuild(agent_name?) — destroy and recreate a fresh sandbox.")
def _sandbox_rebuild(agent: str, agent_name: str = "", **_: Any) -> str:
    return _sandbox.rebuild_sandbox(agent_name or agent)


# --------------------------------------------------------------------------
# Semantic memory tools (per-agent SQLite FTS5 memory store)
# --------------------------------------------------------------------------
from . import memory as _memory  # noqa: E402


@tool("memory_save", "memory_save(key, content, tags?) — save a memory entry with optional tags.")
def _memory_save(agent: str, key: str = "", content: str = "", tags: list | None = None, **_: Any) -> str:
    mid = _memory.save(agent, key, content, tags)
    return f"Saved memory #{mid} under key '{key}'"


@tool("memory_recall", "memory_recall(query, limit?) — full-text search your memory for relevant entries.")
def _memory_recall(agent: str, query: str = "", limit: int = 10, **_: Any) -> str:
    results = _memory.recall(agent, query, int(limit))
    return json.dumps(results)


@tool("memory_get", "memory_get(key) — retrieve a specific memory entry by exact key.")
def _memory_get(agent: str, key: str = "", **_: Any) -> str:
    result = _memory.get(agent, key)
    return json.dumps(result) if result else f"No memory found for key '{key}'"


@tool("memory_delete", "memory_delete(key) — delete a specific memory entry.")
def _memory_delete(agent: str, key: str = "", **_: Any) -> str:
    ok = _memory.delete(agent, key)
    return f"Deleted memory '{key}'" if ok else f"No memory found for key '{key}'"


@tool("memory_list", "memory_list(tag?) — list all memory keys, optionally filtered by tag.")
def _memory_list(agent: str, tag: str = "", **_: Any) -> str:
    keys = _memory.list_keys(agent, tag or None)
    return json.dumps(keys)


@tool("memory_summarize", "memory_summarize() — summary of this agent's memory (count, tags, recent entries).")
def _memory_summarize(agent: str, **_: Any) -> str:
    return json.dumps(_memory.summarize(agent))


@tool("memory_inject_context", "memory_inject_context(query, max_chars?) — retrieve memory as formatted context string for prompts.")
def _memory_inject(agent: str, query: str = "", max_chars: int = 2000, **_: Any) -> str:
    return _memory.inject_context(agent, query, int(max_chars))


# --------------------------------------------------------------------------
# Monitoring tools
# --------------------------------------------------------------------------
from . import monitoring as _monitoring  # noqa: E402


@tool("org_health", "org_health() — health score and status for every agent in the organization.")
def _org_health(agent: str, **_: Any) -> str:
    return _monitoring.health_summary()


@tool("stale_agents", "stale_agents(threshold_minutes?) — list agents that haven't run recently.")
def _stale_agents(agent: str, threshold_minutes: float = 120.0, **_: Any) -> str:
    stale = _monitoring.stale_agents(float(threshold_minutes))
    return json.dumps([{"name": a.name, "last_run_minutes_ago": a.last_run_minutes_ago} for a in stale])


@tool("erroring_agents", "erroring_agents(threshold_rate?) — list agents with high error rates.")
def _erroring_agents(agent: str, threshold_rate: float = 0.5, **_: Any) -> str:
    erring = _monitoring.erroring_agents(float(threshold_rate))
    return json.dumps([{"name": a.name, "error_rate": a.error_rate, "runs": a.runs} for a in erring])


@tool("log_metric", "log_metric(metric, value) — log a numeric metric for this agent.")
def _log_metric(agent: str, metric: str = "", value: float = 0.0, **_: Any) -> str:
    _monitoring.log_metric(agent, metric, float(value))
    return f"Logged {metric}={value} for {agent}"


@tool("get_metrics", "get_metrics(metric, last_n?) — retrieve recent metric values for this agent.")
def _get_metrics(agent: str, metric: str = "", last_n: int = 100, **_: Any) -> str:
    return json.dumps(_monitoring.get_metrics(agent, metric, int(last_n)))


# --------------------------------------------------------------------------
# Notification tools
# --------------------------------------------------------------------------
from . import notifications as _notifications  # noqa: E402


@tool("notify", "notify(message, level?, title?) — send a notification via all configured channels.")
def _notify(agent: str, message: str = "", level: str = "info", title: str = "", **_: Any) -> str:
    results = _notifications.notify(agent=agent, message=message, level=level, title=title or f"AutoEarn: {agent}")
    sent = [ch for ch, ok in results.items() if ok]
    return f"Notification sent via: {', '.join(sent) or 'none'}"


@tool("notify_error_alert", "notify_error_alert(error, context?) — send error alert to all notification channels.")
def _notify_error(agent: str, error: str = "", context: str = "", **_: Any) -> str:
    results = _notifications.notify_error(agent, error, context)
    sent = [ch for ch, ok in results.items() if ok]
    return f"Error alert sent via: {', '.join(sent) or 'none'}"


@tool("notify_revenue_milestone", "notify_revenue_milestone(amount, source) — send revenue notification to all channels.")
def _notify_revenue(agent: str, amount: float = 0.0, source: str = "", **_: Any) -> str:
    results = _notifications.notify_revenue(agent, float(amount), source)
    sent = [ch for ch, ok in results.items() if ok]
    return f"Revenue notification sent via: {', '.join(sent) or 'none'}"


# --------------------------------------------------------------------------
# Workflow tools
# --------------------------------------------------------------------------
from . import workflows as _workflows  # noqa: E402


@tool("list_workflows", "list_workflows() — list all saved multi-agent workflows.")
def _list_workflows(agent: str, **_: Any) -> str:
    return json.dumps(_workflows.list_workflows())


@tool("run_workflow", "run_workflow(name, context?) — execute a named workflow with optional context variables.")
def _run_workflow(agent: str, name: str = "", context: dict | None = None, **_: Any) -> str:
    wf = _workflows.load_workflow(name)
    if wf is None:
        return f"ERROR: workflow '{name}' not found."
    results = _workflows.run_workflow(wf, context or {})
    return json.dumps({"workflow": name, "steps_run": len(results), "results": results})


# --------------------------------------------------------------------------
# Treasury tools
# --------------------------------------------------------------------------
from . import treasury as _treasury  # noqa: E402


@tool("financial_report", "financial_report(days?) — full P&L report for the past N days.")
def _financial_report(agent: str, days: int = 7, **_: Any) -> str:
    return _treasury.financial_report(int(days))


@tool("budget_health", "budget_health() — budget utilization and remaining budget for each agent.")
def _budget_health(agent: str, **_: Any) -> str:
    return json.dumps(_treasury.budget_health())


@tool("roi_summary", "roi_summary(days?) — ROI per agent: spend vs revenue over the past N days.")
def _roi_summary(agent: str, days: int = 30, **_: Any) -> str:
    return json.dumps(_treasury.roi_summary(int(days)))


@tool("record_spend", "record_spend(amount, category, description?) — record a spending event for budget tracking.")
def _record_spend(agent: str, amount: float = 0.0, category: str = "general", description: str = "", **_: Any) -> str:
    _treasury.record_spend(agent, float(amount), category, description)
    return f"Recorded spend of ${float(amount):.2f} ({category})"


@tool("suggest_budget_reallocation", "suggest_budget_reallocation() — AI-powered budget reallocation recommendations based on ROI.")
def _realloc(agent: str, **_: Any) -> str:
    return json.dumps(_treasury.suggest_reallocation())


# --------------------------------------------------------------------------
# A/B testing tools
# --------------------------------------------------------------------------
from . import ab_testing as _ab  # noqa: E402


@tool("ab_create", "ab_create(name, variants, hypothesis?, min_sample_size?) — create a new A/B experiment.")
def _ab_create(agent: str, name: str = "", variants: list | None = None, hypothesis: str = "", min_sample_size: int = 100, **_: Any) -> str:
    return _ab.create_experiment_tool(name, variants, hypothesis, int(min_sample_size))


@tool("ab_assign", "ab_assign(experiment_name, participant_id) — assign a participant to a variant.")
def _ab_assign(agent: str, experiment_name: str = "", participant_id: str = "", **_: Any) -> str:
    return _ab.assign_tool(experiment_name, participant_id)


@tool("ab_convert", "ab_convert(experiment_name, participant_id, revenue?) — record a conversion event.")
def _ab_convert(agent: str, experiment_name: str = "", participant_id: str = "", revenue: float = 0.0, **_: Any) -> str:
    _ab.record_conversion(experiment_name, participant_id, float(revenue))
    return f"Conversion recorded for '{participant_id}' in experiment '{experiment_name}'"


@tool("ab_analyze", "ab_analyze(experiment_name) — statistical analysis of an A/B experiment.")
def _ab_analyze(agent: str, experiment_name: str = "", **_: Any) -> str:
    return _ab.analyze_tool(experiment_name)


@tool("ab_conclude", "ab_conclude(experiment_name) — auto-select winner if statistically significant.")
def _ab_conclude(agent: str, experiment_name: str = "", **_: Any) -> str:
    return _ab.conclude_experiment_tool(experiment_name)


@tool("ab_list", "ab_list() — list all A/B experiments and their current status.")
def _ab_list(agent: str, **_: Any) -> str:
    return _ab.experiment_summary()


# --------------------------------------------------------------------------
# Lead tracker / CRM tools
# --------------------------------------------------------------------------
from . import lead_tracker as _leads  # noqa: E402


@tool("track_lead", "track_lead(name, source?, contact_email?, company?, estimated_value?, notes?) — add a new lead to the CRM.")
def _track_lead(agent: str, name: str = "", source: str = "", contact_email: str = "", company: str = "", estimated_value: float = 0.0, notes: str = "", **_: Any) -> str:
    return _leads.track_lead(name, source, contact_email, company, float(estimated_value), notes, agent)


@tool("move_lead", "move_lead(lead_id, stage, note?) — advance a lead to a new pipeline stage.")
def _move_lead(agent: str, lead_id: int = 0, stage: str = "", note: str = "", **_: Any) -> str:
    return _leads.move_lead_stage(int(lead_id), stage, agent, note)


@tool("pipeline_report", "pipeline_report() — full CRM pipeline value and conversion summary.")
def _pipeline_report(agent: str, **_: Any) -> str:
    return _leads.get_pipeline_report()


@tool("followup_leads", "followup_leads() — list leads overdue for follow-up (inactive for 3+ days).")
def _followup_leads(agent: str, **_: Any) -> str:
    return _leads.get_followup_leads()


@tool("search_leads", "search_leads(query) — search leads by name, company, or niche.")
def _search_leads(agent: str, query: str = "", **_: Any) -> str:
    return _leads.search_leads(query)


@tool("add_lead_note", "add_lead_note(lead_id, note) — add an activity note to a lead.")
def _add_note(agent: str, lead_id: int = 0, note: str = "", **_: Any) -> str:
    return _leads.add_note(int(lead_id), agent, note)


# --------------------------------------------------------------------------
# Event bus tools
# --------------------------------------------------------------------------
from . import event_bus as _events  # noqa: E402


@tool("publish_event", "publish_event(event_type, data?, priority?) — publish an event to the org event bus.")
def _publish_event(agent: str, event_type: str = "", data: dict | None = None, priority: int = 5, **_: Any) -> str:
    return _events.publish_event(event_type, data, source=agent, priority=int(priority))


@tool("consume_events", "consume_events(limit?) — consume and return pending events for this agent.")
def _consume_events(agent: str, limit: int = 20, **_: Any) -> str:
    return _events.consume_events(agent, int(limit))


@tool("event_stats", "event_stats() — event bus statistics: total events, pending deliveries, by type.")
def _event_stats(agent: str, **_: Any) -> str:
    return _events.get_event_stats()


# --------------------------------------------------------------------------
# Rate limiter / cache management tools
# --------------------------------------------------------------------------
from . import rate_limiter as _rl  # noqa: E402
from . import caching as _cache  # noqa: E402


@tool("limiter_status", "limiter_status() — status of all API rate limiters (available tokens, window counts).")
def _limiter_status(agent: str, **_: Any) -> str:
    return _rl.get_limiter_status()


@tool("cache_stats", "cache_stats() — statistics for all HTTP response caches (hit rates, sizes).")
def _cache_stats(agent: str, **_: Any) -> str:
    return _cache.cache_stats_all()


@tool("clear_cache", "clear_cache(name) — clear a named cache (web, prices, search, api).")
def _clear_cache(agent: str, name: str = "web", **_: Any) -> str:
    return _cache.clear_cache(name)


# --------------------------------------------------------------------------
# Content pipeline tools
# --------------------------------------------------------------------------
from . import content_pipeline as _cp  # noqa: E402


@tool("create_content_piece", "create_content_piece(title, content_type?, niche?, target_keyword?, estimated_words?) — add a new piece to the content pipeline.")
def _create_piece(agent: str, title: str = "", content_type: str = "blog_post", niche: str = "", target_keyword: str = "", estimated_words: int = 1500, **_: Any) -> str:
    return _cp.create_content_piece(title, content_type, niche, target_keyword, int(estimated_words))


@tool("advance_content", "advance_content(piece_id, stage, note?) — move a content piece to the next pipeline stage.")
def _advance_piece(agent: str, piece_id: int = 0, stage: str = "", note: str = "", **_: Any) -> str:
    return _cp.advance_content(int(piece_id), stage, note, agent)


@tool("my_content_queue", "my_content_queue() — list content pieces currently assigned to this agent.")
def _my_queue(agent: str, **_: Any) -> str:
    return _cp.my_content_queue(agent)


@tool("content_pipeline_report", "content_pipeline_report() — full content pipeline statistics and stage breakdown.")
def _pipeline_report(agent: str, **_: Any) -> str:
    return _cp.content_pipeline_report()


@tool("stale_content_report", "stale_content_report() — find content pieces stuck in a stage for 24+ hours.")
def _stale_content(agent: str, **_: Any) -> str:
    return _cp.stale_content_report()


# --------------------------------------------------------------------------
# Competitor monitor tools
# --------------------------------------------------------------------------
from . import competitor_monitor as _cm  # noqa: E402


@tool("add_competitor", "add_competitor(name, domain, niche?, keywords?) — start tracking a competitor website.")
def _add_competitor(agent: str, name: str = "", domain: str = "", niche: str = "", keywords: list | None = None, **_: Any) -> str:
    return _cm.add_competitor_tool(name, domain, niche, keywords)


@tool("check_competitor", "check_competitor(competitor_id, url?) — check a competitor page for changes since last visit.")
def _check_competitor(agent: str, competitor_id: int = 0, url: str = "", **_: Any) -> str:
    return _cm.check_competitor_tool(int(competitor_id), url)


@tool("competitor_report", "competitor_report() — summary of all tracked competitors and recent changes.")
def _competitor_report(agent: str, **_: Any) -> str:
    return _cm.competitor_report_tool()


@tool("recent_competitor_changes", "recent_competitor_changes(days?) — recent competitor change events (price, content, rank).")
def _recent_comp_changes(agent: str, days: int = 7, **_: Any) -> str:
    return _cm.recent_competitor_changes_tool(int(days))


# --------------------------------------------------------------------------
# Social scheduler tools
# --------------------------------------------------------------------------
from . import social_scheduler as _ss  # noqa: E402


@tool("schedule_social_post", "schedule_social_post(platform, content, hashtags?, scheduled_for?) — schedule a social media post.")
def _schedule_post(agent: str, platform: str = "", content: str = "", hashtags: list | None = None, scheduled_for: float | None = None, **_: Any) -> str:
    return _ss.schedule_post_tool(platform, content, hashtags, scheduled_for, agent)


@tool("due_social_posts", "due_social_posts(platform?) — list social posts due for publishing now.")
def _due_posts(agent: str, platform: str = "", **_: Any) -> str:
    return _ss.get_due_posts_tool(platform)


@tool("social_queue", "social_queue(platform?) — view the upcoming social post queue.")
def _social_queue(agent: str, platform: str = "", **_: Any) -> str:
    return _ss.publishing_queue_tool(platform)


@tool("mark_post_published", "mark_post_published(post_id, url?) — mark a scheduled post as published.")
def _mark_published(agent: str, post_id: int = 0, url: str = "", **_: Any) -> str:
    return _ss.mark_published(int(post_id), url)


@tool("social_schedule_stats", "social_schedule_stats(days?) — publishing statistics across platforms.")
def _sched_stats(agent: str, days: int = 7, **_: Any) -> str:
    return _ss.schedule_stats_tool(int(days))


# --------------------------------------------------------------------------
# Keyword tracker tools
# --------------------------------------------------------------------------
from . import keyword_tracker as _kt  # noqa: E402


@tool("track_keyword", "track_keyword(keyword, target_url?, niche?, search_volume?, keyword_difficulty?) — add keyword to rank tracker.")
def _track_kw(agent: str, keyword: str = "", target_url: str = "", niche: str = "", search_volume: int = 0, keyword_difficulty: int = 0, **_: Any) -> str:
    return _kt.add_keyword_tool(keyword, target_url, niche, int(search_volume), int(keyword_difficulty))


@tool("update_keyword_rank", "update_keyword_rank(keyword, rank, url?) — record a new rank for a keyword.")
def _update_rank(agent: str, keyword: str = "", rank: int = 0, url: str = "", **_: Any) -> str:
    return _kt.update_rank_tool(keyword, int(rank), url)


@tool("keyword_opportunities", "keyword_opportunities(niche?, min_volume?) — keywords on page 2-3 ripe for optimization.")
def _kw_opps(agent: str, niche: str = "", min_volume: int = 100, **_: Any) -> str:
    return _kt.keyword_opportunities_tool(niche, int(min_volume))


@tool("keyword_report", "keyword_report() — full keyword tracking report: tiers, top rankings, alerts.")
def _kw_report(agent: str, **_: Any) -> str:
    return _kt.keyword_report_tool()


@tool("rank_alerts", "rank_alerts() — unacknowledged keyword ranking drop alerts.")
def _rank_alerts(agent: str, **_: Any) -> str:
    return _kt.rank_alerts_tool()


# --------------------------------------------------------------------------
# Prompt manager tools
# --------------------------------------------------------------------------
from . import prompt_manager as _pm  # noqa: E402


@tool("save_prompt", "save_prompt(name, template, description?, category?) — save a reusable prompt template with {{variable}} placeholders.")
def _save_prompt(agent: str, name: str = "", template: str = "", description: str = "", category: str = "general", **_: Any) -> str:
    return _pm.save_prompt_tool(name, template, description, category)


@tool("render_prompt", "render_prompt(name, variables?) — render a prompt template by substituting {{variable}} placeholders.")
def _render_prompt(agent: str, name: str = "", variables: dict | None = None, **_: Any) -> str:
    return _pm.render_prompt_tool(name, variables)


@tool("list_prompts", "list_prompts(category?) — list available prompt templates.")
def _list_prompts(agent: str, category: str = "", **_: Any) -> str:
    return _pm.list_prompts_tool(category)


@tool("prompt_stats", "prompt_stats() — usage statistics for prompt templates.")
def _prompt_stats(agent: str, **_: Any) -> str:
    return _pm.prompt_stats_tool()


# --------------------------------------------------------------------------
# Analytics tracker tools
# --------------------------------------------------------------------------
from . import analytics_tracker as _at  # noqa: E402


@tool("track_event", "track_event(event_type, source?, channel?, campaign?, content_id?, value?) — record an analytics event (pageview, click, conversion, signup, etc.).")
def _track_event(agent: str, event_type: str = "", source: str = "", channel: str = "", campaign: str = "", content_id: str = "", value: float = 0.0, **_: Any) -> str:
    return _at.track_event_tool(event_type, source, channel, campaign, content_id, value)


@tool("kpi_summary", "kpi_summary(days?) — revenue, conversion, and traffic KPI dashboard for the last N days.")
def _kpi_summary(agent: str, days: int = 30, **_: Any) -> str:
    return _at.kpi_summary_tool(days)


@tool("channel_breakdown", "channel_breakdown(days?) — revenue and conversions broken down by marketing channel.")
def _channel_breakdown(agent: str, days: int = 30, **_: Any) -> str:
    return _at.channel_breakdown_tool(days)


@tool("top_content", "top_content(days?, limit?) — top content pieces by revenue generated.")
def _top_content(agent: str, days: int = 30, limit: int = 20, **_: Any) -> str:
    return _at.top_content_tool(days, limit)


@tool("daily_series", "daily_series(days?) — daily revenue series for charting trends.")
def _daily_series(agent: str, days: int = 30, **_: Any) -> str:
    return _at.daily_series_tool(days)


@tool("revenue_anomalies", "revenue_anomalies(days?) — detect days with unusual revenue spikes or drops using z-score analysis.")
def _revenue_anomalies(agent: str, days: int = 30, **_: Any) -> str:
    return _at.anomaly_tool(days)


@tool("create_goal", "create_goal(name, description?, target?, unit?) — create a trackable revenue or traffic goal.")
def _create_goal(agent: str, name: str = "", description: str = "", target: float = 0.0, unit: str = "count", **_: Any) -> str:
    return _at.create_goal_tool(name, description, target, unit)


@tool("list_goals", "list_goals() — list all goals with current progress.")
def _list_goals(agent: str, **_: Any) -> str:
    return _at.list_goals_tool()


@tool("funnel_report", "funnel_report(funnel_name) — get step-by-step drop-off analysis for a conversion funnel.")
def _funnel_report(agent: str, funnel_name: str = "", **_: Any) -> str:
    return _at.funnel_report_tool(funnel_name)


# --------------------------------------------------------------------------
# Newsletter tools
# --------------------------------------------------------------------------
from . import newsletter as _nl  # noqa: E402


@tool("nl_subscribe", "nl_subscribe(email, first_name?, last_name?, list_name?, source?, tags?) — subscribe an email address to a newsletter list.")
def _nl_subscribe(agent: str, email: str = "", first_name: str = "", last_name: str = "", list_name: str = "main", source: str = "", tags: str = "", **_: Any) -> str:
    return _nl.subscribe_tool(email, first_name, last_name, list_name, source, tags)


@tool("nl_unsubscribe", "nl_unsubscribe(email) — unsubscribe an email address.")
def _nl_unsubscribe(agent: str, email: str = "", **_: Any) -> str:
    return _nl.unsubscribe_tool(email)


@tool("nl_subscriber_count", "nl_subscriber_count(list_name?, status?) — count subscribers, optionally filtered by list and status.")
def _nl_subscriber_count(agent: str, list_name: str = "", status: str = "confirmed", **_: Any) -> str:
    return _nl.subscriber_count_tool(list_name, status)


@tool("nl_create_campaign", "nl_create_campaign(name, subject, body?, list_name?) — create a draft email campaign.")
def _nl_create_campaign(agent: str, name: str = "", subject: str = "", body: str = "", list_name: str = "main", **_: Any) -> str:
    return _nl.create_campaign_tool(name, subject, body, list_name)


@tool("nl_summary", "nl_summary() — newsletter health summary: subscriber counts, open rates, revenue.")
def _nl_summary(agent: str, **_: Any) -> str:
    return _nl.newsletter_summary_tool()


@tool("nl_due_campaigns", "nl_due_campaigns() — list email campaigns scheduled for sending now or in the past.")
def _nl_due_campaigns(agent: str, **_: Any) -> str:
    return _nl.due_campaigns_tool()


@tool("nl_growth_trend", "nl_growth_trend(days?) — daily subscriber growth and unsubscribe trend.")
def _nl_growth_trend(agent: str, days: int = 30, **_: Any) -> str:
    return _nl.growth_trend_tool(days)


@tool("nl_create_sequence", "nl_create_sequence(name, description?, trigger?, list_name?) — create an email automation sequence.")
def _nl_create_sequence(agent: str, name: str = "", description: str = "", trigger: str = "signup", list_name: str = "main", **_: Any) -> str:
    return _nl.create_sequence_tool(name, description, trigger, list_name)


# --------------------------------------------------------------------------
# Affiliate tracker tools
# --------------------------------------------------------------------------
from . import affiliate_tracker as _aff  # noqa: E402


@tool("aff_add_program", "aff_add_program(name, network?, commission_rate?, commission_type?, preset?) — add an affiliate program. Use preset='amazon', 'clickbank', 'shareasale' etc. for defaults.")
def _aff_add_program(agent: str, name: str = "", network: str = "", commission_rate: float = 0.0, commission_type: str = "percentage", preset: str = "", **_: Any) -> str:
    return _aff.add_program_tool(name, network, commission_rate, commission_type, preset)


@tool("aff_create_link", "aff_create_link(program_name, destination_url, content_id?, campaign?) — create a tracked affiliate link with automatic commission tracking.")
def _aff_create_link(agent: str, program_name: str = "", destination_url: str = "", content_id: str = "", campaign: str = "", **_: Any) -> str:
    return _aff.create_link_tool(program_name, destination_url, content_id, campaign)


@tool("aff_record_conversion", "aff_record_conversion(link_id, sale_amount, order_id?) — record an affiliate sale conversion.")
def _aff_record_conversion(agent: str, link_id: int = 0, sale_amount: float = 0.0, order_id: str = "", **_: Any) -> str:
    return _aff.record_conversion_tool(link_id, sale_amount, order_id)


@tool("aff_summary", "aff_summary() — overall affiliate program performance summary.")
def _aff_summary(agent: str, **_: Any) -> str:
    return _aff.affiliate_summary_tool()


@tool("aff_program_performance", "aff_program_performance(days?) — revenue and commissions per affiliate program.")
def _aff_program_performance(agent: str, days: int = 30, **_: Any) -> str:
    return _aff.program_performance_tool(days)


@tool("aff_top_links", "aff_top_links(days?, limit?) — top performing affiliate links by commission earned.")
def _aff_top_links(agent: str, days: int = 30, limit: int = 10, **_: Any) -> str:
    return _aff.top_links_tool(days, limit)


@tool("aff_list_programs", "aff_list_programs() — list all active affiliate programs.")
def _aff_list_programs(agent: str, **_: Any) -> str:
    return _aff.list_programs_tool()


# --------------------------------------------------------------------------
# SEO optimizer tools
# --------------------------------------------------------------------------
from . import seo_optimizer as _seo_opt  # noqa: E402


@tool("seo_analyze", "seo_analyze(content, title?, meta_description?, target_keyword?, url?) — analyze content for on-page SEO score, issues, and suggestions.")
def _seo_analyze(agent: str, content: str = "", title: str = "", meta_description: str = "", target_keyword: str = "", url: str = "", **_: Any) -> str:
    return _seo_opt.analyze_content_tool(content, title, meta_description, target_keyword, url)


@tool("seo_generate_title", "seo_generate_title(topic, keyword?, style?) — generate 5 SEO-optimized title tag variations. Styles: standard, listicle, question, ecommerce.")
def _seo_generate_title(agent: str, topic: str = "", keyword: str = "", style: str = "standard", **_: Any) -> str:
    return _seo_opt.generate_title_tool(topic, keyword, style)


@tool("seo_generate_meta", "seo_generate_meta(topic, keyword?, cta?) — generate 5 meta description variations (120-160 chars).")
def _seo_generate_meta(agent: str, topic: str = "", keyword: str = "", cta: str = "Learn more", **_: Any) -> str:
    return _seo_opt.generate_meta_tool(topic, keyword, cta)


@tool("seo_faq_schema", "seo_faq_schema(faqs_json) — generate FAQ Page JSON-LD schema from a JSON array of {question, answer} objects.")
def _seo_faq_schema(agent: str, faqs_json: str = "", **_: Any) -> str:
    return _seo_opt.generate_faq_schema_tool(faqs_json)


@tool("seo_site_overview", "seo_site_overview() — SEO health summary across all tracked pages: average score, grade breakdown, orphan pages.")
def _seo_site_overview(agent: str, **_: Any) -> str:
    return _seo_opt.site_overview_tool()


@tool("seo_pages_needing_work", "seo_pages_needing_work(score_threshold?, limit?) — list pages below the SEO score threshold, ordered by lowest score.")
def _seo_pages_needing_work(agent: str, score_threshold: float = 70.0, limit: int = 10, **_: Any) -> str:
    return _seo_opt.pages_needing_work_tool(score_threshold, limit)


@tool("seo_add_redirect", "seo_add_redirect(from_url, to_url, reason?) — add a 301 URL redirect rule.")
def _seo_add_redirect(agent: str, from_url: str = "", to_url: str = "", reason: str = "", **_: Any) -> str:
    return _seo_opt.add_redirect_tool(from_url, to_url, reason)


@tool("seo_orphan_pages", "seo_orphan_pages() — find pages with no internal links pointing to them.")
def _seo_orphan_pages(agent: str, **_: Any) -> str:
    return _seo_opt.orphan_pages_tool()


# --------------------------------------------------------------------------
# Funnel builder tools
# --------------------------------------------------------------------------
from . import funnel_builder as _fb  # noqa: E402


@tool("funnel_create", "funnel_create(name, funnel_type?, description?) — create a new sales funnel.")
def _funnel_create(agent: str, name: str = "", funnel_type: str = "lead_gen", description: str = "", **_: Any) -> str:
    return _fb.create_funnel_tool(name, funnel_type, description)


@tool("funnel_add_step", "funnel_add_step(funnel_id, name, step_type?, url?, headline?, price?) — add a step to a funnel.")
def _funnel_add_step(agent: str, funnel_id: int = 0, name: str = "", step_type: str = "optin", url: str = "", headline: str = "", price: float = 0.0, **_: Any) -> str:
    return _fb.add_step_tool(funnel_id, name, step_type, url, headline, price)


@tool("funnel_stats", "funnel_stats(funnel_id) — full analytics for a funnel including step conversion rates and revenue.")
def _funnel_stats(agent: str, funnel_id: int = 0, **_: Any) -> str:
    return _fb.funnel_stats_tool(funnel_id)


@tool("funnel_list", "funnel_list(status?) — list all funnels, optionally filtered by status.")
def _funnel_list(agent: str, status: str = "", **_: Any) -> str:
    return _fb.list_funnels_tool(status)


@tool("funnel_best", "funnel_best(limit?) — list best performing funnels by conversion rate.")
def _funnel_best(agent: str, limit: int = 10, **_: Any) -> str:
    return _fb.best_funnels_tool(limit)


@tool("funnel_drop_off", "funnel_drop_off(funnel_id) — step-by-step drop-off analysis for a funnel.")
def _funnel_drop_off(agent: str, funnel_id: int = 0, **_: Any) -> str:
    return _fb.drop_off_tool(funnel_id)


@tool("funnel_from_template", "funnel_from_template(template_name, funnel_name?) — build a funnel from a built-in template.")
def _funnel_from_template(agent: str, template_name: str = "", funnel_name: str = "", **_: Any) -> str:
    return _fb.template_tool(template_name, funnel_name)


# --------------------------------------------------------------------------
# Product manager tools
# --------------------------------------------------------------------------
from . import product_manager as _pm  # noqa: E402


@tool("pm_create_product", "pm_create_product(name, product_type?, price?, description?, platform?) — add a product to the catalog.")
def _pm_create_product(agent: str, name: str = "", product_type: str = "ebook", price: float = 0.0, description: str = "", platform: str = "", **_: Any) -> str:
    return _pm.create_product_tool(name, product_type, price, description, platform)


@tool("pm_record_sale", "pm_record_sale(product_id, amount, customer_email?, platform?, utm_source?, utm_campaign?) — record a product sale.")
def _pm_record_sale(agent: str, product_id: int = 0, amount: float = 0.0, customer_email: str = "", platform: str = "", utm_source: str = "", utm_campaign: str = "", **_: Any) -> str:
    return _pm.record_sale_tool(product_id, amount, customer_email, platform, utm_source, utm_campaign)


@tool("pm_product_report", "pm_product_report(product_id, since?, until?) — revenue report for a specific product.")
def _pm_product_report(agent: str, product_id: int = 0, since: str = "", until: str = "", **_: Any) -> str:
    return _pm.product_report_tool(product_id, since, until)


@tool("pm_best_sellers", "pm_best_sellers(limit?, metric?, since?) — top selling products by revenue or units.")
def _pm_best_sellers(agent: str, limit: int = 10, metric: str = "revenue", since: str = "", **_: Any) -> str:
    return _pm.best_sellers_tool(limit, metric, since)


@tool("pm_catalog_summary", "pm_catalog_summary() — high-level product catalog summary with revenue totals.")
def _pm_catalog_summary(agent: str, **_: Any) -> str:
    return _pm.catalog_summary_tool()


@tool("pm_list_products", "pm_list_products(status?, product_type?, limit?) — list all products with optional filters.")
def _pm_list_products(agent: str, status: str = "", product_type: str = "", limit: int = 50, **_: Any) -> str:
    return _pm.list_products_tool(status, product_type, limit)


@tool("pm_add_review", "pm_add_review(product_id, rating, reviewer?, title?, body?) — add a customer review to a product.")
def _pm_add_review(agent: str, product_id: int = 0, rating: int = 5, reviewer: str = "", title: str = "", body: str = "", **_: Any) -> str:
    return _pm.add_review_tool(product_id, rating, reviewer, title, body)


@tool("pm_utm_attribution", "pm_utm_attribution(since?) — revenue attribution by UTM source, medium, and campaign.")
def _pm_utm_attribution(agent: str, since: str = "", **_: Any) -> str:
    return _pm.utm_attribution_tool(since)


# --------------------------------------------------------------------------
# Link builder tools
# --------------------------------------------------------------------------
from . import link_builder as _lb  # noqa: E402


@tool("lb_create_link", "lb_create_link(destination, utm_source?, utm_medium?, utm_campaign?, title?) — create a UTM tracked link.")
def _lb_create_link(agent: str, destination: str = "", utm_source: str = "", utm_medium: str = "", utm_campaign: str = "", title: str = "", **_: Any) -> str:
    return _lb.create_link_tool(destination, utm_source, utm_medium, utm_campaign, title)


@tool("lb_campaign_performance", "lb_campaign_performance(campaign_name) — aggregate click stats for all links in a campaign.")
def _lb_campaign_performance(agent: str, campaign_name: str = "", **_: Any) -> str:
    return _lb.campaign_performance_tool(campaign_name)


@tool("lb_top_links", "lb_top_links(limit?, since?) — highest-performing tracked links by clicks.")
def _lb_top_links(agent: str, limit: int = 20, since: str = "", **_: Any) -> str:
    return _lb.top_links_tool(limit, since)


@tool("lb_create_short_link", "lb_create_short_link(long_url, title?) — shorten a URL.")
def _lb_create_short_link(agent: str, long_url: str = "", title: str = "", **_: Any) -> str:
    return _lb.create_short_link_tool(long_url, title)


@tool("lb_create_bio_page", "lb_create_bio_page(handle, title?, bio?) — create a link-in-bio landing page.")
def _lb_create_bio_page(agent: str, handle: str = "", title: str = "", bio: str = "", **_: Any) -> str:
    return _lb.create_bio_page_tool(handle, title, bio)


@tool("lb_add_bio_link", "lb_add_bio_link(handle, title, url, description?) — add a link to a link-in-bio page.")
def _lb_add_bio_link(agent: str, handle: str = "", title: str = "", url: str = "", description: str = "", **_: Any) -> str:
    return _lb.add_bio_link_tool(handle, title, url, description)


@tool("lb_link_summary", "lb_link_summary() — overall link manager summary stats.")
def _lb_link_summary(agent: str, **_: Any) -> str:
    return _lb.link_summary_tool()


@tool("lb_generate_utm", "lb_generate_utm(base_url, utm_source, utm_medium, utm_campaign, utm_content?) — build a UTM URL without database tracking.")
def _lb_generate_utm(agent: str, base_url: str = "", utm_source: str = "", utm_medium: str = "", utm_campaign: str = "", utm_content: str = "", **_: Any) -> str:
    return _lb.generate_utm_tool(base_url, utm_source, utm_medium, utm_campaign, utm_content)


@tool("lb_bulk_create_links", "lb_bulk_create_links(destination, sources, medium?, campaign?) — create one tracked link per UTM source.")
def _lb_bulk_create_links(agent: str, destination: str = "", sources: str = "", medium: str = "social", campaign: str = "", **_: Any) -> str:
    return _lb.bulk_create_links_tool(destination, sources, medium, campaign)


# --------------------------------------------------------------------------
# Reporting engine tools
# --------------------------------------------------------------------------
from . import reporting_engine as _rpt  # noqa: E402


@tool("rpt_create_report", "rpt_create_report(name, report_type?, frequency?, output_format?, description?) — register a new scheduled report.")
def _rpt_create_report(agent: str, name: str = "", report_type: str = "executive_summary", frequency: str = "weekly", output_format: str = "markdown", description: str = "", **_: Any) -> str:
    return _rpt.create_report_tool(name, report_type, frequency, output_format, description)


@tool("rpt_run_report", "rpt_run_report(report_name, output_format?) — execute a report immediately.")
def _rpt_run_report(agent: str, report_name: str = "", output_format: str = "", **_: Any) -> str:
    return _rpt.run_report_tool(report_name, output_format)


@tool("rpt_adhoc_report", "rpt_adhoc_report(report_type?, output_format?, title?) — generate a one-off report.")
def _rpt_adhoc_report(agent: str, report_type: str = "executive_summary", output_format: str = "markdown", title: str = "", **_: Any) -> str:
    return _rpt.adhoc_report_tool(report_type, output_format, title)


@tool("rpt_list_reports", "rpt_list_reports() — list all defined reports and their schedule.")
def _rpt_list_reports(agent: str, **_: Any) -> str:
    return _rpt.list_reports_tool()


@tool("rpt_run_history", "rpt_run_history(report_name?, limit?) — recent report execution history.")
def _rpt_run_history(agent: str, report_name: str = "", limit: int = 20, **_: Any) -> str:
    return _rpt.run_history_tool(report_name, limit)


@tool("rpt_get_content", "rpt_get_content(run_ref) — retrieve full content of a past report run.")
def _rpt_get_content(agent: str, run_ref: str = "", **_: Any) -> str:
    return _rpt.get_content_tool(run_ref)


@tool("rpt_seed_defaults", "rpt_seed_defaults() — create the default set of scheduled reports.")
def _rpt_seed_defaults(agent: str, **_: Any) -> str:
    return _rpt.seed_defaults_tool()


# --------------------------------------------------------------------------
# Pricing engine tools
# --------------------------------------------------------------------------
from . import pricing_engine as _pe  # noqa: E402


@tool("pe_create_rule", "pe_create_rule(name, base_price, pricing_model?, billing_interval?, product_id?) — create a pricing rule.")
def _pe_create_rule(agent: str, name: str = "", base_price: float = 0.0, pricing_model: str = "flat", billing_interval: str = "once", product_id: int = 0, **_: Any) -> str:
    return _pe.create_rule_tool(name, base_price, pricing_model, billing_interval, product_id)


@tool("pe_resolve_price", "pe_resolve_price(product_id, quantity?, coupon_code?) — determine the correct price given context.")
def _pe_resolve_price(agent: str, product_id: int = 0, quantity: float = 1.0, coupon_code: str = "", **_: Any) -> str:
    return _pe.resolve_price_tool(product_id, quantity, coupon_code)


@tool("pe_create_price_test", "pe_create_price_test(name, variant_a_price, variant_b_price, product_id?) — start an A/B price test.")
def _pe_create_price_test(agent: str, name: str = "", variant_a_price: float = 0.0, variant_b_price: float = 0.0, product_id: int = 0, **_: Any) -> str:
    return _pe.create_price_test_tool(name, variant_a_price, variant_b_price, product_id)


@tool("pe_analyze_price_test", "pe_analyze_price_test(test_name) — statistical analysis of a running price test.")
def _pe_analyze_price_test(agent: str, test_name: str = "", **_: Any) -> str:
    return _pe.analyze_test_tool(test_name)


@tool("pe_recommend_price", "pe_recommend_price(product_id, segment?) — get price recommendations based on LTV and historical data.")
def _pe_recommend_price(agent: str, product_id: int = 0, segment: str = "default", **_: Any) -> str:
    return _pe.recommend_price_tool(product_id, segment)


@tool("pe_create_discount", "pe_create_discount(name, discount_type?, discount_value?, end_at?, usage_limit?) — create a discount rule.")
def _pe_create_discount(agent: str, name: str = "", discount_type: str = "percent", discount_value: float = 10.0, end_at: str = "", usage_limit: int = 0, **_: Any) -> str:
    return _pe.create_discount_tool(name, discount_type, discount_value, end_at, usage_limit)


@tool("pe_pricing_summary", "pe_pricing_summary() — overview of all pricing rules, A/B tests, and discounts.")
def _pe_pricing_summary(agent: str, **_: Any) -> str:
    return _pe.pricing_summary_tool()


@tool("pe_upsert_ltv", "pe_upsert_ltv(segment, avg_ltv, avg_order_value?, churn_rate?, acquisition_cost?) — update LTV estimate for a customer segment.")
def _pe_upsert_ltv(agent: str, segment: str = "", avg_ltv: float = 0.0, avg_order_value: float = 0.0, churn_rate: float = 0.0, acquisition_cost: float = 0.0, **_: Any) -> str:
    return _pe.upsert_ltv_tool(segment, avg_ltv, avg_order_value, churn_rate, acquisition_cost)


# ---------------------------------------------------------------------------
# Auto-register tools from domain modules via their @tool decorators
# ---------------------------------------------------------------------------
from . import email_marketing as _em  # noqa: E402, F401
from . import course_builder as _cb  # noqa: E402, F401
from . import webhook_manager as _wh  # noqa: E402, F401
from . import traffic_analyzer as _ta  # noqa: E402, F401
