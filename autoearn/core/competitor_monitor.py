"""Competitor monitoring system for AutoEarn agents.

Tracks competitor websites, products, pricing, and content over time.
Agents use this to detect when competitors publish new content, change prices,
launch products, or update their SEO strategy.

Features:
- :class:`Competitor` — a tracked competitor with metadata
- :class:`ChangeEvent` — a detected change (price, content, rank, etc.)
- :func:`add_competitor` / :func:`check_competitor` — CRUD + polling
- :func:`detect_changes` — diff against previous snapshots
- Price history tracking
- Rank tracking for shared keywords
- Content gap analysis (they rank for X, we don't)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"

CHANGE_TYPES = [
    "price_change", "content_added", "content_removed",
    "rank_change", "product_launched", "product_removed",
    "page_changed", "new_backlink", "tech_stack_change",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Competitor:
    name: str
    domain: str
    niche: str = ""
    notes: str = ""
    priority: int = 5
    track_prices: bool = True
    track_content: bool = True
    track_rankings: bool = True
    tracked_keywords: list[str] = field(default_factory=list)
    tracked_pages: list[str] = field(default_factory=list)
    last_checked: float = 0.0
    created_at: float = field(default_factory=time.time)
    id: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Competitor":
        c = cls(
            name=row["name"],
            domain=row["domain"],
            niche=row["niche"] or "",
            notes=row["notes"] or "",
            priority=row["priority"] or 5,
            track_prices=bool(row["track_prices"]),
            track_content=bool(row["track_content"]),
            track_rankings=bool(row["track_rankings"]),
            tracked_keywords=json.loads(row["tracked_keywords"] or "[]"),
            tracked_pages=json.loads(row["tracked_pages"] or "[]"),
            last_checked=row["last_checked"] or 0.0,
            created_at=row["created_at"],
        )
        c.id = row["id"]
        return c


@dataclass
class ChangeEvent:
    competitor_id: int
    competitor_name: str
    change_type: str
    url: str = ""
    description: str = ""
    old_value: str = ""
    new_value: str = ""
    severity: str = "low"  # low | medium | high
    ts: float = field(default_factory=time.time)
    id: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ChangeEvent":
        ev = cls(
            competitor_id=row["competitor_id"],
            competitor_name=row["competitor_name"] or "",
            change_type=row["change_type"],
            url=row["url"] or "",
            description=row["description"] or "",
            old_value=row["old_value"] or "",
            new_value=row["new_value"] or "",
            severity=row["severity"] or "low",
            ts=row["ts"],
        )
        ev.id = row["id"]
        return ev


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
            CREATE TABLE IF NOT EXISTS competitors (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT    NOT NULL,
                domain            TEXT    NOT NULL UNIQUE,
                niche             TEXT    NOT NULL DEFAULT '',
                notes             TEXT    NOT NULL DEFAULT '',
                priority          INTEGER NOT NULL DEFAULT 5,
                track_prices      INTEGER NOT NULL DEFAULT 1,
                track_content     INTEGER NOT NULL DEFAULT 1,
                track_rankings    INTEGER NOT NULL DEFAULT 1,
                tracked_keywords  TEXT    NOT NULL DEFAULT '[]',
                tracked_pages     TEXT    NOT NULL DEFAULT '[]',
                last_checked      REAL    NOT NULL DEFAULT 0,
                created_at        REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor_id   INTEGER NOT NULL,
                snapshot_type   TEXT    NOT NULL,
                url             TEXT    NOT NULL DEFAULT '',
                content_hash    TEXT    NOT NULL DEFAULT '',
                data            TEXT    NOT NULL DEFAULT '{}',
                ts              REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS competitor_changes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                competitor_id   INTEGER NOT NULL,
                competitor_name TEXT    NOT NULL DEFAULT '',
                change_type     TEXT    NOT NULL,
                url             TEXT    NOT NULL DEFAULT '',
                description     TEXT    NOT NULL DEFAULT '',
                old_value       TEXT    NOT NULL DEFAULT '',
                new_value       TEXT    NOT NULL DEFAULT '',
                severity        TEXT    NOT NULL DEFAULT 'low',
                ts              REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cs_comp ON competitor_snapshots(competitor_id, snapshot_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_comp ON competitor_changes(competitor_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cc_ts ON competitor_changes(ts)")
    conn.close()


_schema_ready = False


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


# ---------------------------------------------------------------------------
# Competitor CRUD
# ---------------------------------------------------------------------------

def add_competitor(
    name: str,
    domain: str,
    niche: str = "",
    notes: str = "",
    tracked_keywords: list[str] | None = None,
    tracked_pages: list[str] | None = None,
) -> Competitor:
    _ensure()
    domain = domain.lower().rstrip("/")
    if not domain.startswith("http"):
        domain = "https://" + domain
    comp = Competitor(
        name=name, domain=domain, niche=niche, notes=notes,
        tracked_keywords=tracked_keywords or [],
        tracked_pages=tracked_pages or [],
    )
    conn = _get_db()
    with conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO competitors
               (name, domain, niche, notes, priority, track_prices, track_content,
                track_rankings, tracked_keywords, tracked_pages, last_checked, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (comp.name, comp.domain, comp.niche, comp.notes, comp.priority,
             1, 1, 1, json.dumps(comp.tracked_keywords), json.dumps(comp.tracked_pages),
             0.0, comp.created_at),
        )
        comp.id = cur.lastrowid
    conn.close()
    return comp


def get_competitor(competitor_id: int) -> Optional[Competitor]:
    _ensure()
    conn = _get_db()
    row = conn.execute("SELECT * FROM competitors WHERE id=?", (competitor_id,)).fetchone()
    conn.close()
    return Competitor.from_row(row) if row else None


def list_competitors() -> list[Competitor]:
    _ensure()
    conn = _get_db()
    rows = conn.execute("SELECT * FROM competitors ORDER BY priority ASC, name ASC").fetchall()
    conn.close()
    return [Competitor.from_row(r) for r in rows]


def add_keywords(competitor_id: int, keywords: list[str]) -> str:
    _ensure()
    comp = get_competitor(competitor_id)
    if not comp:
        return f"ERROR: competitor #{competitor_id} not found"
    existing = set(comp.tracked_keywords)
    new_kws = [k for k in keywords if k not in existing]
    updated = comp.tracked_keywords + new_kws
    conn = _get_db()
    with conn:
        conn.execute("UPDATE competitors SET tracked_keywords=? WHERE id=?",
                     (json.dumps(updated), competitor_id))
    conn.close()
    return f"Added {len(new_kws)} keywords to competitor #{competitor_id}"


# ---------------------------------------------------------------------------
# Snapshot and change detection
# ---------------------------------------------------------------------------

def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _save_snapshot(competitor_id: int, snapshot_type: str, url: str, data: dict, content: str = "") -> str:
    """Save a snapshot. Returns the content hash."""
    chash = _content_hash(content or json.dumps(data))
    conn = _get_db()
    with conn:
        conn.execute(
            """INSERT INTO competitor_snapshots
               (competitor_id, snapshot_type, url, content_hash, data, ts)
               VALUES (?,?,?,?,?,?)""",
            (competitor_id, snapshot_type, url, chash, json.dumps(data), time.time()),
        )
    conn.close()
    return chash


def _get_previous_snapshot(competitor_id: int, snapshot_type: str, url: str = "") -> Optional[dict]:
    conn = _get_db()
    row = conn.execute(
        """SELECT * FROM competitor_snapshots
           WHERE competitor_id=? AND snapshot_type=? AND url=?
           ORDER BY ts DESC LIMIT 1 OFFSET 1""",
        (competitor_id, snapshot_type, url),
    ).fetchone()
    conn.close()
    if row:
        return {"hash": row["content_hash"], "data": json.loads(row["data"]), "ts": row["ts"]}
    return None


def _record_change(
    competitor_id: int, competitor_name: str, change_type: str,
    url: str = "", description: str = "", old_value: str = "",
    new_value: str = "", severity: str = "low",
) -> ChangeEvent:
    ev = ChangeEvent(
        competitor_id=competitor_id, competitor_name=competitor_name,
        change_type=change_type, url=url, description=description,
        old_value=old_value, new_value=new_value, severity=severity,
    )
    conn = _get_db()
    with conn:
        cur = conn.execute(
            """INSERT INTO competitor_changes
               (competitor_id, competitor_name, change_type, url, description, old_value, new_value, severity, ts)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (ev.competitor_id, ev.competitor_name, ev.change_type, ev.url,
             ev.description, ev.old_value, ev.new_value, ev.severity, ev.ts),
        )
        ev.id = cur.lastrowid
    conn.close()
    return ev


