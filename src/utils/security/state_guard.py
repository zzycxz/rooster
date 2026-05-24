import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# --- Cross-platform file locking ---
_lock_impl = "threading"  # fallback

if sys.platform == "win32":
    try:
        import msvcrt

        _lock_impl = "msvcrt"
    except ImportError:
        pass
else:
    try:
        import fcntl

        _lock_impl = "fcntl"
    except ImportError:
        pass


def _file_lock(fd):
    if _lock_impl == "msvcrt":
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    elif _lock_impl == "fcntl":
        fcntl.flock(fd, fcntl.LOCK_EX)
    # threading fallback: no cross-process lock, threading.Lock handles in-process


def _file_unlock(fd):
    if _lock_impl == "msvcrt":
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    elif _lock_impl == "fcntl":
        fcntl.flock(fd, fcntl.LOCK_UN)


class StateGuard:
    """
    Rooster Global State Guard (RSA-Synchronizer) v2.0
    支持多线程/多进程状态同步、候选池管理及原子 IO。
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(StateGuard, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self, workspace_root: str = None):
        if self._initialized:
            return

        self.workspace_root = Path(workspace_root) if workspace_root else Path.cwd()
        self.state_dir = self.workspace_root / ".rooster" / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.lock_file = self.state_dir / "task_locks.json"
        self._init_lock_file()
        self._initialized = True

    def _init_lock_file(self):
        if not self.lock_file.exists():
            with open(self.lock_file, "w", encoding="utf-8") as f:
                json.dump(
                    {"active_locks": {}, "terminated_tasks": [], "candidate_pools": {}, "processed_messages": {}}, f
                )

    def is_message_seen(self, msg_id: str) -> bool:
        """检查消息 ID 是否已被永久记录"""
        data = self._read_locks()
        processed = data.get("processed_messages", {})
        return msg_id in processed

    def mark_message_seen(self, msg_id: str):
        """物理记录消息 ID，防止重启后重播"""
        with self._lock:
            data = self._read_locks()
            if "processed_messages" not in data:
                data["processed_messages"] = {}

            # 记录时间戳
            data["processed_messages"][msg_id] = datetime.now().isoformat()

            # 控制记录规模：只保留最近 1000 条消息
            if len(data["processed_messages"]) > 1000:
                oldest_key = next(iter(data["processed_messages"]))
                del data["processed_messages"][oldest_key]

            self._write_locks(data)

    def add_candidate(self, task_id: str, soi_id: str, metadata: dict):
        """将一个初步验证有效的资源存入候选池。"""
        with self._lock:
            data = self._read_locks()
            if "candidate_pools" not in data:
                data["candidate_pools"] = {}

            if task_id not in data["candidate_pools"]:
                data["candidate_pools"][task_id] = []

            # 避免重复添加
            if any(c["soi_id"] == soi_id for c in data["candidate_pools"][task_id]):
                return

            data["candidate_pools"][task_id].append(
                {"soi_id": soi_id, "metadata": metadata, "timestamp": datetime.now().isoformat(), "status": "available"}
            )
            self._write_locks(data)

    def get_next_candidate(self, task_id: str):
        """获取下一个可用的候选资源（用于回溯重试）。"""
        with self._lock:
            data = self._read_locks()
            candidates = data.get("candidate_pools", {}).get(task_id, [])
            for c in candidates:
                if c["status"] == "available":
                    c["status"] = "consumed"
                    self._write_locks(data)
                    return c
            return None

    def acquire_lock(self, task_id: str, soi_id: str, agent_id: str) -> bool:
        """尝试锁定一个 SOI 实体。如果已被锁定则返回 False。"""
        with self._lock:
            data = self._read_locks()
            if soi_id in data["active_locks"]:
                owner = data["active_locks"][soi_id]["agent_id"]
                if owner != agent_id:
                    return False

            data["active_locks"][soi_id] = {
                "task_id": task_id,
                "agent_id": agent_id,
                "timestamp": datetime.now().isoformat(),
            }
            self._write_locks(data)
            return True

    def set_terminate_signal(self, task_id: str, is_group: bool = False):
        """
        设置短路信号。
        - task_id: 具体的任务 ID 或 组 ID。
        - is_group: 如果为 True，则视为组 ID，所有属于该组的任务都应停止。
        """
        with self._lock:
            data = self._read_locks()
            key = "terminated_groups" if is_group else "terminated_tasks"
            if key not in data:
                data[key] = []

            if task_id not in data[key]:
                data[key].append(task_id)
            self._write_locks(data)

    def should_terminate(self, task_id: str, group_id: Optional[str] = None) -> bool:
        """检查任务或其所属组是否应停止。"""
        data = self._read_locks()
        if task_id in data.get("terminated_tasks", []):
            return True
        if group_id and group_id in data.get("terminated_groups", []):
            return True
        return False

    def release_locks(self, task_id: str, group_id: Optional[str] = None):
        """物理清理：释放锁并清空相关信号。"""
        with self._lock:
            data = self._read_locks()
            data["active_locks"] = {k: v for k, v in data["active_locks"].items() if v["task_id"] != task_id}

            if task_id in data.get("terminated_tasks", []):
                data["terminated_tasks"].remove(task_id)

            if group_id and group_id in data.get("terminated_groups", []):
                data["terminated_groups"].remove(group_id)

            if task_id in data.get("candidate_pools", {}):
                del data["candidate_pools"][task_id]
            self._write_locks(data)

    def _read_locks(self):
        """带跨进程锁的文件读取。"""
        if not self.lock_file.exists():
            self._init_lock_file()

        try:
            with open(self.lock_file, "r", encoding="utf-8") as f:
                _file_lock(f.fileno())
                try:
                    data = json.load(f)
                    if not isinstance(data, dict):
                        return {
                            "active_locks": {},
                            "terminated_tasks": [],
                            "candidate_pools": {},
                            "processed_messages": {},
                        }
                    return data
                finally:
                    try:
                        _file_unlock(f.fileno())
                    except OSError as e:
                        logger.debug("file unlock failed: %s", e)
        except (json.JSONDecodeError, IOError):
            return {"active_locks": {}, "terminated_tasks": [], "candidate_pools": {}, "processed_messages": {}}

    def _write_locks(self, data):
        """带跨进程锁的文件写入。"""
        try:
            with open(self.lock_file, "r+", encoding="utf-8") as f:
                _file_lock(f.fileno())
                try:
                    f.seek(0)
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.truncate()
                finally:
                    try:
                        _file_unlock(f.fileno())
                    except OSError as e:
                        logger.debug("file unlock failed: %s", e)
        except FileNotFoundError:
            with open(self.lock_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)


# Global Accessor
state_guard = StateGuard()
