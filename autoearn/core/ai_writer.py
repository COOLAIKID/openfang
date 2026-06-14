"""AI-assisted content generation and writing management.

Provides a full writing workflow backed by SQLite: templates, writing jobs,
content briefs, writing styles, brand voices, and publishing history. Includes
pure-Python readability scoring, SEO checks, and keyword extraction.

Workflow::

    brief → outline → job (draft) → review → approved → published

Agents interact via the registered @tool functions or call the core functions
directly. All tool functions return JSON strings and never raise.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTENT_TYPES = [
    "blog_post",
    "social_post",
    "email_subject",
    "email_body",
    "ad_copy",
    "product_description",
    "landing_page",
    "press_release",
    "video_script",
    "podcast_outline",
    "thread",
    "caption",
    "headline",
    "meta_description",
    "cta",
]

TONES = [
    "professional",
    "casual",
    "friendly",
    "authoritative",
    "humorous",
    "inspirational",
    "urgent",
    "empathetic",
    "technical",
    "conversational",
]

JOB_STATUSES = [
    "draft",
    "generating",
    "review",
    "approved",
    "published",
    "archived",
]

# Common English stop words for keyword extraction
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can", "need",
    "it", "its", "this", "that", "these", "those", "i", "me", "my", "we",
    "our", "you", "your", "he", "she", "they", "them", "their", "his",
    "her", "what", "which", "who", "how", "when", "where", "why", "all",
    "not", "no", "so", "if", "then", "than", "up", "out", "about", "into",
    "also", "just", "more", "some", "such", "very", "get", "got", "let",
    "like", "make", "made", "one", "two", "three", "any", "each", "both",
})

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class WritingJob:
    id: int
    title: str
    content_type: str
    template_name: str
    status: str
    tone: str
    target_audience: str
    keywords: list[str]
    platform: str
    word_count: int
    created_at: str

    @property
    def is_complete(self) -> bool:
        return self.status in ("approved", "published")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content_type": self.content_type,
            "template_name": self.template_name,
            "status": self.status,
            "tone": self.tone,
            "target_audience": self.target_audience,
            "keywords": self.keywords,
            "platform": self.platform,
            "word_count": self.word_count,
            "is_complete": self.is_complete,
            "created_at": self.created_at,
        }


@dataclass
class ContentBrief:
    id: int
    title: str
    content_type: str
    target_keyword: str
    secondary_keywords: list[str]
    target_audience: str
    goal: str
    word_count_target: int
    outline: str
    notes: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content_type": self.content_type,
            "target_keyword": self.target_keyword,
            "secondary_keywords": self.secondary_keywords,
            "target_audience": self.target_audience,
            "goal": self.goal,
            "word_count_target": self.word_count_target,
            "outline": self.outline,
            "notes": self.notes,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# Schema bootstrap
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
            CREATE TABLE IF NOT EXISTS writing_templates (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT NOT NULL UNIQUE,
                content_type TEXT NOT NULL DEFAULT '',
                template_body TEXT NOT NULL DEFAULT '',
                variables    TEXT NOT NULL DEFAULT '[]',
                description  TEXT NOT NULL DEFAULT '',
                usage_count  INT NOT NULL DEFAULT 0,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS writing_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                title           TEXT NOT NULL,
                content_type    TEXT NOT NULL DEFAULT '',
                template_name   TEXT NOT NULL DEFAULT '',
                status          TEXT NOT NULL DEFAULT 'draft',
                input_vars      TEXT NOT NULL DEFAULT '{}',
                output_draft    TEXT NOT NULL DEFAULT '',
                output_final    TEXT NOT NULL DEFAULT '',
                word_count      INT NOT NULL DEFAULT 0,
                tone            TEXT NOT NULL DEFAULT '',
                target_audience TEXT NOT NULL DEFAULT '',
                keywords        TEXT NOT NULL DEFAULT '[]',
                platform        TEXT NOT NULL DEFAULT '',
                notes           TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS writing_styles (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL UNIQUE,
                description       TEXT NOT NULL DEFAULT '',
                tone_words        TEXT NOT NULL DEFAULT '[]',
                avoid_words       TEXT NOT NULL DEFAULT '[]',
                example_sentences TEXT NOT NULL DEFAULT '',
                active            INT NOT NULL DEFAULT 1,
                created_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS content_briefs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                title               TEXT NOT NULL,
                content_type        TEXT NOT NULL DEFAULT '',
                target_keyword      TEXT NOT NULL DEFAULT '',
                secondary_keywords  TEXT NOT NULL DEFAULT '[]',
                target_audience     TEXT NOT NULL DEFAULT '',
                goal                TEXT NOT NULL DEFAULT '',
                word_count_target   INT NOT NULL DEFAULT 500,
                outline             TEXT NOT NULL DEFAULT '',
                notes               TEXT NOT NULL DEFAULT '',
                status              TEXT NOT NULL DEFAULT 'draft',
                created_at          TEXT NOT NULL,
                updated_at          TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS writing_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id     INT NOT NULL,
                version    INT NOT NULL DEFAULT 1,
                content    TEXT NOT NULL DEFAULT '',
                word_count INT NOT NULL DEFAULT 0,
                feedback   TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS brand_voices (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT NOT NULL UNIQUE,
                description        TEXT NOT NULL DEFAULT '',
                personality_traits TEXT NOT NULL DEFAULT '[]',
                writing_dos        TEXT NOT NULL DEFAULT '[]',
                writing_donts      TEXT NOT NULL DEFAULT '[]',
                sample_content     TEXT NOT NULL DEFAULT '',
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_wj_status       ON writing_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_wj_content_type ON writing_jobs(content_type);
            CREATE INDEX IF NOT EXISTS idx_wh_job_id       ON writing_history(job_id);
            CREATE INDEX IF NOT EXISTS idx_cb_status       ON content_briefs(status);
        """)
        conn.commit()
    finally:
        conn.close()

    _seed_templates()


