import os
import time
from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any

from utils.config.runtime import RuntimeConfig


class ElitePainter:
    """OmniVision V3.9 精英绘图引擎 - Rooster 集成版"""

    # 四分类规则（medium/high 共用）
    _ACTION_TYPES = {
        "button",
        "menuitem",
        "checkbox",
        "hyperlink",
        "tab",
        "tabitem",
        "splitbutton",
        "link",
        "treeitem",
        "listitem",
        "image",
        "radiobutton",
    }
    _KEYIN_TYPES = {"edit", "combobox", "spinner", "slider"}
    _NAV_TYPES = {"list", "tree", "menu", "toolbar"}

    def __init__(self, output_path="rooster_vision_output.png"):
        self.output_path = output_path
        self.silent_types = ["Text", "Static", "Label"]

    @staticmethod
    def classify(type_name: str) -> str:
        """A/N/K/U 四分类。"""
        t = type_name.lower()
        if any(k in t for k in ElitePainter._ACTION_TYPES):
            return "A"
        elif any(k in t for k in ElitePainter._KEYIN_TYPES):
            return "K"
        elif any(k in t for k in ElitePainter._NAV_TYPES):
            return "N"
        return "U"

    def prepare_labels(self, elements: List[Dict[str, Any]], mode: str = "low") -> List[Dict[str, Any]]:
        """
        [展现层] 核心职责：执行'Selective Focus'语义过滤并分配连续 ID。

        mode:
          low    = 原始逻辑（静默+容器抑制，包含遮挡）
          medium = 类型抑制+包含遮挡，只保留 A/K
          high   = 最少抑制，保留 A/N/K/U 全量
        """
        mode = mode.lower()

        # 分类（medium/high 需要）
        if mode in ("medium", "high"):
            for el in elements:
                el["_cat"] = self.classify(el.get("type", ""))

        # 1. 面积计算和初步状态设定
        # 1. Area calculation and initial state setup
        for el in elements:
            box = el.get("box", [0, 0, 0, 0])
            area = (box[2] - box[0]) * (box[3] - box[1])
            el["_area"] = area
            el["is_suppressed"] = False

            el_type = el.get("type", "Unknown")

            if mode == "low":
                # 原始逻辑：静默类型 + 容器类型抑制
                if el_type in self.silent_types or (
                    el_type in ["Pane", "Group", "Window"] and not el.get("force_draw", False)
                ):
                    el["is_suppressed"] = True
            elif mode == "medium":
                # 静默类型 + 容器类型抑制，且只保留 A/K
                if el_type in self.silent_types or el_type in ["Pane", "Group", "Window"]:
                    el["is_suppressed"] = True
                if el.get("_cat") not in ("A", "K"):
                    el["is_suppressed"] = True
            elif mode == "high":
                # 只抑制 Text/Static/Label，保留容器和全分类
                if el_type in ("Text", "Static", "Label"):
                    el["is_suppressed"] = True

        # 2. 物理包含遮挡 (大吞小) - Selective Focus 核心实现
        # 2. Physical containment obstruction (large swallows small) - Selective Focus core
        # high 模式跳过包含遮挡，保留所有元素
        sorted_elements = sorted(elements, key=lambda x: x["_area"], reverse=True)
        if mode != "high":
            for i, parent in enumerate(sorted_elements):
                if parent["is_suppressed"] or not parent.get("is_container", False):
                    continue

                p_box = parent["box"]
                for j, child in enumerate(sorted_elements):
                    if i == j:
                        continue
                    if child.get("is_suppressed", False):
                        continue

                    c_box = child["box"]
                    # 容差 5 像素
                    # 5-pixel tolerance
                    is_inside = (
                        c_box[0] >= p_box[0] - 5
                        and c_box[1] >= p_box[1] - 5
                        and c_box[2] <= p_box[2] + 5
                        and c_box[3] <= p_box[3] + 5
                    )
                    if is_inside:
                        parent["is_suppressed"] = True
                        break

        # 3. 分配 Base32/Alphabet 编码
        visible_elements = [e for e in elements if not e["is_suppressed"]]
        hidden_elements = [e for e in elements if e["is_suppressed"]]

        # 依照从上到下，从左到右排序
        # Sort top-to-bottom, left-to-right
        visible_elements.sort(key=lambda x: (x["box"][1] // 20, x["box"][0]))

        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        first_alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

        tiny_counter = 0
        large_counter = 0

        for el in visible_elements:
            if el.get("force_draw") and "_id" in el:
                continue  # 保持 Engine 颁发的护照 ID（如 W0）
                # Keep the passport ID issued by Engine (e.g. W0)

            if el["_area"] < 600 and tiny_counter < 26:
                el["_id"] = first_alpha[tiny_counter]
                tiny_counter += 1
            else:
                first_idx = (large_counter // 32) % 26
                second_idx = large_counter % 32
                el["_id"] = first_alpha[first_idx] + alphabet[second_idx]
                large_counter += 1

        # 为隐藏节点贴上隐藏 ID
        # Assign hidden IDs to suppressed nodes
        for i, el in enumerate(hidden_elements):
            el["_id"] = f"-{i}"

        return elements

    def draw_labels(self, image: Image.Image, elements: List[Dict[str, Any]]) -> int:
        """在物理图片上绘制精英标签"""
        draw = ImageDraw.Draw(image)
        color = "#FFFF00"

        try:
            font = ImageFont.truetype("msyh.ttc", 14)
        except Exception:
            _macos_fonts = ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc"]
            font = None
            for fp in _macos_fonts:
                try:
                    font = ImageFont.truetype(fp, 14)
                    break
                except Exception:
                    continue
            if font is None:
                font = ImageFont.load_default()

        labeled_count = 0

        for el in elements:
            if el.get("is_suppressed", False):
                continue

            labeled_count += 1
            box = el["box"]

            draw.rectangle(box, outline=color, width=1)
            label_id = el.get("_id", "X")

            id_len = len(label_id)
            tw = 12 if id_len == 1 else 22
            th = 17

            label_box = [box[0], box[1] - th, box[0] + tw, box[1]]
            if box[1] < th:
                label_box = [box[0], box[1], box[0] + tw, box[1] + th]

            draw.rectangle(label_box, fill=color)
            draw.text((label_box[0] + 2, label_box[1] - 1), label_id, fill="#000000", font=font)

        # 受控落盘逻辑 (通过 .env 或默认配置控制)
        if RuntimeConfig.VISION_DEBUG_SAVE:
            save_dir = RuntimeConfig.VISION_DEBUG_DIR
            os.makedirs(save_dir, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(save_dir, f"vision_output_{timestamp}.png")
            try:
                image.save(save_path)
            except Exception:
                pass
        elif self.output_path and self.output_path != "rooster_vision_output.png":
            # 兼容外部显式传入特定路径的情况
            try:
                image.save(self.output_path)
            except Exception:
                pass

        return labeled_count
