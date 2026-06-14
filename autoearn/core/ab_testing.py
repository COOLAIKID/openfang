"""A/B testing framework for agent strategies, pricing, and content.

Agents often want to compare two approaches: headline variant A vs. B,
price $29 vs. $49, email subject line 1 vs. 2. This module provides a
statistically rigorous experiment runner backed by SQLite.

Key concepts:
- :class:`Experiment` — a named test with two variants (A/B or multi-armed)
- :class:`ExperimentResult` — tracks impressions, conversions, and revenue
- :func:`assign` — assign a participant to a variant (deterministic by ID)
- :func:`record_conversion` — log a conversion event for a participant
- :func:`analyze` — compute statistical significance, lift, winner
- :func:`auto_select_winner` — automatically switch to winner if significant

Usage::

    # Create experiment
    create_experiment("pricing_test", variants=["$29", "$49"],
                     hypothesis="$49 price converts equally well")

    # Each visitor gets assigned
    variant = assign("pricing_test", user_id="user_123")  # → "$29" or "$49"

    # Record conversion
    record_conversion("pricing_test", user_id="user_123", revenue=29.0)

    # Analyze after enough data
    result = analyze("pricing_test")
    # → {"winner": "$49", "lift": 0.23, "p_value": 0.031, "significant": True}
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Variant:
    """One variant in an experiment."""
    name: str
    weight: float = 1.0
    impressions: int = 0
    conversions: int = 0
    revenue: float = 0.0

    @property
    def conversion_rate(self) -> float:
        return self.conversions / max(1, self.impressions)

    @property
    def revenue_per_impression(self) -> float:
        return self.revenue / max(1, self.impressions)

    @property
    def average_order_value(self) -> float:
        return self.revenue / max(1, self.conversions)


@dataclass
class Experiment:
    """A named A/B test."""
    name: str
    variants: list[str]
    hypothesis: str = ""
    status: str = "running"  # running | paused | concluded
    winner: str = ""
    created_at: float = field(default_factory=time.time)
    concluded_at: float = 0.0
    min_sample_size: int = 100
    target_confidence: float = 0.95
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "variants": self.variants,
            "hypothesis": self.hypothesis,
            "status": self.status,
            "winner": self.winner,
            "created_at": self.created_at,
            "concluded_at": self.concluded_at,
            "min_sample_size": self.min_sample_size,
            "target_confidence": self.target_confidence,
            "metadata": self.metadata,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Experiment":
        meta = json.loads(row["metadata"] or "{}")
        return cls(
            name=row["name"],
            variants=json.loads(row["variants"]),
            hypothesis=row["hypothesis"] or "",
            status=row["status"],
            winner=row["winner"] or "",
            created_at=row["created_at"],
            concluded_at=row["concluded_at"] or 0.0,
            min_sample_size=row["min_sample_size"],
            target_confidence=row["target_confidence"],
            metadata=meta,
        )


# ---------------------------------------------------------------------------
# Database layer
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
            CREATE TABLE IF NOT EXISTS ab_experiments (
                name              TEXT PRIMARY KEY,
                variants          TEXT NOT NULL,
                hypothesis        TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'running',
                winner            TEXT NOT NULL DEFAULT '',
                created_at        REAL NOT NULL,
                concluded_at      REAL NOT NULL DEFAULT 0,
                min_sample_size   INTEGER NOT NULL DEFAULT 100,
                target_confidence REAL    NOT NULL DEFAULT 0.95,
                metadata          TEXT    NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ab_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                experiment  TEXT    NOT NULL,
                variant     TEXT    NOT NULL,
                participant TEXT    NOT NULL,
                event_type  TEXT    NOT NULL DEFAULT 'impression',
                revenue     REAL    NOT NULL DEFAULT 0.0,
                ts          REAL    NOT NULL,
                metadata    TEXT    NOT NULL DEFAULT '{}'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_abe_exp ON ab_events(experiment, variant)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_abe_part ON ab_events(experiment, participant)")
    conn.close()


_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        _init_schema()
        _schema_ready = True


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def create_experiment(
    name: str,
    variants: list[str],
    hypothesis: str = "",
    min_sample_size: int = 100,
    target_confidence: float = 0.95,
    metadata: dict | None = None,
) -> Experiment:
    """Create a new A/B experiment."""
    _ensure_schema()
    if len(variants) < 2:
        raise ValueError("Need at least 2 variants")
    exp = Experiment(
        name=name,
        variants=variants,
        hypothesis=hypothesis,
        min_sample_size=min_sample_size,
        target_confidence=target_confidence,
        metadata=metadata or {},
    )
    conn = _get_db()
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO ab_experiments
               (name, variants, hypothesis, status, winner, created_at, concluded_at,
                min_sample_size, target_confidence, metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (name, json.dumps(variants), hypothesis, "running", "",
             exp.created_at, 0.0, min_sample_size, target_confidence,
             json.dumps(metadata or {})),
        )
    conn.close()
    return exp


def get_experiment(name: str) -> Optional[Experiment]:
    _ensure_schema()
    conn = _get_db()
    row = conn.execute("SELECT * FROM ab_experiments WHERE name=?", (name,)).fetchone()
    conn.close()
    return Experiment.from_row(row) if row else None


def list_experiments() -> list[dict[str, Any]]:
    _ensure_schema()
    conn = _get_db()
    rows = conn.execute("SELECT * FROM ab_experiments ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def assign(experiment_name: str, participant_id: str) -> str:
    """Deterministically assign a participant to a variant.

    Uses consistent hashing so the same participant always gets the same
    variant within an experiment — no database lookup needed.
    """
    _ensure_schema()
    exp = get_experiment(experiment_name)
    if exp is None:
        raise ValueError(f"Experiment '{experiment_name}' not found")
    if exp.status == "concluded" and exp.winner:
        return exp.winner

    seed = f"{experiment_name}:{participant_id}"
    digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    idx = digest % len(exp.variants)
    variant = exp.variants[idx]

    _record_event(experiment_name, variant, participant_id, "impression")
    return variant


def record_conversion(
    experiment_name: str,
    participant_id: str,
    revenue: float = 0.0,
    metadata: dict | None = None,
) -> None:
    """Record a conversion event for a participant."""
    _ensure_schema()
    exp = get_experiment(experiment_name)
    if exp is None:
        return
    seed = f"{experiment_name}:{participant_id}"
    digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    variant = exp.variants[digest % len(exp.variants)]
    _record_event(experiment_name, variant, participant_id, "conversion",
                  revenue=revenue, metadata=metadata or {})


def _record_event(
    experiment: str, variant: str, participant: str,
    event_type: str, revenue: float = 0.0, metadata: dict | None = None,
) -> None:
    conn = _get_db()
    with conn:
        conn.execute(
            """INSERT INTO ab_events
               (experiment, variant, participant, event_type, revenue, ts, metadata)
               VALUES (?,?,?,?,?,?,?)""",
            (experiment, variant, participant, event_type, revenue,
             time.time(), json.dumps(metadata or {})),
        )
    conn.close()


def _get_variant_stats(experiment_name: str) -> list[Variant]:
    conn = _get_db()
    exp = get_experiment(experiment_name)
    if not exp:
        conn.close()
        return []
    stats: list[Variant] = []
    for vname in exp.variants:
        row = conn.execute(
            """SELECT
                   COUNT(CASE WHEN event_type='impression' THEN 1 END) as impressions,
                   COUNT(CASE WHEN event_type='conversion' THEN 1 END) as conversions,
                   COALESCE(SUM(CASE WHEN event_type='conversion' THEN revenue END), 0) as revenue
               FROM ab_events WHERE experiment=? AND variant=?""",
            (experiment_name, vname),
        ).fetchone()
        v = Variant(name=vname)
        v.impressions = row["impressions"] or 0
        v.conversions = row["conversions"] or 0
        v.revenue = row["revenue"] or 0.0
        stats.append(v)
    conn.close()
    return stats


# ---------------------------------------------------------------------------
# Statistical analysis
# ---------------------------------------------------------------------------

def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _z_test_two_proportions(
    n_a: int, conv_a: int, n_b: int, conv_b: int
) -> tuple[float, float]:
    """Two-proportion z-test. Returns (z_score, p_value)."""
    if n_a == 0 or n_b == 0:
        return 0.0, 1.0
    p_a = conv_a / n_a
    p_b = conv_b / n_b
    p_pool = (conv_a + conv_b) / (n_a + n_b)
    if p_pool == 0 or p_pool == 1:
        return 0.0, 1.0
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_a + 1 / n_b))
    if se == 0:
        return 0.0, 1.0
    z = (p_b - p_a) / se
    p_value = 2 * (1 - _normal_cdf(abs(z)))
    return z, p_value


def analyze(experiment_name: str) -> dict[str, Any]:
    """Compute full analysis for an experiment."""
    _ensure_schema()
    exp = get_experiment(experiment_name)
    if not exp:
        return {"error": f"Experiment '{experiment_name}' not found"}

    variants = _get_variant_stats(experiment_name)
    if not variants:
        return {"experiment": experiment_name, "status": "no_data", "variants": []}

    control = variants[0]
    results: list[dict[str, Any]] = []
    best_variant = control
    best_revenue = control.revenue_per_impression

    for v in variants:
        if v.revenue_per_impression > best_revenue:
            best_revenue = v.revenue_per_impression
            best_variant = v

        entry: dict[str, Any] = {
            "variant": v.name,
            "impressions": v.impressions,
            "conversions": v.conversions,
            "conversion_rate": round(v.conversion_rate, 4),
            "revenue": round(v.revenue, 2),
            "revenue_per_impression": round(v.revenue_per_impression, 4),
            "avg_order_value": round(v.average_order_value, 2),
        }

        if v.name != control.name:
            z, p = _z_test_two_proportions(
                control.impressions, control.conversions,
                v.impressions, v.conversions,
            )
            lift = (v.conversion_rate - control.conversion_rate) / max(control.conversion_rate, 0.0001)
            entry.update({
                "z_score": round(z, 3),
                "p_value": round(p, 4),
                "lift_vs_control": round(lift, 4),
                "significant": p < (1 - exp.target_confidence),
            })
        results.append(entry)

    min_impressions = min(v.impressions for v in variants)
    has_enough_data = min_impressions >= exp.min_sample_size

    # Find overall winner
    winner_candidate = best_variant.name
    is_significant = False
    if len(variants) == 2 and has_enough_data:
        a, b = variants[0], variants[1]
        _, p = _z_test_two_proportions(a.impressions, a.conversions, b.impressions, b.conversions)
        is_significant = p < (1 - exp.target_confidence)

    return {
        "experiment": experiment_name,
        "status": exp.status,
        "hypothesis": exp.hypothesis,
        "has_enough_data": has_enough_data,
        "winner_candidate": winner_candidate if is_significant else None,
        "significant": is_significant,
        "target_confidence": exp.target_confidence,
        "variants": results,
        "recommendation": (
            f"Switch to variant '{winner_candidate}'" if is_significant else
            f"Continue collecting data (need {exp.min_sample_size - min_impressions} more per variant)"
        ),
    }


def auto_select_winner(experiment_name: str) -> str:
    """If the experiment is significant, mark the winner and conclude the test."""
    _ensure_schema()
    result = analyze(experiment_name)
    if result.get("significant") and result.get("winner_candidate"):
        winner = result["winner_candidate"]
        conn = _get_db()
        with conn:
            conn.execute(
                "UPDATE ab_experiments SET status='concluded', winner=?, concluded_at=? WHERE name=?",
                (winner, time.time(), experiment_name),
            )
        conn.close()
        return f"Concluded '{experiment_name}' — winner: {winner}"
    return f"Not yet significant. {result.get('recommendation', 'Continue testing.')}"


def pause_experiment(name: str) -> str:
    _ensure_schema()
    conn = _get_db()
    with conn:
        conn.execute("UPDATE ab_experiments SET status='paused' WHERE name=?", (name,))
    conn.close()
    return f"Paused experiment '{name}'"


def resume_experiment(name: str) -> str:
    _ensure_schema()
    conn = _get_db()
    with conn:
        conn.execute("UPDATE ab_experiments SET status='running' WHERE name=?", (name,))
    conn.close()
    return f"Resumed experiment '{name}'"


def delete_experiment(name: str) -> str:
    _ensure_schema()
    conn = _get_db()
    with conn:
        conn.execute("DELETE FROM ab_experiments WHERE name=?", (name,))
        conn.execute("DELETE FROM ab_events WHERE experiment=?", (name,))
    conn.close()
    return f"Deleted experiment '{name}'"


def experiment_summary() -> str:
    """JSON summary of all experiments for dashboard display."""
    _ensure_schema()
    exps = list_experiments()
    summaries = []
    for exp_dict in exps:
        name = exp_dict["name"]
        variants = _get_variant_stats(name)
        total_impressions = sum(v.impressions for v in variants)
        total_conversions = sum(v.conversions for v in variants)
        total_revenue = sum(v.revenue for v in variants)
        summaries.append({
            "name": name,
            "status": exp_dict["status"],
            "variants": exp_dict["variants"],
            "winner": exp_dict["winner"],
            "total_impressions": total_impressions,
            "total_conversions": total_conversions,
            "total_revenue": round(total_revenue, 2),
        })
    return json.dumps(summaries)


# ---------------------------------------------------------------------------
# Tool-friendly wrappers
# ---------------------------------------------------------------------------

def create_experiment_tool(
    name: str, variants: list | None = None, hypothesis: str = "",
    min_sample_size: int = 100,
) -> str:
    try:
        exp = create_experiment(name, variants or ["A", "B"], hypothesis, min_sample_size)
        return f"Created experiment '{exp.name}' with variants: {exp.variants}"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def assign_tool(experiment_name: str, participant_id: str) -> str:
    try:
        variant = assign(experiment_name, participant_id)
        return f"Assigned participant '{participant_id}' to variant '{variant}'"
    except Exception as exc:  # noqa: BLE001
        return f"ERROR: {exc}"


def analyze_tool(experiment_name: str) -> str:
    return json.dumps(analyze(experiment_name))


def conclude_experiment_tool(experiment_name: str) -> str:
    return auto_select_winner(experiment_name)
