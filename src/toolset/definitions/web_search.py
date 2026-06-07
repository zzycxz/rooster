import os
import json
import logging
import time
import asyncio
import re
import urllib.request
import urllib.parse
from typing import Type, Dict, Optional
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
import httpx
from utils.config import settings
from utils.browser.manager import BrowserManager
from toolset.base import BaseTool

logger = logging.getLogger(__name__)


class WebSearchArgs(BaseModel):
    query: str = Field(description="The search query or topic to research.")
    en_keywords: str = Field(
        "", description="Optional English keywords for mixed-language optimization and local reranking."
    )
    domain_filter: Optional[str] = Field(None, description="Optional domain constraint (e.g. 'github.com').")
    time_range: Optional[str] = Field("any", description="Time range: 'day', 'week', 'month', 'year', 'any'.")
    deep_research: bool = Field(False, description="Set True for deep iteration research (actively triggers Linkup).")


def get_exa_active() -> bool:
    try:
        path = os.path.join(settings.ROOSTER_HOME, "search_status.json")
        if not os.path.exists(path):
            return True
        with open(path, "r") as f:
            data = json.load(f)
            return data.get("exa_active", True)
    except Exception:
        return True


def disable_exa():
    try:
        path = os.path.join(settings.ROOSTER_HOME, "search_status.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
        data["exa_active"] = False
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


_MONTHLY_QUOTA = 1000


def _get_exa_usage() -> int:
    try:
        path = os.path.join(settings.ROOSTER_HOME, "search_status.json")
        if not os.path.exists(path):
            return 0
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("exa_monthly_usage", 0)
    except Exception:
        return 0


def _increment_exa_usage():
    try:
        path = os.path.join(settings.ROOSTER_HOME, "search_status.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
        data["exa_monthly_usage"] = data.get("exa_monthly_usage", 0) + 1
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def get_mcp_status() -> bool:
    try:
        path = os.path.join(settings.ROOSTER_HOME, "search_status.json")
        if not os.path.exists(path):
            return True
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("glm_plan_search_active", True)
    except Exception:
        return True


def set_mcp_status(active: bool):
    try:
        path = os.path.join(settings.ROOSTER_HOME, "search_status.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        data = {}
        if os.path.exists(path):
            with open(path, "r") as f:
                data = json.load(f)
        data["glm_plan_search_active"] = active
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


class WebSearchTool(BaseTool):
    name: str = "web_search"
    kit: str = "System"
    fc_hidden: bool = False
    description: str = (
        "Primary search tool with 5-tier dynamic fallback. Use domain_filter only if strictly requested by the user."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = WebSearchArgs

    _search_cache: Dict[str, tuple] = {}
    _circuit_breaker: Dict[str, dict] = {
        "Tavily": {"failures": 0, "status": "PENDING"},
        "SearXNG": {"failures": 0, "status": "PENDING"},
    }
    _SEARXNG_COOLDOWN: float = 120.0
    _SEARCH_CACHE_TTL: int = 600  # 10 分钟

    def _check_exa_quota(self) -> bool:
        return get_exa_active()

    def _mark_exa_exhausted(self):
        disable_exa()

    async def _fallback(self, query: str) -> str:
        """Linkup 降级入口：返回错误字符串触发 run() 中的下一级降级"""
        return "Error: Linkup fallback requested."

    async def _fallback_to_common_search(self, query: str) -> str:
        """GLM 降级入口：返回错误字符串触发 run() 中的 7 路并发兜底"""
        return "Error: GLM fallback to common search requested."

    def _is_valid_result(self, res: str) -> bool:
        if not res or len(res.strip()) == 0:
            return False
        error_signatures = ["Error:", "HTTP 401", "HTTP 429", "Authentication failed"]
        if any(sig in res for sig in error_signatures):
            return False
        return True

    async def run(self, **kwargs) -> str:
        query = kwargs.get("query")
        en_keywords = kwargs.get("en_keywords", "")
        deep_research = kwargs.get("deep_research", False)

        if not query:
            return "Error: No search query provided."

        # Tier 1: Linkup
        if deep_research and os.getenv("LINKUP_KEY"):
            try:
                res = await self._run_linkup(kwargs)
                if self._is_valid_result(res):
                    return res
            except asyncio.TimeoutError:
                logger.warning("[WebSearch] Linkup timeout.")
            except Exception as e:
                logger.warning(f"[WebSearch] Linkup failed: {e}")

        # Tier 2: Exa
        if os.getenv("EXA_KEY") and self._check_exa_quota():
            try:
                res = await self._run_exa(kwargs)
                if self._is_valid_result(res):
                    return res
            except asyncio.TimeoutError:
                logger.warning("[WebSearch] Exa timeout.")
            except Exception as e:
                if "429" in str(e) or "quota" in str(e).lower():
                    self._mark_exa_exhausted()
                logger.warning(f"[WebSearch] Exa failed: {e}")

        # Tier 3: GLM
        if os.getenv("ZHIPU_CODINGPLAN_KEY"):
            try:
                res = await self._run_glm(kwargs)
                if self._is_valid_result(res):
                    return res
            except Exception as e:
                logger.warning(f"[WebSearch] GLM search failed: {e}")

        # Tier 4 & 5: 7路并发兜底
        try:
            return await self._seven_lane_search(query, en_keywords)
        except Exception as e:
            logger.error(f"[WebSearch] 7-lane fallback crashed: {e}")
            return f"搜索系统异常: {e}"

    async def _run_linkup(self, kwargs: dict) -> str:
        query = kwargs.get("query")
        if not query:
            return "Error: No search query provided."

        api_key = os.getenv("LINKUP_KEY", "")
        if not api_key:
            return "Error: LINKUP_KEY is not set. Add it to .env.local to enable Linkup search."

        depth = kwargs.get("depth", "standard")
        output_type = kwargs.get("output_type", "searchResults")
        max_results = min(max(kwargs.get("max_results", 5), 1), 10)
        include_domains = kwargs.get("include_domains")
        exclude_domains = kwargs.get("exclude_domains")
        from_date = kwargs.get("from_date")
        to_date = kwargs.get("to_date")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "q": query,
            "depth": depth,
            "outputType": output_type,
            "maxResults": max_results,
        }

        if include_domains:
            payload["includeDomains"] = [d.strip() for d in include_domains.split(",")]
        if exclude_domains:
            payload["excludeDomains"] = [d.strip() for d in exclude_domains.split(",")]
        if from_date:
            payload["fromDate"] = from_date
        if to_date:
            payload["toDate"] = to_date

        try:
            timeout = 30.0 if depth == "deep" else 15.0
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    "https://api.linkup.so/v1/search",
                    headers=headers,
                    json=payload,
                )

                if resp.status_code == 401:
                    return "Error: Invalid LINKUP_KEY. Please check your API key."
                if resp.status_code == 429:
                    logger.warning("[LinkupSearch] Rate limited (429), falling back.")
                    return await self._fallback(query)
                if resp.status_code != 200:
                    err_text = resp.text[:300]
                    logger.error(f"[LinkupSearch] HTTP {resp.status_code}: {err_text}")
                    return await self._fallback(query)

                data = resp.json()

                # sourcedAnswer mode: return the answer with sources
                if output_type == "sourcedAnswer":
                    answer = data.get("answer", "")
                    sources = data.get("sources", [])
                    if not answer and not sources:
                        return await self._fallback(query)

                    parts = []
                    if answer:
                        parts.append(answer)
                    if sources:
                        parts.append("\n**Sources:**")
                        for i, s in enumerate(sources, 1):
                            name = s.get("name", "Untitled")
                            url = s.get("url", "")
                            snippet = s.get("snippet", "")
                            entry = f"[{i}] [{name}]({url})"
                            if snippet:
                                entry += f"\n   {snippet[:200]}"
                            parts.append(entry)

                    logger.info(f"[LinkupSearch] sourcedAnswer success. Sources: {len(sources)}")
                    return "\n".join(parts) if parts else await self._fallback(query)

                # searchResults mode: return structured links
                results = data.get("results", [])
                if not results:
                    return await self._fallback(query)

                logger.info(f"[LinkupSearch] Success ({len(results)} results, depth={depth})")

                items = []
                for i, r in enumerate(results, 1):
                    name = r.get("name", "Untitled")
                    url = r.get("url", "")
                    content = r.get("content", "")

                    if content:
                        entry = f"- {name}\n  {url}\n  {content}"
                    else:
                        entry = f"- {name}\n  {url}"
                    items.append(entry)

                return f"Search results for: {query}\n\n" + "\n\n".join(items)

        except httpx.RequestError as e:
            logger.error(f"[LinkupSearch] Network error: {e}")
            return await self._fallback(query)
        except Exception as e:
            logger.error(f"[LinkupSearch] Error: {e}")
            return await self._fallback(query)

    async def _run_exa(self, kwargs: dict) -> str:
        query = kwargs.get("query")
        if not query:
            return "Error: No search query provided."

        api_key = os.getenv("EXA_KEY", "")
        if not api_key:
            logger.info("[ExaSearch] EXA_KEY not set, falling back.")
            return "Error: Quota exhausted or fallback requested."

        if not get_exa_active():
            logger.warning("[ExaSearch] Monthly quota exhausted, falling back to GLM/web search.")
            return "Error: Quota exhausted or fallback requested."

        num_results = min(max(int(kwargs.get("num_results", 5)), 1), 10)
        use_autoprompt = kwargs.get("use_autoprompt", True)
        search_type = kwargs.get("type", "auto")
        category = kwargs.get("category")
        start_published_date = kwargs.get("start_published_date")
        include_domains = kwargs.get("include_domains")
        exclude_domains = kwargs.get("exclude_domains")

        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }

        payload = {
            "query": query,
            "numResults": num_results,
            "type": search_type,
            "useAutoprompt": use_autoprompt,
            "contents": {
                "text": {"maxCharacters": 3000},
                "summary": {"maxCharacters": 500},
            },
        }

        if category:
            payload["category"] = category
        if start_published_date:
            payload["startPublishedDate"] = start_published_date
        if include_domains:
            payload["includeDomains"] = [d.strip() for d in include_domains.split(",")]
        if exclude_domains:
            payload["excludeDomains"] = [d.strip() for d in exclude_domains.split(",")]

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    "https://api.exa.ai/search",
                    headers=headers,
                    json=payload,
                )

                if resp.status_code == 401:
                    logger.warning("[ExaSearch] Invalid EXA_KEY (401), falling back.")
                    return "Error: Quota exhausted or fallback requested."
                if resp.status_code == 429:
                    logger.warning("[ExaSearch] Rate limited (429), falling back.")
                    return "Error: Quota exhausted or fallback requested."
                if resp.status_code != 200:
                    err_text = resp.text[:300]
                    logger.error(f"[ExaSearch] HTTP {resp.status_code}: {err_text}")
                    return "Error: Quota exhausted or fallback requested."

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    logger.info("[ExaSearch] No results from Exa, trying fallback.")
                    return "Error: Quota exhausted or fallback requested."

                _increment_exa_usage()
                usage = _get_exa_usage()
                logger.info(f"[ExaSearch] Success ({len(results)} results). Monthly usage: {usage}/{_MONTHLY_QUOTA}")

                items = []
                for i, r in enumerate(results, 1):
                    title = r.get("title", "Untitled")
                    url = r.get("url", "")
                    summary = r.get("summary", "")
                    text = r.get("text", "")
                    published = r.get("publishedDate", "")

                    # 优先用 summary，再用 text 全文（调研报告需要充实内容）
                    content = summary or text or ""
                    parts = [f"- {title}\n  {url}"]
                    if published:
                        parts.append(f"  Date: {published[:10]}")
                    if content:
                        parts.append(f"  {content}")
                    items.append("\n".join(parts))

                header = f"Search results for: {query}"
                if usage > _MONTHLY_QUOTA * 0.8:
                    header += f"  (Exa monthly usage: {usage}/{_MONTHLY_QUOTA})"
                return header + "\n\n" + "\n\n".join(items)

        except httpx.RequestError as e:
            logger.error(f"[ExaSearch] Network error: {repr(e)}")
            return "Error: Quota exhausted or fallback requested."
        except Exception as e:
            logger.error(f"[ExaSearch] Error: {repr(e)}")
            return "Error: Quota exhausted or fallback requested."

    async def _run_glm(self, kwargs: dict) -> str:
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
        api_key = os.getenv("ZHIPU_MCP_SEARCH_KEY") or os.getenv("ZHIPU_CODINGPLAN_KEY")
        if not api_key:
            return (
                "Error: Zhipu API Key is not set.\n"
                "Please configure 'ZHIPU_MCP_SEARCH_KEY' or 'ZHIPU_CODINGPLAN_KEY' in your '.env.local' file."
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

    def _preflight_check(self, name: str, key_val: str) -> bool:
        """零配置空值自动感知与激活判定"""
        state = self.__class__._circuit_breaker.get(name)
        if not state:
            return False
        if not key_val or key_val.strip() == "":
            state["status"] = "DISABLED"
            return False
        if state["status"] in ["PENDING", "DISABLED"]:
            state["status"] = "ACTIVE"
        return state["status"] == "ACTIVE"

    def _record_success(self, name: str):
        """成功时立即清空熔断计数"""
        if name in self.__class__._circuit_breaker:
            self.__class__._circuit_breaker[name]["failures"] = 0
            self.__class__._circuit_breaker[name]["status"] = "ACTIVE"

    def _record_failure(self, name: str):
        """三击熔断自愈控制器"""
        if name in self.__class__._circuit_breaker:
            state = self.__class__._circuit_breaker[name]
            state["failures"] += 1
            if state["failures"] >= 3:
                state["status"] = "FUSED"
                logging.warning(
                    f"🌩️ [Circuit Breaker] {name} 通道连续 3 次抛出异常。拉响熔断警报！"
                    f"当前会话已停用该通道，自动降级至 0-Key 免密 HTML 赛道。"
                )

    async def _seven_lane_search(self, query: str, en_keywords: str) -> str:
        """7 路并发搜索逻辑"""
        try:
            cache_key = query.strip().lower()
            now = time.time()
            # 1. Check search cache; return immediately on hit
            # 1. 检查 Search Cache，若命中直接秒级返回
            if cache_key in self.__class__._search_cache:
                cached_result, cached_time = self.__class__._search_cache[cache_key]
                if now - cached_time < self.__class__._SEARCH_CACHE_TTL:
                    logging.info(f"📦 [Search] 搜索缓存直接命中: {query}")
                    return cached_result

            # 2. 7-lane concurrent super-racing
            # 2. 7路超跑并发赛道竞速跑
            all_raw = await self._search_concurrent(query)

            # 3. If all 7 lanes return no results (extremely rare offline) -> Playwright deep browser fallback
            # 3. 7路并发由于极其罕见断网无结果 -> Playwright 深度浏览器兜底
            if not all_raw:
                logging.warning("⚠️ 7路并发无结果，启动 Playwright 兜底补位...")
                all_raw = await self._search_fallback_dynamic(query)

            if not all_raw:
                return "未能找到搜索结果，建议更换关键词。"

            # 4. [Plan A: local 1ms fast word-frequency rerank] — first-screen response in 0.3s!
            # 4. 【方案一：本地 1ms 快速词频打分重排】—— 首屏响应 0.3 秒极致速度！
            local_streamlined = self._rerank_local_algebraic(all_raw, query, en_keywords)
            if not local_streamlined:
                return "未能找到高相关性的搜索结果，建议更换关键词。"

            final_res = [f"Search results for: {query}"]
            final_res.extend(local_streamlined)
            first_screen_result = "\n\n".join(final_res)

            # 5. Cache first-screen result immediately for next-round cache hit
            # 5. 首屏结果立刻存入缓存，保障本次返回后，用户下一轮交互若命中即可直接消费
            self.__class__._search_cache[cache_key] = (first_screen_result, now)
            self._prune_search_cache()

            # 6. [Plan A async rerank sentinel] — silently launch LLM semantic rerank in background, refresh cache after 1s!
            # 6. 【方案一异步重排哨兵】—— 后台默默拉起大模型语义 Rerank，1秒后刷新缓存矫正记忆！
            _rerank_task = asyncio.create_task(self._async_llm_rerank_and_update_cache(query, all_raw, cache_key, now, en_keywords))
            _rerank_task.add_done_callback(
                lambda t: logger.warning(f"Background rerank failed: {t.exception()}") if not t.cancelled() and t.exception() else None
            )

            # 7. Return the first-screen 1ms physical result instantly!
            # 7. 瞬间把首屏 1ms 物理结果返回，速度快得不可思议！
            return first_screen_result

        except Exception as e:
            logging.error(f"❌ 搜索流程异常: {str(e)}")
            return f"搜索系统异常: {str(e)}"

    async def _async_llm_rerank_and_update_cache(
        self, query: str, raw_results: list, cache_key: str, cache_time: float, en_keywords: str
    ):
        """后台异步语义重排序矫正器，温热写入本地缓存"""
        if not raw_results:
            return
        try:
            # Only take top 10 high-quality raw entries from concurrent results to send to LLM
            # 仅取并发回来的前 10 条高质量原始条目送交大模型
            candidates = raw_results[:10]

            from agents.llm_client import LLMClient

            # Reuse the fast model (no extra key overhead)
            # 复用大脑小脑的极速快速模型，0 额外 Key 门槛
            llm = LLMClient(provider=settings.FAST_MODEL_PROVIDER, model=settings.FAST_MODEL_NAME, lightweight=True)

            prompt_content = f"""你是一个搜索引擎结果重排（Re-ranking）专家。
请仔细阅读以下最多 10 个网页搜索结果，分析它们与用户当前问题“{query}”的语义相关性、时效性与真实度。

网页列表：
"""
            for idx, r in enumerate(candidates, 1):
                prompt_content += f"{idx}. Title: {r.get('title', '')}, Snippet: {r.get('content', '')[:120]}\n"

            prompt_content += """
请从上面列表中挑选出前 5 个最能帮助解答用户提问、且内容质量最高、最无广告干扰的网页序号。
请严格仅返回这 5 个结果在上面列表中的序号，以合法的 JSON 数组格式输出，不要任何其他的解释。
例如：[3, 1, 5, 2, 4]"""

            messages = [
                {
                    "role": "system",
                    "content": "You are a precise search ranker. Only output JSON arrays like [1, 2, 3].",
                },
                {"role": "user", "content": prompt_content},
            ]

            response = await asyncio.wait_for(
                llm.chat_non_stream(messages),
                timeout=getattr(settings, "LLM_CALL_TIMEOUT", 120.0),
            )
            raw = response.content.strip()

            # Strip possible Markdown code block markers
            # 去掉可能的 Markdown Code Block 标记
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            import json

            ranked_indices = json.loads(raw)
            if isinstance(ranked_indices, list) and len(ranked_indices) > 0:
                final_items = []
                seen_urls = set()
                for idx in ranked_indices:
                    real_idx = int(idx) - 1
                    if 0 <= real_idx < len(candidates):
                        item = candidates[real_idx]
                        url = item.get("url", "")
                        if url and url not in seen_urls:
                            final_items.append(item)
                            seen_urls.add(url)

                # Reassemble high-fidelity semantic LLM reranked card results
                # 重新拼装高保真语义大模型重排序卡片结果
                streamlined_cards = []
                seen_domains = set()
                for r in final_items[: settings.SEARCH_MAX_RESULTS]:
                    title = r.get("title", "").strip()
                    url = r.get("url", "")
                    domain = url.split("/")[2] if "//" in url else url.split("/")[0]
                    if domain in seen_domains and "wikipedia" not in domain:
                        continue

                    snippet = r.get("content", "").strip()
                    if snippet:
                        if len(snippet) > 200:
                            snippet = snippet[:200] + "..."
                        streamlined_cards.append(f"- {title}\n  {url}\n  {snippet}")
                    else:
                        streamlined_cards.append(f"- {title}\n  {url}")
                    seen_domains.add(domain)

                if streamlined_cards:
                    final_res = [f"Search results for: {query}"]
                    final_res.extend(streamlined_cards)
                    optimized_result = "\n\n".join(final_res)

                    # Silently overwrite local cache, completing perfect background semantic correction!
                    # 默默覆写本地缓存记忆，完成完美的后台语义矫正自愈！
                    self.__class__._search_cache[cache_key] = (optimized_result, cache_time)
                    logging.info("🧠 [Rerank] 后台异步语义打分重排序成功！已默默覆写矫正本地缓存。")
        except Exception as e:
            logging.debug(f"🔍 [Rerank] 后台异步大模型打分失败: {e}，保留首屏本地打分缓存。")

    def _rerank_local_algebraic(self, results: list, query: str, en_keywords: str = "") -> list:
        """本地 1ms 词频正则打分算法 (防断网、防限流防线)"""
        final = []
        seen_urls = set()
        seen_domains = set()

        zh_words = set(re.findall(r"\w+", query.lower()))
        en_words = set(re.findall(r"\w+", en_keywords.lower())) if en_keywords else set()
        all_signals = zh_words | en_words
        query_numbers = set(re.findall(r"\d+", query))

        scored_results = []
        for r in results:
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue

            title = r.get("title", "").strip()
            if not title:
                continue

            # 1. Engine base score
            # 1. 引擎基础分
            score = r.get("score", 0.6)

            # 2. Title token match bonus (+0.05 per hit, max 0.15)
            # 2. 标题切词匹配加分 (每命中一个切词加 0.05，上限 0.15)
            title_lower = title.lower()
            matches = sum(1 for word in all_signals if word in title_lower)
            score += min(0.15, matches * 0.05)

            # 3. Timeliness number strong-match privilege (bonus +0.10)
            # 3. 时效性数字强命中特权 (奖励 +0.10)
            if query_numbers:
                title_numbers = set(re.findall(r"\d+", title))
                if query_numbers & title_numbers:
                    score += 0.10

            # 4. Spam snippet filter (penalty -0.30)
            # 4. 垃圾摘要屏蔽 (惩罚 -0.30)
            snippet = r.get("content", "").strip()
            if any(
                trash in snippet.lower() or trash in title_lower
                for trash in ["广告", "促销", "ad", "promotion", "无法访问"]
            ):
                score -= 0.30

            scored_results.append((score, r))
            seen_urls.add(url)

        # Sort by local score descending
        # 按本地得分降序重排
        scored_results.sort(key=lambda x: x[0], reverse=True)

        for score, r in scored_results:
            # 0.70 score pass threshold (below = discard)
            # 0.70分良好放行防线 (不及格则丢弃)
            if score < 0.70:
                continue

            url = r.get("url", "")
            domain = url.split("/")[2] if "//" in url else url.split("/")[0]
            if domain in seen_domains and "wikipedia" not in domain:
                continue

            title = r.get("title", "").strip()
            snippet = r.get("content", "").strip()
            if snippet:
                if len(snippet) > 200:
                    snippet = snippet[:200] + "..."
                final.append(f"- {title}\n  {url}\n  {snippet}")
            else:
                final.append(f"- {title}\n  {url}")

            seen_domains.add(domain)
            if len(final) >= settings.SEARCH_MAX_RESULTS:
                break

        return final

    async def _resolve_redirect(self, client: httpx.AsyncClient, url: str) -> str:
        """轻量级 HEAD/GET 跳转追踪器，100% 还原百度/搜狗等加密真实终点 URL"""
        if "baidu.com/link" not in url and "sogou.com/link" not in url:
            return url
        try:
            resp = await client.head(url, follow_redirects=False, timeout=3.0)
            if "Location" in resp.headers:
                return resp.headers["Location"]
            resp = await client.get(url, follow_redirects=False, timeout=3.0)
            return resp.headers.get("Location", url)
        except Exception:
            return url  # 降级自愈：解析失败退避返回加密链，保障大模型仍然可以通过超链接跳转

    async def _search_concurrent(self, query: str) -> list:
        """并发超跑：同时启动所有可用的 7 条赛道，拿满 10 条结果瞬间刹车截断"""
        tasks = []

        # Tavily - auto-sensing activation
        # Tavily - 自动感应激活
        if self._preflight_check("Tavily", settings.TAVILY_API_KEY):
            tasks.append(("Tavily", self._search_tavily(query)))

        # SearXNG - auto-sensing activation (default False, only active when local config enabled)
        # SearXNG - 自动感应激活 (默认 False 仅在本地配置开启时生效)
        if settings.ENABLE_SEARCH_SEARXNG and self._preflight_check("SearXNG", settings.SEARXNG_URL):
            tasks.append(("SearXNG", self._search_searxng(query)))

        # 5 key-free direct sources always active
        # 5路免Key直连源始终激活
        tasks.append(("DDG", self._search_ddg_lite(query)))
        tasks.append(("Bing", self._search_bing_direct(query)))
        tasks.append(("Yahoo", self._search_yahoo_direct(query)))
        tasks.append(("Baidu", self._search_baidu_direct(query)))
        tasks.append(("Sogou", self._search_sogou_direct(query)))

        # ClawHub - skill search engine (key-free)
        # ClawHub - 技能搜索引擎（免Key）
        tasks.append(("ClawHub", self._search_clawhub(query)))

        named_tasks = [(asyncio.create_task(coro), name) for name, coro in tasks]
        pending = {t: name for t, name in named_tasks}
        results_pool = []

        try:
            while pending and len(results_pool) < 10:
                done, _ = await asyncio.wait(pending.keys(), return_when=asyncio.FIRST_COMPLETED)
                for fut in done:
                    name = pending.pop(fut)
                    try:
                        results = fut.result()
                        if results:
                            results_pool.extend(results)
                            if name in ["Tavily", "SearXNG"]:
                                self._record_success(name)
                    except Exception as e:
                        if name in ["Tavily", "SearXNG"]:
                            self._record_failure(name)
                        logging.debug(f"🌩️ [Search] {name} 赛道网络异常: {e}")
        finally:
            # Overspeed cutoff! Force Cancel() to all slow channels, zero-delay braking!
            # 🏁 超速截断！瞬间对所有慢速通道强行发送 Cancel()，0延时刹车收网！
            for t in pending:
                t.cancel()

        return results_pool

    def _prune_search_cache(self):
        """清理过期缓存"""
        now = time.time()
        stale = [k for k, (_, t) in self.__class__._search_cache.items() if now - t > self.__class__._SEARCH_CACHE_TTL]
        for k in stale:
            del self.__class__._search_cache[k]

    async def _search_ddg_lite(self, query: str) -> list:
        # DDG Lite: no JS, lightweight, precise snippets
        # DDG Lite: 无 JS, 轻量, 摘要精准
        url = "https://html.duckduckgo.com/html/"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            async with httpx.AsyncClient(proxy=use_proxy, timeout=12.0, verify=False, headers=headers) as client:
                resp = await client.post(url, data={"q": query, "b": ""})
                if resp.status_code == 202 or "ratelimit" in resp.text.lower():
                    return []
                if 200 <= resp.status_code < 300:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = []
                    for r in soup.select(".result")[:5]:
                        t_tag = r.select_one(".result__title")
                        s_tag = r.select_one(".result__snippet")
                        if t_tag and s_tag:
                            items.append(
                                {
                                    "title": t_tag.get_text(strip=True),
                                    "url": t_tag.find("a")["href"] if t_tag.find("a") else "",
                                    "content": s_tag.get_text(strip=True),
                                    "score": 0.80,
                                }
                            )
                    return items
                return []
        except Exception:
            return []

    async def _search_bing_direct(self, query: str) -> list:
        """微软 Bing 0-Key 免密轻量级网页直连"""
        url = f"https://cn.bing.com/search?q={urllib.parse.quote(query)}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            async with httpx.AsyncClient(proxy=use_proxy, timeout=8.0, verify=False, headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = []
                    for r in soup.select(".b_algo")[:5]:
                        t_tag = r.select_one("h2 a")
                        s_tag = r.select_one(".b_caption p, .b_snippet")
                        if t_tag:
                            items.append(
                                {
                                    "title": t_tag.get_text(strip=True),
                                    "url": t_tag.get("href", ""),
                                    "content": s_tag.get_text(strip=True) if s_tag else "",
                                    "score": 0.85,
                                }
                            )
                    return items
                return []
        except Exception:
            return []

    async def _search_yahoo_direct(self, query: str) -> list:
        """雅虎 Yahoo 0-Key 免密轻量级网页直连"""
        url = f"https://search.yahoo.com/search?p={urllib.parse.quote(query)}"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            async with httpx.AsyncClient(proxy=use_proxy, timeout=8.0, verify=False, headers=headers) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = []
                    for r in soup.select(".algo")[:5]:
                        t_tag = r.select_one(".compTitle a")
                        s_tag = r.select_one(".compText")
                        if t_tag:
                            items.append(
                                {
                                    "title": t_tag.get_text(strip=True),
                                    "url": t_tag.get("href", ""),
                                    "content": s_tag.get_text(strip=True) if s_tag else "",
                                    "score": 0.80,
                                }
                            )
                    return items
                return []
        except Exception:
            return []

    async def _search_baidu_direct(self, query: str) -> list:
        """百度 Baidu 0-Key 免密直连 + 加密重定向解密自愈"""
        url = "https://www.baidu.com/s"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            async with httpx.AsyncClient(proxy=use_proxy, timeout=8.0, verify=False, headers=headers) as client:
                resp = await client.get(url, params={"wd": query})
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = []

                    # Use asyncio.gather for millisecond-level concurrent HEAD redirect resolution on Baidu encrypted URLs!
                    # 使用 asyncio.gather 对百度结果加密 URL 进行毫秒级并发 HEAD 追踪还原！
                    resolve_tasks = []
                    raw_items = []

                    for r in soup.select(".result.c-container")[:5]:
                        t_tag = r.select_one("h3 a")
                        s_tag = r.select_one(".c-abstract") or r.select_one(".content_right_col")
                        if t_tag:
                            raw_href = t_tag.get("href", "")
                            resolve_tasks.append(self._resolve_redirect(client, raw_href))
                            raw_items.append((t_tag.get_text(strip=True), s_tag.get_text(strip=True) if s_tag else ""))

                    resolved_urls = await asyncio.gather(*resolve_tasks)
                    for (title, snippet), real_url in zip(raw_items, resolved_urls):
                        items.append({"title": title, "url": real_url, "content": snippet, "score": 0.75})
                    return items
                return []
        except Exception:
            return []

    async def _search_sogou_direct(self, query: str) -> list:
        """搜狗 Sogou 0-Key 免密直连 + 加密重定向解密自愈"""
        url = "https://www.sogou.com/web"
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            async with httpx.AsyncClient(proxy=use_proxy, timeout=8.0, verify=False, headers=headers) as client:
                resp = await client.get(url, params={"query": query})
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "html.parser")
                    items = []

                    resolve_tasks = []
                    raw_items = []

                    # Sogou standard result containers: .vrwrap or .rb
                    # 搜狗标准结果容器为 .vrwrap 或 .rb
                    for r in soup.select(".vrwrap, .rb")[:5]:
                        t_tag = r.select_one("h3 a")
                        s_tag = r.select_one(".c-gap-top-small, p")
                        if t_tag:
                            raw_href = t_tag.get("href", "")
                            # Handle Sogou relative link completion
                            # 处理搜狗相对链接补全
                            if raw_href.startswith("/"):
                                raw_href = "https://www.sogou.com" + raw_href
                            resolve_tasks.append(self._resolve_redirect(client, raw_href))
                            raw_items.append((t_tag.get_text(strip=True), s_tag.get_text(strip=True) if s_tag else ""))

                    resolved_urls = await asyncio.gather(*resolve_tasks)
                    for (title, snippet), real_url in zip(raw_items, resolved_urls):
                        items.append({"title": title, "url": real_url, "content": snippet, "score": 0.70})
                    return items
                return []
        except Exception:
            return []

    async def _search_searxng(self, query: str) -> list:
        searxng_url = settings.SEARXNG_URL
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Referer": f"{searxng_url}/",
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            if use_proxy and ("127.0.0.1" in searxng_url or "localhost" in searxng_url):
                use_proxy = None

            async with httpx.AsyncClient(proxy=use_proxy, timeout=8.0, headers=headers, verify=False) as client:
                resp = await client.get(f"{searxng_url}/search", params={"q": query, "format": "json"})
                if resp.status_code == 200:
                    data = resp.json()
                    items = []
                    for r in data.get("results", [])[: settings.SEARCH_MAX_RESULTS]:
                        items.append(
                            {
                                "title": r.get("title", ""),
                                "url": r.get("url", ""),
                                "content": r.get("content", r.get("snippet", "")),
                                "score": r.get("score", 0.85),
                            }
                        )
                    return items
                return []
        except Exception:
            return []

    async def _search_tavily(self, query: str) -> list:
        api_key = settings.TAVILY_API_KEY
        if not api_key:
            return []
        url = "https://api.tavily.com/search"
        try:
            headers = {"Content-Type": "application/json"}
            data = {
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "max_results": settings.SEARCH_MAX_RESULTS,
            }
            use_proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            async with httpx.AsyncClient(proxy=use_proxy, timeout=15.0, verify=False) as client:
                resp = await client.post(url, json=data, headers=headers)
                if resp.status_code == 200:
                    res_data = resp.json()
                    items = []
                    for r in res_data.get("results", []):
                        items.append(
                            {
                                "title": r.get("title", ""),
                                "url": r.get("url", ""),
                                "content": r.get("content", ""),
                                "score": r.get("score", 0.95),
                            }
                        )
                    return items
                return []
        except Exception:
            return []

    async def _search_clawhub(self, query: str) -> list:
        """[ClawHub 技能搜索]：从 clawhub.ai 搜索相关 Agent 技能包"""
        try:
            import urllib.request
            import json as _json

            url = f"https://clawhub.ai/api/v1/search?q={urllib.parse.quote(query)}&limit=5"
            req = urllib.request.Request(url, headers={"User-Agent": "RoosterAgent/1.0"})
            loop = asyncio.get_running_loop()

            def _fetch():
                with urllib.request.urlopen(req, timeout=4) as resp:
                    return _json.loads(resp.read().decode("utf-8"))

            data = await loop.run_in_executor(None, _fetch)
            results = []
            for item in data.get("results", [])[:5]:
                slug = item.get("slug", "")
                if not slug:
                    continue
                results.append(
                    {
                        "title": f" {item.get('displayName', slug)} (ClawHub Skill)",
                        "url": f"https://clawhub.ai/skills/{slug}",
                        "content": item.get("summary", "")[:200],
                        "score": min(item.get("score", 0.5) / 5.0, 0.95),
                    }
                )
            return results
        except Exception:
            return []

    async def _search_fallback_dynamic(self, query: str) -> list:
        """[深潜模式 v2]：Playwright 暴力兜底"""
        logging.info(f"🌐 正在启动深潜模式 (Playwright) 搜索: {query}")
        manager = await BrowserManager.get_instance()
        results = []
        try:
            async with manager.page_scope() as page:
                search_url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
                await page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await page.wait_for_selector(".b_algo", timeout=8000)
                except Exception:
                    pass
                html = await page.content()
                soup = BeautifulSoup(html, "html.parser")
                items = soup.select(".b_algo")
                if not items:
                    items = [li for li in soup.select("li") if li.select_one("h2 a")]

                for item in items[:5]:
                    t_tag = item.select_one("h2 a")
                    c_tag = item.select_one(".b_caption p, .b_snippet, .st")
                    if t_tag:
                        title = t_tag.get_text(strip=True)
                        href = t_tag.get("href", "")
                        content = c_tag.get_text(strip=True) if c_tag else ""
                        if title and href.startswith("http"):
                            results.append({"title": title, "url": href, "content": content, "score": 0.85})

                if not results:
                    await page.goto(
                        f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}",
                        wait_until="domcontentloaded",
                        timeout=25000,
                    )
                    html = await page.content()
                    soup = BeautifulSoup(html, "html.parser")
                    items = soup.select(".result, .c-container")
                    for item in items[:3]:
                        t_tag = item.select_one("h3 a")
                        c_tag = item.select_one(".c-abstract")
                        if t_tag:
                            results.append(
                                {
                                    "title": t_tag.get_text(strip=True),
                                    "url": t_tag.get("href", ""),
                                    "content": c_tag.get_text(strip=True) if c_tag else "百度快照内容",
                                    "score": 0.75,
                                }
                            )
            return results
        except Exception:
            return []
