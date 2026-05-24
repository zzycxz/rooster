import os
import shutil
import asyncio
import datetime
import logging
from typing import Any
from concurrent.futures import ThreadPoolExecutor

from utils.config import settings

logger = logging.getLogger(__name__)

# 懒加载 SecretsMask（避免循环导入）
# Lazy-load SecretsMask (avoid circular import)
_secrets_mask = None


def _get_mask():
    global _secrets_mask
    if _secrets_mask is None:
        try:
            from utils.security.secrets_mask import secrets_mask

            _secrets_mask = secrets_mask
        except Exception:
            _secrets_mask = None
    return _secrets_mask


class AuditManager:
    """
    Rooster BlackBox Audit 审计管理器。
    采用异步队列模式，确保主推理循环零延迟。
    """

    _instance = None
    _executor = ThreadPoolExecutor(max_workers=2)

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(AuditManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.enabled = settings.AUDIT_LOG_ENABLED
        self.root_path = os.path.abspath(os.path.join(os.getcwd(), ".rooster", "logs"))
        self.queue = asyncio.Queue()
        self.worker_task = None
        self._ref_count = 0
        self._ref_lock = asyncio.Lock()
        self._initialized = True

        if self.enabled:
            if not os.path.exists(self.root_path):
                os.makedirs(self.root_path, exist_ok=True)
            logger.info(f"🛡️ Audit System Initialized. Root: {self.root_path}")

    async def start_worker(self):
        """启动后台持久化任务（引用计数）"""
        if not self.enabled:
            return
        async with self._ref_lock:
            self._ref_count += 1
            if self.worker_task is None:
                self.worker_task = asyncio.create_task(self._process_queue())
                logger.info("🚀 Audit Worker started.")

    async def stop_worker(self):
        """停止后台任务（引用计数归零时才真正停止）"""
        if not self.enabled:
            return
        async with self._ref_lock:
            self._ref_count = max(0, self._ref_count - 1)
            if self._ref_count == 0 and self.worker_task:
                await self.queue.put(None)
                await self.worker_task
                self.worker_task = None
                logger.info("🛑 Audit Worker stopped.")

    async def _process_queue(self):
        while True:
            item = await self.queue.get()
            if item is None:
                self.queue.task_done()
                break

            try:
                op_type = item.get("type")
                if op_type == "write_file":
                    await self._async_write(item["path"], item["content"], item["binary"])
                elif op_type == "cleanup":
                    await self._async_cleanup()
            except Exception as e:
                logger.error(f"❌ Audit Worker Error: {str(e)}")
            finally:
                self.queue.task_done()

    async def _async_write(self, path: str, content: Any, binary: bool = False):
        def _write():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            mode = "wb" if binary else "w"
            encoding = None if binary else "utf-8"
            with open(path, mode, encoding=encoding) as f:
                if binary:
                    f.write(content)
                else:
                    f.write(str(content))

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _write)

    async def _async_cleanup(self):
        """执行日志清理逻辑"""

        def _cleanup():
            if not os.path.exists(self.root_path):
                return

            now = datetime.datetime.now()
            retention_days = settings.AUDIT_LOG_RETENTION_DAYS
            count = 0

            # 日期目录格式: YYYY-MM-DD
            # Date directory format: YYYY-MM-DD
            for date_dir in os.listdir(self.root_path):
                dir_path = os.path.join(self.root_path, date_dir)
                if not os.path.isdir(dir_path):
                    continue

                try:
                    dir_date = datetime.datetime.strptime(date_dir, "%Y-%m-%d")
                    delta = now - dir_date
                    if delta.days >= retention_days:
                        logger.info(f"🧹 Purging old audit logs: {date_dir}")
                        shutil.rmtree(dir_path)
                        count += 1
                except ValueError:
                    # 不是日期格式的文件夹，跳过
                    # Not a date-formatted folder, skip
                    continue

            if count > 0:
                logger.info(f"✨ Audit cleanup finished. Removed {count} day(s) of logs.")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, _cleanup)

    def log_step_detail(self, session_id: str, step: int, filename: str, content: Any, binary: bool = False):
        """
        提交一个写入任务到队列。
        """
        if not self.enabled:
            return

        # 根据配置开关过滤项
        # Filter by config toggles
        if filename == "prompt_full.md" and not settings.AUDIT_SAVE_PROMPT:
            return
        if filename == "raw_llm_out.txt" and not settings.AUDIT_SAVE_RAW:
            return
        if filename.endswith(".png") and not settings.AUDIT_SAVE_SCREENSHOT:
            return
        # telemetry 以后单独实现
        # Telemetry to be implemented separately later

        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(self.root_path, today, session_id, f"step_{step:03d}", filename)

        # 对非二进制内容应用 SecretsMask（审计日志不应含明文密钥）
        # Apply SecretsMask to non-binary content (audit logs must not contain plaintext keys)
        if not binary:
            mask = _get_mask()
            if mask is not None:
                if isinstance(content, dict):
                    content = mask.mask_dict(content)
                elif isinstance(content, str):
                    content = mask.mask(content)

        # 放入队列
        self.queue.put_nowait({"type": "write_file", "path": file_path, "content": content, "binary": binary})

    def trigger_cleanup(self):
        """手动触发清理检测"""
        if not self.enabled:
            return
        self.queue.put_nowait({"type": "cleanup"})


# 单例导出
# Singleton export
audit_manager = AuditManager()
