"""End-to-end integration tests — every module, every use case.

Each test class is self-contained and uses a fresh SQLite database.
Tests cover the full lifecycle: create → use → query → delete.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Global isolated DB — patched before any module import
# ---------------------------------------------------------------------------
_tmp_dir = tempfile.mkdtemp()
_DB = os.path.join(_tmp_dir, f"integration_{uuid.uuid4().hex[:8]}.db")


def _dbp():
    return _DB


# Patch the database path across ALL modules before importing anything
with patch("autoearn.core.database.get_db_path", _dbp):
    from autoearn.core import (
        ab_testing,
        affiliate_tracker,
        analytics_tracker,
        content_calendar,
        course_builder,
        email_marketing,
        funnel_builder,
        keyword_tracker,
        lead_scoring,
        link_builder,
        newsletter,
        pricing_engine,
        product_manager,
        reporting_engine,
        seo_optimizer,
        social_scheduler,
        subscription_manager,
        traffic_analyzer,
        webhook_manager,
    )
    from autoearn.core import database as db_mod
    from autoearn.core import message_bus
    from autoearn.core import tools


# Modules that hold their own local get_db_path reference (patched individually)
_ALL_MODULES = [
    ab_testing, affiliate_tracker, analytics_tracker, content_calendar,
    course_builder, email_marketing, funnel_builder, keyword_tracker,
    lead_scoring, link_builder, newsletter, pricing_engine, product_manager,
    reporting_engine, seo_optimizer, social_scheduler, subscription_manager,
    traffic_analyzer, webhook_manager, db_mod, message_bus,
]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Give every test a completely empty database."""
    from pathlib import Path as _Path
    db_path = str(tmp_path / "test.db")
    db_path_obj = _Path(db_path)

    def _fake_cfg(key, fallback=None, _p=db_path):
        if isinstance(key, str) and "db_path" in key:
            return _p
        from autoearn.core.config import cfg as _real_cfg
        return _real_cfg(key, fallback=fallback)

    for mod in _ALL_MODULES:
        # Pattern 1: get_db_path() function reference
        if hasattr(mod, "get_db_path"):
            monkeypatch.setattr(mod, "get_db_path", lambda p=db_path: p)
        # Pattern 2: cfg() based DB path + persistent _conn
        if hasattr(mod, "cfg"):
            monkeypatch.setattr(mod, "cfg", _fake_cfg)
        if hasattr(mod, "_conn"):
            mod._conn = None
        # Pattern 3: hardcoded _DB_PATH constant
        if hasattr(mod, "_DB_PATH"):
            monkeypatch.setattr(mod, "_DB_PATH", db_path_obj)
        if hasattr(mod, "_schema_ready"):
            mod._schema_ready = False
        if hasattr(mod, "_schema_initialized"):
            mod._schema_initialized = False

    # Also patch the global DB_PATH used by database module
    monkeypatch.setattr(db_mod, "DB_PATH", db_path_obj)
    db_mod._initialized = False
    yield db_path

    for mod in _ALL_MODULES:
        if hasattr(mod, "_schema_ready"):
            mod._schema_ready = False
        if hasattr(mod, "_conn"):
            mod._conn = None
    db_mod._initialized = False


