import os
import stat
from typing import List, Optional

_UNRESTRICTED_MARKER = "*"


class PathGuard:
    """
    Path safety guard.
    Ensures AI agents can only access whitelisted directories.

    Hardening:
    - Uses os.path.realpath (resolves symlinks) instead of os.path.abspath
    - Proper prefix matching with os.sep to prevent substring bypass
    - Empty whitelist = deny all; use ["*"] for unrestricted mode
    - Locks state file permissions to owner-only (0600)
    """

    _global_instance: Optional["PathGuard"] = None

    def __init__(self, allowed_paths: List[str]):
        if allowed_paths == [_UNRESTRICTED_MARKER]:
            self._unrestricted = True
            self.allowed_paths = []
        else:
            self._unrestricted = False
            self.allowed_paths = [os.path.realpath(p) for p in allowed_paths if p.strip()]

    @classmethod
    def get_global(cls) -> "PathGuard":
        """全局单例，从 os.environ 实时读取 ALLOWED_PATHS。供 tool_dispatch 等全局路径检查使用。"""
        if cls._global_instance is None:
            try:
                raw = os.environ.get("ALLOWED_PATHS", "")
                if raw.strip() == "*":
                    paths = ["*"]
                elif raw.strip():
                    paths = [p.strip() for p in raw.split(",") if p.strip()]
                else:
                    paths = [os.getcwd()]
            except Exception:
                paths = ["*"]
            cls._global_instance = cls(paths)
        return cls._global_instance

    @classmethod
    def refresh(cls):
        """强制重建全局单例，用于配置热重载。"""
        cls._global_instance = None

    def is_safe(self, target_path: str) -> bool:
        if self._unrestricted:
            return True

        if not self.allowed_paths:
            return False

        try:
            abs_target = os.path.realpath(target_path)
            for allowed in self.allowed_paths:
                if abs_target == allowed or abs_target.startswith(allowed + os.sep):
                    return True
            return False
        except Exception:
            return False

    def get_safe_path(self, target_path: str) -> str:
        if self.is_safe(target_path):
            return os.path.realpath(target_path)
        raise PermissionError(f"Access Denied: Path '{target_path}' is outside of allowed directories.")


def secure_file_permissions(path: str):
    """Set file to owner-read-write only (0600). No-op on non-Unix."""
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except (OSError, AttributeError):
        pass
