import logging
import asyncio
import time
from typing import Type, List, Dict
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
from toolset.base import BaseTool
from utils.browser.manager import BrowserManager, html_to_markdown, semantic_prune_markdown
from utils.browser.pruner import MarkdownPruner
from utils.config import settings

logger = logging.getLogger(__name__)


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

            response = await asyncio.wait_for(
                llm.chat_non_stream(messages),
                timeout=getattr(settings, "LLM_CALL_TIMEOUT", 120.0),
            )
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
