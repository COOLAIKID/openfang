"""
Link Builder — UTM link generator, short-link manager, and link-in-bio manager.

Generates tracked URLs for campaigns, manages a short-link registry, and
builds link-in-bio pages for social profiles. All stored in SQLite.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs, urljoin

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# UTM sources / mediums / presets
# ---------------------------------------------------------------------------

UTM_SOURCES = [
    "google", "facebook", "instagram", "twitter", "linkedin", "youtube",
    "tiktok", "pinterest", "email", "newsletter", "reddit", "telegram",
    "discord", "quora", "medium", "direct", "referral", "organic", "other",
]

UTM_MEDIUMS = [
    "cpc", "cpm", "email", "social", "organic", "referral",
    "affiliate", "display", "video", "push", "sms", "qr", "banner", "other",
]

LINK_STATUSES = ["active", "paused", "expired", "archived"]

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
            CREATE TABLE IF NOT EXISTS tracked_links (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                slug          TEXT NOT NULL UNIQUE,
                destination   TEXT NOT NULL,
                title         TEXT,
                campaign_name TEXT,
                utm_source    TEXT,
                utm_medium    TEXT,
                utm_campaign  TEXT,
                utm_term      TEXT,
                utm_content   TEXT,
                custom_params TEXT DEFAULT '{}',
                tags          TEXT DEFAULT '[]',
                status        TEXT NOT NULL DEFAULT 'active',
                expires_at    TEXT,
                click_count   INTEGER NOT NULL DEFAULT 0,
                unique_clicks INTEGER NOT NULL DEFAULT 0,
                last_clicked  TEXT,
                created_at    TEXT NOT NULL,
                metadata      TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS link_clicks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id    INTEGER NOT NULL REFERENCES tracked_links(id) ON DELETE CASCADE,
                ip_hash    TEXT,
                user_agent TEXT,
                referrer   TEXT,
                country    TEXT,
                device     TEXT DEFAULT 'unknown',
                clicked_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS short_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                short_code  TEXT NOT NULL UNIQUE,
                long_url    TEXT NOT NULL,
                title       TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1,
                click_count INTEGER NOT NULL DEFAULT 0,
                expires_at  TEXT,
                created_at  TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS link_in_bio_pages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                handle      TEXT NOT NULL UNIQUE,
                title       TEXT,
                bio         TEXT,
                avatar_url  TEXT,
                theme       TEXT NOT NULL DEFAULT 'dark',
                accent_color TEXT NOT NULL DEFAULT '#58a6ff',
                custom_css  TEXT,
                is_active   INTEGER NOT NULL DEFAULT 1,
                view_count  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS link_in_bio_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                page_id     INTEGER NOT NULL REFERENCES link_in_bio_pages(id) ON DELETE CASCADE,
                title       TEXT NOT NULL,
                url         TEXT NOT NULL,
                description TEXT,
                icon        TEXT,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                is_active   INTEGER NOT NULL DEFAULT 1,
                click_count INTEGER NOT NULL DEFAULT 0,
                highlight   INTEGER NOT NULL DEFAULT 0,
                metadata    TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS campaign_groups (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                description TEXT,
                budget_usd  REAL NOT NULL DEFAULT 0.0,
                start_date  TEXT,
                end_date    TEXT,
                status      TEXT NOT NULL DEFAULT 'active',
                created_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tracked_links_slug     ON tracked_links(slug);
            CREATE INDEX IF NOT EXISTS idx_tracked_links_campaign ON tracked_links(utm_campaign);
            CREATE INDEX IF NOT EXISTS idx_link_clicks_link       ON link_clicks(link_id);
            CREATE INDEX IF NOT EXISTS idx_link_clicks_at         ON link_clicks(clicked_at);
            CREATE INDEX IF NOT EXISTS idx_short_links_code       ON short_links(short_code);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TrackedLink:
    destination: str
    slug: str = ""
    title: str = ""
    campaign_name: str = ""
    utm_source: str = ""
    utm_medium: str = ""
    utm_campaign: str = ""
    utm_term: str = ""
    utm_content: str = ""
    custom_params: Dict[str, str] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    status: str = "active"
    expires_at: Optional[str] = None
    click_count: int = 0
    unique_clicks: int = 0
    last_clicked: Optional[str] = None
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    @property
    def full_url(self) -> str:
        """Build the final URL with UTM parameters appended."""
        params: Dict[str, str] = {}
        if self.utm_source:
            params["utm_source"] = self.utm_source
        if self.utm_medium:
            params["utm_medium"] = self.utm_medium
        if self.utm_campaign:
            params["utm_campaign"] = self.utm_campaign
        if self.utm_term:
            params["utm_term"] = self.utm_term
        if self.utm_content:
            params["utm_content"] = self.utm_content
        params.update(self.custom_params)

        parsed = urlparse(self.destination)
        existing = parse_qs(parsed.query, keep_blank_values=True)
        for k, v in params.items():
            existing[k] = [v]
        flat = {k: v[0] for k, v in existing.items()}
        new_query = urlencode(flat)
        return urlunparse(parsed._replace(query=new_query))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "destination": self.destination,
            "full_url": self.full_url,
            "title": self.title,
            "campaign_name": self.campaign_name,
            "utm_source": self.utm_source,
            "utm_medium": self.utm_medium,
            "utm_campaign": self.utm_campaign,
            "utm_term": self.utm_term,
            "utm_content": self.utm_content,
            "custom_params": self.custom_params,
            "tags": self.tags,
            "status": self.status,
            "expires_at": self.expires_at,
            "click_count": self.click_count,
            "unique_clicks": self.unique_clicks,
            "last_clicked": self.last_clicked,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class ShortLink:
    long_url: str
    short_code: str = ""
    title: str = ""
    is_active: bool = True
    click_count: int = 0
    expires_at: Optional[str] = None
    created_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "short_code": self.short_code,
            "long_url": self.long_url,
            "title": self.title,
            "is_active": self.is_active,
            "click_count": self.click_count,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


@dataclass
class LinkInBioPage:
    handle: str
    title: str = ""
    bio: str = ""
    avatar_url: str = ""
    theme: str = "dark"
    accent_color: str = "#58a6ff"
    custom_css: str = ""
    is_active: bool = True
    view_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None
    items: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "handle": self.handle,
            "title": self.title,
            "bio": self.bio,
            "avatar_url": self.avatar_url,
            "theme": self.theme,
            "accent_color": self.accent_color,
            "is_active": self.is_active,
            "view_count": self.view_count,
            "item_count": len(self.items),
            "items": self.items,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------

def _link_from_row(row: sqlite3.Row) -> TrackedLink:
    return TrackedLink(
        id=row["id"],
        slug=row["slug"],
        destination=row["destination"],
        title=row["title"] or "",
        campaign_name=row["campaign_name"] or "",
        utm_source=row["utm_source"] or "",
        utm_medium=row["utm_medium"] or "",
        utm_campaign=row["utm_campaign"] or "",
        utm_term=row["utm_term"] or "",
        utm_content=row["utm_content"] or "",
        custom_params=json.loads(row["custom_params"] or "{}"),
        tags=json.loads(row["tags"] or "[]"),
        status=row["status"],
        expires_at=row["expires_at"],
        click_count=row["click_count"] or 0,
        unique_clicks=row["unique_clicks"] or 0,
        last_clicked=row["last_clicked"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


def _short_from_row(row: sqlite3.Row) -> ShortLink:
    return ShortLink(
        id=row["id"],
        short_code=row["short_code"],
        long_url=row["long_url"],
        title=row["title"] or "",
        is_active=bool(row["is_active"]),
        click_count=row["click_count"] or 0,
        expires_at=row["expires_at"],
        created_at=row["created_at"],
        metadata=json.loads(row["metadata"] or "{}"),
    )


# ---------------------------------------------------------------------------
# Slug / code generation
# ---------------------------------------------------------------------------

def _gen_slug(length: int = 8) -> str:
    return uuid.uuid4().hex[:length]


def _gen_short_code(url: str) -> str:
    digest = hashlib.sha256(url.encode()).hexdigest()[:6]
    return digest


# ---------------------------------------------------------------------------
# Tracked links CRUD
# ---------------------------------------------------------------------------

def create_link(
    destination: str,
    title: str = "",
    campaign_name: str = "",
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    utm_term: str = "",
    utm_content: str = "",
    custom_params: Optional[Dict[str, str]] = None,
    tags: Optional[List[str]] = None,
    expires_at: Optional[str] = None,
    slug: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> TrackedLink:
    """Create a new tracked UTM link."""
    _ensure()
    now = datetime.utcnow().isoformat()
    final_slug = slug or _gen_slug()

    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO tracked_links
               (slug, destination, title, campaign_name, utm_source, utm_medium,
                utm_campaign, utm_term, utm_content, custom_params, tags,
                expires_at, created_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                final_slug, destination, title, campaign_name, utm_source, utm_medium,
                utm_campaign, utm_term, utm_content,
                json.dumps(custom_params or {}), json.dumps(tags or []),
                expires_at, now, json.dumps(metadata or {}),
            ),
        )
        conn.commit()
        lnk = TrackedLink(
            id=cur.lastrowid,
            slug=final_slug,
            destination=destination,
            title=title,
            campaign_name=campaign_name,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            custom_params=custom_params or {},
            tags=tags or [],
            status="active",
            expires_at=expires_at,
            created_at=now,
            metadata=metadata or {},
        )
        return lnk
    except sqlite3.IntegrityError:
        conn.close()
        return create_link(
            destination=destination, title=title, campaign_name=campaign_name,
            utm_source=utm_source, utm_medium=utm_medium, utm_campaign=utm_campaign,
            utm_term=utm_term, utm_content=utm_content, custom_params=custom_params,
            tags=tags, expires_at=expires_at, metadata=metadata,
        )
    finally:
        conn.close()


def get_link(link_id: int) -> Optional[TrackedLink]:
    """Fetch a tracked link by ID."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM tracked_links WHERE id = ?", (link_id,)).fetchone()
        return _link_from_row(row) if row else None
    finally:
        conn.close()


