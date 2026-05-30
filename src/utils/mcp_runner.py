"""
src/utils/mcp_runner.py

MCP Server 进程管理器 — 通过 UV / npx 在隔离环境中启动本地 MCP Server，
并管理其完整生命周期（安装、启动、停止、健康检查、崩溃重启）。

架构设计：
- Python MCP Server → uv venv + uv pip install + uv run
- Node MCP Server   → npx -y (自带临时安装隔离)
- 进程信息持久化到 .rooster/mcp/registry.json，重启后自动恢复
"""

import asyncio
import json
import logging
import os
import shutil
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── MCP Server 定义结构 ────────────────────────────────────────────────


class MCPServerDef:
    """MCP Server 的安装定义（来自市场或用户配置）。"""

    def __init__(
        self,
        name: str,
        runtime: str,  # "python" | "node"
        command: str,  # 启动命令，如 "mcp-server-filesystem" 或 "@anthropic/mcp-server-github"
        packages: Optional[List[str]] = None,  # 需要安装的包
        args: Optional[List[str]] = None,  # 启动参数
        env_vars: Optional[Dict[str, str]] = None,  # 环境变量
        description: str = "",
        category: str = "utility",
        emoji: str = "🔌",
        author: str = "community",
        port: int = 0,  # 0 = auto-assign
    ):
        self.name = name
        self.runtime = runtime
        self.command = command
        self.packages = packages or []
        self.args = args or []
        self.env_vars = env_vars or {}
        self.description = description
        self.category = category
        self.emoji = emoji
        self.author = author
        self.port = port

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "runtime": self.runtime,
            "command": self.command,
            "packages": self.packages,
            "args": self.args,
            "env_vars": self.env_vars,
            "description": self.description,
            "category": self.category,
            "emoji": self.emoji,
            "author": self.author,
            "port": self.port,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MCPServerDef":
        return cls(**{k: v for k, v in d.items() if k in cls.__init__.__code__.co_varnames})


# ── 进程运行时状态 ────────────────────────────────────────────────────


class MCPServerInstance:
    """运行中的 MCP Server 实例。"""

    def __init__(self, server_def: MCPServerDef, base_dir: str):
        self.defn = server_def
        self.base_dir = base_dir  # .rooster/mcp/{name}/
        self.process: Optional[asyncio.subprocess.Process] = None
        self.status: str = "stopped"  # stopped | installing | starting | running | error
        self.url: str = ""
        self.pid: int = 0
        self.started_at: float = 0.0
        self.last_health_ok: float = 0.0
        self.error_message: str = ""
        self.restart_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.defn.name,
            "runtime": self.defn.runtime,
            "status": self.status,
            "url": self.url,
            "pid": self.pid,
            "started_at": self.started_at,
            "last_health_ok": self.last_health_ok,
            "error_message": self.error_message,
            "restart_count": self.restart_count,
            "emoji": self.defn.emoji,
            "description": self.defn.description,
            "category": self.defn.category,
            "author": self.defn.author,
        }


# ── 核心进程管理器 ────────────────────────────────────────────────────


