"""ToolRouter — Kit-based FC schema selector for context-aware tool injection.

Round 8: Instead of sending all 46 FC schemas every ReAct step, route only the
schemas relevant to the current task context. System kit (tool_list / tool_search /
skill_read + orchestration) is always included. A keyword→kit table determines
which additional kits to inject. Recently-used tool names ensure continuity.

Config knobs (via environment variables / settings):
  TOOL_ROUTER_ENABLED   bool  default True   — kill-switch: False → send all schemas
  TOOL_ROUTER_MAX_TOOLS int   default 20     — hard cap per step
  TOOL_ROUTER_RULES_JSON str  default ""     — JSON override for keyword→kit mapping
"""

import re
import json
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System kit is always included — contains meta-tools and orchestration tools.
# _META_TOOL_NAMES is a subset kept for explicit bookkeeping / documentation.
# ---------------------------------------------------------------------------
_ALWAYS_INCLUDE_KITS: Set[str] = {"System"}
_META_TOOL_NAMES: Set[str] = {"tool_info", "skill_read"}

# Minimum number of routed tools before we fall back to full set.
# Prevents the LLM from being stranded with only System tools on ambiguous tasks.
_MIN_TOOLS_BEFORE_FALLBACK = 8

# ---------------------------------------------------------------------------
# Default keyword → kit routing rules (regex patterns, case-insensitive).
# Key: Python regex, Value: list of kit names to activate.
# Covers both English and Chinese keywords.
# ---------------------------------------------------------------------------
_DEFAULT_RULES: Dict[str, List[str]] = {
    # File & document operations
    r"file|read|write|save|folder|director|path|创建文件|写文件|读文件|保存": [
        "FileSystem",
        "Office",
    ],
    # Web browsing / page interaction
    r"browser|navigate|webpage|website|http|url|scrape|crawl|页面|网页|浏览器|打开网址": [
        "Browser",
    ],
    # Generic web fetch / web search
    r"web.{0,10}fetch|web.{0,10}search|web.{0,10}read|网络搜索|抓取|爬取": [
        "Browser",
        "Search",
    ],
    # Research / search tasks
    r"search|find\s+info|look.?up|research|搜索|查找|查询|exa|linkup|搜一下|检索": [
        "Search",
        "Browser",
    ],
    # Email
    r"email|send.*mail|inbox|smtp|邮件|发邮件|寄信": ["Comms"],
    # Code execution
    r"code|python|script|execute|run.*program|interpret|执行代码|运行脚本|编程|写代码": [
        "Interpreter",
    ],
    # Vision / desktop automation / OCR
    r"image|screenshot|ocr|vision|visual|screen|desktop|click.*ui|截图|识别|视觉|桌面|点击界面": [
        "Vision",
    ],
    # Memory operations
    r"memory|remember|recall|learn.*fact|记忆|记住|回忆|存记": ["Memory"],
    # Office documents
    r"office|word|excel|pdf|docx|xlsx|spreadsheet|文档|表格|报告|word文件": [
        "Office",
        "FileSystem",
    ],
    # Multimedia download
    r"download|video|movie|media|multimedia|下载|视频|电影|媒体": [
        "Multimedia",
        "Browser",
    ],
    # Feishu / collaboration
    r"feishu|lark|notify|webhook|飞书|通知|推送": ["Network"],
    # Subagent / delegation patterns
    r"subagent|spawn|delegate|parallel|子代理|并行|派遣": ["System"],
}


