"""
Newsletter manager — manage subscriber lists, campaigns, and email sequences.

Handles subscriber lifecycle (subscribe, confirm, unsubscribe, bounce),
campaign creation, send-time scheduling, and open/click tracking.
All data stored locally in SQLite; actual sending goes through the
email connectors (SendGrid, Mailgun) registered in core/connectors/.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import cfg

# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    db_path = cfg("newsletter.db_path", fallback="autoearn.db")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _ensure_schema(_conn)
    return _conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS nl_subscribers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT    NOT NULL UNIQUE,
            first_name      TEXT    NOT NULL DEFAULT '',
            last_name       TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT 'pending',
            list_name       TEXT    NOT NULL DEFAULT 'main',
            source          TEXT    NOT NULL DEFAULT '',
            tags            TEXT    NOT NULL DEFAULT '[]',
            custom_fields   TEXT    NOT NULL DEFAULT '{}',
            subscribed_at   REAL    NOT NULL,
            confirmed_at    REAL,
            unsubscribed_at REAL,
            bounced_at      REAL,
            token           TEXT    NOT NULL DEFAULT '',
            open_rate       REAL    NOT NULL DEFAULT 0.0,
            click_rate      REAL    NOT NULL DEFAULT 0.0,
            total_opens     INTEGER NOT NULL DEFAULT 0,
            total_clicks    INTEGER NOT NULL DEFAULT 0,
            emails_received INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS nl_lists (
            name            TEXT    PRIMARY KEY,
            description     TEXT    NOT NULL DEFAULT '',
            created_at      REAL    NOT NULL,
            double_optin    INTEGER NOT NULL DEFAULT 1,
            from_name       TEXT    NOT NULL DEFAULT '',
            from_email      TEXT    NOT NULL DEFAULT '',
            reply_to        TEXT    NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS nl_campaigns (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            subject         TEXT    NOT NULL,
            preview_text    TEXT    NOT NULL DEFAULT '',
            html_body       TEXT    NOT NULL DEFAULT '',
            text_body       TEXT    NOT NULL DEFAULT '',
            list_name       TEXT    NOT NULL DEFAULT 'main',
            tags_filter     TEXT    NOT NULL DEFAULT '[]',
            status          TEXT    NOT NULL DEFAULT 'draft',
            created_at      REAL    NOT NULL,
            scheduled_at    REAL,
            sent_at         REAL,
            recipients      INTEGER NOT NULL DEFAULT 0,
            delivered       INTEGER NOT NULL DEFAULT 0,
            opens           INTEGER NOT NULL DEFAULT 0,
            unique_opens    INTEGER NOT NULL DEFAULT 0,
            clicks          INTEGER NOT NULL DEFAULT 0,
            unique_clicks   INTEGER NOT NULL DEFAULT 0,
            bounces         INTEGER NOT NULL DEFAULT 0,
            unsubscribes    INTEGER NOT NULL DEFAULT 0,
            revenue         REAL    NOT NULL DEFAULT 0.0
        );

        CREATE TABLE IF NOT EXISTS nl_sequences (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,
            description     TEXT    NOT NULL DEFAULT '',
            trigger         TEXT    NOT NULL DEFAULT 'signup',
            list_name       TEXT    NOT NULL DEFAULT 'main',
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS nl_sequence_emails (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sequence_id     INTEGER NOT NULL,
            step_number     INTEGER NOT NULL,
            delay_hours     INTEGER NOT NULL DEFAULT 0,
            subject         TEXT    NOT NULL,
            html_body       TEXT    NOT NULL DEFAULT '',
            text_body       TEXT    NOT NULL DEFAULT '',
            UNIQUE(sequence_id, step_number)
        );

        CREATE TABLE IF NOT EXISTS nl_sequence_enrollments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sequence_id     INTEGER NOT NULL,
            subscriber_id   INTEGER NOT NULL,
            current_step    INTEGER NOT NULL DEFAULT 0,
            enrolled_at     REAL    NOT NULL,
            next_send_at    REAL,
            completed       INTEGER NOT NULL DEFAULT 0,
            cancelled       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS nl_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ts              REAL    NOT NULL,
            event_type      TEXT    NOT NULL,
            subscriber_id   INTEGER NOT NULL DEFAULT 0,
            campaign_id     INTEGER NOT NULL DEFAULT 0,
            sequence_id     INTEGER NOT NULL DEFAULT 0,
            step_number     INTEGER NOT NULL DEFAULT 0,
            url             TEXT    NOT NULL DEFAULT '',
            meta            TEXT    NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_nl_sub_email  ON nl_subscribers(email);
        CREATE INDEX IF NOT EXISTS idx_nl_sub_list   ON nl_subscribers(list_name);
        CREATE INDEX IF NOT EXISTS idx_nl_sub_status ON nl_subscribers(status);
        CREATE INDEX IF NOT EXISTS idx_nl_camp_status ON nl_campaigns(status);
        CREATE INDEX IF NOT EXISTS idx_nl_enroll_seq ON nl_sequence_enrollments(sequence_id);
        CREATE INDEX IF NOT EXISTS idx_nl_enroll_sub ON nl_sequence_enrollments(subscriber_id);
        CREATE INDEX IF NOT EXISTS idx_nl_events_ts  ON nl_events(ts);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

SUBSCRIBER_STATUSES = ["pending", "confirmed", "unsubscribed", "bounced", "complained"]


@dataclass
class Subscriber:
    email: str
    first_name: str = ""
    last_name: str = ""
    status: str = "pending"
    list_name: str = "main"
    source: str = ""
    tags: list = field(default_factory=list)
    custom_fields: dict = field(default_factory=dict)
    subscribed_at: float = field(default_factory=time.time)
    confirmed_at: float | None = None
    unsubscribed_at: float | None = None
    bounced_at: float | None = None
    token: str = field(default_factory=lambda: uuid.uuid4().hex)
    open_rate: float = 0.0
    click_rate: float = 0.0
    total_opens: int = 0
    total_clicks: int = 0
    emails_received: int = 0
    id: int = 0

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p).strip() or self.email

    @property
    def is_active(self) -> bool:
        return self.status == "confirmed"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "status": self.status,
            "list_name": self.list_name,
            "source": self.source,
            "tags": self.tags,
            "custom_fields": self.custom_fields,
            "subscribed_at": self.subscribed_at,
            "is_active": self.is_active,
            "open_rate": round(self.open_rate, 4),
            "click_rate": round(self.click_rate, 4),
            "total_opens": self.total_opens,
            "total_clicks": self.total_clicks,
            "emails_received": self.emails_received,
        }


@dataclass
class Campaign:
    name: str
    subject: str
    html_body: str = ""
    text_body: str = ""
    preview_text: str = ""
    list_name: str = "main"
    tags_filter: list = field(default_factory=list)
    status: str = "draft"
    created_at: float = field(default_factory=time.time)
    scheduled_at: float | None = None
    sent_at: float | None = None
    recipients: int = 0
    delivered: int = 0
    opens: int = 0
    unique_opens: int = 0
    clicks: int = 0
    unique_clicks: int = 0
    bounces: int = 0
    unsubscribes: int = 0
    revenue: float = 0.0
    id: int = 0

    @property
    def open_rate(self) -> float:
        if self.delivered == 0:
            return 0.0
        return round(self.unique_opens / self.delivered * 100, 2)

    @property
    def click_rate(self) -> float:
        if self.unique_opens == 0:
            return 0.0
        return round(self.unique_clicks / self.unique_opens * 100, 2)

    @property
    def click_to_open_rate(self) -> float:
        if self.opens == 0:
            return 0.0
        return round(self.clicks / self.opens * 100, 2)

    @property
    def bounce_rate(self) -> float:
        if self.recipients == 0:
            return 0.0
        return round(self.bounces / self.recipients * 100, 2)

    @property
    def unsubscribe_rate(self) -> float:
        if self.delivered == 0:
            return 0.0
        return round(self.unsubscribes / self.delivered * 100, 4)

    @property
    def revenue_per_email(self) -> float:
        if self.delivered == 0:
            return 0.0
        return round(self.revenue / self.delivered, 4)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "subject": self.subject,
            "preview_text": self.preview_text,
            "list_name": self.list_name,
            "status": self.status,
            "scheduled_at": self.scheduled_at,
            "sent_at": self.sent_at,
            "recipients": self.recipients,
            "delivered": self.delivered,
            "opens": self.opens,
            "unique_opens": self.unique_opens,
            "clicks": self.clicks,
            "unique_clicks": self.unique_clicks,
            "bounces": self.bounces,
            "unsubscribes": self.unsubscribes,
            "revenue": round(self.revenue, 4),
            "open_rate_pct": self.open_rate,
            "click_rate_pct": self.click_rate,
            "click_to_open_rate_pct": self.click_to_open_rate,
            "bounce_rate_pct": self.bounce_rate,
            "unsubscribe_rate_pct": self.unsubscribe_rate,
            "revenue_per_email": self.revenue_per_email,
        }


# ---------------------------------------------------------------------------
# Subscriber management
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(email.strip()))


def subscribe(
    email: str,
    first_name: str = "",
    last_name: str = "",
    list_name: str = "main",
    source: str = "",
    tags: list | None = None,
    custom_fields: dict | None = None,
    double_optin: bool = True,
) -> dict:
    """
    Add or update a subscriber. Returns dict with status and action taken.
    If double_optin=True, status starts as 'pending'; confirm_subscriber() activates them.
    """
    email = email.strip().lower()
    if not _validate_email(email):
        return {"ok": False, "error": f"invalid email: {email}"}

    status = "pending" if double_optin else "confirmed"
    confirmed_at = None if double_optin else time.time()
    token = hashlib.sha256(f"{email}{time.time()}".encode()).hexdigest()[:32]
    tags_json = json.dumps(tags or [])
    fields_json = json.dumps(custom_fields or {})
    now = time.time()

    db = _db()
    existing = db.execute(
        "SELECT id, status FROM nl_subscribers WHERE email=?", (email,)
    ).fetchone()

    if existing:
        if existing["status"] == "unsubscribed":
            return {"ok": False, "error": "subscriber previously unsubscribed", "email": email}
        db.execute(
            """UPDATE nl_subscribers SET
               first_name=?, last_name=?, list_name=?, source=?, tags=?,
               custom_fields=?, token=?
               WHERE email=?""",
            (first_name, last_name, list_name, source, tags_json, fields_json, token, email),
        )
        db.commit()
        return {"ok": True, "action": "updated", "email": email, "status": existing["status"]}

    db.execute(
        """INSERT INTO nl_subscribers
           (email, first_name, last_name, status, list_name, source, tags,
            custom_fields, subscribed_at, confirmed_at, token)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (email, first_name, last_name, status, list_name, source,
         tags_json, fields_json, now, confirmed_at, token),
    )
    db.commit()
    return {"ok": True, "action": "subscribed", "email": email, "status": status,
            "token": token, "requires_confirmation": double_optin}


