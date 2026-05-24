import logging
import time
import platform
import ctypes
from PIL import Image
from typing import List, Dict, Any
from dataclasses import dataclass

# 强制 DPI 感知（仅 Windows，其他平台跳过）
if platform.system().lower() == "windows":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

logger = logging.getLogger(__name__)


@dataclass
class UIElement:
    id: str
    category: str  # 'A' (Action), 'N' (Navigation), 'K' (Key-in/Data)
    source: str  # 'uia' or 'yolo'
    name: str
    box: tuple  # [x1, y1, x2, y2]
    center_abs: tuple
    confidence: float = 1.0


@dataclass
class VisualObservation:
    node_id: str
    screenshot: Image.Image
    elements: List[UIElement]
    timestamp: float


class VisualGrounder:
    """视觉对齐引擎 - Rooster 增强版 (基于 OmniVision V3.9 Elite 架构)"""

    def __init__(self, model_path: str = None):
        # 视觉模型占位
        self.model = None
        from .painter import ElitePainter

        self.painter = ElitePainter()

    def scan(self, node_id: str, screenshot: Image.Image, uia_elements: List[Dict[str, Any]]) -> VisualObservation:
        """
        核心对齐与打标逻辑 (V3.9 Elite 实现)
        """
        # 1. 调用 ElitePainter 进行语义过滤与 ID 分配 (Selective Focus)
        labeled_nodes = self.painter.prepare_labels(uia_elements)

        # 2. 物理绘图 (Selective Focus 掩码版)
        self.painter.draw_labels(screenshot, labeled_nodes)

        # 3. 转化为 Rooster 标准的 UIElement 对象
        final_elements = []
        for node in labeled_nodes:
            if node.get("is_suppressed", False):
                continue

            # 分类映射 (A/N/K 兼容层)
            c_type = str(node.get("type", "Unknown")).lower()
            if any(t in c_type for t in ["button", "menuitem", "checkbox", "hyperlink"]):
                cat = "A"
            elif any(t in c_type for t in ["edit", "combobox"]):
                cat = "K"
            else:
                cat = "N"

            final_elements.append(
                UIElement(
                    id=node["_id"],
                    category=cat,
                    source="ROOSTER-VISION-ELITE-V3.9",
                    name=node.get("name", "Unknown"),
                    box=tuple(node["box"]),
                    center_abs=tuple(node["center"]),
                )
            )

        logger.info(f"🎨 [VisualGrounder] 分配了 {len(final_elements)} 个视觉标签 (Elite 模式)")
        return VisualObservation(node_id, screenshot, final_elements, time.time())
