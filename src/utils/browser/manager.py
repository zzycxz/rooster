import asyncio
import os
import logging
from typing import Optional, Dict
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from bs4 import BeautifulSoup
import re

ID_INJECTION_JS = """
(elements) => {
    let index = 0;
    elements.forEach(tagName => {
        document.querySelectorAll(tagName).forEach(el => {
            el.setAttribute('data-rooster-id', index++);
        });
    });
}
"""


def html_to_markdown(html: str) -> str:
    """
    [V2.0] 智能 HTML→Markdown 管线。
    1. PruningContentFilter: 4 维评分提取主体内容区域
    2. TableExtractor: 表格专项提取为 markdown table
    3. markdownify: 转换为 markdown
    4. CitationLinkConverter: 内联链接 → 引用式格式
    """
    from utils.browser.pruner import PruningContentFilter, TableExtractor, CitationLinkConverter
    from markdownify import markdownify as md

    # Step 1: PruningContentFilter — 在 HTML 阶段过滤噪声
    content_filter = PruningContentFilter(min_word_threshold=5)
    fit_html = content_filter.filter(html)

    # Step 2: 提取表格
    fit_soup = BeautifulSoup(fit_html, "html.parser")
    tables = TableExtractor.extract_tables(fit_soup)

    # Step 3: 移除剩余噪声标签后转 markdown
    for tag in fit_soup(["script", "style", "nav", "footer", "header", "iframe", "noscript", "aside", "form"]):
        tag.decompose()
    markdown_text = md(str(fit_soup), heading_style="ATX", bullets="-")
    markdown_text = re.sub(r"\n{3,}", "\n\n", markdown_text).strip()

    # Step 4: 追加 markdownify 未捕获的表格
    if tables:
        missing_tables = []
        for t in tables:
            # 检查表格的核心内容是否已出现在 markdown 中
            # 取表格第二行（第一行数据行）作为特征
            lines = t.strip().split("\n")
            if len(lines) >= 3:
                signature = lines[2].strip()  # 第一行数据
                if signature and signature not in markdown_text:
                    missing_tables.append(t)
            else:
                missing_tables.append(t)
        if missing_tables:
            table_section = "\n\n### Extracted Tables\n\n" + "\n\n".join(missing_tables)
            markdown_text += table_section

    # Step 5: 引用式链接转换
    converted, references = CitationLinkConverter.convert(markdown_text)
    if references:
        converted += "\n\n" + references

    return converted


