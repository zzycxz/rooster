import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../..")))
from typing import Type
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from skills._loader import SkillLoader


class SkillReadArgs(BaseModel):
    skill_name: str = Field(..., description="技能的名称，如 'visual-control'")


def _build_skill_description() -> str:
    """根据已安装 skills 动态构建 description，包含完整技能菜单。"""
    # skill_tool.py 在 rooster/src/toolset/definitions/ 下
    # 上 3 级 = rooster/src/，再上 1 级 = rooster/（项目根）
    _src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _project_root = os.path.dirname(_src_dir)
    loader = SkillLoader(skills_dir=os.path.join(_project_root, "skills"))

    active_skills = [s for s in loader.skills.values() if s.enabled]
    if not active_skills:
        return (
            "Read the full usage guide for a named skill. "
            "Call this when a skill seems relevant but you are unsure of its exact commands or parameters."
        )

    menu_lines = []
    for s in sorted(active_skills, key=lambda x: x.name):
        menu_lines.append(f"- {s.name}: {s.description}")

    return (
        "Load a skill's full instructions. Available skills:\n"
        + "\n".join(menu_lines)
        + "\n\nCall with a skill name to get its complete usage guide."
    )


class SkillReadTool(BaseTool):
    """
    读取指定技能的完整使用说明。
    description 在模块加载时动态生成，内嵌所有已安装 skill 的菜单，
    LLM 在 tools 参数中即可看到完整技能列表，无需额外 System Prompt 注入。
    """

    name: str = "skill_read"
    description: str = _build_skill_description()
    kit: str = "System"
    args_schema: Type[BaseModel] = SkillReadArgs
    domain: str = "system"

    def _get_loader(self) -> SkillLoader:
        """用项目根路径解析，避免 CWD 依赖。"""
        _src_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _project_root = os.path.dirname(_src_dir)
        return SkillLoader(skills_dir=os.path.join(_project_root, "skills"))

    async def run(self, skill_name: str) -> str:
        loader = self._get_loader()
        detail = loader.get_skill_detail(skill_name)

        if detail.startswith("Error:"):
            all_skills = list(loader.skills.keys())
            return f"{detail}\n当前可用技能列表: {', '.join(all_skills)}"

        return f"--- SKILL DETAIL: {skill_name.upper()} ---\n\n{detail}"
