"""
Email Marketing — sequences, automations, broadcasts, segmentation, A/B tests.

Manages email campaigns with full lifecycle: draft → scheduled → sent → tracked.
Stores subscriber segments, drip sequences, automation triggers, and deliverability
metrics. Integrates with the newsletter module for low-level send operations.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EMAIL_TYPES = [
    "broadcast",       # one-time send to a segment
    "sequence",        # ordered drip series
    "trigger",         # sent on event (signup, purchase, etc.)
    "transactional",   # order confirmation, receipt
    "re_engagement",   # win-back cold subscribers
    "nurture",         # educational series
    "promotional",     # time-limited offer
    "weekly_digest",   # curated content digest
    "announcement",    # product update / launch
    "welcome",         # first message after signup
]

TRIGGER_EVENTS = [
    "signup",
    "confirmed",
    "purchase",
    "first_purchase",
    "cart_abandoned",
    "page_viewed",
    "link_clicked",
    "email_opened",
    "tag_added",
    "segment_entered",
    "date_anniversary",
    "custom",
]

SEGMENT_CONDITIONS = [
    "all_subscribers",
    "tag_matches",
    "purchased_product",
    "opened_campaign",
    "clicked_campaign",
    "joined_after",
    "joined_before",
    "country_is",
    "custom_field",
]

# ---------------------------------------------------------------------------
# Schema
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
            CREATE TABLE IF NOT EXISTS email_segments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                condition   TEXT NOT NULL DEFAULT 'all_subscribers',
                params      TEXT NOT NULL DEFAULT '{}',
                is_dynamic  INTEGER NOT NULL DEFAULT 1,
                member_count INTEGER NOT NULL DEFAULT 0,
                last_built  TEXT,
                created_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_campaigns (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                email_type      TEXT NOT NULL DEFAULT 'broadcast',
                subject         TEXT NOT NULL,
                preview_text    TEXT,
                from_name       TEXT NOT NULL DEFAULT 'AutoEarn',
                from_email      TEXT NOT NULL DEFAULT 'noreply@autoearn.ai',
                reply_to        TEXT,
                body_html       TEXT,
                body_text       TEXT,
                segment_id      INTEGER REFERENCES email_segments(id),
                status          TEXT NOT NULL DEFAULT 'draft',
                scheduled_at    TEXT,
                sent_at         TEXT,
                total_sent      INTEGER NOT NULL DEFAULT 0,
                total_opens     INTEGER NOT NULL DEFAULT 0,
                unique_opens    INTEGER NOT NULL DEFAULT 0,
                total_clicks    INTEGER NOT NULL DEFAULT 0,
                unique_clicks   INTEGER NOT NULL DEFAULT 0,
                unsubscribes    INTEGER NOT NULL DEFAULT 0,
                bounces         INTEGER NOT NULL DEFAULT 0,
                spam_reports    INTEGER NOT NULL DEFAULT 0,
                revenue_usd     REAL NOT NULL DEFAULT 0.0,
                ab_variant      TEXT,
                ab_parent_id    INTEGER,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL,
                metadata        TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS email_sequences (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                description     TEXT,
                trigger_event   TEXT NOT NULL DEFAULT 'signup',
                trigger_params  TEXT NOT NULL DEFAULT '{}',
                is_active       INTEGER NOT NULL DEFAULT 1,
                delay_hours     INTEGER NOT NULL DEFAULT 24,
                max_emails      INTEGER NOT NULL DEFAULT 0,
                goal            TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sequence_emails (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id     INTEGER NOT NULL REFERENCES email_sequences(id) ON DELETE CASCADE,
                step_number     INTEGER NOT NULL,
                subject         TEXT NOT NULL,
                preview_text    TEXT,
                body_html       TEXT,
                body_text       TEXT,
                delay_hours     INTEGER NOT NULL DEFAULT 24,
                send_condition  TEXT DEFAULT 'always',
                total_sent      INTEGER NOT NULL DEFAULT 0,
                total_opens     INTEGER NOT NULL DEFAULT 0,
                total_clicks    INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sequence_enrollments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_id     INTEGER NOT NULL REFERENCES email_sequences(id) ON DELETE CASCADE,
                subscriber_email TEXT NOT NULL,
                current_step    INTEGER NOT NULL DEFAULT 1,
                status          TEXT NOT NULL DEFAULT 'active',
                enrolled_at     TEXT NOT NULL,
                next_send_at    TEXT,
                completed_at    TEXT,
                unsubscribed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS automation_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                trigger_event   TEXT NOT NULL,
                trigger_params  TEXT NOT NULL DEFAULT '{}',
                action_type     TEXT NOT NULL DEFAULT 'send_email',
                action_params   TEXT NOT NULL DEFAULT '{}',
                delay_minutes   INTEGER NOT NULL DEFAULT 0,
                is_active       INTEGER NOT NULL DEFAULT 1,
                run_count       INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id     INTEGER REFERENCES email_campaigns(id),
                sequence_id     INTEGER REFERENCES email_sequences(id),
                email_step      INTEGER,
                subscriber_email TEXT,
                event_type      TEXT NOT NULL,
                ip_hash         TEXT,
                user_agent      TEXT,
                link_url        TEXT,
                occurred_at     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS email_ab_tests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                campaign_a_id   INTEGER NOT NULL REFERENCES email_campaigns(id),
                campaign_b_id   INTEGER NOT NULL REFERENCES email_campaigns(id),
                test_metric     TEXT NOT NULL DEFAULT 'open_rate',
                traffic_split   REAL NOT NULL DEFAULT 0.5,
                status          TEXT NOT NULL DEFAULT 'running',
                winner_id       INTEGER REFERENCES email_campaigns(id),
                started_at      TEXT NOT NULL,
                ended_at        TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_email_events_campaign ON email_events(campaign_id);
            CREATE INDEX IF NOT EXISTS idx_email_events_type     ON email_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_seq_enrollments_seq   ON sequence_enrollments(sequence_id);
            CREATE INDEX IF NOT EXISTS idx_seq_enrollments_email ON sequence_enrollments(subscriber_email);
            CREATE INDEX IF NOT EXISTS idx_seq_emails_seq        ON sequence_emails(sequence_id);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EmailSegment:
    name: str
    description: str = ""
    condition: str = "all_subscribers"
    params: Dict[str, Any] = field(default_factory=dict)
    is_dynamic: bool = True
    member_count: int = 0
    last_built: Optional[str] = None
    created_at: str = ""
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "condition": self.condition,
            "params": self.params,
            "is_dynamic": self.is_dynamic,
            "member_count": self.member_count,
            "last_built": self.last_built,
            "created_at": self.created_at,
        }


@dataclass
class EmailCampaign:
    name: str
    subject: str
    email_type: str = "broadcast"
    preview_text: str = ""
    from_name: str = "AutoEarn"
    from_email: str = "noreply@autoearn.ai"
    reply_to: str = ""
    body_html: str = ""
    body_text: str = ""
    segment_id: Optional[int] = None
    status: str = "draft"
    scheduled_at: Optional[str] = None
    sent_at: Optional[str] = None
    total_sent: int = 0
    total_opens: int = 0
    unique_opens: int = 0
    total_clicks: int = 0
    unique_clicks: int = 0
    unsubscribes: int = 0
    bounces: int = 0
    spam_reports: int = 0
    revenue_usd: float = 0.0
    ab_variant: Optional[str] = None
    ab_parent_id: Optional[int] = None
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    @property
    def open_rate(self) -> float:
        return self.unique_opens / max(self.total_sent, 1)

    @property
    def click_rate(self) -> float:
        return self.unique_clicks / max(self.total_sent, 1)

    @property
    def click_to_open_rate(self) -> float:
        return self.unique_clicks / max(self.unique_opens, 1)

    @property
    def unsubscribe_rate(self) -> float:
        return self.unsubscribes / max(self.total_sent, 1)

    @property
    def bounce_rate(self) -> float:
        return self.bounces / max(self.total_sent, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "email_type": self.email_type,
            "subject": self.subject,
            "preview_text": self.preview_text,
            "from_name": self.from_name,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "sent_at": self.sent_at,
            "total_sent": self.total_sent,
            "total_opens": self.total_opens,
            "unique_opens": self.unique_opens,
            "open_rate": round(self.open_rate * 100, 2),
            "total_clicks": self.total_clicks,
            "unique_clicks": self.unique_clicks,
            "click_rate": round(self.click_rate * 100, 2),
            "click_to_open_rate": round(self.click_to_open_rate * 100, 2),
            "unsubscribes": self.unsubscribes,
            "unsubscribe_rate": round(self.unsubscribe_rate * 100, 2),
            "bounces": self.bounces,
            "bounce_rate": round(self.bounce_rate * 100, 2),
            "revenue_usd": self.revenue_usd,
            "created_at": self.created_at,
        }


@dataclass
class EmailSequence:
    name: str
    trigger_event: str = "signup"
    description: str = ""
    trigger_params: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = True
    delay_hours: int = 24
    max_emails: int = 0
    goal: str = ""
    created_at: str = ""
    updated_at: str = ""
    id: Optional[int] = None
    steps: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "trigger_event": self.trigger_event,
            "description": self.description,
            "is_active": self.is_active,
            "delay_hours": self.delay_hours,
            "goal": self.goal,
            "step_count": len(self.steps),
            "steps": self.steps,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _segment_from_row(row: sqlite3.Row) -> EmailSegment:
    return EmailSegment(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        condition=row["condition"],
        params=json.loads(row["params"] or "{}"),
        is_dynamic=bool(row["is_dynamic"]),
        member_count=row["member_count"] or 0,
        last_built=row["last_built"],
        created_at=row["created_at"],
    )


def _campaign_from_row(row: sqlite3.Row) -> EmailCampaign:
    return EmailCampaign(
        id=row["id"],
        name=row["name"],
        email_type=row["email_type"],
        subject=row["subject"],
        preview_text=row["preview_text"] or "",
        from_name=row["from_name"],
        from_email=row["from_email"],
        reply_to=row["reply_to"] or "",
        body_html=row["body_html"] or "",
        body_text=row["body_text"] or "",
        segment_id=row["segment_id"],
        status=row["status"],
        scheduled_at=row["scheduled_at"],
        sent_at=row["sent_at"],
        total_sent=row["total_sent"] or 0,
        total_opens=row["total_opens"] or 0,
        unique_opens=row["unique_opens"] or 0,
        total_clicks=row["total_clicks"] or 0,
        unique_clicks=row["unique_clicks"] or 0,
        unsubscribes=row["unsubscribes"] or 0,
        bounces=row["bounces"] or 0,
        spam_reports=row["spam_reports"] or 0,
        revenue_usd=row["revenue_usd"] or 0.0,
        ab_variant=row["ab_variant"],
        ab_parent_id=row["ab_parent_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _sequence_from_row(row: sqlite3.Row, steps: Optional[List[Dict]] = None) -> EmailSequence:
    return EmailSequence(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        trigger_event=row["trigger_event"],
        trigger_params=json.loads(row["trigger_params"] or "{}"),
        is_active=bool(row["is_active"]),
        delay_hours=row["delay_hours"],
        max_emails=row["max_emails"],
        goal=row["goal"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        steps=steps or [],
    )


# ---------------------------------------------------------------------------
# Segment CRUD
# ---------------------------------------------------------------------------

def create_segment(
    name: str,
    description: str = "",
    condition: str = "all_subscribers",
    conditions_json: str = "",
    params: Optional[Dict[str, Any]] = None,
    is_dynamic: bool = True,
) -> Dict[str, Any]:
    """Create a subscriber segment. Returns a dict."""
    _ensure()
    if conditions_json and not params:
        try:
            params = json.loads(conditions_json)
        except Exception:
            pass
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO email_segments
               (name, description, condition, params, is_dynamic, created_at)
               VALUES (?,?,?,?,?,?)""",
            (name, description, condition, json.dumps(params or {}), int(is_dynamic), now),
        )
        conn.commit()
        seg = EmailSegment(
            id=cur.lastrowid,
            name=name,
            description=description,
            condition=condition,
            params=params or {},
            is_dynamic=is_dynamic,
            created_at=now,
        )
        return seg.to_dict()
    except sqlite3.IntegrityError:
        raise ValueError(f"Segment '{name}' already exists")
    finally:
        conn.close()


