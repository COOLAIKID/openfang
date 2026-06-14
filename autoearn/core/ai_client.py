"""Cascading multi-provider AI client.

A single :func:`ask` call tries each configured provider (see :mod:`core.providers`)
in priority order and falls back to a local provider if every cloud provider fails
or is unconfigured. Agents may express a ``model_preference`` like
``"groq/llama-3.3-70b-versatile"`` or ``"ollama/mistral"``; the matching provider
is tried first, then the normal cascade fills in behind it.

This module is the stable public surface (``ask``, ``ask_json``, ``AIResult``,
``AIError``, ``available_providers``); the provider implementations live in the
``providers`` package so each can be added/tested in isolation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import providers
from .providers import Message, NotConfigured, ProviderError


@dataclass
class AIResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __str__(self) -> str:
        return self.text


class AIError(RuntimeError):
    """Raised only when every provider in the cascade fails."""


# --------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------
def ask(prompt: str, system: str = "", model_preference: str | None = None) -> AIResult:
    """Return a completion, cascading across providers until one succeeds."""
    preferred, model = _split_preference(model_preference)
    messages = _messages(system, prompt)
    errors: list[str] = []

    for provider in providers.cascade(preferred):
        chosen_model = model if (preferred and provider.name == preferred and model) else None
        try:
            completion = provider.complete(messages, model=chosen_model)
        except NotConfigured:
            continue  # silently skip providers without credentials
        except ProviderError as exc:
            errors.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - never let one provider break the cascade
            errors.append(f"{provider.name}: {exc}")
            continue
        if completion.text and completion.text.strip():
            return AIResult(
                text=completion.text.strip(),
                provider=completion.provider,
                model=completion.model,
                prompt_tokens=completion.prompt_tokens,
                completion_tokens=completion.completion_tokens,
            )
        errors.append(f"{provider.name}: empty response")

    raise AIError("all providers failed -> " + " | ".join(errors) if errors else "no provider configured")


def ask_json(prompt: str, system: str = "", model_preference: str | None = None) -> dict:
    """Ask and parse the reply as JSON, tolerating ```json fenced blocks."""
    result = ask(prompt, system, model_preference)
    return extract_json(result.text)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _split_preference(preference: str | None) -> tuple[str | None, str | None]:
    if not preference:
        return None, None
    if "/" in preference:
        prov, _, mdl = preference.partition("/")
        return prov.strip() or None, mdl.strip() or None
    return preference.strip() or None, None


def _messages(system: str, prompt: str) -> list[Message]:
    msgs: list[Message] = []
    if system:
        msgs.append(Message(role="system", content=system))
    msgs.append(Message(role="user", content=prompt))
    return msgs


def extract_json(text: str) -> dict:
    """Best-effort JSON extraction from an LLM reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {}


# Backwards-compatible alias kept for callers/tests that imported the private name.
_extract_json = extract_json


def available_providers() -> list[str]:
    """Which providers are actually configured/reachable (for the dashboard)."""
    names = providers.configured_names()
    # Ensure at least the local floor shows even if not currently reachable.
    if not names:
        return ["ollama"]
    return names
