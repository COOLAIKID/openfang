"""Local model providers — the last-resort fallback when every cloud is down.

Three flavors of local inference are supported:

* **Ollama** — native ``/api/chat`` endpoint.
* **LM Studio** — OpenAI-compatible server (default port 1234).
* **llama.cpp** — the ``llama-server`` OpenAI-compatible endpoint (port 8080).

These are tried last (highest priority numbers) and require no API key — only a
running local server. ``local = True`` marks them so the cascade always keeps at
least one of them as a floor.
"""
from __future__ import annotations

import requests

from .. import config
from .base import Completion, Message, Provider, ProviderError, register
from .openai_compatible import OpenAICompatibleProvider


@register
class OllamaProvider(Provider):
    name = "ollama"
    default_model = "llama3"
    priority = 200
    local = True

    def base_url(self) -> str:
        return config.get("ai", "ollama_base_url", "http://localhost:11434").rstrip("/")

    def is_configured(self) -> bool:
        try:
            requests.get(self.base_url() + "/api/tags", timeout=2)
            return True
        except requests.RequestException:
            return False

    def complete(self, messages: list[Message], model: str | None = None, **kwargs) -> Completion:
        mdl = model or config.get("ai", "ollama_model", self.default_model)
        try:
            resp = requests.post(
                self.base_url() + "/api/chat",
                json={"model": mdl, "messages": [m.as_dict() for m in messages], "stream": False},
                timeout=300,
            )
        except requests.RequestException as exc:
            raise ProviderError(f"ollama: request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise ProviderError(f"ollama: HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        return Completion(
            text=text,
            provider=self.name,
            model=mdl,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            raw=data,
        )


@register
class LMStudioProvider(OpenAICompatibleProvider):
    name = "lmstudio"
    config_key = ""
    requires_key = False
    default_model = "local-model"
    priority = 210
    local = True

    @property
    def base_url(self) -> str:  # type: ignore[override]
        return config.get("ai", "lmstudio_base_url", "http://localhost:1234/v1")

    def is_configured(self) -> bool:
        try:
            requests.get(self.base_url.rstrip("/") + "/models", timeout=2)
            return True
        except requests.RequestException:
            return False


@register
class LlamaCppProvider(OpenAICompatibleProvider):
    name = "llamacpp"
    config_key = ""
    requires_key = False
    default_model = "default"
    priority = 220
    local = True

    @property
    def base_url(self) -> str:  # type: ignore[override]
        return config.get("ai", "llamacpp_base_url", "http://localhost:8080/v1")

    def is_configured(self) -> bool:
        try:
            requests.get(self.base_url.rstrip("/") + "/models", timeout=2)
            return True
        except requests.RequestException:
            return False
