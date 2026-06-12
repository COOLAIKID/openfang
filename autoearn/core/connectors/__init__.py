"""Connector package — outbound integrations with external platforms.

Importing this package registers every connector. Use :func:`get` to fetch one by
name and :func:`all_connectors` to enumerate them (e.g. for the dashboard or the
``connectors`` tool).
"""
from __future__ import annotations

from .base import Connector, ConnectorResult, all_connectors, get, register

from . import blogging  # noqa: E402,F401
from . import commerce  # noqa: E402,F401
from . import social  # noqa: E402,F401

__all__ = [
    "Connector",
    "ConnectorResult",
    "get",
    "all_connectors",
    "register",
    "configured",
]


def configured() -> list[dict]:
    """Describe every connector and whether it is currently configured."""
    return [c.describe() for c in all_connectors()]
