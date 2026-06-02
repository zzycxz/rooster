import json
import logging
from typing import Optional, Tuple, Dict, Any
from pydantic import BaseModel, ValidationError

from toolset.base import BaseTool
from utils.config import settings

_logger = logging.getLogger(__name__)


class ToolCallValidator:
    """
    LLM 工具调用的 Schema 校验 + 自愈 (Validation-Driven Self-Healing)。
    捕获 Pydantic ValidationError，并允许 LLM 进行局部修复重试，避免异常冒泡。
    """

    MAX_HEAL_RETRIES = 2

    async def validate_and_heal(
        self,
        tool: BaseTool,
        raw_args: Any,
        llm_client: Any,
        session_history: list,
    ) -> Tuple[Optional[BaseModel], Optional[str]]:
        """
        Returns: (validated_args_model, error_message)
        - (args, None) = 校验通过
        - (None, error_msg) = 校验失败且自愈耗尽
        """
        # 1. 尝试初始的 JSON 解析
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError as e:
                healed = await self._heal_json_parse(tool, raw_args, str(e), llm_client)
                if healed is None:
                    return None, f"JSON parse error: {e}"
                raw_args = healed

        # 如果未定义 Pydantic schema，直接放行 (兼容老工具)
        schema = tool.args_schema
        if schema is None:
            # 伪造一个兼容的 BaseModel 使得接口统一，实际上应该直接用原始 args
            # 这里为了简单起见，如果没 schema，返回 None，由上层处理
            return None, None

        # 2. Pydantic Schema 校验
        try:
            validated = schema.model_validate(raw_args)
            return validated, None
        except ValidationError as e:
            error_detail = self._format_validation_error(e, tool.name)
            _logger.warning(f"[ToolValidator] '{tool.name}' initial validation failed. Starting self-heal loop.")

            # 3. 自愈: 将错误回传给 LLM 修复
            for attempt in range(self.MAX_HEAL_RETRIES):
                healed_args = await self._heal_schema(tool, raw_args, error_detail, llm_client)
                if healed_args is not None:
                    try:
                        validated = schema.model_validate(healed_args)
                        _logger.info(f"[ToolValidator] '{tool.name}' healed successfully on attempt {attempt + 1}.")
                        return validated, None
                    except ValidationError as e2:
                        error_detail = self._format_validation_error(e2, tool.name)
                        _logger.debug(f"[ToolValidator] Healing attempt {attempt + 1} failed: {error_detail}")
                        continue
                else:
                    break

            return None, error_detail

    def _format_validation_error(self, e: ValidationError, tool_name: str) -> str:
        """将 Pydantic 错误转换为 LLM 可理解的清晰提示"""
        lines = []
        for err in e.errors():
            loc = ".".join(str(x) for x in err["loc"])
            lines.append(f"  - Parameter '{loc}': {err['msg']} (expected {err.get('type', 'unknown')})")
        return f"Tool '{tool_name}' parameter validation failed:\n" + "\n".join(lines)

    async def _heal_json_parse(
        self, tool: BaseTool, raw_string: str, error_msg: str, llm_client: Any
    ) -> Optional[Dict]:
        """专门修复纯 JSON 语法错误"""
        prompt = (
            f"You attempted to call the tool '{tool.name}', but your JSON arguments are malformed.\n"
            f"Error: {error_msg}\n\n"
            f"Your output:\n{raw_string}\n\n"
            f"Please output ONLY a valid JSON object without any markdown formatting."
        )
        return await self._ask_llm_for_fix(prompt, llm_client)

    async def _heal_schema(self, tool: BaseTool, raw_args: Dict, error_detail: str, llm_client: Any) -> Optional[Dict]:
        """专门修复结构与参数类型错误"""
        try:
            schema_json = tool.args_schema.model_json_schema()
        except Exception:
            schema_json = "No schema available"

        prompt = (
            f"The tool call you made has parameter schema errors:\n{error_detail}\n\n"
            f"Expected JSON Schema for {tool.name}:\n{json.dumps(schema_json, indent=2)}\n\n"
            f"Your original arguments: {json.dumps(raw_args, ensure_ascii=False)}\n\n"
            f"Please output ONLY a valid JSON object with corrected parameters matching the schema."
        )
        return await self._ask_llm_for_fix(prompt, llm_client)

    async def _ask_llm_for_fix(self, prompt: str, llm_client: Any) -> Optional[Dict]:
        """公共的底层 LLM 修正请求"""
        try:
            resp = await llm_client.chat_non_stream(
                messages=[{"role": "user", "content": prompt}],
                model=getattr(settings, "ROUTER_MODEL_NAME", ""),
                temperature=0.1,
            )
            text = (resp.content or "").strip()

            # 剥离 Markdown 代码块
            if "```" in text:
                parts = text.split("```")
                if len(parts) >= 3:
                    text = parts[1]
                    if text.startswith("json"):
                        text = text[4:]
            return json.loads(text.strip())
        except Exception as e:
            _logger.debug(f"[ToolValidator] Failed to get/parse fix from LLM: {e}")
            return None
