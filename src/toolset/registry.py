import logging
from typing import Dict, List, Type, Any, Optional
from toolset.base import BaseTool, CURRENT_PLATFORM

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Rooster 工具注册中心。
    管理所有已加载的工具实例，并生成合并的 Schema 系统提示。
    """

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        self.context = context or {}
        self._tools: Dict[str, BaseTool] = {}

    def register_tool(self, tool_class: Type[BaseTool]):
        """
        Instantiate and register a tool class with schema validation.
        """
        # Schema validation: ensure required BaseTool contract
        _validate_tool_contract(tool_class)

        tool_instance = tool_class(context=self.context)
        self._tools[tool_instance.name] = tool_instance
        logger.debug(f"Registered Tool: {tool_instance.name}")

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """
        根据工具名称获取实例。
        """
        return self._tools.get(name)

    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """
        提供给 LLM 使用的完整工具表（内部格式）。
        """
        return [tool.get_schema() for tool in self._tools.values() if not getattr(tool, "fc_hidden", False)]

    def get_all_fc_schemas(self) -> List[Dict[str, Any]]:
        """
        [Phase2] 返回 OpenAI Function Calling 标准格式的工具列表。
        格式: [{"type": "function", "function": {"name", "description", "parameters"}}]
        [Round 9] 自动过滤 fc_hidden=True 的工具（内部协议工具或冗余工具）。
        """
        result = []
        for tool in self._tools.values():
            if getattr(tool, "fc_hidden", False):
                continue
            raw = tool.get_schema()
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": raw["name"],
                        "description": raw["description"],
                        "parameters": raw["parameters"],
                    },
                }
            )
        return result

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def clone(self) -> "ToolRegistry":
        """Create an independent copy of the tool registry for ISOLATED subagent isolation.
        Each tool instance is reconstructed with an independent context dict, sharing no mutable state.
        创建独立的工具注册表副本，用于 ISOLATED 子代理隔离执行。
        每个工具实例都重新构造，持有独立的 context 字典，不共享任何可变状态。
        """
        cloned = ToolRegistry(context=dict(self.context))
        for tool_instance in self._tools.values():
            try:
                cloned._tools[tool_instance.name] = type(tool_instance)(context=cloned.context)
            except Exception as e:
                logger.warning(f"[ToolRegistry.clone] Tool '{tool_instance.name}' clone failed, skipping: {e}")
                logger.warning(f"[ToolRegistry.clone] 工具 '{tool_instance.name}' 克隆失败，跳过: {e}")
        return cloned

    def get_tools_by_domain(self, domain: str) -> List[Dict[str, Any]]:
        """
        [RSA] 根据职能域 (Domain) 提取工具列表，用于向对应角色按需注入。
        """
        return [tool.get_schema() for tool in self._tools.values() if getattr(tool, "domain", "general") == domain and not getattr(tool, "fc_hidden", False)]

    def get_kit_names(self) -> List[str]:
        """Return deduplicated, sorted list of Kit names from all registered tools.
        返回所有已注册工具涉及的 Kit 名称列表（去重、已排序）。"""
        kits = {getattr(tool, "kit", "General") for tool in self._tools.values()}
        return sorted(kits)

    def get_tools_by_kit(self, kit_name: str) -> List[Dict[str, Any]]:
        """Return schema list of all tools under the specified Kit (filtering fc_hidden).
        返回指定 Kit 下所有工具的 Schema 列表（过滤 fc_hidden）。"""
        return [
            tool.get_schema()
            for tool in self._tools.values()
            if getattr(tool, "kit", "General") == kit_name and not getattr(tool, "fc_hidden", False)
        ]

    def get_fc_schemas_for_prompt(
        self,
        prompt: str,
        step: int = 1,
        recently_used: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """[Round 8] Return routed FC schemas based on task context.

        Uses ToolRouter to select only the relevant kit schemas instead of
        sending all 46 schemas every step. Falls back to full set if routing
        would leave the LLM with too few tools.
        """
        from toolset.router import ToolRouter

        all_schemas = self.get_all_fc_schemas()
        kit_map: Dict[str, str] = {tool.name: getattr(tool, "kit", "general") for tool in self._tools.values()}
        return ToolRouter.get().select_schemas(
            prompt=prompt,
            step=step,
            recently_used=recently_used or [],
            all_fc_schemas=all_schemas,
            kit_map=kit_map,
        )

    def get_compact_kit_overview(self) -> str:
        """
        [Kit-OS] 生成精简的 Kit 概览摘要，用于替代全量 JSON Schema 注入。
        格式：每个 Kit 一行，列出工具名称，不含完整参数定义。
        AI 需要工具详情时，应调用 tool_search / tool_list 按需获取。
        """
        lines = ["### Tool Kits Overview", ""]
        for kit_name in self.get_kit_names():
            tool_names = [t["name"] for t in self.get_tools_by_kit(kit_name)]
            lines.append(f"**[{kit_name}]** → {', '.join(tool_names)}")
        lines.append("")
        lines.append(
            "> Use `tool_info(action='search', query='<keyword>')` to find a tool, "
            "then `tool_info(action='list', kit_filter='<kit>')` to get its tools."
        )
        return "\n".join(lines)


# Global tool registry instance
# 全局工具注册表实例
global_tool_registry = ToolRegistry()


def _validate_tool_contract(tool_class: Type[BaseTool]):
    """Validate that a tool class satisfies the BaseTool contract."""
    if not hasattr(tool_class, "name") or not tool_class.name or tool_class.name == "base_tool":
        raise ValueError(f"Tool class {tool_class.__name__} must define a non-empty 'name' attribute")

    if not hasattr(tool_class, "description") or not tool_class.description:
        raise ValueError(f"Tool '{tool_class.name}' must define a non-empty 'description'")

    if not hasattr(tool_class, "run"):
        raise ValueError(f"Tool '{tool_class.name}' must implement async def run(self, **kwargs)")

    import inspect as _inspect

    if not _inspect.iscoroutinefunction(tool_class.run):
        raise ValueError(f"Tool '{tool_class.name}'.run() must be async")

    if hasattr(tool_class, "args_schema") and tool_class.args_schema is not None:
        from pydantic import BaseModel

        if not (isinstance(tool_class.args_schema, type) and issubclass(tool_class.args_schema, BaseModel)):
            raise ValueError(f"Tool '{tool_class.name}' args_schema must be a Pydantic BaseModel subclass")


# 自动发现并注册所有工具
import pkgutil
import importlib
import inspect


def _init_registry():
    import toolset.definitions

    skipped = []
    for _, modname, _ in pkgutil.iter_modules(toolset.definitions.__path__):
        try:
            module = importlib.import_module(f"toolset.definitions.{modname}")
            for name, obj in inspect.getmembers(module):
                if inspect.isclass(obj) and issubclass(obj, BaseTool) and obj is not BaseTool:
                    # Skip abstract base classes that do not represent concrete tools
                    if (
                        getattr(obj, "name", "base_tool") == "base_tool"
                        or obj.__name__.startswith("Base")
                        or obj.__name__.endswith("BaseTool")
                    ):
                        continue
                    # Platform filter: skip tools not supported on current OS
                    supported = getattr(obj, "platforms", None)
                    if supported is not None and CURRENT_PLATFORM not in supported:
                        skipped.append(obj.name)
                        continue
                    global_tool_registry.register_tool(obj)
        except Exception as e:
            logger.warning(f"Failed to load tool module {modname}: {e}")
            logger.warning(f"加载工具模块 {modname} 失败: {e}")
    if skipped:
        logger.info(
            f"Platform filter: skipped {len(skipped)} incompatible tools ({CURRENT_PLATFORM}): {', '.join(skipped)}"
        )
        logger.info(f"🔧 平台过滤: 跳过 {len(skipped)} 个不兼容工具 ({CURRENT_PLATFORM}): {', '.join(skipped)}")
    logger.info(f"Tool registration complete, {len(global_tool_registry._tools)} tools ready")
    logger.info(f"🔧 工具注册完毕，共 {len(global_tool_registry._tools)} 个工具就绪")


_init_registry()
