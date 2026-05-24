"""
scripts/daily_weather.py
定时天气推送入口，供 Windows Task Scheduler 调用。
用法：python scripts/daily_weather.py
"""
import asyncio
import os
import sys

# 确保 src 在 path 中
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from dotenv import load_dotenv
load_dotenv(os.path.join(ROOT, ".env"))
load_dotenv(os.path.join(ROOT, ".env.local"), override=True)

PROMPT = "帮我搜索今天北京的天气预报，整理成中文摘要（最高温、最低温、天气状况、风力），然后通过飞书发送给我。"


async def run():
    from agents.router import Router
    router = Router.get_instance()
    await router.memory_manager.initialize_async()

    from channels.feishu import FeishuChannel
    from channels.registry import ChannelRegistry
    feishu = FeishuChannel(channel_id="feishu")
    ChannelRegistry.get_instance().register(feishu)

    from channels.base import InboundMessage

    open_id = os.getenv("FEISHU_USER_OPEN_ID", "")
    if not open_id:
        print("❌ FEISHU_USER_OPEN_ID 未配置，无法发送。", flush=True)
        return

    msg = InboundMessage(
        sender_id=open_id,
        text=PROMPT,
        channel_id="feishu",
        session_id=f"scheduled_{open_id}",
    )
    await router.handle_inbound(msg, feishu)
    print("✅ 天气任务已触发。", flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(run())
