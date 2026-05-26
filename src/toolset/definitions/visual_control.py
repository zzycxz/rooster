import io
import os
import base64
import logging
import platform
from typing import Dict, Any
from PIL import Image
from pydantic import BaseModel, Field
from utils.vision.interaction import DesktopInteraction

# pywinauto is Windows-only; skip import on other platforms
# pywinauto 是 Windows 专属依赖，其他平台跳过导入
if platform.system().lower() == "windows":
    try:
        import importlib.util
        if not importlib.util.find_spec("pywinauto"):
            raise ImportError
        import pywinauto  # noqa: F401 — availability check
        from pywinauto import Desktop  # noqa: F401 — availability check
    except ImportError:
        pass

logger = logging.getLogger("VisualControl")


class DesktopController:
    """Windows 全局视觉与 UIA 控制器 (V31 全新人类视角版)"""

    @staticmethod
    async def get_screenshot() -> Dict[str, Any]:
        """抓取物理屏幕快照，无 GUI 环境时自动降级到模拟画布"""
        try:
            img = DesktopInteraction.capture_screen()
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            img_str = base64.b64encode(buffered.getvalue()).decode()
            return {"status": "success", "base64": img_str, "size": [img.width, img.height]}
        except Exception as e:
            logger.warning(f"⚠️ 无法抓取物理屏幕（无 GUI 或权限受限：{e}），已自动切换为 1920x1080 虚拟画布...")
            try:
                img = Image.new("RGB", (1920, 1080), "black")
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_str = base64.b64encode(buffered.getvalue()).decode()
                return {"status": "success", "base64": img_str, "size": [1920, 1080]}
            except Exception as inner_e:
                return {"status": "error", "message": f"Screenshot failed, fallback failed: {inner_e}"}

    @staticmethod
    async def dump_uia(depth: int = 6) -> Dict[str, Any]:
        """物理抓取 UIA 结构 (V3.9 Elite 整合版)"""
        try:
            from utils.vision.engine import EliteEngine

            engine = EliteEngine()

            # Use elite detection engine, auto PID lock and black-box compensation
            # 使用精英探测引擎，自动执行 PID 锁定与黑盒补偿
            elements = engine.dump()

            # For forward compatibility, unify control_type field name
            # 为了保持向前兼容，统一 control_type 字段名
            for el in elements:
                if "type" in el and "control_type" not in el:
                    el["control_type"] = el["type"]

            logger.info(f"✨ [DesktopController] 扫描完成，获得 {len(elements)} 个精英节点")
            return {"status": "success", "elements": elements}
        except Exception as e:
            logger.error(f"❌ [DesktopController] 扫描失败: {e}")
            return {"status": "error", "message": str(e)}

    @staticmethod
    async def perform_click(x: int, y: int, double: bool = False):
        DesktopInteraction.click(x, y, double=double)
        return {"status": "success"}

    @staticmethod
    async def perform_type(text: str):
        DesktopInteraction.type_text(text)
        return {"status": "success"}

    @staticmethod
    async def perform_scroll(direction: str, amount: int):
        DesktopInteraction.scroll(direction, amount)
        return {"status": "success"}

    @staticmethod
    async def perform_drag(start_x: int, start_y: int, end_x: int, end_y: int):
        DesktopInteraction.drag_to(start_x, start_y, end_x, end_y)
        return {"status": "success"}

    @staticmethod
    async def perform_hotkey(keys: list):
        DesktopInteraction.press_keys(*keys)
        return {"status": "success"}


class VNodeTargetArgs(BaseModel):
    nodeId: str = Field(description="目标受控节点的 ID")


class VNodeClickArgs(BaseModel):
    nodeId: str = Field(description="目标节点的 ID")
    elementId: str = Field(description="目标打标 ID")
    doubleClick: bool = Field(description="是否执行双击", default=False)


class VNodeTypeArgs(BaseModel):
    nodeId: str = Field(description="目标受控节点的 ID")
    elementId: str = Field(description="目标打标 ID")
    text: str = Field(description="要输入的文本")


