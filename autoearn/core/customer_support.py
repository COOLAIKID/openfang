"""
Customer Support — tickets, knowledge base, canned responses, SLA, analytics.

Provides a full-featured support desk for the AutoEarn organisation:

- Multi-channel ticket intake (email, chat, phone, social, web form, API)
- Threaded ticket messages with internal notes
- Knowledge-base articles with helpfulness ratings
- Canned response templates with variable substitution
- SLA policy enforcement with breach detection
- CSAT tracking and analytics (volume, resolution time, agent workload)

All data is stored in the shared ``autoearn.db`` SQLite database.

Tool-decorated functions expose the most common operations to AI agents so
they can handle support queues autonomously.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TICKET_STATUSES = ["new", "open", "pending", "on_hold", "solved", "closed"]
PRIORITIES = ["low", "normal", "high", "urgent", "critical"]
CATEGORIES = [
    "billing",
    "technical",
    "account",
    "feature_request",
    "bug_report",
    "refund",
    "shipping",
    "general",
]
CHANNELS = ["email", "chat", "phone", "social", "web_form", "api"]
AUTHOR_TYPES = ["customer", "agent", "bot", "system"]

# SLA first-response / resolution hours by priority (defaults)
_DEFAULT_SLA: dict[str, tuple[float, float]] = {
    "critical": (1.0, 4.0),
    "urgent": (2.0, 8.0),
    "high": (4.0, 24.0),
    "normal": (8.0, 48.0),
    "low": (24.0, 72.0),
}

# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------

_schema_ready = False


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
            CREATE TABLE IF NOT EXISTS support_tickets (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_number       TEXT    NOT NULL UNIQUE,
                customer_email      TEXT    NOT NULL DEFAULT '',
                customer_name       TEXT    NOT NULL DEFAULT '',
                subject             TEXT    NOT NULL DEFAULT '',
                description         TEXT    NOT NULL DEFAULT '',
                status              TEXT    NOT NULL DEFAULT 'new',
                priority            TEXT    NOT NULL DEFAULT 'normal',
                category            TEXT    NOT NULL DEFAULT 'general',
                assigned_to         TEXT    NOT NULL DEFAULT '',
                tags                TEXT    NOT NULL DEFAULT '[]',
                channel             TEXT    NOT NULL DEFAULT 'web_form',
                product_id          INTEGER NOT NULL DEFAULT 0,
                order_id            TEXT    NOT NULL DEFAULT '',
                satisfaction_rating INTEGER,
                satisfaction_comment TEXT   NOT NULL DEFAULT '',
                first_response_at   REAL,
                resolved_at         REAL,
                created_at          REAL    NOT NULL,
                updated_at          REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ticket_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id   INTEGER NOT NULL,
                author_email TEXT   NOT NULL DEFAULT '',
                author_name  TEXT   NOT NULL DEFAULT '',
                author_type  TEXT   NOT NULL DEFAULT 'agent',
                body         TEXT   NOT NULL DEFAULT '',
                is_internal  INTEGER NOT NULL DEFAULT 0,
                attachments  TEXT   NOT NULL DEFAULT '[]',
                created_at   REAL   NOT NULL
            );

            CREATE TABLE IF NOT EXISTS support_articles (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL DEFAULT '',
                slug            TEXT NOT NULL UNIQUE,
                category        TEXT NOT NULL DEFAULT 'general',
                body            TEXT NOT NULL DEFAULT '',
                tags            TEXT NOT NULL DEFAULT '[]',
                helpful_count   INTEGER NOT NULL DEFAULT 0,
                not_helpful_count INTEGER NOT NULL DEFAULT 0,
                view_count      INTEGER NOT NULL DEFAULT 0,
                status          TEXT NOT NULL DEFAULT 'draft',
                created_at      REAL NOT NULL,
                updated_at      REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS support_categories (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL UNIQUE,
                description   TEXT NOT NULL DEFAULT '',
                parent_id     INTEGER NOT NULL DEFAULT 0,
                sort_order    INTEGER NOT NULL DEFAULT 0,
                article_count INTEGER NOT NULL DEFAULT 0,
                created_at    REAL    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS canned_responses (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                category   TEXT NOT NULL DEFAULT '',
                subject    TEXT NOT NULL DEFAULT '',
                body       TEXT NOT NULL DEFAULT '',
                tags       TEXT NOT NULL DEFAULT '[]',
                use_count  INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sla_policies (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                name                 TEXT NOT NULL UNIQUE,
                priority             TEXT NOT NULL DEFAULT 'normal',
                first_response_hours REAL NOT NULL DEFAULT 8.0,
                resolution_hours     REAL NOT NULL DEFAULT 48.0,
                business_hours_only  INTEGER NOT NULL DEFAULT 1,
                created_at           REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ticket_tags (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id  INTEGER NOT NULL,
                tag        TEXT    NOT NULL,
                created_at REAL    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tickets_status   ON support_tickets(status);
            CREATE INDEX IF NOT EXISTS idx_tickets_priority ON support_tickets(priority);
            CREATE INDEX IF NOT EXISTS idx_tickets_email    ON support_tickets(customer_email);
            CREATE INDEX IF NOT EXISTS idx_tickets_assigned ON support_tickets(assigned_to);
            CREATE INDEX IF NOT EXISTS idx_tm_ticket        ON ticket_messages(ticket_id);
            CREATE INDEX IF NOT EXISTS idx_articles_slug    ON support_articles(slug);
            CREATE INDEX IF NOT EXISTS idx_articles_cat     ON support_articles(category);
            CREATE INDEX IF NOT EXISTS idx_ticket_tags_tid  ON ticket_tags(ticket_id);
        """)
        conn.commit()
        _seed_sla_policies(conn)
    finally:
        conn.close()


