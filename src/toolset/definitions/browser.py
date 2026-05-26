import asyncio
import logging
import re
import time
import httpx
import urllib.request
import urllib.parse
from bs4 import BeautifulSoup
from typing import List, Dict, Optional, Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from utils.browser.manager import BrowserManager, HTMLCleaner, ID_INJECTION_JS, html_to_markdown
from utils.config import settings

# --- Constants and utility functions ---
# --- 常量与工具函数 ---
# --- Parameter models ---
# --- 参数模型 ---


class BrowserBaseArgs(BaseModel):
    pass


class BrowserNavArgs(BaseModel):
    url: str = Field(description="URL")


class BrowserActionArgs(BaseModel):
    index: int = Field(description="ID", default=0)


class BrowserSearchArgs(BaseModel):
    query: str = Field(description="The search query in your primary language (e.g., Chinese).")
    en_keywords: str = Field(
        description="Optional: Relevant English keywords for the query to help filter international results better.",
        default="",
    )


class BrowserScrollArgs(BaseModel):
    direction: str = Field(description="Dir", default="down")
    amount: int = Field(description="Amount", default=800)


class BrowserTypeArgs(BaseModel):
    index: int = Field(description="The data-rooster-id of the input element to type into.")
    text: str = Field(description="The text to type into the input field.")
    clear: bool = Field(description="Whether to clear the field before typing.", default=True)


class WebFetchArgs(BaseModel):
    url: str = Field(description="The URL to fetch content from.")
    prompt: str = Field(
        description="A question or instruction about the page content. The fetched content will be summarized against this prompt."
    )
    mode: str = Field(description="Output mode: 'summary' (AI analyzed) or 'raw' (pruned markdown)", default="summary")


class BatchWebFetchArgs(BaseModel):
    urls: List[str] = Field(description="List of URLs to fetch concurrently (max 5).")
    prompt: str = Field(
        description="A question or instruction applied to ALL pages. Each page is summarized against this prompt."
    )
    mode: str = Field(description="Output mode: 'summary' (AI analyzed) or 'raw' (pruned markdown)", default="summary")


class BrowserExtractLinksArgs(BaseModel):
    keyword: str = Field(
        description="Filter links by this keyword in their text or surrounding context (optional).", default=""
    )


class BrowserPaginationArgs(BaseModel):
    pass


# --- Tool definitions ---
# --- 工具定义 ---


class BrowserBaseTool(BaseTool):
    async def _get_processed_content(self, page) -> str:
        """读取、清理并截断页面内容，防止爆上下文"""
        if page is None:
            return "Error: Browser page is not initialized."
        await page.evaluate(ID_INJECTION_JS, ["button", "a", "input", "select"])
        html = await page.content()
        cleaned = HTMLCleaner.clean(html)
        # Dynamic truncation to align with context window quota
        # 动态截断以对齐上下文窗口配额
        from utils.config import settings

        limit = settings.OBSERVATION_CHAR_LIMIT
        return cleaned[:limit] + (f" ... [Content Truncated to {limit}]" if len(cleaned) > limit else "")


class BrowserNavTool(BrowserBaseTool):
    name: str = "browser_nav"
    kit: str = "Browser"
    description: str = "导航到 URL。"
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserNavArgs

    async def run(self, **kwargs) -> str:
        url = kwargs.get("url")
        if not url:
            return "Error: Missing 'url'."
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return await self._get_processed_content(page)
        except Exception as e:
            return f"Error: {str(e)}"


class BrowserReadTool(BrowserBaseTool):
    name: str = "browser_read"
    kit: str = "Browser"
    description: str = "读取当前页面内容。"
    domain: str = "recon"
    fc_hidden: bool = True  # [Round 9] browser_nav/browser_click/browser_scroll 已返回页面内容，此工具对 LLM 冗余
    args_schema: Type[BaseModel] = BrowserBaseArgs

    async def run(self, **kwargs) -> str:
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()
        return await self._get_processed_content(page)


class BrowserClickTool(BrowserBaseTool):
    name: str = "browser_click"
    kit: str = "Browser"
    fc_hidden: bool = True  # [Round 10] Use browser_act(action="click", index=...) instead
    description: str = "点击。输入 ID。"
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserActionArgs

    async def run(self, **kwargs) -> str:
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()
        index = kwargs.get("index", 0)
        element = page.locator(f'[data-rooster-id="{index}"]')
        if await element.count() == 0:
            await page.evaluate(ID_INJECTION_JS, ["button", "a"])
        await element.scroll_into_view_if_needed()
        await element.click(timeout=10000)
        await asyncio.sleep(1.5)
        return await self._get_processed_content(page)


