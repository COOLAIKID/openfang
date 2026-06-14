"""
Content Calendar — planning, scheduling, and tracking for all content types.

Provides a full editorial calendar backed by SQLite: campaign management,
calendar entries (idea → scheduled → published), ideas backlog, reusable
templates, and optimal publishing slot configuration. Analytics helpers
give the org a real-time view of content velocity and mix.

Lifecycle::

    idea (backlog) → drafted → review → approved → scheduled → published
                                                                ↑
                                        promote_idea() creates an entry here

Agents interact via the registered @tool functions or call the core functions
directly.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTENT_TYPES = [
    "blog_post", "social_post", "email", "video", "podcast",
    "infographic", "webinar", "newsletter", "short_form", "thread",
    "reel", "story",
]

PLATFORMS = [
    "wordpress", "medium", "twitter", "linkedin", "instagram",
    "facebook", "youtube", "tiktok", "pinterest", "reddit",
    "telegram", "email_list",
]

ENTRY_STATUSES = [
    "idea", "drafted", "review", "approved", "scheduled",
    "published", "cancelled", "archived",
]

PRIORITIES: dict[int, str] = {
    1: "critical",
    2: "high",
    3: "medium",
    4: "low",
    5: "backlog",
}

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_schema_ready = False


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


def _init_schema() -> None:
    conn = _db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS calendar_campaigns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                start_date  TEXT,
                end_date    TEXT,
                goal        TEXT NOT NULL DEFAULT '',
                budget      REAL NOT NULL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'active',
                tags        TEXT NOT NULL DEFAULT '[]',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS calendar_entries (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                content_type    TEXT NOT NULL DEFAULT 'blog_post',
                platform        TEXT NOT NULL DEFAULT 'wordpress',
                scheduled_at    TEXT,
                status          TEXT NOT NULL DEFAULT 'idea',
                author          TEXT NOT NULL DEFAULT '',
                tags            TEXT NOT NULL DEFAULT '[]',
                description     TEXT NOT NULL DEFAULT '',
                body_draft      TEXT NOT NULL DEFAULT '',
                body_final      TEXT NOT NULL DEFAULT '',
                notes           TEXT NOT NULL DEFAULT '',
                campaign_id     INTEGER REFERENCES calendar_campaigns(id) ON DELETE SET NULL,
                priority        INTEGER NOT NULL DEFAULT 3,
                recurring_rule  TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_ideas (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                title               TEXT NOT NULL,
                content_type        TEXT NOT NULL DEFAULT 'blog_post',
                platform            TEXT NOT NULL DEFAULT '',
                description         TEXT NOT NULL DEFAULT '',
                priority            INTEGER NOT NULL DEFAULT 3,
                source              TEXT NOT NULL DEFAULT '',
                status              TEXT NOT NULL DEFAULT 'new',
                campaign_id         INTEGER REFERENCES calendar_campaigns(id) ON DELETE SET NULL,
                converted_to_entry_id INTEGER,
                tags                TEXT NOT NULL DEFAULT '[]',
                created_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_templates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                content_type    TEXT NOT NULL DEFAULT 'blog_post',
                platform        TEXT NOT NULL DEFAULT 'wordpress',
                title_template  TEXT NOT NULL DEFAULT '',
                body_template   TEXT NOT NULL DEFAULT '',
                tags            TEXT NOT NULL DEFAULT '[]',
                usage_count     INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS publishing_slots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                platform        TEXT NOT NULL,
                day_of_week     INTEGER NOT NULL,
                hour_of_day     INTEGER NOT NULL,
                minute_of_hour  INTEGER NOT NULL DEFAULT 0,
                is_active       INTEGER NOT NULL DEFAULT 1,
                timezone        TEXT NOT NULL DEFAULT 'UTC',
                created_at      TEXT NOT NULL,
                UNIQUE(platform, day_of_week, hour_of_day, minute_of_hour)
            );
        """)
        conn.commit()
        _seed_templates(conn)
    finally:
        conn.close()


