"""
Analytics tracker — unified revenue, traffic, and conversion tracking.

Stores all metrics in SQLite. Agents use this to measure performance,
compute ROI per channel, and surface the best-performing content/campaigns.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import cfg

# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

_DB_PATH: str | None = None
_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn, _DB_PATH
    if _conn is not None:
        return _conn
    db_path = cfg("analytics.db_path", fallback="autoearn.db")
    _DB_PATH = db_path
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _ensure_schema(_conn)
    return _conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          REAL    NOT NULL,
            event_type  TEXT    NOT NULL,
            source      TEXT    NOT NULL DEFAULT '',
            channel     TEXT    NOT NULL DEFAULT '',
            campaign    TEXT    NOT NULL DEFAULT '',
            content_id  TEXT    NOT NULL DEFAULT '',
            url         TEXT    NOT NULL DEFAULT '',
            value       REAL    NOT NULL DEFAULT 0.0,
            meta        TEXT    NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS analytics_sessions (
            session_id  TEXT    PRIMARY KEY,
            started_at  REAL    NOT NULL,
            ended_at    REAL,
            source      TEXT    NOT NULL DEFAULT '',
            channel     TEXT    NOT NULL DEFAULT '',
            campaign    TEXT    NOT NULL DEFAULT '',
            landing_url TEXT    NOT NULL DEFAULT '',
            pages_seen  INTEGER NOT NULL DEFAULT 1,
            converted   INTEGER NOT NULL DEFAULT 0,
            revenue     REAL    NOT NULL DEFAULT 0.0,
            meta        TEXT    NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS analytics_goals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT    NOT NULL DEFAULT '',
            target      REAL    NOT NULL DEFAULT 0.0,
            current     REAL    NOT NULL DEFAULT 0.0,
            unit        TEXT    NOT NULL DEFAULT 'count',
            created_at  REAL    NOT NULL,
            deadline    REAL
        );

        CREATE TABLE IF NOT EXISTS analytics_funnels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            funnel_name TEXT    NOT NULL,
            step_name   TEXT    NOT NULL,
            step_order  INTEGER NOT NULL DEFAULT 0,
            ts          REAL    NOT NULL,
            session_id  TEXT    NOT NULL DEFAULT '',
            dropped     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS analytics_cohorts (
            cohort_date TEXT    NOT NULL,
            cohort_size INTEGER NOT NULL DEFAULT 0,
            day_n       INTEGER NOT NULL DEFAULT 0,
            retained    INTEGER NOT NULL DEFAULT 0,
            revenue     REAL    NOT NULL DEFAULT 0.0,
            PRIMARY KEY (cohort_date, day_n)
        );

        CREATE INDEX IF NOT EXISTS idx_ae_ts    ON analytics_events(ts);
        CREATE INDEX IF NOT EXISTS idx_ae_type  ON analytics_events(event_type);
        CREATE INDEX IF NOT EXISTS idx_ae_src   ON analytics_events(source);
        CREATE INDEX IF NOT EXISTS idx_ae_ch    ON analytics_events(channel);
        CREATE INDEX IF NOT EXISTS idx_af_funnel ON analytics_funnels(funnel_name);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AnalyticsEvent:
    event_type: str
    source: str = ""
    channel: str = ""
    campaign: str = ""
    content_id: str = ""
    url: str = ""
    value: float = 0.0
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    id: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "source": self.source,
            "channel": self.channel,
            "campaign": self.campaign,
            "content_id": self.content_id,
            "url": self.url,
            "value": self.value,
            "meta": self.meta,
            "ts": self.ts,
            "datetime": datetime.fromtimestamp(self.ts).isoformat(),
        }


@dataclass
class ChannelMetrics:
    channel: str
    sessions: int = 0
    conversions: int = 0
    revenue: float = 0.0
    events: int = 0

    @property
    def conversion_rate(self) -> float:
        if self.sessions == 0:
            return 0.0
        return round(self.conversions / self.sessions * 100, 2)

    @property
    def revenue_per_session(self) -> float:
        if self.sessions == 0:
            return 0.0
        return round(self.revenue / self.sessions, 4)

    @property
    def average_order_value(self) -> float:
        if self.conversions == 0:
            return 0.0
        return round(self.revenue / self.conversions, 4)

    def to_dict(self) -> dict:
        return {
            "channel": self.channel,
            "sessions": self.sessions,
            "conversions": self.conversions,
            "revenue": round(self.revenue, 4),
            "events": self.events,
            "conversion_rate_pct": self.conversion_rate,
            "revenue_per_session": self.revenue_per_session,
            "average_order_value": self.average_order_value,
        }


@dataclass
class FunnelStep:
    step_name: str
    step_order: int
    entered: int = 0
    dropped: int = 0

    @property
    def completed(self) -> int:
        return self.entered - self.dropped

    @property
    def drop_rate(self) -> float:
        if self.entered == 0:
            return 0.0
        return round(self.dropped / self.entered * 100, 2)

    @property
    def completion_rate(self) -> float:
        return round(100.0 - self.drop_rate, 2)

    def to_dict(self) -> dict:
        return {
            "step_name": self.step_name,
            "step_order": self.step_order,
            "entered": self.entered,
            "completed": self.completed,
            "dropped": self.dropped,
            "completion_rate_pct": self.completion_rate,
            "drop_rate_pct": self.drop_rate,
        }


# ---------------------------------------------------------------------------
# Core tracking functions
# ---------------------------------------------------------------------------

def track_event(
    event_type: str,
    source: str = "",
    channel: str = "",
    campaign: str = "",
    content_id: str = "",
    url: str = "",
    value: float = 0.0,
    meta: dict | None = None,
    ts: float | None = None,
) -> int:
    """Record a single analytics event. Returns the new event ID."""
    now = ts or time.time()
    meta_json = json.dumps(meta or {})
    db = _db()
    cur = db.execute(
        """INSERT INTO analytics_events
           (ts, event_type, source, channel, campaign, content_id, url, value, meta)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (now, event_type, source, channel, campaign, content_id, url, value, meta_json),
    )
    db.commit()
    return cur.lastrowid or 0


