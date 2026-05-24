"""
src/utils/security/input_guard.py

工具参数输入安全守卫。
在 tool_dispatch 执行工具前对参数进行轻量级扫描，记录可疑模式。

设计原则：
- 告知，不阻止（默认行为）：返回 findings，由调用方决定是否拦截
- 只有 severity=critical 的发现默认阻止（如路径遍历尝试）
- severity=high 在 CONFIRMATION_BEHAVIOR=block 时阻止
- severity=low/medium 只记录日志，不影响执行
- 不扫描 code/script 类参数（交由 CodeSafety 专门处理）
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 不扫描命令注入的字段名（这些字段本身就是代码/脚本）
_CODE_FIELDS = {"code", "script", "command", "expression", "query", "sql", "template", "content", "body", "text"}

# 命令注入特征（不在 code 字段中检测）
_CMD_INJECTION_PATTERNS = re.compile(
    r"(?:"
    r"(?<![a-zA-Z0-9])[;&|`](?![a-zA-Z0-9])"  # ; & | ` (不在标识符内)
    r"|\$\([^)]*\)"  # $(...)
    r"|\$\{[^}]*\}"  # ${...}
    r"|(?:^|\s)(?:rm|del|format|mkfs|dd\s+if)\s"  # 危险命令
    r")"
)

# 路径遍历模式
_PATH_TRAVERSAL = re.compile(r"(?:\.\.[/\\]|[/\\]\.\.)")

# 允许的 URL scheme（默认）
_DEFAULT_ALLOWED_SCHEMES = {"http", "https", "ftp", "ftps", "file"}


@dataclass
class GuardFinding:
    """单个安全发现。"""

    field: str
    pattern: str
    severity: str  # low | medium | high | critical
    message: str
    value_preview: str = ""  # 前 60 字符（用于日志）

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] field={self.field!r} pattern={self.pattern!r}: {self.message}"


@dataclass
class GuardReport:
    """InputGuard 扫描报告。"""

    tool_name: str
    findings: List[GuardFinding] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)

    @property
    def has_high(self) -> bool:
        return any(f.severity == "high" for f in self.findings)

    @property
    def is_clean(self) -> bool:
        return len(self.findings) == 0

    def summary(self) -> str:
        if self.is_clean:
            return f"[InputGuard] {self.tool_name}: clean"
        return f"[InputGuard] {self.tool_name}: {len(self.findings)} finding(s) — " + "; ".join(
            str(f) for f in self.findings
        )


class InputGuard:
    """
    工具参数输入守卫（单例）。

    用法：
        report = InputGuard.get().scan_args(tool_name, args_dict)
        if report.has_critical:
            # block
        elif report.findings:
            # log warning
    """

    _instance: Optional["InputGuard"] = None

    def __init__(
        self,
        allowed_url_schemes: Optional[set] = None,
        blocked_url_domains: Optional[set] = None,
        max_arg_length: int = 50_000,
    ):
        self._allowed_schemes = allowed_url_schemes or _DEFAULT_ALLOWED_SCHEMES
        self._blocked_domains = blocked_url_domains or set()
        self._max_arg_length = max_arg_length

    @classmethod
    def get(cls) -> "InputGuard":
        """懒加载单例，从 settings 读取配置。"""
        if cls._instance is None:
            try:
                from utils.config import settings

                schemes_raw = getattr(settings, "ALLOWED_URL_SCHEMES", "http,https")
                schemes = {s.strip().lower() for s in schemes_raw.split(",") if s.strip()}
                domains_raw = getattr(settings, "BLOCKED_URL_DOMAINS", "")
                domains = {d.strip().lower() for d in domains_raw.split(",") if d.strip()}
            except Exception:
                schemes = _DEFAULT_ALLOWED_SCHEMES
                domains = set()
            cls._instance = cls(allowed_url_schemes=schemes, blocked_url_domains=domains)
        return cls._instance

    def scan_args(self, tool_name: str, args: dict) -> GuardReport:
        """扫描工具参数，返回安全发现报告。"""
        report = GuardReport(tool_name=tool_name)
        if not args:
            return report

        for field_name, value in args.items():
            if not isinstance(value, str) or not value:
                continue

            # 参数长度检查
            if len(value) > self._max_arg_length:
                report.findings.append(
                    GuardFinding(
                        field=field_name,
                        pattern="arg_too_long",
                        severity="medium",
                        message=f"Argument length {len(value)} exceeds limit {self._max_arg_length}",
                        value_preview=value[:60],
                    )
                )
                continue  # 超长参数不继续扫描（性能）

            field_lower = field_name.lower()

            # 路径类参数：检测路径遍历
            if any(x in field_lower for x in ("path", "file", "dir", "folder", "dest", "src", "target")):
                self._check_path(field_name, value, report)

            # URL 类参数：检测 scheme 和域名
            if any(x in field_lower for x in ("url", "uri", "endpoint", "link", "href", "src")):
                self._check_url(field_name, value, report)

            # 非代码参数：检测命令注入特征
            if field_lower not in _CODE_FIELDS:
                self._check_cmd_injection(field_name, value, report)

        return report

    def _check_path(self, field_name: str, value: str, report: GuardReport) -> None:
        """路径遍历检测。"""
        if _PATH_TRAVERSAL.search(value):
            # 进一步验证：是否真正逃出了起始目录
            try:
                resolved = os.path.normpath(value)
                if resolved.startswith("..") or (".." + os.sep) in resolved:
                    report.findings.append(
                        GuardFinding(
                            field=field_name,
                            pattern="path_traversal",
                            severity="critical",
                            message=f"Path traversal detected: '{value[:60]}'",
                            value_preview=value[:60],
                        )
                    )
                    return
            except Exception:
                pass
            # 模式出现但未真正逃出，降级为 high
            report.findings.append(
                GuardFinding(
                    field=field_name,
                    pattern="path_traversal_pattern",
                    severity="high",
                    message=f"Path traversal pattern detected: '{value[:60]}'",
                    value_preview=value[:60],
                )
            )

    def _check_url(self, field_name: str, value: str, report: GuardReport) -> None:
        """URL scheme 和域名检测。"""
        try:
            parsed = urlparse(value)
            scheme = parsed.scheme.lower()
            if scheme and scheme not in self._allowed_schemes:
                report.findings.append(
                    GuardFinding(
                        field=field_name,
                        pattern="url_scheme",
                        severity="high",
                        message=f"URL scheme '{scheme}' not in allowed list {self._allowed_schemes}",
                        value_preview=value[:60],
                    )
                )
            if self._blocked_domains and parsed.netloc:
                netloc = parsed.netloc.lower().split(":")[0]
                for blocked in self._blocked_domains:
                    if netloc == blocked or netloc.endswith("." + blocked):
                        report.findings.append(
                            GuardFinding(
                                field=field_name,
                                pattern="blocked_domain",
                                severity="high",
                                message=f"Domain '{netloc}' is in blocked list",
                                value_preview=value[:60],
                            )
                        )
                        break
        except Exception:
            pass

    def _check_cmd_injection(self, field_name: str, value: str, report: GuardReport) -> None:
        """命令注入特征检测（非代码字段）。"""
        match = _CMD_INJECTION_PATTERNS.search(value)
        if match:
            report.findings.append(
                GuardFinding(
                    field=field_name,
                    pattern="cmd_injection",
                    severity="medium",
                    message=f"Possible command injection pattern '{match.group()[:20]}' in non-code field",
                    value_preview=value[:60],
                )
            )
