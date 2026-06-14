"""
Product Manager — digital product catalog, sales tracking, and revenue reporting.

Handles ebooks, courses, templates, SaaS subscriptions, coaching offers, and
physical products. All data stored in SQLite via the shared database module.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PRODUCT_TYPES = [
    "ebook",
    "course",
    "template",
    "software",
    "saas",
    "coaching",
    "membership",
    "bundle",
    "physical",
    "service",
    "plugin",
    "script",
    "report",
    "toolkit",
    "masterclass",
]

DELIVERY_METHODS = [
    "download",
    "email",
    "membership_area",
    "zoom",
    "streaming",
    "physical_shipping",
    "api_key",
    "manual",
]

SALE_STATUSES = [
    "pending",
    "completed",
    "refunded",
    "chargeback",
    "failed",
    "cancelled",
]

PLATFORMS = [
    "gumroad",
    "lemon_squeezy",
    "paddle",
    "stripe",
    "paypal",
    "woocommerce",
    "shopify",
    "teachable",
    "thinkific",
    "podia",
    "own_site",
    "amazon_kdp",
    "etsy",
    "appsumo",
    "other",
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
            CREATE TABLE IF NOT EXISTS products (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                sku           TEXT NOT NULL UNIQUE,
                name          TEXT NOT NULL,
                product_type  TEXT NOT NULL DEFAULT 'ebook',
                description   TEXT,
                short_desc    TEXT,
                price         REAL NOT NULL DEFAULT 0.0,
                sale_price    REAL,
                currency      TEXT NOT NULL DEFAULT 'USD',
                platform      TEXT,
                platform_url  TEXT,
                delivery      TEXT DEFAULT 'download',
                category      TEXT,
                tags          TEXT DEFAULT '[]',
                status        TEXT NOT NULL DEFAULT 'draft',
                is_featured   INTEGER NOT NULL DEFAULT 0,
                cover_url     TEXT,
                file_path     TEXT,
                page_count    INTEGER,
                duration_mins INTEGER,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                metadata      TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS product_variants (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,
                sku         TEXT NOT NULL UNIQUE,
                price       REAL NOT NULL,
                sale_price  REAL,
                description TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS product_sales (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_ref        TEXT NOT NULL UNIQUE,
                product_id      INTEGER NOT NULL REFERENCES products(id),
                variant_id      INTEGER REFERENCES product_variants(id),
                customer_email  TEXT,
                customer_name   TEXT,
                amount          REAL NOT NULL,
                currency        TEXT NOT NULL DEFAULT 'USD',
                platform        TEXT,
                platform_txn_id TEXT,
                status          TEXT NOT NULL DEFAULT 'completed',
                refund_reason   TEXT,
                coupon_code     TEXT,
                discount_amount REAL NOT NULL DEFAULT 0.0,
                affiliate_id    TEXT,
                commission_pct  REAL NOT NULL DEFAULT 0.0,
                source_url      TEXT,
                utm_source      TEXT,
                utm_medium      TEXT,
                utm_campaign    TEXT,
                country         TEXT,
                sold_at         TEXT NOT NULL,
                metadata        TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS product_reviews (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                reviewer    TEXT,
                email       TEXT,
                rating      INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                title       TEXT,
                body        TEXT,
                is_verified INTEGER NOT NULL DEFAULT 0,
                is_public   INTEGER NOT NULL DEFAULT 1,
                source      TEXT DEFAULT 'manual',
                reviewed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_coupons (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT NOT NULL UNIQUE,
                product_id      INTEGER REFERENCES products(id) ON DELETE CASCADE,
                discount_type   TEXT NOT NULL DEFAULT 'percent',
                discount_value  REAL NOT NULL,
                max_uses        INTEGER,
                used_count      INTEGER NOT NULL DEFAULT 0,
                expires_at      TEXT,
                is_active       INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_product_sales_product ON product_sales(product_id);
            CREATE INDEX IF NOT EXISTS idx_product_sales_sold_at ON product_sales(sold_at);
            CREATE INDEX IF NOT EXISTS idx_product_sales_status  ON product_sales(status);
            CREATE INDEX IF NOT EXISTS idx_product_reviews_product ON product_reviews(product_id);
            CREATE INDEX IF NOT EXISTS idx_products_status ON products(status);
            CREATE INDEX IF NOT EXISTS idx_products_type ON products(product_type);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProductVariant:
    name: str
    sku: str
    price: float
    sale_price: Optional[float] = None
    description: str = ""
    is_active: bool = True
    sort_order: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    product_id: Optional[int] = None

    @property
    def effective_price(self) -> float:
        return self.sale_price if self.sale_price is not None else self.price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "product_id": self.product_id,
            "name": self.name,
            "sku": self.sku,
            "price": self.price,
            "sale_price": self.sale_price,
            "effective_price": self.effective_price,
            "description": self.description,
            "is_active": self.is_active,
            "sort_order": self.sort_order,
            "metadata": self.metadata,
        }


@dataclass
class Product:
    name: str
    product_type: str = "ebook"
    price: float = 0.0
    description: str = ""
    short_desc: str = ""
    sale_price: Optional[float] = None
    currency: str = "USD"
    platform: str = ""
    platform_url: str = ""
    delivery: str = "download"
    category: str = ""
    tags: List[str] = field(default_factory=list)
    status: str = "draft"
    is_featured: bool = False
    cover_url: str = ""
    file_path: str = ""
    page_count: Optional[int] = None
    duration_mins: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    sku: str = ""
    created_at: str = ""
    updated_at: str = ""
    variants: List[ProductVariant] = field(default_factory=list)

    @property
    def effective_price(self) -> float:
        return self.sale_price if self.sale_price is not None else self.price

    @property
    def is_on_sale(self) -> bool:
        return self.sale_price is not None and self.sale_price < self.price

    @property
    def discount_percent(self) -> float:
        if not self.is_on_sale:
            return 0.0
        return round((self.price - self.sale_price) / self.price * 100, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sku": self.sku,
            "name": self.name,
            "product_type": self.product_type,
            "description": self.description,
            "short_desc": self.short_desc,
            "price": self.price,
            "sale_price": self.sale_price,
            "effective_price": self.effective_price,
            "is_on_sale": self.is_on_sale,
            "discount_percent": self.discount_percent,
            "currency": self.currency,
            "platform": self.platform,
            "platform_url": self.platform_url,
            "delivery": self.delivery,
            "category": self.category,
            "tags": self.tags,
            "status": self.status,
            "is_featured": self.is_featured,
            "cover_url": self.cover_url,
            "file_path": self.file_path,
            "page_count": self.page_count,
            "duration_mins": self.duration_mins,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "variants": [v.to_dict() for v in self.variants],
        }


@dataclass
class Sale:
    product_id: int
    amount: float
    customer_email: str = ""
    customer_name: str = ""
    currency: str = "USD"
    platform: str = ""
    platform_txn_id: str = ""
    status: str = "completed"
    coupon_code: str = ""
    discount_amount: float = 0.0
    affiliate_id: str = ""
    commission_pct: float = 0.0
    source_url: str = ""
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    country: str = ""
    variant_id: Optional[int] = None
    refund_reason: str = ""
    sold_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    sale_ref: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sale_ref": self.sale_ref,
            "product_id": self.product_id,
            "variant_id": self.variant_id,
            "customer_email": self.customer_email,
            "customer_name": self.customer_name,
            "amount": self.amount,
            "currency": self.currency,
            "platform": self.platform,
            "platform_txn_id": self.platform_txn_id,
            "status": self.status,
            "coupon_code": self.coupon_code,
            "discount_amount": self.discount_amount,
            "affiliate_id": self.affiliate_id,
            "commission_pct": self.commission_pct,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "country": self.country,
            "sold_at": self.sold_at,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _product_from_row(row: sqlite3.Row, variants: Optional[List[ProductVariant]] = None) -> Product:
    p = Product(
        id=row["id"],
        sku=row["sku"],
        name=row["name"],
        product_type=row["product_type"],
        description=row["description"] or "",
        short_desc=row["short_desc"] or "",
        price=row["price"],
        sale_price=row["sale_price"],
        currency=row["currency"],
        platform=row["platform"] or "",
        platform_url=row["platform_url"] or "",
        delivery=row["delivery"] or "download",
        category=row["category"] or "",
        tags=json.loads(row["tags"] or "[]"),
        status=row["status"],
        is_featured=bool(row["is_featured"]),
        cover_url=row["cover_url"] or "",
        file_path=row["file_path"] or "",
        page_count=row["page_count"],
        duration_mins=row["duration_mins"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=json.loads(row["metadata"] or "{}"),
        variants=variants or [],
    )
    return p


def _variant_from_row(row: sqlite3.Row) -> ProductVariant:
    return ProductVariant(
        id=row["id"],
        product_id=row["product_id"],
        name=row["name"],
        sku=row["sku"],
        price=row["price"],
        sale_price=row["sale_price"],
        description=row["description"] or "",
        is_active=bool(row["is_active"]),
        sort_order=row["sort_order"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _sale_from_row(row: sqlite3.Row) -> Sale:
    return Sale(
        id=row["id"],
        sale_ref=row["sale_ref"],
        product_id=row["product_id"],
        variant_id=row["variant_id"],
        customer_email=row["customer_email"] or "",
        customer_name=row["customer_name"] or "",
        amount=row["amount"],
        currency=row["currency"],
        platform=row["platform"] or "",
        platform_txn_id=row["platform_txn_id"] or "",
        status=row["status"],
        coupon_code=row["coupon_code"] or "",
        discount_amount=row["discount_amount"],
        affiliate_id=row["affiliate_id"] or "",
        commission_pct=row["commission_pct"],
        source_url=row["source_url"] or "",
        utm_source=row["utm_source"] or "",
        utm_medium=row["utm_medium"] or "",
        utm_campaign=row["utm_campaign"] or "",
        country=row["country"] or "",
        sold_at=row["sold_at"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _gen_sku(name: str) -> str:
    slug = name.upper().replace(" ", "-")[:20]
    uid = uuid.uuid4().hex[:6].upper()
    return f"AE-{slug}-{uid}"


# ---------------------------------------------------------------------------
# Product CRUD
# ---------------------------------------------------------------------------

def create_product(
    name: str,
    product_type: str = "ebook",
    price: float = 0.0,
    description: str = "",
    short_desc: str = "",
    sale_price: Optional[float] = None,
    currency: str = "USD",
    platform: str = "",
    platform_url: str = "",
    delivery: str = "download",
    category: str = "",
    tags: Optional[List[str]] = None,
    cover_url: str = "",
    file_path: str = "",
    page_count: Optional[int] = None,
    duration_mins: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Product:
    """Create a new digital product in the catalog."""
    _ensure()
    now = datetime.utcnow().isoformat()
    sku = _gen_sku(name)
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO products
               (sku, name, product_type, description, short_desc, price, sale_price,
                currency, platform, platform_url, delivery, category, tags, status,
                cover_url, file_path, page_count, duration_mins, created_at, updated_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sku, name, product_type, description, short_desc, price, sale_price,
                currency, platform, platform_url, delivery, category,
                json.dumps(tags or []), "draft",
                cover_url, file_path, page_count, duration_mins, now, now,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        prod = Product(
            id=cur.lastrowid,
            sku=sku,
            name=name,
            product_type=product_type,
            description=description,
            short_desc=short_desc,
            price=price,
            sale_price=sale_price,
            currency=currency,
            platform=platform,
            platform_url=platform_url,
            delivery=delivery,
            category=category,
            tags=tags or [],
            status="draft",
            cover_url=cover_url,
            file_path=file_path,
            page_count=page_count,
            duration_mins=duration_mins,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )
        return prod
    finally:
        conn.close()


def get_product(product_id: int) -> Optional[Product]:
    """Fetch product by ID with all variants."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not row:
            return None
        variant_rows = conn.execute(
            "SELECT * FROM product_variants WHERE product_id = ? ORDER BY sort_order",
            (product_id,),
        ).fetchall()
        variants = [_variant_from_row(r) for r in variant_rows]
        return _product_from_row(row, variants)
    finally:
        conn.close()