def track_pageview(url: str, source: str = "", channel: str = "", campaign: str = "") -> int:
    """Convenience: track a page view."""
    return track_event("pageview", source=source, channel=channel, campaign=campaign, url=url)


def track_conversion(
    source: str = "",
    channel: str = "",
    campaign: str = "",
    content_id: str = "",
    revenue: float = 0.0,
    meta: dict | None = None,
) -> int:
    """Convenience: track a conversion/sale."""
    return track_event(
        "conversion",
        source=source,
        channel=channel,
        campaign=campaign,
        content_id=content_id,
        value=revenue,
        meta=meta,
    )


def track_click(
    url: str,
    source: str = "",
    channel: str = "",
    campaign: str = "",
    content_id: str = "",
) -> int:
    """Convenience: track a click/CTA event."""
    return track_event(
        "click", source=source, channel=channel, campaign=campaign,
        content_id=content_id, url=url,
    )


def track_signup(source: str = "", channel: str = "", campaign: str = "") -> int:
    """Convenience: track an email signup / lead capture."""
    return track_event("signup", source=source, channel=channel, campaign=campaign)


# ---------------------------------------------------------------------------
# Session tracking
# ---------------------------------------------------------------------------

def start_session(
    session_id: str,
    source: str = "",
    channel: str = "",
    campaign: str = "",
    landing_url: str = "",
    meta: dict | None = None,
) -> None:
    """Begin tracking a user session."""
    db = _db()
    db.execute(
        """INSERT OR REPLACE INTO analytics_sessions
           (session_id, started_at, source, channel, campaign, landing_url, meta)
           VALUES (?,?,?,?,?,?,?)""",
        (session_id, time.time(), source, channel, campaign, landing_url,
         json.dumps(meta or {})),
    )
    db.commit()


