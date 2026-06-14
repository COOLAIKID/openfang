"""
Pricing Engine — dynamic pricing rules, A/B price tests, discount ladders,
subscription billing intervals, and LTV-based pricing recommendations.

Agents can use this module to set optimal prices, run tests, and apply
time-sensitive discounts to maximize revenue per user.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRICING_MODELS = [
    "flat",
    "tiered",
    "per_seat",
    "usage_based",
    "freemium",
    "subscription",
    "one_time",
    "pwyw",           # pay-what-you-want
    "auction",
    "dynamic",
]

DISCOUNT_TYPES = [
    "percent",
    "fixed",
    "bogo",           # buy one get one
    "free_shipping",
    "bundle",
    "loyalty",
    "referral",
]

BILLING_INTERVALS = [
    "once",
    "weekly",
    "monthly",
    "quarterly",
    "semiannual",
    "annual",
    "biennial",
]

# ---------------------------------------------------------------------------
# Schema
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
            CREATE TABLE IF NOT EXISTS pricing_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                product_id      INTEGER,
                pricing_model   TEXT NOT NULL DEFAULT 'flat',
                base_price      REAL NOT NULL,
                currency        TEXT NOT NULL DEFAULT 'USD',
                billing_interval TEXT NOT NULL DEFAULT 'once',
                min_price       REAL,
                max_price       REAL,
                is_active       INTEGER NOT NULL DEFAULT 1,
                priority        INTEGER NOT NULL DEFAULT 0,
                conditions      TEXT DEFAULT '{}',
                metadata        TEXT DEFAULT '{}',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_tiers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id     INTEGER NOT NULL REFERENCES pricing_rules(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                price       REAL NOT NULL,
                up_to       REAL,
                features    TEXT DEFAULT '[]',
                sort_order  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS price_tests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                product_id      INTEGER,
                rule_id         INTEGER REFERENCES pricing_rules(id),
                status          TEXT NOT NULL DEFAULT 'running',
                variant_a_price REAL NOT NULL,
                variant_b_price REAL NOT NULL,
                variant_a_name  TEXT NOT NULL DEFAULT 'Control',
                variant_b_name  TEXT NOT NULL DEFAULT 'Variant',
                traffic_split   REAL NOT NULL DEFAULT 0.5,
                a_impressions   INTEGER NOT NULL DEFAULT 0,
                b_impressions   INTEGER NOT NULL DEFAULT 0,
                a_conversions   INTEGER NOT NULL DEFAULT 0,
                b_conversions   INTEGER NOT NULL DEFAULT 0,
                a_revenue       REAL NOT NULL DEFAULT 0.0,
                b_revenue       REAL NOT NULL DEFAULT 0.0,
                winner          TEXT,
                confidence      REAL,
                started_at      TEXT NOT NULL,
                ended_at        TEXT,
                metadata        TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS discount_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                discount_type   TEXT NOT NULL DEFAULT 'percent',
                discount_value  REAL NOT NULL,
                product_id      INTEGER,
                min_order_value REAL NOT NULL DEFAULT 0.0,
                max_discount    REAL,
                start_at        TEXT,
                end_at          TEXT,
                usage_limit     INTEGER,
                usage_count     INTEGER NOT NULL DEFAULT 0,
                customer_limit  INTEGER NOT NULL DEFAULT 1,
                is_active       INTEGER NOT NULL DEFAULT 1,
                stackable       INTEGER NOT NULL DEFAULT 0,
                conditions      TEXT DEFAULT '{}',
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ltv_estimates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                segment         TEXT NOT NULL UNIQUE,
                avg_ltv         REAL NOT NULL DEFAULT 0.0,
                avg_order_value REAL NOT NULL DEFAULT 0.0,
                avg_orders      REAL NOT NULL DEFAULT 0.0,
                churn_rate      REAL NOT NULL DEFAULT 0.0,
                acquisition_cost REAL NOT NULL DEFAULT 0.0,
                margin_pct      REAL NOT NULL DEFAULT 100.0,
                updated_at      TEXT NOT NULL,
                metadata        TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL,
                old_price   REAL NOT NULL,
                new_price   REAL NOT NULL,
                reason      TEXT,
                changed_by  TEXT,
                changed_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pricing_rules_product  ON pricing_rules(product_id);
            CREATE INDEX IF NOT EXISTS idx_price_tests_product    ON price_tests(product_id);
            CREATE INDEX IF NOT EXISTS idx_price_tests_status     ON price_tests(status);
            CREATE INDEX IF NOT EXISTS idx_discount_rules_product ON discount_rules(product_id);
            CREATE INDEX IF NOT EXISTS idx_price_history_product  ON price_history(product_id);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PriceTier:
    name: str
    price: float
    up_to: Optional[float] = None
    features: List[str] = field(default_factory=list)
    sort_order: int = 0
    id: Optional[int] = None
    rule_id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "price": self.price,
            "up_to": self.up_to,
            "features": self.features,
            "sort_order": self.sort_order,
        }


@dataclass
class PricingRule:
    name: str
    base_price: float
    product_id: Optional[int] = None
    pricing_model: str = "flat"
    currency: str = "USD"
    billing_interval: str = "once"
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    is_active: bool = True
    priority: int = 0
    conditions: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    id: Optional[int] = None
    tiers: List[PriceTier] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "product_id": self.product_id,
            "pricing_model": self.pricing_model,
            "base_price": self.base_price,
            "currency": self.currency,
            "billing_interval": self.billing_interval,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "is_active": self.is_active,
            "priority": self.priority,
            "conditions": self.conditions,
            "tiers": [t.to_dict() for t in self.tiers],
            "created_at": self.created_at,
        }


@dataclass
class PriceTest:
    name: str
    variant_a_price: float
    variant_b_price: float
    product_id: Optional[int] = None
    rule_id: Optional[int] = None
    status: str = "running"
    variant_a_name: str = "Control"
    variant_b_name: str = "Variant"
    traffic_split: float = 0.5
    a_impressions: int = 0
    b_impressions: int = 0
    a_conversions: int = 0
    b_conversions: int = 0
    a_revenue: float = 0.0
    b_revenue: float = 0.0
    winner: Optional[str] = None
    confidence: Optional[float] = None
    started_at: str = ""
    ended_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    @property
    def a_conversion_rate(self) -> float:
        return self.a_conversions / max(self.a_impressions, 1)

    @property
    def b_conversion_rate(self) -> float:
        return self.b_conversions / max(self.b_impressions, 1)

    @property
    def a_arpu(self) -> float:
        return self.a_revenue / max(self.a_impressions, 1)

    @property
    def b_arpu(self) -> float:
        return self.b_revenue / max(self.b_impressions, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "product_id": self.product_id,
            "status": self.status,
            "variant_a": {
                "name": self.variant_a_name,
                "price": self.variant_a_price,
                "impressions": self.a_impressions,
                "conversions": self.a_conversions,
                "conversion_rate": round(self.a_conversion_rate * 100, 2),
                "revenue": self.a_revenue,
                "arpu": round(self.a_arpu, 4),
            },
            "variant_b": {
                "name": self.variant_b_name,
                "price": self.variant_b_price,
                "impressions": self.b_impressions,
                "conversions": self.b_conversions,
                "conversion_rate": round(self.b_conversion_rate * 100, 2),
                "revenue": self.b_revenue,
                "arpu": round(self.b_arpu, 4),
            },
            "traffic_split": self.traffic_split,
            "winner": self.winner,
            "confidence": self.confidence,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _rule_from_row(row: sqlite3.Row, tiers: Optional[List[PriceTier]] = None) -> PricingRule:
    return PricingRule(
        id=row["id"],
        name=row["name"],
        product_id=row["product_id"],
        pricing_model=row["pricing_model"],
        base_price=row["base_price"],
        currency=row["currency"],
        billing_interval=row["billing_interval"],
        min_price=row["min_price"],
        max_price=row["max_price"],
        is_active=bool(row["is_active"]),
        priority=row["priority"],
        conditions=json.loads(row["conditions"] or "{}"),
        metadata=json.loads(row["metadata"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        tiers=tiers or [],
    )


def _test_from_row(row: sqlite3.Row) -> PriceTest:
    return PriceTest(
        id=row["id"],
        name=row["name"],
        product_id=row["product_id"],
        rule_id=row["rule_id"],
        status=row["status"],
        variant_a_price=row["variant_a_price"],
        variant_b_price=row["variant_b_price"],
        variant_a_name=row["variant_a_name"],
        variant_b_name=row["variant_b_name"],
        traffic_split=row["traffic_split"],
        a_impressions=row["a_impressions"] or 0,
        b_impressions=row["b_impressions"] or 0,
        a_conversions=row["a_conversions"] or 0,
        b_conversions=row["b_conversions"] or 0,
        a_revenue=row["a_revenue"] or 0.0,
        b_revenue=row["b_revenue"] or 0.0,
        winner=row["winner"],
        confidence=row["confidence"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


# ---------------------------------------------------------------------------
# Pricing rules CRUD
# ---------------------------------------------------------------------------

def create_pricing_rule(
    name: str,
    base_price: float,
    product_id: Optional[int] = None,
    pricing_model: str = "flat",
    currency: str = "USD",
    billing_interval: str = "once",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    priority: int = 0,
    conditions: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> PricingRule:
    """Create a new pricing rule."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO pricing_rules
               (name, product_id, pricing_model, base_price, currency,
                billing_interval, min_price, max_price, priority, conditions, metadata,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, product_id, pricing_model, base_price, currency,
                billing_interval, min_price, max_price, priority,
                json.dumps(conditions or {}), json.dumps(metadata or {}), now, now,
            ),
        )
        conn.commit()
        return PricingRule(
            id=cur.lastrowid,
            name=name,
            product_id=product_id,
            pricing_model=pricing_model,
            base_price=base_price,
            currency=currency,
            billing_interval=billing_interval,
            min_price=min_price,
            max_price=max_price,
            priority=priority,
            conditions=conditions or {},
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Pricing rule '{name}' already exists")
    finally:
        conn.close()


def get_pricing_rule(name: str) -> Optional[PricingRule]:
    """Fetch a pricing rule by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM pricing_rules WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        tier_rows = conn.execute(
            "SELECT * FROM price_tiers WHERE rule_id = ? ORDER BY sort_order", (row["id"],)
        ).fetchall()
        tiers = [
            PriceTier(
                id=t["id"],
                rule_id=t["rule_id"],
                name=t["name"],
                price=t["price"],
                up_to=t["up_to"],
                features=json.loads(t["features"] or "[]"),
                sort_order=t["sort_order"],
            )
            for t in tier_rows
        ]
        return _rule_from_row(row, tiers)
    finally:
        conn.close()


def list_pricing_rules(product_id: Optional[int] = None, active_only: bool = True) -> List[PricingRule]:
    """List pricing rules."""
    _ensure()
    clauses = []
    params: List[Any] = []
    if active_only:
        clauses.append("is_active = 1")
    if product_id is not None:
        clauses.append("product_id = ?")
        params.append(product_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM pricing_rules {where} ORDER BY priority DESC, created_at",
            params,
        ).fetchall()
        rules = []
        for r in rows:
            tier_rows = conn.execute(
                "SELECT * FROM price_tiers WHERE rule_id = ? ORDER BY sort_order", (r["id"],)
            ).fetchall()
            tiers = [
                PriceTier(
                    id=t["id"],
                    rule_id=t["rule_id"],
                    name=t["name"],
                    price=t["price"],
                    up_to=t["up_to"],
                    features=json.loads(t["features"] or "[]"),
                    sort_order=t["sort_order"],
                )
                for t in tier_rows
            ]
            rules.append(_rule_from_row(r, tiers))
        return rules
    finally:
        conn.close()


def add_tier(
    rule_name: str,
    tier_name: str,
    price: float,
    up_to: Optional[float] = None,
    features: Optional[List[str]] = None,
    sort_order: int = 0,
) -> PriceTier:
    """Add a price tier to a pricing rule."""
    _ensure()
    rule = get_pricing_rule(rule_name)
    if not rule:
        raise ValueError(f"Pricing rule '{rule_name}' not found")
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO price_tiers (rule_id, name, price, up_to, features, sort_order)
               VALUES (?,?,?,?,?,?)""",
            (rule.id, tier_name, price, up_to, json.dumps(features or []), sort_order),
        )
        conn.commit()
        return PriceTier(
            id=cur.lastrowid,
            rule_id=rule.id,
            name=tier_name,
            price=price,
            up_to=up_to,
            features=features or [],
            sort_order=sort_order,
        )
    finally:
        conn.close()


