"""
core/treasury.py — Organizational treasury and financial management for AutoEarn.

Tracks budgets per agent, records expenditures by category, computes ROI,
and generates financial reports.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

expense_categories: list[str] = [
    "ads",
    "api_costs",
    "tools",
    "subscriptions",
    "outsourcing",
    "other",
]

PERIODS = ("daily", "weekly", "monthly")

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
        _init_schema(conn)
        _db_conn = conn
    return _db_conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS budgets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT    NOT NULL UNIQUE,
            amount_usd  REAL    NOT NULL DEFAULT 0.0,
            period      TEXT    NOT NULL DEFAULT 'monthly',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_budgets_agent ON budgets (agent);

        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT    NOT NULL,
            amount_usd  REAL    NOT NULL,
            category    TEXT    NOT NULL DEFAULT 'other',
            note        TEXT    NOT NULL DEFAULT '',
            recorded_at TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_expenses_agent ON expenses (agent, recorded_at DESC);
        CREATE INDEX IF NOT EXISTS idx_expenses_cat   ON expenses (category, recorded_at DESC);
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _period_start(period: str) -> str:
    """Return the ISO timestamp for the start of the current period."""
    now = datetime.now(timezone.utc)
    if period == "daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:  # monthly
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat()


def _days_ago(days: int) -> str:
    """Return ISO timestamp for N days ago."""
    ts = datetime.now(timezone.utc) - timedelta(days=days)
    return ts.isoformat()


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class Budget:
    """A budget allocation for one agent for one period."""
    agent: str
    amount_usd: float
    period: str
    spent: float = 0.0
    remaining: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "amount_usd": round(self.amount_usd, 2),
            "period": self.period,
            "spent": round(self.spent, 2),
            "remaining": round(self.remaining, 2),
        }


# ---------------------------------------------------------------------------
# Budget management
# ---------------------------------------------------------------------------


def allocate_budget(agent: str, amount: float, period: str = "monthly") -> Budget:
    """
    Set or update a budget allocation for an agent.

    Parameters
    ----------
    agent:  Agent name.
    amount: Budget amount in USD.
    period: One of 'daily', 'weekly', 'monthly'.

    Returns
    -------
    The updated Budget object including current spend.
    """
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got '{period}'")
    db = _get_db()
    ts = _now()
    with _lock:
        db.execute(
            """
            INSERT INTO budgets (agent, amount_usd, period, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(agent) DO UPDATE SET
                amount_usd = excluded.amount_usd,
                period     = excluded.period,
                updated_at = excluded.updated_at
            """,
            (agent, amount, period, ts, ts),
        )
        db.commit()
    logger.info("budget allocated: agent=%s amount=%.2f period=%s", agent, amount, period)
    return get_budget(agent)


def get_budget(agent: str) -> Optional[Budget]:
    """
    Retrieve the current budget for an agent, including spend this period.

    Returns None if no budget has been allocated for this agent.
    """
    db = _get_db()
    row = db.execute(
        "SELECT agent, amount_usd, period FROM budgets WHERE agent = ?",
        (agent,),
    ).fetchone()
    if row is None:
        return None

    period = row["period"]
    period_start = _period_start(period)
    spend_row = db.execute(
        """
        SELECT COALESCE(SUM(amount_usd), 0.0) AS total
        FROM expenses
        WHERE agent = ? AND recorded_at >= ?
        """,
        (agent, period_start),
    ).fetchone()
    spent = spend_row["total"] if spend_row else 0.0
    amount = row["amount_usd"]
    return Budget(
        agent=agent,
        amount_usd=amount,
        period=period,
        spent=spent,
        remaining=max(0.0, amount - spent),
    )


# ---------------------------------------------------------------------------
# Expense recording
# ---------------------------------------------------------------------------


def record_spend(
    agent: str,
    amount: float,
    category: str = "other",
    note: str = "",
) -> int:
    """
    Log an expenditure for an agent.

    Parameters
    ----------
    agent:    Agent incurring the expense.
    amount:   Amount in USD (positive value).
    category: One of the expense_categories.
    note:     Optional description.

    Returns
    -------
    The expense record ID.
    """
    if category not in expense_categories:
        logger.warning(
            "treasury.record_spend: unknown category '%s', defaulting to 'other'", category
        )
        category = "other"
    if amount < 0:
        raise ValueError("Expense amount must be non-negative")

    db = _get_db()
    with _lock:
        cursor = db.execute(
            "INSERT INTO expenses (agent, amount_usd, category, note, recorded_at) VALUES (?,?,?,?,?)",
            (agent, amount, category, note, _now()),
        )
        db.commit()
    logger.debug("expense recorded: agent=%s amount=%.2f cat=%s", agent, amount, category)
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


def get_spend_breakdown(
    agent: Optional[str] = None,
    days: int = 30,
) -> dict[str, float]:
    """
    Return total spend by category over the last *days* days.

    Parameters
    ----------
    agent: If provided, restrict to this agent. None = org-wide.
    days:  Look-back window in days.

    Returns
    -------
    Dict mapping category to total USD spent.
    """
    db = _get_db()
    since = _days_ago(days)
    if agent:
        rows = db.execute(
            """
            SELECT category, COALESCE(SUM(amount_usd), 0.0) AS total
            FROM expenses
            WHERE agent = ? AND recorded_at >= ?
            GROUP BY category
            """,
            (agent, since),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT category, COALESCE(SUM(amount_usd), 0.0) AS total
            FROM expenses
            WHERE recorded_at >= ?
            GROUP BY category
            """,
            (since,),
        ).fetchall()
    breakdown: dict[str, float] = {cat: 0.0 for cat in expense_categories}
    for row in rows:
        cat = row["category"] if row["category"] in expense_categories else "other"
        breakdown[cat] = round(breakdown[cat] + row["total"], 2)
    return breakdown


