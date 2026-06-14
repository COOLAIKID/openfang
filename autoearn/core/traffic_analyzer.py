"""
Traffic Analyzer — web traffic tracking, analytics, goal management, and heatmaps.

Provides session tracking, pageview recording, event tracking, goal funnels,
UTM attribution, geo/device breakdowns, conversion funnels, and heatmap data.
All data stored in SQLite. Tools allow agents to record and query traffic data.

Usage::

    session = start_session(ip_hash="abc123", user_agent="Mozilla/5.0...",
                            landing_page="/", utm_source="google")
    record_pageview(session.session_id, "/blog/post-1", "My Post")
    track_event(session.session_id, "signup", "conversion", revenue=29.0)
    summary = traffic_summary(days=7)
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEVICE_TYPES = ["desktop", "mobile", "tablet", "bot", "unknown"]
GOAL_TYPES = ["pageview", "event", "duration", "scroll_depth", "custom"]
EVENT_CATEGORIES = [
    "engagement", "conversion", "ecommerce", "navigation",
    "social", "media", "form", "error",
]

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_schema_ready = False


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


def _init_schema() -> None:
    conn = _db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS traffic_sessions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id       TEXT NOT NULL UNIQUE,
                visitor_id       TEXT NOT NULL DEFAULT '',
                ip_hash          TEXT NOT NULL DEFAULT '',
                user_agent       TEXT NOT NULL DEFAULT '',
                device_type      TEXT NOT NULL DEFAULT 'unknown',
                os               TEXT NOT NULL DEFAULT '',
                browser          TEXT NOT NULL DEFAULT '',
                country          TEXT NOT NULL DEFAULT '',
                region           TEXT NOT NULL DEFAULT '',
                city             TEXT NOT NULL DEFAULT '',
                referrer         TEXT NOT NULL DEFAULT '',
                utm_source       TEXT NOT NULL DEFAULT '',
                utm_medium       TEXT NOT NULL DEFAULT '',
                utm_campaign     TEXT NOT NULL DEFAULT '',
                utm_term         TEXT NOT NULL DEFAULT '',
                landing_page     TEXT NOT NULL DEFAULT '',
                exit_page        TEXT NOT NULL DEFAULT '',
                page_views       INTEGER NOT NULL DEFAULT 0,
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                bounced          INTEGER NOT NULL DEFAULT 0,
                converted        INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                last_seen_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS traffic_pageviews (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          TEXT NOT NULL,
                page_url            TEXT NOT NULL DEFAULT '',
                page_title          TEXT NOT NULL DEFAULT '',
                referrer            TEXT NOT NULL DEFAULT '',
                time_on_page_seconds INTEGER NOT NULL DEFAULT 0,
                scroll_depth_pct    INTEGER NOT NULL DEFAULT 0,
                exit_intent         INTEGER NOT NULL DEFAULT 0,
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS traffic_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id     TEXT NOT NULL,
                event_name     TEXT NOT NULL DEFAULT '',
                event_category TEXT NOT NULL DEFAULT '',
                properties     TEXT NOT NULL DEFAULT '{}',
                revenue        REAL NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS traffic_goals (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE,
                goal_type    TEXT NOT NULL DEFAULT 'pageview',
                target_url   TEXT NOT NULL DEFAULT '',
                target_event TEXT NOT NULL DEFAULT '',
                target_value REAL NOT NULL DEFAULT 0,
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS traffic_goal_completions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id    INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                value      REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS traffic_heatmap_data (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                page_url         TEXT NOT NULL,
                element_selector TEXT NOT NULL,
                click_count      INTEGER NOT NULL DEFAULT 0,
                hover_count      INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT NOT NULL,
                updated_at       TEXT NOT NULL,
                UNIQUE(page_url, element_selector)
            );

            CREATE INDEX IF NOT EXISTS idx_ts_session_id   ON traffic_sessions(session_id);
            CREATE INDEX IF NOT EXISTS idx_ts_created_at   ON traffic_sessions(created_at);
            CREATE INDEX IF NOT EXISTS idx_ts_visitor_id   ON traffic_sessions(visitor_id);
            CREATE INDEX IF NOT EXISTS idx_ts_utm_source   ON traffic_sessions(utm_source);
            CREATE INDEX IF NOT EXISTS idx_ts_country      ON traffic_sessions(country);
            CREATE INDEX IF NOT EXISTS idx_tp_session_id   ON traffic_pageviews(session_id);
            CREATE INDEX IF NOT EXISTS idx_tp_page_url     ON traffic_pageviews(page_url);
            CREATE INDEX IF NOT EXISTS idx_tp_created_at   ON traffic_pageviews(created_at);
            CREATE INDEX IF NOT EXISTS idx_te_session_id   ON traffic_events(session_id);
            CREATE INDEX IF NOT EXISTS idx_te_event_name   ON traffic_events(event_name);
            CREATE INDEX IF NOT EXISTS idx_te_created_at   ON traffic_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_tgc_goal_id     ON traffic_goal_completions(goal_id);
            CREATE INDEX IF NOT EXISTS idx_tgc_session_id  ON traffic_goal_completions(session_id);
            CREATE INDEX IF NOT EXISTS idx_thd_page_url    ON traffic_heatmap_data(page_url);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class TrafficSession:
    id: int
    session_id: str
    visitor_id: str
    device_type: str
    country: str
    landing_page: str
    page_views: int
    duration_seconds: int
    bounced: int
    converted: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "visitor_id": self.visitor_id,
            "device_type": self.device_type,
            "country": self.country,
            "landing_page": self.landing_page,
            "page_views": self.page_views,
            "duration_seconds": self.duration_seconds,
            "bounced": bool(self.bounced),
            "converted": bool(self.converted),
            "bounce_rate": self.bounce_rate,
            "created_at": self.created_at,
        }

    @property
    def bounce_rate(self) -> int:
        return 100 if self.bounced else 0


@dataclass
class PageView:
    id: int
    session_id: str
    page_url: str
    page_title: str
    time_on_page_seconds: int
    scroll_depth_pct: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "page_url": self.page_url,
            "page_title": self.page_title,
            "time_on_page_seconds": self.time_on_page_seconds,
            "scroll_depth_pct": self.scroll_depth_pct,
            "created_at": self.created_at,
        }


@dataclass
class TrafficGoal:
    id: int
    name: str
    goal_type: str
    target_url: str
    target_event: str
    target_value: float
    enabled: int
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "goal_type": self.goal_type,
            "target_url": self.target_url,
            "target_event": self.target_event,
            "target_value": self.target_value,
            "enabled": bool(self.enabled),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _since_iso(days: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.isoformat()


def _row_to_session(row: sqlite3.Row) -> TrafficSession:
    return TrafficSession(
        id=row["id"],
        session_id=row["session_id"],
        visitor_id=row["visitor_id"],
        device_type=row["device_type"],
        country=row["country"],
        landing_page=row["landing_page"],
        page_views=row["page_views"],
        duration_seconds=row["duration_seconds"],
        bounced=row["bounced"],
        converted=row["converted"],
        created_at=row["created_at"],
    )


def _row_to_pageview(row: sqlite3.Row) -> PageView:
    return PageView(
        id=row["id"],
        session_id=row["session_id"],
        page_url=row["page_url"],
        page_title=row["page_title"],
        time_on_page_seconds=row["time_on_page_seconds"],
        scroll_depth_pct=row["scroll_depth_pct"],
        created_at=row["created_at"],
    )


def _row_to_goal(row: sqlite3.Row) -> TrafficGoal:
    return TrafficGoal(
        id=row["id"],
        name=row["name"],
        goal_type=row["goal_type"],
        target_url=row["target_url"],
        target_event=row["target_event"],
        target_value=row["target_value"],
        enabled=row["enabled"],
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# User-agent parsing
# ---------------------------------------------------------------------------

# Bot signatures (checked first)
_BOT_RE = re.compile(
    r"(bot|crawl|spider|slurp|scrape|fetcher|archiver|scanner|"
    r"Googlebot|Bingbot|Baiduspider|YandexBot|DuckDuckBot|ia_archiver|"
    r"facebookexternalhit|Twitterbot|LinkedInBot|Pinterest|WhatsApp|"
    r"Applebot|SemrushBot|AhrefsBot|MJ12bot|dotbot|rogerbot)",
    re.IGNORECASE,
)

# OS detection patterns (order matters)
_OS_PATTERNS: List[tuple] = [
    ("Windows 11",  re.compile(r"Windows NT 10\.0.*Win64", re.I)),
    ("Windows 10",  re.compile(r"Windows NT 10\.0", re.I)),
    ("Windows 8.1", re.compile(r"Windows NT 6\.3", re.I)),
    ("Windows 8",   re.compile(r"Windows NT 6\.2", re.I)),
    ("Windows 7",   re.compile(r"Windows NT 6\.1", re.I)),
    ("Windows",     re.compile(r"Windows", re.I)),
    ("macOS",       re.compile(r"Mac OS X|Macintosh", re.I)),
    ("iOS",         re.compile(r"\(iP(hone|od|ad)", re.I)),
    ("Android",     re.compile(r"Android", re.I)),
    ("Linux",       re.compile(r"Linux", re.I)),
    ("ChromeOS",    re.compile(r"CrOS", re.I)),
]

# Browser detection patterns (order matters — more specific first)
_BROWSER_PATTERNS: List[tuple] = [
    ("Edge",       re.compile(r"Edg/|Edge/", re.I)),
    ("Opera",      re.compile(r"OPR/|Opera/", re.I)),
    ("Samsung",    re.compile(r"SamsungBrowser/", re.I)),
    ("Chrome",     re.compile(r"Chrome/(?!.*Chromium)", re.I)),
    ("Chromium",   re.compile(r"Chromium/", re.I)),
    ("Firefox",    re.compile(r"Firefox/", re.I)),
    ("Safari",     re.compile(r"Safari/(?!.*Chrome)", re.I)),
    ("IE",         re.compile(r"MSIE |Trident/", re.I)),
]

# Tablet signals
_TABLET_RE = re.compile(r"iPad|Tablet|tablet|Kindle|PlayBook|Silk", re.I)
# Mobile signals
_MOBILE_RE = re.compile(
    r"Mobile|Android(?!.*Tablet)|iPhone|iPod|BlackBerry|IEMobile|"
    r"Opera Mini|Windows Phone",
    re.I,
)


def _parse_user_agent(ua_string: str) -> Dict[str, str]:
    """Parse a User-Agent string into device_type, os, and browser."""
    if not ua_string:
        return {"device_type": "unknown", "os": "", "browser": ""}

    if _BOT_RE.search(ua_string):
        return {"device_type": "bot", "os": "", "browser": "Bot"}

    # OS
    detected_os = ""
    for name, pat in _OS_PATTERNS:
        if pat.search(ua_string):
            detected_os = name
            break

    # Browser
    detected_browser = ""
    for name, pat in _BROWSER_PATTERNS:
        if pat.search(ua_string):
            detected_browser = name
            break

    # Device type
    if _TABLET_RE.search(ua_string) or (
        "iPad" in ua_string or "Tablet" in ua_string
    ):
        device_type = "tablet"
    elif _MOBILE_RE.search(ua_string):
        device_type = "mobile"
    elif detected_os in ("iOS", "Android") and not _TABLET_RE.search(ua_string):
        device_type = "mobile"
    elif detected_os:
        device_type = "desktop"
    else:
        device_type = "unknown"

    return {
        "device_type": device_type,
        "os": detected_os,
        "browser": detected_browser,
    }


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def start_session(
    ip_hash: str,
    user_agent: str,
    landing_page: str,
    referrer: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    utm_term: str = "",
) -> TrafficSession:
    """Create a new traffic session. Returns a TrafficSession dataclass."""
    _ensure()
    ua_info = _parse_user_agent(user_agent)
    session_id = str(uuid.uuid4())
    visitor_id = hashlib.sha256(ip_hash.encode()).hexdigest()[:16]
    now = _now_iso()

    conn = _db()
    try:
        conn.execute(
            """INSERT INTO traffic_sessions
               (session_id, visitor_id, ip_hash, user_agent, device_type, os,
                browser, referrer, utm_source, utm_medium, utm_campaign, utm_term,
                landing_page, page_views, duration_seconds, bounced, converted,
                created_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,1,0,?,?)""",
            (
                session_id, visitor_id, ip_hash, user_agent,
                ua_info["device_type"], ua_info["os"], ua_info["browser"],
                referrer, utm_source, utm_medium, utm_campaign, utm_term,
                landing_page, now, now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM traffic_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return _row_to_session(row)
    finally:
        conn.close()


def get_session(session_id: str) -> Optional[TrafficSession]:
    """Return a TrafficSession by session_id, or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM traffic_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return _row_to_session(row) if row else None
    finally:
        conn.close()


