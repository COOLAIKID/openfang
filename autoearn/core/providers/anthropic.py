"""Anthropic (Claude) provider.

Uses the Messages API. System prompts are passed via the top-level ``system``
field rather than as a message, per the Anthropic contract. Included so the org
can use the most capable models when a key is available; it sits low in the
cascade since it is not free.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Completion, Message, NotConfigured, Provider, ProviderError, register

ANTHROPIC_VERSION = "2023-06-01"


@register
class AnthropicProvider(Provider):
    name = "anthropic"
    default_model = "claude-haiku-4-5-20251001"
    priority = 65

    def api_key(self) -> str:
        return config.get("ai", "anthropic_api_key", "")

    def is_configured(self) -> bool:
        return bool(self.api_key())

    def complete(self, messages: list[Message], model: str | None = None, **kwargs) -> Completion:
        key = self.api_key()
        if not key:
            raise NotConfigured("anthropic: no API key")
        mdl = model or self.default_model

        system = "\n".join(m.content for m in messages if m.role == "system")
        chat = [
            {"role": ("assistant" if m.role == "assistant" else "user"), "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        body: dict = {
            "model": mdl,
            "max_tokens": kwargs.get("max_tokens", 2048),
            "messages": chat,
        }
        if system:
            body["system"] = system

        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=120,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"anthropic: request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"anthropic: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            text = "".join(block.get("text", "") for block in data["content"])
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"anthropic: bad response: {exc}") from exc
        usage = data.get("usage", {})
        return Completion(
            text=text,
            provider=self.name,
            model=mdl,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            raw=data,
        )
