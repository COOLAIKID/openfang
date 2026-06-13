"""
Affiliate tracker — manage affiliate programs, links, commissions, and payouts.

Tracks clicks, conversions, and commissions per affiliate link. Supports
multiple affiliate programs (Amazon, ClickBank, Impact, ShareASale, etc.)
and generates performance reports. All data stored in SQLite.
"""

from __future__ import annotations

import hashlib
import json
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
    db_path = cfg("affiliate.db_path", fallback="autoearn.db")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _ensure_schema(_conn)
    return _conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS affiliate_programs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,
            network         TEXT    NOT NULL DEFAULT '',
            base_url        TEXT    NOT NULL DEFAULT '',
            tracking_param  TEXT    NOT NULL DEFAULT 'ref',
            affiliate_id    TEXT    NOT NULL DEFAULT '',
            commission_type TEXT    NOT NULL DEFAULT 'percentage',
            commission_rate REAL    NOT NULL DEFAULT 0.0,
            cookie_days     INTEGER NOT NULL DEFAULT 30,
            payout_threshold REAL   NOT NULL DEFAULT 50.0,
            currency        TEXT    NOT NULL DEFAULT 'USD',
            active          INTEGER NOT NULL DEFAULT 1,
            notes           TEXT    NOT NULL DEFAULT '',
            created_at      REAL    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_links (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id      INTEGER NOT NULL,
            slug            TEXT    NOT NULL UNIQUE,
            destination_url TEXT    NOT NULL,
            short_code      TEXT    NOT NULL UNIQUE,
            content_id      TEXT    NOT NULL DEFAULT '',
            campaign        TEXT    NOT NULL DEFAULT '',
            created_at      REAL    NOT NULL,
            active          INTEGER NOT NULL DEFAULT 1,
            clicks          INTEGER NOT NULL DEFAULT 0,
            unique_clicks   INTEGER NOT NULL DEFAULT 0,
            conversions     INTEGER NOT NULL DEFAULT 0,
            revenue         REAL    NOT NULL DEFAULT 0.0,
            commissions     REAL    NOT NULL DEFAULT 0.0,
            last_click_at   REAL
        );

        CREATE TABLE IF NOT EXISTS affiliate_clicks (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id         INTEGER NOT NULL,
            ts              REAL    NOT NULL,
            ip_hash         TEXT    NOT NULL DEFAULT '',
            referrer        TEXT    NOT NULL DEFAULT '',
            user_agent      TEXT    NOT NULL DEFAULT '',
            country         TEXT    NOT NULL DEFAULT '',
            unique_click    INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS affiliate_conversions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id         INTEGER NOT NULL,
            program_id      INTEGER NOT NULL,
            ts              REAL    NOT NULL,
            order_id        TEXT    NOT NULL DEFAULT '',
            sale_amount     REAL    NOT NULL DEFAULT 0.0,
            commission      REAL    NOT NULL DEFAULT 0.0,
            status          TEXT    NOT NULL DEFAULT 'pending',
            paid_at         REAL,
            click_id        INTEGER NOT NULL DEFAULT 0,
            meta            TEXT    NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS affiliate_payouts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            program_id      INTEGER NOT NULL,
            amount          REAL    NOT NULL,
            currency        TEXT    NOT NULL DEFAULT 'USD',
            paid_at         REAL    NOT NULL,
            method          TEXT    NOT NULL DEFAULT '',
            reference       TEXT    NOT NULL DEFAULT '',
            conversion_ids  TEXT    NOT NULL DEFAULT '[]',
            notes           TEXT    NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_aff_link_prog  ON affiliate_links(program_id);
        CREATE INDEX IF NOT EXISTS idx_aff_link_slug  ON affiliate_links(slug);
        CREATE INDEX IF NOT EXISTS idx_aff_click_link ON affiliate_clicks(link_id);
        CREATE INDEX IF NOT EXISTS idx_aff_click_ts   ON affiliate_clicks(ts);
        CREATE INDEX IF NOT EXISTS idx_aff_conv_link  ON affiliate_conversions(link_id);
        CREATE INDEX IF NOT EXISTS idx_aff_conv_prog  ON affiliate_conversions(program_id);
        CREATE INDEX IF NOT EXISTS idx_aff_conv_ts    ON affiliate_conversions(ts);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

COMMISSION_TYPES = ["percentage", "flat", "tiered", "cpa", "cpl", "cpc"]
CONVERSION_STATUSES = ["pending", "approved", "rejected", "paid"]

POPULAR_NETWORKS = {
    "amazon":    {"network": "Amazon Associates", "commission_type": "percentage", "commission_rate": 4.0, "cookie_days": 24},
    "clickbank": {"network": "ClickBank",         "commission_type": "percentage", "commission_rate": 50.0, "cookie_days": 60},
    "shareasale":{"network": "ShareASale",         "commission_type": "percentage", "commission_rate": 10.0, "cookie_days": 30},
    "impact":    {"network": "Impact",             "commission_type": "percentage", "commission_rate": 15.0, "cookie_days": 30},
    "cj":        {"network": "CJ Affiliate",       "commission_type": "percentage", "commission_rate": 8.0,  "cookie_days": 30},
    "rakuten":   {"network": "Rakuten",            "commission_type": "percentage", "commission_rate": 7.0,  "cookie_days": 30},
    "awin":      {"network": "Awin",               "commission_type": "percentage", "commission_rate": 8.0,  "cookie_days": 30},
    "partnerstack":{"network": "PartnerStack",     "commission_type": "percentage", "commission_rate": 20.0, "cookie_days": 90},
}


@dataclass
class AffiliateLink:
    program_id: int
    slug: str
    destination_url: str
    short_code: str = ""
    content_id: str = ""
    campaign: str = ""
    active: bool = True
    clicks: int = 0
    unique_clicks: int = 0
    conversions: int = 0
    revenue: float = 0.0
    commissions: float = 0.0
    id: int = 0

    @property
    def conversion_rate(self) -> float:
        if self.unique_clicks == 0:
            return 0.0
        return round(self.conversions / self.unique_clicks * 100, 2)

    @property
    def epc(self) -> float:
        """Earnings per click."""
        if self.clicks == 0:
            return 0.0
        return round(self.commissions / self.clicks, 4)

    @property
    def avg_order_value(self) -> float:
        if self.conversions == 0:
            return 0.0
        return round(self.revenue / self.conversions, 4)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "program_id": self.program_id,
            "slug": self.slug,
            "destination_url": self.destination_url,
            "short_code": self.short_code,
            "content_id": self.content_id,
            "campaign": self.campaign,
            "active": self.active,
            "clicks": self.clicks,
            "unique_clicks": self.unique_clicks,
            "conversions": self.conversions,
            "revenue": round(self.revenue, 4),
            "commissions": round(self.commissions, 4),
            "conversion_rate_pct": self.conversion_rate,
            "epc": self.epc,
            "avg_order_value": self.avg_order_value,
        }


