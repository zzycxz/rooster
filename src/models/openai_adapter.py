import httpx
import json
import logging
from typing import List, Dict, AsyncGenerator, Any
from .base import BaseModelClient, LLMResponseDelta

logger = logging.getLogger(__name__)


class OpenAILikeClient(BaseModelClient):
    """
    OpenAI 兼容接口的通用实现 (九天、本地 Ollama 均可使用)。

    Generic OpenAI-compatible interface implementation (works with Jiutian, local Ollama, etc.).
    """

    def __init__(self, base_url: str, api_key: str):
        # 兼容性处理：如果用户填入了完整的 completions 地址，自动剥离后半部分
        # Compatibility: if user provided full completions URL, auto-strip the suffix
        for suffix in ["/chat/completions/", "/chat/completions"]:
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)]
                break

        # 确保 base_url 以 / 结尾，否则 httpx 会在追加相对路径时裁掉最后一级路径
        # Ensure base_url ends with /, otherwise httpx truncates the last path segment when appending relative paths
        self.base_url = base_url if base_url.endswith("/") else (base_url + "/")
        self.api_key = api_key
        # 手动解析代理配置，避免 httpx trust_env=True 在复杂 NO_PROXY 下的 Bug
        # Manually parse proxy config to avoid httpx trust_env=True bugs with complex NO_PROXY
        import os
        from urllib.parse import urlparse
        from utils.config import settings

        # 仅当系统配置明确启用代理时才应用代理
        # Only apply proxy when system config explicitly enables it
        http_proxy = None
        no_proxy = ""
        if getattr(settings, "ENABLE_REGIONAL_PROXY", False):
            http_proxy = os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or getattr(settings, "HTTP_PROXY", None)
            no_proxy = os.getenv("NO_PROXY", "") or os.getenv("no_proxy", "") or getattr(settings, "NO_PROXY", "")

        parsed = urlparse(self.base_url)
        host = parsed.hostname or ""
        skip_proxy = any(np.strip() in host for np in no_proxy.split(",") if np.strip()) if no_proxy else True
        proxy_url = None if skip_proxy else (http_proxy if http_proxy else None)

        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=45.0, proxy=proxy_url)

    async def chat_stream(
        self, model: str, messages: List[Dict[str, Any]], **kwargs
    ) -> AsyncGenerator[LLMResponseDelta, None]:
        payload = {"model": model, "messages": self._safe_messages(messages), "stream": True, **kwargs}

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        MAX_RETRIES = 2
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self.client.stream("POST", "chat/completions", json=payload, headers=headers) as response:
                    if response.status_code != 200:
                        error_text = await response.aread()
                        raise Exception(f"API Error: {response.status_code} - {error_text.decode('utf-8', 'ignore')}")

                    yielded_any = False
                    # [Phase2] 流式 tool_calls 聚合缓冲区
                    # [Phase2] Streaming tool_calls aggregation buffer
                    pending_tool_calls: Dict[int, dict] = {}
                    accumulated_reasoning: str = ""  # MiMo thinking-mode reasoning accumulator

                    async for line in response.aiter_lines():
                        if not line:
                            continue

                        if not line.startswith("data:"):
                            logger.debug(f"ℹ️ [OpenAILike] 忽略非 data 行: {line}")
                            continue

                        data_str = line[len("data:") :].strip()
                        if data_str == "[DONE]":
                            break

                        try:
                            data = json.loads(data_str)
                            choices = data.get("choices", [])
                            if not choices:
                                logger.debug(f"⚠️ [OpenAILike] 收到空 choices: {data}")
                                continue

                            delta_obj = choices[0].get("delta", {})
                            finish_reason = choices[0].get("finish_reason")

                            # Reasoning scratchpad (MiMo thinking mode) — accumulate and
                            # yield at end so executor can store it for round-trip fidelity.
                            reasoning = delta_obj.get("reasoning_content") or delta_obj.get("reasoning") or ""
                            content = delta_obj.get("content") or ""

                            if reasoning:
                                accumulated_reasoning += reasoning
                                logger.debug(f"🧠 [Internal Thought]: {reasoning}")

                            if content:
                                yielded_any = True

                            # [Phase2] 聚合流式 tool_calls delta
                            tc_deltas = delta_obj.get("tool_calls") or []
                            for tc_delta in tc_deltas:
                                idx = tc_delta.get("index", 0)
                                if idx not in pending_tool_calls:
                                    pending_tool_calls[idx] = {
                                        "id": tc_delta.get("id", ""),
                                        "type": tc_delta.get("type", "function"),
                                        "function": {"name": "", "arguments": ""},
                                    }
                                tc_entry = pending_tool_calls[idx]
                                fn_delta = tc_delta.get("function", {})
                                if fn_delta.get("name"):
                                    tc_entry["function"]["name"] += fn_delta["name"]
                                if fn_delta.get("arguments"):
                                    tc_entry["function"]["arguments"] += fn_delta["arguments"]
                                if tc_delta.get("id"):
                                    tc_entry["id"] = tc_delta["id"]

                            yield LLMResponseDelta(
                                content=str(content), role=delta_obj.get("role"), finish_reason=finish_reason
                            )
                        except json.JSONDecodeError:
                            logger.debug(f"⚠️ [OpenAILike] 无法解析 JSON: {data_str}")
                            continue

                    # [Phase2] 流结束后，如果有聚合完毕的 tool_calls，发出最终 delta
                    if pending_tool_calls:
                        assembled = list(pending_tool_calls.values())
                        logger.info(f"🔧 [OpenAILike] 流式 tool_calls 聚合完成: {len(assembled)} 个工具调用")
                        yield LLMResponseDelta(
                            content="",
                            finish_reason="tool_calls",
                            tool_calls=assembled,
                            reasoning_content=accumulated_reasoning or None,
                        )
                        yielded_any = True
                    elif accumulated_reasoning:
                        # Reasoning-only turn (e.g. standard text reply from a thinking model):
                        # Fallback: if we only got reasoning and no actual content/tools,
                        # inject the reasoning as content to prevent downstream JSON parse failures.
                        yield LLMResponseDelta(
                            content=accumulated_reasoning,
                            finish_reason=None,
                            reasoning_content=accumulated_reasoning,
                        )

                    if not yielded_any:
                        logger.warning("⚠️ [OpenAILike] 流结束，但未产生任何有效 content。")
                break  # 成功则跳出重试  # Exit retry loop on success
            except (httpx.NetworkError, httpx.TimeoutException) as e:
                if attempt < MAX_RETRIES:
                    import asyncio

                    await asyncio.sleep(2 * (attempt + 1))
                    continue

                # 直接抛出异常，让上层 LLMClient 感知并执行 Failover
                # Must raise to trigger LLMClient Failover mechanism
                raise e
            except Exception as e:
                # 直接抛出异常
                raise e

    async def chat_non_stream(self, model: str, messages: List[Dict[str, Any]], **kwargs) -> LLMResponseDelta:
        payload = {"model": model, "messages": self._safe_messages(messages), "stream": False, **kwargs}

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        MAX_RETRIES = 2
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = await self.client.post("chat/completions", json=payload, headers=headers)
                if resp.status_code != 200:
                    raise Exception(f"API Error: {resp.status_code} - {resp.text}")

                result = resp.json()
                choices = result.get("choices", [])
                if not choices:
                    raise Exception(f"API Response missing 'choices': {json.dumps(result, ensure_ascii=False)}")

                message = choices[0].get("message", {})
                if not message:
                    raise Exception(
                        f"API Response missing 'message' in choices: {json.dumps(result, ensure_ascii=False)}"
                    )

                # 九天特有字段: reasoning 独立日志输出（兼容 reasoning_content 和 reasoning 两种键名）
                # Jiutian-specific: reasoning output logged separately (compatible with both key names)
                content = message.get("content") or ""
                reasoning = message.get("reasoning_content") or message.get("reasoning") or ""

                if reasoning:
                    logger.debug(f"🧠 [Internal Thought]: {reasoning}")

                # [Phase2] 提取原生 tool_calls
                tool_calls = message.get("tool_calls")
                if tool_calls:
                    logger.info(f"🔧 [OpenAILike] 非流式 tool_calls 收到: {len(tool_calls)} 个工具调用")
                elif not content:
                    logger.warning(f"⚠️ 模型返回内容为空 | 原报文: {result}")
                    if reasoning:
                        content = reasoning
                        logger.info("🔧 [OpenAILike] 回退机制：使用 reasoning_content 填充空 content")

                return LLMResponseDelta(
                    content=str(content),
                    role=message.get("role"),
                    finish_reason=result["choices"][0].get("finish_reason"),
                    tool_calls=tool_calls,
                    reasoning_content=reasoning,
                )
            except (httpx.NetworkError, httpx.TimeoutException) as e:
                # [Fix] 严禁返回内容负载，必须抛出异常以触发 LLMClient 的 Failover 机制
                # [Fix] Must raise exception to trigger LLMClient Failover, never return content payload
                logger.error(f"🌐 [OpenAILike] 网络异常: {e}")
                raise e
            except Exception as e:
                # 尝试打印出原始报文以便调试
                # Try to print the raw message for debugging
                try:
                    if "result" in locals():
                        logger.error(f"❌ API 原始报错报文: {json.dumps(result, ensure_ascii=False)}")
                except Exception:
                    pass
                logger.error(f"🚨 [OpenAILike] 关键异常: {e}")
                raise e

    def _safe_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        safe_msg = []
        for m in messages:
            raw_content = m.get("content")
            # FC assistant messages legitimately have content=None (tool_calls only).
            # Preserve None so providers validate the FC conversation format correctly.
            # For all other messages, fall back to "" to avoid API 422 on missing content.
            if raw_content is None and m.get("tool_calls"):
                content = None
            else:
                content = raw_content or ""
            if isinstance(content, str):
                content = content.encode("utf-8", "ignore").decode("utf-8", "ignore")
            elif isinstance(content, list):
                # 处理多模态内容
                # Handle multimodal content
                new_content = []
                for part in content:
                    if part.get("type") == "text":
                        part["text"] = part["text"].encode("utf-8", "ignore").decode("utf-8", "ignore")
                    new_content.append(part)
                content = new_content
            safe_msg.append({**m, "content": content})
        return safe_msg

    async def close(self):
        await self.client.aclose()
