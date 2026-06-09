import os
import hashlib
import logging
from typing import Optional, Type, Any
from pydantic import BaseModel, Field
from toolset.base import BaseTool
from utils.config import settings

logger = logging.getLogger(__name__)


class FileSystemOpArgs(BaseModel):
    action: str = Field(description="操作类型: read, write, list, search, mkdir, download, hash")
    path: str = Field(description="目标路径（download 时为目标保存路径）")
    content: Optional[str] = Field(description="[write专用] 要写入的文本内容", default=None)
    append: Optional[bool] = Field(description="[write专用] 是否追加模式，默认 False", default=False)
    pattern: Optional[str] = Field(description="[search专用] 搜索关键词", default=None)
    recursive: Optional[bool] = Field(description="[search专用] 是否递归，默认 True", default=True)
    url: Optional[str] = Field(description="[download专用] 下载链接", default=None)
    algorithm: Optional[str] = Field(description="[hash专用] 算法 md5/sha1/sha256，默认 md5", default="md5")


class FileSystemOpTool(BaseTool):
    """
    文件系统全能宏工具 (Omni-Tool)
    """

    name: str = "file_system_op"
    kit: str = "FileSystem"
    risk_level: str = "high"
    description: str = (
        "File System operations. Actions:\n"
        "- 'read': Read a text file's content. Requires: path.\n"
        "- 'write': Write or append text to a file. Requires: path, content. Optional: append (bool).\n"
        "- 'list': List directory contents. Requires: path.\n"
        "- 'search': Search files by name pattern. Requires: path, pattern. Optional: recursive.\n"
        "- 'mkdir': Create a directory (recursive). Requires: path.\n"
        "- 'download': Download a file from URL to local path. Requires: url, path.\n"
        "- 'hash': Calculate file hash (md5/sha256). Requires: path. Optional: algorithm.\n"
        "NOT for: Excel/PDF/Word files (use excel_op/pdf_op/office_docx_write), web browsing (use browser_nav)."
    )
    domain: str = "craft"
    args_schema: Type[BaseModel] = FileSystemOpArgs

    def _resolve_path_guard(self, ctx):
        """优先从注入的 ctx 获取 path_guard，回退到 self.context / self.path_guard"""
        if ctx and hasattr(ctx, "security_policy") and ctx.security_policy:
            guard = ctx.security_policy.get("path_guard")
            if guard:
                return guard
        return self.path_guard

    async def run(self, **kwargs) -> Any:
        action = kwargs.get("action", "").lower()
        path = kwargs.get("path")

        if not action or not path:
            return "Error: 'action' and 'path' are required arguments."

        # 支持注入的 ctx 和旧式 path_guard 两种来源
        # Support both injected ctx and legacy path_guard sources
        ctx = kwargs.get("ctx")
        path_guard = self._resolve_path_guard(ctx)
        safe_path = path_guard.get_safe_path(path) if path_guard else path

        try:
            if action == "list":
                return os.listdir(safe_path)

            elif action == "read":
                with open(safe_path, "r", encoding="utf-8") as f:
                    return f.read()

            elif action == "write":
                content = kwargs.get("content", "")
                append_mode = kwargs.get("append", False)
                parent_dir = os.path.dirname(os.path.abspath(safe_path))
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                mode = "a" if append_mode else "w"
                with open(safe_path, mode, encoding="utf-8") as f:
                    f.write(content)
                return f"Successfully written to {path}"

            elif action == "mkdir":
                os.makedirs(safe_path, exist_ok=True)
                return f"Successfully created directory: {path}"

            elif action == "search":
                pattern = kwargs.get("pattern", "")
                if not pattern:
                    return "Error: 'pattern' is required for search action."
                recursive = kwargs.get("recursive", True)
                matches = []
                if recursive:
                    for root, dirs, files in os.walk(safe_path):
                        for file in files:
                            if pattern.lower() in file.lower():
                                matches.append(os.path.relpath(os.path.join(root, file), safe_path))
                else:
                    for file in os.listdir(safe_path):
                        if os.path.isfile(os.path.join(safe_path, file)) and pattern.lower() in file.lower():
                            matches.append(file)
                return matches if matches else ["No matches found."]

            elif action == "download":
                url = kwargs.get("url")
                if not url:
                    return "Error: 'url' is required for download action."
                import httpx

                os.makedirs(os.path.dirname(os.path.abspath(safe_path)), exist_ok=True)
                headers = {}
                mode = "wb"
                downloaded = 0
                if os.path.exists(safe_path):
                    downloaded = os.path.getsize(safe_path)
                    headers["Range"] = f"bytes={downloaded}-"
                    mode = "ab"
                async with httpx.AsyncClient(timeout=3600.0, follow_redirects=True, verify=not getattr(settings, 'INSECURE_SKIP_TLS', False)) as client:
                    async with client.stream("GET", url, headers=headers) as response:
                        if response.status_code == 416:
                            return f"✅ File at `{safe_path}` already fully downloaded."
                        response.raise_for_status()
                        content_type = response.headers.get("Content-Type", "").lower()
                        if "text/html" in content_type and (
                            ".mp4" in url.lower() or ".mkv" in url.lower() or ".zip" in url.lower()
                        ):
                            return "❌ Download Blocked: URL returned HTML page."
                        with open(safe_path, mode) as f:
                            async for chunk in response.aiter_bytes(chunk_size=1024 * 1024):
                                f.write(chunk)
                                downloaded += len(chunk)
                if downloaded < 1024 * 1024 and (".mp4" in safe_path.lower() or ".mkv" in safe_path.lower()):
                    os.remove(safe_path)
                    return f"❌ Downloaded file too small ({downloaded / 1024:.2f} KB). Deleted."
                return f"✅ Successfully downloaded to `{safe_path}` ({downloaded / (1024 * 1024):.2f} MB)"

            elif action == "hash":
                algo = kwargs.get("algorithm", "md5").lower()
                if not os.path.exists(safe_path):
                    return f"Error: File not found at {path}"
                hash_func = (
                    hashlib.md5() if algo == "md5" else (hashlib.sha256() if algo == "sha256" else hashlib.sha1())
                )
                with open(safe_path, "rb") as f:
                    for chunk in iter(lambda: f.read(4096), b""):
                        hash_func.update(chunk)
                return f"RESULT_PATH: {path}\nHash ({algo}): {hash_func.hexdigest()}"

            else:
                return f"Error: Unknown action '{action}'"

        except Exception as e:
            return f"Error executing '{action}': {str(e)}"