def _get_revenue(agent: Optional[str] = None, days: int = 30) -> float:
    """Pull total revenue from the metrics table (revenue_usd metric)."""
    db = _get_db()
    since = _days_ago(days)
    try:
        if agent:
            row = db.execute(
                """
                SELECT COALESCE(SUM(value), 0.0) AS total
                FROM metrics
                WHERE agent = ? AND metric = 'revenue_usd' AND recorded_at >= ?
                """,
                (agent, since),
            ).fetchone()
        else:
            row = db.execute(
                """
                SELECT COALESCE(SUM(value), 0.0) AS total
                FROM metrics
                WHERE metric = 'revenue_usd' AND recorded_at >= ?
                """,
                (since,),
            ).fetchone()
        return float(row["total"]) if row else 0.0
    except sqlite3.OperationalError:
        return 0.0


def roi_summary(days: int = 30) -> list[dict[str, Any]]:
    """
    Compute revenue vs spend per agent over the last *days* days.

    Returns
    -------
    List of dicts sorted by ROI descending:
    [{agent, revenue, spend, profit, roi_pct}]
    """
    db = _get_db()
    since = _days_ago(days)

    # Get all agents with spend or budget
    agent_set: set[str] = set()
    for row in db.execute("SELECT DISTINCT agent FROM expenses").fetchall():
        agent_set.add(row["agent"])
    for row in db.execute("SELECT DISTINCT agent FROM budgets").fetchall():
        agent_set.add(row["agent"])

    results = []
    for agent in sorted(agent_set):
        revenue = _get_revenue(agent=agent, days=days)
        spend_row = db.execute(
            """
            SELECT COALESCE(SUM(amount_usd), 0.0) AS total
            FROM expenses
            WHERE agent = ? AND recorded_at >= ?
            """,
            (agent, since),
        ).fetchone()
        spend = float(spend_row["total"]) if spend_row else 0.0
        profit = revenue - spend
        roi_pct = (profit / spend * 100.0) if spend > 0 else (100.0 if revenue > 0 else 0.0)
        results.append({
            "agent": agent,
            "revenue": round(revenue, 2),
            "spend": round(spend, 2),
            "profit": round(profit, 2),
            "roi_pct": round(roi_pct, 1),
        })

    results.sort(key=lambda x: x["roi_pct"], reverse=True)
    return results


def budget_health() -> list[dict[str, Any]]:
    """
    Return budget health for all agents with allocated budgets.

    Flags over-budget agents (spent > allocated) and under-utilized ones
    (spent < 10% of budget with > 50% of the period elapsed).

    Returns
    -------
    List of dicts: {agent, status, spent_pct, amount, spent, remaining}
    """
    db = _get_db()
    rows = db.execute("SELECT agent, amount_usd, period FROM budgets").fetchall()
    results = []
    for row in rows:
        b = get_budget(row["agent"])
        if b is None:
            continue
        spent_pct = (b.spent / b.amount_usd * 100.0) if b.amount_usd > 0 else 0.0
        if b.spent > b.amount_usd:
            status = "over_budget"
        elif spent_pct < 10.0:
            status = "under_utilized"
        else:
            status = "on_track"
        results.append({
            "agent": b.agent,
            "status": status,
            "spent_pct": round(spent_pct, 1),
            "amount_usd": b.amount_usd,
            "spent": b.spent,
            "remaining": b.remaining,
            "period": b.period,
        })
    return results


