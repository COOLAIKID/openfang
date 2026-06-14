"""
Marketplace Connector — manage multi-marketplace product listings, orders,
fees, reviews, and price-sync rules.

Supports Etsy, Gumroad, Shopify, Amazon, eBay, Redbubble, Teepublic,
Printful, Payhip, Sellfy, LemonSqueezy, Paddle, and FastSpring.

All data is persisted in the shared SQLite database. The module follows the
standard AutoEarn lazy-init pattern: ``_schema_ready`` flag, ``_db()`` opens
a fresh connection, and ``_ensure()`` initialises the schema on first use.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MARKETPLACES = [
    "etsy",
    "gumroad",
    "shopify",
    "amazon",
    "ebay",
    "redbubble",
    "teepublic",
    "printful",
    "payhip",
    "sellfy",
    "lemonsqueezy",
    "paddle",
    "fastspring",
]

LISTING_STATUSES = [
    "active",
    "inactive",
    "sold_out",
    "expired",
    "draft",
    "under_review",
    "removed",
]

ORDER_STATUSES = [
    "pending",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
    "disputed",
]

FEE_TYPES = [
    "transaction",
    "listing",
    "payment_processing",
    "shipping_label",
    "subscription",
    "advertising",
    "refund",
]

_ESTIMATED_FEE_RATE = 0.05  # 5% platform fee estimate for net_revenue

# ---------------------------------------------------------------------------
# SQLite bootstrap
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
            CREATE TABLE IF NOT EXISTS marketplace_accounts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                marketplace     TEXT    NOT NULL,
                account_name    TEXT    NOT NULL,
                api_key_hash    TEXT    NOT NULL DEFAULT '',
                shop_url        TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'active',
                last_sync_at    TEXT,
                sync_count      INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                UNIQUE(marketplace, account_name)
            );

            CREATE TABLE IF NOT EXISTS marketplace_listings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                marketplace     TEXT    NOT NULL,
                listing_id      TEXT    NOT NULL,
                product_title   TEXT    NOT NULL DEFAULT '',
                sku             TEXT    NOT NULL DEFAULT '',
                price           REAL    NOT NULL DEFAULT 0.0,
                sale_price      REAL    NOT NULL DEFAULT 0.0,
                quantity        INTEGER NOT NULL DEFAULT 0,
                status          TEXT    NOT NULL DEFAULT 'active',
                views           INTEGER NOT NULL DEFAULT 0,
                sales_count     INTEGER NOT NULL DEFAULT 0,
                revenue         REAL    NOT NULL DEFAULT 0.0,
                category        TEXT    NOT NULL DEFAULT '',
                tags            TEXT    NOT NULL DEFAULT '[]',
                image_urls      TEXT    NOT NULL DEFAULT '[]',
                description     TEXT    NOT NULL DEFAULT '',
                url             TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL,
                last_synced_at  TEXT,
                UNIQUE(account_id, listing_id)
            );

            CREATE TABLE IF NOT EXISTS marketplace_orders (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                marketplace     TEXT    NOT NULL,
                order_id        TEXT    NOT NULL UNIQUE,
                listing_id      TEXT    NOT NULL DEFAULT '',
                buyer_name      TEXT    NOT NULL DEFAULT '',
                buyer_email     TEXT    NOT NULL DEFAULT '',
                quantity        INTEGER NOT NULL DEFAULT 1,
                unit_price      REAL    NOT NULL DEFAULT 0.0,
                total_price     REAL    NOT NULL DEFAULT 0.0,
                shipping_cost   REAL    NOT NULL DEFAULT 0.0,
                status          TEXT    NOT NULL DEFAULT 'pending',
                ship_by         TEXT,
                shipped_at      TEXT,
                tracking_number TEXT    NOT NULL DEFAULT '',
                notes           TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS marketplace_fees (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                marketplace     TEXT    NOT NULL,
                fee_type        TEXT    NOT NULL,
                amount          REAL    NOT NULL DEFAULT 0.0,
                order_id        TEXT    NOT NULL DEFAULT '',
                period_start    TEXT    NOT NULL DEFAULT '',
                period_end      TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS marketplace_reviews (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                marketplace     TEXT    NOT NULL,
                listing_id      TEXT    NOT NULL DEFAULT '',
                reviewer_name   TEXT    NOT NULL DEFAULT '',
                rating          INTEGER NOT NULL DEFAULT 5,
                review_text     TEXT    NOT NULL DEFAULT '',
                responded       INTEGER NOT NULL DEFAULT 0,
                response_text   TEXT    NOT NULL DEFAULT '',
                review_date     TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS price_sync_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id      INTEGER NOT NULL,
                listing_id      TEXT    NOT NULL,
                rule_type       TEXT    NOT NULL DEFAULT 'fixed',
                value           REAL    NOT NULL DEFAULT 0.0,
                competitor_url  TEXT    NOT NULL DEFAULT '',
                min_price       REAL    NOT NULL DEFAULT 0.0,
                max_price       REAL    NOT NULL DEFAULT 0.0,
                enabled         INTEGER NOT NULL DEFAULT 1,
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_mc_accounts_mkt
                ON marketplace_accounts(marketplace);
            CREATE INDEX IF NOT EXISTS idx_mc_listings_acct
                ON marketplace_listings(account_id);
            CREATE INDEX IF NOT EXISTS idx_mc_listings_mkt
                ON marketplace_listings(marketplace, status);
            CREATE INDEX IF NOT EXISTS idx_mc_listings_revenue
                ON marketplace_listings(revenue DESC);
            CREATE INDEX IF NOT EXISTS idx_mc_orders_acct
                ON marketplace_orders(account_id);
            CREATE INDEX IF NOT EXISTS idx_mc_orders_mkt
                ON marketplace_orders(marketplace, status);
            CREATE INDEX IF NOT EXISTS idx_mc_orders_created
                ON marketplace_orders(created_at);
            CREATE INDEX IF NOT EXISTS idx_mc_fees_mkt
                ON marketplace_fees(marketplace, created_at);
            CREATE INDEX IF NOT EXISTS idx_mc_reviews_listing
                ON marketplace_reviews(listing_id);
            CREATE INDEX IF NOT EXISTS idx_mc_price_rules_acct
                ON price_sync_rules(account_id);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class MarketplaceListing:
    id: int
    account_id: int
    marketplace: str
    listing_id: str
    product_title: str
    sku: str
    price: float
    sale_price: float
    quantity: int
    status: str
    views: int
    sales_count: int
    revenue: float
    category: str
    tags: list
    image_urls: list
    description: str
    url: str
    created_at: str
    updated_at: str
    last_synced_at: str | None = None

    @property
    def net_revenue(self) -> float:
        """Revenue minus estimated 5% platform fees."""
        return self.revenue - (self.revenue * _ESTIMATED_FEE_RATE)

    @property
    def avg_sale_price(self) -> float:
        """Average revenue per sale; falls back to listing price."""
        if self.sales_count > 0:
            return self.revenue / self.sales_count
        return self.price

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "marketplace": self.marketplace,
            "listing_id": self.listing_id,
            "product_title": self.product_title,
            "sku": self.sku,
            "price": self.price,
            "sale_price": self.sale_price,
            "quantity": self.quantity,
            "status": self.status,
            "views": self.views,
            "sales_count": self.sales_count,
            "revenue": self.revenue,
            "net_revenue": self.net_revenue,
            "avg_sale_price": self.avg_sale_price,
            "category": self.category,
            "tags": self.tags,
            "image_urls": self.image_urls,
            "description": self.description,
            "url": self.url,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_synced_at": self.last_synced_at,
        }


@dataclass
class MarketplaceOrder:
    id: int
    account_id: int
    marketplace: str
    order_id: str
    listing_id: str
    buyer_name: str
    buyer_email: str
    quantity: int
    unit_price: float
    total_price: float
    shipping_cost: float
    status: str
    ship_by: str | None
    shipped_at: str | None
    tracking_number: str
    notes: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "marketplace": self.marketplace,
            "order_id": self.order_id,
            "listing_id": self.listing_id,
            "buyer_name": self.buyer_name,
            "buyer_email": self.buyer_email,
            "quantity": self.quantity,
            "unit_price": self.unit_price,
            "total_price": self.total_price,
            "shipping_cost": self.shipping_cost,
            "status": self.status,
            "ship_by": self.ship_by,
            "shipped_at": self.shipped_at,
            "tracking_number": self.tracking_number,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


def _row_to_listing(row: sqlite3.Row) -> MarketplaceListing:
    return MarketplaceListing(
        id=row["id"],
        account_id=row["account_id"],
        marketplace=row["marketplace"],
        listing_id=row["listing_id"],
        product_title=row["product_title"],
        sku=row["sku"],
        price=row["price"],
        sale_price=row["sale_price"],
        quantity=row["quantity"],
        status=row["status"],
        views=row["views"],
        sales_count=row["sales_count"],
        revenue=row["revenue"],
        category=row["category"],
        tags=json.loads(row["tags"] or "[]"),
        image_urls=json.loads(row["image_urls"] or "[]"),
        description=row["description"],
        url=row["url"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_synced_at=row["last_synced_at"],
    )


def _row_to_order(row: sqlite3.Row) -> MarketplaceOrder:
    return MarketplaceOrder(
        id=row["id"],
        account_id=row["account_id"],
        marketplace=row["marketplace"],
        order_id=row["order_id"],
        listing_id=row["listing_id"],
        buyer_name=row["buyer_name"],
        buyer_email=row["buyer_email"],
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        total_price=row["total_price"],
        shipping_cost=row["shipping_cost"],
        status=row["status"],
        ship_by=row["ship_by"],
        shipped_at=row["shipped_at"],
        tracking_number=row["tracking_number"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------

def connect_account(
    marketplace: str,
    account_name: str,
    api_key: str,
    shop_url: str = "",
) -> dict[str, Any]:
    """Register a marketplace account. The API key is hashed before storing."""
    _ensure()
    now = _now()
    key_hash = _hash_key(api_key)
    conn = _db()
    try:
        conn.execute(
            """
            INSERT INTO marketplace_accounts
                (marketplace, account_name, api_key_hash, shop_url, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(marketplace, account_name) DO UPDATE SET
                api_key_hash = excluded.api_key_hash,
                shop_url     = excluded.shop_url,
                status       = 'active',
                updated_at   = excluded.updated_at
            """,
            (marketplace, account_name, key_hash, shop_url, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM marketplace_accounts WHERE marketplace=? AND account_name=?",
            (marketplace, account_name),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def get_account(marketplace: str, account_name: str) -> dict[str, Any] | None:
    """Return account dict or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM marketplace_accounts WHERE marketplace=? AND account_name=?",
            (marketplace, account_name),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_accounts(
    marketplace: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    """Return all accounts, optionally filtered by marketplace and/or status."""
    _ensure()
    query = "SELECT * FROM marketplace_accounts WHERE 1=1"
    params: list[Any] = []
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)
    if status:
        query += " AND status=?"
        params.append(status)
    query += " ORDER BY marketplace, account_name"
    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def disconnect_account(account_id: int) -> bool:
    """Mark an account as disconnected. Returns True if a row was updated."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE marketplace_accounts SET status='disconnected', updated_at=? WHERE id=?",
            (now, account_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def mark_synced(account_id: int) -> bool:
    """Bump sync_count and last_sync_at for an account."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """
            UPDATE marketplace_accounts
            SET last_sync_at=?, sync_count=sync_count+1, updated_at=?
            WHERE id=?
            """,
            (now, now, account_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def sync_listing(
    account_id: int,
    listing_id: str,
    product_title: str,
    price: float,
    quantity: int,
    status: str = "active",
    category: str = "",
    sku: str = "",
    description: str = "",
    tags_list: list[str] | None = None,
    url: str = "",
) -> MarketplaceListing:
    """Upsert a listing by (account_id, listing_id). Returns the listing."""
    _ensure()
    now = _now()
    tags_json = json.dumps(tags_list or [])

    # Resolve marketplace from the account row
    conn = _db()
    try:
        acct = conn.execute(
            "SELECT marketplace FROM marketplace_accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        marketplace = acct["marketplace"] if acct else ""

        conn.execute(
            """
            INSERT INTO marketplace_listings
                (account_id, marketplace, listing_id, product_title, sku, price,
                 quantity, status, category, tags, description, url,
                 created_at, updated_at, last_synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, listing_id) DO UPDATE SET
                product_title  = excluded.product_title,
                sku            = CASE WHEN excluded.sku != '' THEN excluded.sku ELSE sku END,
                price          = excluded.price,
                quantity       = excluded.quantity,
                status         = excluded.status,
                category       = CASE WHEN excluded.category != '' THEN excluded.category ELSE category END,
                tags           = CASE WHEN excluded.tags != '[]' THEN excluded.tags ELSE tags END,
                description    = CASE WHEN excluded.description != '' THEN excluded.description ELSE description END,
                url            = CASE WHEN excluded.url != '' THEN excluded.url ELSE url END,
                updated_at     = excluded.updated_at,
                last_synced_at = excluded.last_synced_at
            """,
            (
                account_id, marketplace, listing_id, product_title, sku, price,
                quantity, status, category, tags_json, description, url,
                now, now, now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM marketplace_listings WHERE account_id=? AND listing_id=?",
            (account_id, listing_id),
        ).fetchone()
        return _row_to_listing(row)
    finally:
        conn.close()


def get_listing(account_id: int, listing_id: str) -> MarketplaceListing | None:
    """Return a single listing or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM marketplace_listings WHERE account_id=? AND listing_id=?",
            (account_id, listing_id),
        ).fetchone()
        return _row_to_listing(row) if row else None
    finally:
        conn.close()


