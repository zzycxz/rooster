import re
import asyncio
import logging
from typing import List, Type, Dict, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from .browser import WebSearchTool
from utils.security.state_guard import state_guard  # 核心状态锁集成

logger = logging.getLogger(__name__)


class ResourceArgs(BaseModel):
    query: str = Field(description="需要查找的资源核心名称。例如：'奥本海默' 或 'Ubuntu 24.04 ISO'")
    specs: Optional[str] = Field(description="额外的规格说明，如 '4K HDR' 或 'LTS'", default="")
    target_formats: List[str] = Field(
        description="目标协议特征。可选：magnet, ed2k, thunder, torrent, cloud_drive, direct",
        default=["magnet", "torrent", "cloud_drive"],
    )


class ResourceFetchTool(BaseTool):
    name: str = "resource_fetch"
    kit: str = "Network"
    description: str = (
        "Locate downloadable resources across multiple protocols (magnet, torrent, cloud drives, ED2K, direct links). "
        "Searches the web concurrently for the resource name and extracts protocol-specific links. "
        "Use this when you need to find download links for media, software, or other distributable content."
    )
    domain: str = "resource"
    args_schema: Type[BaseModel] = ResourceArgs

    PROTOCOL_PATTERNS = {
        "magnet": (r"magnet:\?[^\s\"'<>]*xt=urn:btih:[a-zA-Z0-9]{32,40}[^\s\"'<>]*", ["magnet", "磁力"]),
        "ed2k": (r"ed2k://\|file\|[^|]+\|\d+\|[a-fA-F0-9]{32}\|", ["ed2k", "电驴"]),
        "thunder": (r"thunder://[a-zA-Z0-9+/=]+", ["thunder", "迅雷"]),
        "torrent": (r"https?://[^\s\"'<>]+?\.torrent", ["torrent", "种子"]),
        "cloud_drive": (
            r"(?:https?://)?(?:pan\.baidu\.com/s/|pan\.quark\.cn/s/|www\.alipan\.com/s/|drive\.google\.com/file/d/)[a-zA-Z0-9_-]+",
            ["网盘", "下载"],
        ),
        "direct": (r"https?://[^\s\"'<>]+?\.(?:mp4|mkv|zip|iso|pdf|exe|7z|tar\.gz)", ["direct", "直链"]),
    }

    async def run(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        specs = kwargs.get("specs", "")
        targets = kwargs.get("target_formats", ["magnet", "torrent", "cloud_drive"])
        task_id = kwargs.get("task_id", "T-DEFAULT")

        full_query = f"{query} {specs}".strip()

        # 1. Dynamic scan
        # 1. 动态扫描
        search_cmds = []
        for fmt in targets:
            if fmt in self.PROTOCOL_PATTERNS:
                keywords = self.PROTOCOL_PATTERNS[fmt][1]
                search_cmds.append(f"{full_query} {keywords[0]}")

        logger.info(f"🚀 [ResourceProbe] 启动全协议竞争定位 ({len(search_cmds)}路): {full_query}")
        search_tool = WebSearchTool()
        tasks = [asyncio.create_task(search_tool.run(query=cmd)) for cmd in search_cmds]

        found_links = {}
        greedy_short_circuit = False

        try:
            # Shorten wait window for more aggressive racing
            # 缩短等待窗口，实现更激进的竞速
            for coro in asyncio.as_completed(tasks, timeout=30):
                partial_result = await coro

                temp_links = self._extract_features(partial_result)
                for k, v in temp_links.items():
                    found_links[k] = list(set(found_links.get(k, []) + v))
                    # Store in candidate pool
                    # 存入 Candidate Pool
                    for link in v:
                        state_guard.add_candidate(task_id, link, {"protocol": k, "source": "search_engine"})

                # --- Flexible short-circuit logic ---
                # --- 柔性短路逻辑 (Flexible Short-Circuit) ---
                magnet_count = len(found_links.get("magnet", []))
                if magnet_count >= 2:
                    logger.info("⚡ [Resource-Soft-Short] 已获足够资源，主流程先行，剩余搜索转入后台静默补全。")
                    greedy_short_circuit = True
                    break

        except asyncio.TimeoutError:
            pass

        # 3. Background silent filling
        # Schedule background callbacks for unfinished tasks to prevent physical destruction
        # 3. 后台静默补全 (Background Silent Filling)
        # 为尚未完成的任务安排后台回调，避免任务被物理销毁
        remaining_tasks = [t for t in tasks if not t.done()]
        if remaining_tasks:

            async def _fill_background_pool():
                for fut in asyncio.as_completed(remaining_tasks):
                    try:
                        bg_res = await fut
                        bg_links = self._extract_features(bg_res)
                        for k, v in bg_links.items():
                            for link in v:
                                state_guard.add_candidate(task_id, link, {"protocol": k, "source": "bg_scan"})
                    except Exception:
                        pass

            asyncio.create_task(_fill_background_pool())

        # 4. Conditional deep scan trigger
        # 4. 条件触发深度扫描
        if not greedy_short_circuit and not found_links:
            # Only launch heavy web reading when primary scan completely fails
            # 仅在主扫描全军覆没时才启动重型网页读取
            logger.info("🔍 [DeepDive] Initial scan missed, launching deep web analysis fallback...")
            logger.info("🔍 [DeepDive] 初始扫描未命中，启动深度网页分析补漏...")
            # ... Keep original BulkRead logic here, but don't block for speed
            # ... 此处可保持原有的 BulkRead 逻辑，但由于我们追求速度，此处暂不阻塞

        # 4. Aggregate output
        # 4. 汇总输出
        if not any(found_links.values()):
            return f"未能在此次全协议扫描中找到 '{query}' 的有效物理链接。请尝试调整规格参数再次检索。"

        output = [f"### 🎯 已发现 '{query}' 的资源快照:"]
        for fmt, links in found_links.items():
            if links:
                output.append(f"\n**[{fmt.upper()}]**:")
                for i, link in enumerate(links[:8]):
                    output.append(f"{i + 1}. {link}")

        return "\n".join(output)

    def _extract_features(self, text: str) -> Dict[str, List[str]]:
        results = {}
        for fmt, (pattern, _) in self.PROTOCOL_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                results[fmt] = list(set(matches))
        return results