def update_session(session_id: str, **fields: Any) -> bool:
    """Update arbitrary columns on a session row. Returns True on success."""
    _ensure()
    allowed = {
        "exit_page", "page_views", "duration_seconds", "bounced", "converted",
        "country", "region", "city", "last_seen_at",
    }
    pairs = {k: v for k, v in fields.items() if k in allowed}
    if not pairs:
        return False
    set_clause = ", ".join(f"{k}=?" for k in pairs)
    values = list(pairs.values()) + [session_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE traffic_sessions SET {set_clause} WHERE session_id=?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def end_session(session_id: str) -> Dict[str, Any]:
    """Mark a session as ended and return final stats."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM traffic_sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        if not row:
            return {"error": "session not found", "session_id": session_id}

        # If only one page view → bounced
        bounced = 1 if row["page_views"] <= 1 else 0
        now = _now_iso()
        conn.execute(
            "UPDATE traffic_sessions SET bounced=?, last_seen_at=? WHERE session_id=?",
            (bounced, now, session_id),
        )
        conn.commit()

        pv_count = conn.execute(
            "SELECT COUNT(*) FROM traffic_pageviews WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM traffic_events WHERE session_id=?",
            (session_id,),
        ).fetchone()[0]
        return {
            "session_id": session_id,
            "page_views": row["page_views"],
            "duration_seconds": row["duration_seconds"],
            "bounced": bool(bounced),
            "converted": bool(row["converted"]),
            "pageview_records": pv_count,
            "event_records": ev_count,
            "ended_at": now,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Page tracking
# ---------------------------------------------------------------------------

def record_pageview(
    session_id: str,
    page_url: str,
    page_title: str = "",
    referrer: str = "",
    time_on_page: int = 0,
    scroll_depth: int = 0,
) -> int:
    """Record a pageview for a session. Returns the pageview row id."""
    _ensure()
    now = _now_iso()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO traffic_pageviews
               (session_id, page_url, page_title, referrer,
                time_on_page_seconds, scroll_depth_pct, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (session_id, page_url, page_title, referrer,
             time_on_page, scroll_depth, now),
        )
        pv_id = cur.lastrowid

        # Update session: increment page_views, update exit_page, unbounce if >1
        row = conn.execute(
            "SELECT page_views FROM traffic_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is not None:
            new_pv = row["page_views"] + 1
            duration_add = time_on_page
            conn.execute(
                """UPDATE traffic_sessions
                   SET page_views=?, exit_page=?,
                       duration_seconds=duration_seconds+?,
                       bounced=?, last_seen_at=?
                   WHERE session_id=?""",
                (new_pv, page_url, duration_add,
                 1 if new_pv <= 1 else 0, now, session_id),
            )
        conn.commit()
        return pv_id
    finally:
        conn.close()


def get_pageviews(session_id: str) -> List[Dict[str, Any]]:
    """Return all pageviews for a session as a list of dicts."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM traffic_pageviews WHERE session_id=? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [_row_to_pageview(r).to_dict() for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Event tracking
# ---------------------------------------------------------------------------

def track_event(
    session_id: str,
    event_name: str,
    event_category: str = "engagement",
    properties: Optional[Dict[str, Any]] = None,
    revenue: float = 0.0,
) -> int:
    """Record a named event for a session. Returns the event row id."""
    _ensure()
    now = _now_iso()
    props_json = json.dumps(properties or {})
    if event_category not in EVENT_CATEGORIES:
        event_category = "engagement"
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO traffic_events
               (session_id, event_name, event_category, properties, revenue, created_at)
               VALUES (?,?,?,?,?,?)""",
            (session_id, event_name, event_category, props_json, revenue, now),
        )
        ev_id = cur.lastrowid
        if revenue > 0:
            conn.execute(
                "UPDATE traffic_sessions SET converted=1 WHERE session_id=?",
                (session_id,),
            )
        conn.commit()
        return ev_id
    finally:
        conn.close()


def get_events(
    session_id: Optional[str] = None,
    event_name: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return events filtered by session_id and/or event_name."""
    _ensure()
    clauses: List[str] = []
    params: List[Any] = []
    if session_id:
        clauses.append("session_id=?")
        params.append(session_id)
    if event_name:
        clauses.append("event_name=?")
        params.append(event_name)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM traffic_events {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "session_id": r["session_id"],
                "event_name": r["event_name"],
                "event_category": r["event_category"],
                "properties": json.loads(r["properties"] or "{}"),
                "revenue": r["revenue"],
                "created_at": r["created_at"],
            })
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Goal management
# ---------------------------------------------------------------------------

