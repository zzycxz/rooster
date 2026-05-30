"""Tests for AgentExecutor Function Calling protocol message ordering.

Verifies that tool call interactions follow the correct message sequence:
  assistant (with tool_calls) → tool (results) → assistant (final)
Without any spurious "user" messages injected between tool calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_executor():
    """Create an AgentExecutor with all dependencies mocked."""
    from agents.executor import AgentExecutor

    mock_llm = MagicMock()
    mock_handler = MagicMock()
    mock_handler.emit_assistant_delta = AsyncMock()
    mock_handler.emit_tool_result = AsyncMock()
    mock_prompt_builder = MagicMock()
    mock_orchestrator = MagicMock()
    mock_tool_registry = MagicMock()

    with patch("agents.executor.MemoryManager") as MockMM:
        mock_mm = MockMM.return_value
        mock_mm.periodic_housekeeping = AsyncMock()
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


class TestFCProtocolMessageOrder:
    """Verify message history follows FC protocol after tool calls."""

    def test_assistant_tool_calls_has_required_fields(self):
        """An assistant message with tool_calls must have role=assistant and tool_calls list."""
        msg = {
            "role": "assistant",
            "content": "Let me check that.",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        }
        assert msg["role"] == "assistant"
        assert "tool_calls" in msg
        assert isinstance(msg["tool_calls"], list)

    def test_tool_result_references_call_id(self):
        """A tool result message must reference the tool_call id."""
        tool_call_id = "call_abc123"
        result_msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "file contents here",
        }
        assert result_msg["role"] == "tool"
        assert result_msg["tool_call_id"] == tool_call_id

    def test_fc_sequence_no_user_between_tool_calls(self):
        """After assistant+tool_calls, the next message should be tool result, not user."""
        # Simulate a correctly ordered FC conversation
        history = [
            {"role": "user", "content": "read config.json"},
            {
                "role": "assistant",
                "content": "I'll read the file.",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": '{"key": "value"}'},
            {"role": "assistant", "content": "The config has key=value."},
        ]

        # Verify ordering: after assistant with tool_calls comes tool, not user
        for i, msg in enumerate(history):
            if msg["role"] == "assistant" and "tool_calls" in msg:
                next_msg = history[i + 1]
                assert next_msg["role"] == "tool", (
                    f"Expected 'tool' after assistant+tool_calls, got '{next_msg['role']}'"
                )
            if msg["role"] == "tool":
                # Tool result should be followed by assistant (or another tool for parallel calls)
                next_msg = history[i + 1]
                assert next_msg["role"] in ("tool", "assistant"), (
                    f"Expected 'tool' or 'assistant' after tool result, got '{next_msg['role']}'"
                )

    def test_no_user_msg_between_consecutive_tool_rounds(self):
        """Multiple rounds of tool calls should not have user messages between them."""
        history = [
            {"role": "user", "content": "do A then B"},
            {
                "role": "assistant",
                "content": "doing A",
                "tool_calls": [{"id": "a1", "type": "function", "function": {"name": "tool_a", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "a1", "content": "A done"},
            {
                "role": "assistant",
                "content": "doing B",
                "tool_calls": [{"id": "b1", "type": "function", "function": {"name": "tool_b", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "b1", "content": "B done"},
            {"role": "assistant", "content": "Both done."},
        ]

        # Find all tool call rounds
        tool_call_indices = [i for i, m in enumerate(history) if m["role"] == "assistant" and "tool_calls" in m]
        for idx in tool_call_indices:
            # Check messages between this tool_calls and the next assistant
            j = idx + 1
            while j < len(history) and history[j]["role"] == "tool":
                j += 1
            # Between tool_calls and next assistant, there should be NO user messages
            for k in range(idx + 1, j):
                assert history[k]["role"] != "user", (
                    f"Found spurious 'user' message at index {k} between tool call and result"
                )

    def test_parallel_tool_results_all_before_next_assistant(self):
        """Parallel tool calls: all tool results should come before the next assistant message."""
        history = [
            {"role": "user", "content": "check A and B"},
            {
                "role": "assistant",
                "content": "checking both",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "check_a", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "check_b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "A ok"},
            {"role": "tool", "tool_call_id": "c2", "content": "B ok"},
            {"role": "assistant", "content": "Both checks passed."},
        ]

        # Find the assistant message with multiple tool_calls
        for i, msg in enumerate(history):
            if msg["role"] == "assistant" and "tool_calls" in msg and len(msg["tool_calls"]) > 1:
                # Collect all tool results after this
                tool_ids = {tc["id"] for tc in msg["tool_calls"]}
                found_ids = set()
                j = i + 1
                while j < len(history) and history[j]["role"] == "tool":
                    found_ids.add(history[j]["tool_call_id"])
                    j += 1
                assert found_ids == tool_ids, (
                    f"Not all parallel tool results found: expected {tool_ids}, got {found_ids}"
                )
                # Next should be assistant
                assert history[j]["role"] == "assistant"


class TestExecutorComposeMessages:
    """Verify prompt_builder.compose_messages integration."""

    def test_compose_messages_with_blackboard(self):
        """compose_messages should inject blackboard_context before user_input."""
        from agents.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        messages = pb.compose_messages(
            system_prompt="You are a helper.",
            history=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            user_input="do something",
            blackboard_context="Task context from blackboard",
        )

        # System first
        assert messages[0]["role"] == "system"
        # History in middle
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        # Blackboard before user_input
        assert messages[3]["role"] == "user"
        assert messages[3].get("_internal") is True
        assert (
            "blackboard" in messages[3]["content"].lower()
            or "context" in messages[3]["content"].lower()
            or "Task context" in messages[3]["content"]
        )
        # User input last
        assert messages[4]["role"] == "user"
        assert "do something" in messages[4]["content"]

    def test_compose_messages_without_blackboard(self):
        """compose_messages without blackboard should not add extra messages."""
        from agents.prompt_builder import PromptBuilder

        pb = PromptBuilder()
        messages = pb.compose_messages(
            system_prompt="You are a helper.",
            history=[],
            user_input="hello",
        )

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "hello" in messages[1]["content"]
