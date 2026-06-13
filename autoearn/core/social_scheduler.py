"""Social media post scheduler and queue manager.

Agents generate content that needs to be published at optimal times across
multiple platforms. This module provides a scheduling queue backed by SQLite
so posts don't overlap, platform rate limits are respected, and the best
engagement windows are targeted.

Features:
- :class:`ScheduledPost` — a queued social post with platform, content, time
- :func:`schedule_post` — add a post to the queue
- :func:`get_due_posts` — fetch posts ready to publish right now
- :func:`mark_published` — record that a post went live
- :func:`optimal_slot` — compute next available slot for a platform
- Platform-specific rate limits (posts per day/hour)
- Analytics: engagement tracking, best-time learning
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"

PLATFORMS = [
    "twitter", "linkedin", "instagram", "facebook", "tiktok",
    "reddit", "telegram", "discord", "mastodon", "pinterest",
    "youtube_community", "threads",
]

# Max posts per platform per day (conservative defaults)
DAILY_LIMITS: dict[str, int] = {
    "twitter": 15,
    "linkedin": 3,
    "instagram": 2,
    "facebook": 3,
    "tiktok": 4,
    "reddit": 5,
    "telegram": 20,
    "discord": 20,
    "mastodon": 10,
    "pinterest": 20,
    "youtube_community": 2,
    "threads": 5,
}

# Optimal posting hours (UTC) by platform
OPTIMAL_HOURS: dict[str, list[int]] = {
    "twitter": [8, 12, 17, 20],
    "linkedin": [8, 12, 17],
    "instagram": [9, 12, 18, 21],
    "facebook": [9, 13, 16],
    "tiktok": [7, 12, 19, 21],
    "reddit": [9, 12, 17],
    "telegram": [9, 18],
    "discord": [14, 19],
    "mastodon": [9, 13, 18],
    "pinterest": [8, 14, 20],
    "youtube_community": [15, 19],
    "threads": [9, 12, 18, 21],
}

POST_STATUSES = ["pending", "scheduled", "published", "failed", "cancelled"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ScheduledPost:
    platform: str
    content: str
    content_type: str = "text"  # text | image | video | link | carousel
    media_paths: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    scheduled_for: float = 0.0  # Unix timestamp
    status: str = "pending"
    created_by: str = "system"
    connector: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    publish_url: str = ""
    error_message: str = ""
    created_at: float = field(default_factory=time.time)
    published_at: float = 0.0
    id: int = 0

    @property
    def full_content(self) -> str:
        base = self.content
        if self.hashtags:
            base += "\n\n" + " ".join(f"#{h.lstrip('#')}" for h in self.hashtags)
        return base

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ScheduledPost":
        p = cls(
            platform=row["platform"],
            content=row["content"],
            content_type=row["content_type"] or "text",
            media_paths=json.loads(row["media_paths"] or "[]"),
            hashtags=json.loads(row["hashtags"] or "[]"),
            scheduled_for=row["scheduled_for"] or 0.0,
            status=row["status"],
            created_by=row["created_by"] or "system",
            connector=row["connector"] or "",
            metadata=json.loads(row["metadata"] or "{}"),
            publish_url=row["publish_url"] or "",
            error_message=row["error_message"] or "",
            created_at=row["created_at"],
            published_at=row["published_at"] or 0.0,
        )
        p.id = row["id"]
        return p


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
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                platform        TEXT    NOT NULL,
                content         TEXT    NOT NULL,
                content_type    TEXT    NOT NULL DEFAULT 'text',
                media_paths     TEXT    NOT NULL DEFAULT '[]',
                hashtags        TEXT    NOT NULL DEFAULT '[]',
                scheduled_for   REAL    NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'pending',
                created_by      TEXT    NOT NULL DEFAULT 'system',
                connector       TEXT    NOT NULL DEFAULT '',
                metadata        TEXT    NOT NULL DEFAULT '{}',
                publish_url     TEXT    NOT NULL DEFAULT '',
                error_message   TEXT    NOT NULL DEFAULT '',
                created_at      REAL    NOT NULL,
                published_at    REAL    NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS post_engagement (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id     INTEGER NOT NULL,
                platform    TEXT    NOT NULL,
                likes       INTEGER NOT NULL DEFAULT 0,
                comments    INTEGER NOT NULL DEFAULT 0,
                shares      INTEGER NOT NULL DEFAULT 0,
                clicks      INTEGER NOT NULL DEFAULT 0,
                impressions INTEGER NOT NULL DEFAULT 0,
                checked_at  REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_status ON scheduled_posts(status, scheduled_for)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sp_platform ON scheduled_posts(platform)")
    conn.close()


_schema_ready = False


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


# ---------------------------------------------------------------------------
# Scheduling logic
# ---------------------------------------------------------------------------

def _posts_today(platform: str) -> int:
    """Count posts published today on this platform."""
    start_of_day = time.time() - (time.time() % 86400)
    conn = _get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM scheduled_posts WHERE platform=? AND status='published' AND published_at > ?",
        (platform, start_of_day),
    ).fetchone()[0]
    conn.close()
    return count


def _next_optimal_slot(platform: str, after: float | None = None) -> float:
    """Find next available optimal posting slot for a platform."""
    after = after or time.time()
    optimal = OPTIMAL_HOURS.get(platform, [9, 17])
    daily_limit = DAILY_LIMITS.get(platform, 5)

    if _posts_today(platform) >= daily_limit:
        # Push to tomorrow
        tomorrow = after + 86400
        dt = datetime.fromtimestamp(tomorrow, tz=timezone.utc)
        slot = dt.replace(hour=optimal[0], minute=0, second=0, microsecond=0)
        return slot.timestamp()

    # Find next optimal hour today or tomorrow
    dt_now = datetime.fromtimestamp(after, tz=timezone.utc)
    for hour in optimal:
        if dt_now.hour < hour:
            slot = dt_now.replace(hour=hour, minute=0, second=0, microsecond=0)
            return slot.timestamp()

    # All optimal hours passed today — use first slot tomorrow
    tomorrow = dt_now.timestamp() + 86400 - (dt_now.hour * 3600 + dt_now.minute * 60 + dt_now.second)
    dt_tomorrow = datetime.fromtimestamp(tomorrow, tz=timezone.utc)
    slot = dt_tomorrow.replace(hour=optimal[0], minute=0, second=0, microsecond=0)
    return slot.timestamp()


def schedule_post(
    platform: str,
    content: str,
    content_type: str = "text",
    hashtags: list[str] | None = None,
    media_paths: list[str] | None = None,
    scheduled_for: float | None = None,
    created_by: str = "system",
    connector: str = "",
    metadata: dict | None = None,
) -> ScheduledPost:
    """Schedule a post for publication."""
    _ensure()
    if platform not in PLATFORMS:
        raise ValueError(f"Unknown platform '{platform}'. Valid: {', '.join(PLATFORMS)}")

    slot = scheduled_for or _next_optimal_slot(platform)

    post = ScheduledPost(
        platform=platform,
        content=content,
        content_type=content_type,
        hashtags=hashtags or [],
        media_paths=media_paths or [],
        scheduled_for=slot,
        created_by=created_by,
        connector=connector,
        metadata=metadata or {},
    )

    conn = _get_db()
    with conn:
        cur = conn.execute(
            """INSERT INTO scheduled_posts
               (platform, content, content_type, media_paths, hashtags,
                scheduled_for, status, created_by, connector, metadata,
                publish_url, error_message, created_at, published_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (post.platform, post.content, post.content_type,
             json.dumps(post.media_paths), json.dumps(post.hashtags),
             post.scheduled_for, "scheduled", post.created_by,
             post.connector, json.dumps(post.metadata),
             "", "", post.created_at, 0.0),
        )
        post.id = cur.lastrowid
    conn.close()
    return post


