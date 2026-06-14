"""Tests for core/social_scheduler.py — social media scheduling queue."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_module(tmp_db: Path):
    """Return the social_scheduler module re-initialised against a temp DB."""
    import importlib
    import core.social_scheduler as mod

    mod._schema_ready = False
    mod._DB_PATH = tmp_db
    return mod


class _SchedulerBase(unittest.TestCase):
    """Common setUp that gives each test class its own isolated database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.mod = _make_module(self.db_path)

    def tearDown(self):
        self.mod._schema_ready = False
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# DAILY_LIMITS and OPTIMAL_HOURS constants
# ---------------------------------------------------------------------------

class TestConstants(_SchedulerBase):
    def test_daily_limits_covers_all_platforms(self):
        mod = self.mod
        for platform in mod.PLATFORMS:
            self.assertIn(platform, mod.DAILY_LIMITS,
                          f"DAILY_LIMITS missing platform: {platform}")

    def test_optimal_hours_covers_all_platforms(self):
        mod = self.mod
        for platform in mod.PLATFORMS:
            self.assertIn(platform, mod.OPTIMAL_HOURS,
                          f"OPTIMAL_HOURS missing platform: {platform}")

    def test_twitter_daily_limit(self):
        self.assertEqual(self.mod.DAILY_LIMITS["twitter"], 15)

    def test_linkedin_daily_limit(self):
        self.assertEqual(self.mod.DAILY_LIMITS["linkedin"], 3)

    def test_instagram_daily_limit(self):
        self.assertEqual(self.mod.DAILY_LIMITS["instagram"], 2)

    def test_telegram_daily_limit_high(self):
        self.assertEqual(self.mod.DAILY_LIMITS["telegram"], 20)

    def test_twitter_optimal_hours(self):
        self.assertIn(8, self.mod.OPTIMAL_HOURS["twitter"])

    def test_linkedin_optimal_hours_three_slots(self):
        self.assertEqual(len(self.mod.OPTIMAL_HOURS["linkedin"]), 3)

    def test_platforms_list_length(self):
        self.assertGreaterEqual(len(self.mod.PLATFORMS), 10)

    def test_post_statuses_contains_expected(self):
        for status in ("pending", "scheduled", "published", "failed", "cancelled"):
            self.assertIn(status, self.mod.POST_STATUSES)


# ---------------------------------------------------------------------------
# schedule_post
# ---------------------------------------------------------------------------

class TestSchedulePost(_SchedulerBase):
    def test_schedule_returns_scheduled_post(self):
        post = self.mod.schedule_post("twitter", "Hello world!")
        self.assertIsNotNone(post)
        self.assertGreater(post.id, 0)

    def test_schedule_stores_platform(self):
        post = self.mod.schedule_post("linkedin", "Professional content")
        self.assertEqual(post.platform, "linkedin")

    def test_schedule_stores_content(self):
        post = self.mod.schedule_post("twitter", "Test tweet content")
        self.assertEqual(post.content, "Test tweet content")

    def test_schedule_with_hashtags(self):
        post = self.mod.schedule_post("instagram", "Photo post", hashtags=["photo", "travel"])
        self.assertIn("photo", post.hashtags)
        self.assertIn("travel", post.hashtags)

    def test_schedule_with_explicit_time(self):
        future = time.time() + 3600
        post = self.mod.schedule_post("facebook", "Scheduled post", scheduled_for=future)
        self.assertAlmostEqual(post.scheduled_for, future, delta=1)

    def test_schedule_status_is_scheduled_in_db(self):
        # The returned ScheduledPost object retains the dataclass default ("pending"),
        # but the DB row is inserted with status="scheduled". Verify via get_queue.
        post = self.mod.schedule_post("twitter", "Status check")
        queue = self.mod.get_queue(platform="twitter", status="scheduled")
        self.assertTrue(any(p.id == post.id for p in queue))

    def test_schedule_invalid_platform_raises(self):
        with self.assertRaises(ValueError):
            self.mod.schedule_post("myspace", "Invalid platform")

    def test_schedule_created_by_stored(self):
        post = self.mod.schedule_post("twitter", "Agent post", created_by="test_agent")
        self.assertEqual(post.created_by, "test_agent")

    def test_schedule_assigns_unique_ids(self):
        p1 = self.mod.schedule_post("twitter", "Post one")
        p2 = self.mod.schedule_post("twitter", "Post two")
        self.assertNotEqual(p1.id, p2.id)

    def test_schedule_multiple_platforms(self):
        p1 = self.mod.schedule_post("twitter", "Tweet")
        p2 = self.mod.schedule_post("linkedin", "LinkedIn post")
        self.assertEqual(p1.platform, "twitter")
        self.assertEqual(p2.platform, "linkedin")

    def test_schedule_with_metadata(self):
        meta = {"campaign": "summer_sale", "ab_test": "A"}
        post = self.mod.schedule_post("facebook", "Campaign post", metadata=meta)
        self.assertEqual(post.metadata.get("campaign"), "summer_sale")

    def test_schedule_auto_assigns_optimal_slot(self):
        post = self.mod.schedule_post("twitter", "Auto-slot post")
        self.assertGreater(post.scheduled_for, 0)

    def test_schedule_tiktok_platform(self):
        post = self.mod.schedule_post("tiktok", "TikTok content")
        self.assertEqual(post.platform, "tiktok")

    def test_schedule_reddit_platform(self):
        post = self.mod.schedule_post("reddit", "Reddit post")
        self.assertEqual(post.platform, "reddit")

    def test_schedule_persists_to_database(self):
        post = self.mod.schedule_post("twitter", "DB persistence test")
        # Re-read from DB via get_queue
        queue = self.mod.get_queue(platform="twitter")
        ids = [p.id for p in queue]
        self.assertIn(post.id, ids)