def _seed_templates(conn: sqlite3.Connection) -> None:
    """Seed three built-in templates if the table is empty."""
    row = conn.execute("SELECT COUNT(*) FROM content_templates").fetchone()
    if row[0] > 0:
        return

    now = datetime.utcnow().isoformat()
    seeds = [
        (
            "weekly_roundup",
            "blog_post",
            "medium",
            "Weekly Roundup: {date}",
            "{intro}\n\n## Top Picks\n{picks}\n\n{cta}",
            '["roundup","weekly","blog"]',
        ),
        (
            "product_launch_tweet",
            "social_post",
            "twitter",
            "{product} is LIVE!",
            "🚀 {product} just launched!\n\n{benefit_1}\n{benefit_2}\n{benefit_3}\n\n{link} {hashtags}",
            '["launch","twitter","product"]',
        ),
        (
            "newsletter_issue",
            "email",
            "email_list",
            "{brand} Newsletter — {month}",
            (
                "Hi {name},\n\n{intro}\n\n## What's New\n{news}\n\n"
                "## Featured\n{featured}\n\n{cta}\n\nUntil next time,\n{author}"
            ),
            '["newsletter","email","brand"]',
        ),
    ]
    conn.executemany(
        """INSERT OR IGNORE INTO content_templates
           (name, content_type, platform, title_template, body_template, tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [(n, ct, pl, tt, bt, tg, now, now) for n, ct, pl, tt, bt, tg in seeds],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CalendarEntry:
    id: int
    title: str
    content_type: str
    platform: str
    scheduled_at: Optional[str]
    status: str
    author: str
    tags: list[str]
    description: str
    priority: int
    campaign_id: Optional[int]
    created_at: str

    @property
    def priority_label(self) -> str:
        return PRIORITIES.get(self.priority, "medium")

    @property
    def is_overdue(self) -> bool:
        if not self.scheduled_at:
            return False
        if self.status in ("published", "cancelled"):
            return False
        try:
            scheduled = datetime.fromisoformat(self.scheduled_at)
            return scheduled < datetime.utcnow()
        except ValueError:
            return False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content_type": self.content_type,
            "platform": self.platform,
            "scheduled_at": self.scheduled_at,
            "status": self.status,
            "author": self.author,
            "tags": self.tags,
            "description": self.description,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "campaign_id": self.campaign_id,
            "created_at": self.created_at,
            "is_overdue": self.is_overdue,
        }


@dataclass
class ContentIdea:
    id: int
    title: str
    content_type: str
    platform: str
    description: str
    priority: int
    source: str
    status: str
    campaign_id: Optional[int]
    converted_to_entry_id: Optional[int]
    tags: list[str]
    created_at: str

    @property
    def priority_label(self) -> str:
        return PRIORITIES.get(self.priority, "medium")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "content_type": self.content_type,
            "platform": self.platform,
            "description": self.description,
            "priority": self.priority,
            "priority_label": self.priority_label,
            "source": self.source,
            "status": self.status,
            "campaign_id": self.campaign_id,
            "converted_to_entry_id": self.converted_to_entry_id,
            "tags": self.tags,
            "created_at": self.created_at,
        }


@dataclass
class ContentTemplate:
    id: int
    name: str
    content_type: str
    platform: str
    title_template: str
    body_template: str
    tags: list[str]
    usage_count: int
    created_at: str
    updated_at: str

    def render(self, vars: dict) -> dict:
        """Render the template title and body with the given variables."""
        try:
            rendered_title = self.title_template.format_map(vars)
        except KeyError as exc:
            rendered_title = self.title_template  # return raw if key missing
        try:
            rendered_body = self.body_template.format_map(vars)
        except KeyError:
            rendered_body = self.body_template
        return {"title": rendered_title, "body": rendered_body}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "content_type": self.content_type,
            "platform": self.platform,
            "title_template": self.title_template,
            "body_template": self.body_template,
            "tags": self.tags,
            "usage_count": self.usage_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_to_entry(row: sqlite3.Row) -> CalendarEntry:
    tags: list[str] = []
    try:
        tags = json.loads(row["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        pass
    return CalendarEntry(
        id=row["id"],
        title=row["title"],
        content_type=row["content_type"],
        platform=row["platform"],
        scheduled_at=row["scheduled_at"],
        status=row["status"],
        author=row["author"],
        tags=tags,
        description=row["description"],
        priority=row["priority"],
        campaign_id=row["campaign_id"],
        created_at=row["created_at"],
    )


def _row_to_idea(row: sqlite3.Row) -> ContentIdea:
    tags: list[str] = []
    try:
        tags = json.loads(row["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        pass
    return ContentIdea(
        id=row["id"],
        title=row["title"],
        content_type=row["content_type"],
        platform=row["platform"],
        description=row["description"],
        priority=row["priority"],
        source=row["source"],
        status=row["status"],
        campaign_id=row["campaign_id"],
        converted_to_entry_id=row["converted_to_entry_id"],
        tags=tags,
        created_at=row["created_at"],
    )


def _row_to_template(row: sqlite3.Row) -> ContentTemplate:
    tags: list[str] = []
    try:
        tags = json.loads(row["tags"] or "[]")
    except (json.JSONDecodeError, TypeError):
        pass
    return ContentTemplate(
        id=row["id"],
        name=row["name"],
        content_type=row["content_type"],
        platform=row["platform"],
        title_template=row["title_template"],
        body_template=row["body_template"],
        tags=tags,
        usage_count=row["usage_count"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _campaign_id_for_name(conn: sqlite3.Connection, name: str) -> Optional[int]:
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM calendar_campaigns WHERE name = ?", (name,)
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Campaign management
# ---------------------------------------------------------------------------

def create_campaign(
    name: str,
    description: str = "",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    goal: str = "",
    budget: float = 0.0,
) -> dict:
    """Create a new campaign. Returns the campaign dict."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO calendar_campaigns
               (name, description, start_date, end_date, goal, budget, status, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', '[]', ?, ?)""",
            (name, description, start_date, end_date, goal, budget, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM calendar_campaigns WHERE name = ?", (name,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_campaign(name: str) -> Optional[dict]:
    """Return a campaign dict by name, or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM calendar_campaigns WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_campaigns(status: Optional[str] = None) -> list[dict]:
    """List campaigns, optionally filtered by status."""
    _ensure()
    conn = _db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM calendar_campaigns WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM calendar_campaigns ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_campaign(campaign_id: int, **fields: Any) -> bool:
    """Update arbitrary campaign fields. Returns True on success."""
    _ensure()
    allowed = {
        "name", "description", "start_date", "end_date",
        "goal", "budget", "status", "tags",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    now = datetime.utcnow().isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [campaign_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE calendar_campaigns SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Calendar entries
# ---------------------------------------------------------------------------

def add_entry(
    title: str,
    content_type: str = "blog_post",
    platform: str = "wordpress",
    scheduled_at: Optional[str] = None,
    author: str = "",
    description: str = "",
    body_draft: str = "",
    tags_list: Optional[list[str]] = None,
    campaign_name: Optional[str] = None,
    priority: int = 3,
) -> CalendarEntry:
    """Add a new calendar entry. Returns the created CalendarEntry."""
    _ensure()
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(tags_list or [])
    conn = _db()
    try:
        campaign_id = _campaign_id_for_name(conn, campaign_name or "")
        cur = conn.execute(
            """INSERT INTO calendar_entries
               (title, content_type, platform, scheduled_at, status, author, tags,
                description, body_draft, campaign_id, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'idea', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title, content_type, platform, scheduled_at, author, tags_json,
                description, body_draft, campaign_id, priority, now, now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM calendar_entries WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_entry(row)
    finally:
        conn.close()


def get_entry(entry_id: int) -> Optional[CalendarEntry]:
    """Return a CalendarEntry by ID, or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM calendar_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_entry(row) if row else None
    finally:
        conn.close()


def list_entries(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    content_type: Optional[str] = None,
    days_ahead: Optional[int] = None,
    days_behind: Optional[int] = None,
    campaign_name: Optional[str] = None,
) -> list[CalendarEntry]:
    """List calendar entries with optional filters."""
    _ensure()
    conn = _db()
    try:
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        if content_type:
            clauses.append("content_type = ?")
            params.append(content_type)
        if days_ahead is not None:
            future = (datetime.utcnow() + timedelta(days=days_ahead)).isoformat()
            clauses.append("scheduled_at <= ?")
            params.append(future)
        if days_behind is not None:
            past = (datetime.utcnow() - timedelta(days=days_behind)).isoformat()
            clauses.append("scheduled_at >= ?")
            params.append(past)
        if campaign_name:
            campaign_id = _campaign_id_for_name(conn, campaign_name)
            if campaign_id:
                clauses.append("campaign_id = ?")
                params.append(campaign_id)
            else:
                return []

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM calendar_entries {where} ORDER BY scheduled_at ASC, priority ASC",
            params,
        ).fetchall()
        return [_row_to_entry(r) for r in rows]
    finally:
        conn.close()


def update_entry(entry_id: int, **fields: Any) -> bool:
    """Update arbitrary entry fields. Returns True on success."""
    _ensure()
    allowed = {
        "title", "content_type", "platform", "scheduled_at", "status",
        "author", "tags", "description", "body_draft", "body_final",
        "notes", "campaign_id", "priority", "recurring_rule",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    now = datetime.utcnow().isoformat()
    updates["updated_at"] = now
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [entry_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE calendar_entries SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def approve_entry(entry_id: int) -> bool:
    """Set entry status to 'approved'. Returns True on success."""
    return update_entry(entry_id, status="approved")


def publish_entry(entry_id: int, final_body: Optional[str] = None) -> bool:
    """Mark an entry as published. Optionally record final body."""
    kwargs: dict[str, Any] = {"status": "published"}
    if final_body is not None:
        kwargs["body_final"] = final_body
    # Record publish timestamp in notes
    ts = datetime.utcnow().isoformat()
    entry = get_entry(entry_id)
    if entry:
        existing_notes = ""
        conn = _db()
        try:
            row = conn.execute(
                "SELECT notes FROM calendar_entries WHERE id = ?", (entry_id,)
            ).fetchone()
            if row:
                existing_notes = row["notes"] or ""
        finally:
            conn.close()
        kwargs["notes"] = (existing_notes + f"\nPublished at {ts}").strip()
    return update_entry(entry_id, **kwargs)


def cancel_entry(entry_id: int, reason: Optional[str] = None) -> bool:
    """Cancel a calendar entry."""
    kwargs: dict[str, Any] = {"status": "cancelled"}
    if reason:
        entry = get_entry(entry_id)
        if entry:
            conn = _db()
            try:
                row = conn.execute(
                    "SELECT notes FROM calendar_entries WHERE id = ?", (entry_id,)
                ).fetchone()
                existing = (row["notes"] or "") if row else ""
            finally:
                conn.close()
            kwargs["notes"] = (existing + f"\nCancelled: {reason}").strip()
    return update_entry(entry_id, **kwargs)


def delete_entry(entry_id: int) -> bool:
    """Hard-delete a calendar entry."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM calendar_entries WHERE id = ?", (entry_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def reschedule_entry(entry_id: int, new_scheduled_at: str) -> bool:
    """Change the scheduled_at datetime of an entry."""
    return update_entry(entry_id, scheduled_at=new_scheduled_at)


def entries_due_today() -> list[CalendarEntry]:
    """Return all entries scheduled for today (UTC) that are not published/cancelled."""
    _ensure()
    today = datetime.utcnow().date()
    day_start = datetime(today.year, today.month, today.day, 0, 0, 0).isoformat()
    day_end = datetime(today.year, today.month, today.day, 23, 59, 59).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT * FROM calendar_entries
               WHERE scheduled_at >= ? AND scheduled_at <= ?
               AND status NOT IN ('published', 'cancelled', 'archived')
               ORDER BY scheduled_at ASC""",
            (day_start, day_end),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]
    finally:
        conn.close()


def overdue_entries() -> list[CalendarEntry]:
    """Return all entries whose scheduled_at is in the past and are not done."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT * FROM calendar_entries
               WHERE scheduled_at < ?
               AND status NOT IN ('published', 'cancelled', 'archived')
               ORDER BY scheduled_at ASC""",
            (now,),
        ).fetchall()
        return [_row_to_entry(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ideas backlog
# ---------------------------------------------------------------------------

def add_idea(
    title: str,
    content_type: str = "blog_post",
    platform: Optional[str] = None,
    description: str = "",
    source: str = "",
    tags_list: Optional[list[str]] = None,
    priority: int = 3,
) -> ContentIdea:
    """Add a content idea to the backlog. Returns the created ContentIdea."""
    _ensure()
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(tags_list or [])
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO content_ideas
               (title, content_type, platform, description, priority, source,
                status, tags, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
            (title, content_type, platform or "", description, priority,
             source, tags_json, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM content_ideas WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_idea(row)
    finally:
        conn.close()


def list_ideas(
    status: Optional[str] = None,
    platform: Optional[str] = None,
    limit: int = 50,
) -> list[ContentIdea]:
    """List content ideas, optionally filtered by status and platform."""
    _ensure()
    conn = _db()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM content_ideas {where} ORDER BY priority ASC, created_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_idea(r) for r in rows]
    finally:
        conn.close()


def promote_idea(
    idea_id: int,
    scheduled_at: str,
    author: str = "",
) -> CalendarEntry:
    """Promote a content idea to a calendar entry.

    Creates a CalendarEntry from the idea, marks the idea as 'converted',
    and links back via converted_to_entry_id. Returns the new CalendarEntry.
    Raises ValueError if the idea is not found.
    """
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM content_ideas WHERE id = ?", (idea_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Idea {idea_id} not found")
        idea = _row_to_idea(row)
    finally:
        conn.close()

    entry = add_entry(
        title=idea.title,
        content_type=idea.content_type,
        platform=idea.platform or "wordpress",
        scheduled_at=scheduled_at,
        author=author,
        description=idea.description,
        tags_list=idea.tags,
        priority=idea.priority,
    )

    conn = _db()
    try:
        now = datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE content_ideas
               SET status = 'converted', converted_to_entry_id = ?
               WHERE id = ?""",
            (entry.id, idea_id),
        )
        conn.commit()
    finally:
        conn.close()

    return entry


def archive_idea(idea_id: int) -> bool:
    """Set an idea's status to 'archived'. Returns True on success."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE content_ideas SET status = 'archived' WHERE id = ?", (idea_id,)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

def create_template(
    name: str,
    content_type: str,
    platform: str,
    title_template: str,
    body_template: str,
    tags_list: Optional[list[str]] = None,
) -> ContentTemplate:
    """Create a content template. Returns the created ContentTemplate."""
    _ensure()
    now = datetime.utcnow().isoformat()
    tags_json = json.dumps(tags_list or [])
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO content_templates
               (name, content_type, platform, title_template, body_template, tags, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, content_type, platform, title_template, body_template, tags_json, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM content_templates WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return _row_to_template(row)
    finally:
        conn.close()


def get_template(name: str) -> Optional[ContentTemplate]:
    """Return a ContentTemplate by name, or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM content_templates WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_template(row) if row else None
    finally:
        conn.close()


def list_templates(
    content_type: Optional[str] = None,
    platform: Optional[str] = None,
) -> list[ContentTemplate]:
    """List templates, optionally filtered by content_type and platform."""
    _ensure()
    conn = _db()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if content_type:
            clauses.append("content_type = ?")
            params.append(content_type)
        if platform:
            clauses.append("platform = ?")
            params.append(platform)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM content_templates {where} ORDER BY name ASC",
            params,
        ).fetchall()
        return [_row_to_template(r) for r in rows]
    finally:
        conn.close()


def create_entry_from_template(
    template_name: str,
    scheduled_at: str,
    vars: dict,
    author: str = "",
    campaign_name: Optional[str] = None,
) -> CalendarEntry:
    """Render a template with vars and create a calendar entry from it.

    Increments the template's usage_count. Returns the new CalendarEntry.
    Raises ValueError if template not found.
    """
    template = get_template(template_name)
    if not template:
        raise ValueError(f"Template '{template_name}' not found")

    rendered = template.render(vars)

    entry = add_entry(
        title=rendered["title"],
        content_type=template.content_type,
        platform=template.platform,
        scheduled_at=scheduled_at,
        author=author,
        body_draft=rendered["body"],
        tags_list=template.tags,
        campaign_name=campaign_name,
    )

    # Increment usage count
    conn = _db()
    try:
        now = datetime.utcnow().isoformat()
        conn.execute(
            "UPDATE content_templates SET usage_count = usage_count + 1, updated_at = ? WHERE name = ?",
            (now, template_name),
        )
        conn.commit()
    finally:
        conn.close()

    return entry


# ---------------------------------------------------------------------------
# Publishing slots
# ---------------------------------------------------------------------------

def set_publishing_slot(
    platform: str,
    day_of_week: int,
    hour_of_day: int,
    minute: int = 0,
    timezone: str = "UTC",
) -> dict:
    """Create or update a publishing slot. Returns the slot as a dict.

    day_of_week: 0=Monday … 6=Sunday (ISO weekday - 1)
    """
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO publishing_slots
               (platform, day_of_week, hour_of_day, minute_of_hour, is_active, timezone, created_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)
               ON CONFLICT(platform, day_of_week, hour_of_day, minute_of_hour)
               DO UPDATE SET is_active=1, timezone=excluded.timezone""",
            (platform, day_of_week, hour_of_day, minute, timezone, now),
        )
        conn.commit()
        row = conn.execute(
            """SELECT * FROM publishing_slots
               WHERE platform = ? AND day_of_week = ? AND hour_of_day = ? AND minute_of_hour = ?""",
            (platform, day_of_week, hour_of_day, minute),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_publishing_slots(platform: Optional[str] = None) -> list[dict]:
    """List publishing slots, optionally filtered by platform."""
    _ensure()
    conn = _db()
    try:
        if platform:
            rows = conn.execute(
                """SELECT * FROM publishing_slots
                   WHERE platform = ? AND is_active = 1
                   ORDER BY day_of_week ASC, hour_of_day ASC, minute_of_hour ASC""",
                (platform,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM publishing_slots
                   WHERE is_active = 1
                   ORDER BY platform ASC, day_of_week ASC, hour_of_day ASC""",
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def next_slots(platform: Optional[str] = None, count: int = 5) -> list[str]:
    """Return the next N publishing slot datetimes as ISO strings.

    Walks forward from now to find the upcoming matching day/hour/minute
    slots. Returns at most `count` results.
    """
    _ensure()
    slots = list_publishing_slots(platform)
    if not slots:
        return []

    now = datetime.utcnow()
    results: list[datetime] = []
    # Check up to 8 weeks ahead to fill `count` slots
    candidate = now.replace(second=0, microsecond=0)
    days_checked = 0
    while len(results) < count and days_checked < 56:
        for slot in slots:
            if slot["day_of_week"] == candidate.weekday():
                slot_dt = candidate.replace(
                    hour=slot["hour_of_day"],
                    minute=slot["minute_of_hour"],
                    second=0,
                    microsecond=0,
                )
                if slot_dt > now and slot_dt not in results:
                    results.append(slot_dt)
        candidate += timedelta(days=1)
        days_checked += 1

    results.sort()
    return [dt.isoformat() for dt in results[:count]]


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def publishing_velocity(days: int = 30) -> dict:
    """Return count of published entries per platform per week over last N days.

    Returns: {platform: {week_label: count, ...}, ...}
    """
    _ensure()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT platform, scheduled_at
               FROM calendar_entries
               WHERE status = 'published' AND scheduled_at >= ?
               ORDER BY scheduled_at ASC""",
            (since,),
        ).fetchall()
    finally:
        conn.close()

    velocity: dict[str, dict[str, int]] = {}
    for row in rows:
        plat = row["platform"]
        try:
            dt = datetime.fromisoformat(row["scheduled_at"])
        except (ValueError, TypeError):
            continue
        # ISO week label e.g. "2024-W03"
        week_label = dt.strftime("%Y-W%W")
        velocity.setdefault(plat, {})
        velocity[plat][week_label] = velocity[plat].get(week_label, 0) + 1

    return velocity


def content_mix(days: int = 30) -> dict:
    """Return a breakdown of content_type counts over last N days.

    Returns: {content_type: count, ...}
    """
    _ensure()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT content_type, COUNT(*) as cnt
               FROM calendar_entries
               WHERE created_at >= ?
               GROUP BY content_type
               ORDER BY cnt DESC""",
            (since,),
        ).fetchall()
    finally:
        conn.close()

    return {row["content_type"]: row["cnt"] for row in rows}


def campaign_coverage(campaign_name: str) -> dict:
    """Return entry breakdown for a campaign by status, platform, and date range.

    Returns: {
        campaign: {name, start_date, end_date, goal},
        total_entries: int,
        by_status: {status: count},
        by_platform: {platform: count},
        date_range: {earliest: str, latest: str},
    }
    """
    _ensure()
    campaign = get_campaign(campaign_name)
    if not campaign:
        return {"error": f"Campaign '{campaign_name}' not found"}

    conn = _db()
    try:
        rows = conn.execute(
            """SELECT status, platform, scheduled_at
               FROM calendar_entries
               WHERE campaign_id = ?""",
            (campaign["id"],),
        ).fetchall()
    finally:
        conn.close()

    by_status: dict[str, int] = {}
    by_platform: dict[str, int] = {}
    dates: list[str] = []

    for row in rows:
        st = row["status"]
        pl = row["platform"]
        by_status[st] = by_status.get(st, 0) + 1
        by_platform[pl] = by_platform.get(pl, 0) + 1
        if row["scheduled_at"]:
            dates.append(row["scheduled_at"])

    return {
        "campaign": {
            "name": campaign["name"],
            "start_date": campaign["start_date"],
            "end_date": campaign["end_date"],
            "goal": campaign["goal"],
        },
        "total_entries": len(rows),
        "by_status": by_status,
        "by_platform": by_platform,
        "date_range": {
            "earliest": min(dates) if dates else None,
            "latest": max(dates) if dates else None,
        },
    }


def content_calendar_summary() -> dict:
    """Return a high-level summary of the content calendar.

    Returns: {
        total_entries, scheduled_this_week, overdue, ideas_pending,
        campaigns_active, platform_breakdown, upcoming_deadlines
    }
    """
    _ensure()
    now = datetime.utcnow()
    week_end = (now + timedelta(days=7)).isoformat()
    now_iso = now.isoformat()

    conn = _db()
    try:
        total_entries = conn.execute(
            "SELECT COUNT(*) FROM calendar_entries"
        ).fetchone()[0]

        scheduled_this_week = conn.execute(
            """SELECT COUNT(*) FROM calendar_entries
               WHERE scheduled_at >= ? AND scheduled_at <= ?
               AND status NOT IN ('published','cancelled','archived')""",
            (now_iso, week_end),
        ).fetchone()[0]

        overdue_count = conn.execute(
            """SELECT COUNT(*) FROM calendar_entries
               WHERE scheduled_at < ?
               AND status NOT IN ('published','cancelled','archived')""",
            (now_iso,),
        ).fetchone()[0]

        ideas_pending = conn.execute(
            "SELECT COUNT(*) FROM content_ideas WHERE status = 'new'"
        ).fetchone()[0]

        campaigns_active = conn.execute(
            "SELECT COUNT(*) FROM calendar_campaigns WHERE status = 'active'"
        ).fetchone()[0]

        platform_rows = conn.execute(
            """SELECT platform, COUNT(*) as cnt
               FROM calendar_entries
               WHERE status NOT IN ('cancelled','archived')
               GROUP BY platform
               ORDER BY cnt DESC""",
        ).fetchall()
        platform_breakdown = {r["platform"]: r["cnt"] for r in platform_rows}

        deadline_rows = conn.execute(
            """SELECT id, title, platform, content_type, scheduled_at, status, priority
               FROM calendar_entries
               WHERE scheduled_at >= ? AND scheduled_at <= ?
               AND status NOT IN ('published','cancelled','archived')
               ORDER BY scheduled_at ASC
               LIMIT 10""",
            (now_iso, week_end),
        ).fetchall()
        upcoming_deadlines = [
            {
                "id": r["id"],
                "title": r["title"],
                "platform": r["platform"],
                "content_type": r["content_type"],
                "scheduled_at": r["scheduled_at"],
                "status": r["status"],
                "priority": r["priority"],
            }
            for r in deadline_rows
        ]
    finally:
        conn.close()

    return {
        "total_entries": total_entries,
        "scheduled_this_week": scheduled_this_week,
        "overdue": overdue_count,
        "ideas_pending": ideas_pending,
        "campaigns_active": campaigns_active,
        "platform_breakdown": platform_breakdown,
        "upcoming_deadlines": upcoming_deadlines,
    }


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

@tool("cc_add_entry", "Add a new entry to the content calendar.")
def cc_add_entry_tool(
    title: str,
    content_type: str = "blog_post",
    platform: str = "wordpress",
    scheduled_at: str = "",
    description: str = "",
    author: str = "",
    campaign_name: str = "",
) -> str:
    """Add a new calendar entry and return it as JSON."""
    try:
        entry = add_entry(
            title=title,
            content_type=content_type,
            platform=platform,
            scheduled_at=scheduled_at or None,
            author=author,
            description=description,
            campaign_name=campaign_name or None,
        )
        return json.dumps({"ok": True, "entry": entry.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_list_entries", "List calendar entries with optional filters.")
def cc_list_entries_tool(
    status: str = "",
    platform: str = "",
    days_ahead: str = "",
) -> str:
    """Return a JSON list of calendar entries."""
    try:
        da: Optional[int] = int(days_ahead) if days_ahead else None
        entries = list_entries(
            status=status or None,
            platform=platform or None,
            days_ahead=da,
        )
        return json.dumps({"ok": True, "entries": [e.to_dict() for e in entries], "count": len(entries)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_add_idea", "Add a content idea to the backlog.")
def cc_add_idea_tool(
    title: str,
    content_type: str = "blog_post",
    platform: str = "",
    description: str = "",
    priority: str = "3",
) -> str:
    """Add a content idea and return it as JSON."""
    try:
        prio = int(priority) if priority else 3
        idea = add_idea(
            title=title,
            content_type=content_type,
            platform=platform or None,
            description=description,
            priority=prio,
        )
        return json.dumps({"ok": True, "idea": idea.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_promote_idea", "Promote a content idea to a scheduled calendar entry.")
def cc_promote_idea_tool(idea_id: str, scheduled_at: str) -> str:
    """Promote idea to entry and return the new entry as JSON."""
    try:
        entry = promote_idea(int(idea_id), scheduled_at)
        return json.dumps({"ok": True, "entry": entry.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_entries_due_today", "List all content calendar entries due today.")
def cc_entries_due_today_tool() -> str:
    """Return JSON list of entries scheduled for today."""
    try:
        entries = entries_due_today()
        return json.dumps({"ok": True, "entries": [e.to_dict() for e in entries], "count": len(entries)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_calendar_summary", "Get a high-level summary of the content calendar.")
def cc_calendar_summary_tool() -> str:
    """Return a JSON summary of the content calendar state."""
    try:
        summary = content_calendar_summary()
        return json.dumps({"ok": True, **summary})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_create_campaign", "Create a new content campaign.")
def cc_create_campaign_tool(
    name: str,
    start_date: str = "",
    end_date: str = "",
    goal: str = "",
) -> str:
    """Create a campaign and return it as JSON."""
    try:
        campaign = create_campaign(
            name=name,
            start_date=start_date or None,
            end_date=end_date or None,
            goal=goal,
        )
        return json.dumps({"ok": True, "campaign": campaign})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_create_entry_from_template", "Create a calendar entry from a named template.")
def cc_create_entry_from_template_tool(
    template_name: str,
    scheduled_at: str,
    vars_json: str = "{}",
) -> str:
    """Render a template and create a calendar entry. vars_json is a JSON object string."""
    try:
        vars_dict: dict = json.loads(vars_json) if vars_json else {}
        entry = create_entry_from_template(
            template_name=template_name,
            scheduled_at=scheduled_at,
            vars=vars_dict,
        )
        return json.dumps({"ok": True, "entry": entry.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("cc_next_slots", "Get the next N publishing slot datetimes for a platform.")
def cc_next_slots_tool(platform: str = "", count: str = "5") -> str:
    """Return the next scheduled publishing slots as ISO datetime strings."""
    try:
        n = int(count) if count else 5
        slots = next_slots(platform=platform or None, count=n)
        return json.dumps({"ok": True, "slots": slots, "count": len(slots)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