def confirm_subscriber(token: str) -> dict:
    """Confirm a subscriber via their double-optin token."""
    db = _db()
    row = db.execute(
        "SELECT id, email, status FROM nl_subscribers WHERE token=?", (token,)
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "invalid token"}
    if row["status"] == "confirmed":
        return {"ok": True, "action": "already_confirmed", "email": row["email"]}
    db.execute(
        "UPDATE nl_subscribers SET status='confirmed', confirmed_at=? WHERE token=?",
        (time.time(), token),
    )
    db.commit()
    _log_event("confirmed", subscriber_id=row["id"])
    return {"ok": True, "action": "confirmed", "email": row["email"]}


def unsubscribe(email: str, reason: str = "") -> dict:
    """Unsubscribe an email address."""
    email = email.strip().lower()
    db = _db()
    row = db.execute(
        "SELECT id FROM nl_subscribers WHERE email=?", (email,)
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "subscriber not found"}
    db.execute(
        "UPDATE nl_subscribers SET status='unsubscribed', unsubscribed_at=? WHERE email=?",
        (time.time(), email),
    )
    db.commit()
    _log_event("unsubscribed", subscriber_id=row["id"], meta={"reason": reason})
    return {"ok": True, "action": "unsubscribed", "email": email}


def record_bounce(email: str, bounce_type: str = "hard") -> dict:
    """Mark an email as bounced."""
    email = email.strip().lower()
    db = _db()
    row = db.execute(
        "SELECT id FROM nl_subscribers WHERE email=?", (email,)
    ).fetchone()
    if row is None:
        return {"ok": False, "error": "subscriber not found"}
    new_status = "bounced" if bounce_type == "hard" else "confirmed"
    db.execute(
        "UPDATE nl_subscribers SET status=?, bounced_at=? WHERE email=?",
        (new_status, time.time(), email),
    )
    db.commit()
    _log_event("bounced", subscriber_id=row["id"], meta={"bounce_type": bounce_type})
    return {"ok": True, "action": "bounced", "email": email, "bounce_type": bounce_type}


