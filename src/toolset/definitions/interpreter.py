import asyncio
import os
import subprocess
import tempfile
from typing import Optional, Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool

try:
    from e2b_code_interpreter import Sandbox as E2BSandbox

    E2B_AVAILABLE = True
except ImportError:
    E2B_AVAILABLE = False


def _check_code_safety(code: str) -> Optional[str]:
    """AST-based safety check. Returns error string if dangerous, else None."""
    from utils.code_safety import ast_safety_check

    safe, violations = ast_safety_check(code)
    if not safe:
        return (
            f"Security: code contains blocked operations: {', '.join(violations)}. "
            "Use kernel='local' explicitly if you trust this code and need local system access, "
            "or set INTERPRETER_ALLOW_LOCAL=true to bypass all checks."
        )
    return None


class InterpreterArgs(BaseModel):
    code: str = Field(description="The Python code to execute.")
    kernel: str = Field(
        description=(
            "Execution kernel: 'e2b' (cloud sandbox, requires E2B_API_KEY) or "
            "'local' (host subprocess — for local filesystem, system APIs, pip, CLI tools, desktop access). "
            "Prefer 'local' when the task involves local file creation, running CLI commands, or package installation."
        ),
        default="local",
    )


class PythonInterpreterTool(BaseTool):
    """Python code interpreter: local subprocess (default) or E2B cloud sandbox."""

    name: str = "python_interpreter"
    kit: str = "Interpreter"
    risk_level: str = "high"
    description: str = (
        "Execute Python code for data analysis, plotting, calculations, file operations, or automation scripts. "
        "Default: local subprocess (direct host access, safe with AST check). "
        "Set kernel='e2b' for isolated cloud sandbox execution when E2B_API_KEY is configured. "
        "[Bash equivalent] With kernel='local', this tool acts as Rooster's shell: use subprocess.run() "
        "to call CLI tools (git, ffmpeg, curl, pip, etc.), os/shutil for file management, "
        "or any system operation without needing a separate shell tool. "
        "For file creation tasks (PPT, Excel, Word, etc.), always use kernel='local' to save to local disk."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = InterpreterArgs

    async def run(self, **kwargs) -> str:
        code = kwargs.get("code")
        kernel = kwargs.get("kernel", "local")
        if not code:
            return "Error: No code provided."

        allow_local = os.getenv("INTERPRETER_ALLOW_LOCAL", "false").lower() == "true"

        # --- E2B cloud sandbox path ---
        # E2B_API_KEY 存储于 .env.local（不提交 git），由启动时统一加载。
        # 降级优先级：先检查 SDK 包可用性，再检查 API Key 是否存在。
        # 若任一条件不满足且 allow_local=true，自动 fallback 到本地执行，
        # 避免将错误字符串返回给 LLM（LLM 会误解为可执行指令）。
        if kernel == "e2b":
            if not E2B_AVAILABLE:
                # e2b-code-interpreter 包未安装 (pip install e2b-code-interpreter)
                if allow_local:
                    import logging as _logging

                    _logging.getLogger(__name__).warning(
                        "[Interpreter] e2b-code-interpreter SDK not installed, "
                        "auto-fallback to local kernel (INTERPRETER_ALLOW_LOCAL=true). "
                        "Run 'pip install e2b-code-interpreter' to enable E2B cloud sandbox."
                    )
                    kernel = "local"
                else:
                    return (
                        "Error: E2B SDK is not installed. "
                        "Run: pip install e2b-code-interpreter  OR  set kernel='local' for local execution."
                    )
            else:
                api_key = os.getenv("E2B_API_KEY", "").strip()
                if not api_key:
                    # SDK 已安装但 E2B_API_KEY 未在 .env.local 中配置
                    if allow_local:
                        import logging as _logging

                        _logging.getLogger(__name__).info(
                            "[Interpreter] E2B_API_KEY not set in .env.local, auto-fallback to local kernel."
                        )
                        kernel = "local"
                    else:
                        return "Error: E2B_API_KEY is not configured. Add it to .env.local or switch to kernel='local'."
                else:
                    return await self._run_e2b(code)

        # --- Local execution path ---
        if not allow_local:
            safety_error = _check_code_safety(code)
            if safety_error:
                return f"Error: {safety_error}"

        return await self._run_local(code)

    async def _run_local(self, code: str) -> str:
        """Local subprocess execution, no Docker overhead."""
        from utils.config import settings

        timeout_sec = getattr(settings, "INTERPRETER_TIMEOUT_SECONDS", 120.0)

        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8", delete=False, dir=tempfile.gettempdir()
        ) as tf:
            tf.write(code)
            tmp_file = tf.name
        try:
            strip_proxy = os.getenv("INTERPRETER_STRIP_PROXY", "false").lower() == "true"
            if strip_proxy:
                clean_env = {
                    k: v for k, v in os.environ.items() if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
                }
            else:
                clean_env = os.environ.copy()

            process = await asyncio.create_subprocess_exec(
                "python",
                tmp_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=clean_env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_sec)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return f"Error: Python execution timed out ({timeout_sec}s). Code may contain infinite loop."

            result = stdout.decode(encoding="utf-8", errors="replace")
            if stderr:
                err_text = stderr.decode(encoding="utf-8", errors="replace")
                result += f"\nStderr:\n{err_text}"
            return result or "Execution successful (no output)."

        except Exception as e:
            return f"Local execution failed: {e}"
        finally:
            if os.path.exists(tmp_file):
                os.remove(tmp_file)

    async def _run_e2b(self, code: str) -> str:
        """E2B 云端沙箱执行 (v2.x API: Sandbox)，带超时保护。"""
        from utils.config import settings

        timeout_sec = getattr(settings, "INTERPRETER_TIMEOUT_SECONDS", 120.0)

        api_key = os.getenv("E2B_API_KEY")
        if not api_key:
            return "E2B error: E2B_API_KEY not set."

        def execute_sync():
            sbx = E2BSandbox.create(api_key=api_key)
            try:
                execution = sbx.run_code(code)
                stdout = "".join(getattr(execution.logs, "stdout", []) or [])
                stderr = "".join(getattr(execution.logs, "stderr", []) or [])
                if execution.error:
                    err = execution.error
                    return f"Error: {getattr(err, 'name', 'ExecutionError')}\n{getattr(err, 'value', str(err))}"
                result = stdout.strip() or "Execution successful (no output)."
                if stderr.strip():
                    result += f"\nStderr:\n{stderr.strip()}"
                return result
            finally:
                sbx.kill()

        try:
            return await asyncio.wait_for(asyncio.to_thread(execute_sync), timeout=timeout_sec)
        except asyncio.TimeoutError:
            return f"Error: E2B execution timed out ({timeout_sec}s)."
        except Exception as e:
            return f"E2B execution failed: {e}"
