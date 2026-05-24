import asyncio
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from toolset.base import BaseTool


class OcrExtractArgs(BaseModel):
    image_path: str = Field(description="图片文件路径（支持 PNG/JPG/BMP）")
    language: str = Field("ch", description="语言: ch(中英混合) | en(纯英文)")
    output_format: str = Field("text", description="输出格式: text | json")


class OcrExtractTool(BaseTool):
    """OCR 文字提取工具 — 从图片中识别并提取文字（发票、收据、截图）"""

    name: str = "ocr_extract"
    kit: str = "Vision"
    description: str = (
        "Extract text from images using PaddleOCR. "
        "Supports Chinese+English mixed recognition. "
        "Input: image file path. Output: extracted text or JSON with positions."
    )
    domain: str = "system"
    args_schema: Optional[type] = OcrExtractArgs

    async def run(self, **kwargs) -> str:
        image_path = kwargs.get("image_path")
        language = kwargs.get("language", "ch")
        output_format = kwargs.get("output_format", "text")

        if not image_path:
            return "Error: 'image_path' is required."

        p = Path(image_path)
        if not p.exists():
            return f"Error: image file not found: {image_path}"

        try:
            from paddleocr import PaddleOCR
        except ImportError:
            return "Error: 'paddleocr' not installed. Please run: pip install paddlepaddle paddleocr"

        try:
            # Run blocking OCR inference in thread pool
            # 在线程池中运行阻塞的 OCR 推理
            result = await asyncio.get_event_loop().run_in_executor(
                None, self._extract, str(p), language, output_format
            )
            return result
        except Exception as e:
            return f"OCR Error: {type(e).__name__}: {e}"

    def _extract(self, image_path: str, language: str, output_format: str) -> str:
        from paddleocr import PaddleOCR

        ocr = PaddleOCR(use_angle_cls=True, lang=language, show_log=False)
        result = ocr.ocr(image_path, cls=True)

        if not result or not result[0]:
            return "No text detected in the image."

        if output_format == "json":
            import json

            items = []
            for line in result[0]:
                box, (text, conf) = line
                items.append(
                    {
                        "text": text,
                        "confidence": round(conf, 4),
                        "box": [[int(p[0]), int(p[1])] for p in box],
                    }
                )
            return json.dumps(items, ensure_ascii=False, indent=2)
        else:
            texts = [line[1][0] for line in result[0]]
            return "\n".join(texts)
