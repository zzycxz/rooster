from PIL import Image, ImageDraw, ImageFont
from typing import List, Dict, Any


class ElitePainter:
    """OmniVision V3.9 精英绘图引擎 - Rooster 集成版"""

    def __init__(self, output_path="rooster_vision_output.png"):
        self.output_path = output_path
        self.silent_types = ["Text", "Static", "Label"]

    def prepare_labels(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        [展现层] 核心职责：执行'Selective Focus'语义过滤并分配连续 ID。
        """
        # 1. 面积计算和初步状态设定
        # 1. Area calculation and initial state setup
        for el in elements:
            box = el.get("box", [0, 0, 0, 0])
            area = (box[2] - box[0]) * (box[3] - box[1])
            el["_area"] = area
            el["is_suppressed"] = False

            el_type = el.get("type", "Unknown")
            # 基础不展现：静默类型，或者无明确业务意义的超大面板，或者被标记为遮挡
            # Default suppressed: silent types, or oversized panels with no business significance, or marked obstructed
            if el_type in self.silent_types or (
                el_type in ["Pane", "Group", "Window"] and not el.get("force_draw", False)
            ):
                el["is_suppressed"] = True

        # 2. 物理包含遮挡 (大吞小) - Selective Focus 核心实现
        # 2. Physical containment obstruction (large swallows small) - Selective Focus core
        sorted_elements = sorted(elements, key=lambda x: x["_area"], reverse=True)
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

        image.save(self.output_path)
        return labeled_count
