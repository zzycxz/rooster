"""Tests for Strategist.replan with history tracking (contrastive directive)."""

import json
from unittest.mock import AsyncMock

import pytest

from agents.protocol import MissionPlan, SubTask
from agents.strategist import Strategist


@pytest.fixture
def strategist_with_mock_llm():
    """Strategist with a mocked LLM client that returns a canned plan."""
    mock_llm = AsyncMock()
    mock_response = AsyncMock()
    mock_response.content = json.dumps(
        {
            "schema_version": "10.0",
            "task_id": "TEST-TASK",
            "os_context": "windows",
            "goal": "Test population search",
            "autonomy": "AUTO",
            "subtasks": [
                {
                    "id": "ST_R1",
                    "domain": "RESOURCE",
                    "tool": "web_search",
                    "instruction": "Search general query 'tokyo population stats' without domain constraints",
                    "depends_on": [],
                    "on_failure": "RETRY",
                    "requires_confirm": False,
                    "timeout": 120,
                }
            ],
        }
    )
    mock_llm.chat_non_stream.return_value = mock_response
    return Strategist(llm_client=mock_llm), mock_llm


class TestReplanHistory:
    @pytest.mark.asyncio
    async def test_first_replan_adds_history_entry(self, strategist_with_mock_llm):
        strategist, _ = strategist_with_mock_llm
        plan = MissionPlan(
            task_id="TEST-TASK",
            goal="Test population search",
            subtasks=[
                SubTask(
                    id="ST1",
                    instruction="Search population of Tokyo site:metro.tokyo.lg.jp",
                    domain="RESOURCE",
                    tool="web_search",
                )
            ],
        )

        new_plan = await strategist.replan(
            current_plan=plan,
            roadblock_reason="Found 0 results under domain constraint",
            completed_tasks=[],
        )

        assert len(new_plan.replan_history) == 1
        entry = new_plan.replan_history[0]
        assert entry["replan_index"] == 1
        assert entry["roadblock"] == "Found 0 results under domain constraint"

    @pytest.mark.asyncio
    async def test_second_replan_accumulates_history(self, strategist_with_mock_llm):
        strategist, _ = strategist_with_mock_llm
        plan = MissionPlan(
            task_id="TEST-TASK",
            goal="Test population search",
            subtasks=[SubTask(id="ST1", instruction="Search query", domain="RESOURCE", tool="web_search")],
        )

        plan1 = await strategist.replan(
            current_plan=plan,
            roadblock_reason="Found 0 results under domain constraint",
            completed_tasks=[],
        )
        plan2 = await strategist.replan(
            current_plan=plan1,
            roadblock_reason="Search query too long",
            completed_tasks=[],
        )

        assert len(plan2.replan_history) == 2
        assert plan2.replan_history[0]["replan_index"] == 1
        assert plan2.replan_history[1]["replan_index"] == 2
        assert plan2.replan_history[1]["roadblock"] == "Search query too long"

    @pytest.mark.asyncio
    async def test_replan_prompt_contains_failure_history(self, strategist_with_mock_llm):
        strategist, mock_llm = strategist_with_mock_llm
        plan = MissionPlan(
            task_id="TEST-TASK",
            goal="Test population search",
            subtasks=[SubTask(id="ST1", instruction="Search query", domain="RESOURCE", tool="web_search")],
        )

        await strategist.replan(current_plan=plan, roadblock_reason="reason1", completed_tasks=[])
        await strategist.replan(current_plan=plan, roadblock_reason="reason2", completed_tasks=[])

        # Inspect the last call's prompt
        _, called_kwargs = mock_llm.chat_non_stream.call_args
        messages = called_kwargs.get("messages", [])
        system_content = messages[0]["content"] if messages else ""

        assert (
            "Failure History" in system_content
            or "失败历史" in system_content
            or "Contrastive" in system_content
            or "对比" in system_content
        )
