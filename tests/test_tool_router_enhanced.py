"""
Test suite for enhanced ToolRouter: forced_kits, safe fallback, and SkillIndex integration.

Phase 3: Verify forced_kits forces Kit inclusion.
Phase 4: Verify safe fallback behavior (System kit only, not all tools).
"""

import pytest


class TestToolRouterForcedKits:
    """Phase 3: Verify forced_kits parameter forces Kit inclusion."""

    @pytest.fixture(autouse=True)
    def _init_registry(self):
        from toolset.registry import _ensure_initialized
        _ensure_initialized()

    def _get_all_schemas_and_maps(self):
        """Helper to get all FC schemas and kit_map."""
        from toolset.registry import global_tool_registry
        all_schemas = global_tool_registry.get_all_fc_schemas()
        kit_map = {
            t.name: getattr(t, "kit", "general")
            for t in global_tool_registry._tools.values()
        }
        return all_schemas, kit_map

    def test_forced_kits_includes_vision_without_keywords(self):
        """When forced_kits={'Vision'}, Vision tools should appear even without keywords."""
        from toolset.router import ToolRouter
        all_schemas, kit_map = self._get_all_schemas_and_maps()

        result = ToolRouter.get().select_schemas(
            prompt="something completely unrelated to vision",  # no vision keywords
            step=1,
            recently_used=[],
            all_fc_schemas=all_schemas,
            kit_map=kit_map,
            forced_kits={"Vision"},
        )
        tool_names = {s["function"]["name"] for s in result}
        # At least one Vision tool should be present
        vision_tools = {n for n, k in kit_map.items() if k == "Vision" and n in tool_names}
        # Some Vision tools may be fc_hidden, so check non-hidden ones
        non_hidden_vision = {n for n, k in kit_map.items() if k == "Vision"}
        assert vision_tools & non_hidden_vision, (
            f"forced_kits=Vision should include Vision tools, got: {tool_names}"
        )

    def test_forced_kits_includes_browser(self):
        """When forced_kits={'Browser'}, Browser tools should appear."""
        from toolset.router import ToolRouter
        all_schemas, kit_map = self._get_all_schemas_and_maps()

        result = ToolRouter.get().select_schemas(
            prompt="nothing about browsing here",
            step=1,
            recently_used=[],
            all_fc_schemas=all_schemas,
            kit_map=kit_map,
            forced_kits={"Browser"},
        )
        tool_names = {s["function"]["name"] for s in result}
        browser_tools = {n for n, k in kit_map.items() if k == "Browser" and n in tool_names}
        non_hidden_browser = {n for n, k in kit_map.items() if k == "Browser"}
        assert browser_tools & non_hidden_browser, (
            f"forced_kits=Browser should include Browser tools, got: {tool_names}"
        )

    def test_no_forced_kits_unchanged(self):
        """Without forced_kits, behavior should be unchanged (backward compatible)."""
        from toolset.router import ToolRouter
        all_schemas, kit_map = self._get_all_schemas_and_maps()

        result = ToolRouter.get().select_schemas(
            prompt="read a file",
            step=1,
            recently_used=[],
            all_fc_schemas=all_schemas,
            kit_map=kit_map,
            forced_kits=None,
        )
        assert len(result) > 0


