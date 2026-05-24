# src/models/vision_strategy.py
"""
四层视觉识别策略 + 安全降级机制

根据 UIA 元素扫描完整度，动态选择最优识别路径。
每层失败自动降级到下一层，直到成功或全部耗尽。

层级:
  Tier 1 (完整):  >=5 UIA 元素 → 标注图 + 元素列表 → 语义选择 + UIA 坐标
  Tier 2 (稀疏):  1-4 UIA 元素 → 裁剪外框 → 视觉模型识别子元素
  Tier 3 (黑盒):  0 元素但有窗口框 → 裁剪窗口 → 纯视觉定位
  Tier 4 (全屏):  0 元素无窗口 → 全屏截图 → 纯视觉定位
"""

import base64
import io
import re
import time
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
from PIL import Image

logger = logging.getLogger(__name__)

# --- 常量 ---
CONFIDENCE_PATTERN = re.compile(r"\[CONFIDENCE[:\s]+(\d{1,3})\]", re.IGNORECASE)

# 坐标解析器链：按优先级排列，第一个命中就返回
COORD_PATTERNS = [
    # 1. [TARGET_ACTION] payload 中的 x/y (最高优先级)
    re.compile(
        r"\[TARGET_ACTION\].*?payload.*?['\"]?x['\"]?\s*[:=]\s*(\d+\.?\d*)\s*[,;]\s*['\"]?y['\"]?\s*[:=]\s*(\d+\.?\d*)",
        re.IGNORECASE | re.DOTALL,
    ),
    # 2. JSON 格式: "x": 123, "y": 456
    re.compile(
        r'["\']x["\']\s*:\s*(\d+\.?\d*)\s*,\s*["\']y["\']\s*:\s*(\d+\.?\d*)',
    ),
    # 3. 中文标点: x：123，y：456
    re.compile(
        r"x\s*[：:]\s*(\d+\.?\d*)\s*[，,]\s*y\s*[：:]\s*(\d+\.?\d*)",
    ),
    # 4. 标准格式: x=123, y=456 或 x: 123, y: 456
    re.compile(
        r"x\s*[:=]\s*(\d+\.?\d*)\s*[,;]\s*y\s*[:=]\s*(\d+\.?\d*)",
        re.IGNORECASE,
    ),
    # 5. 无逗号: x=123 y=456
    re.compile(
        r"x\s*[:=]\s*(\d+\.?\d*)\s+y\s*[:=]\s*(\d+\.?\d*)",
        re.IGNORECASE,
    ),
    # 6. 括号坐标: center=(123, 456) 或 坐标：(123, 456)
    re.compile(
        r"(?:center|坐标|位置|point)\s*[:=]?\s*\(\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*\)",
        re.IGNORECASE,
    ),
    # 7. 纯括号 (最低优先级，需有上下文): (123, 456) 前面有数字相关描述
    re.compile(
        r"(?:位于|在|at|@)\s*\(\s*(\d+\.?\d*)\s*[,，]\s*(\d+\.?\d*)\s*\)",
        re.IGNORECASE,
    ),
]

# 最小裁剪区域 (像素) — 小于此值的裁剪图没有分析价值
MIN_CROP_SIZE = 50
# 裁剪区域外扩比例 (每次降级扩大 20%)
CROP_EXPAND_RATIO = 0.2
# 最大视觉尝试次数 (跨所有层级)
MAX_VISION_ATTEMPTS = 4
# UIA 缓存 TTL (秒)
UIA_CACHE_TTL = 3.0


class UIACache:
    """UIA 扫描结果缓存，避免短时间内重复全量扫描"""

    def __init__(self, ttl: float = UIA_CACHE_TTL):
        self._ttl = ttl
        self._cached_elements: List[Dict[str, Any]] = []
        self._cached_at: float = 0.0

    def get(self) -> Optional[List[Dict[str, Any]]]:
        if self._cached_elements and (time.time() - self._cached_at) < self._ttl:
            return self._cached_elements
        return None

    def put(self, elements: List[Dict[str, Any]]):
        self._cached_elements = elements
        self._cached_at = time.time()

    def invalidate(self):
        self._cached_elements = []
        self._cached_at = 0.0


