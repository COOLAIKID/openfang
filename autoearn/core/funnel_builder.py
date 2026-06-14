"""
Sales funnel builder — create multi-step funnels, track visitors through
each step, record conversions, and analyse drop-off points.

Funnel types supported: lead-gen, product launch, webinar, tripwire,
high-ticket, and SaaS trial funnels.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import cfg

# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    db_path = cfg("funnel.db_path", fallback="autoearn.db")
    _conn = sqlite3.connect(db_path, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    _ensure_schema(_conn)
    return _conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS funnels (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL UNIQUE,
            description     TEXT    NOT NULL DEFAULT '',
            funnel_type     TEXT    NOT NULL DEFAULT 'generic',
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      REAL    NOT NULL,
            total_visitors  INTEGER NOT NULL DEFAULT 0,
            total_revenue   REAL    NOT NULL DEFAULT 0.0,
            goal_revenue    REAL    NOT NULL DEFAULT 0.0,
            tags            TEXT    NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS funnel_steps (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            funnel_id       INTEGER NOT NULL,
            step_order      INTEGER NOT NULL DEFAULT 0,
            name            TEXT    NOT NULL,
            step_type       TEXT    NOT NULL DEFAULT 'content',
            headline        TEXT    NOT NULL DEFAULT '',
            description     TEXT    NOT NULL DEFAULT '',
            cta_text        TEXT    NOT NULL DEFAULT '',
            cta_url         TEXT    NOT NULL DEFAULT '',
            price           REAL    NOT NULL DEFAULT 0.0,
            video_url       TEXT    NOT NULL DEFAULT '',
            expected_conv_rate REAL NOT NULL DEFAULT 0.1,
            UNIQUE(funnel_id, step_order)
        );

        CREATE TABLE IF NOT EXISTS funnel_visitors (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            funnel_id       INTEGER NOT NULL,
            step_id         INTEGER NOT NULL,
            session_id      TEXT    NOT NULL,
            source          TEXT    NOT NULL DEFAULT '',
            channel         TEXT    NOT NULL DEFAULT '',
            entered_at      REAL    NOT NULL,
            exited_at       REAL,
            converted       INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS funnel_conversions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            funnel_id       INTEGER NOT NULL,
            step_id         INTEGER NOT NULL,
            session_id      TEXT    NOT NULL,
            revenue         REAL    NOT NULL DEFAULT 0.0,
            converted_at    REAL    NOT NULL,
            order_id        TEXT    NOT NULL DEFAULT '',
            product_name    TEXT    NOT NULL DEFAULT ''
        );

        CREATE INDEX IF NOT EXISTS idx_fv_funnel  ON funnel_visitors(funnel_id);
        CREATE INDEX IF NOT EXISTS idx_fv_step    ON funnel_visitors(step_id);
        CREATE INDEX IF NOT EXISTS idx_fv_session ON funnel_visitors(session_id);
        CREATE INDEX IF NOT EXISTS idx_fc_funnel  ON funnel_conversions(funnel_id);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

STEP_TYPES = [
    "optin",     # email capture
    "sales",     # main sales page
    "upsell",    # order bump / upsell
    "downsell",  # cheaper alternative
    "thankyou",  # confirmation page
    "webinar",   # webinar registration/replay
    "content",   # free content / lead magnet
    "quiz",      # quiz funnel step
    "checkout",  # payment page
    "vsl",       # video sales letter
]

FUNNEL_TYPES = [
    "lead_gen",     # collect emails
    "product_launch",
    "webinar",
    "tripwire",     # low-price entry → upsell
    "high_ticket",  # application → call → close
    "saas_trial",   # trial signup → paid
    "affiliate",    # bridge page → affiliate offer
    "generic",
]


@dataclass
class FunnelStep:
    funnel_id: int
    step_order: int
    name: str
    step_type: str = "content"
    headline: str = ""
    description: str = ""
    cta_text: str = ""
    cta_url: str = ""
    price: float = 0.0
    video_url: str = ""
    expected_conv_rate: float = 0.10
    id: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "funnel_id": self.funnel_id,
            "step_order": self.step_order,
            "name": self.name,
            "step_type": self.step_type,
            "headline": self.headline,
            "description": self.description,
            "cta_text": self.cta_text,
            "cta_url": self.cta_url,
            "price": self.price,
            "expected_conv_rate": self.expected_conv_rate,
        }


@dataclass
class Funnel:
    name: str
    description: str = ""
    funnel_type: str = "generic"
    active: bool = True
    goal_revenue: float = 0.0
    tags: list = field(default_factory=list)
    steps: list[FunnelStep] = field(default_factory=list)
    total_visitors: int = 0
    total_revenue: float = 0.0
    id: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def revenue_progress_pct(self) -> float:
        if self.goal_revenue <= 0:
            return 0.0
        return round(self.total_revenue / self.goal_revenue * 100, 1)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "funnel_type": self.funnel_type,
            "active": self.active,
            "goal_revenue": self.goal_revenue,
            "total_visitors": self.total_visitors,
            "total_revenue": round(self.total_revenue, 4),
            "revenue_progress_pct": self.revenue_progress_pct,
            "steps": [s.to_dict() for s in self.steps],
            "tags": self.tags,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Funnel CRUD
# ---------------------------------------------------------------------------

def create_funnel(
    name: str,
    description: str = "",
    funnel_type: str = "generic",
    goal_revenue: float = 0.0,
    tags: list | None = None,
) -> int:
    """Create a new sales funnel. Returns funnel ID."""
    db = _db()
    cur = db.execute(
        """INSERT INTO funnels
           (name, description, funnel_type, active, created_at, goal_revenue, tags)
           VALUES (?,?,?,1,?,?,?)
           ON CONFLICT(name) DO UPDATE SET
             description=excluded.description,
             funnel_type=excluded.funnel_type,
             goal_revenue=excluded.goal_revenue""",
        (name, description, funnel_type, time.time(), goal_revenue,
         json.dumps(tags or [])),
    )
    db.commit()
    return cur.lastrowid or 0


def add_step(
    funnel_id: int,
    name: str,
    step_type: str = "content",
    step_order: int | None = None,
    headline: str = "",
    description: str = "",
    cta_text: str = "",
    cta_url: str = "",
    price: float = 0.0,
    video_url: str = "",
    expected_conv_rate: float = 0.10,
) -> int:
    """Add a step to a funnel. Returns step ID."""
    db = _db()
    if step_order is None:
        row = db.execute(
            "SELECT COALESCE(MAX(step_order),0)+1 FROM funnel_steps WHERE funnel_id=?",
            (funnel_id,),
        ).fetchone()[0]
        step_order = row

    cur = db.execute(
        """INSERT INTO funnel_steps
           (funnel_id, step_order, name, step_type, headline, description,
            cta_text, cta_url, price, video_url, expected_conv_rate)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(funnel_id, step_order) DO UPDATE SET
             name=excluded.name,
             step_type=excluded.step_type,
             headline=excluded.headline""",
        (funnel_id, step_order, name, step_type, headline, description,
         cta_text, cta_url, price, video_url, expected_conv_rate),
    )
    db.commit()
    return cur.lastrowid or 0


def get_funnel(funnel_id: int) -> dict | None:
    """Get funnel with all steps."""
    db = _db()
    row = db.execute("SELECT * FROM funnels WHERE id=?", (funnel_id,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["tags"] = json.loads(d.get("tags", "[]"))
    steps = db.execute(
        "SELECT * FROM funnel_steps WHERE funnel_id=? ORDER BY step_order",
        (funnel_id,),
    ).fetchall()
    d["steps"] = [dict(s) for s in steps]
    return d


def get_funnel_by_name(name: str) -> dict | None:
    """Get funnel by name."""
    row = _db().execute("SELECT id FROM funnels WHERE name=?", (name,)).fetchone()
    return get_funnel(row["id"]) if row else None


def list_funnels(active_only: bool = False, funnel_type: str = "") -> list[dict]:
    """List all funnels."""
    query = "SELECT * FROM funnels WHERE 1=1"
    params: list[Any] = []
    if active_only:
        query += " AND active=1"
    if funnel_type:
        query += " AND funnel_type=?"
        params.append(funnel_type)
    query += " ORDER BY total_revenue DESC"
    rows = _db().execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_funnel(funnel_id: int, **fields) -> bool:
    """Update funnel metadata."""
    allowed = {"name", "description", "funnel_type", "active", "goal_revenue"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    db = _db()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    db.execute(
        f"UPDATE funnels SET {set_clause} WHERE id=?",
        list(updates.values()) + [funnel_id],
    )
    db.commit()
    return True


def duplicate_funnel(funnel_id: int, new_name: str) -> int:
    """Copy a funnel and all its steps to a new name. Returns new funnel ID."""
    source = get_funnel(funnel_id)
    if source is None:
        return 0
    new_id = create_funnel(
        new_name,
        description=f"Copy of {source['name']}",
        funnel_type=source["funnel_type"],
        goal_revenue=source["goal_revenue"],
    )
    for step in source["steps"]:
        add_step(
            new_id,
            name=step["name"],
            step_type=step["step_type"],
            step_order=step["step_order"],
            headline=step["headline"],
            cta_text=step["cta_text"],
            price=step["price"],
            expected_conv_rate=step["expected_conv_rate"],
        )
    return new_id


# ---------------------------------------------------------------------------
# Visitor tracking
# ---------------------------------------------------------------------------

def record_visitor(
    funnel_id: int,
    step_id: int,
    session_id: str = "",
    source: str = "",
    channel: str = "",
) -> int:
    """Record a visitor entering a funnel step. Returns visitor record ID."""
    if not session_id:
        session_id = uuid.uuid4().hex
    db = _db()
    cur = db.execute(
        """INSERT INTO funnel_visitors
           (funnel_id, step_id, session_id, source, channel, entered_at)
           VALUES (?,?,?,?,?,?)""",
        (funnel_id, step_id, session_id, source, channel, time.time()),
    )
    db.execute(
        "UPDATE funnels SET total_visitors=total_visitors+1 WHERE id=?",
        (funnel_id,),
    )
    db.commit()
    return cur.lastrowid or 0


def record_exit(session_id: str, step_id: int) -> None:
    """Record when a visitor exits a step without converting."""
    db = _db()
    db.execute(
        "UPDATE funnel_visitors SET exited_at=? WHERE session_id=? AND step_id=?",
        (time.time(), session_id, step_id),
    )
    db.commit()


def record_conversion(
    funnel_id: int,
    step_id: int,
    session_id: str,
    revenue: float = 0.0,
    order_id: str = "",
    product_name: str = "",
) -> dict:
    """Record a conversion at a funnel step."""
    db = _db()
    cur = db.execute(
        """INSERT INTO funnel_conversions
           (funnel_id, step_id, session_id, revenue, converted_at, order_id, product_name)
           VALUES (?,?,?,?,?,?,?)""",
        (funnel_id, step_id, session_id, revenue, time.time(), order_id, product_name),
    )
    db.execute(
        "UPDATE funnel_visitors SET converted=1, exited_at=? WHERE session_id=? AND step_id=?",
        (time.time(), session_id, step_id),
    )
    db.execute(
        "UPDATE funnels SET total_revenue=total_revenue+? WHERE id=?",
        (revenue, funnel_id),
    )
    db.commit()
    return {
        "conversion_id": cur.lastrowid,
        "funnel_id": funnel_id,
        "step_id": step_id,
        "session_id": session_id,
        "revenue": revenue,
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def get_step_stats(funnel_id: int, step_id: int) -> dict:
    """Get visitor and conversion stats for a single step."""
    db = _db()
    visitors = db.execute(
        "SELECT COUNT(*) FROM funnel_visitors WHERE funnel_id=? AND step_id=?",
        (funnel_id, step_id),
    ).fetchone()[0]
    conversions = db.execute(
        "SELECT COUNT(*) FROM funnel_conversions WHERE funnel_id=? AND step_id=?",
        (funnel_id, step_id),
    ).fetchone()[0]
    revenue = db.execute(
        "SELECT COALESCE(SUM(revenue),0) FROM funnel_conversions WHERE funnel_id=? AND step_id=?",
        (funnel_id, step_id),
    ).fetchone()[0]
    conv_rate = round(conversions / visitors * 100, 2) if visitors > 0 else 0.0
    return {
        "step_id": step_id,
        "visitors": visitors,
        "conversions": conversions,
        "revenue": round(revenue, 4),
        "conversion_rate_pct": conv_rate,
        "revenue_per_visitor": round(revenue / visitors, 4) if visitors > 0 else 0.0,
    }


def funnel_overview(funnel_id: int) -> dict:
    """Full funnel analysis with per-step stats."""
    funnel = get_funnel(funnel_id)
    if funnel is None:
        return {"error": f"funnel {funnel_id} not found"}

    step_stats = []
    for step in funnel.get("steps", []):
        stats = get_step_stats(funnel_id, step["id"])
        stats["name"] = step["name"]
        stats["step_type"] = step["step_type"]
        stats["step_order"] = step["step_order"]
        stats["expected_conv_rate"] = step["expected_conv_rate"]
        stats["conv_rate_vs_expected"] = round(
            stats["conversion_rate_pct"] - step["expected_conv_rate"] * 100, 2
        )
        step_stats.append(stats)

    total_revenue = funnel["total_revenue"]
    total_visitors = funnel["total_visitors"]

    return {
        "funnel_id": funnel_id,
        "name": funnel["name"],
        "funnel_type": funnel["funnel_type"],
        "total_visitors": total_visitors,
        "total_revenue": round(total_revenue, 4),
        "revenue_per_visitor": round(total_revenue / total_visitors, 4) if total_visitors > 0 else 0.0,
        "steps": step_stats,
        "step_count": len(step_stats),
    }


def drop_off_analysis(funnel_id: int) -> list[dict]:
    """Find where visitors are dropping out of the funnel."""
    overview = funnel_overview(funnel_id)
    steps = overview.get("steps", [])
    result = []
    for i, step in enumerate(steps):
        prev_visitors = steps[i - 1]["visitors"] if i > 0 else step["visitors"]
        curr_visitors = step["visitors"]
        if prev_visitors > 0:
            drop_rate = round((prev_visitors - curr_visitors) / prev_visitors * 100, 2)
        else:
            drop_rate = 0.0
        result.append({
            "step_order": step["step_order"],
            "step_name": step["name"],
            "visitors": curr_visitors,
            "prev_visitors": prev_visitors,
            "drop_rate_pct": max(0.0, drop_rate),
            "conversion_rate_pct": step["conversion_rate_pct"],
            "revenue": step["revenue"],
        })
    # Sort by worst drop-off
    return sorted(result, key=lambda x: -x["drop_rate_pct"])


def best_performing_funnels(days: int = 30, limit: int = 10) -> list[dict]:
    """Rank funnels by revenue for the last N days."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT f.id, f.name, f.funnel_type, f.total_visitors,
                  COALESCE(SUM(fc.revenue),0) as period_revenue,
                  COUNT(fc.id) as period_conversions
           FROM funnels f
           LEFT JOIN funnel_conversions fc ON fc.funnel_id=f.id AND fc.converted_at>=?
           GROUP BY f.id
           ORDER BY period_revenue DESC
           LIMIT ?""",
        (since, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def funnel_revenue_by_source(funnel_id: int, days: int = 30) -> list[dict]:
    """Revenue breakdown by traffic source for a funnel."""
    since = time.time() - days * 86400
    rows = _db().execute(
        """SELECT v.source,
                  COUNT(DISTINCT v.session_id) as visitors,
                  COUNT(c.id) as conversions,
                  COALESCE(SUM(c.revenue),0) as revenue
           FROM funnel_visitors v
           LEFT JOIN funnel_conversions c ON c.session_id=v.session_id AND c.funnel_id=v.funnel_id
           WHERE v.funnel_id=? AND v.entered_at>=?
           GROUP BY v.source
           ORDER BY revenue DESC""",
        (funnel_id, since),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Built-in funnel templates
# ---------------------------------------------------------------------------

FUNNEL_TEMPLATES = {
    "lead_gen_basic": {
        "name": "Basic Lead Gen Funnel",
        "funnel_type": "lead_gen",
        "steps": [
            {"name": "Landing Page", "step_type": "optin",    "headline": "Get Your Free Guide", "price": 0,   "cta_text": "Download Now"},
            {"name": "Thank You",    "step_type": "thankyou", "headline": "Check Your Email!",    "price": 0,   "cta_text": ""},
            {"name": "Tripwire",     "step_type": "sales",    "headline": "Special One-Time Offer","price": 7,  "cta_text": "Get Instant Access"},
            {"name": "Upsell",       "step_type": "upsell",   "headline": "Upgrade Your Order",   "price": 27, "cta_text": "Yes, Add This"},
        ],
    },
    "webinar_funnel": {
        "name": "Webinar Registration Funnel",
        "funnel_type": "webinar",
        "steps": [
            {"name": "Registration", "step_type": "optin",   "headline": "Join the Free Webinar",   "price": 0,    "cta_text": "Register Free"},
            {"name": "Confirmation", "step_type": "content",  "headline": "You're Registered!",      "price": 0,    "cta_text": ""},
            {"name": "Webinar",      "step_type": "webinar",  "headline": "Live Training",            "price": 0,    "cta_text": ""},
            {"name": "Sales Page",   "step_type": "sales",    "headline": "Get the Full Program",    "price": 997,  "cta_text": "Enroll Now"},
            {"name": "Order Form",   "step_type": "checkout", "headline": "Complete Your Enrollment", "price": 997,  "cta_text": ""},
        ],
    },
    "tripwire_funnel": {
        "name": "Tripwire Product Funnel",
        "funnel_type": "tripwire",
        "steps": [
            {"name": "Lead Magnet",  "step_type": "optin",    "headline": "Free Resource",      "price": 0,   "cta_text": "Get Free Access"},
            {"name": "Tripwire",     "step_type": "sales",    "headline": "One-Time Offer $7",  "price": 7,   "cta_text": "Get It Now"},
            {"name": "Upsell 1",     "step_type": "upsell",   "headline": "Complete Package",   "price": 47,  "cta_text": "Yes, Upgrade Me"},
            {"name": "Upsell 2",     "step_type": "upsell",   "headline": "Done-For-You Setup", "price": 197, "cta_text": "Add to My Order"},
            {"name": "Downsell",     "step_type": "downsell", "headline": "Special Discount",   "price": 27,  "cta_text": "Yes, I'll Take It"},
            {"name": "Thank You",    "step_type": "thankyou", "headline": "Welcome Aboard!",    "price": 0,   "cta_text": ""},
        ],
    },
}


def create_from_template(template_name: str, funnel_name: str = "") -> int:
    """Create a funnel from a built-in template."""
    template = FUNNEL_TEMPLATES.get(template_name)
    if template is None:
        return 0
    name = funnel_name or template["name"]
    funnel_id = create_funnel(name, funnel_type=template["funnel_type"])
    for i, step_def in enumerate(template["steps"]):
        add_step(
            funnel_id,
            name=step_def["name"],
            step_type=step_def["step_type"],
            step_order=i + 1,
            headline=step_def.get("headline", ""),
            cta_text=step_def.get("cta_text", ""),
            price=step_def.get("price", 0.0),
        )
    return funnel_id


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def create_funnel_tool(
    name: str = "",
    description: str = "",
    funnel_type: str = "generic",
    goal_revenue: float = 0.0,
) -> str:
    if not name:
        return "error: name required"
    fid = create_funnel(name, description, funnel_type, goal_revenue)
    return f"funnel '{name}' created (id={fid}, type={funnel_type})"


def add_step_tool(
    funnel_id: int = 0,
    name: str = "",
    step_type: str = "content",
    price: float = 0.0,
    cta_text: str = "",
) -> str:
    if not funnel_id or not name:
        return "error: funnel_id and name required"
    sid = add_step(funnel_id, name, step_type, price=price, cta_text=cta_text)
    return f"step '{name}' added to funnel {funnel_id} (id={sid})"


def funnel_stats_tool(funnel_id: int = 0) -> str:
    if not funnel_id:
        return "error: funnel_id required"
    overview = funnel_overview(funnel_id)
    if "error" in overview:
        return overview["error"]
    return json.dumps({
        "name": overview["name"],
        "total_visitors": overview["total_visitors"],
        "total_revenue": overview["total_revenue"],
        "step_count": overview["step_count"],
        "top_steps": sorted(overview["steps"], key=lambda x: -x["revenue"])[:3],
    }, indent=2)


def list_funnels_tool(active_only: bool = True) -> str:
    funnels = list_funnels(active_only)
    if not funnels:
        return "no funnels found"
    return json.dumps([{
        "id": f["id"], "name": f["name"], "type": f["funnel_type"],
        "visitors": f["total_visitors"], "revenue": round(f["total_revenue"], 2),
    } for f in funnels], indent=2)


def best_funnels_tool(days: int = 30, limit: int = 5) -> str:
    funnels = best_performing_funnels(days, limit)
    if not funnels:
        return "no funnel revenue data"
    return json.dumps(funnels, indent=2)


def drop_off_tool(funnel_id: int = 0) -> str:
    if not funnel_id:
        return "error: funnel_id required"
    analysis = drop_off_analysis(funnel_id)
    if not analysis:
        return "no funnel data"
    return json.dumps(analysis[:5], indent=2)


def template_tool(template_name: str = "", funnel_name: str = "") -> str:
    templates = list(FUNNEL_TEMPLATES.keys())
    if not template_name:
        return f"available templates: {', '.join(templates)}"
    fid = create_from_template(template_name, funnel_name)
    if not fid:
        return f"error: template '{template_name}' not found. Available: {', '.join(templates)}"
    return f"funnel created from template '{template_name}' (id={fid})"
