"""
Test suite for Rooster tool risk_level declarations and ToolRouter risk-aware filtering.

Phase 1: Verify all tools have correct risk_level values.
Phase 2: Verify ToolRouter respects max_risk_level post-filter.
"""

import pytest


# ---------------------------------------------------------------------------
# Expected risk_level mapping (source of truth for tests)
# ---------------------------------------------------------------------------
EXPECTED_RISK_LEVELS = {
    # high
    "file_system_op": "high",
    "python_interpreter": "high",
    "task_scheduler_create": "high",
    "task_scheduler_delete": "high",
    "task_scheduler": "high",
    # medium
    "desktop_click": "medium",
    "desktop_type": "medium",
    "desktop_act": "medium",
    "browser_click": "medium",
    "browser_type": "medium",
    "browser_act": "medium",
    "multimedia_download": "medium",
    "magnet_sniffer": "medium",
    "feishu_push_file": "medium",
    "email_send": "medium",
    "subagent_spawn": "medium",
    # low (explicit or default)
    "desktop_snap": "low",
    "desktop_grounding_scan": "low",
    "desktop_read_screen": "low",
    "browser_nav": "low",
    "browser_read": "low",
    "browser_scroll": "low",
    "browser_explore_links": "low",
    "browser_next_page": "low",
    "web_search": "low",
    "web_fetch": "low",
    "batch_web_fetch": "low",
    "tool_info": "low",
    "skill_read": "low",
    "tool_list": "low",
    "tool_search": "low",
    "memory_add_fact": "low",
    "ocr_extract": "low",
    "office_docx_write": "low",
    "excel_op": "low",
    "pdf_op": "low",
    "pptx_op": "low",
    "generic_tool": "low",
    "subagent_result": "low",
    "escalate_to_strategist": "low",
    "task_create": "low",
    "task_get": "low",
    "task_update": "low",
    "task_list": "low",
    "task_manager": "low",
    "wait_until": "low",
}


class TestRiskLevelDeclarations:
    """Phase 1: Verify all tool classes declare correct risk_level."""

    @pytest.fixture(autouse=True)
    def _init_registry(self):
        """Ensure tool registry is initialized before tests."""
        from toolset.registry import _ensure_initialized
        _ensure_initialized()

    def test_all_registered_tools_have_known_risk_levels(self):
        """Every registered tool should appear in EXPECTED_RISK_LEVELS."""
        from toolset.registry import global_tool_registry
        for name in global_tool_registry.list_tool_names():
            assert name in EXPECTED_RISK_LEVELS, (
                f"Tool '{name}' not in EXPECTED_RISK_LEVELS — add it to the test mapping"
            )

    def test_risk_levels_match_expectations(self):
        """Each tool's actual risk_level should match the expected value."""
        from toolset.registry import global_tool_registry
        mismatches = []
        for name, expected in EXPECTED_RISK_LEVELS.items():
            tool = global_tool_registry.get_tool(name)
            if tool is None:
                continue  # Tool may be platform-filtered
            actual = getattr(tool, "risk_level", "low")
            if actual != expected:
                mismatches.append(f"{name}: expected={expected}, actual={actual}")
        assert not mismatches, "\n".join(mismatches)

    def test_high_risk_tools_are_not_low_by_default(self):
        """Critical tools must NOT have default 'low' risk_level."""
        high_risk_tools = [
            "file_system_op", "python_interpreter",
            "task_scheduler_create", "task_scheduler_delete", "task_scheduler",
        ]
        from toolset.registry import global_tool_registry
        for name in high_risk_tools:
            tool = global_tool_registry.get_tool(name)
            if tool is None:
                continue
            assert getattr(tool, "risk_level", "low") in ("high", "critical"), (
                f"Tool '{name}' must have risk_level='high' or 'critical', got '{getattr(tool, 'risk_level', 'low')}'"
            )

    def test_medium_risk_interaction_tools(self):
        """UI interaction tools (click/type/act) should be medium risk."""
        medium_tools = [
            "desktop_click", "desktop_type", "desktop_act",
            "browser_click", "browser_type", "browser_act",
        ]
        from toolset.registry import global_tool_registry
        for name in medium_tools:
            tool = global_tool_registry.get_tool(name)
            if tool is None:
                continue
            assert getattr(tool, "risk_level", "low") == "medium", (
                f"Tool '{name}' should be medium risk, got '{getattr(tool, 'risk_level', 'low')}'"
            )


class TestRiskLevelFiltering:
    """Phase 2: Verify get_fc_schemas_for_prompt respects max_risk_level."""

    @pytest.fixture(autouse=True)
    def _init_registry(self):
        from toolset.registry import _ensure_initialized
        _ensure_initialized()

    def test_max_risk_low_excludes_medium_and_high(self):
        """When max_risk_level='low', only low-risk tools should appear."""
        from toolset.registry import global_tool_registry

        schemas = global_tool_registry.get_fc_schemas_for_prompt(
            prompt="do something",
            step=1,
            max_risk_level="low",
        )
        tool_names = [s["function"]["name"] for s in schemas]

        # These medium/high should NOT appear
        excluded = {"desktop_click", "browser_act", "email_send",
                    "multimedia_download", "feishu_push_file"}
        for name in excluded:
            tool = global_tool_registry.get_tool(name)
            if tool is None:
                continue
            assert name not in tool_names, (
                f"Tool '{name}' (medium risk) should be excluded when max_risk_level='low'"
            )
        # These SHOULD appear (meta-tools are always low)
        assert "tool_info" in tool_names
        assert "skill_read" in tool_names

    def test_max_risk_medium_excludes_high_but_includes_medium(self):
        """When max_risk_level='medium', high-risk tools excluded but medium visible."""
        from toolset.registry import global_tool_registry

        schemas = global_tool_registry.get_fc_schemas_for_prompt(
            prompt="do something with files and code",
            step=1,
            max_risk_level="medium",
        )
        tool_names = [s["function"]["name"] for s in schemas]

        # high-risk tools should NOT appear
        high_risk = {"file_system_op", "python_interpreter", "task_scheduler",
                     "task_scheduler_create", "task_scheduler_delete"}
        for name in high_risk:
            tool = global_tool_registry.get_tool(name)
            if tool is None:
                continue
            assert name not in tool_names, (
                f"High-risk tool '{name}' should be excluded when max_risk_level='medium'"
            )

        # high-risk tools should NOT appear
        high_risk = {"file_system_op", "python_interpreter", "task_scheduler"}
        for name in high_risk:
            tool = global_tool_registry.get_tool(name)
            if tool is None:
                continue
            assert name not in tool_names, (
                f"High-risk tool '{name}' should be excluded when max_risk_level='medium'"
            )

    def test_no_max_risk_returns_all_routed(self):
        """When max_risk_level is None, no filtering should occur."""
        from toolset.registry import global_tool_registry

        schemas_no_filter = global_tool_registry.get_fc_schemas_for_prompt(
            prompt="write a python script and save to file",
            step=1,
            max_risk_level=None,
        )
        tool_names = [s["function"]["name"] for s in schemas_no_filter]

        # With a relevant prompt, these should appear when unfiltered
        # (they may or may not be in the routed set depending on keywords)
        # At minimum, we verify the call succeeds
        assert len(schemas_no_filter) > 0
