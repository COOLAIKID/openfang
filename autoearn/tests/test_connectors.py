"""Tests for the connector registry and not-configured behavior."""
from __future__ import annotations

from core import connectors


def test_connectors_registered():
    names = {c["name"] for c in connectors.configured()}
    for expected in ("wordpress", "ghost", "telegram", "gumroad", "stripe", "discord"):
        assert expected in names


def test_unconfigured_connector_returns_message(monkeypatch):
    from core import config

    monkeypatch.setattr(config, "section", lambda name: {})
    c = connectors.get("wordpress")
    result = c.publish("title", "body")
    assert result.ok is False
    assert "not configured" in result.message.lower()


def test_get_unknown_connector():
    assert connectors.get("nonexistent-xyz") is None


def test_capabilities_present():
    c = connectors.get("telegram")
    assert "post" in c.capabilities
    c2 = connectors.get("gumroad")
    assert "read_sales" in c2.capabilities


def test_describe_shape():
    desc = connectors.get("medium").describe()
    assert set(desc) >= {"name", "label", "capabilities", "configured"}
