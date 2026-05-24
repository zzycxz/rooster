"""JSON → Markdown 迁移工具：首次 v3 启动时自动将 JSON 存储转为 Markdown。"""

import json
import logging
import shutil
from pathlib import Path

from .models import MemoryFact, MemoryFactType

logger = logging.getLogger(__name__)

_MARKER_NAME = ".migrated_to_markdown"


def migrate_json_to_markdown(
    json_path: str = ".rooster/project_memory.json",
    base_dir: str = ".rooster",
) -> int:
    """
    将 JSON 后端的事实迁移到 Markdown 后端。
    返回迁移的事实数量。已完成则跳过。

    流程：备份 → 写入 → 写 marker，保证部分失败可重入。
    """
    json_file = Path(json_path)
    base = Path(base_dir)
    marker = base / _MARKER_NAME

    if marker.exists():
        return 0

    if not json_file.exists():
        return 0

    # 加载 JSON 事实
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"读取 JSON 文件失败: {e}")
        return 0

    # 兼容旧格式：data 可能是 list 或 dict
    if isinstance(data, dict):
        raw_facts = data.get("facts", [])
        legacy_facts = data.get("project_facts", [])
    elif isinstance(data, list):
        raw_facts = data
        legacy_facts = []
    else:
        return 0

    if not raw_facts and not legacy_facts:
        # 空数据，直接写 marker
        marker.write_text("empty", encoding="utf-8")
        return 0

    # Step 1: 备份（在任何写入之前）
    backup_path = json_file.with_suffix(".json.bak")
    if not backup_path.exists():
        try:
            shutil.copy2(json_file, backup_path)
            logger.info(f"原 JSON 文件已备份至 {backup_path}")
        except Exception as e:
            logger.warning(f"备份失败: {e}，继续迁移")

    # 初始化 Markdown 后端
    from .backends import MarkdownBackend

    md_backend = MarkdownBackend(base_dir=base_dir)

    # 读取已有 ID（支持部分迁移后重入）
    existing_ids = {f.fact_id for f in md_backend.facts}
    migrated = 0

    # Step 2: 迁移结构化事实
    for item in raw_facts:
        try:
            fact = MemoryFact.model_validate(item)
            if fact.fact_id not in existing_ids:
                md_backend.add_fact(fact)
                existing_ids.add(fact.fact_id)
                migrated += 1
        except Exception as e:
            logger.warning(f"跳过无效事实: {e}")

    # Step 3: 迁移旧格式字符串事实
    for i, content in enumerate(legacy_facts):
        fid = f"legacy_{i}"
        if fid in existing_ids:
            continue
        fact = MemoryFact(
            fact_id=fid,
            fact_type=MemoryFactType.RESEARCH_FINDING,
            content=content,
            source_agent="system",
            weight=0.8,
        )
        md_backend.add_fact(fact)
        existing_ids.add(fid)
        migrated += 1

    if migrated > 0:
        logger.info(f"JSON → Markdown 迁移完成: {migrated} 条事实")

    # Step 4: 写 marker（迁移完成标记）
    try:
        marker.write_text(f"migrated {migrated} facts", encoding="utf-8")
    except Exception:
        pass

    return migrated


def auto_migrate_if_needed(
    json_path: str = ".rooster/project_memory.json",
    base_dir: str = ".rooster",
    backend_type: str = "markdown",
) -> bool:
    """
    自动检测并执行迁移。仅在以下条件同时满足时执行：
    1. 目标后端是 markdown
    2. JSON 文件存在且有数据
    3. 迁移 marker 不存在
    返回是否执行了迁移。
    """
    if backend_type != "markdown":
        return False

    json_file = Path(json_path)
    if not json_file.exists():
        return False

    marker = Path(base_dir) / _MARKER_NAME
    if marker.exists():
        return False  # 已迁移

    # 检查 JSON 是否有数据
    try:
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        has_data = bool(
            (isinstance(data, dict) and (data.get("facts") or data.get("project_facts")))
            or (isinstance(data, list) and data)
        )
    except Exception:
        return False

    if not has_data:
        # 空 JSON，直接写 marker
        try:
            marker.write_text("empty", encoding="utf-8")
        except Exception:
            pass
        return False

    count = migrate_json_to_markdown(json_path, base_dir)
    return count > 0