# ---------------------------------------------------------------------------
# Program management
# ---------------------------------------------------------------------------

def add_program(
    name: str,
    network: str = "",
    base_url: str = "",
    affiliate_id: str = "",
    commission_type: str = "percentage",
    commission_rate: float = 0.0,
    cookie_days: int = 30,
    payout_threshold: float = 50.0,
    currency: str = "USD",
    tracking_param: str = "ref",
    notes: str = "",
    preset: str = "",
) -> int:
    """Add an affiliate program. Optionally use a preset network config."""
    if preset and preset in POPULAR_NETWORKS:
        defaults = POPULAR_NETWORKS[preset]
        network = network or defaults["network"]
        commission_type = commission_type or defaults["commission_type"]
        commission_rate = commission_rate or defaults["commission_rate"]
        cookie_days = cookie_days or defaults["cookie_days"]

    db = _db()
    cur = db.execute(
        """INSERT INTO affiliate_programs
           (name, network, base_url, tracking_param, affiliate_id, commission_type,
            commission_rate, cookie_days, payout_threshold, currency, notes, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
             network=excluded.network,
             commission_rate=excluded.commission_rate,
             affiliate_id=excluded.affiliate_id""",
        (name, network, base_url, tracking_param, affiliate_id, commission_type,
         commission_rate, cookie_days, payout_threshold, currency, notes, time.time()),
    )
    db.commit()
    return cur.lastrowid or 0


def get_program(program_id: int) -> dict | None:
    """Get program details by ID."""
    row = _db().execute(
        "SELECT * FROM affiliate_programs WHERE id=?", (program_id,)
    ).fetchone()
    return dict(row) if row else None


def get_program_by_name(name: str) -> dict | None:
    """Get program details by name."""
    row = _db().execute(
        "SELECT * FROM affiliate_programs WHERE name=?", (name,)
    ).fetchone()
    return dict(row) if row else None


