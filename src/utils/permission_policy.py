"""
src/utils/permission_policy.py

[P1-2] 工具级权限控制。

在工具执行前（事前）根据 risk_level 策略决定是否放行，
比 auditor 的事后审计更早一层拦截高风险操作。

策略配置（.rooster/permissions.yml 或 .env）：
  PERMISSION_POLICY = strict | balanced | permissive
  - strict:      risk >= medium 都需要确认（开发/审计场景）
  - balanced:    risk = high/critical 需要确认（默认）
  - permissive:  risk = critical 才需要确认（自动化场景）
"""

import logging
import os
from typing import Optional, Set, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 策略 → 需要确认的最低 risk_level
# Policy → minimum risk_level requiring confirmation
_POLICY_THRESHOLDS: Dict[str, int] = {
    "strict": 2,  # medium, high, critical
    # medium, high, critical
    "balanced": 3,  # high, critical（默认）
    # high, critical (default)
    "permissive": 4,  # critical only
}

_RISK_RANKS: Dict[str, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


@dataclass
class ToolPermissionPolicy:
    """
    工具权限策略对象，可在 AgentExecutor 初始化时注入。
    thread-safe: 只读属性 + 不可变集合。
    """

    policy: str = "balanced"  # strict / balanced / permissive
    blocked_tools: Set[str] = field(default_factory=set)  # 直接黑名单
    # Direct blocklist
    allowed_tools: Set[str] = field(default_factory=set)  # 白名单（非空时只允许白名单）
    # Allowlist (when non-empty, only allowlisted tools are permitted)

    @classmethod
    def from_env(cls) -> "ToolPermissionPolicy":
        """从环境变量加载策略配置。"""
        policy = os.getenv("PERMISSION_POLICY", "balanced").lower()
        if policy not in _POLICY_THRESHOLDS:
            logger.warning(f"[PermissionPolicy] 无效策略 '{policy}'，使用 'balanced'")
            policy = "balanced"

        blocked_raw = os.getenv("BLOCKED_TOOLS", "")
        allowed_raw = os.getenv("ALLOWED_TOOLS", "")
        blocked = {t.strip() for t in blocked_raw.split(",") if t.strip()}
        allowed = {t.strip() for t in allowed_raw.split(",") if t.strip()}
        return cls(policy=policy, blocked_tools=blocked, allowed_tools=allowed)

    def check(self, tool_name: str, risk_level: str = "low") -> "PolicyDecision":
        """
        检查工具是否允许执行。
        返回 PolicyDecision(allowed, reason)。
        """
        # 1. 白名单检查（白名单非空时只允许白名单内工具）
        # 1. Allowlist check (when non-empty, only allowlisted tools pass)
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return PolicyDecision(allowed=False, reason=f"Tool '{tool_name}' is not in the allowed-tools whitelist.")

        # 2. 黑名单检查
        # 2. Blocklist check
        if tool_name in self.blocked_tools:
            return PolicyDecision(allowed=False, reason=f"Tool '{tool_name}' is explicitly blocked by policy.")

        # 3. Risk-level 阈值检查
        # 3. Risk-level threshold check
        risk_rank = _RISK_RANKS.get(risk_level.lower(), 1)
        threshold = _POLICY_THRESHOLDS.get(self.policy, 3)
        if risk_rank >= threshold:
            return PolicyDecision(
                allowed=False,
                requires_confirmation=True,
                reason=(
                    f"Tool '{tool_name}' has risk_level='{risk_level}' "
                    f"which requires confirmation under '{self.policy}' policy."
                ),
            )

        return PolicyDecision(allowed=True, reason="ok")

    def blocks(self, tool_name: str) -> bool:
        """快速检查（无 risk level）——用于工具列表过滤。"""
        if self.allowed_tools and tool_name not in self.allowed_tools:
            return True
        return tool_name in self.blocked_tools


@dataclass
class PolicyDecision:
    """工具权限校验结果。"""

    allowed: bool
    reason: str = "ok"
    requires_confirmation: bool = False

    def to_error_message(self) -> str:
        if self.requires_confirmation:
            return (
                f"⚠️ PERMISSION CHECK: {self.reason}\nReply 'confirm' to allow this tool call, or 'cancel' to skip it."
            )
        return f"🚫 PERMISSION DENIED: {self.reason}"


# 全局策略单例（懒加载）
# Global policy singleton (lazy-loaded)
_global_policy: Optional[ToolPermissionPolicy] = None


def get_global_policy() -> ToolPermissionPolicy:
    """获取全局工具权限策略（单例，从环境变量初始化）。"""
    global _global_policy
    if _global_policy is None:
        _global_policy = ToolPermissionPolicy.from_env()
        logger.info(
            f"[PermissionPolicy] 策略加载完毕: policy={_global_policy.policy}, "
            f"blocked={_global_policy.blocked_tools or 'none'}"
        )
    return _global_policy


def make_sandboxed_policy() -> ToolPermissionPolicy:
    """创建 SANDBOXED 子代理专用的严格隔离策略。
    - 使用 strict 级别（medium 及以上风险需确认）
    - 预置高危工具黑名单，直接阻断代码执行、文件删除等危险操作
    """
    _SANDBOXED_BLOCKED = {
        "shell_exec",
        "terminal",
        "code_exec",
        "python_exec",
        "delete_file",
        "file_system_rm",
        "file_delete",
        "system_cmd",
        "run_command",
        "execute_script",
    }
    return ToolPermissionPolicy(policy="strict", blocked_tools=_SANDBOXED_BLOCKED)
