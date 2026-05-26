"""
src/utils/mcp_dynamic.py

[P3-1] MCP 工具动态注册 — 启动时拉取 MCP Server 暴露的工具列表，
并动态生成 BaseTool 子类注册到 ToolRegistry。

工作原理：
1. 从 .env / config 读取 MCP Server 端点列表
2. 通过 MCP utils/mcp_client.py 拉取 tools/list
3. 为每个 MCP 工具动态创建 BaseTool 子类（工厂模式）
4. 注册到 global_tool_registry

配置项（.env）：
  MCP_DYNAMIC_ENABLED=true
  MCP_SERVER_URLS=http://127.0.0.1:3000,http://127.0.0.1:3001
"""

import logging
import os
from typing import List, Optional, Dict, Any, Type

from toolset.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 动态工具工厂
# Dynamic tool factory
# --------------------------------------------------------------------------- #


def _make_mcp_tool_class(
    tool_name: str,
    description: str,
    parameters: Dict[str, Any],
    server_url: str,
    mcp_client_factory,
) -> Type[BaseTool]:
    """
    工厂函数：根据 MCP tool definition 动态生成 BaseTool 子类。
    避免在模块级别污染全局命名空间。
    """
    safe_name = tool_name.replace("-", "_").replace(".", "_")
    _params = parameters
    _server_url = server_url
    _mcp_factory = mcp_client_factory
    _description = description

    class _DynamicMCPTool(BaseTool):
        name: str = safe_name
        kit: str = "MCP"
        domain: str = "system"
        risk_level: str = "medium"
        reversible: bool = False
        description: str = _description

        @classmethod
        def get_schema(cls) -> Dict[str, Any]:
            return {
                "name": cls.name,
                "description": cls.description,
                "kit": cls.kit,
                "parameters": _params,
            }

        async def run(self, **kwargs) -> Any:
            try:
                client = _mcp_factory(_server_url)
                result = await client.call_tool(tool_name, kwargs)
                return ToolResult.success(str(result))
            except Exception as e:
                logger.error(f"[MCP DynTool] {tool_name} 调用失败: {e}")
                return ToolResult.error(f"MCP tool '{tool_name}' failed: {e}")

    _DynamicMCPTool.__name__ = f"MCPTool_{safe_name}"
    _DynamicMCPTool.__qualname__ = f"MCPTool_{safe_name}"
    return _DynamicMCPTool


# --------------------------------------------------------------------------- #
# 动态注册入口
# Dynamic registration entry point
# --------------------------------------------------------------------------- #


async def register_mcp_tools_from_servers(
    server_urls: Optional[List[str]] = None,
) -> int:
    """
    从 MCP Server 拉取工具列表并注册到 global_tool_registry。
    返回成功注册的工具数量。

    Args:
        server_urls: MCP 服务端点列表；为 None 时从 MCP_SERVER_URLS 环境变量读取。
    """
    if not os.getenv("MCP_DYNAMIC_ENABLED", "false").lower() == "true":
        logger.debug("[MCP Dynamic] MCP_DYNAMIC_ENABLED=false，跳过动态注册")
        return 0

    if server_urls is None:
        raw = os.getenv("MCP_SERVER_URLS", "")
        server_urls = [u.strip() for u in raw.split(",") if u.strip()]

    if not server_urls:
        logger.debug("[MCP Dynamic] 无 MCP Server 端点配置，跳过")
        return 0

    # 尝试导入 MCP Client
    try:
        from utils.system.mcp_client import MCPClient  # type: ignore

        mcp_factory = lambda url: MCPClient(server_url=url)
    except ImportError:
        logger.warning("[MCP Dynamic] utils.system.mcp_client 不可用，使用简化 HTTP 客户端")
        mcp_factory = _make_simple_mcp_client

    from toolset.registry import global_tool_registry

    registered_count = 0

    for url in server_urls:
        try:
            client = mcp_factory(url)
            tools_list = await _fetch_tool_list(client, url)
            for tool_def in tools_list:
                tool_name = tool_def.get("name", "")
                if not tool_name:
                    continue
                description = tool_def.get("description", f"MCP tool: {tool_name}")
                parameters = tool_def.get("inputSchema", {"type": "object", "properties": {}, "required": []})
                # 避免重复注册（同名工具跳过）
                # Skip duplicate registration (same-name tools)
                if tool_name.replace("-", "_") in global_tool_registry.list_tool_names():
                    logger.debug(f"[MCP Dynamic] 跳过已存在工具: {tool_name}")
                    continue

                tool_cls = _make_mcp_tool_class(tool_name, description, parameters, url, mcp_factory)
                global_tool_registry.register_tool(tool_cls)
                registered_count += 1
                logger.info(f"[MCP Dynamic] 已注册 MCP 工具: {tool_name} from {url}")
        except Exception as e:
            logger.warning(f"[MCP Dynamic] 从 {url} 拉取工具失败: {e}")

    logger.info(f"[MCP Dynamic] 动态注册完成，共注册 {registered_count} 个 MCP 工具")
    return registered_count


