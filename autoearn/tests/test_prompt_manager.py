"""Tests for core/prompt_manager.py — versioned prompt template management."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_module(tmp_db: Path):
    """Return prompt_manager re-initialised against a fresh temp DB."""
    import core.prompt_manager as mod

    mod._schema_ready = False
    mod._DB_PATH = tmp_db
    return mod


class _ManagerBase(unittest.TestCase):
    """Each test class gets its own isolated SQLite database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "pm_test.db"
        self.mod = _make_module(self.db_path)

    def tearDown(self):
        self.mod._schema_ready = False
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# BUILTIN_TEMPLATES constant
# ---------------------------------------------------------------------------

class TestBuiltinTemplatesConstant(_ManagerBase):
    def test_builtin_templates_is_dict(self):
        self.assertIsInstance(self.mod.BUILTIN_TEMPLATES, dict)

    def test_builtin_templates_has_16_entries(self):
        self.assertEqual(len(self.mod.BUILTIN_TEMPLATES), 16)

    def test_chain_of_thought_present(self):
        self.assertIn("chain_of_thought", self.mod.BUILTIN_TEMPLATES)

    def test_role_expert_present(self):
        self.assertIn("role_expert", self.mod.BUILTIN_TEMPLATES)

    def test_seo_article_present(self):
        self.assertIn("seo_article", self.mod.BUILTIN_TEMPLATES)

    def test_social_caption_present(self):
        self.assertIn("social_caption", self.mod.BUILTIN_TEMPLATES)

    def test_each_builtin_has_template_key(self):
        for name, meta in self.mod.BUILTIN_TEMPLATES.items():
            self.assertIn("template", meta, f"'{name}' missing 'template' key")

    def test_each_builtin_has_description_key(self):
        for name, meta in self.mod.BUILTIN_TEMPLATES.items():
            self.assertIn("description", meta, f"'{name}' missing 'description' key")

    def test_template_variable_re_pattern(self):
        """Verify the regex extracts {{var}} variables correctly."""
        text = "Hello {{name}}, your {{product}} is ready."
        matches = self.mod.TEMPLATE_VARIABLES_RE.findall(text)
        self.assertIn("name", matches)
        self.assertIn("product", matches)


# ---------------------------------------------------------------------------
# _seed_builtins / built-in seeding on first use
# ---------------------------------------------------------------------------

class TestSeedBuiltins(_ManagerBase):
    def test_builtins_seeded_on_first_use(self):
        # Trigger _ensure() which calls _seed_builtins()
        templates = self.mod.list_templates(category="builtin")
        self.assertEqual(len(templates), 16)

    def test_builtin_names_prefixed(self):
        templates = self.mod.list_templates(category="builtin")
        for t in templates:
            self.assertTrue(t.name.startswith("builtin:"),
                            f"Expected 'builtin:' prefix, got: {t.name}")

    def test_builtin_category_is_builtin(self):
        templates = self.mod.list_templates(category="builtin")
        self.assertTrue(all(t.category == "builtin" for t in templates))

    def test_builtin_chain_of_thought_retrievable(self):
        t = self.mod.get_template("builtin:chain_of_thought")
        self.assertIsNotNone(t)

    def test_builtin_seo_article_retrievable(self):
        t = self.mod.get_template("builtin:seo_article")
        self.assertIsNotNone(t)

    def test_builtin_seed_idempotent(self):
        """Calling _seed_builtins twice should not duplicate templates."""
        self.mod.list_templates()  # triggers first seed
        self.mod._seed_builtins()
        templates = self.mod.list_templates(category="builtin")
        # Should still be 16 active builtins (re-saving bumps version but only one active)
        self.assertEqual(len(templates), 16)

    def test_builtin_templates_have_variables(self):
        t = self.mod.get_template("builtin:role_expert")
        self.assertIsNotNone(t)
        self.assertGreater(len(t.variables), 0)


# ---------------------------------------------------------------------------
# save_template
# ---------------------------------------------------------------------------

