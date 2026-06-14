"""
core/memory.py — Per-agent semantic memory backed by SQLite FTS5.

Each agent has its own namespace. Supports full-text search via FTS5 MATCH
with TF-IDF-style scoring, exact key lookup, tag filtering, and LLM context
injection.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    """Return a thread-safe WAL-mode connection, creating schema on first use."""
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
    """Create memory tables and FTS5 virtual table if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            agent       TEXT    NOT NULL,
            key         TEXT    NOT NULL,
            content     TEXT    NOT NULL DEFAULT '',
            tags        TEXT    NOT NULL DEFAULT '[]',
            embedding   BLOB,
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL,
            UNIQUE (agent, key)
        );

        CREATE INDEX IF NOT EXISTS idx_memories_agent ON memories (agent);
        CREATE INDEX IF NOT EXISTS idx_memories_agent_key ON memories (agent, key);

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
        USING fts5(
            agent,
            key,
            content,
            tags,
            content='memories',
            content_rowid='id',
            tokenize='porter ascii'
        );

        CREATE TRIGGER IF NOT EXISTS memories_ai
        AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts (rowid, agent, key, content, tags)
            VALUES (new.id, new.agent, new.key, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au
        AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts (memories_fts, rowid, agent, key, content, tags)
            VALUES ('delete', old.id, old.agent, old.key, old.content, old.tags);
            INSERT INTO memories_fts (rowid, agent, key, content, tags)
            VALUES (new.id, new.agent, new.key, new.content, new.tags);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad
        AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts (memories_fts, rowid, agent, key, content, tags)
            VALUES ('delete', old.id, old.agent, old.key, old.content, old.tags);
        END;
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_fts_query(query: str) -> str:
    """Escape FTS5 special characters in a user query string."""
    # Replace double-quotes with escaped version; wrap each word
    words = query.replace('"', '""').split()
    if not words:
        return '""'
    return " OR ".join(f'"{w}"' for w in words)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save(agent: str, key: str, content: str, tags: list[str] | None = None) -> int:
    """
    Save a memory for *agent* under *key*.

    If a record with the same (agent, key) already exists it is updated in
    place (upsert). Tags are stored as a JSON array.

    Returns the row id.
    """
    if tags is None:
        tags = []
    db = _get_db()
    tags_json = json.dumps(tags)
    ts = _now()
    with _lock:
        cursor = db.execute(
            """
            INSERT INTO memories (agent, key, content, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent, key) DO UPDATE SET
                content    = excluded.content,
                tags       = excluded.tags,
                updated_at = excluded.updated_at
            """,
            (agent, key, content, tags_json, ts, ts),
        )
        db.commit()
    logger.debug("memory.save agent=%s key=%s rowid=%s", agent, key, cursor.lastrowid)
    return cursor.lastrowid or 0


def recall(agent: str, query: str, limit: int = 10) -> list[dict[str, Any]]:
    """
    Full-text search over an agent's memories.

    Uses FTS5 MATCH with bm25() ranking (lower is better → we negate).
    Returns a list of dicts ordered by relevance descending.
    """
    db = _get_db()
    safe_query = _sanitize_fts_query(query)
    try:
        rows = db.execute(
            """
            SELECT m.id, m.agent, m.key, m.content, m.tags,
                   m.created_at, m.updated_at,
                   -bm25(memories_fts) AS score
            FROM memories_fts f
            JOIN memories m ON m.id = f.rowid
            WHERE memories_fts MATCH ?
              AND f.agent = ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (safe_query, agent, limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        logger.warning("memory.recall FTS error: %s", exc)
        return []
    results = []
    for row in rows:
        results.append({
            "id": row["id"],
            "agent": row["agent"],
            "key": row["key"],
            "content": row["content"],
            "tags": json.loads(row["tags"] or "[]"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "score": row["score"],
        })
    return results


def get(agent: str, key: str) -> dict[str, Any] | None:
    """
    Exact lookup by (agent, key).

    Returns a dict or None if not found.
    """
    db = _get_db()
    row = db.execute(
        "SELECT * FROM memories WHERE agent = ? AND key = ?",
        (agent, key),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "agent": row["agent"],
        "key": row["key"],
        "content": row["content"],
        "tags": json.loads(row["tags"] or "[]"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def delete(agent: str, key: str) -> bool:
    """
    Delete a memory by (agent, key).

    Returns True if a row was deleted.
    """
    db = _get_db()
    with _lock:
        cursor = db.execute(
            "DELETE FROM memories WHERE agent = ? AND key = ?",
            (agent, key),
        )
        db.commit()
    deleted = cursor.rowcount > 0
    logger.debug("memory.delete agent=%s key=%s deleted=%s", agent, key, deleted)
    return deleted


def list_keys(agent: str, tag: str | None = None) -> list[str]:
    """
    Return all memory keys for *agent*.

    If *tag* is provided, only return keys whose tag list contains *tag*.
    """
    db = _get_db()
    if tag is None:
        rows = db.execute(
            "SELECT key FROM memories WHERE agent = ? ORDER BY updated_at DESC",
            (agent,),
        ).fetchall()
        return [r["key"] for r in rows]
    # SQLite JSON function: json_each to filter by tag
    rows = db.execute(
        """
        SELECT DISTINCT m.key
        FROM memories m, json_each(m.tags) t
        WHERE m.agent = ? AND t.value = ?
        ORDER BY m.updated_at DESC
        """,
        (agent, tag),
    ).fetchall()
    return [r["key"] for r in rows]


def wipe(agent: str) -> int:
    """
    Delete all memories for *agent*.

    Returns the number of rows deleted.
    """
    db = _get_db()
    with _lock:
        cursor = db.execute("DELETE FROM memories WHERE agent = ?", (agent,))
        db.commit()
    logger.info("memory.wipe agent=%s rows_deleted=%d", agent, cursor.rowcount)
    return cursor.rowcount


def summarize(agent: str) -> dict[str, Any]:
    """
    Return a summary dict for an agent's memory namespace.

    Includes total count, tag breakdown, and the 5 most recently updated
    memories (key + first 100 chars of content).
    """
    db = _get_db()
    count_row = db.execute(
        "SELECT COUNT(*) AS cnt FROM memories WHERE agent = ?",
        (agent,),
    ).fetchone()
    count = count_row["cnt"] if count_row else 0

    recent_rows = db.execute(
        """
        SELECT key, content, tags, updated_at
        FROM memories
        WHERE agent = ?
        ORDER BY updated_at DESC
        LIMIT 5
        """,
        (agent,),
    ).fetchall()

    recent = [
        {
            "key": r["key"],
            "preview": r["content"][:100],
            "tags": json.loads(r["tags"] or "[]"),
            "updated_at": r["updated_at"],
        }
        for r in recent_rows
    ]

    # Tag frequency
    tag_rows = db.execute(
        """
        SELECT t.value AS tag, COUNT(*) AS cnt
        FROM memories m, json_each(m.tags) t
        WHERE m.agent = ?
        GROUP BY t.value
        ORDER BY cnt DESC
        """,
        (agent,),
    ).fetchall()
    tag_freq = {r["tag"]: r["cnt"] for r in tag_rows}

    return {
        "agent": agent,
        "total_memories": count,
        "tag_frequency": tag_freq,
        "recent": recent,
    }


def inject_context(agent: str, query: str, max_chars: int = 2000) -> str:
    """
    Build a formatted memory-context block suitable for injection into an LLM prompt.

    Performs a recall() search for *query*, then formats the top results into
    a human-readable block, truncating to *max_chars*.

    Returns an empty string if no relevant memories exist.
    """
    hits = recall(agent, query, limit=15)
    if not hits:
        return ""

    lines: list[str] = ["## Relevant memories\n"]
    chars_used = len(lines[0])

    for hit in hits:
        entry = f"- [{hit['key']}] {hit['content'].strip()}\n"
        if chars_used + len(entry) > max_chars:
            break
        lines.append(entry)
        chars_used += len(entry)

    if len(lines) == 1:
        # Nothing fit
        return ""

    return "".join(lines)


# ---------------------------------------------------------------------------
# Maintenance helpers
# ---------------------------------------------------------------------------


def rebuild_fts() -> None:
    """Rebuild the FTS5 index from scratch (use after bulk imports)."""
    db = _get_db()
    with _lock:
        db.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        db.commit()
    logger.info("memory.rebuild_fts complete")


def optimize_fts() -> None:
    """Run FTS5 merge optimization to compact the index."""
    db = _get_db()
    with _lock:
        db.execute("INSERT INTO memories_fts(memories_fts) VALUES('optimize')")
        db.commit()
    logger.info("memory.optimize_fts complete")


def memory_stats() -> dict[str, Any]:
    """Return DB-wide memory statistics."""
    db = _get_db()
    total = db.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
    agents_row = db.execute(
        "SELECT COUNT(DISTINCT agent) AS c FROM memories"
    ).fetchone()["c"]
    oldest = db.execute(
        "SELECT MIN(created_at) AS t FROM memories"
    ).fetchone()["t"]
    newest = db.execute(
        "SELECT MAX(updated_at) AS t FROM memories"
    ).fetchone()["t"]
    return {
        "total_memories": total,
        "unique_agents": agents_row,
        "oldest_memory": oldest,
        "newest_memory": newest,
    }


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    save("test_agent", "hello", "Hello, world! This is a test memory.", tags=["test"])
    save("test_agent", "revenue", "We made $500 today from affiliate sales.", tags=["finance"])
    save("test_agent", "plan", "The plan is to focus on SEO content.", tags=["strategy"])

    print("recall('test_agent', 'affiliate sales'):")
    for r in recall("test_agent", "affiliate sales"):
        print(" ", r["key"], "→", r["content"][:60])

    print("\nget:", get("test_agent", "hello"))
    print("keys:", list_keys("test_agent"))
    print("keys[finance]:", list_keys("test_agent", tag="finance"))
    print("summarize:", summarize("test_agent"))
    print("inject_context:", inject_context("test_agent", "SEO"))
    delete("test_agent", "hello")
    print("after delete keys:", list_keys("test_agent"))
    wipe("test_agent")
    print("after wipe keys:", list_keys("test_agent"))
    print("stats:", memory_stats())
