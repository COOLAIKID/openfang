"""
core/reporting.py — Automated report generation for AutoEarn.

Produces daily and weekly org-performance reports in both dict and
Markdown formats, with charting data, agent scorecards, heatmaps,
and APScheduler integration.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    with _lock:
        if _db_conn is not None:
            return _db_conn
        try:
            from core.database import DB_PATH
        except ImportError:
            DB_PATH = "/tmp/autoearn.db"
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _db_conn = conn
    return _db_conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _date_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Data fetch helpers
# ---------------------------------------------------------------------------


def _fetch_revenue(since: str, until: Optional[str] = None) -> float:
    """Sum revenue_usd metric entries in the given time window."""
    db = _get_db()
    try:
        if until:
            row = db.execute(
                "SELECT COALESCE(SUM(value),0) AS t FROM metrics "
                "WHERE metric='revenue_usd' AND recorded_at>=? AND recorded_at<?",
                (since, until),
            ).fetchone()
        else:
            row = db.execute(
                "SELECT COALESCE(SUM(value),0) AS t FROM metrics "
                "WHERE metric='revenue_usd' AND recorded_at>=?",
                (since,),
            ).fetchone()
        return float(row["t"]) if row else 0.0
    except sqlite3.OperationalError:
        return 0.0


def _fetch_agent_statuses() -> list[sqlite3.Row]:
    db = _get_db()
    try:
        return db.execute(
            "SELECT agent_name, total_runs, total_errors, last_run_at, last_result, status "
            "FROM agent_status ORDER BY agent_name"
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _fetch_message_count(since: str) -> int:
    db = _get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM messages WHERE created_at>=?", (since,)
        ).fetchone()
        return int(row["c"]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _fetch_plan_count(since: str) -> int:
    db = _get_db()
    try:
        row = db.execute(
            "SELECT COUNT(*) AS c FROM plans WHERE created_at>=?", (since,)
        ).fetchone()
        return int(row["c"]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _fetch_workflow_runs(since: str, limit: int = 5) -> list[dict[str, Any]]:
    db = _get_db()
    try:
        rows = db.execute(
            """
            SELECT workflow, COUNT(*) AS cnt, status
            FROM workflow_runs
            WHERE started_at >= ?
            GROUP BY workflow
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
        return [{"workflow": r["workflow"], "count": r["cnt"]} for r in rows]
    except sqlite3.OperationalError:
        return []


