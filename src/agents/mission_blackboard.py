"""
src/agents/mission_blackboard.py

[Sub-Agent Coordination Layer]

MissionBlackboard 是单次任务（mission_id）范围内所有子代理共享的协调原语。
它解决了以下经典多代理问题：

1. 发现广播（Discovery Broadcasting）
   ST1 在执行中途发现 "API 鉴权需要 Bearer Token"，ST2/ST3 立即可见，无需 depends_on 关系。

2. 进度共享（Progress Sharing）
   每个子代理实时更新自己的当前步骤和意图，其他子代理可感知"谁在干什么"，避免重复或冲突。

3. 文件级资源锁（File-level Resource Locking）
   两个并行子代理不会同时写同一个文件。写前 try_lock_resource，写后 release_resource。

4. 竞速组管理（Race Group Management）
   RACE 模式下，同 race_group 的子代理并发执行，第一个成功的宣告获胜，
   MissionRunner 据此取消其余兄弟任务。

设计原则：
- 纯内存，asyncio.Lock 保证并发安全
- 无副作用（不写文件，不调 LLM）
- 每个 mission_id 持有独立实例，由 MissionRunner 创建并传递
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_FACT_MAX_LEN = 400  # 单条 fact 在 context 注入时的最大字符数 / Max chars per fact in context injection
_CONTEXT_MAX_FACTS = 10  # 最多注入多少条 fact（防止上下文爆炸） / Max facts to inject (prevent context explosion)


class MissionBlackboard:
    """
    Per-mission 子代理协调黑板。
    由 MissionRunner 在 run() 入口创建，通过 AgentRunConfig.blackboard 传递给每个子代理。
    """

    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self._lock = asyncio.Lock()

        # 事实共享：key -> {"value", "author", "timestamp"}
        # Fact sharing: key -> {"value", "author", "timestamp"}
        self._facts: Dict[str, Dict] = {}

        # 文件/资源锁：resource_key -> owner_subtask_id
        # File/Resource lock: resource_key -> owner_subtask_id
        self._resource_locks: Dict[str, str] = {}

        # 子代理进度：subtask_id -> {"status", "step", "intent", "updated_at"}
        # Sub-agent progress: subtask_id -> {"status", "step", "intent", "updated_at"}
        self._progress: Dict[str, Dict] = {}

        # 竞速组获胜者：race_group -> winning_subtask_id
        # Race group winner: race_group -> winning_subtask_id
        self._race_winners: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # 发现广播
    # Discovery Broadcasting
    # ------------------------------------------------------------------

    async def post_fact(self, key: str, value: Any, author: str) -> None:
        """子代理 author 向黑板广播一条发现/事实。"""
        async with self._lock:
            self._facts[key] = {
                "value": value,
                "author": author,
                "timestamp": datetime.now().isoformat(),
            }
        logger.debug(f"[Blackboard:{self.mission_id}] {author} → fact '{key}'")

    async def get_fact(self, key: str) -> Optional[Any]:
        """读取一条 fact 的值，不存在返回 None。"""
        async with self._lock:
            entry = self._facts.get(key)
            return entry["value"] if entry else None

    def get_context_snapshot(self, for_subtask: str) -> str:
        """
        生成当前黑板状态的文本快照，用于注入子代理的 ReAct 循环。
        只注入其他子代理的发现（不注入自己发布的），避免噪音。
        """
        lines: list[str] = []

        # 其他代理的发现（只取最新的 N 条，时间倒序）
        # Other agents' discoveries (only latest N entries, reverse chronological)
        other_facts = [(k, v) for k, v in self._facts.items() if v["author"] != for_subtask]
        if other_facts:
            # 按时间倒序，取最新 N 条
            # Reverse chronological, take latest N
            other_facts.sort(key=lambda x: x[1]["timestamp"], reverse=True)
            lines.append("📋 [Shared Discoveries from Peer Agents]:")
            for k, v in other_facts[:_CONTEXT_MAX_FACTS]:
                val_str = str(v["value"])[:_FACT_MAX_LEN]
                lines.append(f"  [{v['author']}] {k}: {val_str}")

        # 当前正在运行的其他子代理意图（帮助协调，避免重复）
        # Currently running other sub-agent intents (help coordination, avoid duplication)
        running_peers = {
            sid: p for sid, p in self._progress.items() if sid != for_subtask and p.get("status") == "running"
        }
        if running_peers:
            lines.append("⚡ [Currently Running Peer Agents]:")
            for sid, p in running_peers.items():
                intent_str = p.get("intent", "working...")[:120]
                lines.append(f"  [{sid}] Step {p.get('step', '?')}: {intent_str}")

        if not lines:
            return ""

        return "[MISSION SHARED CONTEXT]\n" + "\n".join(lines) + "\n[END SHARED CONTEXT]"

    # ------------------------------------------------------------------
    # 进度追踪
    # Progress Tracking
    # ------------------------------------------------------------------

    async def update_progress(
        self,
        subtask_id: str,
        status: str,
        step: int = 0,
        intent: str = "",
    ) -> None:
        """
        子代理广播自己的当前状态。
        status: "running" | "done" | "failed" | "waiting"
        intent: 本步骤的简短意图描述（如工具名称或任务片段）
        """
        async with self._lock:
            self._progress[subtask_id] = {
                "status": status,
                "step": step,
                "intent": intent[:120],
                "updated_at": datetime.now().isoformat(),
            }

    def get_progress_summary(self) -> str:
        """返回所有子代理进度的格式化摘要（用于日志和 Dashboard）。"""
        if not self._progress:
            return "(no progress data)"
        lines = []
        for sid, p in self._progress.items():
            status = p.get("status", "?")
            step = p.get("step", 0)
            intent = p.get("intent", "")
            emoji = {"running": "⚡", "done": "✅", "failed": "❌", "waiting": "⏳"}.get(status, "•")
            lines.append(f"  {emoji} [{sid}] step={step} {intent}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 文件/资源锁
    # File/Resource Locking
    # ------------------------------------------------------------------

    async def try_lock_resource(self, resource: str, owner: str) -> bool:
        """
        尝试为 owner 申请 resource 的独占锁。
        resource 通常是文件路径（规范化后），也可以是 URL、API 名称等。
        返回 True 表示锁定成功，False 表示已被其他代理占用。
        """
        async with self._lock:
            current = self._resource_locks.get(resource)
            if current is not None and current != owner:
                logger.warning(
                    f"[Blackboard:{self.mission_id}] Resource conflict: "
                    f"'{resource}' locked by '{current}', '{owner}' must wait"
                )
                return False
            self._resource_locks[resource] = owner
            logger.debug(f"[Blackboard:{self.mission_id}] '{owner}' locked '{resource}'")
            return True

    async def wait_for_resource(
        self,
        resource: str,
        owner: str,
        poll_interval: float = 0.5,
        timeout: float = 30.0,
    ) -> bool:
        """
        轮询等待 resource 释放，直到获取锁或超时。
        返回 True 表示最终获取成功，False 表示超时。
        """
        import asyncio as _asyncio

        start = _asyncio.get_event_loop().time()
        while True:
            if await self.try_lock_resource(resource, owner):
                return True
            if _asyncio.get_event_loop().time() - start > timeout:
                logger.error(f"[Blackboard:{self.mission_id}] '{owner}' timed out waiting for resource '{resource}'")
                return False
            await _asyncio.sleep(poll_interval)

    async def release_resource(self, resource: str, owner: str) -> None:
        """释放 owner 持有的 resource 锁。"""
        async with self._lock:
            if self._resource_locks.get(resource) == owner:
                del self._resource_locks[resource]
                logger.debug(f"[Blackboard:{self.mission_id}] '{owner}' released '{resource}'")

    # ------------------------------------------------------------------
    # 竞速组管理
    # Race Group Management
    # ------------------------------------------------------------------

    async def declare_race_winner(self, race_group: str, winner_id: str) -> bool:
        """
        RACE 模式：winner_id 宣告自己是 race_group 中的第一个完成者。
        返回 True 表示成功登记为获胜者（此前无人获胜），False 表示已有其他获胜者。
        """
        async with self._lock:
            if race_group not in self._race_winners:
                self._race_winners[race_group] = winner_id
                logger.info(f"[Blackboard:{self.mission_id}] 🏁 RACE winner: '{winner_id}' in group '{race_group}'")
                return True
            existing = self._race_winners[race_group]
            logger.debug(
                f"[Blackboard:{self.mission_id}] RACE: '{winner_id}' arrived late, "
                f"'{existing}' already won group '{race_group}'"
            )
            return False

    def get_race_winner(self, race_group: str) -> Optional[str]:
        """查询 race_group 的当前获胜者（None 表示尚无人获胜）。"""
        return self._race_winners.get(race_group)

    def has_race_winner(self, race_group: str) -> bool:
        return race_group in self._race_winners