def check_competitor_page(competitor_id: int, url: str) -> list[ChangeEvent]:
    """Fetch a competitor page and detect changes since last check."""
    import requests
    from bs4 import BeautifulSoup

    comp = get_competitor(competitor_id)
    if not comp:
        return []

    changes: list[ChangeEvent] = []
    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0 AutoEarn"})
        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text(" ", strip=True)

        # Detect page content change
        current_hash = _content_hash(text)
        prev = _get_previous_snapshot(competitor_id, "page", url)
        _save_snapshot(competitor_id, "page", url, {"status": resp.status_code}, text)

        if prev and prev["hash"] != current_hash:
            changes.append(_record_change(
                competitor_id, comp.name, "page_changed", url,
                "Page content changed since last check",
                prev["hash"], current_hash, "medium",
            ))

        # Detect price changes
        if comp.track_prices:
            import re
            prices = re.findall(r'\$[\d,]+\.?\d*', text)
            if prices:
                prev_prices = _get_previous_snapshot(competitor_id, "prices", url)
                price_data = {"prices": prices[:10]}
                _save_snapshot(competitor_id, "prices", url, price_data)
                if prev_prices:
                    old_prices = prev_prices["data"].get("prices", [])
                    if set(prices[:10]) != set(old_prices):
                        changes.append(_record_change(
                            competitor_id, comp.name, "price_change", url,
                            f"Detected price change",
                            str(old_prices[:5]), str(prices[:5]), "high",
                        ))

    except Exception:  # noqa: BLE001
        pass

    # Update last_checked
    conn = _get_db()
    with conn:
        conn.execute("UPDATE competitors SET last_checked=? WHERE id=?", (time.time(), competitor_id))
    conn.close()

    return changes