def suggest_reallocation() -> list[dict[str, Any]]:
    """
    Simple heuristic reallocation: move budget from zero-ROI agents to high-ROI ones.

    Returns a list of suggested transfers:
    [{from_agent, to_agent, amount_usd, reason}]
    """
    roi = roi_summary(days=30)
    health = {bh["agent"]: bh for bh in budget_health()}

    zero_roi = [r for r in roi if r["roi_pct"] <= 0 and health.get(r["agent"])]
    high_roi = [r for r in roi if r["roi_pct"] > 100 and health.get(r["agent"])]

    suggestions = []
    for loser in zero_roi:
        bh = health.get(loser["agent"])
        if bh and bh["remaining"] > 0:
            transfer_amount = round(bh["remaining"] * 0.5, 2)
            if transfer_amount < 1.0:
                continue
            for winner in high_roi[:2]:  # Top 2 high-ROI agents
                suggestions.append({
                    "from_agent": loser["agent"],
                    "to_agent": winner["agent"],
                    "amount_usd": transfer_amount,
                    "reason": (
                        f"{loser['agent']} has {loser['roi_pct']:.0f}% ROI with "
                        f"${bh['remaining']:.2f} unspent; "
                        f"{winner['agent']} has {winner['roi_pct']:.0f}% ROI"
                    ),
                })
    return suggestions


def financial_report(days: int = 7) -> str:
    """
    Generate a full P&L report as a human-readable string.

    Parameters
    ----------
    days: Look-back window in days (default 7 = weekly).

    Returns
    -------
    Multi-line Markdown string with revenue, expenses, ROI, and budget health.
    """
    total_revenue = _get_revenue(days=days)
    breakdown = get_spend_breakdown(days=days)
    total_spend = sum(breakdown.values())
    profit = total_revenue - total_spend
    roi = (profit / total_spend * 100) if total_spend > 0 else 0.0

    period_label = f"last {days} days"
    lines: list[str] = [
        f"# AutoEarn Financial Report — {period_label}",
        f"Generated: {_now()}",
        "",
        "## Summary",
        f"- **Revenue** : ${total_revenue:,.2f}",
        f"- **Expenses**: ${total_spend:,.2f}",
        f"- **Profit**  : ${profit:,.2f}",
        f"- **ROI**     : {roi:.1f}%",
        "",
        "## Expense Breakdown",
    ]
    for cat, amt in sorted(breakdown.items(), key=lambda x: -x[1]):
        if amt > 0:
            lines.append(f"  - {cat:<15} ${amt:,.2f}")

    lines += ["", "## ROI by Agent"]
    for entry in roi_summary(days=days):
        lines.append(
            f"  - {entry['agent']:<20} "
            f"rev=${entry['revenue']:,.2f}  "
            f"spend=${entry['spend']:,.2f}  "
            f"roi={entry['roi_pct']:.0f}%"
        )

    lines += ["", "## Budget Health"]
    for bh in budget_health():
        icon = {"over_budget": "OVER", "under_utilized": "LOW", "on_track": "OK"}.get(
            bh["status"], "?"
        )
        lines.append(
            f"  - [{icon}] {bh['agent']:<20} "
            f"allocated=${bh['amount_usd']:,.2f}  "
            f"spent=${bh['spent']:,.2f} ({bh['spent_pct']:.0f}%)  "
            f"period={bh['period']}"
        )

    suggestions = suggest_reallocation()
    if suggestions:
        lines += ["", "## Suggested Reallocations"]
        for s in suggestions:
            lines.append(f"  - Move ${s['amount_usd']:.2f} from {s['from_agent']} → {s['to_agent']}")
            lines.append(f"    Reason: {s['reason']}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    allocate_budget("writer", 200.0, "monthly")
    allocate_budget("researcher", 100.0, "monthly")
    allocate_budget("cfo", 50.0, "weekly")
    record_spend("writer", 25.50, "api_costs", "GPT-4 usage for articles")
    record_spend("writer", 10.00, "tools", "Grammarly subscription")
    record_spend("researcher", 5.00, "subscriptions", "Ahrefs")
    print(get_budget("writer"))
    print("Breakdown:", get_spend_breakdown(days=30))
    print("ROI:", roi_summary(days=30))
    print("Budget health:", budget_health())
    print("\n" + financial_report(days=7))
