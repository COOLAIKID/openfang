"""Content production pipeline manager.

Tracks every piece of content from idea → research → draft → edit → QC →
publish. Provides a Kanban-style board backed by SQLite, hooks into the
message bus so agents automatically receive work when a piece moves to their
stage, and records publishing history so the CFO can see content ROI.

Lifecycle::

    idea → research → writing → editing → qc_review → approved → published

Agents update the stage of a piece using :func:`move_piece`. The pipeline
emits a message via :mod:`message_bus` to the target agent on each move.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"

STAGES = [
    "idea",
    "research",
    "writing",
    "editing",
    "qc_review",
    "approved",
    "published",
    "rejected",
    "archived",
]

STAGE_OWNERS = {
    "idea": "researcher",
    "research": "writer",
    "writing": "editor",
    "editing": "content_qc",
    "qc_review": "content_qc",
    "approved": "content_qc",
    "published": None,
    "rejected": "editor",
    "archived": None,
}

CONTENT_TYPES = [
    "seo_article", "blog_post", "product_review", "affiliate_roundup",
    "youtube_script", "email_newsletter", "social_post", "ebook_chapter",
    "landing_page", "press_release", "case_study", "podcast_outline",
    "twitter_thread", "linkedin_post", "ad_copy", "email_sequence",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ContentPiece:
    title: str
    content_type: str = "blog_post"
    niche: str = ""
    target_keyword: str = ""
    stage: str = "idea"
    assigned_to: str = "researcher"
    priority: int = 5  # 1=highest
    estimated_words: int = 1500
    actual_words: int = 0
    content_body: str = ""
    output_path: str = ""
    publish_url: str = ""
    seo_score: float = 0.0
    quality_score: float = 0.0
    rejection_count: int = 0
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_by: str = "system"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    published_at: float = 0.0
    id: int = 0

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ContentPiece":
        piece = cls(
            title=row["title"],
            content_type=row["content_type"] or "blog_post",
            niche=row["niche"] or "",
            target_keyword=row["target_keyword"] or "",
            stage=row["stage"],
            assigned_to=row["assigned_to"] or "researcher",
            priority=row["priority"] or 5,
            estimated_words=row["estimated_words"] or 1500,
            actual_words=row["actual_words"] or 0,
            content_body=row["content_body"] or "",
            output_path=row["output_path"] or "",
            publish_url=row["publish_url"] or "",
            seo_score=row["seo_score"] or 0.0,
            quality_score=row["quality_score"] or 0.0,
            rejection_count=row["rejection_count"] or 0,
            tags=json.loads(row["tags"] or "[]"),
            notes=row["notes"] or "",
            created_by=row["created_by"] or "system",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            published_at=row["published_at"] or 0.0,
        )
        piece.id = row["id"]
        return piece


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
            CREATE TABLE IF NOT EXISTS content_pieces (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT    NOT NULL,
                content_type    TEXT    NOT NULL DEFAULT 'blog_post',
                niche           TEXT    NOT NULL DEFAULT '',
                target_keyword  TEXT    NOT NULL DEFAULT '',
                stage           TEXT    NOT NULL DEFAULT 'idea',
                assigned_to     TEXT    NOT NULL DEFAULT 'researcher',
                priority        INTEGER NOT NULL DEFAULT 5,
                estimated_words INTEGER NOT NULL DEFAULT 1500,
                actual_words    INTEGER NOT NULL DEFAULT 0,
                content_body    TEXT    NOT NULL DEFAULT '',
                output_path     TEXT    NOT NULL DEFAULT '',
                publish_url     TEXT    NOT NULL DEFAULT '',
                seo_score       REAL    NOT NULL DEFAULT 0,
                quality_score   REAL    NOT NULL DEFAULT 0,
                rejection_count INTEGER NOT NULL DEFAULT 0,
                tags            TEXT    NOT NULL DEFAULT '[]',
                notes           TEXT    NOT NULL DEFAULT '',
                created_by      TEXT    NOT NULL DEFAULT 'system',
                created_at      REAL    NOT NULL,
                updated_at      REAL    NOT NULL,
                published_at    REAL    NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS content_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                piece_id    INTEGER NOT NULL,
                from_stage  TEXT    NOT NULL,
                to_stage    TEXT    NOT NULL,
                agent       TEXT    NOT NULL,
                note        TEXT    NOT NULL DEFAULT '',
                ts          REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_stage ON content_pieces(stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cp_type ON content_pieces(content_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ch_piece ON content_history(piece_id)")
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

def create_piece(
    title: str,
    content_type: str = "blog_post",
    niche: str = "",
    target_keyword: str = "",
    estimated_words: int = 1500,
    priority: int = 5,
    tags: list[str] | None = None,
    created_by: str = "system",
) -> ContentPiece:
    _ensure()
    piece = ContentPiece(
        title=title, content_type=content_type, niche=niche,
        target_keyword=target_keyword, estimated_words=estimated_words,
        priority=priority, tags=tags or [], created_by=created_by,
    )
    conn = _get_db()
    with conn:
        cur = conn.execute(
            """INSERT INTO content_pieces
               (title, content_type, niche, target_keyword, stage, assigned_to,
                priority, estimated_words, actual_words, content_body, output_path,
                publish_url, seo_score, quality_score, rejection_count, tags, notes,
                created_by, created_at, updated_at, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (piece.title, piece.content_type, piece.niche, piece.target_keyword,
             piece.stage, piece.assigned_to, piece.priority, piece.estimated_words,
             0, "", "", "", 0.0, 0.0, 0, json.dumps(piece.tags), "",
             piece.created_by, piece.created_at, piece.updated_at, 0.0),
        )
        piece.id = cur.lastrowid
    conn.close()
    return piece


