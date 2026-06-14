"""CRM-style lead and deal tracker for the outreach team.

The outreach agents (scout → proposer → closer) need to track leads through
a sales pipeline. This module provides a lightweight SQLite-backed CRM:

- :class:`Lead` — a potential client or deal
- Pipeline stages: ``new → contacted → proposal_sent → negotiating → won | lost``
- Revenue projection based on win probability
- Activity logging per lead
- Reporting: pipeline value, conversion rates, average cycle time

Agents call the tool-friendly wrappers (``track_lead``, ``update_stage``, etc.)
rather than the internal functions directly.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"

STAGES = ["new", "contacted", "proposal_sent", "negotiating", "won", "lost"]
STAGE_WIN_PROBABILITIES = {
    "new": 0.05,
    "contacted": 0.15,
    "proposal_sent": 0.30,
    "negotiating": 0.60,
    "won": 1.0,
    "lost": 0.0,
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Lead:
    name: str
    source: str = ""
    contact_email: str = ""
    company: str = ""
    niche: str = ""
    stage: str = "new"
    estimated_value: float = 0.0
    currency: str = "USD"
    probability: float = 0.0
    notes: str = ""
    assigned_to: str = "scout"
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    id: int = 0

    @property
    def weighted_value(self) -> float:
        prob = self.probability or STAGE_WIN_PROBABILITIES.get(self.stage, 0.05)
        return self.estimated_value * prob

    def to_row(self) -> tuple:
        return (
            self.name, self.source, self.contact_email, self.company,
            self.niche, self.stage, self.estimated_value, self.currency,
            self.probability, self.notes, self.assigned_to,
            json.dumps(self.tags), self.created_at, self.updated_at,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Lead":
        lead = cls(
            name=row["name"],
            source=row["source"] or "",
            contact_email=row["contact_email"] or "",
            company=row["company"] or "",
            niche=row["niche"] or "",
            stage=row["stage"],
            estimated_value=row["estimated_value"] or 0.0,
            currency=row["currency"] or "USD",
            probability=row["probability"] or 0.0,
            notes=row["notes"] or "",
            assigned_to=row["assigned_to"] or "scout",
            tags=json.loads(row["tags"] or "[]"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        lead.id = row["id"]
        return lead


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema() -> None:
    conn = _get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                source          TEXT    NOT NULL DEFAULT '',
                contact_email   TEXT    NOT NULL DEFAULT '',
                company         TEXT    NOT NULL DEFAULT '',
                niche           TEXT    NOT NULL DEFAULT '',
                stage           TEXT    NOT NULL DEFAULT 'new',
                estimated_value REAL    NOT NULL DEFAULT 0,
                currency        TEXT    NOT NULL DEFAULT 'USD',
                probability     REAL    NOT NULL DEFAULT 0,
                notes           TEXT    NOT NULL DEFAULT '',
                assigned_to     TEXT    NOT NULL DEFAULT 'scout',
                tags            TEXT    NOT NULL DEFAULT '[]',
                created_at      REAL    NOT NULL,
                updated_at      REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lead_activities (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id     INTEGER NOT NULL,
                agent       TEXT    NOT NULL,
                activity    TEXT    NOT NULL,
                note        TEXT    NOT NULL DEFAULT '',
                ts          REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_stage ON leads(stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_niche ON leads(niche)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_la_lead ON lead_activities(lead_id)")
    conn.close()


_schema_ready = False


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_lead(
    name: str,
    source: str = "",
    contact_email: str = "",
    company: str = "",
    niche: str = "",
    estimated_value: float = 0.0,
    notes: str = "",
    assigned_to: str = "scout",
    tags: list[str] | None = None,
) -> Lead:
    _ensure()
    lead = Lead(
        name=name, source=source, contact_email=contact_email,
        company=company, niche=niche, estimated_value=estimated_value,
        notes=notes, assigned_to=assigned_to, tags=tags or [],
    )
    conn = _get_db()
    with conn:
        cur = conn.execute(
            """INSERT INTO leads
               (name, source, contact_email, company, niche, stage, estimated_value,
                currency, probability, notes, assigned_to, tags, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            lead.to_row(),
        )
        lead.id = cur.lastrowid
    conn.close()
    _log_activity(lead.id, assigned_to, "created", f"Lead created from {source}")
    return lead


def get_lead(lead_id: int) -> Optional[Lead]:
    _ensure()
    conn = _get_db()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    return Lead.from_row(row) if row else None


def find_lead(name: str) -> Optional[Lead]:
    _ensure()
    conn = _get_db()
    row = conn.execute("SELECT * FROM leads WHERE name LIKE ?", (f"%{name}%",)).fetchone()
    conn.close()
    return Lead.from_row(row) if row else None


def update_stage(lead_id: int, new_stage: str, agent: str = "system", note: str = "") -> str:
    if new_stage not in STAGES:
        return f"ERROR: invalid stage '{new_stage}'. Valid: {', '.join(STAGES)}"
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute(
            "UPDATE leads SET stage=?, updated_at=? WHERE id=?",
            (new_stage, time.time(), lead_id),
        )
    conn.close()
    _log_activity(lead_id, agent, "stage_change", f"→ {new_stage}. {note}")
    return f"Lead #{lead_id} moved to stage '{new_stage}'"


def update_lead(lead_id: int, **kwargs: Any) -> str:
    _ensure()
    allowed = {"contact_email", "company", "niche", "estimated_value", "probability",
               "notes", "assigned_to", "tags", "currency"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return "No valid fields to update"
    updates["updated_at"] = time.time()
    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])
    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [lead_id]
    conn = _get_db()
    with conn:
        conn.execute(f"UPDATE leads SET {cols} WHERE id=?", vals)
    conn.close()
    return f"Updated lead #{lead_id}: {list(updates.keys())}"


