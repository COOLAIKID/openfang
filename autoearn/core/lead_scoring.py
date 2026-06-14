"""
Lead Scoring — B2B/B2C lead scoring and management.

Manages the full lead lifecycle: capture, enrichment, behavioral scoring,
pipeline progression, list management, and analytics. Scoring is rule-based
with support for demographic, behavioral, firmographic, engagement, and intent
categories. All data stored in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LEAD_STATUSES = [
    "new",
    "contacted",
    "qualified",
    "proposal",
    "negotiation",
    "won",
    "lost",
    "unqualified",
    "nurturing",
]

GRADES = ["A", "B", "C", "D", "F"]

ACTIVITY_TYPES = [
    "page_view",
    "form_submit",
    "email_open",
    "email_click",
    "demo_request",
    "download",
    "webinar_register",
    "webinar_attend",
    "pricing_view",
    "checkout_started",
    "purchase",
    "referral",
    "social_share",
    "content_upgrade",
    "unsubscribe",
    "bounce",
]

SCORING_CATEGORIES = [
    "demographic",
    "behavioral",
    "firmographic",
    "engagement",
    "intent",
]

COMPANY_SIZES = [
    "1-10",
    "11-50",
    "51-200",
    "201-500",
    "501-1000",
    "1001-5000",
    "5000+",
]

INDUSTRIES = [
    "saas",
    "ecommerce",
    "healthcare",
    "finance",
    "education",
    "marketing",
    "real_estate",
    "consulting",
    "manufacturing",
    "media",
    "other",
]

# Grade thresholds: score >= threshold → grade
_GRADE_THRESHOLDS = [
    (75, "A"),
    (50, "B"),
    (25, "C"),
    (10, "D"),
    (0,  "F"),
]

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
            CREATE TABLE IF NOT EXISTS leads (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email           TEXT NOT NULL UNIQUE,
                name            TEXT,
                company         TEXT,
                title           TEXT,
                phone           TEXT,
                website         TEXT,
                source          TEXT,
                utm_source      TEXT,
                utm_medium      TEXT,
                utm_campaign    TEXT,
                country         TEXT,
                industry        TEXT,
                company_size    TEXT,
                annual_revenue  REAL DEFAULT 0,
                score           INT DEFAULT 0,
                grade           TEXT DEFAULT 'F',
                status          TEXT DEFAULT 'new',
                assigned_to     TEXT,
                tags            TEXT DEFAULT '[]',
                notes           TEXT,
                first_seen_at   TEXT,
                last_activity_at TEXT,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scoring_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                category        TEXT NOT NULL DEFAULT 'behavioral',
                condition_field TEXT NOT NULL,
                condition_op    TEXT NOT NULL DEFAULT 'eq',
                condition_value TEXT NOT NULL,
                points          INT DEFAULT 0,
                is_active       INT DEFAULT 1,
                description     TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lead_activities (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id         INTEGER NOT NULL,
                activity_type   TEXT NOT NULL,
                description     TEXT,
                score_delta     INT DEFAULT 0,
                metadata        TEXT,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lead_lists (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                description     TEXT,
                filter_json     TEXT,
                is_dynamic      INT DEFAULT 1,
                lead_count      INT DEFAULT 0,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lead_list_members (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id         INTEGER NOT NULL,
                lead_id         INTEGER NOT NULL,
                added_at        TEXT NOT NULL,
                UNIQUE(list_id, lead_id)
            );

            CREATE TABLE IF NOT EXISTS lead_notes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id         INTEGER NOT NULL,
                author          TEXT,
                body            TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS lead_stages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                order_pos       INT DEFAULT 0,
                color           TEXT DEFAULT '#888888',
                is_won          INT DEFAULT 0,
                is_lost         INT DEFAULT 0,
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_leads_email     ON leads(email);
            CREATE INDEX IF NOT EXISTS idx_leads_status    ON leads(status);
            CREATE INDEX IF NOT EXISTS idx_leads_score     ON leads(score);
            CREATE INDEX IF NOT EXISTS idx_activities_lead ON lead_activities(lead_id);
            CREATE INDEX IF NOT EXISTS idx_list_members_list ON lead_list_members(list_id);
            CREATE INDEX IF NOT EXISTS idx_list_members_lead ON lead_list_members(lead_id);
        """)
        conn.commit()
        _seed_default_rules(conn)
        _seed_default_stages(conn)
    finally:
        conn.close()