def get_segment(name: str) -> Optional[EmailSegment]:
    """Fetch a segment by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM email_segments WHERE name = ?", (name,)).fetchone()
        return _segment_from_row(row) if row else None
    finally:
        conn.close()


def list_segments() -> List[Dict[str, Any]]:
    """List all segments as dicts."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM email_segments ORDER BY name").fetchall()
        return [_segment_from_row(r).to_dict() for r in rows]
    finally:
        conn.close()


def update_segment_count(segment_id: int, count: int) -> None:
    """Update the member count and last_built timestamp for a segment."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE email_segments SET member_count = ?, last_built = ? WHERE id = ?",
            (count, datetime.utcnow().isoformat(), segment_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Campaign CRUD
# ---------------------------------------------------------------------------

def create_campaign(
    name: str,
    subject: str,
    email_type: str = "broadcast",
    preview_text: str = "",
    from_name: str = "AutoEarn",
    from_email: str = "noreply@autoearn.ai",
    reply_to: str = "",
    body_html: str = "",
    body_text: str = "",
    segment_id: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> EmailCampaign:
    """Create a new email campaign."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO email_campaigns
               (name, email_type, subject, preview_text, from_name, from_email,
                reply_to, body_html, body_text, segment_id, created_at, updated_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, email_type, subject, preview_text, from_name, from_email,
                reply_to, body_html, body_text, segment_id, now, now,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return EmailCampaign(
            id=cur.lastrowid,
            name=name,
            email_type=email_type,
            subject=subject,
            preview_text=preview_text,
            from_name=from_name,
            from_email=from_email,
            reply_to=reply_to,
            body_html=body_html,
            body_text=body_text,
            segment_id=segment_id,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Campaign '{name}' already exists")
    finally:
        conn.close()


def get_campaign(name: str) -> Optional[EmailCampaign]:
    """Fetch a campaign by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM email_campaigns WHERE name = ?", (name,)).fetchone()
        return _campaign_from_row(row) if row else None
    finally:
        conn.close()


def get_campaign_by_id(campaign_id: int) -> Optional[EmailCampaign]:
    """Fetch a campaign by ID."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)).fetchone()
        return _campaign_from_row(row) if row else None
    finally:
        conn.close()


def list_campaigns(
    status: Optional[str] = None,
    email_type: Optional[str] = None,
    limit: int = 50,
) -> List[EmailCampaign]:
    """List campaigns with optional filters."""
    _ensure()
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if email_type:
        clauses.append("email_type = ?")
        params.append(email_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM email_campaigns {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [_campaign_from_row(r) for r in rows]
    finally:
        conn.close()


def update_campaign(campaign_id: int, **kwargs) -> bool:
    """Update campaign fields."""
    _ensure()
    allowed = {
        "subject", "preview_text", "from_name", "from_email", "reply_to",
        "body_html", "body_text", "segment_id", "status", "scheduled_at", "metadata",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    if "metadata" in updates:
        updates["metadata"] = json.dumps(updates["metadata"])
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE email_campaigns SET {set_clause} WHERE id = ?",
            (*updates.values(), campaign_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def schedule_campaign(campaign_id: int, send_at: str) -> bool:
    """Schedule a campaign for delivery."""
    return update_campaign(campaign_id, status="scheduled", scheduled_at=send_at)


def mark_campaign_sent(campaign_id: int, total_sent: int = 0) -> bool:
    """Mark a campaign as sent with recipient count."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """UPDATE email_campaigns
               SET status = 'sent', sent_at = ?, total_sent = ?, updated_at = ?
               WHERE id = ?""",
            (now, total_sent, now, campaign_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_campaign(campaign_id: int) -> bool:
    """Delete a campaign (only drafts)."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM email_campaigns WHERE id = ? AND status = 'draft'",
            (campaign_id,),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Campaign event tracking
# ---------------------------------------------------------------------------

def record_event(
    event_type: str,
    subscriber_email: str,
    campaign_id: Optional[int] = None,
    sequence_id: Optional[int] = None,
    email_step: Optional[int] = None,
    link_url: str = "",
    ip_hash: str = "",
    user_agent: str = "",
) -> None:
    """Record an email event (open, click, unsubscribe, bounce, spam_report)."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO email_events
               (campaign_id, sequence_id, email_step, subscriber_email,
                event_type, ip_hash, user_agent, link_url, occurred_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (campaign_id, sequence_id, email_step, subscriber_email,
             event_type, ip_hash, user_agent[:500], link_url[:2000], now),
        )
        # Update campaign counters
        if campaign_id:
            col_map = {
                "open": ("total_opens",),
                "unique_open": ("total_opens", "unique_opens"),
                "click": ("total_clicks",),
                "unique_click": ("total_clicks", "unique_clicks"),
                "unsubscribe": ("unsubscribes",),
                "bounce": ("bounces",),
                "spam_report": ("spam_reports",),
            }
            cols = col_map.get(event_type, ())
            for col in cols:
                conn.execute(
                    f"UPDATE email_campaigns SET {col} = {col} + 1 WHERE id = ?",
                    (campaign_id,),
                )
        conn.commit()
    finally:
        conn.close()


def attribute_revenue(campaign_id: int, amount: float) -> bool:
    """Attribute revenue to a campaign."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE email_campaigns SET revenue_usd = revenue_usd + ? WHERE id = ?",
            (amount, campaign_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sequences
# ---------------------------------------------------------------------------

def create_sequence(
    name: str,
    trigger_event: str = "signup",
    description: str = "",
    trigger_params: Optional[Dict[str, Any]] = None,
    delay_hours: int = 24,
    goal: str = "",
) -> EmailSequence:
    """Create an email drip sequence."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO email_sequences
               (name, description, trigger_event, trigger_params, delay_hours, goal, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, description, trigger_event, json.dumps(trigger_params or {}),
             delay_hours, goal, now, now),
        )
        conn.commit()
        return EmailSequence(
            id=cur.lastrowid,
            name=name,
            description=description,
            trigger_event=trigger_event,
            trigger_params=trigger_params or {},
            delay_hours=delay_hours,
            goal=goal,
            created_at=now,
            updated_at=now,
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Sequence '{name}' already exists")
    finally:
        conn.close()


def get_sequence(name: str) -> Optional[EmailSequence]:
    """Fetch a sequence by name, including all steps."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM email_sequences WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return None
        step_rows = conn.execute(
            "SELECT * FROM sequence_emails WHERE sequence_id = ? ORDER BY step_number",
            (row["id"],),
        ).fetchall()
        steps = [dict(s) for s in step_rows]
        return _sequence_from_row(row, steps)
    finally:
        conn.close()


def list_sequences(active_only: bool = True) -> List[EmailSequence]:
    """List all sequences."""
    _ensure()
    where = "WHERE is_active = 1" if active_only else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM email_sequences {where} ORDER BY name"
        ).fetchall()
        seqs = []
        for r in rows:
            step_rows = conn.execute(
                "SELECT * FROM sequence_emails WHERE sequence_id = ? ORDER BY step_number",
                (r["id"],),
            ).fetchall()
            seqs.append(_sequence_from_row(r, [dict(s) for s in step_rows]))
        return seqs
    finally:
        conn.close()


def add_sequence_email(
    sequence_name,
    step_number: int = 1,
    subject: str = "",
    body_html: str = "",
    body_text: str = "",
    body: str = "",
    preview_text: str = "",
    delay_hours: int = 24,
    send_condition: str = "always",
) -> Dict[str, Any]:
    """Add an email step to a sequence. Accepts sequence name or id."""
    _ensure()
    body_html = body_html or body
    seq = get_sequence(sequence_name) if isinstance(sequence_name, str) else None
    if seq is None:
        # Try lookup by id
        conn = _db()
        try:
            row = conn.execute("SELECT * FROM email_sequences WHERE id=?", (sequence_name,)).fetchone()
            if row:
                seq = _sequence_from_row(row)
        finally:
            conn.close()
    if not seq:
        raise ValueError(f"Sequence '{sequence_name}' not found")
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO sequence_emails
               (sequence_id, step_number, subject, preview_text, body_html,
                body_text, delay_hours, send_condition, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (seq.id, step_number, subject, preview_text, body_html,
             body_text, delay_hours, send_condition, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "sequence": sequence_name,
            "step_number": step_number,
            "subject": subject,
            "delay_hours": delay_hours,
        }
    finally:
        conn.close()


def enroll_subscriber(
    sequence_name,
    subscriber_email: str,
    start_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Enroll a subscriber in a sequence. Accepts sequence name or id."""
    _ensure()
    seq = get_sequence(sequence_name) if isinstance(sequence_name, str) else None
    if seq is None:
        conn = _db()
        try:
            row = conn.execute("SELECT * FROM email_sequences WHERE id=?", (sequence_name,)).fetchone()
            if row:
                seq = _sequence_from_row(row)
        finally:
            conn.close()
    if not seq:
        raise ValueError(f"Sequence '{sequence_name}' not found")
    if not seq.is_active:
        raise ValueError(f"Sequence '{sequence_name}' is not active")

    now = datetime.utcnow().isoformat()
    next_send = start_at or (datetime.utcnow() + timedelta(hours=seq.delay_hours)).isoformat()

    conn = _db()
    try:
        # Check if already enrolled
        existing = conn.execute(
            """SELECT id FROM sequence_enrollments
               WHERE sequence_id = ? AND subscriber_email = ? AND status = 'active'""",
            (seq.id, subscriber_email),
        ).fetchone()
        if existing:
            return {"status": "already_enrolled", "email": subscriber_email}

        cur = conn.execute(
            """INSERT INTO sequence_enrollments
               (sequence_id, subscriber_email, current_step, status, enrolled_at, next_send_at)
               VALUES (?,?,?,?,?,?)""",
            (seq.id, subscriber_email, 1, "active", now, next_send),
        )
        conn.commit()
        return {
            "enrollment_id": cur.lastrowid,
            "sequence": sequence_name,
            "email": subscriber_email,
            "next_send_at": next_send,
        }
    finally:
        conn.close()


def get_due_sequence_emails(
    as_of: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return sequence enrollments whose next_send_at has passed."""
    _ensure()
    now = as_of or datetime.utcnow().isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT
                   e.id as enrollment_id,
                   e.sequence_id,
                   e.subscriber_email,
                   e.current_step,
                   s.name as sequence_name,
                   se.id as email_id,
                   se.subject,
                   se.body_html,
                   se.body_text,
                   se.delay_hours,
                   e.next_send_at
               FROM sequence_enrollments e
               JOIN email_sequences s ON s.id = e.sequence_id
               LEFT JOIN sequence_emails se
                   ON se.sequence_id = e.sequence_id AND se.step_number = e.current_step
               WHERE e.status = 'active'
                 AND (e.next_send_at IS NULL OR e.next_send_at <= ?)
               ORDER BY e.next_send_at""",
            (now,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def advance_enrollment(enrollment_id: int) -> Dict[str, Any]:
    """Move an enrollment to the next step or mark complete."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM sequence_enrollments WHERE id = ?", (enrollment_id,)
        ).fetchone()
        if not row:
            return {"error": "enrollment not found"}

        next_step = row["current_step"] + 1
        total_steps = conn.execute(
            "SELECT COUNT(*) as cnt FROM sequence_emails WHERE sequence_id = ?",
            (row["sequence_id"],),
        ).fetchone()["cnt"]

        if next_step > total_steps:
            conn.execute(
                """UPDATE sequence_enrollments
                   SET status = 'completed', completed_at = ? WHERE id = ?""",
                (datetime.utcnow().isoformat(), enrollment_id),
            )
            conn.commit()
            return {"status": "completed", "enrollment_id": enrollment_id}

        delay_row = conn.execute(
            "SELECT delay_hours FROM sequence_emails WHERE sequence_id = ? AND step_number = ?",
            (row["sequence_id"], next_step),
        ).fetchone()
        delay = delay_row["delay_hours"] if delay_row else 24
        next_send = (datetime.utcnow() + timedelta(hours=delay)).isoformat()

        conn.execute(
            """UPDATE sequence_enrollments
               SET current_step = ?, next_send_at = ? WHERE id = ?""",
            (next_step, next_send, enrollment_id),
        )
        conn.commit()
        return {
            "status": "advanced",
            "enrollment_id": enrollment_id,
            "next_step": next_step,
            "next_send_at": next_send,
        }
    finally:
        conn.close()


def unenroll_subscriber(sequence_name, subscriber_email: str) -> bool:
    """Remove a subscriber from a sequence. Accepts name or id."""
    _ensure()
    seq = get_sequence(sequence_name) if isinstance(sequence_name, str) else None
    if seq is None:
        conn2 = _db()
        try:
            row = conn2.execute("SELECT * FROM email_sequences WHERE id=?", (sequence_name,)).fetchone()
            if row:
                seq = _sequence_from_row(row)
        finally:
            conn2.close()
    if not seq:
        return False
    conn = _db()
    try:
        cur = conn.execute(
            """UPDATE sequence_enrollments
               SET status = 'unsubscribed', unsubscribed_at = ?
               WHERE sequence_id = ? AND subscriber_email = ? AND status = 'active'""",
            (datetime.utcnow().isoformat(), seq.id, subscriber_email),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Automation rules
# ---------------------------------------------------------------------------

def create_automation(
    name: str,
    trigger_event: str,
    action_type: str = "send_email",
    action_params: Optional[Dict[str, Any]] = None,
    trigger_params: Optional[Dict[str, Any]] = None,
    delay_minutes: int = 0,
) -> Dict[str, Any]:
    """Create an automation rule."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO automation_rules
               (name, trigger_event, trigger_params, action_type, action_params,
                delay_minutes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, trigger_event, json.dumps(trigger_params or {}),
             action_type, json.dumps(action_params or {}), delay_minutes, now, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "name": name,
            "trigger_event": trigger_event,
            "action_type": action_type,
            "delay_minutes": delay_minutes,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Automation '{name}' already exists")
    finally:
        conn.close()


def list_automations(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all automation rules."""
    _ensure()
    where = "WHERE is_active = 1" if active_only else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM automation_rules {where} ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def fire_automation(trigger_event: str, subscriber_email: str = "", context: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Find and queue automations matching a trigger event."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM automation_rules WHERE trigger_event = ? AND is_active = 1",
            (trigger_event,),
        ).fetchall()
        fired = []
        for r in rows:
            conn.execute(
                "UPDATE automation_rules SET run_count = run_count + 1 WHERE id = ?",
                (r["id"],),
            )
            fired.append({
                "automation_id": r["id"],
                "name": r["name"],
                "action_type": r["action_type"],
                "action_params": json.loads(r["action_params"] or "{}"),
                "delay_minutes": r["delay_minutes"],
                "context": context or {},
            })
        conn.commit()
        return fired
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A/B tests
# ---------------------------------------------------------------------------

def create_ab_test(
    name: str,
    campaign_a_id: int,
    campaign_b_id: int,
    test_metric: str = "open_rate",
    traffic_split: float = 0.5,
) -> Dict[str, Any]:
    """Create an A/B test comparing two campaigns."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO email_ab_tests
               (name, campaign_a_id, campaign_b_id, test_metric, traffic_split, started_at)
               VALUES (?,?,?,?,?,?)""",
            (name, campaign_a_id, campaign_b_id, test_metric, traffic_split, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "name": name,
            "campaign_a_id": campaign_a_id,
            "campaign_b_id": campaign_b_id,
            "test_metric": test_metric,
            "status": "running",
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"A/B test '{name}' already exists")
    finally:
        conn.close()


def analyze_ab_test(test_name: str) -> Dict[str, Any]:
    """Determine the winner of an email A/B test."""
    _ensure()
    conn = _db()
    try:
        test = conn.execute(
            "SELECT * FROM email_ab_tests WHERE name = ?", (test_name,)
        ).fetchone()
        if not test:
            return {"error": "test not found"}

        a = conn.execute(
            "SELECT * FROM email_campaigns WHERE id = ?", (test["campaign_a_id"],)
        ).fetchone()
        b = conn.execute(
            "SELECT * FROM email_campaigns WHERE id = ?", (test["campaign_b_id"],)
        ).fetchone()

        metric = test["test_metric"]
        a_val = _get_metric(dict(a) if a else {}, metric)
        b_val = _get_metric(dict(b) if b else {}, metric)

        winner = "a" if a_val >= b_val else "b"
        lift = abs(a_val - b_val) / max(min(a_val, b_val), 0.0001) * 100

        return {
            "test": test_name,
            "metric": metric,
            "variant_a": {"campaign": a["name"] if a else None, "value": round(a_val, 4)},
            "variant_b": {"campaign": b["name"] if b else None, "value": round(b_val, 4)},
            "winner": winner,
            "lift_pct": round(lift, 2),
        }
    finally:
        conn.close()


def _get_metric(camp: Dict, metric: str) -> float:
    sent = max(camp.get("total_sent", 1), 1)
    if metric == "open_rate":
        return camp.get("unique_opens", 0) / sent
    if metric == "click_rate":
        return camp.get("unique_clicks", 0) / sent
    if metric == "revenue":
        return camp.get("revenue_usd", 0.0)
    return 0.0


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def campaign_analytics(campaign_id: int) -> Dict[str, Any]:
    """Full analytics for a campaign."""
    _ensure()
    camp = get_campaign_by_id(campaign_id)
    if not camp:
        return {"error": f"Campaign {campaign_id} not found"}

    conn = _db()
    try:
        hourly = conn.execute(
            """SELECT strftime('%H', occurred_at) as hour, event_type, COUNT(*) as cnt
               FROM email_events WHERE campaign_id = ?
               GROUP BY hour, event_type ORDER BY hour""",
            (campaign_id,),
        ).fetchall()
        top_links = conn.execute(
            """SELECT link_url, COUNT(*) as clicks
               FROM email_events
               WHERE campaign_id = ? AND event_type IN ('click', 'unique_click') AND link_url != ''
               GROUP BY link_url ORDER BY clicks DESC LIMIT 10""",
            (campaign_id,),
        ).fetchall()
        camp_dict = camp.to_dict()
        return {
            "campaign": camp_dict,
            "open_rate": camp_dict.get("open_rate", 0.0),
            "click_rate": camp_dict.get("click_rate", 0.0),
            "hourly_activity": [dict(r) for r in hourly],
            "top_links": [dict(r) for r in top_links],
        }
    finally:
        conn.close()


def email_marketing_summary() -> Dict[str, Any]:
    """Overall email marketing health summary."""
    _ensure()
    conn = _db()
    try:
        camp_row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent_count,
                   SUM(total_sent) as total_recipients,
                   AVG(CASE WHEN total_sent > 0 THEN unique_opens * 1.0 / total_sent END) as avg_open_rate,
                   AVG(CASE WHEN total_sent > 0 THEN unique_clicks * 1.0 / total_sent END) as avg_click_rate,
                   SUM(revenue_usd) as total_revenue
               FROM email_campaigns WHERE status = 'sent'"""
        ).fetchone()
        seq_row = conn.execute(
            "SELECT COUNT(*) as total, SUM(is_active) as active FROM email_sequences"
        ).fetchone()
        enroll_row = conn.execute(
            "SELECT COUNT(*) as active FROM sequence_enrollments WHERE status = 'active'"
        ).fetchone()
        auto_row = conn.execute(
            "SELECT COUNT(*) as total, SUM(run_count) as total_runs FROM automation_rules WHERE is_active = 1"
        ).fetchone()
        return {
            "campaigns": {
                "total": camp_row["total"] or 0,
                "sent": camp_row["sent_count"] or 0,
                "total_recipients": camp_row["total_recipients"] or 0,
                "avg_open_rate": round((camp_row["avg_open_rate"] or 0) * 100, 2),
                "avg_click_rate": round((camp_row["avg_click_rate"] or 0) * 100, 2),
                "total_revenue": round(camp_row["total_revenue"] or 0, 2),
            },
            "sequences": {
                "total": seq_row["total"] or 0,
                "active": seq_row["active"] or 0,
                "active_enrollments": enroll_row["active"] or 0,
            },
            "automations": {
                "active": auto_row["total"] or 0,
                "total_runs": auto_row["total_runs"] or 0,
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Built-in sequence templates
# ---------------------------------------------------------------------------

SEQUENCE_TEMPLATES = {
    "welcome_series": {
        "name": "Welcome Series",
        "trigger_event": "signup",
        "delay_hours": 0,
        "goal": "Onboard new subscribers and build relationship",
        "steps": [
            {
                "step": 1, "delay_hours": 0,
                "subject": "Welcome! Here's what's coming your way",
                "body_text": "Hi {{first_name}},\n\nWelcome aboard! I'm thrilled to have you here.\n\nOver the next few days I'll share my best tips on making money online...",
            },
            {
                "step": 2, "delay_hours": 24,
                "subject": "The #1 mistake beginners make (and how to avoid it)",
                "body_text": "Hi {{first_name}},\n\nYesterday I introduced myself. Today I want to share the single biggest mistake...",
            },
            {
                "step": 3, "delay_hours": 72,
                "subject": "My 3-step framework for generating your first $1,000 online",
                "body_text": "Hi {{first_name}},\n\nIn today's email I'm sharing my proven 3-step framework...",
            },
            {
                "step": 4, "delay_hours": 120,
                "subject": "Quick question for you",
                "body_text": "Hi {{first_name}},\n\nI only have one question: what is your biggest challenge right now with making money online?\n\nHit reply and tell me — I read every response.",
            },
        ],
    },
    "product_launch": {
        "name": "Product Launch",
        "trigger_event": "signup",
        "delay_hours": 0,
        "goal": "Launch a new product with urgency sequence",
        "steps": [
            {
                "step": 1, "delay_hours": 0,
                "subject": "Something exciting is coming...",
                "body_text": "I've been working on something I can't wait to share with you. Stay tuned.",
            },
            {
                "step": 2, "delay_hours": 48,
                "subject": "The wait is almost over",
                "body_text": "Tomorrow I'm finally revealing what I've been building. Here's a sneak peek...",
            },
            {
                "step": 3, "delay_hours": 72,
                "subject": "It's LIVE — but only for 72 hours",
                "body_text": "Today is the day! {{product_name}} is officially open.\n\nGet it here: {{product_url}}\n\nThis offer closes in 72 hours.",
            },
            {
                "step": 4, "delay_hours": 120,
                "subject": "Last chance: 24 hours left",
                "body_text": "Quick reminder — the cart closes tomorrow at midnight.\n\n{{product_url}}",
            },
            {
                "step": 5, "delay_hours": 144,
                "subject": "Closing tonight at midnight",
                "body_text": "Final notice: in a few hours I'm closing the cart and this price goes away forever.\n\n{{product_url}}",
            },
        ],
    },
    "re_engagement": {
        "name": "Re-Engagement Campaign",
        "trigger_event": "segment_entered",
        "delay_hours": 0,
        "goal": "Win back inactive subscribers or clean list",
        "steps": [
            {
                "step": 1, "delay_hours": 0,
                "subject": "We miss you 👋",
                "body_text": "Hi {{first_name}},\n\nI noticed you haven't been opening my emails lately. I want to make sure I'm still sending you stuff you care about.",
            },
            {
                "step": 2, "delay_hours": 72,
                "subject": "Are you still there?",
                "body_text": "Just checking in. Click here if you still want to hear from me: {{confirm_url}}",
            },
            {
                "step": 3, "delay_hours": 144,
                "subject": "This is goodbye (unless you say otherwise)",
                "body_text": "I'll stop emailing you soon. If you want to stay subscribed, click here: {{confirm_url}}\n\nOtherwise, no hard feelings.",
            },
        ],
    },
}


def create_sequence_from_template(template_name: str, sequence_name: str = "") -> EmailSequence:
    """Instantiate a sequence from a built-in template."""
    _ensure()
    tpl = SEQUENCE_TEMPLATES.get(template_name)
    if not tpl:
        raise ValueError(f"Unknown template '{template_name}'. Available: {list(SEQUENCE_TEMPLATES)}")

    name = sequence_name or f"{tpl['name']} {datetime.utcnow().strftime('%Y%m%d')}"
    seq = create_sequence(
        name=name,
        trigger_event=tpl["trigger_event"],
        description=tpl.get("goal", ""),
        delay_hours=tpl.get("delay_hours", 24),
        goal=tpl.get("goal", ""),
    )
    for step_def in tpl.get("steps", []):
        add_sequence_email(
            sequence_name=name,
            step_number=step_def["step"],
            subject=step_def["subject"],
            body_text=step_def.get("body_text", ""),
            delay_hours=step_def.get("delay_hours", 24),
        )
    return get_sequence(name)


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("em_create_campaign", "Create a new email campaign")
def create_campaign_tool(
    name: str,
    subject: str,
    email_type: str = "broadcast",
    body_html: str = "",
    body_text: str = "",
    preview_text: str = "",
) -> str:
    try:
        camp = create_campaign(
            name=name,
            subject=subject,
            email_type=email_type,
            body_html=body_html,
            body_text=body_text,
            preview_text=preview_text,
        )
        return json.dumps({"ok": True, "campaign": camp.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("em_list_campaigns", "List email campaigns with optional status filter")
def list_campaigns_tool(status: str = "", email_type: str = "", limit: int = 20) -> str:
    try:
        camps = list_campaigns(status=status or None, email_type=email_type or None, limit=limit)
        return json.dumps([c.to_dict() for c in camps], default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("em_schedule_campaign", "Schedule an email campaign for delivery at a specific time (ISO datetime)")
def schedule_campaign_tool(campaign_name: str, send_at: str) -> str:
    try:
        camp = get_campaign(campaign_name)
        if not camp:
            return json.dumps({"ok": False, "error": "Campaign not found"})
        ok = schedule_campaign(camp.id, send_at)
        return json.dumps({"ok": ok, "scheduled_at": send_at})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("em_record_event", "Record an email event: open, click, unsubscribe, bounce")
def record_event_tool(
    event_type: str,
    subscriber_email: str,
    campaign_name: str = "",
    campaign_id: int = 0,
    link_url: str = "",
) -> str:
    try:
        cid = campaign_id or None
        if not cid and campaign_name:
            camp = get_campaign(campaign_name)
            if camp:
                cid = camp.id
        record_event(event_type, subscriber_email, campaign_id=cid, link_url=link_url)
        return json.dumps({"ok": True})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("em_create_sequence", "Create an email drip sequence")
def create_sequence_tool(
    name: str,
    trigger_event: str = "signup",
    description: str = "",
    delay_hours: int = 24,
) -> str:
    try:
        seq = create_sequence(name=name, trigger_event=trigger_event,
                              description=description, delay_hours=delay_hours)
        return json.dumps({"ok": True, "sequence": seq.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("em_enroll_subscriber", "Enroll a subscriber in an email sequence")
def enroll_tool(sequence_name: str, subscriber_email: str) -> str:
    try:
        result = enroll_subscriber(sequence_name, subscriber_email)
        return json.dumps({"ok": True, **result})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("em_summary", "Email marketing summary: campaign stats, sequences, automations")
def summary_tool() -> str:
    try:
        return json.dumps(email_marketing_summary(), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("em_sequence_from_template", "Create a sequence from a built-in template")
def sequence_template_tool(template_name: str, sequence_name: str = "") -> str:
    try:
        seq = create_sequence_from_template(template_name, sequence_name)
        return json.dumps({"ok": True, "sequence": seq.to_dict() if seq else None,
                           "available_templates": list(SEQUENCE_TEMPLATES)})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc),
                           "available_templates": list(SEQUENCE_TEMPLATES)})


@tool("em_create_automation", "Create an email automation rule")
def create_automation_tool(
    name: str,
    trigger_event: str,
    action_type: str = "send_email",
    delay_minutes: int = 0,
) -> str:
    try:
        auto = create_automation(name=name, trigger_event=trigger_event,
                                 action_type=action_type, delay_minutes=delay_minutes)
        return json.dumps({"ok": True, "automation": auto})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