class TestSaveTemplate(_ManagerBase):
    def test_save_returns_prompt_template(self):
        pt = self.mod.save_template("my_template", "Hello {{name}}")
        self.assertIsNotNone(pt)

    def test_save_assigns_id(self):
        pt = self.mod.save_template("id_test", "Content {{var}}")
        self.assertGreater(pt.id, 0)

    def test_save_stores_name(self):
        pt = self.mod.save_template("name_check", "Template body")
        self.assertEqual(pt.name, "name_check")

    def test_save_stores_template_body(self):
        pt = self.mod.save_template("body_check", "My template body {{x}}")
        self.assertEqual(pt.template, "My template body {{x}}")

    def test_save_stores_description(self):
        pt = self.mod.save_template("desc_check", "Body", description="A test desc")
        self.assertEqual(pt.description, "A test desc")

    def test_save_stores_category(self):
        pt = self.mod.save_template("cat_check", "Body", category="marketing")
        self.assertEqual(pt.category, "marketing")

    def test_save_first_version_is_1(self):
        pt = self.mod.save_template("v1_check", "First version")
        self.assertEqual(pt.version, 1)

    def test_save_is_active(self):
        pt = self.mod.save_template("active_check", "Active template")
        self.assertTrue(pt.is_active)

    def test_save_default_category_general(self):
        pt = self.mod.save_template("default_cat", "Body")
        self.assertEqual(pt.category, "general")

    def test_save_created_by_stored(self):
        pt = self.mod.save_template("creator_check", "Body", created_by="agent_007")
        self.assertEqual(pt.created_by, "agent_007")


# ---------------------------------------------------------------------------
# Auto-versioning
# ---------------------------------------------------------------------------

class TestAutoVersioning(_ManagerBase):
    def test_save_same_name_twice_creates_v2(self):
        self.mod.save_template("versioned", "Version 1 content")
        pt2 = self.mod.save_template("versioned", "Version 2 content")
        self.assertEqual(pt2.version, 2)

    def test_save_same_name_three_times_creates_v3(self):
        self.mod.save_template("multi_version", "v1")
        self.mod.save_template("multi_version", "v2")
        pt3 = self.mod.save_template("multi_version", "v3")
        self.assertEqual(pt3.version, 3)

    def test_only_latest_is_active(self):
        self.mod.save_template("active_latest", "v1")
        self.mod.save_template("active_latest", "v2")
        self.mod.save_template("active_latest", "v3")
        pt = self.mod.get_template("active_latest")
        self.assertEqual(pt.version, 3)

    def test_get_without_version_returns_latest(self):
        self.mod.save_template("latest_test", "First")
        self.mod.save_template("latest_test", "Second")
        self.mod.save_template("latest_test", "Third")
        pt = self.mod.get_template("latest_test")
        self.assertEqual(pt.template, "Third")

    def test_get_specific_version(self):
        self.mod.save_template("specific_v", "Content v1")
        self.mod.save_template("specific_v", "Content v2")
        pt_v1 = self.mod.get_template("specific_v", version=1)
        self.assertIsNotNone(pt_v1)
        self.assertEqual(pt_v1.template, "Content v1")

    def test_previous_version_deactivated(self):
        self.mod.save_template("deactivate_old", "v1")
        self.mod.save_template("deactivate_old", "v2")
        pt_v1 = self.mod.get_template("deactivate_old", version=1)
        self.assertFalse(pt_v1.is_active)

    def test_list_returns_only_active_versions(self):
        self.mod.save_template("list_active", "v1")
        self.mod.save_template("list_active", "v2")
        templates = self.mod.list_templates()
        matches = [t for t in templates if t.name == "list_active"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].version, 2)


# ---------------------------------------------------------------------------
# get_template
# ---------------------------------------------------------------------------

class TestGetTemplate(_ManagerBase):
    def test_get_existing_template(self):
        self.mod.save_template("get_test", "Get me")
        pt = self.mod.get_template("get_test")
        self.assertIsNotNone(pt)

    def test_get_nonexistent_returns_none(self):
        # Trigger _ensure first so builtins are seeded
        self.mod.list_templates()
        pt = self.mod.get_template("does_not_exist_xyz999")
        self.assertIsNone(pt)

    def test_get_returns_prompt_template_instance(self):
        self.mod.save_template("type_test", "Template body")
        pt = self.mod.get_template("type_test")
        self.assertIsInstance(pt, self.mod.PromptTemplate)

    def test_get_correct_template_body(self):
        self.mod.save_template("body_test", "My exact body {{x}}")
        pt = self.mod.get_template("body_test")
        self.assertEqual(pt.template, "My exact body {{x}}")


# ---------------------------------------------------------------------------
# PromptTemplate.variables
# ---------------------------------------------------------------------------

