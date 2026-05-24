import os
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class ToolOrchestrator:
    """
    Rooster 超级调度编排器。
    核心原则：解耦。
    作为 Executor 与 Tool 之间的中间件，负责输入预处理、机械化策略应用和输出结果提纯。
    """

    def __init__(self, workspace_dir: str, allowed_paths: List[str] = None):
        self.workspace_dir = os.path.abspath(workspace_dir)
        # --- 全平台语义化路径自动探测 (核心：普适性加固) ---
        # --- Cross-platform semantic path auto-detection (core: universal hardening) ---
        home = os.path.expanduser("~")

        # 针对 Windows OneDrive 的自适应逻辑
        # Adaptive logic for Windows OneDrive
        potential_desktops = [
            os.path.join(home, "Desktop"),
            os.path.join(home, "OneDrive", "Desktop"),
            os.path.join(home, "OneDrive - Personal", "Desktop"),
            os.path.join(
                home, "用户目录", "桌面"
            ),  # 兼容极少数本地化极深的情况 / Compatible with rare deep-localization cases
        ]

        real_desktop = home  # Fallback to home if no desktop found
        for d in potential_desktops:
            if os.path.exists(d):
                real_desktop = d
                break

        self.sys_paths = {
            "HOME": home,
            "DESKTOP": real_desktop,
            "DOCUMENTS": os.path.join(home, "Documents"),
            "WORKSPACE": self.workspace_dir,
        }
        self.allowed_paths = allowed_paths or []

    async def pre_dispatch(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        [规则引擎 1：预处理]
        对大模型的原始参数进行机械化修正与路径普适化映射。
        """
        # [Rule Engine 1: Pre-processing]
        # Mechanically correct LLM raw parameters and universal path mapping
        # 策略 A: 路径扩展与绝对化 (普适性支持)
        # Strategy A: Path expansion and absolutization (universal support)
        if "path" in args:
            args["path"] = self._resolve_path(args["path"])

        return args

    async def post_dispatch(self, tool_name: str, result: Any) -> str:
        """
        [规则引擎 2：后处理/提纯]
        对工具返回的海量原始数据进行"脱水"，转化为模型更易吸收的形式。
        """
        # [Rule Engine 2: Post-processing/Refinement]
        # 'Dehydrate' massive raw data from tool returns into a form easier for the model to absorb
        raw_output = str(result)

        # 策略 C: 长文本截断与语义保留 (动态对齐 AGENT_CONTEXT_LIMIT)
        # Strategy C: Long text truncation with semantic retention (dynamic alignment to AGENT_CONTEXT_LIMIT)
        from utils.config import settings

        limit = settings.OBSERVATION_CHAR_LIMIT

        if len(raw_output) > limit:
            if "[LABEL_IMAGE:" in raw_output or "[RESULT_PATH:" in raw_output:
                # 视觉数据交由 AgentExecutor 后的脱敏环节处理，此处保持原样
                # Visual data handled by AgentExecutor's sanitization step, keep as-is here
                return raw_output

            # 动态计算 Head 和 Tail
            # Dynamically calculate Head and Tail
            head = int(limit * 0.8)
            tail = int(limit * 0.2)
            return (
                raw_output[:head]
                + f"\n... (Data too large [{len(raw_output)} chars], auto-truncated to {limit} by Orchestrator) ...\n"
                + raw_output[-tail:]
            )

        return raw_output

    def _resolve_path(self, raw_path: str) -> str:
        """强化版路径解析：支持中文、大小写语义路径，并防止项目内路径污染"""  # Enhanced path resolution: support Chinese, case-insensitive semantic paths
        # 1. 语义占位符模糊匹配 (普适支持)
        # 1. Semantic placeholder fuzzy matching (universal support)
        normalized_raw = raw_path.replace("\\", "/").lower()

        # 针对常见中文/英文桌面的强制映射策略
        # Forced mapping strategy for common Chinese/English desktop names
        desktop_keys = ["desktop", "桌面", "DESKTOP"]
        final_path = raw_path

        for d_key in desktop_keys:
            if normalized_raw.startswith(d_key.lower()):
                # 语义替换：将 "桌面/..." 替换为真实的系统桌面绝对路径
                # Semantic substitution: replace 'Desktop/...' with real system desktop absolute path
                real_desktop = self.sys_paths["DESKTOP"]
                # 保持原始路径中斜杠后的子路径
                # Preserve sub-path after slash in original path
                sub_path = raw_path[len(d_key) :].lstrip("\\/")
                final_path = os.path.join(real_desktop, sub_path)
                return os.path.abspath(final_path)

        # 2. 通用语义替换
        # 2. Universal semantic substitution
        for key, val in self.sys_paths.items():
            if raw_path.upper().startswith(key):
                final_path = raw_path.replace(raw_path[: len(key)], val, 1)
                return os.path.abspath(final_path)

        # 3. 相对路径补全 (如果是大模型传来的相对路径，基于工作空间目录)
        # 3. Relative path completion (if path from LLM is relative, base on workspace directory)
        if not os.path.isabs(final_path):
            return os.path.abspath(os.path.join(self.workspace_dir, final_path))

        return os.path.abspath(final_path)