class MCPRunner:
    """
    MCP Server 进程管理器。
    负责安装依赖、启动/停止进程、健康检查、崩溃重启。
    """

    MAX_RESTARTS = 3  # 最大连续重启次数
    RESTART_DELAY = 5.0  # 崩溃重启延迟（秒）
    HEALTH_INTERVAL = 30.0  # 健康检查间隔（秒）
    REGISTRY_FILE = "registry.json"  # 持久化文件名

    def __init__(self, rooster_dir: str):
        self._rooster_dir = rooster_dir
        self._mcp_base = os.path.join(rooster_dir, "mcp")
        self._instances: Dict[str, MCPServerInstance] = {}
        self._health_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        os.makedirs(self._mcp_base, exist_ok=True)

    # ── 公共 API ───────────────────────────────────────────────────

    async def install(self, server_def: MCPServerDef) -> Dict[str, Any]:
        """安装 MCP Server 依赖（UV venv + pip install / npx check）。"""
        name = server_def.name
        inst = self._get_or_create_instance(server_def)
        inst.status = "installing"
        inst.error_message = ""

        try:
            if server_def.runtime == "python":
                await self._install_python(inst)
            elif server_def.runtime == "node":
                await self._install_node(inst)
            else:
                raise ValueError(f"Unsupported runtime: {server_def.runtime}")

            # 持久化 server 定义
            self._save_registry()
            inst.status = "stopped"
            logger.info(f"[MCP Runner] Installed MCP server '{name}' ({server_def.runtime})")
            return {"ok": True, "name": name, "status": "installed"}

        except Exception as e:
            inst.status = "error"
            inst.error_message = str(e)[:200]
            logger.error(f"[MCP Runner] Install failed for '{name}': {e}")
            return {"ok": False, "name": name, "error": str(e)[:200]}

    async def start(self, name: str) -> Dict[str, Any]:
        """启动指定 MCP Server 进程。"""
        async with self._lock:
            inst = self._instances.get(name)
            if not inst:
                return {"ok": False, "error": f"MCP server '{name}' not found. Install it first."}
            if inst.status in ("running", "starting"):
                return {"ok": True, "name": name, "status": inst.status, "url": inst.url}

            return await self._start_process(inst)

    async def stop(self, name: str) -> Dict[str, Any]:
        """停止指定 MCP Server 进程。"""
        inst = self._instances.get(name)
        if not inst:
            return {"ok": False, "error": f"MCP server '{name}' not found."}

        await self._stop_process(inst)
        self._save_registry()
        return {"ok": True, "name": name, "status": "stopped"}

    async def uninstall(self, name: str) -> Dict[str, Any]:
        """卸载 MCP Server（停止进程 + 删除目录）。"""
        inst = self._instances.get(name)
        if inst:
            await self._stop_process(inst)
            self._instances.pop(name, None)

        # 删除安装目录
        server_dir = os.path.join(self._mcp_base, name)
        if os.path.exists(server_dir):
            shutil.rmtree(server_dir, ignore_errors=True)

        self._save_registry()
        logger.info(f"[MCP Runner] Uninstalled MCP server '{name}'")
        return {"ok": True, "name": name}

    async def restart(self, name: str) -> Dict[str, Any]:
        """重启 MCP Server。"""
        inst = self._instances.get(name)
        if not inst:
            return {"ok": False, "error": f"MCP server '{name}' not found."}

        await self._stop_process(inst)
        inst.restart_count = 0  # 手动重启重置计数器
        return await self._start_process(inst)

    def list_servers(self) -> List[Dict[str, Any]]:
        """列出所有已注册的 MCP Server 及其状态。"""
        return [inst.to_dict() for inst in self._instances.values()]

    def get_server(self, name: str) -> Optional[Dict[str, Any]]:
        """获取指定 MCP Server 的状态。"""
        inst = self._instances.get(name)
        return inst.to_dict() if inst else None

    def get_running_urls(self) -> List[str]:
        """获取所有正在运行的 MCP Server URL 列表。"""
        return [inst.url for inst in self._instances.values() if inst.status == "running" and inst.url]

    async def restore_all(self) -> int:
        """从 registry.json 恢复所有 MCP Server（自动重启之前在运行的）。"""
        registry = self._load_registry()
        count = 0
        for name, data in registry.items():
            server_def = MCPServerDef.from_dict(data.get("def", data))
            inst = self._get_or_create_instance(server_def)
            was_running = data.get("was_running", False)
            if was_running:
                try:
                    result = await self._start_process(inst)
                    if result.get("ok"):
                        count += 1
                except Exception as e:
                    logger.warning(f"[MCP Runner] Failed to restore '{name}': {e}")
        if count > 0:
            logger.info(f"[MCP Runner] Restored {count} MCP servers on startup")
        return count

    # ── 安装方法 ───────────────────────────────────────────────────

    async def _install_python(self, inst: MCPServerInstance):
        """使用 UV 创建隔离 venv 并安装 Python MCP Server 依赖。"""
        server_dir = inst.base_dir
        venv_dir = os.path.join(server_dir, ".venv")
        os.makedirs(server_dir, exist_ok=True)

        # 检测 UV 是否可用
        uv_cmd = await self._find_uv()
        if not uv_cmd:
            # 降级：使用系统 Python venv + pip
            logger.warning("[MCP Runner] UV not found, falling back to python -m venv + pip")
            await self._run_cmd(f'python -m venv "{venv_dir}"')
            pip_path = os.path.join(venv_dir, "Scripts" if os.name == "nt" else "bin", "pip")
            for pkg in inst.defn.packages:
                await self._run_cmd(f'"{pip_path}" install {pkg}')
        else:
            # UV 快速路径
            await self._run_cmd(f'{uv_cmd} venv "{venv_dir}" --python 3.12')
            for pkg in inst.defn.packages:
                await self._run_cmd(f'{uv_cmd} pip install --python "{venv_dir}" {pkg}')

        logger.info(f"[MCP Runner] Python venv created at {venv_dir}")

    async def _install_node(self, inst: MCPServerInstance):
        """检查 npx 是否可用（Node MCP Server 运行时无需预安装，npx -y 自动下载）。"""
        npx_cmd = await self._find_npx()
        if not npx_cmd:
            raise RuntimeError(
                "npx not found. Install Node.js (>=18) to use Node-based MCP servers. Download: https://nodejs.org/"
            )
        # npx -y 在首次 run 时自动安装，无需预先安装
        logger.info(f"[MCP Runner] npx found at {npx_cmd}, Node MCP server ready for lazy install")

    # ── 启动/停止 ──────────────────────────────────────────────────

    async def _start_process(self, inst: MCPServerInstance) -> Dict[str, Any]:
        """启动 MCP Server 子进程。"""
        inst.status = "starting"
        inst.error_message = ""

        try:
            if inst.defn.runtime == "python":
                cmd, env = self._build_python_cmd(inst)
            elif inst.defn.runtime == "node":
                cmd, env = self._build_node_cmd(inst)
            else:
                raise ValueError(f"Unsupported runtime: {inst.defn.runtime}")

            # 合并环境变量
            full_env = {**os.environ, **inst.defn.env_vars, **env}

            process = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                cwd=inst.base_dir,
            )

            inst.process = process
            inst.pid = process.pid
            inst.started_at = time.time()
            inst.restart_count += 1

            # 等待进程启动（读取 stderr/stdout 获取端口信息）
            url = await self._detect_url(inst)
            inst.url = url
            inst.status = "running"
            inst.last_health_ok = time.time()

            # 启动健康检查任务
            self._start_health_check(inst)

            # 启动崩溃监控
            asyncio.create_task(self._watch_process(inst))

            self._save_registry()
            logger.info(f"[MCP Runner] Started MCP server '{inst.defn.name}' (pid={inst.pid}, url={inst.url})")
            return {"ok": True, "name": inst.defn.name, "status": "running", "url": inst.url}

        except Exception as e:
            inst.status = "error"
            inst.error_message = str(e)[:200]
            logger.error(f"[MCP Runner] Failed to start '{inst.defn.name}': {e}")
            return {"ok": False, "name": inst.defn.name, "error": str(e)[:200]}

    async def _stop_process(self, inst: MCPServerInstance):
        """停止 MCP Server 子进程。"""
        # 停止健康检查
        task = self._health_tasks.pop(inst.defn.name, None)
        if task and not task.done():
            task.cancel()

        if inst.process and inst.process.returncode is None:
            try:
                inst.process.terminate()
                await asyncio.wait_for(inst.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                inst.process.kill()
                await inst.process.wait()
            except Exception as e:
                logger.warning(f"[MCP Runner] Error stopping '{inst.defn.name}': {e}")

        inst.process = None
        inst.pid = 0
        inst.status = "stopped"
        inst.url = ""

    def _build_python_cmd(self, inst: MCPServerInstance) -> tuple:
        """构建 Python MCP Server 启动命令。"""
        venv_dir = os.path.join(inst.base_dir, ".venv")
        if os.name == "nt":
            python_path = os.path.join(venv_dir, "Scripts", "python.exe")
        else:
            python_path = os.path.join(venv_dir, "bin", "python")

        # 如果 UV 可用，用 uv run 以确保环境隔离
        # 否则直接用 venv 中的 python
        cmd_parts = [f'"{python_path}"', "-m", inst.defn.command]
        if inst.defn.args:
            cmd_parts.extend(inst.defn.args)

        # 端口环境变量
        env = {}
        if inst.defn.port > 0:
            env["MCP_PORT"] = str(inst.defn.port)

        return " ".join(cmd_parts), env

    def _build_node_cmd(self, inst: MCPServerInstance) -> tuple:
        """构建 Node MCP Server 启动命令。"""
        cmd_parts = ["npx", "-y", inst.defn.command]
        if inst.defn.args:
            cmd_parts.extend(inst.defn.args)

        env = {}
        if inst.defn.port > 0:
            env["PORT"] = str(inst.defn.port)

        return " ".join(cmd_parts), env

    async def _detect_url(self, inst: MCPServerInstance, timeout: float = 8.0) -> str:
        """
        等待 MCP Server 启动，尝试检测其 URL。
        大多数 MCP Server 会在 stdout/stderr 中打印监听地址。
        """
        if not inst.process or not inst.process.stderr:
            return ""

        url = ""
        deadline = time.time() + timeout

        while time.time() < deadline:
            try:
                line = await asyncio.wait_for(inst.process.stderr.readline(), timeout=1.0)
                line = line.decode("utf-8", errors="replace").strip()
            except (asyncio.TimeoutError, Exception):
                break

            if not line:
                if inst.process.returncode is not None:
                    break
                continue

            # 检测 URL 模式
            for pattern in ["http://", "https://", "localhost:", "127.0.0.1:", "0.0.0.0:"]:
                idx = line.find(pattern)
                if idx >= 0:
                    # 提取 URL
                    url_part = line[idx:].split()[0]
                    if pattern in ("localhost:", "127.0.0.1:", "0.0.0.0:"):
                        url_part = f"http://{url_part}"
                    url = url_part.rstrip("/")
                    break

            if url:
                break

        # 如果没有从日志中检测到 URL，尝试默认端口
        if not url and inst.defn.port > 0:
            url = f"http://127.0.0.1:{inst.defn.port}"

        return url

    # ── 健康检查与崩溃恢复 ─────────────────────────────────────────

    def _start_health_check(self, inst: MCPServerInstance):
        """启动定期健康检查。"""
        name = inst.defn.name
        old_task = self._health_tasks.pop(name, None)
        if old_task and not old_task.done():
            old_task.cancel()
        self._health_tasks[name] = asyncio.create_task(self._health_loop(inst))

    async def _health_loop(self, inst: MCPServerInstance):
        """定期检查 MCP Server 健康状态。"""
        while inst.status == "running":
            await asyncio.sleep(self.HEALTH_INTERVAL)
            if inst.status != "running":
                break

            try:
                ok = await self._check_health(inst)
                if ok:
                    inst.last_health_ok = time.time()
                else:
                    logger.warning(f"[MCP Runner] Health check failed for '{inst.defn.name}'")
                    # 如果连续失败且未超过重启上限，自动重启
                    if inst.restart_count < self.MAX_RESTARTS:
                        logger.info(
                            f"[MCP Runner] Auto-restarting '{inst.defn.name}' (attempt {inst.restart_count + 1})"
                        )
                        await self._stop_process(inst)
                        await asyncio.sleep(self.RESTART_DELAY)
                        await self._start_process(inst)
                    else:
                        inst.status = "error"
                        inst.error_message = f"Exceeded max restarts ({self.MAX_RESTARTS})"
            except Exception as e:
                logger.warning(f"[MCP Runner] Health check error for '{inst.defn.name}': {e}")

    async def _check_health(self, inst: MCPServerInstance) -> bool:
        """HTTP 健康检查。"""
        if not inst.url:
            return False

        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{inst.url}/health")
                return resp.status_code < 500
        except Exception:
            # 尝试 SSE 端点
            try:
                import httpx

                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(
                        f"{inst.url}/sse",
                        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
                        headers={"Content-Type": "application/json"},
                    )
                    return resp.status_code < 500
            except Exception:
                return False

    async def _watch_process(self, inst: MCPServerInstance):
        """监控进程退出，自动重启。"""
        if not inst.process:
            return

        returncode = await inst.process.wait()
        if inst.status != "running":
            return  # 主动停止，不需要重启

        logger.warning(f"[MCP Runner] Process '{inst.defn.name}' exited with code {returncode}")

        # 如果崩溃且未超过重启上限，自动重启
        if inst.restart_count < self.MAX_RESTARTS:
            inst.status = "stopped"
            await asyncio.sleep(self.RESTART_DELAY)
            try:
                await self._start_process(inst)
            except Exception as e:
                logger.error(f"[MCP Runner] Auto-restart failed for '{inst.defn.name}': {e}")
        else:
            inst.status = "error"
            inst.error_message = f"Process crashed (exit={returncode}), exceeded max restarts"
            self._save_registry()

    # ── 工具方法 ───────────────────────────────────────────────────

    def _get_or_create_instance(self, server_def: MCPServerDef) -> MCPServerInstance:
        """获取或创建 MCP Server 实例。"""
        if server_def.name not in self._instances:
            server_dir = os.path.join(self._mcp_base, server_def.name)
            os.makedirs(server_dir, exist_ok=True)
            self._instances[server_def.name] = MCPServerInstance(server_def, server_dir)
        else:
            # 更新定义（可能市场版本更新了）
            self._instances[server_def.name].defn = server_def
        return self._instances[server_def.name]

    def _save_registry(self):
        """持久化 MCP Server 注册表。"""
        registry = {}
        for name, inst in self._instances.items():
            registry[name] = {
                "def": inst.defn.to_dict(),
                "was_running": inst.status == "running",
            }
        path = os.path.join(self._mcp_base, self.REGISTRY_FILE)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(registry, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"[MCP Runner] Failed to save registry: {e}")

    def _load_registry(self) -> Dict[str, Any]:
        """加载持久化的 MCP Server 注册表。"""
        path = os.path.join(self._mcp_base, self.REGISTRY_FILE)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"[MCP Runner] Failed to load registry: {e}")
            return {}

    @staticmethod
    async def _run_cmd(cmd: str, timeout: float = 120.0):
        """执行系统命令。"""
        logger.debug(f"[MCP Runner] Running: {cmd}")
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"Command timed out: {cmd[:100]}")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"Command failed (exit={proc.returncode}): {err}")

        return stdout.decode("utf-8", errors="replace")

    @staticmethod
    async def _find_uv() -> Optional[str]:
        """检测 UV 是否可用。"""
        for cmd in ("uv", "uv.exe"):
            try:
                proc = await asyncio.create_subprocess_shell(
                    f"{cmd} --version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
                if proc.returncode == 0:
                    return cmd
            except Exception:
                continue
        return None

    @staticmethod
    async def _find_npx() -> Optional[str]:
        """检测 npx 是否可用。"""
        for cmd in ("npx", "npx.cmd", "npx.exe"):
            try:
                proc = await asyncio.create_subprocess_shell(
                    f"{cmd} --version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5.0)
                if proc.returncode == 0:
                    return cmd
            except Exception:
                continue
        return None


# ── 全局单例 ──────────────────────────────────────────────────────────

_runner: Optional[MCPRunner] = None


def get_mcp_runner(rooster_dir: str = "") -> MCPRunner:
    """获取全局 MCPRunner 单例。"""
    global _runner
    if _runner is None:
        if not rooster_dir:
            # 默认使用 .rooster 目录
            rooster_dir = os.path.join(os.path.expanduser("~"), ".rooster")
        _runner = MCPRunner(rooster_dir)
    return _runner
