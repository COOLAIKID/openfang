"""Google Gemini provider.

Gemini has a generous free tier and uses its own request shape (not the OpenAI
one), so it gets a dedicated implementation. The system message is folded into a
``system_instruction`` and the remaining messages become ``contents``.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Completion, Message, NotConfigured, Provider, ProviderError, register


@register
class GeminiProvider(Provider):
    name = "gemini"
    default_model = "gemini-1.5-flash"
    priority = 15

    def api_key(self) -> str:
        return config.get("ai", "gemini_api_key", "")

    def is_configured(self) -> bool:
        return bool(self.api_key())

    def complete(self, messages: list[Message], model: str | None = None, **kwargs) -> Completion:
        key = self.api_key()
        if not key:
            raise NotConfigured("gemini: no API key")
        mdl = model or self.default_model

        system_parts = [m.content for m in messages if m.role == "system"]
        contents = []
        for m in messages:
            if m.role == "system":
                continue
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content}]})

        body: dict = {"contents": contents}
        if system_parts:
            body["system_instruction"] = {"parts": [{"text": "\n".join(system_parts)}]}

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{mdl}:generateContent?key={key}"
        try:
            resp = requests.post(url, json=body, timeout=90)
        except requests.RequestException as exc:
            raise ProviderError(f"gemini: request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"gemini: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"gemini: bad response: {exc}") from exc
        usage = data.get("usageMetadata", {})
        return Completion(
            text=text,
            provider=self.name,
            model=mdl,
            prompt_tokens=usage.get("promptTokenCount", 0),
            completion_tokens=usage.get("candidatesTokenCount", 0),
            raw=data,
        )