def _seed_default_rules(conn: sqlite3.Connection) -> None:
    """Insert default scoring rules if none exist yet."""
    existing = conn.execute("SELECT COUNT(*) FROM scoring_rules").fetchone()[0]
    if existing > 0:
        return
    now = _now_str()
    defaults = [
        ("Demo Request",           "behavioral",   "activity_type",  "eq",  "demo_request",     30,  "Lead requested a product demo"),
        ("Purchase",               "intent",       "activity_type",  "eq",  "purchase",         50,  "Lead completed a purchase"),
        ("Pricing Page View",      "intent",       "activity_type",  "eq",  "pricing_view",     15,  "Lead viewed the pricing page"),
        ("Email Click",            "engagement",   "activity_type",  "eq",  "email_click",       5,  "Lead clicked a link in email"),
        ("Email Open",             "engagement",   "activity_type",  "eq",  "email_open",        2,  "Lead opened an email"),
        ("Webinar Attended",       "behavioral",   "activity_type",  "eq",  "webinar_attend",   20,  "Lead attended a live webinar"),
        ("Industry SaaS",          "firmographic", "industry",       "eq",  "saas",             10,  "Lead works in SaaS industry"),
        ("Company Size 51-200",    "firmographic", "company_size",   "eq",  "51-200",           10,  "Mid-market company size"),
        ("Company Size 201-500",   "firmographic", "company_size",   "eq",  "201-500",          15,  "Upper mid-market company size"),
        ("Unsubscribe",            "engagement",   "activity_type",  "eq",  "unsubscribe",     -25,  "Lead unsubscribed from emails"),
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO scoring_rules
            (name, category, condition_field, condition_op, condition_value, points, is_active, description, created_at)
        VALUES (?,?,?,?,?,?,1,?,?)
        """,
        [(n, cat, cf, op, cv, pts, desc, now) for n, cat, cf, op, cv, pts, desc in defaults],
    )
    conn.commit()


def _seed_default_stages(conn: sqlite3.Connection) -> None:
    """Insert default pipeline stages if none exist."""
    existing = conn.execute("SELECT COUNT(*) FROM lead_stages").fetchone()[0]
    if existing > 0:
        return
    now = _now_str()
    stages = [
        ("New",          0, "#6c757d", 0, 0),
        ("Contacted",    1, "#0d6efd", 0, 0),
        ("Qualified",    2, "#0dcaf0", 0, 0),
        ("Proposal",     3, "#ffc107", 0, 0),
        ("Negotiation",  4, "#fd7e14", 0, 0),
        ("Won",          5, "#198754", 1, 0),
        ("Lost",         6, "#dc3545", 0, 1),
        ("Unqualified",  7, "#6c757d", 0, 1),
        ("Nurturing",    8, "#6f42c1", 0, 0),
    ]
    conn.executemany(
        "INSERT OR IGNORE INTO lead_stages (name, order_pos, color, is_won, is_lost, created_at) VALUES (?,?,?,?,?,?)",
        [(n, pos, color, is_won, is_lost, now) for n, pos, color, is_won, is_lost in stages],
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_str() -> str:
    return datetime.utcnow().isoformat()


def _grade_for_score(score: int) -> str:
    """Return letter grade for a numeric score."""
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Lead:
    id: int
    email: str
    name: Optional[str]
    company: Optional[str]
    title: Optional[str]
    source: Optional[str]
    country: Optional[str]
    industry: Optional[str]
    company_size: Optional[str]
    score: int
    grade: str
    status: str
    tags: list[str]
    first_seen_at: Optional[str]
    last_activity_at: Optional[str]
    created_at: str

    @property
    def is_hot(self) -> bool:
        return self.score >= 75

    @property
    def days_in_pipeline(self) -> int:
        if not self.first_seen_at:
            return 0
        try:
            start = datetime.fromisoformat(self.first_seen_at)
            return (datetime.utcnow() - start).days
        except (ValueError, TypeError):
            return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "company": self.company,
            "title": self.title,
            "source": self.source,
            "country": self.country,
            "industry": self.industry,
            "company_size": self.company_size,
            "score": self.score,
            "grade": self.grade,
            "status": self.status,
            "tags": self.tags,
            "is_hot": self.is_hot,
            "days_in_pipeline": self.days_in_pipeline,
            "first_seen_at": self.first_seen_at,
            "last_activity_at": self.last_activity_at,
            "created_at": self.created_at,
        }


@dataclass
class ScoringRule:
    id: int
    name: str
    category: str
    condition_field: str
    condition_op: str
    condition_value: str
    points: int
    is_active: bool
    description: Optional[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "condition_field": self.condition_field,
            "condition_op": self.condition_op,
            "condition_value": self.condition_value,
            "points": self.points,
            "is_active": bool(self.is_active),
            "description": self.description,
            "created_at": self.created_at,
        }


@dataclass
class LeadActivity:
    id: int
    lead_id: int
    activity_type: str
    description: Optional[str]
    score_delta: int
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "lead_id": self.lead_id,
            "activity_type": self.activity_type,
            "description": self.description,
            "score_delta": self.score_delta,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Row → dataclass helpers
# ---------------------------------------------------------------------------

def _row_to_lead(row: sqlite3.Row) -> Lead:
    tags_raw = row["tags"] or "[]"
    try:
        tags = json.loads(tags_raw)
    except (json.JSONDecodeError, TypeError):
        tags = []
    return Lead(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        company=row["company"],
        title=row["title"],
        source=row["source"],
        country=row["country"],
        industry=row["industry"],
        company_size=row["company_size"],
        score=row["score"] or 0,
        grade=row["grade"] or "F",
        status=row["status"] or "new",
        tags=tags if isinstance(tags, list) else [],
        first_seen_at=row["first_seen_at"],
        last_activity_at=row["last_activity_at"],
        created_at=row["created_at"],
    )


def _row_to_rule(row: sqlite3.Row) -> ScoringRule:
    return ScoringRule(
        id=row["id"],
        name=row["name"],
        category=row["category"],
        condition_field=row["condition_field"],
        condition_op=row["condition_op"],
        condition_value=row["condition_value"],
        points=row["points"] or 0,
        is_active=bool(row["is_active"]),
        description=row["description"],
        created_at=row["created_at"],
    )


def _row_to_activity(row: sqlite3.Row) -> LeadActivity:
    return LeadActivity(
        id=row["id"],
        lead_id=row["lead_id"],
        activity_type=row["activity_type"],
        description=row["description"],
        score_delta=row["score_delta"] or 0,
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# Lead management
# ---------------------------------------------------------------------------

def create_lead(
    email: str,
    name: Optional[str] = None,
    company: Optional[str] = None,
    title: Optional[str] = None,
    source: Optional[str] = None,
    utm_source: Optional[str] = None,
    utm_medium: Optional[str] = None,
    utm_campaign: Optional[str] = None,
    country: Optional[str] = None,
    industry: Optional[str] = None,
    company_size: Optional[str] = None,
    tags: Optional[list[str]] = None,
) -> Lead:
    """Create a new lead. Raises ValueError if email already exists."""
    _ensure()
    email = email.lower().strip()
    if not email:
        raise ValueError("email is required")
    now = _now_str()
    tags_json = json.dumps(tags or [])
    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO leads
                (email, name, company, title, source, utm_source, utm_medium,
                 utm_campaign, country, industry, company_size, tags,
                 score, grade, status, first_seen_at, last_activity_at,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,'F','new',?,?,?,?)
            """,
            (email, name, company, title, source, utm_source, utm_medium,
             utm_campaign, country, industry, company_size, tags_json,
             now, now, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM leads WHERE email=?", (email,)).fetchone()
        return _row_to_lead(row)
    finally:
        conn.close()


def get_lead(email: str) -> Optional[Lead]:
    """Fetch a lead by email address."""
    _ensure()
    email = email.lower().strip()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM leads WHERE email=?", (email,)).fetchone()
        return _row_to_lead(row) if row else None
    finally:
        conn.close()


def get_lead_by_id(lead_id: int) -> Optional[Lead]:
    """Fetch a lead by database ID."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        return _row_to_lead(row) if row else None
    finally:
        conn.close()


def update_lead(lead_id: int, **fields: Any) -> bool:
    """Update arbitrary lead fields. Returns True if a row was updated."""
    _ensure()
    if not fields:
        return False
    allowed = {
        "name", "company", "title", "phone", "website", "source",
        "utm_source", "utm_medium", "utm_campaign", "country", "industry",
        "company_size", "annual_revenue", "status", "assigned_to",
        "tags", "notes",
    }
    sanitized: dict[str, Any] = {}
    for k, v in fields.items():
        if k in allowed:
            sanitized[k] = json.dumps(v) if k == "tags" and isinstance(v, list) else v
    if not sanitized:
        return False
    sanitized["updated_at"] = _now_str()
    set_clause = ", ".join(f"{k}=?" for k in sanitized)
    values = list(sanitized.values()) + [lead_id]
    conn = _db()
    try:
        cur = conn.execute(f"UPDATE leads SET {set_clause} WHERE id=?", values)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_lead(lead_id: int) -> bool:
    """Delete a lead and all associated data."""
    _ensure()
    conn = _db()
    try:
        conn.execute("DELETE FROM lead_activities WHERE lead_id=?", (lead_id,))
        conn.execute("DELETE FROM lead_notes WHERE lead_id=?", (lead_id,))
        conn.execute("DELETE FROM lead_list_members WHERE lead_id=?", (lead_id,))
        cur = conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def merge_leads(primary_email: str, duplicate_email: str) -> bool:
    """
    Merge duplicate lead into primary. Moves all activities to primary,
    then deletes the duplicate. Returns True on success.
    """
    _ensure()
    primary = get_lead(primary_email)
    duplicate = get_lead(duplicate_email)
    if not primary or not duplicate:
        return False
    if primary.id == duplicate.id:
        return False
    conn = _db()
    try:
        conn.execute(
            "UPDATE lead_activities SET lead_id=? WHERE lead_id=?",
            (primary.id, duplicate.id),
        )
        conn.execute(
            "UPDATE lead_notes SET lead_id=? WHERE lead_id=?",
            (primary.id, duplicate.id),
        )
        conn.execute(
            "DELETE FROM lead_list_members WHERE lead_id=?",
            (duplicate.id,),
        )
        conn.execute("DELETE FROM leads WHERE id=?", (duplicate.id,))
        conn.commit()
        # Recompute score for the primary after merging activities
        new_score = _recompute_score(primary.id)
        new_grade = _grade_for_score(new_score)
        conn2 = _db()
        try:
            conn2.execute(
                "UPDATE leads SET score=?, grade=?, updated_at=? WHERE id=?",
                (new_score, new_grade, _now_str(), primary.id),
            )
            conn2.commit()
        finally:
            conn2.close()
        return True
    finally:
        conn.close()


def list_leads(
    status: Optional[str] = None,
    min_score: Optional[int] = None,
    max_score: Optional[int] = None,
    grade: Optional[str] = None,
    source: Optional[str] = None,
    limit: int = 100,
) -> list[Lead]:
    """List leads with optional filters."""
    _ensure()
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if min_score is not None:
        clauses.append("score>=?")
        params.append(min_score)
    if max_score is not None:
        clauses.append("score<=?")
        params.append(max_score)
    if grade:
        clauses.append("grade=?")
        params.append(grade)
    if source:
        clauses.append("source=?")
        params.append(source)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(max(1, int(limit)))
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM leads {where} ORDER BY score DESC LIMIT ?", params
        ).fetchall()
        return [_row_to_lead(r) for r in rows]
    finally:
        conn.close()


def search_leads(query: str) -> list[Lead]:
    """Full-text search across name, email, and company."""
    _ensure()
    pattern = f"%{query.strip()}%"
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE name LIKE ? OR email LIKE ? OR company LIKE ?
            ORDER BY score DESC LIMIT 50
            """,
            (pattern, pattern, pattern),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]
    finally:
        conn.close()


def assign_lead(lead_id: int, assignee: str) -> bool:
    """Assign a lead to a team member."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE leads SET assigned_to=?, updated_at=? WHERE id=?",
            (assignee, _now_str(), lead_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def change_status(lead_id: int, new_status: str, note: Optional[str] = None) -> bool:
    """
    Update lead status and optionally log an activity note.
    Returns True if the lead was found and updated.
    """
    _ensure()
    if new_status not in LEAD_STATUSES:
        return False
    lead = get_lead_by_id(lead_id)
    if not lead:
        return False
    conn = _db()
    try:
        conn.execute(
            "UPDATE leads SET status=?, updated_at=? WHERE id=?",
            (new_status, _now_str(), lead_id),
        )
        conn.commit()
    finally:
        conn.close()
    desc = note or f"Status changed to {new_status}"
    record_activity(lead.email, "form_submit", desc, 0)
    return True


# ---------------------------------------------------------------------------
# Activity tracking & scoring
# ---------------------------------------------------------------------------

def record_activity(
    email: str,
    activity_type: str,
    description: Optional[str] = None,
    score_delta: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> LeadActivity:
    """
    Record an activity for a lead. If the lead does not exist it is created.
    Updates lead.score and recomputes grade automatically.
    """
    _ensure()
    email = email.lower().strip()
    lead = get_lead(email)
    if lead is None:
        lead = create_lead(email)

    # Determine score delta
    if score_delta is None:
        score_delta = _activity_type_delta(activity_type)

    now = _now_str()
    meta_json = json.dumps(metadata or {})
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO lead_activities
                (lead_id, activity_type, description, score_delta, metadata, created_at)
            VALUES (?,?,?,?,?,?)
            """,
            (lead.id, activity_type, description, score_delta, meta_json, now),
        )
        activity_id = cur.lastrowid
        conn.commit()

        # Update score incrementally
        new_score = max(0, (lead.score or 0) + score_delta)
        new_grade = _grade_for_score(new_score)
        conn.execute(
            "UPDATE leads SET score=?, grade=?, last_activity_at=?, updated_at=? WHERE id=?",
            (new_score, new_grade, now, now, lead.id),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM lead_activities WHERE id=?", (activity_id,)
        ).fetchone()
        return _row_to_activity(row)
    finally:
        conn.close()