class ToolRouter:
    """
    Context-aware FC schema selector.

    Selection strategy (per step):
    1. Always include System kit (tool_list / tool_search / skill_read + orchestration).
    2. Match prompt keywords against _DEFAULT_RULES → include matched kits.
    3. Include kits used by recently-used tools (continuity within a ReAct run).
    4. If matched tool count < _MIN_TOOLS_BEFORE_FALLBACK → fall back to full set.
    5. Cap at TOOL_ROUTER_MAX_TOOLS; prioritise recently-used + meta tools.
    """

    _instance: Optional["ToolRouter"] = None

    def __init__(self) -> None:
        self._rules: Dict[str, List[str]] = self._load_rules()

    @classmethod
    def get(cls) -> "ToolRouter":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Force re-initialisation (useful after config changes in tests)."""
        cls._instance = None

    def _load_rules(self) -> Dict[str, List[str]]:
        try:
            from utils.config import settings  # lazy import to avoid circular deps

            raw = getattr(settings, "TOOL_ROUTER_RULES_JSON", "")
            if raw:
                custom = json.loads(raw)
                logger.info("[ToolRouter] Loaded custom routing rules from TOOL_ROUTER_RULES_JSON")
                return custom
        except Exception as e:
            logger.warning(f"[ToolRouter] Failed to load TOOL_ROUTER_RULES_JSON: {e}")
        return _DEFAULT_RULES

    def _match_kits(self, text: str) -> Set[str]:
        text_lower = text.lower()
        matched: Set[str] = set()
        for pattern, kits in self._rules.items():
            if re.search(pattern, text_lower):
                matched.update(kits)
        return matched

    def select_schemas(
        self,
        prompt: str,
        step: int,
        recently_used: List[str],
        all_fc_schemas: List[Dict],
        kit_map: Dict[str, str],
    ) -> List[Dict]:
        """
        Return a filtered (and capped) FC schema list for this ReAct step.

        Args:
            prompt:           The task instruction / user query.
            step:             Current ReAct loop step (1-based).
            recently_used:    Tool names used in earlier steps of this run.
            all_fc_schemas:   Full FC schema list from ToolRegistry.
            kit_map:          {tool_name: kit_name} for every registered tool.

        Returns:
            Subset of all_fc_schemas (never empty — falls back to full set).
        """
        try:
            from utils.config import settings

            enabled: bool = getattr(settings, "TOOL_ROUTER_ENABLED", True)
            max_tools: int = getattr(settings, "TOOL_ROUTER_MAX_TOOLS", 20)
        except Exception:
            enabled, max_tools = True, 20

        if not enabled:
            return all_fc_schemas

        # --- 1. Determine target kits ---
        target_kits: Set[str] = set(_ALWAYS_INCLUDE_KITS)
        matched_kits = self._match_kits(prompt)
        target_kits.update(matched_kits)

        # Include kits of recently-used tools (continuity)
        for name in recently_used:
            tool_kit = kit_map.get(name)
            if tool_kit:
                target_kits.add(tool_kit)

        # --- 2. Collect tool names that belong to target kits ---
        selected_names: Set[str] = set()
        for schema in all_fc_schemas:
            name = schema.get("function", {}).get("name", "")
            if kit_map.get(name, "general") in target_kits:
                selected_names.add(name)

        # Always include meta-tools (they may not have been kit-matched)
        for schema in all_fc_schemas:
            name = schema.get("function", {}).get("name", "")
            if name in _META_TOOL_NAMES:
                selected_names.add(name)

        # Always include recently used tools by name (regardless of kit)
        selected_names.update(recently_used[-5:])

        # --- 3. Fallback guard: if selection is too small, send everything ---
        if len(selected_names) < _MIN_TOOLS_BEFORE_FALLBACK and not matched_kits:
            logger.debug(f"[ToolRouter] Step {step}: no kit match, falling back to all {len(all_fc_schemas)} schemas")
            return all_fc_schemas

        # --- 4. Build filtered list preserving original registry order ---
        result = [s for s in all_fc_schemas if s.get("function", {}).get("name", "") in selected_names]

        # --- 5. Apply hard cap; prioritise recently-used + meta tools ---
        if len(result) > max_tools:
            priority_names = set(recently_used[-5:]) | _META_TOOL_NAMES
            high = [s for s in result if s.get("function", {}).get("name", "") in priority_names]
            low = [s for s in result if s.get("function", {}).get("name", "") not in priority_names]
            result = (high + low)[:max_tools]

        logger.debug(
            f"[ToolRouter] Step {step}: {len(result)}/{len(all_fc_schemas)} schemas "
            f"(kits={sorted(target_kits)}, recently_used={recently_used[-3:]})"
        )
        return result