def get_piece(piece_id: int) -> Optional[ContentPiece]:
    _ensure()
    conn = _get_db()
    row = conn.execute("SELECT * FROM content_pieces WHERE id=?", (piece_id,)).fetchone()
    conn.close()
    return ContentPiece.from_row(row) if row else None


def move_piece(
    piece_id: int, new_stage: str, agent: str = "system",
    note: str = "", seo_score: float | None = None, quality_score: float | None = None,
) -> str:
    """Move a content piece to a new stage and notify the target agent."""
    if new_stage not in STAGES:
        return f"ERROR: invalid stage '{new_stage}'. Valid: {', '.join(STAGES)}"
    _ensure()
    piece = get_piece(piece_id)
    if not piece:
        return f"ERROR: piece #{piece_id} not found"

    old_stage = piece.stage
    new_assigned = STAGE_OWNERS.get(new_stage, agent)
    updates: dict[str, Any] = {
        "stage": new_stage,
        "assigned_to": new_assigned or agent,
        "updated_at": time.time(),
    }
    if new_stage == "rejected":
        updates["rejection_count"] = piece.rejection_count + 1
    if new_stage == "published":
        updates["published_at"] = time.time()
    if seo_score is not None:
        updates["seo_score"] = float(seo_score)
    if quality_score is not None:
        updates["quality_score"] = float(quality_score)

    cols = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [piece_id]
    conn = _get_db()
    with conn:
        conn.execute(f"UPDATE content_pieces SET {cols} WHERE id=?", vals)
        conn.execute(
            "INSERT INTO content_history (piece_id, from_stage, to_stage, agent, note, ts) VALUES (?,?,?,?,?,?)",
            (piece_id, old_stage, new_stage, agent, note, time.time()),
        )
    conn.close()

    # Notify target agent via message bus
    if new_assigned:
        try:
            from . import message_bus
            message_bus.send(
                from_agent=agent,
                to_agent=new_assigned,
                msg_type="work_item",
                subject=f"Content piece #{piece_id}: {piece.title}",
                body=f"Stage: {old_stage} → {new_stage}\nKeyword: {piece.target_keyword}\n{note}",
            )
        except Exception:  # noqa: BLE001
            pass

    return f"Piece #{piece_id} moved: {old_stage} → {new_stage} (assigned to {new_assigned})"


def update_piece_content(
    piece_id: int, content_body: str, output_path: str = "", agent: str = "system"
) -> str:
    _ensure()
    words = len(content_body.split())
    conn = _get_db()
    with conn:
        conn.execute(
            "UPDATE content_pieces SET content_body=?, actual_words=?, output_path=?, updated_at=? WHERE id=?",
            (content_body, words, output_path, time.time(), piece_id),
        )
    conn.close()
    return f"Updated content for piece #{piece_id} ({words} words)"


def publish_piece(piece_id: int, url: str, agent: str = "content_qc") -> str:
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute(
            "UPDATE content_pieces SET publish_url=?, stage='published', published_at=?, updated_at=? WHERE id=?",
            (url, time.time(), time.time(), piece_id),
        )
        conn.execute(
            "INSERT INTO content_history (piece_id, from_stage, to_stage, agent, note, ts) VALUES (?,?,?,?,?,?)",
            (piece_id, "approved", "published", agent, f"Published at {url}", time.time()),
        )
    conn.close()
    return f"Piece #{piece_id} published at {url}"


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_stage_queue(stage: str) -> list[ContentPiece]:
    """All pieces currently in a given stage, ordered by priority."""
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM content_pieces WHERE stage=? ORDER BY priority ASC, created_at ASC",
        (stage,),
    ).fetchall()
    conn.close()
    return [ContentPiece.from_row(r) for r in rows]


