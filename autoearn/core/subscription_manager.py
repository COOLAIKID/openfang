"""
Subscription Manager — plans, billing cycles, invoicing, usage, coupons, analytics.

Manages the full subscription lifecycle: plan creation, subscriber onboarding,
billing cycle renewals, coupon redemption, usage tracking, and revenue analytics
(MRR, ARR, churn, LTV, cohort retention). All data stored in SQLite via the
shared autoearn.db database.

Key concepts:
- :class:`SubscriptionPlan`   — a product tier with monthly/annual pricing
- :class:`Subscriber`         — a customer on a plan with billing state
- Billing cycles: monthly, annual, lifetime
- Subscriber statuses: trialing, active, past_due, cancelled, paused, expired
- Event audit trail records every lifecycle transition
- Coupon codes support percent and flat discounts with optional expiry/use limits
"""

from __future__ import annotations

import json
import math
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

BILLING_CYCLES = ["monthly", "annual", "lifetime"]

SUB_STATUSES = [
    "trialing",
    "active",
    "past_due",
    "cancelled",
    "paused",
    "expired",
]

EVENT_TYPES = [
    "created",
    "activated",
    "upgraded",
    "downgraded",
    "cancelled",
    "reactivated",
    "trial_started",
    "trial_ended",
    "payment_failed",
    "payment_succeeded",
    "paused",
    "resumed",
    "refunded",
]