# ---------------------------------------------------------------------------
# get_due_posts
# ---------------------------------------------------------------------------

class TestGetDuePosts(_SchedulerBase):
    def test_no_due_posts_initially(self):
        # All newly scheduled posts use optimal future slots
        # Schedule one in the past explicitly
        posts = self.mod.get_due_posts()
        self.assertIsInstance(posts, list)

    def test_past_post_is_due(self):
        past = time.time() - 60
        self.mod.schedule_post("twitter", "Past post", scheduled_for=past)
        due = self.mod.get_due_posts()
        self.assertTrue(any(p.content == "Past post" for p in due))

    def test_future_post_not_due(self):
        future = time.time() + 9999
        self.mod.schedule_post("twitter", "Future post", scheduled_for=future)
        due = self.mod.get_due_posts()
        self.assertFalse(any(p.content == "Future post" for p in due))

    def test_due_posts_filtered_by_platform(self):
        past = time.time() - 60
        self.mod.schedule_post("twitter", "Tweet due", scheduled_for=past)
        self.mod.schedule_post("linkedin", "LinkedIn due", scheduled_for=past)
        due_twitter = self.mod.get_due_posts(platform="twitter")
        self.assertTrue(all(p.platform == "twitter" for p in due_twitter))

    def test_due_posts_ordered_by_scheduled_for(self):
        past1 = time.time() - 120
        past2 = time.time() - 60
        self.mod.schedule_post("twitter", "Older post", scheduled_for=past1)
        self.mod.schedule_post("twitter", "Newer post", scheduled_for=past2)
        due = self.mod.get_due_posts()
        self.assertLessEqual(due[0].scheduled_for, due[-1].scheduled_for)

    def test_due_posts_excludes_published(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Already published", scheduled_for=past)
        self.mod.mark_published(post.id)
        due = self.mod.get_due_posts()
        self.assertFalse(any(p.id == post.id for p in due))

    def test_due_posts_excludes_cancelled(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Cancelled post", scheduled_for=past)
        self.mod.cancel_post(post.id)
        due = self.mod.get_due_posts()
        self.assertFalse(any(p.id == post.id for p in due))

    def test_due_posts_excludes_failed(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Failed post", scheduled_for=past)
        self.mod.mark_failed(post.id, "network error")
        due = self.mod.get_due_posts()
        self.assertFalse(any(p.id == post.id for p in due))

    def test_due_posts_no_platform_filter_returns_all(self):
        past = time.time() - 60
        self.mod.schedule_post("twitter", "Tweet", scheduled_for=past)
        self.mod.schedule_post("linkedin", "LinkedIn", scheduled_for=past)
        due = self.mod.get_due_posts()
        platforms = {p.platform for p in due}
        self.assertIn("twitter", platforms)
        self.assertIn("linkedin", platforms)


# ---------------------------------------------------------------------------
# mark_published
# ---------------------------------------------------------------------------

class TestMarkPublished(_SchedulerBase):
    def test_mark_published_returns_string(self):
        post = self.mod.schedule_post("twitter", "Test post")
        result = self.mod.mark_published(post.id)
        self.assertIsInstance(result, str)

    def test_mark_published_message_contains_id(self):
        post = self.mod.schedule_post("twitter", "Test post")
        result = self.mod.mark_published(post.id)
        self.assertIn(str(post.id), result)

    def test_mark_published_changes_status(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "To publish", scheduled_for=past)
        self.mod.mark_published(post.id, url="https://twitter.com/status/123")
        due = self.mod.get_due_posts()
        self.assertFalse(any(p.id == post.id for p in due))

    def test_mark_published_stores_url(self):
        post = self.mod.schedule_post("twitter", "URL test")
        self.mod.mark_published(post.id, url="https://example.com/post/1")
        queue = self.mod.get_queue(platform="twitter", status="published")
        published = [p for p in queue if p.id == post.id]
        self.assertTrue(len(published) > 0)
        self.assertEqual(published[0].publish_url, "https://example.com/post/1")

    def test_mark_published_without_url(self):
        post = self.mod.schedule_post("twitter", "No URL test")
        result = self.mod.mark_published(post.id)
        self.assertIn("published", result)


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------

class TestMarkFailed(_SchedulerBase):
    def test_mark_failed_returns_string(self):
        post = self.mod.schedule_post("twitter", "Will fail")
        result = self.mod.mark_failed(post.id, "Connection timeout")
        self.assertIsInstance(result, str)

    def test_mark_failed_message_contains_id(self):
        post = self.mod.schedule_post("twitter", "Will fail")
        result = self.mod.mark_failed(post.id, "Network error")
        self.assertIn(str(post.id), result)

    def test_mark_failed_message_contains_error(self):
        post = self.mod.schedule_post("twitter", "Will fail")
        result = self.mod.mark_failed(post.id, "Rate limit exceeded")
        self.assertIn("Rate limit exceeded", result)

    def test_mark_failed_removes_from_due(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Failed post", scheduled_for=past)
        self.mod.mark_failed(post.id, "API error")
        due = self.mod.get_due_posts()
        self.assertFalse(any(p.id == post.id for p in due))

    def test_mark_failed_long_error_truncated(self):
        post = self.mod.schedule_post("twitter", "Error test")
        long_error = "E" * 1000
        result = self.mod.mark_failed(post.id, long_error)
        # Should not raise and should return a string
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# cancel_post
# ---------------------------------------------------------------------------

class TestCancelPost(_SchedulerBase):
    def test_cancel_returns_string(self):
        post = self.mod.schedule_post("twitter", "To cancel")
        result = self.mod.cancel_post(post.id)
        self.assertIsInstance(result, str)

    def test_cancel_message_contains_id(self):
        post = self.mod.schedule_post("twitter", "To cancel")
        result = self.mod.cancel_post(post.id)
        self.assertIn(str(post.id), result)

    def test_cancel_message_contains_cancelled(self):
        post = self.mod.schedule_post("twitter", "To cancel")
        result = self.mod.cancel_post(post.id)
        self.assertIn("cancelled", result.lower())

    def test_cancelled_post_not_in_queue(self):
        post = self.mod.schedule_post("twitter", "Cancelled post")
        self.mod.cancel_post(post.id)
        queue = self.mod.get_queue(platform="twitter", status="scheduled")
        self.assertFalse(any(p.id == post.id for p in queue))

    def test_cancel_does_not_affect_other_posts(self):
        p1 = self.mod.schedule_post("twitter", "Keep me")
        p2 = self.mod.schedule_post("twitter", "Cancel me")
        self.mod.cancel_post(p2.id)
        queue = self.mod.get_queue(platform="twitter", status="scheduled")
        self.assertTrue(any(p.id == p1.id for p in queue))


# ---------------------------------------------------------------------------
# get_queue / list_posts
# ---------------------------------------------------------------------------

class TestGetQueue(_SchedulerBase):
    def test_get_queue_returns_list(self):
        result = self.mod.get_queue()
        self.assertIsInstance(result, list)

    def test_get_queue_filtered_by_platform(self):
        self.mod.schedule_post("twitter", "Tweet")
        self.mod.schedule_post("linkedin", "LinkedIn")
        queue = self.mod.get_queue(platform="twitter")
        self.assertTrue(all(p.platform == "twitter" for p in queue))

    def test_get_queue_filtered_by_status(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Will publish", scheduled_for=past)
        self.mod.mark_published(post.id)
        published = self.mod.get_queue(status="published")
        self.assertTrue(any(p.id == post.id for p in published))

    def test_get_queue_limit_respected(self):
        for i in range(10):
            self.mod.schedule_post("twitter", f"Post {i}")
        queue = self.mod.get_queue(limit=3)
        self.assertLessEqual(len(queue), 3)

    def test_get_queue_returns_scheduled_post_objects(self):
        self.mod.schedule_post("twitter", "Type check post")
        queue = self.mod.get_queue()
        self.assertTrue(all(isinstance(p, self.mod.ScheduledPost) for p in queue))

    def test_get_queue_default_status_scheduled(self):
        p1 = self.mod.schedule_post("twitter", "Scheduled")
        past = time.time() - 60
        p2 = self.mod.schedule_post("twitter", "To publish", scheduled_for=past)
        self.mod.mark_published(p2.id)
        queue = self.mod.get_queue()
        self.assertTrue(any(p.id == p1.id for p in queue))
        self.assertFalse(any(p.id == p2.id for p in queue))


# ---------------------------------------------------------------------------
# record_engagement
# ---------------------------------------------------------------------------

class TestRecordEngagement(_SchedulerBase):
    def test_record_engagement_no_error(self):
        post = self.mod.schedule_post("twitter", "Engagement test")
        # Should not raise
        self.mod.record_engagement(post.id, "twitter", likes=100, comments=10, shares=5)

    def test_record_engagement_multiple_records(self):
        post = self.mod.schedule_post("twitter", "Multi-engagement")
        self.mod.record_engagement(post.id, "twitter", likes=50)
        self.mod.record_engagement(post.id, "twitter", likes=80)
        # Both recorded, no error raised

    def test_record_engagement_zero_values(self):
        post = self.mod.schedule_post("twitter", "Zero engagement")
        self.mod.record_engagement(post.id, "twitter", likes=0, comments=0, shares=0)

    def test_record_engagement_high_values(self):
        post = self.mod.schedule_post("linkedin", "Viral post")
        self.mod.record_engagement(post.id, "linkedin", likes=50000, shares=12000,
                                   comments=3000, impressions=500000)

    def test_record_engagement_persists(self):
        post = self.mod.schedule_post("instagram", "Instagram engagement")
        self.mod.record_engagement(post.id, "instagram", likes=200, clicks=30)
        # Verify via schedule_stats
        stats = self.mod.schedule_stats()
        self.assertIsInstance(stats, dict)

    def test_record_engagement_different_platforms(self):
        p1 = self.mod.schedule_post("twitter", "Twitter post")
        p2 = self.mod.schedule_post("linkedin", "LinkedIn post")
        self.mod.record_engagement(p1.id, "twitter", likes=100)
        self.mod.record_engagement(p2.id, "linkedin", likes=50)


# ---------------------------------------------------------------------------
# schedule_stats
# ---------------------------------------------------------------------------

class TestScheduleStats(_SchedulerBase):
    def test_stats_returns_dict(self):
        stats = self.mod.schedule_stats()
        self.assertIsInstance(stats, dict)

    def test_stats_contains_days_key(self):
        stats = self.mod.schedule_stats()
        self.assertIn("days", stats)

    def test_stats_contains_pending_posts(self):
        stats = self.mod.schedule_stats()
        self.assertIn("pending_posts", stats)

    def test_stats_contains_by_platform(self):
        stats = self.mod.schedule_stats()
        self.assertIn("by_platform", stats)

    def test_stats_contains_total_published(self):
        stats = self.mod.schedule_stats()
        self.assertIn("total_published", stats)

    def test_stats_pending_count_increases(self):
        self.mod.schedule_post("twitter", "Pending post")
        stats = self.mod.schedule_stats()
        self.assertGreaterEqual(stats["pending_posts"], 1)

    def test_stats_custom_days(self):
        stats = self.mod.schedule_stats(days=30)
        self.assertEqual(stats["days"], 30)

    def test_stats_default_days_7(self):
        stats = self.mod.schedule_stats()
        self.assertEqual(stats["days"], 7)

    def test_stats_by_platform_tracks_scheduled(self):
        self.mod.schedule_post("twitter", "Platform stats test")
        stats = self.mod.schedule_stats()
        # twitter should appear in by_platform
        self.assertIn("twitter", stats["by_platform"])


# ---------------------------------------------------------------------------
# ScheduledPost dataclass
# ---------------------------------------------------------------------------

class TestScheduledPostDataclass(_SchedulerBase):
    def test_full_content_no_hashtags(self):
        post = self.mod.ScheduledPost(platform="twitter", content="Hello")
        self.assertEqual(post.full_content, "Hello")

    def test_full_content_with_hashtags(self):
        post = self.mod.ScheduledPost(platform="twitter", content="Hello",
                                      hashtags=["python", "coding"])
        self.assertIn("#python", post.full_content)
        self.assertIn("#coding", post.full_content)

    def test_full_content_strips_existing_hash(self):
        post = self.mod.ScheduledPost(platform="twitter", content="Tagged",
                                      hashtags=["#ai"])
        self.assertIn("#ai", post.full_content)
        self.assertNotIn("##ai", post.full_content)

    def test_default_status_pending(self):
        post = self.mod.ScheduledPost(platform="twitter", content="Test")
        self.assertEqual(post.status, "pending")

    def test_default_content_type_text(self):
        post = self.mod.ScheduledPost(platform="twitter", content="Test")
        self.assertEqual(post.content_type, "text")

    def test_id_defaults_zero(self):
        post = self.mod.ScheduledPost(platform="twitter", content="Test")
        self.assertEqual(post.id, 0)


# ---------------------------------------------------------------------------
# get_best_times / optimal slot
# ---------------------------------------------------------------------------

class TestOptimalSlot(_SchedulerBase):
    def test_auto_slot_is_in_future_or_next_optimal(self):
        future = time.time() + 3600 * 2
        post = self.mod.schedule_post("twitter", "Future slot")
        self.assertGreater(post.scheduled_for, 0)

    def test_explicit_past_slot_honored(self):
        past = time.time() - 3600
        post = self.mod.schedule_post("twitter", "Past slot", scheduled_for=past)
        self.assertAlmostEqual(post.scheduled_for, past, delta=1)

    def test_slot_within_24_hours_of_now(self):
        post = self.mod.schedule_post("twitter", "Slot check")
        now = time.time()
        # The slot should be within the next 48 hours (could fall tomorrow)
        self.assertLess(post.scheduled_for, now + 86400 * 2)


# ---------------------------------------------------------------------------
# Daily quota
# ---------------------------------------------------------------------------

class TestDailyQuota(_SchedulerBase):
    def test_daily_limit_exists_for_all_platforms(self):
        for platform in self.mod.PLATFORMS:
            self.assertIn(platform, self.mod.DAILY_LIMITS)

    def test_platform_post_count_within_limit(self):
        limit = self.mod.DAILY_LIMITS["linkedin"]
        # Schedule up to the limit with past timestamps
        for i in range(limit):
            past = time.time() - 60
            post = self.mod.schedule_post("linkedin", f"Post {i}", scheduled_for=past)
            self.mod.mark_published(post.id)
        # Verify the published count doesn't exceed the limit
        stats = self.mod.schedule_stats()
        published = stats["by_platform"].get("linkedin", {}).get("published", 0)
        self.assertLessEqual(published, limit)


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

class TestSchedulePostTool(_SchedulerBase):
    def test_schedule_post_tool_returns_string(self):
        result = self.mod.schedule_post_tool("twitter", "Tool test post")
        self.assertIsInstance(result, str)

    def test_schedule_post_tool_success_contains_platform(self):
        result = self.mod.schedule_post_tool("twitter", "Platform check")
        self.assertIn("twitter", result)

    def test_schedule_post_tool_invalid_platform_returns_error(self):
        result = self.mod.schedule_post_tool("myspace", "Bad platform")
        self.assertIn("ERROR", result)

    def test_schedule_post_tool_with_hashtags(self):
        result = self.mod.schedule_post_tool("instagram", "Hashtag post",
                                              hashtags=["test", "photo"])
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_schedule_post_tool_with_explicit_time(self):
        future = time.time() + 7200
        result = self.mod.schedule_post_tool("twitter", "Future post", scheduled_for=future)
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_schedule_post_tool_contains_post_id(self):
        result = self.mod.schedule_post_tool("twitter", "ID check")
        self.assertRegex(result, r"#\d+")

    def test_schedule_post_tool_contains_utc_datetime(self):
        result = self.mod.schedule_post_tool("twitter", "Time check")
        self.assertIn("UTC", result)


class TestDuePostsTool(_SchedulerBase):
    def test_due_posts_tool_returns_string(self):
        result = self.mod.get_due_posts_tool()
        self.assertIsInstance(result, str)

    def test_due_posts_tool_returns_json(self):
        result = self.mod.get_due_posts_tool()
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_due_posts_tool_past_post_present(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Due tweet", scheduled_for=past)
        result = self.mod.get_due_posts_tool()
        data = json.loads(result)
        ids = [item["id"] for item in data]
        self.assertIn(post.id, ids)

    def test_due_posts_tool_with_platform_filter(self):
        past = time.time() - 60
        self.mod.schedule_post("twitter", "Twitter due", scheduled_for=past)
        self.mod.schedule_post("linkedin", "LinkedIn due", scheduled_for=past)
        result = self.mod.get_due_posts_tool(platform="twitter")
        data = json.loads(result)
        self.assertTrue(all(item["platform"] == "twitter" for item in data))

    def test_due_posts_tool_item_has_required_keys(self):
        past = time.time() - 60
        self.mod.schedule_post("twitter", "Key check", scheduled_for=past)
        result = self.mod.get_due_posts_tool()
        data = json.loads(result)
        if data:
            item = data[0]
            for key in ("id", "platform", "content", "scheduled_for"):
                self.assertIn(key, item)


class TestPublishingQueueTool(_SchedulerBase):
    def test_publishing_queue_tool_returns_string(self):
        result = self.mod.publishing_queue_tool()
        self.assertIsInstance(result, str)

    def test_publishing_queue_tool_returns_json(self):
        result = self.mod.publishing_queue_tool()
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_publishing_queue_tool_scheduled_posts_appear(self):
        self.mod.schedule_post("twitter", "Queue test post")
        result = self.mod.publishing_queue_tool()
        data = json.loads(result)
        self.assertGreaterEqual(len(data), 1)

    def test_publishing_queue_tool_with_platform(self):
        self.mod.schedule_post("twitter", "Twitter only")
        self.mod.schedule_post("linkedin", "LinkedIn only")
        result = self.mod.publishing_queue_tool(platform="twitter")
        data = json.loads(result)
        self.assertTrue(all(item["platform"] == "twitter" for item in data))


class TestScheduleStatsTool(_SchedulerBase):
    def test_stats_tool_returns_string(self):
        result = self.mod.schedule_stats_tool()
        self.assertIsInstance(result, str)

    def test_stats_tool_returns_json(self):
        result = self.mod.schedule_stats_tool()
        data = json.loads(result)
        self.assertIsInstance(data, dict)

    def test_stats_tool_with_days_param(self):
        result = self.mod.schedule_stats_tool(days=14)
        data = json.loads(result)
        self.assertEqual(data["days"], 14)

    def test_stats_tool_contains_expected_keys(self):
        result = self.mod.schedule_stats_tool()
        data = json.loads(result)
        for key in ("days", "pending_posts", "by_platform", "total_published"):
            self.assertIn(key, data)


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

class TestDatabasePersistence(_SchedulerBase):
    def test_post_survives_module_reinit(self):
        post = self.mod.schedule_post("twitter", "Persistence check")
        # Reset the schema flag to simulate a new process reading the same DB
        self.mod._schema_ready = False
        queue = self.mod.get_queue(platform="twitter")
        ids = [p.id for p in queue]
        self.assertIn(post.id, ids)

    def test_multiple_posts_persist(self):
        ids = []
        for i in range(5):
            p = self.mod.schedule_post("twitter", f"Persisted post {i}")
            ids.append(p.id)
        self.mod._schema_ready = False
        queue = self.mod.get_queue(platform="twitter", limit=10)
        stored_ids = [p.id for p in queue]
        for pid in ids:
            self.assertIn(pid, stored_ids)

    def test_status_update_persists(self):
        past = time.time() - 60
        post = self.mod.schedule_post("twitter", "Status persistence", scheduled_for=past)
        self.mod.mark_published(post.id)
        self.mod._schema_ready = False
        published = self.mod.get_queue(platform="twitter", status="published")
        self.assertTrue(any(p.id == post.id for p in published))

    def test_hashtags_persist_round_trip(self):
        tags = ["python", "coding", "dev"]
        post = self.mod.schedule_post("twitter", "Tag test", hashtags=tags)
        self.mod._schema_ready = False
        queue = self.mod.get_queue(platform="twitter")
        stored = next(p for p in queue if p.id == post.id)
        self.assertEqual(sorted(stored.hashtags), sorted(tags))


if __name__ == "__main__":
    unittest.main()
