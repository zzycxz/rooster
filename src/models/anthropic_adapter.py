import httpx
import json
import logging
from typing import List, Dict, AsyncGenerator, Optional, Any
from .base import BaseModelClient, LLMResponseDelta

logger = logging.getLogger(__name__)

# Anthropic API version
_ANTHROPIC_VERSION = "2023-06-01"


def _convert_vision_content(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI multimodal content list to Anthropic format.

    OpenAI:  {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}
    Anthropic: {"type":"image","source":{"type":"base64","media_type":"image/png","data":"..."}}
    """
    converted = []
    for part in parts:
        if part.get("type") == "image_url":
            img = part.get("image_url", {})
            url = img.get("url", "")
            if url.startswith("data:"):
                # data:image/png;base64,iVBOR...
                header, _, b64data = url.partition(",")
                media_type = header.split(";")[0].replace("data:", "")
                converted.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64data,
                        },
                    }
                )
            else:
                # External URL — Anthropic supports url type
                converted.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "url",
                            "url": url,
                        },
                    }
                )
        else:
            # Text blocks and other types pass through
            converted.append(part)
    return converted


def _convert_messages(messages: List[Dict[str, Any]]) -> tuple:
    """Convert OpenAI-style messages to Anthropic Messages API format.

    Returns (system: str | None, anthropic_messages: list).
    - System messages are extracted as a top-level ``system`` parameter.
    - Assistant messages with ``tool_calls`` → ``tool_use`` content blocks.
    - Tool messages (role=tool) → ``tool_result`` content blocks.
    - ``_internal`` fields are stripped (Rooster-specific, not for API).
    - OpenAI vision format is converted to Anthropic base64 format.
    """
    system_parts: List[str] = []
    converted: List[Dict[str, Any]] = []

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")

        # Strip Rooster-internal fields before sending to Anthropic
        # (e.g. _internal, metadata, etc.)

        if role == "system":
            # Anthropic treats system as a separate top-level field
            system_parts.append(str(content or ""))
            continue

        if role == "tool":
            # OpenAI tool response → Anthropic tool_result block
            tool_call_id = m.get("tool_call_id", "")
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": str(content or ""),
                        }
                    ],
                }
            )
            continue

        if role == "assistant" and m.get("tool_calls"):
            # Assistant with tool calls → tool_use content blocks
            blocks: List[Dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": str(content)})
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": json.loads(fn.get("arguments", "{}"))
                        if isinstance(fn.get("arguments"), str)
                        else (fn.get("arguments") or {}),
                    }
                )
            converted.append({"role": "assistant", "content": blocks})
            continue

        # Normal user / assistant messages
        if isinstance(content, list):
            # Multimodal content list — convert OpenAI vision format to Anthropic
            converted.append({"role": role, "content": _convert_vision_content(content)})
        else:
            converted.append({"role": role, "content": str(content or "")})

    system_str = "\n\n".join(system_parts) if system_parts else None
    return system_str, converted


def _extract_tool_calls_from_blocks(content_blocks: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Extract tool_use blocks from Anthropic response content into OpenAI-style tool_calls."""
    tool_calls = []
    for block in content_blocks:
        if block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": block.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": block.get("name", ""),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                    },
                }
            )
    return tool_calls if tool_calls else None