def list_programs(active_only: bool = True) -> list[dict]:
    """List all affiliate programs."""
    query = "SELECT * FROM affiliate_programs"
    if active_only:
        query += " WHERE active=1"
    query += " ORDER BY name"
    return [dict(row) for row in _db().execute(query).fetchall()]


def deactivate_program(program_id: int) -> bool:
    """Deactivate a program."""
    db = _db()
    db.execute("UPDATE affiliate_programs SET active=0 WHERE id=?", (program_id,))
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Link management
# ---------------------------------------------------------------------------

def _make_short_code(destination_url: str) -> str:
    """Generate a short 8-char alphanumeric code from the destination URL."""
    h = hashlib.sha256(f"{destination_url}{time.time()}".encode()).hexdigest()
    return h[:8]


def create_link(
    program_name: str,
    destination_url: str,
    slug: str = "",
    content_id: str = "",
    campaign: str = "",
) -> dict:
    """Create a tracked affiliate link. Returns link details."""
    program = get_program_by_name(program_name)
    if program is None:
        return {"error": f"program '{program_name}' not found"}

    short_code = _make_short_code(destination_url)
    if not slug:
        slug = short_code

    # Build the tracking URL
    sep = "&" if "?" in destination_url else "?"
    tracking_url = f"{destination_url}{sep}{program['tracking_param']}={program['affiliate_id']}"

    db = _db()
    try:
        cur = db.execute(
            """INSERT INTO affiliate_links
               (program_id, slug, destination_url, short_code, content_id, campaign, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (program["id"], slug, tracking_url, short_code, content_id, campaign, time.time()),
        )
        db.commit()
        link_id = cur.lastrowid or 0
    except sqlite3.IntegrityError:
        # Slug already exists, append unique suffix
        slug = f"{slug}-{short_code[:4]}"
        cur = db.execute(
            """INSERT INTO affiliate_links
               (program_id, slug, destination_url, short_code, content_id, campaign, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (program["id"], slug, tracking_url, short_code, content_id, campaign, time.time()),
        )
        db.commit()
        link_id = cur.lastrowid or 0

    return {
        "ok": True,
        "link_id": link_id,
        "slug": slug,
        "short_code": short_code,
        "tracking_url": tracking_url,
        "program": program_name,
    }


def get_link(link_id: int) -> dict | None:
    """Get link details by ID."""
    row = _db().execute(
        "SELECT * FROM affiliate_links WHERE id=?", (link_id,)
    ).fetchone()
    if row is None:
        return None
    link = AffiliateLink(
        program_id=row["program_id"],
        slug=row["slug"],
        destination_url=row["destination_url"],
        short_code=row["short_code"],
        content_id=row["content_id"],
        campaign=row["campaign"],
        clicks=row["clicks"],
        unique_clicks=row["unique_clicks"],
        conversions=row["conversions"],
        revenue=row["revenue"],
        commissions=row["commissions"],
        id=row["id"],
    )
    return link.to_dict()


def get_link_by_slug(slug: str) -> dict | None:
    """Get link details by slug."""
    row = _db().execute(
        "SELECT * FROM affiliate_links WHERE slug=?", (slug,)
    ).fetchone()
    return dict(row) if row else None


def list_links(
    program_name: str = "",
    content_id: str = "",
    campaign: str = "",
    active_only: bool = True,
    limit: int = 100,
) -> list[dict]:
    """List affiliate links with optional filters."""
    query = """SELECT l.*, p.name as program_name, p.commission_rate
               FROM affiliate_links l
               JOIN affiliate_programs p ON p.id = l.program_id
               WHERE 1=1"""
    params: list[Any] = []
    if program_name:
        query += " AND p.name=?"
        params.append(program_name)
    if content_id:
        query += " AND l.content_id=?"
        params.append(content_id)
    if campaign:
        query += " AND l.campaign=?"
        params.append(campaign)
    if active_only:
        query += " AND l.active=1"
    query += " ORDER BY l.commissions DESC LIMIT ?"
    params.append(limit)

    rows = _db().execute(query, params).fetchall()
    results = []
    for row in rows:
        link = AffiliateLink(
            program_id=row["program_id"],
            slug=row["slug"],
            destination_url=row["destination_url"],
            short_code=row["short_code"],
            content_id=row.get("content_id", ""),
            campaign=row.get("campaign", ""),
            clicks=row["clicks"],
            unique_clicks=row["unique_clicks"],
            conversions=row["conversions"],
            revenue=row["revenue"],
            commissions=row["commissions"],
            id=row["id"],
        )
        d = link.to_dict()
        d["program_name"] = row["program_name"]
        d["commission_rate"] = row["commission_rate"]
        results.append(d)
    return results


# ---------------------------------------------------------------------------
# Click tracking
# ---------------------------------------------------------------------------

def record_click(
    link_id: int,
    ip_hash: str = "",
    referrer: str = "",
    user_agent: str = "",
    country: str = "",
) -> dict:
    """Record a click on an affiliate link. Returns click details."""
    db = _db()
    now = time.time()

    # Check if unique click (same IP in last 24h = not unique)
    is_unique = True
    if ip_hash:
        recent = db.execute(
            """SELECT COUNT(*) FROM affiliate_clicks
               WHERE link_id=? AND ip_hash=? AND ts > ?""",
            (link_id, ip_hash, now - 86400),
        ).fetchone()[0]
        is_unique = recent == 0

    cur = db.execute(
        """INSERT INTO affiliate_clicks
           (link_id, ts, ip_hash, referrer, user_agent, country, unique_click)
           VALUES (?,?,?,?,?,?,?)""",
        (link_id, now, ip_hash, referrer, user_agent, country, int(is_unique)),
    )
    click_id = cur.lastrowid or 0

    # Update link aggregates
    db.execute(
        """UPDATE affiliate_links SET
           clicks=clicks+1,
           unique_clicks=unique_clicks+?,
           last_click_at=?
           WHERE id=?""",
        (int(is_unique), now, link_id),
    )
    db.commit()

    return {"click_id": click_id, "link_id": link_id, "unique": is_unique}


def record_conversion(
    link_id: int,
    sale_amount: float,
    order_id: str = "",
    status: str = "pending",
    click_id: int = 0,
    meta: dict | None = None,
) -> dict:
    """Record an affiliate conversion (sale). Calculates commission automatically."""
    db = _db()
    link_row = db.execute("SELECT * FROM affiliate_links WHERE id=?", (link_id,)).fetchone()
    if link_row is None:
        return {"error": f"link {link_id} not found"}

    prog = db.execute(
        "SELECT * FROM affiliate_programs WHERE id=?", (link_row["program_id"],)
    ).fetchone()
    if prog is None:
        return {"error": "program not found"}

    # Calculate commission
    if prog["commission_type"] == "percentage":
        commission = sale_amount * prog["commission_rate"] / 100.0
    elif prog["commission_type"] == "flat":
        commission = prog["commission_rate"]
    elif prog["commission_type"] in ("cpa", "cpl", "cpc"):
        commission = prog["commission_rate"]
    else:
        commission = sale_amount * prog["commission_rate"] / 100.0

    cur = db.execute(
        """INSERT INTO affiliate_conversions
           (link_id, program_id, ts, order_id, sale_amount, commission, status, click_id, meta)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (link_id, link_row["program_id"], time.time(), order_id,
         sale_amount, commission, status, click_id, json.dumps(meta or {})),
    )
    conversion_id = cur.lastrowid or 0

    db.execute(
        """UPDATE affiliate_links SET
           conversions=conversions+1,
           revenue=revenue+?,
           commissions=commissions+?
           WHERE id=?""",
        (sale_amount, commission, link_id),
    )
    db.commit()

    return {
        "conversion_id": conversion_id,
        "link_id": link_id,
        "sale_amount": round(sale_amount, 4),
        "commission": round(commission, 4),
        "commission_rate": prog["commission_rate"],
        "status": status,
    }


def approve_conversion(conversion_id: int) -> bool:
    """Approve a pending conversion."""
    db = _db()
    db.execute(
        "UPDATE affiliate_conversions SET status='approved' WHERE id=? AND status='pending'",
        (conversion_id,),
    )
    db.commit()
    return True


def reject_conversion(conversion_id: int, reason: str = "") -> bool:
    """Reject a conversion and reverse link aggregates."""
    db = _db()
    row = db.execute(
        "SELECT * FROM affiliate_conversions WHERE id=?", (conversion_id,)
    ).fetchone()
    if row is None:
        return False

    db.execute(
        "UPDATE affiliate_conversions SET status='rejected' WHERE id=?", (conversion_id,)
    )
    db.execute(
        """UPDATE affiliate_links SET
           conversions=conversions-1,
           revenue=revenue-?,
           commissions=commissions-?
           WHERE id=?""",
        (row["sale_amount"], row["commission"], row["link_id"]),
    )
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Payout tracking
# ---------------------------------------------------------------------------

def record_payout(
    program_id: int,
    amount: float,
    method: str = "bank_transfer",
    reference: str = "",
    notes: str = "",
    currency: str = "USD",
) -> int:
    """Record receipt of a payout from an affiliate network."""
    db = _db()

    # Get all approved, unpaid conversions for this program
    conv_rows = db.execute(
        """SELECT id FROM affiliate_conversions
           WHERE program_id=? AND status='approved' AND paid_at IS NULL""",
        (program_id,),
    ).fetchall()
    conv_ids = [r["id"] for r in conv_rows]

    cur = db.execute(
        """INSERT INTO affiliate_payouts
           (program_id, amount, currency, paid_at, method, reference, conversion_ids, notes)
           VALUES (?,?,?,?,?,?,?,?)""",
        (program_id, amount, currency, time.time(), method, reference,
         json.dumps(conv_ids), notes),
    )
    payout_id = cur.lastrowid or 0

    # Mark conversions as paid
    for cid in conv_ids:
        db.execute(
            "UPDATE affiliate_conversions SET status='paid', paid_at=? WHERE id=?",
            (time.time(), cid),
        )
    db.commit()
    return payout_id


def pending_payout_estimate(program_id: int) -> dict:
    """Estimate pending payout for a program."""
    db = _db()
    prog = get_program(program_id)
    if prog is None:
        return {"error": "program not found"}

    row = db.execute(
        """SELECT COUNT(*) as conversions, COALESCE(SUM(commission),0) as total
           FROM affiliate_conversions
           WHERE program_id=? AND status='approved' AND paid_at IS NULL""",
        (program_id,),
    ).fetchone()
    total = row["total"]
    threshold = prog["payout_threshold"]

    return {
        "program": prog["name"],
        "pending_conversions": row["conversions"],
        "pending_commission": round(total, 4),
        "payout_threshold": threshold,
        "above_threshold": total >= threshold,
        "currency": prog["currency"],
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def program_performance(days: int = 30) -> list[dict]:
    """Revenue and commissions per program for the last N days."""
    since = time.time() - days * 86400
    db = _db()
    rows = db.execute(
        """SELECT p.name as program, p.network,
                  COUNT(DISTINCT l.id) as links,
                  COALESCE(SUM(c.sale_amount),0) as revenue,
                  COALESCE(SUM(c.commission),0) as commissions,
                  COUNT(c.id) as conversions
           FROM affiliate_programs p
           LEFT JOIN affiliate_links l ON l.program_id=p.id
           LEFT JOIN affiliate_conversions c ON c.program_id=p.id AND c.ts>=?
           WHERE p.active=1
           GROUP BY p.id
           ORDER BY commissions DESC""",
        (since,),
    ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        clicks = db.execute(
            """SELECT COALESCE(SUM(l.clicks),0) as clicks
               FROM affiliate_links l WHERE l.program_id=(
                 SELECT id FROM affiliate_programs WHERE name=?
               )""",
            (d["program"],),
        ).fetchone()["clicks"]
        d["clicks"] = clicks
        d["epc"] = round(d["commissions"] / clicks, 4) if clicks > 0 else 0.0
        d["revenue"] = round(d["revenue"], 4)
        d["commissions"] = round(d["commissions"], 4)
        result.append(d)
    return result


def top_links(days: int = 30, limit: int = 20) -> list[dict]:
    """Top performing affiliate links by commissions."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT l.id, l.slug, l.content_id, l.campaign,
                  p.name as program,
                  l.clicks, l.unique_clicks, l.conversions,
                  COALESCE(SUM(c.sale_amount),0) as period_revenue,
                  COALESCE(SUM(c.commission),0) as period_commissions
           FROM affiliate_links l
           JOIN affiliate_programs p ON p.id=l.program_id
           LEFT JOIN affiliate_conversions c ON c.link_id=l.id AND c.ts>=?
           GROUP BY l.id
           ORDER BY period_commissions DESC
           LIMIT ?""",
        (since, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def affiliate_summary() -> dict:
    """Overall affiliate program summary."""
    db = _db()
    total_programs = db.execute(
        "SELECT COUNT(*) FROM affiliate_programs WHERE active=1"
    ).fetchone()[0]
    total_links = db.execute(
        "SELECT COUNT(*) FROM affiliate_links WHERE active=1"
    ).fetchone()[0]
    total_clicks = db.execute(
        "SELECT COALESCE(SUM(clicks),0) FROM affiliate_links"
    ).fetchone()[0]
    total_conversions = db.execute(
        "SELECT COUNT(*) FROM affiliate_conversions WHERE status != 'rejected'"
    ).fetchone()[0]
    total_revenue = db.execute(
        "SELECT COALESCE(SUM(sale_amount),0) FROM affiliate_conversions WHERE status != 'rejected'"
    ).fetchone()[0]
    total_commissions = db.execute(
        "SELECT COALESCE(SUM(commission),0) FROM affiliate_conversions WHERE status != 'rejected'"
    ).fetchone()[0]
    total_paid = db.execute(
        "SELECT COALESCE(SUM(amount),0) FROM affiliate_payouts"
    ).fetchone()[0]

    return {
        "active_programs": total_programs,
        "active_links": total_links,
        "total_clicks": total_clicks,
        "total_conversions": total_conversions,
        "total_revenue": round(total_revenue, 4),
        "total_commissions_earned": round(total_commissions, 4),
        "total_commissions_paid": round(total_paid, 4),
        "total_commissions_pending": round(total_commissions - total_paid, 4),
        "overall_conversion_rate_pct": round(total_conversions / total_clicks * 100, 2) if total_clicks > 0 else 0.0,
        "overall_epc": round(total_commissions / total_clicks, 4) if total_clicks > 0 else 0.0,
    }


def content_performance(days: int = 30) -> list[dict]:
    """Commission performance per content piece."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT l.content_id,
                  COUNT(DISTINCT l.id) as links,
                  SUM(l.clicks) as clicks,
                  SUM(l.conversions) as conversions,
                  COALESCE(SUM(c.commission),0) as commissions
           FROM affiliate_links l
           LEFT JOIN affiliate_conversions c ON c.link_id=l.id AND c.ts>=?
           WHERE l.content_id != ''
           GROUP BY l.content_id
           ORDER BY commissions DESC""",
        (since,),
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def add_program_tool(
    name: str = "",
    network: str = "",
    commission_rate: float = 0.0,
    commission_type: str = "percentage",
    preset: str = "",
) -> str:
    """Agent-callable: add an affiliate program."""
    if not name:
        return "error: program name required"
    pid = add_program(name, network=network, commission_rate=commission_rate,
                      commission_type=commission_type, preset=preset)
    return f"affiliate program '{name}' added (id={pid})"


def create_link_tool(
    program_name: str = "",
    destination_url: str = "",
    content_id: str = "",
    campaign: str = "",
) -> str:
    """Agent-callable: create a tracked affiliate link."""
    if not program_name or not destination_url:
        return "error: program_name and destination_url required"
    result = create_link(program_name, destination_url, content_id=content_id, campaign=campaign)
    if "error" in result:
        return f"error: {result['error']}"
    return json.dumps(result)


def record_conversion_tool(
    link_id: int = 0,
    sale_amount: float = 0.0,
    order_id: str = "",
) -> str:
    """Agent-callable: record an affiliate conversion."""
    if not link_id:
        return "error: link_id required"
    result = record_conversion(link_id, sale_amount, order_id)
    return json.dumps(result)


def affiliate_summary_tool() -> str:
    """Agent-callable: overall affiliate summary as JSON."""
    return json.dumps(affiliate_summary(), indent=2)


def program_performance_tool(days: int = 30) -> str:
    """Agent-callable: program performance report as JSON."""
    data = program_performance(days)
    if not data:
        return "no affiliate program data"
    return json.dumps(data, indent=2)


def top_links_tool(days: int = 30, limit: int = 10) -> str:
    """Agent-callable: top performing affiliate links."""
    links = top_links(days, limit)
    if not links:
        return "no affiliate link data"
    return json.dumps(links, indent=2)


def list_programs_tool() -> str:
    """Agent-callable: list all active affiliate programs."""
    programs = list_programs()
    if not programs:
        return "no affiliate programs configured"
    return json.dumps([{"id": p["id"], "name": p["name"], "network": p["network"],
                         "commission_rate": p["commission_rate"],
                         "commission_type": p["commission_type"]}
                        for p in programs], indent=2)
