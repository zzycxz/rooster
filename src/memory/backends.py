"""记忆后端：JSON 文件持久化 + 衰减感知操作。"""  # Memory backend: JSON file persistence + decay-aware operations

import json
import logging
import re
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict
from pathlib import Path

from .models import MemoryFact, MemoryFactType, TYPE_PRIORITY

logger = logging.getLogger(__name__)


class MemoryBackend(ABC):
    @abstractmethod
    def add_fact(self, fact: MemoryFact):
        pass

    @abstractmethod
    def get_facts(self, types: Optional[List[MemoryFactType]] = None, limit: int = 100) -> List[MemoryFact]:
        pass

    @abstractmethod
    def update_fact(self, fact_id: str, **kwargs):
        pass

    @abstractmethod
    def remove_fact(self, fact_id: str):
        pass

    @abstractmethod
    def clear(self):
        pass

    def reload(self):
        """从磁盘重新加载数据。默认实现为 no-op，子类按需覆盖。"""  # Reload data from disk. Default is no-op, subclasses override as needed
        pass


class JSONFileBackend(MemoryBackend):
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.facts: List[MemoryFact] = []
        self._load()

    def _load(self):
        if not self.file_path.exists():
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                clean_facts = []
                for item in data:
                    if not isinstance(item, dict):
                        # 跳过非 dict 条目（旧数据可能是纯字符串）
                        # Skip non-dict entries (old data may be plain strings)
                        if isinstance(item, str) and item.strip():
                            item = {
                                "content": item,
                                "fact_id": f"migrated_{uuid.uuid4().hex[:8]}",
                                "fact_type": "research_finding",
                                "source_agent": "system",
                                "created_at": datetime.now().isoformat(),
                            }
                        else:
                            continue
                    try:
                        clean_facts.append(MemoryFact.model_validate(item))
                    except Exception:
                        try:
                            item.setdefault("fact_id", f"migrated_{uuid.uuid4().hex[:8]}")
                            item.setdefault("fact_type", MemoryFactType.RESEARCH_FINDING)
                            item.setdefault("source_agent", "system")
                            if not item.get("created_at"):
                                item["created_at"] = datetime.now().isoformat()
                            # 兼容旧数据：补充衰减字段
                            # Backward compat: fill in decay fields for old data
                            item.setdefault("access_count", 0)
                            item.setdefault("weight", 1.0)
                            item.setdefault("locked", False)
                            clean_facts.append(MemoryFact.model_validate(item))
                        except Exception as inner_e:
                            logger.warning(f"无法挽救损坏的事实条目: {inner_e}")
                self.facts = clean_facts
        except Exception as e:
            logger.error(f"加载记忆文件失败 {self.file_path}: {e}")

    def _save(self):
        from filelock import FileLock

        try:
            with FileLock(str(self.file_path) + ".lock", timeout=10):
                with open(self.file_path, "w", encoding="utf-8") as f:
                    json.dump(
                        [fact.model_dump(mode="json") for fact in self.facts],
                        f,
                        indent=2,
                        ensure_ascii=False,
                    )
        except Exception as e:
            logger.error(f"保存记忆文件失败 {self.file_path}: {e}")

    def add_fact(self, fact: MemoryFact):
        self.facts = [f for f in self.facts if f.fact_id != fact.fact_id]
        self.facts.append(fact)
        self.facts.sort(key=lambda x: x.created_at)
        self._save()

    def get_facts(self, types: Optional[List[MemoryFactType]] = None, limit: int = 100) -> List[MemoryFact]:
        results = self.facts
        if types:
            results = [f for f in results if f.fact_type in types]
        return results[-limit:]

    def get_all_facts(self) -> List[MemoryFact]:
        return list(self.facts)

    def update_fact(self, fact_id: str, **kwargs):
        for i, fact in enumerate(self.facts):
            if fact.fact_id == fact_id:
                try:
                    self.facts[i] = fact.model_copy(update=kwargs)
                except Exception as e:
                    logger.warning(f"update_fact 校验失败: {e}")
                    return
                self._save()
                return
        logger.warning(f"update_fact: fact_id={fact_id} 不存在")

    def remove_fact(self, fact_id: str):
        before = len(self.facts)
        self.facts = [f for f in self.facts if f.fact_id != fact_id]
        if len(self.facts) < before:
            self._save()

    def clear(self):
        self.facts = []
        self._save()

    def reload(self):
        self._load()

    def get_by_priority(self, limit: int = 50) -> List[MemoryFact]:
        """按类型优先级 × 权重 × 置信度排序返回"""  # Sort by type priority x weight x confidence

        def sort_key(f: MemoryFact):
            base = TYPE_PRIORITY.get(f.fact_type, 0.5)
            return base * f.weight * f.confidence

        return sorted(self.facts, key=sort_key, reverse=True)[:limit]

    def get_locked(self) -> List[MemoryFact]:
        """返回所有被锁定的事实"""  # Return all locked facts
        return [f for f in self.facts if f.locked]

    def prune_expired(self) -> int:
        """清理已过期的事实，返回清理数量"""  # Prune expired facts, return count removed
        now = datetime.now()
        before = len(self.facts)
        self.facts = [f for f in self.facts if f.expires_at is None or f.expires_at > now]
        removed = before - len(self.facts)
        if removed > 0:
            self._save()
        return removed

    def cull_old_facts(self, max_per_type: Dict[MemoryFactType, int]):
        """根据各类别限制阈值，自动清理过期老旧记忆（锁定的事实不清理）"""  # Auto-cull old facts by per-type limits (locked facts are never culled)
        type_groups: Dict[MemoryFactType, List[MemoryFact]] = {}
        for f in self.facts:
            type_groups.setdefault(f.fact_type, []).append(f)

        new_facts = []
        for f_type, f_list in type_groups.items():
            limit = max_per_type.get(f_type)
            # 锁定的事实永不清理
            # Locked facts are never culled
            locked = [f for f in f_list if f.locked]
            unlocked = [f for f in f_list if not f.locked]
            if limit is not None and limit >= 0:
                # 优先保留权重高的
                unlocked.sort(key=lambda x: x.weight * x.confidence, reverse=True)
                kept = unlocked[:limit]
            else:
                kept = unlocked
            new_facts.extend(locked + kept)

        self.facts = sorted(new_facts, key=lambda x: x.created_at)
        self._save()


