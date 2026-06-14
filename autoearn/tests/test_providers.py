"""Tests for the AI provider package and cascade ordering."""
from __future__ import annotations

from core import providers
from core.providers import Message
from core.providers.base import Completion, Provider, register


def test_all_providers_registered():
    names = {p.name for p in providers.instantiate_all()}
    # A representative sample of what should be present.
    for expected in ("groq", "gemini", "openrouter", "ollama", "anthropic", "cohere"):
        assert expected in names


def test_cascade_sorted_by_priority():
    cascade = providers.cascade()
    priorities = [p.priority for p in cascade]
    assert priorities == sorted(priorities)


def test_cascade_local_last():
    cascade = providers.cascade()
    # The very last provider should be a local one (the floor).
    assert cascade[-1].local is True


def test_preferred_provider_first():
    cascade = providers.cascade("together")
    assert cascade[0].name == "together"


def test_preferred_unknown_is_ignored_gracefully():
    cascade = providers.cascade("does-not-exist")
    # Still returns the full list, just without reordering.
    assert len(cascade) == len(providers.instantiate_all())


def test_message_as_dict():
    m = Message(role="user", content="hi")
    assert m.as_dict() == {"role": "user", "content": "hi"}


def test_completion_total_tokens():
    c = Completion(text="x", provider="p", model="m", prompt_tokens=3, completion_tokens=4)
    assert c.total_tokens == 7


def test_custom_provider_registration():
    @register
    class _Dummy(Provider):
        name = "dummy-test-provider"
        default_model = "d"
        priority = 999
        local = True

        def is_configured(self):
            return True

        def complete(self, messages, model=None, **kwargs):
            return Completion(text="ok", provider=self.name, model=self.default_model)

    names = {p.name for p in providers.instantiate_all()}
    assert "dummy-test-provider" in names
