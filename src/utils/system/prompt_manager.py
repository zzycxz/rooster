import os
from typing import Dict, Any
from loguru import logger

# Resolve to absolute path at import time, regardless of cwd
_DEFAULT_PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "prompts")


class PromptManager:
    """[V3.0] Rooster 主权角色协议 (Rooster Sovereign Protocol) 管理引擎"""

    def __init__(self, prompt_dir: str = None):
        self.prompt_dir = os.path.normpath(prompt_dir or _DEFAULT_PROMPT_DIR)
        self.base_prompt = ""
        self._load_base()

    def _load_base(self):
        """加载基础协议"""
        base_path = os.path.join(self.prompt_dir, "base.md")
        if os.path.exists(base_path):
            with open(base_path, "r", encoding="utf-8") as f:
                self.base_prompt = f.read()
            logger.info("✅ [PromptEngine] 加载基础协议成功 (Base Protocol)")
        else:
            logger.warning("⚠️ [PromptEngine] base.md 缺失")

    def get_prompt(self, role_name: str, variables: Dict[str, Any] = None) -> str:
        """
        组合并渲染指定角色的提示词模板
        role_name: 如 'strategist', 'executor', 'auditor'
        variables: 需要替换的动态变量
        """
        variables = variables or {}
        role_path = os.path.join(self.prompt_dir, f"{role_name}.md")

        if not os.path.exists(role_path):
            logger.error(f"❌ [PromptEngine] 找不到角色协议: {role_path}")
            return self.base_prompt

        with open(role_path, "r", encoding="utf-8") as f:
            role_prompt = f.read()

        full_content = f"{self.base_prompt}\n\n{role_prompt}"

        # [V3.0] 动态变量注入逻辑
        # [V3.0] Dynamic variable injection logic
        try:
            # 简单的变量查找替换
            # Simple find-and-replace for variables
            rendered = full_content
            for key, val in variables.items():
                pattern = f"{{{key}}}"
                rendered = rendered.replace(pattern, str(val))
            return rendered
        except Exception as e:
            logger.error(f"❌ [PromptEngine] 渲染变量失败: {e}")
            return full_content


# 全局单例
# Global singleton
prompt_manager = PromptManager()