def resolve_price(
    product_id: int,
    quantity: float = 1.0,
    segment: str = "",
    coupon_code: str = "",
) -> Dict[str, Any]:
    """Determine the correct price for a product given context."""
    _ensure()
    rules = list_pricing_rules(product_id=product_id, active_only=True)
    if not rules:
        return {"product_id": product_id, "price": None, "note": "no active rules"}

    rule = rules[0]  # highest priority
    price = rule.base_price

    # Tiered pricing
    if rule.pricing_model == "tiered" and rule.tiers:
        for tier in sorted(rule.tiers, key=lambda t: t.sort_order):
            if tier.up_to is None or quantity <= tier.up_to:
                price = tier.price
                break

    # Per-seat
    if rule.pricing_model == "per_seat":
        price = rule.base_price * quantity

    # Usage-based: quantity IS usage, price is per-unit
    if rule.pricing_model == "usage_based":
        price = rule.base_price * quantity

    # Apply min/max bounds
    if rule.min_price is not None:
        price = max(price, rule.min_price)
    if rule.max_price is not None:
        price = min(price, rule.max_price)

    # Check discounts
    discount_applied = 0.0
    if coupon_code:
        from . import product_manager as _pm
        try:
            v = _pm.validate_coupon(coupon_code)
            if v.get("valid"):
                if v["discount_type"] == "percent":
                    discount_applied = price * v["discount_value"] / 100
                else:
                    discount_applied = v["discount_value"]
        except Exception:
            pass

    final_price = max(0.0, price - discount_applied)
    annual_value = _to_annual(final_price, rule.billing_interval)

    return {
        "product_id": product_id,
        "rule_name": rule.name,
        "pricing_model": rule.pricing_model,
        "base_price": rule.base_price,
        "resolved_price": round(price, 2),
        "discount_applied": round(discount_applied, 2),
        "final_price": round(final_price, 2),
        "currency": rule.currency,
        "billing_interval": rule.billing_interval,
        "annual_equivalent": round(annual_value, 2),
    }