def _convert_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI tool schemas to Anthropic tool format.

    OpenAI:  {"type":"function","function":{"name","description","parameters"}}
    Anthropic: {"name","description","input_schema"}
    """
    converted = []
    for t in tools:
        fn = t.get("function", t)
        converted.append(
            {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _convert_tool_choice(tool_choice: Any) -> Any:
    """Convert OpenAI tool_choice to Anthropic tool_choice format.

    OpenAI:  "auto" | "none" | "required" | {"type":"function","function":{"name":"xxx"}}
    Anthropic: {"type":"auto"} | {"type":"any"} | {"type":"tool","name":"xxx"}
    """
    if isinstance(tool_choice, str):
        mapping = {
            "auto": {"type": "auto"},
            "none": {"type": "auto"},  # Anthropic has no "none"; use auto
            "required": {"type": "any"},
        }
        return mapping.get(tool_choice, {"type": "auto"})
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function", {})
        name = fn.get("name", "")
        if name:
            return {"type": "tool", "name": name}
    return None


class AnthropicAdapter(BaseModelClient):
    """Native Anthropic Messages API adapter.

    Uses the official Anthropic REST API format:
    - Endpoint: POST /v1/messages
    - Auth: x-api-key header
    - Version: anthropic-version header
    """

    def __init__(self, base_url: str = "https://api.anthropic.com", api_key: str = ""):
        # Normalise base URL — strip path suffix if user provided full endpoint
        for suffix in ["/v1/messages/", "/v1/messages", "/v1/", "/v1"]:
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)]
                break
        # Ensure trailing slash for httpx base_url
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.api_key = api_key

        # Proxy handling — same logic as OpenAILikeClient
        import os
        from urllib.parse import urlparse
        from utils.config import settings

        http_proxy = None
        no_proxy = ""
        if getattr(settings, "ENABLE_REGIONAL_PROXY", False):
            http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or getattr(settings, "HTTP_PROXY", None)
            no_proxy = os.getenv("NO_PROXY", "") or os.getenv("no_proxy", "") or getattr(settings, "NO_PROXY", "")

        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        skip_proxy = any(np.strip() in host for np in no_proxy.split(",") if np.strip()) if no_proxy else True
        proxy_url = None if skip_proxy else (http_proxy if http_proxy else None)

        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=60.0,
            proxy=proxy_url,
        )

    # ── helpers ─────────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _build_payload(self, model: str, messages: List[Dict[str, Any]], stream: bool, **kwargs) -> Dict[str, Any]:
        system_str, converted = _convert_messages(messages)

        # max_tokens is required by Anthropic
        max_tokens = kwargs.pop("max_tokens", kwargs.pop("max_tokens_to_sample", None))
        if not max_tokens:
            max_tokens = 8192

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": converted,
            "stream": stream,
        }
        if system_str:
            payload["system"] = system_str

        # Forward common parameters
        if "temperature" in kwargs:
            payload["temperature"] = kwargs.pop("temperature")
        if "top_p" in kwargs:
            payload["top_p"] = kwargs.pop("top_p")
        if "tools" in kwargs:
            raw_tools = kwargs.pop("tools")
            payload["tools"] = _convert_tools(raw_tools)
        if "tool_choice" in kwargs:
            tc = _convert_tool_choice(kwargs.pop("tool_choice"))
            if tc:
                payload["tool_choice"] = tc

        return payload

    # ── streaming ───────────────────────────────────────────────────────

    async def chat_stream(
        self, model: str, messages: List[Dict[str, Any]], **kwargs
    ) -> AsyncGenerator[LLMResponseDelta, None]:
        payload = self._build_payload(model, messages, stream=True, **kwargs)
        MAX_RETRIES = 2

        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self.client.stream("POST", "v1/messages", json=payload, headers=self._headers()) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        raise Exception(
                            f"Anthropic API Error: {response.status_code} - {error_text.decode('utf-8', 'ignore')}"
                        )

                    yielded_any = False
                    # Accumulate tool_use blocks across streaming events
                    pending_tool_blocks: Dict[int, Dict[str, Any]] = {}
                    # Track content block indices → types
                    block_types: Dict[int, str] = {}

                    async for raw_line in response.aiter_lines():
                        if not raw_line or not raw_line.startswith("data:"):
                            continue

                        data_str = raw_line[5:].strip()
                        if not data_str:
                            continue

                        try:
                            evt = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        evt_type = evt.get("type", "")

                        if evt_type == "content_block_start":
                            idx = evt.get("index", 0)
                            block = evt.get("content_block", {})
                            btype = block.get("type", "")
                            block_types[idx] = btype
                            if btype == "tool_use":
                                pending_tool_blocks[idx] = {
                                    "id": block.get("id", ""),
                                    "name": block.get("name", ""),
                                    "input_json": "",
                                }

                        elif evt_type == "content_block_delta":
                            idx = evt.get("index", 0)
                            delta = evt.get("delta", {})
                            dtype = delta.get("type", "")

                            if dtype == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yielded_any = True
                                    yield LLMResponseDelta(content=text)

                            elif dtype == "input_json_delta":
                                # Streaming partial JSON for tool_use input
                                if idx in pending_tool_blocks:
                                    pending_tool_blocks[idx]["input_json"] += delta.get("partial_json", "")

                        elif evt_type == "message_delta":
                            # Contains finish_reason at end of message
                            delta = evt.get("delta", {})
                            finish = delta.get("stop_reason")
                            if finish:
                                # Map Anthropic stop_reason to OpenAI-style finish_reason
                                fr_map = {
                                    "end_turn": "stop",
                                    "max_tokens": "length",
                                    "stop_sequence": "stop",
                                    "tool_use": "tool_calls",
                                }
                                mapped = fr_map.get(finish, finish)

                                # Emit accumulated tool calls
                                if pending_tool_blocks:
                                    assembled = []
                                    for tb in pending_tool_blocks.values():
                                        try:
                                            parsed_input = json.loads(tb["input_json"]) if tb["input_json"] else {}
                                        except json.JSONDecodeError:
                                            parsed_input = {}
                                        assembled.append(
                                            {
                                                "id": tb["id"],
                                                "type": "function",
                                                "function": {
                                                    "name": tb["name"],
                                                    "arguments": json.dumps(parsed_input, ensure_ascii=False),
                                                },
                                            }
                                        )
                                    yield LLMResponseDelta(
                                        content="",
                                        finish_reason=mapped,
                                        tool_calls=assembled,
                                    )
                                    yielded_any = True

                        elif evt_type == "message_stop":
                            break

                        elif evt_type == "error":
                            error_msg = evt.get("error", evt.get("message", "Unknown error"))
                            logger.error(f"[Anthropic] Stream error event: {error_msg}")
                            raise Exception(f"Anthropic stream error: {error_msg}")

                    if not yielded_any:
                        logger.warning("[Anthropic] Stream ended with no content.")
                break  # success — exit retry loop

            except (httpx.NetworkError, httpx.TimeoutException) as e:
                if attempt < MAX_RETRIES:
                    import asyncio

                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise e
            except Exception as e:
                raise e

    # ── non-streaming ───────────────────────────────────────────────────

    async def chat_non_stream(self, model: str, messages: List[Dict[str, Any]], **kwargs) -> LLMResponseDelta:
        payload = self._build_payload(model, messages, stream=False, **kwargs)
        MAX_RETRIES = 2

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self.client.post("v1/messages", json=payload, headers=self._headers())
                if resp.status_code != 200:
                    raise Exception(f"Anthropic API Error: {resp.status_code} - {resp.text}")

                result = resp.json()
                content_blocks = result.get("content", [])

                # Extract text content
                text_parts = []
                for block in content_blocks:
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                content = "".join(text_parts)

                # Extract tool calls
                tool_calls = _extract_tool_calls_from_blocks(content_blocks)

                # Map stop_reason
                stop_reason = result.get("stop_reason", "")
                fr_map = {
                    "end_turn": "stop",
                    "max_tokens": "length",
                    "stop_sequence": "stop",
                    "tool_use": "tool_calls",
                }
                finish_reason = fr_map.get(stop_reason, stop_reason)

                if tool_calls:
                    logger.info(f"[Anthropic] Non-stream tool_calls received: {len(tool_calls)} calls")
                elif not content:
                    logger.warning(f"[Anthropic] Empty response: {json.dumps(result, ensure_ascii=False)[:300]}")

                return LLMResponseDelta(
                    content=content,
                    role="assistant",
                    finish_reason=finish_reason,
                    tool_calls=tool_calls,
                )

            except (httpx.NetworkError, httpx.TimeoutException) as e:
                logger.error(f"[Anthropic] Network error: {e}")
                if attempt < MAX_RETRIES:
                    import asyncio

                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                raise e
            except Exception as e:
                try:
                    if "result" in locals():
                        logger.error(f"[Anthropic] Raw error: {json.dumps(result, ensure_ascii=False)[:500]}")
                except Exception:
                    pass
                logger.error(f"[Anthropic] Exception: {e}")
                raise e

    # ── lifecycle ───────────────────────────────────────────────────────

    async def close(self):
        await self.client.aclose()
