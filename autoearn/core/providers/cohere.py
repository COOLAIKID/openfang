"""Cohere provider (Chat v2 API).

Cohere offers a free trial tier. Its v2 chat endpoint is close to, but not
exactly, the OpenAI shape, so it gets its own module.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Completion, Message, NotConfigured, Provider, ProviderError, register


@register
class CohereProvider(Provider):
    name = "cohere"
    default_model = "command-r-08-2024"
    priority = 50

    def api_key(self) -> str:
        return config.get("ai", "cohere_api_key", "")

    def is_configured(self) -> bool:
        return bool(self.api_key())

    def complete(self, messages: list[Message], model: str | None = None, **kwargs) -> Completion:
        key = self.api_key()
        if not key:
            raise NotConfigured("cohere: no API key")
        mdl = model or self.default_model
        payload = {
            "model": mdl,
            "messages": [m.as_dict() for m in messages],
            "temperature": kwargs.get("temperature", 0.7),
        }
        try:
            resp = requests.post(
                "https://api.cohere.com/v2/chat",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=90,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"cohere: request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"cohere: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            text = "".join(part.get("text", "") for part in data["message"]["content"])
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"cohere: bad response: {exc}") from exc
        usage = data.get("usage", {}).get("tokens", {})
        return Completion(
            text=text,
            provider=self.name,
            model=mdl,
            prompt_tokens=int(usage.get("input_tokens", 0) or 0),
            completion_tokens=int(usage.get("output_tokens", 0) or 0),
            raw=data,
        )
