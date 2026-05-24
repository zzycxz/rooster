import os
from typing import Type, List, Optional, Dict
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from toolset.registry import global_tool_registry


class ToolListArgs(BaseModel):
    kit_filter: Optional[str] = Field(
        None, description="过滤特定的 Kit 名称 (如: Browser, FileSystem)。若为空则列出所有 Kit。"
    )


class ToolListTool(BaseTool):
    """
    列出系统中注册的所有工具。支持按 Kit 分组。
    """

    name: str = "tool_list"
    kit: str = "System"
    fc_hidden: bool = True  # [Round 10] Use tool_info(action="list") instead
    description: str = "List all available tools in Rooster, grouped by functional Kits. Use this to see what capabilities are currently registered."
    args_schema: Type[BaseModel] = ToolListArgs
    domain: str = "system"

    async def run(self, kit_filter: Optional[str] = None) -> str:
        schemas = global_tool_registry.get_all_tool_schemas()

        kits = {}
        for s in schemas:
            k = s.get("kit", "general")
            if k not in kits:
                kits[k] = []
            kits[k].append(s["name"])

        if kit_filter and kit_filter in kits:
            tools = kits[kit_filter]
            return f"### {kit_filter} Kit 中的工具:\n- " + "\n- ".join(tools)

        report = ["### 🛠️ Rooster 工具套件概览:"]
        for k, tools in kits.items():
            report.append(f"\n**[{k}]**\n- " + ", ".join(tools))

        return "\n".join(report)


class ToolSearchArgs(BaseModel):
    query: str = Field(..., description="搜索关键词，如 'excel', '翻译', '截图'")


class ToolSearchTool(BaseTool):
    """
    通过模糊匹配关键词在 KIT_INDEX.md 中搜索最合适的 Kit。
    """

    name: str = "tool_search"
    kit: str = "System"
    fc_hidden: bool = True  # [Round 10] Use tool_info(action="search", query=...) instead
    description: str = "Search for functional kits using natural language keywords. Use this when you are unsure which kit to use for a specific task."
    args_schema: Type[BaseModel] = ToolSearchArgs
    domain: str = "system"

    async def run(self, query: str) -> str:
        index_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "KIT_INDEX.md")
        if not os.path.exists(index_path):
            return "Error: KIT_INDEX.md not found. Tool discovery index is missing."

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Simple keyword fuzzy matching
            # 简单的关键词模糊匹配
            results = []
            lines = content.split("\n")
            for line in lines:
                if "|" in line and query.lower() in line.lower():
                    results.append(line)

            if not results:
                return f"未找到与 '{query}' 相关的 Kit。请尝试更通用的词汇或使用 tool_info(action='list') 列出所有。"

            output = [
                "### 🔍 搜索结果 (匹配到的能力套件):",
                "| Kit | 关键词 | 内含工具 | 适用场景 |",
                "|-----|--------|----------|----------|",
            ]
            output.extend(results)
            output.append(
                "\n💡 建议：确定 Kit 后，可使用 `tool_info(action='list', kit_filter='KitName')` 查看具体参数详情。"
            )
            return "\n".join(output)
        except Exception as e:
            return f"Search Error: {str(e)}"


# ---------------------------------------------------------------------------
# [Round 10] tool_info — unified tool discovery macro
# Replaces: tool_list, tool_search
# ---------------------------------------------------------------------------


class ToolInfoArgs(BaseModel):
    action: str = Field(description="'list' to see all tools by Kit, 'search' to find tools by keyword")
    query: Optional[str] = Field(
        default=None, description="[search] Keyword to search for (e.g. 'excel', 'translate', 'screenshot')"
    )
    kit_filter: Optional[str] = Field(
        default=None, description="[list] Filter to a specific Kit name (e.g. Browser, Office)"
    )


class ToolInfoTool(BaseTool):
    """[Round 10] Unified tool discovery macro: list all tools or search by keyword."""

    name: str = "tool_info"
    kit: str = "System"
    description: str = (
        "Discover available tools. Use action='list' to see all registered tools grouped by Kit "
        "(optionally filtered by kit_filter). Use action='search' with a query keyword to find "
        "the right Kit for a specific task."
    )
    args_schema: Type[BaseModel] = ToolInfoArgs
    domain: str = "system"

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "list").lower()
        query = kwargs.get("query", "")
        kit_filter = kwargs.get("kit_filter")

        if action == "list":
            schemas = global_tool_registry.get_all_tool_schemas()
            kits: Dict[str, List[str]] = {}
            for s in schemas:
                k = s.get("kit", "general")
                if k not in kits:
                    kits[k] = []
                kits[k].append(s["name"])

            if kit_filter and kit_filter in kits:
                tools = kits[kit_filter]
                return f"### {kit_filter} Kit 中的工具:\n- " + "\n- ".join(tools)

            report = ["### 🛠️ Rooster 工具套件概览:"]
            for k, tools in kits.items():
                report.append(f"\n**[{k}]**\n- " + ", ".join(tools))
            return "\n".join(report)

        elif action == "search":
            if not query:
                return "Error: 'query' is required for action='search'."
            index_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "KIT_INDEX.md")
            if not os.path.exists(index_path):
                return "Error: KIT_INDEX.md not found."
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    content = f.read()
                results = []
                for line in content.split("\n"):
                    if "|" in line and query.lower() in line.lower():
                        results.append(line)
                if not results:
                    return (
                        f"未找到与 '{query}' 相关的 Kit。请尝试更通用的词汇或使用 tool_info(action='list') 查看全部。"
                    )
                output = [
                    "### 🔍 搜索结果:",
                    "| Kit | 关键词 | 内含工具 | 适用场景 |",
                    "|-----|--------|----------|----------|",
                ]
                output.extend(results)
                output.append("\n💡 使用 `tool_info(action='list', kit_filter='KitName')` 查看具体工具详情。")
                return "\n".join(output)
            except Exception as e:
                return f"Search Error: {str(e)}"

        else:
            return f"Error: Unknown action '{action}'. Valid: 'list', 'search'."