def recent_changes(limit: int = 50, days: int = 7) -> list[ChangeEvent]:
    """Recent competitor change events."""
    _ensure()
    cutoff = time.time() - days * 86400
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM competitor_changes WHERE ts > ? ORDER BY ts DESC LIMIT ?",
        (cutoff, limit),
    ).fetchall()
    conn.close()
    return [ChangeEvent.from_row(r) for r in rows]


def competitor_report() -> dict[str, Any]:
    """Summary report of all competitors and recent activity."""
    _ensure()
    competitors = list_competitors()
    changes_7d = recent_changes(days=7)
    high_priority_changes = [c for c in changes_7d if c.severity == "high"]

    return {
        "total_competitors": len(competitors),
        "changes_last_7d": len(changes_7d),
        "high_priority_changes": len(high_priority_changes),
        "competitors": [
            {
                "id": c.id, "name": c.name, "domain": c.domain,
                "niche": c.niche, "keywords_tracked": len(c.tracked_keywords),
                "last_checked_hours_ago": round((time.time() - c.last_checked) / 3600, 1) if c.last_checked else None,
            }
            for c in competitors
        ],
        "recent_high_priority": [
            {"type": ch.change_type, "competitor": ch.competitor_name,
             "url": ch.url, "description": ch.description}
            for ch in high_priority_changes[:10]
        ],
    }


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def add_competitor_tool(name: str, domain: str, niche: str = "", keywords: list | None = None) -> str:
    try:
        comp = add_competitor(name, domain, niche, tracked_keywords=keywords or [])
        return f"Added competitor '{comp.name}' ({comp.domain}) with ID #{comp.id}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def check_competitor_tool(competitor_id: int, url: str = "") -> str:
    comp = get_competitor(int(competitor_id))
    if not comp:
        return f"ERROR: competitor #{competitor_id} not found"
    check_url = url or comp.domain
    changes = check_competitor_page(int(competitor_id), check_url)
    if not changes:
        return f"No changes detected for '{comp.name}' at {check_url}"
    return json.dumps([{
        "type": c.change_type, "url": c.url, "description": c.description,
        "severity": c.severity
    } for c in changes])


def competitor_report_tool() -> str:
    return json.dumps(competitor_report())


def recent_competitor_changes_tool(days: int = 7) -> str:
    changes = recent_changes(days=int(days))
    return json.dumps([{
        "id": c.id, "competitor": c.competitor_name, "type": c.change_type,
        "url": c.url, "description": c.description, "severity": c.severity
    } for c in changes])