def list_listings(
    account_id: int | None = None,
    marketplace: str | None = None,
    status: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    limit: int = 200,
) -> list[MarketplaceListing]:
    """Return listings matching the given filters."""
    _ensure()
    query = "SELECT * FROM marketplace_listings WHERE 1=1"
    params: list[Any] = []
    if account_id is not None:
        query += " AND account_id=?"
        params.append(account_id)
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)
    if status:
        query += " AND status=?"
        params.append(status)
    if min_price is not None:
        query += " AND price>=?"
        params.append(min_price)
    if max_price is not None:
        query += " AND price<=?"
        params.append(max_price)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_listing(r) for r in rows]
    finally:
        conn.close()


def update_listing_price(
    account_id: int,
    listing_id: str,
    new_price: float,
) -> bool:
    """Update listing price and record a fixed price-sync rule entry for history."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE marketplace_listings SET price=?, updated_at=? WHERE account_id=? AND listing_id=?",
            (new_price, now, account_id, listing_id),
        )
        if cur.rowcount > 0:
            conn.execute(
                """
                INSERT INTO price_sync_rules
                    (account_id, listing_id, rule_type, value, created_at)
                VALUES (?, ?, 'fixed', ?, ?)
                """,
                (account_id, listing_id, new_price, now),
            )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def update_listing_quantity(
    account_id: int,
    listing_id: str,
    quantity: int,
) -> bool:
    """Update the available quantity for a listing."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE marketplace_listings SET quantity=?, updated_at=? WHERE account_id=? AND listing_id=?",
            (quantity, now, account_id, listing_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def deactivate_listing(account_id: int, listing_id: str) -> bool:
    """Set listing status to 'inactive'."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE marketplace_listings SET status='inactive', updated_at=? WHERE account_id=? AND listing_id=?",
            (now, account_id, listing_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def top_listings(
    marketplace: str | None = None,
    limit: int = 10,
) -> list[MarketplaceListing]:
    """Return listings ordered by total revenue descending."""
    _ensure()
    query = "SELECT * FROM marketplace_listings WHERE 1=1"
    params: list[Any] = []
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)
    query += " ORDER BY revenue DESC LIMIT ?"
    params.append(limit)

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_listing(r) for r in rows]
    finally:
        conn.close()


def low_stock_listings(threshold: int = 5) -> list[MarketplaceListing]:
    """Return active listings where quantity is at or below the threshold."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM marketplace_listings WHERE quantity<=? AND status='active' ORDER BY quantity ASC",
            (threshold,),
        ).fetchall()
        return [_row_to_listing(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def record_order(
    account_id: int,
    order_id: str,
    listing_id: str,
    marketplace: str,
    buyer_name: str,
    buyer_email: str,
    quantity: int,
    unit_price: float,
    total_price: float,
    shipping_cost: float = 0.0,
    status: str = "pending",
) -> MarketplaceOrder:
    """Insert or replace an order record. Also updates listing sales stats."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        # Check if this is a new order (for updating listing stats)
        existing = conn.execute(
            "SELECT id FROM marketplace_orders WHERE order_id=?",
            (order_id,),
        ).fetchone()

        conn.execute(
            """
            INSERT INTO marketplace_orders
                (account_id, marketplace, order_id, listing_id, buyer_name,
                 buyer_email, quantity, unit_price, total_price, shipping_cost,
                 status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(order_id) DO UPDATE SET
                status        = excluded.status,
                buyer_name    = excluded.buyer_name,
                buyer_email   = excluded.buyer_email,
                quantity      = excluded.quantity,
                unit_price    = excluded.unit_price,
                total_price   = excluded.total_price,
                shipping_cost = excluded.shipping_cost,
                updated_at    = excluded.updated_at
            """,
            (
                account_id, marketplace, order_id, listing_id, buyer_name,
                buyer_email, quantity, unit_price, total_price, shipping_cost,
                status, now, now,
            ),
        )

        # Update listing revenue / sales_count only for new completed-ish orders
        if not existing and status not in ("cancelled", "refunded", "disputed"):
            conn.execute(
                """
                UPDATE marketplace_listings
                SET sales_count = sales_count + ?,
                    revenue     = revenue + ?,
                    updated_at  = ?
                WHERE account_id=? AND listing_id=?
                """,
                (quantity, total_price, now, account_id, listing_id),
            )

        conn.commit()
        row = conn.execute(
            "SELECT * FROM marketplace_orders WHERE order_id=?",
            (order_id,),
        ).fetchone()
        return _row_to_order(row)
    finally:
        conn.close()


def get_order(order_id: str) -> MarketplaceOrder | None:
    """Return a single order or None."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM marketplace_orders WHERE order_id=?",
            (order_id,),
        ).fetchone()
        return _row_to_order(row) if row else None
    finally:
        conn.close()


def list_orders(
    account_id: int | None = None,
    marketplace: str | None = None,
    status: str | None = None,
    days: int | None = None,
    limit: int = 200,
) -> list[MarketplaceOrder]:
    """Return orders matching the given filters."""
    _ensure()
    query = "SELECT * FROM marketplace_orders WHERE 1=1"
    params: list[Any] = []
    if account_id is not None:
        query += " AND account_id=?"
        params.append(account_id)
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)
    if status:
        query += " AND status=?"
        params.append(status)
    if days is not None:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        query += " AND created_at>=?"
        params.append(cutoff)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [_row_to_order(r) for r in rows]
    finally:
        conn.close()


def update_order_status(
    order_id: str,
    new_status: str,
    tracking_number: str | None = None,
    shipped_at: str | None = None,
) -> bool:
    """Update order status and optionally set shipping details."""
    _ensure()
    now = _now()
    updates = ["status=?", "updated_at=?"]
    params: list[Any] = [new_status, now]
    if tracking_number is not None:
        updates.append("tracking_number=?")
        params.append(tracking_number)
    if shipped_at is not None:
        updates.append("shipped_at=?")
        params.append(shipped_at)
    params.append(order_id)
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE marketplace_orders SET {', '.join(updates)} WHERE order_id=?",
            params,
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def cancel_order(order_id: str, reason: str = "") -> bool:
    """Cancel an order and append the reason to its notes field."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        note_suffix = f" | Cancelled: {reason}" if reason else ""
        cur = conn.execute(
            """
            UPDATE marketplace_orders
            SET status='cancelled', updated_at=?,
                notes = notes || ?
            WHERE order_id=?
            """,
            (now, note_suffix, order_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------

def record_fee(
    account_id: int,
    marketplace: str,
    fee_type: str,
    amount: float,
    order_id: str = "",
    period_start: str = "",
    period_end: str = "",
) -> dict[str, Any]:
    """Record a marketplace fee. Returns the inserted row as a dict."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO marketplace_fees
                (account_id, marketplace, fee_type, amount, order_id,
                 period_start, period_end, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, marketplace, fee_type, amount, order_id,
             period_start, period_end, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM marketplace_fees WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def fee_summary(
    marketplace: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Aggregate fees by type. Returns {by_type: {fee_type: total}, total_fees}."""
    _ensure()
    query = "SELECT fee_type, SUM(amount) AS total FROM marketplace_fees WHERE 1=1"
    params: list[Any] = []
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)
    if days is not None:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        query += " AND created_at>=?"
        params.append(cutoff)
    query += " GROUP BY fee_type"

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        by_type = {r["fee_type"]: round(r["total"], 4) for r in rows}
        total = round(sum(by_type.values()), 4)
        return {
            "marketplace": marketplace,
            "days": days,
            "by_type": by_type,
            "total_fees": total,
        }
    finally:
        conn.close()


def net_revenue_report(
    marketplace: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    """Gross revenue minus recorded fees, broken down by marketplace."""
    _ensure()
    order_q = "SELECT marketplace, SUM(total_price) AS gross FROM marketplace_orders WHERE status NOT IN ('cancelled','refunded','disputed')"
    fee_q = "SELECT marketplace, SUM(amount) AS total_fees FROM marketplace_fees WHERE 1=1"
    params_o: list[Any] = []
    params_f: list[Any] = []

    if marketplace:
        order_q += " AND marketplace=?"
        fee_q += " AND marketplace=?"
        params_o.append(marketplace)
        params_f.append(marketplace)
    if days is not None:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        order_q += " AND created_at>=?"
        fee_q += " AND created_at>=?"
        params_o.append(cutoff)
        params_f.append(cutoff)

    order_q += " GROUP BY marketplace"
    fee_q += " GROUP BY marketplace"

    conn = _db()
    try:
        gross_rows = {r["marketplace"]: r["gross"] for r in conn.execute(order_q, params_o).fetchall()}
        fee_rows = {r["marketplace"]: r["total_fees"] for r in conn.execute(fee_q, params_f).fetchall()}

        all_mkts = set(gross_rows) | set(fee_rows)
        by_marketplace: dict[str, Any] = {}
        total_gross = 0.0
        total_fees = 0.0

        for mkt in sorted(all_mkts):
            gross = gross_rows.get(mkt, 0.0)
            fees = fee_rows.get(mkt, 0.0)
            net = gross - fees
            by_marketplace[mkt] = {
                "gross": round(gross, 4),
                "fees": round(fees, 4),
                "net": round(net, 4),
            }
            total_gross += gross
            total_fees += fees

        return {
            "gross": round(total_gross, 4),
            "fees": round(total_fees, 4),
            "net": round(total_gross - total_fees, 4),
            "days": days,
            "by_marketplace": by_marketplace,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

def record_review(
    account_id: int,
    marketplace: str,
    listing_id: str,
    reviewer_name: str,
    rating: int,
    review_text: str = "",
    review_date: str = "",
) -> dict[str, Any]:
    """Insert a review record. Returns the inserted row."""
    _ensure()
    now = _now()
    if not review_date:
        review_date = now
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO marketplace_reviews
                (account_id, marketplace, listing_id, reviewer_name, rating,
                 review_text, review_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, marketplace, listing_id, reviewer_name, rating,
             review_text, review_date, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM marketplace_reviews WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def respond_to_review(review_id: int, response_text: str) -> bool:
    """Save a seller response to an existing review."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE marketplace_reviews SET responded=1, response_text=? WHERE id=?",
            (response_text, review_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def review_summary(
    listing_id: str | None = None,
    marketplace: str | None = None,
) -> dict[str, Any]:
    """Return avg_rating, count, per-star breakdown, and response_rate."""
    _ensure()
    query = "SELECT rating, responded FROM marketplace_reviews WHERE 1=1"
    params: list[Any] = []
    if listing_id:
        query += " AND listing_id=?"
        params.append(listing_id)
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        if not rows:
            return {
                "listing_id": listing_id,
                "marketplace": marketplace,
                "count": 0,
                "avg_rating": 0.0,
                "by_star": {},
                "response_rate": 0.0,
            }

        total = len(rows)
        rating_sum = 0
        responded_count = 0
        by_star: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for r in rows:
            star = max(1, min(5, int(r["rating"])))
            by_star[star] = by_star.get(star, 0) + 1
            rating_sum += star
            if r["responded"]:
                responded_count += 1

        return {
            "listing_id": listing_id,
            "marketplace": marketplace,
            "count": total,
            "avg_rating": round(rating_sum / total, 2),
            "by_star": by_star,
            "response_rate": round(responded_count / total, 4),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Price sync rules
# ---------------------------------------------------------------------------

def create_price_rule(
    account_id: int,
    listing_id: str,
    rule_type: str,
    value: float,
    min_price: float = 0.0,
    max_price: float = 0.0,
) -> dict[str, Any]:
    """
    Create a price-sync rule for a listing.

    rule_type options:
      - ``fixed``                  — set price to ``value``
      - ``percent_off_competitor`` — reduce competitor price by ``value`` %
      - ``match_competitor``       — match competitor price exactly
      - ``dynamic_floor``          — never go below ``value`` (used as floor)
    """
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """
            INSERT INTO price_sync_rules
                (account_id, listing_id, rule_type, value, min_price, max_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (account_id, listing_id, rule_type, value, min_price, max_price, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM price_sync_rules WHERE id=?",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_price_rules(
    account_id: int | None = None,
    enabled_only: bool = False,
) -> list[dict[str, Any]]:
    """Return price-sync rules, optionally filtered by account and enabled flag."""
    _ensure()
    query = "SELECT * FROM price_sync_rules WHERE 1=1"
    params: list[Any] = []
    if account_id is not None:
        query += " AND account_id=?"
        params.append(account_id)
    if enabled_only:
        query += " AND enabled=1"
    query += " ORDER BY created_at DESC"

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def apply_price_rule(rule_id: int, competitor_price: float | None = None) -> float:
    """
    Calculate and return the new price according to the given rule.

    For ``fixed`` → returns rule value.
    For ``percent_off_competitor`` → competitor_price * (1 - value/100).
    For ``match_competitor`` → competitor_price.
    For ``dynamic_floor`` → max(current listing price, rule value).
    """
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM price_sync_rules WHERE id=?",
            (rule_id,),
        ).fetchone()
        if not row:
            return 0.0

        rule_type = row["rule_type"]
        value = row["value"]
        min_price = row["min_price"]
        max_price = row["max_price"]

        if rule_type == "fixed":
            new_price = value
        elif rule_type == "percent_off_competitor":
            if competitor_price is None:
                return 0.0
            new_price = competitor_price * (1.0 - value / 100.0)
        elif rule_type == "match_competitor":
            if competitor_price is None:
                return 0.0
            new_price = competitor_price
        elif rule_type == "dynamic_floor":
            listing = conn.execute(
                "SELECT price FROM marketplace_listings WHERE account_id=? AND listing_id=?",
                (row["account_id"], row["listing_id"]),
            ).fetchone()
            current_price = listing["price"] if listing else value
            new_price = max(current_price, value)
        else:
            new_price = value

        # Clamp to min/max if set
        if min_price > 0:
            new_price = max(new_price, min_price)
        if max_price > 0:
            new_price = min(new_price, max_price)

        return round(new_price, 4)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def marketplace_revenue_report(days: int = 30) -> dict[str, Any]:
    """Revenue, orders, avg order value, fees, and net per marketplace."""
    _ensure()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    conn = _db()
    try:
        order_rows = conn.execute(
            """
            SELECT marketplace,
                   SUM(total_price) AS revenue,
                   COUNT(*)         AS orders
            FROM marketplace_orders
            WHERE created_at>=?
              AND status NOT IN ('cancelled','refunded','disputed')
            GROUP BY marketplace
            """,
            (cutoff,),
        ).fetchall()

        fee_rows = conn.execute(
            """
            SELECT marketplace, SUM(amount) AS fees
            FROM marketplace_fees
            WHERE created_at>=?
            GROUP BY marketplace
            """,
            (cutoff,),
        ).fetchall()
        fees_by_mkt = {r["marketplace"]: r["fees"] for r in fee_rows}

        result: dict[str, Any] = {}
        for r in order_rows:
            mkt = r["marketplace"]
            revenue = r["revenue"] or 0.0
            orders = r["orders"] or 0
            fees = fees_by_mkt.get(mkt, 0.0)
            result[mkt] = {
                "revenue": round(revenue, 4),
                "orders": orders,
                "avg_order_value": round(revenue / orders, 4) if orders else 0.0,
                "fees": round(fees, 4),
                "net": round(revenue - fees, 4),
            }
        return {"days": days, "by_marketplace": result}
    finally:
        conn.close()


def best_selling_products(
    marketplace: str | None = None,
    limit: int = 10,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """Return top products ranked by sales_count (and revenue as tiebreaker)."""
    _ensure()
    query = """
        SELECT l.id, l.listing_id, l.product_title, l.marketplace,
               l.sku, l.price, l.sales_count, l.revenue, l.views, l.status
        FROM marketplace_listings l
        WHERE 1=1
    """
    params: list[Any] = []
    if marketplace:
        query += " AND l.marketplace=?"
        params.append(marketplace)
    if days is not None:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        query += " AND l.last_synced_at>=?"
        params.append(cutoff)
    query += " ORDER BY l.sales_count DESC, l.revenue DESC LIMIT ?"
    params.append(limit)

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def geographic_sales_breakdown(
    marketplace: str | None = None,
    days: int | None = None,
) -> list[dict[str, Any]]:
    """
    Approximate geographic breakdown derived from buyer email domains.

    Groups orders by the TLD of the buyer email domain and sums revenue.
    Returns a list of {region, order_count, revenue} dicts sorted by revenue.
    """
    _ensure()
    query = "SELECT buyer_email, total_price FROM marketplace_orders WHERE status NOT IN ('cancelled','refunded','disputed')"
    params: list[Any] = []
    if marketplace:
        query += " AND marketplace=?"
        params.append(marketplace)
    if days is not None:
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
        query += " AND created_at>=?"
        params.append(cutoff)

    conn = _db()
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    region_data: dict[str, dict[str, Any]] = {}
    for r in rows:
        email = r["buyer_email"] or ""
        if "@" in email:
            domain = email.split("@", 1)[1].lower()
            tld = domain.rsplit(".", 1)[-1] if "." in domain else "unknown"
        else:
            tld = "unknown"

        # Map common TLDs to region names
        tld_map = {
            "com": "US/International",
            "us": "United States",
            "uk": "United Kingdom",
            "co": "Colombia/Unknown",
            "ca": "Canada",
            "au": "Australia",
            "de": "Germany",
            "fr": "France",
            "jp": "Japan",
            "in": "India",
            "br": "Brazil",
            "mx": "Mexico",
            "nl": "Netherlands",
            "es": "Spain",
            "it": "Italy",
        }
        region = tld_map.get(tld, tld.upper())
        if region not in region_data:
            region_data[region] = {"region": region, "order_count": 0, "revenue": 0.0}
        region_data[region]["order_count"] += 1
        region_data[region]["revenue"] += r["total_price"] or 0.0

    result = sorted(region_data.values(), key=lambda x: x["revenue"], reverse=True)
    for item in result:
        item["revenue"] = round(item["revenue"], 4)
    return result


def marketplace_summary() -> dict[str, Any]:
    """
    High-level dashboard snapshot:
    total_marketplaces, active_listings, total_revenue_30d,
    total_orders_30d, avg_rating, top_marketplace, pending_orders.
    """
    _ensure()
    cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat(timespec="seconds")
    conn = _db()
    try:
        total_mkts = conn.execute(
            "SELECT COUNT(DISTINCT marketplace) FROM marketplace_accounts WHERE status='active'"
        ).fetchone()[0]

        active_listings = conn.execute(
            "SELECT COUNT(*) FROM marketplace_listings WHERE status='active'"
        ).fetchone()[0]

        rev_row = conn.execute(
            "SELECT SUM(total_price) FROM marketplace_orders WHERE created_at>=? AND status NOT IN ('cancelled','refunded','disputed')",
            (cutoff_30d,),
        ).fetchone()
        total_revenue_30d = round(rev_row[0] or 0.0, 4)

        total_orders_30d = conn.execute(
            "SELECT COUNT(*) FROM marketplace_orders WHERE created_at>=? AND status NOT IN ('cancelled','refunded','disputed')",
            (cutoff_30d,),
        ).fetchone()[0]

        rating_row = conn.execute(
            "SELECT AVG(rating) FROM marketplace_reviews"
        ).fetchone()
        avg_rating = round(rating_row[0] or 0.0, 2)

        top_mkt_row = conn.execute(
            """
            SELECT marketplace, SUM(total_price) AS rev
            FROM marketplace_orders
            WHERE created_at>=? AND status NOT IN ('cancelled','refunded','disputed')
            GROUP BY marketplace
            ORDER BY rev DESC
            LIMIT 1
            """,
            (cutoff_30d,),
        ).fetchone()
        top_marketplace = top_mkt_row["marketplace"] if top_mkt_row else None

        pending_orders = conn.execute(
            "SELECT COUNT(*) FROM marketplace_orders WHERE status='pending'"
        ).fetchone()[0]

        return {
            "total_marketplaces": total_mkts,
            "active_listings": active_listings,
            "total_revenue_30d": total_revenue_30d,
            "total_orders_30d": total_orders_30d,
            "avg_rating": avg_rating,
            "top_marketplace": top_marketplace,
            "pending_orders": pending_orders,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

@tool("mc_connect_account", "Connect a marketplace account by storing hashed credentials")
def mc_connect_account_tool(
    marketplace: str,
    account_name: str,
    api_key: str,
    shop_url: str = "",
) -> str:
    """Register or update a marketplace account."""
    try:
        if marketplace not in MARKETPLACES:
            return json.dumps({"error": f"Unknown marketplace '{marketplace}'. Valid: {MARKETPLACES}"})
        result = connect_account(marketplace, account_name, api_key, shop_url)
        # Never leak the hash in responses — redact it
        result.pop("api_key_hash", None)
        return json.dumps({"ok": True, "account": result})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_sync_listing", "Upsert a product listing from a marketplace account")
def mc_sync_listing_tool(
    account_id: int,
    listing_id: str,
    product_title: str,
    price: float,
    quantity: int,
    status: str = "active",
) -> str:
    """Sync (insert or update) a marketplace listing."""
    try:
        listing = sync_listing(
            account_id=account_id,
            listing_id=listing_id,
            product_title=product_title,
            price=price,
            quantity=quantity,
            status=status,
        )
        return json.dumps({"ok": True, "listing": listing.to_dict()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_list_listings", "List marketplace product listings with optional filters")
def mc_list_listings_tool(
    marketplace: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> str:
    """Return listings filtered by marketplace and/or status."""
    try:
        listings = list_listings(marketplace=marketplace, status=status, limit=limit)
        return json.dumps({
            "ok": True,
            "count": len(listings),
            "listings": [l.to_dict() for l in listings],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_record_order", "Record a new marketplace order and update listing stats")
def mc_record_order_tool(
    account_id: int,
    order_id: str,
    listing_id: str,
    marketplace: str,
    buyer_name: str,
    buyer_email: str,
    quantity: int,
    total_price: float,
) -> str:
    """Insert or update a marketplace order."""
    try:
        unit_price = total_price / max(quantity, 1)
        order = record_order(
            account_id=account_id,
            order_id=order_id,
            listing_id=listing_id,
            marketplace=marketplace,
            buyer_name=buyer_name,
            buyer_email=buyer_email,
            quantity=quantity,
            unit_price=unit_price,
            total_price=total_price,
        )
        return json.dumps({"ok": True, "order": order.to_dict()})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_revenue_report", "Get per-marketplace revenue report for the last N days")
def mc_revenue_report_tool(days: int = 30) -> str:
    """Return revenue, orders, fees, and net per marketplace."""
    try:
        report = marketplace_revenue_report(days=days)
        return json.dumps({"ok": True, "report": report})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_top_listings", "Retrieve top-earning product listings")
def mc_top_listings_tool(
    marketplace: str | None = None,
    limit: int = 10,
) -> str:
    """Return the highest-revenue listings, optionally filtered by marketplace."""
    try:
        listings = top_listings(marketplace=marketplace, limit=limit)
        return json.dumps({
            "ok": True,
            "count": len(listings),
            "listings": [l.to_dict() for l in listings],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_marketplace_summary", "High-level snapshot of all marketplace activity")
def mc_marketplace_summary_tool() -> str:
    """Return a dashboard-style summary of marketplace health."""
    try:
        summary = marketplace_summary()
        return json.dumps({"ok": True, "summary": summary})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_fee_summary", "Summarise marketplace fees by type")
def mc_fee_summary_tool(
    marketplace: str | None = None,
    days: int | None = None,
) -> str:
    """Aggregate recorded fees, broken down by fee type."""
    try:
        summary = fee_summary(marketplace=marketplace, days=days)
        return json.dumps({"ok": True, "fee_summary": summary})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_low_stock", "Find active listings with low inventory")
def mc_low_stock_tool(threshold: int = 5) -> str:
    """Return active listings where quantity is at or below the threshold."""
    try:
        listings = low_stock_listings(threshold=threshold)
        return json.dumps({
            "ok": True,
            "threshold": threshold,
            "count": len(listings),
            "listings": [l.to_dict() for l in listings],
        })
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("mc_review_summary", "Summarise customer reviews for a listing or marketplace")
def mc_review_summary_tool(
    listing_id: str | None = None,
    marketplace: str | None = None,
) -> str:
    """Return average rating, star breakdown, and response rate for reviews."""
    try:
        summary = review_summary(listing_id=listing_id, marketplace=marketplace)
        return json.dumps({"ok": True, "review_summary": summary})
    except Exception as exc:
        return json.dumps({"error": str(exc)})
