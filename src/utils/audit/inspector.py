# src/utils/evidence_inspector.py
import os
import logging
import base64
import pandas as pd
import docx
from pypdf import PdfReader
from typing import Dict, Any

logger = logging.getLogger(__name__)


class EvidenceInspector:
    """
    证据分析器 (EvidenceInspector)：
    职能：根据文件类型对执行证据进行"提炼"，为审计官提供可读的上下文。
    """

    SUPPORTED_IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
    SUPPORTED_DATA = {".xlsx", ".xls", ".csv"}
    SUPPORTED_DOCS = {".docx", ".pdf"}
    SUPPORTED_TEXT = {".txt", ".md", ".py", ".json", ".yaml", ".yml", ".log"}

    @classmethod
    def inspect(cls, file_path: str) -> Dict[str, Any]:
        """
        分析文件并返回负载。
        返回格式：{
            "type": "image|text|metadata",
            "content": "...",
            "metadata": {...}
        }
        """
        if not file_path or not os.path.exists(file_path):
            return {"type": "error", "content": "文件不存在"}

        ext = os.path.splitext(file_path)[1].lower()
        file_size = os.path.getsize(file_path)

        # 基本元数据
        # Basic metadata
        metadata = {"filename": os.path.basename(file_path), "size_bytes": file_size, "extension": ext}

        # 1. 处理视觉证据
        # 1. Handle visual evidence
        if ext in cls.SUPPORTED_IMAGES:
            return cls._handle_image(file_path, metadata)

        # 2. 处理结构化数据
        # 2. Handle structured data
        if ext in cls.SUPPORTED_DATA:
            return cls._handle_data(file_path, metadata)

        # 3. 处理文档证据
        # 3. Handle document evidence
        if ext in cls.SUPPORTED_DOCS:
            if ext == ".docx":
                return cls._handle_docx(file_path, metadata)
            else:
                return cls._handle_pdf(file_path, metadata)

        # 4. 处理文本证据
        # 4. Handle text evidence
        if ext in cls.SUPPORTED_TEXT:
            return cls._handle_text(file_path, metadata)

        # 5. 其他类型（仅返回元数据）
        # 5. Other types (return metadata only)
        return {
            "type": "metadata",
            "content": f"已确认物理生成文件: {metadata['filename']} ({file_size} bytes)",
            "metadata": metadata,
        }

    @classmethod
    def _handle_image(cls, path: str, metadata: dict) -> dict:
        try:
            with open(path, "rb") as f:
                encoded = base64.b64encode(f.read()).decode("utf-8")
            return {"type": "image", "content": encoded, "metadata": metadata}
        except Exception as e:
            return {"type": "error", "content": f"图片读取失败: {e}"}

    @classmethod
    def _handle_data(cls, path: str, metadata: dict) -> dict:
        try:
            from utils.config import settings

            strictness = getattr(settings, "AUDIT_STRICTNESS", "medium").lower()

            # 动态调整采样深度
            # Dynamically adjust sampling depth
            nrows = 50 if strictness == "strict" else 5

            ext = metadata["extension"]
            if ext == ".csv":
                df = pd.read_csv(path, nrows=nrows)
            else:
                df = pd.read_excel(path, nrows=nrows)

            summary = df.to_markdown(index=False)
            info = f"数据文件摘要 ({strictness} 模式, 前 {len(df)} 行):\n\n{summary}\n\n[属性] 列名: {list(df.columns)}"
            return {"type": "text", "content": info, "metadata": metadata}
        except Exception as e:
            logger.warning(f"⚠️ [Inspector] 数据解析失败: {e}")
            return {"type": "text", "content": f"无法提取数据预览，但文件已存在。错误: {e}", "metadata": metadata}

    @classmethod
    def _handle_docx(cls, path: str, metadata: dict) -> dict:
        try:
            doc = docx.Document(path)
            full_text = []
            for para in doc.paragraphs:
                full_text.append(para.text)

            content = "\n".join(full_text)
            char_limit = 3000
            preview = content[:char_limit] + ("..." if len(content) > char_limit else "")

            return {
                "type": "text",
                "content": f"Word 文档预览 ({metadata['filename']}):\n{preview}",
                "metadata": metadata,
            }
        except Exception as e:
            logger.warning(f"⚠️ [Inspector] Word 解析失败: {e}")
            return {"type": "text", "content": f"Word 解析失败，但文件已存在: {e}", "metadata": metadata}

    @classmethod
    def _handle_pdf(cls, path: str, metadata: dict) -> dict:
        try:
            with open(path, "rb") as f:
                reader = PdfReader(f)
                num_pages = len(reader.pages)
                # 仅提取第一页作为预览
                # Extract only first page as preview
                first_page = reader.pages[0].extract_text()

            return {
                "type": "text",
                "content": f"PDF 文档预览 (共 {num_pages} 页):\n{first_page[:2000]}",
                "metadata": metadata,
            }
        except Exception as e:
            logger.warning(f"⚠️ [Inspector] PDF 解析失败: {e}")
            return {"type": "text", "content": f"PDF 解析失败，但文件已存在: {e}", "metadata": metadata}

    @classmethod
    def _handle_text(cls, path: str, metadata: dict) -> dict:
        try:
            from utils.config import settings

            strictness = getattr(settings, "AUDIT_STRICTNESS", "medium").lower()

            # 动态调整采样深度
            # Dynamically adjust sampling depth
            char_limit = 5000 if strictness == "strict" else 1000

            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(char_limit)

            suffix = "..." if metadata["size_bytes"] > char_limit else ""
            return {
                "type": "text",
                "content": f"文件内容预览 ({strictness} 模式):\n```\n{content}{suffix}\n```",
                "metadata": metadata,
            }
        except Exception as e:
            return {"type": "error", "content": f"文本读取失败: {e}"}
