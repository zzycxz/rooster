# src/utils/vault_archiver.py
import os
import shutil
import logging
from datetime import datetime
from typing import Optional

from utils.system import sanitize_path_name

logger = logging.getLogger(__name__)


class VaultArchiver:
    """
    主权归档器 (VaultArchiver)：
    职能：管理会话证据的生命周期，实现文件的物理归档与溯源。
    目录结构：.rooster/evidence/YYYYMMDD/SESSIONID/
    """

    def __init__(self, evidence_root: str, session_id: str):
        self.evidence_root = evidence_root
        # 使用全局统一的路径安全清洗 (Rooster Standard)
        # Use globally unified path sanitization (Rooster Standard)
        self.session_id = sanitize_path_name(session_id)
        self.date_str = datetime.now().strftime("%Y%m%d")
        self.archive_dir = os.path.join(self.evidence_root, self.date_str, self.session_id)
        self._ensure_dir()

    def _ensure_dir(self):
        try:
            os.makedirs(self.archive_dir, exist_ok=True)
            logger.info(f"📂 [Vault] 归档目录就绪: {self.archive_dir}")
        except Exception as e:
            logger.error(f"❌ [Vault] 无法创建归档目录: {e}")

    def archive_file(self, source_path: str) -> Optional[str]:
        """
        将文件移动到归档目录。
        返回新的归档路径。
        """
        if not source_path or not os.path.exists(source_path):
            return None

        try:
            filename = os.path.basename(source_path)
            # 避免同一会话内同名文件覆盖，允许通过时间戳区分或直接覆盖（视场景而定）
            # Avoid same-name file collisions within a session; allow timestamp disambiguation or overwrite
            # 当前逻辑：在该会话目录下同名文件会被覆盖或重构
            # Current logic: same-name files in session dir are overwritten or reconstructed
            dest_path = os.path.join(self.archive_dir, filename)

            # 如果源文件已经在目标位置，直接返回
            if os.path.abspath(source_path) == os.path.abspath(dest_path):
                return dest_path

            # 如果目标已存在，先删除（避免移动失败）
            # If destination exists, delete first (avoid move failure)
            if os.path.exists(dest_path):
                os.remove(dest_path)

            shutil.move(source_path, dest_path)
            logger.info(f"📦 [Vault] 已归档: {filename} -> {self.archive_dir}")
            return dest_path
        except Exception as e:
            logger.warning(f"⚠️ [Vault] 归档操作失败: {e}")
            return source_path  # 降级返回原路径
            # Fallback: return original path

    def get_archive_relative_path(self) -> str:
        """返回相对于项目根目录的归档路径，用于展示"""
        return os.path.relpath(self.archive_dir, os.getcwd())
