"""Tests for the pure-python toolkit helpers (no network)."""
from __future__ import annotations

import json

from core.toolkit import content, finance, files


# -- content ----------------------------------------------------------------
def test_word_count():
    assert content.word_count("one two three") == 3


def test_slugify():
    assert content.slugify("Hello, World! 2026") == "hello-world-2026"


def test_reading_time_mentions_words():
    out = content.reading_time("word " * 440)
    assert "min read" in out


def test_keyword_density_returns_json():
    data = json.loads(content.keyword_density("apple apple banana apple banana"))
    assert data[0]["word"] == "apple"
    assert data[0]["count"] == 3


def test_flesch_runs():
    out = content.flesch_reading_ease("This is a simple sentence. It has small words.")
    assert "Flesch" in out


def test_meta_description_truncates():
    desc = content.meta_description("word " * 100, max_len=50)
    assert len(desc) <= 51


# -- finance ----------------------------------------------------------------
def test_pct_change():
    assert finance.pct_change([100, 110]) == 10.0
    assert finance.pct_change([]) == 0.0


def test_sma():
    assert finance.sma([1, 2, 3, 4], 2) == 3.5
    assert finance.sma([1], 5) == 0.0


def test_rsi_bounds():
    rising = list(range(1, 30))
    val = finance.rsi(rising)
    assert 0 <= val <= 100


def test_trend_signal_handles_short_series(monkeypatch):
    monkeypatch.setattr(finance, "crypto_market_chart", lambda coin, days: [1.0, 2.0])
    out = json.loads(finance.trend_signal("bitcoin"))
    assert out["signal"] == "hold"


def test_trend_signal_buy(monkeypatch):
    # Steadily rising series -> short SMA above long SMA -> buy.
    series = [float(i) for i in range(1, 30)]
    monkeypatch.setattr(finance, "crypto_market_chart", lambda coin, days: series)
    out = json.loads(finance.trend_signal("bitcoin", 14))
    assert out["signal"] in {"buy", "hold"}


# -- files ------------------------------------------------------------------
def test_files_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(files, "OUTPUT", tmp_path / "out")
    files.save_text("articles/test.md", "# hi")
    assert "hi" in files.read_text("articles/test.md")
    listing = files.list_output()
    assert "articles/test.md" in listing


def test_files_path_traversal_blocked(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(files, "OUTPUT", tmp_path / "out")
    # Any path escaping output/ must raise PathError (dispatch turns this into a
    # readable ERROR string for agents).
    with pytest.raises(files.PathError):
        files._resolve("../../etc/passwd")
    with pytest.raises(files.PathError):
        files.read_text("../escape.txt")