def create_goal(
    name: str,
    goal_type: str,
    target_url: str = "",
    target_event: str = "",
    target_value: float = 0.0,
) -> TrafficGoal:
    """Create a new conversion goal. Returns a TrafficGoal dataclass."""
    _ensure()
    if goal_type not in GOAL_TYPES:
        goal_type = "pageview"
    now = _now_iso()
    conn = _db()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO traffic_goals
               (name, goal_type, target_url, target_event, target_value, enabled, created_at)
               VALUES (?,?,?,?,?,1,?)""",
            (name, goal_type, target_url, target_event, target_value, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM traffic_goals WHERE name=?", (name,)
        ).fetchone()
        return _row_to_goal(row)
    finally:
        conn.close()


def get_goal(name: str) -> Optional[TrafficGoal]:
    """Return a TrafficGoal by name, or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM traffic_goals WHERE name=?", (name,)
        ).fetchone()
        return _row_to_goal(row) if row else None
    finally:
        conn.close()


def list_goals(enabled_only: bool = True) -> List[TrafficGoal]:
    """Return all goals, optionally filtered to enabled ones."""
    _ensure()
    conn = _db()
    try:
        query = "SELECT * FROM traffic_goals"
        if enabled_only:
            query += " WHERE enabled=1"
        query += " ORDER BY created_at"
        rows = conn.execute(query).fetchall()
        return [_row_to_goal(r) for r in rows]
    finally:
        conn.close()