class TestVariableExtraction(_ManagerBase):
    def test_no_variables_empty_list(self):
        pt = self.mod.PromptTemplate(name="t", template="No variables here")
        self.assertEqual(pt.variables, [])

    def test_single_variable_extracted(self):
        pt = self.mod.PromptTemplate(name="t", template="Hello {{name}}")
        self.assertIn("name", pt.variables)

    def test_multiple_variables_extracted(self):
        pt = self.mod.PromptTemplate(name="t",
                                      template="{{first}} and {{second}} and {{third}}")
        vars_ = pt.variables
        self.assertIn("first", vars_)
        self.assertIn("second", vars_)
        self.assertIn("third", vars_)

    def test_duplicate_variable_deduplicated(self):
        pt = self.mod.PromptTemplate(name="t",
                                      template="{{name}} is {{name}}")
        self.assertEqual(len(pt.variables), 1)

    def test_saved_template_variables_accessible(self):
        self.mod.save_template("var_extract", "Write about {{topic}} for {{audience}}")
        pt = self.mod.get_template("var_extract")
        self.assertIn("topic", pt.variables)
        self.assertIn("audience", pt.variables)


# ---------------------------------------------------------------------------
# render_template
# ---------------------------------------------------------------------------

class TestRenderTemplate(_ManagerBase):
    def test_render_basic_substitution(self):
        self.mod.save_template("render_basic", "Hello {{name}}!")
        result = self.mod.render_template("render_basic", {"name": "World"})
        self.assertEqual(result, "Hello World!")

    def test_render_multiple_variables(self):
        self.mod.save_template("render_multi",
                                "Dear {{recipient}}, your {{product}} is ready.")
        result = self.mod.render_template("render_multi",
                                           {"recipient": "Alice", "product": "order"})
        self.assertIn("Alice", result)
        self.assertIn("order", result)

    def test_render_missing_variable_returns_error(self):
        self.mod.save_template("render_missing", "Hello {{name}} and {{title}}")
        result = self.mod.render_template("render_missing", {"name": "Bob"})
        self.assertIn("ERROR", result)
        self.assertIn("title", result)

    def test_render_nonexistent_template_returns_error(self):
        # Trigger _ensure first
        self.mod.list_templates()
        result = self.mod.render_template("nonexistent_template_xyz", {})
        self.assertIn("ERROR", result)

    def test_render_all_variables_provided(self):
        self.mod.save_template("render_complete",
                                "{{a}} - {{b}} - {{c}}")
        result = self.mod.render_template("render_complete",
                                           {"a": "X", "b": "Y", "c": "Z"})
        self.assertEqual(result, "X - Y - Z")

    def test_render_no_variables_unchanged(self):
        self.mod.save_template("no_vars_render", "Static content only")
        result = self.mod.render_template("no_vars_render", {})
        self.assertEqual(result, "Static content only")

    def test_render_builtin_template(self):
        t = self.mod.get_template("builtin:chain_of_thought")
        self.assertIsNotNone(t)
        vars_ = {v: f"value_{v}" for v in t.variables}
        result = self.mod.render_template("builtin:chain_of_thought", vars_)
        self.assertNotIn("ERROR", result)
        self.assertNotIn("{{", result)

    def test_render_substitutes_all_occurrences(self):
        self.mod.save_template("repeat_var", "{{word}} and {{word}} again")
        result = self.mod.render_template("repeat_var", {"word": "hello"})
        self.assertEqual(result.count("hello"), 2)

    def test_render_returns_string(self):
        self.mod.save_template("str_return", "Text {{x}}")
        result = self.mod.render_template("str_return", {"x": "val"})
        self.assertIsInstance(result, str)


# ---------------------------------------------------------------------------
# PromptTemplate.missing_variables
# ---------------------------------------------------------------------------

class TestMissingVariables(_ManagerBase):
    def test_no_missing_when_all_provided(self):
        pt = self.mod.PromptTemplate(name="t", template="{{a}} {{b}}")
        missing = pt.missing_variables({"a": "1", "b": "2"})
        self.assertEqual(missing, [])

    def test_missing_returns_unfilled_vars(self):
        pt = self.mod.PromptTemplate(name="t", template="{{a}} {{b}} {{c}}")
        missing = pt.missing_variables({"a": "1"})
        self.assertIn("b", missing)
        self.assertIn("c", missing)

    def test_extra_variables_not_flagged(self):
        pt = self.mod.PromptTemplate(name="t", template="{{a}}")
        missing = pt.missing_variables({"a": "1", "z": "extra"})
        self.assertEqual(missing, [])

    def test_no_variables_template_no_missing(self):
        pt = self.mod.PromptTemplate(name="t", template="No vars")
        missing = pt.missing_variables({})
        self.assertEqual(missing, [])


