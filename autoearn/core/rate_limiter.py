"""Token-bucket and sliding-window rate limiters for external API calls.

Agents make many external API calls (LLMs, search, social, payment gateways).
Without rate limiting they quickly hit 429 errors. This module provides:

- :class:`TokenBucket` — classic token-bucket with configurable refill rate
- :class:`SlidingWindowCounter` — sliding-window counter (better burst control)
- :class:`RateLimiterRegistry` — global registry of named limiters, persisted to SQLite
- :func:`limit` decorator — wraps any function with a named rate limiter
- :func:`wait_for_capacity` — blocking wait until a limiter has capacity
- Predefined limiters for common APIs (Groq, Gemini, OpenAI, Reddit, Twitter…)
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
# Token Bucket
# ---------------------------------------------------------------------------

@dataclass
class TokenBucket:
    """Leaky-bucket rate limiter.

    Args:
        name: Identifier for this limiter.
        capacity: Max tokens (burst size).
        refill_rate: Tokens added per second.
    """

    name: str
    capacity: float
    refill_rate: float  # tokens per second
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now

    def consume(self, tokens: float = 1.0) -> bool:
        """Attempt to consume tokens. Returns True if successful."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def wait_and_consume(self, tokens: float = 1.0, timeout: float = 60.0) -> bool:
        """Block until tokens are available or timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.consume(tokens):
                return True
            needed = tokens - self._tokens
            wait_secs = needed / self.refill_rate
            time.sleep(min(wait_secs, 0.1))
        return False

    def available(self) -> float:
        """Current available tokens."""
        with self._lock:
            self._refill()
            return self._tokens

    def time_until_available(self, tokens: float = 1.0) -> float:
        """Seconds until enough tokens are available."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                return 0.0
            needed = tokens - self._tokens
            return needed / self.refill_rate

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": "token_bucket",
            "capacity": self.capacity,
            "refill_rate_per_second": self.refill_rate,
            "available": round(self.available(), 2),
        }


# ---------------------------------------------------------------------------
# Sliding Window Counter
# ---------------------------------------------------------------------------