def end_session(session_id: str, pages_seen: int = 1) -> None:
    """Close a session, recording pages visited."""
    db = _db()
    db.execute(
        "UPDATE analytics_sessions SET ended_at=?, pages_seen=? WHERE session_id=?",
        (time.time(), pages_seen, session_id),
    )
    db.commit()


def record_session_conversion(session_id: str, revenue: float = 0.0) -> None:
    """Mark a session as converted."""
    db = _db()
    db.execute(
        "UPDATE analytics_sessions SET converted=1, revenue=? WHERE session_id=?",
        (revenue, session_id),
    )
    db.commit()


def get_session(session_id: str) -> dict | None:
    """Retrieve session data."""
    row = _db().execute(
        "SELECT * FROM analytics_sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["meta"] = json.loads(d.get("meta", "{}"))
    return d


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------

def create_goal(
    name: str,
    description: str = "",
    target: float = 0.0,
    unit: str = "count",
    deadline: float | None = None,
) -> int:
    """Create a trackable goal. Returns goal ID."""
    db = _db()
    cur = db.execute(
        """INSERT INTO analytics_goals
           (name, description, target, unit, created_at, deadline)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
             description=excluded.description,
             target=excluded.target,
             unit=excluded.unit,
             deadline=excluded.deadline""",
        (name, description, target, unit, time.time(), deadline),
    )
    db.commit()
    return cur.lastrowid or 0


def update_goal_progress(name: str, current: float) -> bool:
    """Set current progress on a goal. Returns True if goal is now met."""
    db = _db()
    db.execute(
        "UPDATE analytics_goals SET current=? WHERE name=?",
        (current, name),
    )
    db.commit()
    row = db.execute(
        "SELECT target, current FROM analytics_goals WHERE name=?", (name,)
    ).fetchone()
    if row is None:
        return False
    return row["current"] >= row["target"]


def increment_goal(name: str, amount: float = 1.0) -> float:
    """Increment goal progress by amount. Returns new current value."""
    db = _db()
    db.execute(
        "UPDATE analytics_goals SET current=current+? WHERE name=?",
        (amount, name),
    )
    db.commit()
    row = db.execute(
        "SELECT current FROM analytics_goals WHERE name=?", (name,)
    ).fetchone()
    return row["current"] if row else 0.0


def get_goal(name: str) -> dict | None:
    """Get goal details including progress percentage."""
    row = _db().execute(
        "SELECT * FROM analytics_goals WHERE name=?", (name,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["progress_pct"] = round(d["current"] / d["target"] * 100, 1) if d["target"] > 0 else 0.0
    d["met"] = d["current"] >= d["target"]
    return d


def list_goals() -> list[dict]:
    """List all goals with progress."""
    rows = _db().execute(
        "SELECT * FROM analytics_goals ORDER BY created_at DESC"
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["progress_pct"] = round(d["current"] / d["target"] * 100, 1) if d["target"] > 0 else 0.0
        d["met"] = d["current"] >= d["target"]
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Funnel tracking
# ---------------------------------------------------------------------------

def track_funnel_step(
    funnel_name: str,
    step_name: str,
    step_order: int,
    session_id: str = "",
    dropped: bool = False,
) -> None:
    """Record a user reaching (or dropping at) a funnel step."""
    db = _db()
    db.execute(
        """INSERT INTO analytics_funnels
           (funnel_name, step_name, step_order, ts, session_id, dropped)
           VALUES (?,?,?,?,?,?)""",
        (funnel_name, step_name, step_order, time.time(), session_id, int(dropped)),
    )
    db.commit()


def get_funnel_report(funnel_name: str) -> list[dict]:
    """Get step-by-step funnel analysis."""
    rows = _db().execute(
        """SELECT step_name, step_order,
                  COUNT(*) as entered,
                  SUM(dropped) as dropped
           FROM analytics_funnels
           WHERE funnel_name=?
           GROUP BY step_name, step_order
           ORDER BY step_order""",
        (funnel_name,),
    ).fetchall()
    steps = []
    for row in rows:
        s = FunnelStep(
            step_name=row["step_name"],
            step_order=row["step_order"],
            entered=row["entered"],
            dropped=row["dropped"] or 0,
        )
        steps.append(s.to_dict())
    if steps:
        # Add overall conversion rate (first step to last step)
        first = steps[0]["entered"]
        last = steps[-1]["completed"] if steps else 0
        for s in steps:
            s["overall_conv_pct"] = round(last / first * 100, 2) if first > 0 else 0.0
    return steps


# ---------------------------------------------------------------------------
# Channel and campaign analysis
# ---------------------------------------------------------------------------

def channel_breakdown(days: int = 30) -> list[dict]:
    """Revenue and conversions broken down by channel for the last N days."""
    since = time.time() - days * 86400
    db = _db()

    # Sessions per channel
    sessions_rows = db.execute(
        """SELECT channel,
                  COUNT(*) as sessions,
                  SUM(converted) as conversions,
                  SUM(revenue) as revenue
           FROM analytics_sessions
           WHERE started_at >= ?
           GROUP BY channel""",
        (since,),
    ).fetchall()

    # Events per channel
    event_rows = db.execute(
        """SELECT channel, COUNT(*) as cnt
           FROM analytics_events
           WHERE ts >= ?
           GROUP BY channel""",
        (since,),
    ).fetchall()
    event_map = {r["channel"]: r["cnt"] for r in event_rows}

    metrics = {}
    for row in sessions_rows:
        ch = row["channel"] or "direct"
        m = ChannelMetrics(
            channel=ch,
            sessions=row["sessions"],
            conversions=row["conversions"] or 0,
            revenue=row["revenue"] or 0.0,
            events=event_map.get(ch, 0),
        )
        metrics[ch] = m

    return sorted([m.to_dict() for m in metrics.values()], key=lambda x: -x["revenue"])


def campaign_breakdown(days: int = 30) -> list[dict]:
    """Revenue and conversions broken down by campaign."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT campaign,
                  COUNT(*) as events,
                  SUM(CASE WHEN event_type='conversion' THEN 1 ELSE 0 END) as conversions,
                  SUM(CASE WHEN event_type='conversion' THEN value ELSE 0 END) as revenue
           FROM analytics_events
           WHERE ts >= ?
           GROUP BY campaign
           ORDER BY revenue DESC""",
        (since,),
    ).fetchall()
    return [dict(row) for row in rows]


def top_content(days: int = 30, limit: int = 20) -> list[dict]:
    """Top-performing content pieces by revenue generated."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT content_id,
                  COUNT(*) as events,
                  SUM(CASE WHEN event_type='conversion' THEN 1 ELSE 0 END) as conversions,
                  SUM(CASE WHEN event_type='conversion' THEN value ELSE 0 END) as revenue,
                  SUM(CASE WHEN event_type='click' THEN 1 ELSE 0 END) as clicks,
                  SUM(CASE WHEN event_type='pageview' THEN 1 ELSE 0 END) as pageviews
           FROM analytics_events
           WHERE ts >= ? AND content_id != ''
           GROUP BY content_id
           ORDER BY revenue DESC
           LIMIT ?""",
        (since, limit),
    ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        clicks = d["clicks"] or 0
        d["click_to_conversion_pct"] = round(d["conversions"] / clicks * 100, 2) if clicks > 0 else 0.0
        result.append(d)
    return result


def top_sources(days: int = 30, limit: int = 20) -> list[dict]:
    """Top traffic sources by conversion count."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT source,
                  COUNT(*) as events,
                  SUM(CASE WHEN event_type='conversion' THEN 1 ELSE 0 END) as conversions,
                  SUM(CASE WHEN event_type='conversion' THEN value ELSE 0 END) as revenue
           FROM analytics_events
           WHERE ts >= ? AND source != ''
           GROUP BY source
           ORDER BY revenue DESC
           LIMIT ?""",
        (since, limit),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Cohort analysis
# ---------------------------------------------------------------------------

def record_cohort_retention(
    cohort_date: str,
    cohort_size: int,
    day_n: int,
    retained: int,
    revenue: float = 0.0,
) -> None:
    """Record cohort retention data (cohort_date=YYYY-MM-DD, day_n=0,1,7,30,...)."""
    db = _db()
    db.execute(
        """INSERT INTO analytics_cohorts (cohort_date, cohort_size, day_n, retained, revenue)
           VALUES (?,?,?,?,?)
           ON CONFLICT(cohort_date, day_n) DO UPDATE SET
             cohort_size=excluded.cohort_size,
             retained=excluded.retained,
             revenue=excluded.revenue""",
        (cohort_date, cohort_size, day_n, retained, revenue),
    )
    db.commit()


def get_cohort_table(cohorts: int = 8) -> list[dict]:
    """Get retention table for the last N cohorts."""
    rows = _db().execute(
        """SELECT * FROM analytics_cohorts
           ORDER BY cohort_date DESC, day_n ASC
           LIMIT ?""",
        (cohorts * 10,),
    ).fetchall()
    table: dict[str, dict] = {}
    for row in rows:
        cd = row["cohort_date"]
        if cd not in table:
            table[cd] = {"cohort_date": cd, "cohort_size": row["cohort_size"], "days": {}}
        dn = row["day_n"]
        size = row["cohort_size"] or 1
        table[cd]["days"][f"day_{dn}"] = {
            "retained": row["retained"],
            "retention_pct": round(row["retained"] / size * 100, 1),
            "revenue": round(row["revenue"], 4),
        }
    return list(table.values())


# ---------------------------------------------------------------------------
# Summary and KPIs
# ---------------------------------------------------------------------------

def kpi_summary(days: int = 30) -> dict:
    """High-level KPI dashboard for the last N days."""
    since = time.time() - days * 86400
    db = _db()

    total_revenue = db.execute(
        "SELECT COALESCE(SUM(value),0) FROM analytics_events WHERE event_type='conversion' AND ts>=?",
        (since,),
    ).fetchone()[0]

    total_conversions = db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='conversion' AND ts>=?",
        (since,),
    ).fetchone()[0]

    total_clicks = db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='click' AND ts>=?",
        (since,),
    ).fetchone()[0]

    total_pageviews = db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='pageview' AND ts>=?",
        (since,),
    ).fetchone()[0]

    total_signups = db.execute(
        "SELECT COUNT(*) FROM analytics_events WHERE event_type='signup' AND ts>=?",
        (since,),
    ).fetchone()[0]

    total_sessions = db.execute(
        "SELECT COUNT(*) FROM analytics_sessions WHERE started_at>=?",
        (since,),
    ).fetchone()[0]

    avg_order_value = round(total_revenue / total_conversions, 4) if total_conversions > 0 else 0.0
    session_conv_rate = round(total_conversions / total_sessions * 100, 2) if total_sessions > 0 else 0.0
    click_conv_rate = round(total_conversions / total_clicks * 100, 2) if total_clicks > 0 else 0.0

    # Revenue trend: compare this period to previous period
    prev_since = since - days * 86400
    prev_revenue = db.execute(
        "SELECT COALESCE(SUM(value),0) FROM analytics_events WHERE event_type='conversion' AND ts>=? AND ts<?",
        (prev_since, since),
    ).fetchone()[0]

    revenue_change_pct = 0.0
    if prev_revenue > 0:
        revenue_change_pct = round((total_revenue - prev_revenue) / prev_revenue * 100, 2)

    return {
        "period_days": days,
        "total_revenue": round(total_revenue, 4),
        "total_conversions": total_conversions,
        "total_clicks": total_clicks,
        "total_pageviews": total_pageviews,
        "total_signups": total_signups,
        "total_sessions": total_sessions,
        "avg_order_value": avg_order_value,
        "session_conversion_rate_pct": session_conv_rate,
        "click_to_conversion_rate_pct": click_conv_rate,
        "revenue_vs_prev_period_pct": revenue_change_pct,
        "previous_period_revenue": round(prev_revenue, 4),
    }