async def check_mcp_health() -> Dict[str, Any]:
    """Health check for configured MCP servers. Returns status per server."""
    from utils.config import settings

    if not settings.MCP_DYNAMIC_ENABLED:
        return {"enabled": False, "servers": []}

    raw = settings.MCP_SERVER_URLS
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    results = []

    for url in urls:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url.rstrip('/')}/health")
                results.append({"url": url, "ok": resp.status_code < 500, "status": resp.status_code})
        except Exception as e:
            results.append({"url": url, "ok": False, "error": str(e)[:100]})

    return {"enabled": True, "servers": results}


async def _fetch_tool_list(client, url: str) -> List[Dict[str, Any]]:
    """从 MCP Client 获取工具列表（兼容两种接口风格）。"""
    # 优先使用标准 list_tools 方法
    if hasattr(client, "list_tools"):
        result = await client.list_tools()
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("tools", [])
    # SSE fallback: POST /sse with JSON-RPC "tools/list"
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.post(
                f"{url.rstrip('/')}/sse",
                json={"jsonrpc": "2.0", "method": "tools/list", "id": 1},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code < 500:
                content_type = resp.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    # Parse SSE event stream: extract JSON from "data:" lines
                    data = None
                    for line in resp.text.split("\n"):
                        line = line.strip()
                        if line.startswith("data:"):
                            payload = line[len("data:"):].strip()
                            if payload:
                                import json as _json
                                data = _json.loads(payload)
                                break
                else:
                    data = resp.json()
                if isinstance(data, dict) and "result" in data:
                    tools = data["result"].get("tools", [])
                    if isinstance(tools, list):
                        return tools
    except Exception:
        pass  # SSE not supported, fall through
    # Final fallback: HTTP GET /tools/list
    return await _http_get_tools(url)


async def _http_get_tools(server_url: str) -> List[Dict[str, Any]]:
    """简化 HTTP fallback：GET /tools/list。"""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{server_url.rstrip('/')}/tools/list")
            resp.raise_for_status()
            data = resp.json()
            return data.get("tools", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.warning(f"[MCP Dynamic] HTTP GET {server_url}/tools/list 失败: {e}")
        return []


def _make_simple_mcp_client(server_url: str):
    """简化的 MCP HTTP Client（当 utils.system.mcp_client 不可用时）。"""

    class _SimpleMCPClient:
        def __init__(self, url):
            self._url = url

        async def call_tool(self, tool_name: str, args: dict) -> str:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as c:
                resp = await c.post(
                    f"{self._url.rstrip('/')}/tools/call",
                    json={"name": tool_name, "arguments": args},
                )
                resp.raise_for_status()
                return resp.text

        async def list_tools(self) -> List[Dict[str, Any]]:
            return await _http_get_tools(self._url)

    return _SimpleMCPClient(server_url)
