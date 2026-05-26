"""MCP Market API routes — marketplace, install, start, stop, uninstall, status."""

import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp", tags=["mcp"])

# Shared state — set by app.py during wiring
_rooster_dir: str = ""
_mcp_runner = None  # MCPRunner instance


class MCPInstallRequest(BaseModel):
    name: str
    runtime: str = "python"  # python | node
    command: str = ""
    packages: list = []
    args: list = []
    env_vars: Dict[str, str] = {}
    description: str = ""
    category: str = "utility"
    emoji: str = "🔌"
    author: str = "community"
    port: int = 0


class MCPActionRequest(BaseModel):
    name: str


def wire(rooster_dir: str):
    """Wire shared state into this router (called by app.py)."""
    global _rooster_dir, _mcp_runner
    _rooster_dir = rooster_dir
    # Lazy-init MCPRunner on first wire
    from utils.mcp_runner import get_mcp_runner
    _mcp_runner = get_mcp_runner(rooster_dir)


def _get_runner():
    if _mcp_runner is None:
        from utils.mcp_runner import get_mcp_runner
        return get_mcp_runner(_rooster_dir)
    return _mcp_runner


# ---------------------------------------------------------------------------
# MCP Community Market Registry (内置常用 MCP Server)
# MCP Community Market Registry (built-in popular MCP servers)
# ---------------------------------------------------------------------------

MCP_MARKET: Dict[str, Dict[str, Any]] = {
    "filesystem": {
        "name": "filesystem",
        "runtime": "python",
        "command": "mcp_server_filesystem",
        "packages": ["mcp-server-filesystem"],
        "args": [],
        "env_vars": {},
        "description": "Secure file system operations — read, write, search, and manage files and directories with configurable access controls.",
        "category": "system",
        "emoji": "📂",
        "author": "anthropic",
        "port": 0,
    },
    "github": {
        "name": "github",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-github",
        "packages": [],
        "args": [],
        "env_vars": {},
        "description": "GitHub API integration — manage repos, issues, PRs, branches, and code search directly from your agent.",
        "category": "development",
        "emoji": "🐙",
        "author": "anthropic",
        "port": 0,
    },
    "sqlite": {
        "name": "sqlite",
        "runtime": "python",
        "command": "mcp_server_sqlite",
        "packages": ["mcp-server-sqlite"],
        "args": [],
        "env_vars": {},
        "description": "SQLite database operations — create, query, and manage SQLite databases with natural language.",
        "category": "data",
        "emoji": "🗃️",
        "author": "anthropic",
        "port": 0,
    },
    "brave-search": {
        "name": "brave-search",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-brave-search",
        "packages": [],
        "args": [],
        "env_vars": {"BRAVE_API_KEY": ""},
        "description": "Brave Search API — perform web searches with privacy-first results and rich snippets.",
        "category": "search",
        "emoji": "🔍",
        "author": "anthropic",
        "port": 0,
    },
    "puppeteer": {
        "name": "puppeteer",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-puppeteer",
        "packages": [],
        "args": [],
        "env_vars": {},
        "description": "Browser automation via Puppeteer — navigate, screenshot, interact with web pages and SPA content.",
        "category": "automation",
        "emoji": "🎭",
        "author": "anthropic",
        "port": 0,
    },
    "memory": {
        "name": "memory",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-memory",
        "packages": [],
        "args": [],
        "env_vars": {},
        "description": "Knowledge graph memory — persistent entity-relation storage for long-term memory across conversations.",
        "category": "memory",
        "emoji": "🧠",
        "author": "anthropic",
        "port": 0,
    },
    "slack": {
        "name": "slack",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-slack",
        "packages": [],
        "args": [],
        "env_vars": {"SLACK_BOT_TOKEN": "", "SLACK_TEAM_ID": ""},
        "description": "Slack workspace integration — send messages, read channels, manage threads and reactions.",
        "category": "collaboration",
        "emoji": "💬",
        "author": "anthropic",
        "port": 0,
    },
    "fetch": {
        "name": "fetch",
        "runtime": "python",
        "command": "mcp_server_fetch",
        "packages": ["mcp-server-fetch"],
        "args": [],
        "env_vars": {},
        "description": "Web content fetcher — retrieve and parse web pages, APIs, and structured data with smart extraction.",
        "category": "utility",
        "emoji": "🌐",
        "author": "anthropic",
        "port": 0,
    },
    "git": {
        "name": "git",
        "runtime": "python",
        "command": "mcp_server_git",
        "packages": ["mcp-server-git"],
        "args": [],
        "env_vars": {},
        "description": "Git repository operations — commit, diff, log, branch, merge, and manage repositories.",
        "category": "development",
        "emoji": "🔀",
        "author": "anthropic",
        "port": 0,
    },
    "google-drive": {
        "name": "google-drive",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-google-drive",
        "packages": [],
        "args": [],
        "env_vars": {"GOOGLE_OAUTH_CLIENT_ID": "", "GOOGLE_OAUTH_CLIENT_SECRET": ""},
        "description": "Google Drive file access — search, read, and manage documents, sheets, and slides on Google Drive.",
        "category": "productivity",
        "emoji": "📁",
        "author": "anthropic",
        "port": 0,
    },
    "postgres": {
        "name": "postgres",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-postgres",
        "packages": [],
        "args": [],
        "env_vars": {"POSTGRES_CONNECTION_STRING": ""},
        "description": "PostgreSQL database connector — query, schema inspection, and data management for Postgres databases.",
        "category": "data",
        "emoji": "🐘",
        "author": "anthropic",
        "port": 0,
    },
    "sequential-thinking": {
        "name": "sequential-thinking",
        "runtime": "node",
        "command": "@modelcontextprotocol/server-sequential-thinking",
        "packages": [],
        "args": [],
        "env_vars": {},
        "description": "Sequential reasoning engine — step-by-step thinking, analysis, and decision-making with chain-of-thought.",
        "category": "analysis",
        "emoji": "🧩",
        "author": "anthropic",
        "port": 0,
    },
}


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@router.get("/market")
async def api_mcp_market():
    """列出 MCP 市场（内置 + 已安装状态）。"""
    runner = _get_runner()
    servers = runner.list_servers()
    installed_names = {s["name"] for s in servers}

    market_list = []
    for name, meta in MCP_MARKET.items():
        server_info = {
            "name": meta["name"],
            "emoji": meta["emoji"],
            "category": meta["category"],
            "description": meta["description"],
            "author": meta["author"],
            "runtime": meta["runtime"],
            "env_vars": meta.get("env_vars", {}),
            "installed": name in installed_names,
            "can_uninstall": True,
        }
        # 如果已安装，附带运行状态
        if name in installed_names:
            srv = next(s for s in servers if s["name"] == name)
            server_info["status"] = srv["status"]
            server_info["url"] = srv.get("url", "")
            server_info["pid"] = srv.get("pid", 0)
        market_list.append(server_info)

    return {"servers": market_list}


