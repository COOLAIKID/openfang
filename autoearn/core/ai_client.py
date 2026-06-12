"""Cascading multi-provider AI client.

A single :func:`ask` call tries each configured free provider in priority order
and falls back to local Ollama if every cloud provider fails or is unconfigured.
Agents may express a ``model_preference`` like ``"groq/llama-3.3-70b-versatile"``
or ``"ollama/mistral"``; the matching provider is tried first, then the normal
cascade fills in behind it.

Every provider is imported lazily so a missing optional dependency only disables
that one provider instead of breaking the whole process.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from . import config

# Default model per provider.
_DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-1.5-flash",
    "huggingface": "mistralai/Mistral-7B-Instruct-v0.3",
    "mistral": "mistral-small-latest",
    "ollama": None,  # filled from config at call time
}

# Cascade order (cloud first, local last).
_ORDER = ["groq", "gemini", "huggingface", "mistral", "ollama"]


@dataclass
class AIResult:
    text: str
    provider: str
    model: str

    def __str__(self) -> str:  # convenient for callers that just want text
        return self.text


class AIError(RuntimeError):
    """Raised only when every provider in the cascade fails."""


# --------------------------------------------------------------------------
# Public entrypoint
# --------------------------------------------------------------------------
def ask(prompt: str, system: str = "", model_preference: str | None = None) -> AIResult:
    """Return a completion, cascading across providers until one succeeds."""
    order = _build_order(model_preference)
    errors: list[str] = []

    for provider, model in order:
        fn = _PROVIDERS.get(provider)
        if fn is None:
            continue
        try:
            text = fn(prompt, system, model)
            if text and text.strip():
                return AIResult(text=text.strip(), provider=provider, model=model)
            errors.append(f"{provider}: empty response")
        except Exception as exc:  # noqa: BLE001 - we want to fall through on anything
            errors.append(f"{provider}: {exc}")

    raise AIError("all providers failed -> " + " | ".join(errors))


def ask_json(prompt: str, system: str = "", model_preference: str | None = None) -> dict:
    """Ask and parse the reply as JSON, tolerating ```json fenced blocks."""
    result = ask(prompt, system, model_preference)
    return _extract_json(result.text)


# --------------------------------------------------------------------------
# Ordering
# --------------------------------------------------------------------------
def _build_order(preference: str | None) -> list[tuple[str, str]]:
    order: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(provider: str, model: str | None) -> None:
        if provider in seen:
            return
        seen.add(provider)
        order.append((provider, model or _model_for(provider)))

    if preference:
        if "/" in preference:
            prov, _, mdl = preference.partition("/")
            add(prov.strip(), mdl.strip())
        else:
            add(preference.strip(), None)

    for provider in _ORDER:
        add(provider, None)
    return order


def _model_for(provider: str) -> str:
    if provider == "ollama":
        return config.get("ai", "ollama_model", "llama3")
    return _DEFAULT_MODELS.get(provider, "")


# --------------------------------------------------------------------------
# Providers — each returns plain text or raises.
# --------------------------------------------------------------------------
def _groq(prompt: str, system: str, model: str) -> str:
    key = config.get("ai", "groq_api_key")
    if not key:
        raise RuntimeError("no api key")
    from groq import Groq

    client = Groq(api_key=key)
    resp = client.chat.completions.create(
        model=model or _DEFAULT_MODELS["groq"],
        messages=_messages(system, prompt),
        temperature=0.7,
    )
    return resp.choices[0].message.content or ""


def _gemini(prompt: str, system: str, model: str) -> str:
    key = config.get("ai", "gemini_api_key")
    if not key:
        raise RuntimeError("no api key")
    import google.generativeai as genai

    genai.configure(api_key=key)
    gen_model = genai.GenerativeModel(
        model or _DEFAULT_MODELS["gemini"],
        system_instruction=system or None,
    )
    resp = gen_model.generate_content(prompt)
    return resp.text or ""


def _huggingface(prompt: str, system: str, model: str) -> str:
    key = config.get("ai", "huggingface_api_key")
    if not key:
        raise RuntimeError("no api key")
    from huggingface_hub import InferenceClient

    client = InferenceClient(token=key)
    resp = client.chat_completion(
        messages=_messages(system, prompt),
        model=model or _DEFAULT_MODELS["huggingface"],
        max_tokens=2048,
    )
    return resp.choices[0].message.content or ""


def _mistral(prompt: str, system: str, model: str) -> str:
    key = config.get("ai", "mistral_api_key")
    if not key:
        raise RuntimeError("no api key")
    import requests

    resp = requests.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model or _DEFAULT_MODELS["mistral"], "messages": _messages(system, prompt)},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def _ollama(prompt: str, system: str, model: str) -> str:
    import requests

    base = config.get("ai", "ollama_base_url", "http://localhost:11434").rstrip("/")
    mdl = model or config.get("ai", "ollama_model", "llama3")
    resp = requests.post(
        f"{base}/api/chat",
        json={"model": mdl, "messages": _messages(system, prompt), "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json().get("message", {}).get("content", "")


_PROVIDERS: dict[str, Callable[[str, str, str], str]] = {
    "groq": _groq,
    "gemini": _gemini,
    "huggingface": _huggingface,
    "mistral": _mistral,
    "ollama": _ollama,
}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _messages(system: str, prompt: str) -> list[dict[str, str]]:
    msgs: list[dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from an LLM reply."""
    text = text.strip()
    if text.startswith("```"):
        # strip ```json ... ``` fences
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


def available_providers() -> list[str]:
    """Which providers are actually configured (for the dashboard / diagnostics)."""
    out = []
    if config.get("ai", "groq_api_key"):
        out.append("groq")
    if config.get("ai", "gemini_api_key"):
        out.append("gemini")
    if config.get("ai", "huggingface_api_key"):
        out.append("huggingface")
    if config.get("ai", "mistral_api_key"):
        out.append("mistral")
    out.append("ollama")  # always available as local fallback (if running)
    return out