def get_product_by_sku(sku: str) -> Optional[Product]:
    """Fetch product by SKU."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        if not row:
            return None
        return get_product(row["id"])
    finally:
        conn.close()


def list_products(
    status: Optional[str] = None,
    product_type: Optional[str] = None,
    category: Optional[str] = None,
    platform: Optional[str] = None,
    featured_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> List[Product]:
    """List products with optional filters."""
    _ensure()
    clauses = []
    params: List[Any] = []

    if status:
        clauses.append("status = ?")
        params.append(status)
    if product_type:
        clauses.append("product_type = ?")
        params.append(product_type)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    if featured_only:
        clauses.append("is_featured = 1")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM products {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        products = []
        for r in rows:
            variant_rows = conn.execute(
                "SELECT * FROM product_variants WHERE product_id = ? ORDER BY sort_order",
                (r["id"],),
            ).fetchall()
            variants = [_variant_from_row(vr) for vr in variant_rows]
            products.append(_product_from_row(r, variants))
        return products
    finally:
        conn.close()


def update_product(product_id: int, **kwargs) -> bool:
    """Update product fields. Pass keyword arguments matching column names."""
    _ensure()
    allowed = {
        "name", "product_type", "description", "short_desc", "price", "sale_price",
        "currency", "platform", "platform_url", "delivery", "category", "tags",
        "status", "is_featured", "cover_url", "file_path", "page_count",
        "duration_mins", "metadata",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False

    if "tags" in updates:
        updates["tags"] = json.dumps(updates["tags"])
    if "metadata" in updates:
        updates["metadata"] = json.dumps(updates["metadata"])
    if "is_featured" in updates:
        updates["is_featured"] = int(updates["is_featured"])

    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [product_id]
    conn = _db()
    try:
        conn.execute(f"UPDATE products SET {set_clause} WHERE id = ?", params)
        conn.commit()
        return True
    finally:
        conn.close()


def publish_product(product_id: int) -> bool:
    """Set product status to 'active'."""
    return update_product(product_id, status="active")


def archive_product(product_id: int) -> bool:
    """Set product status to 'archived'."""
    return update_product(product_id, status="archived")


def delete_product(product_id: int) -> bool:
    """Hard delete a product (cascades to variants, reviews)."""
    _ensure()
    conn = _db()
    try:
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Variant management
# ---------------------------------------------------------------------------

def add_variant(
    product_id: int,
    name: str,
    price: float,
    sale_price: Optional[float] = None,
    description: str = "",
    sort_order: int = 0,
    metadata: Optional[Dict[str, Any]] = None,
) -> ProductVariant:
    """Add a pricing variant to a product (e.g. Basic / Pro / Enterprise)."""
    _ensure()
    prod = get_product(product_id)
    if not prod:
        raise ValueError(f"Product {product_id} not found")

    sku = _gen_sku(f"{prod.name}-{name}")
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO product_variants
               (product_id, name, sku, price, sale_price, description, sort_order, metadata)
               VALUES (?,?,?,?,?,?,?,?)""",
            (product_id, name, sku, price, sale_price, description, sort_order,
             json.dumps(metadata or {})),
        )
        conn.commit()
        return ProductVariant(
            id=cur.lastrowid,
            product_id=product_id,
            name=name,
            sku=sku,
            price=price,
            sale_price=sale_price,
            description=description,
            sort_order=sort_order,
            metadata=metadata or {},
        )
    finally:
        conn.close()