class MarkdownBackend(MemoryBackend):
    """
    Markdown 文件后端：人类可读、git 友好。

    布局：
      {base_dir}/MEMORY.md              — 锁定/高优先级的策划事实
      {base_dir}/memory/daily/YYYY-MM-DD.md — 按日追加的日志事实

    每条事实渲染为一个 ### section，解析时按 ### 分割还原。
    """

    def __init__(self, base_dir: str = ".rooster"):
        self.base_dir = Path(base_dir)
        self.memory_file = self.base_dir / "MEMORY.md"
        self.daily_dir = self.base_dir / "memory" / "daily"
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.facts: List[MemoryFact] = []
        self._load()

    def _load(self):
        """从 MEMORY.md + 所有 daily 文件加载事实。"""
        self.facts = []
        # 加载 MEMORY.md
        if self.memory_file.exists():
            self._load_file(self.memory_file)
        # 加载 daily 文件
        for f in sorted(self.daily_dir.glob("*.md")):
            self._load_file(f)
        logger.debug(f"MarkdownBackend 加载了 {len(self.facts)} 条事实")

    def _load_file(self, path: Path):
        """从单个 Markdown 文件解析事实。"""
        try:
            text = path.read_text(encoding="utf-8")
            sections = re.split(r"\n### ", text)
            for section in sections:
                if not section.strip():
                    continue
                fact = self._parse_fact(section)
                if fact:
                    # 去重（可能从 MEMORY.md 和 daily 都加载到了同一个 ID）
                    if not any(f.fact_id == fact.fact_id for f in self.facts):
                        self.facts.append(fact)
        except Exception as e:
            logger.warning(f"解析 {path} 失败: {e}")

    def _render_fact(self, fact: MemoryFact) -> str:
        """将 MemoryFact 渲染为 Markdown section。"""
        lines = [
            f"### {fact.fact_id}",
            f"- **Type**: {fact.fact_type.value}",
            f"- **Agent**: {fact.source_agent}",
            f"- **Confidence**: {fact.confidence}",
            f"- **Weight**: {fact.weight}",
            f"- **Created**: {fact.created_at.isoformat() if isinstance(fact.created_at, datetime) else fact.created_at}",
        ]
        if fact.locked:
            lines.append("- **Locked**: true")
        if fact.tags:
            lines.append(f"- **Tags**: {', '.join(fact.tags)}")
        if fact.evidence_path:
            lines.append(f"- **Evidence**: {fact.evidence_path}")
        if fact.expires_at:
            lines.append(f"- **Expires**: {fact.expires_at.isoformat()}")
        if fact.entity_key:
            lines.append(f"- **EntityKey**: {fact.entity_key}")
        if fact.entity_value:
            lines.append(f"- **EntityValue**: {fact.entity_value}")
        lines.append("")
        lines.append(fact.content)
        lines.append("")
        return "\n".join(lines)

    def _parse_fact(self, section: str) -> Optional[MemoryFact]:
        """从 Markdown section 解析出 MemoryFact。"""
        lines = section.strip().split("\n")
        if not lines:
            return None

        # 第一行是 ### fact_id
        fact_id = lines[0].strip().lstrip("#").strip()
        if not fact_id:
            return None

        metadata = {}
        content_lines = []
        metadata_done = False

        for line in lines[1:]:
            if not metadata_done:
                match = re.match(r"-\s*\*\*(\w+)\*\*:\s*(.*)", line)
                if match:
                    key = match.group(1).lower()
                    val = match.group(2).strip()
                    metadata[key] = val
                    continue
                else:
                    # 第一个非 metadata 行（包括空行）：元数据块结束
                    metadata_done = True

            content_lines.append(line)

        content = "\n".join(content_lines).strip()
        if not content:
            return None

        try:
            fact_type = MemoryFactType(metadata.get("type", "research_finding"))
        except ValueError:
            fact_type = MemoryFactType.RESEARCH_FINDING

        created_str = metadata.get("created", "")
        try:
            created_at = datetime.fromisoformat(created_str) if created_str else datetime.now()
        except ValueError:
            created_at = datetime.now()

        expires_str = metadata.get("expires", "")
        try:
            expires_at = datetime.fromisoformat(expires_str) if expires_str else None
        except ValueError:
            expires_at = None

        tags_str = metadata.get("tags", "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

        try:
            confidence = float(metadata.get("confidence", "1.0"))
        except ValueError:
            confidence = 1.0

        try:
            weight = float(metadata.get("weight", "1.0"))
        except ValueError:
            weight = 1.0

        return MemoryFact(
            fact_id=fact_id,
            fact_type=fact_type,
            content=content,
            source_agent=metadata.get("agent", "system"),
            confidence=confidence,
            created_at=created_at,
            expires_at=expires_at,
            tags=tags,
            evidence_path=metadata.get("evidence"),
            locked=metadata.get("locked", "").lower() == "true",
            weight=weight,
            entity_key=metadata.get("entitykey"),
            entity_value=metadata.get("entityvalue"),
        )

    def _save_fact(self, fact: MemoryFact):
        """将单条事实写入对应文件。"""
        md = self._render_fact(fact)

        if fact.locked or TYPE_PRIORITY.get(fact.fact_type, 0.5) >= 0.8:
            # 高优先级 → MEMORY.md
            # High priority → MEMORY.md
            self._append_to_file(self.memory_file, md)
        else:
            # 日常事实 → daily 文件
            # Daily facts → daily files
            date_str = (
                fact.created_at.strftime("%Y-%m-%d")
                if isinstance(fact.created_at, datetime)
                else datetime.now().strftime("%Y-%m-%d")
            )
            daily_file = self.daily_dir / f"{date_str}.md"
            self._append_to_file(daily_file, md)

    def _append_to_file(self, path: Path, content: str):
        """追加到文件（创建 header 如果是新文件），使用 FileLock 防止多进程竞争。"""
        from filelock import FileLock

        try:
            with FileLock(str(path) + ".lock", timeout=10):
                existing = ""
                if path.exists():
                    existing = path.read_text(encoding="utf-8")

                if not existing:
                    header = f"# {path.stem.replace('_', ' ').title()}\n\n"
                    path.write_text(header + content, encoding="utf-8")
                else:
                    with open(path, "a", encoding="utf-8") as f:
                        f.write("\n" + content)
        except Exception as e:
            logger.error(f"_append_to_file 写入失败 {path}: {e}")

    def add_fact(self, fact: MemoryFact):
        self.facts = [f for f in self.facts if f.fact_id != fact.fact_id]
        self.facts.append(fact)
        self.facts.sort(key=lambda x: x.created_at)
        self._save_fact(fact)

    def get_facts(self, types: Optional[List[MemoryFactType]] = None, limit: int = 100) -> List[MemoryFact]:
        results = self.facts
        if types:
            results = [f for f in results if f.fact_type in types]
        return results[-limit:]

    def get_all_facts(self) -> List[MemoryFact]:
        return list(self.facts)

    def update_fact(self, fact_id: str, **kwargs):
        for i, fact in enumerate(self.facts):
            if fact.fact_id == fact_id:
                try:
                    self.facts[i] = fact.model_copy(update=kwargs)
                    self._rewrite_all()
                except Exception as e:
                    logger.warning(f"update_fact 校验失败: {e}")
                return
        logger.warning(f"update_fact: fact_id={fact_id} 不存在")

    def remove_fact(self, fact_id: str):
        before = len(self.facts)
        self.facts = [f for f in self.facts if f.fact_id != fact_id]
        if len(self.facts) < before:
            self._rewrite_all()

    def clear(self):
        self.facts = []
        # 清空文件
        if self.memory_file.exists():
            self.memory_file.write_text("", encoding="utf-8")
        for f in self.daily_dir.glob("*.md"):
            f.unlink()

    def reload(self):
        self._load()

    def _rewrite_all(self):
        """重写所有文件（用于 update/remove 后保持一致性），使用 FileLock 防止多进程竞争。"""  # Rewrite all files for consistency after update/remove, with FileLock to prevent race conditions
        from filelock import FileLock

        # 重写 MEMORY.md
        locked_high = [f for f in self.facts if f.locked or TYPE_PRIORITY.get(f.fact_type, 0.5) >= 0.8]
        try:
            with FileLock(str(self.memory_file) + ".lock", timeout=10):
                if locked_high:
                    content = "# Long-Term Memory (Curated)\n\n"
                    content += "\n".join(self._render_fact(f) for f in locked_high)
                    self.memory_file.write_text(content, encoding="utf-8")
                elif self.memory_file.exists():
                    self.memory_file.write_text("", encoding="utf-8")
        except Exception as e:
            logger.error(f"_rewrite_all: MEMORY.md 写入失败: {e}")

        # 重写 daily 文件
        daily_facts: Dict[str, List[MemoryFact]] = {}
        for f in self.facts:
            if not (f.locked or TYPE_PRIORITY.get(f.fact_type, 0.5) >= 0.8):
                date_str = (
                    f.created_at.strftime("%Y-%m-%d")
                    if isinstance(f.created_at, datetime)
                    else datetime.now().strftime("%Y-%m-%d")
                )
                daily_facts.setdefault(date_str, []).append(f)

        # 清空旧 daily 文件
        for f in self.daily_dir.glob("*.md"):
            f.unlink()

        # 写入新的
        for date_str, facts in sorted(daily_facts.items()):
            daily_file = self.daily_dir / f"{date_str}.md"
            lock_path = str(daily_file) + ".lock"
            try:
                with FileLock(lock_path, timeout=10):
                    content = f"# Daily Log {date_str}\n\n"
                    content += "\n".join(self._render_fact(f) for f in facts)
                    daily_file.write_text(content, encoding="utf-8")
            except Exception as e:
                logger.error(f"_rewrite_all: {daily_file.name} 写入失败: {e}")

    def get_by_priority(self, limit: int = 50) -> List[MemoryFact]:
        def sort_key(f: MemoryFact):
            base = TYPE_PRIORITY.get(f.fact_type, 0.5)
            return base * f.weight * f.confidence

        return sorted(self.facts, key=sort_key, reverse=True)[:limit]

    def get_locked(self) -> List[MemoryFact]:
        return [f for f in self.facts if f.locked]

    def prune_expired(self) -> int:
        now = datetime.now()
        before = len(self.facts)
        self.facts = [f for f in self.facts if f.expires_at is None or f.expires_at > now]
        removed = before - len(self.facts)
        if removed > 0:
            self._rewrite_all()
        return removed

    def cull_old_facts(self, max_per_type: Dict[MemoryFactType, int]):
        type_groups: Dict[MemoryFactType, List[MemoryFact]] = {}
        for f in self.facts:
            type_groups.setdefault(f.fact_type, []).append(f)

        new_facts = []
        for f_type, f_list in type_groups.items():
            limit = max_per_type.get(f_type)
            locked = [f for f in f_list if f.locked]
            unlocked = [f for f in f_list if not f.locked]
            if limit is not None and limit > 0:
                unlocked.sort(key=lambda x: x.weight * x.confidence, reverse=True)
                kept = unlocked[:limit]
            else:
                kept = unlocked
            new_facts.extend(locked + kept)

        self.facts = sorted(new_facts, key=lambda x: x.created_at)
        self._rewrite_all()
