import os
import re
import subprocess
import platform
import json
import logging
import asyncio
import html as _html
import urllib.parse

from utils.config import settings
from typing import Type, Optional, List, Dict
from pydantic import BaseModel, Field
from toolset.base import BaseTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified resource download/search tool (merged from multimedia_download,
# movie_downloader, and magnet_sniffer).
# ---------------------------------------------------------------------------


class MultimediaDownloadArgs(BaseModel):
    action: str = Field(
        description="操作类型: 'download' | 'search' | 'auto'"
    )
    # [download] 给定 URI 直接下载
    uri: Optional[str] = Field(default=None, description="[download] 下载地址（magnet/ed2k/http）")
    filename: Optional[str] = Field(default="movie_resource", description="[download] 建议文件名")
    # [search] 搜索资源链接（不下载）
    query: Optional[str] = Field(default=None, description="[search/auto] 搜索关键词或片名")
    specs: Optional[str] = Field(default="", description="[search] 附加规格，如 '4K HDR'")
    target_formats: Optional[List[str]] = Field(
        default=None,
        description="[search] 目标协议: magnet, ed2k, cloud_drive, torrent, direct",
    )
    # [auto] 一键搜+下载
    title: Optional[str] = Field(default=None, description="[auto] 影片/资源名称，如 '奥本海默'")
    quality: Optional[str] = Field(default="1080p", description="[auto] 画质偏好: 1080p/4K")


class MultimediaDownloadTool(BaseTool):
    """
    统一资源工具：下载 + 搜索 + 一键下载，通过 action 参数分发。
    合并了原 multimedia_download、movie_downloader、magnet_sniffer。
    """

    name: str = "multimedia_download"
    kit: str = "Multimedia"
    risk_level: str = "medium"
    description: str = (
        "Download and resource search tool. Actions:\n"
        "- 'download': Start downloading a file by URI (magnet/ed2k/http). Requires: uri. "
        "Supports aria2 RPC or system default client (Xunlei/Thunder).\n"
        "- 'search': Search for downloadable resource links (magnet/torrent/cloud drive/ed2k). "
        "Requires: query. Returns links only — does NOT start downloading.\n"
        "- 'auto': One-step search + download by title. Requires: title. "
        "Searches torrent engines, picks best match, starts download automatically.\n"
        "NOT for: web page downloads (use file_system_op), web search (use web_search)."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = MultimediaDownloadArgs

    # ── Torrent search constants (from original MovieDownloaderTool) ────
    _MAGNET_RE = re.compile(
        r'magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}(?:[&;][^\s\'"<>\)]{1,120})*',
        re.IGNORECASE,
    )
    _TRACKERS = [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://tracker.dler.org:6969/announce",
        "http://tracker.bt4g.com:2095/announce",
        "udp://9.rarbg.com:2810/announce",
        "udp://tracker.openbittorrent.com:6969/announce",
    ]
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    _QUALITY_WORDS = {
        "1080p", "720p", "480p", "4k", "2160p", "bluray", "blu-ray",
        "web-dl", "webdl", "hdr", "remux", "hevc", "x265", "x264",
        "aac", "magnet", "torrent", "download", "1080", "720",
    }
    _ADULT_KEYWORDS = {
        "无码", "有码", "番号", "AV片", "成人视频", "自拍偷拍", "国产自拍",
        "巨乳", "萝莉", "人妻", "调教", "国产av", "日本av", "无码av",
        "kfa11", "国产自拍",
    }
    _ADULT_PATTERNS = [
        re.compile(r"(^|[^a-z0-9])(av|jav|porn|xxx|adult|sex|fc2|xvideos|xnxx|onlyfans)([^a-z0-9]|$)", re.I),
    ]

    # ── Multi-protocol patterns (from original MagnetSnifferTool) ───────
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

    # ── Main dispatch ───────────────────────────────────────────────────

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "").lower().strip()
        if not action:
            return "Error: 'action' is required. Choose: 'download', 'search', or 'auto'."

        if action == "download":
            return await self._do_download(kwargs)
        elif action == "search":
            return await self._do_search(kwargs)
        elif action == "auto":
            return await self._do_auto(kwargs)
        else:
            return f"Error: Unknown action '{action}'. Valid: 'download', 'search', 'auto'."

    # ===================================================================
    # Action: download (original multimedia_download logic)
    # ===================================================================

    async def _do_download(self, kwargs: dict) -> str:
        uri = (kwargs.get("uri") or "").strip()
        enabled = os.getenv("DOWNLOADER_ENABLED", "true").lower() == "true"

        if not enabled:
            return "Error: Downloader is currently disabled in .env."
        if not uri:
            return "Error: 'uri' is required for action='download'."

        provider = os.getenv("DOWNLOADER_PROVIDER", "system_default")
        if provider == "aria2_rpc":
            return await self._aria2_download(uri)
        else:
            return await self._system_default_download(uri)

    async def _system_default_download(self, uri: str) -> str:
        """Launch local protocol handler (e.g. Thunder), then auto-scan for popup."""
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(uri)
            elif system == "Darwin":
                subprocess.run(["open", uri], check=True)
            else:
                subprocess.run(["xdg-open", uri], check=True)
        except Exception as e:
            return f"❌ Failed to trigger local downloader: {str(e)}"

        await asyncio.sleep(3.0)

        try:
            from toolset.definitions.visual_control import DesktopGroundingScanTool

            scanner = DesktopGroundingScanTool(context=self.context)
            scan_result = await scanner.run(wait_seconds=0)
            hint = "💡 如看到迅雷确认弹窗，请调用 `desktop_click` 并传入对应的元素 ID（如立即下载/确认按钮的 ID）完成确认。"
            return (
                "✅ 已通过 system_default 唤起本地协议客户端。\n\n"
                "**自动桌面扫描结果（3秒后）：**\n"
                f"{scan_result}\n\n{hint}"
            )
        except Exception as e:
            return (
                "✅ 已通过 system_default 唤起本地协议客户端。\n"
                f"⚠️ 自动扫描失败（{e}），请手动调用 `desktop_grounding_scan` 观察屏幕并找到确认按钮。"
            )

    async def _aria2_download(self, uri: str) -> str:
        """Push download task to aria2 via JSON-RPC."""
        import httpx as _httpx

        rpc_url = os.getenv("ARIA2_RPC_URL", "http://localhost:6800/jsonrpc")
        rpc_secret = os.getenv("ARIA2_TOKEN", "")

        payload = {"jsonrpc": "2.0", "method": "aria2.addUri", "id": "rooster_dispatch", "params": [[uri]]}
        if rpc_secret:
            payload["params"].insert(0, f"token:{rpc_secret}")

        try:
            async with _httpx.AsyncClient(timeout=10) as client:
                response = await client.post(rpc_url, json=payload)
            result = response.json()
            if "result" in result:
                return f"✅ 任务已成功推送到 aria2 RPC。GID: {result['result']}"
            else:
                return f"❌ aria2 返回错误: {json.dumps(result.get('error'))}"
        except Exception as e:
            return f"❌ 无法连接到 aria2 RPC 服务: {str(e)}"

    async def _launch_download(self, magnet: str) -> str:
        """Unified download launch: try aria2 first, fallback to system default (non-blocking)."""
        # Priority: aria2 RPC
        try:
            import httpx as _httpx

            rpc_url = os.getenv("ARIA2_RPC_URL", "http://localhost:6800/jsonrpc")
            rpc_secret = os.getenv("ARIA2_TOKEN", "")

            rpc_params = [[magnet]]
            if rpc_secret:
                rpc_params = [f"token:{rpc_secret}", [magnet]]

            payload = {
                "jsonrpc": "2.0",
                "id": "rooster-auto",
                "method": "aria2.addUri",
                "params": rpc_params,
            }
            async with _httpx.AsyncClient(timeout=2.0) as client:
                response = await client.post(rpc_url, json=payload)
            resp_data = response.json()
            if "result" in resp_data:
                gid = resp_data["result"]
                logger.info(f"⚡ [Aria2] Auto-mode: pushed to aria2 RPC. GID: {gid}")
                return (
                    f"🎉 **[Aria2 静默直通车]** 已成功将任务推送到 Aria2 后台下载！\n"
                    f"- 任务 GID: `{gid}`\n"
                    f"- 磁力链接: `{magnet[:60]}...`"
                )
        except Exception as e:
            logger.info(f"ℹ️ Aria2 不可用，降级为系统客户端: {e}")

        # Fallback: system default client (non-blocking)
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(magnet)
            elif system == "Darwin":
                subprocess.run(["open", magnet], check=True)
            else:
                subprocess.run(["xdg-open", magnet], check=True)
        except Exception as e:
            return f"FAILED to launch client: {str(e)}\nmagnet: {magnet}"

        return (
            f"✅ 已拉起系统默认客户端（迅雷）开始下载。\n"
            f"- 磁力链接: `{magnet[:80]}...`\n"
            f"- 注意：如迅雷弹出确认窗口，请在迅雷界面中手动点击确认。\n"
            f"- aria2 静默通道不可用，已降级为系统协议调用。"
        )

    # ===================================================================
    # Action: search (merged from magnet_sniffer + movie_downloader search)
    # ===================================================================

    async def _do_search(self, kwargs: dict) -> str:
        query = (kwargs.get("query") or "").strip()
        specs = kwargs.get("specs", "")
        targets = kwargs.get("target_formats") or ["magnet", "torrent", "cloud_drive"]

        if not query:
            return "Error: 'query' is required for action='search'."

        full_query = f"{query} {specs}".strip()

        # Phase 1: If targeting magnet/torrent → use vertical torrent engines (stronger)
        needs_torrent = any(t in targets for t in ["magnet", "torrent"])
        if needs_torrent:
            magnet = await self._search_torrent_engines(full_query)
            if magnet:
                return (
                    f"### 🎯 已发现 '{query}' 的资源:\n\n"
                    f"**[MAGNET]** (torrent engines):\n"
                    f"1. {magnet}\n\n"
                    f"💡 调用 `multimedia_download(action='download', uri='{magnet[:80]}...')` 开始下载。"
                )

        # Phase 2: General WebSearchTool search + multi-protocol extraction (broader)
        results = await self._search_multi_protocol(full_query, targets)
        if not any(results.values()):
            return f"未能在搜索中找到 '{query}' 的有效资源链接。请尝试调整关键词或规格参数。"

        output = [f"### 🎯 已发现 '{query}' 的资源快照:"]
        for fmt, links in results.items():
            if links:
                output.append(f"\n**[{fmt.upper()}]**:")
                for i, link in enumerate(links[:8]):
                    output.append(f"{i + 1}. {link}")
        output.append(f"\n💡 调用 `multimedia_download(action='download', uri=...)` 开始下载。")
        return "\n".join(output)

    async def _search_multi_protocol(self, query: str, target_formats: List[str]) -> Dict[str, List[str]]:
        """Search using WebSearchTool + multi-protocol regex extraction (from magnet_sniffer)."""
        from .web_search import WebSearchTool
        from utils.security import state_guard

        search_cmds = []
        for fmt in target_formats:
            if fmt in self.PROTOCOL_PATTERNS:
                keywords = self.PROTOCOL_PATTERNS[fmt][1]
                search_cmds.append(f"{query} {keywords[0]}")

        if not search_cmds:
            return {}

        logger.info(f"🚀 [MultiProtocol] 启动 {len(search_cmds)} 路并发搜索: {query}")
        search_tool = WebSearchTool()
        tasks = [asyncio.create_task(search_tool.run(query=cmd)) for cmd in search_cmds]

        found_links: Dict[str, List[str]] = {}
        try:
            for coro in asyncio.as_completed(tasks, timeout=30):
                try:
                    partial_result = await coro
                    temp = self._extract_features(partial_result)
                    for k, v in temp.items():
                        found_links[k] = list(set(found_links.get(k, []) + v))
                        task_id = f"search-{query[:20]}"
                        for link in v:
                            state_guard.add_candidate(task_id, link, {"protocol": k, "source": "multi_protocol"})
                except Exception:
                    pass
        except asyncio.TimeoutError:
            pass

        # Background fill for remaining tasks
        remaining = [t for t in tasks if not t.done()]
        if remaining:

            async def _fill_bg():
                for fut in asyncio.as_completed(remaining):
                    try:
                        bg_res = await fut
                        bg_links = self._extract_features(bg_res)
                        for k, v in bg_links.items():
                            for link in v:
                                state_guard.add_candidate(
                                    f"search-{query[:20]}", link, {"protocol": k, "source": "bg_scan"}
                                )
                    except Exception:
                        pass

            asyncio.create_task(_fill_bg())

        return found_links

    def _extract_features(self, text: str) -> Dict[str, List[str]]:
        """Extract protocol-matched links from text."""
        results = {}
        for fmt, (pattern, _) in self.PROTOCOL_PATTERNS.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                results[fmt] = list(set(matches))
        return results

    # ===================================================================
    # Action: auto (original movie_downloader logic, using shared methods)
    # ===================================================================

    async def _do_auto(self, kwargs: dict) -> str:
        title = (kwargs.get("title") or "").strip()
        quality = (kwargs.get("quality") or "1080p").strip()

        if not title:
            return "Error: 'title' is required for action='auto'."

        try:
            return await asyncio.wait_for(
                self._auto_inner(title, quality),
                timeout=settings.DOWNLOAD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return (
                f"FAILED: auto download timed out after {settings.DOWNLOAD_TIMEOUT}s for '{title}'. "
                f"The BT search sites may be unreachable. Try again or use action='search' to find links manually."
            )

    async def _auto_inner(self, title: str, quality: str) -> str:
        clean_title = title.replace("《", "").replace("》", "").strip()
        search_query = f"{clean_title} {quality}"

        magnet = await self._search_torrent_engines(search_query)
        if not magnet:
            magnet = await self._search_torrent_engines(clean_title)

        if not magnet:
            return f"FAILED: no magnet link found for '{clean_title}'. Try action='search' to find links manually."

        # Append mainstream trackers
        for tr in self._TRACKERS:
            if tr not in magnet:
                magnet += "&tr=" + tr

        return await self._launch_download(magnet)

    # ===================================================================
    # Torrent engine search (from original MovieDownloaderTool._find_magnet)
    # ===================================================================

    async def _search_torrent_engines(self, query: str) -> Optional[str]:
        """
        Three-phase magnet search:
        1. Concurrent vertical torrent engines (Nyaa, Bitsearch, TPB, 1337x, ApiBay)
        2. Playwright headless browser fallback (btdig.com)
        Returns the best-scoring magnet URI, or None.
        """
        import httpx as _httpx

        eq = urllib.parse.quote_plus(query)

        # Phase 0: concurrent vertical engine search
        logger.info("🚀 启动并发垂直种子引擎群搜...")
        try:
            title_only = re.sub(
                r"\s*\b(?:1080p?|720p?|4[Kk]|2160p?|blu[_-]?ray|web[_-]?dl|hdr|remux|hevc|x265|x264|aac)\b\s*",
                " ", query, flags=re.IGNORECASE,
            ).strip()
            eq_title = urllib.parse.quote_plus(title_only)

            async with _httpx.AsyncClient(timeout=8, follow_redirects=True, headers=self._HEADERS) as client:

                async def _fetch_site(url: str) -> str:
                    try:
                        resp = await client.get(url, timeout=8)
                        if resp.status_code == 200:
                            return _html.unescape(resp.text)
                    except Exception as e:
                        logger.warning(f"⚠️ 垂直种子抓取 {url[:60]} 失败: {e}")
                    return ""

                async def _fetch_apibay():
                    try:
                        resp = await client.get(f"https://apibay.org/q.php?q={eq_title}", timeout=8)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list) and len(data) > 0 and data[0].get("id") != "0":
                                return data
                    except Exception as e:
                        logger.warning(f"⚠️ ApiBay API 请求失败: {e}")
                    return None

                tasks = [
                    _fetch_site(f"https://nyaa.si/?f=0&c=0_0&q={eq_title}"),
                    _fetch_site(f"https://bitsearch.to/search?q={eq_title}"),
                    _fetch_site(f"https://tpb.party/search/{eq_title}/0/7/0"),
                    _fetch_site(f"https://1337x.to/search/{eq_title}/1/"),
                ]
                results = await asyncio.gather(*tasks, _fetch_apibay(), return_exceptions=True)

                candidates = []
                for r in results[:-1]:
                    if isinstance(r, str) and r:
                        candidates.extend(self._extract_all_candidates(r, query))

                apibay_res = results[-1]
                if isinstance(apibay_res, list):
                    for item in apibay_res:
                        info_hash = item.get("info_hash")
                        name = item.get("name")
                        size_str = item.get("size", "0")
                        try:
                            size_bytes = int(size_str)
                        except ValueError:
                            size_bytes = 0

                        if info_hash and info_hash != "0000000000000000000000000000000000000000":
                            mag = f"magnet:?xt=urn:btih:{info_hash}&dn={urllib.parse.quote_plus(name)}"
                            score = self._score_magnet(mag, name, query)
                            if score >= 0:
                                if size_bytes > 25 * 1024 ** 3:
                                    score -= 3.0
                                elif 2 * 1024 ** 3 <= size_bytes <= 12 * 1024 ** 3:
                                    score += 2.0
                            if score >= 1.0:
                                candidates.append({"magnet": mag, "score": score})

                if candidates:
                    candidates.sort(key=lambda x: x["score"], reverse=True)
                    best = candidates[0]
                    if best["score"] >= 1.0:
                        logger.info(f"🎉 并发搜索成功! Best score: {best['score']}: {best['magnet'][:60]}")
                        return best["magnet"]
        except Exception as e:
            logger.warning(f"⚠️ 并发群搜异常: {e}")

        # Phase 2: Playwright fallback
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
                ctx = await browser.new_context(
                    user_agent=self._HEADERS["User-Agent"],
                    locale="zh-CN",
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
                )
                page = await ctx.new_page()
                url = f"https://btdig.com/search?q={eq}&p=0&f=0"
                try:
                    await page.goto(url, timeout=25000, wait_until="domcontentloaded")
                    try:
                        await page.wait_for_selector('a[href^="magnet:"]', timeout=10000)
                    except Exception:
                        pass
                    html_content = await page.content()
                    candidates = self._extract_all_candidates(html_content, query)
                    if candidates:
                        candidates.sort(key=lambda x: x["score"], reverse=True)
                        best = candidates[0]
                        logger.info(f"✅ Playwright btdig 成功, score={best['score']}: {best['magnet'][:60]}")
                        return best["magnet"]
                except Exception as e:
                    logger.warning(f"⚠️ Playwright btdig 失败: {e}")
                await browser.close()
        except Exception as e:
            logger.warning(f"⚠️ Playwright 启动失败: {e}")

        return None

    # ── Torrent scoring helpers (lifted from nested functions) ──────────

    def _looks_adult(self, text: str) -> bool:
        t = (text or "").lower()
        if any(k in t for k in self._ADULT_KEYWORDS):
            return True
        return any(p.search(t) for p in self._ADULT_PATTERNS)

    @staticmethod
    def _decode_dn(magnet: str) -> str:
        try:
            m = re.search(r"[?&]dn=([^&]+)", magnet, flags=re.IGNORECASE)
            if not m:
                return ""
            return urllib.parse.unquote_plus(m.group(1)).strip().lower()
        except Exception:
            return ""

    def _score_magnet(self, magnet: str, context: str, q: str) -> float:
        """Torrent quality scoring: base hits + quality bonus - adult spam penalty."""
        context_l = context.lower()
        dn = self._decode_dn(magnet)
        joined = f"{context_l} {dn}".lower()

        query_is_adult = self._looks_adult(q)
        if self._looks_adult(joined) and not query_is_adult:
            return -100.0

        terms = [
            t
            for t in re.split(r"[\s+]+", q.lower())
            if len(t) > 1 and t not in self._QUALITY_WORDS
            and (any("一" <= c <= "鿿" for c in t) or len(t) > 4)
        ]
        if not terms:
            return 1.0

        hit_terms = [t for t in terms if t in joined]
        if not hit_terms:
            return -1.0

        dn_hits = sum(1 for t in terms if t in dn)
        base_score = len(hit_terms) * 1.0 + dn_hits * 10.0

        is_sweet_spot = any(w in joined for w in ["1080p", "1080", "web-dl", "webdl", "x265", "hevc", "x264"])
        is_heavy_disk = any(w in joined for w in ["4k", "2160p", "remux", "complete", "bd50", "bd25"])

        quality_bonus = 0.0
        if is_sweet_spot:
            quality_bonus += 3.0
        if is_heavy_disk:
            quality_bonus -= 2.0
        elif "720p" in joined or "720" in joined:
            quality_bonus += 0.5

        # Anti-fake: if filename (dn) has zero hits, this is likely spam
        if dn_hits == 0:
            quality_bonus *= 0.1
            base_score *= 0.5

        return base_score + quality_bonus

    def _extract_all_candidates(self, text: str, q: str, window: int = 300) -> list:
        """Extract all candidate magnets from HTML and score them."""
        candidates = []
        text_l = text.lower()
        for m in self._MAGNET_RE.finditer(text):
            mag = m.group(0)
            pos = m.start()
            context = text_l[max(0, pos - window) : pos + window]
            try:
                context_decoded = urllib.parse.unquote(context)
            except Exception:
                context_decoded = context
            score = self._score_magnet(mag, context_decoded, q)
            if score >= 1.0:
                candidates.append({"magnet": mag, "score": score})

        # Also look for bare info hashes
        hashes = re.findall(r"(?<![a-fA-F0-9])([a-fA-F0-9]{40})(?![a-fA-F0-9])", text)
        for h in hashes:
            mag = "magnet:?xt=urn:btih:" + h
            score = self._score_magnet(mag, "", q)
            if score >= 1.0:
                candidates.append({"magnet": mag, "score": score})
        return candidates