def check_goal_completion(session_id: str, goal_id: int) -> bool:
    """Check if a session already triggered a specific goal."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            """SELECT COUNT(*) FROM traffic_goal_completions
               WHERE session_id=? AND goal_id=?""",
            (session_id, goal_id),
        ).fetchone()
        return row[0] > 0
    finally:
        conn.close()


def record_goal_completion(
    session_id: str, goal_id: int, value: float = 0.0
) -> bool:
    """Record that a session completed a goal. Returns True on success."""
    _ensure()
    now = _now_iso()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO traffic_goal_completions
               (goal_id, session_id, value, created_at) VALUES (?,?,?,?)""",
            (goal_id, session_id, value, now),
        )
        conn.execute(
            "UPDATE traffic_sessions SET converted=1 WHERE session_id=?",
            (session_id,),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def goal_conversion_rate(goal_id: int, days: int = 30) -> float:
    """Return the conversion rate (0.0–1.0) for a goal over recent days."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        total_sessions = conn.execute(
            "SELECT COUNT(*) FROM traffic_sessions WHERE created_at>=?", (since,)
        ).fetchone()[0]
        completions = conn.execute(
            """SELECT COUNT(DISTINCT session_id) FROM traffic_goal_completions
               WHERE goal_id=? AND created_at>=?""",
            (goal_id, since),
        ).fetchone()[0]
        if total_sessions == 0:
            return 0.0
        return round(completions / total_sessions, 4)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def traffic_summary(days: int = 30) -> Dict[str, Any]:
    """Return a high-level traffic summary for the given date range."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        sessions_row = conn.execute(
            """SELECT COUNT(*) as total,
                      COUNT(DISTINCT visitor_id) as unique_v,
                      SUM(page_views) as pv,
                      AVG(CASE WHEN duration_seconds>0 THEN duration_seconds END) as avg_dur,
                      SUM(bounced) as bounces
               FROM traffic_sessions WHERE created_at>=?""",
            (since,),
        ).fetchone()

        total_sessions = sessions_row["total"] or 0
        unique_visitors = sessions_row["unique_v"] or 0
        page_views = sessions_row["pv"] or 0
        avg_dur = round(sessions_row["avg_dur"] or 0, 1)
        bounces = sessions_row["bounces"] or 0
        bounce_rate = round(bounces / max(1, total_sessions) * 100, 2)

        top_pages_rows = conn.execute(
            """SELECT page_url, COUNT(*) as views
               FROM traffic_pageviews WHERE created_at>=?
               GROUP BY page_url ORDER BY views DESC LIMIT 10""",
            (since,),
        ).fetchall()
        top_pages_data = [{"page_url": r["page_url"], "views": r["views"]}
                          for r in top_pages_rows]

        top_referrers_rows = conn.execute(
            """SELECT referrer, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=? AND referrer!=''
               GROUP BY referrer ORDER BY cnt DESC LIMIT 10""",
            (since,),
        ).fetchall()
        top_referrers_data = [{"referrer": r["referrer"], "count": r["cnt"]}
                               for r in top_referrers_rows]

        device_rows = conn.execute(
            """SELECT device_type, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=?
               GROUP BY device_type ORDER BY cnt DESC""",
            (since,),
        ).fetchall()
        device_breakdown = {r["device_type"]: r["cnt"] for r in device_rows}

        country_rows = conn.execute(
            """SELECT country, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=? AND country!=''
               GROUP BY country ORDER BY cnt DESC LIMIT 20""",
            (since,),
        ).fetchall()
        country_breakdown = [{"country": r["country"], "count": r["cnt"]}
                              for r in country_rows]

        utm_rows = conn.execute(
            """SELECT utm_source, utm_medium, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=? AND utm_source!=''
               GROUP BY utm_source, utm_medium ORDER BY cnt DESC LIMIT 20""",
            (since,),
        ).fetchall()
        utm_breakdown = [
            {"utm_source": r["utm_source"], "utm_medium": r["utm_medium"], "count": r["cnt"]}
            for r in utm_rows
        ]

        return {
            "days": days,
            "total_sessions": total_sessions,
            "unique_visitors": unique_visitors,
            "page_views": page_views,
            "bounce_rate": bounce_rate,
            "avg_duration": avg_dur,
            "top_pages": top_pages_data,
            "top_referrers": top_referrers_data,
            "device_breakdown": device_breakdown,
            "country_breakdown": country_breakdown,
            "utm_breakdown": utm_breakdown,
        }
    finally:
        conn.close()