DISCOUNT_TYPES = ["percent", "flat"]

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
            CREATE TABLE IF NOT EXISTS subscription_plans (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL,
                slug            TEXT NOT NULL UNIQUE,
                description     TEXT,
                price_monthly   REAL NOT NULL DEFAULT 0.0,
                price_annual    REAL NOT NULL DEFAULT 0.0,
                trial_days      INTEGER NOT NULL DEFAULT 0,
                features        TEXT NOT NULL DEFAULT '[]',
                max_users       INTEGER NOT NULL DEFAULT 1,
                status          TEXT NOT NULL DEFAULT 'active',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscribers (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                email                   TEXT NOT NULL UNIQUE,
                name                    TEXT NOT NULL DEFAULT '',
                plan_id                 INTEGER NOT NULL,
                status                  TEXT NOT NULL DEFAULT 'trialing',
                billing_cycle           TEXT NOT NULL DEFAULT 'monthly',
                trial_ends_at           TEXT,
                current_period_start    TEXT NOT NULL,
                current_period_end      TEXT NOT NULL,
                cancel_at_period_end    INTEGER NOT NULL DEFAULT 0,
                stripe_customer_id      TEXT,
                stripe_subscription_id  TEXT,
                metadata                TEXT NOT NULL DEFAULT '{}',
                created_at              TEXT NOT NULL,
                updated_at              TEXT NOT NULL,
                FOREIGN KEY (plan_id) REFERENCES subscription_plans(id)
            );

            CREATE TABLE IF NOT EXISTS subscription_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id   INTEGER NOT NULL,
                event_type      TEXT NOT NULL,
                plan_id         INTEGER,
                amount          REAL NOT NULL DEFAULT 0.0,
                notes           TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
            );

            CREATE TABLE IF NOT EXISTS subscription_invoices (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id   INTEGER NOT NULL,
                amount          REAL NOT NULL DEFAULT 0.0,
                currency        TEXT NOT NULL DEFAULT 'USD',
                status          TEXT NOT NULL DEFAULT 'open',
                billing_reason  TEXT,
                period_start    TEXT,
                period_end      TEXT,
                paid_at         TEXT,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
            );

            CREATE TABLE IF NOT EXISTS subscription_usage (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                subscriber_id   INTEGER NOT NULL,
                metric_name     TEXT NOT NULL,
                usage_count     INTEGER NOT NULL DEFAULT 0,
                period_start    TEXT NOT NULL,
                period_end      TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                FOREIGN KEY (subscriber_id) REFERENCES subscribers(id)
            );

            CREATE TABLE IF NOT EXISTS subscription_coupons (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT NOT NULL UNIQUE,
                discount_type   TEXT NOT NULL DEFAULT 'percent',
                discount_value  REAL NOT NULL DEFAULT 0.0,
                duration_months INTEGER NOT NULL DEFAULT 1,
                max_uses        INTEGER NOT NULL DEFAULT 0,
                uses            INTEGER NOT NULL DEFAULT 0,
                expires_at      TEXT,
                created_at      TEXT NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SubscriptionPlan:
    id: int
    name: str
    slug: str
    description: str
    price_monthly: float
    price_annual: float
    trial_days: int
    features: list
    max_users: int
    status: str
    created_at: str
    updated_at: str

    @property
    def annual_savings_percent(self) -> float:
        """Percentage saved by paying annually vs 12 monthly payments."""
        if self.price_monthly <= 0:
            return 0.0
        monthly_total = self.price_monthly * 12
        if monthly_total <= 0:
            return 0.0
        savings = monthly_total - self.price_annual
        return round((savings / monthly_total) * 100, 2)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "price_monthly": self.price_monthly,
            "price_annual": self.price_annual,
            "trial_days": self.trial_days,
            "features": self.features,
            "max_users": self.max_users,
            "status": self.status,
            "annual_savings_percent": self.annual_savings_percent,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SubscriptionPlan":
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            description=row["description"] or "",
            price_monthly=row["price_monthly"] or 0.0,
            price_annual=row["price_annual"] or 0.0,
            trial_days=row["trial_days"] or 0,
            features=json.loads(row["features"] or "[]"),
            max_users=row["max_users"] or 1,
            status=row["status"] or "active",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass
class Subscriber:
    id: int
    email: str
    name: str
    plan_id: int
    status: str
    billing_cycle: str
    trial_ends_at: Optional[str]
    current_period_start: str
    current_period_end: str
    cancel_at_period_end: bool
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]
    metadata: dict
    created_at: str
    updated_at: str
    # plan is optionally populated
    plan: Optional[SubscriptionPlan] = field(default=None, compare=False)

    @property
    def is_active(self) -> bool:
        return self.status in ("active", "trialing")

    @property
    def monthly_value(self) -> float:
        """Normalised monthly revenue from this subscriber."""
        if self.plan is None:
            return 0.0
        if self.billing_cycle == "annual":
            return round(self.plan.price_annual / 12, 4)
        if self.billing_cycle == "lifetime":
            # Treat lifetime as a one-time purchase; monthly value is 0 for MRR
            return 0.0
        return self.plan.price_monthly

    @property
    def days_since_signup(self) -> int:
        try:
            created = datetime.fromisoformat(self.created_at)
            delta = datetime.utcnow() - created
            return max(0, delta.days)
        except Exception:
            return 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "plan_id": self.plan_id,
            "plan": self.plan.to_dict() if self.plan else None,
            "status": self.status,
            "billing_cycle": self.billing_cycle,
            "trial_ends_at": self.trial_ends_at,
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "cancel_at_period_end": self.cancel_at_period_end,
            "stripe_customer_id": self.stripe_customer_id,
            "stripe_subscription_id": self.stripe_subscription_id,
            "metadata": self.metadata,
            "is_active": self.is_active,
            "monthly_value": self.monthly_value,
            "days_since_signup": self.days_since_signup,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row, plan: Optional[SubscriptionPlan] = None) -> "Subscriber":
        return cls(
            id=row["id"],
            email=row["email"],
            name=row["name"] or "",
            plan_id=row["plan_id"],
            status=row["status"] or "active",
            billing_cycle=row["billing_cycle"] or "monthly",
            trial_ends_at=row["trial_ends_at"],
            current_period_start=row["current_period_start"],
            current_period_end=row["current_period_end"],
            cancel_at_period_end=bool(row["cancel_at_period_end"]),
            stripe_customer_id=row["stripe_customer_id"],
            stripe_subscription_id=row["stripe_subscription_id"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            plan=plan,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat()


def _slugify(name: str) -> str:
    import re
    slug = name.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug.strip("-")


def _period_end(start: str, cycle: str) -> str:
    dt = datetime.fromisoformat(start)
    if cycle == "annual":
        return (dt + timedelta(days=365)).isoformat()
    if cycle == "lifetime":
        return (dt + timedelta(days=36500)).isoformat()
    # monthly
    return (dt + timedelta(days=30)).isoformat()


def _record_event(
    conn: sqlite3.Connection,
    subscriber_id: int,
    event_type: str,
    plan_id: Optional[int] = None,
    amount: float = 0.0,
    notes: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO subscription_events
           (subscriber_id, event_type, plan_id, amount, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (subscriber_id, event_type, plan_id, amount, notes, _now()),
    )


def _load_plan(conn: sqlite3.Connection, plan_id: int) -> Optional[SubscriptionPlan]:
    row = conn.execute(
        "SELECT * FROM subscription_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    return SubscriptionPlan.from_row(row) if row else None


def _load_plan_by_slug(conn: sqlite3.Connection, slug_or_name: str) -> Optional[SubscriptionPlan]:
    row = conn.execute(
        "SELECT * FROM subscription_plans WHERE slug = ? OR name = ? LIMIT 1",
        (slug_or_name, slug_or_name),
    ).fetchone()
    return SubscriptionPlan.from_row(row) if row else None


def _load_subscriber(conn: sqlite3.Connection, email: str) -> Optional[Subscriber]:
    row = conn.execute(
        "SELECT * FROM subscribers WHERE email = ?", (email,)
    ).fetchone()
    if not row:
        return None
    plan = _load_plan(conn, row["plan_id"])
    return Subscriber.from_row(row, plan=plan)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------

def create_plan(
    name: str,
    price_monthly: float,
    price_annual: Optional[float] = None,
    trial_days: int = 0,
    features_list: Optional[List[str]] = None,
    max_users: int = 1,
    description: str = "",
) -> SubscriptionPlan:
    """Create a new subscription plan. Returns the created SubscriptionPlan."""
    _ensure()
    slug = _slugify(name)
    if price_annual is None:
        price_annual = price_monthly * 10  # default ~2 months free
    features = json.dumps(features_list or [])
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO subscription_plans
               (name, slug, description, price_monthly, price_annual,
                trial_days, features, max_users, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (name, slug, description, price_monthly, price_annual,
             trial_days, features, max_users, now, now),
        )
        conn.commit()
        plan_id = cur.lastrowid
        plan = _load_plan(conn, plan_id)
        return plan
    finally:
        conn.close()


def get_plan(slug_or_name: str) -> Optional[SubscriptionPlan]:
    """Retrieve a plan by slug or name."""
    _ensure()
    conn = _db()
    try:
        return _load_plan_by_slug(conn, slug_or_name)
    finally:
        conn.close()


def list_plans(active_only: bool = True) -> List[SubscriptionPlan]:
    """Return all plans, optionally filtered to active ones."""
    _ensure()
    conn = _db()
    try:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM subscription_plans WHERE status = 'active' ORDER BY price_monthly ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM subscription_plans ORDER BY price_monthly ASC"
            ).fetchall()
        return [SubscriptionPlan.from_row(r) for r in rows]
    finally:
        conn.close()


def update_plan(plan_id: int, **fields) -> bool:
    """Update specific fields on a plan. Returns True on success."""
    _ensure()
    allowed = {
        "name", "description", "price_monthly", "price_annual",
        "trial_days", "features", "max_users", "status",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    # Serialize features if provided as list
    if "features" in updates and isinstance(updates["features"], list):
        updates["features"] = json.dumps(updates["features"])
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [plan_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE subscription_plans SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def archive_plan(plan_id: int) -> bool:
    """Mark a plan as archived so it no longer appears in active listings."""
    return update_plan(plan_id, status="archived")


# ---------------------------------------------------------------------------
# Subscriptions
# ---------------------------------------------------------------------------

def subscribe(
    email: str,
    name: str,
    plan_slug: str,
    billing_cycle: str = "monthly",
    coupon_code: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Subscriber:
    """
    Create a new subscription for a customer. If trial_days > 0 on the plan,
    the subscriber starts in 'trialing' status. Returns the Subscriber.
    """
    _ensure()
    if billing_cycle not in BILLING_CYCLES:
        billing_cycle = "monthly"
    conn = _db()
    try:
        plan = _load_plan_by_slug(conn, plan_slug)
        if plan is None:
            raise ValueError(f"Plan '{plan_slug}' not found")

        now = _now()
        now_dt = datetime.utcnow()

        # Determine trial
        if plan.trial_days > 0:
            status = "trialing"
            trial_ends_at = (now_dt + timedelta(days=plan.trial_days)).isoformat()
        else:
            status = "active"
            trial_ends_at = None

        period_start = now
        period_end = _period_end(now, billing_cycle)

        # Apply coupon if provided
        amount = plan.price_monthly if billing_cycle == "monthly" else plan.price_annual
        if coupon_code:
            try:
                amount = apply_coupon(coupon_code, email)
            except Exception:
                pass  # Ignore invalid coupons silently

        meta_str = json.dumps(metadata or {})

        cur = conn.execute(
            """INSERT INTO subscribers
               (email, name, plan_id, status, billing_cycle, trial_ends_at,
                current_period_start, current_period_end, cancel_at_period_end,
                metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (email, name, plan.id, status, billing_cycle, trial_ends_at,
             period_start, period_end, meta_str, now, now),
        )
        conn.commit()
        sub_id = cur.lastrowid

        # Record creation event
        event_type = "trial_started" if status == "trialing" else "created"
        _record_event(conn, sub_id, event_type, plan_id=plan.id, amount=amount)
        if status == "active":
            _record_event(conn, sub_id, "activated", plan_id=plan.id, amount=amount)
        conn.commit()

        subscriber = _load_subscriber(conn, email)
        return subscriber
    finally:
        conn.close()


def get_subscriber(email: str) -> Optional[Subscriber]:
    """Return a Subscriber by email, or None if not found."""
    _ensure()
    conn = _db()
    try:
        return _load_subscriber(conn, email)
    finally:
        conn.close()


def list_subscribers(
    status: Optional[str] = None,
    plan_slug: Optional[str] = None,
    limit: int = 100,
) -> List[Subscriber]:
    """List subscribers with optional filters on status and plan."""
    _ensure()
    conn = _db()
    try:
        conditions = []
        params: List[Any] = []

        if status:
            conditions.append("s.status = ?")
            params.append(status)

        if plan_slug:
            plan = _load_plan_by_slug(conn, plan_slug)
            if plan:
                conditions.append("s.plan_id = ?")
                params.append(plan.id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        rows = conn.execute(
            f"""SELECT s.* FROM subscribers s
                {where}
                ORDER BY s.created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

        result = []
        for row in rows:
            plan = _load_plan(conn, row["plan_id"])
            result.append(Subscriber.from_row(row, plan=plan))
        return result
    finally:
        conn.close()


def _change_plan(
    email: str,
    new_plan_slug: str,
    event_type: str,
) -> Subscriber:
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, email)
        if sub is None:
            raise ValueError(f"Subscriber '{email}' not found")
        new_plan = _load_plan_by_slug(conn, new_plan_slug)
        if new_plan is None:
            raise ValueError(f"Plan '{new_plan_slug}' not found")
        now = _now()
        conn.execute(
            "UPDATE subscribers SET plan_id = ?, updated_at = ? WHERE email = ?",
            (new_plan.id, now, email),
        )
        _record_event(conn, sub.id, event_type, plan_id=new_plan.id)
        conn.commit()
        return _load_subscriber(conn, email)
    finally:
        conn.close()


def upgrade_plan(email: str, new_plan_slug: str) -> Subscriber:
    """Move a subscriber to a higher-tier plan. Records 'upgraded' event."""
    return _change_plan(email, new_plan_slug, "upgraded")


def downgrade_plan(email: str, new_plan_slug: str) -> Subscriber:
    """Move a subscriber to a lower-tier plan. Records 'downgraded' event."""
    return _change_plan(email, new_plan_slug, "downgraded")


def cancel_subscription(email: str, immediately: bool = False) -> bool:
    """
    Cancel a subscription. If immediately=True, sets status to 'cancelled' now.
    Otherwise sets cancel_at_period_end so access continues until period ends.
    """
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, email)
        if sub is None:
            return False
        now = _now()
        if immediately:
            conn.execute(
                """UPDATE subscribers
                   SET status = 'cancelled', cancel_at_period_end = 0, updated_at = ?
                   WHERE email = ?""",
                (now, email),
            )
        else:
            conn.execute(
                """UPDATE subscribers
                   SET cancel_at_period_end = 1, updated_at = ?
                   WHERE email = ?""",
                (now, email),
            )
        _record_event(
            conn, sub.id, "cancelled",
            plan_id=sub.plan_id,
            notes="immediate" if immediately else "at_period_end",
        )
        conn.commit()
        return True
    finally:
        conn.close()


def reactivate_subscription(email: str) -> bool:
    """Reactivate a cancelled or paused subscription."""
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, email)
        if sub is None:
            return False
        now = _now()
        period_end = _period_end(now, sub.billing_cycle)
        conn.execute(
            """UPDATE subscribers
               SET status = 'active', cancel_at_period_end = 0,
                   current_period_start = ?, current_period_end = ?,
                   updated_at = ?
               WHERE email = ?""",
            (now, period_end, now, email),
        )
        _record_event(conn, sub.id, "reactivated", plan_id=sub.plan_id)
        conn.commit()
        return True
    finally:
        conn.close()


def pause_subscription(email: str) -> bool:
    """Pause a subscription (billing stops, access suspended)."""
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, email)
        if sub is None or sub.status not in ("active", "trialing"):
            return False
        now = _now()
        conn.execute(
            "UPDATE subscribers SET status = 'paused', updated_at = ? WHERE email = ?",
            (now, email),
        )
        _record_event(conn, sub.id, "paused", plan_id=sub.plan_id)
        conn.commit()
        return True
    finally:
        conn.close()


def resume_subscription(email: str) -> bool:
    """Resume a paused subscription."""
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, email)
        if sub is None or sub.status != "paused":
            return False
        now = _now()
        period_end = _period_end(now, sub.billing_cycle)
        conn.execute(
            """UPDATE subscribers
               SET status = 'active', current_period_start = ?,
                   current_period_end = ?, updated_at = ?
               WHERE email = ?""",
            (now, period_end, now, email),
        )
        _record_event(conn, sub.id, "resumed", plan_id=sub.plan_id)
        conn.commit()
        return True
    finally:
        conn.close()


def expire_trials() -> int:
    """
    Transition trial subscribers whose trial has ended.
    Trials that had a payment method activate; others expire.
    Returns the count of subscribers transitioned.
    """
    _ensure()
    conn = _db()
    try:
        now = _now()
        # Find all trialing subscribers whose trial_ends_at has passed
        rows = conn.execute(
            """SELECT * FROM subscribers
               WHERE status = 'trialing' AND trial_ends_at IS NOT NULL
               AND trial_ends_at <= ?""",
            (now,),
        ).fetchall()

        count = 0
        for row in rows:
            # If stripe_customer_id is set, assume payment method exists → activate
            if row["stripe_customer_id"]:
                new_status = "active"
                event = "activated"
                period_end = _period_end(now, row["billing_cycle"])
                conn.execute(
                    """UPDATE subscribers
                       SET status = ?, trial_ends_at = NULL,
                           current_period_start = ?, current_period_end = ?,
                           updated_at = ?
                       WHERE id = ?""",
                    (new_status, now, period_end, now, row["id"]),
                )
            else:
                new_status = "expired"
                event = "trial_ended"
                conn.execute(
                    """UPDATE subscribers
                       SET status = 'expired', updated_at = ?
                       WHERE id = ?""",
                    (now, row["id"]),
                )
            _record_event(conn, row["id"], event, plan_id=row["plan_id"])
            count += 1

        conn.commit()
        return count
    finally:
        conn.close()


def process_renewals() -> dict:
    """
    Process billing renewals for subscribers whose current_period_end has passed.
    Returns {"renewed": int, "failed": int, "expired": int}.
    """
    _ensure()
    conn = _db()
    try:
        now = _now()
        # Active subscribers past their period end
        rows = conn.execute(
            """SELECT s.*, p.price_monthly, p.price_annual, p.status as plan_status
               FROM subscribers s
               JOIN subscription_plans p ON s.plan_id = p.id
               WHERE s.status = 'active'
               AND s.current_period_end <= ?
               AND s.cancel_at_period_end = 0""",
            (now,),
        ).fetchall()

        renewed = 0
        failed = 0

        for row in rows:
            cycle = row["billing_cycle"]
            amount = row["price_annual"] if cycle == "annual" else row["price_monthly"]
            new_start = row["current_period_end"]
            new_end = _period_end(new_start, cycle)

            # In a real system, Stripe charge would happen here.
            # We simulate success if stripe_customer_id is set.
            if row["stripe_customer_id"]:
                conn.execute(
                    """UPDATE subscribers
                       SET current_period_start = ?, current_period_end = ?, updated_at = ?
                       WHERE id = ?""",
                    (new_start, new_end, now, row["id"]),
                )
                _record_event(
                    conn, row["id"], "payment_succeeded",
                    plan_id=row["plan_id"], amount=amount,
                )
                # Create invoice
                conn.execute(
                    """INSERT INTO subscription_invoices
                       (subscriber_id, amount, currency, status, billing_reason,
                        period_start, period_end, paid_at, created_at)
                       VALUES (?, ?, 'USD', 'paid', 'renewal', ?, ?, ?, ?)""",
                    (row["id"], amount, new_start, new_end, now, now),
                )
                renewed += 1
            else:
                conn.execute(
                    "UPDATE subscribers SET status = 'past_due', updated_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                _record_event(
                    conn, row["id"], "payment_failed",
                    plan_id=row["plan_id"], amount=amount,
                )
                failed += 1

        # Handle cancel_at_period_end subscribers whose period ended
        expired_rows = conn.execute(
            """SELECT * FROM subscribers
               WHERE cancel_at_period_end = 1
               AND current_period_end <= ?
               AND status NOT IN ('cancelled', 'expired')""",
            (now,),
        ).fetchall()

        expired = 0
        for row in expired_rows:
            conn.execute(
                "UPDATE subscribers SET status = 'cancelled', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            expired += 1

        conn.commit()
        return {"renewed": renewed, "failed": failed, "expired": expired}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

def create_invoice(
    subscriber_email: str,
    amount: float,
    billing_reason: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> dict:
    """Create an invoice record for a subscriber. Returns the invoice dict."""
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, subscriber_email)
        if sub is None:
            return {"error": f"Subscriber '{subscriber_email}' not found"}
        now = _now()
        cur = conn.execute(
            """INSERT INTO subscription_invoices
               (subscriber_id, amount, currency, status, billing_reason,
                period_start, period_end, created_at)
               VALUES (?, ?, 'USD', 'open', ?, ?, ?, ?)""",
            (sub.id, amount, billing_reason, period_start, period_end, now),
        )
        conn.commit()
        invoice_id = cur.lastrowid
        return {
            "id": invoice_id,
            "subscriber_id": sub.id,
            "subscriber_email": subscriber_email,
            "amount": amount,
            "currency": "USD",
            "status": "open",
            "billing_reason": billing_reason,
            "period_start": period_start,
            "period_end": period_end,
            "created_at": now,
        }
    finally:
        conn.close()


def mark_invoice_paid(invoice_id: int, paid_at: Optional[str] = None) -> bool:
    """Mark an invoice as paid. Returns True on success."""
    _ensure()
    conn = _db()
    try:
        paid_at = paid_at or _now()
        cur = conn.execute(
            "UPDATE subscription_invoices SET status = 'paid', paid_at = ? WHERE id = ?",
            (paid_at, invoice_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_invoices(
    subscriber_email: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
    """Return invoices optionally filtered by subscriber email and status."""
    _ensure()
    conn = _db()
    try:
        conditions = []
        params: List[Any] = []

        if subscriber_email:
            sub = _load_subscriber(conn, subscriber_email)
            if sub:
                conditions.append("i.subscriber_id = ?")
                params.append(sub.id)
            else:
                return []

        if status:
            conditions.append("i.status = ?")
            params.append(status)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(limit)

        rows = conn.execute(
            f"""SELECT i.*, s.email as subscriber_email
                FROM subscription_invoices i
                JOIN subscribers s ON i.subscriber_id = s.id
                {where}
                ORDER BY i.created_at DESC
                LIMIT ?""",
            params,
        ).fetchall()

        return [
            {
                "id": r["id"],
                "subscriber_id": r["subscriber_id"],
                "subscriber_email": r["subscriber_email"],
                "amount": r["amount"],
                "currency": r["currency"],
                "status": r["status"],
                "billing_reason": r["billing_reason"],
                "period_start": r["period_start"],
                "period_end": r["period_end"],
                "paid_at": r["paid_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def record_usage(
    subscriber_email: str,
    metric_name: str,
    count: int = 1,
) -> bool:
    """
    Increment usage for a metric within the subscriber's current period.
    Upserts a usage record for the current billing period.
    """
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, subscriber_email)
        if sub is None:
            return False
        now = _now()
        period_start = sub.current_period_start
        period_end = sub.current_period_end

        existing = conn.execute(
            """SELECT id, usage_count FROM subscription_usage
               WHERE subscriber_id = ? AND metric_name = ?
               AND period_start = ? AND period_end = ?""",
            (sub.id, metric_name, period_start, period_end),
        ).fetchone()

        if existing:
            conn.execute(
                "UPDATE subscription_usage SET usage_count = usage_count + ? WHERE id = ?",
                (count, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT INTO subscription_usage
                   (subscriber_id, metric_name, usage_count, period_start, period_end, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (sub.id, metric_name, count, period_start, period_end, now),
            )
        conn.commit()
        return True
    finally:
        conn.close()


def get_usage(
    subscriber_email: str,
    metric_name: str,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> dict:
    """Return usage data for a subscriber and metric, optionally filtered by period."""
    _ensure()
    conn = _db()
    try:
        sub = _load_subscriber(conn, subscriber_email)
        if sub is None:
            return {"error": f"Subscriber '{subscriber_email}' not found"}

        conditions = ["subscriber_id = ?", "metric_name = ?"]
        params: List[Any] = [sub.id, metric_name]

        if period_start:
            conditions.append("period_start >= ?")
            params.append(period_start)
        if period_end:
            conditions.append("period_end <= ?")
            params.append(period_end)

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"""SELECT * FROM subscription_usage
                WHERE {where}
                ORDER BY period_start DESC""",
            params,
        ).fetchall()

        total = sum(r["usage_count"] for r in rows)
        records = [
            {
                "id": r["id"],
                "metric_name": r["metric_name"],
                "usage_count": r["usage_count"],
                "period_start": r["period_start"],
                "period_end": r["period_end"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        return {
            "subscriber_email": subscriber_email,
            "metric_name": metric_name,
            "total_usage": total,
            "records": records,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------

def create_coupon(
    code: str,
    discount_type: str,
    discount_value: float,
    duration_months: int = 1,
    max_uses: int = 0,
    expires_at: Optional[str] = None,
) -> dict:
    """
    Create a discount coupon. discount_type: 'percent' or 'flat'.
    max_uses=0 means unlimited. Returns the coupon dict.
    """
    _ensure()
    if discount_type not in DISCOUNT_TYPES:
        return {"error": f"Invalid discount_type '{discount_type}'. Must be one of {DISCOUNT_TYPES}"}
    conn = _db()
    try:
        now = _now()
        conn.execute(
            """INSERT INTO subscription_coupons
               (code, discount_type, discount_value, duration_months,
                max_uses, uses, expires_at, created_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (code.upper(), discount_type, discount_value, duration_months,
             max_uses, expires_at, now),
        )
        conn.commit()
        return {
            "code": code.upper(),
            "discount_type": discount_type,
            "discount_value": discount_value,
            "duration_months": duration_months,
            "max_uses": max_uses,
            "uses": 0,
            "expires_at": expires_at,
            "created_at": now,
        }
    except sqlite3.IntegrityError:
        return {"error": f"Coupon code '{code}' already exists"}
    finally:
        conn.close()


def validate_coupon(code: str) -> dict:
    """
    Validate a coupon code. Returns the coupon dict with an added 'valid' bool
    and optional 'reason' explaining why it's invalid.
    """
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM subscription_coupons WHERE code = ?", (code.upper(),)
        ).fetchone()
        if not row:
            return {"valid": False, "reason": "Coupon code not found"}

        result = {
            "id": row["id"],
            "code": row["code"],
            "discount_type": row["discount_type"],
            "discount_value": row["discount_value"],
            "duration_months": row["duration_months"],
            "max_uses": row["max_uses"],
            "uses": row["uses"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
        }

        now = _now()
        if row["expires_at"] and row["expires_at"] < now:
            result["valid"] = False
            result["reason"] = "Coupon has expired"
            return result

        if row["max_uses"] > 0 and row["uses"] >= row["max_uses"]:
            result["valid"] = False
            result["reason"] = "Coupon has reached its maximum uses"
            return result

        result["valid"] = True
        return result
    finally:
        conn.close()


def apply_coupon(code: str, email: str) -> float:
    """
    Apply a coupon for a subscriber. Increments use count and returns the
    discounted amount for the subscriber's plan and billing cycle.
    Raises ValueError if coupon is invalid.
    """
    _ensure()
    conn = _db()
    try:
        validation = validate_coupon(code)
        if not validation.get("valid"):
            raise ValueError(validation.get("reason", "Invalid coupon"))

        sub = _load_subscriber(conn, email)
        if sub is None:
            raise ValueError(f"Subscriber '{email}' not found")

        plan = sub.plan
        if plan is None:
            plan = _load_plan(conn, sub.plan_id)
        if plan is None:
            raise ValueError("Subscriber has no associated plan")

        # Base amount from billing cycle
        if sub.billing_cycle == "annual":
            base = plan.price_annual
        else:
            base = plan.price_monthly

        discount_type = validation["discount_type"]
        discount_value = validation["discount_value"]

        if discount_type == "percent":
            discount_amount = base * (min(discount_value, 100.0) / 100.0)
        else:  # flat
            discount_amount = min(discount_value, base)

        discounted = max(0.0, base - discount_amount)

        # Increment use count
        conn.execute(
            "UPDATE subscription_coupons SET uses = uses + 1 WHERE code = ?",
            (code.upper(),),
        )
        conn.commit()
        return round(discounted, 2)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def mrr() -> float:
    """
    Monthly Recurring Revenue: sum of monthly_value for all active subscribers.
    Annual subscriptions contribute price_annual / 12.
    """
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT s.billing_cycle, p.price_monthly, p.price_annual
               FROM subscribers s
               JOIN subscription_plans p ON s.plan_id = p.id
               WHERE s.status IN ('active', 'trialing')""",
        ).fetchall()

        total = 0.0
        for row in rows:
            if row["billing_cycle"] == "annual":
                total += (row["price_annual"] or 0.0) / 12.0
            elif row["billing_cycle"] == "monthly":
                total += row["price_monthly"] or 0.0
            # lifetime contributes 0 to MRR
        return round(total, 2)
    finally:
        conn.close()


def arr() -> float:
    """Annual Recurring Revenue = MRR × 12."""
    return round(mrr() * 12, 2)


def churn_rate(days: int = 30) -> float:
    """
    Churn rate over the past N days as a percentage.
    = (subscribers cancelled in period / subscribers at start of period) × 100
    """
    _ensure()
    conn = _db()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()

        # Subscribers who cancelled in the period
        cancelled = conn.execute(
            """SELECT COUNT(*) as cnt FROM subscription_events
               WHERE event_type = 'cancelled' AND created_at >= ?""",
            (cutoff,),
        ).fetchone()["cnt"]

        # Total active subscribers at the start of the period (approximated
        # as those created before the cutoff who are still around)
        total_at_start = conn.execute(
            """SELECT COUNT(*) as cnt FROM subscribers
               WHERE created_at <= ?""",
            (cutoff,),
        ).fetchone()["cnt"]

        if total_at_start == 0:
            return 0.0
        return round((cancelled / total_at_start) * 100, 4)
    finally:
        conn.close()


def subscriber_growth(days: int = 30) -> List[dict]:
    """
    Daily new subscriber counts over the past N days.
    Returns a list of {"date": str, "new_subscribers": int}.
    """
    _ensure()
    conn = _db()
    try:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            """SELECT DATE(created_at) as day, COUNT(*) as new_subscribers
               FROM subscribers
               WHERE created_at >= ?
               GROUP BY DATE(created_at)
               ORDER BY day ASC""",
            (cutoff,),
        ).fetchall()
        return [{"date": r["day"], "new_subscribers": r["new_subscribers"]} for r in rows]
    finally:
        conn.close()


def plan_distribution() -> List[dict]:
    """
    Subscriber count and MRR per plan for active subscribers.
    Returns list of dicts sorted by MRR descending.
    """
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT p.id, p.name, p.slug, p.price_monthly, p.price_annual,
                      s.billing_cycle,
                      COUNT(s.id) as subscriber_count
               FROM subscription_plans p
               LEFT JOIN subscribers s ON s.plan_id = p.id
                   AND s.status IN ('active', 'trialing')
               GROUP BY p.id, p.name, p.slug, p.price_monthly, p.price_annual, s.billing_cycle
               ORDER BY p.price_monthly DESC""",
        ).fetchall()

        # Aggregate by plan
        plan_data: Dict[int, dict] = {}
        for row in rows:
            pid = row["id"]
            if pid not in plan_data:
                plan_data[pid] = {
                    "plan_id": pid,
                    "plan_name": row["name"],
                    "plan_slug": row["slug"],
                    "subscriber_count": 0,
                    "mrr": 0.0,
                }
            count = row["subscriber_count"] or 0
            plan_data[pid]["subscriber_count"] += count
            if row["billing_cycle"] == "annual":
                plan_data[pid]["mrr"] += (row["price_annual"] or 0.0) / 12.0 * count
            elif row["billing_cycle"] == "monthly":
                plan_data[pid]["mrr"] += (row["price_monthly"] or 0.0) * count

        result = sorted(plan_data.values(), key=lambda x: x["mrr"], reverse=True)
        for item in result:
            item["mrr"] = round(item["mrr"], 2)
        return result
    finally:
        conn.close()


def ltv_estimate(plan_slug: Optional[str] = None) -> float:
    """
    Estimate average lifetime value:
    avg_monthly_revenue × avg_months_active for (optionally) a specific plan.
    """
    _ensure()
    conn = _db()
    try:
        if plan_slug:
            plan = _load_plan_by_slug(conn, plan_slug)
            if not plan:
                return 0.0
            rows = conn.execute(
                """SELECT s.created_at, s.billing_cycle,
                          p.price_monthly, p.price_annual
                   FROM subscribers s
                   JOIN subscription_plans p ON s.plan_id = p.id
                   WHERE s.plan_id = ?
                   AND s.status IN ('active', 'trialing', 'cancelled', 'expired')""",
                (plan.id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.created_at, s.billing_cycle,
                          p.price_monthly, p.price_annual
                   FROM subscribers s
                   JOIN subscription_plans p ON s.plan_id = p.id
                   WHERE s.status IN ('active', 'trialing', 'cancelled', 'expired')""",
            ).fetchall()

        if not rows:
            return 0.0

        total_revenue = 0.0
        total_months = 0.0
        for row in rows:
            try:
                created = datetime.fromisoformat(row["created_at"])
                months = max(1, (datetime.utcnow() - created).days / 30.0)
            except Exception:
                months = 1.0

            if row["billing_cycle"] == "annual":
                monthly_val = (row["price_annual"] or 0.0) / 12.0
            else:
                monthly_val = row["price_monthly"] or 0.0

            total_revenue += monthly_val * months
            total_months += months

        if len(rows) == 0:
            return 0.0

        avg_ltv = total_revenue / len(rows)
        return round(avg_ltv, 2)
    finally:
        conn.close()


def cohort_retention(months: int = 6) -> List[dict]:
    """
    Monthly cohort retention rates for the past N months.
    Each entry: {"cohort_month": str, "cohort_size": int, "retained": int, "retention_rate": float}
    """
    _ensure()
    conn = _db()
    try:
        result = []
        now = datetime.utcnow()

        for i in range(months, 0, -1):
            # Cohort: subscribers who joined in month i months ago
            cohort_start = (now - timedelta(days=30 * i)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            cohort_end = (cohort_start + timedelta(days=32)).replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )

            cohort_start_s = cohort_start.isoformat()
            cohort_end_s = cohort_end.isoformat()

            cohort_size = conn.execute(
                """SELECT COUNT(*) as cnt FROM subscribers
                   WHERE created_at >= ? AND created_at < ?""",
                (cohort_start_s, cohort_end_s),
            ).fetchone()["cnt"]

            retained = conn.execute(
                """SELECT COUNT(*) as cnt FROM subscribers
                   WHERE created_at >= ? AND created_at < ?
                   AND status IN ('active', 'trialing')""",
                (cohort_start_s, cohort_end_s),
            ).fetchone()["cnt"]

            retention_rate = round((retained / cohort_size * 100), 2) if cohort_size > 0 else 0.0

            result.append({
                "cohort_month": cohort_start.strftime("%Y-%m"),
                "cohort_size": cohort_size,
                "retained": retained,
                "retention_rate": retention_rate,
            })

        return result
    finally:
        conn.close()


def subscription_summary() -> dict:
    """
    High-level subscription summary: MRR, ARR, counts, churn, top plan, avg LTV.
    """
    _ensure()
    conn = _db()
    try:
        current_mrr = mrr()
        current_arr = arr()

        total_active = conn.execute(
            "SELECT COUNT(*) as cnt FROM subscribers WHERE status IN ('active', 'trialing')"
        ).fetchone()["cnt"]

        total_trialing = conn.execute(
            "SELECT COUNT(*) as cnt FROM subscribers WHERE status = 'trialing'"
        ).fetchone()["cnt"]

        total_cancelled = conn.execute(
            "SELECT COUNT(*) as cnt FROM subscribers WHERE status = 'cancelled'"
        ).fetchone()["cnt"]

        total_all = conn.execute(
            "SELECT COUNT(*) as cnt FROM subscribers"
        ).fetchone()["cnt"]

        current_churn = churn_rate(30)
        avg_ltv = ltv_estimate()
        distribution = plan_distribution()
        top_plan = distribution[0]["plan_name"] if distribution else None

        return {
            "mrr": current_mrr,
            "arr": current_arr,
            "total_active": total_active,
            "total_trialing": total_trialing,
            "total_cancelled": total_cancelled,
            "total_subscribers": total_all,
            "churn_rate_30d": current_churn,
            "avg_ltv": avg_ltv,
            "top_plan": top_plan,
            "plan_distribution": distribution,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("sm_create_plan", "Create a new subscription plan with pricing tiers.")
def sm_create_plan_tool(
    name: str,
    price_monthly: float,
    price_annual: Optional[float] = None,
    trial_days: int = 0,
    **kwargs,
) -> str:
    try:
        _price_monthly = float(price_monthly)
        _price_annual = float(price_annual) if price_annual is not None else None
        _trial_days = int(trial_days)
        plan = create_plan(
            name=name,
            price_monthly=_price_monthly,
            price_annual=_price_annual,
            trial_days=_trial_days,
        )
        return json.dumps({"ok": True, "plan": plan.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_subscribe", "Subscribe a new customer to a plan by email.")
def sm_subscribe_tool(
    email: str,
    name: str,
    plan_slug: str,
    billing_cycle: str = "monthly",
    coupon_code: Optional[str] = None,
    **kwargs,
) -> str:
    try:
        sub = subscribe(
            email=email,
            name=name,
            plan_slug=plan_slug,
            billing_cycle=billing_cycle,
            coupon_code=coupon_code or None,
        )
        return json.dumps({"ok": True, "subscriber": sub.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_cancel", "Cancel a subscription by subscriber email.")
def sm_cancel_tool(email: str, immediately: bool = False, **kwargs) -> str:
    try:
        _immediately = bool(immediately)
        success = cancel_subscription(email=email, immediately=_immediately)
        return json.dumps({"ok": success, "email": email, "immediately": _immediately})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_upgrade", "Upgrade a subscriber to a higher-tier plan.")
def sm_upgrade_tool(email: str, new_plan_slug: str, **kwargs) -> str:
    try:
        sub = upgrade_plan(email=email, new_plan_slug=new_plan_slug)
        return json.dumps({"ok": True, "subscriber": sub.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_mrr", "Return Monthly Recurring Revenue, ARR, and subscriber counts.")
def sm_mrr_tool(**kwargs) -> str:
    try:
        current_mrr = mrr()
        current_arr = arr()
        conn = _db()
        try:
            total_active = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscribers WHERE status IN ('active', 'trialing')"
            ).fetchone()["cnt"]
            total_all = conn.execute(
                "SELECT COUNT(*) as cnt FROM subscribers"
            ).fetchone()["cnt"]
        finally:
            conn.close()
        return json.dumps({
            "ok": True,
            "mrr": current_mrr,
            "arr": current_arr,
            "total_active_subscribers": total_active,
            "total_subscribers": total_all,
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_plan_distribution", "Show subscriber count and MRR breakdown per subscription plan.")
def sm_plan_distribution_tool(**kwargs) -> str:
    try:
        distribution = plan_distribution()
        return json.dumps({"ok": True, "plans": distribution})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_subscription_summary", "Return a full subscription business summary: MRR, ARR, churn, LTV, top plan.")
def sm_subscription_summary_tool(**kwargs) -> str:
    try:
        summary = subscription_summary()
        return json.dumps({"ok": True, **summary})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_churn_rate", "Calculate subscriber churn rate over the past N days.")
def sm_churn_rate_tool(days: int = 30, **kwargs) -> str:
    try:
        _days = int(days)
        rate = churn_rate(_days)
        return json.dumps({"ok": True, "churn_rate_percent": rate, "period_days": _days})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("sm_create_coupon", "Create a discount coupon code for subscriptions.")
def sm_create_coupon_tool(
    code: str,
    discount_type: str,
    discount_value: float,
    max_uses: int = 0,
    **kwargs,
) -> str:
    try:
        _discount_value = float(discount_value)
        _max_uses = int(max_uses)
        coupon = create_coupon(
            code=code,
            discount_type=discount_type,
            discount_value=_discount_value,
            max_uses=_max_uses,
        )
        return json.dumps({"ok": "error" not in coupon, **coupon})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
