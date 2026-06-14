"""Base classes for AI providers.

Every provider implements :class:`Provider`, exposing a uniform ``complete``
method that takes a list of chat messages and returns text. Providers declare
their default model, whether they are currently configured (have credentials or,
for local providers, are reachable), and a priority used to order the cascade.

This package replaces the inline provider functions that used to live in
``ai_client``: each provider is now its own small, testable module, registered
through :func:`register`. Adding a new provider is a matter of dropping a file in
this directory and decorating its class with ``@register``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Message:
    """A single chat message."""

    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Completion:
    """The result of a provider call."""

    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class ProviderError(RuntimeError):
    """Raised by a provider when it cannot fulfil a request."""


class NotConfigured(ProviderError):
    """The provider has no credentials / is not reachable."""


class Provider:
    """Base class for all AI providers."""

    #: short, unique identifier (e.g. ``"groq"``)
    name: str = "base"
    #: default model id used when the caller does not specify one
    default_model: str = ""
    #: lower numbers are tried first in the cascade
    priority: int = 100
    #: whether this provider runs locally (used as a last-resort fallback)
    local: bool = False

    def is_configured(self) -> bool:
        """Return True if the provider can currently be used."""
        raise NotImplementedError

    def complete(self, messages: list[Message], model: str | None = None, **kwargs) -> Completion:
        """Run a chat completion. Raise :class:`ProviderError` on failure."""
        raise NotImplementedError

    # Convenience -----------------------------------------------------------
    def describe(self) -> dict[str, object]:
        return {
            "name": self.name,
            "default_model": self.default_model,
            "priority": self.priority,
            "local": self.local,
            "configured": self._safe_configured(),
        }

    def _safe_configured(self) -> bool:
        try:
            return self.is_configured()
        except Exception:  # noqa: BLE001
            return False


# Registry -----------------------------------------------------------------
_REGISTRY: dict[str, type[Provider]] = {}


def register(cls: type[Provider]) -> type[Provider]:
    """Class decorator that adds a provider to the global registry."""
    _REGISTRY[cls.name] = cls
    return cls


def all_provider_classes() -> list[type[Provider]]:
    return list(_REGISTRY.values())


def get_provider_class(name: str) -> type[Provider] | None:
    return _REGISTRY.get(name)


def instantiate_all() -> list[Provider]:
    """Instantiate every registered provider, sorted by priority."""
    instances = [cls() for cls in _REGISTRY.values()]
    instances.sort(key=lambda p: p.priority)
    return instances