def _log_activity(lead_id: int, agent: str, activity: str, note: str = "") -> None:
    conn = _get_db()
    with conn:
        conn.execute(
            "INSERT INTO lead_activities (lead_id, agent, activity, note, ts) VALUES (?,?,?,?,?)",
            (lead_id, agent, activity, note, time.time()),
        )
    conn.close()


def add_note(lead_id: int, agent: str, note: str) -> str:
    _ensure()
    _log_activity(lead_id, agent, "note", note)
    return f"Added note to lead #{lead_id}"


def get_activities(lead_id: int, limit: int = 20) -> list[dict[str, Any]]:
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM lead_activities WHERE lead_id=? ORDER BY ts DESC LIMIT ?",
        (lead_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_pipeline(stage: str | None = None, assigned_to: str | None = None) -> list[Lead]:
    _ensure()
    conn = _get_db()
    conds: list[str] = []
    params: list[Any] = []
    if stage:
        conds.append("stage=?")
        params.append(stage)
    if assigned_to:
        conds.append("assigned_to=?")
        params.append(assigned_to)
    where = "WHERE " + " AND ".join(conds) if conds else ""
    rows = conn.execute(f"SELECT * FROM leads {where} ORDER BY updated_at DESC", params).fetchall()
    conn.close()
    return [Lead.from_row(r) for r in rows]


def pipeline_summary() -> dict[str, Any]:
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        """SELECT stage, COUNT(*) as count,
                  SUM(estimated_value) as total_value,
                  AVG(estimated_value) as avg_value
           FROM leads GROUP BY stage"""
    ).fetchall()
    conn.close()
    stages_data = {r["stage"]: {
        "count": r["count"],
        "total_value": round(r["total_value"] or 0, 2),
        "avg_value": round(r["avg_value"] or 0, 2),
        "weighted_value": round((r["total_value"] or 0) * STAGE_WIN_PROBABILITIES.get(r["stage"], 0.05), 2),
    } for r in rows}

    total_weighted = sum(s["weighted_value"] for s in stages_data.values())
    won_value = stages_data.get("won", {}).get("total_value", 0)
    total_leads = sum(s["count"] for s in stages_data.values())
    won_count = stages_data.get("won", {}).get("count", 0)

    return {
        "pipeline": stages_data,
        "total_leads": total_leads,
        "total_pipeline_value": sum(s["total_value"] for s in stages_data.values()),
        "weighted_pipeline_value": round(total_weighted, 2),
        "won_revenue": won_value,
        "win_rate": round(won_count / max(1, total_leads), 3),
    }


def leads_due_for_followup(days_since_update: int = 3) -> list[Lead]:
    """Find leads that haven't been updated in N days and are still active."""
    _ensure()
    cutoff = time.time() - days_since_update * 86400
    active_stages = ["contacted", "proposal_sent", "negotiating"]
    placeholders = ",".join("?" * len(active_stages))
    conn = _get_db()
    rows = conn.execute(
        f"SELECT * FROM leads WHERE stage IN ({placeholders}) AND updated_at < ? ORDER BY updated_at ASC",
        [*active_stages, cutoff],
    ).fetchall()
    conn.close()
    return [Lead.from_row(r) for r in rows]


def won_deals(days: int = 30) -> list[Lead]:
    """Recently won deals."""
    _ensure()
    cutoff = time.time() - days * 86400
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM leads WHERE stage='won' AND updated_at > ? ORDER BY updated_at DESC",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [Lead.from_row(r) for r in rows]


def revenue_from_leads(days: int = 30) -> float:
    """Total value of won deals in the past N days."""
    wins = won_deals(days)
    return sum(l.estimated_value for l in wins)


# ---------------------------------------------------------------------------
# Tool-friendly wrappers
# ---------------------------------------------------------------------------

def track_lead(
    name: str, source: str = "", contact_email: str = "",
    company: str = "", estimated_value: float = 0.0,
    notes: str = "", assigned_to: str = "scout",
) -> str:
    try:
        lead = create_lead(name, source, contact_email, company, notes=notes,
                          estimated_value=float(estimated_value), assigned_to=assigned_to)
        return f"Tracked lead #{lead.id}: '{lead.name}' (${lead.estimated_value:.0f}, assigned to {lead.assigned_to})"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def move_lead_stage(lead_id: int, stage: str, agent: str = "system", note: str = "") -> str:
    return update_stage(int(lead_id), stage, agent, note)


def get_pipeline_report() -> str:
    """Full pipeline report as JSON string for agent consumption."""
    return json.dumps(pipeline_summary())


def get_followup_leads() -> str:
    """Leads overdue for follow-up, as JSON."""
    leads = leads_due_for_followup()
    return json.dumps([
        {"id": l.id, "name": l.name, "stage": l.stage, "company": l.company,
         "assigned_to": l.assigned_to, "estimated_value": l.estimated_value}
        for l in leads
    ])


def search_leads(query: str) -> str:
    """Search leads by name, company, or niche."""
    _ensure()
    conn = _get_db()
    q = f"%{query}%"
    rows = conn.execute(
        "SELECT * FROM leads WHERE name LIKE ? OR company LIKE ? OR niche LIKE ? ORDER BY updated_at DESC LIMIT 20",
        (q, q, q),
    ).fetchall()
    conn.close()
    leads = [Lead.from_row(r) for r in rows]
    return json.dumps([
        {"id": l.id, "name": l.name, "company": l.company, "stage": l.stage,
         "estimated_value": l.estimated_value, "assigned_to": l.assigned_to}
        for l in leads
    ])
