"""Standalone Agent Runner for Scheduled Tasks

This script runs a single agent mission completely headless, taking the task prompt as an argument.
It is intended to be invoked by the OS scheduler (via schedule_runner.py).
Usage: python run_agent.py --prompt "Your task description"
"""

import os
import sys
import argparse
import asyncio
import logging

# Ensure project root is in sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
src_dir = os.path.join(project_root, "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(project_root, ".env.local"), override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - [AgentRunner] %(message)s")
logger = logging.getLogger(__name__)

async def run_mission(prompt: str):
    from agents.runners.mission_runner import MissionRunner
    from models.factory import ModelFactory
    from toolset.registry import ToolRegistry
    from utils.config import settings
    
    logger.info(f"🚀 Starting Agent Mission: {prompt[:100]}")
    
    # Initialize basic components
    active_provider = None
    for p in ["zhipu", "mimo", "cloud", "openai", "jiutian", "local"]:
        if getattr(settings, f"{p.upper()}_KEY", "") or p == "local":
            active_provider = p
            break
            
    llm_client = ModelFactory.get_client(active_provider)
    if not llm_client:
        logger.error("No LLM client could be initialized. Please check .env.local")
        sys.exit(1)
        
    from toolset.registry import global_tool_registry
    tool_registry = global_tool_registry.clone()
    
    from agents.executor import AgentExecutor, AgentRunConfig
    from agents.prompt_builder import PromptBuilder
    from gateway.event_handler import AgentEventHandler
    
    class DummyEventHandler(AgentEventHandler):
        def __init__(self):
            self._run_seq = {}
        async def emit_assistant_delta(self, *args, **kwargs): pass
        async def emit_tool_call(self, *args, **kwargs): pass
        async def emit_tool_response(self, *args, **kwargs): pass
        async def emit_lifecycle(self, *args, **kwargs): pass
        async def emit_error(self, *args, **kwargs):
            logger.error(f"Agent Error: {kwargs.get('message')}")
        async def emit_audit_verdict(self, *args, **kwargs): pass
        async def emit_subtask_start(self, *args, **kwargs): pass
        async def emit_subtask_complete(self, *args, **kwargs): pass
        
    executor = AgentExecutor(
        event_handler=DummyEventHandler(),
        llm_client=llm_client,
        tool_registry=tool_registry,
        prompt_builder=PromptBuilder()
    )
    
    config = AgentRunConfig(
        session_id="scheduled_agent_run",
        session_key="scheduled_agent_run",
        agent_id="scheduled_agent",
        prompt=prompt,
        workspace_dir=project_root,
        model=ModelFactory.get_default_model(active_provider),
        tool_registry=tool_registry,
        history=[]
    )
    
    try:
        logger.info("Executing plan...")
        report = await executor.run(config)
        
        logger.info("\n" + "="*50)
        logger.info("🎉 Mission Complete")
        logger.info("="*50)
        logger.info(report)
        logger.info("="*50)
        return report
        
    except Exception as e:
        logger.error(f"Mission failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a headless agent mission.")
    parser.add_argument("--prompt", type=str, required=True, help="The task for the agent to perform.")
    args = parser.parse_args()
    
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        
    asyncio.run(run_mission(args.prompt))
