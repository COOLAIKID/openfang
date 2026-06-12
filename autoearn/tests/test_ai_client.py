"""Tests for the AI client cascade and JSON extraction."""
from __future__ import annotations

import pytest

from core import ai_client
from core.providers.base import Completion


def test_extract_json_plain():
    assert ai_client.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "```json\n{\"x\": [1,2,3]}\n```"
    assert ai_client.extract_json(text) == {"x": [1, 2, 3]}


def test_extract_json_embedded():
    text = "Here you go: {\"ok\": true} cheers"
    assert ai_client.extract_json(text) == {"ok": True}


def test_extract_json_garbage_returns_empty():
    assert ai_client.extract_json("not json at all") == {}


def test_split_preference():
    assert ai_client._split_preference("groq/llama-3.3") == ("groq", "llama-3.3")
    assert ai_client._split_preference("ollama") == ("ollama", None)
    assert ai_client._split_preference(None) == (None, None)


def test_ask_uses_first_working_provider(monkeypatch):
    from core import providers

    class Boom:
        name = "boom"

        def complete(self, *a, **k):
            raise providers.ProviderError("boom down")

    class Good:
        name = "good"

        def complete(self, messages, model=None, **k):
            return Completion(text="hello world", provider="good", model="m")

    monkeypatch.setattr(providers, "cascade", lambda preferred=None: [Boom(), Good()])
    result = ai_client.ask("hi")
    assert result.text == "hello world"
    assert result.provider == "good"


def test_ask_raises_when_all_fail(monkeypatch):
    from core import providers

    class Boom:
        name = "boom"

        def complete(self, *a, **k):
            raise providers.ProviderError("down")

    monkeypatch.setattr(providers, "cascade", lambda preferred=None: [Boom()])
    with pytest.raises(ai_client.AIError):
        ai_client.ask("hi")
