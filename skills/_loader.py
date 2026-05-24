import os
import platform
import yaml
import logging
import shutil
import importlib.util
from typing import List, Dict, Optional, Any
from dataclasses import dataclass

_RAW_PLATFORM = platform.system().lower()  # "windows", "darwin", "linux"
# 映射 Python 平台名到 SKILL.md 中使用的平台名
_PLATFORM_MAP = {"windows": "windows", "darwin": "macos", "linux": "linux"}
_CURRENT_PLATFORM = _PLATFORM_MAP.get(_RAW_PLATFORM, _RAW_PLATFORM)

logger = logging.getLogger(__name__)

@dataclass
class SkillMeta:
    name: str
    description: str
    emoji: str
    category: str
    platform: List[str]
    missing_deps: List[str]
    full_path: str
    enabled: bool = True

class SkillLoader:
    """
    Rooster Skill 加载器。
    负责自动发现、YAML 解析以及健康检查。
    """
    def __init__(self, skills_dir: str = "skills"):
        self.skills_dir = os.path.abspath(skills_dir)
        self.skills: Dict[str, SkillMeta] = {}
        self.reload()

    def reload(self):
        """扫描目录并加载所有有效的 Skill。"""
        self.skills = {}
        if not os.path.exists(self.skills_dir):
            return

        for root, dirs, files in os.walk(self.skills_dir):
            if "SKILL.md" in files:
                skill_file = os.path.join(root, "SKILL.md")
                self._load_skill(skill_file, enabled=True)
            elif "SKILL.md.disabled" in files:
                skill_file = os.path.join(root, "SKILL.md.disabled")
                self._load_skill(skill_file, enabled=False)

    @staticmethod
    def _get_vendor_meta(meta_data: dict) -> dict:
        """Extract vendor-specific metadata from YAML frontmatter.

        Looks for ``metadata.rooster`` first, then ``metadata.openclaw`` for
        compatibility with externally sourced skills.
        """
        top = meta_data.get("metadata", {})
        vendor = top.get("rooster") or top.get("openclaw") or {}
        return vendor

    def _load_skill(self, path: str, enabled: bool = True):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

            # Advanced Security: skill poisoning detection (default OFF)
            try:
                import sys, os as _os
                src_path = _os.path.join(_os.path.dirname(__file__), "..", "src")
                if src_path not in sys.path:
                    sys.path.insert(0, src_path)
                from utils.security.advanced_guard import AdvancedGuard
                skill_report = AdvancedGuard.verify_skill(path, content)
                if skill_report.should_block:
                    logger.error(
                        f"[SkillVerifier] Skill 被拒绝加载（投毒检测）: {path} — "
                        f"{skill_report.threats[0].description}"
                    )
                    return  # 拒绝加载此 skill
                if skill_report.has_threats:
                    logger.warning(
                        f"[SkillVerifier] Skill 含可疑内容（high/medium），已标记: {path} — "
                        + "; ".join(t.description for t in skill_report.threats)
                    )
            except ImportError:
                pass  # 安全模块不可用时降级，不影响正常加载
            except Exception as _sv_err:
                logger.debug(f"[SkillVerifier] check skipped for {path}: {_sv_err}")

            # 解析 YAML Frontmatter
            parts = content.split("---")
            if len(parts) < 3: return

            meta_data = yaml.safe_load(parts[1])
            name = meta_data.get("name")
            if not name: return

            vendor = self._get_vendor_meta(meta_data)

            # 平台过滤（支持 platform 和 os 两种字段名）
            platforms = vendor.get("platform", vendor.get("os", []))
            if isinstance(platforms, str):
                platforms = [platforms]
            if platforms and "any" not in platforms and _CURRENT_PLATFORM not in platforms:
                return

            # 健康检查
            missing = self._check_dependencies(meta_data)

            skill = SkillMeta(
                name=name,
                description=meta_data.get("description", ""),
                emoji=vendor.get("emoji", "🔧"),
                category=vendor.get("category", "automation"),
                platform=platforms,
                missing_deps=missing,
                full_path=path,
                enabled=enabled
            )
            self.skills[name] = skill

        except Exception as e:
            logger.error(f"❌ 加载 Skill 失败 ({path}): {e}")

    def _check_dependencies(self, meta: dict) -> List[str]:
        """健康检查：验证 Python 包、系统命令和环境变量。"""
        missing = []
        vendor = self._get_vendor_meta(meta)
        requires = vendor.get("requires", {})

        # 1. 检查 Python 包 (rooster: python_packages)
        for pkg in requires.get("python_packages", []):
            if importlib.util.find_spec(pkg) is None:
                missing.append(f"pip:{pkg}")

        # 2. 检查系统命令
        for bin_cmd in requires.get("bins", []):
            if shutil.which(bin_cmd) is None:
                missing.append(f"bin:{bin_cmd}")

        # 3. 检查环境变量 (支持 env 和 env_vars 两种字段名)
        env_vars = requires.get("env", requires.get("env_vars", []))
        for env_name in env_vars:
            if not os.getenv(env_name):
                missing.append(f"env:{env_name}")

        return missing

    def get_skills_digest(self) -> str:
        """生成用于 System Prompt 的摘要。"""
        active_skills = [s for s in self.skills.values() if s.enabled]
        if not active_skills: return ""
        
        digest = ["## 🔧 可用技能 (Skills)"]
        
        # 按分类组织
        categories = {}
        for s in active_skills:
            categories.setdefault(s.category, []).append(s)
            
        for cat, skills in categories.items():
            digest.append(f"\n**{cat.capitalize()} 类**")
            for s in skills:
                warn = f" ⚠️(缺少: {', '.join(s.missing_deps)})" if s.missing_deps else ""
                digest.append(f"- {s.name} {s.emoji}{warn}: {s.description}")
        
        digest.append("\n> 使用 `skill_read` 工具按名称获取任意技能的详细说明。")
        return "\n".join(digest)

    def get_skill_detail(self, name: str) -> str:
        """获取技能详情（不含 Frontmatter）。"""
        skill = self.skills.get(name)
        if not skill: return f"Error: Skill '{name}' not found."
        
        try:
            with open(skill.full_path, "r", encoding="utf-8") as f:
                content = f.read()
            parts = content.split("---")
            return parts[2].strip() if len(parts) >= 3 else content
        except Exception as e:
            return f"Error reading skill detail: {e}"
