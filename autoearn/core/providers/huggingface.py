"""Hugging Face Inference API provider.

Uses the router's OpenAI-compatible chat endpoint when possible. Hundreds of open
models are available on the free inference tier.
"""
from __future__ import annotations

from .base import register
from .openai_compatible import OpenAICompatibleProvider


@register
class HuggingFaceProvider(OpenAICompatibleProvider):
    name = "huggingface"
    base_url = "https://router.huggingface.co/v1"
    config_key = "huggingface_api_key"
    default_model = "meta-llama/Llama-3.1-8B-Instruct"
    priority = 55
