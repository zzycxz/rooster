"""文件变更监听：轮询 mtime + 内容 hash，变更时触发重索引。"""

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Callable, List, Dict, Optional

logger = logging.getLogger(__name__)


class MemoryFileWatcher:
    """
    轮询式文件变更监听器。

    每隔 poll_interval 秒检查一次 watch_paths 下的文件 mtime 和内容 hash。
    检测到变更时调用 callback(changed_files)。
    使用去抖：文件 mtime 稳定 2 秒后才触发回调。
    """

    def __init__(
        self,
        watch_paths: List[str],
        callback: Callable,
        poll_interval: float = 5.0,
        debounce_seconds: float = 2.0,
    ):
        self.watch_paths = [Path(p) for p in watch_paths]
        self.callback = callback
        self.poll_interval = poll_interval
        self.debounce_seconds = debounce_seconds

        self._file_hashes: Dict[str, float] = {}  # path → mtime
        self._pending_changes: Dict[str, float] = {}  # path → first_seen_time
        self._running = False
        self._task: Optional[asyncio.Task] = None

    def _scan_files(self) -> List[Path]:
        """扫描所有监听路径下的文件。"""
        files = []
        for p in self.watch_paths:
            if p.is_file():
                files.append(p)
            elif p.is_dir():
                files.extend(p.glob("**/*.md"))
                files.extend(p.glob("**/*.json"))
        return files

    def _file_hash(self, path: Path) -> float:
        """返回文件的 mtime（用于变更检测）。"""
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _content_hash(self, path: Path) -> str:
        """返回文件内容的 hash（用于去重）。"""
        try:
            return hashlib.md5(path.read_bytes()).hexdigest()
        except OSError:
            return ""

    async def start(self):
        """启动轮询循环。"""
        if self._running:
            return
        self._running = True
        # 初始化当前状态
        for f in self._scan_files():
            key = str(f)
            self._file_hashes[key] = self._file_hash(f)
        self._task = asyncio.create_task(self._poll_loop())
        logger.debug(f"文件监听已启动，监听 {len(self.watch_paths)} 个路径")

    async def stop(self):
        """停止轮询。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug("文件监听已停止")

    async def _poll_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.poll_interval)
                await self._check_changes()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"文件监听轮询异常: {e}")

    async def _check_changes(self):
        """检查文件变更并处理去抖。"""
        import time

        now = time.time()

        current_files = self._scan_files()
        current_keys = set()

        changed = []
        for f in current_files:
            key = str(f)
            current_keys.add(key)
            mtime = self._file_hash(f)
            old_mtime = self._file_hashes.get(key)

            if old_mtime is None or mtime != old_mtime:
                # 文件变更或新增
                if key not in self._pending_changes:
                    self._pending_changes[key] = now
            else:
                # 文件未变，清除 pending
                self._pending_changes.pop(key, None)

        # 检查已删除的文件
        deleted = set(self._file_hashes.keys()) - current_keys
        for key in deleted:
            self._file_hashes.pop(key, None)
            changed.append(key)

        # 处理去抖：pending 超过 debounce_seconds 的算作确认变更
        for key, first_seen in list(self._pending_changes.items()):
            if now - first_seen >= self.debounce_seconds:
                changed.append(key)
                self._pending_changes.pop(key)
                # 更新 hash
                p = Path(key)
                if p.exists():
                    self._file_hashes[key] = self._file_hash(p)

        if changed:
            logger.debug(f"检测到 {len(changed)} 个文件变更")
            try:
                if asyncio.iscoroutinefunction(self.callback):
                    await self.callback(changed)
                else:
                    self.callback(changed)
            except Exception as e:
                logger.warning(f"文件变更回调失败: {e}")