def list_variants(product_id: int) -> List[ProductVariant]:
    """List all variants for a product."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM product_variants WHERE product_id = ? ORDER BY sort_order",
            (product_id,),
        ).fetchall()
        return [_variant_from_row(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------

def record_sale(
    product_id: int,
    amount: float,
    customer_email: str = "",
    customer_name: str = "",
    currency: str = "USD",
    platform: str = "",
    platform_txn_id: str = "",
    status: str = "completed",
    coupon_code: str = "",
    discount_amount: float = 0.0,
    affiliate_id: str = "",
    commission_pct: float = 0.0,
    source_url: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    country: str = "",
    variant_id: Optional[int] = None,
    sold_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Sale:
    """Record a new product sale."""
    _ensure()
    sale_ref = f"SALE-{uuid.uuid4().hex[:12].upper()}"
    ts = sold_at or datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO product_sales
               (sale_ref, product_id, variant_id, customer_email, customer_name,
                amount, currency, platform, platform_txn_id, status,
                coupon_code, discount_amount, affiliate_id, commission_pct,
                source_url, utm_source, utm_medium, utm_campaign, country,
                sold_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sale_ref, product_id, variant_id, customer_email, customer_name,
                amount, currency, platform, platform_txn_id, status,
                coupon_code, discount_amount, affiliate_id, commission_pct,
                source_url, utm_source, utm_medium, utm_campaign, country,
                ts, json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        s = Sale(
            id=cur.lastrowid,
            sale_ref=sale_ref,
            product_id=product_id,
            variant_id=variant_id,
            customer_email=customer_email,
            customer_name=customer_name,
            amount=amount,
            currency=currency,
            platform=platform,
            platform_txn_id=platform_txn_id,
            status=status,
            coupon_code=coupon_code,
            discount_amount=discount_amount,
            affiliate_id=affiliate_id,
            commission_pct=commission_pct,
            source_url=source_url,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            country=country,
            sold_at=ts,
            metadata=metadata or {},
        )
        return s
    finally:
        conn.close()


def refund_sale(sale_id: int, reason: str = "") -> bool:
    """Mark a sale as refunded."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE product_sales SET status = 'refunded', refund_reason = ? WHERE id = ?",
            (reason, sale_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_sales(
    product_id: Optional[int] = None,
    status: Optional[str] = None,
    platform: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
) -> List[Sale]:
    """List sales with optional filters."""
    _ensure()
    clauses = []
    params: List[Any] = []
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if platform:
        clauses.append("platform = ?")
        params.append(platform)
    if since:
        clauses.append("sold_at >= ?")
        params.append(since)
    if until:
        clauses.append("sold_at <= ?")
        params.append(until)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM product_sales {where} ORDER BY sold_at DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [_sale_from_row(r) for r in rows]
    finally:
        conn.close()


def get_sale_by_ref(sale_ref: str) -> Optional[Sale]:
    """Fetch a sale by its reference code."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM product_sales WHERE sale_ref = ?", (sale_ref,)
        ).fetchone()
        return _sale_from_row(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def add_review(
    product_id: int,
    rating: int,
    reviewer: str = "",
    email: str = "",
    title: str = "",
    body: str = "",
    is_verified: bool = False,
    is_public: bool = True,
    source: str = "manual",
    reviewed_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Add a customer review for a product."""
    _ensure()
    if not 1 <= rating <= 5:
        raise ValueError("Rating must be 1-5")
    ts = reviewed_at or datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO product_reviews
               (product_id, reviewer, email, rating, title, body,
                is_verified, is_public, source, reviewed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (product_id, reviewer, email, rating, title, body,
             int(is_verified), int(is_public), source, ts),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "product_id": product_id,
            "reviewer": reviewer,
            "rating": rating,
            "title": title,
            "body": body,
            "is_verified": is_verified,
            "reviewed_at": ts,
        }
    finally:
        conn.close()


def get_reviews(
    product_id: int,
    public_only: bool = True,
    min_rating: int = 1,
) -> List[Dict[str, Any]]:
    """Get all reviews for a product."""
    _ensure()
    clauses = ["product_id = ?", "rating >= ?"]
    params: List[Any] = [product_id, min_rating]
    if public_only:
        clauses.append("is_public = 1")
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM product_reviews WHERE {' AND '.join(clauses)} ORDER BY reviewed_at DESC",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def review_summary(product_id: int) -> Dict[str, Any]:
    """Aggregate review stats for a product."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   AVG(rating) as avg_rating,
                   SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) as five_star,
                   SUM(CASE WHEN rating = 4 THEN 1 ELSE 0 END) as four_star,
                   SUM(CASE WHEN rating = 3 THEN 1 ELSE 0 END) as three_star,
                   SUM(CASE WHEN rating = 2 THEN 1 ELSE 0 END) as two_star,
                   SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as one_star,
                   SUM(CASE WHEN is_verified = 1 THEN 1 ELSE 0 END) as verified_count
               FROM product_reviews WHERE product_id = ? AND is_public = 1""",
            (product_id,),
        ).fetchone()
        total = row["total"] or 0
        avg = round(row["avg_rating"] or 0, 2)
        distribution = {
            "5": row["five_star"] or 0,
            "4": row["four_star"] or 0,
            "3": row["three_star"] or 0,
            "2": row["two_star"] or 0,
            "1": row["one_star"] or 0,
        }
        return {
            "product_id": product_id,
            "total_reviews": total,
            "average_rating": avg,
            "verified_count": row["verified_count"] or 0,
            "distribution": distribution,
            "rating_label": _rating_label(avg),
        }
    finally:
        conn.close()