# ---------------------------------------------------------------------------
# rollback_template
# ---------------------------------------------------------------------------

class TestRollbackTemplate(_ManagerBase):
    def test_rollback_returns_string(self):
        self.mod.save_template("rollback_me", "v1")
        self.mod.save_template("rollback_me", "v2")
        result = self.mod.rollback_template("rollback_me")
        self.assertIsInstance(result, str)

    def test_rollback_restores_previous_version(self):
        self.mod.save_template("rollback_test", "First version content")
        self.mod.save_template("rollback_test", "Second version content")
        self.mod.rollback_template("rollback_test")
        pt = self.mod.get_template("rollback_test")
        self.assertEqual(pt.template, "First version content")

    def test_rollback_message_contains_version(self):
        self.mod.save_template("rollback_msg", "v1")
        self.mod.save_template("rollback_msg", "v2")
        result = self.mod.rollback_template("rollback_msg")
        self.assertIn("1", result)  # version 1

    def test_rollback_single_version_returns_no_previous(self):
        self.mod.save_template("single_version", "Only one version")
        result = self.mod.rollback_template("single_version")
        self.assertIn("No previous", result)

    def test_rollback_makes_old_version_active(self):
        self.mod.save_template("active_after_rollback", "v1")
        self.mod.save_template("active_after_rollback", "v2")
        self.mod.rollback_template("active_after_rollback")
        pt = self.mod.get_template("active_after_rollback")
        self.assertTrue(pt.is_active)
        self.assertEqual(pt.version, 1)

    def test_rollback_deactivates_current_version(self):
        self.mod.save_template("deactivate_current", "v1")
        self.mod.save_template("deactivate_current", "v2")
        self.mod.rollback_template("deactivate_current")
        pt_v2 = self.mod.get_template("deactivate_current", version=2)
        self.assertFalse(pt_v2.is_active)

    def test_rollback_triple_versioned(self):
        self.mod.save_template("triple_rollback", "v1")
        self.mod.save_template("triple_rollback", "v2")
        self.mod.save_template("triple_rollback", "v3")
        self.mod.rollback_template("triple_rollback")
        pt = self.mod.get_template("triple_rollback")
        # After rollback from v3, v2 should be active
        self.assertEqual(pt.version, 2)


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------

class TestListTemplates(_ManagerBase):
    def test_list_returns_list(self):
        result = self.mod.list_templates()
        self.assertIsInstance(result, list)

    def test_list_includes_saved_template(self):
        self.mod.save_template("listed_template", "Body")
        templates = self.mod.list_templates()
        names = [t.name for t in templates]
        self.assertIn("listed_template", names)

    def test_list_filtered_by_category(self):
        self.mod.save_template("cat_a_template", "Body", category="cat_a")
        self.mod.save_template("cat_b_template", "Body", category="cat_b")
        result = self.mod.list_templates(category="cat_a")
        self.assertTrue(all(t.category == "cat_a" for t in result))

    def test_list_only_active_templates(self):
        self.mod.save_template("active_only", "v1")
        self.mod.save_template("active_only", "v2")
        templates = self.mod.list_templates()
        active_only_versions = [t.version for t in templates if t.name == "active_only"]
        self.assertEqual(active_only_versions, [2])

    def test_list_builtin_category(self):
        templates = self.mod.list_templates(category="builtin")
        self.assertEqual(len(templates), 16)

    def test_list_no_filter_includes_all_categories(self):
        self.mod.save_template("general_tmpl", "Body", category="general")
        templates = self.mod.list_templates()
        categories = {t.category for t in templates}
        self.assertIn("general", categories)
        self.assertIn("builtin", categories)

    def test_list_returns_prompt_template_instances(self):
        self.mod.save_template("instance_check", "Body")
        templates = self.mod.list_templates()
        for t in templates:
            self.assertIsInstance(t, self.mod.PromptTemplate)


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------

