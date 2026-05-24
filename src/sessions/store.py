import logging
import json
from typing import Dict, Optional, Any
from pathlib import Path
from sessions.models import Session
from .epilogue import SessionEpilogue

logger = logging.getLogger(__name__)


class SessionStore:
    _instance: Optional["SessionStore"] = None

    def __init__(self, storage_dir: str = ".rooster/sessions"):
        # 会话存档目录
        # Session archive directory
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        # 内存缓存
        # In-memory cache
        self._sessions: Dict[str, Session] = {}

        # 启动时预加载磁盘会话
        # Preload sessions from disk on startup
        self._load_from_disk()
        self.epilogue = SessionEpilogue()

    @classmethod
    def get_instance(cls) -> "SessionStore":
        """获取全局唯一实例"""
        return global_session_store

    def _load_from_disk(self):
        """扫描存储目录，让 Rooster 恢复记忆"""
        # 清理上次崩溃遗留的临时文件
        # Clean up leftover temp files from previous crash
        for tmp_p in self.storage_dir.glob("*.json.tmp"):
            try:
                tmp_p.unlink()
                logger.debug(f"清理遗留临时文件: {tmp_p.name}")
            except Exception:
                pass

        count = 0
        for p in self.storage_dir.glob("*.json"):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    session = Session.model_validate(data)
                    self._sessions[session.session_id] = session
                    count += 1
            except Exception as e:
                logger.error(f"❌ 加载会话 {p.name} 失败: {e}")
        if count > 0:
            logger.info(f"🧠 记忆恢复：成功从磁盘加载了 {count} 个历史会话。")

    def save_session(self, session_id: str):
        """将单个会话原子写入磁盘（写临时文件后重命名，避免写入中断导致数据损坏）"""
        session = self._sessions.get(session_id)
        if not session:
            return

        file_path = self.storage_dir / f"{session_id}.json"
        tmp_path = file_path.with_suffix(".json.tmp")
        try:
            tmp_path.write_text(session.model_dump_json(indent=2), encoding="utf-8")
            tmp_path.replace(file_path)
        except Exception as e:
            logger.error(f"❌ 序列化会话 {session_id} 失败: {e}")
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def get_session(self, session_id: str) -> Optional[Session]:
        """获取现有会话"""
        return self._sessions.get(session_id)

    def create_session(self, session_id: str, metadata: Optional[Dict] = None) -> Session:
        """创建并存储新会话"""
        session = Session(session_id=session_id, metadata=metadata or {})
        self._sessions[session_id] = session
        logger.info(f"New session created: {session_id}")
        self.save_session(session_id)  # 初始创建即存档
        # Persist immediately on creation
        return session

    def get_or_create(self, session_id: str) -> Session:
        """获取或创建会话；使用 setdefault 保证同一 session_id 只被初始化一次。"""
        if session_id in self._sessions:
            return self._sessions[session_id]
        new_session = Session(session_id=session_id, metadata={})
        existing = self._sessions.setdefault(session_id, new_session)
        if existing is new_session:
            logger.info(f"New session created: {session_id}")
            self.save_session(session_id)
        return existing

    def list_sessions(self) -> Dict[str, Session]:
        return self._sessions

    async def delete_session(
        self, session_id: str, memory_manager: Optional[Any] = None, llm_client: Optional[Any] = None
    ):
        """销毁会话（先执行终章处理，再清理内存和磁盘）"""
        try:
            if session_id in self._sessions and memory_manager and llm_client:
                history = [{"role": m.role, "content": m.content} for m in self._sessions[session_id].history]
                await self.epilogue.finalize_session(session_id, history, memory_manager, llm_client)
        finally:
            self._sessions.pop(session_id, None)
            file_path = self.storage_dir / f"{session_id}.json"
            if file_path.exists():
                file_path.unlink()
                logger.info(f"Session deleted from disk: {session_id}")

    def clear(self):
        """清空全场"""
        # 为了安全，clear 只清空内存，不删除磁盘文件，除非明确需要
        # For safety, clear only in-memory; disk files preserved unless explicitly requested
        self._sessions.clear()
        logger.info("Memory sessions cleared, disk archives intact.")


# 全局单例
# Global singleton
global_session_store = SessionStore()
