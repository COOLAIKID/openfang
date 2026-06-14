"""Event bus — publish/subscribe system for inter-agent coordination.

Extends the basic :mod:`message_bus` with typed events, priority queues,
event filtering, replay, and async fan-out. Agents can subscribe to event
types and receive them on their next tick without coupling to specific
agent names.

Event flow::

    publisher → event_bus.publish(event) → subscribers[event_type]
              ↘ SQLite event_log (replay)
              ↘ optional webhooks (notify external systems)

Typical usage::

    # Subscribe to any "revenue" event
    subscribe("*", "cfo", lambda e: ...)  # wildcard
    subscribe("revenue.logged", "cfo", ...)
    subscribe("agent.error", "ceo", ...)

    # Publish from any agent or tool
    publish(Event(type="revenue.logged", data={"amount": 99.0, "source": "gumroad"}))

    # Consume pending events for an agent
    pending = consume("cfo")
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Event dataclass
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """An event published to the bus.

    Args:
        type: Dot-separated event type, e.g. ``"revenue.logged"``.
        data: Arbitrary JSON-serialisable payload.
        source: Name of the publishing agent.
        priority: Lower = higher priority (1–10). Default 5.
        correlation_id: Optional ID linking related events.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    priority: int = 5
    correlation_id: str = ""
    id: int = field(default=0, init=False)
    timestamp: float = field(default_factory=time.time, init=False)

    def to_row(self) -> tuple:
        return (
            self.type,
            json.dumps(self.data),
            self.source,
            self.priority,
            self.correlation_id,
            self.timestamp,
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        e = cls(
            type=row["type"],
            data=json.loads(row["data"] or "{}"),
            source=row["source"],
            priority=row["priority"],
            correlation_id=row["correlation_id"] or "",
        )
        e.id = row["id"]
        e.timestamp = row["timestamp"]
        return e

    def __str__(self) -> str:
        return f"Event({self.type!r}, src={self.source!r}, id={self.id})"


# ---------------------------------------------------------------------------
# Subscription record
# ---------------------------------------------------------------------------

@dataclass
class Subscription:
    """Records one agent's interest in an event type pattern.

    ``pattern`` supports:
    - Exact: ``"revenue.logged"``
    - Prefix wildcard: ``"revenue.*"``
    - Global wildcard: ``"*"``
    """

    agent: str
    pattern: str
    callback: Optional[Callable[[Event], None]] = field(default=None, repr=False)
    id: int = field(default=0, init=False)

    def matches(self, event_type: str) -> bool:
        if self.pattern == "*":
            return True
        if self.pattern.endswith(".*"):
            prefix = self.pattern[:-2]
            return event_type == prefix or event_type.startswith(prefix + ".")
        return self.pattern == event_type


# ---------------------------------------------------------------------------
# In-memory subscription registry
# ---------------------------------------------------------------------------

_subscriptions: list[Subscription] = []
_sub_lock = threading.Lock()
_sub_counter = 0


def subscribe(pattern: str, agent: str, callback: Callable[[Event], None] | None = None) -> int:
    """Register an interest in events matching ``pattern``.

    Returns subscription ID.
    """
    global _sub_counter
    with _sub_lock:
        _sub_counter += 1
        sub = Subscription(agent=agent, pattern=pattern, callback=callback)
        sub.id = _sub_counter
        _subscriptions.append(sub)
        return sub.id


def unsubscribe(subscription_id: int) -> bool:
    with _sub_lock:
        before = len(_subscriptions)
        _subscriptions[:] = [s for s in _subscriptions if s.id != subscription_id]
        return len(_subscriptions) < before


def subscriptions_for(agent: str) -> list[Subscription]:
    with _sub_lock:
        return [s for s in _subscriptions if s.agent == agent]


def all_subscriptions() -> list[dict[str, Any]]:
    with _sub_lock:
        return [{"id": s.id, "agent": s.agent, "pattern": s.pattern} for s in _subscriptions]


def _find_subscribers(event_type: str) -> list[Subscription]:
    with _sub_lock:
        return [s for s in _subscriptions if s.matches(event_type)]


# ---------------------------------------------------------------------------
# SQLite persistence
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
            CREATE TABLE IF NOT EXISTS event_log (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                type           TEXT    NOT NULL,
                data           TEXT    NOT NULL DEFAULT '{}',
                source         TEXT    NOT NULL DEFAULT 'system',
                priority       INTEGER NOT NULL DEFAULT 5,
                correlation_id TEXT    NOT NULL DEFAULT '',
                timestamp      REAL    NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS event_deliveries (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   INTEGER NOT NULL,
                agent      TEXT    NOT NULL,
                delivered  INTEGER NOT NULL DEFAULT 0,
                consumed   INTEGER NOT NULL DEFAULT 0,
                ts         REAL    NOT NULL,
                UNIQUE(event_id, agent)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_el_type ON event_log(type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_el_ts ON event_log(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ed_agent ON event_deliveries(agent, consumed)")
    conn.close()


_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


# ---------------------------------------------------------------------------
# Core publish/consume
# ---------------------------------------------------------------------------

def publish(event: Event) -> int:
    """Publish an event to the bus.

    1. Persists to ``event_log`` table.
    2. Creates delivery records for all matching subscribers.
    3. Invokes in-process callbacks (fire-and-forget, exceptions swallowed).

    Returns the assigned event ID.
    """
    _ensure_schema()
    conn = _get_db()
    with conn:
        cur = conn.execute(
            "INSERT INTO event_log (type, data, source, priority, correlation_id, timestamp) VALUES (?,?,?,?,?,?)",
            event.to_row(),
        )
        event.id = cur.lastrowid

        subscribers = _find_subscribers(event.type)
        for sub in subscribers:
            conn.execute(
                "INSERT OR IGNORE INTO event_deliveries (event_id, agent, delivered, consumed, ts) VALUES (?,?,1,0,?)",
                (event.id, sub.agent, time.time()),
            )
            if sub.callback:
                try:
                    sub.callback(event)
                except Exception:  # noqa: BLE001
                    pass
    conn.close()
    return event.id


def consume(agent: str, limit: int = 50) -> list[Event]:
    """Return unconsumed events for an agent and mark them consumed."""
    _ensure_schema()
    conn = _get_db()
    rows = conn.execute(
        """SELECT el.*, ed.id AS del_id
           FROM event_log el
           JOIN event_deliveries ed ON el.id = ed.event_id
           WHERE ed.agent=? AND ed.consumed=0
           ORDER BY el.priority ASC, el.timestamp ASC
           LIMIT ?""",
        (agent, limit),
    ).fetchall()

    events: list[Event] = []
    delivery_ids: list[int] = []
    for row in rows:
        events.append(Event.from_row(row))
        delivery_ids.append(row["del_id"])

    if delivery_ids:
        placeholders = ",".join("?" * len(delivery_ids))
        with conn:
            conn.execute(
                f"UPDATE event_deliveries SET consumed=1 WHERE id IN ({placeholders})",
                delivery_ids,
            )
    conn.close()
    return events


def peek(agent: str, limit: int = 10) -> list[Event]:
    """Return unconsumed events without marking them consumed."""
    _ensure_schema()
    conn = _get_db()
    rows = conn.execute(
        """SELECT el.*
           FROM event_log el
           JOIN event_deliveries ed ON el.id = ed.event_id
           WHERE ed.agent=? AND ed.consumed=0
           ORDER BY el.priority ASC, el.timestamp ASC
           LIMIT ?""",
        (agent, limit),
    ).fetchall()
    conn.close()
    return [Event.from_row(r) for r in rows]


def pending_count(agent: str) -> int:
    """Number of unconsumed events waiting for an agent."""
    _ensure_schema()
    conn = _get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM event_deliveries WHERE agent=? AND consumed=0",
        (agent,),
    ).fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Replay and history
# ---------------------------------------------------------------------------

def replay(event_type: str | None = None, since: float | None = None,
           limit: int = 100) -> list[Event]:
    """Replay events from the log for audit or re-delivery."""
    _ensure_schema()
    conn = _get_db()
    conditions = []
    params: list[Any] = []
    if event_type:
        if event_type.endswith("*"):
            conditions.append("type LIKE ?")
            params.append(event_type.replace("*", "%"))
        else:
            conditions.append("type=?")
            params.append(event_type)
    if since:
        conditions.append("timestamp>=?")
        params.append(since)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT * FROM event_log {where} ORDER BY timestamp DESC LIMIT ?", params
    ).fetchall()
    conn.close()
    return [Event.from_row(r) for r in rows]


def recent_events(limit: int = 50) -> list[dict[str, Any]]:
    """Recent events as plain dicts for dashboard display."""
    _ensure_schema()
    conn = _get_db()
    rows = conn.execute(
        "SELECT id, type, source, priority, timestamp FROM event_log ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def event_stats() -> dict[str, Any]:
    """Aggregate stats by event type."""
    _ensure_schema()
    conn = _get_db()
    by_type = conn.execute(
        "SELECT type, COUNT(*) as cnt FROM event_log GROUP BY type ORDER BY cnt DESC LIMIT 20"
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM event_deliveries WHERE consumed=0").fetchone()[0]
    conn.close()
    return {
        "total_events": total,
        "pending_deliveries": pending,
        "by_type": [{"type": r["type"], "count": r["cnt"]} for r in by_type],
        "active_subscriptions": len(_subscriptions),
    }


# ---------------------------------------------------------------------------
# Convenience publishers
# ---------------------------------------------------------------------------

def emit_revenue(agent: str, amount: float, source: str, note: str = "") -> int:
    return publish(Event(
        type="revenue.logged",
        data={"amount": amount, "source": source, "note": note},
        source=agent,
        priority=2,
    ))


def emit_error(agent: str, error: str, context: str = "") -> int:
    return publish(Event(
        type="agent.error",
        data={"error": error, "context": context},
        source=agent,
        priority=1,
    ))


def emit_output_ready(agent: str, output_path: str, output_type: str = "content") -> int:
    return publish(Event(
        type=f"output.ready.{output_type}",
        data={"path": output_path, "output_type": output_type},
        source=agent,
        priority=4,
    ))


def emit_directive(from_agent: str, to_team: str, directive: str, budget_usd: float = 0.0) -> int:
    return publish(Event(
        type="council.directive",
        data={"to_team": to_team, "directive": directive, "budget_usd": budget_usd},
        source=from_agent,
        priority=2,
    ))


def emit_qc_result(agent: str, output_ref: str, passed: bool, score: float, feedback: str = "") -> int:
    status = "approved" if passed else "rejected"
    return publish(Event(
        type=f"qc.{status}",
        data={"output_ref": output_ref, "score": score, "feedback": feedback},
        source=agent,
        priority=3,
    ))


def emit_market_signal(agent: str, signal_type: str, asset: str, direction: str, strength: float) -> int:
    return publish(Event(
        type=f"market.signal.{signal_type}",
        data={"asset": asset, "direction": direction, "strength": strength},
        source=agent,
        priority=3,
    ))


# ---------------------------------------------------------------------------
# Tool-friendly wrappers
# ---------------------------------------------------------------------------

def publish_event(event_type: str, data: dict | None = None, source: str = "system",
                  priority: int = 5, correlation_id: str = "") -> str:
    """Publish an event from an agent tool. Returns event ID."""
    eid = publish(Event(
        type=event_type,
        data=data or {},
        source=source,
        priority=int(priority),
        correlation_id=correlation_id,
    ))
    return f"Published event #{eid} ({event_type})"


def consume_events(agent: str, limit: int = 20) -> str:
    """Consume and return pending events for an agent as JSON."""
    events = consume(agent, int(limit))
    return json.dumps([
        {"id": e.id, "type": e.type, "source": e.source, "data": e.data,
         "priority": e.priority, "timestamp": e.timestamp}
        for e in events
    ])


def get_event_stats() -> str:
    """JSON string of event bus statistics."""
    return json.dumps(event_stats())