class TestRecordUsage(_ManagerBase):
    def test_record_usage_no_error(self):
        self.mod.save_template("usage_test", "Body {{x}}")
        self.mod.record_usage("usage_test", agent="test_agent",
                               variables={"x": "val"}, success=True, latency_ms=42.0)

    def test_record_usage_success_increments_success_count(self):
        self.mod.save_template("success_count_test", "Body")
        self.mod.record_usage("success_count_test", agent="agent1",
                               variables={}, success=True)
        pt = self.mod.get_template("success_count_test")
        self.assertEqual(pt.success_count, 1)

    def test_record_usage_failure_increments_failure_count(self):
        self.mod.save_template("failure_count_test", "Body")
        self.mod.record_usage("failure_count_test", agent="agent1",
                               variables={}, success=False)
        pt = self.mod.get_template("failure_count_test")
        self.assertEqual(pt.failure_count, 1)

    def test_record_usage_increments_usage_count(self):
        self.mod.save_template("usage_count_test", "Body")
        self.mod.record_usage("usage_count_test", agent="agent1", variables={})
        self.mod.record_usage("usage_count_test", agent="agent1", variables={})
        pt = self.mod.get_template("usage_count_test")
        self.assertEqual(pt.usage_count, 2)

    def test_record_usage_nonexistent_template_no_error(self):
        # Should silently skip (no exception)
        self.mod.list_templates()  # ensure seeded
        self.mod.record_usage("nonexistent_xyz999", agent="agent1", variables={})

    def test_record_usage_success_rate_calculation(self):
        self.mod.save_template("rate_test", "Body")
        self.mod.record_usage("rate_test", agent="a", variables={}, success=True)
        self.mod.record_usage("rate_test", agent="a", variables={}, success=True)
        self.mod.record_usage("rate_test", agent="a", variables={}, success=False)
        pt = self.mod.get_template("rate_test")
        self.assertAlmostEqual(pt.success_rate, 2/3, places=2)

    def test_record_usage_mixed_success_failure(self):
        self.mod.save_template("mixed_rate", "Body")
        for _ in range(3):
            self.mod.record_usage("mixed_rate", agent="a", variables={}, success=True)
        for _ in range(1):
            self.mod.record_usage("mixed_rate", agent="a", variables={}, success=False)
        pt = self.mod.get_template("mixed_rate")
        self.assertEqual(pt.success_count, 3)
        self.assertEqual(pt.failure_count, 1)


# ---------------------------------------------------------------------------
# template_stats
# ---------------------------------------------------------------------------

class TestTemplateStats(_ManagerBase):
    def test_stats_returns_dict(self):
        result = self.mod.template_stats()
        self.assertIsInstance(result, dict)

    def test_stats_contains_total_templates(self):
        result = self.mod.template_stats()
        self.assertIn("total_templates", result)

    def test_stats_contains_by_category(self):
        result = self.mod.template_stats()
        self.assertIn("by_category", result)

    def test_stats_contains_top_used(self):
        result = self.mod.template_stats()
        self.assertIn("top_used", result)

    def test_stats_total_includes_builtins(self):
        result = self.mod.template_stats()
        self.assertGreaterEqual(result["total_templates"], 16)

    def test_stats_by_category_has_builtin(self):
        result = self.mod.template_stats()
        self.assertIn("builtin", result["by_category"])

    def test_stats_builtin_count_16(self):
        result = self.mod.template_stats()
        self.assertEqual(result["by_category"]["builtin"], 16)

    def test_stats_top_used_is_list(self):
        result = self.mod.template_stats()
        self.assertIsInstance(result["top_used"], list)

    def test_stats_after_usage_reflects_count(self):
        self.mod.save_template("stats_usage", "Body")
        self.mod.record_usage("stats_usage", agent="a", variables={})
        self.mod.record_usage("stats_usage", agent="a", variables={})
        result = self.mod.template_stats()
        top = result["top_used"]
        names = [item["name"] for item in top]
        self.assertIn("stats_usage", names)


# ---------------------------------------------------------------------------
# delete_template
# ---------------------------------------------------------------------------

