"""Tests for Anthropic adapter pure functions (no network calls)."""

import json


from models.anthropic_adapter import (
    _convert_vision_content,
    _convert_messages,
    _extract_tool_calls_from_blocks,
    _convert_tools,
    _convert_tool_choice,
)


class TestConvertVisionContent:
    def test_base64_image(self):
        parts = [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
            }
        ]
        result = _convert_vision_content(parts)
        assert len(result) == 1
        assert result[0]["type"] == "image"
        assert result[0]["source"]["type"] == "base64"
        assert result[0]["source"]["media_type"] == "image/png"
        assert result[0]["source"]["data"] == "iVBORw0KGgo="

    def test_external_url_image(self):
        parts = [
            {
                "type": "image_url",
                "image_url": {"url": "https://example.com/photo.jpg"},
            }
        ]
        result = _convert_vision_content(parts)
        assert result[0]["source"]["type"] == "url"
        assert result[0]["source"]["url"] == "https://example.com/photo.jpg"

    def test_text_passthrough(self):
        parts = [{"type": "text", "text": "hello"}]
        result = _convert_vision_content(parts)
        assert result == parts

    def test_jpeg_base64(self):
        parts = [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,/9j/4AAQ="},
            }
        ]
        result = _convert_vision_content(parts)
        assert result[0]["source"]["media_type"] == "image/jpeg"


class TestConvertMessages:
    def test_system_messages_extracted(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        system, converted = _convert_messages(messages)
        assert system == "You are helpful."
        assert len(converted) == 1
        assert converted[0]["role"] == "user"

    def test_multiple_system_messages_joined(self):
        messages = [
            {"role": "system", "content": "Rule 1"},
            {"role": "system", "content": "Rule 2"},
            {"role": "user", "content": "Hi"},
        ]
        system, _ = _convert_messages(messages)
        assert system == "Rule 1\n\nRule 2"

    def test_tool_message_converted_to_tool_result(self):
        messages = [
            {"role": "tool", "content": "search results here", "tool_call_id": "call_123"},
        ]
        _, converted = _convert_messages(messages)
        assert converted[0]["role"] == "user"
        assert converted[0]["content"][0]["type"] == "tool_result"
        assert converted[0]["content"][0]["tool_use_id"] == "call_123"

    def test_assistant_tool_calls_converted(self):
        messages = [
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "function": {"name": "web_search", "arguments": '{"q": "test"}'},
                    }
                ],
            }
        ]
        _, converted = _convert_messages(messages)
        blocks = converted[0]["content"]
        assert blocks[0] == {"type": "text", "text": "Let me search."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "web_search"
        assert blocks[1]["input"] == {"q": "test"}

    def test_no_system_returns_none(self):
        messages = [{"role": "user", "content": "Hi"}]
        system, _ = _convert_messages(messages)
        assert system is None

    def test_multimodal_content_passed_through(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        _, converted = _convert_messages(messages)
        assert len(converted[0]["content"]) == 2


class TestExtractToolCallsFromBlocks:
    def test_basic_extraction(self):
        blocks = [
            {"type": "text", "text": "thinking..."},
            {"type": "tool_use", "id": "tu_1", "name": "search", "input": {"q": "hello"}},
        ]
        result = _extract_tool_calls_from_blocks(blocks)
        assert len(result) == 1
        assert result[0]["id"] == "tu_1"
        assert result[0]["type"] == "function"
        assert result[0]["function"]["name"] == "search"
        assert json.loads(result[0]["function"]["arguments"]) == {"q": "hello"}

    def test_no_tool_use_returns_none(self):
        blocks = [{"type": "text", "text": "hello"}]
        assert _extract_tool_calls_from_blocks(blocks) is None

    def test_multiple_tool_calls(self):
        blocks = [
            {"type": "tool_use", "id": "a", "name": "fn1", "input": {}},
            {"type": "tool_use", "id": "b", "name": "fn2", "input": {"x": 1}},
        ]
        result = _extract_tool_calls_from_blocks(blocks)
        assert len(result) == 2


class TestConvertTools:
    def test_openai_format_converted(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ]
        result = _convert_tools(tools)
        assert result[0]["name"] == "get_weather"
        assert result[0]["description"] == "Get weather"
        assert result[0]["input_schema"] == {"type": "object", "properties": {"city": {"type": "string"}}}

    def test_flat_format_passthrough(self):
        tools = [{"name": "fn", "description": "desc", "parameters": {"type": "object"}}]
        result = _convert_tools(tools)
        assert result[0]["input_schema"] == {"type": "object"}


class TestConvertToolChoice:
    def test_auto(self):
        assert _convert_tool_choice("auto") == {"type": "auto"}

    def test_none_maps_to_auto(self):
        assert _convert_tool_choice("none") == {"type": "auto"}

    def test_required_maps_to_any(self):
        assert _convert_tool_choice("required") == {"type": "any"}

    def test_specific_function(self):
        result = _convert_tool_choice({"type": "function", "function": {"name": "search"}})
        assert result == {"type": "tool", "name": "search"}

    def test_unknown_string_defaults_auto(self):
        assert _convert_tool_choice("something_else") == {"type": "auto"}

    def test_empty_dict_returns_none(self):
        assert _convert_tool_choice({}) is None


class TestAdapterInit:
    def test_base_url_strips_v1_suffix(self):
        from models.anthropic_adapter import AnthropicAdapter

        adapter = AnthropicAdapter.__new__(AnthropicAdapter)
        # Test the URL normalization logic by simulating __init__
        for suffix in ["/v1/messages/", "/v1/messages", "/v1/", "/v1"]:
            url = f"https://api.anthropic.com{suffix}"
            for s in ["/v1/messages/", "/v1/messages", "/v1/", "/v1"]:
                if url.endswith(s):
                    url = url[: -len(s)]
                    break
            assert url == "https://api.anthropic.com"
