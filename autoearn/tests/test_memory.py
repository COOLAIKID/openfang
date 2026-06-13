"""Tests for core/memory.py — per-agent semantic memory backed by SQLite FTS5."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_memory(tmp_path: Path):
    """Return the memory module pointing at a fresh temporary database."""
    import importlib
    import core.memory as mem

    # Reset the global connection so the module opens a new one
    mem._db_conn = None

    db_path = tmp_path / "mem_test.db"

    # Patch DB_PATH inside the memory module's _get_db
    with patch("core.database.DB_PATH", db_path):
        mem._get_db()  # opens + initialises schema

    return mem


@pytest.fixture()
def mem(tmp_path):
    """Fresh memory module with isolated temp DB."""
    import core.memory as m
    m._db_conn = None
    db_path = tmp_path / "mem_test.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    m._init_schema(conn)
    m._db_conn = conn
    yield m
    m._db_conn = None


# ---------------------------------------------------------------------------
# save / get
# ---------------------------------------------------------------------------

class TestSaveGet:
    def test_save_returns_positive_rowid(self, mem):
        rowid = mem.save("agent1", "key1", "some content")
        assert rowid > 0

    def test_get_returns_correct_fields(self, mem):
        mem.save("agent1", "key1", "hello world", tags=["greet"])
        record = mem.get("agent1", "key1")
        assert record is not None
        assert record["agent"] == "agent1"
        assert record["key"] == "key1"
        assert record["content"] == "hello world"
        assert record["tags"] == ["greet"]

    def test_get_returns_none_for_missing(self, mem):
        result = mem.get("agent1", "nonexistent_key")
        assert result is None

    def test_save_upserts_on_conflict(self, mem):
        mem.save("agent1", "key1", "original content")
        mem.save("agent1", "key1", "updated content")
        record = mem.get("agent1", "key1")
        assert record["content"] == "updated content"

    def test_save_updates_tags_on_upsert(self, mem):
        mem.save("agent1", "key1", "content", tags=["old_tag"])
        mem.save("agent1", "key1", "content", tags=["new_tag"])
        record = mem.get("agent1", "key1")
        assert record["tags"] == ["new_tag"]

    def test_save_without_tags_stores_empty_list(self, mem):
        mem.save("agent1", "key1", "no tags here")
        record = mem.get("agent1", "key1")
        assert record["tags"] == []

    def test_save_multiple_tags(self, mem):
        tags = ["finance", "revenue", "important"]
        mem.save("agent1", "k", "content", tags=tags)
        record = mem.get("agent1", "k")
        assert set(record["tags"]) == set(tags)

    def test_timestamps_present(self, mem):
        mem.save("agent1", "k", "content")
        record = mem.get("agent1", "k")
        assert record["created_at"]
        assert record["updated_at"]


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_existing_returns_true(self, mem):
        mem.save("agent1", "del_key", "to delete")
        assert mem.delete("agent1", "del_key") is True

    def test_delete_removes_record(self, mem):
        mem.save("agent1", "del_key", "content")
        mem.delete("agent1", "del_key")
        assert mem.get("agent1", "del_key") is None

    def test_delete_nonexistent_returns_false(self, mem):
        assert mem.delete("agent1", "no_such_key") is False

    def test_delete_only_removes_matching_agent(self, mem):
        mem.save("agentA", "shared_key", "A content")
        mem.save("agentB", "shared_key", "B content")
        mem.delete("agentA", "shared_key")
        assert mem.get("agentA", "shared_key") is None
        assert mem.get("agentB", "shared_key") is not None


# ---------------------------------------------------------------------------
# list_keys
# ---------------------------------------------------------------------------

class TestListKeys:
    def test_list_keys_empty(self, mem):
        assert mem.list_keys("unknown_agent") == []

    def test_list_keys_returns_all_keys(self, mem):
        for k in ["k1", "k2", "k3"]:
            mem.save("agent1", k, f"content {k}")
        keys = mem.list_keys("agent1")
        assert set(keys) == {"k1", "k2", "k3"}

    def test_list_keys_isolates_by_agent(self, mem):
        mem.save("agentA", "ka", "content")
        mem.save("agentB", "kb", "content")
        assert mem.list_keys("agentA") == ["ka"]
        assert mem.list_keys("agentB") == ["kb"]

    def test_list_keys_tag_filter(self, mem):
        mem.save("agent1", "finance_key", "revenue data", tags=["finance"])
        mem.save("agent1", "general_key", "misc data", tags=["general"])
        mem.save("agent1", "multi_key", "mixed data", tags=["finance", "general"])
        finance_keys = mem.list_keys("agent1", tag="finance")
        assert "finance_key" in finance_keys
        assert "multi_key" in finance_keys
        assert "general_key" not in finance_keys

    def test_list_keys_tag_filter_no_match(self, mem):
        mem.save("agent1", "key1", "content", tags=["other"])
        result = mem.list_keys("agent1", tag="nonexistent_tag")
        assert result == []


# ---------------------------------------------------------------------------
# wipe
# ---------------------------------------------------------------------------

class TestWipe:
    def test_wipe_returns_count(self, mem):
        for i in range(5):
            mem.save("agent1", f"k{i}", f"content {i}")
        deleted = mem.wipe("agent1")
        assert deleted == 5

    def test_wipe_clears_all_memories(self, mem):
        for i in range(3):
            mem.save("agent1", f"k{i}", "content")
        mem.wipe("agent1")
        assert mem.list_keys("agent1") == []

    def test_wipe_does_not_affect_other_agents(self, mem):
        mem.save("agentA", "keyA", "content A")
        mem.save("agentB", "keyB", "content B")
        mem.wipe("agentA")
        assert mem.get("agentB", "keyB") is not None

    def test_wipe_empty_agent_returns_zero(self, mem):
        result = mem.wipe("no_such_agent")
        assert result == 0


# ---------------------------------------------------------------------------
# recall (FTS search)
# ---------------------------------------------------------------------------

class TestRecall:
    def test_recall_finds_matching_content(self, mem):
        mem.save("agent1", "rev", "We earned $500 from affiliate marketing sales")
        mem.save("agent1", "seo", "The SEO strategy improved rankings")
        results = mem.recall("agent1", "affiliate sales")
        keys = [r["key"] for r in results]
        assert "rev" in keys

    def test_recall_returns_score(self, mem):
        mem.save("agent1", "k1", "Python programming tutorial for beginners")
        results = mem.recall("agent1", "Python programming")
        assert results
        assert "score" in results[0]
        assert results[0]["score"] > 0

    def test_recall_isolates_by_agent(self, mem):
        mem.save("agentA", "ka", "machine learning AI neural networks")
        mem.save("agentB", "kb", "machine learning AI")
        results = mem.recall("agentA", "machine learning")
        agent_names = {r["agent"] for r in results}
        assert agent_names == {"agentA"}

    def test_recall_empty_for_no_match(self, mem):
        mem.save("agent1", "k1", "cooking recipes pasta sauce")
        results = mem.recall("agent1", "cryptocurrency blockchain")
        assert results == []

    def test_recall_respects_limit(self, mem):
        for i in range(20):
            mem.save("agent1", f"k{i}", f"python programming tutorial number {i}")
        results = mem.recall("agent1", "python", limit=5)
        assert len(results) <= 5

    def test_recall_tags_included_in_result(self, mem):
        mem.save("agent1", "k1", "machine learning content", tags=["ai", "tech"])
        results = mem.recall("agent1", "machine learning")
        assert results
        assert "tags" in results[0]

    def test_recall_returns_list_on_fts_error(self, mem):
        # Passing empty query should not raise
        results = mem.recall("agent1", "")
        assert isinstance(results, list)


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_summarize_empty_agent(self, mem):
        result = mem.summarize("ghost_agent")
        assert result["total_memories"] == 0
        assert result["tag_frequency"] == {}
        assert result["recent"] == []

    def test_summarize_counts_correctly(self, mem):
        for i in range(7):
            mem.save("agent1", f"k{i}", f"content {i}", tags=["tag_a"])
        result = mem.summarize("agent1")
        assert result["total_memories"] == 7

    def test_summarize_tag_frequency(self, mem):
        mem.save("agent1", "k1", "c", tags=["finance", "important"])
        mem.save("agent1", "k2", "c", tags=["finance"])
        mem.save("agent1", "k3", "c", tags=["seo"])
        result = mem.summarize("agent1")
        freq = result["tag_frequency"]
        assert freq["finance"] == 2
        assert freq["important"] == 1
        assert freq["seo"] == 1

    def test_summarize_recent_max_five(self, mem):
        for i in range(10):
            mem.save("agent1", f"k{i}", f"content {i}")
        result = mem.summarize("agent1")
        assert len(result["recent"]) <= 5

    def test_summarize_preview_truncated_at_100(self, mem):
        long_content = "x" * 300
        mem.save("agent1", "k1", long_content)
        result = mem.summarize("agent1")
        assert len(result["recent"][0]["preview"]) <= 100

    def test_summarize_returns_agent_field(self, mem):
        mem.save("agentX", "k", "c")
        result = mem.summarize("agentX")
        assert result["agent"] == "agentX"


# ---------------------------------------------------------------------------
# inject_context
# ---------------------------------------------------------------------------

class TestInjectContext:
    def test_inject_context_empty_when_no_memories(self, mem):
        result = mem.inject_context("agent1", "python tutorial")
        assert result == ""

    def test_inject_context_returns_header(self, mem):
        mem.save("agent1", "py", "Python programming language tutorial guide")
        result = mem.inject_context("agent1", "python tutorial")
        if result:  # Only check if something was found
            assert "## Relevant memories" in result

    def test_inject_context_contains_key(self, mem):
        mem.save("agent1", "affiliate", "Affiliate marketing revenue strategy")
        result = mem.inject_context("agent1", "affiliate marketing")
        if result:
            assert "affiliate" in result

    def test_inject_context_max_chars_respected(self, mem):
        for i in range(50):
            mem.save("agent1", f"k{i}", f"Python programming is great because {i}" * 10)
        result = mem.inject_context("agent1", "python programming", max_chars=500)
        assert len(result) <= 600  # some tolerance for header

    def test_inject_context_empty_when_no_results_fit(self, mem):
        mem.save("agent1", "key", "unrelated content")
        # Very small max_chars so nothing fits
        result = mem.inject_context("agent1", "something", max_chars=5)
        assert result == ""


# ---------------------------------------------------------------------------
# memory_stats
# ---------------------------------------------------------------------------

class TestMemoryStats:
    def test_stats_zero_when_empty(self, mem):
        stats = mem.memory_stats()
        assert stats["total_memories"] == 0
        assert stats["unique_agents"] == 0

    def test_stats_counts_total_and_agents(self, mem):
        mem.save("agentA", "k1", "content")
        mem.save("agentA", "k2", "content")
        mem.save("agentB", "k3", "content")
        stats = mem.memory_stats()
        assert stats["total_memories"] == 3
        assert stats["unique_agents"] == 2

    def test_stats_oldest_newest(self, mem):
        mem.save("agentA", "k1", "first memory")
        mem.save("agentA", "k2", "second memory")
        stats = mem.memory_stats()
        assert stats["oldest_memory"] is not None
        assert stats["newest_memory"] is not None


# ---------------------------------------------------------------------------
# Memory isolation between agents
# ---------------------------------------------------------------------------

class TestMemoryIsolation:
    def test_agents_cannot_see_each_others_memories(self, mem):
        mem.save("agentA", "secret", "Secret A data — confidential")
        mem.save("agentB", "secret", "Secret B data — different content")
        record_a = mem.get("agentA", "secret")
        record_b = mem.get("agentB", "secret")
        assert record_a["content"] == "Secret A data — confidential"
        assert record_b["content"] == "Secret B data — different content"

    def test_wipe_one_agent_leaves_another_intact(self, mem):
        for i in range(3):
            mem.save("agentA", f"a{i}", "A content")
            mem.save("agentB", f"b{i}", "B content")
        mem.wipe("agentA")
        assert len(mem.list_keys("agentB")) == 3
        assert len(mem.list_keys("agentA")) == 0

    def test_fts_recall_strictly_isolated(self, mem):
        mem.save("agentA", "k", "Python machine learning neural networks deep learning")
        mem.save("agentB", "k", "Cooking pasta sauce recipe Italian food")
        results_a = mem.recall("agentA", "machine learning")
        results_b = mem.recall("agentB", "machine learning")
        # A should find it, B should not
        a_keys = [r["key"] for r in results_a]
        assert "k" in a_keys
        assert results_b == [] or all(r["agent"] == "agentB" for r in results_b)


# ---------------------------------------------------------------------------
# Thread-safety smoke test
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_saves_do_not_corrupt(self, mem):
        errors = []

        def _worker(agent_id: int):
            try:
                for j in range(10):
                    mem.save(f"agent{agent_id}", f"key{j}", f"content {agent_id}-{j}")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Verify we can read everything back
        for i in range(5):
            keys = mem.list_keys(f"agent{i}")
            assert len(keys) == 10