def top_pages(limit: int = 20, days: int = 30) -> List[Dict[str, Any]]:
    """Return top pages by views with engagement metrics."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT page_url,
                      COUNT(*) as views,
                      COUNT(DISTINCT session_id) as unique_sessions,
                      AVG(time_on_page_seconds) as avg_time,
                      AVG(scroll_depth_pct) as avg_scroll
               FROM traffic_pageviews WHERE created_at>=?
               GROUP BY page_url ORDER BY views DESC LIMIT ?""",
            (since, limit),
        ).fetchall()
        return [
            {
                "page_url": r["page_url"],
                "views": r["views"],
                "unique_sessions": r["unique_sessions"],
                "avg_time_on_page": round(r["avg_time"] or 0, 1),
                "avg_scroll_depth": round(r["avg_scroll"] or 0, 1),
            }
            for r in rows
        ]
    finally:
        conn.close()


def referrer_analysis(days: int = 30) -> Dict[str, Any]:
    """Break down traffic by referrer category."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT referrer, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=?
               GROUP BY referrer""",
            (since,),
        ).fetchall()

        categories: Dict[str, int] = {
            "organic": 0, "direct": 0, "social": 0,
            "email": 0, "paid": 0, "other": 0,
        }
        breakdown: List[Dict[str, Any]] = []

        social_domains = re.compile(
            r"facebook|twitter|instagram|linkedin|pinterest|tiktok|"
            r"youtube|reddit|snapchat|discord|telegram",
            re.I,
        )
        search_domains = re.compile(
            r"google|bing|yahoo|duckduckgo|baidu|yandex|ask\.com", re.I
        )
        email_signals = re.compile(r"email|mail|newsletter|substack|mailchimp|sendinblue", re.I)

        for r in rows:
            ref = r["referrer"] or ""
            cnt = r["cnt"]
            if not ref:
                cat = "direct"
            elif email_signals.search(ref):
                cat = "email"
            elif social_domains.search(ref):
                cat = "social"
            elif search_domains.search(ref):
                cat = "organic"
            else:
                cat = "other"
            categories[cat] += cnt
            breakdown.append({"referrer": ref, "category": cat, "count": cnt})

        breakdown.sort(key=lambda x: x["count"], reverse=True)
        result = dict(categories)
        result["breakdown"] = breakdown
        return result
    finally:
        conn.close()


