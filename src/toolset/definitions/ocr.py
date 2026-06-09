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
        "Input: an EXISTING image file path on the local disk. "
        "Output: extracted text or JSON with positions. "
        "IMPORTANT: Do NOT use this tool to read the current computer screen! "
        "If you want to read the current desktop screen, use the 'desktop_read_screen' tool instead."
    )
    domain: str = "system"
    args_schema: Optional[type] = OcrExtractArgs

    async def execute(self, args: OcrExtractArgs) -> str:
        image_path = args.image_path
        language = args.language
        output_format = args.output_format

        if not image_path:
            return "Error: 'image_path' is required."

        p = Path(image_path)
        if not p.exists():
            return f"Error: image file not found: {image_path}"

        try:
            import importlib.util

            if not importlib.util.find_spec("paddleocr"):
                raise ImportError
        except ImportError:
            return "Error: 'paddleocr' not installed. Please run: pip install paddlepaddle paddleocr"

        try:
            # 尝试导入 logging 以遵循日志规范
            import logging
            logger = logging.getLogger("OcrExtractTool")
            
            # 获取环境变量中的超时设置，消除硬编码的魔法数字
            import os
            timeout_str = os.getenv("OCR_TIMEOUT", "60.0")
            try:
                timeout_val = float(timeout_str)
            except ValueError:
                timeout_val = 60.0

            # 解决 Windows 上 PyTorch/Paddle 同时导入时的 OpenMP 崩溃问题
            os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

            # 在线程池中运行阻塞的 OCR 推理
            result = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, self._extract, str(p), language, output_format),
                timeout=timeout_val,
            )
            return result
        except asyncio.TimeoutError:
            logger.error(f"OCR Execution timed out after {timeout_val} seconds.")
            return f"OCR Error: Execution timed out after {timeout_val} seconds."
        except Exception as e:
            import traceback
            logger.error(f"OCR Execution failed: {e}\n{traceback.format_exc()}")
            return f"OCR Error: {type(e).__name__}: {e}"

    def _extract(self, image_path: str, language: str, output_format: str) -> str:
        import logging
        logging.getLogger('ppocr').setLevel(logging.ERROR)
        from paddleocr import PaddleOCR

        # PaddleOCR 3.0+ 废弃了 use_angle_cls，改为 use_textline_orientation
        # 且在部分 Windows 环境下，MKLDNN (oneDNN) 会抛出 NotImplementedError 导致崩溃，因此关闭 mkldnn
        ocr = PaddleOCR(use_textline_orientation=True, lang=language, enable_mkldnn=False)
        
        # 兼容 PaddleOCR 3.x (predict) 和 2.x (ocr)
        if hasattr(ocr, 'predict'):
            # PaddleOCR 3.6+
            raw_result = list(ocr.predict(image_path))
            if not raw_result:
                return "No text detected in the image."
            res_dict = raw_result[0]
            texts = res_dict.get('rec_texts', [])
            scores = res_dict.get('rec_scores', [])
            boxes = res_dict.get('rec_polys', res_dict.get('dt_polys', []))
            
            if not texts:
                return "No text detected in the image."
                
            if output_format == "json":
                import json
                items = []
                for i in range(len(texts)):
                    # boxes[i] 可能是 numpy array，转换为 list
                    box_list = boxes[i].tolist() if hasattr(boxes[i], 'tolist') else list(boxes[i])
                    box = [[int(p[0]), int(p[1])] for p in box_list]
                    items.append({
                        "text": str(texts[i]),
                        "confidence": round(float(scores[i]), 4) if i < len(scores) else 1.0,
                        "box": box
                    })
                return json.dumps(items, ensure_ascii=False, indent=2)
            else:
                return "\n".join([str(t) for t in texts])
        else:
            # PaddleOCR 2.x 兼容逻辑
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
