"""Tests for tool call parsing (extract_tool_calls, extract_tool_calls_native)."""

import json

from agents.tool_dispatch import (
    extract_tool_calls,
    extract_tool_calls_native,
    _find_balanced_json,
    _extract_from_xml_tags,
)


class TestExtractToolCallsNative:
    """Tests for native Function Calling tool call extraction."""

    def test_basic_native_call(self):
        data = [{"function": {"name": "web_search", "arguments": '{"query": "test"}'}}]
        result = extract_tool_calls_native(data)
        assert len(result) == 1
        assert result[0] == ("web_search", {"query": "test"})

    def test_empty_arguments_string(self):
        data = [{"function": {"name": "list_files", "arguments": "{}"}}]
        result = extract_tool_calls_native(data)
        assert result[0][1] == {}

    def test_dict_arguments_passthrough(self):
        data = [{"function": {"name": "read_file", "arguments": {"path": "/tmp/a.txt"}}}]
        result = extract_tool_calls_native(data)
        assert result[0][1] == {"path": "/tmp/a.txt"}

    def test_malformed_json_fallback(self):
        data = [{"function": {"name": "search", "arguments": "{'key': 'value'}"}}]
        result = extract_tool_calls_native(data)
        assert len(result) == 1
        assert result[0][1] == {"key": "value"}

    def test_empty_function_name_skipped(self):
        data = [{"function": {"name": "", "arguments": "{}"}}]
        result = extract_tool_calls_native(data)
        assert len(result) == 0

    def test_multiple_tool_calls(self):
        data = [
            {"function": {"name": "web_search", "arguments": '{"query": "a"}'}},
            {"function": {"name": "read_file", "arguments": '{"path": "/tmp/b"}'}},
        ]
        result = extract_tool_calls_native(data)
        assert len(result) == 2
        assert result[0][0] == "web_search"
        assert result[1][0] == "read_file"

    def test_totally_broken_json_returns_empty_dict(self):
        data = [{"function": {"name": "tool", "arguments": "not json at all {{{{"}}]
        result = extract_tool_calls_native(data)
        assert len(result) == 1
        assert result[0][1] == {}


class TestExtractToolCalls:
    """Tests for XML-based tool call extraction."""

    def test_basic_xml_tool_call(self):
        content = '<tool_code name="web_search">{"query": "python"}</tool_code>'
        result = extract_tool_calls(content)
        assert len(result) == 1
        assert result[0] == ("web_search", {"query": "python"})

    def test_empty_args(self):
        content = '<tool_code name="list_files"></tool_code>'
        result = extract_tool_calls(content)
        assert len(result) == 1
        assert result[0] == ("list_files", {})

    def test_nested_json(self):
        content = '<tool_code name="write_file">{"path": "/tmp/a.txt", "content": "hello\\nworld"}</tool_code>'
        result = extract_tool_calls(content)
        assert len(result) == 1
        assert result[0][0] == "write_file"
        assert result[0][1]["path"] == "/tmp/a.txt"

    def test_multiple_tool_calls(self):
        content = (
            '<tool_code name="search">{"q": "a"}</tool_code>'
            "some text between"
            '<tool_code name="read">{"path": "b"}</tool_code>'
        )
        result = extract_tool_calls(content)
        assert len(result) == 2

    def test_unclosed_tool_code_tag(self):
        """Should still extract from unclosed tags (matches to end of string)."""
        content = '<tool_code name="search">{"q": "a"}'
        result = extract_tool_calls(content)
        assert len(result) == 1

    def test_no_tool_calls(self):
        content = "Just regular text with no tool calls."
        result = extract_tool_calls(content)
        assert len(result) == 0

    def test_case_insensitive_tag(self):
        content = '<TOOL_CODE name="search">{"q": "test"}</TOOL_CODE>'
        result = extract_tool_calls(content)
        assert len(result) == 1


class TestFindBalancedJson:
    """Tests for _find_balanced_json."""

    def test_simple_object(self):
        assert _find_balanced_json('{"a": 1}') == '{"a": 1}'

    def test_nested_object(self):
        s = '{"a": {"b": 2}}'
        assert _find_balanced_json(s) == s

    def test_truncated_json_auto_repair(self):
        s = '{"a": {"b": 2}'
        result = _find_balanced_json(s)
        assert result == '{"a": {"b": 2}}'

    def test_no_braces(self):
        assert _find_balanced_json("plain text") is None

    def test_with_surrounding_text(self):
        s = 'here is some json {"key": "value"} and more'
        result = _find_balanced_json(s)
        assert result == '{"key": "value"}'

    def test_array_in_value(self):
        s = '{"items": [1, 2, 3]}'
        result = _find_balanced_json(s)
        assert '"items"' in result

    def test_string_with_braces(self):
        s = '{"text": "hello {world}"}'
        result = _find_balanced_json(s)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["text"] == "hello {world}"


class TestExtractFromXmlTags:
    """Tests for _extract_from_xml_tags."""

    def test_parameter_tags(self):
        s = '<parameter = "key1">value1</parameter><parameter = "key2">value2</parameter>'
        result = _extract_from_xml_tags(s)
        assert result is not None
        assert len(result) == 2
        # Values are correctly extracted regardless of key quoting
        assert "value1" in result.values()
        assert "value2" in result.values()

    def test_param_name_tags(self):
        s = '<param name="path">/tmp/file</param>'
        result = _extract_from_xml_tags(s)
        assert result == {"path": "/tmp/file"}

    def test_no_tags(self):
        assert _extract_from_xml_tags("plain text") is None
