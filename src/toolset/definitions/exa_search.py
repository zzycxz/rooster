"""
Exa.ai AI 搜索引擎 — 神经网络语义搜索，每月 1000 次免费额度。
AI 搜索第一梯队：Exa 优先 → GLM Plan Search 兜底 → 普通搜索保底。
与 WebSearchTool 的 7 路普通搜索池隔离，保证质量独立可控。
"""

import os
import json
import logging
import time
import httpx
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
_STATUS_FILE = os.path.join(_PROJECT_ROOT, ".rooster", "search_status.json")

_MONTHLY_QUOTA = 1000


def _read_status() -> dict:
    try:
        if os.path.exists(_STATUS_FILE):
            with open(_STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _write_status(data: dict):
    try:
        os.makedirs(os.path.dirname(_STATUS_FILE), exist_ok=True)
        with open(_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to write search status: {e}")


def _get_exa_usage() -> int:
    data = _read_status()
    exa = data.get("exa", {})
    saved_month = exa.get("month", "")
    now_month = time.strftime("%Y-%m")
    if saved_month != now_month:
        return 0
    return int(exa.get("usage", 0))


def _increment_exa_usage():
    data = _read_status()
    now_month = time.strftime("%Y-%m")
    if "exa" not in data:
        data["exa"] = {}
    exa = data["exa"]
    if exa.get("month") != now_month:
        exa["month"] = now_month
        exa["usage"] = 1
    else:
        exa["usage"] = int(exa.get("usage", 0)) + 1
    _write_status(data)
    return exa["usage"]


def get_exa_active() -> bool:
    key = os.getenv("EXA_KEY", "")
    if not key:
        return False
    return _get_exa_usage() < _MONTHLY_QUOTA


class ExaSearchArgs(BaseModel):
    query: str = Field(description="The search query string.")
    num_results: Optional[int] = Field(
        default=5,
        description="Number of results to return (1-10).",
    )
    use_autoprompt: Optional[bool] = Field(
        default=True,
        description="Let Exa automatically optimize the query for best results.",
    )
    include_domains: Optional[str] = Field(
        default=None,
        description="Comma-separated whitelist domains, e.g., 'github.com,stackoverflow.com'.",
    )
    exclude_domains: Optional[str] = Field(
        default=None,
        description="Comma-separated blacklist domains to exclude.",
    )
    category: Optional[str] = Field(
        default=None,
        description="Content category: company, research paper, news, github, tweet, movie, song, personal site, or pdf.",
    )
    start_published_date: Optional[str] = Field(
        default=None,
        description="Only return results published after this ISO date, e.g., '2024-01-01T00:00:00.000Z'.",
    )
    type: Optional[str] = Field(
        default="auto",
        description="Search type: auto, neural, or keyword.",
    )


class ExaSearchTool(BaseTool):
    """Exa.ai neural search — AI search tier 1 (Exa → GLM → web_search)."""

    name: str = "exa_search"
    kit: str = "Search"
    fc_hidden: bool = True  # 由 web_search 内部调用，LLM 不可见
    description: str = (
        "Primary search tool — works with or without API keys. "
        "Uses a 4-tier fallback chain: Exa.ai neural search (if EXA_KEY set) → "
        "Linkup agentic search (if LINKUP_KEY set) → GLM cloud search (if ZHIPU_KEY set) → "
        "free multi-engine web search (always available). "
        "Automatically picks the best available backend. "
        "For deep multi-iteration research or sourced-answer mode, use linkup_search directly."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = ExaSearchArgs

    async def run(self, **kwargs) -> str:
        query = kwargs.get("query")
        if not query:
            return "Error: No search query provided."

        api_key = os.getenv("EXA_KEY", "")
        if not api_key:
            logger.info("[ExaSearch] EXA_KEY not set, falling back.")
            return await self._fallback(query)

        if not get_exa_active():
            logger.warning("[ExaSearch] Monthly quota exhausted, falling back to GLM/web search.")
            return await self._fallback(query)

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
                    return await self._fallback(query)
                if resp.status_code == 429:
                    logger.warning("[ExaSearch] Rate limited (429), falling back.")
                    return await self._fallback(query)
                if resp.status_code != 200:
                    err_text = resp.text[:300]
                    logger.error(f"[ExaSearch] HTTP {resp.status_code}: {err_text}")
                    return await self._fallback(query)

                data = resp.json()
                results = data.get("results", [])
                if not results:
                    logger.info("[ExaSearch] No results from Exa, trying fallback.")
                    return await self._fallback(query)

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
            return await self._fallback(query)
        except Exception as e:
            logger.error(f"[ExaSearch] Error: {repr(e)}")
            return await self._fallback(query)

    async def _fallback(self, query: str) -> str:
        """Exa → Linkup → GLM Plan Search 3-tier fallback chain.
        Exa → Linkup → GLM Plan Search 三级降级链。web_search 在外层兜底，不在此处调用。"""
        try:
            from toolset.definitions.linkup_search import LinkupSearchTool

            logger.info("[ExaSearch] Fallback to Linkup.")
            linkup = LinkupSearchTool()
            return await linkup.run(query=query)
        except Exception as e:
            logger.warning(f"[ExaSearch] Linkup fallback failed: {e}")
        try:
            from toolset.definitions.glm_plan_search import GLMPlanSearchTool

            logger.info("[ExaSearch] Fallback to GLM Plan Search.")
            glm = GLMPlanSearchTool()
            return await glm.run(query=query)
        except Exception as e:
            logger.warning(f"[ExaSearch] GLM fallback failed: {e}")
            return f"Error: AI search chain exhausted: {e}"
