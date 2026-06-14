"""
Reporting Engine — scheduled report generation, PDF/Markdown/JSON export,
and automated email/Telegram delivery of business intelligence reports.

Pulls data from all other modules to assemble executive, campaign, product,
funnel, and content performance reports. All report definitions and history
stored in SQLite.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .database import get_db_path
from .tools import tool

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_TYPES = [
    "executive_summary",
    "revenue_report",
    "content_performance",
    "campaign_performance",
    "funnel_report",
    "affiliate_summary",
    "product_catalog",
    "newsletter_health",
    "seo_audit",
    "agent_activity",
    "custom",
]

OUTPUT_FORMATS = ["markdown", "json", "text", "html"]

DELIVERY_CHANNELS = ["save", "telegram", "email", "slack", "webhook"]

FREQUENCIES = [
    "once",
    "daily",
    "weekly",
    "monthly",
    "quarterly",
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
            CREATE TABLE IF NOT EXISTS report_definitions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL UNIQUE,
                report_type   TEXT NOT NULL DEFAULT 'executive_summary',
                description   TEXT,
                output_format TEXT NOT NULL DEFAULT 'markdown',
                frequency     TEXT NOT NULL DEFAULT 'weekly',
                next_run_at   TEXT,
                last_run_at   TEXT,
                is_active     INTEGER NOT NULL DEFAULT 1,
                delivery      TEXT DEFAULT '["save"]',
                delivery_cfg  TEXT DEFAULT '{}',
                parameters    TEXT DEFAULT '{}',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_runs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id       INTEGER NOT NULL REFERENCES report_definitions(id) ON DELETE CASCADE,
                run_ref         TEXT NOT NULL UNIQUE,
                status          TEXT NOT NULL DEFAULT 'pending',
                output_format   TEXT NOT NULL DEFAULT 'markdown',
                content         TEXT,
                file_path       TEXT,
                byte_size       INTEGER,
                error_message   TEXT,
                started_at      TEXT NOT NULL,
                completed_at    TEXT,
                parameters      TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS report_sections (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      INTEGER NOT NULL REFERENCES report_runs(id) ON DELETE CASCADE,
                section     TEXT NOT NULL,
                title       TEXT NOT NULL,
                content     TEXT,
                sort_order  INTEGER NOT NULL DEFAULT 0,
                data_json   TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS report_metrics_cache (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                metric_key  TEXT NOT NULL UNIQUE,
                value_json  TEXT NOT NULL,
                cached_at   TEXT NOT NULL,
                expires_at  TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_report_runs_report  ON report_runs(report_id);
            CREATE INDEX IF NOT EXISTS idx_report_runs_started ON report_runs(started_at);
            CREATE INDEX IF NOT EXISTS idx_report_runs_status  ON report_runs(status);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ReportDefinition:
    name: str
    report_type: str = "executive_summary"
    description: str = ""
    output_format: str = "markdown"
    frequency: str = "weekly"
    next_run_at: Optional[str] = None
    last_run_at: Optional[str] = None
    is_active: bool = True
    delivery: List[str] = field(default_factory=lambda: ["save"])
    delivery_cfg: Dict[str, Any] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "report_type": self.report_type,
            "description": self.description,
            "output_format": self.output_format,
            "frequency": self.frequency,
            "next_run_at": self.next_run_at,
            "last_run_at": self.last_run_at,
            "is_active": self.is_active,
            "delivery": self.delivery,
            "parameters": self.parameters,
            "created_at": self.created_at,
        }


@dataclass
class ReportRun:
    report_id: int
    output_format: str = "markdown"
    status: str = "pending"
    content: str = ""
    file_path: str = ""
    byte_size: int = 0
    error_message: str = ""
    started_at: str = ""
    completed_at: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    run_ref: str = ""
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "report_id": self.report_id,
            "run_ref": self.run_ref,
            "status": self.status,
            "output_format": self.output_format,
            "byte_size": self.byte_size,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _next_run_for(frequency: str, from_dt: Optional[datetime] = None) -> str:
    base = from_dt or datetime.utcnow()
    if frequency == "daily":
        return (base + timedelta(days=1)).isoformat()
    if frequency == "weekly":
        return (base + timedelta(weeks=1)).isoformat()
    if frequency == "monthly":
        return (base + timedelta(days=30)).isoformat()
    if frequency == "quarterly":
        return (base + timedelta(days=90)).isoformat()
    return base.isoformat()


def _def_from_row(row: sqlite3.Row) -> ReportDefinition:
    return ReportDefinition(
        id=row["id"],
        name=row["name"],
        report_type=row["report_type"],
        description=row["description"] or "",
        output_format=row["output_format"],
        frequency=row["frequency"],
        next_run_at=row["next_run_at"],
        last_run_at=row["last_run_at"],
        is_active=bool(row["is_active"]),
        delivery=json.loads(row["delivery"] or '["save"]'),
        delivery_cfg=json.loads(row["delivery_cfg"] or "{}"),
        parameters=json.loads(row["parameters"] or "{}"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Report definition CRUD
# ---------------------------------------------------------------------------

def create_report_definition(
    name: str,
    report_type: str = "executive_summary",
    description: str = "",
    output_format: str = "markdown",
    frequency: str = "weekly",
    delivery: Optional[List[str]] = None,
    delivery_cfg: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
) -> ReportDefinition:
    """Register a new scheduled report definition."""
    _ensure()
    now = datetime.utcnow().isoformat()
    next_run = _next_run_for(frequency)
    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO report_definitions
               (name, report_type, description, output_format, frequency,
                next_run_at, delivery, delivery_cfg, parameters, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name, report_type, description, output_format, frequency, next_run,
                json.dumps(delivery or ["save"]),
                json.dumps(delivery_cfg or {}),
                json.dumps(parameters or {}),
                now, now,
            ),
        )
        conn.commit()
        return ReportDefinition(
            id=cur.lastrowid,
            name=name,
            report_type=report_type,
            description=description,
            output_format=output_format,
            frequency=frequency,
            next_run_at=next_run,
            delivery=delivery or ["save"],
            delivery_cfg=delivery_cfg or {},
            parameters=parameters or {},
            created_at=now,
            updated_at=now,
        )
    except sqlite3.IntegrityError:
        raise ValueError(f"Report '{name}' already exists")
    finally:
        conn.close()


def get_report_definition(name: str) -> Optional[ReportDefinition]:
    """Fetch a report definition by name."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM report_definitions WHERE name = ?", (name,)
        ).fetchone()
        return _def_from_row(row) if row else None
    finally:
        conn.close()