@router.get("/status")
async def api_mcp_status():
    """获取所有 MCP Server 的运行状态。"""
    runner = _get_runner()
    return {"servers": runner.list_servers()}


@router.get("/status/{name}")
async def api_mcp_server_status(name: str):
    """获取指定 MCP Server 的运行状态。"""
    runner = _get_runner()
    info = runner.get_server(name)
    if not info:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return info


@router.post("/install")
async def api_mcp_install(req: MCPInstallRequest):
    """安装 MCP Server（创建隔离环境 + 安装依赖）。"""
    runner = _get_runner()

    # 如果从市场安装，使用市场定义覆盖
    if req.name in MCP_MARKET and not req.command:
        meta = MCP_MARKET[req.name]
        req = MCPInstallRequest(
            name=meta["name"],
            runtime=meta["runtime"],
            command=meta["command"],
            packages=meta.get("packages", []),
            args=meta.get("args", []),
            env_vars=meta.get("env_vars", {}),
            description=meta.get("description", ""),
            category=meta.get("category", "utility"),
            emoji=meta.get("emoji", "🔌"),
            author=meta.get("author", "community"),
            port=meta.get("port", 0),
        )

    if not req.command:
        raise HTTPException(status_code=400, detail="Missing 'command' field for MCP server")

    from utils.mcp_runner import MCPServerDef

    server_def = MCPServerDef(
        name=req.name,
        runtime=req.runtime,
        command=req.command,
        packages=req.packages,
        args=req.args,
        env_vars=req.env_vars,
        description=req.description,
        category=req.category,
        emoji=req.emoji,
        author=req.author,
        port=req.port,
    )

    result = await runner.install(server_def)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Install failed"))
    return result


@router.post("/start")
async def api_mcp_start(req: MCPActionRequest):
    """启动 MCP Server。"""
    runner = _get_runner()
    result = await runner.start(req.name)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Start failed"))

    # 启动后自动注册 MCP 工具到 global_tool_registry
    if result.get("url"):
        try:
            from utils.mcp_dynamic import register_mcp_tools_from_servers

            count = await register_mcp_tools_from_servers([result["url"]])
            result["tools_registered"] = count
        except Exception as e:
            logger.warning(f"[MCP API] Tool registration failed: {e}")
            result["tools_registered"] = 0

    return result


@router.post("/stop")
async def api_mcp_stop(req: MCPActionRequest):
    """停止 MCP Server。"""
    runner = _get_runner()
    result = await runner.stop(req.name)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Stop failed"))
    return result


@router.post("/restart")
async def api_mcp_restart(req: MCPActionRequest):
    """重启 MCP Server。"""
    runner = _get_runner()
    result = await runner.restart(req.name)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Restart failed"))
    return result


@router.post("/uninstall")
async def api_mcp_uninstall(req: MCPActionRequest):
    """卸载 MCP Server（停止进程 + 删除安装目录）。"""
    runner = _get_runner()
    result = await runner.uninstall(req.name)
    if not result.get("ok"):
        raise HTTPException(status_code=500, detail=result.get("error", "Uninstall failed"))
    return result


@router.get("/health")
async def api_mcp_health():
    """MCP 子系统整体健康检查。"""
    runner = _get_runner()
    servers = runner.list_servers()
    running = [s for s in servers if s["status"] == "running"]
    errored = [s for s in servers if s["status"] == "error"]

    # 检测 UV 和 npx 可用性
    from utils.mcp_runner import MCPRunner

    uv_available = bool(await MCPRunner._find_uv())
    npx_available = bool(await MCPRunner._find_npx())

    return {
        "total": len(servers),
        "running": len(running),
        "errored": len(errored),
        "uv_available": uv_available,
        "npx_available": npx_available,
        "servers": servers,
    }