def _activity_type_delta(activity_type: str) -> int:
    """Default score delta for well-known activity types."""
    defaults = {
        "demo_request":    30,
        "purchase":        50,
        "pricing_view":    15,
        "email_click":      5,
        "email_open":       2,
        "webinar_attend":  20,
        "webinar_register": 5,
        "checkout_started": 10,
        "download":         5,
        "content_upgrade":  8,
        "form_submit":      3,
        "referral":        15,
        "page_view":        1,
        "social_share":     5,
        "unsubscribe":    -25,
        "bounce":         -10,
    }
    return defaults.get(activity_type, 0)


def _recompute_score(lead_id: int) -> int:
    """
    Run all active scoring rules against lead data and cumulative activities.
    Returns the new total score (clamped to >=0).
    """
    _ensure()
    conn = _db()
    try:
        lead_row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
        if not lead_row:
            return 0
        lead_data = dict(lead_row)

        # Aggregate activity deltas
        activity_rows = conn.execute(
            "SELECT activity_type, SUM(score_delta) as total FROM lead_activities WHERE lead_id=? GROUP BY activity_type",
            (lead_id,),
        ).fetchall()
        activity_totals: dict[str, int] = {r["activity_type"]: (r["total"] or 0) for r in activity_rows}

        rules_rows = conn.execute(
            "SELECT * FROM scoring_rules WHERE is_active=1"
        ).fetchall()
        rules = [_row_to_rule(r) for r in rules_rows]
    finally:
        conn.close()

    total = 0
    for rule in rules:
        field_val = None
        if rule.condition_field == "activity_type":
            # Check whether the lead has that activity type at all
            field_val = rule.condition_value if rule.condition_value in activity_totals else None
        else:
            field_val = lead_data.get(rule.condition_field)

        if _evaluate_rule(rule, lead_data, field_val):
            if rule.condition_field == "activity_type":
                # Apply delta per occurrence count (cap at activity totals)
                count = sum(
                    1 for _ in [None]  # at least once present is enough for boolean rules
                )
                total += rule.points
            else:
                total += rule.points

    return max(0, total)


