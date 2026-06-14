"""Shared pytest fixtures.

Each test gets an isolated, temporary SQLite database so tests never touch the
real ``autoearn.db`` and don't interfere with one another.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Point the database module at a throwaway file and re-init it."""
    from core import database as db

    test_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", test_path)
    monkeypatch.setattr(db, "_initialized", False)
    db.init()
    yield db


@pytest.fixture()
def clean_registry():
    """Reload tools so the registry is fresh for tests that inspect it."""
    from core import tools

    importlib.reload(tools)
    return tools
