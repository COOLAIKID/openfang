"""Keyword rank and performance tracker.

Content agents need to track how published content ranks for target keywords
over time. This module stores keyword→URL rank snapshots, computes trends,
and alerts when rankings drop significantly.

Features:
- Rank snapshots per keyword + URL
- Rank trend (improving/declining/stable)
- Keyword opportunity scoring
- Content-to-keyword mapping
- Alert on ranking drops > N positions
- Bulk rank checking
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"

OPPORTUNITY_TIERS = {
    "featured_snippet": (0, 0),
    "top_3": (1, 3),
    "page_1": (4, 10),
    "page_2": (11, 20),
    "page_3": (21, 30),
    "deep": (31, 100),
    "not_ranking": (101, 9999),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class KeywordRecord:
    keyword: str
    target_url: str = ""
    current_rank: int = 0
    best_rank: int = 0
    niche: str = ""
    search_volume: int = 0
    keyword_difficulty: int = 0
    cpc_usd: float = 0.0
    content_type: str = ""
    notes: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    id: int = 0

    @property
    def tier(self) -> str:
        for name, (low, high) in OPPORTUNITY_TIERS.items():
            if low <= self.current_rank <= high:
                return name
        return "not_ranking"

    @property
    def opportunity_score(self) -> float:
        if self.current_rank == 0:
            return 0.0
        position_score = max(0, (100 - self.current_rank)) / 100
        volume_score = min(1.0, self.search_volume / 10000)
        difficulty_score = max(0, (100 - self.keyword_difficulty)) / 100
        return round((position_score * 0.5 + volume_score * 0.3 + difficulty_score * 0.2) * 100, 1)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "KeywordRecord":
        kw = cls(
            keyword=row["keyword"],
            target_url=row["target_url"] or "",
            current_rank=row["current_rank"] or 0,
            best_rank=row["best_rank"] or 0,
            niche=row["niche"] or "",
            search_volume=row["search_volume"] or 0,
            keyword_difficulty=row["keyword_difficulty"] or 0,
            cpc_usd=row["cpc_usd"] or 0.0,
            content_type=row["content_type"] or "",
            notes=row["notes"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        kw.id = row["id"]
        return kw


@dataclass
class RankSnapshot:
    keyword_id: int
    rank: int
    url: str = ""
    serp_features: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    id: int = 0


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
            CREATE TABLE IF NOT EXISTS keywords (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword             TEXT    NOT NULL UNIQUE,
                target_url          TEXT    NOT NULL DEFAULT '',
                current_rank        INTEGER NOT NULL DEFAULT 0,
                best_rank           INTEGER NOT NULL DEFAULT 0,
                niche               TEXT    NOT NULL DEFAULT '',
                search_volume       INTEGER NOT NULL DEFAULT 0,
                keyword_difficulty  INTEGER NOT NULL DEFAULT 0,
                cpc_usd             REAL    NOT NULL DEFAULT 0,
                content_type        TEXT    NOT NULL DEFAULT '',
                notes               TEXT    NOT NULL DEFAULT '',
                created_at          REAL    NOT NULL,
                updated_at          REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rank_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_id      INTEGER NOT NULL,
                rank            INTEGER NOT NULL,
                url             TEXT    NOT NULL DEFAULT '',
                serp_features   TEXT    NOT NULL DEFAULT '[]',
                ts              REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rank_alerts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_id      INTEGER NOT NULL,
                keyword         TEXT    NOT NULL,
                old_rank        INTEGER NOT NULL,
                new_rank        INTEGER NOT NULL,
                position_drop   INTEGER NOT NULL,
                ts              REAL    NOT NULL,
                acknowledged    INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kw_niche ON keywords(niche)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rs_kw ON rank_snapshots(keyword_id, ts)")
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

def add_keyword(
    keyword: str,
    target_url: str = "",
    niche: str = "",
    search_volume: int = 0,
    keyword_difficulty: int = 0,
    cpc_usd: float = 0.0,
    content_type: str = "",
) -> KeywordRecord:
    _ensure()
    kw = KeywordRecord(
        keyword=keyword.lower().strip(),
        target_url=target_url,
        niche=niche,
        search_volume=search_volume,
        keyword_difficulty=keyword_difficulty,
        cpc_usd=cpc_usd,
        content_type=content_type,
    )
    conn = _get_db()
    with conn:
        try:
            cur = conn.execute(
                """INSERT INTO keywords
                   (keyword, target_url, current_rank, best_rank, niche,
                    search_volume, keyword_difficulty, cpc_usd, content_type,
                    notes, created_at, updated_at)
                   VALUES (?,?,0,0,?,?,?,?,?,?,?,?)""",
                (kw.keyword, kw.target_url, kw.niche, kw.search_volume,
                 kw.keyword_difficulty, kw.cpc_usd, kw.content_type, "",
                 kw.created_at, kw.updated_at),
            )
            kw.id = cur.lastrowid
        except sqlite3.IntegrityError:
            # Keyword already exists — return existing
            row = conn.execute("SELECT * FROM keywords WHERE keyword=?", (kw.keyword,)).fetchone()
            kw = KeywordRecord.from_row(row)
    conn.close()
    return kw


def get_keyword(keyword_id: int) -> Optional[KeywordRecord]:
    _ensure()
    conn = _get_db()
    row = conn.execute("SELECT * FROM keywords WHERE id=?", (keyword_id,)).fetchone()
    conn.close()
    return KeywordRecord.from_row(row) if row else None


def find_keyword(keyword: str) -> Optional[KeywordRecord]:
    _ensure()
    conn = _get_db()
    row = conn.execute("SELECT * FROM keywords WHERE keyword=?", (keyword.lower().strip(),)).fetchone()
    conn.close()
    return KeywordRecord.from_row(row) if row else None


def update_rank(keyword_id: int, rank: int, url: str = "", serp_features: list[str] | None = None) -> str:
    """Record a new rank observation and detect drops."""
    _ensure()
    kw = get_keyword(keyword_id)
    if not kw:
        return f"ERROR: keyword #{keyword_id} not found"

    old_rank = kw.current_rank
    new_best = min(rank, kw.best_rank) if kw.best_rank > 0 else rank

    conn = _get_db()
    with conn:
        conn.execute(
            "UPDATE keywords SET current_rank=?, best_rank=?, updated_at=? WHERE id=?",
            (rank, new_best, time.time(), keyword_id),
        )
        conn.execute(
            "INSERT INTO rank_snapshots (keyword_id, rank, url, serp_features, ts) VALUES (?,?,?,?,?)",
            (keyword_id, rank, url, json.dumps(serp_features or []), time.time()),
        )

        # Detect significant drop (> 5 positions)
        if old_rank > 0 and rank > old_rank + 5:
            conn.execute(
                """INSERT INTO rank_alerts
                   (keyword_id, keyword, old_rank, new_rank, position_drop, ts, acknowledged)
                   VALUES (?,?,?,?,?,?,0)""",
                (keyword_id, kw.keyword, old_rank, rank, rank - old_rank, time.time()),
            )
    conn.close()

    direction = "↓" if rank > old_rank else "↑" if rank < old_rank else "→"
    return f"Rank updated for '{kw.keyword}': {old_rank} → {rank} {direction}"


def rank_history(keyword_id: int, days: int = 30) -> list[dict[str, Any]]:
    _ensure()
    cutoff = time.time() - days * 86400
    conn = _get_db()
    rows = conn.execute(
        "SELECT rank, ts FROM rank_snapshots WHERE keyword_id=? AND ts > ? ORDER BY ts ASC",
        (keyword_id, cutoff),
    ).fetchall()
    conn.close()
    return [{"rank": r["rank"], "ts": r["ts"]} for r in rows]


def rank_trend(keyword_id: int, days: int = 14) -> str:
    """Compute rank trend: improving/declining/stable/insufficient_data."""
    history = rank_history(keyword_id, days)
    if len(history) < 2:
        return "insufficient_data"
    ranks = [h["rank"] for h in history if h["rank"] > 0]
    if len(ranks) < 2:
        return "not_ranking"
    delta = ranks[-1] - ranks[0]
    if delta < -5:
        return "improving"
    if delta > 5:
        return "declining"
    return "stable"


def get_opportunities(niche: str = "", min_volume: int = 100) -> list[KeywordRecord]:
    """Keywords in positions 11-30 (page 2-3) with decent volume — prime for optimization."""
    _ensure()
    conn = _get_db()
    params: list[Any] = [11, 30, min_volume]
    cond = "WHERE current_rank BETWEEN ? AND ? AND search_volume >= ?"
    if niche:
        cond += " AND niche=?"
        params.append(niche)
    rows = conn.execute(
        f"SELECT * FROM keywords {cond} ORDER BY search_volume DESC LIMIT 50",
        params,
    ).fetchall()
    conn.close()
    return [KeywordRecord.from_row(r) for r in rows]


def list_keywords(niche: str = "", limit: int = 100) -> list[KeywordRecord]:
    _ensure()
    conn = _get_db()
    if niche:
        rows = conn.execute(
            "SELECT * FROM keywords WHERE niche=? ORDER BY current_rank ASC LIMIT ?",
            (niche, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM keywords ORDER BY current_rank ASC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [KeywordRecord.from_row(r) for r in rows]


def rank_alerts(unacknowledged_only: bool = True) -> list[dict[str, Any]]:
    _ensure()
    conn = _get_db()
    cond = "WHERE acknowledged=0" if unacknowledged_only else ""
    rows = conn.execute(
        f"SELECT * FROM rank_alerts {cond} ORDER BY ts DESC LIMIT 50"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def acknowledge_alerts() -> str:
    _ensure()
    conn = _get_db()
    with conn:
        r = conn.execute("UPDATE rank_alerts SET acknowledged=1 WHERE acknowledged=0")
    conn.close()
    return f"Acknowledged {r.rowcount} rank alerts"


def keyword_report() -> dict[str, Any]:
    _ensure()
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM keywords").fetchone()[0]
    by_tier = {}
    for tier, (low, high) in OPPORTUNITY_TIERS.items():
        cnt = conn.execute(
            "SELECT COUNT(*) FROM keywords WHERE current_rank BETWEEN ? AND ?",
            (low, high),
        ).fetchone()[0]
        by_tier[tier] = cnt
    top_10 = conn.execute(
        "SELECT keyword, current_rank, search_volume FROM keywords WHERE current_rank > 0 AND current_rank <= 10 ORDER BY current_rank ASC LIMIT 10"
    ).fetchall()
    alert_count = conn.execute("SELECT COUNT(*) FROM rank_alerts WHERE acknowledged=0").fetchone()[0]
    conn.close()
    return {
        "total_keywords": total,
        "by_tier": by_tier,
        "top_10": [dict(r) for r in top_10],
        "unread_alerts": alert_count,
    }


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def add_keyword_tool(keyword: str, target_url: str = "", niche: str = "",
                     search_volume: int = 0, keyword_difficulty: int = 0) -> str:
    try:
        kw = add_keyword(keyword, target_url, niche, int(search_volume), int(keyword_difficulty))
        return f"Added keyword '{kw.keyword}' (ID #{kw.id}, volume: {kw.search_volume}, KD: {kw.keyword_difficulty})"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def update_rank_tool(keyword: str, rank: int, url: str = "") -> str:
    kw = find_keyword(keyword)
    if not kw:
        kw = add_keyword(keyword)
    return update_rank(kw.id, int(rank), url)


def keyword_opportunities_tool(niche: str = "", min_volume: int = 100) -> str:
    opps = get_opportunities(niche, int(min_volume))
    return json.dumps([
        {"id": k.id, "keyword": k.keyword, "rank": k.current_rank,
         "volume": k.search_volume, "tier": k.tier, "score": k.opportunity_score}
        for k in opps
    ])


def keyword_report_tool() -> str:
    return json.dumps(keyword_report())


def rank_alerts_tool() -> str:
    return json.dumps(rank_alerts())
