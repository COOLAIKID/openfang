"""Concrete OpenAI-compatible providers.

Each of these services implements the OpenAI chat-completions contract, so they
share :class:`OpenAICompatibleProvider` and only differ in base URL, config key,
default model and cascade priority. Many offer a free tier or free models, which
is exactly what AutoEarn wants.

Add a key for any of them under ``[ai]`` in ``config.toml`` (e.g.
``openrouter_api_key = "..."``) and it joins the cascade automatically.
"""
from __future__ import annotations

from .base import register
from .openai_compatible import OpenAICompatibleProvider


@register
class GroqProvider(OpenAICompatibleProvider):
    name = "groq"
    base_url = "https://api.groq.com/openai/v1"
    config_key = "groq_api_key"
    default_model = "llama-3.3-70b-versatile"
    priority = 10


@register
class OpenRouterProvider(OpenAICompatibleProvider):
    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    config_key = "openrouter_api_key"
    # OpenRouter hosts many free models suffixed ``:free``.
    default_model = "meta-llama/llama-3.3-70b-instruct:free"
    priority = 20


@register
class TogetherProvider(OpenAICompatibleProvider):
    name = "together"
    base_url = "https://api.together.xyz/v1"
    config_key = "together_api_key"
    default_model = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"
    priority = 25


@register
class DeepSeekProvider(OpenAICompatibleProvider):
    name = "deepseek"
    base_url = "https://api.deepseek.com/v1"
    config_key = "deepseek_api_key"
    default_model = "deepseek-chat"
    priority = 30


@register
class MistralProvider(OpenAICompatibleProvider):
    name = "mistral"
    base_url = "https://api.mistral.ai/v1"
    config_key = "mistral_api_key"
    default_model = "mistral-small-latest"
    priority = 35


@register
class FireworksProvider(OpenAICompatibleProvider):
    name = "fireworks"
    base_url = "https://api.fireworks.ai/inference/v1"
    config_key = "fireworks_api_key"
    default_model = "accounts/fireworks/models/llama-v3p1-8b-instruct"
    priority = 40


@register
class PerplexityProvider(OpenAICompatibleProvider):
    name = "perplexity"
    base_url = "https://api.perplexity.ai"
    config_key = "perplexity_api_key"
    default_model = "sonar"
    priority = 45


@register
class CerebrasProvider(OpenAICompatibleProvider):
    name = "cerebras"
    base_url = "https://api.cerebras.ai/v1"
    config_key = "cerebras_api_key"
    default_model = "llama3.1-8b"
    priority = 28


@register
class SambaNovaProvider(OpenAICompatibleProvider):
    name = "sambanova"
    base_url = "https://api.sambanova.ai/v1"
    config_key = "sambanova_api_key"
    default_model = "Meta-Llama-3.1-8B-Instruct"
    priority = 32


@register
class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    base_url = "https://api.openai.com/v1"
    config_key = "openai_api_key"
    default_model = "gpt-4o-mini"
    priority = 60
