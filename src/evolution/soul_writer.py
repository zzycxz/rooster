import os
import logging
from datetime import datetime
from filelock import FileLock

logger = logging.getLogger(__name__)


class SoulWriter:
    """
    SOUL.md 字段级追加器。
    支持白名单、文件锁、去重和 Git 版本保护逻辑预留。
    """

    def __init__(self, soul_path: str = ".rooster/SOUL.md"):
        self.soul_path = os.path.abspath(soul_path)
        self.lock_path = self.soul_path + ".lock"
        # 允许进化的白名单章节
        # Whitelist sections allowed for evolution
        self.whitelist_sections = ["## 核心行为原则", "## 语气与风格"]

    def append_insight(self, section_name: str, insight: str) -> bool:
        """
        向指定章节追加一条进化洞察。
        """
        # Append an evolution insight to the specified section
        if section_name not in self.whitelist_sections:
            logger.warning(f"🚫 试图修改黑名单章节: {section_name}")
            return False

        if not insight:
            return False

        lock = FileLock(self.lock_path, timeout=5)
        try:
            with lock:
                with open(self.soul_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                # 寻找章节位置
                # Find section position
                section_idx = -1
                for i, line in enumerate(lines):
                    if line.strip() == section_name:
                        section_idx = i
                        break

                if section_idx == -1:
                    logger.error(f"❌ 未找到章节: {section_name}")
                    return False

                # 构造新条目
                # Construct new entry
                timestamp = datetime.now().strftime("%Y-%m-%d")
                new_entry = f"- [{timestamp} 进化引擎] {insight}\n"

                # 重复检测（相似度简单检查）
                # Duplicate detection (simple similarity check)
                for i in range(section_idx + 1, len(lines)):
                    if lines[i].startswith("##"):
                        break  # 进入下一章了
                    if insight[:20] in lines[i]:  # 简单的前缀匹配去重
                        logger.info(f"⏭️ 洞察相似度过高，跳过追加: {insight[:30]}...")
                        return True

                # 插入到章节末尾
                # Insert at end of section
                insert_pos = section_idx + 1
                while insert_pos < len(lines) and not lines[insert_pos].startswith("##"):
                    insert_pos += 1

                # 在章节末尾插入
                # Insert at end of section
                lines.insert(insert_pos, new_entry)

                # 写回文件
                # Write back to file
                with open(self.soul_path, "w", encoding="utf-8") as f:
                    f.writelines(lines)

                logger.info(f"🧬 已成功进化 SOUL.md -> {section_name}: {insight[:30]}...")
                return True

        except Exception as e:
            logger.error(f"❌ 进化写回失败: {str(e)}")
            return False
