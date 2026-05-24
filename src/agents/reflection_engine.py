# src/agents/reflection_engine.py
"""
P0 核心：工具级反思自愈引擎 (Tool-Level Reflection Engine)

职责：
- 当工具调用失败时，拦截异常并尝试"私有修复"
- 修复成功后静默返回结果，不向 LLM 暴露错误中间过程
- 修复失败后，将原始错误连同所有尝试记录一并返回给 LLM

支持的自愈策略：
1. ModuleNotFoundError  -> pip install + 重新导入
2. FileNotFoundError    -> fs_stat 探测路径 + 修正参数
3. PermissionError      -> 切换写入路径至 tmp 目录
4. 代码逻辑错误          -> 调用 LLM 重写代码并重试（Deferred，暂为基础版）
"""

import asyncio
import logging
import re
import sys
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

# Allowlist of packages the self-healing engine is permitted to install.
# Intentionally restrictive — LLM controls the error message that drives pip install;
# an unrestricted allowlist is an arbitrary-package-installation vulnerability.
_ALLOWED_PACKAGES = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "pandas",
        "numpy",
        "scipy",
        "openpyxl",
        "xlrd",
        "xlwt",
        "pillow",
        "matplotlib",
        "beautifulsoup4",
        "lxml",
        "pydantic",
        "pyyaml",
        "toml",
        "python-docx",
        "pypdf2",
        "reportlab",
        "rich",
        "tqdm",
    }
)

# 单次错误的最大自愈尝试次数
# Max self-healing attempts per single error
MAX_REPAIR_ATTEMPTS = 3


class RepairBudgetExhausted(Exception):
    """修复配额耗尽，终止自愈并向上层汇报。"""

    pass