def get_link_by_slug(slug: str) -> Optional[TrackedLink]:
    """Fetch a tracked link by slug."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute("SELECT * FROM tracked_links WHERE slug = ?", (slug,)).fetchone()
        return _link_from_row(row) if row else None
    finally:
        conn.close()


def list_links(
    campaign_name: Optional[str] = None,
    utm_source: Optional[str] = None,
    status: str = "active",
    tag: Optional[str] = None,
    limit: int = 100,
) -> List[TrackedLink]:
    """List tracked links with optional filters."""
    _ensure()
    clauses = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if campaign_name:
        clauses.append("campaign_name = ?")
        params.append(campaign_name)
    if utm_source:
        clauses.append("utm_source = ?")
        params.append(utm_source)
    if tag:
        clauses.append("tags LIKE ?")
        params.append(f"%{tag}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM tracked_links {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [_link_from_row(r) for r in rows]
    finally:
        conn.close()


def record_click(
    slug: str,
    ip: str = "",
    user_agent: str = "",
    referrer: str = "",
    country: str = "",
    device: str = "unknown",
) -> Optional[str]:
    """Record a click and return the destination URL (or None if invalid/expired)."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM tracked_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return None
        if row["status"] != "active":
            return None
        if row["expires_at"] and row["expires_at"] < datetime.utcnow().isoformat():
            conn.execute("UPDATE tracked_links SET status = 'expired' WHERE slug = ?", (slug,))
            conn.commit()
            return None

        ip_hash = hashlib.sha256(ip.encode()).hexdigest()[:16] if ip else ""
        now = datetime.utcnow().isoformat()

        # Check if this IP already clicked (for unique count)
        existing = conn.execute(
            "SELECT id FROM link_clicks WHERE link_id = ? AND ip_hash = ?",
            (row["id"], ip_hash),
        ).fetchone()
        is_unique = existing is None

        conn.execute(
            """INSERT INTO link_clicks (link_id, ip_hash, user_agent, referrer, country, device, clicked_at)
               VALUES (?,?,?,?,?,?,?)""",
            (row["id"], ip_hash, user_agent[:500], referrer[:1000], country, device, now),
        )
        conn.execute(
            """UPDATE tracked_links
               SET click_count = click_count + 1,
                   unique_clicks = unique_clicks + ?,
                   last_clicked = ?
               WHERE slug = ?""",
            (1 if is_unique else 0, now, slug),
        )
        conn.commit()
        lnk = _link_from_row(row)
        return lnk.full_url
    finally:
        conn.close()


