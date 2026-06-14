"""Base classes for outbound connectors.

A *connector* is an integration with an external platform the organization can
publish to, sell through, or read from — WordPress, Ghost, Dev.to, Mastodon,
Discord, Gumroad, Shopify, Stripe, and so on. Each connector subclasses
:class:`Connector`, declares the ``config`` section it reads credentials from, and
implements one or more capability methods.

Connectors are intentionally tolerant: when unconfigured, :meth:`is_configured`
returns False and the agent-facing tools surface a readable "not configured"
message instead of raising — so the org simply routes around platforms you have
not set up.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import config


@dataclass
class ConnectorResult:
    ok: bool
    message: str
    url: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class Connector:
    #: unique id (e.g. ``"ghost"``)
    name: str = "base"
    #: human label
    label: str = "Base Connector"
    #: config.toml section to read credentials from
    config_section: str = ""
    #: which credential keys must be present for the connector to be usable
    required_keys: tuple[str, ...] = ()
    #: capabilities, e.g. ("publish",), ("post",), ("sell",)
    capabilities: tuple[str, ...] = ()

    # ------------------------------------------------------------------
    def cfg(self) -> dict[str, Any]:
        return config.section(self.config_section) if self.config_section else {}

    def is_configured(self) -> bool:
        c = self.cfg()
        return all(c.get(k) for k in self.required_keys)

    def require(self) -> dict[str, Any] | None:
        """Return config if configured, else None."""
        return self.cfg() if self.is_configured() else None

    def not_configured(self) -> ConnectorResult:
        keys = ", ".join(self.required_keys)
        return ConnectorResult(
            ok=False,
            message=f"{self.label} not configured (set [{self.config_section}] {keys}).",
        )

    # ------------------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "capabilities": list(self.capabilities),
            "configured": self.is_configured(),
        }


# Registry -----------------------------------------------------------------
_REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> Connector | None:
    cls = _REGISTRY.get(name)
    return cls() if cls else None


def all_connectors() -> list[Connector]:
    return [cls() for cls in _REGISTRY.values()]