# ---------------------------------------------------------------------------
# Seed built-in templates
# ---------------------------------------------------------------------------

_BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "name": "blog_post_standard",
        "content_type": "blog_post",
        "description": "Standard long-form blog post with intro, two sections, and CTA.",
        "variables": ["title", "intro", "section_1_title", "section_1_body",
                      "section_2_title", "section_2_body", "conclusion", "cta"],
        "template_body": (
            "# {title}\n\n"
            "## Introduction\n{intro}\n\n"
            "## {section_1_title}\n{section_1_body}\n\n"
            "## {section_2_title}\n{section_2_body}\n\n"
            "## Conclusion\n{conclusion}\n\n"
            "{cta}"
        ),
    },
    {
        "name": "social_twitter",
        "content_type": "social_post",
        "description": "Twitter/X thread starter with hook, three points, and CTA.",
        "variables": ["hook", "point_1", "point_2", "point_3", "cta", "link"],
        "template_body": (
            "{hook}\n\n"
            "{point_1}\n"
            "{point_2}\n"
            "{point_3}\n\n"
            "{cta} {link}"
        ),
    },
    {
        "name": "email_newsletter",
        "content_type": "email_body",
        "description": "Personalized newsletter email with main content block.",
        "variables": ["first_name", "intro", "main_content", "cta", "sender_name"],
        "template_body": (
            "Hi {first_name},\n\n"
            "{intro}\n\n"
            "{main_content}\n\n"
            "{cta}\n\n"
            "Best,\n"
            "{sender_name}"
        ),
    },
    {
        "name": "product_description",
        "content_type": "product_description",
        "description": "E-commerce product description with benefits and pricing.",
        "variables": ["product_name", "tagline", "description",
                      "benefit_1", "benefit_2", "benefit_3", "cta", "price"],
        "template_body": (
            "**{product_name}**\n\n"
            "{tagline}\n\n"
            "**What it does:**\n{description}\n\n"
            "**Key benefits:**\n"
            "• {benefit_1}\n"
            "• {benefit_2}\n"
            "• {benefit_3}\n\n"
            "**{cta}** — {price}"
        ),
    },
    {
        "name": "ad_headline",
        "content_type": "ad_copy",
        "description": "Short ad headline with hook, benefit, and CTA.",
        "variables": ["hook", "benefit", "cta"],
        "template_body": "{hook} | {benefit} | {cta}",
    },
    {
        "name": "meta_description",
        "content_type": "meta_description",
        "description": "SEO meta description with keyword, value prop, and brand.",
        "variables": ["primary_keyword", "value_prop", "cta", "brand_name"],
        "template_body": "{primary_keyword}: {value_prop}. {cta}. {brand_name}.",
    },
    {
        "name": "press_release",
        "content_type": "press_release",
        "description": "Standard press release format with boilerplate and contact.",
        "variables": ["headline", "city", "date", "company",
                      "announcement", "body", "about", "contact_info"],
        "template_body": (
            "FOR IMMEDIATE RELEASE\n\n"
            "{headline}\n\n"
            "{city}, {date} — {company} today announced {announcement}.\n\n"
            "{body}\n\n"
            "About {company}\n"
            "{about}\n\n"
            "Contact: {contact_info}"
        ),
    },
    {
        "name": "video_script_intro",
        "content_type": "video_script",
        "description": "Video script with hook, intro, main content, CTA, and outro.",
        "variables": ["hook", "intro", "content", "cta", "outro"],
        "template_body": (
            "[HOOK - first 5 seconds]\n{hook}\n\n"
            "[INTRO]\n{intro}\n\n"
            "[MAIN CONTENT]\n{content}\n\n"
            "[CTA]\n{cta}\n\n"
            "[OUTRO]\n{outro}"
        ),
    },
]


