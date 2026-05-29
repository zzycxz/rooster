import os
import json
import asyncio
import logging
import re
import httpx
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)

# Status file lives in the runtime state dir (.rooster/) so it is never committed
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
STATUS_FILE = os.path.join(_PROJECT_ROOT, ".rooster", "search_status.json")


def get_mcp_status() -> bool:
    """Read persisted Zhipu MCP quota status. Defaults to True (available) when missing."""
    try:
        if os.path.exists(STATUS_FILE):
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("glm_plan_search_active", True)
    except Exception as e:
        logger.debug(f"Read search status error: {e}")
    return True


def set_mcp_status(active: bool):
    """Persist Zhipu MCP quota status with a file-level lock to prevent race conditions."""
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        try:
            from filelock import FileLock

            lock = FileLock(STATUS_FILE + ".lock", timeout=5)
            lock_ctx = lock
        except ImportError:
            import contextlib

            lock_ctx = contextlib.nullcontext()

        with lock_ctx:
            data = {}
            if os.path.exists(STATUS_FILE):
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    try:
                        data = json.load(f)
                    except Exception:
                        data = {}
            data["glm_plan_search_active"] = active
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[GLMPlanSearch] MCP status persisted: {active}")
    except Exception as e:
        logger.error(f"Failed to write search status: {e}")


class GLMPlanSearchArgs(BaseModel):
    query: str = Field(description="The search query string to lookup on the web.")
    domain_filter: Optional[str] = Field(
        default=None, description="Optional whitelist domain to restrict results, e.g., 'www.wikipedia.org'."
    )
    recency_filter: Optional[str] = Field(
        default="noLimit",
        description="Search time range filter. Options: 'oneDay', 'oneWeek', 'oneMonth', 'oneYear', 'noLimit'.",
    )
    content_size: Optional[str] = Field(
        default="medium",
        description="Search result detail size. Options: 'medium' (balanced summaries), 'high' (comprehensive details).",
    )


