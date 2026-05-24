# src/models/vision_analyzer.py
import logging
from typing import Optional, Tuple
from utils.config import settings
from .factory import ModelFactory
from .base import LLMResponseDelta

logger = logging.getLogger(__name__)


class VisionAnalyzer:
    """
    视觉分析代理：将图像发送至云端大模型，换取文本描述。

    支持两种模式：
    1. 全屏分析：无 region，分析整个截图
    2. 区域约束分析：传入 UIA 提供的 region (left, top, right, bottom)，
       告诉视觉模型"目标在这个区域内"，只需判断内部子元素的精确位置
    """

    @staticmethod
    async def analyze_screen(
        image_base64: str,
        task_context: str = "",
        model_name: Optional[str] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> str:
        """
        分析屏幕截图，返回行动指令。

        Args:
            image_base64: 截图 base64
            task_context: 任务上下文描述
            model_name: 指定模型（可选）
            region: UIA 提供的元素边界 (left, top, right, bottom) 像素坐标。
                    传入后视觉模型只需在该区域内识别子元素，精度大幅提升。
        """
        if region:
            left, top, right, bottom = region
            w, h = right - left, bottom - top
            prompt = f"""你是一个精准的 GUI 视觉分析助手。

[任务]: {task_context or "识别屏幕交互元素"}

[关键信息]: 目标元素位于屏幕的这个区域内：
  - 左上角像素坐标: ({left}, {top})
  - 右下角像素坐标: ({right}, {bottom})
  - 区域尺寸: {w} x {h} 像素

[你的职责]:
1. 描述该区域内显示的内容。
2. 识别该区域内的所有可交互子元素（按钮、输入框、链接等）。
3. 如果找到目标，输出格式为:
   [TARGET_ACTION]: vnode_action(command='input.tap', payload={{'x': <中心点X (0-1000)>, 'y': <中心点Y (0-1000)>}})
4. 坐标是相对于整个屏幕的归一化坐标（0-1000），不是相对于该区域的。
5. 必须指向子元素的几何中心，严禁返回边缘坐标。

[诚实性约束]: 如果你在图片中找不到任务要求的目标元素，必须输出 [NO_TARGET]，绝不允许猜测或编造坐标。瞎猜比不猜更危险。

[置信度]: 在结论末尾输出你对本次判断的确信程度: [CONFIDENCE: 0-100] (100=完全确定, 50=半信半疑, 0=完全瞎猜)

直接给出结论。"""
        else:
            prompt = f"""你是一个精准的 GUI 视觉分析助手。

[任务]: {task_context or "识别屏幕交互元素"}

[输出要求]:
1. 必须首先输出 【当前场景描述】。
2. 【中心锚点校准】：请先在脑中确立目标的边界框（Bounding Box），计算其几何中心点。
3. 如果找到目标，必须输出格式为:
   [TARGET_ACTION]: vnode_action(command='input.tap', payload={{'x': <中心点X (0-1000)>, 'y': <中心点Y (0-1000)>}})
4. 严禁返回目标的边角或边缘坐标，必须指向元素的最中心。

[诚实性约束]: 如果你在图片中找不到任务要求的目标元素，必须输出 [NO_TARGET]，绝不允许猜测或编造坐标。瞎猜比不猜更危险。

[置信度]: 在结论末尾输出你对本次判断的确信程度: [CONFIDENCE: 0-100] (100=完全确定, 50=半信半疑, 0=完全瞎猜)

请仔细观察图片，直接给出结论，不要输出冗长的废话。"""

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                ],
            }
        ]

        # --- 策略：模型梯队重试 ---
        models_to_try = []
        if model_name:
            models_to_try.append(model_name)
        if settings.CLOUD_MODEL:
            models_to_try.append(settings.CLOUD_MODEL)
        models_to_try = list(dict.fromkeys(models_to_try))

        # --- 策略 1：云端梯队重试 ---
        providers_to_try = []
        if settings.CLOUD_KEY and settings.CLOUD_URL:
            providers_to_try.append(("cloud", settings.CLOUD_MODEL))
        if settings.ZHIPU_KEY:
            providers_to_try.append(("zhipu", settings.ZHIPU_MODEL))
        for provider, default_model in providers_to_try:
            client = ModelFactory.get_client(provider)
            models = [m for m in models_to_try if m != "local"]
            if not models:
                models = [default_model]

            for model in models:
                try:
                    logger.debug("Trying %s vision model: %s", provider, model)
                    response: LLMResponseDelta = await client.chat_non_stream(
                        model=model,
                        messages=messages,
                        max_tokens=600,
                    )
                    if response.content and "[API" not in response.content:
                        return f"({provider.upper()} - {model}) {response.content}"
                    logger.warning("Model %s on %s abnormal, trying next...", model, provider)
                except Exception as e:
                    logger.warning("%s Model %s failed: %s", provider, model, e)

        # --- 策略 2: 回退至本地识别 ---
        try:
            client = ModelFactory.get_client("local")
            local_model = settings.LOCAL_MODEL or "qwen3.5-4b"
            response: LLMResponseDelta = await client.chat_non_stream(
                model=local_model,
                messages=messages,
                max_tokens=600,
            )
            if response.content:
                return f"(本地大脑) {response.content}"
        except Exception as e:
            return f"[视觉分析彻底失败: 梯队模型全灭且本地报错: {str(e)}]"

        return "[视觉分析失败: 未能获取到任何有效响应]"
