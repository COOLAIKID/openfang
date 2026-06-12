"""Shared base for the many providers that speak the OpenAI chat API.

A large number of free/cheap inference services (OpenRouter, Together, DeepSeek,
Fireworks, Perplexity, Groq, Mistral, local llama.cpp / LM Studio, …) expose the
exact same ``POST /v1/chat/completions`` contract. Rather than duplicate the HTTP
plumbing in each, they subclass :class:`OpenAICompatibleProvider` and only
declare their base URL, env/config key, and default model.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Completion, Message, NotConfigured, Provider, ProviderError


class OpenAICompatibleProvider(Provider):
    #: base URL up to and including ``/v1``
    base_url: str = ""
    #: config key under the ``[ai]`` section holding the API key
    config_key: str = ""
    #: whether an API key is required (local servers often need none)
    requires_key: bool = True
    timeout: int = 90

    # ------------------------------------------------------------------
    def api_key(self) -> str:
        return config.get("ai", self.config_key, "") if self.config_key else ""

    def is_configured(self) -> bool:
        if not self.requires_key:
            return True
        return bool(self.api_key())

    # ------------------------------------------------------------------
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def payload(self, messages: list[Message], model: str, **kwargs) -> dict:
        body = {
            "model": model,
            "messages": [m.as_dict() for m in messages],
            "temperature": kwargs.get("temperature", 0.7),
        }
        if "max_tokens" in kwargs:
            body["max_tokens"] = kwargs["max_tokens"]
        return body

    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    # ------------------------------------------------------------------
    def complete(self, messages: list[Message], model: str | None = None, **kwargs) -> Completion:
        if self.requires_key and not self.api_key():
            raise NotConfigured(f"{self.name}: no API key")
        mdl = model or self.default_model
        try:
            resp = requests.post(
                self.endpoint(),
                headers=self.headers(),
                json=self.payload(messages, mdl, **kwargs),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"{self.name}: request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage", {})
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(f"{self.name}: bad response: {exc}") from exc
        return Completion(
            text=text,
            provider=self.name,
            model=mdl,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            raw=data,
        )
