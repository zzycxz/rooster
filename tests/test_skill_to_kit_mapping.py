"""
Test suite for SkillIndex → ToolRouter Kit mapping.

Verifies that:
- hint_to_forced_kits() dynamically resolves kits from SKILL.md frontmatter
- Every SKILL.md that declares kits has them correctly parsed
- All built-in skills have kits declared
- All declared kits exist in the tool registry
"""

import pytest


class TestHintToForcedKits:
    """Verify the dynamic skill hint → Kit resolution."""

    @pytest.fixture(autouse=True)
    def _init(self):
        """Ensure SkillIndex is initialized before tests."""
        from agents.skill_index import get_skill_index
        get_skill_index()

    def test_visual_control_maps_to_vision(self):
        from agents.skill_index import hint_to_forced_kits
        assert hint_to_forced_kits("visual-control") == {"Vision"}

    def test_browser_automation_maps_to_browser(self):
        from agents.skill_index import hint_to_forced_kits
        assert hint_to_forced_kits("browser-automation") == {"Browser"}

    def test_web_search_maps_to_browser(self):
        from agents.skill_index import hint_to_forced_kits
        assert hint_to_forced_kits("web-search") == {"Browser"}

    def test_resource_downloader_maps_to_multimedia_and_browser(self):
        from agents.skill_index import hint_to_forced_kits
        result = hint_to_forced_kits("resource-downloader")
        assert "Multimedia" in result
        assert "Browser" in result

    def test_coding_agent_maps_to_interpreter(self):
        from agents.skill_index import hint_to_forced_kits
        assert hint_to_forced_kits("coding-agent") == {"Interpreter"}

    def test_office_documents_maps_to_office_and_filesystem(self):
        from agents.skill_index import hint_to_forced_kits
        result = hint_to_forced_kits("office-documents")
        assert "Office" in result
        assert "FileSystem" in result

    def test_unknown_skill_returns_empty_set(self):
        from agents.skill_index import hint_to_forced_kits
        assert hint_to_forced_kits("nonexistent_skill") == set()


class TestBuiltinSkillsHaveKits:
    """Verify all built-in SkillIndex entries declare kits."""

    def test_all_builtin_skills_have_kits(self):
        from agents.skill_index import SkillIndex
        for skill in SkillIndex._BUILTIN_SKILLS:
            name = skill["name"]
            kits = skill.get("kits", [])
            assert len(kits) > 0, (
                f"Built-in skill '{name}' has no 'kits' field. "
                "Add e.g. kits=['Browser'] to the entry."
            )

    def test_builtin_skill_kits_exist_in_registry(self):
        """Every Kit in built-in skills should exist in the tool registry."""
        from agents.skill_index import SkillIndex
        from toolset.registry import _ensure_initialized, global_tool_registry

        _ensure_initialized()
        registry_kits = set(global_tool_registry.get_kit_names())

        for skill in SkillIndex._BUILTIN_SKILLS:
            for kit in skill.get("kits", []):
                assert kit in registry_kits, (
                    f"Built-in skill '{skill['name']}' references Kit '{kit}' "
                    f"not found in registry. Available: {registry_kits}"
                )


class TestSkillMdKitsParsed:
    """Verify SKILL.md files with kits are correctly parsed by SkillIndex."""

    @pytest.fixture(autouse=True)
    def _init(self):
        from agents.skill_index import get_skill_index
        get_skill_index()

    def test_all_skill_md_kits_exist_in_registry(self):
        """Every Kit declared in SKILL.md frontmatter should exist in registry."""
        from agents.skill_index import get_skill_index
        from toolset.registry import _ensure_initialized, global_tool_registry

        _ensure_initialized()
        registry_kits = set(global_tool_registry.get_kit_names())

        idx = get_skill_index()
        for entry in idx._index:
            for kit in entry.get("kits", []):
                assert kit in registry_kits, (
                    f"Skill '{entry['name']}' references Kit '{kit}' "
                    f"not found in registry. Available: {registry_kits}"
                )

    def test_skill_md_kits_match_hint_to_forced_kits(self):
        """hint_to_forced_kits() should return the same kits as parsed from SKILL.md."""
        from agents.skill_index import get_skill_index, hint_to_forced_kits

        idx = get_skill_index()
        for entry in idx._index:
            name = entry.get("name", "")
            expected_kits = set(entry.get("kits", []))
            actual_kits = hint_to_forced_kits(name)
            assert actual_kits == expected_kits, (
                f"Skill '{name}': hint_to_forced_kits returned {actual_kits}, "
                f"but index has {expected_kits}"
            )


class TestGetMaxRiskLevelForPolicy:
    """Verify the permission_policy interface (隐患2 fix)."""

    def test_strict_returns_medium(self):
        """strict 策略：隐藏 high/critical，暴露 low + medium"""
        from utils.permission_policy import get_max_risk_level_for_policy
        assert get_max_risk_level_for_policy("strict") == "medium"

    def test_balanced_returns_none(self):
        """balanced 策略：不隐藏任何工具，high 工具执行时需确认即可"""
        from utils.permission_policy import get_max_risk_level_for_policy
        assert get_max_risk_level_for_policy("balanced") is None

    def test_permissive_returns_none(self):
        """permissive 策略：不过滤"""
        from utils.permission_policy import get_max_risk_level_for_policy
        assert get_max_risk_level_for_policy("permissive") is None

    def test_unknown_policy_defaults_to_balanced(self):
        """未知策略回退到 balanced（不过滤）"""
        from utils.permission_policy import get_max_risk_level_for_policy
        assert get_max_risk_level_for_policy("nonexistent") is None
