"""AI provider package.

Importing this package registers every provider (each module decorates its
classes with ``@register``). :func:`cascade` returns provider instances ordered
for fallback: explicitly preferred provider first, then by priority, guaranteeing
a local provider sits at the end as a floor.
"""
from __future__ import annotations

from .base import (
    Completion,
    Message,
    NotConfigured,
    Provider,
    ProviderError,
    all_provider_classes,
    get_provider_class,
    instantiate_all,
    register,
)

# Import side-effect: register all concrete providers.
from . import anthropic  # noqa: E402,F401
from . import cloud_openai_compat  # noqa: E402,F401
from . import cohere  # noqa: E402,F401
from . import gemini  # noqa: E402,F401
from . import huggingface  # noqa: E402,F401
from . import local  # noqa: E402,F401

__all__ = [
    "Completion",
    "Message",
    "NotConfigured",
    "Provider",
    "ProviderError",
    "cascade",
    "instantiate_all",
    "get_provider_class",
    "all_provider_classes",
    "register",
    "configured_names",
]


def cascade(preferred: str | None = None) -> list[Provider]:
    """Return providers ordered for the fallback cascade."""
    providers = instantiate_all()  # already sorted by priority
    if not preferred:
        return providers
    ordered: list[Provider] = []
    for p in providers:
        if p.name == preferred:
            ordered.insert(0, p)
        else:
            ordered.append(p)
    return ordered


def configured_names() -> list[str]:
    """Names of providers that are currently usable."""
    return [p.name for p in instantiate_all() if p._safe_configured()]