def _evaluate_rule(
    rule: ScoringRule,
    lead_data: dict[str, Any],
    override_value: Any = None,
) -> bool:
    """
    Evaluate a single scoring rule against lead data.
    Supports ops: eq, neq, gt, lt, gte, lte, contains, not_contains.
    """
    if override_value is not None:
        actual = override_value
    else:
        actual = lead_data.get(rule.condition_field)

    if actual is None:
        return False

    target = rule.condition_value
    op = rule.condition_op.lower()

    try:
        if op == "eq":
            return str(actual).lower() == str(target).lower()
        elif op == "neq":
            return str(actual).lower() != str(target).lower()
        elif op == "gt":
            return float(actual) > float(target)
        elif op == "lt":
            return float(actual) < float(target)
        elif op == "gte":
            return float(actual) >= float(target)
        elif op == "lte":
            return float(actual) <= float(target)
        elif op == "contains":
            return str(target).lower() in str(actual).lower()
        elif op == "not_contains":
            return str(target).lower() not in str(actual).lower()
        else:
            return False
    except (ValueError, TypeError):
        return False


def get_activities(email: str, limit: int = 50) -> list[LeadActivity]:
    """Return recent activities for a lead."""
    _ensure()
    lead = get_lead(email)
    if not lead:
        return []
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM lead_activities
            WHERE lead_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (lead.id, max(1, int(limit))),
        ).fetchall()
        return [_row_to_activity(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Scoring rules
# ---------------------------------------------------------------------------

def create_scoring_rule(
    name: str,
    category: str,
    condition_field: str,
    condition_op: str,
    condition_value: str,
    points: int,
    description: Optional[str] = None,
) -> ScoringRule:
    """Create a new scoring rule."""
    _ensure()
    if category not in SCORING_CATEGORIES:
        raise ValueError(f"category must be one of {SCORING_CATEGORIES}")
    now = _now_str()
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO scoring_rules
                (name, category, condition_field, condition_op, condition_value,
                 points, is_active, description, created_at)
            VALUES (?,?,?,?,?,?,1,?,?)
            """,
            (name, category, condition_field, condition_op, str(condition_value),
             int(points), description, now),
        )
        rule_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM scoring_rules WHERE id=?", (rule_id,)).fetchone()
        return _row_to_rule(row)
    finally:
        conn.close()


def list_scoring_rules(
    category: Optional[str] = None,
    active_only: bool = False,
) -> list[ScoringRule]:
    """Return scoring rules, optionally filtered."""
    _ensure()
    clauses = []
    params: list[Any] = []
    if category:
        clauses.append("category=?")
        params.append(category)
    if active_only:
        clauses.append("is_active=1")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM scoring_rules {where} ORDER BY category, name", params
        ).fetchall()
        return [_row_to_rule(r) for r in rows]
    finally:
        conn.close()


def update_scoring_rule(rule_id: int, **fields: Any) -> bool:
    """Update fields on a scoring rule."""
    _ensure()
    allowed = {
        "name", "category", "condition_field", "condition_op",
        "condition_value", "points", "is_active", "description",
    }
    sanitized = {k: v for k, v in fields.items() if k in allowed}
    if not sanitized:
        return False
    set_clause = ", ".join(f"{k}=?" for k in sanitized)
    values = list(sanitized.values()) + [rule_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE scoring_rules SET {set_clause} WHERE id=?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_scoring_rule(rule_id: int) -> bool:
    """Delete a scoring rule by ID."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute("DELETE FROM scoring_rules WHERE id=?", (rule_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def rescore_all_leads() -> dict[str, Any]:
    """
    Recompute scores for every lead using current active rules.
    Returns stats: {rescored, avg_score_before, avg_score_after}.
    """
    _ensure()
    conn = _db()
    try:
        rows = conn.execute("SELECT id, score FROM leads").fetchall()
        leads_data = [(r["id"], r["score"] or 0) for r in rows]
    finally:
        conn.close()

    if not leads_data:
        return {"rescored": 0, "avg_score_before": 0.0, "avg_score_after": 0.0}

    before_total = sum(s for _, s in leads_data)
    after_total = 0
    now = _now_str()

    for lead_id, _ in leads_data:
        new_score = _recompute_score(lead_id)
        new_grade = _grade_for_score(new_score)
        after_total += new_score
        conn2 = _db()
        try:
            conn2.execute(
                "UPDATE leads SET score=?, grade=?, updated_at=? WHERE id=?",
                (new_score, new_grade, now, lead_id),
            )
            conn2.commit()
        finally:
            conn2.close()

    n = len(leads_data)
    return {
        "rescored": n,
        "avg_score_before": round(before_total / n, 2),
        "avg_score_after": round(after_total / n, 2),
    }


# ---------------------------------------------------------------------------
# Lead lists
# ---------------------------------------------------------------------------

def create_list(
    name: str,
    description: Optional[str] = None,
    filter_json: Optional[str] = None,
    is_dynamic: bool = True,
) -> dict[str, Any]:
    """Create a named lead list."""
    _ensure()
    now = _now_str()
    conn = _db()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO lead_lists
                (name, description, filter_json, is_dynamic, lead_count, created_at, updated_at)
            VALUES (?,?,?,?,0,?,?)
            """,
            (name, description, filter_json, 1 if is_dynamic else 0, now, now),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM lead_lists WHERE name=?", (name,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_list(name: str) -> Optional[dict[str, Any]]:
    """Fetch a lead list by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM lead_lists WHERE name=?", (name,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_lists() -> list[dict[str, Any]]:
    """Return all lead lists."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM lead_lists ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_to_list(list_name: str, email: str) -> bool:
    """Add a lead to a list by name. Creates the lead if not found."""
    _ensure()
    lst = get_list(list_name)
    if not lst:
        return False
    email = email.lower().strip()
    lead = get_lead(email)
    if not lead:
        lead = create_lead(email)
    now = _now_str()
    conn = _db()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO lead_list_members (list_id, lead_id, added_at) VALUES (?,?,?)",
            (lst["id"], lead.id, now),
        )
        conn.execute(
            "UPDATE lead_lists SET lead_count=(SELECT COUNT(*) FROM lead_list_members WHERE list_id=?), updated_at=? WHERE id=?",
            (lst["id"], now, lst["id"]),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def remove_from_list(list_name: str, email: str) -> bool:
    """Remove a lead from a list."""
    _ensure()
    lst = get_list(list_name)
    if not lst:
        return False
    lead = get_lead(email)
    if not lead:
        return False
    now = _now_str()
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM lead_list_members WHERE list_id=? AND lead_id=?",
            (lst["id"], lead.id),
        )
        conn.execute(
            "UPDATE lead_lists SET lead_count=(SELECT COUNT(*) FROM lead_list_members WHERE list_id=?), updated_at=? WHERE id=?",
            (lst["id"], now, lst["id"]),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_list_members(list_name: str, limit: int = 200) -> list[Lead]:
    """Return the leads that belong to a named list."""
    _ensure()
    lst = get_list(list_name)
    if not lst:
        return []
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT l.* FROM leads l
            JOIN lead_list_members m ON m.lead_id = l.id
            WHERE m.list_id=?
            ORDER BY l.score DESC LIMIT ?
            """,
            (lst["id"], max(1, int(limit))),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]
    finally:
        conn.close()


def refresh_dynamic_list(list_name: str) -> int:
    """
    Re-evaluate a dynamic list's filter and add all matching leads.
    Returns the number of members after refresh.
    """
    _ensure()
    lst = get_list(list_name)
    if not lst or not lst.get("is_dynamic"):
        return 0
    filter_str = lst.get("filter_json") or "{}"
    try:
        filters = json.loads(filter_str)
    except (json.JSONDecodeError, TypeError):
        filters = {}
    # Apply supported filter keys
    leads = list_leads(
        status=filters.get("status"),
        min_score=filters.get("min_score"),
        max_score=filters.get("max_score"),
        grade=filters.get("grade"),
        source=filters.get("source"),
        limit=10000,
    )
    now = _now_str()
    conn = _db()
    try:
        for lead in leads:
            conn.execute(
                "INSERT OR IGNORE INTO lead_list_members (list_id, lead_id, added_at) VALUES (?,?,?)",
                (lst["id"], lead.id, now),
            )
        count = conn.execute(
            "SELECT COUNT(*) FROM lead_list_members WHERE list_id=?", (lst["id"],)
        ).fetchone()[0]
        conn.execute(
            "UPDATE lead_lists SET lead_count=?, updated_at=? WHERE id=?",
            (count, now, lst["id"]),
        )
        conn.commit()
        return count
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

def add_note(email: str, author: str, body: str) -> dict[str, Any]:
    """Add a note to a lead."""
    _ensure()
    lead = get_lead(email)
    if not lead:
        lead = create_lead(email)
    now = _now_str()
    conn = _db()
    try:
        cur = conn.execute(
            "INSERT INTO lead_notes (lead_id, author, body, created_at) VALUES (?,?,?,?)",
            (lead.id, author, body, now),
        )
        note_id = cur.lastrowid
        conn.commit()
        row = conn.execute("SELECT * FROM lead_notes WHERE id=?", (note_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_notes(email: str) -> list[dict[str, Any]]:
    """Return all notes for a lead."""
    _ensure()
    lead = get_lead(email)
    if not lead:
        return []
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM lead_notes WHERE lead_id=? ORDER BY id DESC",
            (lead.id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def lead_funnel_report() -> dict[str, Any]:
    """
    Count leads at each status stage and compute sequential conversion rates.
    """
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) as count FROM leads GROUP BY status"
        ).fetchall()
    finally:
        conn.close()

    counts: dict[str, int] = {r["status"]: r["count"] for r in rows}
    total = sum(counts.values())

    # Ordered pipeline stages for conversion rate computation
    pipeline_order = ["new", "contacted", "qualified", "proposal", "negotiation", "won"]
    stage_counts = [counts.get(s, 0) for s in pipeline_order]

    conversions = []
    for i in range(len(pipeline_order) - 1):
        from_stage = pipeline_order[i]
        to_stage = pipeline_order[i + 1]
        from_count = stage_counts[i]
        to_count = stage_counts[i + 1]
        rate = round(to_count / from_count * 100, 1) if from_count > 0 else 0.0
        conversions.append({
            "from": from_stage,
            "to": to_stage,
            "from_count": from_count,
            "to_count": to_count,
            "conversion_rate_pct": rate,
        })

    return {
        "total": total,
        "by_status": counts,
        "pipeline_conversions": conversions,
        "won_count": counts.get("won", 0),
        "lost_count": counts.get("lost", 0),
        "overall_win_rate_pct": round(counts.get("won", 0) / total * 100, 1) if total > 0 else 0.0,
    }


def source_analysis(days: int = 30) -> list[dict[str, Any]]:
    """Return leads per source with avg score and conversion rate."""
    _ensure()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT
                source,
                COUNT(*) as total,
                AVG(score) as avg_score,
                SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) as won_count
            FROM leads
            WHERE created_at >= ?
            GROUP BY source
            ORDER BY total DESC
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    result = []
    for r in rows:
        total = r["total"] or 0
        won = r["won_count"] or 0
        result.append({
            "source": r["source"] or "(unknown)",
            "total": total,
            "avg_score": round((r["avg_score"] or 0.0), 1),
            "won_count": won,
            "conversion_rate_pct": round(won / total * 100, 1) if total > 0 else 0.0,
        })
    return result


def hot_leads(limit: int = 25) -> list[Lead]:
    """Return leads with score >= 75, not won/lost, ordered by score desc."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE score >= 75 AND status NOT IN ('won', 'lost')
            ORDER BY score DESC LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]
    finally:
        conn.close()


def stale_leads(days_inactive: int = 14) -> list[Lead]:
    """Return leads with no activity in N days, not won or lost."""
    _ensure()
    cutoff = (datetime.utcnow() - timedelta(days=days_inactive)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """
            SELECT * FROM leads
            WHERE status NOT IN ('won', 'lost')
              AND (last_activity_at IS NULL OR last_activity_at < ?)
            ORDER BY last_activity_at ASC LIMIT 200
            """,
            (cutoff,),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]
    finally:
        conn.close()


def score_distribution() -> dict[str, int]:
    """Return counts of leads in score buckets: 0-24, 25-49, 50-74, 75-100."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute("SELECT score FROM leads").fetchall()
    finally:
        conn.close()

    buckets = {"0-24": 0, "25-49": 0, "50-74": 0, "75-100": 0}
    for r in rows:
        s = r["score"] or 0
        if s >= 75:
            buckets["75-100"] += 1
        elif s >= 50:
            buckets["50-74"] += 1
        elif s >= 25:
            buckets["25-49"] += 1
        else:
            buckets["0-24"] += 1
    return buckets


def lead_velocity(days: int = 30) -> dict[str, Any]:
    """
    Return new leads per day, average time to qualify, and win rate
    over the specified window.
    """
    _ensure()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = _db()
    try:
        new_count = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]
        qualified_rows = conn.execute(
            """
            SELECT created_at, last_activity_at FROM leads
            WHERE status IN ('qualified','proposal','negotiation','won')
              AND created_at >= ?
            """,
            (cutoff,),
        ).fetchall()
        won_count = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status='won' AND created_at >= ?", (cutoff,)
        ).fetchone()[0]
        total_in_window = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE created_at >= ?", (cutoff,)
        ).fetchone()[0]
    finally:
        conn.close()

    # Average time to qualify (days from created_at to last_activity_at)
    qualify_times = []
    for r in qualified_rows:
        try:
            created = datetime.fromisoformat(r["created_at"])
            last_act = datetime.fromisoformat(r["last_activity_at"]) if r["last_activity_at"] else created
            qualify_times.append((last_act - created).days)
        except (ValueError, TypeError):
            pass

    avg_time_to_qualify = round(sum(qualify_times) / len(qualify_times), 1) if qualify_times else 0.0
    win_rate = round(won_count / total_in_window * 100, 1) if total_in_window > 0 else 0.0
    leads_per_day = round(new_count / days, 2) if days > 0 else 0.0

    return {
        "window_days": days,
        "new_leads": new_count,
        "leads_per_day": leads_per_day,
        "qualified_count": len(qualify_times),
        "avg_days_to_qualify": avg_time_to_qualify,
        "won_count": won_count,
        "win_rate_pct": win_rate,
    }


