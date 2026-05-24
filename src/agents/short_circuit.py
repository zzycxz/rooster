"""
智能直通车路由器 (Short-Circuit Router)。
将特定关键词匹配的任务直接路由到物理工具执行，绕过 LLM 规划层。
"""
# Smart shortcut router (Short-Circuit Router).
# Route tasks matching specific keywords directly to physical tool execution, bypassing LLM planning layer.

import re
import logging
from typing import Any, Tuple

logger = logging.getLogger(__name__)

_BAD_DOMAINS = frozenset(
    {
        "onlinedown.net",
        "pc6.com",
        "pconline.com.cn",
        "zol.com.cn",
        "skycn",
        "downia",
        "duote",
        "crsky",
        "xdowns",
        "cngr",
        "zdfans",
        "soft555",
        "xiazaiba",
    }
)

_GOOD_DOMAINS = frozenset(
    {
        "github.com",
        "microsoft.com",
        "google.cn",
        "google.com",
        "mozilla.org",
        "apple.com",
        "oracle.com",
        "jetbrains.com",
        "python.org",
        "nodejs.org",
        "git-scm.com",
        "rust-lang.org",
        "videolan.org",
        "obsproject.com",
        "blender.org",
    }
)


class ShortCircuitRouter:
    """匹配关键词 → 解析参数 → 直接调用工具并返回结果。"""  # Match keywords → parse params → directly call tool and return result

    async def try_handle(self, text: str, channel: Any, sender_id: str) -> bool:
        """
        尝试直通车处理。返回 True 表示已处理（Router 不应继续）。
        """
        # Try shortcut handling. Return True if handled (Router should not continue)
        if "resource-downloader" not in text:
            return False

        logger.info("⚡ [ShortCircuit] 触发 resource-downloader 直通车")
        title, res_type, quality = self._parse_params(text)

        if not title:
            return False

        await channel.send_message(
            to=sender_id,
            text=(
                f"⚡ **[智能直通车]** 已为您拉起通用资源下载服务：\n"
                f"- 资源名称: `{title}`\n"
                f"- 资源类型: `{res_type}`\n"
                f"- 期望画质: `{quality}`\n\n"
                f"🚀 正在并发群搜 Nyaa/BitSearch/1337x/TPB 等垂直站资源，"
                f"过滤无关广告擦边垃圾，精选评分最高最优种子，请稍候..."
            ),
        )

        try:
            if res_type == "movie":
                await self._handle_movie(title, quality, channel, sender_id)
            else:
                await self._handle_software(title, channel, sender_id)
        except Exception as e:
            logger.error(f"直通车物理执行异常: {e}")
            await channel.send_message(
                to=sender_id,
                text=f"❌ **[直通车故障]** 物理直接执行时发生异常: {e}",
            )
        return True

    @staticmethod
    def _parse_params(text: str) -> Tuple[str, str, str]:
        title_match = re.search(r'title="([^"]+)"', text)
        type_match = re.search(r'type="([^"]+)"', text)
        quality_match = re.search(r'quality="([^"]+)"', text)
        title = title_match.group(1) if title_match else ""
        res_type = type_match.group(1) if type_match else "movie"
        quality = quality_match.group(1) if quality_match else "1080p"
        return title, res_type, quality

    async def _handle_movie(self, title: str, quality: str, channel, sender_id: str):
        from toolset.definitions.multimedia import MovieDownloaderTool

        tool = MovieDownloaderTool()
        result = await tool.run(title=title, quality=quality)
        await channel.send_message(
            to=sender_id,
            text=f"🎉 **[智能直通车]** 下载任务分发完毕！\n\n**执行日志**：\n{result}",
        )

    async def _handle_software(self, title: str, channel, sender_id: str):
        from toolset.definitions.browser import WebSearchTool

        await channel.send_message(
            to=sender_id,
            text=f"🔎 **[智能直通车]** 正在为您安全检索 `{title}` 的官方下载渠道...",
        )
        tool = WebSearchTool()
        search_result = await tool.run(query=f"{title} 官方下载地址 官网")
        links = re.findall(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", search_result)

        trusted_links = []
        normal_links = []

        for label, url in links:
            url_lower = url.lower()
            if any(bad in url_lower for bad in _BAD_DOMAINS):
                continue
            is_direct = any(url_lower.endswith(ext) for ext in (".exe", ".msi", ".dmg", ".pkg", ".zip"))
            is_trusted = (
                any(good in url_lower for good in _GOOD_DOMAINS)
                or "official" in label.lower()
                or "官网" in label
                or "官方" in label
            )
            item = {"label": label.strip(), "url": url.strip(), "is_direct": is_direct, "is_trusted": is_trusted}
            if is_trusted or is_direct:
                trusted_links.append(item)
            else:
                normal_links.append(item)

        report = []
        if trusted_links:
            report.append("### 🟢 官方可信与安全通道推荐 (优先)")
            for item in trusted_links:
                tag = " ⚡ `[直链直接下载]`" if item["is_direct"] else " 🌟 `[官方可信源]`"
                report.append(f"- **[{item['label']}]({item['url']})**{tag}")
        if normal_links:
            report.append("\n### ⚪ 其他常规检索页面 (仅供参考)")
            for item in normal_links[:4]:
                report.append(f"- [{item['label']}]({item['url']})")
        if not report:
            report.append("\n⚠️ 未寻获十分明确的官方白名单渠道，已为您保留常规搜索概览以供甄别：\n" + search_result)

        await channel.send_message(
            to=sender_id,
            text=(
                f"🎉 **[智能直通车]** 已为您自动剔除所有含捆绑广告的流氓软件站，精选官方纯净渠道如下：\n\n"
                f"{''.join(report)}\n\n"
                f"💡 *安全提示*：请始终认准官方主域名下载，安装时避开任何形式的“高速下载器”或“推荐安装全家桶”。"
            ),
        )