def get_my_queue(agent: str) -> list[ContentPiece]:
    """All pieces assigned to a specific agent."""
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM content_pieces WHERE assigned_to=? AND stage NOT IN ('published','archived') ORDER BY priority ASC",
        (agent,),
    ).fetchall()
    conn.close()
    return [ContentPiece.from_row(r) for r in rows]


def get_history(piece_id: int) -> list[dict[str, Any]]:
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM content_history WHERE piece_id=? ORDER BY ts ASC",
        (piece_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def pipeline_stats() -> dict[str, Any]:
    """Full pipeline statistics for the dashboard."""
    _ensure()
    conn = _get_db()
    stage_counts = {r["stage"]: r["cnt"] for r in conn.execute(
        "SELECT stage, COUNT(*) as cnt FROM content_pieces GROUP BY stage"
    ).fetchall()}
    published = conn.execute(
        "SELECT COUNT(*) as cnt, AVG(actual_words) as avg_words, AVG(seo_score) as avg_seo "
        "FROM content_pieces WHERE stage='published'"
    ).fetchone()
    type_counts = {r["content_type"]: r["cnt"] for r in conn.execute(
        "SELECT content_type, COUNT(*) as cnt FROM content_pieces GROUP BY content_type ORDER BY cnt DESC"
    ).fetchall()}
    avg_cycle_seconds = conn.execute(
        """SELECT AVG(published_at - created_at) as avg_cycle
           FROM content_pieces WHERE stage='published' AND published_at > 0"""
    ).fetchone()
    conn.close()

    avg_cycle_hours = 0.0
    if avg_cycle_seconds and avg_cycle_seconds["avg_cycle"]:
        avg_cycle_hours = round(avg_cycle_seconds["avg_cycle"] / 3600, 1)

    return {
        "by_stage": stage_counts,
        "total": sum(stage_counts.values()),
        "published_count": published["cnt"] or 0,
        "avg_published_words": round(published["avg_words"] or 0),
        "avg_seo_score": round(published["avg_seo"] or 0, 2),
        "avg_cycle_hours": avg_cycle_hours,
        "by_type": type_counts,
    }


def content_velocity(days: int = 7) -> dict[str, Any]:
    """How many pieces were published in the last N days."""
    _ensure()
    cutoff = time.time() - days * 86400
    conn = _get_db()
    published = conn.execute(
        "SELECT COUNT(*) as cnt FROM content_pieces WHERE stage='published' AND published_at > ?",
        (cutoff,),
    ).fetchone()
    created = conn.execute(
        "SELECT COUNT(*) as cnt FROM content_pieces WHERE created_at > ?",
        (cutoff,),
    ).fetchone()
    conn.close()
    return {
        "days": days,
        "published": published["cnt"] or 0,
        "created": created["cnt"] or 0,
        "daily_velocity": round((published["cnt"] or 0) / days, 2),
    }


def stale_pieces(stale_hours: int = 24) -> list[ContentPiece]:
    """Pieces stuck in a non-terminal stage for too long."""
    cutoff = time.time() - stale_hours * 3600
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        """SELECT * FROM content_pieces
           WHERE stage NOT IN ('published', 'archived')
           AND updated_at < ?
           ORDER BY updated_at ASC""",
        (cutoff,),
    ).fetchall()
    conn.close()
    return [ContentPiece.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Tool-friendly wrappers
# ---------------------------------------------------------------------------

def create_content_piece(
    title: str, content_type: str = "blog_post", niche: str = "",
    target_keyword: str = "", estimated_words: int = 1500,
) -> str:
    try:
        piece = create_piece(title, content_type, niche, target_keyword, int(estimated_words))
        return f"Created content piece #{piece.id}: '{piece.title}' ({piece.content_type}, keyword: {piece.target_keyword})"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def advance_content(piece_id: int, stage: str, note: str = "", agent: str = "system") -> str:
    return move_piece(int(piece_id), stage, agent, note)


def my_content_queue(agent: str) -> str:
    pieces = get_my_queue(agent)
    return json.dumps([
        {"id": p.id, "title": p.title, "stage": p.stage, "keyword": p.target_keyword,
         "priority": p.priority, "type": p.content_type}
        for p in pieces
    ])


def content_pipeline_report() -> str:
    return json.dumps(pipeline_stats())


def stale_content_report() -> str:
    pieces = stale_pieces()
    return json.dumps([
        {"id": p.id, "title": p.title, "stage": p.stage, "assigned_to": p.assigned_to,
         "hours_stale": round((time.time() - p.updated_at) / 3600, 1)}
        for p in pieces
    ])
