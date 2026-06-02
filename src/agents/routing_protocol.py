"""
[Executor-first Gateway] 统一路由决策协议。

RouteDecision 贯穿 Phase 0→5，所有路由分支必须返回此结构，
避免后续各 Phase 各自发明一套状态枚举。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class RouteTarget(str, Enum):
    """路由目标 — 与 executor_first_gateway_roadmap.md 中的 target 枚举一一对应。"""

    TALK = "talk"
    DIRECT_EXECUTOR = "direct_executor"
    MISSION = "mission"
    BLOCK = "block"
    CLARIFY = "clarify"
    SCHEDULE = "schedule"


@dataclass
class RouteDecision:
    """
    统一路由决策。

    Attributes:
        target:         路由目标（RouteTarget 枚举值）
        confidence:     路由置信度 0.0-1.0
        reason:         人类可读的路由理由（日志/调试用）
        route_tag:      命中的 hard-route / tag；无则为 None
        llm_used:       本次 triage 是否调用了 LLM（用于监控 fallback 比例）
        requires_tools: 是否预期发生环境/工具交互（Phase 2 分流 SoloRunner/ExecutorEntry 用）
    """

    target: RouteTarget
    confidence: float = 1.0
    reason: str = ""
    route_tag: Optional[str] = None
    llm_used: bool = False
    requires_tools: bool = False
    preprocessors: List[str] = field(default_factory=list)
    """送达前需要经过的前置处理步骤（有序）。
    已知值: "reframe" | "short_circuit" | "clarify" | "download_redirect"
    空列表 = 直达目标，无前置处理。
    """

    # ---- 便捷工厂 ----

    @classmethod
    def talk(cls, reason: str = "short greeting / capability query", llm_used: bool = False, **kw) -> RouteDecision:
        return cls(target=RouteTarget.TALK, reason=reason, llm_used=llm_used, requires_tools=False, **kw)

    @classmethod
    def direct(cls, reason: str = "", llm_used: bool = False, confidence: float = 1.0, **kw) -> RouteDecision:
        return cls(
            target=RouteTarget.DIRECT_EXECUTOR,
            reason=reason or "clear actionable task",
            llm_used=llm_used,
            requires_tools=True,
            confidence=confidence,
            **kw,
        )

    @classmethod
    def mission(cls, reason: str = "", llm_used: bool = False, **kw) -> RouteDecision:
        return cls(target=RouteTarget.MISSION, reason=reason or "complex multi-step task", llm_used=llm_used, requires_tools=True, **kw)

    @classmethod
    def block(cls, reason: str = "safety policy", llm_used: bool = False, **kw) -> RouteDecision:
        return cls(target=RouteTarget.BLOCK, reason=reason, llm_used=llm_used, **kw)

    @classmethod
    def clarify(cls, reason: str = "ambiguity detected", llm_used: bool = False, **kw) -> RouteDecision:
        return cls(target=RouteTarget.CLARIFY, reason=reason, llm_used=llm_used, **kw)

    @classmethod
    def schedule(cls, reason: str = "recurring task", llm_used: bool = False, **kw) -> RouteDecision:
        return cls(target=RouteTarget.SCHEDULE, reason=reason, llm_used=llm_used, requires_tools=False, **kw)