def get_due_posts(platform: str | None = None) -> list[ScheduledPost]:
    """Fetch posts that are due to be published now."""
    _ensure()
    now = time.time()
    conn = _get_db()
    if platform:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status='scheduled' AND scheduled_for <= ? AND platform=? ORDER BY scheduled_for ASC",
            (now, platform),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status='scheduled' AND scheduled_for <= ? ORDER BY scheduled_for ASC",
            (now,),
        ).fetchall()
    conn.close()
    return [ScheduledPost.from_row(r) for r in rows]


def mark_published(post_id: int, url: str = "") -> str:
    """Mark a post as published."""
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute(
            "UPDATE scheduled_posts SET status='published', publish_url=?, published_at=? WHERE id=?",
            (url, time.time(), post_id),
        )
    conn.close()
    return f"Post #{post_id} marked as published"


def mark_failed(post_id: int, error: str = "") -> str:
    """Mark a post as failed."""
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute(
            "UPDATE scheduled_posts SET status='failed', error_message=? WHERE id=?",
            (error[:500], post_id),
        )
    conn.close()
    return f"Post #{post_id} marked as failed: {error[:100]}"


def cancel_post(post_id: int) -> str:
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute("UPDATE scheduled_posts SET status='cancelled' WHERE id=?", (post_id,))
    conn.close()
    return f"Post #{post_id} cancelled"


