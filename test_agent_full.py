import asyncio
import os
import sys
import logging
from dotenv import load_dotenv

# Ensure src directory is in path before importing
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

# Load environment before importing tools
load_dotenv(".env.local")

from agents.runners.mission_runner import MissionRunner
from models.factory import ModelFactory
from toolset.registry import global_tool_registry
from gateway.event_handler import AgentEventHandler
from utils.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

class PrintEventHandler(AgentEventHandler):
    def __init__(self):
        super().__init__(broadcast_callback=lambda *a, **kw: None)

    async def emit_assistant_delta(self, *args, **kwargs): pass
    async def emit_tool_call(self, *args, **kwargs): pass
    async def emit_tool_response(self, *args, **kwargs): pass
    async def emit_lifecycle(self, status, message, **kwargs):
        print(f"[{status}] {message}")
    async def emit_error(self, *args, **kwargs):
        print(f"[ERROR] {kwargs.get('message')}")
    async def emit_audit_verdict(self, verdict, reason, task_id, **kwargs):
        print(f"⚖️ [Auditor Verdict] {verdict} - {reason}")
    async def emit_subtask_start(self, subtask, **kwargs):
        print(f"🚀 [Start Subtask] {subtask.instruction}")
    async def emit_subtask_complete(self, subtask, report, **kwargs):
        print(f"✅ [Complete Subtask] {subtask.instruction}")

async def main():
    print(f"Current Audit Strictness: {settings.AUDIT_STRICTNESS}")
    prompt = "帮我对比下华为显卡和pro 6000的性能"
    
    active_provider = None
    for p in ["zhipu", "mimo", "cloud", "openai", "jiutian", "local"]:
        if getattr(settings, f"{p.upper()}_KEY", "") or p == "local":
            active_provider = p
            break
            
    llm_client = ModelFactory.get_client(active_provider)
    if not llm_client:
        print("No LLM client configured!")
        sys.exit(1)

    runner = MissionRunner(
        event_handler=PrintEventHandler(),
        llm_client=llm_client,
        tool_registry=global_tool_registry,
        session_id="test_mission_001"
    )
    
    print(f"Starting Mission: {prompt}")
    await runner.run_mission(prompt=prompt)
    print("Mission Finished.")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