def _to_annual(price: float, interval: str) -> float:
    multipliers = {
        "once": 1.0,
        "weekly": 52.0,
        "monthly": 12.0,
        "quarterly": 4.0,
        "semiannual": 2.0,
        "annual": 1.0,
        "biennial": 0.5,
    }
    return price * multipliers.get(interval, 1.0)


# ---------------------------------------------------------------------------
# Price history
# ---------------------------------------------------------------------------

def record_price_change(
    product_id: int,
    old_price: float,
    new_price: float,
    reason: str = "",
    changed_by: str = "system",
) -> None:
    """Log a price change for audit purposes."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO price_history (product_id, old_price, new_price, reason, changed_by, changed_at)
               VALUES (?,?,?,?,?,?)""",
            (product_id, old_price, new_price, reason, changed_by,
             datetime.utcnow().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_price_history(product_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Return price change history for a product."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT * FROM price_history WHERE product_id = ?
               ORDER BY changed_at DESC LIMIT ?""",
            (product_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A/B Price tests
# ---------------------------------------------------------------------------

def create_price_test(
    name: str,
    variant_a_price: float,
    variant_b_price: float,
    product_id: Optional[int] = None,
    variant_a_name: str = "Control",
    variant_b_name: str = "Variant",
    traffic_split: float = 0.5,
    metadata: Optional[Dict[str, Any]] = None,
) -> PriceTest:
    """Create a new A/B price test."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO price_tests
               (name, product_id, variant_a_price, variant_b_price,
                variant_a_name, variant_b_name, traffic_split, started_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                name, product_id, variant_a_price, variant_b_price,
                variant_a_name, variant_b_name, traffic_split, now,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return PriceTest(
            id=cur.lastrowid,
            name=name,
            product_id=product_id,
            status="running",
            variant_a_price=variant_a_price,
            variant_b_price=variant_b_price,
            variant_a_name=variant_a_name,
            variant_b_name=variant_b_name,
            traffic_split=traffic_split,
            started_at=now,
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Price test '{name}' already exists")
    finally:
        conn.close()


def get_price_test(name: str) -> Optional[PriceTest]:
    """Fetch a price test by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM price_tests WHERE name = ?", (name,)).fetchone()
        return _test_from_row(row) if row else None
    finally:
        conn.close()


def assign_price_variant(test_name: str) -> Dict[str, Any]:
    """Randomly assign a visitor to variant A or B based on traffic_split."""
    _ensure()
    test = get_price_test(test_name)
    if not test or test.status != "running":
        return {"variant": "a", "price": None, "error": "test not running"}

    variant = "b" if random.random() < (1 - test.traffic_split) else "a"
    price = test.variant_a_price if variant == "a" else test.variant_b_price
    col = f"{'a' if variant == 'a' else 'b'}_impressions"

    conn = _db()
    try:
        conn.execute(
            f"UPDATE price_tests SET {col} = {col} + 1 WHERE name = ?", (test_name,)
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "variant": variant,
        "price": price,
        "test": test_name,
        "name": test.variant_a_name if variant == "a" else test.variant_b_name,
    }


def record_price_test_conversion(
    test_name: str,
    variant: str,
    amount: float,
) -> bool:
    """Record a conversion for a price test variant."""
    _ensure()
    if variant not in ("a", "b"):
        return False
    conn = _db()
    try:
        col_conv = f"{variant}_conversions"
        col_rev = f"{variant}_revenue"
        conn.execute(
            f"UPDATE price_tests SET {col_conv} = {col_conv} + 1, {col_rev} = {col_rev} + ? WHERE name = ?",
            (amount, test_name),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _chi_square_p_value(a_conv: int, a_total: int, b_conv: int, b_total: int) -> float:
    """Approximate p-value using chi-square test (2x2 contingency table)."""
    if a_total == 0 or b_total == 0:
        return 1.0
    n = a_total + b_total
    a_non = a_total - a_conv
    b_non = b_total - b_conv
    expected_a_conv = (a_conv + b_conv) * a_total / n
    expected_b_conv = (a_conv + b_conv) * b_total / n
    expected_a_non = (a_non + b_non) * a_total / n
    expected_b_non = (a_non + b_non) * b_total / n

    def _chi_term(obs: float, exp: float) -> float:
        if exp == 0:
            return 0.0
        return (obs - exp) ** 2 / exp

    chi2 = (
        _chi_term(a_conv, expected_a_conv)
        + _chi_term(b_conv, expected_b_conv)
        + _chi_term(a_non, expected_a_non)
        + _chi_term(b_non, expected_b_non)
    )
    # p-value approximation for chi2 with df=1
    p = math.exp(-0.5 * chi2) if chi2 < 30 else 0.0
    return min(p, 1.0)


def analyze_price_test(test_name: str) -> Dict[str, Any]:
    """Calculate statistical significance and recommend a winner."""
    _ensure()
    test = get_price_test(test_name)
    if not test:
        return {"error": "Test not found"}

    p_value = _chi_square_p_value(
        test.a_conversions, test.a_impressions,
        test.b_conversions, test.b_impressions,
    )
    confidence = round((1 - p_value) * 100, 1)
    is_significant = confidence >= 95.0

    a_arpu = test.a_arpu
    b_arpu = test.b_arpu

    if is_significant:
        if b_arpu > a_arpu:
            winner = "b"
            lift = round((b_arpu - a_arpu) / max(a_arpu, 0.01) * 100, 2)
        else:
            winner = "a"
            lift = round((a_arpu - b_arpu) / max(b_arpu, 0.01) * 100, 2)
    else:
        winner = None
        lift = 0.0

    return {
        "test": test.to_dict(),
        "p_value": round(p_value, 4),
        "confidence_pct": confidence,
        "is_significant": is_significant,
        "recommended_winner": winner,
        "revenue_lift_pct": lift,
        "min_sample_needed": _min_sample_size(0.05, 0.8, test.a_conversion_rate),
    }


def _min_sample_size(alpha: float, power: float, baseline_rate: float) -> int:
    """Estimate minimum sample size per variant for an 80% powered test."""
    if baseline_rate <= 0 or baseline_rate >= 1:
        return 1000
    z_alpha = 1.96  # two-tailed 95%
    z_beta = 0.842  # 80% power
    p = baseline_rate
    n = ((z_alpha + z_beta) ** 2 * p * (1 - p)) / (0.05 * p) ** 2
    return max(int(n), 100)


def conclude_price_test(test_name: str, winner: Optional[str] = None) -> Dict[str, Any]:
    """Mark a price test as concluded with a winner."""
    _ensure()
    analysis = analyze_price_test(test_name)
    final_winner = winner or analysis.get("recommended_winner")
    confidence = analysis.get("confidence_pct", 0)

    conn = _db()
    try:
        conn.execute(
            """UPDATE price_tests
               SET status = 'concluded', winner = ?, confidence = ?, ended_at = ?
               WHERE name = ?""",
            (final_winner, confidence, datetime.utcnow().isoformat(), test_name),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "test": test_name,
        "winner": final_winner,
        "confidence": confidence,
        "analysis": analysis,
    }


def list_price_tests(
    status: Optional[str] = None,
    product_id: Optional[int] = None,
) -> List[PriceTest]:
    """List price tests."""
    _ensure()
    clauses = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM price_tests {where} ORDER BY started_at DESC", params
        ).fetchall()
        return [_test_from_row(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Discount rules
# ---------------------------------------------------------------------------

def create_discount_rule(
    name: str,
    discount_type: str = "percent",
    discount_value: float = 10.0,
    product_id: Optional[int] = None,
    min_order_value: float = 0.0,
    max_discount: Optional[float] = None,
    start_at: Optional[str] = None,
    end_at: Optional[str] = None,
    usage_limit: Optional[int] = None,
    stackable: bool = False,
    conditions: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a discount rule."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO discount_rules
               (name, discount_type, discount_value, product_id, min_order_value,
                max_discount, start_at, end_at, usage_limit, stackable, conditions, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, discount_type, discount_value, product_id, min_order_value,
                max_discount, start_at, end_at, usage_limit, int(stackable),
                json.dumps(conditions or {}), now,
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "name": name,
            "discount_type": discount_type,
            "discount_value": discount_value,
            "product_id": product_id,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Discount rule '{name}' already exists")
    finally:
        conn.close()


def active_discounts(product_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return currently active discount rules."""
    _ensure()
    now = datetime.utcnow().isoformat()
    clauses = [
        "is_active = 1",
        "(start_at IS NULL OR start_at <= ?)",
        "(end_at IS NULL OR end_at >= ?)",
        "(usage_limit IS NULL OR usage_count < usage_limit)",
    ]
    params: List[Any] = [now, now]
    if product_id is not None:
        clauses.append("(product_id IS NULL OR product_id = ?)")
        params.append(product_id)
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM discount_rules WHERE {' AND '.join(clauses)} ORDER BY discount_value DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def apply_discount(
    price: float,
    discount_type: str,
    discount_value: float,
    max_discount: Optional[float] = None,
) -> Tuple[float, float]:
    """Apply a single discount. Returns (final_price, discount_amount)."""
    if discount_type == "percent":
        discount_amount = price * discount_value / 100.0
    elif discount_type == "fixed":
        discount_amount = discount_value
    else:
        discount_amount = 0.0

    if max_discount is not None:
        discount_amount = min(discount_amount, max_discount)

    final_price = max(0.0, price - discount_amount)
    return round(final_price, 2), round(discount_amount, 2)


def increment_discount_usage(name: str) -> bool:
    """Increment the usage counter for a discount rule."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE discount_rules SET usage_count = usage_count + 1 WHERE name = ?", (name,)
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# LTV estimates
# ---------------------------------------------------------------------------

def upsert_ltv_estimate(
    segment: str,
    avg_ltv: float,
    avg_order_value: float = 0.0,
    avg_orders: float = 0.0,
    churn_rate: float = 0.0,
    acquisition_cost: float = 0.0,
    margin_pct: float = 100.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Insert or update an LTV estimate for a customer segment."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO ltv_estimates
               (segment, avg_ltv, avg_order_value, avg_orders, churn_rate,
                acquisition_cost, margin_pct, updated_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(segment) DO UPDATE SET
                 avg_ltv = excluded.avg_ltv,
                 avg_order_value = excluded.avg_order_value,
                 avg_orders = excluded.avg_orders,
                 churn_rate = excluded.churn_rate,
                 acquisition_cost = excluded.acquisition_cost,
                 margin_pct = excluded.margin_pct,
                 updated_at = excluded.updated_at,
                 metadata = excluded.metadata""",
            (
                segment, avg_ltv, avg_order_value, avg_orders, churn_rate,
                acquisition_cost, margin_pct, now, json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        return {
            "segment": segment,
            "avg_ltv": avg_ltv,
            "roas_1x": round(avg_ltv / max(acquisition_cost, 0.01), 2),
            "payback_months": round(acquisition_cost / max(avg_order_value / 12, 0.01), 1),
        }
    finally:
        conn.close()


def get_ltv_estimates() -> List[Dict[str, Any]]:
    """Return all LTV estimates."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM ltv_estimates ORDER BY avg_ltv DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def recommend_price(
    product_id: int,
    segment: str = "default",
    margin_target_pct: float = 70.0,
) -> Dict[str, Any]:
    """Suggest an optimal price based on LTV data and margin targets."""
    _ensure()
    conn = _db()
    try:
        ltv_row = conn.execute(
            "SELECT * FROM ltv_estimates WHERE segment = ?", (segment,)
        ).fetchone()
        ltv = dict(ltv_row) if ltv_row else {"avg_ltv": 0, "avg_order_value": 0}

        # Historical conversion data
        hist = conn.execute(
            """SELECT
                   COUNT(*) as sales,
                   AVG(amount) as avg_amount,
                   MIN(amount) as min_amount,
                   MAX(amount) as max_amount
               FROM product_sales
               WHERE product_id = ? AND status = 'completed'""",
            (product_id,),
        ).fetchone()
    finally:
        conn.close()

    avg_amount = hist["avg_amount"] or 0.0
    ltv_value = ltv.get("avg_ltv", 0.0)

    # Recommendation heuristics
    suggestions = []
    if avg_amount > 0:
        # 10% above historical average
        suggestions.append({"label": "10% above avg", "price": round(avg_amount * 1.1, 2)})
        # Margin-based: if we know cost
        margin_price = avg_amount / (1 - margin_target_pct / 100)
        suggestions.append({"label": "margin-target", "price": round(margin_price, 2)})

    if ltv_value > 0:
        # Anchor at 10–20% of LTV
        suggestions.append({"label": "10% of LTV", "price": round(ltv_value * 0.10, 2)})
        suggestions.append({"label": "20% of LTV", "price": round(ltv_value * 0.20, 2)})

    # Psychological price points
    if avg_amount > 0:
        base = avg_amount * 1.05
        psych = _nearest_psych_price(base)
        suggestions.append({"label": "psychological", "price": psych})

    return {
        "product_id": product_id,
        "segment": segment,
        "historical": {
            "total_sales": hist["sales"] or 0,
            "avg_price": round(avg_amount, 2),
            "min_price": hist["min_amount"] or 0.0,
            "max_price": hist["max_amount"] or 0.0,
        },
        "ltv": ltv,
        "suggestions": suggestions,
        "recommended": min(suggestions, key=lambda s: abs(s["price"] - avg_amount * 1.1))["price"]
        if suggestions else None,
    }


def _nearest_psych_price(price: float) -> float:
    """Round to nearest psychological price point (.99, .97, .95 endings)."""
    base = int(price)
    candidates = [base - 0.01, base + 0.99, base + 4.99, base + 9.99, base + 19.99,
                  base + 29.99, base + 49.99, base + 99.99]
    return min((c for c in candidates if c > 0), key=lambda c: abs(c - price))


# ---------------------------------------------------------------------------
# Pricing summary
# ---------------------------------------------------------------------------

def pricing_summary() -> Dict[str, Any]:
    """High-level pricing engine status."""
    _ensure()
    conn = _db()
    try:
        rules_row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active
               FROM pricing_rules"""
        ).fetchone()
        tests_row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) as running,
                   SUM(CASE WHEN status = 'concluded' THEN 1 ELSE 0 END) as concluded
               FROM price_tests"""
        ).fetchone()
        discounts_row = conn.execute(
            "SELECT COUNT(*) as total FROM discount_rules WHERE is_active = 1"
        ).fetchone()
        ltv_row = conn.execute("SELECT COUNT(*) as total FROM ltv_estimates").fetchone()
        return {
            "pricing_rules": {
                "total": rules_row["total"] or 0,
                "active": rules_row["active"] or 0,
            },
            "price_tests": {
                "total": tests_row["total"] or 0,
                "running": tests_row["running"] or 0,
                "concluded": tests_row["concluded"] or 0,
            },
            "active_discounts": discounts_row["total"] or 0,
            "ltv_segments": ltv_row["total"] or 0,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("pe_create_rule", "Create a pricing rule for a product")
def create_rule_tool(
    name: str,
    base_price: float,
    pricing_model: str = "flat",
    billing_interval: str = "once",
    product_id: int = 0,
) -> str:
    try:
        rule = create_pricing_rule(
            name=name,
            base_price=base_price,
            pricing_model=pricing_model,
            billing_interval=billing_interval,
            product_id=product_id or None,
        )
        return json.dumps({"ok": True, "rule": rule.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("pe_resolve_price", "Get the correct price for a product given quantity and context")
def resolve_price_tool(
    product_id: int,
    quantity: float = 1.0,
    coupon_code: str = "",
) -> str:
    try:
        return json.dumps(resolve_price(
            product_id=product_id,
            quantity=quantity,
            coupon_code=coupon_code,
        ), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pe_create_price_test", "Start an A/B price test")
def create_price_test_tool(
    name: str,
    variant_a_price: float,
    variant_b_price: float,
    product_id: int = 0,
) -> str:
    try:
        test = create_price_test(
            name=name,
            variant_a_price=variant_a_price,
            variant_b_price=variant_b_price,
            product_id=product_id or None,
        )
        return json.dumps({"ok": True, "test": test.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("pe_analyze_price_test", "Get statistical analysis of a running price test")
def analyze_test_tool(test_name: str) -> str:
    try:
        return json.dumps(analyze_price_test(test_name), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pe_recommend_price", "Get AI-informed price recommendations for a product")
def recommend_price_tool(product_id: int, segment: str = "default") -> str:
    try:
        return json.dumps(recommend_price(product_id, segment), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pe_create_discount", "Create a discount rule (percent or fixed)")
def create_discount_tool(
    name: str,
    discount_type: str = "percent",
    discount_value: float = 10.0,
    end_at: str = "",
    usage_limit: int = 0,
) -> str:
    try:
        disc = create_discount_rule(
            name=name,
            discount_type=discount_type,
            discount_value=discount_value,
            end_at=end_at or None,
            usage_limit=usage_limit or None,
        )
        return json.dumps({"ok": True, "discount": disc})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("pe_pricing_summary", "High-level overview of all pricing rules, tests, and discounts")
def pricing_summary_tool() -> str:
    try:
        return json.dumps(pricing_summary(), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pe_upsert_ltv", "Update LTV estimate for a customer segment")
def upsert_ltv_tool(
    segment: str,
    avg_ltv: float,
    avg_order_value: float = 0.0,
    churn_rate: float = 0.0,
    acquisition_cost: float = 0.0,
) -> str:
    try:
        result = upsert_ltv_estimate(
            segment=segment,
            avg_ltv=avg_ltv,
            avg_order_value=avg_order_value,
            churn_rate=churn_rate,
            acquisition_cost=acquisition_cost,
        )
        return json.dumps({"ok": True, **result})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