def add_tag(email: str, tag: str) -> bool:
    """Add a tag to a subscriber."""
    db = _db()
    row = db.execute(
        "SELECT id, tags FROM nl_subscribers WHERE email=?", (email.strip().lower(),)
    ).fetchone()
    if row is None:
        return False
    tags = json.loads(row["tags"] or "[]")
    if tag not in tags:
        tags.append(tag)
        db.execute(
            "UPDATE nl_subscribers SET tags=? WHERE id=?",
            (json.dumps(tags), row["id"]),
        )
        db.commit()
    return True


def remove_tag(email: str, tag: str) -> bool:
    """Remove a tag from a subscriber."""
    db = _db()
    row = db.execute(
        "SELECT id, tags FROM nl_subscribers WHERE email=?", (email.strip().lower(),)
    ).fetchone()
    if row is None:
        return False
    tags = json.loads(row["tags"] or "[]")
    if tag in tags:
        tags.remove(tag)
        db.execute(
            "UPDATE nl_subscribers SET tags=? WHERE id=?",
            (json.dumps(tags), row["id"]),
        )
        db.commit()
    return True


def get_subscriber(email: str) -> dict | None:
    """Get subscriber details."""
    row = _db().execute(
        "SELECT * FROM nl_subscribers WHERE email=?", (email.strip().lower(),)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tags"] = json.loads(d.get("tags", "[]"))
    d["custom_fields"] = json.loads(d.get("custom_fields", "{}"))
    return d


def list_subscribers(
    list_name: str = "",
    status: str = "confirmed",
    tag: str = "",
    limit: int = 500,
    offset: int = 0,
) -> list[dict]:
    """List subscribers with optional filters."""
    query = "SELECT * FROM nl_subscribers WHERE 1=1"
    params: list[Any] = []
    if list_name:
        query += " AND list_name=?"
        params.append(list_name)
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY subscribed_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    rows = _db().execute(query, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags", "[]"))
        d["custom_fields"] = json.loads(d.get("custom_fields", "{}"))
        if tag and tag not in d["tags"]:
            continue
        result.append(d)
    return result


def subscriber_count(list_name: str = "", status: str = "confirmed") -> int:
    """Count subscribers matching filters."""
    query = "SELECT COUNT(*) FROM nl_subscribers WHERE 1=1"
    params: list[Any] = []
    if list_name:
        query += " AND list_name=?"
        params.append(list_name)
    if status:
        query += " AND status=?"
        params.append(status)
    return _db().execute(query, params).fetchone()[0]


# ---------------------------------------------------------------------------
# List management
# ---------------------------------------------------------------------------

def create_list(
    name: str,
    description: str = "",
    from_name: str = "",
    from_email: str = "",
    reply_to: str = "",
    double_optin: bool = True,
) -> dict:
    """Create a subscriber list."""
    db = _db()
    db.execute(
        """INSERT INTO nl_lists (name, description, created_at, double_optin, from_name, from_email, reply_to)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
             description=excluded.description,
             from_name=excluded.from_name,
             from_email=excluded.from_email,
             reply_to=excluded.reply_to""",
        (name, description, time.time(), int(double_optin), from_name, from_email, reply_to),
    )
    db.commit()
    return {"ok": True, "name": name, "double_optin": double_optin}


def get_list_stats(list_name: str) -> dict:
    """Get statistics for a subscriber list."""
    db = _db()
    row = db.execute("SELECT * FROM nl_lists WHERE name=?", (list_name,)).fetchone()
    if row is None:
        return {"error": f"list '{list_name}' not found"}
    stats = dict(row)
    for status in SUBSCRIBER_STATUSES:
        stats[f"{status}_count"] = db.execute(
            "SELECT COUNT(*) FROM nl_subscribers WHERE list_name=? AND status=?",
            (list_name, status),
        ).fetchone()[0]
    stats["total"] = sum(stats.get(f"{s}_count", 0) for s in SUBSCRIBER_STATUSES)
    return stats


# ---------------------------------------------------------------------------
# Campaign management
# ---------------------------------------------------------------------------

def create_campaign(
    name: str,
    subject: str,
    html_body: str = "",
    text_body: str = "",
    preview_text: str = "",
    list_name: str = "main",
    tags_filter: list | None = None,
    scheduled_at: float | None = None,
) -> int:
    """Create a draft campaign. Returns campaign ID."""
    db = _db()
    cur = db.execute(
        """INSERT INTO nl_campaigns
           (name, subject, html_body, text_body, preview_text, list_name,
            tags_filter, status, created_at, scheduled_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (name, subject, html_body, text_body, preview_text, list_name,
         json.dumps(tags_filter or []), "draft", time.time(), scheduled_at),
    )
    db.commit()
    return cur.lastrowid or 0


def get_campaign(campaign_id: int) -> dict | None:
    """Get campaign details."""
    row = _db().execute(
        "SELECT * FROM nl_campaigns WHERE id=?", (campaign_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tags_filter"] = json.loads(d.get("tags_filter", "[]"))
    # Attach computed rates
    c = Campaign(**{k: d[k] for k in Campaign.__dataclass_fields__ if k in d})
    result = d.copy()
    result.update({
        "open_rate_pct": c.open_rate,
        "click_rate_pct": c.click_rate,
        "bounce_rate_pct": c.bounce_rate,
        "revenue_per_email": c.revenue_per_email,
    })
    return result


def schedule_campaign(campaign_id: int, send_at: float) -> bool:
    """Schedule a draft campaign for sending."""
    db = _db()
    db.execute(
        "UPDATE nl_campaigns SET status='scheduled', scheduled_at=? WHERE id=? AND status='draft'",
        (send_at, campaign_id),
    )
    db.commit()
    return db.execute(
        "SELECT status FROM nl_campaigns WHERE id=?", (campaign_id,)
    ).fetchone()["status"] == "scheduled"


def mark_campaign_sent(campaign_id: int, recipients: int) -> None:
    """Mark a campaign as sent."""
    db = _db()
    db.execute(
        "UPDATE nl_campaigns SET status='sent', sent_at=?, recipients=? WHERE id=?",
        (time.time(), recipients, campaign_id),
    )
    db.commit()


def record_campaign_open(campaign_id: int, subscriber_id: int, unique: bool = True) -> None:
    """Record an email open event."""
    db = _db()
    db.execute(
        "UPDATE nl_campaigns SET opens=opens+1{}  WHERE id=?".format(
            ", unique_opens=unique_opens+1" if unique else ""
        ),
        (campaign_id,),
    )
    if unique:
        db.execute(
            "UPDATE nl_subscribers SET total_opens=total_opens+1 WHERE id=?",
            (subscriber_id,),
        )
    db.commit()
    _log_event("email_open", subscriber_id=subscriber_id, campaign_id=campaign_id)


def record_campaign_click(
    campaign_id: int, subscriber_id: int, url: str = "", unique: bool = True
) -> None:
    """Record a link click in a campaign."""
    db = _db()
    db.execute(
        "UPDATE nl_campaigns SET clicks=clicks+1{}  WHERE id=?".format(
            ", unique_clicks=unique_clicks+1" if unique else ""
        ),
        (campaign_id,),
    )
    if unique:
        db.execute(
            "UPDATE nl_subscribers SET total_clicks=total_clicks+1 WHERE id=?",
            (subscriber_id,),
        )
    db.commit()
    _log_event("email_click", subscriber_id=subscriber_id, campaign_id=campaign_id, url=url)


def record_campaign_revenue(campaign_id: int, amount: float) -> None:
    """Attribute revenue to a campaign."""
    _db().execute(
        "UPDATE nl_campaigns SET revenue=revenue+? WHERE id=?",
        (amount, campaign_id),
    )
    _db().commit()


def list_campaigns(status: str = "", limit: int = 50) -> list[dict]:
    """List campaigns with optional status filter."""
    query = "SELECT * FROM nl_campaigns"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    rows = _db().execute(query, params).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["tags_filter"] = json.loads(d.get("tags_filter", "[]"))
        result.append(d)
    return result


def due_campaigns() -> list[dict]:
    """Get campaigns scheduled for sending now or in the past."""
    now = time.time()
    rows = _db().execute(
        "SELECT * FROM nl_campaigns WHERE status='scheduled' AND scheduled_at<=? ORDER BY scheduled_at",
        (now,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Email sequences
# ---------------------------------------------------------------------------

def create_sequence(
    name: str,
    description: str = "",
    trigger: str = "signup",
    list_name: str = "main",
) -> int:
    """Create an email automation sequence. Returns sequence ID."""
    db = _db()
    cur = db.execute(
        """INSERT INTO nl_sequences (name, description, trigger, list_name, active, created_at)
           VALUES (?,?,?,?,1,?)
           ON CONFLICT(name) DO UPDATE SET
             description=excluded.description,
             trigger=excluded.trigger,
             list_name=excluded.list_name""",
        (name, description, trigger, list_name, time.time()),
    )
    db.commit()
    return cur.lastrowid or 0


def add_sequence_email(
    sequence_id: int,
    step_number: int,
    subject: str,
    html_body: str = "",
    text_body: str = "",
    delay_hours: int = 0,
) -> int:
    """Add an email step to a sequence. Returns step ID."""
    db = _db()
    cur = db.execute(
        """INSERT INTO nl_sequence_emails
           (sequence_id, step_number, delay_hours, subject, html_body, text_body)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(sequence_id, step_number) DO UPDATE SET
             delay_hours=excluded.delay_hours,
             subject=excluded.subject,
             html_body=excluded.html_body,
             text_body=excluded.text_body""",
        (sequence_id, step_number, delay_hours, subject, html_body, text_body),
    )
    db.commit()
    return cur.lastrowid or 0


def enroll_in_sequence(sequence_id: int, subscriber_id: int) -> dict:
    """Enroll a subscriber in an automation sequence."""
    db = _db()
    existing = db.execute(
        """SELECT id FROM nl_sequence_enrollments
           WHERE sequence_id=? AND subscriber_id=? AND completed=0 AND cancelled=0""",
        (sequence_id, subscriber_id),
    ).fetchone()
    if existing:
        return {"ok": False, "error": "already enrolled"}

    # Get first step delay
    first_step = db.execute(
        "SELECT delay_hours FROM nl_sequence_emails WHERE sequence_id=? AND step_number=1",
        (sequence_id,),
    ).fetchone()
    next_send = time.time() + (first_step["delay_hours"] * 3600 if first_step else 0)

    cur = db.execute(
        """INSERT INTO nl_sequence_enrollments
           (sequence_id, subscriber_id, current_step, enrolled_at, next_send_at)
           VALUES (?,?,0,?,?)""",
        (sequence_id, subscriber_id, time.time(), next_send),
    )
    db.commit()
    return {"ok": True, "enrollment_id": cur.lastrowid, "next_send_at": next_send}


def advance_enrollment(enrollment_id: int) -> dict:
    """Advance an enrollment to the next sequence step. Returns next step info or completion."""
    db = _db()
    row = db.execute(
        "SELECT * FROM nl_sequence_enrollments WHERE id=?", (enrollment_id,)
    ).fetchone()
    if row is None:
        return {"error": "enrollment not found"}
    if row["completed"] or row["cancelled"]:
        return {"status": "finished"}

    next_step = row["current_step"] + 1
    step_row = db.execute(
        "SELECT * FROM nl_sequence_emails WHERE sequence_id=? AND step_number=?",
        (row["sequence_id"], next_step),
    ).fetchone()

    if step_row is None:
        db.execute(
            "UPDATE nl_sequence_enrollments SET completed=1 WHERE id=?",
            (enrollment_id,),
        )
        db.commit()
        return {"status": "completed", "enrollment_id": enrollment_id}

    next_send = time.time() + step_row["delay_hours"] * 3600
    db.execute(
        "UPDATE nl_sequence_enrollments SET current_step=?, next_send_at=? WHERE id=?",
        (next_step, next_send, enrollment_id),
    )
    db.commit()
    return {
        "status": "advanced",
        "enrollment_id": enrollment_id,
        "current_step": next_step,
        "next_send_at": next_send,
        "subject": step_row["subject"],
    }


def due_sequence_emails() -> list[dict]:
    """Get sequence enrollments due for sending their next email."""
    now = time.time()
    rows = _db().execute(
        """SELECT e.*, s.name as sequence_name,
                  se.subject, se.html_body, se.text_body, se.step_number,
                  sub.email, sub.first_name
           FROM nl_sequence_enrollments e
           JOIN nl_sequences s ON s.id = e.sequence_id
           JOIN nl_sequence_emails se ON se.sequence_id=e.sequence_id AND se.step_number=e.current_step+1
           JOIN nl_subscribers sub ON sub.id = e.subscriber_id
           WHERE e.completed=0 AND e.cancelled=0 AND e.next_send_at<=? AND sub.status='confirmed'
           ORDER BY e.next_send_at""",
        (now,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def newsletter_summary() -> dict:
    """High-level newsletter health summary."""
    db = _db()
    confirmed = db.execute(
        "SELECT COUNT(*) FROM nl_subscribers WHERE status='confirmed'"
    ).fetchone()[0]
    total = db.execute(
        "SELECT COUNT(*) FROM nl_subscribers"
    ).fetchone()[0]
    bounced = db.execute(
        "SELECT COUNT(*) FROM nl_subscribers WHERE status='bounced'"
    ).fetchone()[0]
    unsub = db.execute(
        "SELECT COUNT(*) FROM nl_subscribers WHERE status='unsubscribed'"
    ).fetchone()[0]
    pending = db.execute(
        "SELECT COUNT(*) FROM nl_subscribers WHERE status='pending'"
    ).fetchone()[0]
    campaigns_sent = db.execute(
        "SELECT COUNT(*) FROM nl_campaigns WHERE status='sent'"
    ).fetchone()[0]
    total_revenue = db.execute(
        "SELECT COALESCE(SUM(revenue),0) FROM nl_campaigns WHERE status='sent'"
    ).fetchone()[0]
    avg_open_rate = db.execute(
        "SELECT AVG(CAST(unique_opens AS REAL)/NULLIF(delivered,0)*100) FROM nl_campaigns WHERE status='sent'"
    ).fetchone()[0] or 0.0
    avg_click_rate = db.execute(
        "SELECT AVG(CAST(unique_clicks AS REAL)/NULLIF(unique_opens,0)*100) FROM nl_campaigns WHERE status='sent'"
    ).fetchone()[0] or 0.0

    return {
        "confirmed_subscribers": confirmed,
        "pending_subscribers": pending,
        "bounced_subscribers": bounced,
        "unsubscribed": unsub,
        "total_subscribers": total,
        "list_health_pct": round(confirmed / total * 100, 1) if total > 0 else 0.0,
        "campaigns_sent": campaigns_sent,
        "total_revenue": round(total_revenue, 4),
        "avg_open_rate_pct": round(avg_open_rate, 2),
        "avg_click_rate_pct": round(avg_click_rate, 2),
    }


def growth_trend(days: int = 30) -> list[dict]:
    """Net subscriber growth per day for the last N days."""
    since = time.time() - days * 86400
    new_subs = _db().execute(
        """SELECT date(subscribed_at, 'unixepoch') as day, COUNT(*) as new_subs
           FROM nl_subscribers WHERE subscribed_at>=?
           GROUP BY day ORDER BY day""",
        (since,),
    ).fetchall()
    unsubs = _db().execute(
        """SELECT date(unsubscribed_at, 'unixepoch') as day, COUNT(*) as unsubs
           FROM nl_subscribers WHERE unsubscribed_at>=?
           GROUP BY day ORDER BY day""",
        (since,),
    ).fetchall()
    unsub_map = {row["day"]: row["unsubs"] for row in unsubs}
    result = []
    for row in new_subs:
        day = row["day"]
        net = row["new_subs"] - unsub_map.get(day, 0)
        result.append({"day": day, "new": row["new_subs"],
                        "unsubscribed": unsub_map.get(day, 0), "net": net})
    return result


# ---------------------------------------------------------------------------
# Event logging (internal)
# ---------------------------------------------------------------------------

def _log_event(
    event_type: str,
    subscriber_id: int = 0,
    campaign_id: int = 0,
    sequence_id: int = 0,
    step_number: int = 0,
    url: str = "",
    meta: dict | None = None,
) -> None:
    _db().execute(
        """INSERT INTO nl_events
           (ts, event_type, subscriber_id, campaign_id, sequence_id, step_number, url, meta)
           VALUES (?,?,?,?,?,?,?,?)""",
        (time.time(), event_type, subscriber_id, campaign_id, sequence_id,
         step_number, url, json.dumps(meta or {})),
    )
    _db().commit()


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def subscribe_tool(
    email: str = "",
    first_name: str = "",
    last_name: str = "",
    list_name: str = "main",
    source: str = "",
    tags: str = "",
) -> str:
    """Agent-callable: subscribe an email address."""
    if not email:
        return "error: email required"
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    result = subscribe(email, first_name, last_name, list_name, source, tag_list)
    return json.dumps(result)


def unsubscribe_tool(email: str = "") -> str:
    """Agent-callable: unsubscribe an email address."""
    if not email:
        return "error: email required"
    return json.dumps(unsubscribe(email))


def subscriber_count_tool(list_name: str = "", status: str = "confirmed") -> str:
    """Agent-callable: count subscribers."""
    count = subscriber_count(list_name, status)
    return f"{count} {status} subscribers" + (f" in list '{list_name}'" if list_name else "")


def create_campaign_tool(
    name: str = "",
    subject: str = "",
    body: str = "",
    list_name: str = "main",
) -> str:
    """Agent-callable: create a draft email campaign."""
    if not name or not subject:
        return "error: name and subject required"
    cid = create_campaign(name, subject, html_body=body, text_body=body, list_name=list_name)
    return f"campaign '{name}' created (id={cid})"


def newsletter_summary_tool() -> str:
    """Agent-callable: newsletter health summary as JSON."""
    return json.dumps(newsletter_summary(), indent=2)


def due_campaigns_tool() -> str:
    """Agent-callable: list campaigns due for sending."""
    campaigns = due_campaigns()
    if not campaigns:
        return "no campaigns due for sending"
    return json.dumps([{"id": c["id"], "name": c["name"], "subject": c["subject"],
                         "list_name": c["list_name"]} for c in campaigns], indent=2)


def create_sequence_tool(
    name: str = "",
    description: str = "",
    trigger: str = "signup",
    list_name: str = "main",
) -> str:
    """Agent-callable: create an email automation sequence."""
    if not name:
        return "error: name required"
    sid = create_sequence(name, description, trigger, list_name)
    return f"sequence '{name}' created (id={sid}, trigger={trigger})"


def growth_trend_tool(days: int = 30) -> str:
    """Agent-callable: subscriber growth trend as JSON."""
    trend = growth_trend(days)
    if not trend:
        return "no subscriber data for the given period"
    return json.dumps(trend, indent=2)
