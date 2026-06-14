"""Configuration loader.

Reads config.toml once and exposes it as a plain dict. Environment variables
override file values for any AI key (handy for `GROQ_API_KEY=... python main.py`).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - fallback for 3.10
    import tomli as tomllib  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"

# Environment variable -> (section, key) overrides.
_ENV_OVERRIDES = {
    "GROQ_API_KEY": ("ai", "groq_api_key"),
    "GEMINI_API_KEY": ("ai", "gemini_api_key"),
    "GOOGLE_API_KEY": ("ai", "gemini_api_key"),
    "HUGGINGFACE_API_KEY": ("ai", "huggingface_api_key"),
    "HF_TOKEN": ("ai", "huggingface_api_key"),
    "MISTRAL_API_KEY": ("ai", "mistral_api_key"),
}

_cache: dict[str, Any] | None = None


def load() -> dict[str, Any]:
    """Load (and memoize) the config dict, applying env overrides."""
    global _cache
    if _cache is not None:
        return _cache

    data: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as fh:
            data = tomllib.load(fh)

    for env_key, (section, key) in _ENV_OVERRIDES.items():
        val = os.environ.get(env_key)
        if val:
            data.setdefault(section, {})[key] = val

    _cache = data
    return data


def section(name: str) -> dict[str, Any]:
    """Return a config section, or an empty dict if absent."""
    return load().get(name, {})


def get(section_name: str, key: str, default: Any = None) -> Any:
    return section(section_name).get(key, default)


def cfg(dotted_key: str, fallback: Any = None) -> Any:
    """Look up a dotted config key (e.g. 'analytics.db_path'), returning fallback if absent."""
    parts = dotted_key.split(".", 1)
    if len(parts) == 2:
        sec, key = parts
        return section(sec).get(key, fallback)
    return load().get(parts[0], fallback)