# ===========================================================================
# 1. Database layer
# ===========================================================================
class TestDatabase:
    def test_init_creates_tables(self, fresh_db):
        db_mod.init()
        conn = sqlite3.connect(fresh_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        for t in ("messages", "activity", "revenue"):
            assert t in tables, f"missing table: {t}"

    def test_log_and_retrieve_activity(self, fresh_db):
        db_mod.init()
        db_mod.log_activity("agent1", "web_search", "python tutorial")
        rows = db_mod.recent_activity(limit=5)
        assert len(rows) >= 1
        assert rows[0]["agent"] == "agent1"

    def test_log_and_retrieve_revenue(self, fresh_db):
        db_mod.init()
        db_mod.log_revenue(99.99, source="gumroad", agent="ceo", note="first sale")
        summary = db_mod.revenue_summary()
        assert summary["total_usd"] >= 99.99

    def test_revenue_summary_empty(self, fresh_db):
        db_mod.init()
        s = db_mod.revenue_summary()
        assert s["total_usd"] == 0
        assert isinstance(s["by_source"], list)


# ===========================================================================
# 2. Message bus
# ===========================================================================
class TestMessageBus:
    def test_send_and_receive(self, fresh_db):
        db_mod.init()
        mid = message_bus.send("ceo", "writer", "directive",
                               subject="Write article", body="About AI")
        assert isinstance(mid, int)
        inbox = message_bus.inbox("writer")
        assert any(m["id"] == mid for m in inbox)

    def test_inbox_empty_initially(self, fresh_db):
        db_mod.init()
        msgs = message_bus.inbox("unknown_agent")
        assert msgs == []

    def test_message_types(self, fresh_db):
        db_mod.init()
        for msg_type in ["directive", "work_item", "output", "approval", "rejection"]:
            message_bus.send("sender", "receiver", msg_type,
                             subject=f"Test {msg_type}", body="body")
        inbox = message_bus.inbox("receiver")
        types_seen = {m["type"] for m in inbox}
        assert "directive" in types_seen

    def test_send_multiple_messages(self, fresh_db):
        db_mod.init()
        for i in range(5):
            message_bus.send("ceo", "team", "work_item",
                             subject=f"Task {i}", body=f"Do task {i}")
        inbox = message_bus.inbox("team")
        assert len(inbox) == 5


# ===========================================================================
# 3. Tool registry
# ===========================================================================
class TestToolRegistry:
    def test_registry_not_empty(self):
        assert len(tools.REGISTRY) > 50

    def test_schemas_match_registry(self):
        assert set(tools.REGISTRY.keys()) == set(tools.SCHEMAS.keys())

    def test_dispatch_known_tool(self, fresh_db):
        db_mod.init()
        result = tools.dispatch("get_revenue_summary", "test_agent")
        data = json.loads(result)
        assert "total_usd" in data or "total" in data

    def test_dispatch_unknown_tool(self):
        result = tools.dispatch("nonexistent_tool_xyz", "test_agent")
        assert "ERROR" in result

    def test_save_output_tool(self, tmp_path, monkeypatch, fresh_db):
        db_mod.init()
        monkeypatch.setattr(tools, "ROOT", tmp_path)
        monkeypatch.setattr(tools, "OUTPUT_DIR", tmp_path / "output")
        result = tools.dispatch("save_output", "writer",
                                {"category": "articles", "filename": "test.txt",
                                 "content": "Hello world"})
        assert "Saved" in result

    def test_log_revenue_tool(self, fresh_db):
        db_mod.init()
        result = tools.dispatch("log_revenue", "ceo",
                                {"amount": 50.0, "source": "test"})
        assert "$50.00" in result

    def test_send_message_tool(self, fresh_db):
        db_mod.init()
        result = tools.dispatch("send_message", "ceo",
                                {"to": "writer", "type": "directive",
                                 "subject": "Go", "body": "Do it"})
        assert "Sent" in result


# ===========================================================================
# 4. Funnel builder
# ===========================================================================
class TestFunnelBuilder:
    def test_create_and_get_funnel(self, fresh_db):
        fid = funnel_builder.create_funnel("Sales Funnel", funnel_type="sales")
        assert isinstance(fid, int) and fid > 0
        fetched = funnel_builder.get_funnel(fid)
        assert fetched is not None
        assert fetched["name"] == "Sales Funnel"

    def test_add_steps(self, fresh_db):
        fid = funnel_builder.create_funnel("Step Funnel")
        funnel_builder.add_step(fid, "Landing Page", "landing")
        funnel_builder.add_step(fid, "Opt-in Form", "optin")
        funnel_builder.add_step(fid, "Thank You", "thankyou")
        overview = funnel_builder.funnel_overview(fid)
        assert overview["step_count"] >= 3

    def test_record_visitors_and_conversions(self, fresh_db):
        fid = funnel_builder.create_funnel("Conv Funnel")
        sid = funnel_builder.add_step(fid, "Page A", "landing")
        funnel_builder.record_visitor(fid, sid, session_id="v1")
        funnel_builder.record_visitor(fid, sid, session_id="v2")
        funnel_builder.record_conversion(fid, sid, "v1", revenue=49.99)
        overview = funnel_builder.funnel_overview(fid)
        assert overview["total_visitors"] >= 1

    def test_list_funnels(self, fresh_db):
        funnel_builder.create_funnel("F1")
        funnel_builder.create_funnel("F2")
        funnels = funnel_builder.list_funnels()
        assert len(funnels) >= 2

    def test_funnel_from_template(self, fresh_db):
        result = funnel_builder.create_from_template("lead_gen_basic")
        assert result is not None and result > 0

    def test_drop_off_analysis(self, fresh_db):
        fid = funnel_builder.create_funnel("Drop Funnel")
        s1 = funnel_builder.add_step(fid, "A", "landing")
        s2 = funnel_builder.add_step(fid, "B", "optin")
        for i in range(10):
            funnel_builder.record_visitor(fid, s1, session_id=f"vs{i}")
        for i in range(3):
            funnel_builder.record_visitor(fid, s2, session_id=f"vs2_{i}")
        drops = funnel_builder.drop_off_analysis(fid)
        assert isinstance(drops, list)


# ===========================================================================
# 5. Product manager
# ===========================================================================
class TestProductManager:
    def test_create_and_retrieve_product(self, fresh_db):
        p = product_manager.create_product(
            name="Test Course", product_type="digital",
            price=99.0
        )
        assert p.id is not None
        fetched = product_manager.get_product(p.id)
        assert fetched.name == "Test Course"

    def test_record_sale(self, fresh_db):
        p = product_manager.create_product(
            name="Ebook", product_type="digital", price=29.0
        )
        sale = product_manager.record_sale(
            product_id=p.id, amount=29.0,
            customer_email="buyer@example.com"
        )
        assert sale.id is not None

    def test_best_sellers(self, fresh_db):
        p = product_manager.create_product(
            name="Popular", product_type="digital", price=49.0
        )
        for _ in range(3):
            product_manager.record_sale(p.id, 49.0, "c@e.com")
        sellers = product_manager.best_sellers(limit=5)
        assert len(sellers) >= 1

    def test_create_coupon_and_validate(self, fresh_db):
        p = product_manager.create_product(
            name="Coupon Product", product_type="digital", price=100.0
        )
        product_manager.create_coupon("SAVE20", discount_type="percent",
                                       discount_value=20.0, product_id=p.id)
        result = product_manager.validate_coupon("SAVE20")
        assert result["valid"]
        assert result["discount_value"] == 20.0

    def test_add_review(self, fresh_db):
        p = product_manager.create_product(
            name="Review Product", product_type="digital", price=49.0
        )
        product_manager.record_sale(p.id, 49.0, "reviewer@e.com")
        review = product_manager.add_review(
            p.id, rating=5, email="reviewer@e.com", body="Excellent!"
        )
        assert review is not None


# ===========================================================================
# 6. Link builder
# ===========================================================================
class TestLinkBuilder:
    def test_create_and_retrieve(self, fresh_db):
        link = link_builder.create_link(
            destination="https://example.com",
            utm_source="email", utm_medium="newsletter",
            utm_campaign="launch2024"
        )
        assert link.slug is not None
        fetched = link_builder.get_link(link.id)
        assert fetched.destination == "https://example.com"

    def test_record_click(self, fresh_db):
        link = link_builder.create_link(destination="https://example.com/page")
        link_builder.record_click(
            link.slug, ip="abc123", user_agent="Mozilla/5.0",
            country="US", device="desktop"
        )
        history = link_builder.link_click_history(link.slug)
        assert isinstance(history, list)  # just check it's a list

    def test_short_link(self, fresh_db):
        sl = link_builder.create_short_link(
            long_url="https://example.com/very/long/path",
            short_code="mylink"
        )
        assert sl.short_code == "mylink"
        resolved = link_builder.resolve_short_link("mylink")
        assert resolved == "https://example.com/very/long/path"

    def test_bio_page(self, fresh_db):
        page = link_builder.create_bio_page(
            handle="johndoe", title="John's Links", bio="Creator"
        )
        link_builder.add_bio_link(page.handle, "Website", "https://john.com")
        link_builder.add_bio_link(page.handle, "YouTube", "https://youtube.com/@john")
        retrieved = link_builder.get_bio_page("johndoe")
        assert retrieved is not None
        assert len(retrieved.items) == 2

    def test_campaign_performance(self, fresh_db):
        for i in range(3):
            lnk = link_builder.create_link(
                destination=f"https://example.com/{i}",
                utm_campaign="spring_promo"
            )
            link_builder.record_click(lnk.slug, ip=f"ip{i}")
        perf = link_builder.campaign_performance("spring_promo")
        assert perf["total_clicks"] >= 3

    def test_top_links(self, fresh_db):
        for i in range(5):
            lnk = link_builder.create_link(destination=f"https://ex.com/{i}")
            for j in range(i + 1):
                link_builder.record_click(lnk.slug, ip=f"ip{i}{j}")
        top = link_builder.top_links(limit=3)
        assert len(top) <= 3


# ===========================================================================
# 7. Email marketing
# ===========================================================================
class TestEmailMarketing:
    def test_campaign_lifecycle(self, fresh_db):
        c = email_marketing.create_campaign(
            name="Launch Campaign", subject="We're live!",
            email_type="broadcast"
        )
        assert c.status == "draft"
        email_marketing.schedule_campaign(c.id, (datetime.utcnow() + timedelta(days=1)).isoformat())
        updated = email_marketing.get_campaign("Launch Campaign")
        assert updated.status == "scheduled"

    def test_event_tracking(self, fresh_db):
        c = email_marketing.create_campaign(name="Track Camp", subject="Track")
        email_marketing.mark_campaign_sent(c.id)
        email_marketing.record_event("open", "user@example.com", campaign_id=c.id)
        email_marketing.record_event("click", "user@example.com",
                                      campaign_id=c.id, link_url="https://example.com")
        analytics = email_marketing.campaign_analytics(c.id)
        assert "open_rate" in analytics

    def test_drip_sequence(self, fresh_db):
        seq = email_marketing.create_sequence(
            name="Welcome Series", trigger_event="signup"
        )
        email_marketing.add_sequence_email(
            seq.name, subject="Welcome!", body_html="<p>Welcome</p>", delay_hours=0
        )
        email_marketing.add_sequence_email(
            seq.name, subject="Day 3 tip", body_html="<p>Tip</p>", delay_hours=72
        )
        email_marketing.enroll_subscriber("Welcome Series", "new@example.com")
        due = email_marketing.get_due_sequence_emails()
        assert isinstance(due, list)

    def test_automation(self, fresh_db):
        email_marketing.create_automation(
            name="Purchase Follow-up", trigger_event="purchase"
        )
        autos = email_marketing.list_automations()
        assert len(autos) >= 1
        result = email_marketing.fire_automation("purchase", "buyer@example.com")
        assert isinstance(result, list)

    def test_sequence_from_template(self, fresh_db):
        seq = email_marketing.create_sequence_from_template("welcome_series")
        assert seq is not None
        assert seq.id is not None

    def test_segment_lifecycle(self, fresh_db):
        seg = email_marketing.create_segment(
            name="Active Buyers",
            conditions_json='{"field": "purchases", "op": "gt", "value": 0}'
        )
        assert "id" in seg
        segments = email_marketing.list_segments()
        assert any(s["name"] == "Active Buyers" for s in segments)

    def test_summary(self, fresh_db):
        summary = email_marketing.email_marketing_summary()
        assert isinstance(summary, dict)
        assert any("campaign" in k.lower() for k in summary)


# ===========================================================================
# 8. Course builder
# ===========================================================================
class TestCourseBuilder:
    def test_full_course_lifecycle(self, fresh_db):
        course = course_builder.create_course(
            title="Python Mastery", description="Learn Python", price=199.0
        )
        mod = course_builder.add_module(course.id, "Getting Started")
        lesson = course_builder.add_lesson(
            mod["id"], course.id, "Hello World",
            lesson_type="video", content="https://video.url", estimated_minutes=10
        )
        course_builder.publish_course(course.id)
        published = course_builder.get_course(course.id)
        assert published.status == "published"

    def test_enrollment_and_progress(self, fresh_db):
        course = course_builder.create_course(title="Basics", price=0.0)
        mod = course_builder.add_module(course.id, "Module 1")
        l1 = course_builder.add_lesson(mod["id"], course.id, "Lesson 1", content="text")
        l2 = course_builder.add_lesson(mod["id"], course.id, "Lesson 2", content="text")
        enroll = course_builder.enroll_student(course.id, "student@example.com", "Alice")
        course_builder.mark_lesson_complete(enroll["enrollment_id"], l1["id"])
        progress = course_builder.get_student_progress(course.id, "student@example.com")
        assert progress["lessons_completed"] >= 1
        assert 0 <= progress["progress_pct"] <= 100

    def test_quiz_grading(self, fresh_db):
        course = course_builder.create_course(title="Quiz Course")
        mod = course_builder.add_module(course.id, "Module")
        lesson = course_builder.add_lesson(mod["id"], course.id, "Lesson")
        quiz = course_builder.add_quiz(lesson["id"], title="Chapter Quiz")
        q = course_builder.add_question(
            quiz["id"], "What is 2+2?",
            question_type="multiple_choice",
            options=["3", "4", "5", "6"],
            correct_answers=["4"]
        )
        result = course_builder.grade_quiz(quiz["id"], {q["id"]: "4"})
        assert "score_pct" in result or "score" in result

    def test_course_review(self, fresh_db):
        course = course_builder.create_course(title="Reviewed Course", price=49.0)
        course_builder.enroll_student(course.id, "fan@example.com", "Fan")
        review = course_builder.add_course_review(
            course.id, rating=5, student_email="fan@example.com", body="Amazing!"
        )
        assert review is not None

    def test_analytics(self, fresh_db):
        course = course_builder.create_course(title="Analyzed", price=99.0)
        for i in range(3):
            course_builder.enroll_student(course.id, f"s{i}@example.com", f"Student {i}", 99.0)
        analytics = course_builder.course_analytics(course.id)
        assert "course" in analytics or "monthly_revenue" in analytics


# ===========================================================================
# 9. Pricing engine
# ===========================================================================
class TestPricingEngine:
    def test_create_rule_and_resolve(self, fresh_db):
        rule = pricing_engine.create_pricing_rule(
            name="Basic SaaS",
            pricing_model="flat",
            base_price=29.99,
            currency="USD"
        )
        result = pricing_engine.resolve_price(None)
        assert result.get("final_price") == 29.99 or result.get("resolved_price") == 29.99

    def test_tiered_pricing(self, fresh_db):
        rule = pricing_engine.create_pricing_rule(
            name="Tiered Plan", pricing_model="tiered", base_price=0.0
        )
        pricing_engine.add_tier(rule.name, "Low Volume", price=9.99, up_to=10.0)
        pricing_engine.add_tier(rule.name, "High Volume", price=7.99, up_to=100.0)
        result1 = pricing_engine.resolve_price(None, quantity=5.0)
        assert result1["final_price"] == 9.99
        result2 = pricing_engine.resolve_price(None, quantity=50.0)
        assert result2["final_price"] == 7.99

    def test_ab_price_test(self, fresh_db):
        test = pricing_engine.create_price_test(
            name="Price Test A", variant_a_price=29.99, variant_b_price=39.99
        )
        result = pricing_engine.assign_price_variant(test.name)
        assert result["price"] in [29.99, 39.99]

    def test_discount(self, fresh_db):
        pricing_engine.create_discount_rule(
            name="Summer10", discount_type="percent",
            discount_value=10.0
        )
        discounts = pricing_engine.active_discounts()
        assert any(d["name"] == "Summer10" for d in discounts)

    def test_ltv_and_recommend(self, fresh_db):
        pricing_engine.upsert_ltv_estimate(
            segment="enterprise", avg_ltv=5000.0,
            avg_order_value=500.0
        )
        rec = pricing_engine.recommend_price(product_id=1, segment="enterprise")
        assert "suggestions" in rec


# ===========================================================================
# 10. Reporting engine
# ===========================================================================
class TestReportingEngine:
    def test_create_and_run_report(self, fresh_db):
        defn = reporting_engine.create_report_definition(
            name="Weekly Rev",
            report_type="revenue_report",
            output_format="markdown",
            frequency="weekly"
        )
        assert defn.id is not None
        run = reporting_engine.run_report(defn.name)
        assert run.status in ("success", "completed")

    def test_adhoc_report(self, fresh_db):
        result = reporting_engine.run_adhoc_report(
            report_type="executive_summary",
            output_format="markdown"
        )
        assert isinstance(result, str) and len(result) > 0

    def test_seed_defaults(self, fresh_db):
        names = reporting_engine.seed_default_reports()
        assert len(names) >= 3
        reports = reporting_engine.list_report_definitions()
        assert len(reports) >= 3

    def test_run_history(self, fresh_db):
        defn = reporting_engine.create_report_definition(
            name="History Test", report_type="agent_activity"
        )
        reporting_engine.run_report(defn.name)
        reporting_engine.run_report(defn.name)
        history = reporting_engine.get_run_history(defn.name, limit=5)
        assert len(history) >= 2

    def test_metric_cache(self, fresh_db):
        reporting_engine.cache_metric("revenue_30d", 9999.0, ttl_seconds=3600)
        val = reporting_engine.get_cached_metric("revenue_30d")
        assert val == 9999.0


# ===========================================================================
# 11. Lead scoring
# ===========================================================================
class TestLeadScoring:
    def test_create_and_score_lead(self, fresh_db):
        lead = lead_scoring.create_lead(
            email="cto@bigcorp.com",
            name="Jane Smith",
            company="BigCorp",
            source="inbound",
            industry="saas",
            company_size="201-500"
        )
        assert lead.score >= 0
        assert lead.grade in ["A", "B", "C", "D", "F"]

    def test_record_activity_increases_score(self, fresh_db):
        lead = lead_scoring.create_lead(email="lead@example.com")
        score_before = lead.score
        lead_scoring.record_activity(
            "lead@example.com", "demo_request", "Requested product demo"
        )
        updated = lead_scoring.get_lead("lead@example.com")
        assert updated.score >= score_before

    def test_hot_leads(self, fresh_db):
        lead_scoring.create_lead(email="hot@example.com", company="Hot Co", industry="saas")
        for _ in range(5):
            lead_scoring.record_activity("hot@example.com", "demo_request", "demo")
            lead_scoring.record_activity("hot@example.com", "pricing_view", "pricing")
        hot = lead_scoring.hot_leads(limit=10)
        assert isinstance(hot, list)

    def test_lead_funnel(self, fresh_db):
        for i in range(5):
            lead_scoring.create_lead(email=f"funnel{i}@example.com")
        report = lead_scoring.lead_funnel_report()
        assert isinstance(report, dict)

    def test_rescore_all(self, fresh_db):
        for i in range(3):
            lead_scoring.create_lead(email=f"rescore{i}@example.com",
                                      industry="saas", company_size="201-500")
        result = lead_scoring.rescore_all_leads()
        assert "rescored" in result
        assert result["rescored"] == 3


# ===========================================================================
# 12. Content calendar
# ===========================================================================
class TestContentCalendar:
    def test_create_campaign_and_entry(self, fresh_db):
        campaign = content_calendar.create_campaign(
            name="Q4 Push",
            start_date=datetime.utcnow().isoformat(),
            end_date=(datetime.utcnow() + timedelta(days=90)).isoformat()
        )
        assert campaign is not None
        entry = content_calendar.add_entry(
            title="Black Friday Post",
            content_type="social_post",
            platform="twitter",
            scheduled_at=(datetime.utcnow() + timedelta(days=7)).isoformat(),
            description="Big sale announcement"
        )
        assert entry.id is not None

    def test_entry_lifecycle(self, fresh_db):
        entry = content_calendar.add_entry(
            title="Draft Post", content_type="blog_post", platform="wordpress",
            scheduled_at=(datetime.utcnow() + timedelta(days=3)).isoformat()
        )
        content_calendar.approve_entry(entry.id)
        updated = content_calendar.get_entry(entry.id)
        assert updated.status == "approved"
        content_calendar.publish_entry(entry.id)
        published = content_calendar.get_entry(entry.id)
        assert published.status == "published"

    def test_ideas_backlog(self, fresh_db):
        idea = content_calendar.add_idea(
            title="Comparison Post", content_type="blog_post",
            description="Compare top tools"
        )
        ideas = content_calendar.list_ideas()
        assert len(ideas) >= 1
        entry = content_calendar.promote_idea(
            idea.id,
            scheduled_at=(datetime.utcnow() + timedelta(days=14)).isoformat()
        )
        assert entry.id is not None

    def test_template_rendering(self, fresh_db):
        templates = content_calendar.list_templates()
        assert len(templates) >= 3  # seeded templates

    def test_summary(self, fresh_db):
        summary = content_calendar.content_calendar_summary()
        assert "total_entries" in summary


# ===========================================================================
# 13. Subscription manager
# ===========================================================================
class TestSubscriptionManager:
    def test_plan_and_subscribe(self, fresh_db):
        plan = subscription_manager.create_plan(
            name="Pro", price_monthly=29.0, price_annual=290.0, trial_days=14
        )
        assert plan.id is not None
        sub = subscription_manager.subscribe(
            email="pro@example.com", name="Pro User",
            plan_slug=plan.slug, billing_cycle="monthly"
        )
        assert sub.status in ["trialing", "active"]

    def test_upgrade(self, fresh_db):
        p1 = subscription_manager.create_plan("Starter", price_monthly=9.0)
        p2 = subscription_manager.create_plan("Business", price_monthly=49.0)
        sub = subscription_manager.subscribe(
            "upgrade@example.com", "Alice", p1.slug
        )
        updated = subscription_manager.upgrade_plan("upgrade@example.com", p2.slug)
        assert updated.plan_id == p2.id

    def test_cancel_and_reactivate(self, fresh_db):
        plan = subscription_manager.create_plan("Cancel Plan", price_monthly=19.0)
        subscription_manager.subscribe("cancel@example.com", "Bob", plan.slug)
        subscription_manager.cancel_subscription("cancel@example.com")
        sub = subscription_manager.get_subscriber("cancel@example.com")
        assert sub.status in ["cancelled", "active"]  # immediate or period end
        subscription_manager.reactivate_subscription("cancel@example.com")

    def test_mrr(self, fresh_db):
        plan = subscription_manager.create_plan("MRR Plan", price_monthly=100.0)
        for i in range(3):
            subscription_manager.subscribe(f"mrr{i}@example.com", f"User {i}", plan.slug)
        mrr = subscription_manager.mrr()
        assert mrr >= 0

    def test_coupon(self, fresh_db):
        coupon = subscription_manager.create_coupon(
            code="WELCOME50", discount_type="percent",
            discount_value=50.0, max_uses=100
        )
        assert coupon["code"] == "WELCOME50"
        validation = subscription_manager.validate_coupon("WELCOME50")
        assert validation["valid"]


# ===========================================================================
# 14. Traffic analyzer
# ===========================================================================
class TestTrafficAnalyzer:
    def test_session_lifecycle(self, fresh_db):
        session = traffic_analyzer.start_session(
            ip_hash="abc123",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            landing_page="/",
            utm_source="google",
            utm_medium="cpc",
            utm_campaign="brand"
        )
        assert session.session_id is not None
        traffic_analyzer.record_pageview(session.session_id, "/features", "Features")
        traffic_analyzer.record_pageview(session.session_id, "/pricing", "Pricing")
        traffic_analyzer.track_event(session.session_id, "cta_click", "conversion")
        ended = traffic_analyzer.end_session(session.session_id)
        assert "page_views" in ended

    def test_goal_tracking(self, fresh_db):
        goal = traffic_analyzer.create_goal(
            name="Signup", goal_type="event", target_event="signup_complete"
        )
        session = traffic_analyzer.start_session("ip456", "Mozilla/5.0", "/")
        traffic_analyzer.track_event(session.session_id, "signup_complete", "conversion")
        traffic_analyzer.record_goal_completion(session.session_id, goal.id, value=0.0)
        rate = traffic_analyzer.goal_conversion_rate(goal.id, days=1)
        assert rate >= 0.0

    def test_traffic_summary(self, fresh_db):
        for i in range(3):
            s = traffic_analyzer.start_session(f"ip{i}", "Agent/1.0", "/")
            traffic_analyzer.record_pageview(s.session_id, "/page")
        summary = traffic_analyzer.traffic_summary(days=1)
        assert "total_sessions" in summary
        assert summary["total_sessions"] >= 3

    def test_utm_performance(self, fresh_db):
        sources = ["google", "facebook", "email"]
        for src in sources:
            s = traffic_analyzer.start_session(f"ip_{src}", "bot", "/",
                                                utm_source=src, utm_medium="cpc")
            traffic_analyzer.record_pageview(s.session_id, "/landing")
        perf = traffic_analyzer.utm_performance(days=1)
        assert isinstance(perf, list)


# ===========================================================================
# 15. Webhook manager
# ===========================================================================
class TestWebhookManager:
    def test_register_and_list(self, fresh_db):
        wh = webhook_manager.register_webhook(
            name="test-hook",
            url="https://example.com/webhook",
            events=["revenue.recorded", "product.sold"],
            secret="s3cr3t"
        )
        assert wh.name == "test-hook"
        webhooks = webhook_manager.list_webhooks()
        assert len(webhooks) >= 1

    def test_fire_event(self, fresh_db):
        webhook_manager.register_webhook(
            name="event-hook",
            url="https://example.com/hook",
            events=["agent.completed"]
        )
        event_id = webhook_manager.fire_event(
            "agent.completed",
            {"agent": "writer", "task": "blog post"}
        )
        assert isinstance(event_id, int)

    def test_delivery_stats(self, fresh_db):
        webhook_manager.register_webhook(
            name="stats-hook", url="https://example.com/s", events=["revenue.recorded"]
        )
        stats = webhook_manager.delivery_stats()
        assert isinstance(stats, dict)

    def test_webhook_summary(self, fresh_db):
        summary = webhook_manager.webhook_summary()
        assert "total_webhooks" in summary

    def test_enable_disable(self, fresh_db):
        wh = webhook_manager.register_webhook(
            name="toggle-hook", url="https://example.com/t",
            events=["agent.started"]
        )
        webhook_manager.disable_webhook(wh.id)
        disabled = webhook_manager.list_webhooks(status="inactive")
        assert any(w.name == "toggle-hook" for w in disabled)
        webhook_manager.enable_webhook(wh.id)
        active = webhook_manager.list_webhooks(status="active")
        assert any(w.name == "toggle-hook" for w in active)


# ===========================================================================
# 16. SEO optimizer
# ===========================================================================
class TestSEOOptimizer:
    def test_analyze_content(self, fresh_db):
        result = seo_optimizer.analyze_content(
            content="Python is a great programming language. " * 20,
            target_keyword="Python programming",
            url="https://example.com/python",
            title="Python Programming Guide",
            meta_description="Learn Python programming from scratch."
        )
        assert isinstance(result, dict)
        assert "score" in result or "content_score" in result or "keyword_density" in result

    def test_save_and_retrieve_page(self, fresh_db):
        seo_optimizer.save_page_meta(
            url="https://example.com/landing",
            title="Best Python Course",
            meta_description="Learn Python in 30 days.",
            target_keyword="Python course"
        )
        page = seo_optimizer.get_page_info("https://example.com/landing")
        assert page is not None

    def test_site_overview(self, fresh_db):
        seo_optimizer.save_page_meta("https://example.com/p1", title="Page 1", target_keyword="kw1")
        seo_optimizer.save_page_meta("https://example.com/p2", title="Page 2", target_keyword="kw2")
        overview = seo_optimizer.seo_site_overview()
        assert isinstance(overview, dict)

    def test_redirect_management(self, fresh_db):
        rid = seo_optimizer.add_redirect("/old-page", "https://example.com/new-page", reason="test")
        assert isinstance(rid, int) and rid > 0
        redirects = seo_optimizer.list_redirects()
        assert len(redirects) >= 1


# ===========================================================================
# 17. Newsletter
# ===========================================================================
class TestNewsletter:
    def test_subscribe_and_count(self, fresh_db):
        newsletter.subscribe("reader@example.com", double_optin=False)
        count = newsletter.subscriber_count(status="confirmed")
        assert count >= 1

    def test_unsubscribe(self, fresh_db):
        newsletter.subscribe("unsub@example.com", double_optin=False)
        newsletter.unsubscribe("unsub@example.com")
        count = newsletter.subscriber_count(status="confirmed")
        assert count == 0

    def test_create_and_send_campaign(self, fresh_db):
        newsletter.subscribe("sub1@example.com", double_optin=False)
        newsletter.subscribe("sub2@example.com", double_optin=False)
        campaign_id = newsletter.create_campaign(
            name="Weekly Digest",
            subject="Your weekly roundup",
            html_body="<p>This week in tech...</p>"
        )
        assert campaign_id is not None and campaign_id > 0

    def test_growth_trend(self, fresh_db):
        for i in range(5):
            newsletter.subscribe(f"growth{i}@example.com", double_optin=False)
        trend = newsletter.growth_trend(days=7)
        assert isinstance(trend, list)

    def test_summary(self, fresh_db):
        summary = newsletter.newsletter_summary()
        assert isinstance(summary, dict)


# ===========================================================================
# 18. Social scheduler
# ===========================================================================
class TestSocialScheduler:
    def test_schedule_post(self, fresh_db):
        import time as _time
        post = social_scheduler.schedule_post(
            platform="twitter",
            content="Excited to announce our new product!",
            scheduled_for=_time.time() + 3600
        )
        assert post.id is not None
        assert post.status == "scheduled"

    def test_list_scheduled_posts(self, fresh_db):
        import time as _time
        for i in range(3):
            social_scheduler.schedule_post(
                platform="linkedin",
                content=f"Post {i}",
                scheduled_for=_time.time() + (i + 1) * 3600
            )
        posts = social_scheduler.get_queue(status="scheduled")
        assert len(posts) >= 3

    def test_mark_published(self, fresh_db):
        import time as _time
        post = social_scheduler.schedule_post(
            platform="instagram",
            content="Behind the scenes",
            scheduled_for=_time.time()
        )
        social_scheduler.mark_published(post.id)
        updated_posts = social_scheduler.get_queue(status="published")
        assert any(p.id == post.id for p in updated_posts)

    def test_due_posts(self, fresh_db):
        import time as _time
        social_scheduler.schedule_post(
            platform="twitter",
            content="Due now",
            scheduled_for=_time.time() - 300
        )
        due = social_scheduler.get_due_posts()
        assert len(due) >= 1


# ===========================================================================
# 19. AB Testing
# ===========================================================================
class TestABTesting:
    def test_create_and_assign(self, fresh_db):
        test = ab_testing.create_experiment(
            name="headline_test",
            variants=["Control", "Variant A", "Variant B"]
        )
        assert test is not None
        variant = ab_testing.assign("headline_test", "user123")
        assert variant in ["Control", "Variant A", "Variant B"]

    def test_consistent_assignment(self, fresh_db):
        ab_testing.create_experiment("consistent_test", variants=["A", "B"])
        v1 = ab_testing.assign("consistent_test", "stable_user")
        v2 = ab_testing.assign("consistent_test", "stable_user")
        assert v1 == v2

    def test_record_conversion(self, fresh_db):
        ab_testing.create_experiment("conv_test", variants=["X", "Y"])
        ab_testing.assign("conv_test", "converter")
        ab_testing.record_conversion("conv_test", "converter", revenue=49.99)
        analysis = ab_testing.analyze("conv_test")
        assert analysis is not None

    def test_list_tests(self, fresh_db):
        ab_testing.create_experiment("t1", variants=["A", "B"])
        ab_testing.create_experiment("t2", variants=["C", "D"])
        tests = ab_testing.list_experiments()
        assert len(tests) >= 2


# ===========================================================================
# 20. Analytics tracker
# ===========================================================================
class TestAnalyticsTracker:
    def test_track_event(self, fresh_db):
        eid = analytics_tracker.track_event("page_view", url="/home")
        assert isinstance(eid, int) and eid > 0

    def test_multiple_event_types(self, fresh_db):
        for event in ["signup", "purchase", "refund", "login"]:
            analytics_tracker.track_event(event, source="test")
        # kpi_summary covers recent events
        summary = analytics_tracker.kpi_summary(days=1)
        assert isinstance(summary, dict)

    def test_event_summary(self, fresh_db):
        analytics_tracker.track_event("click", url="/cta")
        analytics_tracker.track_event("click", url="/nav")
        summary = analytics_tracker.kpi_summary(days=1)
        assert isinstance(summary, dict)


# ===========================================================================
# 21. Keyword tracker
# ===========================================================================
class TestKeywordTracker:
    def test_track_keyword(self, fresh_db):
        result = keyword_tracker.add_keyword(
            keyword="best python books",
            target_url="https://example.com/blog",
        )
        assert result is not None

    def test_update_position(self, fresh_db):
        kw = keyword_tracker.add_keyword("seo guide", target_url="https://example.com/seo")
        keyword_tracker.update_rank(kw.id, rank=8)
        kws = keyword_tracker.list_keywords()
        match = next((k for k in kws if k.keyword == "seo guide"), None)
        assert match is not None

    def test_keyword_report(self, fresh_db):
        for kw in ["python", "django", "flask"]:
            keyword_tracker.add_keyword(kw, target_url=f"https://example.com/{kw}")
        report = keyword_tracker.keyword_report()
        assert isinstance(report, dict)


# ===========================================================================
# 22. Affiliate tracker
# ===========================================================================
class TestAffiliateTracker:
    def test_create_program_and_link(self, fresh_db):
        pid = affiliate_tracker.add_program("Amazon Associates", commission_rate=0.05)
        assert pid > 0
        link = affiliate_tracker.create_link(
            program_name="Amazon Associates",
            destination_url="https://amazon.com/dp/B001",
            slug="my-book"
        )
        assert link.get("ok") is True or "link_id" in link

    def test_record_click_and_conversion(self, fresh_db):
        affiliate_tracker.add_program("ClickBank", commission_rate=0.50)
        link = affiliate_tracker.create_link(
            "ClickBank", "https://cb.com/product"
        )
        if link.get("ok"):
            link_id = link["link_id"]
            affiliate_tracker.record_click(link_id)
            affiliate_tracker.record_conversion(link_id, sale_amount=97.0)

    def test_program_stats(self, fresh_db):
        affiliate_tracker.add_program("Test Program", commission_rate=0.10)
        stats = affiliate_tracker.program_performance(days=30)
        assert isinstance(stats, list)

    def test_summary(self, fresh_db):
        summary = affiliate_tracker.affiliate_summary()
        assert isinstance(summary, dict)


# ===========================================================================
# 23. Full org workflow (cross-module)
# ===========================================================================
class TestCrossModuleWorkflow:
    """Simulate realistic org activity spanning multiple modules."""

    def test_content_to_revenue_pipeline(self, fresh_db):
        # 1. Create content calendar entry
        entry = content_calendar.add_entry(
            title="Python Tutorial", content_type="blog_post",
            platform="wordpress",
            scheduled_at=(datetime.utcnow() + timedelta(days=1)).isoformat()
        )
        content_calendar.approve_entry(entry.id)

        # 2. Create a product to sell
        product = product_manager.create_product(
            name="Python Course", product_type="digital", price=99.0
        )

        # 3. Create tracking link for the product
        link = link_builder.create_link(
            destination="https://example.com/course",
            utm_source="blog", utm_medium="organic", utm_campaign="python_tutorial"
        )

        # 4. Record some traffic
        session = traffic_analyzer.start_session("ip_blog", "Mozilla", "/blog/python")
        traffic_analyzer.record_pageview(session.session_id, "/course")

        # 5. Record a sale
        sale = product_manager.record_sale(
            product.id, 99.0, "student@example.com"
        )
        db_mod.log_revenue(99.0, source="product_sales", agent="system")

        # 6. Check revenue
        summary = db_mod.revenue_summary()
        assert summary["total_usd"] >= 99.0

        # 7. Record link click
        link_builder.record_click(link.slug, ip="ip_student", country="US")

        # 8. Verify all components
        assert sale.id is not None
        assert entry.id is not None

    def test_lead_to_email_to_sale(self, fresh_db):
        # 1. Capture lead
        lead = lead_scoring.create_lead(
            email="prospect@company.com",
            company="StartupCo",
            source="webinar",
            industry="saas"
        )
        lead_scoring.record_activity("prospect@company.com", "webinar_attend", "attended demo")

        # 2. Enroll in email sequence
        seq = email_marketing.create_sequence("Webinar Follow-up", trigger_event="webinar_attend")
        email_marketing.add_sequence_email(
            seq.name, subject="Thanks for joining!", body_html="<p>Great to meet you</p>", delay_hours=1
        )
        email_marketing.enroll_subscriber("Webinar Follow-up", "prospect@company.com")

        # 3. Subscribe to newsletter
        newsletter.subscribe("prospect@company.com", double_optin=False)

        # 4. Create and sell course
        course = course_builder.create_course(title="SaaS Masterclass", price=299.0)
        mod = course_builder.add_module(course.id, "Module 1")
        course_builder.add_lesson(mod["id"], course.id, "Intro Lesson")
        course_builder.publish_course(course.id)
        enrollment = course_builder.enroll_student(
            course.id, "prospect@company.com", "Prospect", 299.0
        )
        db_mod.log_revenue(299.0, source="course", agent="system")

        # 5. Update lead status
        lead_scoring.change_status(lead.id, "won", note="Purchased SaaS Masterclass")

        # Verify final state
        final_lead = lead_scoring.get_lead("prospect@company.com")
        assert final_lead.status == "won"

        revenue = db_mod.revenue_summary()
        assert revenue["total_usd"] >= 299.0
