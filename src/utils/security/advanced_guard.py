"""
src/utils/security/advanced_guard.py

Round 7 — 高级安全防护模块（默认 OFF）

通过环境变量 ADVANCED_SECURITY=true 启用全部防护。
各子检测器可独立开启：
  GUARD_JAILBREAK=true      # 用户输入越狱检测
  GUARD_PROMPT_INJECTION=true  # 工具返回内容注入检测
  GUARD_SKILL_VERIFY=true   # Skill 包投毒检测

设计原则：
- 检测到 critical → 阻断并给出清晰提示（不是静默失败）
- 检测到 high/medium → 在内容前插入警告标注，继续执行
- 所有检测均可 degrade 降级（出错时默认放行，不因安全模块崩溃阻断用户）
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置（懒加载，避免循环导入）
# Configuration (lazy-loaded, avoid circular import)
# ---------------------------------------------------------------------------


def _is_enabled(feature: str) -> bool:
    """检查某个安全特性是否启用。按以下优先级：
    1. 专项开关（如 GUARD_JAILBREAK）
    2. 主开关 ADVANCED_SECURITY
    """
    try:
        from utils.config import settings

        master = getattr(settings, "ADVANCED_SECURITY", "false").lower() in ("true", "1", "yes")
        specific = getattr(settings, feature, "").lower()
        if specific == "true":
            return True
        if specific == "false":
            return False
        return master
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class ThreatFinding:
    category: str  # jailbreak | prompt_injection | skill_poison | exfiltration
    severity: str  # critical | high | medium | low
    description: str
    evidence: str  # 触发的具体文本片段（截断至 120 字符）
    # Specific text snippet that triggered detection (truncated to 120 chars)


@dataclass
class AdvancedGuardReport:
    threats: List[ThreatFinding] = field(default_factory=list)

    @property
    def has_threats(self) -> bool:
        return bool(self.threats)

    @property
    def highest_severity(self) -> str:
        order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        if not self.threats:
            return "none"
        return max(self.threats, key=lambda t: order.get(t.severity, 0)).severity

    @property
    def should_block(self) -> bool:
        return self.highest_severity == "critical"

    def to_user_message(self) -> str:
        """生成面向用户的警告/阻断说明（不暴露内部规则细节）。"""
        if self.should_block:
            return "⚠️ 检测到可能的越狱尝试，无法执行该请求。\n如果您的请求是合法的，请换一种方式描述您的需求。"
        summaries = [f"[{t.severity.upper()}] {t.description}" for t in self.threats]
        return "⚠️ 安全提示：检测到可疑内容，已作标注，请谨慎参考。\n" + "\n".join(summaries)

    def to_warning_prefix(self) -> str:
        """用于在工具观测结果前插入的警告标注。"""
        summaries = [t.description for t in self.threats]
        return (
            "[⚠️ SECURITY WARNING: Potential prompt injection detected in tool output — "
            + "; ".join(summaries)
            + "]\n\n"
        )


# ---------------------------------------------------------------------------
# Jailbreak 检测器
# ---------------------------------------------------------------------------

# 中英文越狱模式（按严重度分组）
# Chinese & English jailbreak patterns (grouped by severity)
_JB_CRITICAL: List[re.Pattern] = [
    p
    for p in [
        # 明确指令覆盖
        # Explicit instruction override
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|guidelines?|rules?|prompt)", re.I),
        re.compile(r"disregard\s+(all\s+)?(your\s+)?(instructions?|guidelines?|rules?|training|prompt)", re.I),
        re.compile(r"override\s+(the\s+)?(system\s+prompt|your\s+instructions?|safety|restrictions?)", re.I),
        re.compile(r"your\s+(new\s+)?(system\s+prompt|instructions?|rules?)\s+(are|is)\s*[\:\n]", re.I),
        re.compile(r"new\s+system\s+prompt\s*[\:\n]", re.I),
        re.compile(r"forget\s+(everything|all|your)\s+.{0,30}(instructions?|training|guidelines?|rules?)", re.I),
        # 知名越狱关键词
        # Well-known jailbreak keywords
        re.compile(r"\bDAN\b", re.I),  # Do Anything Now
        re.compile(r"developer\s+mode", re.I),
        re.compile(r"jailbreak\s+mode", re.I),
        re.compile(r"no\s+restrictions?\s+mode", re.I),
        re.compile(r"god\s+mode\s+(?:enabled|activated|on)", re.I),
        re.compile(r"unrestricted\s+mode", re.I),
        # 中文越狱
        # Chinese jailbreak patterns
        re.compile(r"忽略.{0,10}(之前|上面|所有).{0,5}(指令|规则|限制|提示|约束)"),
        re.compile(r"(解除|取消|删除|移除).{0,10}(限制|约束|规则|安全)"),
        re.compile(r"现在你(没有|不受|无视).{0,10}(限制|约束|规则)"),
        re.compile(r"你的新(系统提示词?|指令|规则)"),
    ]
    if p
]

_JB_HIGH: List[re.Pattern] = [
    p
    for p in [
        # 角色扮演攻击
        # Role-play attacks
        re.compile(r"you\s+are\s+now\s+(?!helpful|an?\s+AI|a\s+language)", re.I),
        re.compile(r"act\s+as\s+(?:an?\s+)?(?!helpful|AI|assistant|expert)", re.I),
        re.compile(r"pretend\s+(to\s+be|you\s+are|you'?re)", re.I),
        re.compile(r"roleplay\s+as", re.I),
        re.compile(r"simulate\s+(?:being\s+)?(?:an?\s+)?(?!normal|simple)", re.I),
        re.compile(r"imagine\s+you\s+(?:have\s+no|are\s+free\s+from|without)", re.I),
        # 能力声明攻击
        # Capability declaration attacks
        re.compile(
            r"you\s+(?:can|are\s+able\s+to|are\s+allowed\s+to)\s+(?:now\s+)?(?:do|say|generate|create)\s+anything", re.I
        ),
        re.compile(r"you\s+have\s+no\s+(?:restrictions?|limitations?|rules?|filters?)", re.I),
        re.compile(r"without\s+(?:any\s+)?(?:restrictions?|limitations?|censorship|filters?)", re.I),
        # 中文角色扮演
        # Chinese role-play patterns
        re.compile(r"(你现在是|你是一个).{0,20}(没有限制|不受限制|可以做任何)"),
        re.compile(r"(假装|扮演|模拟).{0,10}你是.{0,20}(AI|助手|机器人)"),
        re.compile(r"你可以(做|说|生成|输出)任何(东西|内容)"),
    ]
    if p
]

_JB_MEDIUM: List[re.Pattern] = [
    p
    for p in [
        # 模糊越狱线索
        # Ambiguous jailbreak hints
        re.compile(r"hypothetically\s+speaking.*?(no\s+restrictions?|anything)", re.I | re.DOTALL),
        re.compile(
            r"for\s+(?:a\s+)?(?:creative\s+)?(?:writing\s+)?(?:fiction|story|game).+?\bno\s+restrictions?\b", re.I
        ),
        re.compile(r"as\s+a\s+(?:fictional|hypothetical|made[- ]up)\s+AI", re.I),
        re.compile(r"the\s+(?:previous|old|current)\s+instructions?\s+(?:were|are)\s+(?:wrong|incorrect|bad)", re.I),
        re.compile(r"sudo\s+(?:mode|override|prompt|access)", re.I),
    ]
    if p
]


class JailbreakDetector:
    """基于规则的越狱尝试检测器。"""

    def scan(self, text: str) -> AdvancedGuardReport:
        if not _is_enabled("GUARD_JAILBREAK"):
            return AdvancedGuardReport()

        report = AdvancedGuardReport()
        if not text or not isinstance(text, str):
            return report

        for pattern in _JB_CRITICAL:
            m = pattern.search(text)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="jailbreak",
                        severity="critical",
                        description=f"检测到指令覆盖尝试（模式: {pattern.pattern[:50]}）",
                        evidence=m.group(0)[:120],
                    )
                )

        for pattern in _JB_HIGH:
            m = pattern.search(text)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="jailbreak",
                        severity="high",
                        description=f"检测到角色扮演/能力声明越狱（模式: {pattern.pattern[:50]}）",
                        evidence=m.group(0)[:120],
                    )
                )

        for pattern in _JB_MEDIUM:
            m = pattern.search(text)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="jailbreak",
                        severity="medium",
                        description=f"检测到模糊越狱线索（模式: {pattern.pattern[:50]}）",
                        evidence=m.group(0)[:120],
                    )
                )

        if report.has_threats:
            logger.warning(
                f"[JailbreakDetector] {len(report.threats)} threat(s), "
                f"highest={report.highest_severity}, "
                f"evidence={report.threats[0].evidence!r}"
            )
        return report


# ---------------------------------------------------------------------------
# 工具输出注入检测器
# ---------------------------------------------------------------------------

_PI_HIGH: List[re.Pattern] = [
    p
    for p in [
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|guidelines?|prompt)", re.I),
        re.compile(r"disregard\s+(all\s+)?(your\s+)?(instructions?|guidelines?|training)", re.I),
        re.compile(r"\[SYSTEM\]\s*[:：]", re.I),
        re.compile(r"<\s*system\s*>", re.I),
        re.compile(r"\[INST\]|\[/?SYS\]", re.I),
        re.compile(r"your\s+new\s+(task|instructions?|goal|objective)\s+(is|are)\s*[\:\n]", re.I),
        re.compile(r"new\s+instructions?\s+from\s+(system|admin|operator)\s*[\:\n]", re.I),
    ]
    if p
]

_PI_MEDIUM: List[re.Pattern] = [
    p
    for p in [
        re.compile(r"you\s+are\s+now\s+(?!helpful|an?\s+AI)", re.I),
        re.compile(r"act\s+as\s+(?:an?\s+)?(?!helpful|AI|assistant)", re.I),
        re.compile(r"forget\s+(the\s+)?previous\s+(?:task|instructions?|context)", re.I),
        re.compile(r"STOP\s+what\s+you\s+(?:are|were)\s+doing", re.I),
        re.compile(r"instead\s+of\s+(?:the\s+)?(?:previous|above|current)\s+task", re.I),
    ]
    if p
]

# 不做工具输出扫描的工具（代码工具，输出就是代码，误报率高）
# Tools exempt from output scanning (code tools; output is code, high false-positive rate)
_PI_EXEMPT_TOOLS = {
    "python_interpreter",
    "code_exec",
    "terminal",
    "shell_exec",
    "run_script",
    "execute_script",
    "python_exec",
}


class PromptInjectionScanner:
    """检测工具返回内容中嵌入的 Prompt Injection 攻击。"""

    def scan(self, tool_name: str, result_text: str) -> AdvancedGuardReport:
        if not _is_enabled("GUARD_PROMPT_INJECTION"):
            return AdvancedGuardReport()

        report = AdvancedGuardReport()
        if tool_name in _PI_EXEMPT_TOOLS:
            return report
        if not result_text or not isinstance(result_text, str):
            return report

        for pattern in _PI_HIGH:
            m = pattern.search(result_text)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="prompt_injection",
                        severity="high",
                        description=f"工具返回内容含疑似指令注入（tool={tool_name}）",
                        evidence=m.group(0)[:120],
                    )
                )

        for pattern in _PI_MEDIUM:
            m = pattern.search(result_text)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="prompt_injection",
                        severity="medium",
                        description=f"工具返回内容含可疑角色覆盖指令（tool={tool_name}）",
                        evidence=m.group(0)[:120],
                    )
                )

        if report.has_threats:
            logger.warning(
                f"[PromptInjectionScanner] tool={tool_name}, "
                f"{len(report.threats)} threat(s), highest={report.highest_severity}"
            )
        return report


# ---------------------------------------------------------------------------
# Skill 投毒检测器
# ---------------------------------------------------------------------------

# 在 Skill 文件中检测的模式
# Patterns detected in Skill files
_SKILL_CRITICAL: List[re.Pattern] = [
    p
    for p in [
        # 描述/说明中夹带指令覆盖
        # Instruction override smuggled in descriptions
        re.compile(r"ignore\s+(all\s+)?(previous|prior|above|your)\s+(instructions?|guidelines?|rules?|prompt)", re.I),
        re.compile(r"override\s+(the\s+)?(system\s+prompt|your\s+instructions?|safety)", re.I),
        re.compile(r"your\s+(new\s+)?(system\s+prompt|instructions?)\s+(are|is)\s*[\:\n]", re.I),
        re.compile(r"disregard\s+(all\s+)?(your\s+)?(instructions?|guidelines?|training)", re.I),
        # 在 Skill body 中试图给 AI 新任务
        # Attempting to assign new tasks to AI within Skill body
        re.compile(
            r"when\s+(?:you\s+)?(?:use|execute|run)\s+this\s+skill.{0,50}(also|additionally|secretly)\s+(do|perform|execute)",
            re.I,
        ),
        re.compile(r"in\s+addition\s+to\s+(?:your\s+)?(?:main\s+)?task.{0,80}(send|upload|exfiltrate|transmit)", re.I),
        re.compile(r"忽略.{0,10}(之前|上面|所有).{0,5}(指令|规则|限制)"),
    ]
    if p
]

_SKILL_HIGH: List[re.Pattern] = [
    p
    for p in [
        # 代码块中的危险模式（混淆 + 执行）
        # Dangerous patterns in code blocks (obfuscation + execution)
        re.compile(r"exec\s*\(\s*(?:base64|__import__|compile)", re.I),
        re.compile(r"eval\s*\(\s*(?:base64|__import__|open|requests)", re.I),
        re.compile(r"__import__\s*\(\s*['\"](?:subprocess|os|sys|socket)['\"]", re.I),
        re.compile(r"base64\.b64decode\s*\(.{0,200}exec", re.I | re.DOTALL),
        # 外泄模式：读文件后立即请求网络
        # Exfiltration pattern: read file then immediately request network
        re.compile(r"open\s*\(.{0,100}requests?\.", re.I | re.DOTALL),
        # 隐藏注释中的指令
        # Hidden instructions in comments
        re.compile(r"<!--\s*(?:ignore|override|system\s*prompt|new\s*instructions?)", re.I),
        re.compile(r"\[//\]:\s*#\s*\((?:ignore|override|system)", re.I),
    ]
    if p
]

_SKILL_MEDIUM: List[re.Pattern] = [
    p
    for p in [
        # 可疑的超长 base64（可能是混淆载荷）
        # Suspiciously long base64 (possible obfuscated payload)
        re.compile(r"[A-Za-z0-9+/]{200,}={0,2}"),
        # 直接要求系统权限的描述
        # Descriptions directly requesting system permissions
        re.compile(r"(?:run|execute|perform).{0,30}(?:as\s+admin|with\s+sudo|elevated\s+privileges?)", re.I),
    ]
    if p
]


class SkillVerifier:
    """Skill 包投毒检测器：在加载/安装 SKILL.md 时扫描内容安全性。"""

    def verify(self, skill_path: str, content: str) -> AdvancedGuardReport:
        if not _is_enabled("GUARD_SKILL_VERIFY"):
            return AdvancedGuardReport()

        report = AdvancedGuardReport()
        if not content:
            return report

        for pattern in _SKILL_CRITICAL:
            m = pattern.search(content)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="skill_poison",
                        severity="critical",
                        description=f"Skill 文件含指令注入内容，拒绝加载: {skill_path}",
                        evidence=m.group(0)[:120],
                    )
                )

        for pattern in _SKILL_HIGH:
            m = pattern.search(content)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="skill_poison",
                        severity="high",
                        description=f"Skill 文件含可疑代码模式（可能的混淆执行/外泄）: {skill_path}",
                        evidence=m.group(0)[:120],
                    )
                )

        for pattern in _SKILL_MEDIUM:
            m = pattern.search(content)
            if m:
                report.threats.append(
                    ThreatFinding(
                        category="skill_poison",
                        severity="medium",
                        description=f"Skill 文件含可疑内容（超长编码/权限请求）: {skill_path}",
                        evidence=m.group(0)[:120],
                    )
                )

        if report.has_threats:
            logger.warning(
                f"[SkillVerifier] skill={skill_path}, "
                f"{len(report.threats)} threat(s), highest={report.highest_severity}"
            )
        return report


# ---------------------------------------------------------------------------
# 统一入口（单例）
# Unified entry point (singleton)
# ---------------------------------------------------------------------------


class AdvancedGuard:
    """
    高级安全防护主控，统一入口。
    所有子检测器均在此协调，通过 ADVANCED_SECURITY 环境变量开关。

    用法：
        from utils.security.advanced_guard import AdvancedGuard
        report = AdvancedGuard.scan_user_message(text)
        report = AdvancedGuard.scan_tool_output(tool_name, result)
        report = AdvancedGuard.verify_skill(path, content)
    """

    _jailbreak = JailbreakDetector()
    _injection = PromptInjectionScanner()
    _skill = SkillVerifier()

    @classmethod
    def scan_user_message(cls, text: str) -> AdvancedGuardReport:
        """扫描用户输入。检测越狱尝试。调用方负责根据 should_block 决定行动。"""
        try:
            return cls._jailbreak.scan(text)
        except Exception as e:
            logger.debug(f"[AdvancedGuard] scan_user_message degraded: {e}")
            return AdvancedGuardReport()

    @classmethod
    def scan_tool_output(cls, tool_name: str, result_text: str) -> AdvancedGuardReport:
        """扫描工具返回内容。检测 Prompt Injection 攻击。"""
        try:
            return cls._injection.scan(tool_name, result_text)
        except Exception as e:
            logger.debug(f"[AdvancedGuard] scan_tool_output degraded: {e}")
            return AdvancedGuardReport()

    @classmethod
    def verify_skill(cls, skill_path: str, content: str) -> AdvancedGuardReport:
        """扫描 Skill 包内容。检测投毒/注入攻击。"""
        try:
            return cls._skill.verify(skill_path, content)
        except Exception as e:
            logger.debug(f"[AdvancedGuard] verify_skill degraded: {e}")
            return AdvancedGuardReport()