def device_breakdown(days: int = 30) -> Dict[str, Any]:
    """Return device type counts and percentages."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT device_type, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=?
               GROUP BY device_type""",
            (since,),
        ).fetchall()
        counts = {r["device_type"]: r["cnt"] for r in rows}
        total = sum(counts.values()) or 1
        result: Dict[str, Any] = {"total": sum(counts.values())}
        for dt in DEVICE_TYPES:
            c = counts.get(dt, 0)
            result[dt] = {"count": c, "pct": round(c / total * 100, 2)}
        return result
    finally:
        conn.close()


def geo_breakdown(days: int = 30) -> List[Dict[str, Any]]:
    """Return country/region traffic counts."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT country, region, COUNT(*) as cnt
               FROM traffic_sessions WHERE created_at>=? AND country!=''
               GROUP BY country, region ORDER BY cnt DESC LIMIT 100""",
            (since,),
        ).fetchall()
        return [
            {"country": r["country"], "region": r["region"], "count": r["cnt"]}
            for r in rows
        ]
    finally:
        conn.close()


def utm_performance(days: int = 30) -> List[Dict[str, Any]]:
    """Return UTM source/medium/campaign performance metrics."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT utm_source, utm_medium, utm_campaign,
                      COUNT(*) as sessions,
                      SUM(converted) as conversions,
                      AVG(duration_seconds) as avg_duration,
                      SUM(page_views) as total_pv,
                      SUM(bounced) as bounces
               FROM traffic_sessions WHERE created_at>=? AND utm_source!=''
               GROUP BY utm_source, utm_medium, utm_campaign
               ORDER BY sessions DESC""",
            (since,),
        ).fetchall()
        result = []
        for r in rows:
            sessions = r["sessions"] or 0
            conversions = r["conversions"] or 0
            bounces = r["bounces"] or 0
            result.append({
                "utm_source": r["utm_source"],
                "utm_medium": r["utm_medium"],
                "utm_campaign": r["utm_campaign"],
                "sessions": sessions,
                "conversions": conversions,
                "conversion_rate": round(conversions / max(1, sessions), 4),
                "bounce_rate": round(bounces / max(1, sessions) * 100, 2),
                "avg_duration": round(r["avg_duration"] or 0, 1),
                "total_pageviews": r["total_pv"] or 0,
            })
        return result
    finally:
        conn.close()


def conversion_funnel(steps: List[str], days: int = 30) -> List[Dict[str, Any]]:
    """Compute drop-off at each step of a URL funnel.

    Each step is a page URL. Returns per-step visitor counts and drop-off rates.
    """
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        result = []
        prev_sessions: Optional[set] = None

        for i, step_url in enumerate(steps):
            rows = conn.execute(
                """SELECT DISTINCT session_id FROM traffic_pageviews
                   WHERE page_url=? AND created_at>=?""",
                (step_url, since),
            ).fetchall()
            step_sessions = {r["session_id"] for r in rows}

            if prev_sessions is not None:
                reached = step_sessions & prev_sessions
            else:
                reached = step_sessions

            count = len(reached)
            drop_off = 0.0
            if prev_sessions and len(prev_sessions) > 0:
                drop_off = round(
                    (1 - count / max(1, len(prev_sessions))) * 100, 2
                )

            result.append({
                "step": i + 1,
                "page_url": step_url,
                "visitors": count,
                "drop_off_pct": drop_off if i > 0 else 0.0,
            })
            prev_sessions = reached

        return result
    finally:
        conn.close()