def daily_revenue_series(days: int = 30) -> list[dict]:
    """Revenue per day for the last N days (for charting)."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT date(ts, 'unixepoch') as day,
                  COUNT(*) as conversions,
                  SUM(value) as revenue
           FROM analytics_events
           WHERE event_type='conversion' AND ts>=?
           GROUP BY day
           ORDER BY day""",
        (since,),
    ).fetchall()
    return [{"day": row["day"], "conversions": row["conversions"],
             "revenue": round(row["revenue"] or 0.0, 4)} for row in rows]


def event_count_series(event_type: str, days: int = 30) -> list[dict]:
    """Count of a specific event type per day."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT date(ts, 'unixepoch') as day, COUNT(*) as cnt
           FROM analytics_events
           WHERE event_type=? AND ts>=?
           GROUP BY day
           ORDER BY day""",
        (event_type, since),
    ).fetchall()
    return [{"day": row["day"], "count": row["cnt"]} for row in rows]


# ---------------------------------------------------------------------------
# Anomaly detection (simple z-score on daily revenue)
# ---------------------------------------------------------------------------

def detect_revenue_anomalies(days: int = 30, z_threshold: float = 2.0) -> list[dict]:
    """Flag days where revenue deviated significantly from the mean."""
    series = daily_revenue_series(days)
    if len(series) < 3:
        return []
    values = [d["revenue"] for d in series]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return []
    anomalies = []
    for d in series:
        z = (d["revenue"] - mean) / std
        if abs(z) >= z_threshold:
            anomalies.append({
                "day": d["day"],
                "revenue": d["revenue"],
                "z_score": round(z, 3),
                "direction": "spike" if z > 0 else "drop",
                "deviation_pct": round((d["revenue"] - mean) / mean * 100, 1) if mean > 0 else 0.0,
            })
    return anomalies