class TestDeleteTemplate(_ManagerBase):
    def test_delete_returns_string(self):
        self.mod.save_template("to_delete", "Body")
        result = self.mod.delete_template("to_delete")
        self.assertIsInstance(result, str)

    def test_delete_removes_template(self):
        self.mod.save_template("delete_me", "Body")
        self.mod.delete_template("delete_me")
        # Trigger _ensure through list
        templates = self.mod.list_templates()
        self.assertFalse(any(t.name == "delete_me" for t in templates))

    def test_delete_removes_all_versions(self):
        self.mod.save_template("delete_all_v", "v1")
        self.mod.save_template("delete_all_v", "v2")
        self.mod.delete_template("delete_all_v")
        pt_v1 = self.mod.get_template("delete_all_v", version=1)
        pt_v2 = self.mod.get_template("delete_all_v", version=2)
        self.assertIsNone(pt_v1)
        self.assertIsNone(pt_v2)


# ---------------------------------------------------------------------------
# Tool wrappers
# ---------------------------------------------------------------------------

class TestSavePromptTool(_ManagerBase):
    def test_save_prompt_tool_returns_string(self):
        result = self.mod.save_prompt_tool("tool_template", "Hello {{name}}")
        self.assertIsInstance(result, str)

    def test_save_prompt_tool_contains_name(self):
        result = self.mod.save_prompt_tool("my_tool_tmpl", "Body")
        self.assertIn("my_tool_tmpl", result)

    def test_save_prompt_tool_contains_version(self):
        result = self.mod.save_prompt_tool("v_tool_tmpl", "Body")
        self.assertIn("v1", result)

    def test_save_prompt_tool_lists_variables(self):
        result = self.mod.save_prompt_tool("var_tool", "Hello {{name}} and {{title}}")
        self.assertIn("name", result)
        self.assertIn("title", result)

    def test_save_prompt_tool_with_description(self):
        result = self.mod.save_prompt_tool("desc_tool", "Body",
                                            description="My description")
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_save_prompt_tool_with_category(self):
        result = self.mod.save_prompt_tool("cat_tool", "Body", category="outreach")
        self.assertIsInstance(result, str)
        self.assertNotIn("ERROR", result)

    def test_save_prompt_tool_version_increments(self):
        self.mod.save_prompt_tool("increment_tool", "v1")
        result = self.mod.save_prompt_tool("increment_tool", "v2")
        self.assertIn("v2", result)


class TestRenderPromptTool(_ManagerBase):
    def test_render_prompt_tool_returns_string(self):
        self.mod.save_template("render_tool_tmpl", "Text {{x}}")
        result = self.mod.render_prompt_tool("render_tool_tmpl", {"x": "value"})
        self.assertIsInstance(result, str)

    def test_render_prompt_tool_substitutes_vars(self):
        self.mod.save_template("render_sub_tool", "Hello {{who}}")
        result = self.mod.render_prompt_tool("render_sub_tool", {"who": "World"})
        self.assertEqual(result, "Hello World")

    def test_render_prompt_tool_missing_vars_error(self):
        self.mod.save_template("render_miss_tool", "{{a}} and {{b}}")
        result = self.mod.render_prompt_tool("render_miss_tool", {"a": "1"})
        self.assertIn("ERROR", result)

    def test_render_prompt_tool_none_variables_defaults_empty(self):
        self.mod.save_template("render_none_vars", "Static text")
        result = self.mod.render_prompt_tool("render_none_vars", None)
        self.assertEqual(result, "Static text")

    def test_render_prompt_tool_nonexistent_returns_error(self):
        self.mod.list_templates()  # seed builtins
        result = self.mod.render_prompt_tool("nonexistent_template_render_xyz", {})
        self.assertIn("ERROR", result)


class TestListPromptsTool(_ManagerBase):
    def test_list_prompts_tool_returns_string(self):
        result = self.mod.list_prompts_tool()
        self.assertIsInstance(result, str)

    def test_list_prompts_tool_returns_json(self):
        result = self.mod.list_prompts_tool()
        data = json.loads(result)
        self.assertIsInstance(data, list)

    def test_list_prompts_tool_items_have_required_keys(self):
        result = self.mod.list_prompts_tool()
        data = json.loads(result)
        if data:
            item = data[0]
            for key in ("name", "category", "version", "variables", "usage", "success_rate"):
                self.assertIn(key, item)

    def test_list_prompts_tool_category_filter(self):
        self.mod.save_template("filter_tool_tmpl", "Body", category="seo")
        result = self.mod.list_prompts_tool(category="seo")
        data = json.loads(result)
        self.assertTrue(all(item["category"] == "seo" for item in data))

    def test_list_prompts_tool_includes_builtins(self):
        result = self.mod.list_prompts_tool()
        data = json.loads(result)
        builtin_count = sum(1 for item in data if item["category"] == "builtin")
        self.assertEqual(builtin_count, 16)


