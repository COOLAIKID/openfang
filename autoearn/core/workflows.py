"""
core/workflows.py — Multi-agent workflow engine for AutoEarn.

A Workflow is a named, ordered sequence of WorkflowSteps. Each step
addresses a message to a specific agent with a templated body. The engine
resolves templates against a context dict and dispatches messages via the
message bus, returning the list of sent message IDs.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from string import Template
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_db_conn: sqlite3.Connection | None = None


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is not None:
        return _db_conn
    with _lock:
        if _db_conn is not None:
            return _db_conn
        try:
            from core.database import DB_PATH
        except ImportError:
            DB_PATH = "/tmp/autoearn.db"
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        _init_schema(conn)
        _db_conn = conn
    return _db_conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS workflows (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            description TEXT    NOT NULL DEFAULT '',
            steps_json  TEXT    NOT NULL DEFAULT '[]',
            created_by  TEXT    NOT NULL DEFAULT 'system',
            created_at  TEXT    NOT NULL,
            updated_at  TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_workflows_name ON workflows (name);

        CREATE TABLE IF NOT EXISTS workflow_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow    TEXT    NOT NULL,
            trigger     TEXT    NOT NULL DEFAULT 'system',
            context     TEXT    NOT NULL DEFAULT '{}',
            message_ids TEXT    NOT NULL DEFAULT '[]',
            started_at  TEXT    NOT NULL,
            finished_at TEXT,
            status      TEXT    NOT NULL DEFAULT 'running'
        );
        CREATE INDEX IF NOT EXISTS idx_wf_runs_workflow
            ON workflow_runs (workflow, started_at DESC);
    """)
    conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WorkflowStep:
    """A single step in a workflow."""
    to_agent: str
    message_type: str
    subject: str
    body_template: str
    depends_on: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "to_agent": self.to_agent,
            "message_type": self.message_type,
            "subject": self.subject,
            "body_template": self.body_template,
            "depends_on": self.depends_on,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowStep":
        return cls(
            to_agent=d["to_agent"],
            message_type=d.get("message_type", "task"),
            subject=d.get("subject", ""),
            body_template=d.get("body_template", ""),
            depends_on=d.get("depends_on", []),
        )


@dataclass
class Workflow:
    """A named, ordered sequence of WorkflowSteps."""
    name: str
    steps: list[WorkflowStep]
    description: str = ""
    created_by: str = "system"
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Workflow":
        steps = [WorkflowStep.from_dict(s) for s in d.get("steps", [])]
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            steps=steps,
            created_by=d.get("created_by", "system"),
            created_at=d.get("created_at", _now()),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def save_workflow(workflow: Workflow) -> None:
    """Persist (upsert) a workflow to the database."""
    db = _get_db()
    steps_json = json.dumps([s.to_dict() for s in workflow.steps])
    ts = _now()
    with _lock:
        db.execute(
            """
            INSERT INTO workflows (name, description, steps_json, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description = excluded.description,
                steps_json  = excluded.steps_json,
                created_by  = excluded.created_by,
                updated_at  = excluded.updated_at
            """,
            (workflow.name, workflow.description, steps_json,
             workflow.created_by, workflow.created_at, ts),
        )
        db.commit()
    logger.info("workflow saved: %s (%d steps)", workflow.name, len(workflow.steps))