@dataclass
class VisionResult:
    """视觉分析的标准化返回"""

    success: bool
    tier_used: int  # 1-4, 0=全部失败
    report: str  # 文字报告
    coordinates: Optional[Tuple[float, float]] = None  # 归一化 0-1000
    confidence: int = 0  # 模型自报置信度 0-100
    element_hint: Optional[str] = None  # 视觉模型建议的目标元素名
    attempts: int = 0  # 总尝试次数
    tier_log: List[str] = field(default_factory=list)  # 降级轨迹


class VisionStrategy:
    """
    四层视觉识别策略引擎。

    Usage:
        strategy = VisionStrategy()
        result = await strategy.execute(
            screenshot=img,           # PIL Image
            uia_elements=elements,    # UIA 扫描结果
            labeled_image=labeled,    # ElitePainter 标注过的 PIL Image
            task_context="点击登录按钮",
            analyzer_fn=VisionAnalyzer.analyze_screen,
        )
    """

    def __init__(self):
        self._b64_cache: Dict[str, str] = {}
        self._t1_saw_no_coord: bool = False  # Tier 1 跳级信号

    @staticmethod
    def classify_tier(uia_elements: List[Dict[str, Any]]) -> int:
        """根据 UIA 元素数量判定初始层级"""
        count = len(uia_elements) if uia_elements else 0
        if count >= 5:
            return 1
        if count >= 1:
            return 2
        # 检查是否有窗口框 (Window/Pane 类型)
        has_frame = any(el.get("type", "") in ("Window", "Pane", "Dialog") for el in (uia_elements or []))
        if has_frame:
            return 3
        return 4

    @staticmethod
    def _validate_coordinates(
        coords: Tuple[float, float],
        expected_region: Optional[Tuple[int, int, int, int]] = None,
    ) -> bool:
        """验证坐标是否在合理范围内"""
        x, y = coords
        # 归一化坐标应在 0-1000 范围内
        if not (0 <= x <= 1000 and 0 <= y <= 1000):
            return False
        # 如果有预期区域，检查坐标是否在区域内 (归一化后)
        if expected_region:
            left, top, right, bottom = expected_region
            screen_w, screen_h = 1920, 1080  # 会被实际值覆盖
            norm_left = left / screen_w * 1000
            norm_top = top / screen_h * 1000
            norm_right = right / screen_w * 1000
            norm_bottom = bottom / screen_h * 1000
            # 允许 10% 的误差容差
            margin = 50  # ~5% of 1000
            if x < norm_left - margin or x > norm_right + margin or y < norm_top - margin or y > norm_bottom + margin:
                return False
        return True

    @staticmethod
    def _parse_coordinates(text: str) -> Optional[Tuple[float, float]]:
        """从视觉模型返回文本中提取坐标 — 解析器链，按优先级尝试多种格式"""
        for pattern in COORD_PATTERNS:
            match = pattern.search(text)
            if match:
                try:
                    x, y = float(match.group(1)), float(match.group(2))
                    if 0 <= x <= 1000 and 0 <= y <= 1000:
                        return (x, y)
                except (ValueError, IndexError):
                    continue
        return None

    @staticmethod
    def _parse_confidence(text: str) -> int:
        """从视觉模型返回文本中提取置信度 [CONFIDENCE: 0-100]"""
        match = CONFIDENCE_PATTERN.search(text)
        if match:
            try:
                val = int(match.group(1))
                return max(0, min(100, val))
            except (ValueError, IndexError):
                pass
        return 0

    def _image_to_base64(self, img: Image.Image, max_size: int = 1280) -> str:
        """PIL Image → base64 string (自动缩放大图 + 内存缓存)"""
        # 缓存 key: 尺寸 + 内容 hash (采样 4 角 + 中心)
        cache_key = "%d_%d_%s" % (img.width, img.height, hash(img.tobytes()[:2048]))
        if cache_key in self._b64_cache:
            return self._b64_cache[cache_key]

        # 缩放过大的图片以节省 API 费用
        if img.width > max_size or img.height > max_size:
            ratio = min(max_size / img.width, max_size / img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        self._b64_cache[cache_key] = b64
        return b64

    @staticmethod
    def _crop_region(
        img: Image.Image,
        region: Tuple[int, int, int, int],
        expand_ratio: float = 0.0,
    ) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
        """
        裁剪图像区域，可选外扩。

        Returns:
            (cropped_image, actual_region) — actual_region 用于坐标映射
        """
        left, top, right, bottom = region
        w, h = right - left, bottom - top
        expand_x = int(w * expand_ratio)
        expand_y = int(h * expand_ratio)

        left = max(0, left - expand_x)
        top = max(0, top - expand_y)
        right = min(img.width, right + expand_x)
        bottom = min(img.height, bottom + expand_y)

        # 确保最小尺寸
        if (right - left) < MIN_CROP_SIZE or (bottom - top) < MIN_CROP_SIZE:
            # 扩展到最小尺寸
            cx = (left + right) // 2
            cy = (top + bottom) // 2
            half = MIN_CROP_SIZE // 2
            left = max(0, cx - half)
            top = max(0, cy - half)
            right = min(img.width, cx + half)
            bottom = min(img.height, cy + half)

        return img.crop((left, top, right, bottom)), (left, top, right, bottom)

    @staticmethod
    def _find_window_frame(uia_elements: List[Dict[str, Any]]) -> Optional[Tuple[int, int, int, int]]:
        """找到最大的窗口框作为裁剪区域"""
        frames = [el for el in (uia_elements or []) if el.get("type", "") in ("Window", "Pane", "Dialog")]
        if not frames:
            return None
        # 选最大的
        largest = max(frames, key=lambda el: (el["box"][2] - el["box"][0]) * (el["box"][3] - el["box"][1]))
        return tuple(largest["box"])

    @staticmethod
    def _build_element_list(uia_elements: List[Dict[str, Any]]) -> str:
        """将 UIA 元素格式化为文本列表，供视觉模型参考"""
        lines = []
        for i, el in enumerate(uia_elements[:20]):  # 最多 20 个
            name = el.get("name", "Unknown")
            etype = el.get("type", "Unknown")
            box = el.get("box", [0, 0, 0, 0])
            cx = (box[0] + box[2]) // 2
            cy = (box[1] + box[3]) // 2
            lines.append(f"  [{i}] {etype} '{name}' @ ({cx}, {cy})")
        return "\n".join(lines)

    async def _try_tier_1(
        self,
        labeled_image: Image.Image,
        uia_elements: List[Dict[str, Any]],
        task_context: str,
        analyzer_fn,
    ) -> Optional[VisionResult]:
        """Tier 1: 标注图 + 元素列表 → 语义选择"""
        b64 = self._image_to_base64(labeled_image)
        element_list = self._build_element_list(uia_elements)

        prompt = (
            "你是一个精准的 GUI 视觉分析助手。\n\n"
            "[任务]: %s\n\n"
            "[屏幕上已标注的交互元素]:\n%s\n\n"
            "[图片说明]: 图片上每个交互元素已用字母标签 (A0, B1, C2...) 标注。\n\n"
            "[你的职责]:\n"
            "1. 根据任务语义，从标注列表中选择最匹配的目标元素。\n"
            "2. 输出格式:\n"
            "   [TARGET_ACTION]: vnode_action(command='input.tap', payload={'x': <中心点X (0-1000)>, 'y': <中心点Y (0-1000)>})\n"
            "3. 坐标是整个屏幕的归一化坐标 (0-1000)。\n\n"
            "[诚实性约束]: 如果你在图片中找不到任务要求的目标，必须输出 [NO_TARGET]。绝不允许猜测或编造坐标。瞎猜比不猜更危险。\n\n"
            "[置信度]: 在结论末尾输出你对本次判断的确信程度: [CONFIDENCE: 0-100] (100=完全确定, 50=半信半疑, 0=完全瞎猜)\n\n"
            "直接给出结论。"
        ) % (task_context, element_list)

        try:
            report = await analyzer_fn(b64, task_context=prompt)
        except Exception as e:
            logger.warning(f"[VisionStrategy] Tier 1 API 失败: {e}")
            return None

        if not report or "[Vision Analysis Failed" in report or "[API" in report:
            return None

        coords = self._parse_coordinates(report)
        if not coords:
            if "[NO_TARGET]" in report:
                return None
            # 有描述但没坐标 — 设置跳级信号，直奔 Tier 4
            self._t1_saw_no_coord = True
            logger.info("[VisionStrategy] Tier 1 返回了描述但无坐标，标记跳级")
            return None

        if not self._validate_coordinates(coords):
            logger.warning(f"[VisionStrategy] Tier 1 坐标越界: {coords}，降级")
            return None

        conf = self._parse_confidence(report)
        return VisionResult(
            success=True,
            tier_used=1,
            report=report,
            coordinates=coords,
            confidence=conf,
            attempts=1,
        )

    async def _try_tier_2(
        self,
        screenshot: Image.Image,
        uia_elements: List[Dict[str, Any]],
        task_context: str,
        analyzer_fn,
        expand_ratio: float = 0.0,
    ) -> Optional[VisionResult]:
        """Tier 2: 裁剪 UIA 外框 → 视觉模型识别子元素"""
        frame = self._find_window_frame(uia_elements)
        if not frame:
            logger.info("[VisionStrategy] Tier 2: 无窗口框可裁剪，跳过")
            return None

        cropped, actual_region = self._crop_region(screenshot, frame, expand_ratio)
        if cropped.width < MIN_CROP_SIZE or cropped.height < MIN_CROP_SIZE:
            logger.info("[VisionStrategy] Tier 2: 裁剪区域太小，跳过")
            return None

        b64 = self._image_to_base64(cropped)
        left, top, right, bottom = actual_region
        w, h = right - left, bottom - top
        element_list = self._build_element_list(uia_elements)

        prompt = (
            "你是一个精准的 GUI 视觉分析助手。\n\n"
            "[任务]: %s\n\n"
            "[关键信息]: 目标元素位于屏幕的这个区域内:\n"
            "  - 左上角像素坐标: (%d, %d)\n"
            "  - 右下角像素坐标: (%d, %d)\n"
            "  - 区域尺寸: %d x %d 像素\n\n"
            "[已知 UIA 元素]:\n%s\n\n"
            "[你的职责]:\n"
            "1. 描述该区域内显示的内容。\n"
            "2. 识别该区域内的所有可交互子元素。\n"
            "3. 如果找到目标，输出格式:\n"
            "   [TARGET_ACTION]: vnode_action(command='input.tap', payload={'x': <中心点X (0-1000)>, 'y': <中心点Y (0-1000)>})\n"
            "4. 坐标是整个屏幕的归一化坐标 (0-1000)，不是相对于该区域的。\n\n"
            "[诚实性约束]: 如果你在图片中找不到任务要求的目标，必须输出 [NO_TARGET]。绝不允许猜测或编造坐标。瞎猜比不猜更危险。\n\n"
            "[置信度]: 在结论末尾输出你对本次判断的确信程度: [CONFIDENCE: 0-100] (100=完全确定, 50=半信半疑, 0=完全瞎猜)\n\n"
            "直接给出结论。"
        ) % (task_context, left, top, right, bottom, w, h, element_list)

        try:
            report = await analyzer_fn(b64, task_context=prompt)
        except Exception as e:
            logger.warning("[VisionStrategy] Tier 2 API 失败: %s" % e)
            return None

        if not report or "[Vision Analysis Failed" in report or "[API" in report:
            return None

        coords = self._parse_coordinates(report)
        conf = self._parse_confidence(report)
        if not coords:
            if "[NO_TARGET]" in report:
                return None
            # 有描述但没坐标 — 返回描述性结果（非坐标类）
            return VisionResult(
                success=True,
                tier_used=2,
                report=report,
                confidence=conf,
                attempts=1,
            )

        if not self._validate_coordinates(coords):
            logger.warning(f"[VisionStrategy] Tier 2 坐标越界: {coords}，降级")
            return None

        return VisionResult(
            success=True,
            tier_used=2,
            report=report,
            coordinates=coords,
            confidence=conf,
            attempts=1,
        )

    async def _try_tier_3(
        self,
        screenshot: Image.Image,
        uia_elements: List[Dict[str, Any]],
        task_context: str,
        analyzer_fn,
        expand_ratio: float = 0.0,
    ) -> Optional[VisionResult]:
        """Tier 3: 裁剪窗口 → 纯视觉定位"""
        frame = self._find_window_frame(uia_elements)
        if not frame:
            # 没有窗口框，降级到 Tier 4
            return None

        cropped, actual_region = self._crop_region(screenshot, frame, expand_ratio)
        if cropped.width < MIN_CROP_SIZE or cropped.height < MIN_CROP_SIZE:
            return None

        b64 = self._image_to_base64(cropped)
        left, top, right, bottom = actual_region

        prompt = (
            "你是一个精准的 GUI 视觉分析助手。\n\n"
            "[任务]: %s\n\n"
            "[关键信息]: 你看到的是屏幕的一个裁剪区域:\n"
            "  - 区域左上角: (%d, %d)\n"
            "  - 区域右下角: (%d, %d)\n\n"
            "[你的职责]:\n"
            "1. 识别该区域内的所有可交互元素。\n"
            "2. 如果找到目标，输出:\n"
            "   [TARGET_ACTION]: vnode_action(command='input.tap', payload={'x': <X (0-1000)>, 'y': <Y (0-1000)>})\n"
            "3. 坐标是整个屏幕的归一化坐标 (0-1000)。\n\n"
            "[诚实性约束]: 如果你在图片中找不到任务要求的目标，必须输出 [NO_TARGET]。绝不允许猜测或编造坐标。瞎猜比不猜更危险。\n\n"
            "[置信度]: 在结论末尾输出你对本次判断的确信程度: [CONFIDENCE: 0-100] (100=完全确定, 50=半信半疑, 0=完全瞎猜)\n\n"
            "直接给出结论。"
        ) % (task_context, left, top, right, bottom)

        try:
            report = await analyzer_fn(b64, task_context=prompt)
        except Exception as e:
            logger.warning(f"[VisionStrategy] Tier 3 API 失败: {e}")
            return None

        if not report or "[Vision Analysis Failed" in report or "[API" in report:
            return None

        coords = self._parse_coordinates(report)
        conf = self._parse_confidence(report)
        if not coords:
            if "[NO_TARGET]" in report:
                return None
            return VisionResult(
                success=True,
                tier_used=3,
                report=report,
                confidence=conf,
                attempts=1,
            )

        if not self._validate_coordinates(coords):
            logger.warning(f"[VisionStrategy] Tier 3 坐标越界: {coords}，降级")
            return None

        return VisionResult(
            success=True,
            tier_used=3,
            report=report,
            coordinates=coords,
            confidence=conf,
            attempts=1,
        )

    async def _try_tier_4(
        self,
        screenshot: Image.Image,
        task_context: str,
        analyzer_fn,
    ) -> Optional[VisionResult]:
        """Tier 4: 全屏截图 → 纯视觉定位 (最终保底)"""
        b64 = self._image_to_base64(screenshot)

        prompt = (
            "你是一个精准的 GUI 视觉分析助手。\n\n"
            "[任务]: %s\n\n"
            "[输出要求]:\n"
            "1. 必须首先输出【当前场景描述】。\n"
            "2. 如果找到目标，输出:\n"
            "   [TARGET_ACTION]: vnode_action(command='input.tap', payload={'x': <X (0-1000)>, 'y': <Y (0-1000)>})\n"
            "3. 坐标指向元素的几何中心，严禁返回边缘坐标。\n\n"
            "[诚实性约束]: 如果你在图片中找不到任务要求的目标，必须输出 [NO_TARGET]。绝不允许猜测或编造坐标。瞎猜比不猜更危险。\n\n"
            "[置信度]: 在结论末尾输出你对本次判断的确信程度: [CONFIDENCE: 0-100] (100=完全确定, 50=半信半疑, 0=完全瞎猜)\n\n"
            "请仔细观察图片，直接给出结论。"
        ) % task_context

        try:
            report = await analyzer_fn(b64, task_context=prompt)
        except Exception as e:
            logger.warning(f"[VisionStrategy] Tier 4 API 失败: {e}")
            return None

        if not report or "[Vision Analysis Failed" in report or "[API" in report:
            return None

        coords = self._parse_coordinates(report)
        conf = self._parse_confidence(report)
        if not coords:
            # Tier 4 是最后保底 — 如果有文字描述也算成功
            if "[NO_TARGET]" in report:
                return VisionResult(
                    success=False,
                    tier_used=4,
                    report="视觉模型在全屏范围内未找到目标。",
                    confidence=conf,
                    attempts=1,
                )
            return VisionResult(
                success=True,
                tier_used=4,
                report=report,
                confidence=conf,
                attempts=1,
            )

        if not self._validate_coordinates(coords):
            logger.warning(f"[VisionStrategy] Tier 4 坐标越界: {coords}")
            # 最后一层不降级，返回失败
            return VisionResult(
                success=False,
                tier_used=4,
                report="视觉模型返回了越界坐标 %s，无法使用。" % (coords,),
                confidence=conf,
                attempts=1,
            )

        return VisionResult(
            success=True,
            tier_used=4,
            report=report,
            coordinates=coords,
            confidence=conf,
            attempts=1,
        )

    async def execute(
        self,
        screenshot: Image.Image,
        uia_elements: List[Dict[str, Any]],
        labeled_image: Optional[Image.Image],
        task_context: str,
        analyzer_fn,
        screen_size: Tuple[int, int] = (1920, 1080),
    ) -> VisionResult:
        """
        执行四层视觉识别策略，自动降级。

        Args:
            screenshot: 原始全屏截图 (PIL Image)
            uia_elements: EliteEngine 扫描到的 UIA 元素列表
            labeled_image: ElitePainter 标注过的截图 (Tier 1 专用，可为 None)
            task_context: 任务上下文描述
            analyzer_fn: async (base64, task_context, ...) -> str  视觉分析函数
            screen_size: 屏幕分辨率 (width, height)

        Returns:
            VisionResult — 包含成功/失败、使用的层级、坐标、报告等
        """
        initial_tier = self.classify_tier(uia_elements)
        tier_log = []
        total_attempts = 0
        # 跳级标记：Tier 1 "看到了但没坐标" → 直跳 Tier 4
        skip_to_tier4 = False

        logger.info("[VisionStrategy] UIA 元素数: %d，初始层级: Tier %d" % (len(uia_elements or []), initial_tier))

        # === Tier 1 ===
        if initial_tier <= 1 and total_attempts < MAX_VISION_ATTEMPTS:
            tier_log.append("Tier 1: 标注图 + 语义选择")
            total_attempts += 1
            result = await self._try_tier_1(labeled_image or screenshot, uia_elements, task_context, analyzer_fn)
            if result and result.success:
                result.attempts = total_attempts
                result.tier_log = tier_log
                logger.info("[VisionStrategy] OK Tier 1 成功 (尝试 %d 次)" % total_attempts)
                return result
            # 检测"看到了但没坐标" → 跳级到 Tier 4
            if self._t1_saw_no_coord:
                skip_to_tier4 = True
                tier_log[-1] += " -> 有描述无坐标，跳级到 Tier 4"
                logger.info("[VisionStrategy] Tier 1 模型看到了但没坐标，跳过 Tier 2/3 直达 Tier 4")
            else:
                tier_log[-1] += " -> 失败"

        # === Tier 2 (跳级时跳过) ===
        if not skip_to_tier4 and initial_tier <= 2 and total_attempts < MAX_VISION_ATTEMPTS:
            # 第一次尝试用原始区域，如果降级来的则扩大区域
            expand = 0.0 if initial_tier == 2 else CROP_EXPAND_RATIO
            tier_log.append("Tier 2: 裁剪外框 (expand=%.0f%%)" % (expand * 100))
            total_attempts += 1
            result = await self._try_tier_2(screenshot, uia_elements, task_context, analyzer_fn, expand_ratio=expand)
            if result and result.success:
                result.attempts = total_attempts
                result.tier_log = tier_log
                logger.info("[VisionStrategy] OK Tier 2 成功 (尝试 %d 次)" % total_attempts)
                return result
            tier_log[-1] += " -> 失败"

            # Tier 2 失败 → 扩大区域重试一次
            if total_attempts < MAX_VISION_ATTEMPTS:
                tier_log.append("Tier 2 重试: 扩大裁剪区域 (expand=%.0f%%)" % (CROP_EXPAND_RATIO * 100))
                total_attempts += 1
                result = await self._try_tier_2(
                    screenshot,
                    uia_elements,
                    task_context,
                    analyzer_fn,
                    expand_ratio=CROP_EXPAND_RATIO,
                )
                if result and result.success:
                    result.attempts = total_attempts
                    result.tier_log = tier_log
                    logger.info("[VisionStrategy] OK Tier 2 扩大重试成功 (尝试 %d 次)" % total_attempts)
                    return result
                tier_log[-1] += " -> 失败"

        # === Tier 3 (跳级时跳过) ===
        if not skip_to_tier4 and initial_tier <= 3 and total_attempts < MAX_VISION_ATTEMPTS:
            expand = 0.0 if initial_tier == 3 else CROP_EXPAND_RATIO
            tier_log.append("Tier 3: 裁剪窗口 (expand=%.0f%%)" % (expand * 100))
            total_attempts += 1
            result = await self._try_tier_3(screenshot, uia_elements, task_context, analyzer_fn, expand_ratio=expand)
            if result and result.success:
                result.attempts = total_attempts
                result.tier_log = tier_log
                logger.info("[VisionStrategy] OK Tier 3 成功 (尝试 %d 次)" % total_attempts)
                return result
            tier_log[-1] += " -> 失败"

        # === Tier 4 (最终保底 / 跳级目标) ===
        if total_attempts < MAX_VISION_ATTEMPTS:
            tier_log.append("Tier 4: 全屏保底")
            total_attempts += 1
            result = await self._try_tier_4(screenshot, task_context, analyzer_fn)
            if result:
                result.attempts = total_attempts
                result.tier_log = tier_log
                if result.success:
                    logger.info(f"[VisionStrategy] ✓ Tier 4 保底成功 (尝试 {total_attempts} 次)")
                else:
                    logger.warning(f"[VisionStrategy] ✗ 全部层级失败 (尝试 {total_attempts} 次)")
                return result

        # 所有层级全部耗尽
        logger.warning(f"[VisionStrategy] ✗ 全部层级耗尽 (尝试 {total_attempts} 次)")
        return VisionResult(
            success=False,
            tier_used=0,
            report=(
                "❌ 视觉识别系统全面失败。\n"
                "【重要指令】：由于目前无法'看到'屏幕，严禁进行任何'猜测性'的点击操作 (vnode_action)。\n"
                "请尝试：1. 重新调用 vnode_camera_snap 截图； 2. 如果多次失败，请如实告知用户视觉系统故障。"
            ),
            attempts=total_attempts,
            tier_log=tier_log,
        )
