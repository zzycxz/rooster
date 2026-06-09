# src/agents/skill_index.py
"""
V15: 能力索引层（TF-IDF 第一版）。
零外部依赖，关键词匹配，全局单例。
输出 SkillHint 给 Strategist.decide() 作为参考。
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 项目根目录（CWD 无关，基于 __file__ 推导）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@dataclass
class SkillHint:
    """能力索引查询结果（只是 hint，不是硬路由）。"""

    hint_skill: str
    confidence: float
    model_hint: str = "standard"  # "fast" | "standard" | "reasoning"
    hard_route: bool = False  # 始终 False，只是 hint

    def to_dict(self) -> dict:
        return {
            "hint_skill": self.hint_skill,
            "confidence": self.confidence,
            "model_hint": self.model_hint,
            "hard_route": self.hard_route,
        }


# ---------------------------------------------------------------------------
# [Phase 3] SkillIndex hint → ToolRouter Kit mapping
# Resolved dynamically from SKILL.md frontmatter (kits field) and built-in index.
# No hardcoded mapping — third-party skills declare their own kits.
# ---------------------------------------------------------------------------


def hint_to_forced_kits(hint_skill: str) -> set:
    """Convert a SkillIndex hint_skill to a set of Kit names for ToolRouter.

    Looks up the skill in the global SkillIndex. Kits are declared per-skill
    in SKILL.md frontmatter (``kits: ["Vision", "Browser"]``) or in
    _BUILTIN_SKILLS entries. Returns empty set if the skill has no kits.
    """
    try:
        idx = get_skill_index()
        for entry in idx._index:
            if entry.get("name") == hint_skill:
                kits = entry.get("kits", [])
                return set(kits) if kits else set()
    except Exception:
        pass
    return set()


class SkillIndex:
    """
    v1: TF-IDF 关键词匹配（零外部依赖）
    v2（Phase 3.5b）: Ollama 嵌入升级
    """

    _BUILTIN_SKILLS = [
        {
            "name": "media_download",
            "keywords": ["下载", "download", "电影", "音乐", "安装", "install", "视频", "video"],
            "model_hint": "fast",
            "kits": ["Multimedia", "Browser"],
        },
        {
            "name": "web_search",
            "keywords": ["搜索", "查找", "search", "find", "查询", "查一下", "搜一下"],
            "model_hint": "fast",
            "kits": ["Browser"],
        },
        {
            "name": "code_agent",
            "keywords": ["代码", "编程", "python", "写代码", "debug", "bug", "脚本", "script", "函数", "function"],
            "model_hint": "reasoning",
            "kits": ["Interpreter"],
        },
        {
            "name": "file_agent",
            "keywords": ["文件", "读取", "写入", "目录", "路径", "file", "read", "write", "folder"],
            "model_hint": "standard",
            "kits": ["FileSystem", "Office"],
        },
        {
            "name": "browser_agent",
            "keywords": ["浏览器", "网页", "截图", "点击", "browser", "screenshot", "webpage"],
            "model_hint": "standard",
            "kits": ["Browser"],
        },
        {
            "name": "schedule_agent",
            "keywords": ["定时", "提醒", "每天", "每周", "schedule", "remind", "daily", "weekly"],
            "model_hint": "fast",
            "kits": ["System"],
        },
    ]

    def __init__(self, skills_dirs: Optional[List[str]] = None, threshold: float = 0.3):
        self._index: List[Dict] = list(self._BUILTIN_SKILLS)
        self._threshold = threshold
        if skills_dirs is None:
            # CWD 安全的绝对路径
            skills_dirs = [
                os.path.join(_PROJECT_ROOT, "skills"),
                os.path.join(_PROJECT_ROOT, ".agents", "skills"),
            ]
        self._load_from_dirs(skills_dirs)
        logger.info(f"[SkillIndex] 索引构建完成，共 {len(self._index)} 个技能，阈值={self._threshold}")

    def _load_from_dirs(self, dirs: List[str]):
        for d in dirs:
            if not os.path.isdir(d):
                continue
            try:
                for entry in os.scandir(d):
                    if entry.is_dir():
                        manifest = os.path.join(entry.path, "SKILL.md")
                        if os.path.exists(manifest):
                            try:
                                skill = self._parse_skill_md(manifest, entry.name)
                                if skill:
                                    self._index.append(skill)
                            except Exception as e:
                                logger.debug(f"[SkillIndex] 解析 {manifest} 失败: {e}")
            except PermissionError:
                logger.debug(f"[SkillIndex] 无权访问目录: {d}")

    def _parse_skill_md(self, path: str, skill_name: str) -> Optional[Dict]:
        with open(path, encoding="utf-8") as f:
            content = f.read()
        desc_match = re.search(r'description["\']?\s*[:：]\s*["\']?(.+)', content, re.IGNORECASE)
        if not desc_match:
            return None
        desc = desc_match.group(1).strip().strip("\"'")
        keywords = [w for w in re.findall(r"\w+", desc.lower()) if len(w) > 2]
        result = {"name": skill_name, "keywords": keywords, "model_hint": "standard"}

        # Extract kits from frontmatter: kits: ["Vision", "Browser"]
        kits_match = re.search(r'kits\s*:\s*\[([^\]]*)\]', content)
        if kits_match:
            kits_raw = kits_match.group(1)
            kits = [k.strip().strip("\"'") for k in kits_raw.split(",") if k.strip()]
            if kits:
                result["kits"] = kits

        return result

    def query(self, text: str) -> Optional[SkillHint]:
        """查询最匹配的技能，返回 SkillHint 或 None（置信度不足时）。"""
        text_lower = text.lower()
        best_score, best_skill = 0.0, None
        for entry in self._index:
            hits = sum(1 for kw in entry["keywords"] if kw in text_lower)
            if hits == 0:
                continue
            score = hits / (1 + math.log(1 + len(entry["keywords"])))
            if score > best_score:
                best_score, best_skill = score, entry
        if best_score < self._threshold or best_skill is None:
            return None
        confidence = min(best_score, 1.0)
        return SkillHint(
            hint_skill=best_skill["name"],
            confidence=round(confidence, 2),
            model_hint=best_skill.get("model_hint", "standard"),
            hard_route=False,
        )


# 全局单例（启动时构建，重启重建）
_index: Optional[SkillIndex] = None


def get_skill_index(threshold: float = 0.3) -> SkillIndex:
    global _index
    if _index is None:
        _index = SkillIndex(threshold=threshold)
    return _index
