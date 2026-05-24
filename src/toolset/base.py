import platform as _platform
from typing import Any, Dict, List, Optional, Type
from pydantic import BaseModel

# Current OS name, matching platform.system() output: "Windows", "Darwin", "Linux"
CURRENT_PLATFORM: str = _platform.system()


class ToolResult:
    """
    工具执行结果的封装类。
    支持成功与错误的静态构造，并可通过 str() 获取输出内容。
    """

    def __init__(self, output: str, is_error: bool = False):
        self.output = output
        self.is_error = is_error

    @classmethod
    def success(cls, output: str):
        return cls(output, is_error=False)

    @classmethod
    def error(cls, output: str):
        return cls(output, is_error=True)

    def __str__(self) -> str:
        return self.output


class Tool:
    """
    Rooster 工具基类。
    同时支持两种模式：
    1. 经典模式：实现 async def run(self, **kwargs)
    2. 类型安全模式：实现 async def execute(self, args: BaseModel)

    platforms: 支持的平台列表，None 表示全平台兼容。
    值为 platform.system() 的输出: "Windows", "Darwin", "Linux"。
    例: ["Windows"] = 仅 Windows, ["Windows", "Darwin"] = Windows + macOS。
    """

    name: str = "base_tool"
    description: str = "这是一个工具描述"
    domain: str = "general"  # RSA: combat, craft, recon, comms, archive, forensics
    risk_level: str = "low"  # RSA: low, medium, high, critical
    reversible: bool = False  # RSA: whether the action can be rolled back
    kit: str = "general"  # Kit 分组名称，用于 tool_list 命令的分类筛选
    fc_hidden: bool = False  # [Round 9] True → 工具仍在注册表中但不出现在 FC Schema 列表里
    args_schema: Optional[Type[BaseModel]] = None
    platforms: Optional[List[str]] = None  # None = all platforms

    def __init__(self, context: Optional[Dict[str, Any]] = None):
        self.context = context or {}
        self.path_guard = self.context.get("path_guard")

    @classmethod
    def get_schema(cls) -> Dict[str, Any]:
        parameters = {"type": "object", "properties": {}, "required": []}
        if cls.args_schema:
            pydantic_schema = cls.args_schema.model_json_schema()
            parameters["properties"] = pydantic_schema.get("properties", {})
            parameters["required"] = pydantic_schema.get("required", [])

        return {"name": cls.name, "description": cls.description, "kit": cls.kit, "parameters": parameters}

    async def run(self, **kwargs) -> Any:
        """
        默认的执行入口。
        如果子类实现了 execute 且定义了 args_schema，则自动转换参数并调用 execute。
        否则，子类应重写此方法。
        """
        # validate_args hook: subclasses can override for custom validation; return None to pass, string for error
        # validate_args 钩子：子类可重写实现自定义验证，返回 None 表示通过，返回字符串表示错误
        validation_error = self.validate_args(**kwargs)
        if validation_error:
            return ToolResult.error(f"[InputValidation] {validation_error}")

        if self.args_schema and hasattr(self, "execute"):
            # Auto-inject parameter model validation
            # 自动注入参数模型验证
            try:
                args_obj = self.args_schema(**kwargs)
            except Exception as e:
                return ToolResult.error(f"[ArgumentValidation] {e}")
            return await self.execute(args_obj)

        raise NotImplementedError("工具类必须实现 run() 或 execute() 方法。")

    def validate_args(self, **kwargs) -> "str | None":
        """
        工具级自定义参数验证钩子（可选重写）。
        返回 None：验证通过。
        返回字符串：验证失败，内容作为错误信息返回给 LLM（不触发异常）。

        示例：
            def validate_args(self, url: str = "", **kwargs):
                if not url.startswith("https://"):
                    return "URL must use HTTPS"
        """
        return None


# 保持对旧代码的兼容性
BaseTool = Tool
