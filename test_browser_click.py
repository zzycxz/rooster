import asyncio
from playwright.async_api import async_playwright

async def run_test():
    print("=== Rooster 浏览器点击稳健性测试 ===")
    print("正在启动 Playwright 无头浏览器...\n")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        # 构造一个极端的测试页面：
        # 1. 按钮非常透明 (opacity: 0.01)
        # 2. 按钮上方有一个完全遮挡的透明悬浮层 (z-index很高)
        # 这种场景下，Playwright 默认的 actionability check (可见性、可交互性) 必败，并抛出 timeout
        html_content = """
        <html>
            <body>
                <button id="target" onclick="document.getElementById('result').innerText='SUCCESS! 成功点击!'" style="position: absolute; top: 50px; left: 50px; opacity: 0.01;">
                    隐藏的按钮
                </button>
                <div style="position: absolute; top: 0; left: 0; width: 100vw; height: 100vh; background: transparent; z-index: 1000;">
                    这是透明遮挡层
                </div>
                <div id="result" style="margin-top: 150px; font-weight: bold; color: red;">Not Clicked (未点击)</div>
            </body>
        </html>
        """
        await page.set_content(html_content)
        
        button = page.locator("#target")
        result = page.locator("#result")
        
        print("【阶段 1：模拟旧版代码 (标准 click)】")
        print("预期：Playwright 会等待元素稳定（无遮挡），最终超时报错。")
        try:
            # 缩短超时时间以便快速演示
            await button.click(timeout=2000)
            print("  -> [警告] 常规点击居然成功了？(不符合预期)")
        except Exception as e:
            print(f"  -> [通过] 常规点击失败，捕获到预期的错误: {str(e).splitlines()[0]}")
            
        print("\n【阶段 2：模拟第一级降级 (force=True)】")
        print("预期：跳过可交互性检查强制派发事件，但有时仍可能因严格遮挡或复杂 CSS 失效。")
        try:
            await button.click(timeout=2000, force=True)
            text = await result.inner_text()
            print(f"  -> [结果] force=True 点击完成。当前状态: {text}")
        except Exception as e:
            print(f"  -> [失败] force=True 也失败了: {str(e).splitlines()[0]}")
            
        # 重置状态
        await page.evaluate("document.getElementById('result').innerText='Not Clicked (未点击)'")
        
        print("\n【阶段 3：模拟第二级降级 (JS 原生 Click)】")
        print("预期：直接注入 JS 在 DOM 层触发 click 事件，最高优先级，无视任何渲染阻碍。")
        try:
            await button.evaluate("el => el.click()")
            text = await result.inner_text()
            if "SUCCESS" in text:
                print(f"  -> [通过] JS 原生点击成功！状态更新为: {text}")
            else:
                print(f"  -> [失败] 状态未按预期更新: {text}")
        except Exception as e:
            print(f"  -> [失败] JS 点击失败: {str(e).splitlines()[0]}")
            
        await browser.close()
        print("\n=== 测试完成 ===")

if __name__ == "__main__":
    asyncio.run(run_test())