def session_duration_distribution(days: int = 30) -> Dict[str, int]:
    """Bucket sessions by duration into time ranges."""
    _ensure()
    since = _since_iso(days)
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT duration_seconds FROM traffic_sessions
               WHERE created_at>=? AND duration_seconds>0""",
            (since,),
        ).fetchall()
        buckets: Dict[str, int] = {
            "0-30s": 0, "30-60s": 0, "1-3m": 0, "3-10m": 0, "10m+": 0
        }
        for r in rows:
            d = r["duration_seconds"]
            if d <= 30:
                buckets["0-30s"] += 1
            elif d <= 60:
                buckets["30-60s"] += 1
            elif d <= 180:
                buckets["1-3m"] += 1
            elif d <= 600:
                buckets["3-10m"] += 1
            else:
                buckets["10m+"] += 1
        return buckets
    finally:
        conn.close()


def page_flow_analysis(start_page: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Show what pages users visit after start_page."""
    _ensure()
    conn = _db()
    try:
        # Find sessions that visited start_page
        session_rows = conn.execute(
            """SELECT DISTINCT session_id FROM traffic_pageviews
               WHERE page_url=?""",
            (start_page,),
        ).fetchall()
        session_ids = tuple(r["session_id"] for r in session_rows)
        if not session_ids:
            return []

        # For each session, find pageviews after start_page
        placeholders = ",".join("?" * len(session_ids))
        next_page_rows = conn.execute(
            f"""SELECT tp2.page_url, COUNT(*) as cnt
                FROM traffic_pageviews tp1
                JOIN traffic_pageviews tp2
                  ON tp1.session_id = tp2.session_id
                  AND tp2.created_at > tp1.created_at
                WHERE tp1.page_url=?
                  AND tp1.session_id IN ({placeholders})
                  AND tp2.page_url != ?
                GROUP BY tp2.page_url ORDER BY cnt DESC LIMIT ?""",
            (start_page, *session_ids, start_page, limit),
        ).fetchall()

        total = sum(r["cnt"] for r in next_page_rows) or 1
        return [
            {
                "next_page": r["page_url"],
                "count": r["cnt"],
                "pct": round(r["cnt"] / total * 100, 2),
            }
            for r in next_page_rows
        ]
    finally:
        conn.close()