class BrowserTypeTool(BrowserBaseTool):
    name: str = "browser_type"
    kit: str = "Browser"
    fc_hidden: bool = True  # [Round 10] Use browser_act(action="type", index=..., text=...) instead
    description: str = (
        "在浏览器输入框中输入文字。通过 data-rooster-id 定位输入框，"
        "支持先清空再输入。适用于表单填写、搜索框输入等场景。"
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserTypeArgs

    async def run(self, **kwargs) -> str:
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()
        index = kwargs.get("index", 0)
        text = kwargs.get("text", "")
        clear = kwargs.get("clear", True)

        if not text:
            return "Error: Missing 'text'."

        element = page.locator(f'[data-rooster-id="{index}"]')
        if await element.count() == 0:
            # Inject IDs and retry
            # 注入 ID 后重试
            await page.evaluate(ID_INJECTION_JS, ["input", "textarea", "select"])
            element = page.locator(f'[data-rooster-id="{index}"]')
            if await element.count() == 0:
                return f"Error: Element with data-rooster-id={index} not found."

        try:
            await element.scroll_into_view_if_needed()
            if clear:
                await element.fill("")
            await element.fill(text)
            await asyncio.sleep(0.5)
            return f"Successfully typed '{text}' into element {index}.\n" + await self._get_processed_content(page)
        except Exception:
            # When fill fails, fall back to per-keystroke input
            # fill 失败时降级为逐字符输入
            try:
                if clear:
                    await element.press("Control+a")
                    await element.press("Backspace")
                await element.type(text, delay=50)
                await asyncio.sleep(0.5)
                return f"Typed '{text}' into element {index} (keystroke mode).\n" + await self._get_processed_content(
                    page
                )
            except Exception as e2:
                return f"Error typing into element {index}: {str(e2)}"


class WebSearchTool(BaseTool):
    name: str = "web_search"
    kit: str = "Browser"
    fc_hidden: bool = False  # executor prompt 首选 web_search，必须在 FC 中可用
    description: str = (
        "Search the web. Returns results with titles, URLs, and brief snippets. "
        "Use this tool when you need to find information, look up facts, or discover URLs. "
        "This is the FIRST choice for any research task. "
        "To read the full content of a search result, use web_fetch with the URL."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserSearchArgs

    # Static session-level sliding window circuit breaker state machine
    # 静态会话级滑动窗口熔断器状态机
    _circuit_breaker: Dict[str, dict] = {
        "Tavily": {"failures": 0, "status": "PENDING"},
        "SearXNG": {"failures": 0, "status": "PENDING"},
    }
    _SEARXNG_COOLDOWN: float = 120.0

    # Search result cache (supports async LLM rerank background warm-write)
    # 搜索结果缓存 (支持异步大模型 Rerank 后台温热写入)
    _search_cache: Dict[str, tuple] = {}
    _SEARCH_CACHE_TTL: int = 600  # 10 分钟

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

    async def run(self, **kwargs) -> str:
        query = kwargs.get("query")
        en_keywords = kwargs.get("en_keywords", "")
        if not query:
            return "Error: No query provided."

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
            asyncio.create_task(self._async_llm_rerank_and_update_cache(query, all_raw, cache_key, now, en_keywords))

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

            response = await llm.chat_non_stream(messages)
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
            loop = asyncio.get_event_loop()

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


class BrowserScrollTool(BrowserBaseTool):
    name: str = "browser_scroll"
    kit: str = "Browser"
    fc_hidden: bool = True  # [Round 10] Use browser_act(action="scroll", direction=..., amount=...) instead
    description: str = "滚动。"
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserScrollArgs

    async def run(self, **kwargs) -> str:
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()
        px = kwargs.get("amount", 800) if kwargs.get("direction", "down") == "down" else -kwargs.get("amount", 800)
        await page.mouse.wheel(0, px)
        await asyncio.sleep(0.5)
        return await self._get_processed_content(page)


class WebFetchTool(BaseTool):
    name: str = "web_fetch"
    kit: str = "Browser"
    description: str = (
        "Fetch a web page and extract information using a prompt. "
        "Downloads the page, converts HTML to clean Markdown, then uses a fast AI model "
        "to summarize or answer questions about the content. Results cached 15 minutes. "
        "Use this to READ web page content (e.g. articles, documentation, GitHub pages). "
        "Do NOT use this for file downloads — use download_file or multimedia_download instead."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = WebFetchArgs

    _cache: Dict[str, tuple] = {}
    _CACHE_TTL: int = 900  # 15 minutes
    _CACHE_MAX_SIZE: int = 200  # 缓存上限

    async def run(self, **kwargs) -> str:
        url = kwargs.get("url")
        prompt = kwargs.get("prompt")
        mode = kwargs.get("mode", "summary")

        if not url or not prompt:
            return "Error: Both 'url' and 'prompt' are required."

        # Check cache
        cache_key = f"{url}::{prompt}::{mode}"
        now = time.time()
        if cache_key in self.__class__._cache:
            cached_result, cached_time = self.__class__._cache[cache_key]
            if now - cached_time < self.__class__._CACHE_TTL:
                logging.info(f"📦 web_fetch cache hit: {url}")
                return cached_result

        # Step 1: Smart Fetch
        try:
            manager = await BrowserManager.get_instance()
            raw_html, fetch_method = await manager.smart_fetch(url)
            logging.info(f"🌐 [WebFetch] Fetched {url} via {fetch_method}")
        except Exception as e:
            return f"Error fetching URL: {str(e)}"

        # Step 2: Smart content extraction pipeline (PruningContentFilter + Table + Citation)
        # Step 2: 智能内容提取管线 (PruningContentFilter + Table + Citation)
        from utils.browser.manager import semantic_prune_markdown
        from utils.browser.pruner import MarkdownPruner

        # First extract scent links from raw HTML (all links)
        # 先从原始 HTML 中提取 scent links（全量链接）
        full_soup = BeautifulSoup(raw_html, "html.parser")
        all_links_md = []
        for a in full_soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            url = a["href"]
            if text and url.startswith("http"):
                all_links_md.append(f"[{text}]({url})")
        scent_links = MarkdownPruner.extract_scent_links("\n".join(all_links_md))

        # New pipeline: PruningContentFilter → Table → markdownify → Citation
        # 新管线：PruningContentFilter → Table → markdownify → Citation
        markdown_content = html_to_markdown(raw_html)
        pruned_content = semantic_prune_markdown(markdown_content)

        # Step 3: Actionable Output
        if mode == "raw":
            result = f"### [RAW CONTENT] {url}\n\n{pruned_content}"
            if scent_links:
                result += "\n\n### 🔮 Recommended Next Hits\n" + "\n".join(scent_links)
            return result

        # Mode summary
        try:
            from agents.llm_client import LLMClient

            llm = LLMClient(provider=settings.FAST_MODEL_PROVIDER, model=settings.FAST_MODEL_NAME, lightweight=True)

            messages = [
                {
                    "role": "system",
                    "content": "You are a precise web information extractor. Answer based ONLY on the content. Be concise.",
                },
                {"role": "user", "content": f"Content from {url}:\n\n{pruned_content}\n\nQuestion: {prompt}"},
            ]

            response = await llm.chat_non_stream(messages)
            result = response.content

            if scent_links:
                result += "\n\n### 🔮 Recommended Next Hits\n" + "\n".join(scent_links)

            # Cache
            self.__class__._cache[cache_key] = (result, now)
            self._prune_cache()
            return result

        except Exception as e:
            logging.error(f"web_fetch summarize failed: {e}")
            return f"Note: Summarization failed. Pruned content:\n{pruned_content[:3000]}"

    def _prune_cache(self):
        """Remove expired entries and enforce size cap."""
        now = time.time()
        # Remove expired entries
        # 删除过期条目
        stale_keys = [k for k, (_, t) in self.__class__._cache.items() if now - t > self.__class__._CACHE_TTL]
        for k in stale_keys:
            del self.__class__._cache[k]
        # When over limit, remove oldest entries
        # 超出上限时删除最旧的条目
        if len(self.__class__._cache) > self.__class__._CACHE_MAX_SIZE:
            sorted_keys = sorted(self.__class__._cache.keys(), key=lambda k: self.__class__._cache[k][1])
            for k in sorted_keys[: len(self.__class__._cache) - self.__class__._CACHE_MAX_SIZE]:
                del self.__class__._cache[k]


class BatchWebFetchTool(BaseTool):
    """并发获取多个 URL — 最多 5 个，共享同一个 prompt"""

    name: str = "batch_web_fetch"
    kit: str = "Browser"
    description: str = (
        "Fetch multiple web pages concurrently (max 5 URLs) and summarize each against the same prompt. "
        "Use this instead of calling web_fetch multiple times when you need to compare or extract info from several pages. "
        "Much faster than sequential web_fetch calls."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = BatchWebFetchArgs

    _MAX_CONCURRENCY: int = 3

    async def run(self, **kwargs) -> str:
        urls = kwargs.get("urls", [])
        prompt = kwargs.get("prompt", "")
        mode = kwargs.get("mode", "summary")

        if not urls:
            return "Error: 'urls' list is required."
        if not prompt:
            return "Error: 'prompt' is required."

        urls = urls[:5]  # 硬上限 5 个

        # Reuse WebFetchTool instance (shared cache)
        # 复用 WebFetchTool 实例（共享缓存）
        fetch_tool = WebFetchTool()
        semaphore = asyncio.Semaphore(self._MAX_CONCURRENCY)

        async def fetch_one(url: str, idx: int) -> str:
            async with semaphore:
                try:
                    result = await fetch_tool.run(url=url, prompt=prompt, mode=mode)
                    return f"### [{idx + 1}/{len(urls)}] {url}\n\n{result}"
                except Exception as e:
                    return f"### [{idx + 1}/{len(urls)}] {url}\n\nError: {e}"

        tasks = [fetch_one(url, i) for i, url in enumerate(urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        output = [f"## Batch Fetch Results ({len(urls)} pages)\n"]
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                output.append(f"### [{i + 1}/{len(urls)}] {urls[i]}\n\nError: {r}")
            else:
                output.append(r)

        return "\n\n---\n\n".join(output)


class BrowserExtractLinksTool(BrowserBaseTool):
    name: str = "browser_explore_links"
    kit: str = "Browser"
    description: str = "Extract and filter promising links from the current page. Helps the agent decide where to 'click' or 'peek' next."
    domain: str = "recon"
    fc_hidden: bool = True  # [Round 9] browser_nav/click/scroll 返回的处理后内容已包含链接；此工具仅在需要提取全部原始 href 时内部使用
    args_schema: Type[BaseModel] = BrowserExtractLinksArgs

    async def run(self, **kwargs) -> str:
        keyword = kwargs.get("keyword", "").lower()
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()

        # We need a custom JS to get links with their context
        js_code = """
        () => {
            const results = [];
            const links = document.querySelectorAll('a');
            links.forEach(a => {
                const text = a.innerText.trim();
                const href = a.href;
                if (text && href && href.startsWith('http')) {
                    // Extract surrounding text for context (scent of information)
                    const parent = a.parentElement;
                    const context = parent ? parent.innerText.trim().substring(0, 100) : "";
                    results.append({ text, href, context });
                }
            });
            return results;
        }
        """
        # Note: Need to fix the JS results.append (should be push)
        js_code = js_code.replace("results.append", "results.push")

        try:
            links = await page.evaluate(js_code)

            # Filter by keyword if provided
            if keyword:
                links = [l for l in links if keyword in l["text"].lower() or keyword in l["context"].lower()]

            # Limit to 20 links for brevity
            links = links[:20]

            if not links:
                return "No matching links found on the current page."

            output = ["### 🧬 Found promising links:"]
            for i, l in enumerate(links):
                output.append(f"[{i}] **{l['text']}**\n  URL: {l['href']}\n  Context: {l['context']}...")

            return "\n\n".join(output)
        except Exception as e:
            return f"Error extracting links: {str(e)}"


class BrowserPaginationTool(BrowserBaseTool):
    name: str = "browser_next_page"
    kit: str = "Browser"
    fc_hidden: bool = (
        True  # [Round 10] Use browser_act(action="click") with the Next button element ID after desktop_grounding_scan
    )
    description: str = "Automatically find and click the 'Next' page button on search engines or lists. Returns the next set of results."
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserPaginationArgs

    async def run(self, **kwargs) -> str:
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()

        # Heuristic JS to find the "Next" button
        js_code = """
        () => {
            const nextPatterns = [
                'next', 'Next', '下一页', '>', '»', '更多', 'More', 'more',
                '[aria-label*="Next"]', 'a.next', 'a.pn-next', 'a.n', 'button.next'
            ];

            for (let pattern of nextPatterns) {
                // Try as selector first
                try {
                    const el = document.querySelector(pattern);
                    if (el && el.offsetParent !== null) {
                        el.click();
                        return "SUCCESS: Clicked matching selector: " + pattern;
                    }
                } catch(e) {}

                // Try as text content
                const elements = document.querySelectorAll('a, button, span');
                for (let el of elements) {
                    if (el.innerText.includes(pattern) && el.offsetParent !== null) {
                        el.click();
                        return "SUCCESS: Clicked element with text: " + pattern;
                    }
                }
            }
            return "FAILED: Could not find a recognizable 'Next' button or link.";
        }
        """

        try:
            result = await page.evaluate(js_code)
            if "SUCCESS" in result:
                await asyncio.sleep(2.0)  # Wait for page load
                return f"{result}\n\n[New Page Content]:\n" + await self._get_processed_content(page)
            return result
        except Exception as e:
            return f"Error during pagination: {str(e)}"


# ---------------------------------------------------------------------------
# [Round 10] browser_act — unified browser interaction macro
# Replaces: browser_click, browser_scroll, browser_type
# ---------------------------------------------------------------------------


class BrowserActArgs(BaseModel):
    action: str = Field(
        description="Action type: 'click' (click element by ID), 'scroll' (scroll page), 'type' (type text into input)"
    )
    index: Optional[int] = Field(
        default=None, description="[click / type] data-rooster-id of the element to interact with"
    )
    text: Optional[str] = Field(default=None, description="[type] Text to type into the input element")
    clear: Optional[bool] = Field(
        default=True, description="[type] Whether to clear the field before typing (default: True)"
    )
    direction: Optional[str] = Field(default="down", description="[scroll] Scroll direction: 'up' or 'down'")
    amount: Optional[int] = Field(default=800, description="[scroll] Scroll distance in pixels (default: 800)")


class BrowserActTool(BrowserBaseTool):
    """[Round 10] Unified browser interaction macro: click, scroll, or type in one tool."""

    name: str = "browser_act"
    kit: str = "Browser"
    description: str = (
        "Unified browser interaction tool. Use action='click' to click an element by its data-rooster-id, "
        "action='scroll' to scroll the page up/down, or action='type' to type text into an input field. "
        "All actions return the updated page content after the interaction."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = BrowserActArgs

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "").lower()
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()

        if action == "click":
            index = kwargs.get("index", 0)
            element = page.locator(f'[data-rooster-id="{index}"]')
            if await element.count() == 0:
                await page.evaluate(ID_INJECTION_JS, ["button", "a"])
            await element.scroll_into_view_if_needed()
            await element.click(timeout=10000)
            await asyncio.sleep(1.5)
            return await self._get_processed_content(page)

        elif action == "scroll":
            direction = kwargs.get("direction", "down")
            amount = kwargs.get("amount", 800)
            px = amount if direction == "down" else -amount
            await page.mouse.wheel(0, px)
            await asyncio.sleep(0.5)
            return await self._get_processed_content(page)

        elif action == "type":
            index = kwargs.get("index", 0)
            text = kwargs.get("text", "")
            clear = kwargs.get("clear", True)
            if not text:
                return "Error: 'text' is required for action='type'."
            element = page.locator(f'[data-rooster-id="{index}"]')
            if await element.count() == 0:
                await page.evaluate(ID_INJECTION_JS, ["input", "textarea", "select"])
                element = page.locator(f'[data-rooster-id="{index}"]')
                if await element.count() == 0:
                    return f"Error: Element with data-rooster-id={index} not found."
            try:
                await element.scroll_into_view_if_needed()
                if clear:
                    await element.fill("")
                await element.fill(text)
                await asyncio.sleep(0.5)
                return f"Typed '{text}' into element {index}.\n" + await self._get_processed_content(page)
            except Exception:
                try:
                    if clear:
                        await element.press("Control+a")
                        await element.press("Backspace")
                    await element.type(text, delay=50)
                    await asyncio.sleep(0.5)
                    return (
                        f"Typed '{text}' into element {index} (keystroke mode).\n"
                        + await self._get_processed_content(page)
                    )
                except Exception as e2:
                    return f"Error typing into element {index}: {str(e2)}"

        else:
            return f"Error: Unknown action '{action}'. Valid actions: click, scroll, type."