def _rating_label(avg: float) -> str:
    if avg >= 4.8:
        return "Exceptional"
    if avg >= 4.5:
        return "Excellent"
    if avg >= 4.0:
        return "Very Good"
    if avg >= 3.5:
        return "Good"
    if avg >= 3.0:
        return "Average"
    return "Below Average"


# ---------------------------------------------------------------------------
# Coupons
# ---------------------------------------------------------------------------

def create_coupon(
    code: str,
    discount_type: str = "percent",
    discount_value: float = 10.0,
    product_id: Optional[int] = None,
    max_uses: Optional[int] = None,
    expires_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a discount coupon."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO product_coupons
               (code, product_id, discount_type, discount_value,
                max_uses, expires_at, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (code.upper(), product_id, discount_type, discount_value,
             max_uses, expires_at, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "code": code.upper(),
            "discount_type": discount_type,
            "discount_value": discount_value,
            "product_id": product_id,
            "max_uses": max_uses,
            "expires_at": expires_at,
        }
    except sqlite3.IntegrityError:
        conn.close()
        raise ValueError(f"Coupon code '{code}' already exists")
    finally:
        conn.close()


def validate_coupon(code: str) -> Dict[str, Any]:
    """Check if a coupon is valid and return its details."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM product_coupons WHERE code = ? AND is_active = 1",
            (code.upper(),),
        ).fetchone()
        if not row:
            return {"valid": False, "reason": "Coupon not found"}
        if row["max_uses"] is not None and row["used_count"] >= row["max_uses"]:
            return {"valid": False, "reason": "Coupon has reached maximum uses"}
        if row["expires_at"] and row["expires_at"] < datetime.utcnow().isoformat():
            return {"valid": False, "reason": "Coupon has expired"}
        return {
            "valid": True,
            "code": row["code"],
            "discount_type": row["discount_type"],
            "discount_value": row["discount_value"],
            "product_id": row["product_id"],
            "uses_remaining": (row["max_uses"] - row["used_count"]) if row["max_uses"] else None,
        }
    finally:
        conn.close()


def use_coupon(code: str) -> bool:
    """Increment coupon usage counter."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE product_coupons SET used_count = used_count + 1 WHERE code = ?",
            (code.upper(),),
        )
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def product_revenue_report(
    product_id: int,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> Dict[str, Any]:
    """Full revenue breakdown for a single product."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    until = until or datetime.utcnow().isoformat()

    conn = _db()
    try:
        prod = get_product(product_id)
        if not prod:
            return {"error": f"Product {product_id} not found"}

        row = conn.execute(
            """SELECT
                   COUNT(*) as total_sales,
                   SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) as gross_revenue,
                   SUM(CASE WHEN status = 'refunded' THEN amount ELSE 0 END) as refunded_amount,
                   SUM(CASE WHEN status = 'completed' THEN discount_amount ELSE 0 END) as total_discounts,
                   AVG(CASE WHEN status = 'completed' THEN amount END) as avg_order_value,
                   COUNT(CASE WHEN status = 'refunded' THEN 1 END) as refund_count
               FROM product_sales
               WHERE product_id = ? AND sold_at BETWEEN ? AND ?""",
            (product_id, since, until),
        ).fetchone()

        gross = row["gross_revenue"] or 0.0
        refunded = row["refunded_amount"] or 0.0
        net = gross - refunded

        # Daily breakdown
        daily_rows = conn.execute(
            """SELECT DATE(sold_at) as day,
                      SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) as revenue,
                      COUNT(CASE WHEN status = 'completed' THEN 1 END) as units
               FROM product_sales
               WHERE product_id = ? AND sold_at BETWEEN ? AND ?
               GROUP BY DATE(sold_at)
               ORDER BY day""",
            (product_id, since, until),
        ).fetchall()

        # Platform breakdown
        plat_rows = conn.execute(
            """SELECT platform,
                      SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) as revenue,
                      COUNT(CASE WHEN status = 'completed' THEN 1 END) as sales
               FROM product_sales
               WHERE product_id = ? AND sold_at BETWEEN ? AND ?
               GROUP BY platform""",
            (product_id, since, until),
        ).fetchall()

        # Review summary
        rev_summary = review_summary(product_id)

        return {
            "product_id": product_id,
            "product_name": prod.name,
            "sku": prod.sku,
            "price": prod.price,
            "status": prod.status,
            "period": {"since": since, "until": until},
            "total_sales": row["total_sales"] or 0,
            "completed_sales": (row["total_sales"] or 0) - (row["refund_count"] or 0),
            "refund_count": row["refund_count"] or 0,
            "refund_rate": round(
                (row["refund_count"] or 0) / max(row["total_sales"] or 1, 1) * 100, 2
            ),
            "gross_revenue": round(gross, 2),
            "refunded_amount": round(refunded, 2),
            "net_revenue": round(net, 2),
            "total_discounts": round(row["total_discounts"] or 0, 2),
            "avg_order_value": round(row["avg_order_value"] or 0, 2),
            "daily_breakdown": [dict(r) for r in daily_rows],
            "platform_breakdown": [dict(r) for r in plat_rows],
            "reviews": rev_summary,
        }
    finally:
        conn.close()


def best_sellers(
    limit: int = 10,
    since: Optional[str] = None,
    metric: str = "revenue",
) -> List[Dict[str, Any]]:
    """Rank products by revenue or units sold."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    order_by = "revenue DESC" if metric == "revenue" else "units DESC"

    conn = _db()
    try:
        rows = conn.execute(
            f"""SELECT
                    p.id, p.name, p.sku, p.product_type, p.price, p.status,
                    SUM(CASE WHEN s.status = 'completed' THEN s.amount ELSE 0 END) as revenue,
                    COUNT(CASE WHEN s.status = 'completed' THEN 1 END) as units,
                    COUNT(CASE WHEN s.status = 'refunded' THEN 1 END) as refunds
                FROM products p
                LEFT JOIN product_sales s ON s.product_id = p.id AND s.sold_at >= ?
                GROUP BY p.id
                ORDER BY {order_by}
                LIMIT ?""",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revenue_by_type(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Revenue grouped by product type."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT p.product_type,
                      COUNT(DISTINCT p.id) as product_count,
                      SUM(CASE WHEN s.status = 'completed' THEN s.amount ELSE 0 END) as revenue,
                      COUNT(CASE WHEN s.status = 'completed' THEN 1 END) as sales
               FROM products p
               LEFT JOIN product_sales s ON s.product_id = p.id AND s.sold_at >= ?
               GROUP BY p.product_type
               ORDER BY revenue DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def revenue_by_platform(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """Revenue grouped by sales platform."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT platform,
                      SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) as revenue,
                      COUNT(CASE WHEN status = 'completed' THEN 1 END) as sales,
                      COUNT(CASE WHEN status = 'refunded' THEN 1 END) as refunds
               FROM product_sales
               WHERE sold_at >= ?
               GROUP BY platform
               ORDER BY revenue DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def geo_revenue_breakdown(
    product_id: Optional[int] = None,
    since: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Revenue by country."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    clauses = ["sold_at >= ?", "status = 'completed'"]
    params: List[Any] = [since]
    if product_id:
        clauses.append("product_id = ?")
        params.append(product_id)

    conn = _db()
    try:
        rows = conn.execute(
            f"""SELECT country,
                        SUM(amount) as revenue,
                        COUNT(*) as sales
                FROM product_sales
                WHERE {' AND '.join(clauses)}
                GROUP BY country
                ORDER BY revenue DESC
                LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def coupon_performance(since: Optional[str] = None) -> List[Dict[str, Any]]:
    """How much revenue / how many sales each coupon generated."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT coupon_code,
                      COUNT(*) as uses,
                      SUM(amount) as revenue,
                      SUM(discount_amount) as discounts_given
               FROM product_sales
               WHERE coupon_code != '' AND sold_at >= ? AND status = 'completed'
               GROUP BY coupon_code
               ORDER BY revenue DESC""",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def product_catalog_summary() -> Dict[str, Any]:
    """High-level summary of the entire product catalog."""
    _ensure()
    conn = _db()
    try:
        counts = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                   SUM(CASE WHEN status = 'draft'  THEN 1 ELSE 0 END) as draft,
                   SUM(CASE WHEN status = 'archived' THEN 1 ELSE 0 END) as archived,
                   SUM(CASE WHEN is_featured = 1 THEN 1 ELSE 0 END) as featured
               FROM products""",
        ).fetchone()

        revenue_row = conn.execute(
            """SELECT
                   SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END) as all_time_revenue,
                   COUNT(CASE WHEN status = 'completed' THEN 1 END) as all_time_sales,
                   SUM(CASE WHEN status = 'completed' AND sold_at >= ? THEN amount ELSE 0 END) as month_revenue,
                   COUNT(CASE WHEN status = 'completed' AND sold_at >= ? THEN 1 END) as month_sales
               FROM product_sales""",
            (
                (datetime.utcnow() - timedelta(days=30)).isoformat(),
                (datetime.utcnow() - timedelta(days=30)).isoformat(),
            ),
        ).fetchone()

        type_rows = conn.execute(
            "SELECT product_type, COUNT(*) as cnt FROM products GROUP BY product_type ORDER BY cnt DESC"
        ).fetchall()

        return {
            "catalog": {
                "total": counts["total"] or 0,
                "active": counts["active"] or 0,
                "draft": counts["draft"] or 0,
                "archived": counts["archived"] or 0,
                "featured": counts["featured"] or 0,
            },
            "revenue": {
                "all_time": round(revenue_row["all_time_revenue"] or 0, 2),
                "all_time_sales": revenue_row["all_time_sales"] or 0,
                "last_30_days": round(revenue_row["month_revenue"] or 0, 2),
                "last_30_day_sales": revenue_row["month_sales"] or 0,
            },
            "by_type": [dict(r) for r in type_rows],
        }
    finally:
        conn.close()


def utm_attribution(since: Optional[str] = None) -> Dict[str, Any]:
    """Revenue attribution by UTM source / medium / campaign."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    conn = _db()
    try:
        source_rows = conn.execute(
            """SELECT utm_source, SUM(amount) as revenue, COUNT(*) as sales
               FROM product_sales
               WHERE status = 'completed' AND sold_at >= ?
               GROUP BY utm_source ORDER BY revenue DESC LIMIT 20""",
            (since,),
        ).fetchall()
        medium_rows = conn.execute(
            """SELECT utm_medium, SUM(amount) as revenue, COUNT(*) as sales
               FROM product_sales
               WHERE status = 'completed' AND sold_at >= ?
               GROUP BY utm_medium ORDER BY revenue DESC LIMIT 20""",
            (since,),
        ).fetchall()
        campaign_rows = conn.execute(
            """SELECT utm_campaign, SUM(amount) as revenue, COUNT(*) as sales
               FROM product_sales
               WHERE status = 'completed' AND sold_at >= ?
               GROUP BY utm_campaign ORDER BY revenue DESC LIMIT 20""",
            (since,),
        ).fetchall()
        return {
            "by_source": [dict(r) for r in source_rows],
            "by_medium": [dict(r) for r in medium_rows],
            "by_campaign": [dict(r) for r in campaign_rows],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("pm_create_product", "Create a new product in the catalog")
def create_product_tool(
    name: str,
    product_type: str = "ebook",
    price: float = 0.0,
    description: str = "",
    platform: str = "",
) -> str:
    try:
        p = create_product(
            name=name,
            product_type=product_type,
            price=price,
            description=description,
            platform=platform,
        )
        return json.dumps({"ok": True, "product": p.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("pm_record_sale", "Record a product sale")
def record_sale_tool(
    product_id: int,
    amount: float,
    customer_email: str = "",
    platform: str = "",
    utm_source: str = "",
    utm_campaign: str = "",
) -> str:
    try:
        s = record_sale(
            product_id=product_id,
            amount=amount,
            customer_email=customer_email,
            platform=platform,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
        )
        return json.dumps({"ok": True, "sale": s.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("pm_product_report", "Revenue report for a specific product")
def product_report_tool(product_id: int, since: str = "", until: str = "") -> str:
    try:
        report = product_revenue_report(
            product_id,
            since=since or None,
            until=until or None,
        )
        return json.dumps(report, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pm_best_sellers", "List best-selling products by revenue or units")
def best_sellers_tool(limit: int = 10, metric: str = "revenue", since: str = "") -> str:
    try:
        data = best_sellers(limit=limit, metric=metric, since=since or None)
        return json.dumps(data, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pm_catalog_summary", "High-level product catalog summary with revenue totals")
def catalog_summary_tool() -> str:
    try:
        return json.dumps(product_catalog_summary(), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pm_list_products", "List all products with optional type/status filter")
def list_products_tool(
    status: str = "",
    product_type: str = "",
    limit: int = 50,
) -> str:
    try:
        products = list_products(
            status=status or None,
            product_type=product_type or None,
            limit=limit,
        )
        return json.dumps([p.to_dict() for p in products], default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("pm_add_review", "Add a customer review to a product")
def add_review_tool(
    product_id: int,
    rating: int,
    reviewer: str = "",
    title: str = "",
    body: str = "",
) -> str:
    try:
        rv = add_review(
            product_id=product_id,
            rating=rating,
            reviewer=reviewer,
            title=title,
            body=body,
        )
        return json.dumps({"ok": True, "review": rv})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("pm_utm_attribution", "Revenue attribution by UTM parameters")
def utm_attribution_tool(since: str = "") -> str:
    try:
        return json.dumps(utm_attribution(since=since or None), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})