class VNodeScrollArgs(BaseModel):
    nodeId: str = Field(description="目标受控节点的 ID")
    elementId: str = Field(description="目标打标 ID")
    direction: str = Field(description="滚动方向: up, down")
    amount: int = Field(description="滚动行数", default=3)


class VNodeDragArgs(BaseModel):
    nodeId: str = Field(description="目标受控节点的 ID")
    fromElementId: str = Field(description="起始位置 ID")
    toElementId: str = Field(description="结束位置 ID")


# ---------------------------------------------------------------------------
# 直连本机桌面工具 (不依赖 WebSocket 网关/VNode 协议)
# ---------------------------------------------------------------------------

import asyncio
import time
import json
from typing import Type, Optional
from toolset.base import BaseTool


class DesktopSnapArgs(BaseModel):
    save_path: Optional[str] = Field(
        default=None, description="截图保存路径（绝对路径）。不填则自动保存到 .rooster/evidence/temp_snapshots/"
    )


class DesktopSnapTool(BaseTool):
    """直接截取本机屏幕，返回 base64 PNG 图像，并保存截图文件。"""

    name: str = "desktop_snap"
    kit: str = "Vision"
    description: str = (
        "Take a screenshot of the local desktop screen immediately without needing a VNode connection. "
        "Returns a base64-encoded PNG image and saves it to disk. "
        "Use this to observe the current screen state before clicking buttons or reading UI content."
    )
    domain = "vision"
    platforms: list = ["Windows", "Darwin"]
    fc_hidden: bool = True  # [Round 9] desktop_grounding_scan 已内含截图；读屏文字请用 desktop_read_screen
    args_schema: Type[BaseModel] = DesktopSnapArgs

    async def run(self, **kwargs) -> str:
        save_path = kwargs.get("save_path")
        result = await DesktopController.get_screenshot()
        if result["status"] != "success":
            return f"❌ 截图失败：{result.get('message')}"

        # 保存图片
        if not save_path:
            evidence_dir = os.path.join(os.getcwd(), ".rooster", "evidence", "temp_snapshots")
            os.makedirs(evidence_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(evidence_dir, f"{ts}_desktop_snap.png")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        img_bytes = base64.b64decode(result["base64"])
        with open(save_path, "wb") as f:
            f.write(img_bytes)

        w, h = result["size"]
        return (
            f"✅ 截图成功（{w}×{h}）\n"
            f"- 已保存：{save_path}\n"
            f"- base64 长度：{len(result['base64'])} chars\n"
            f"[IMAGE_BASE64_PNG]{result['base64']}[/IMAGE_BASE64_PNG]"
        )


class DesktopGroundingScanArgs(BaseModel):
    wait_seconds: float = Field(default=2.0, description="扫描前等待时间（秒），用于等待窗口渲染完成")
    save_path: Optional[str] = Field(
        default=None, description="打标图片保存路径。不填则自动保存到 .rooster/evidence/temp_snapshots/"
    )


class DesktopGroundingScanTool(BaseTool):
    """截图 + UIA 扫描 + 在截图上绘制元素 ID 标签，返回可操作元素列表和打标图片。
    常用于：唤起迅雷后等待弹窗出现，然后扫描并找到"立即下载"按钮的 ID。"""

    name: str = "desktop_grounding_scan"
    kit: str = "Vision"
    description: str = (
        "Capture the local desktop screen, scan all interactive UI elements via UIA, "
        "draw labeled bounding boxes (ID like A, B, AA...) on the screenshot, and return "
        "the element list (id, name, type, center coordinates). "
        "After calling this tool, use desktop_click to click any element by its ID. "
        "Use this after launching apps (e.g. Thunder/Xunlei download dialog) to find confirm buttons."
    )
    domain = "vision"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = DesktopGroundingScanArgs

    async def run(self, **kwargs) -> str:
        wait_secs = float(kwargs.get("wait_seconds", 2.0))
        save_path = kwargs.get("save_path")

        if wait_secs > 0:
            await asyncio.sleep(wait_secs)

        # 截图
        snap = await DesktopController.get_screenshot()
        if snap["status"] != "success":
            return f"❌ 截图失败：{snap.get('message')}"

        img_bytes = base64.b64decode(snap["base64"])
        screenshot = Image.open(io.BytesIO(img_bytes))

        # UIA 扫描
        uia_result = await DesktopController.dump_uia()
        if uia_result["status"] != "success":
            return f"❌ UIA 扫描失败：{uia_result.get('message')}"

        elements_raw = uia_result.get("elements", [])

        # 视觉打标
        try:
            from utils.vision.grounding import VisualGrounder

            grounder = VisualGrounder()
            observation = grounder.scan("local", screenshot, elements_raw)
            labeled_elements = [
                {
                    "id": el.id,
                    "name": el.name,
                    "type": el.category,
                    "center": list(el.center_abs),
                    "box": list(el.box),
                }
                for el in observation.elements
            ]
            labeled_image = screenshot  # grounder.scan mutates screenshot in-place via painter
        except Exception:
            # Fallback: no labeling, return raw UIA elements
            # 降级：不打标，直接返回 UIA 元素
            labeled_elements = [
                {
                    "id": str(i),
                    "name": el.get("name", ""),
                    "type": el.get("type", ""),
                    "center": el.get("center", []),
                    "box": el.get("box", []),
                }
                for i, el in enumerate(elements_raw[:60])
            ]
            labeled_image = screenshot

        # 保存打标图
        if not save_path:
            evidence_dir = os.path.join(os.getcwd(), ".rooster", "evidence", "temp_snapshots")
            os.makedirs(evidence_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(evidence_dir, f"{ts}_grounding_scan.png")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        labeled_image.save(save_path)

        # 序列化结果
        buffered = io.BytesIO()
        labeled_image.save(buffered, format="PNG")
        img_b64 = base64.b64encode(buffered.getvalue()).decode()

        summary_lines = [f"  [{el['id']}] {el['name']} ({el['type']}) @ {el['center']}" for el in labeled_elements[:40]]
        summary = "\n".join(summary_lines)

        return (
            f"✅ 桌面扫描完成，共识别 {len(labeled_elements)} 个可交互元素\n"
            f"打标图已保存：{save_path}\n\n"
            f"**元素列表（用 desktop_click 按 ID 点击）：**\n{summary}\n\n"
            f"[IMAGE_BASE64_PNG]{img_b64}[/IMAGE_BASE64_PNG]"
        )


class DesktopClickArgs(BaseModel):
    element_id: Optional[str] = Field(default=None, description="桌面扫描返回的元素 ID（如 A、B、AA），与 x/y 二选一")
    x: Optional[int] = Field(default=None, description="直接指定点击的屏幕 X 坐标（像素）")
    y: Optional[int] = Field(default=None, description="直接指定点击的屏幕 Y 坐标（像素）")
    double_click: bool = Field(default=False, description="是否双击")
    scan_cache: Optional[str] = Field(
        default=None, description="desktop_grounding_scan 返回的元素列表 JSON 字符串，用于将 element_id 解析为坐标"
    )


class DesktopClickTool(BaseTool):
    """点击本机桌面上的指定元素或坐标。配合 desktop_grounding_scan 使用：先扫描获得元素 ID，再用此工具点击。"""

    name: str = "desktop_click"
    kit: str = "Vision"
    fc_hidden: bool = True  # [Round 10] Use desktop_act(action="click") instead
    description: str = (
        "Click on a UI element on the local desktop by element ID (from desktop_grounding_scan) "
        "or by absolute screen coordinates (x, y). "
        "Use after desktop_grounding_scan to click buttons like '立即下载', '确认', 'OK', etc."
    )
    domain = "vision"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = DesktopClickArgs

    async def run(self, **kwargs) -> str:
        element_id = kwargs.get("element_id")
        x = kwargs.get("x")
        y = kwargs.get("y")
        double_click = bool(kwargs.get("double_click", False))
        scan_cache = kwargs.get("scan_cache")

        # Resolve coordinates via element_id + scan_cache
        # 通过 element_id + scan_cache 解析坐标
        if element_id and scan_cache and x is None:
            try:
                elements = json.loads(scan_cache)
                for el in elements:
                    if str(el.get("id")) == str(element_id):
                        center = el.get("center", [])
                        if len(center) >= 2:
                            x, y = int(center[0]), int(center[1])
                        break
            except Exception:
                pass

        if x is None or y is None:
            return (
                "❌ 无法确定点击坐标。请提供 x/y 坐标，"
                "或同时提供 element_id 和 scan_cache（desktop_grounding_scan 返回的元素列表 JSON）。"
            )

        result = await DesktopController.perform_click(x, y, double=double_click)
        if result.get("status") == "success":
            action = "双击" if double_click else "单击"
            return f"✅ {action} ({x}, {y}) 成功"
        return f"❌ 点击失败：{result.get('message', '未知错误')}"


class DesktopTypeArgs(BaseModel):
    text: str = Field(description="要输入的文字内容")


class DesktopTypeTool(BaseTool):
    """向当前焦点控件输入文字（中英文均支持，自动使用剪贴板模式）。"""

    name: str = "desktop_type"
    kit: str = "Vision"
    fc_hidden: bool = True  # [Round 10] Use desktop_act(action="type") instead
    description: str = (
        "Type text into the currently focused input field on the local desktop. "
        "Supports Chinese and English. Use after clicking an input field with desktop_click."
    )
    domain = "vision"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = DesktopTypeArgs

    async def run(self, **kwargs) -> str:
        text = kwargs.get("text", "")
        if not text:
            return "❌ text 不能为空"
        result = await DesktopController.perform_type(text)
        if result.get("status") == "success":
            return f"✅ 已输入文字：{text[:50]}{'...' if len(text) > 50 else ''}"
        return f"❌ 输入失败：{result.get('message', '未知错误')}"


# ---------------------------------------------------------------------------
# [Round 9] desktop_read_screen — 截图 + OCR 一步到位的宏工具
# 替代 "先 desktop_snap，再 ocr_extract" 两步流程
# ---------------------------------------------------------------------------


class DesktopReadScreenArgs(BaseModel):
    language: str = Field(default="ch", description="OCR 语言：ch（中英混合，默认）| en（纯英文）")
    save_path: Optional[str] = Field(
        default=None, description="截图保存路径（绝对路径）。不填则自动保存到 .rooster/evidence/temp_snapshots/"
    )
    output_format: str = Field(default="text", description="输出格式：text（纯文本，默认）| json（含坐标信息）")


class DesktopReadScreenTool(BaseTool):
    """[Round 9] 截图 + OCR 一步宏：截取本机屏幕后立即提取文字，无需分两步调用。"""

    name: str = "desktop_read_screen"
    kit: str = "Vision"
    description: str = (
        "Take a screenshot of the local desktop and immediately extract all visible text via OCR. "
        "This is a single-step macro replacing 'desktop_snap → ocr_extract'. "
        "Use this when you need to READ text from the screen (UI labels, dialog content, error messages). "
        "For UI automation (finding buttons to click), use desktop_grounding_scan instead."
    )
    domain = "vision"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = DesktopReadScreenArgs

    async def run(self, **kwargs) -> str:
        language = kwargs.get("language", "ch")
        save_path = kwargs.get("save_path")
        output_format = kwargs.get("output_format", "text")

        # Step 1: Screenshot
        # Step 1: 截图
        snap = await DesktopController.get_screenshot()
        if snap["status"] != "success":
            return f"❌ 截图失败：{snap.get('message')}"

        # Step 2: Save screenshot to disk (ocr_extract needs file path)
        # Step 2: 保存截图到磁盘（ocr_extract 需要文件路径）
        if not save_path:
            evidence_dir = os.path.join(os.getcwd(), ".rooster", "evidence", "temp_snapshots")
            os.makedirs(evidence_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            save_path = os.path.join(evidence_dir, f"{ts}_read_screen.png")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)

        img_bytes = base64.b64decode(snap["base64"])
        with open(save_path, "wb") as f:
            f.write(img_bytes)

        # Step 3: OCR text extraction (reuse OcrExtractTool logic)
        # Step 3: OCR 提取文字（复用 OcrExtractTool 逻辑）
        try:
            from toolset.definitions.ocr import OcrExtractTool

            ocr_tool = OcrExtractTool()
            ocr_result = await ocr_tool.run(
                image_path=save_path,
                language=language,
                output_format=output_format,
            )
        except Exception as e:
            ocr_result = f"⚠️ OCR 提取失败（截图已保存至 {save_path}）: {e}"

        w, h = snap["size"]
        return f"✅ 截图（{w}×{h}）已保存：{save_path}\n\n**屏幕文字内容：**\n{ocr_result}"


# ---------------------------------------------------------------------------
# [Round 10] desktop_act — unified desktop interaction macro
# Replaces: desktop_click, desktop_type
# ---------------------------------------------------------------------------


class DesktopActArgs(BaseModel):
    action: str = Field(
        description="Action type: 'click' (click element or coordinates), 'double_click' (double-click), 'type' (type text)"
    )
    element_id: Optional[str] = Field(
        default=None, description="[click / double_click] Element ID from desktop_grounding_scan (e.g. 'A', 'B', 'AA')"
    )
    x: Optional[int] = Field(default=None, description="[click / double_click] Screen X coordinate (pixels)")
    y: Optional[int] = Field(default=None, description="[click / double_click] Screen Y coordinate (pixels)")
    scan_cache: Optional[str] = Field(
        default=None,
        description="[click / double_click] JSON string of elements from desktop_grounding_scan, to resolve element_id to coordinates",
    )
    text: Optional[str] = Field(default=None, description="[type] Text to type into the focused input")


class DesktopActTool(BaseTool):
    """[Round 10] Unified desktop interaction macro: click, double-click, or type in one tool."""

    name: str = "desktop_act"
    kit: str = "Vision"
    description: str = (
        "Unified desktop interaction tool. Use action='click' or 'double_click' to click a UI element "
        "by element_id (from desktop_grounding_scan) or by x/y coordinates. "
        "Use action='type' to type text into the currently focused input field. "
        "Typical flow: desktop_grounding_scan → desktop_act(click) → desktop_act(type)."
    )
    domain = "vision"
    platforms: list = ["Windows", "Darwin"]
    args_schema: Type[BaseModel] = DesktopActArgs

    async def run(self, **kwargs) -> str:
        action = kwargs.get("action", "").lower()
        element_id = kwargs.get("element_id")
        x = kwargs.get("x")
        y = kwargs.get("y")
        scan_cache = kwargs.get("scan_cache")
        text = kwargs.get("text", "")

        if action in ("click", "double_click"):
            double = action == "double_click"
            if element_id and scan_cache and x is None:
                try:
                    elements = json.loads(scan_cache)
                    for el in elements:
                        if str(el.get("id")) == str(element_id):
                            center = el.get("center", [])
                            if len(center) >= 2:
                                x, y = int(center[0]), int(center[1])
                            break
                except Exception:
                    pass
            if x is None or y is None:
                return (
                    "❌ 无法确定点击坐标。请提供 x/y 坐标，"
                    "或同时提供 element_id 和 scan_cache（desktop_grounding_scan 返回的元素列表 JSON）。"
                )
            result = await DesktopController.perform_click(x, y, double=double)
            if result.get("status") == "success":
                return f"✅ {'双击' if double else '单击'} ({x}, {y}) 成功"
            return f"❌ 点击失败：{result.get('message', '未知错误')}"

        elif action == "type":
            if not text:
                return "❌ text 不能为空"
            result = await DesktopController.perform_type(text)
            if result.get("status") == "success":
                return f"✅ 已输入文字：{text[:50]}{'...' if len(text) > 50 else ''}"
            return f"❌ 输入失败：{result.get('message', '未知错误')}"

        else:
            return f"Error: Unknown action '{action}'. Valid: 'click', 'double_click', 'type'."
