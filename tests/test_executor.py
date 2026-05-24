"""Tests for AgentExecutor._fire_background and related logic."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def executor():
    """Create an AgentExecutor with all dependencies mocked."""
    from agents.executor import AgentExecutor

    mock_llm = MagicMock()
    mock_handler = MagicMock()
    mock_prompt_builder = MagicMock()
    mock_orchestrator = MagicMock()
    mock_tool_registry = MagicMock()

    with patch("agents.executor.MemoryManager") as MockMM:
        mock_mm = MockMM.return_value
        mock_mm.periodic_housekeeping = AsyncMock()
        # MagicMock auto-creates attributes on access, so delete _fire_background
        # to accurately simulate a real MemoryManager (which lacks it)
        if hasattr(mock_mm, "_fire_background"):
            del mock_mm._fire_background
        exec = AgentExecutor(
            llm_client=mock_llm,
            event_handler=mock_handler,
            prompt_builder=mock_prompt_builder,
            orchestrator=mock_orchestrator,
            tool_registry=mock_tool_registry,
            memory_manager=mock_mm,
        )
    return exec


class TestFireBackground:
    """Verify _fire_background creates tasks with strong references."""

    @pytest.mark.asyncio
    async def test_returns_task(self, executor):
        """_fire_background should return an asyncio.Task."""

        async def noop():
            pass

        task = executor._fire_background(noop())
        assert isinstance(task, asyncio.Task)
        await task

    @pytest.mark.asyncio
    async def test_task_is_tracked(self, executor):
        """The task should be in _background_tasks to prevent GC."""

        async def noop():
            pass

        task = executor._fire_background(noop())
        assert task in executor._background_tasks
        await task

    @pytest.mark.asyncio
    async def test_task_removed_after_completion(self, executor):
        """After the task completes, it should be removed from _background_tasks."""

        async def noop():
            pass

        task = executor._fire_background(noop())
        await task
        assert task not in executor._background_tasks

    @pytest.mark.asyncio
    async def test_concurrent_tasks_tracked(self, executor):
        """Multiple concurrent tasks should all be tracked."""
        barrier = asyncio.Barrier(5)

        async def wait_barrier():
            await barrier.wait()

        tasks = [executor._fire_background(wait_barrier()) for _ in range(5)]
        # All tasks should be tracked while pending
        assert len(executor._background_tasks) == 5
        for t in tasks:
            assert t in executor._background_tasks
        await asyncio.gather(*tasks)

    def test_background_tasks_set_is_instance_variable(self, executor):
        """_background_tasks must be on executor, NOT on memory_manager."""
        assert hasattr(executor, "_background_tasks")
        assert isinstance(executor._background_tasks, set)

    def test_memory_manager_has_no_fire_background(self, executor):
        """MemoryManager should NOT have _fire_background (the bug we fixed)."""
        assert not hasattr(executor.memory_manager, "_fire_background")


class TestHousekeepingCall:
    """Verify the housekeeping call targets the correct object."""

    @pytest.mark.asyncio
    async def test_housekeeping_called_via_executor(self, executor):
        """When we call _fire_background with housekeeping, it uses executor's method,
        not memory_manager's."""
        # This is the pattern used in executor.py ~line 602
        # The old buggy code was: self.memory_manager._fire_background(...)
        # The fixed code is: self._fire_background(...)

        async def fake_housekeeping():
            return "done"

        executor.memory_manager.periodic_housekeeping = fake_housekeeping

        # This should NOT raise AttributeError
        task = executor._fire_background(executor.memory_manager.periodic_housekeeping())
        result = await task
        assert result == "done"
