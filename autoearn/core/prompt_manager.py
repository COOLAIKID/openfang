"""Prompt template manager for AutoEarn agents.

Agents use system prompts and structured templates to communicate with LLMs.
This module provides versioned prompt storage, A/B prompt testing, and
performance tracking so agents can improve their prompts over time.

Features:
- Versioned prompt templates with metadata
- Prompt variable substitution with {{variable}} syntax
- Performance tracking: success rate per prompt version
- Prompt diff and rollback
- Auto-optimization suggestions based on success history
- Template library for common patterns (persuasion, analysis, writing, etc.)
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"

TEMPLATE_VARIABLES_RE = re.compile(r"\{\{(\w+)\}\}")


# ---------------------------------------------------------------------------
# Built-in prompt templates
# ---------------------------------------------------------------------------

BUILTIN_TEMPLATES: dict[str, dict[str, str]] = {
    "chain_of_thought": {
        "description": "Encourage step-by-step reasoning before answering.",
        "template": "Think step by step. First, {{setup}}. Then reason through each step carefully before giving your final answer.",
    },
    "role_expert": {
        "description": "Prime the LLM as a domain expert.",
        "template": "You are an expert {{domain}} specialist with 10+ years of experience. You provide {{output_type}} that is accurate, actionable, and tailored for {{audience}}.",
    },
    "persuasion_pas": {
        "description": "Problem → Agitate → Solution copywriting formula.",
        "template": "Write copy using the PAS formula:\n- Problem: {{problem}}\n- Agitate: Make the reader feel the pain of not solving this.\n- Solution: {{solution}}\nTone: {{tone}}. Length: {{length}}.",
    },
    "seo_article": {
        "description": "Long-form SEO article prompt.",
        "template": "Write a {{word_count}}-word SEO article about '{{keyword}}'. Target audience: {{audience}}. Include: H2 headings, a compelling intro, bullet points, FAQ section with 5 Q&As, and a strong CTA at the end. Primary keyword density: 1-2%.",
    },
    "product_review": {
        "description": "Honest affiliate product review.",
        "template": "Write a detailed, honest review of {{product_name}} for {{audience}}. Cover: pros (at least 5), cons (at least 3), who it's for, who it's NOT for, alternatives, and a verdict score out of 10. Be specific, not generic.",
    },
    "email_subject_lines": {
        "description": "Generate email subject line variations.",
        "template": "Generate {{count}} email subject lines for {{topic}}. Include variations: curiosity-driven, benefit-driven, fear-of-missing-out, question-based, and number-list formats. Audience: {{audience}}.",
    },
    "sales_page_headline": {
        "description": "Sales page headline with subheadline.",
        "template": "Write 5 headline + subheadline combinations for {{product}} targeting {{audience}}. The headline must promise {{main_benefit}}. Make each variation use a different emotional hook: aspiration, pain relief, curiosity, social proof, urgency.",
    },
    "competitor_teardown": {
        "description": "Competitor strategy analysis.",
        "template": "Analyze {{competitor}} as a competitor in the {{niche}} space. Cover: their positioning, content strategy, pricing, strengths, weaknesses, and what we can learn from them. Conclude with 3 specific actions we can take to outcompete them.",
    },
    "content_brief": {
        "description": "Content brief for writers.",
        "template": "Create a detailed content brief for an article targeting keyword '{{keyword}}'. Include: title options (3), search intent, target word count ({{word_count}}), H2 outline with {{num_sections}} sections, suggested internal links, key points to cover, tone of voice, and CTA.",
    },
    "social_caption": {
        "description": "Social media caption generator.",
        "template": "Write a {{platform}} caption for {{content_topic}}. Tone: {{tone}}. Include a hook in the first line, {{num_paragraphs}} paragraphs of value, and end with {{cta}}. Add {{num_hashtags}} relevant hashtags.",
    },
    "outreach_email": {
        "description": "Cold outreach email.",
        "template": "Write a cold outreach email to {{recipient_role}} at {{company_type}} companies. Subject line should tease {{value_prop}}. Keep it under 150 words. Personalization hook: {{personalization}}. CTA: {{cta}}.",
    },
    "data_insight_summary": {
        "description": "Turn data into actionable narrative.",
        "template": "Summarize the following data in plain English for {{audience}}: {{data}}. Focus on: the 3 most important insights, what they mean for business decisions, and 2 specific recommendations based on the data. Keep it under {{word_limit}} words.",
    },
    "meeting_agenda": {
        "description": "Meeting agenda creator.",
        "template": "Create a {{duration}}-minute {{meeting_type}} meeting agenda. Attendees: {{attendees}}. Goal: {{goal}}. Include: warm-up, main discussion points (with time allocations), decision points, action items capture, and wrap-up. Keep tone {{tone}}.",
    },
    "press_release": {
        "description": "Press release template.",
        "template": "Write a press release announcing {{announcement}} by {{company}}. Include: headline (news-style), subheadline, dateline, 3 body paragraphs, a quote from {{spokesperson_title}}, boilerplate about the company, and contact info section.",
    },
    "podcast_episode": {
        "description": "Podcast episode outline.",
        "template": "Outline a {{duration}}-minute podcast episode on '{{topic}}'. Include: episode title, hook (first 30 seconds script), 4-6 main segments with talking points, guest questions (if applicable: {{guest_expertise}}), sponsor spots, and outro CTA.",
    },
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PromptTemplate:
    name: str
    template: str
    description: str = ""
    category: str = "general"
    version: int = 1
    is_active: bool = True
    success_count: int = 0
    failure_count: int = 0
    usage_count: int = 0
    created_by: str = "system"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    id: int = 0

    @property
    def variables(self) -> list[str]:
        return list(set(TEMPLATE_VARIABLES_RE.findall(self.template)))

    @property
    def success_rate(self) -> float:
        total = self.success_count + self.failure_count
        return round(self.success_count / max(1, total), 3)

    def render(self, variables: dict[str, str]) -> str:
        text = self.template
        for k, v in variables.items():
            text = text.replace(f"{{{{{k}}}}}", str(v))
        return text

    def missing_variables(self, variables: dict[str, str]) -> list[str]:
        return [v for v in self.variables if v not in variables]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PromptTemplate":
        pt = cls(
            name=row["name"],
            template=row["template"],
            description=row["description"] or "",
            category=row["category"] or "general",
            version=row["version"] or 1,
            is_active=bool(row["is_active"]),
            success_count=row["success_count"] or 0,
            failure_count=row["failure_count"] or 0,
            usage_count=row["usage_count"] or 0,
            created_by=row["created_by"] or "system",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
        pt.id = row["id"]
        return pt


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema() -> None:
    conn = _get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                template        TEXT    NOT NULL,
                description     TEXT    NOT NULL DEFAULT '',
                category        TEXT    NOT NULL DEFAULT 'general',
                version         INTEGER NOT NULL DEFAULT 1,
                is_active       INTEGER NOT NULL DEFAULT 1,
                success_count   INTEGER NOT NULL DEFAULT 0,
                failure_count   INTEGER NOT NULL DEFAULT 0,
                usage_count     INTEGER NOT NULL DEFAULT 0,
                created_by      TEXT    NOT NULL DEFAULT 'system',
                created_at      REAL    NOT NULL,
                updated_at      REAL    NOT NULL,
                UNIQUE(name, version)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_usage_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                template_id INTEGER NOT NULL,
                agent       TEXT    NOT NULL,
                variables   TEXT    NOT NULL DEFAULT '{}',
                success     INTEGER NOT NULL DEFAULT 1,
                latency_ms  REAL    NOT NULL DEFAULT 0,
                ts          REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_name ON prompt_templates(name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pt_cat ON prompt_templates(category)")
    conn.close()


_schema_ready = False


def _ensure() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _seed_builtins()
        _schema_ready = True


def _seed_builtins() -> None:
    """Seed built-in templates if not already present."""
    for name, meta in BUILTIN_TEMPLATES.items():
        save_template(
            name=f"builtin:{name}",
            template=meta["template"],
            description=meta["description"],
            category="builtin",
            created_by="system",
        )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def save_template(
    name: str,
    template: str,
    description: str = "",
    category: str = "general",
    created_by: str = "system",
) -> PromptTemplate:
    _ensure()
    now = time.time()
    conn = _get_db()

    existing = conn.execute(
        "SELECT id, version FROM prompt_templates WHERE name=? ORDER BY version DESC LIMIT 1",
        (name,),
    ).fetchone()

    if existing:
        new_version = existing["version"] + 1
        with conn:
            conn.execute(
                "UPDATE prompt_templates SET is_active=0 WHERE name=?", (name,)
            )
    else:
        new_version = 1

    with conn:
        cur = conn.execute(
            """INSERT INTO prompt_templates
               (name, template, description, category, version, is_active,
                success_count, failure_count, usage_count, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,1,0,0,0,?,?,?)""",
            (name, template, description, category, new_version, created_by, now, now),
        )
        new_id = cur.lastrowid
    conn.close()

    pt = PromptTemplate(
        name=name, template=template, description=description,
        category=category, version=new_version, created_by=created_by,
        created_at=now, updated_at=now,
    )
    pt.id = new_id
    return pt


def get_template(name: str, version: int | None = None) -> Optional[PromptTemplate]:
    _ensure()
    conn = _get_db()
    if version:
        row = conn.execute(
            "SELECT * FROM prompt_templates WHERE name=? AND version=?", (name, version)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM prompt_templates WHERE name=? AND is_active=1", (name,)
        ).fetchone()
    conn.close()
    return PromptTemplate.from_row(row) if row else None


def list_templates(category: str = "") -> list[PromptTemplate]:
    _ensure()
    conn = _get_db()
    if category:
        rows = conn.execute(
            "SELECT * FROM prompt_templates WHERE is_active=1 AND category=? ORDER BY name",
            (category,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM prompt_templates WHERE is_active=1 ORDER BY category, name",
        ).fetchall()
    conn.close()
    return [PromptTemplate.from_row(r) for r in rows]


def render_template(name: str, variables: dict[str, str]) -> str:
    """Render a named template with variable substitution."""
    _ensure()
    pt = get_template(name)
    if not pt:
        return f"ERROR: template '{name}' not found"
    missing = pt.missing_variables(variables)
    if missing:
        return f"ERROR: missing variables: {', '.join(missing)}"
    return pt.render(variables)


def record_usage(
    name: str, agent: str, variables: dict[str, str],
    success: bool = True, latency_ms: float = 0.0,
) -> None:
    _ensure()
    pt = get_template(name)
    if not pt:
        return
    conn = _get_db()
    with conn:
        conn.execute(
            "INSERT INTO prompt_usage_log (template_id, agent, variables, success, latency_ms, ts) VALUES (?,?,?,?,?,?)",
            (pt.id, agent, json.dumps(variables), int(success), latency_ms, time.time()),
        )
        if success:
            conn.execute("UPDATE prompt_templates SET success_count=success_count+1, usage_count=usage_count+1 WHERE id=?", (pt.id,))
        else:
            conn.execute("UPDATE prompt_templates SET failure_count=failure_count+1, usage_count=usage_count+1 WHERE id=?", (pt.id,))
    conn.close()


def delete_template(name: str) -> str:
    _ensure()
    conn = _get_db()
    with conn:
        conn.execute("DELETE FROM prompt_templates WHERE name=?", (name,))
    conn.close()
    return f"Deleted all versions of template '{name}'"


def rollback_template(name: str) -> str:
    """Rollback to the previous version of a template."""
    _ensure()
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, version FROM prompt_templates WHERE name=? ORDER BY version DESC LIMIT 2",
        (name,),
    ).fetchall()
    if len(rows) < 2:
        conn.close()
        return f"No previous version to roll back to for '{name}'"
    current_id, prev_id = rows[0]["id"], rows[1]["id"]
    with conn:
        conn.execute("UPDATE prompt_templates SET is_active=0 WHERE id=?", (current_id,))
        conn.execute("UPDATE prompt_templates SET is_active=1 WHERE id=?", (prev_id,))
    conn.close()
    return f"Rolled back '{name}' to version {rows[1]['version']}"


def template_stats() -> dict[str, Any]:
    _ensure()
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM prompt_templates WHERE is_active=1").fetchone()[0]
    by_cat = {r["category"]: r["cnt"] for r in conn.execute(
        "SELECT category, COUNT(*) as cnt FROM prompt_templates WHERE is_active=1 GROUP BY category"
    ).fetchall()}
    top_used = conn.execute(
        "SELECT name, usage_count, success_count FROM prompt_templates WHERE is_active=1 ORDER BY usage_count DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return {
        "total_templates": total,
        "by_category": by_cat,
        "top_used": [dict(r) for r in top_used],
    }


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

def save_prompt_tool(name: str, template: str, description: str = "", category: str = "general") -> str:
    try:
        pt = save_template(name, template, description, category)
        return f"Saved prompt template '{pt.name}' v{pt.version} (variables: {pt.variables})"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def render_prompt_tool(name: str, variables: dict | None = None) -> str:
    return render_template(name, variables or {})


def list_prompts_tool(category: str = "") -> str:
    templates = list_templates(category)
    return json.dumps([
        {"name": t.name, "category": t.category, "version": t.version,
         "variables": t.variables, "usage": t.usage_count, "success_rate": t.success_rate}
        for t in templates
    ])


def prompt_stats_tool() -> str:
    return json.dumps(template_stats())
