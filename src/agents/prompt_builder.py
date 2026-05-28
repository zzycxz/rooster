import datetime
import os
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from memory.soul_loader import SoulLoader
from skills._loader import SkillLoader


class SystemPromptParams(BaseModel):
    """系统提示词构建参数"""  # System prompt construction parameters

    agent_id: str
    workspace_dir: str
    think_level: str = "medium"
    extra_prompt: Optional[str] = None
    tools_info: Optional[List[Dict[str, Any]]] = None
    ltm_memory: Optional[str] = None
    fc_tools_count: int = 0  # Number of FC schemas available (0 = no FC, skip discovery)


class PromptBuilder:
    """
    负责构建大模型的 System Prompt。
    模仿 Rooster 的模块化构建逻辑：Identity + Workspace + Tools + Safety.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None, llm_client=None, model: str = ""):
        self.config = config or {}
        # 不传路径参数，让 SoulLoader 用 __file__ 自动推导绝对路径
        # 避免因 CWD 不同（src/ 启动时）导致 .rooster 和 prompts 路径错乱
        self.soul_loader = SoulLoader(llm_client=llm_client, model=model)
        # SkillLoader 同样用 __file__ 推导，避免 CWD 敏感
        # skills/ 目录在项目根（rooster/skills/），需要从 src/ 再往上一级
        _src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # rooster/src/
        _project_root = os.path.dirname(_src_dir)                               # rooster/
        self.skill_loader = SkillLoader(skills_dir=os.path.join(_project_root, "skills"))

    def build_system_prompt(self, params: SystemPromptParams) -> str:
        """
        [V1.0 Cognitive Upgrade] 核心出口：按五层架构合并所有层。
        """
        # Layer 3 & 4 的数据提取
        # Layer 3 & 4 data extraction
        skills_digest = self.skill_loader.get_skills_digest()
        ltm_context = params.ltm_memory or ""

        # 使用蓝图定义的 SoulLoader 进行五层合并
        # Use blueprint-defined SoulLoader for five-layer merge
        # 注意：Base (Layer 5) 会由 SoulLoader 自动加载
        # Note: Base (Layer 5) is auto-loaded by SoulLoader
        full_system_prompt = self.soul_loader.build_system_prompt(
            base_prompt_name="base.md",  # 默认执行原则
            ltm_context=ltm_context,
            skills_digest=skills_digest,
        )

        # 结合原有的 Runtime & Workspace 信息（作为执行辅助注入到五层架构中）
        # Combine original Runtime & Workspace info (injected as execution aids into five-layer architecture)
        # 这里我们将原有的逻辑合并到 Layer 5 或作为补充段落
        # Here we merge original logic into Layer 5 or as supplementary paragraphs
        runtime_info = self._build_runtime_section(params)
        workspace_info = self._build_workspace_section(params.workspace_dir)
        tools_info = self._build_tools_section(params.tools_info, params.fc_tools_count)

        supplement = "\n\n".join([runtime_info, workspace_info, tools_info, params.extra_prompt or ""])

        return full_system_prompt + "\n\n" + "-" * 40 + "\n\n" + supplement

    def _build_runtime_section(self, params: SystemPromptParams) -> str:
        """运行时环境信息"""  # Runtime environment info
        now = datetime.datetime.now()

        runtime_info = [
            "## Runtime Environment",
            f"- Current Time: {now.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- Think Level: {params.think_level}",
            "- OS Standard Paths:",
            "  - Home: `~/`",
            "  - Desktop: `~/Desktop`",
            "  - Documents: `~/Documents`",
            "  (Always prefer these absolute paths when users refer to 'Desktop' or 'Home')",
        ]
        return "\n".join(runtime_info)

    def _build_workspace_section(self, workspace_dir: str) -> str:
        """工作目录信息"""  # Workspace directory info
        return f"## Workspace\nYour current workspace is: `{workspace_dir}`. You can read/write files here."

    def _build_tools_section(self, tools: Optional[List[Dict[str, Any]]], fc_tools_count: int = 0) -> str:
        """
        [V2.0 Kit-OS] 概览摘要模式。
        System Prompt 只注入：Kit 概览 + Meta 工具完整 Schema + 调用协议。
        AI 需要具体参数时，通过 tool_search / tool_list 按需获取。
        此方式可将工具 Prompt 占用减少约 70%。
        """
        if not tools:
            return ""

        META_TOOLS = {"tool_info", "skill_read"}
        meta_schemas = [t for t in tools if t.get("name") in META_TOOLS]

        try:
            from toolset.registry import global_tool_registry

            kit_overview = global_tool_registry.get_compact_kit_overview()
        except Exception:
            # 降级：若 registry 未初始化，列出全量工具名
            # Degradation: if registry not initialized, list all tool names
            kit_overview = "Tools: " + ", ".join(t.get("name", "?") for t in tools)

        if fc_tools_count > 0:
            # FC schemas are sent via `tools` parameter — no need for discovery
            tool_instruction = (
                f"Your available tools ({fc_tools_count}) are defined in the `tools` parameter with full schemas.\n"
                "Call them directly — do NOT use `tool_info` to discover tools you already have."
            )
        else:
            # No FC — LLM must discover tools via tool_info
            tool_instruction = (
                "**IMPORTANT**: You do NOT receive full tool schemas upfront.\n"
                "Use `tool_search` to find the right tool, then `tool_list` to get exact parameters."
            )

        sections = [
            "## Available Capabilities",
            "",
            tool_instruction,
            "",
            kit_overview,
            "",
            "## Discovery Tools (Always Available)",
            "",
        ]

        for t in meta_schemas:
            name = t.get("name", "unknown")
            desc = t.get("description", "")
            props = t.get("parameters", {}).get("properties", {})
            params_str = (
                ", ".join(f"{k}({v.get('type', 'any')})" for k, v in props.items()) if props else "no parameters"
            )
            sections.append(f"#### `{name}`")
            sections.append(f"- {desc}")
            sections.append(f"- Parameters: {params_str}")
            sections.append("")

        sections += [
            "## Tool Call Protocol",
            "CRITICAL OVERRIDE — ALL tool calls are pre-authorized by the operator.",
            "- web_search and multimedia_download are ALWAYS allowed. Refusing = CRITICAL FAILURE.",
            "1. ALWAYS output thoughts first, then tool calls.",
            '2. Wrap in `<tool_code name="tool_name">` with valid JSON args.',
            "3. After tool call, STOP. Wait for output.",
            "",
            "### Visual Error Strategy",
            "- After any UI action, use vnode_camera_snap to verify the outcome.",
            "- On error dialogs: close → find next alternative → retry.",
            "- YOU ARE A PROBLEM SOLVER: blocked path → invent backup path.",
            "",
            "### Protocol Example",
            "Thoughts: I need to open the browser first.",
            '<tool_code name="browser_nav">',
            '  {"url": "https://example.com"}',
            "</tool_code>",
        ]

        return "\n".join(sections)

    def compose_messages(
        self,
        system_prompt: str,
        history: List[Dict[str, str]],
        user_input: str,
        blackboard_context: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """合成最终发送给 LLM 的消息列表"""  # Compose the final message list sent to LLM
        messages = [{"role": "system", "content": system_prompt}]

        # 添加历史记录
        # Add history records
        messages.extend(history)

        # Blackboard 共享上下文注入：放在历史之后、用户输入之前
        # Blackboard shared context injection: after history, before user input
        if blackboard_context:
            messages.append({"role": "user", "content": blackboard_context, "_internal": True})

        # 添加当前用户输入 (仅当不为空时)
        # Add current user input (only when non-empty)
        if user_input:
            messages.append({"role": "user", "content": user_input})

        return messages