def _seed_sla_policies(conn: sqlite3.Connection) -> None:
    """Insert the five default SLA policies if they don't exist yet."""
    defaults = [
        ("Critical Response SLA", "critical", 1.0, 4.0, 0),
        ("Urgent Response SLA", "urgent", 2.0, 8.0, 1),
        ("High Response SLA", "high", 4.0, 24.0, 1),
        ("Normal Response SLA", "normal", 8.0, 48.0, 1),
        ("Low Response SLA", "low", 24.0, 72.0, 1),
    ]
    now = time.time()
    for name, priority, fr, res, biz in defaults:
        conn.execute(
            """INSERT OR IGNORE INTO sla_policies
               (name, priority, first_response_hours, resolution_hours,
                business_hours_only, created_at)
               VALUES (?,?,?,?,?,?)""",
            (name, priority, fr, res, biz, now),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> float:
    return time.time()


def _ts() -> str:
    """ISO-8601 timestamp string for the current moment."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug_from(text: str) -> str:
    """Convert a string to a URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80]


def _next_ticket_number() -> str:
    """Generate a ticket number like TKT-20240614-0001."""
    date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
    conn = _db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM support_tickets WHERE ticket_number LIKE ?",
            (f"TKT-{date_part}-%",),
        ).fetchone()
        seq = (row["cnt"] if row else 0) + 1
    finally:
        conn.close()
    return f"TKT-{date_part}-{seq:04d}"


