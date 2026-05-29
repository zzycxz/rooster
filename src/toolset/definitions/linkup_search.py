"""
Linkup.so AI 搜索引擎 — Agentic Web Search，支持 sourcedAnswer 直接回答。
AI 搜索双引擎之一：Exa 优先 → Linkup 兜底 → GLM Plan Search → 普通搜索。
"""

import os
import logging
import httpx
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)


class LinkupSearchArgs(BaseModel):
    query: str = Field(description="The search query string.")
    depth: Optional[str] = Field(
        default="standard",
        description="Search depth: 'fast' (<1s), 'standard' (1-3s, agentic), 'deep' (5-30s, multi-iteration).",
    )
    output_type: Optional[str] = Field(
        default="searchResults",
        description="Output type: 'searchResults' (links+snippets), 'sourcedAnswer' (answer with citations).",
    )
    include_domains: Optional[str] = Field(
        default=None,
        description="Comma-separated whitelist domains, e.g., 'github.com,stackoverflow.com'.",
    )
    exclude_domains: Optional[str] = Field(
        default=None,
        description="Comma-separated blacklist domains to exclude.",
    )
    from_date: Optional[str] = Field(
        default=None,
        description="Only results from this date onwards (ISO 8601, e.g., '2024-01-01').",
    )
    to_date: Optional[str] = Field(
        default=None,
        description="Only results up to this date (ISO 8601).",
    )
    max_results: Optional[int] = Field(
        default=5,
        description="Maximum number of results (1-10).",
    )


class LinkupSearchTool(BaseTool):
    """Linkup.so agentic web search — AI search tier (Linkup → GLM → web_search)."""

    name: str = "linkup_search"
    kit: str = "Search"
    fc_hidden: bool = True  # 由 web_search 内部调用，LLM 不可见
    description: str = (
        "Agentic web search powered by Linkup.so. Supports three depth modes: fast (<1s), "
        "standard (agentic, 1-3s), and deep (multi-iteration, 5-30s). Can return either "
        "search results with links and snippets, or a sourced natural-language answer with "
        "inline citations. No content censorship — returns all relevant results. "
        "Fallback chain: Linkup → GLM Plan Search → standard web search."
    )
    domain: str = "recon"
    args_schema: Type[BaseModel] = LinkupSearchArgs

    async def run(self, **kwargs) -> str:
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

    async def _fallback(self, query: str) -> str:
        """Linkup → GLM Plan Search 降级链。web_search 在外层兜底，不在此处调用。"""
        try:
            from toolset.definitions.glm_plan_search import GLMPlanSearchTool

            logger.info("[LinkupSearch] Fallback to GLM Plan Search.")
            glm = GLMPlanSearchTool()
            return await glm.run(query=query)
        except Exception as e:
            logger.warning(f"[LinkupSearch] GLM fallback failed: {e}")
            return f"Error: AI search chain exhausted: {e}"
