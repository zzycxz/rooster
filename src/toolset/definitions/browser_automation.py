import logging
import asyncio
from typing import Type, Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from utils.browser.manager import BrowserManager, HTMLCleaner, ID_INJECTION_JS

logger = logging.getLogger(__name__)


class BrowserBaseArgs(BaseModel):
    pass


class BrowserNavArgs(BaseModel):
    url: str = Field(description="URL")


class BrowserActionArgs(BaseModel):
    index: int = Field(description="ID", default=0)


class BrowserScrollArgs(BaseModel):
    direction: str = Field(description="Dir", default="down")
    amount: int = Field(description="Amount", default=800)


class BrowserTypeArgs(BaseModel):
    index: int = Field(description="The data-rooster-id of the input element to type into.")
    text: str = Field(description="The text to type into the input field.")
    clear: bool = Field(description="Whether to clear the field before typing.", default=True)


class BrowserExtractLinksArgs(BaseModel):
    keyword: str = Field(
        description="Filter links by this keyword in their text or surrounding context (optional).", default=""
    )


class BrowserPaginationArgs(BaseModel):
    pass


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
    amount: Optional[int] = Field(default=800, description="[scroll] Scroll distance in pixels")


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
    description: str = (
        "Open a real browser and navigate to a URL. Returns the page content with clickable element IDs.\n"
        "Requires: url.\n"
        "Use when: you need to INTERACT with a web page — click, type, submit, login, download via browser.\n"
        "After navigating, use browser_act to interact with elements by their data-rooster-id.\n"
        "NOT for: just reading article text without interaction (use web_fetch instead, it's faster).\n"
        "Key difference from web_fetch: this launches a real browser, enables full interaction, but is slower."
    )
    domain: str = "UI"
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
    domain: str = "UI"
    args_schema: Type[BaseModel] = BrowserActionArgs

    async def run(self, **kwargs) -> str:
        manager = await BrowserManager.get_instance()
        page = await manager.get_page()
        index = kwargs.get("index", 0)
        element = page.locator(f'[data-rooster-id="{index}"]')
        if await element.count() == 0:
            await page.evaluate(ID_INJECTION_JS, ["button", "a"])
        await element.scroll_into_view_if_needed()
        try:
            await element.click(timeout=5000)
        except Exception as e:
            logger.warning(f"BrowserClickTool: Standard click failed, fallback to force=True. Error: {e}")
            try:
                await element.click(timeout=3000, force=True)
            except Exception as e2:
                logger.warning(f"BrowserClickTool: Force click failed, fallback to JS click. Error: {e2}")
                await element.evaluate("el => el.click()")
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
    domain: str = "UI"
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


class BrowserActTool(BrowserBaseTool):
    """[Round 10] Unified browser interaction macro: click, scroll, or type in one tool."""

    name: str = "browser_act"
    kit: str = "Browser"
    description: str = (
        "MANDATORY for all web page interaction tasks. "
        "Use this tool when the task requires clicking buttons, filling forms, logging in, "
        "navigating UI elements, or performing any action INSIDE a web browser. "
        "Actions: 'click' (click by data-rooster-id), 'scroll' (scroll page), 'type' (type text into input). "
        "All actions return the updated page content. "
        "Do NOT use web_search for tasks that require browser interaction."
    )
    domain: str = "UI"
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
            try:
                await element.click(timeout=5000)
            except Exception as e:
                logger.warning(f"BrowserActTool (click): Standard click failed, fallback to force=True. Error: {e}")
                try:
                    await element.click(timeout=3000, force=True)
                except Exception as e2:
                    logger.warning(f"BrowserActTool (click): Force click failed, fallback to JS click. Error: {e2}")
                    await element.evaluate("el => el.click()")
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