class ReflectionEngine:
    """
    工具调用级别的反思自愈引擎。
    应在 AgentExecutor._execute_tool 的 except 分支中被调用。
    """

    def __init__(self, tool_registry: Any):
        self.tool_registry = tool_registry
        # 记录每个 (tool_name, error_signature) 的修复次数，防止死循环
        # Track repair count per (tool_name, error_signature) to prevent infinite loops
        self._repair_ledger: dict[str, int] = {}

    def _repair_key(self, tool_name: str, error_type: str) -> str:
        return f"{tool_name}::{error_type}"

    def _increment_repair(self, key: str) -> int:
        self._repair_ledger[key] = self._repair_ledger.get(key, 0) + 1
        return self._repair_ledger[key]

    async def heal(
        self,
        tool_name: str,
        args: dict,
        error: Exception,
        retry_callable: Callable[..., Awaitable[str]],
    ) -> str:
        error_type = type(error).__name__
        key = self._repair_key(tool_name, error_type)

        attempt = self._increment_repair(key)
        if attempt > MAX_REPAIR_ATTEMPTS:
            raise RepairBudgetExhausted(
                f"[ReflectionEngine] 工具 '{tool_name}' 的 {error_type} 已耗尽 {MAX_REPAIR_ATTEMPTS} 次修复配额。"
            )

        logger.warning(
            f"[ReflectionEngine] Attempt {attempt}/{MAX_REPAIR_ATTEMPTS} | "
            f"Tool={tool_name} | Error={error_type}: {str(error)[:120]}"
        )

        # Audit trail
        import time as _time

        _audit_start = _time.monotonic()
        _original_args = dict(args)

        # 按错误类型路由修复策略
        # Route repair strategy by error type
        if isinstance(error, ModuleNotFoundError):
            return await self._repair_missing_module(tool_name, args, error, retry_callable)

        if isinstance(error, FileNotFoundError):
            return await self._repair_file_not_found(tool_name, args, error, retry_callable)

        if isinstance(error, PermissionError):
            return await self._repair_permission_error(tool_name, args, error, retry_callable)

        # 兜底：无法识别的错误类型，直接上报
        # Fallback: unrecognized error type, report directly
        logger.error(f"❌ [ReflectionEngine] 不可自愈的错误类型 {error_type}，终止自愈。")
        return f"[REFLECTION_FAILED] Tool={tool_name} | Error={error_type}: {str(error)}"

    # -------------------------------------------------------------------
    # 修复策略 1：依赖缺失 → pip 自动补装
    # Repair Strategy 1: Missing dependency → auto pip install
    # -------------------------------------------------------------------
    async def _repair_missing_module(
        self, tool_name: str, args: dict, error: ModuleNotFoundError, retry: Callable
    ) -> str:
        # 提取缺失的顶级包名（如 "No module named 'requests'" → "requests"）
        # Extract missing top-level package name
        match = re.search(r"No module named '([^'\.]+)", str(error))
        if not match:
            return f"[REFLECTION_FAILED] 无法解析缺失模块名: {error}"

        pkg_name = match.group(1)

        # Security: only install from the explicit allowlist.
        # The package name comes from an LLM-generated error message which could be injected.
        if pkg_name not in _ALLOWED_PACKAGES:
            logger.warning(
                f"🚫 [ReflectionEngine] 拒绝安装未在白名单中的包 '{pkg_name}'。"
                f"如需支持，请手动将其添加至 _ALLOWED_PACKAGES。"
            )
            return f"[REFLECTION_FAILED] Package '{pkg_name}' is not in the installation allowlist."

        logger.info(f"📦 [ReflectionEngine] 检测到缺失依赖 '{pkg_name}'，开始静默安装...")

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pip",
                "install",
                pkg_name,
                "--quiet",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)

            if proc.returncode != 0:
                err_detail = stderr.decode()[:200]
                return f"[REFLECTION_FAILED] pip install {pkg_name} 失败: {err_detail}"

            logger.info(f"✅ [ReflectionEngine] '{pkg_name}' 安装成功，正在重试工具调用...")
            return await retry(args)

        except asyncio.TimeoutError:
            return f"[REFLECTION_FAILED] pip install '{pkg_name}' 超时（>60s）。"
        except Exception as e:
            return f"[REFLECTION_FAILED] 安装过程崩溃: {e}"

    # -------------------------------------------------------------------
    # 修复策略 2：文件路径不存在 → 嗅探实际路径
    # Repair Strategy 2: File path not found → sniff actual path
    # -------------------------------------------------------------------
    async def _repair_file_not_found(
        self, tool_name: str, args: dict, error: FileNotFoundError, retry: Callable
    ) -> str:
        import os

        # 从错误消息或 args 中尝试提取路径
        # Extract path from error message or args
        bad_path = str(error).split(":")[-1].strip().strip("'\"")
        if not bad_path:
            return "[REFLECTION_FAILED] 无法从 FileNotFoundError 中解析路径。"

        # 尝试在工作目录下搜索同名文件 (depth-limited to 5 levels)
        # Search for same-name file in working directory (depth-limited to 5 levels)
        filename = os.path.basename(bad_path)
        logger.info(f"[ReflectionEngine] Path not found: '{bad_path}', searching for '{filename}'...")

        found_candidates = []
        _MAX_WALK_DEPTH = 5
        for root, dirs, files in os.walk("."):
            depth = root.count(os.sep)
            if depth >= _MAX_WALK_DEPTH:
                dirs.clear()
                continue
            for f in files:
                if f == filename:
                    found_candidates.append(os.path.abspath(os.path.join(root, f)))

        if not found_candidates:
            return f"[REFLECTION_FAILED] 文件 '{filename}' 在当前工作目录下不存在，且无法找到可用的替代路径。"

        # Use the first candidate, but limit walk depth to prevent unbounded traversal
        corrected_args = {
            k: (v.replace(bad_path, found_candidates[0]) if isinstance(v, str) else v) for k, v in args.items()
        }
        logger.info(f"[ReflectionEngine] Path corrected to '{found_candidates[0]}', retrying...")
        # Audit: log the path correction
        try:
            from utils.audit import audit_manager

            audit_manager.log_step_detail(
                "reflection",
                0,
                f"repair_path_{tool_name}.log",
                f"FileNotFound repair: '{bad_path}' -> '{found_candidates[0]}'\n"
                f"Original args: {args}\n"
                f"Corrected args: {corrected_args}",
            )
        except Exception:
            pass
        return await retry(corrected_args)

    # -------------------------------------------------------------------
    # 修复策略 3：权限错误 → 切换至 tmp 目录
    # Repair Strategy 3: Permission error → switch to tmp directory
    # -------------------------------------------------------------------
    async def _repair_permission_error(
        self, tool_name: str, args: dict, error: PermissionError, retry: Callable
    ) -> str:
        import os
        import tempfile

        # 将 args 中所有路径类参数重定向到临时目录
        # Redirect all path-type args to temp directory
        tmp_dir = tempfile.gettempdir()
        corrected_args = {}
        redirected = False

        for k, v in args.items():
            if isinstance(v, str) and (os.sep in v or "/" in v):
                filename = os.path.basename(v) or "output.tmp"
                new_path = os.path.join(tmp_dir, filename)
                corrected_args[k] = new_path
                redirected = True
                logger.info(f"🔀 [ReflectionEngine] 权限受限，路径已重定向: '{v}' → '{new_path}'")
            else:
                corrected_args[k] = v

        if not redirected:
            return "[REFLECTION_FAILED] PermissionError 但未找到可重定向的路径参数。"

        return await retry(corrected_args)