# ---------------------------------------------------------------------------
# Tool wrappers (for agent use)
# ---------------------------------------------------------------------------

def track_event_tool(
    event_type: str = "",
    source: str = "",
    channel: str = "",
    campaign: str = "",
    content_id: str = "",
    value: float = 0.0,
) -> str:
    """Agent-callable: track a named event."""
    if not event_type:
        return "error: event_type required"
    eid = track_event(event_type, source=source, channel=channel,
                      campaign=campaign, content_id=content_id, value=value)
    return f"tracked event '{event_type}' (id={eid})"


def kpi_summary_tool(days: int = 30) -> str:
    """Agent-callable: get KPI dashboard as JSON string."""
    return json.dumps(kpi_summary(days), indent=2)


def channel_breakdown_tool(days: int = 30) -> str:
    """Agent-callable: get channel breakdown as JSON."""
    return json.dumps(channel_breakdown(days), indent=2)


def top_content_tool(days: int = 30, limit: int = 20) -> str:
    """Agent-callable: get top content by revenue."""
    return json.dumps(top_content(days, limit), indent=2)


def daily_series_tool(days: int = 30) -> str:
    """Agent-callable: get daily revenue series as JSON."""
    return json.dumps(daily_revenue_series(days), indent=2)


def anomaly_tool(days: int = 30) -> str:
    """Agent-callable: detect revenue anomalies."""
    anomalies = detect_revenue_anomalies(days)
    if not anomalies:
        return "no significant revenue anomalies detected"
    return json.dumps(anomalies, indent=2)


def create_goal_tool(
    name: str = "",
    description: str = "",
    target: float = 0.0,
    unit: str = "count",
) -> str:
    """Agent-callable: create a revenue/traffic goal."""
    if not name:
        return "error: name required"
    gid = create_goal(name, description, target, unit)
    return f"goal '{name}' created (id={gid}, target={target} {unit})"


def list_goals_tool() -> str:
    """Agent-callable: list all goals with progress."""
    goals = list_goals()
    if not goals:
        return "no goals defined"
    return json.dumps(goals, indent=2)


def funnel_report_tool(funnel_name: str = "") -> str:
    """Agent-callable: get funnel step analysis."""
    if not funnel_name:
        return "error: funnel_name required"
    steps = get_funnel_report(funnel_name)
    if not steps:
        return f"no data for funnel '{funnel_name}'"
    return json.dumps(steps, indent=2)