class SlidingWindowCounter:
    """Sliding-window rate limiter.

    Tracks timestamps of the last N requests within a window.

    Args:
        name: Identifier.
        max_calls: Maximum calls allowed in the window.
        window_seconds: Duration of the sliding window.
    """

    def __init__(self, name: str, max_calls: int, window_seconds: float) -> None:
        self.name = name
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._timestamps: list[float] = []
        self._lock = threading.Lock()

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.window_seconds
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def consume(self) -> bool:
        """Try to consume one slot. Returns True if allowed."""
        with self._lock:
            self._prune()
            if len(self._timestamps) < self.max_calls:
                self._timestamps.append(time.monotonic())
                return True
            return False

    def wait_and_consume(self, timeout: float = 60.0) -> bool:
        """Block until a slot is available or timeout expires."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.consume():
                return True
            with self._lock:
                self._prune()
                if self._timestamps:
                    oldest = self._timestamps[0]
                    wait = (oldest + self.window_seconds) - time.monotonic()
                    time.sleep(min(max(wait, 0.0), 0.5))
                else:
                    time.sleep(0.05)
        return False

    def current_count(self) -> int:
        with self._lock:
            self._prune()
            return len(self._timestamps)

    def remaining(self) -> int:
        return max(0, self.max_calls - self.current_count())

    def time_until_available(self) -> float:
        with self._lock:
            self._prune()
            if len(self._timestamps) < self.max_calls:
                return 0.0
            oldest = self._timestamps[0]
            return max(0.0, (oldest + self.window_seconds) - time.monotonic())

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": "sliding_window",
            "max_calls": self.max_calls,
            "window_seconds": self.window_seconds,
            "current_count": self.current_count(),
            "remaining": self.remaining(),
        }


# ---------------------------------------------------------------------------
# Composite (bucket + window together)
# ---------------------------------------------------------------------------

class CompositeRateLimiter:
    """Combines a token bucket (burst) with a sliding window (sustained).

    Both must pass for a call to be allowed.
    """

    def __init__(self, name: str, bucket: TokenBucket, window: SlidingWindowCounter) -> None:
        self.name = name
        self.bucket = bucket
        self.window = window

    def consume(self) -> bool:
        if not self.window.consume():
            return False
        if not self.bucket.consume():
            return False
        return True

    def wait_and_consume(self, timeout: float = 60.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.consume():
                return True
            wait = max(self.bucket.time_until_available(), self.window.time_until_available())
            time.sleep(min(wait + 0.01, 1.0))
        return False

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": "composite",
            "bucket": self.bucket.describe(),
            "window": self.window.describe(),
        }


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, TokenBucket | SlidingWindowCounter | CompositeRateLimiter] = {}
_registry_lock = threading.Lock()


def register(limiter: TokenBucket | SlidingWindowCounter | CompositeRateLimiter) -> None:
    """Register a limiter by name."""
    with _registry_lock:
        _REGISTRY[limiter.name] = limiter


def get(name: str) -> TokenBucket | SlidingWindowCounter | CompositeRateLimiter | None:
    return _REGISTRY.get(name)


def get_or_create_bucket(name: str, capacity: float, refill_rate: float) -> TokenBucket:
    with _registry_lock:
        if name not in _REGISTRY:
            _REGISTRY[name] = TokenBucket(name, capacity, refill_rate)
        return _REGISTRY[name]  # type: ignore[return-value]


def get_or_create_window(name: str, max_calls: int, window_seconds: float) -> SlidingWindowCounter:
    with _registry_lock:
        if name not in _REGISTRY:
            _REGISTRY[name] = SlidingWindowCounter(name, max_calls, window_seconds)
        return _REGISTRY[name]  # type: ignore[return-value]


def list_limiters() -> list[dict[str, Any]]:
    with _registry_lock:
        return [lim.describe() for lim in _REGISTRY.values()]


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def limit(name: str, tokens: float = 1.0, timeout: float = 30.0) -> Callable:
    """Decorator: rate-limit a function using a named registered limiter.

    Usage::

        @limit("groq_api")
        def call_groq(prompt):
            ...
    """
    def decorator(fn: Callable) -> Callable:
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            lim = get(name)
            if lim is None:
                return fn(*args, **kwargs)
            if isinstance(lim, TokenBucket):
                allowed = lim.wait_and_consume(tokens, timeout)
            else:
                allowed = lim.wait_and_consume(timeout)
            if not allowed:
                raise RuntimeError(f"Rate limit exceeded for '{name}' (timeout {timeout}s)")
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper
    return decorator


def wait_for_capacity(name: str, tokens: float = 1.0, timeout: float = 30.0) -> bool:
    """Block until the named limiter has capacity. Returns False on timeout."""
    lim = get(name)
    if lim is None:
        return True
    if isinstance(lim, TokenBucket):
        return lim.wait_and_consume(tokens, timeout)
    return lim.wait_and_consume(timeout)


# ---------------------------------------------------------------------------
# Usage logging (SQLite)
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_schema() -> None:
    conn = _get_db()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_limit_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                ts       REAL    NOT NULL,
                limiter  TEXT    NOT NULL,
                agent    TEXT    NOT NULL DEFAULT '',
                allowed  INTEGER NOT NULL,
                waited_ms REAL   NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rll_ts ON rate_limit_log(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rll_limiter ON rate_limit_log(limiter)")
    conn.close()


_schema_init = False


def log_call(limiter_name: str, agent: str, allowed: bool, waited_ms: float = 0.0) -> None:
    """Record a rate-limit check in the database."""
    global _schema_init
    if not _schema_init:
        _init_schema()
        _schema_init = True
    conn = _get_db()
    with conn:
        conn.execute(
            "INSERT INTO rate_limit_log (ts, limiter, agent, allowed, waited_ms) VALUES (?,?,?,?,?)",
            (time.time(), limiter_name, agent, int(allowed), waited_ms),
        )
    conn.close()


def call_stats(limiter_name: str, last_minutes: int = 60) -> dict[str, Any]:
    """Return call stats for a limiter over the last N minutes."""
    cutoff = time.time() - last_minutes * 60
    conn = _get_db()
    rows = conn.execute(
        """SELECT allowed, COUNT(*) as cnt, AVG(waited_ms) as avg_wait
           FROM rate_limit_log
           WHERE limiter=? AND ts>=?
           GROUP BY allowed""",
        (limiter_name, cutoff),
    ).fetchall()
    conn.close()
    allowed = next((r["cnt"] for r in rows if r["allowed"]), 0)
    blocked = next((r["cnt"] for r in rows if not r["allowed"]), 0)
    avg_wait = next((r["avg_wait"] for r in rows if r["allowed"]), 0.0) or 0.0
    return {
        "limiter": limiter_name,
        "allowed": allowed,
        "blocked": blocked,
        "total": allowed + blocked,
        "block_rate": round(blocked / max(1, allowed + blocked), 3),
        "avg_wait_ms": round(avg_wait, 1),
        "window_minutes": last_minutes,
    }


# ---------------------------------------------------------------------------
# Pre-defined limiters for common APIs
# ---------------------------------------------------------------------------

def _setup_default_limiters() -> None:
    """Register sensible rate limiters for common APIs used by AutoEarn agents."""

    # Groq: 30 req/min on free tier
    register(SlidingWindowCounter("groq_api", max_calls=25, window_seconds=60))

    # Google Gemini: 60 req/min free
    register(SlidingWindowCounter("gemini_api", max_calls=55, window_seconds=60))

    # OpenAI / OpenAI-compat: 500 RPM (conservative)
    register(CompositeRateLimiter(
        "openai_api",
        bucket=TokenBucket("openai_burst", capacity=20, refill_rate=8.3),
        window=SlidingWindowCounter("openai_window", max_calls=490, window_seconds=60),
    ))

    # Hugging Face inference: 300 req/hour free
    register(SlidingWindowCounter("huggingface_api", max_calls=290, window_seconds=3600))

    # DuckDuckGo scraper: polite 1 req/sec
    register(TokenBucket("duckduckgo", capacity=3, refill_rate=1.0))

    # Reddit PRAW: 60 req/min OAuth
    register(SlidingWindowCounter("reddit_api", max_calls=55, window_seconds=60))

    # Twitter API v2: 500k tweets/month (roughly 333/min)
    register(SlidingWindowCounter("twitter_api", max_calls=300, window_seconds=60))

    # Telegram Bot API: 30 messages/second
    register(TokenBucket("telegram_bot", capacity=30, refill_rate=30))

    # Stripe: 100 req/sec
    register(TokenBucket("stripe_api", capacity=50, refill_rate=100))

    # SendGrid: 100 emails/sec
    register(TokenBucket("sendgrid_api", capacity=50, refill_rate=100))

    # Mailgun: 100 req/min
    register(SlidingWindowCounter("mailgun_api", max_calls=95, window_seconds=60))

    # CoinGecko free: 30 calls/min
    register(SlidingWindowCounter("coingecko_api", max_calls=28, window_seconds=60))

    # GitHub API: 5000 req/hour authenticated
    register(SlidingWindowCounter("github_api", max_calls=4990, window_seconds=3600))

    # WordPress REST API: internal, generous
    register(TokenBucket("wordpress_api", capacity=20, refill_rate=10))

    # Generic web scraper: polite 2 req/sec
    register(TokenBucket("web_scraper", capacity=5, refill_rate=2.0))

    # YouTube Data API v3: 10,000 units/day → ~7/min safe
    register(SlidingWindowCounter("youtube_api", max_calls=7, window_seconds=60))


_setup_default_limiters()


# ---------------------------------------------------------------------------
# Tool-friendly interface
# ---------------------------------------------------------------------------

def get_limiter_status() -> str:
    """JSON string of all limiter statuses — safe to return from an agent tool."""
    return json.dumps(list_limiters(), default=str)


def reset_limiter(name: str) -> str:
    """Reset a limiter to full capacity."""
    lim = get(name)
    if lim is None:
        return f"ERROR: no limiter '{name}'"
    if isinstance(lim, TokenBucket):
        with lim._lock:
            lim._tokens = lim.capacity
            lim._last_refill = time.monotonic()
    elif isinstance(lim, SlidingWindowCounter):
        with lim._lock:
            lim._timestamps.clear()
    elif isinstance(lim, CompositeRateLimiter):
        reset_limiter(lim.bucket.name)
        reset_limiter(lim.window.name)
    return f"Reset limiter '{name}'"


def pause_limiter(name: str, seconds: float = 10.0) -> str:
    """Drain a limiter (set tokens to 0) to pause outbound calls."""
    lim = get(name)
    if lim is None:
        return f"ERROR: no limiter '{name}'"
    if isinstance(lim, TokenBucket):
        with lim._lock:
            lim._tokens = 0.0
    elif isinstance(lim, SlidingWindowCounter):
        with lim._lock:
            now = time.monotonic()
            lim._timestamps = [now - lim.window_seconds + seconds + i * 0.001
                               for i in range(lim.max_calls)]
    return f"Paused limiter '{name}' for ~{seconds:.0f}s"
