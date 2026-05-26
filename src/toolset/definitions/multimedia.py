import os
import re
import subprocess
import platform
import json
import ctypes
import logging
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool


class MultimediaDownloadArgs(BaseModel):
    uri: str = Field(description="下载地址（磁力链接 magnet:、电驴链接 ed2k: 或 普通 URL）")
    filename: str = Field(description="建议的文件名", default="movie_resource")


class MultimediaDownloadTool(BaseTool):
    """
    高级多媒体资源索引调度工具。
    根据 .env 中的 DOWNLOADER_PROVIDER 配置，支持：
    1. system_default: 唤起本地默认客户端（如迅雷）。
    2. aria2_rpc: 通过 JSON-RPC 协议远程下发任务。
    """

    name: str = "multimedia_download"
    kit: str = "Multimedia"
    description: str = (
        "Download large multimedia files using advanced protocols (Magnet, ED2K) or HTTP(S). "
        "For files >500MB, automatically delegates to local protocol handlers (e.g. Xunlei) or aria2 "
        "for multi-threaded/P2P accelerated downloads. "
        "Use this for movies, large archives, or any bulk download task."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = MultimediaDownloadArgs

    async def run(self, **kwargs) -> str:
        uri = kwargs.get("uri", "").strip()
        provider = os.getenv("DOWNLOADER_PROVIDER", "system_default")
        enabled = os.getenv("DOWNLOADER_ENABLED", "true").lower() == "true"

        if not enabled:
            return "Error: Downloader is currently disabled in .env."

        if not uri:
            return "Error: URI is empty."

        # Core logic: execute download based on provider
        # 核心逻辑：根据 Provider 执行下载
        if provider == "aria2_rpc":
            return await self._aria2_download(uri)
        else:
            return await self._system_default_download(uri)

    async def _system_default_download(self, uri: str) -> str:
        """Launch local protocol handler (e.g. Thunder), then auto-screenshot and scan popups.
        唤起本地协议关联程序（迅雷等），然后自动截图扫描弹窗"""
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

        # Wait for download client popup to render (Thunder typically 2-4s)
        # 等待下载客户端弹窗渲染（迅雷通常 2-4 秒）
        import asyncio

        await asyncio.sleep(3.0)

        # Auto desktop scan to find the confirm button
        # 自动执行桌面扫描，找到确认按钮
        try:
            from toolset.definitions.visual_control import DesktopGroundingScanTool

            scanner = DesktopGroundingScanTool(context=self.context)
            scan_result = await scanner.run(wait_seconds=0)
            hint = "💡 如看到迅雷确认弹窗，请调用 `desktop_click` 并传入对应的元素 ID（如立即下载/确认按钮的 ID）完成确认。"
            return (
                "✅ 已通过 system_default 唤起本地协议客户端。\n\n"
                "**自动桌面扫描结果（3秒后）：**\n"
                f"{scan_result}\n\n"
                f"{hint}"
            )
        except Exception as e:
            return (
                "✅ 已通过 system_default 唤起本地协议客户端。\n"
                f"⚠️ 自动扫描失败（{e}），请手动调用 `desktop_grounding_scan` 观察屏幕并找到确认按钮。"
            )

    async def _aria2_download(self, uri: str) -> str:
        """通过 JSON-RPC 发送任务给 aria2"""
        import httpx as _httpx

        rpc_url = os.getenv("ARIA2_RPC_URL", "http://localhost:6800/jsonrpc")
        rpc_secret = os.getenv("ARIA2_RPC_SECRET", "")

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


# ─────────────────────────────────────────────────────────────────────────────
# MovieDownloaderTool: search + extract magnet + launch Thunder, all in one step
# MovieDownloaderTool: 搜索 + 提取磁力 + 唤起迅雷，一步完成
# ─────────────────────────────────────────────────────────────────────────────


class MovieDownloaderArgs(BaseModel):
    title: str = Field(description="影片名称，如 '奥本海默' 或 'Oppenheimer 2023'")
    quality: Optional[str] = Field(description="画质偏好，如 '1080p'、'4K'", default="1080p")


class MovieDownloaderTool(BaseTool):
    """
    一步式电影下载工具：自动搜索磁力链接并唤起本地迅雷客户端开始下载。
    无需手动分两步操作，直接传入影片名即可完成完整下载流程。

    重要：调用前必须确认用户意图。如果搜索结果中存在多个同名/相似名资源（如不同年份、
    不同版本），必须先用 CONFIRM_REQUIRED 询问用户要下载哪一个，不要自行猜测。
    """

    name: str = "movie_downloader"
    kit: str = "Multimedia"
    description: str = (
        "All-in-one movie downloader: searches for magnet/torrent links and immediately launches "
        "the local Xunlei (Thunder) client to start downloading. "
        "Just provide the movie title. Handles search, link extraction, and download trigger automatically. "
        "IMPORTANT: If multiple versions exist (e.g. different years, theatrical vs director's cut), "
        "you MUST ask the user which one to download via CONFIRM_REQUIRED before calling this tool."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = MovieDownloaderArgs

    # BT search site list (try in order)
    # BT搜索站列表（依次尝试）
    _SEARCH_ENGINES = [
        "https://btdig.com/search?q={query}&p=0&f=0",
        "https://btsow.pics/search/{query}",
        "https://www.seedhub.cc/search?query={query}",
    ]

    _MAGNET_RE = re.compile(r'magnet:\?xt=urn:btih:[a-zA-Z0-9]{32,40}(?:[&;][^\s\'"<>\)]{1,120})*', re.IGNORECASE)

    async def run(self, **kwargs) -> str:
        import asyncio

        title = kwargs.get("title", "").strip()
        quality = kwargs.get("quality", "1080p").strip()
        if not title:
            return "❌ 未提供影片名称。"

        try:
            return await asyncio.wait_for(self._run_inner(title, quality), timeout=120)
        except asyncio.TimeoutError:
            return f"FAILED: movie_downloader timed out after 120s for '{title}'. The BT search sites may be unreachable. Try again or search manually."

    async def _run_inner(self, title: str, quality: str) -> str:
        import asyncio

        search_query = f"{title} {quality}"
        magnet = await self._find_magnet(search_query)

        if not magnet:
            # Fallback: retry without quality keywords
            # 降级：去掉画质词重试
            magnet = await self._find_magnet(title)

        if not magnet:
            return "FAILED: no magnet link found for '" + title + "'. Try btdig.com or seedhub.cc manually."

        # Append mainstream trackers (prevent Thunder from failing to fetch torrent metadata)
        # 附加主流 Tracker（避免迅雷无法获取种子元数据）
        _TRACKERS = [
            "udp://tracker.opentrackr.org:1337/announce",
            "udp://open.stealth.si:80/announce",
            "udp://tracker.torrent.eu.org:451/announce",
            "udp://tracker.dler.org:6969/announce",
            "http://tracker.bt4g.com:2095/announce",
            "udp://9.rarbg.com:2810/announce",
            "udp://tracker.openbittorrent.com:6969/announce",
        ]
        for tr in _TRACKERS:
            if tr not in magnet:
                magnet += "&tr=" + tr

        # ── Priority: adaptively detect and push to Aria2 RPC ──────────────
        # ── 优先自适应探测并推送到 Aria2 RPC ──────────────────────────
        try:
            import json
            import urllib.request

            aria2_url = "http://localhost:6800/jsonrpc"
            aria2_token = os.environ.get("ARIA2_TOKEN", "")

            rpc_params = [[magnet]]
            if aria2_token:
                rpc_params = [f"token:{aria2_token}", [magnet]]

            payload = {"jsonrpc": "2.0", "id": "rooster-downloader", "method": "aria2.addUri", "params": rpc_params}

            req = urllib.request.Request(
                aria2_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # 2s ultra-short probe; if unreachable, fall back to standard flow
            # 2秒超短探测，如果不通则走常规降级
            with urllib.request.urlopen(req, timeout=2.0) as response:
                resp_data = json.loads(response.read().decode("utf-8"))
                if "result" in resp_data:
                    gid = resp_data["result"]
                    logging.info(f"⚡ [Aria2] Successfully pushed to Aria2 background download via RPC! GID: {gid}")
                    logging.info(f"⚡ [Aria2] 成功通过 RPC 静默推送到 Aria2 后台下载！GID: {gid}")
                    return (
                        f"🎉 **[Aria2 静默直通车]** 已成功将任务推送到您的 Aria2 后台静默下载！\n"
                        f"- 任务 GID: `{gid}`\n"
                        f"- 磁力链接: `{magnet[:60]}...`"
                    )
        except Exception as e:
            logging.info(
                f"ℹ️ Aria2 service not running or auth failed, smoothly falling back to standard client (Thunder): {e}"
            )
            logging.info(f"ℹ️ Aria2 服务未运行或鉴权失败，已平滑降级拉起常规客户端 (迅雷): {e}")

        # ── Fallback: launch system default client (Thunder) ──────────────
        # ── 降级：拉起系统默认客户端 (迅雷) ──────────────────────────
        try:
            system = platform.system()
            if system == "Windows":
                os.startfile(magnet)
            elif system == "Darwin":
                subprocess.run(["open", magnet], check=True)
            else:
                subprocess.run(["xdg-open", magnet], check=True)
        except Exception as e:
            return "FAILED to launch client: " + str(e) + "\nmagnet: " + magnet

        # 非阻塞快速返回：迅雷已拉起，不等待弹窗确认
        # Non-blocking quick return: Thunder launched, don't wait for popup confirmation
        # 用户可在迅雷界面手动确认，或通过 Dashboard 的桌面截图观察状态
        return (
            f"✅ 已拉起系统默认客户端（迅雷）开始下载。\n"
            f"- 磁力链接: `{magnet[:80]}...`\n"
            f"- 注意：如迅雷弹出确认窗口，请在迅雷界面中手动点击确认。\n"
            f"- aria2 静默通道不可用，已降级为系统协议调用。"
        )

    async def _find_magnet(self, query: str) -> Optional[str]:
        """
        三级磁力搜索防线：
        1. 并发群搜：同时发起 3+ 个垂直种子搜索器，极速聚合所有磁力并统一打分，若质量合格直接返回下载。
        2. 浏览器直接搜索：无 JS 防护或 Playwright 启动无头浏览器直接打开并抓取 btdig.com 检索。
        3. 常规网页搜索引擎抓取 (不含 AI 联网搜索)：调用 WebSearchTool 静态搜索网页并提取。
        """
        import urllib.parse
        import httpx as _httpx
        import asyncio
        import html as _html

        _HEADERS = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }

        _QUALITY_WORDS = {
            "1080p",
            "720p",
            "480p",
            "4k",
            "2160p",
            "bluray",
            "blu-ray",
            "web-dl",
            "webdl",
            "hdr",
            "remux",
            "hevc",
            "x265",
            "x264",
            "aac",
            "magnet",
            "torrent",
            "download",
            "1080",
            "720",
        }

        _ADULT_KEYWORDS = {
            "无码",
            "有码",
            "番号",
            "AV片",
            "成人视频",
            "自拍偷拍",
            "国产自拍",
            "巨乳",
            "萝莉",
            "人妻",
            "调教",
            "国产av",
            "日本av",
            "无码av",
            "kfa11",
            "国产自拍",
        }
        _ADULT_PATTERNS = [
            re.compile(r"(^|[^a-z0-9])(av|jav|porn|xxx|adult|sex|fc2|xvideos|xnxx|onlyfans)([^a-z0-9]|$)", re.I),
        ]

        def _looks_adult(text: str) -> bool:
            t = (text or "").lower()
            if any(k in t for k in _ADULT_KEYWORDS):
                return True
            return any(p.search(t) for p in _ADULT_PATTERNS)

        def _decode_dn(magnet: str) -> str:
            try:
                import urllib.parse as _up

                m = re.search(r"[?&]dn=([^&]+)", magnet, flags=re.IGNORECASE)
                if not m:
                    return ""
                return _up.unquote_plus(m.group(1)).strip().lower()
            except Exception:
                return ""

        def _score_magnet(magnet: str, context: str, q: str) -> float:
            """
            Torrent quality scoring algorithm:
            - Base score: number of core search term hits
            - Bonus: quality feature matches (1080p, 4k, bluray)
            - Blocker: adult/spam content penalized to -100 during normal search; adaptive release when user explicitly searches restricted content.
            种子质量评估算法：
            - 基础分：命中搜索核心影片词的个数
            - 加分项：命中画质特征（1080p, 4k, bluray）
            - 阻断项：擦边与成人垃圾内容在正常检索时扣到 -100，当用户主动检索限制级时自适应放行。
            """
            context_l = context.lower()
            dn = _decode_dn(magnet)
            joined = f"{context_l} {dn}".lower()

            # Determine if user is explicitly searching for restricted/adult content
            # 判定用户是否主动检索限制级/成人资源
            query_is_adult = _looks_adult(q)

            if _looks_adult(joined) and not query_is_adult:
                return -100.0  # Only during normal search: aggressively block spam/adult ads and polluted sources
                return -100.0  # 仅在普通检索时，强力封杀垃圾擦边广告和污染源

            terms = [
                t
                for t in re.split(r"[\s+]+", q.lower())
                if len(t) > 1 and t not in _QUALITY_WORDS and (any("\u4e00" <= c <= "\u9fff" for c in t) or len(t) > 4)
            ]
            if not terms:
                return 1.0

            hit_terms = [t for t in terms if (t in joined)]
            if not hit_terms:
                return -1.0  # No search term match
                return -1.0  # 未命中搜索词

            dn_hits = sum(1 for t in terms if t in dn)
            base_score = len(hit_terms) + dn_hits * 2.0

            # ── Fine quality and size balancing control ─────────────────────
            # ── 精细画质与体积均衡控制 ──────────────────────────────────
            # 1. Sweet spot quality: 1080p, x265, hevc, x264, web-dl. Size typically 2GB-8GB, great quality and fast download, highest bonus!
            # 1. 甜点画质区：1080p, x265, hevc, x264, web-dl。体积一般在 2GB-8GB 之间，画质出众且下载极快，给予最高加分！
            is_sweet_spot = any(w in joined for w in ["1080p", "1080", "web-dl", "webdl", "x265", "hevc", "x264"])

            # 2. Oversized disc penalty zone: 4k, 2160p, remux, complete-bluray. Typically 30GB-100GB, easily fills disk or freezes Thunder, apply penalty!
            # 2. 超大原盘惩罚区：4k, 2160p, remux, complete-bluray。这类资源体积通常在 30GB-100GB 之间，极易塞爆硬盘或导致迅雷卡死，进行分值减扣（降权）！
            is_heavy_disk = any(w in joined for w in ["4k", "2160p", "remux", "complete", "bd50", "bd25"])

            quality_bonus = 0.0
            if is_sweet_spot:
                quality_bonus += 3.0  # Priority bonus for 1080p sweet spot HD / 优先奖励 1080p 甜点高清

            if is_heavy_disk:
                quality_bonus -= 2.0  # Oversized penalty / 超大体积降权
            elif "720p" in joined or "720" in joined:
                quality_bonus += 0.5  # 720p secondary alternative / 720p 次优备选

            return base_score + quality_bonus

        def _extract_all_candidates(text: str, q: str, window: int = 300) -> list:
            """从网页源 HTML 文本中提取所有候选磁力，计算评估质量分"""
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
                score = _score_magnet(mag, context_decoded, q)
                if score >= 1.0:  # 基础合格分
                    candidates.append({"magnet": mag, "score": score})

            hashes = re.findall(r"(?<![a-fA-F0-9])([a-fA-F0-9]{40})(?![a-fA-F0-9])", text)
            for h in hashes:
                mag = "magnet:?xt=urn:btih:" + h
                score = _score_magnet(mag, "", q)
                if score >= 1.0:
                    candidates.append({"magnet": mag, "score": score})
            return candidates

        eq = urllib.parse.quote_plus(query)

        # ── Phase 0: concurrent search across 3+ vertical torrent engines ──────────────
        # ── 阶段 0：并发群搜 3+ 垂直种子引擎 ────────────────────────────────
        _title_only = re.sub(
            r"\s*\b(?:1080p?|720p?|4[Kk]|2160p?|blu[_-]?ray|web[_-]?dl|hdr|remux|hevc|x265|x264|aac)\b\s*",
            " ",
            query,
            flags=re.IGNORECASE,
        ).strip()

        async def _fetch_site(client, url: str) -> str:
            try:
                resp = await client.get(url, timeout=8)
                if resp.status_code == 200:
                    return _html.unescape(resp.text)
            except Exception as e:
                logging.warning(f"⚠️ 并发垂直种子抓取 {url[:60]} 失败: {e}")
            return ""

        logging.info("🚀 启动阶段 0：并发垂直种子引擎群搜...")
        try:
            async with _httpx.AsyncClient(timeout=8, follow_redirects=True, headers=_HEADERS) as client:
                eq_title = urllib.parse.quote_plus(_title_only)

                # Build multi-engine concurrent sources
                # 建立多引擎并发源
                tasks = []
                # 1. Nyaa ACG source
                # 1. Nyaa ACG源
                tasks.append(_fetch_site(client, f"https://nyaa.si/?f=0&c=0_0&q={eq_title}"))
                # 2. Bitsearch movie source
                # 2. Bitsearch 影视源
                tasks.append(_fetch_site(client, f"https://bitsearch.to/search?q={eq_title}"))
                # 3. TPB Pirate Bay mirror source
                # 3. TPB 海盗湾镜像源
                tasks.append(_fetch_site(client, f"https://tpb.party/search/{eq_title}/0/7/0"))
                # 4. 1337x descending health source
                # 4. 1337x 降序健康源
                tasks.append(_fetch_site(client, f"https://1337x.to/search/{eq_title}/1/"))

                # 5. ApiBay (Pirate Bay official JSON API, fast and no HTML parsing needed)
                # 5. ApiBay (海盗湾官方 JSON API，极速且免 HTML 解析)
                async def _fetch_apibay():
                    try:
                        resp = await client.get(f"https://apibay.org/q.php?q={eq_title}", timeout=8)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list) and len(data) > 0 and data[0].get("id") != "0":
                                return data
                    except Exception as e:
                        logging.warning(f"⚠️ ApiBay API 请求失败: {e}")
                    return None

                results = await asyncio.gather(*tasks, _fetch_apibay(), return_exceptions=True)

                candidates = []
                # Aggregate magnets from 4 HTML pages
                # 汇总 4 个 HTML 页面提取的磁力
                for r in results[:-1]:
                    if isinstance(r, str) and r:
                        candidates.extend(_extract_all_candidates(r, query))

                # Aggregate ApiBay JSON magnets
                # 汇总 ApiBay 的 JSON 磁力
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
                            score = _score_magnet(mag, name, query)

                            # Physical size penalty and sweet spot bonus
                            # 物理体积惩罚与甜点奖励
                            if size_bytes > 25 * 1024 * 1024 * 1024:
                                score -= 3.0  # 超过 25G 的原盘大文件扣 3 分
                            elif 2 * 1024 * 1024 * 1024 <= size_bytes <= 12 * 1024 * 1024 * 1024:
                                score += 2.0  # 处于 2G - 12G 之间的优质甜点体积加 2 分

                            if score >= 1.0:
                                candidates.append({"magnet": mag, "score": score})

                if candidates:
                    candidates.sort(key=lambda x: x["score"], reverse=True)
                    best = candidates[0]
                    if best["score"] >= 1.0:
                        logging.info(
                            f"🎉 Phase 0 concurrent search success! Selected highest-scored torrent (score: {best['score']}): {best['magnet'][:60]}"
                        )
                        logging.info(
                            f"🎉 阶段 0 并发群搜成功！已为您挑选评分最高的唯一最优种子 (得分: {best['score']}): {best['magnet'][:60]}"
                        )
                        return best["magnet"]
        except Exception as e:
            logging.warning(f"⚠️ Phase 0 concurrent search error: {e}")
            logging.warning(f"⚠️ 阶段 0 并发群搜异常: {e}")

        # ── Phase 2: Playwright opens btdig directly (real browser bypasses Cloudflare) ──
        # ── 阶段 2：Playwright 直接打开 btdig（真实浏览器绕过 Cloudflare）──
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
                )
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="zh-CN",
                    extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
                )
                page = await ctx.new_page()
                url = f"https://btdig.com/search?q={eq}&p=0&f=0"
                try:
                    await page.goto(url, timeout=25000, wait_until="domcontentloaded")
                    # Wait for magnet links to appear (max additional 10s)
                    # 等待磁力链接出现（最多额外 10 秒）
                    try:
                        await page.wait_for_selector('a[href^="magnet:"]', timeout=10000)
                    except Exception:
                        pass
                    html = await page.content()
                    result = _extract_all_candidates(html, query)
                    if result:
                        await browser.close()
                        return result
                except Exception as e:
                    logging.warning(f"⚠️ Playwright btdig 失败: {e}")
                await browser.close()
        except Exception as e:
            logging.warning(f"⚠️ Playwright launch failed: {e}")
            logging.warning(f"⚠️ Playwright 启动失败: {e}")

        return None

    def _find_thunder_hwnd(self) -> int:
        """返回最适合置前台的 Thunder 窗口（重叠最大的）。"""
        hwnds = self._enum_thunder_hwnds()
        return hwnds[0][0] if hwnds else 0

    def _enum_thunder_hwnds(self) -> list:
        """
        枚举所有 Thunder 进程的可见顶层窗口，返回列表，按面积升序（小窗口/弹窗优先）。
        只保留与主屏幕有足够重叠的窗口（重叠 > 10000 像素²）。
        非 Windows 平台直接返回空列表（无 HWND 支持）。
        """
        if platform.system().lower() != "windows":
            return []
        try:
            import win32gui
            import win32process
            import win32api
            import win32con as _wcon
            import psutil

            THUNDER_PROCS = {"thunder.exe", "xldownload.exe", "thunder_x64.exe", "xunlei.exe", "thundermini.exe"}
            thunder_pids = {
                p.pid
                for p in psutil.process_iter(["pid", "name"])
                if p.info["name"] and p.info["name"].lower() in THUNDER_PROCS
            }
            if not thunder_pids:
                return []

            sw = win32api.GetSystemMetrics(_wcon.SM_CXSCREEN)
            sh = win32api.GetSystemMetrics(_wcon.SM_CYSCREEN)

            candidates = []

            def _cb(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in thunder_pids:
                    return True
                x1, y1, x2, y2 = win32gui.GetWindowRect(hwnd)
                w, h = x2 - x1, y2 - y1
                if w <= 0 or h <= 0:
                    return True
                ox = max(0, min(x2, sw) - max(x1, 0))
                oy = max(0, min(y2, sh) - max(y1, 0))
                overlap = ox * oy
                if overlap > 10000:
                    candidates.append((hwnd, w * h, (x1, y1, x2, y2)))
                return True

            win32gui.EnumWindows(_cb, None)
            # Sort by area ascending: small windows (popups) first, more likely to contain "Download Now" button
            # 按面积升序：小窗口（弹窗）在前，更可能包含「立即下载」按钮
            candidates.sort(key=lambda x: x[1])
            return candidates
        except Exception:
            return []

    def _force_thunder_foreground(self, hwnd: int):
        """
        将指定 HWND 的窗口强制置为前台，绕过 Windows SetForegroundWindow 限制。
        使用 AttachThreadInput 技巧：把当前线程挂到目标窗口的 UI 线程，
        再调用 SetForegroundWindow，最后解挂。
        非 Windows 平台为空操作。
        """
        if platform.system().lower() != "windows":
            return
        try:
            import win32gui
            import win32process
            import win32api
            import win32con as _wcon

            # Restore from minimized
            # 恢复最小化
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, _wcon.SW_RESTORE)
            curr_tid = win32api.GetCurrentThreadId()
            target_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
            win32process.AttachThreadInput(curr_tid, target_tid, True)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
            win32process.AttachThreadInput(curr_tid, target_tid, False)
        except Exception:
            pass

    async def _dismiss_thunder_blocking_popup(self, DesktopController, hwnd: int) -> bool:
        """
        处理迅雷下载页上的遮挡弹窗（如 SVIP 免费试用广告）。
        策略：
        1) 优先点弹窗常见的右上角关闭位（相对窗口坐标）；
        2) 再发送 ESC；
        3) 每次动作后检测下载按钮是否出现。
        """
        if platform.system().lower() != "windows":
            return False
        try:
            import win32gui
            import asyncio

            if not hwnd:
                return False

            x1, y1, x2, y2 = win32gui.GetWindowRect(hwnd)
            w, h = max(1, x2 - x1), max(1, y2 - y1)

            # Heuristic positions: SVIP popup top-right X and window top-right area
            # 经验位：SVIP 弹窗右上角 X 及窗口右上角区域
            candidates = [
                (int(x1 + w * 0.74), int(y1 + h * 0.20)),
                (int(x1 + w * 0.78), int(y1 + h * 0.22)),
                (int(x1 + w * 0.70), int(y1 + h * 0.18)),
                (int(x1 + w * 0.95), int(y1 + h * 0.08)),
            ]

            for cx, cy in candidates:
                self._force_thunder_foreground(hwnd)
                await asyncio.sleep(0.12)
                await DesktopController.perform_click(cx, cy)
                await asyncio.sleep(0.45)
                if await self._find_blue_button(DesktopController, hwnd=hwnd):
                    return True

            # Some popups can be closed with ESC
            # 部分弹窗可被 ESC 关闭
            try:
                await DesktopController.perform_hotkey(["esc"])
                await asyncio.sleep(0.35)
                if await self._find_blue_button(DesktopController, hwnd=hwnd):
                    return True
            except Exception:
                pass

            return False
        except Exception:
            return False

    async def _find_blue_button(self, DesktopController, hwnd: int = 0) -> Optional[tuple]:
        """
        截图后用颜色分析定位迅雷「立即下载」蓝色按钮。
        策略：依次尝试所有 Thunder 窗口（面积从小到大），
        对每个窗口裁剪后查找横向跨越窗口宽度 ≥40% 的蓝色行段，
        这种全宽蓝色区域才是底部确认按钮，可排除导航栏/图标等干扰。
        """
        try:
            import numpy as np
            from PIL import Image
            import io
            import base64

            snap = await DesktopController.get_screenshot()
            if snap.get("status") != "success":
                return None

            img_bytes = base64.b64decode(snap["base64"])
            full_img = Image.open(io.BytesIO(img_bytes)).convert("RGB")

            # Collect candidate regions to search: all visible Thunder windows (small windows first)
            # 收集要搜索的候选区域：所有可见 Thunder 窗口（小窗口优先）
            win_candidates = []
            for _hwnd, _area, _rect in self._enum_thunder_hwnds():
                x1, y1, x2, y2 = _rect
                cx1 = max(0, x1)
                cy1 = max(0, y1)
                cx2 = min(full_img.width, x2)
                cy2 = min(full_img.height, y2)
                if cx2 > cx1 + 50 and cy2 > cy1 + 50:
                    win_candidates.append((_hwnd, cx1, cy1, cx2, cy2))
            # If target hwnd specified, search that window first
            # 若指定了目标 hwnd，则优先搜索该窗口
            if hwnd:
                win_candidates.sort(key=lambda item: 0 if item[0] == hwnd else 1)
            # If no windows enumerated, search full screen
            # 若没有枚举到任何窗口则搜索全屏
            if not win_candidates:
                win_candidates = [(0, 0, 0, full_img.width, full_img.height)]

            for _hwnd, cx1, cy1, cx2, cy2 in win_candidates:
                crop = full_img.crop((cx1, cy1, cx2, cy2))
                arr = np.array(crop)
                r = arr[:, :, 0].astype(int)
                g = arr[:, :, 1].astype(int)
                b = arr[:, :, 2].astype(int)

                blue_mask = (b > 140) & (b > r + 60) & (b > g + 40) & (r < 180) & (g < 200)

                ys, xs = np.where(blue_mask)
                if len(xs) < 100:
                    continue

                crop_w = cx2 - cx1
                # Find all blue row segments, keep only those spanning >= 40% of window width
                # The "Download Now" button is full-width; nav bars/icons never reach this width
                # 找所有蓝色行段，只保留横向宽度 ≥ 窗口宽度 40% 的行段
                # 「立即下载」是全宽按钮，导航栏/图标不会达到这个宽度
                y_counts = np.bincount(ys, minlength=arr.shape[0])
                min_width = crop_w * 0.40

                regions = []
                in_region, cur_start, cur_sum = False, 0, 0
                for i, cnt in enumerate(y_counts):
                    if cnt >= min_width:
                        if not in_region:
                            cur_start, cur_sum, in_region = i, 0, True
                        cur_sum += cnt
                    else:
                        if in_region:
                            regions.append((cur_start, i, cur_sum))
                            in_region = False
                if in_region:
                    regions.append((cur_start, len(y_counts), cur_sum))

                if not regions:
                    continue

                # Take the segment with the most pixels (largest full-width blue area = the button)
                # 取像素总数最多的行段（最大的全宽蓝色区域 = 按钮）
                regions.sort(key=lambda x: -x[2])
                best_s, best_e, _ = regions[0]

                region_mask = blue_mask.copy()
                region_mask[:best_s] = False
                region_mask[best_e:] = False
                ry, rx = np.where(region_mask)
                if len(rx) == 0:
                    continue

                return (int(rx.mean()) + cx1, int(ry.mean()) + cy1)

            return None

        except Exception:
            return None
