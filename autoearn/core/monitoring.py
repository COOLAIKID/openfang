"""
core/monitoring.py — Org-wide health monitoring for AutoEarn agents.

Reads agent_status from the shared SQLite DB, computes health scores,
tracks custom metrics, and exposes human-readable summaries.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None

# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------


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
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _init_schema(conn)
        _db_conn = conn
    return _db_conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metrics (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            agent      TEXT    NOT NULL,
            metric     TEXT    NOT NULL,
            value      REAL    NOT NULL,
            recorded_at TEXT   NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_agent_metric
            ON metrics (agent, metric, recorded_at DESC);

        CREATE TABLE IF NOT EXISTS agent_latencies (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            agent      TEXT    NOT NULL,
            ms         REAL    NOT NULL,
            recorded_at TEXT   NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_latencies_agent
            ON agent_latencies (agent, recorded_at DESC);
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _minutes_since(ts_str: str | None) -> float:
    """Return minutes since *ts_str* (ISO 8601). Returns 99999 if ts is None."""
    if not ts_str:
        return 99999.0
    try:
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() / 60.0
    except (ValueError, OverflowError):
        return 99999.0


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentHealth:
    name: str
    runs: int = 0
    errors: int = 0
    error_rate: float = 0.0
    last_run_age_minutes: float = 99999.0
    status: str = "unknown"          # healthy | degraded | dead | unknown
    last_result_preview: str = ""
    last_run_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "runs": self.runs,
            "errors": self.errors,
            "error_rate": round(self.error_rate, 4),
            "last_run_age_minutes": round(self.last_run_age_minutes, 1),
            "status": self.status,
            "last_result_preview": self.last_result_preview,
            "last_run_at": self.last_run_at,
        }


# ---------------------------------------------------------------------------
# Core read helpers
# ---------------------------------------------------------------------------


def _read_agent_statuses() -> list[sqlite3.Row]:
    db = _get_db()
    try:
        rows = db.execute(
            """
            SELECT agent_name, total_runs, total_errors, last_run_at,
                   last_result, status
            FROM agent_status
            ORDER BY agent_name
            """
        ).fetchall()
    except sqlite3.OperationalError:
        # Table may not exist yet
        rows = []
    return rows


def _compute_status(error_rate: float, age_minutes: float) -> str:
    if age_minutes > 240:
        return "dead"
    if error_rate >= 0.5 or age_minutes > 120:
        return "degraded"
    return "healthy"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_all() -> list[AgentHealth]:
    """
    Read all agent rows from agent_status and return a list of AgentHealth.

    Uses total_runs / total_errors to compute error_rate and determines
    status from error_rate and last_run recency.
    """
    rows = _read_agent_statuses()
    result: list[AgentHealth] = []
    for row in rows:
        runs = row["total_runs"] or 0
        errors = row["total_errors"] or 0
        error_rate = (errors / runs) if runs > 0 else 0.0
        age = _minutes_since(row["last_run_at"])
        status = _compute_status(error_rate, age)
        preview = str(row["last_result"] or "")[:120]
        result.append(AgentHealth(
            name=row["agent_name"],
            runs=runs,
            errors=errors,
            error_rate=error_rate,
            last_run_age_minutes=age,
            status=status,
            last_result_preview=preview,
            last_run_at=row["last_run_at"] or "",
        ))
    return result


def org_health_score() -> int:
    """
    Compute a 0-100 health score for the entire organisation.

    Scoring:
    - 40 pts: proportion of healthy agents (vs total known agents)
    - 30 pts: inverse of average error rate across all agents
    - 30 pts: proportion of agents that have run in the last 60 min
    """
    agents = check_all()
    if not agents:
        return 0

    total = len(agents)
    healthy = sum(1 for a in agents if a.status == "healthy")
    avg_error = sum(a.error_rate for a in agents) / total
    recently_run = sum(1 for a in agents if a.last_run_age_minutes <= 60)

    health_pts = int(40 * (healthy / total))
    error_pts  = int(30 * max(0.0, 1.0 - avg_error))
    recent_pts = int(30 * (recently_run / total))

    return min(100, health_pts + error_pts + recent_pts)


def stale_agents(threshold_minutes: float = 120.0) -> list[AgentHealth]:
    """Return agents that haven't run within *threshold_minutes*."""
    return [a for a in check_all() if a.last_run_age_minutes > threshold_minutes]


def erroring_agents(threshold_rate: float = 0.5) -> list[AgentHealth]:
    """Return agents whose error_rate exceeds *threshold_rate*."""
    return [a for a in check_all() if a.error_rate >= threshold_rate]


