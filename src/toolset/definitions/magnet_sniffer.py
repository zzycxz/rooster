"""
MagnetSnifferTool — now a thin shim delegating to multimedia_download(action="search").

Original multi-protocol search logic has been merged into MultimediaDownloadTool.
This file is kept for backward compatibility and fc_hidden=True registration.
"""

import re
import asyncio
import logging
from typing import List, Type, Dict, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)


class ResourceArgs(BaseModel):
    query: str = Field(description="需要查找的资源核心名称。例如：'奥本海默' 或 'Ubuntu 24.04 ISO'")
    specs: Optional[str] = Field(description="额外的规格说明，如 '4K HDR' 或 'LTS'", default="")
    target_formats: List[str] = Field(
        description="目标协议特征。可选：magnet, ed2k, thunder, torrent, cloud_drive, direct",
        default=["magnet", "torrent", "cloud_drive"],
    )


class MagnetSnifferTool(BaseTool):
    name: str = "magnet_sniffer"
    kit: str = "Network"
    risk_level: str = "medium"
    fc_hidden: bool = True  # [Merge] 已合并到 multimedia_download(action="search")
    description: str = (
        "[DEPRECATED] Use multimedia_download(action='search') instead. "
        "This tool is kept for backward compatibility only."
    )
    domain: str = "resource"
    args_schema: Type[BaseModel] = ResourceArgs

    async def run(self, **kwargs) -> str:
        from .multimedia import MultimediaDownloadTool

        query = kwargs.get("query", "")
        specs = kwargs.get("specs", "")
        targets = kwargs.get("target_formats", ["magnet", "torrent", "cloud_drive"])

        tool = MultimediaDownloadTool(context=self.context)
        return await tool.run(
            action="search",
            query=query,
            specs=specs,
            target_formats=targets,
        )