class GLMPlanSearchTool(BaseTool):
    """基于智谱 GLM Coding Plan 官方 SSE 协议的 MCP 联网搜索核心主力工具（集成额度超限自愈降级）"""

    name: str = "glm_plan_search"
    kit: str = "Search"
    description: str = (
        "Zhipu GLM Coding Plan official Model Context Protocol (MCP) Web Search service. "
        "Performs high-quality cloud search and returns clean, structured web summaries, titles, and sources."
    )
    domain: str = "recon"
    fc_hidden: bool = True  # [Round 9] exa_search 内置 4 级降级链（Exa→Linkup→GLM→web_search），LLM 无需直接调用
    args_schema: Type[BaseModel] = GLMPlanSearchArgs

    async def run(self, **kwargs) -> str:
        query = kwargs.get("query")
        if not query:
            return "Error: No search query provided."

        domain_filter = kwargs.get("domain_filter")
        recency_filter = kwargs.get("recency_filter", "noLimit")
        content_size = kwargs.get("content_size", "medium")

        # ----------------- 1. Pre-flight quota self-check -----------------
        # ----------------- 🎯 1. 运行前状态自检 (Pre-flight Quota Check) -----------------
        if not get_mcp_status():
            logger.warning(
                "⚠️ [GLMPlanSearch] Previously marked as [quota exceeded/billing issue], triggering pre-healing route, seamlessly downgrading to standard polling search..."
            )
            logger.warning(
                "⚠️ [GLMPlanSearch] 探测到先前已标记为【额度超限/欠费】，触发预先自愈路由，无缝降级至普通轮询搜索..."
            )
            return await self._fallback_to_common_search(query)

        # Prefer ZHIPU_MCP_SEARCH_KEY; fall back to global ZHIPU_KEY if absent
        # 优先读取 ZHIPU_MCP_SEARCH_KEY，如无则回退使用全局 ZHIPU_KEY
        api_key = os.getenv("ZHIPU_MCP_SEARCH_KEY") or os.getenv("ZHIPU_KEY")
        if not api_key:
            return (
                "Error: Zhipu API Key is not set.\n"
                "Please configure 'ZHIPU_MCP_SEARCH_KEY' or 'ZHIPU_KEY' in your '.env.local' file."
            )

        # Strip possible Bearer prefix or whitespace to ensure clean credentials
        # 剥离可能存在的 Bearer 前缀或空格，确保证书纯净
        api_key = api_key.strip()
        if api_key.lower().startswith("bearer "):
            api_key = api_key[7:].strip()

        # Assemble global auth headers for both SSE and POST paths
        # 组装全局鉴权头与流式要求头，确保 SSE 和 POST 双路鉴权彻底打通
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        # Establish MCP SSE long connection and subscribe to get the dedicated sessionId POST endpoint
        # 建立标准的 MCP SSE 长连接，订阅并获取专属的 sessionId POST 端点
        sse_url = f"https://open.bigmodel.cn/api/mcp/web_search_prime/sse?Authorization={api_key}"
        post_url = None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                async with client.stream("GET", sse_url, headers=headers) as response:
                    if response.status_code != 200:
                        # 401/403 errors usually indicate credential or quota issues; trigger mark & downgrade
                        # 401/403 异常，往往是证书或额度问题，触发标记降级
                        if response.status_code in (401, 403, 429):
                            logger.error(
                                f"❌ [GLMPlanSearch] SSE handshake failed (HTTP {response.status_code}). Marking and downgrading to standard search..."
                            )
                            logger.error(
                                f"❌ [GLMPlanSearch] SSE 握手响应失败 (HTTP {response.status_code})。做好标记并降级切换普通搜索..."
                            )
                            set_mcp_status(False)
                            return await self._fallback_to_common_search(query)
                        return f"Error: Failed to handshake with Zhipu MCP SSE server: {response.status_code}"

                    done_handshake = asyncio.Event()
                    output_chunks = []
                    error_container = []

                    # Declare a single-lifetime aiter_text() stream reading task
                    # 声明单一、唯一生命周期的 aiter_text() 流读取任务
                    async def read_stream():
                        nonlocal post_url
                        buffer = ""
                        try:
                            # Pure text append channel until stream naturally closes
                            # 100% 物理必然通畅的纯文本追加通道，直到流自然关闭
                            async for chunk in response.aiter_text():
                                output_chunks.append(chunk)
                                buffer += chunk

                                # Sniff and capture POST endpoint
                                # 嗅探并捕获 POST endpoint
                                if not post_url:
                                    for line in buffer.splitlines():
                                        line = line.strip()
                                        if line.startswith("data:"):
                                            endpoint_path = line[5:].strip()
                                            post_url = f"https://open.bigmodel.cn{endpoint_path}"
                                            done_handshake.set()
                                            break
                        except Exception as e:
                            error_container.append(f"Stream reading error: {e}")
                        finally:
                            done_handshake.set()  # Fallback to prevent deadlock
                            done_handshake.set()  # 兜底防止死锁

                    # Start background stream reading task
                    # 启动后台流监听 Task
                    read_task = asyncio.create_task(read_stream())

                    # 等待握手完成以捕获 POST 路径，加入 10 秒超时防护
                    try:
                        await asyncio.wait_for(done_handshake.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        read_task.cancel()
                        try:
                            await read_task
                        except asyncio.CancelledError:
                            pass
                        return "Error: Handshake timeout waiting for Zhipu MCP SSE endpoint."

                    if not post_url:
                        read_task.cancel()
                        try:
                            await read_task
                        except asyncio.CancelledError:
                            pass
                        return f"Error: Failed to obtain valid session post endpoint. Details: {os.linesep.join(error_container)}"

                    # Assemble parameters for POST delivery, strip any redundant undefined params
                    # 组装参数并准备投递 POST，清洗一切冗余未定义参数
                    arguments = {"search_query": query}
                    if recency_filter and recency_filter != "noLimit":
                        arguments["search_recency_filter"] = recency_filter
                    if domain_filter:
                        arguments["search_domain_filter"] = domain_filter
                    if content_size and content_size != "medium":
                        arguments["content_size"] = content_size

                    payload = {
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {"name": "web_search_prime", "arguments": arguments},
                        "id": 1,
                    }

                    # ----------------- 2. Network delivery and response check (POST Send Check) -----------------
                    # ----------------- 🎯 2. 网络投递与响应检测 (POST Send Check) -----------------
                    try:
                        async with httpx.AsyncClient(timeout=15.0) as client_post:
                            post_resp = await client_post.post(post_url, headers=headers, json=payload)
                            if post_resp.status_code not in (200, 202):
                                set_mcp_status(False)
                                logger.error(
                                    f"[GLMPlanSearch] POST delivery failed (HTTP {post_resp.status_code}). Falling back..."
                                )
                                read_task.cancel()
                                try:
                                    await read_task
                                except asyncio.CancelledError:
                                    pass
                                return await self._fallback_to_common_search(query)
                    except Exception as post_err:
                        error_container.append(f"Failed to post tools/call request: {post_err}")

                    # If POST raised a network-level exception, cancel stream and return error
                    if error_container:
                        read_task.cancel()
                        try:
                            await read_task
                        except asyncio.CancelledError:
                            pass
                        return "\n".join(error_container)

                    # If POST succeeded, wait for background read_task to finish (max 20s timeout)
                    # 若 POST 成功，平和地等待后台 read_task 自然读到流尽头（或者最大 20 秒超时）
                    try:
                        await asyncio.wait_for(read_task, timeout=20.0)
                    except asyncio.TimeoutError:
                        # On timeout, try to salvage received data instead of reporting error
                        # 超时也尝试去抢救已接收数据，这里不直接报错
                        pass

                    # ----------------- 3. In-stream deep feature matching and parsing -----------------
                    # ----------------- 🎯 3. 运行中事件流深度特征匹配与解析 -----------------
                    full_text = "".join(output_chunks)

                    # Check raw data for specific errors (detect quota exhaustion patterns)
                    # 首先检测返回的原始数据中是否包含特定报错（捕获 Quota 耗尽特征）
                    for line in full_text.splitlines():
                        line = line.strip()
                        if line.startswith("data:") and "/message?sessionId=" not in line:
                            json_str = line[5:].strip()
                            try:
                                resp_data = json.loads(json_str)
                                if "error" in resp_data:
                                    err_msg = resp_data["error"].get("message", resp_data["error"])
                                    err_lower = str(err_msg).lower()
                                    # Smart intercept quota insufficient, rate-limit and other error keywords
                                    # 智能拦截额度不足、频控等报错关键字
                                    if any(
                                        k in err_lower
                                        for k in [
                                            "quota",
                                            "limit",
                                            "insufficient",
                                            "credit",
                                            "balance",
                                            "key not found",
                                            "unauthorized",
                                        ]
                                    ):
                                        logger.error(
                                            f"❌ [GLMPlanSearch] Detected Zhipu MCP quota exhaustion or auth anomaly: '{err_msg}'. Marking invalid and downgrading to standard search..."
                                        )
                                        logger.error(
                                            f"❌ [GLMPlanSearch] 探测到智谱 MCP 额度耗尽或授权异常: '{err_msg}'。标记失效并降级切换普通搜索..."
                                        )
                                        set_mcp_status(False)
                                        return await self._fallback_to_common_search(query)
                            except Exception:
                                pass

                    # Regex rescue for webpage metadata extraction and rendering
                    # 物理正则抢救网页元数据并渲染
                    pattern = re.compile(
                        r'\\*"title\\*"\s*:\s*\\*"([^"]+?)\\*"\s*,\s*\\*"link\\*"\s*:\s*\\*"([^"]+?)\\*"\s*,\s*\\*"content\\*"\s*:\s*\\*"([^"]+?)\\*"'
                    )
                    matches = pattern.findall(full_text)

                    markdown_items = []
                    for idx, (title, link, content) in enumerate(matches, 1):
                        title = title.replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\")
                        link = link.replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\")
                        content = content.replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\")

                        markdown_items.append(f"- {title}\n  {link}\n  {content}")

                    if markdown_items:
                        return "\n\n".join(markdown_items)

                    # Fallback: try standard JSON parsing
                    # 回退尝试普通 JSON 解析
                    output_texts = []
                    for line in full_text.splitlines():
                        line = line.strip()
                        if line.startswith("data:") and "/message?sessionId=" not in line:
                            json_str = line[5:].strip()
                            try:
                                resp_data = json.loads(json_str)
                                result = resp_data.get("result", {})
                                for item in result.get("content", []):
                                    if item.get("type") == "text":
                                        output_texts.append(item.get("text", ""))
                            except Exception:
                                pass

                    if output_texts:
                        return "\n\n".join(output_texts)

                    # ----------------- 4. Timeout or no-result self-healing downgrade -----------------
                    # ----------------- 🎯 4. 超时或无结果自愈降级 -----------------
                    # If no valid search results found (e.g. network black swan), downgrade to standard search
                    # 如果走到这里还没有拿到任何有效搜索结果（例如发生网络黑天鹅事件导致无返回），
                    # 我们不要给用户返回冷冰冰的 "No search results"，而是直接降级跑普通搜索，提供 100% 极致体验！
                    logger.warning(
                        "⚠️ [GLMPlanSearch] No valid search results from stream, triggering self-healing fallback to standard search..."
                    )
                    logger.warning("⚠️ [GLMPlanSearch] 未能在流中获取到有效搜索结果，触发自愈兜底普通搜索...")
                    return await self._fallback_to_common_search(query)

        except httpx.RequestError as exc:
            logger.error(f"🌩️ [GLMPlanSearch] Network error: {exc}. Attempting downgrade to standard search...")
            logger.error(f"🌩️ [GLMPlanSearch] 网络异常: {exc}。尝试降级切换普通搜索...")
            return await self._fallback_to_common_search(query)
        except Exception as e:
            logger.error(f"❌ [GLMPlanSearch] Unknown runtime error: {e}. Attempting downgrade to standard search...")
            logger.error(f"❌ [GLMPlanSearch] 运行未知异常: {e}。尝试降级切换普通搜索...")
            return await self._fallback_to_common_search(query)

    async def _fallback_to_common_search(self, query: str) -> str:
        """Core fallback route: smoothly dispatch to built-in standard web_search engine.
        核心降级路由：平滑调度内置的普通 web_search 引擎"""
        try:
            from toolset.definitions.browser import WebSearchTool

            fallback_tool = WebSearchTool()
            # Pass query to standard web polling search tool
            # 将 query 传入普通网页轮询搜索工具中
            logger.info(f"🔄 [GLMPlanSearch] Running standard polling web search, keyword: '{query}'...")
            logger.info(f"🔄 [GLMPlanSearch] 正在无缝运行普通轮询网页搜索，关键词: '{query}'...")
            return await fallback_tool.run(query=query)
        except Exception as err:
            return f"Error: Failed to routing to fallback search engine: {err}"