def health_summary() -> str:
    """
    Return a human-readable multi-line health report.

    Suitable for display in a council briefing or Slack notification.
    """
    agents = check_all()
    score = org_health_score()
    stale = stale_agents()
    erroring = erroring_agents()

    lines: list[str] = [
        f"=== AutoEarn Org Health Report ===",
        f"Overall score : {score}/100",
        f"Total agents  : {len(agents)}",
        f"Healthy       : {sum(1 for a in agents if a.status == 'healthy')}",
        f"Degraded      : {sum(1 for a in agents if a.status == 'degraded')}",
        f"Dead          : {sum(1 for a in agents if a.status == 'dead')}",
        "",
    ]

    if agents:
        lines.append("Agent details:")
        for a in sorted(agents, key=lambda x: x.name):
            icon = {"healthy": "✓", "degraded": "!", "dead": "✗"}.get(a.status, "?")
            lines.append(
                f"  [{icon}] {a.name:<20} runs={a.runs:>5}  "
                f"err_rate={a.error_rate:.1%}  "
                f"last_run={a.last_run_age_minutes:.0f}m ago"
            )
        lines.append("")

    if stale:
        lines.append(f"Stale agents (>{120}m since last run):")
        for a in stale:
            lines.append(f"  - {a.name} ({a.last_run_age_minutes:.0f}m ago)")
        lines.append("")

    if erroring:
        lines.append("High-error agents (≥50% error rate):")
        for a in erroring:
            lines.append(f"  - {a.name} ({a.error_rate:.1%} error rate)")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def log_metric(agent: str, metric: str, value: float) -> None:
    """Store a custom numeric metric for *agent*."""
    db = _get_db()
    with _lock:
        db.execute(
            "INSERT INTO metrics (agent, metric, value, recorded_at) VALUES (?,?,?,?)",
            (agent, metric, value, _now()),
        )
        db.commit()


def get_metrics(agent: str, metric: str, last_n: int = 100) -> list[dict[str, Any]]:
    """
    Retrieve the last *last_n* data points for a given agent + metric.

    Returns a list of {'ts': str, 'value': float} dicts ordered oldest-first.
    """
    db = _get_db()
    rows = db.execute(
        """
        SELECT recorded_at AS ts, value
        FROM metrics
        WHERE agent = ? AND metric = ?
        ORDER BY recorded_at DESC
        LIMIT ?
        """,
        (agent, metric, last_n),
    ).fetchall()
    return [{"ts": r["ts"], "value": r["value"]} for r in reversed(rows)]


def record_latency(agent: str, ms: float) -> None:
    """Record a single run latency observation for *agent*."""
    db = _get_db()
    with _lock:
        db.execute(
            "INSERT INTO agent_latencies (agent, ms, recorded_at) VALUES (?,?,?)",
            (agent, ms, _now()),
        )
        db.commit()
    # Also mirror to the generic metrics table for unified querying
    log_metric(agent, "latency_ms", ms)


def p95_latency(agent: str) -> float | None:
    """
    Return the 95th-percentile latency (ms) for *agent* across all recorded
    observations. Returns None if no data exists.
    """
    db = _get_db()
    rows = db.execute(
        "SELECT ms FROM agent_latencies WHERE agent = ? ORDER BY ms",
        (agent,),
    ).fetchall()
    if not rows:
        return None
    values = [r["ms"] for r in rows]
    idx = int(len(values) * 0.95)
    idx = min(idx, len(values) - 1)
    return values[idx]


# ---------------------------------------------------------------------------
# Agent-level helpers used internally by agent runners
# ---------------------------------------------------------------------------


def record_run(agent: str, success: bool, result_preview: str = "") -> None:
    """
    Update the agent_status table after a run completes.

    Creates the row if it doesn't exist; increments counters otherwise.
    """
    db = _get_db()
    ts = _now()
    with _lock:
        try:
            db.execute(
                """
                INSERT INTO agent_status
                    (agent_name, total_runs, total_errors, last_run_at, last_result, status)
                VALUES (?, 1, ?, ?, ?, 'active')
                ON CONFLICT(agent_name) DO UPDATE SET
                    total_runs   = total_runs + 1,
                    total_errors = total_errors + excluded.total_errors,
                    last_run_at  = excluded.last_run_at,
                    last_result  = excluded.last_result
                """,
                (agent, 0 if success else 1, ts, result_preview[:250]),
            )
            db.commit()
        except sqlite3.OperationalError as exc:
            logger.warning("record_run failed (agent_status table missing?): %s", exc)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log_metric("cfo", "revenue_usd", 120.50)
    log_metric("cfo", "revenue_usd", 230.00)
    record_latency("cfo", 450.0)
    record_latency("cfo", 1200.0)
    record_latency("cfo", 800.0)
    print("metrics:", get_metrics("cfo", "revenue_usd"))
    print("p95 latency:", p95_latency("cfo"))
    print("\n" + health_summary())
    print("org score:", org_health_score())
