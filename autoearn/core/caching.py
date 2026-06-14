"""HTTP response caching and in-memory LRU cache for AutoEarn agents.

External tool calls (web_search, fetch_url, crypto prices, stock data) are
expensive in both time and API quota. This module provides:

- :class:`DiskCache` — SQLite-backed persistent cache with TTL and tag eviction
- :class:`LRUCache` — in-memory least-recently-used cache with size limit
- :func:`cached_get` — convenience wrapper for requests.get with automatic caching
- :func:`cache_key` — deterministic cache key from URL + params
- Cache invalidation by tag, prefix, or TTL expiry
- Hit/miss statistics per cache name
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

_DB_PATH = Path(__file__).resolve().parent.parent / "autoearn.db"
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def cache_key(url: str, params: dict | None = None, extra: str = "") -> str:
    """Deterministic SHA-256 key from URL, query params, and extra discriminator."""
    raw = url.rstrip("/")
    if params:
        raw += "?" + urlencode(sorted(params.items()))
    if extra:
        raw += "|" + extra
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# Disk Cache (SQLite)
# ---------------------------------------------------------------------------

class DiskCache:
    """Persistent HTTP response cache backed by SQLite.

    Args:
        name: Logical cache namespace (used in stats).
        default_ttl: Seconds before a cached entry expires (default 1 hour).
        max_entries: Max rows before oldest entries are evicted.
    """

    def __init__(self, name: str = "default", default_ttl: float = 3600, max_entries: int = 10_000) -> None:
        self.name = name
        self.default_ttl = default_ttl
        self.max_entries = max_entries
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()
        self._init_schema()

    def _get_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        conn = self._get_db()
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS http_cache (
                    cache_key   TEXT    NOT NULL,
                    cache_name  TEXT    NOT NULL,
                    url         TEXT    NOT NULL DEFAULT '',
                    body        TEXT    NOT NULL,
                    status_code INTEGER NOT NULL DEFAULT 200,
                    headers     TEXT    NOT NULL DEFAULT '{}',
                    tags        TEXT    NOT NULL DEFAULT '[]',
                    created_at  REAL    NOT NULL,
                    expires_at  REAL    NOT NULL,
                    hit_count   INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (cache_key, cache_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_exp ON http_cache(expires_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hc_name ON http_cache(cache_name)")
        conn.close()

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """Retrieve a cached entry. Returns None on miss or expiry."""
        conn = self._get_db()
        row = conn.execute(
            "SELECT body, status_code, headers, hit_count FROM http_cache "
            "WHERE cache_key=? AND cache_name=? AND expires_at>?",
            (key, self.name, _now()),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE http_cache SET hit_count=hit_count+1 WHERE cache_key=? AND cache_name=?",
                (key, self.name),
            )
            conn.commit()
            conn.close()
            with self._lock:
                self._hits += 1
            return {
                "body": row["body"],
                "status_code": row["status_code"],
                "headers": json.loads(row["headers"]),
                "hit": True,
            }
        conn.close()
        with self._lock:
            self._misses += 1
        return None

    def set(self, key: str, body: str, url: str = "", status_code: int = 200,
            headers: dict | None = None, ttl: float | None = None,
            tags: list[str] | None = None) -> None:
        """Store a response in the cache."""
        ttl = ttl if ttl is not None else self.default_ttl
        expires = _now() + ttl
        conn = self._get_db()
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO http_cache
                    (cache_key, cache_name, url, body, status_code, headers, tags, created_at, expires_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                key, self.name, url, body, status_code,
                json.dumps(headers or {}),
                json.dumps(tags or []),
                _now(), expires,
            ))
        conn.close()
        self._evict_old()

    def _evict_old(self) -> None:
        conn = self._get_db()
        with conn:
            conn.execute("DELETE FROM http_cache WHERE expires_at<=?", (_now(),))
            count = conn.execute(
                "SELECT COUNT(*) FROM http_cache WHERE cache_name=?", (self.name,)
            ).fetchone()[0]
            if count > self.max_entries:
                overage = count - self.max_entries
                conn.execute("""
                    DELETE FROM http_cache WHERE rowid IN (
                        SELECT rowid FROM http_cache WHERE cache_name=?
                        ORDER BY created_at ASC LIMIT ?
                    )
                """, (self.name, overage))
        conn.close()

    def invalidate(self, key: str) -> bool:
        """Remove a specific cache entry."""
        conn = self._get_db()
        with conn:
            r = conn.execute(
                "DELETE FROM http_cache WHERE cache_key=? AND cache_name=?",
                (key, self.name),
            )
        conn.close()
        return r.rowcount > 0

    def invalidate_by_tag(self, tag: str) -> int:
        """Remove all entries with a given tag."""
        conn = self._get_db()
        with conn:
            r = conn.execute(
                "DELETE FROM http_cache WHERE cache_name=? AND tags LIKE ?",
                (self.name, f'%"{tag}"%'),
            )
        conn.close()
        return r.rowcount

    def invalidate_by_prefix(self, url_prefix: str) -> int:
        """Remove all entries whose URL starts with a prefix."""
        conn = self._get_db()
        with conn:
            r = conn.execute(
                "DELETE FROM http_cache WHERE cache_name=? AND url LIKE ?",
                (self.name, f"{url_prefix}%"),
            )
        conn.close()
        return r.rowcount

    def clear(self) -> int:
        conn = self._get_db()
        with conn:
            r = conn.execute("DELETE FROM http_cache WHERE cache_name=?", (self.name,))
        conn.close()
        return r.rowcount

    def stats(self) -> dict[str, Any]:
        conn = self._get_db()
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(hit_count) as hits FROM http_cache WHERE cache_name=?",
            (self.name,),
        ).fetchone()
        conn.close()
        total_requests = self._hits + self._misses
        return {
            "name": self.name,
            "entries": row["total"] or 0,
            "total_hit_count": row["hits"] or 0,
            "session_hits": self._hits,
            "session_misses": self._misses,
            "hit_rate": round(self._hits / max(1, total_requests), 3),
        }


# ---------------------------------------------------------------------------
# In-memory LRU Cache
# ---------------------------------------------------------------------------

class LRUCache:
    """Thread-safe in-memory LRU cache.

    Useful for caching expensive pure-Python computations (NLP, hashing,
    price calculations) that don't need to survive restarts.

    Args:
        max_size: Maximum number of entries before LRU eviction.
        default_ttl: Optional TTL in seconds (None = no expiry).
    """

    def __init__(self, max_size: int = 1000, default_ttl: float | None = None) -> None:
        self.max_size = max_size
        self.default_ttl = default_ttl
        self._cache: OrderedDict[str, tuple[Any, float | None]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Any:
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            value, expires = self._cache[key]
            if expires is not None and time.monotonic() > expires:
                del self._cache[key]
                self._misses += 1
                return None
            self._cache.move_to_end(key)
            self._hits += 1
            return value

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        ttl = ttl if ttl is not None else self.default_ttl
        expires = time.monotonic() + ttl if ttl is not None else None
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (value, expires)
            while len(self._cache) > self.max_size:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": round(self._hits / max(1, total), 3),
            }


# ---------------------------------------------------------------------------
# Global caches
# ---------------------------------------------------------------------------

_DISK_CACHES: dict[str, DiskCache] = {}
_LRU_CACHES: dict[str, LRUCache] = {}
_global_lock = threading.Lock()


def get_disk_cache(name: str, default_ttl: float = 3600, max_entries: int = 10_000) -> DiskCache:
    with _global_lock:
        if name not in _DISK_CACHES:
            _DISK_CACHES[name] = DiskCache(name, default_ttl, max_entries)
        return _DISK_CACHES[name]


def get_lru_cache(name: str, max_size: int = 1000, default_ttl: float | None = None) -> LRUCache:
    with _global_lock:
        if name not in _LRU_CACHES:
            _LRU_CACHES[name] = LRUCache(max_size, default_ttl)
        return _LRU_CACHES[name]


# Pre-create standard caches
web_cache = get_disk_cache("web", default_ttl=3600)          # 1-hour web page cache
price_cache = get_disk_cache("prices", default_ttl=300)       # 5-min price cache
search_cache = get_disk_cache("search", default_ttl=1800)     # 30-min search cache
api_cache = get_disk_cache("api", default_ttl=600)            # 10-min API response cache
nlp_cache = get_lru_cache("nlp", max_size=500, default_ttl=3600)  # NLP computation cache


# ---------------------------------------------------------------------------
# Cached HTTP GET
# ---------------------------------------------------------------------------

def cached_get(url: str, params: dict | None = None, headers: dict | None = None,
               cache: DiskCache | None = None, ttl: float | None = None,
               tags: list[str] | None = None, timeout: int = 30) -> dict[str, Any]:
    """Perform a cached HTTP GET request.

    Returns a dict with ``body`` (str), ``status_code`` (int), ``hit`` (bool).
    Falls back to live request on cache miss.
    """
    import requests

    cache = cache or web_cache
    key = cache_key(url, params)
    cached = cache.get(key)
    if cached:
        return cached

    resp = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
    body = resp.text
    cache.set(key, body, url=url, status_code=resp.status_code,
              headers=dict(resp.headers), ttl=ttl, tags=tags)
    return {"body": body, "status_code": resp.status_code, "headers": dict(resp.headers), "hit": False}


def cached_get_json(url: str, params: dict | None = None, headers: dict | None = None,
                    cache: DiskCache | None = None, ttl: float | None = None) -> Any:
    """Like :func:`cached_get` but parses and returns JSON."""
    result = cached_get(url, params, headers, cache, ttl)
    return json.loads(result["body"])


# ---------------------------------------------------------------------------
# Cache management tools (for use from agent tools)
# ---------------------------------------------------------------------------

def cache_stats_all() -> str:
    """JSON string of stats for all active caches."""
    stats: list[dict[str, Any]] = []
    with _global_lock:
        for c in _DISK_CACHES.values():
            stats.append({"type": "disk", **c.stats()})
        for c in _LRU_CACHES.values():
            stats.append({"type": "lru", **c.stats()})
    return json.dumps(stats)


def clear_cache(name: str) -> str:
    """Clear a named cache. Returns count of entries removed."""
    with _global_lock:
        if name in _DISK_CACHES:
            count = _DISK_CACHES[name].clear()
            return f"Cleared {count} entries from disk cache '{name}'"
        if name in _LRU_CACHES:
            _LRU_CACHES[name].clear()
            return f"Cleared LRU cache '{name}'"
    return f"No cache named '{name}'"


def invalidate_url(url: str, cache_name: str = "web") -> str:
    """Invalidate cache entry for a specific URL."""
    key = cache_key(url)
    with _global_lock:
        c = _DISK_CACHES.get(cache_name)
    if c is None:
        return f"ERROR: no disk cache '{cache_name}'"
    ok = c.invalidate(key)
    return f"Invalidated '{url}' in cache '{cache_name}'" if ok else f"Not found in cache"


def warm_cache(urls: list[str], cache_name: str = "web", ttl: float = 3600) -> str:
    """Pre-warm a cache by fetching a list of URLs."""
    import requests

    cache = get_disk_cache(cache_name, default_ttl=ttl)
    results = []
    for url in urls[:20]:  # safety cap
        key = cache_key(url)
        if cache.get(key):
            results.append({"url": url, "status": "already_cached"})
            continue
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "AutoEarn/1.0"})
            cache.set(key, resp.text, url=url, status_code=resp.status_code, ttl=ttl)
            results.append({"url": url, "status": resp.status_code})
        except Exception as exc:  # noqa: BLE001
            results.append({"url": url, "status": f"ERROR: {exc}"})
    return json.dumps(results)