def _hours_since(ts: float) -> float:
    return (time.time() - ts) / 3600.0


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SupportTicket:
    id: int
    ticket_number: str
    customer_email: str
    customer_name: str
    subject: str
    description: str
    status: str
    priority: str
    category: str
    assigned_to: str
    tags: list[str]
    channel: str
    product_id: int
    order_id: str
    satisfaction_rating: Optional[int]
    satisfaction_comment: str
    first_response_at: Optional[float]
    resolved_at: Optional[float]
    created_at: float
    updated_at: float

    # ------------------------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self.status not in ("solved", "closed")

    @property
    def age_hours(self) -> float:
        return _hours_since(self.created_at)

    @property
    def sla_breached(self) -> bool:
        """Simple breach check: open ticket past default SLA resolution hours."""
        if not self.is_open:
            return False
        fr, res = _DEFAULT_SLA.get(self.priority, (8.0, 48.0))
        return self.age_hours > res

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ticket_number": self.ticket_number,
            "customer_email": self.customer_email,
            "customer_name": self.customer_name,
            "subject": self.subject,
            "description": self.description,
            "status": self.status,
            "priority": self.priority,
            "category": self.category,
            "assigned_to": self.assigned_to,
            "tags": self.tags,
            "channel": self.channel,
            "product_id": self.product_id,
            "order_id": self.order_id,
            "satisfaction_rating": self.satisfaction_rating,
            "satisfaction_comment": self.satisfaction_comment,
            "first_response_at": self.first_response_at,
            "resolved_at": self.resolved_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "is_open": self.is_open,
            "age_hours": round(self.age_hours, 2),
            "sla_breached": self.sla_breached,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SupportTicket":
        return cls(
            id=row["id"],
            ticket_number=row["ticket_number"],
            customer_email=row["customer_email"] or "",
            customer_name=row["customer_name"] or "",
            subject=row["subject"] or "",
            description=row["description"] or "",
            status=row["status"] or "new",
            priority=row["priority"] or "normal",
            category=row["category"] or "general",
            assigned_to=row["assigned_to"] or "",
            tags=json.loads(row["tags"] or "[]"),
            channel=row["channel"] or "web_form",
            product_id=row["product_id"] or 0,
            order_id=row["order_id"] or "",
            satisfaction_rating=row["satisfaction_rating"],
            satisfaction_comment=row["satisfaction_comment"] or "",
            first_response_at=row["first_response_at"],
            resolved_at=row["resolved_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class SupportArticle:
    id: int
    title: str
    slug: str
    category: str
    body: str
    tags: list[str]
    helpful_count: int
    not_helpful_count: int
    view_count: int
    status: str
    created_at: float
    updated_at: float

    @property
    def helpfulness_rate(self) -> float:
        total = self.helpful_count + self.not_helpful_count
        return self.helpful_count / total if total > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "slug": self.slug,
            "category": self.category,
            "body": self.body,
            "tags": self.tags,
            "helpful_count": self.helpful_count,
            "not_helpful_count": self.not_helpful_count,
            "view_count": self.view_count,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "helpfulness_rate": round(self.helpfulness_rate, 4),
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SupportArticle":
        return cls(
            id=row["id"],
            title=row["title"] or "",
            slug=row["slug"],
            category=row["category"] or "general",
            body=row["body"] or "",
            tags=json.loads(row["tags"] or "[]"),
            helpful_count=row["helpful_count"] or 0,
            not_helpful_count=row["not_helpful_count"] or 0,
            view_count=row["view_count"] or 0,
            status=row["status"] or "draft",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ---------------------------------------------------------------------------
# Ticket CRUD
# ---------------------------------------------------------------------------

def create_ticket(
    customer_email: str,
    subject: str,
    description: str,
    customer_name: str = "",
    priority: str = "normal",
    category: str = "general",
    channel: str = "web_form",
    product_id: int = 0,
    order_id: str = "",
) -> SupportTicket:
    """Create a new support ticket and return the :class:`SupportTicket`."""
    _ensure()
    if priority not in PRIORITIES:
        priority = "normal"
    if category not in CATEGORIES:
        category = "general"
    if channel not in CHANNELS:
        channel = "web_form"

    now = _now()
    ticket_number = _next_ticket_number()

    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO support_tickets
               (ticket_number, customer_email, customer_name, subject, description,
                status, priority, category, assigned_to, tags, channel,
                product_id, order_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                ticket_number, customer_email, customer_name, subject, description,
                "new", priority, category, "", "[]", channel,
                product_id, order_id, now, now,
            ),
        )
        ticket_id = cur.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT * FROM support_tickets WHERE id=?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()

    ticket = SupportTicket.from_row(row)
    _add_system_message(
        ticket_id,
        f"Ticket created via {channel}. Priority: {priority}.",
    )
    return ticket


def get_ticket(ticket_number: str) -> Optional[SupportTicket]:
    """Return a ticket by its ticket_number, or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM support_tickets WHERE ticket_number=?",
            (ticket_number,),
        ).fetchone()
    finally:
        conn.close()
    return SupportTicket.from_row(row) if row else None


def get_ticket_by_id(ticket_id: int) -> Optional[SupportTicket]:
    """Return a ticket by its numeric id, or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM support_tickets WHERE id=?", (ticket_id,)
        ).fetchone()
    finally:
        conn.close()
    return SupportTicket.from_row(row) if row else None


def list_tickets(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    assigned_to: Optional[str] = None,
    customer_email: Optional[str] = None,
    limit: int = 100,
) -> list[SupportTicket]:
    """Return tickets, filtered by any combination of the optional params."""
    _ensure()
    clauses: list[str] = []
    params: list[Any] = []

    if status:
        clauses.append("status=?")
        params.append(status)
    if priority:
        clauses.append("priority=?")
        params.append(priority)
    if category:
        clauses.append("category=?")
        params.append(category)
    if assigned_to:
        clauses.append("assigned_to=?")
        params.append(assigned_to)
    if customer_email:
        clauses.append("customer_email=?")
        params.append(customer_email)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM support_tickets {where} ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = _db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [SupportTicket.from_row(r) for r in rows]


def update_ticket(ticket_number: str, **fields: Any) -> bool:
    """Update arbitrary fields on a ticket.  Returns True on success."""
    _ensure()
    allowed = {
        "customer_email", "customer_name", "subject", "description",
        "status", "priority", "category", "assigned_to", "tags",
        "channel", "product_id", "order_id", "satisfaction_rating",
        "satisfaction_comment", "first_response_at", "resolved_at",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    # Serialise tags list to JSON if provided
    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [ticket_number]

    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE support_tickets SET {set_clause} WHERE ticket_number=?",
            values,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def assign_ticket(ticket_number: str, agent_email: str) -> bool:
    """Assign a ticket to an agent. Transitions status to 'open'."""
    _ensure()
    ok = update_ticket(ticket_number, assigned_to=agent_email, status="open")
    if ok:
        t = get_ticket(ticket_number)
        if t:
            _add_system_message(
                t.id, f"Ticket assigned to {agent_email}."
            )
    return ok


def change_status(
    ticket_number: str,
    new_status: str,
    note: str = "",
) -> bool:
    """Change ticket status and optionally append a system note."""
    _ensure()
    if new_status not in TICKET_STATUSES:
        return False

    extra: dict[str, Any] = {}
    if new_status in ("solved", "closed"):
        extra["resolved_at"] = _now()

    ok = update_ticket(ticket_number, status=new_status, **extra)
    if ok:
        t = get_ticket(ticket_number)
        if t:
            msg = f"Status changed to '{new_status}'."
            if note:
                msg += f" Note: {note}"
            _add_system_message(t.id, msg)
    return ok


def change_priority(ticket_number: str, new_priority: str) -> bool:
    """Change ticket priority."""
    _ensure()
    if new_priority not in PRIORITIES:
        return False
    ok = update_ticket(ticket_number, priority=new_priority)
    if ok:
        t = get_ticket(ticket_number)
        if t:
            _add_system_message(t.id, f"Priority changed to '{new_priority}'.")
    return ok


def close_ticket(
    ticket_number: str,
    resolution_note: str = "",
) -> bool:
    """Close a ticket with an optional resolution note."""
    _ensure()
    return change_status(ticket_number, "closed", note=resolution_note or "Ticket closed.")


def reopen_ticket(ticket_number: str, reason: str = "") -> bool:
    """Re-open a solved/closed ticket."""
    _ensure()
    t = get_ticket(ticket_number)
    if not t:
        return False
    ok = update_ticket(ticket_number, status="open", resolved_at=None)
    if ok:
        msg = f"Ticket reopened. Reason: {reason}" if reason else "Ticket reopened."
        _add_system_message(t.id, msg)
    return ok


def rate_ticket(
    ticket_number: str,
    rating: int,
    comment: str = "",
) -> bool:
    """Record customer satisfaction rating (1–5 stars)."""
    _ensure()
    if not 1 <= rating <= 5:
        return False
    return update_ticket(
        ticket_number,
        satisfaction_rating=rating,
        satisfaction_comment=comment,
    )


def merge_tickets(primary_number: str, duplicate_number: str) -> bool:
    """Merge *duplicate_number* into *primary_number*.

    Moves all messages from the duplicate ticket to the primary ticket and
    then closes the duplicate.
    """
    _ensure()
    primary = get_ticket(primary_number)
    duplicate = get_ticket(duplicate_number)
    if not primary or not duplicate:
        return False

    conn = _db()
    try:
        conn.execute(
            "UPDATE ticket_messages SET ticket_id=? WHERE ticket_id=?",
            (primary.id, duplicate.id),
        )
        conn.commit()
    finally:
        conn.close()

    _add_system_message(
        primary.id,
        f"Merged with duplicate ticket {duplicate_number}.",
    )
    change_status(duplicate_number, "closed", note=f"Merged into {primary_number}.")
    return True


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def _add_system_message(ticket_id: int, body: str) -> dict[str, Any]:
    """Internal helper: add a system-generated message to a ticket."""
    return _insert_message(
        ticket_id=ticket_id,
        author_email="system@autoearn.internal",
        author_name="AutoEarn System",
        body=body,
        author_type="system",
        is_internal=True,
    )


def _insert_message(
    ticket_id: int,
    author_email: str,
    author_name: str,
    body: str,
    author_type: str = "agent",
    is_internal: bool = False,
    attachments: Optional[list[str]] = None,
) -> dict[str, Any]:
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO ticket_messages
               (ticket_id, author_email, author_name, author_type, body,
                is_internal, attachments, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                ticket_id, author_email, author_name, author_type, body,
                1 if is_internal else 0,
                json.dumps(attachments or []),
                now,
            ),
        )
        msg_id = cur.lastrowid
        conn.commit()

        # Update first_response_at if this is the first non-customer, non-system reply
        if author_type in ("agent", "bot") and not is_internal:
            conn.execute(
                """UPDATE support_tickets
                   SET first_response_at=?, updated_at=?
                   WHERE id=? AND first_response_at IS NULL""",
                (now, now, ticket_id),
            )
            conn.commit()
        else:
            conn.execute(
                "UPDATE support_tickets SET updated_at=? WHERE id=?",
                (now, ticket_id),
            )
            conn.commit()

        row = conn.execute(
            "SELECT * FROM ticket_messages WHERE id=?", (msg_id,)
        ).fetchone()
    finally:
        conn.close()

    return dict(row)


def add_message(
    ticket_number: str,
    author_email: str,
    author_name: str,
    body: str,
    author_type: str = "agent",
    is_internal: bool = False,
    attachments: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Add a message/reply to a ticket thread."""
    _ensure()
    t = get_ticket(ticket_number)
    if not t:
        return {}
    if author_type not in AUTHOR_TYPES:
        author_type = "agent"

    # Move ticket out of 'new' when an agent first responds
    if t.status == "new" and author_type in ("agent", "bot") and not is_internal:
        update_ticket(ticket_number, status="open")

    return _insert_message(
        ticket_id=t.id,
        author_email=author_email,
        author_name=author_name,
        body=body,
        author_type=author_type,
        is_internal=is_internal,
        attachments=attachments,
    )


def get_messages(
    ticket_number: str,
    include_internal: bool = False,
) -> list[dict[str, Any]]:
    """Return all messages for a ticket."""
    _ensure()
    t = get_ticket(ticket_number)
    if not t:
        return []

    sql = "SELECT * FROM ticket_messages WHERE ticket_id=?"
    params: list[Any] = [t.id]
    if not include_internal:
        sql += " AND is_internal=0"
    sql += " ORDER BY created_at ASC"

    conn = _db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def edit_message(message_id: int, new_body: str) -> bool:
    """Edit the body of an existing message."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE ticket_messages SET body=? WHERE id=?",
            (new_body, message_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Knowledge base — articles
# ---------------------------------------------------------------------------

def create_article(
    title: str,
    body: str,
    category: str,
    tags_list: Optional[list[str]] = None,
    slug: Optional[str] = None,
) -> SupportArticle:
    """Create a new knowledge-base article in draft status."""
    _ensure()
    if not slug:
        slug = _slug_from(title)

    # Ensure slug uniqueness by appending a counter
    base_slug = slug
    counter = 1
    conn = _db()
    try:
        while conn.execute(
            "SELECT 1 FROM support_articles WHERE slug=?", (slug,)
        ).fetchone():
            slug = f"{base_slug}-{counter}"
            counter += 1

        now = _now()
        cur = conn.execute(
            """INSERT INTO support_articles
               (title, slug, category, body, tags, helpful_count,
                not_helpful_count, view_count, status, created_at, updated_at)
               VALUES (?,?,?,?,?,0,0,0,?,?,?)""",
            (
                title, slug, category, body,
                json.dumps(tags_list or []),
                "draft", now, now,
            ),
        )
        article_id = cur.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT * FROM support_articles WHERE id=?", (article_id,)
        ).fetchone()
    finally:
        conn.close()

    return SupportArticle.from_row(row)


def get_article(slug: str) -> Optional[SupportArticle]:
    """Retrieve an article by slug."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM support_articles WHERE slug=?", (slug,)
        ).fetchone()
    finally:
        conn.close()
    return SupportArticle.from_row(row) if row else None


def list_articles(
    category: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> list[SupportArticle]:
    """List articles with optional filters."""
    _ensure()
    clauses: list[str] = []
    params: list[Any] = []

    if category:
        clauses.append("category=?")
        params.append(category)
    if status:
        clauses.append("status=?")
        params.append(status)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT * FROM support_articles {where} "
        f"ORDER BY view_count DESC LIMIT ?"
    )
    params.append(limit)

    conn = _db()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [SupportArticle.from_row(r) for r in rows]


def search_articles(query: str) -> list[SupportArticle]:
    """Full-text search over article titles and bodies."""
    _ensure()
    pattern = f"%{query}%"
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT * FROM support_articles
               WHERE (title LIKE ? OR body LIKE ? OR tags LIKE ?)
               AND status='published'
               ORDER BY helpful_count DESC, view_count DESC
               LIMIT 20""",
            (pattern, pattern, pattern),
        ).fetchall()
    finally:
        conn.close()
    return [SupportArticle.from_row(r) for r in rows]


def update_article(slug: str, **fields: Any) -> bool:
    """Update article fields."""
    _ensure()
    allowed = {"title", "body", "category", "tags", "status"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False

    if "tags" in updates and isinstance(updates["tags"], list):
        updates["tags"] = json.dumps(updates["tags"])

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [slug]

    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE support_articles SET {set_clause} WHERE slug=?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def publish_article(slug: str) -> bool:
    """Set article status to 'published'."""
    _ensure()
    return update_article(slug, status="published")


def rate_article(slug: str, helpful: bool) -> bool:
    """Increment helpful or not_helpful counter for an article."""
    _ensure()
    col = "helpful_count" if helpful else "not_helpful_count"
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE support_articles SET {col}={col}+1, updated_at=? WHERE slug=?",
            (_now(), slug),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def record_article_view(slug: str) -> bool:
    """Increment view counter for an article."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE support_articles SET view_count=view_count+1 WHERE slug=?",
            (slug,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------

def create_canned_response(
    name: str,
    subject: str,
    body: str,
    category: str = "",
    tags: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Create a canned response template."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT OR REPLACE INTO canned_responses
               (name, category, subject, body, tags, use_count, created_at, updated_at)
               VALUES (?,?,?,?,?,0,?,?)""",
            (name, category, subject, body, json.dumps(tags or []), now, now),
        )
        cr_id = cur.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT * FROM canned_responses WHERE id=?", (cr_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row)


def get_canned_response(name: str) -> Optional[dict[str, Any]]:
    """Fetch a canned response by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM canned_responses WHERE name=?", (name,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_canned_responses(category: Optional[str] = None) -> list[dict[str, Any]]:
    """List canned responses, optionally filtered by category."""
    _ensure()
    conn = _db()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM canned_responses WHERE category=? ORDER BY name",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM canned_responses ORDER BY name"
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def use_canned_response(
    name: str,
    ticket_number: str,
    variables: Optional[dict[str, str]] = None,
) -> str:
    """Render a canned response body, substituting {variable} placeholders.

    Increments the ``use_count`` and returns the rendered body string.
    Returns an empty string if the canned response is not found.
    """
    _ensure()
    cr = get_canned_response(name)
    if not cr:
        return ""

    body: str = cr["body"]
    if variables:
        for key, val in variables.items():
            body = body.replace(f"{{{key}}}", val)

    # Also substitute ticket fields automatically
    t = get_ticket(ticket_number)
    if t:
        auto_vars = {
            "ticket_number": t.ticket_number,
            "customer_name": t.customer_name or "there",
            "customer_email": t.customer_email,
            "subject": t.subject,
            "status": t.status,
            "priority": t.priority,
        }
        for key, val in auto_vars.items():
            body = body.replace(f"{{{key}}}", val)

    # Increment use_count
    conn = _db()
    try:
        conn.execute(
            "UPDATE canned_responses SET use_count=use_count+1, updated_at=? WHERE name=?",
            (_now(), name),
        )
        conn.commit()
    finally:
        conn.close()

    return body


# ---------------------------------------------------------------------------
# SLA policies
# ---------------------------------------------------------------------------

def create_sla_policy(
    name: str,
    priority: str,
    first_response_hours: float,
    resolution_hours: float,
    business_hours_only: bool = True,
) -> dict[str, Any]:
    """Create or replace an SLA policy."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT OR REPLACE INTO sla_policies
               (name, priority, first_response_hours, resolution_hours,
                business_hours_only, created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                name, priority, first_response_hours, resolution_hours,
                1 if business_hours_only else 0, now,
            ),
        )
        policy_id = cur.lastrowid
        conn.commit()
        row = conn.execute(
            "SELECT * FROM sla_policies WHERE id=?", (policy_id,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row)


def list_sla_policies() -> list[dict[str, Any]]:
    """Return all SLA policies."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM sla_policies ORDER BY first_response_hours ASC"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _get_sla_for_priority(priority: str) -> tuple[float, float]:
    """Return (first_response_hours, resolution_hours) from the DB for a priority."""
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM sla_policies WHERE priority=? ORDER BY id LIMIT 1",
            (priority,),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return row["first_response_hours"], row["resolution_hours"]
    return _DEFAULT_SLA.get(priority, (8.0, 48.0))


def check_sla_breach(ticket_number: str) -> dict[str, Any]:
    """Check whether a ticket has breached its SLA targets.

    Returns a dict with:
    - ``breached_first_response`` (bool)
    - ``breached_resolution`` (bool)
    - ``time_remaining_hours`` (float, negative means overdue)
    - ``first_response_sla_hours`` (float)
    - ``resolution_sla_hours`` (float)
    """
    _ensure()
    t = get_ticket(ticket_number)
    if not t:
        return {
            "error": f"Ticket {ticket_number} not found",
            "breached_first_response": False,
            "breached_resolution": False,
            "time_remaining_hours": 0.0,
        }

    fr_sla, res_sla = _get_sla_for_priority(t.priority)
    now = _now()
    age_h = (now - t.created_at) / 3600.0

    # First response
    if t.first_response_at:
        time_to_first_h = (t.first_response_at - t.created_at) / 3600.0
        breached_fr = time_to_first_h > fr_sla
    else:
        breached_fr = age_h > fr_sla

    # Resolution
    if t.resolved_at:
        time_to_resolve_h = (t.resolved_at - t.created_at) / 3600.0
        breached_res = time_to_resolve_h > res_sla
        time_remaining = res_sla - time_to_resolve_h
    else:
        breached_res = t.is_open and age_h > res_sla
        time_remaining = res_sla - age_h

    return {
        "ticket_number": ticket_number,
        "priority": t.priority,
        "breached_first_response": breached_fr,
        "breached_resolution": breached_res,
        "time_remaining_hours": round(time_remaining, 2),
        "first_response_sla_hours": fr_sla,
        "resolution_sla_hours": res_sla,
        "age_hours": round(age_h, 2),
    }


def overdue_tickets(priority: Optional[str] = None) -> list[SupportTicket]:
    """Return all open tickets that have breached their resolution SLA."""
    _ensure()
    all_open = list_tickets(limit=1000)
    result: list[SupportTicket] = []
    for t in all_open:
        if not t.is_open:
            continue
        if priority and t.priority != priority:
            continue
        _, res_sla = _get_sla_for_priority(t.priority)
        if t.age_hours > res_sla:
            result.append(t)
    return result


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def ticket_volume(days: int = 30) -> dict[str, Any]:
    """Return daily ticket counts grouped by status for the last *days* days."""
    _ensure()
    cutoff = _now() - days * 86400
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT
                   date(created_at, 'unixepoch') AS day,
                   status,
                   COUNT(*) AS cnt
               FROM support_tickets
               WHERE created_at >= ?
               GROUP BY day, status
               ORDER BY day ASC""",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    result: dict[str, dict[str, int]] = {}
    for r in rows:
        day = r["day"]
        if day not in result:
            result[day] = {}
        result[day][r["status"]] = r["cnt"]
    return result


def avg_resolution_time(
    category: Optional[str] = None,
    days: int = 30,
) -> float:
    """Average resolution time in hours over the last *days* days."""
    _ensure()
    cutoff = _now() - days * 86400
    params: list[Any] = [cutoff]
    cat_clause = ""
    if category:
        cat_clause = "AND category=?"
        params.append(category)

    conn = _db()
    try:
        row = conn.execute(
            f"""SELECT AVG((resolved_at - created_at) / 3600.0) AS avg_h
                FROM support_tickets
                WHERE resolved_at IS NOT NULL
                  AND created_at >= ?
                  {cat_clause}""",
            params,
        ).fetchone()
    finally:
        conn.close()
    return round(row["avg_h"] or 0.0, 2)


def first_response_time(days: int = 30) -> float:
    """Average first-response time in hours over the last *days* days."""
    _ensure()
    cutoff = _now() - days * 86400
    conn = _db()
    try:
        row = conn.execute(
            """SELECT AVG((first_response_at - created_at) / 3600.0) AS avg_h
               FROM support_tickets
               WHERE first_response_at IS NOT NULL
                 AND created_at >= ?""",
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    return round(row["avg_h"] or 0.0, 2)


def satisfaction_score(days: int = 30) -> float:
    """Average CSAT rating (1–5) over the last *days* days."""
    _ensure()
    cutoff = _now() - days * 86400
    conn = _db()
    try:
        row = conn.execute(
            """SELECT AVG(CAST(satisfaction_rating AS REAL)) AS avg_r
               FROM support_tickets
               WHERE satisfaction_rating IS NOT NULL
                 AND created_at >= ?""",
            (cutoff,),
        ).fetchone()
    finally:
        conn.close()
    return round(row["avg_r"] or 0.0, 2)


def top_categories(days: int = 30) -> list[dict[str, Any]]:
    """Return ticket counts per category, most common first."""
    _ensure()
    cutoff = _now() - days * 86400
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT category, COUNT(*) AS cnt
               FROM support_tickets
               WHERE created_at >= ?
               GROUP BY category
               ORDER BY cnt DESC""",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [{"category": r["category"], "count": r["cnt"]} for r in rows]


def agent_workload() -> list[dict[str, Any]]:
    """Return open ticket count per assigned agent."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT assigned_to, COUNT(*) AS open_count
               FROM support_tickets
               WHERE status NOT IN ('solved','closed')
                 AND assigned_to != ''
               GROUP BY assigned_to
               ORDER BY open_count DESC""",
        ).fetchall()
    finally:
        conn.close()
    return [{"agent": r["assigned_to"], "open_tickets": r["open_count"]} for r in rows]


def support_summary() -> dict[str, Any]:
    """High-level dashboard summary for the support desk."""
    _ensure()
    conn = _db()
    try:
        open_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM support_tickets WHERE status NOT IN ('solved','closed')"
        ).fetchone()
        sla_count = len(overdue_tickets())
    finally:
        conn.close()

    return {
        "open_tickets": open_row["cnt"] if open_row else 0,
        "avg_first_response_hours_30d": first_response_time(30),
        "avg_resolution_hours_30d": avg_resolution_time(days=30),
        "csat_30d": satisfaction_score(30),
        "top_categories_30d": top_categories(30)[:5],
        "sla_breaches": sla_count,
        "agent_workload": agent_workload(),
    }


# ---------------------------------------------------------------------------
# Tool-decorated functions (AI agent interface)
# ---------------------------------------------------------------------------

@tool(
    "cs_create_ticket",
    "Create a new customer support ticket. Returns ticket details as JSON.",
)
def cs_create_ticket_tool(
    agent: str,
    customer_email: str,
    subject: str,
    description: str,
    priority: str = "normal",
    category: str = "general",
    channel: str = "web_form",
    customer_name: str = "",
    **_: Any,
) -> str:
    try:
        ticket = create_ticket(
            customer_email=customer_email,
            subject=subject,
            description=description,
            customer_name=customer_name,
            priority=priority,
            category=category,
            channel=channel,
        )
        return json.dumps({"ok": True, "ticket": ticket.to_dict()})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_get_ticket",
    "Retrieve a support ticket by its ticket number (e.g. TKT-20240614-0001).",
)
def cs_get_ticket_tool(agent: str, ticket_number: str, **_: Any) -> str:
    try:
        ticket = get_ticket(ticket_number)
        if not ticket:
            return json.dumps({"ok": False, "error": f"Ticket {ticket_number} not found"})
        messages = get_messages(ticket_number, include_internal=False)
        result = ticket.to_dict()
        result["messages"] = messages
        return json.dumps({"ok": True, "ticket": result})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_list_tickets",
    "List support tickets with optional filters for status, priority, and limit.",
)
def cs_list_tickets_tool(
    agent: str,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    category: Optional[str] = None,
    assigned_to: Optional[str] = None,
    customer_email: Optional[str] = None,
    limit: int = 50,
    **_: Any,
) -> str:
    try:
        tickets = list_tickets(
            status=status,
            priority=priority,
            category=category,
            assigned_to=assigned_to,
            customer_email=customer_email,
            limit=limit,
        )
        return json.dumps({
            "ok": True,
            "count": len(tickets),
            "tickets": [t.to_dict() for t in tickets],
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_reply_ticket",
    "Add an agent reply to a support ticket. Moves ticket to 'open' if needed.",
)
def cs_reply_ticket_tool(
    agent: str,
    ticket_number: str,
    agent_email: str,
    body: str,
    author_name: str = "Support Agent",
    is_internal: bool = False,
    **_: Any,
) -> str:
    try:
        msg = add_message(
            ticket_number=ticket_number,
            author_email=agent_email,
            author_name=author_name,
            body=body,
            author_type="agent",
            is_internal=is_internal,
        )
        if not msg:
            return json.dumps({"ok": False, "error": f"Ticket {ticket_number} not found"})
        return json.dumps({"ok": True, "message": msg})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_close_ticket",
    "Close a support ticket with an optional resolution note.",
)
def cs_close_ticket_tool(
    agent: str,
    ticket_number: str,
    resolution_note: str = "",
    **_: Any,
) -> str:
    try:
        ok = close_ticket(ticket_number, resolution_note=resolution_note)
        if not ok:
            return json.dumps({"ok": False, "error": f"Could not close ticket {ticket_number}"})
        ticket = get_ticket(ticket_number)
        return json.dumps({"ok": True, "ticket": ticket.to_dict() if ticket else {}})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_search_kb",
    "Search the knowledge base for articles matching a query string.",
)
def cs_search_kb_tool(agent: str, query: str, **_: Any) -> str:
    try:
        articles = search_articles(query)
        return json.dumps({
            "ok": True,
            "count": len(articles),
            "articles": [a.to_dict() for a in articles],
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_support_summary",
    "Return a high-level dashboard summary of the support desk (open tickets, CSAT, SLA breaches, etc.).",
)
def cs_support_summary_tool(agent: str, **_: Any) -> str:
    try:
        summary = support_summary()
        return json.dumps({"ok": True, "summary": summary})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_create_article",
    "Create a new knowledge-base article (draft). Returns article details as JSON.",
)
def cs_create_article_tool(
    agent: str,
    title: str,
    body: str,
    category: str,
    tags: Optional[str] = None,
    slug: Optional[str] = None,
    **_: Any,
) -> str:
    try:
        tags_list: list[str] = []
        if tags:
            tags_list = [t.strip() for t in tags.split(",") if t.strip()]
        article = create_article(
            title=title,
            body=body,
            category=category,
            tags_list=tags_list,
            slug=slug,
        )
        return json.dumps({"ok": True, "article": article.to_dict()})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_csat",
    "Return the average customer satisfaction score (CSAT, 1–5 stars) for the last N days.",
)
def cs_csat_tool(agent: str, days: int = 30, **_: Any) -> str:
    try:
        score = satisfaction_score(days)
        cutoff = _now() - days * 86400
        conn = _db()
        try:
            row = conn.execute(
                """SELECT COUNT(*) AS cnt
                   FROM support_tickets
                   WHERE satisfaction_rating IS NOT NULL AND created_at >= ?""",
                (cutoff,),
            ).fetchone()
            rated = row["cnt"] if row else 0
        finally:
            conn.close()
        return json.dumps({
            "ok": True,
            "csat": score,
            "rated_tickets": rated,
            "days": days,
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})


@tool(
    "cs_overdue_tickets",
    "Return all open tickets that have breached their SLA resolution target.",
)
def cs_overdue_tickets_tool(
    agent: str,
    priority: Optional[str] = None,
    **_: Any,
) -> str:
    try:
        tickets = overdue_tickets(priority=priority)
        return json.dumps({
            "ok": True,
            "count": len(tickets),
            "tickets": [t.to_dict() for t in tickets],
        })
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(exc)})