def real_time_stats() -> Dict[str, Any]:
    """Return live visitor data: sessions in last 5 minutes + recent events."""
    _ensure()
    five_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = _db()
    try:
        active_row = conn.execute(
            """SELECT COUNT(*) FROM traffic_sessions WHERE last_seen_at>=?""",
            (five_min_ago,),
        ).fetchone()

        unique_row = conn.execute(
            """SELECT COUNT(DISTINCT visitor_id) FROM traffic_sessions
               WHERE last_seen_at>=?""",
            (five_min_ago,),
        ).fetchone()

        recent_events = conn.execute(
            """SELECT event_name, event_category, created_at
               FROM traffic_events WHERE created_at>=?
               ORDER BY created_at DESC LIMIT 20""",
            (one_hour_ago,),
        ).fetchall()

        top_active = conn.execute(
            """SELECT page_url, COUNT(*) as cnt
               FROM traffic_pageviews WHERE created_at>=?
               GROUP BY page_url ORDER BY cnt DESC LIMIT 5""",
            (five_min_ago,),
        ).fetchall()

        return {
            "active_sessions": active_row[0],
            "unique_visitors_5m": unique_row[0],
            "recent_events": [
                {"event_name": r["event_name"],
                 "event_category": r["event_category"],
                 "created_at": r["created_at"]}
                for r in recent_events
            ],
            "top_active_pages": [
                {"page_url": r["page_url"], "count": r["cnt"]}
                for r in top_active
            ],
            "timestamp": _now_iso(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------

def record_click(page_url: str, element_selector: str) -> bool:
    """Increment the click count for a page element in the heatmap table."""
    _ensure()
    now = _now_iso()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO traffic_heatmap_data
               (page_url, element_selector, click_count, hover_count, created_at, updated_at)
               VALUES (?,?,1,0,?,?)
               ON CONFLICT(page_url, element_selector) DO UPDATE SET
                 click_count=click_count+1, updated_at=excluded.updated_at""",
            (page_url, element_selector, now, now),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def record_hover(page_url: str, element_selector: str) -> bool:
    """Increment the hover count for a page element in the heatmap table."""
    _ensure()
    now = _now_iso()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO traffic_heatmap_data
               (page_url, element_selector, click_count, hover_count, created_at, updated_at)
               VALUES (?,?,0,1,?,?)
               ON CONFLICT(page_url, element_selector) DO UPDATE SET
                 hover_count=hover_count+1, updated_at=excluded.updated_at""",
            (page_url, element_selector, now, now),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_heatmap_data(page_url: str) -> List[Dict[str, Any]]:
    """Return all heatmap data for a given page URL."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT element_selector, click_count, hover_count, updated_at
               FROM traffic_heatmap_data WHERE page_url=?
               ORDER BY click_count DESC""",
            (page_url,),
        ).fetchall()
        return [
            {
                "element_selector": r["element_selector"],
                "click_count": r["click_count"],
                "hover_count": r["hover_count"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("ta_start_session", "Start a new traffic session and return the session object")
def ta_start_session_tool(
    ip_hash: str,
    landing_page: str,
    user_agent: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
) -> str:
    """Start a new traffic session.

    Args:
        ip_hash: Hashed IP address for the visitor (for privacy).
        landing_page: The first page the visitor lands on.
        user_agent: Browser User-Agent string (optional).
        utm_source: UTM source parameter (optional).
        utm_medium: UTM medium parameter (optional).
        utm_campaign: UTM campaign parameter (optional).

    Returns:
        JSON with session details including session_id.
    """
    try:
        session = start_session(
            ip_hash=ip_hash,
            user_agent=user_agent,
            landing_page=landing_page,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
        )
        return json.dumps({"ok": True, "session": session.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_record_pageview", "Record a pageview for an existing traffic session")
def ta_record_pageview_tool(
    session_id: str,
    page_url: str,
    page_title: str = "",
    time_on_page: int = 0,
) -> str:
    """Record a pageview for a session.

    Args:
        session_id: The session UUID returned by ta_start_session.
        page_url: Full URL of the page visited.
        page_title: Title of the page (optional).
        time_on_page: Seconds spent on the previous page (optional).

    Returns:
        JSON with the pageview_id.
    """
    try:
        pv_id = record_pageview(
            session_id=session_id,
            page_url=page_url,
            page_title=page_title,
            time_on_page=time_on_page,
        )
        return json.dumps({"ok": True, "pageview_id": pv_id, "session_id": session_id})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_track_event", "Track a named event within a traffic session")
def ta_track_event_tool(
    session_id: str,
    event_name: str,
    event_category: str = "engagement",
    revenue: float = 0.0,
) -> str:
    """Track a named event for a session.

    Args:
        session_id: The session UUID.
        event_name: The name of the event (e.g. 'signup', 'add_to_cart').
        event_category: One of engagement, conversion, ecommerce, navigation,
                        social, media, form, error.
        revenue: Revenue attributed to this event in USD (optional).

    Returns:
        JSON with the event_id.
    """
    try:
        ev_id = track_event(
            session_id=session_id,
            event_name=event_name,
            event_category=event_category,
            revenue=revenue,
        )
        return json.dumps({"ok": True, "event_id": ev_id, "session_id": session_id})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_traffic_summary", "Get a traffic summary for a date range")
def ta_traffic_summary_tool(days: int = 30) -> str:
    """Return a high-level traffic summary.

    Args:
        days: Number of days to look back (default 30).

    Returns:
        JSON with total_sessions, unique_visitors, page_views, bounce_rate,
        avg_duration, top_pages, top_referrers, device_breakdown,
        country_breakdown, utm_breakdown.
    """
    try:
        summary = traffic_summary(days=days)
        return json.dumps({"ok": True, "summary": summary})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_top_pages", "Get top pages by traffic with engagement metrics")
def ta_top_pages_tool(limit: int = 20, days: int = 30) -> str:
    """Return the most visited pages with engagement data.

    Args:
        limit: Maximum number of pages to return (default 20).
        days: Number of days to look back (default 30).

    Returns:
        JSON list of pages with views, unique_sessions, avg_time_on_page,
        avg_scroll_depth.
    """
    try:
        pages = top_pages(limit=limit, days=days)
        return json.dumps({"ok": True, "pages": pages, "count": len(pages)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_referrer_analysis", "Analyze traffic referrer sources and categories")
def ta_referrer_analysis_tool(days: int = 30) -> str:
    """Break down traffic by referrer category (organic, social, direct, etc.).

    Args:
        days: Number of days to look back (default 30).

    Returns:
        JSON with organic, direct, social, email, paid, other counts
        plus a full breakdown list.
    """
    try:
        analysis = referrer_analysis(days=days)
        return json.dumps({"ok": True, "referrers": analysis})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_conversion_funnel", "Analyze conversion drop-off through a URL funnel")
def ta_conversion_funnel_tool(steps_csv: str, days: int = 30) -> str:
    """Compute drop-off at each step of a conversion funnel.

    Args:
        steps_csv: Comma-separated list of page URLs forming the funnel steps.
                   Example: '/landing,/pricing,/checkout,/thank-you'
        days: Number of days to look back (default 30).

    Returns:
        JSON list with per-step visitor count and drop-off percentage.
    """
    try:
        steps = [s.strip() for s in steps_csv.split(",") if s.strip()]
        if not steps:
            return json.dumps({"ok": False, "error": "steps_csv must not be empty"})
        funnel = conversion_funnel(steps=steps, days=days)
        return json.dumps({"ok": True, "funnel": funnel})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_utm_performance", "Get UTM campaign performance metrics")
def ta_utm_performance_tool(days: int = 30) -> str:
    """Return performance data grouped by UTM source, medium, and campaign.

    Args:
        days: Number of days to look back (default 30).

    Returns:
        JSON list with sessions, conversions, conversion_rate, bounce_rate,
        avg_duration per UTM combination.
    """
    try:
        perf = utm_performance(days=days)
        return json.dumps({"ok": True, "utm_performance": perf, "count": len(perf)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("ta_create_goal", "Create a conversion goal for tracking")
def ta_create_goal_tool(
    name: str,
    goal_type: str,
    target_url: str = "",
    target_event: str = "",
) -> str:
    """Create a new conversion goal.

    Args:
        name: Unique goal name (e.g. 'newsletter_signup').
        goal_type: One of pageview, event, duration, scroll_depth, custom.
        target_url: URL to match for pageview goals (optional).
        target_event: Event name to match for event goals (optional).

    Returns:
        JSON with the created goal details.
    """
    try:
        goal = create_goal(
            name=name,
            goal_type=goal_type,
            target_url=target_url,
            target_event=target_event,
        )
        return json.dumps({"ok": True, "goal": goal.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