class TestToolRouterSafeFallback:
    """Phase 4: Verify safe fallback returns System kit only, not all tools."""

    @pytest.fixture(autouse=True)
    def _init_registry(self):
        from toolset.registry import _ensure_initialized
        _ensure_initialized()

    def _get_all_schemas_and_maps(self):
        from toolset.registry import global_tool_registry
        all_schemas = global_tool_registry.get_all_fc_schemas()
        kit_map = {
            t.name: getattr(t, "kit", "general")
            for t in global_tool_registry._tools.values()
        }
        return all_schemas, kit_map

    def test_fallback_returns_system_kit_only(self):
        """With an ambiguous prompt matching no keywords, only System kit tools should appear."""
        from toolset.router import ToolRouter
        from utils.config import settings

        # Ensure safe fallback is enabled (default)
        original_val = getattr(settings, "TOOL_ROUTER_SAFE_FALLBACK", True)
        try:
            settings.TOOL_ROUTER_SAFE_FALLBACK = True
            ToolRouter.reset()

            all_schemas, kit_map = self._get_all_schemas_and_maps()
            total_tools = len(all_schemas)

            result = ToolRouter.get().select_schemas(
                prompt="xyzzy nothing matches this prompt at all qwerty",
                step=1,
                recently_used=[],
                all_fc_schemas=all_schemas,
                kit_map=kit_map,
            )
            tool_names = {s["function"]["name"] for s in result}

            # Should be significantly fewer than total tools
            assert len(result) < total_tools, (
                f"Safe fallback returned {len(result)} tools (total={total_tools}). "
                "Should return only System kit."
            )

            # All returned tools should be System kit or meta-tools
            for name in tool_names:
                kit = kit_map.get(name, "general")
                assert kit == "System" or name in {"tool_info", "skill_read"}, (
                    f"Non-System tool '{name}' (kit={kit}) in fallback result"
                )
        finally:
            ToolRouter.reset()
            if original_val is True:
                delattr(settings, "TOOL_ROUTER_SAFE_FALLBACK")
            else:
                settings.TOOL_ROUTER_SAFE_FALLBACK = original_val

    def test_fallback_with_forced_kits_includes_forced(self):
        """When safe fallback triggers, forced_kits tools should also appear."""
        from toolset.router import ToolRouter
        from utils.config import settings

        original_val = getattr(settings, "TOOL_ROUTER_SAFE_FALLBACK", True)
        try:
            settings.TOOL_ROUTER_SAFE_FALLBACK = True
            ToolRouter.reset()

            all_schemas, kit_map = self._get_all_schemas_and_maps()

            result = ToolRouter.get().select_schemas(
                prompt="xyzzy nothing matches qwerty",
                step=1,
                recently_used=[],
                all_fc_schemas=all_schemas,
                kit_map=kit_map,
                forced_kits={"Vision"},
            )
            tool_names = {s["function"]["name"] for s in result}

            # Vision tools should be included due to forced_kits
            has_vision = any(kit_map.get(n) == "Vision" for n in tool_names)
            assert has_vision, (
                f"Forced Vision kit should appear in fallback result. Got: {tool_names}"
            )
        finally:
            ToolRouter.reset()
            if original_val is True:
                delattr(settings, "TOOL_ROUTER_SAFE_FALLBACK")
            else:
                settings.TOOL_ROUTER_SAFE_FALLBACK = original_val

    def test_legacy_fallback_returns_all(self):
        """When safe_fallback=False and selection < _MIN_TOOLS_BEFORE_FALLBACK,
        old behavior (all tools) should work.

        Note: With the current tool set, System kit alone provides >= 8 tools,
        so the fallback rarely triggers. We force it by using a minimal fake
        schema set to verify the logic branch.
        """
        from toolset.router import ToolRouter, _MIN_TOOLS_BEFORE_FALLBACK

        # Build a minimal fake schema set with fewer than _MIN_TOOLS_BEFORE_FALLBACK tools
        # to force the fallback path
        fake_schemas = [
            {"function": {"name": "tool_info"}, "type": "function"},
            {"function": {"name": "skill_read"}, "type": "function"},
        ]
        fake_kit_map = {"tool_info": "System", "skill_read": "System"}

        from utils.config import settings
        original_val = getattr(settings, "TOOL_ROUTER_SAFE_FALLBACK", True)
        try:
            # Test legacy fallback (safe_fallback=False) returns all schemas
            settings.TOOL_ROUTER_SAFE_FALLBACK = False
            ToolRouter.reset()

            result = ToolRouter.get().select_schemas(
                prompt="asdfqwer zxcvbnm",
                step=1,
                recently_used=[],
                all_fc_schemas=fake_schemas,
                kit_map=fake_kit_map,
            )
            assert len(result) == len(fake_schemas), (
                f"Legacy fallback should return all {len(fake_schemas)} tools. Got {len(result)}"
            )

            # Test safe fallback (safe_fallback=True) returns only System kit
            settings.TOOL_ROUTER_SAFE_FALLBACK = True
            ToolRouter.reset()

            result_safe = ToolRouter.get().select_schemas(
                prompt="asdfqwer zxcvbnm",
                step=1,
                recently_used=[],
                all_fc_schemas=fake_schemas,
                kit_map=fake_kit_map,
            )
            # Both tools are System kit, so both should appear
            assert len(result_safe) == len(fake_schemas)

        finally:
            ToolRouter.reset()
            if original_val is True:
                delattr(settings, "TOOL_ROUTER_SAFE_FALLBACK")
            else:
                settings.TOOL_ROUTER_SAFE_FALLBACK = original_val