def load_workflow(name: str) -> Optional[Workflow]:
    """Load a workflow by name. Returns None if not found."""
    db = _get_db()
    row = db.execute(
        "SELECT name, description, steps_json, created_by, created_at FROM workflows WHERE name = ?",
        (name,),
    ).fetchone()
    if row is None:
        return None
    steps = [WorkflowStep.from_dict(s) for s in json.loads(row["steps_json"] or "[]")]
    return Workflow(
        name=row["name"],
        description=row["description"],
        steps=steps,
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def list_workflows() -> list[dict[str, Any]]:
    """Return metadata for all saved workflows (without full step details)."""
    db = _get_db()
    rows = db.execute(
        """
        SELECT name, description, created_by, created_at,
               json_array_length(steps_json) AS step_count
        FROM workflows
        ORDER BY name
        """
    ).fetchall()
    return [
        {
            "name": row["name"],
            "description": row["description"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "step_count": row["step_count"] or 0,
        }
        for row in rows
    ]


def delete_workflow(name: str) -> bool:
    """Delete a workflow by name. Returns True if deleted."""
    db = _get_db()
    with _lock:
        cursor = db.execute("DELETE FROM workflows WHERE name = ?", (name,))
        db.commit()
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Execution engine
# ---------------------------------------------------------------------------


def _render_template(template_str: str, context: dict[str, Any]) -> str:
    """
    Safely render a Python Template string against context.

    Falls back to the raw template on substitution error.
    """
    try:
        return Template(template_str).safe_substitute(context)
    except Exception:
        return template_str


def _send_message(
    to_agent: str,
    message_type: str,
    subject: str,
    body: str,
    from_agent: str = "system",
) -> Optional[str]:
    """
    Dispatch a message via core.message_bus.

    Returns the message ID or None on failure.
    """
    try:
        from core.message_bus import send  # type: ignore
        msg_id = send(
            to=to_agent,
            subject=subject,
            body=body,
            message_type=message_type,
            from_agent=from_agent,
        )
        return str(msg_id)
    except Exception as exc:
        logger.warning("workflow: failed to send message to %s: %s", to_agent, exc)
        return None


def run_workflow(
    name: str,
    context: Optional[dict[str, Any]] = None,
    trigger_agent: str = "system",
) -> list[str]:
    """
    Execute a saved workflow by name.

    Steps are executed in order. The body of each step's message is rendered
    against *context* using Python Template substitution (${key} syntax).

    Parameters
    ----------
    name:          Workflow name as saved in the DB.
    context:       Dict of variables available in template substitution.
    trigger_agent: Agent or user that triggered the run.

    Returns
    -------
    List of message IDs sent (may include None entries on send failures).
    """
    if context is None:
        context = {}
    workflow = load_workflow(name)
    if workflow is None:
        raise ValueError(f"Workflow '{name}' not found")

    db = _get_db()
    started_at = _now()
    with _lock:
        cursor = db.execute(
            """
            INSERT INTO workflow_runs (workflow, trigger, context, started_at, status)
            VALUES (?, ?, ?, ?, 'running')
            """,
            (name, trigger_agent, json.dumps(context), started_at),
        )
        run_id = cursor.lastrowid
        db.commit()

    message_ids: list[str] = []
    status = "completed"

    for step in workflow.steps:
        rendered_body = _render_template(step.body_template, context)
        rendered_subject = _render_template(step.subject, context)
        msg_id = _send_message(
            to_agent=step.to_agent,
            message_type=step.message_type,
            subject=rendered_subject,
            body=rendered_body,
            from_agent=trigger_agent,
        )
        if msg_id is not None:
            message_ids.append(msg_id)
            logger.info(
                "workflow '%s' step→%s msg_id=%s", name, step.to_agent, msg_id
            )
        else:
            logger.warning(
                "workflow '%s' step→%s: message not sent", name, step.to_agent
            )
            status = "partial"

    finished_at = _now()
    with _lock:
        db.execute(
            """
            UPDATE workflow_runs
            SET message_ids=?, finished_at=?, status=?
            WHERE id=?
            """,
            (json.dumps(message_ids), finished_at, status, run_id),
        )
        db.commit()

    logger.info(
        "workflow '%s' run complete: %d messages sent, status=%s",
        name, len(message_ids), status,
    )
    return message_ids


# ---------------------------------------------------------------------------
# Fluent builder
# ---------------------------------------------------------------------------


class WorkflowBuilder:
    """
    Fluent API for constructing Workflow objects.

    Usage::

        wf = (WorkflowBuilder()
              .step("researcher", "task", "Research topic: ${topic}", "Find info on ${topic}")
              .then("writer", "task", "Write article on ${topic}", "Write a 500-word article.")
              .build("my_workflow", "Example pipeline"))
        save_workflow(wf)
    """

    def __init__(self) -> None:
        self._steps: list[WorkflowStep] = []

    def step(
        self,
        to_agent: str,
        message_type: str,
        subject: str,
        body_template: str,
        depends_on: Optional[list[str]] = None,
    ) -> "WorkflowBuilder":
        """Add a step to the workflow."""
        self._steps.append(WorkflowStep(
            to_agent=to_agent,
            message_type=message_type,
            subject=subject,
            body_template=body_template,
            depends_on=depends_on or [],
        ))
        return self

    def then(
        self,
        to_agent: str,
        message_type: str,
        subject: str,
        body_template: str,
    ) -> "WorkflowBuilder":
        """Alias for step() — reads more naturally in chains."""
        return self.step(to_agent, message_type, subject, body_template)

    def build(
        self,
        name: str,
        description: str = "",
        created_by: str = "system",
    ) -> Workflow:
        """Finalise and return a Workflow object (does NOT save it)."""
        return Workflow(
            name=name,
            description=description,
            steps=list(self._steps),
            created_by=created_by,
        )


# ---------------------------------------------------------------------------
# Built-in starter workflows
# ---------------------------------------------------------------------------


def _content_pipeline() -> Workflow:
    return (
        WorkflowBuilder()
        .step("researcher", "task",
              "Research: ${topic}",
              "Please research the following topic thoroughly and return a structured brief:\n\nTopic: ${topic}\n\nFocus on: ${focus}")
        .then("writer", "task",
              "Write article: ${topic}",
              "Using the research brief provided, write a ${word_count} word article on: ${topic}\n\nTarget audience: ${audience}")
        .then("editor", "task",
              "Edit article: ${topic}",
              "Please review and edit the following article for quality, clarity and SEO:\n\nTopic: ${topic}\n\nEdit for: grammar, flow, keyword density for '${keyword}'")
        .then("qc", "review",
              "QC check: ${topic}",
              "Perform a final quality check on the content for topic '${topic}'.\nVerify: factual accuracy, plagiarism risk, brand voice, CTA presence.")
        .build(
            "content_pipeline",
            description="Researcher → Writer → Editor → QC: end-to-end content production pipeline",
            created_by="system",
        )
    )


def _market_signal() -> Workflow:
    return (
        WorkflowBuilder()
        .step("analyst", "task",
              "Market analysis: ${market}",
              "Analyse current market conditions for ${market}.\nProvide: trend summary, key signals, risk level (1-5), recommended action.")
        .then("trader", "task",
              "Trade decision: ${market}",
              "Based on the analyst's signals for ${market}, determine the optimal trade or action.\nBudget: ${budget_usd} USD. Risk tolerance: ${risk_level}.")
        .then("publisher", "task",
              "Publish signal: ${market}",
              "Publish the market signal for ${market} to the configured output channels.\nInclude: signal strength, confidence score, and disclaimer.")
        .then("qc", "review",
              "QC market signal: ${market}",
              "Review the published market signal for ${market} for accuracy and compliance.")
        .build(
            "market_signal",
            description="Analyst → Trader → Publisher → QC: market signal generation pipeline",
            created_by="system",
        )
    )


def _outreach_pipeline() -> Workflow:
    return (
        WorkflowBuilder()
        .step("scout", "task",
              "Scout prospects: ${niche}",
              "Find 10 qualified prospects in the '${niche}' niche.\nCriteria: ${criteria}\nOutput: name, URL, contact email, estimated audience size.")
        .then("proposer", "task",
              "Draft proposals: ${niche}",
              "Draft personalised outreach proposals for the prospects in '${niche}'.\nOffer: ${offer}\nTone: ${tone}\nMax length: 150 words each.")
        .then("closer", "task",
              "Send outreach: ${niche}",
              "Send the drafted proposals to the prospect list for '${niche}'.\nTrack opens and replies. Report back with delivery stats.")
        .then("qc", "review",
              "QC outreach: ${niche}",
              "Review the outreach results for '${niche}'.\nCheck: spam score, reply rate, conversion potential.")
        .build(
            "outreach_pipeline",
            description="Scout → Proposer → Closer → QC: automated outreach pipeline",
            created_by="system",
        )
    )


def seed_default_workflows() -> None:
    """
    Create the 3 built-in starter workflows if they don't already exist.

    Safe to call multiple times — uses INSERT OR IGNORE semantics.
    """
    for builder_fn in (_content_pipeline, _market_signal, _outreach_pipeline):
        wf = builder_fn()
        existing = load_workflow(wf.name)
        if existing is None:
            save_workflow(wf)
            logger.info("seeded default workflow: %s", wf.name)
        else:
            logger.debug("workflow already exists, skipping seed: %s", wf.name)


# ---------------------------------------------------------------------------
# Module self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    seed_default_workflows()
    print("Workflows:", [w["name"] for w in list_workflows()])
    wf = load_workflow("content_pipeline")
    if wf:
        print(f"content_pipeline has {len(wf.steps)} steps")
        for s in wf.steps:
            print(f"  → {s.to_agent}: {s.subject}")