def lead_summary() -> dict[str, Any]:
    """
    High-level CRM dashboard summary.
    """
    _ensure()
    conn = _db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        avg_score_row = conn.execute("SELECT AVG(score) FROM leads").fetchone()
        avg_score = round((avg_score_row[0] or 0.0), 1)
        hot_count = conn.execute("SELECT COUNT(*) FROM leads WHERE score>=75 AND status NOT IN ('won','lost')").fetchone()[0]
        won_count = conn.execute("SELECT COUNT(*) FROM leads WHERE status='won'").fetchone()[0]
        status_rows = conn.execute("SELECT status, COUNT(*) as cnt FROM leads GROUP BY status").fetchall()
        grade_rows = conn.execute("SELECT grade, COUNT(*) as cnt FROM leads GROUP BY grade").fetchall()
        source_rows = conn.execute(
            "SELECT source, COUNT(*) as cnt FROM leads WHERE source IS NOT NULL GROUP BY source ORDER BY cnt DESC LIMIT 5"
        ).fetchall()
    finally:
        conn.close()

    conversion_rate = round(won_count / total * 100, 1) if total > 0 else 0.0

    return {
        "total": total,
        "avg_score": avg_score,
        "hot_count": hot_count,
        "conversion_rate_pct": conversion_rate,
        "by_status": {r["status"]: r["cnt"] for r in status_rows},
        "by_grade": {r["grade"]: r["cnt"] for r in grade_rows},
        "top_sources": [{"source": r["source"] or "(unknown)", "count": r["cnt"]} for r in source_rows],
    }


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

