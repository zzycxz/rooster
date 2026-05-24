import asyncio
import os
import subprocess
import tempfile
from typing import Optional, Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool

try:
    from e2b_code_interpreter import CodeInterpreter

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
            "Execution kernel: 'e2b' (cloud sandbox, default when E2B_API_KEY is set) or "
            "'local' (host subprocess — requires AST safety pass or user confirmation). "
            "Use 'local' only when code needs local filesystem, system APIs, or desktop access."
        ),
        default="e2b",
    )


class PythonInterpreterTool(BaseTool):
    """Python code interpreter: E2B cloud sandbox (default) or local subprocess with safety gate."""

    name: str = "python_interpreter"
    kit: str = "Interpreter"
    description: str = (
        "Execute Python code for data analysis, plotting, calculations, file operations, or automation scripts. "
        "Default: E2B cloud sandbox (isolated, safe). "
        "Set kernel='local' for code that needs local filesystem, system APIs, or desktop access. "
        "Local execution requires passing AST safety analysis — dangerous operations (os.system, "
        "subprocess, file deletion, etc.) will be blocked or require user confirmation. "
        "[Bash equivalent] With kernel='local', this tool acts as Rooster's shell: use subprocess.run() "
        "to call CLI tools (git, ffmpeg, curl, pip, etc.), os/shutil for file management, "
        "or any system operation without needing a separate shell tool."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = InterpreterArgs

    async def run(self, **kwargs) -> str:
        code = kwargs.get("code")
        kernel = kwargs.get("kernel", "e2b")
        if not code:
            return "Error: No code provided."

        # E2B cloud sandbox: default path when API key is available
        if kernel == "e2b" or (os.getenv("E2B_API_KEY") and kernel != "local"):
            if not E2B_AVAILABLE:
                return "Error: E2B SDK is not installed. Run: pip install e2b-code-interpreter"
            return await self._run_e2b(code)

        # Local execution: AST safety gate (cannot be bypassed by string tricks)
        allow_local = os.getenv("INTERPRETER_ALLOW_LOCAL", "false").lower() == "true"
        if not allow_local:
            safety_error = _check_code_safety(code)
            if safety_error:
                return f"Error: {safety_error}"

        return await self._run_local(code)

    async def _run_local(self, code: str) -> str:
        """Local subprocess execution, no Docker overhead."""
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
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=45.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return "Error: Python execution timed out (45s). Code may contain infinite loop."

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
        """E2B 云端沙箱执行，带超时保护。"""
        api_key = os.getenv("E2B_API_KEY")
        if not api_key:
            return "E2B error: E2B_API_KEY not set."

        def execute_sync():
            with CodeInterpreter(api_key=api_key) as sandbox:
                execution = sandbox.notebook.exec_cell(code)
                if execution.error:
                    return f"Error: {execution.error.name}\n{execution.error.value}\n{execution.error.traceback}"
                return execution.text_output or "Execution successful (no output)."

        try:
            return await asyncio.wait_for(asyncio.to_thread(execute_sync), timeout=90.0)
        except asyncio.TimeoutError:
            return "Error: E2B execution timed out (90s)."
        except Exception as e:
            return f"E2B execution failed: {e}"
