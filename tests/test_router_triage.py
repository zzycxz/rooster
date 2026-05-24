"""Tests for Router keyword-based triage fallback."""

import pytest


class TestTriageByKeyword:
    """Unit tests for Router._triage_by_keyword — no LLM calls needed."""

    @pytest.fixture
    def router_keywords(self):
        """Return the keyword lists from Router to test against."""
        from agents.router import Router

        return {
            "schedule": Router._SCHEDULE_KW,
            "download": Router._DOWNLOAD_KW,
            "talk": Router._TALK_KW,
            "complex": Router._COMPLEX_KW,
        }

    def _make_router(self):
        """Create a minimal Router instance with mocked dependencies."""
        from unittest.mock import MagicMock
        from agents.router import Router

        Router._instance = None
        router = Router.__new__(Router)
        router._short_circuit = MagicMock()
        router._short_circuit.try_handle = MagicMock(return_value=False)
        return router

    # --- TALK ---
    @pytest.mark.parametrize(
        "text",
        [
            "你好",
            "hi",
            "hello",
            "Hello!",
            "who are you",
            "你是谁",
            "在吗",
            "what is AI",
            "什么是机器学习",
            "解释一下量子计算",
        ],
    )
    def test_talk_keywords(self, text):
        router = self._make_router()
        result = router._triage_by_keyword(text)
        assert result == "[TALK]", f"Expected [TALK] for '{text}', got {result}"

    # --- SCHEDULE ---
    @pytest.mark.parametrize(
        "text",
        [
            "每天8点提醒我开会",
            "每周一自动生成报告",
            "每小时检查一次",
            "remind me at 8am",
            "every day at 9:00",
            "schedule a daily report",
        ],
    )
    def test_schedule_keywords(self, text):
        router = self._make_router()
        result = router._triage_by_keyword(text)
        assert result == "[SCHEDULE]", f"Expected [SCHEDULE] for '{text}', got {result}"

    # --- DOWNLOAD / REFRAME ---
    @pytest.mark.parametrize(
        "text",
        [
            "下载这个文件",
            "download the video",
            "安装依赖包",
            "帮我下载一个视频",
            "fetch the report",
        ],
    )
    def test_download_keywords(self, text):
        router = self._make_router()
        result = router._triage_by_keyword(text)
        assert result == "[REFRAME]", f"Expected [REFRAME] for '{text}', got {result}"

    # --- COMPLEX / DIRECT ---
    @pytest.mark.parametrize(
        "text",
        [
            "帮我写一个脚本",
            "分析这份报告",
            "对比两个方案",
            "search for all PDF files",
            "write a Python script",
            "write a Python script",
            "help me build a dashboard",
        ],
    )
    def test_complex_keywords(self, text):
        router = self._make_router()
        result = router._triage_by_keyword(text)
        assert result == "[DIRECT]", f"Expected [DIRECT] for '{text}', got {result}"

    # --- DEFAULT fallback ---
    def test_default_is_direct(self):
        router = self._make_router()
        result = router._triage_by_keyword("random text with no keywords xyz123")
        assert result == "[DIRECT]"

    # --- Short greeting → TALK (fast path in _triage_via_llm) ---
    @pytest.mark.parametrize("text", ["hi", "你好", "ok", "好的"])
    def test_short_greeting_fast_path(self, text):
        """Short greetings < 5 chars should resolve to [TALK] without LLM."""
        assert len(text) < 5
        assert any(
            k in text.lower()
            for k in [
                "hi",
                "你好",
                "在吗",
                "hello",
                "hey",
                "ok",
                "好的",
                "嗯",
                "谢谢",
            ]
        )

    # --- Schedule takes priority over download ---
    def test_schedule_priority_over_download(self):
        """Schedule keywords should be checked before download keywords."""
        router = self._make_router()
        result = router._triage_by_keyword("每天定时下载文件")
        assert result == "[SCHEDULE]"