class TestPromptStatsTool(_ManagerBase):
    def test_prompt_stats_tool_returns_string(self):
        result = self.mod.prompt_stats_tool()
        self.assertIsInstance(result, str)

    def test_prompt_stats_tool_returns_json(self):
        result = self.mod.prompt_stats_tool()
        data = json.loads(result)
        self.assertIsInstance(data, dict)

    def test_prompt_stats_tool_contains_total(self):
        result = self.mod.prompt_stats_tool()
        data = json.loads(result)
        self.assertIn("total_templates", data)

    def test_prompt_stats_tool_contains_by_category(self):
        result = self.mod.prompt_stats_tool()
        data = json.loads(result)
        self.assertIn("by_category", data)

    def test_prompt_stats_tool_contains_top_used(self):
        result = self.mod.prompt_stats_tool()
        data = json.loads(result)
        self.assertIn("top_used", data)


# ---------------------------------------------------------------------------
# PromptTemplate dataclass properties
# ---------------------------------------------------------------------------

class TestPromptTemplateDataclass(_ManagerBase):
    def test_success_rate_zero_with_no_usage(self):
        pt = self.mod.PromptTemplate(name="t", template="Body")
        self.assertEqual(pt.success_rate, 0.0)

    def test_success_rate_100_percent(self):
        pt = self.mod.PromptTemplate(name="t", template="Body",
                                      success_count=10, failure_count=0)
        self.assertAlmostEqual(pt.success_rate, 1.0)

    def test_success_rate_50_percent(self):
        pt = self.mod.PromptTemplate(name="t", template="Body",
                                      success_count=5, failure_count=5)
        self.assertAlmostEqual(pt.success_rate, 0.5)

    def test_render_replaces_variable(self):
        pt = self.mod.PromptTemplate(name="t", template="Hello {{name}}")
        result = pt.render({"name": "Alice"})
        self.assertEqual(result, "Hello Alice")

    def test_render_unreplaced_variable_stays(self):
        pt = self.mod.PromptTemplate(name="t", template="{{a}} and {{b}}")
        result = pt.render({"a": "X"})
        self.assertIn("{{b}}", result)

    def test_id_defaults_zero(self):
        pt = self.mod.PromptTemplate(name="t", template="Body")
        self.assertEqual(pt.id, 0)

    def test_default_version_1(self):
        pt = self.mod.PromptTemplate(name="t", template="Body")
        self.assertEqual(pt.version, 1)

    def test_default_category_general(self):
        pt = self.mod.PromptTemplate(name="t", template="Body")
        self.assertEqual(pt.category, "general")

    def test_is_active_default_true(self):
        pt = self.mod.PromptTemplate(name="t", template="Body")
        self.assertTrue(pt.is_active)


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

class TestPromptManagerPersistence(_ManagerBase):
    def test_template_survives_reinit(self):
        self.mod.save_template("persist_tmpl", "Persistent body")
        self.mod._schema_ready = False
        pt = self.mod.get_template("persist_tmpl")
        self.assertIsNotNone(pt)
        self.assertEqual(pt.template, "Persistent body")

    def test_version_history_persists(self):
        self.mod.save_template("persist_v", "v1")
        self.mod.save_template("persist_v", "v2")
        self.mod._schema_ready = False
        pt_v1 = self.mod.get_template("persist_v", version=1)
        pt_v2 = self.mod.get_template("persist_v", version=2)
        self.assertIsNotNone(pt_v1)
        self.assertIsNotNone(pt_v2)

    def test_usage_counts_persist(self):
        self.mod.save_template("usage_persist", "Body")
        self.mod.record_usage("usage_persist", agent="a", variables={}, success=True)
        self.mod._schema_ready = False
        pt = self.mod.get_template("usage_persist")
        self.assertEqual(pt.usage_count, 1)
        self.assertEqual(pt.success_count, 1)

    def test_rollback_persists(self):
        self.mod.save_template("rollback_persist", "v1 content")
        self.mod.save_template("rollback_persist", "v2 content")
        self.mod.rollback_template("rollback_persist")
        self.mod._schema_ready = False
        pt = self.mod.get_template("rollback_persist")
        self.assertEqual(pt.template, "v1 content")


if __name__ == "__main__":
    unittest.main()