def get_queue(platform: str | None = None, status: str = "scheduled", limit: int = 50) -> list[ScheduledPost]:
    _ensure()
    conn = _get_db()
    if platform:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status=? AND platform=? ORDER BY scheduled_for ASC LIMIT ?",
            (status, platform, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts WHERE status=? ORDER BY scheduled_for ASC LIMIT ?",
            (status, limit),
        ).fetchall()
    conn.close()
    return [ScheduledPost.from_row(r) for r in rows]


def record_engagement(
    post_id: int, platform: str, likes: int = 0, comments: int = 0,
    shares: int = 0, clicks: int = 0, impressions: int = 0,
) -> None:
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute(
            """INSERT INTO post_engagement
               (post_id, platform, likes, comments, shares, clicks, impressions, checked_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (post_id, platform, likes, comments, shares, clicks, impressions, time.time()),
        )
    conn.close()


def schedule_stats(days: int = 7) -> dict[str, Any]:
    """Publishing stats for the past N days."""
    _ensure()
    cutoff = time.time() - days * 86400
    conn = _get_db()
    by_platform = conn.execute(
        """SELECT platform, status, COUNT(*) as cnt
           FROM scheduled_posts WHERE created_at > ?
           GROUP BY platform, status""",
        (cutoff,),
    ).fetchall()
    pending = conn.execute(
        "SELECT COUNT(*) FROM scheduled_posts WHERE status='scheduled'",
    ).fetchone()[0]
    conn.close()

    breakdown: dict[str, dict[str, int]] = {}
    for r in by_platform:
        plat = r["platform"]
        if plat not in breakdown:
            breakdown[plat] = {}
        breakdown[plat][r["status"]] = r["cnt"]

    return {
        "days": days,
        "pending_posts": pending,
        "by_platform": breakdown,
        "total_published": sum(
            v.get("published", 0) for v in breakdown.values()
        ),
    }


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def schedule_post_tool(
    platform: str, content: str, hashtags: list | None = None,
    scheduled_for: float | None = None, created_by: str = "system",
) -> str:
    try:
        post = schedule_post(
            platform, content, hashtags=hashtags,
            scheduled_for=scheduled_for, created_by=created_by,
        )
        slot_str = datetime.fromtimestamp(post.scheduled_for, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        return f"Scheduled post #{post.id} on {post.platform} for {slot_str}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def get_due_posts_tool(platform: str = "") -> str:
    posts = get_due_posts(platform or None)
    return json.dumps([
        {"id": p.id, "platform": p.platform, "content": p.content[:100],
         "hashtags": p.hashtags, "scheduled_for": p.scheduled_for}
        for p in posts
    ])


def publishing_queue_tool(platform: str = "") -> str:
    posts = get_queue(platform or None)
    return json.dumps([
        {"id": p.id, "platform": p.platform, "content": p.content[:100],
         "scheduled_for": p.scheduled_for, "status": p.status}
        for p in posts
    ])


def schedule_stats_tool(days: int = 7) -> str:
    return json.dumps(schedule_stats(int(days)))