def _seed_templates() -> None:
    conn = _db()
    try:
        count = conn.execute("SELECT COUNT(*) FROM writing_templates").fetchone()[0]
        if count > 0:
            return
        now = _now()
        for t in _BUILTIN_TEMPLATES:
            conn.execute(
                """INSERT OR IGNORE INTO writing_templates
                   (name, content_type, template_body, variables, description,
                    usage_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    t["name"],
                    t["content_type"],
                    t["template_body"],
                    json.dumps(t["variables"]),
                    t["description"],
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _parse_json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            result = json.loads(value)
            return result if isinstance(result, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _parse_json_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            result = json.loads(value)
            return result if isinstance(result, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _row_to_job(row: sqlite3.Row) -> WritingJob:
    d = dict(row)
    return WritingJob(
        id=d["id"],
        title=d["title"],
        content_type=d["content_type"],
        template_name=d["template_name"],
        status=d["status"],
        tone=d["tone"],
        target_audience=d["target_audience"],
        keywords=_parse_json_list(d.get("keywords", "[]")),
        platform=d["platform"],
        word_count=d["word_count"],
        created_at=d["created_at"],
    )


def _row_to_brief(row: sqlite3.Row) -> ContentBrief:
    d = dict(row)
    return ContentBrief(
        id=d["id"],
        title=d["title"],
        content_type=d["content_type"],
        target_keyword=d["target_keyword"],
        secondary_keywords=_parse_json_list(d.get("secondary_keywords", "[]")),
        target_audience=d["target_audience"],
        goal=d["goal"],
        word_count_target=d["word_count_target"],
        outline=d["outline"],
        notes=d["notes"],
        status=d["status"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
    )


# ---------------------------------------------------------------------------
# Word count utility
# ---------------------------------------------------------------------------


def word_count(text: str) -> int:
    """Return the number of words in *text*."""
    if not text:
        return 0
    return len(text.split())


# ---------------------------------------------------------------------------
# Template functions
# ---------------------------------------------------------------------------


def create_template(
    name: str,
    content_type: str,
    template_body: str,
    variables_list: Optional[list[str]] = None,
    description: str = "",
) -> dict[str, Any]:
    """Create a new writing template and return it as a dict."""
    _ensure()
    now = _now()
    variables = json.dumps(variables_list or [])
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO writing_templates
               (name, content_type, template_body, variables, description,
                usage_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (name, content_type, template_body, variables, description, now, now),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM writing_templates WHERE name = ?", (name,)
        ).fetchone()
        d = _row_to_dict(row)
        d["variables"] = _parse_json_list(d.get("variables"))
        return d
    finally:
        conn.close()


def get_template(name: str) -> Optional[dict[str, Any]]:
    """Return a template dict or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM writing_templates WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["variables"] = _parse_json_list(d.get("variables"))
        return d
    finally:
        conn.close()


def list_templates(content_type: Optional[str] = None) -> list[dict[str, Any]]:
    """Return all templates, optionally filtered by *content_type*."""
    _ensure()
    conn = _db()
    try:
        if content_type:
            rows = conn.execute(
                "SELECT * FROM writing_templates WHERE content_type = ? ORDER BY name",
                (content_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM writing_templates ORDER BY name"
            ).fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["variables"] = _parse_json_list(d.get("variables"))
            result.append(d)
        return result
    finally:
        conn.close()


def render_template(name: str, variables: dict[str, str]) -> str:
    """Render a template by substituting *variables* into the template body.

    Uses ``str.format_map`` so unknown keys are left as ``{key}`` placeholders.
    Increments the template's ``usage_count``.
    """
    _ensure()
    tmpl = get_template(name)
    if tmpl is None:
        return f"[ERROR: template '{name}' not found]"
    try:
        rendered = tmpl["template_body"].format_map(variables)
    except KeyError as exc:
        rendered = f"[ERROR: missing variable {exc}]"

    # Increment usage counter
    conn = _db()
    try:
        conn.execute(
            "UPDATE writing_templates SET usage_count = usage_count + 1, updated_at = ? WHERE name = ?",
            (_now(), name),
        )
        conn.commit()
    finally:
        conn.close()

    return rendered


def update_template(name: str, **fields: Any) -> bool:
    """Update mutable fields on a template. Returns True on success."""
    _ensure()
    allowed = {"content_type", "template_body", "variables", "description"}
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "variables" and isinstance(value, list):
            value = json.dumps(value)
        updates[key] = value

    if not updates:
        return False

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [name]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE writing_templates SET {set_clause} WHERE name = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Writing job functions
# ---------------------------------------------------------------------------


def create_job(
    title: str,
    content_type: str,
    template_name: Optional[str] = None,
    tone: Optional[str] = None,
    target_audience: Optional[str] = None,
    keywords_list: Optional[list[str]] = None,
    platform: Optional[str] = None,
    notes: Optional[str] = None,
) -> WritingJob:
    """Create a new writing job and return it as a ``WritingJob``."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO writing_jobs
               (title, content_type, template_name, status, input_vars,
                output_draft, output_final, word_count, tone, target_audience,
                keywords, platform, notes, created_at, updated_at)
               VALUES (?, ?, ?, 'draft', '{}', '', '', 0, ?, ?, ?, ?, ?, ?, ?)""",
            (
                title,
                content_type,
                template_name or "",
                tone or "",
                target_audience or "",
                json.dumps(keywords_list or []),
                platform or "",
                notes or "",
                now,
                now,
            ),
        )
        conn.commit()
        job_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM writing_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_job(row)
    finally:
        conn.close()


def get_job(job_id: int) -> Optional[WritingJob]:
    """Return a ``WritingJob`` by ID, or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM writing_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_job(row)
    finally:
        conn.close()


def list_jobs(
    status: Optional[str] = None,
    content_type: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[WritingJob]:
    """Return writing jobs filtered by optional *status* and *content_type*."""
    _ensure()
    conn = _db()
    try:
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if content_type:
            clauses.append("content_type = ?")
            params.append(content_type)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        lim = f"LIMIT {int(limit)}" if limit else ""

        rows = conn.execute(
            f"SELECT * FROM writing_jobs {where} ORDER BY created_at DESC {lim}",
            params,
        ).fetchall()
        return [_row_to_job(r) for r in rows]
    finally:
        conn.close()


def save_draft(job_id: int, content: str, feedback: Optional[str] = None) -> bool:
    """Save a draft to writing_history and update the job record.

    Automatically bumps the version number for subsequent saves.
    """
    _ensure()
    now = _now()
    wc = word_count(content)
    conn = _db()
    try:
        # Determine next version number
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM writing_history WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        next_version = (row[0] or 0) + 1

        conn.execute(
            """INSERT INTO writing_history
               (job_id, version, content, word_count, feedback, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (job_id, next_version, content, wc, feedback or "", now),
        )
        conn.execute(
            """UPDATE writing_jobs
               SET output_draft = ?, word_count = ?, status = 'review',
                   updated_at = ?
               WHERE id = ?""",
            (content, wc, now, job_id),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def approve_job(job_id: int) -> bool:
    """Move a job to 'approved' status and copy draft to final output."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT output_draft FROM writing_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """UPDATE writing_jobs
               SET status = 'approved', output_final = ?, updated_at = ?
               WHERE id = ?""",
            (row["output_draft"], _now(), job_id),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def publish_job(job_id: int) -> bool:
    """Move an approved job to 'published' status."""
    _ensure()
    conn = _db()
    try:
        cur = conn.execute(
            "UPDATE writing_jobs SET status = 'published', updated_at = ? WHERE id = ?",
            (_now(), job_id),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False
    finally:
        conn.close()


def get_job_history(job_id: int) -> list[dict[str, Any]]:
    """Return all historical versions for a writing job, ordered by version."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM writing_history WHERE job_id = ? ORDER BY version",
            (job_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Content brief functions
# ---------------------------------------------------------------------------


def create_brief(
    title: str,
    content_type: str,
    target_keyword: str,
    secondary_keywords_list: Optional[list[str]] = None,
    target_audience: Optional[str] = None,
    goal: Optional[str] = None,
    word_count_target: int = 500,
    notes: Optional[str] = None,
) -> ContentBrief:
    """Create a new content brief and return it as a ``ContentBrief``."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO content_briefs
               (title, content_type, target_keyword, secondary_keywords,
                target_audience, goal, word_count_target, outline, notes,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 'draft', ?, ?)""",
            (
                title,
                content_type,
                target_keyword,
                json.dumps(secondary_keywords_list or []),
                target_audience or "",
                goal or "",
                word_count_target,
                notes or "",
                now,
                now,
            ),
        )
        conn.commit()
        brief_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM content_briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        return _row_to_brief(row)
    finally:
        conn.close()


def get_brief(brief_id: int) -> Optional[ContentBrief]:
    """Return a ``ContentBrief`` by ID, or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM content_briefs WHERE id = ?", (brief_id,)
        ).fetchone()
        if row is None:
            return None
        return _row_to_brief(row)
    finally:
        conn.close()


def list_briefs(status: Optional[str] = None) -> list[ContentBrief]:
    """Return all content briefs, optionally filtered by *status*."""
    _ensure()
    conn = _db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM content_briefs WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM content_briefs ORDER BY created_at DESC"
            ).fetchall()
        return [_row_to_brief(r) for r in rows]
    finally:
        conn.close()


def update_brief(brief_id: int, **fields: Any) -> bool:
    """Update mutable fields on a content brief. Returns True on success."""
    _ensure()
    allowed = {
        "title", "content_type", "target_keyword", "secondary_keywords",
        "target_audience", "goal", "word_count_target", "outline",
        "notes", "status",
    }
    updates: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key == "secondary_keywords" and isinstance(value, list):
            value = json.dumps(value)
        updates[key] = value

    if not updates:
        return False

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [brief_id]
    conn = _db()
    try:
        cur = conn.execute(
            f"UPDATE content_briefs SET {set_clause} WHERE id = ?", values
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def generate_outline(brief_id: int) -> str:
    """Build and save a markdown outline from a content brief.

    Returns the generated outline string, or an error message.
    """
    _ensure()
    brief = get_brief(brief_id)
    if brief is None:
        return f"[ERROR: brief {brief_id} not found]"

    lines: list[str] = []
    lines.append(f"# {brief.title}")
    lines.append("")

    if brief.target_audience:
        lines.append(f"**Target Audience:** {brief.target_audience}")
    if brief.goal:
        lines.append(f"**Goal:** {brief.goal}")
    if brief.target_keyword:
        lines.append(f"**Primary Keyword:** {brief.target_keyword}")
    if brief.secondary_keywords:
        lines.append(f"**Secondary Keywords:** {', '.join(brief.secondary_keywords)}")
    lines.append(f"**Target Word Count:** {brief.word_count_target}")
    lines.append("")

    # Build section structure based on content type
    if brief.content_type == "blog_post":
        lines += [
            "## I. Introduction",
            f"   - Hook the reader with a question or surprising fact about {brief.target_keyword}",
            "   - State the problem/opportunity",
            "   - Preview what the reader will learn",
            "",
            "## II. Background / Context",
            f"   - Explain why {brief.target_keyword} matters",
            "   - Relevant statistics or trends",
            "",
            "## III. Main Section A",
            "   - Key point 1",
            "   - Supporting details",
            "   - Example or case study",
            "",
            "## IV. Main Section B",
            "   - Key point 2",
            "   - Supporting details",
            "   - Example or case study",
            "",
            "## V. Main Section C",
            "   - Key point 3",
            "   - Actionable tips",
            "",
            "## VI. Conclusion",
            "   - Recap key takeaways",
            "   - Call to action",
        ]
    elif brief.content_type == "video_script":
        lines += [
            "## HOOK (0–5 seconds)",
            f"   - Open with a bold statement about {brief.target_keyword}",
            "",
            "## INTRO (5–30 seconds)",
            "   - Introduce the topic and speaker",
            "   - What the viewer will learn",
            "",
            "## MAIN CONTENT",
            "   - Point 1: [fill in]",
            "   - Point 2: [fill in]",
            "   - Point 3: [fill in]",
            "",
            "## CALL TO ACTION",
            "   - Subscribe / Like / Comment prompt",
            "   - Link to resource or next video",
            "",
            "## OUTRO",
            "   - Teaser for next video",
            "   - Sign-off",
        ]
    elif brief.content_type == "email_body":
        lines += [
            "## Subject Line Options",
            "   - Option A: [curiosity-driven]",
            "   - Option B: [benefit-driven]",
            "",
            "## Preview Text",
            "   - [Complement the subject line]",
            "",
            "## Email Body",
            "   1. Opening (personalized greeting)",
            f"   2. Hook — connect to {brief.target_keyword}",
            "   3. Main message / value delivery",
            "   4. Social proof or supporting detail",
            "   5. Call to action (single, clear CTA)",
            "   6. P.S. line (optional)",
        ]
    elif brief.content_type in ("landing_page", "ad_copy"):
        lines += [
            "## Hero Section",
            f"   - Headline: [Bold promise about {brief.target_keyword}]",
            "   - Sub-headline: [Reinforce with specifics]",
            "   - CTA button copy",
            "",
            "## Problem / Pain Point",
            "   - Describe the reader's current situation",
            "   - Agitate the pain",
            "",
            "## Solution",
            "   - Introduce the offer",
            "   - Key benefits (3–5 bullet points)",
            "",
            "## Social Proof",
            "   - Testimonials / reviews",
            "   - Stats or logos",
            "",
            "## CTA Section",
            "   - Repeat CTA with urgency",
            "   - Risk reversal (guarantee)",
        ]
    else:
        # Generic outline for other content types
        lines += [
            "## I. Opening",
            f"   - Introduce {brief.target_keyword}",
            "",
            "## II. Core Message",
            "   - Main points with supporting details",
            "",
            "## III. Key Takeaways",
            "   - Summary bullets",
            "",
            "## IV. Call to Action",
            "   - Next step for the reader",
        ]

    if brief.notes:
        lines.append("")
        lines.append("## Additional Notes")
        lines.append(f"   {brief.notes}")

    outline = "\n".join(lines)

    # Persist the outline back to the brief
    update_brief(brief_id, outline=outline, status="in_progress")

    return outline


def brief_to_job(brief_id: int, template_name: Optional[str] = None) -> WritingJob:
    """Convert a content brief into a writing job.

    Picks a sensible default template based on content_type if none provided.
    """
    _ensure()
    brief = get_brief(brief_id)
    if brief is None:
        raise ValueError(f"Content brief {brief_id} not found")

    # Pick a default template by content type
    if not template_name:
        _type_to_template: dict[str, str] = {
            "blog_post": "blog_post_standard",
            "social_post": "social_twitter",
            "email_body": "email_newsletter",
            "product_description": "product_description",
            "ad_copy": "ad_headline",
            "meta_description": "meta_description",
            "press_release": "press_release",
            "video_script": "video_script_intro",
        }
        template_name = _type_to_template.get(brief.content_type, "")

    job = create_job(
        title=brief.title,
        content_type=brief.content_type,
        template_name=template_name,
        target_audience=brief.target_audience,
        keywords_list=[brief.target_keyword] + brief.secondary_keywords,
        notes=f"Created from brief #{brief_id}. Goal: {brief.goal}",
    )

    # Update brief status to link it to a job
    update_brief(brief_id, status="in_progress")

    return job


# ---------------------------------------------------------------------------
# Writing style functions
# ---------------------------------------------------------------------------


def create_style(
    name: str,
    description: str,
    tone_words_list: list[str],
    avoid_words_list: Optional[list[str]] = None,
    example_sentences: Optional[str] = None,
) -> dict[str, Any]:
    """Create a named writing style and return it as a dict."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO writing_styles
               (name, description, tone_words, avoid_words, example_sentences,
                active, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (
                name,
                description,
                json.dumps(tone_words_list),
                json.dumps(avoid_words_list or []),
                example_sentences or "",
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM writing_styles WHERE name = ?", (name,)
        ).fetchone()
        d = _row_to_dict(row)
        d["tone_words"] = _parse_json_list(d.get("tone_words"))
        d["avoid_words"] = _parse_json_list(d.get("avoid_words"))
        return d
    finally:
        conn.close()


def get_style(name: str) -> Optional[dict[str, Any]]:
    """Return a writing style dict or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM writing_styles WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["tone_words"] = _parse_json_list(d.get("tone_words"))
        d["avoid_words"] = _parse_json_list(d.get("avoid_words"))
        return d
    finally:
        conn.close()


def list_styles(active_only: bool = True) -> list[dict[str, Any]]:
    """Return all writing styles, optionally filtering to only active ones."""
    _ensure()
    conn = _db()
    try:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM writing_styles WHERE active = 1 ORDER BY name"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM writing_styles ORDER BY name"
            ).fetchall()
        result = []
        for row in rows:
            d = _row_to_dict(row)
            d["tone_words"] = _parse_json_list(d.get("tone_words"))
            d["avoid_words"] = _parse_json_list(d.get("avoid_words"))
            result.append(d)
        return result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Brand voice functions
# ---------------------------------------------------------------------------


def create_brand_voice(
    name: str,
    description: str,
    personality_traits_list: list[str],
    writing_dos_list: list[str],
    writing_donts_list: list[str],
    sample_content: Optional[str] = None,
) -> dict[str, Any]:
    """Create a brand voice profile and return it as a dict."""
    _ensure()
    now = _now()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO brand_voices
               (name, description, personality_traits, writing_dos,
                writing_donts, sample_content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name,
                description,
                json.dumps(personality_traits_list),
                json.dumps(writing_dos_list),
                json.dumps(writing_donts_list),
                sample_content or "",
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM brand_voices WHERE name = ?", (name,)
        ).fetchone()
        return _brand_voice_row_to_dict(row)
    finally:
        conn.close()


def _brand_voice_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = _row_to_dict(row)
    d["personality_traits"] = _parse_json_list(d.get("personality_traits"))
    d["writing_dos"] = _parse_json_list(d.get("writing_dos"))
    d["writing_donts"] = _parse_json_list(d.get("writing_donts"))
    return d


def get_brand_voice(name: str) -> Optional[dict[str, Any]]:
    """Return a brand voice dict or None if not found."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM brand_voices WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        return _brand_voice_row_to_dict(row)
    finally:
        conn.close()


def list_brand_voices() -> list[dict[str, Any]]:
    """Return all brand voice profiles ordered by name."""
    _ensure()
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT * FROM brand_voices ORDER BY name"
        ).fetchall()
        return [_brand_voice_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def apply_brand_voice(content: str, brand_voice_name: str) -> dict[str, Any]:
    """Apply brand voice guidelines to content, returning an annotated dict.

    Returns ``{original, guidelines, word_count}`` — the caller (or an LLM)
    uses the guidelines to rewrite/revise the content.
    """
    _ensure()
    bv = get_brand_voice(brand_voice_name)
    if bv is None:
        return {
            "original": content,
            "guidelines": f"[ERROR: brand voice '{brand_voice_name}' not found]",
            "word_count": word_count(content),
        }

    sections: list[str] = [f"Brand Voice: {bv['name']}"]

    if bv["description"]:
        sections.append(f"Description: {bv['description']}")

    if bv["personality_traits"]:
        sections.append("Personality Traits:")
        for trait in bv["personality_traits"]:
            sections.append(f"  • {trait}")

    if bv["writing_dos"]:
        sections.append("DO:")
        for item in bv["writing_dos"]:
            sections.append(f"  ✓ {item}")

    if bv["writing_donts"]:
        sections.append("DON'T:")
        for item in bv["writing_donts"]:
            sections.append(f"  ✗ {item}")

    if bv["sample_content"]:
        sections.append(f"Sample Content:\n---\n{bv['sample_content']}\n---")

    return {
        "original": content,
        "guidelines": "\n".join(sections),
        "word_count": word_count(content),
    }


# ---------------------------------------------------------------------------
# Readability and text analysis
# ---------------------------------------------------------------------------


def _count_syllables(word: str) -> int:
    """Count syllables in a word using vowel-group counting.

    Simple heuristic: count runs of vowels, subtract silent-e where obvious.
    Returns at least 1 for any non-empty word.
    """
    word = word.lower().strip(".,;:!?\"'()-")
    if not word:
        return 1

    vowels = "aeiou"
    count = 0
    prev_vowel = False

    for char in word:
        is_vowel = char in vowels
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel

    # Subtract silent trailing 'e'
    if word.endswith("e") and count > 1:
        count -= 1

    return max(1, count)


def readability_score(text: str) -> dict[str, Any]:
    """Compute a Flesch Reading Ease score for *text*.

    Formula: 206.835 - 1.015 * (words/sentences) - 84.6 * (syllables/words)

    Returns:
        words, sentences, avg_words_per_sentence, avg_syllables (per word),
        flesch_score (0–100, higher = easier to read).
    """
    if not text or not text.strip():
        return {
            "words": 0,
            "sentences": 0,
            "avg_words_per_sentence": 0.0,
            "avg_syllables": 0.0,
            "flesch_score": 0.0,
        }

    # Tokenize words (alpha only for scoring)
    words_raw = text.split()
    words_alpha = [w.strip(".,;:!?\"'()-") for w in words_raw]
    words_alpha = [w for w in words_alpha if w and any(c.isalpha() for c in w)]
    num_words = len(words_alpha)

    # Count sentences (split on . ! ?)
    sentences_raw = re.split(r"[.!?]+", text)
    sentences_trimmed = [s.strip() for s in sentences_raw if s.strip()]
    num_sentences = max(1, len(sentences_trimmed))

    # Count syllables
    total_syllables = sum(_count_syllables(w) for w in words_alpha)

    if num_words == 0:
        return {
            "words": 0,
            "sentences": num_sentences,
            "avg_words_per_sentence": 0.0,
            "avg_syllables": 0.0,
            "flesch_score": 0.0,
        }

    avg_words_per_sentence = num_words / num_sentences
    avg_syllables = total_syllables / num_words

    flesch = 206.835 - 1.015 * avg_words_per_sentence - 84.6 * avg_syllables
    flesch = round(max(0.0, min(100.0, flesch)), 2)

    return {
        "words": num_words,
        "sentences": num_sentences,
        "avg_words_per_sentence": round(avg_words_per_sentence, 2),
        "avg_syllables": round(avg_syllables, 2),
        "flesch_score": flesch,
    }


def extract_keywords(text: str, top_n: int = 10) -> list[str]:
    """Extract the top *top_n* keywords from *text* by frequency.

    Lowercases, strips punctuation, removes stopwords, and returns the most
    frequent content words.
    """
    if not text:
        return []

    # Tokenize and clean
    tokens = re.findall(r"[a-zA-Z]+", text.lower())
    tokens = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]

    if not tokens:
        return []

    freq = Counter(tokens)
    return [word for word, _count in freq.most_common(top_n)]


def seo_check(content: str, target_keyword: str) -> dict[str, Any]:
    """Run a basic SEO check on *content* for *target_keyword*.

    Returns:
        keyword_count, keyword_density (%), has_in_title (bool),
        has_in_first_100_chars (bool), word_count, meta_description_ok (bool).

    ``meta_description_ok`` is True when content is between 50–160 characters,
    suitable for use as a meta description.
    """
    if not content:
        return {
            "keyword_count": 0,
            "keyword_density": 0.0,
            "has_in_title": False,
            "has_in_first_100_chars": False,
            "word_count": 0,
            "meta_description_ok": False,
        }

    kw_lower = target_keyword.lower()
    content_lower = content.lower()

    # Count occurrences (whole-word-ish: simple substring for now)
    keyword_count = content_lower.count(kw_lower)

    wc = word_count(content)
    kw_words = len(target_keyword.split())
    keyword_density = round(
        (keyword_count * kw_words / max(wc, 1)) * 100, 2
    )

    # Check if keyword appears in first line (title proxy)
    first_line = content.split("\n")[0].lower()
    has_in_title = kw_lower in first_line

    # Check first 100 chars
    has_in_first_100 = kw_lower in content_lower[:100]

    # Meta description check: 50–160 characters
    stripped = content.strip()
    meta_ok = 50 <= len(stripped) <= 160

    return {
        "keyword_count": keyword_count,
        "keyword_density": keyword_density,
        "has_in_title": has_in_title,
        "has_in_first_100_chars": has_in_first_100,
        "word_count": wc,
        "meta_description_ok": meta_ok,
    }


def writing_summary() -> dict[str, Any]:
    """Return aggregate writing statistics across all jobs."""
    _ensure()
    conn = _db()
    try:
        total_row = conn.execute("SELECT COUNT(*) FROM writing_jobs").fetchone()
        total_jobs = total_row[0] if total_row else 0

        # By status
        by_status: dict[str, int] = {}
        for status in JOB_STATUSES:
            row = conn.execute(
                "SELECT COUNT(*) FROM writing_jobs WHERE status = ?", (status,)
            ).fetchone()
            by_status[status] = row[0] if row else 0

        # By content type
        by_content_type: dict[str, int] = {}
        rows = conn.execute(
            "SELECT content_type, COUNT(*) as cnt FROM writing_jobs GROUP BY content_type"
        ).fetchall()
        for row in rows:
            by_content_type[row["content_type"]] = row["cnt"]

        # Total and average word count
        agg_row = conn.execute(
            "SELECT COALESCE(SUM(word_count), 0), COALESCE(AVG(word_count), 0.0) FROM writing_jobs"
        ).fetchone()
        total_words = int(agg_row[0]) if agg_row else 0
        avg_words = round(float(agg_row[1]), 1) if agg_row else 0.0

        return {
            "total_jobs": total_jobs,
            "by_status": by_status,
            "by_content_type": by_content_type,
            "total_words_generated": total_words,
            "avg_word_count": avg_words,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------


@tool("aw_create_job", "Create a new AI writing job with title, content type, tone, target audience, and keywords.")
def aw_create_job_tool(
    agent: str,
    title: str,
    content_type: str,
    tone: Optional[str] = None,
    target_audience: Optional[str] = None,
    keywords_csv: Optional[str] = None,
) -> str:
    """Create a new writing job. Returns JSON with the created job."""
    try:
        if content_type not in CONTENT_TYPES:
            return json.dumps({
                "ok": False,
                "error": f"Unknown content_type '{content_type}'. Valid: {CONTENT_TYPES}",
            })
        if tone and tone not in TONES:
            return json.dumps({
                "ok": False,
                "error": f"Unknown tone '{tone}'. Valid: {TONES}",
            })
        keywords_list: list[str] = []
        if keywords_csv:
            keywords_list = [k.strip() for k in keywords_csv.split(",") if k.strip()]
        job = create_job(
            title=title,
            content_type=content_type,
            tone=tone,
            target_audience=target_audience,
            keywords_list=keywords_list,
        )
        return json.dumps({"ok": True, "job": job.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_list_jobs", "List writing jobs, optionally filtered by status and/or content type.")
def aw_list_jobs_tool(
    agent: str,
    status: Optional[str] = None,
    content_type: Optional[str] = None,
) -> str:
    """Return a JSON list of writing jobs with optional filters."""
    try:
        jobs = list_jobs(status=status, content_type=content_type, limit=100)
        return json.dumps({
            "ok": True,
            "count": len(jobs),
            "jobs": [j.to_dict() for j in jobs],
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_save_draft", "Save a content draft for a writing job and advance it to review status.")
def aw_save_draft_tool(
    agent: str,
    job_id: int,
    content: str,
) -> str:
    """Save a draft for *job_id*. Returns JSON with success status and word count."""
    try:
        job_id = int(job_id)
        ok = save_draft(job_id, content)
        wc = word_count(content)
        return json.dumps({
            "ok": ok,
            "job_id": job_id,
            "word_count": wc,
            "message": "Draft saved and job moved to review." if ok else "Failed to save draft.",
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_create_brief", "Create a content brief with title, content type, target keyword, and word count target.")
def aw_create_brief_tool(
    agent: str,
    title: str,
    content_type: str,
    target_keyword: str,
    word_count_target: int = 500,
) -> str:
    """Create a content brief. Returns JSON with the created brief."""
    try:
        if content_type not in CONTENT_TYPES:
            return json.dumps({
                "ok": False,
                "error": f"Unknown content_type '{content_type}'. Valid: {CONTENT_TYPES}",
            })
        brief = create_brief(
            title=title,
            content_type=content_type,
            target_keyword=target_keyword,
            word_count_target=int(word_count_target),
        )
        return json.dumps({"ok": True, "brief": brief.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_generate_outline", "Generate a structured markdown outline from a content brief.")
def aw_generate_outline_tool(
    agent: str,
    brief_id: int,
) -> str:
    """Generate and save an outline for the given brief. Returns JSON with the outline text."""
    try:
        brief_id = int(brief_id)
        outline = generate_outline(brief_id)
        return json.dumps({
            "ok": True,
            "brief_id": brief_id,
            "outline": outline,
            "word_count": word_count(outline),
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_render_template", "Render a writing template by substituting variables into the template body.")
def aw_render_template_tool(
    agent: str,
    template_name: str,
    variables_json: Optional[str] = None,
) -> str:
    """Render a named template with the provided variables (JSON object string).

    Example variables_json: '{"title": "My Post", "intro": "Hello world"}'
    """
    try:
        variables: dict[str, str] = {}
        if variables_json:
            parsed = json.loads(variables_json)
            if isinstance(parsed, dict):
                variables = {str(k): str(v) for k, v in parsed.items()}

        rendered = render_template(template_name, variables)
        return json.dumps({
            "ok": True,
            "template_name": template_name,
            "rendered": rendered,
            "word_count": word_count(rendered),
        })
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"Invalid variables_json: {exc}"})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_readability", "Compute Flesch Reading Ease score and readability metrics for content.")
def aw_readability_tool(
    agent: str,
    content: str,
) -> str:
    """Return JSON readability metrics including Flesch score for the given content."""
    try:
        metrics = readability_score(content)
        # Add a human-readable label
        score = metrics["flesch_score"]
        if score >= 90:
            label = "Very Easy"
        elif score >= 80:
            label = "Easy"
        elif score >= 70:
            label = "Fairly Easy"
        elif score >= 60:
            label = "Standard"
        elif score >= 50:
            label = "Fairly Difficult"
        elif score >= 30:
            label = "Difficult"
        else:
            label = "Very Confusing"
        metrics["readability_label"] = label
        return json.dumps({"ok": True, **metrics})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_seo_check", "Run an SEO check on content for a target keyword: density, placement, word count.")
def aw_seo_check_tool(
    agent: str,
    content: str,
    target_keyword: str,
) -> str:
    """Return JSON SEO metrics for *content* targeting *target_keyword*."""
    try:
        result = seo_check(content, target_keyword)
        # Add recommendations
        recommendations: list[str] = []
        if result["keyword_count"] == 0:
            recommendations.append(f"Include '{target_keyword}' at least once.")
        elif result["keyword_density"] < 0.5:
            recommendations.append("Keyword density is low — consider adding more mentions.")
        elif result["keyword_density"] > 3.0:
            recommendations.append("Keyword density is too high — risk of keyword stuffing.")
        if not result["has_in_title"]:
            recommendations.append("Add the target keyword to the title/first line.")
        if not result["has_in_first_100_chars"]:
            recommendations.append("Mention the keyword in the opening paragraph.")
        if result["word_count"] < 300:
            recommendations.append("Content is short — aim for at least 300 words for SEO.")
        if not recommendations:
            recommendations.append("SEO looks good!")
        result["recommendations"] = recommendations
        return json.dumps({"ok": True, "target_keyword": target_keyword, **result})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_writing_summary", "Get aggregate writing statistics: total jobs, by status, by type, word counts.")
def aw_writing_summary_tool(agent: str) -> str:
    """Return JSON summary of all writing activity."""
    try:
        summary = writing_summary()
        return json.dumps({"ok": True, **summary})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("aw_extract_keywords", "Extract top keywords from text by frequency, excluding common stop words.")
def aw_extract_keywords_tool(
    agent: str,
    content: str,
    top_n: int = 10,
) -> str:
    """Return JSON list of top keywords extracted from *content*."""
    try:
        top_n = int(top_n)
        keywords = extract_keywords(content, top_n=top_n)
        return json.dumps({
            "ok": True,
            "top_n": top_n,
            "keywords": keywords,
            "count": len(keywords),
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