class BrowserManager:
    """
    Playwright 浏览器管理单例。
    负责浏览器的启动、页面分发与环境隔离。
    """

    _instance: Optional["BrowserManager"] = None
    _lock = asyncio.Lock()

    # --- UA 轮换池 ---
    _UA_POOL = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:137.0) Gecko/20100101 Firefox/137.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    ]

    # --- Per-domain 限速 ---
    _domain_last_request: Dict[str, float] = {}
    _DOMAIN_MIN_INTERVAL: float = 1.5  # 同域名最小间隔 1.5 秒

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None  # 默认常驻主页面
        self._semaphore: Optional[asyncio.Semaphore] = None

    @classmethod
    async def get_instance(cls) -> "BrowserManager":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def start(self, headless: bool = True):
        """启动浏览器与上下文"""
        if self.playwright is None:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=headless)

            from utils.config import settings

            # 初始化并发限制信号量 (默认 5，可从配置读取)
            concurrency_limit = int(os.getenv("BROWSER_MAX_PAGES", "5"))
            self._semaphore = asyncio.Semaphore(concurrency_limit)

            raw_proxy_url = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            bypass_list = os.getenv("NO_PROXY", "localhost,127.0.0.1").replace(" ", "")

            proxy = None
            if settings.ENABLE_REGIONAL_PROXY and raw_proxy_url:
                if not raw_proxy_url.startswith("http"):
                    raw_proxy_url = f"http://{raw_proxy_url}"
                proxy = {"server": raw_proxy_url, "bypass": bypass_list}
                logging.info(f"✅ 浏览器引擎已注入代理隧道: {raw_proxy_url}")

            self.context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                proxy=proxy,
                ignore_https_errors=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
            )
            # 预热主页面
            self.page = await self.context.new_page()

    async def get_page(self) -> Page:
        """获取主页面（主要用于交互式操作）"""
        if self.page is None:
            await self.start()
        return self.page

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def page_scope(self):
        """
        [并发核心]：异步上下文管理器。
        1. 抢占信号量令牌（流量控制）；
        2. 分发全新 Page 实例；
        3. 自动关闭并释放资源。
        """
        if self.context is None:
            await self.start()

        async with self._semaphore:
            page = await self.context.new_page()
            try:
                yield page
            finally:
                await page.close()

    async def close(self):
        """关闭所有浏览器资源"""
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None

    @classmethod
    async def restart_with_proxy(cls) -> "BrowserManager":
        """
        代理热切换：关闭当前浏览器实例并用最新 settings 重建。
        调用后调用方应重新通过 get_instance() 获取新实例。
        """
        async with cls._lock:
            if cls._instance is not None:
                try:
                    await cls._instance.close()
                except Exception:
                    pass
                cls._instance = None
            # 重建单例
            cls._instance = cls()
        logging.info("🔄 [BrowserManager] 代理配置已切换，浏览器上下文已重建。")
        return cls._instance

    @staticmethod
    def _get_browser_headers(url: str) -> Dict[str, str]:
        """生成仿真浏览器请求头（随机 UA + 完整 headers）"""
        import random

        ua = random.choice(BrowserManager._UA_POOL)
        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

    @staticmethod
    async def _wait_domain_limit(url: str):
        """Per-domain 限速：同域名请求间隔不低于 1.5 秒"""
        import time
        from urllib.parse import urlparse

        domain = urlparse(url).netloc
        if not domain:
            return
        now = time.time()
        last = BrowserManager._domain_last_request.get(domain, 0)
        elapsed = now - last
        if elapsed < BrowserManager._DOMAIN_MIN_INTERVAL:
            await asyncio.sleep(BrowserManager._DOMAIN_MIN_INTERVAL - elapsed)
        BrowserManager._domain_last_request[domain] = time.time()

    async def smart_fetch(self, url: str, timeout_httpx: float = 10.0) -> tuple[str, str]:
        """
        混合动力抓取引擎（反爬增强版）。
        1. UA 轮换 + 完整浏览器 headers
        2. Per-domain 限速（1.5s 间隔）
        3. 403/429 自动切换 Playwright
        4. 动态页面自动降级 Playwright
        """
        import httpx

        raw_html = ""
        method = "httpx"

        # Per-domain 限速
        await self._wait_domain_limit(url)

        try:
            from utils.config import settings

            proxy = settings.HTTP_PROXY if settings.ENABLE_REGIONAL_PROXY else None
            headers = self._get_browser_headers(url)

            async with httpx.AsyncClient(
                proxy=proxy, timeout=timeout_httpx, follow_redirects=True, verify=False, headers=headers
            ) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    raw_html = resp.text
                elif resp.status_code in (403, 429, 503):
                    # 反爬检测：直接跳到 Playwright
                    logging.warning(f"🔒 [smart_fetch] {url} 返回 {resp.status_code}，疑似反爬，切换 Playwright")
                    raw_html = ""
                else:
                    logging.debug(f"smart_fetch {url} HTTP {resp.status_code}")
                    raw_html = ""
        except Exception as e:
            logging.debug(f"smart_fetch httpx failed for {url}: {type(e).__name__}: {e}")
            raw_html = ""

        # 判定降级逻辑（使用增强的反爬签名检测）
        from utils.browser.pruner import needs_playwright_render

        needs_playwright = needs_playwright_render(raw_html)

        if needs_playwright:
            method = "playwright"
            try:
                async with self.page_scope() as page:
                    await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                    await asyncio.sleep(2)
                    raw_html = await page.content()
            except Exception as e:
                logging.error(f"smart_fetch Playwright fallback failed: {e}")

        return raw_html, method


class HTMLCleaner:
    @staticmethod
    def clean(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for s in soup(["script", "style", "nav", "footer", "header", "iframe"]):
            s.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", text)


def semantic_prune_markdown(markdown: str, max_chars: int = 8000) -> str:
    """语义剪枝：保留骨架，压缩长文本段落"""
    lines = markdown.split("\n")
    output = []
    in_code_block = False
    para_lines = []

    def flush():
        nonlocal para_lines
        if not para_lines:
            return
        text = "\n".join(para_lines).strip()
        if len(text) > 800:
            output.append(text[:300] + "\n\n[... content truncated for token efficiency ...]\n\n" + text[-300:])
        else:
            output.append(text)
        para_lines = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            in_code_block = not in_code_block
            output.append(line)
            continue
        if in_code_block or stripped.startswith("#"):
            flush()
            output.append(line)
            continue
        if not stripped:
            flush()
            continue
        para_lines.append(line)
    flush()

    res = "\n".join(output)
    return res[:max_chars] if len(res) > max_chars else res