def _fetch_top_earners(since: str, limit: int = 5) -> list[dict[str, Any]]:
    db = _get_db()
    try:
        rows = db.execute(
            """
            SELECT agent, COALESCE(SUM(value),0) AS total
            FROM metrics
            WHERE metric='revenue_usd' AND recorded_at>=?
            GROUP BY agent
            ORDER BY total DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
        return [{"agent": r["agent"], "revenue": round(float(r["total"]), 2)} for r in rows]
    except sqlite3.OperationalError:
        return []


def _fetch_errors_today(since: str) -> int:
    db = _get_db()
    try:
        row = db.execute(
            "SELECT COALESCE(SUM(total_errors),0) AS e FROM agent_status", ()
        ).fetchone()
        return int(row["e"]) if row else 0
    except sqlite3.OperationalError:
        return 0


def _fetch_agent_messages(agent: str, since: str) -> list[sqlite3.Row]:
    db = _get_db()
    try:
        return db.execute(
            "SELECT created_at FROM messages WHERE (to_agent=? OR from_agent=?) AND created_at>=?",
            (agent, agent, since),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _fetch_skill_usage(since: str, limit: int = 10) -> list[dict[str, Any]]:
    db = _get_db()
    try:
        rows = db.execute(
            """
            SELECT skill_name, COUNT(*) AS cnt
            FROM skill_executions
            WHERE executed_at >= ?
            GROUP BY skill_name
            ORDER BY cnt DESC
            LIMIT ?
            """,
            (since, limit),
        ).fetchall()
        return [{"skill": r["skill_name"], "count": r["cnt"]} for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Core report generators
# ---------------------------------------------------------------------------


def daily_report() -> dict[str, Any]:
    """
    Generate a daily performance report dict.

    Returns
    -------
    Dict with: date, revenue_today, revenue_7d, top_earners, active_agents,
    errors, plans_executed, messages_sent, top_workflows.
    """
    today_start = _days_ago(0)  # ~now — use midnight
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_iso = midnight.isoformat()
    week_ago = _days_ago(7)

    revenue_today = _fetch_revenue(today_iso)
    revenue_7d = _fetch_revenue(week_ago)

    agent_rows = _fetch_agent_statuses()
    active_agents = [
        r["agent_name"] for r in agent_rows
        if r["status"] in ("active", "healthy", "idle")
    ]

    return {
        "date": _date_str(now),
        "generated_at": _now_iso(),
        "revenue_today": round(revenue_today, 2),
        "revenue_7d": round(revenue_7d, 2),
        "top_earners": _fetch_top_earners(week_ago, limit=5),
        "active_agents": active_agents,
        "active_agent_count": len(agent_rows),
        "errors": _fetch_errors_today(today_iso),
        "plans_executed": _fetch_plan_count(today_iso),
        "messages_sent": _fetch_message_count(today_iso),
        "top_workflows": _fetch_workflow_runs(week_ago, limit=5),
    }


def weekly_report() -> dict[str, Any]:
    """
    Generate an extended weekly performance report.

    Includes everything in daily_report plus: trends, top skills,
    agent rankings, and P&L summary.
    """
    now = datetime.now(timezone.utc)
    week_ago = _days_ago(7)
    two_weeks_ago = _days_ago(14)

    rev_this_week = _fetch_revenue(week_ago)
    rev_last_week = _fetch_revenue(two_weeks_ago, until=week_ago)
    revenue_trend = round(
        ((rev_this_week - rev_last_week) / rev_last_week * 100) if rev_last_week > 0
        else (100.0 if rev_this_week > 0 else 0.0),
        1,
    )

    agent_rows = _fetch_agent_statuses()
    agent_rankings = []
    for row in agent_rows:
        runs = row["total_runs"] or 0
        errors = row["total_errors"] or 0
        error_rate = (errors / runs) if runs > 0 else 0.0
        score = max(0, 100 - int(error_rate * 100) - (10 if runs < 5 else 0))
        agent_rankings.append({
            "agent": row["agent_name"],
            "runs": runs,
            "errors": errors,
            "error_rate": round(error_rate, 3),
            "score": score,
        })
    agent_rankings.sort(key=lambda x: x["score"], reverse=True)

    try:
        from core.treasury import roi_summary, financial_report  # type: ignore
        roi = roi_summary(days=7)
        pnl_text = financial_report(days=7)
    except Exception:
        roi = []
        pnl_text = "Treasury data unavailable."

    return {
        "week_ending": _date_str(now),
        "generated_at": _now_iso(),
        "revenue_this_week": round(rev_this_week, 2),
        "revenue_last_week": round(rev_last_week, 2),
        "revenue_trend_pct": revenue_trend,
        "top_earners": _fetch_top_earners(week_ago, limit=10),
        "active_agent_count": len(agent_rows),
        "agent_rankings": agent_rankings,
        "errors_total": sum(r["total_errors"] or 0 for r in agent_rows),
        "plans_executed": _fetch_plan_count(week_ago),
        "messages_sent": _fetch_message_count(week_ago),
        "top_workflows": _fetch_workflow_runs(week_ago, limit=10),
        "top_skills": _fetch_skill_usage(week_ago, limit=10),
        "roi_by_agent": roi,
        "pnl_summary": pnl_text[:1000],
    }


def agent_scorecard(name: str) -> dict[str, Any]:
    """
    Generate a per-agent performance scorecard.

    Parameters
    ----------
    name: Agent name.

    Returns
    -------
    Dict with runs, errors, error_rate, p95_latency, revenue,
    memory_count, recent_activity, status.
    """
    db = _get_db()
    week_ago = _days_ago(7)

    # Base stats
    row = None
    try:
        row = db.execute(
            "SELECT total_runs, total_errors, last_run_at, status FROM agent_status "
            "WHERE agent_name=?",
            (name,),
        ).fetchone()
    except sqlite3.OperationalError:
        pass

    runs = row["total_runs"] if row else 0
    errors = row["total_errors"] if row else 0
    error_rate = (errors / runs) if runs > 0 else 0.0
    status = row["status"] if row else "unknown"
    last_run = row["last_run_at"] if row else None

    # Revenue
    revenue = _fetch_revenue(week_ago)
    try:
        rev_row = db.execute(
            "SELECT COALESCE(SUM(value),0) AS t FROM metrics "
            "WHERE agent=? AND metric='revenue_usd' AND recorded_at>=?",
            (name, week_ago),
        ).fetchone()
        revenue = float(rev_row["t"]) if rev_row else 0.0
    except sqlite3.OperationalError:
        revenue = 0.0

    # Memory count
    mem_count = 0
    try:
        mr = db.execute(
            "SELECT COUNT(*) AS c FROM memories WHERE agent=?", (name,)
        ).fetchone()
        mem_count = int(mr["c"]) if mr else 0
    except sqlite3.OperationalError:
        pass

    # P95 latency
    p95 = None
    try:
        from core.monitoring import p95_latency  # type: ignore
        p95 = p95_latency(name)
    except Exception:
        pass

    # Recent activity (last 5 messages)
    msg_rows = _fetch_agent_messages(name, week_ago)
    recent_activity = len(msg_rows)

    return {
        "agent": name,
        "status": status,
        "runs_7d": runs,
        "errors_7d": errors,
        "error_rate": round(error_rate, 4),
        "revenue_7d": round(revenue, 2),
        "p95_latency_ms": p95,
        "memory_count": mem_count,
        "messages_7d": recent_activity,
        "last_run_at": last_run,
        "generated_at": _now_iso(),
    }


def revenue_chart_data(days: int = 30) -> list[dict[str, Any]]:
    """
    Return daily revenue data for charting.

    Parameters
    ----------
    days: Number of days to include.

    Returns
    -------
    List of {'date': 'YYYY-MM-DD', 'amount': float} dicts, oldest first.
    """
    db = _get_db()
    results: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for i in range(days - 1, -1, -1):
        day_start = (now - timedelta(days=i)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        amount = 0.0
        try:
            row = db.execute(
                "SELECT COALESCE(SUM(value),0) AS t FROM metrics "
                "WHERE metric='revenue_usd' AND recorded_at>=? AND recorded_at<?",
                (day_start.isoformat(), day_end.isoformat()),
            ).fetchone()
            amount = float(row["t"]) if row else 0.0
        except sqlite3.OperationalError:
            amount = 0.0
        results.append({"date": _date_str(day_start), "amount": round(amount, 2)})

    return results


def activity_heatmap(days: int = 7) -> dict[str, dict[str, int]]:
    """
    Build a {agent: {hour_str: count}} heatmap for the last *days* days.

    Hour strings are '00'..'23'.

    Parameters
    ----------
    days: Number of days to include.

    Returns
    -------
    Nested dict: agent → hour (as zero-padded string) → message count.
    """
    db = _get_db()
    since = _days_ago(days)
    heatmap: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    try:
        rows = db.execute(
            "SELECT from_agent AS agent, created_at FROM messages WHERE created_at >= ?",
            (since,),
        ).fetchall()
        for row in rows:
            agent = row["agent"] or "unknown"
            ts_str = row["created_at"] or ""
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                hour = f"{ts.hour:02d}"
                heatmap[agent][hour] += 1
            except Exception:
                pass
    except sqlite3.OperationalError:
        pass

    # Convert defaultdicts to plain dicts
    return {agent: dict(hours) for agent, hours in heatmap.items()}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_daily_report(report_dict: dict[str, Any]) -> str:
    """
    Pretty-print a daily_report() dict as a Markdown string.

    Parameters
    ----------
    report_dict: Output of daily_report().

    Returns
    -------
    Multi-line Markdown string.
    """
    r = report_dict
    lines = [
        f"# Daily Report — {r.get('date', 'N/A')}",
        f"*Generated: {r.get('generated_at', '')}*",
        "",
        "## Revenue",
        f"- Today   : **${r.get('revenue_today', 0):,.2f}**",
        f"- 7-day   : **${r.get('revenue_7d', 0):,.2f}**",
        "",
        "## Top Earners",
    ]
    for entry in r.get("top_earners", []):
        lines.append(f"  - {entry['agent']}: ${entry['revenue']:,.2f}")
    if not r.get("top_earners"):
        lines.append("  *(no data)*")

    lines += [
        "",
        "## Activity",
        f"- Active agents  : {r.get('active_agent_count', 0)}",
        f"- Messages sent  : {r.get('messages_sent', 0)}",
        f"- Plans executed : {r.get('plans_executed', 0)}",
        f"- Errors logged  : {r.get('errors', 0)}",
        "",
        "## Top Workflows",
    ]
    for wf in r.get("top_workflows", []):
        lines.append(f"  - {wf['workflow']}: {wf['count']} runs")
    if not r.get("top_workflows"):
        lines.append("  *(none)*")

    return "\n".join(lines)


def format_weekly_report(report_dict: dict[str, Any]) -> str:
    """
    Pretty-print a weekly_report() dict as a Markdown string.

    Parameters
    ----------
    report_dict: Output of weekly_report().

    Returns
    -------
    Multi-line Markdown string.
    """
    r = report_dict
    trend = r.get("revenue_trend_pct", 0)
    trend_arrow = "▲" if trend >= 0 else "▼"

    lines = [
        f"# Weekly Report — Week ending {r.get('week_ending', 'N/A')}",
        f"*Generated: {r.get('generated_at', '')}*",
        "",
        "## Revenue Summary",
        f"- This week : **${r.get('revenue_this_week', 0):,.2f}**",
        f"- Last week : ${r.get('revenue_last_week', 0):,.2f}",
        f"- Trend     : {trend_arrow} {abs(trend):.1f}%",
        "",
        "## Top Earners",
    ]
    for entry in r.get("top_earners", [])[:5]:
        lines.append(f"  - {entry['agent']}: ${entry['revenue']:,.2f}")
    if not r.get("top_earners"):
        lines.append("  *(no data)*")

    lines += [
        "",
        "## Agent Rankings",
        "| Agent | Runs | Errors | Error Rate | Score |",
        "|-------|------|--------|------------|-------|",
    ]
    for a in r.get("agent_rankings", [])[:10]:
        lines.append(
            f"| {a['agent']} | {a['runs']} | {a['errors']} "
            f"| {a['error_rate']:.1%} | {a['score']} |"
        )

    lines += [
        "",
        "## Activity",
        f"- Total agents   : {r.get('active_agent_count', 0)}",
        f"- Messages sent  : {r.get('messages_sent', 0)}",
        f"- Plans executed : {r.get('plans_executed', 0)}",
        f"- Total errors   : {r.get('errors_total', 0)}",
        "",
        "## Top Skills Used",
    ]
    for skill in r.get("top_skills", []):
        lines.append(f"  - {skill['skill']}: {skill['count']} uses")
    if not r.get("top_skills"):
        lines.append("  *(no data)*")

    lines += [
        "",
        "## Top Workflows",
    ]
    for wf in r.get("top_workflows", []):
        lines.append(f"  - {wf['workflow']}: {wf['count']} runs")
    if not r.get("top_workflows"):
        lines.append("  *(none)*")

    lines += [
        "",
        "## ROI by Agent",
    ]
    for entry in r.get("roi_by_agent", [])[:8]:
        lines.append(
            f"  - {entry['agent']}: "
            f"rev=${entry['revenue']:,.2f} "
            f"spend=${entry['spend']:,.2f} "
            f"roi={entry['roi_pct']:.0f}%"
        )
    if not r.get("roi_by_agent"):
        lines.append("  *(no data)*")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_report(report_type: str, content: str) -> str:
    """
    Save a report to the output/reports/ directory.

    Parameters
    ----------
    report_type: One of 'daily', 'weekly', 'scorecard', etc.
    content:     Report text content to save.

    Returns
    -------
    Absolute path to the saved file.
    """
    base_dir = os.path.join(
        os.path.dirname(__file__), "..", "output", "reports"
    )
    os.makedirs(base_dir, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filename = f"{report_type}_{today}.md"
    filepath = os.path.abspath(os.path.join(base_dir, filename))
    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(content)
    logger.info("report saved: %s", filepath)
    return filepath


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------


def schedule_reports(orchestrator: Any) -> None:
    """
    Register daily and weekly report generation jobs with APScheduler.

    Parameters
    ----------
    orchestrator: An object with a method ``add_job(func, trigger, **kwargs)``
                  compatible with APScheduler's BackgroundScheduler interface.

    The daily report runs at 00:05 UTC; the weekly report runs on Mondays at 00:10 UTC.
    """

    def _run_daily() -> None:
        try:
            report = daily_report()
            text = format_daily_report(report)
            path = save_report("daily", text)
            logger.info("Daily report saved: %s", path)
            try:
                from core.notifications import notify  # type: ignore
                notify(f"Daily report ready: {os.path.basename(path)}", level="info",
                       agent="reporter")
            except Exception:
                pass
        except Exception as exc:
            logger.error("Daily report generation failed: %s", exc)

    def _run_weekly() -> None:
        try:
            report = weekly_report()
            text = format_weekly_report(report)
            path = save_report("weekly", text)
            logger.info("Weekly report saved: %s", path)
            try:
                from core.notifications import notify  # type: ignore
                notify(f"Weekly report ready: {os.path.basename(path)}", level="info",
                       agent="reporter")
            except Exception:
                pass
        except Exception as exc:
            logger.error("Weekly report generation failed: %s", exc)

    try:
        orchestrator.add_job(_run_daily, "cron", hour=0, minute=5,
                             id="daily_report", replace_existing=True)
        orchestrator.add_job(_run_weekly, "cron", day_of_week="mon",
                             hour=0, minute=10,
                             id="weekly_report", replace_existing=True)
        logger.info("reporting: scheduled daily and weekly report jobs")
    except Exception as exc:
        logger.error("reporting: failed to schedule jobs: %s", exc)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("=== Daily Report ===")
    dr = daily_report()
    print(format_daily_report(dr))
    print("\n=== Weekly Report ===")
    wr = weekly_report()
    print(format_weekly_report(wr))
    print("\n=== Agent Scorecard ===")
    sc = agent_scorecard("writer")
    print(json.dumps(sc, indent=2))
    print("\n=== Revenue Chart Data (7d) ===")
    chart = revenue_chart_data(days=7)
    for pt in chart:
        print(f"  {pt['date']}: ${pt['amount']:.2f}")
    print("\n=== Activity Heatmap ===")
    hm = activity_heatmap(days=7)
    print(json.dumps(hm, indent=2))