def list_report_definitions(active_only: bool = True) -> List[ReportDefinition]:
    """List all report definitions."""
    _ensure()
    where = "WHERE is_active = 1" if active_only else ""
    conn = _db()
    try:
        rows = conn.execute(
            f"SELECT * FROM report_definitions {where} ORDER BY name"
        ).fetchall()
        return [_def_from_row(r) for r in rows]
    finally:
        conn.close()


def update_report_definition(name: str, **kwargs) -> bool:
    """Update report definition fields."""
    _ensure()
    allowed = {
        "description", "output_format", "frequency", "is_active",
        "delivery", "delivery_cfg", "parameters",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    for key in ("delivery", "delivery_cfg", "parameters"):
        if key in updates:
            updates[key] = json.dumps(updates[key])
    updates["updated_at"] = datetime.utcnow().isoformat()
    if "frequency" in kwargs:
        updates["next_run_at"] = _next_run_for(kwargs["frequency"])

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    conn = _db()
    try:
        conn.execute(
            f"UPDATE report_definitions SET {set_clause} WHERE name = ?",
            (*updates.values(), name),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def due_reports() -> List[ReportDefinition]:
    """Return all active report definitions whose next_run_at has passed."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT * FROM report_definitions
               WHERE is_active = 1 AND (next_run_at IS NULL OR next_run_at <= ?)
               ORDER BY next_run_at""",
            (now,),
        ).fetchall()
        return [_def_from_row(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Data gatherers (pull from other modules safely)
# ---------------------------------------------------------------------------

def _safe_import(module_path: str, fn_name: str, *args, **kwargs) -> Any:
    """Call a function from another module, returning None on any error."""
    try:
        parts = module_path.rsplit(".", 1)
        mod = __import__(module_path, fromlist=[fn_name])
        fn = getattr(mod, fn_name)
        return fn(*args, **kwargs)
    except Exception:
        return None


def _gather_revenue_data(since: str, until: str) -> Dict[str, Any]:
    from . import analytics_tracker as _at
    try:
        kpi = _at.kpi_summary(period_days=30)
        daily = _at.daily_revenue_series(days=30)
        channels = _at.channel_breakdown(days=30)
        return {"kpi": kpi, "daily": daily, "channels": channels}
    except Exception:
        return {}


def _gather_product_data() -> Dict[str, Any]:
    try:
        from . import product_manager as _pm
        catalog = _pm.product_catalog_summary()
        best = _pm.best_sellers(limit=5)
        return {"catalog": catalog, "best_sellers": best}
    except Exception:
        return {}


def _gather_affiliate_data() -> Dict[str, Any]:
    try:
        from . import affiliate_tracker as _aff
        return _aff.affiliate_summary()
    except Exception:
        return {}


def _gather_newsletter_data() -> Dict[str, Any]:
    try:
        from . import newsletter as _nl
        summary = _nl.newsletter_summary()
        growth = _nl.growth_trend(weeks=4)
        return {"summary": summary, "growth": growth}
    except Exception:
        return {}


def _gather_seo_data() -> Dict[str, Any]:
    try:
        from . import seo_optimizer as _seo
        overview = _seo.seo_site_overview()
        needs_work = _seo.pages_needing_optimization(limit=5)
        return {"overview": overview, "needs_work": needs_work}
    except Exception:
        return {}


def _gather_agent_data() -> Dict[str, Any]:
    try:
        from .database import get_recent_logs
        logs = get_recent_logs(limit=50)
        return {"recent_logs": [dict(l) for l in logs] if logs else []}
    except Exception:
        return {}


def _gather_funnel_data() -> Dict[str, Any]:
    try:
        from . import funnel_builder as _fb
        best = _fb.best_performing_funnels(limit=5)
        return {"best_funnels": best}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Report assemblers
# ---------------------------------------------------------------------------

def _assemble_executive_summary(params: Dict[str, Any]) -> Dict[str, Any]:
    now = datetime.utcnow()
    since = (now - timedelta(days=30)).isoformat()
    until = now.isoformat()

    revenue = _gather_revenue_data(since, until)
    products = _gather_product_data()
    affiliates = _gather_affiliate_data()
    newsletter = _gather_newsletter_data()
    funnels = _gather_funnel_data()
    seo = _gather_seo_data()

    return {
        "report_type": "executive_summary",
        "period": {"since": since, "until": until},
        "revenue": revenue,
        "products": products,
        "affiliates": affiliates,
        "newsletter": newsletter,
        "funnels": funnels,
        "seo": seo,
        "generated_at": now.isoformat(),
    }


def _assemble_revenue_report(params: Dict[str, Any]) -> Dict[str, Any]:
    days = params.get("days", 30)
    now = datetime.utcnow()
    since = (now - timedelta(days=days)).isoformat()
    until = now.isoformat()
    return {
        "report_type": "revenue_report",
        "period": {"since": since, "until": until, "days": days},
        **_gather_revenue_data(since, until),
        "products": _gather_product_data(),
        "affiliates": _gather_affiliate_data(),
        "generated_at": now.isoformat(),
    }


def _assemble_newsletter_health(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_type": "newsletter_health",
        **_gather_newsletter_data(),
        "generated_at": datetime.utcnow().isoformat(),
    }


def _assemble_seo_audit(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_type": "seo_audit",
        **_gather_seo_data(),
        "generated_at": datetime.utcnow().isoformat(),
    }


def _assemble_agent_activity(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_type": "agent_activity",
        **_gather_agent_data(),
        "generated_at": datetime.utcnow().isoformat(),
    }


def _assemble_funnel_report(params: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_type": "funnel_report",
        **_gather_funnel_data(),
        "generated_at": datetime.utcnow().isoformat(),
    }


_ASSEMBLERS = {
    "executive_summary": _assemble_executive_summary,
    "revenue_report": _assemble_revenue_report,
    "newsletter_health": _assemble_newsletter_health,
    "seo_audit": _assemble_seo_audit,
    "agent_activity": _assemble_agent_activity,
    "funnel_report": _assemble_funnel_report,
}


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _render_markdown(data: Dict[str, Any], title: str) -> str:
    lines = [f"# {title}", f"*Generated: {data.get('generated_at', '')}*", ""]

    def _section(header: str, content: Any) -> None:
        lines.append(f"## {header}")
        lines.append("")
        if isinstance(content, dict):
            for k, v in content.items():
                if isinstance(v, (dict, list)):
                    lines.append(f"**{k}:** _(see below)_")
                else:
                    lines.append(f"**{k}:** {v}")
        elif isinstance(content, list):
            for item in content[:20]:
                if isinstance(item, dict):
                    lines.append("- " + ", ".join(f"{k}: {v}" for k, v in item.items()
                                                   if not isinstance(v, (dict, list))))
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(str(content))
        lines.append("")

    rt = data.get("report_type", "")

    if rt == "executive_summary":
        rev = data.get("revenue", {})
        kpi = rev.get("kpi", {})
        _section("Revenue KPIs (Last 30 Days)", kpi)

        prod = data.get("products", {})
        catalog = prod.get("catalog", {})
        revenue_sum = prod.get("revenue", {})
        lines.append("## Product Catalog")
        lines.append("")
        lines.append(f"- Total products: **{catalog.get('total', 0)}**")
        lines.append(f"- Active: **{catalog.get('active', 0)}**")
        if isinstance(revenue_sum, dict):
            lines.append(f"- Last 30 days revenue: **${revenue_sum.get('last_30_days', 0):.2f}**")
        lines.append("")

        aff = data.get("affiliates", {})
        _section("Affiliate Summary", aff)

        nl = data.get("newsletter", {}).get("summary", {})
        _section("Newsletter Health", nl)

        seo = data.get("seo", {}).get("overview", {})
        _section("SEO Overview", seo)

    elif rt == "revenue_report":
        period = data.get("period", {})
        lines.append(f"> Period: {period.get('since', '')[:10]} → {period.get('until', '')[:10]}")
        lines.append("")
        kpi = data.get("kpi", {})
        _section("KPIs", kpi)
        channels = data.get("channels", [])
        _section("Revenue by Channel", channels)
        prod = data.get("products", {})
        best = prod.get("best_sellers", [])
        _section("Best-Selling Products", best)

    elif rt == "newsletter_health":
        summary = data.get("summary", {})
        _section("Subscriber Summary", summary)
        growth = data.get("growth", [])
        _section("Growth Trend (Last 4 Weeks)", growth)

    elif rt == "seo_audit":
        overview = data.get("overview", {})
        _section("Site Overview", overview)
        needs = data.get("needs_work", [])
        _section("Pages Needing Optimization", needs)

    elif rt == "agent_activity":
        logs = data.get("recent_logs", [])
        _section("Recent Agent Logs (Last 50)", logs)

    elif rt == "funnel_report":
        funnels = data.get("best_funnels", [])
        _section("Best-Performing Funnels", funnels)

    else:
        # Generic dump
        for k, v in data.items():
            if k not in ("report_type", "generated_at"):
                _section(k.replace("_", " ").title(), v)

    return "\n".join(lines)


def _render_html(data: Dict[str, Any], title: str) -> str:
    md = _render_markdown(data, title)
    rows = []
    for line in md.splitlines():
        if line.startswith("# "):
            rows.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            rows.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("- "):
            rows.append(f"<li>{line[2:]}</li>")
        elif line.startswith("**") and ":**" in line:
            rows.append(f"<p><strong>{line}</strong></p>")
        elif line.startswith("*") and line.endswith("*"):
            rows.append(f"<em>{line[1:-1]}</em>")
        elif line.startswith(">"):
            rows.append(f"<blockquote>{line[1:].strip()}</blockquote>")
        elif line:
            rows.append(f"<p>{line}</p>")
    body = "\n".join(rows)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 40px auto;
          background: #0d1117; color: #c9d1d9; padding: 0 20px; }}
  h1   {{ color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 8px; }}
  h2   {{ color: #3fb950; margin-top: 32px; }}
  li   {{ margin: 4px 0; }}
  blockquote {{ border-left: 3px solid #58a6ff; padding-left: 12px; color: #8b949e; }}
  p    {{ line-height: 1.6; }}
  strong {{ color: #f0f6fc; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def _render_text(data: Dict[str, Any], title: str) -> str:
    md = _render_markdown(data, title)
    clean = []
    for line in md.splitlines():
        line = line.replace("**", "").replace("*", "").replace("#", "").strip()
        clean.append(line)
    return "\n".join(clean)


_RENDERERS = {
    "markdown": _render_markdown,
    "html": _render_html,
    "text": _render_text,
    "json": lambda data, _: json.dumps(data, default=str, indent=2),
}


# ---------------------------------------------------------------------------
# Report execution
# ---------------------------------------------------------------------------

def run_report(
    report_name: str,
    output_format: Optional[str] = None,
    extra_params: Optional[Dict[str, Any]] = None,
    save_to_file: bool = True,
    output_dir: Optional[str] = None,
) -> ReportRun:
    """Execute a report and store the result."""
    _ensure()
    rdef = get_report_definition(report_name)
    if not rdef:
        raise ValueError(f"Report '{report_name}' not found")

    fmt = output_format or rdef.output_format
    params = {**rdef.parameters, **(extra_params or {})}

    now = datetime.utcnow().isoformat()
    run_ref = f"RUN-{uuid.uuid4().hex[:12].upper()}"

    conn = _db()
    try:
        cur = conn.execute(
            """INSERT INTO report_runs
               (report_id, run_ref, status, output_format, started_at, parameters)
               VALUES (?,?,?,?,?,?)""",
            (rdef.id, run_ref, "running", fmt, now, json.dumps(params)),
        )
        run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()

    try:
        assembler = _ASSEMBLERS.get(rdef.report_type, _assemble_executive_summary)
        data = assembler(params)

        renderer = _RENDERERS.get(fmt, _render_markdown)
        content = renderer(data, report_name.replace("_", " ").title())

        file_path = ""
        if save_to_file:
            import os
            base = output_dir or "output/reports"
            os.makedirs(base, exist_ok=True)
            ext = {"markdown": "md", "html": "html", "text": "txt", "json": "json"}.get(fmt, "txt")
            ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            safe_name = report_name.replace(" ", "_").lower()
            file_path = os.path.join(base, f"{safe_name}_{ts}.{ext}")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        completed = datetime.utcnow().isoformat()
        conn = _db()
        try:
            conn.execute(
                """UPDATE report_runs
                   SET status = 'completed', content = ?, file_path = ?,
                       byte_size = ?, completed_at = ?
                   WHERE id = ?""",
                (content, file_path, len(content.encode()), completed, run_id),
            )
            # Update definition's last/next run
            next_run = _next_run_for(rdef.frequency)
            conn.execute(
                """UPDATE report_definitions
                   SET last_run_at = ?, next_run_at = ?, updated_at = ?
                   WHERE id = ?""",
                (completed, next_run, completed, rdef.id),
            )
            conn.commit()
        finally:
            conn.close()

        return ReportRun(
            id=run_id,
            report_id=rdef.id,
            run_ref=run_ref,
            status="completed",
            output_format=fmt,
            content=content,
            file_path=file_path,
            byte_size=len(content.encode()),
            started_at=now,
            completed_at=completed,
            parameters=params,
        )

    except Exception as exc:
        err = str(exc)
        conn = _db()
        try:
            conn.execute(
                "UPDATE report_runs SET status = 'failed', error_message = ?, completed_at = ? WHERE id = ?",
                (err, datetime.utcnow().isoformat(), run_id),
            )
            conn.commit()
        finally:
            conn.close()
        return ReportRun(
            id=run_id,
            report_id=rdef.id,
            run_ref=run_ref,
            status="failed",
            error_message=err,
            started_at=now,
        )


def run_adhoc_report(
    report_type: str,
    output_format: str = "markdown",
    params: Optional[Dict[str, Any]] = None,
    title: str = "",
) -> str:
    """Generate a report immediately without a saved definition. Returns content string."""
    _ensure()
    assembler = _ASSEMBLERS.get(report_type, _assemble_executive_summary)
    data = assembler(params or {})
    renderer = _RENDERERS.get(output_format, _render_markdown)
    label = title or report_type.replace("_", " ").title()
    return renderer(data, label)


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------

def get_run_history(
    report_name: Optional[str] = None,
    limit: int = 20,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List recent report runs."""
    _ensure()
    conn = _db()
    try:
        clauses = []
        params: List[Any] = []
        if report_name:
            row = conn.execute(
                "SELECT id FROM report_definitions WHERE name = ?", (report_name,)
            ).fetchone()
            if row:
                clauses.append("r.report_id = ?")
                params.append(row["id"])
        if status:
            clauses.append("r.status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"""SELECT r.*, d.name as report_name
                FROM report_runs r
                LEFT JOIN report_definitions d ON d.id = r.report_id
                {where}
                ORDER BY r.started_at DESC LIMIT ?""",
            (*params, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "run_ref": r["run_ref"],
                "report_name": r["report_name"],
                "status": r["status"],
                "output_format": r["output_format"],
                "byte_size": r["byte_size"],
                "file_path": r["file_path"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "error_message": r["error_message"],
            }
            for r in rows
        ]
    finally:
        conn.close()


def get_run_content(run_ref: str) -> Optional[str]:
    """Retrieve the rendered content for a specific run."""
    _ensure()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT content FROM report_runs WHERE run_ref = ?", (run_ref,)
        ).fetchone()
        return row["content"] if row else None
    finally:
        conn.close()


def delete_run(run_id: int) -> bool:
    """Delete a report run and its sections."""
    _ensure()
    conn = _db()
    try:
        conn.execute("DELETE FROM report_runs WHERE id = ?", (run_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Metrics cache
# ---------------------------------------------------------------------------

def cache_metric(key: str, value: Any, ttl_seconds: int = 3600) -> None:
    """Cache a metric value with a TTL."""
    _ensure()
    now = datetime.utcnow()
    expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO report_metrics_cache (metric_key, value_json, cached_at, expires_at)
               VALUES (?,?,?,?)
               ON CONFLICT(metric_key) DO UPDATE SET
                 value_json = excluded.value_json,
                 cached_at  = excluded.cached_at,
                 expires_at = excluded.expires_at""",
            (key, json.dumps(value, default=str), now.isoformat(), expires),
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_metric(key: str) -> Optional[Any]:
    """Return cached metric if not expired."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        row = conn.execute(
            "SELECT value_json, expires_at FROM report_metrics_cache WHERE metric_key = ?",
            (key,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] < now:
            conn.execute("DELETE FROM report_metrics_cache WHERE metric_key = ?", (key,))
            conn.commit()
            return None
        return json.loads(row["value_json"])
    finally:
        conn.close()


def purge_expired_cache() -> int:
    """Remove expired cache entries. Returns number deleted."""
    _ensure()
    now = datetime.utcnow().isoformat()
    conn = _db()
    try:
        cur = conn.execute(
            "DELETE FROM report_metrics_cache WHERE expires_at < ?", (now,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reporting summary
# ---------------------------------------------------------------------------

def reporting_summary() -> Dict[str, Any]:
    """High-level status of all defined reports."""
    _ensure()
    conn = _db()
    try:
        def_rows = conn.execute(
            """SELECT name, report_type, frequency, is_active,
                      next_run_at, last_run_at
               FROM report_definitions ORDER BY name"""
        ).fetchall()
        run_row = conn.execute(
            """SELECT
                   COUNT(*) as total,
                   SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as completed,
                   SUM(CASE WHEN status = 'failed'    THEN 1 ELSE 0 END) as failed
               FROM report_runs"""
        ).fetchone()
        due = conn.execute(
            """SELECT COUNT(*) as cnt FROM report_definitions
               WHERE is_active = 1 AND (next_run_at IS NULL OR next_run_at <= ?)""",
            (datetime.utcnow().isoformat(),),
        ).fetchone()
        return {
            "definitions": [dict(r) for r in def_rows],
            "runs": {
                "total": run_row["total"] or 0,
                "completed": run_row["completed"] or 0,
                "failed": run_row["failed"] or 0,
            },
            "due_now": due["cnt"] or 0,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Default reports seed
# ---------------------------------------------------------------------------

DEFAULT_REPORTS = [
    {
        "name": "weekly_executive_summary",
        "report_type": "executive_summary",
        "description": "Weekly high-level org performance review",
        "output_format": "markdown",
        "frequency": "weekly",
    },
    {
        "name": "daily_revenue_report",
        "report_type": "revenue_report",
        "description": "Daily revenue and sales breakdown",
        "output_format": "markdown",
        "frequency": "daily",
        "parameters": {"days": 1},
    },
    {
        "name": "monthly_newsletter_health",
        "report_type": "newsletter_health",
        "description": "Monthly newsletter subscriber health check",
        "output_format": "markdown",
        "frequency": "monthly",
    },
    {
        "name": "weekly_seo_audit",
        "report_type": "seo_audit",
        "description": "Weekly SEO performance overview",
        "output_format": "markdown",
        "frequency": "weekly",
    },
    {
        "name": "daily_agent_activity",
        "report_type": "agent_activity",
        "description": "Daily agent log digest",
        "output_format": "text",
        "frequency": "daily",
    },
]


def seed_default_reports() -> List[str]:
    """Create the default set of reports if they don't exist yet."""
    created = []
    for cfg in DEFAULT_REPORTS:
        try:
            create_report_definition(**cfg)
            created.append(cfg["name"])
        except ValueError:
            pass
    return created


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

@tool("rpt_create_report", "Register a new scheduled report definition")
def create_report_tool(
    name: str,
    report_type: str = "executive_summary",
    frequency: str = "weekly",
    output_format: str = "markdown",
    description: str = "",
) -> str:
    try:
        rdef = create_report_definition(
            name=name,
            report_type=report_type,
            frequency=frequency,
            output_format=output_format,
            description=description,
        )
        return json.dumps({"ok": True, "report": rdef.to_dict()})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("rpt_run_report", "Execute a report immediately by name")
def run_report_tool(report_name: str, output_format: str = "") -> str:
    try:
        run = run_report(
            report_name=report_name,
            output_format=output_format or None,
        )
        preview = run.content[:800] if run.content else ""
        return json.dumps({
            "ok": run.status == "completed",
            "run_ref": run.run_ref,
            "status": run.status,
            "file_path": run.file_path,
            "byte_size": run.byte_size,
            "preview": preview,
            "error": run.error_message,
        })
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("rpt_adhoc_report", "Generate a one-off report without saving a definition")
def adhoc_report_tool(
    report_type: str = "executive_summary",
    output_format: str = "markdown",
    title: str = "",
) -> str:
    try:
        content = run_adhoc_report(
            report_type=report_type,
            output_format=output_format,
            title=title,
        )
        return json.dumps({"ok": True, "content": content[:2000]})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})


@tool("rpt_list_reports", "List all defined reports and their schedule")
def list_reports_tool() -> str:
    try:
        return json.dumps(reporting_summary(), default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("rpt_run_history", "Get recent report execution history")
def run_history_tool(report_name: str = "", limit: int = 20) -> str:
    try:
        history = get_run_history(report_name=report_name or None, limit=limit)
        return json.dumps(history, default=str)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("rpt_get_content", "Retrieve the full rendered content of a past report run")
def get_content_tool(run_ref: str) -> str:
    try:
        content = get_run_content(run_ref)
        if content is None:
            return json.dumps({"error": "Run not found"})
        return json.dumps({"ok": True, "content": content})
    except Exception as exc:
        return json.dumps({"error": str(exc)})


@tool("rpt_seed_defaults", "Create the default set of scheduled reports")
def seed_defaults_tool() -> str:
    try:
        created = seed_default_reports()
        return json.dumps({"ok": True, "created": created})
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)})