def update_link_status(slug: str, status: str) -> bool:
    """Set a link's status (active/paused/archived)."""
    _ensure()
    conn = _db()
    try:
        conn.execute("UPDATE tracked_links SET status = ? WHERE slug = ?", (status, slug))
        conn.commit()
        return True
    finally:
        conn.close()


def delete_link(link_id: int) -> bool:
    """Hard delete a tracked link."""
    _ensure()
    conn = _db()
    try:
        conn.execute("DELETE FROM tracked_links WHERE id = ?", (link_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Short links
# ---------------------------------------------------------------------------

def create_short_link(
    long_url: str,
    short_code: Optional[str] = None,
    title: str = "",
    expires_at: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> ShortLink:
    """Create a short link."""
    _ensure()
    now = datetime.utcnow().isoformat()
    code = short_code or _gen_short_code(long_url + now)

    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO short_links (short_code, long_url, title, expires_at, created_at, metadata)
               VALUES (?,?,?,?,?,?)""",
            (code, long_url, title, expires_at, now, json.dumps(metadata or {})),
        )
        conn.commit()
        return ShortLink(
            id=cur.lastrowid,
            short_code=code,
            long_url=long_url,
            title=title,
            is_active=True,
            expires_at=expires_at,
            created_at=now,
            metadata=metadata or {},
        )
    except sqlite3.IntegrityError:
        code = _gen_slug(8)
        return create_short_link(long_url=long_url, short_code=code, title=title,
                                 expires_at=expires_at, metadata=metadata)
    finally:
        conn.close()


def resolve_short_link(code: str) -> Optional[str]:
    """Resolve a short code to its long URL and increment clicks."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM short_links WHERE short_code = ? AND is_active = 1", (code,)
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < datetime.utcnow().isoformat():
            conn.execute("UPDATE short_links SET is_active = 0 WHERE short_code = ?", (code,))
            conn.commit()
            return None
        conn.execute(
            "UPDATE short_links SET click_count = click_count + 1 WHERE short_code = ?",
            (code,),
        )
        conn.commit()
        return row["long_url"]
    finally:
        conn.close()


def list_short_links(limit: int = 100) -> List[ShortLink]:
    """List all short links ordered by creation date."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM short_links ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_short_from_row(r) for r in rows]
    finally:
        conn.close()


def deactivate_short_link(code: str) -> bool:
    """Deactivate a short link so it no longer resolves."""
    _ensure()
    conn = _db()
    try:
        conn.execute("UPDATE short_links SET is_active = 0 WHERE short_code = ?", (code,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Link-in-bio pages
# ---------------------------------------------------------------------------

def create_bio_page(
    handle: str,
    title: str = "",
    bio: str = "",
    avatar_url: str = "",
    theme: str = "dark",
    accent_color: str = "#58a6ff",
    metadata: Optional[Dict[str, Any]] = None,
) -> LinkInBioPage:
    """Create a link-in-bio page."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO link_in_bio_pages
               (handle, title, bio, avatar_url, theme, accent_color, created_at, updated_at, metadata)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (handle, title, bio, avatar_url, theme, accent_color, now, now,
             json.dumps(metadata or {})),
        )
        conn.commit()
        return LinkInBioPage(
            id=cur.lastrowid,
            handle=handle,
            title=title,
            bio=bio,
            avatar_url=avatar_url,
            theme=theme,
            accent_color=accent_color,
            created_at=now,
            updated_at=now,
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Handle '{handle}' is already taken")
    finally:
        conn.close()


def get_bio_page(handle: str) -> Optional[LinkInBioPage]:
    """Fetch a link-in-bio page with all items."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM link_in_bio_pages WHERE handle = ?", (handle,)
        ).fetchone()
        if not row:
            return None
        item_rows = conn.execute(
            """SELECT * FROM link_in_bio_items WHERE page_id = ? AND is_active = 1
               ORDER BY sort_order""",
            (row["id"],),
        ).fetchall()
        page = LinkInBioPage(
            id=row["id"],
            handle=row["handle"],
            title=row["title"] or "",
            bio=row["bio"] or "",
            avatar_url=row["avatar_url"] or "",
            theme=row["theme"],
            accent_color=row["accent_color"],
            is_active=bool(row["is_active"]),
            view_count=row["view_count"] or 0,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"] or "{}"),
            items=[dict(r) for r in item_rows],
        )
        return page
    finally:
        conn.close()


def add_bio_link(
    handle: str,
    title: str,
    url: str,
    description: str = "",
    icon: str = "",
    sort_order: int = 0,
    highlight: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Add a link to a bio page."""
    _ensure()
    conn = _db()
    try:
        page_row = conn.execute(
            "SELECT id FROM link_in_bio_pages WHERE handle = ?", (handle,)
        ).fetchone()
        if not page_row:
            raise ValueError(f"Bio page '{handle}' not found")
        cur = conn.execute(
            """INSERT INTO link_in_bio_items
               (page_id, title, url, description, icon, sort_order, highlight, metadata)
               VALUES (?,?,?,?,?,?,?,?)""",
            (page_row["id"], title, url, description, icon, sort_order,
             int(highlight), json.dumps(metadata or {})),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "handle": handle,
            "title": title,
            "url": url,
            "description": description,
            "icon": icon,
            "sort_order": sort_order,
            "highlight": highlight,
        }
    finally:
        conn.close()


def record_bio_view(handle: str) -> bool:
    """Increment view count for a bio page."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE link_in_bio_pages SET view_count = view_count + 1 WHERE handle = ?",
            (handle,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def record_bio_item_click(item_id: int) -> bool:
    """Increment click count on a bio page link item."""
    _ensure()
    conn = _db()
    try:
        conn.execute(
            "UPDATE link_in_bio_items SET click_count = click_count + 1 WHERE id = ?",
            (item_id,),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def update_bio_page(handle: str, **kwargs) -> bool:
    """Update bio page fields."""
    _ensure()
    allowed = {"title", "bio", "avatar_url", "theme", "accent_color", "custom_css", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _db()
    try:
        conn.execute(
            f"UPDATE link_in_bio_pages SET {set_clause} WHERE handle = ?",
            (*updates.values(), handle),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_bio_pages(active_only: bool = True) -> List[Dict[str, Any]]:
    """List all bio pages."""
    _ensure()
    where = "WHERE is_active = 1" if active_only else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM link_in_bio_pages {where} ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Campaign management
# ---------------------------------------------------------------------------

def create_campaign_group(
    name: str,
    description: str = "",
    budget_usd: float = 0.0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a campaign group to organize related links."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO campaign_groups (name, description, budget_usd, start_date, end_date, created_at)
               VALUES (?,?,?,?,?,?)""",
            (name, description, budget_usd, start_date, end_date, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "name": name,
            "description": description,
            "budget_usd": budget_usd,
            "start_date": start_date,
            "end_date": end_date,
        }
    except sqlite3.IntegrityError:
        raise ValueError(f"Campaign '{name}' already exists")
    finally:
        conn.close()


def campaign_performance(campaign_name: str) -> Dict[str, Any]:
    """Aggregate click stats for all links in a campaign."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT slug, title, utm_source, utm_medium, utm_content,
                      click_count, unique_clicks, last_clicked, status
               FROM tracked_links
               WHERE utm_campaign = ?
               ORDER BY click_count DESC""",
            (campaign_name,),
        ).fetchall()
        total_clicks = sum(r["click_count"] or 0 for r in rows)
        total_unique = sum(r["unique_clicks"] or 0 for r in rows)
        return {
            "campaign": campaign_name,
            "link_count": len(rows),
            "total_clicks": total_clicks,
            "total_unique_clicks": total_unique,
            "links": [dict(r) for r in rows],
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------

def link_click_history(
    slug: str,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """Daily click counts for a specific link over the last N days."""
    _ensure()
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    conn = _db()
    try:
        link_row = conn.execute(
            "SELECT id FROM tracked_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not link_row:
            return []
        rows = conn.execute(
            """SELECT DATE(clicked_at) as day, COUNT(*) as clicks
               FROM link_clicks
               WHERE link_id = ? AND clicked_at >= ?
               GROUP BY DATE(clicked_at)
               ORDER BY day""",
            (link_row["id"], since),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def top_links(
    limit: int = 20,
    since: Optional[str] = None,
    by: str = "clicks",
) -> List[Dict[str, Any]]:
    """Return the highest-performing tracked links."""
    _ensure()
    since = since or (datetime.utcnow() - timedelta(days=30)).isoformat()
    order = "click_count DESC" if by == "clicks" else "unique_clicks DESC"
    conn = _db()
    try:
        rows = conn.execute(
            f"""SELECT id, slug, title, utm_campaign, utm_source, click_count, unique_clicks,
                       last_clicked, status
                FROM tracked_links
                WHERE created_at >= ?
                ORDER BY {order}
                LIMIT ?""",
            (since, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def device_breakdown(slug: str) -> List[Dict[str, Any]]:
    """Click count by device type for a link."""
    _ensure()
    conn = _db()
    try:
        link_row = conn.execute(
            "SELECT id FROM tracked_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not link_row:
            return []
        rows = conn.execute(
            """SELECT device, COUNT(*) as clicks
               FROM link_clicks WHERE link_id = ?
               GROUP BY device ORDER BY clicks DESC""",
            (link_row["id"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def geo_breakdown(slug: str) -> List[Dict[str, Any]]:
    """Click count by country for a link."""
    _ensure()
    conn = _db()
    try:
        link_row = conn.execute(
            "SELECT id FROM tracked_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not link_row:
            return []
        rows = conn.execute(
            """SELECT country, COUNT(*) as clicks
               FROM link_clicks WHERE link_id = ? AND country != ''
               GROUP BY country ORDER BY clicks DESC LIMIT 30""",
            (link_row["id"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def referrer_breakdown(slug: str) -> List[Dict[str, Any]]:
    """Top referrers sending traffic to a link."""
    _ensure()
    conn = _db()
    try:
        link_row = conn.execute(
            "SELECT id FROM tracked_links WHERE slug = ?", (slug,)
        ).fetchone()
        if not link_row:
            return []
        rows = conn.execute(
            """SELECT referrer, COUNT(*) as clicks
               FROM link_clicks WHERE link_id = ? AND referrer != ''
               GROUP BY referrer ORDER BY clicks DESC LIMIT 20""",
            (link_row["id"],),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def link_summary() -> Dict[str, Any]:
    """Overall link manager summary."""
    _ensure()
    conn = _db()
    try:
        link_row = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(click_count) as total_clicks,
                      SUM(unique_clicks) as total_unique,
                      SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                      SUM(CASE WHEN status = 'expired' THEN 1 ELSE 0 END) as expired
               FROM tracked_links"""
        ).fetchone()
        short_row = conn.execute(
            """SELECT COUNT(*) as total, SUM(click_count) as total_clicks
               FROM short_links"""
        ).fetchone()
        bio_row = conn.execute(
            """SELECT COUNT(*) as pages, SUM(view_count) as total_views
               FROM link_in_bio_pages"""
        ).fetchone()
        return {
            "tracked_links": {
                "total": link_row["total"] or 0,
                "active": link_row["active"] or 0,
                "expired": link_row["expired"] or 0,
                "total_clicks": link_row["total_clicks"] or 0,
                "unique_clicks": link_row["total_unique"] or 0,
            },
            "short_links": {
                "total": short_row["total"] or 0,
                "total_clicks": short_row["total_clicks"] or 0,
            },
            "link_in_bio": {
                "pages": bio_row["pages"] or 0,
                "total_views": bio_row["total_views"] or 0,
            },
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Bulk link generation
# ---------------------------------------------------------------------------

def bulk_create_links(
    destination: str,
    sources: List[str],
    medium: str = "social",
    campaign: str = "",
    title_prefix: str = "",
) -> List[TrackedLink]:
    """Create one tracked link per source, all pointing at the same destination."""
    links = []
    for source in sources:
        lnk = create_link(
            destination=destination,
            title=f"{title_prefix} - {source}" if title_prefix else source,
            utm_source=source,
            utm_medium=medium,
            utm_campaign=campaign,
        )
        links.append(lnk)
    return links


def generate_utm_url(
    base_url: str,
    utm_source: str,
    utm_medium: str,
    utm_campaign: str,
    utm_term: str = "",
    utm_content: str = "",
) -> str:
    """Build a plain UTM URL without creating a database record."""
    params: Dict[str, str] = {
        "utm_source": utm_source,
        "utm_medium": utm_medium,
        "utm_campaign": utm_campaign,
    }
    if utm_term:
        params["utm_term"] = utm_term
    if utm_content:
        params["utm_content"] = utm_content

    parsed = urlparse(base_url)
    existing = parse_qs(parsed.query, keep_blank_values=True)
    for k, v in params.items():
        existing[k] = [v]
    flat = {k: v[0] for k, v in existing.items()}
    new_query = urlencode(flat)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("lb_create_link", "Create a new UTM tracked link")
def create_link_tool(
    destination: str,
    utm_source: str = "",
    utm_medium: str = "",
    utm_campaign: str = "",
    title: str = "",
) -> str:
    try:
        lnk = create_link(
            destination=destination,
            title=title,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
        )
        return json.dumps({"ok": True, "link": lnk.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("lb_campaign_performance", "Get click stats for all links in a campaign")
def campaign_performance_tool(campaign_name: str) -> str:
    try:
        return json.dumps(campaign_performance(campaign_name), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("lb_top_links", "List highest-performing tracked links")
def top_links_tool(limit: int = 20, since: str = "") -> str:
    try:
        return json.dumps(top_links(limit=limit, since=since or None), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("lb_create_short_link", "Create a short link for a long URL")
def create_short_link_tool(long_url: str, title: str = "") -> str:
    try:
        sl = create_short_link(long_url=long_url, title=title)
        return json.dumps({"ok": True, "short_link": sl.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("lb_create_bio_page", "Create a link-in-bio page")
def create_bio_page_tool(handle: str, title: str = "", bio: str = "") -> str:
    try:
        page = create_bio_page(handle=handle, title=title, bio=bio)
        return json.dumps({"ok": True, "page": page.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("lb_add_bio_link", "Add a link to an existing link-in-bio page")
def add_bio_link_tool(handle: str, title: str, url: str, description: str = "") -> str:
    try:
        item = add_bio_link(handle=handle, title=title, url=url, description=description)
        return json.dumps({"ok": True, "item": item})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("lb_link_summary", "Overall link manager summary stats")
def link_summary_tool() -> str:
    try:
        return json.dumps(link_summary(), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("lb_generate_utm", "Build a UTM URL without tracking (no database record)")
def generate_utm_tool(
    base_url: str,
    utm_source: str,
    utm_medium: str,
    utm_campaign: str,
    utm_content: str = "",
) -> str:
    try:
        url = generate_utm_url(
            base_url=base_url,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_content=utm_content,
        )
        return json.dumps({"ok": True, "url": url})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("lb_bulk_create_links", "Create one tracked link per UTM source for a destination")
def bulk_create_links_tool(
    destination: str,
    sources: str,
    medium: str = "social",
    campaign: str = "",
) -> str:
    try:
        source_list = [s.strip() for s in sources.split(",") if s.strip()]
        links = bulk_create_links(
            destination=destination,
            sources=source_list,
            medium=medium,
            campaign=campaign,
        )
        return json.dumps({
            "ok": True,
            "count": len(links),
            "links": [lnk.to_dict() for lnk in links],
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