@tool(
    "ls_create_lead",
    "ls_create_lead(email, name?, company?, source?, industry?) — create a new lead in the scoring CRM.",
)
def ls_create_lead_tool(
    email: str = "",
    name: Optional[str] = None,
    company: Optional[str] = None,
    source: Optional[str] = None,
    industry: Optional[str] = None,
    **_: Any,
) -> str:
    try:
        if not email:
            return json.dumps({"error": "email is required"})
        lead = create_lead(
            email=email,
            name=name,
            company=company,
            source=source,
            industry=industry,
        )
        return json.dumps({"ok": True, "lead": lead.to_dict()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_record_activity",
    "ls_record_activity(email, activity_type, description?, score_delta?) — record a lead activity and update score.",
)
def ls_record_activity_tool(
    email: str = "",
    activity_type: str = "page_view",
    description: Optional[str] = None,
    score_delta: Optional[int] = None,
    **_: Any,
) -> str:
    try:
        if not email:
            return json.dumps({"error": "email is required"})
        activity = record_activity(
            email=email,
            activity_type=activity_type,
            description=description,
            score_delta=score_delta,
        )
        lead = get_lead(email)
        return json.dumps({
            "ok": True,
            "activity": activity.to_dict(),
            "lead_score": lead.score if lead else None,
            "lead_grade": lead.grade if lead else None,
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_get_lead",
    "ls_get_lead(email) — retrieve full lead profile and score.",
)
def ls_get_lead_tool(email: str = "", **_: Any) -> str:
    try:
        if not email:
            return json.dumps({"error": "email is required"})
        lead = get_lead(email)
        if not lead:
            return json.dumps({"found": False, "email": email})
        return json.dumps({"found": True, "lead": lead.to_dict()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_hot_leads",
    "ls_hot_leads(limit?) — list hot leads (score >= 75, not won/lost) sorted by score.",
)
def ls_hot_leads_tool(limit: int = 25, **_: Any) -> str:
    try:
        leads = hot_leads(int(limit))
        return json.dumps({"count": len(leads), "leads": [l.to_dict() for l in leads]})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_lead_summary",
    "ls_lead_summary() — high-level CRM dashboard: totals, grade breakdown, conversion rate, top sources.",
)
def ls_lead_summary_tool(**_: Any) -> str:
    try:
        return json.dumps(lead_summary())
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_lead_funnel",
    "ls_lead_funnel() — lead funnel report with counts at each pipeline stage and conversion rates.",
)
def ls_lead_funnel_tool(**_: Any) -> str:
    try:
        return json.dumps(lead_funnel_report())
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_source_analysis",
    "ls_source_analysis(days?) — lead volume, avg score, and conversion rate per acquisition source.",
)
def ls_source_analysis_tool(days: int = 30, **_: Any) -> str:
    try:
        return json.dumps(source_analysis(int(days)))
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_rescore_all",
    "ls_rescore_all() — recompute scores for all leads using the current active scoring rules.",
)
def ls_rescore_all_tool(**_: Any) -> str:
    try:
        return json.dumps(rescore_all_leads())
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_stale_leads",
    "ls_stale_leads(days_inactive?) — list leads with no activity in N days (default 14), not won/lost.",
)
def ls_stale_leads_tool(days_inactive: int = 14, **_: Any) -> str:
    try:
        leads = stale_leads(int(days_inactive))
        return json.dumps({
            "days_inactive": days_inactive,
            "count": len(leads),
            "leads": [l.to_dict() for l in leads],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool(
    "ls_search_leads",
    "ls_search_leads(query) — search leads by name, email, or company (partial match).",
)
def ls_search_leads_tool(query: str = "", **_: Any) -> str:
    try:
        if not query:
            return json.dumps({"error": "query is required"})
        leads = search_leads(query)
        return json.dumps({"count": len(leads), "leads": [l.to_dict() for l in leads]})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
