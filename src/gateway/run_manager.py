import asyncio
import uuid
import logging
from typing import Dict, Optional
from pydantic import BaseModel, Field
from datetime import datetime

logger = logging.getLogger(__name__)


class ClientRun(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    status: str = "running"
    created_at: datetime = Field(default_factory=datetime.now)
    # asyncio Task reference — allows abort_run() to actually cancel the coroutine,
    # not just update the status dict (which previously did nothing to the running task).
    task: Optional[asyncio.Task] = Field(default=None, exclude=True)


class RunManager:
    def __init__(self):
        # 正在活跃的运行任务 run_id -> ClientRun
        # Active running tasks: run_id -> ClientRun
        self.active_runs: Dict[str, ClientRun] = {}
        # 为了防止一个 Session 跑多个 Run，我们加个反向索引 session_id -> run_id
        # Reverse index to prevent a Session from running multiple Runs: session_id -> run_id
        self.session_to_run: Dict[str, str] = {}

    def create_run(self, session_id: str, task: Optional[asyncio.Task] = None) -> ClientRun:
        """为 Session 创建一个新的运行任务"""
        # 如果该 Session 已经有正在运行的任务，理论上应该先中止（Rooster 逻辑）
        # If the Session already has a running task, abort it first (Rooster logic)
        if session_id in self.session_to_run:
            old_run_id = self.session_to_run[session_id]
            self.abort_run(old_run_id)

        run = ClientRun(session_id=session_id, task=task)
        self.active_runs[run.run_id] = run
        self.session_to_run[session_id] = run.run_id
        logger.info(f"Created Run {run.run_id} for Session {session_id}")
        return run

    def register_task(self, run_id: str, task: asyncio.Task):
        """Attach an asyncio Task to an existing run (call after task creation)."""
        if run_id in self.active_runs:
            self.active_runs[run_id].task = task

    def abort_run(self, run_id: str):
        """中止一个任务 — cancels the underlying asyncio Task if one is registered."""
        if run_id in self.active_runs:
            run = self.active_runs[run_id]
            # Actually cancel the coroutine so it stops consuming LLM quota
            if run.task is not None and not run.task.done():
                run.task.cancel()
                logger.info(f"Cancelled asyncio Task for Run {run_id}")
            run.status = "aborted"
            if run.session_id in self.session_to_run:
                del self.session_to_run[run.session_id]
            del self.active_runs[run_id]
            logger.info(f"Aborted Run {run_id}")

    def complete_run(self, run_id: str):
        """标记任务完成"""
        if run_id in self.active_runs:
            run = self.active_runs[run_id]
            if run.session_id in self.session_to_run:
                del self.session_to_run[run.session_id]
            del self.active_runs[run_id]
            logger.info(f"Completed Run {run_id}")

    def abort_all(self) -> list:
        """Abort all active runs (global kill-switch). Returns list of aborted run IDs."""
        run_ids = list(self.active_runs.keys())
        for run_id in run_ids:
            self.abort_run(run_id)
        if run_ids:
            logger.warning(f"[GlobalKill] Aborted all runs: {run_ids}")
        return run_ids


# 全局单例
# Global singleton
global_run_manager = RunManager()
