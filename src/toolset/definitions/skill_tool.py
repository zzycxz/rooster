import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from typing import Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from skills._loader import SkillLoader


class SkillReadArgs(BaseModel):
    skill_name: str = Field(..., description="技能的名称，如 'visual-control'")


class SkillReadTool(BaseTool):
    """
    读取指定技能的完整使用说明。
    当你认为某个技能可能有用、但不确定具体命令或参数格式时，调用此工具获取详情。
    """

    name: str = "skill_read"
    kit: str = "System"
    description: str = (
        "Read the full usage guide for a named skill. "
        "Call this when a skill seems relevant but you are unsure of its exact commands or parameters."
    )
    args_schema: Type[BaseModel] = SkillReadArgs
    domain: str = "system"

    async def run(self, skill_name: str) -> str:
        # Singleton loader, pointing to skills directory
        # 单例化加载器，指向 skills 目录
        loader = SkillLoader(skills_dir="skills")
        detail = loader.get_skill_detail(skill_name)

        if detail.startswith("Error:"):
            # If not found, try fuzzy match or list all
            # 如果没找到，尝试模糊匹配或列出所有
            all_skills = list(loader.skills.keys())
            return f"{detail}\n当前可用技能列表: {', '.join(all_skills)}"

        return f"--- SKILL DETAIL: {skill_name.upper()} ---\n\n{detail}"
