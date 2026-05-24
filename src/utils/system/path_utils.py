# src/utils/path_utils.py
import re


def sanitize_path_name(name: str) -> str:
    """
    [Rooster 普适性标准]：将任意字符串转换为安全的文件目录名。
    - 替换 Windows/Linux 所有的非法字符为下划线
    - 移除连续的下划线
    - 处理末尾空格或点
    """
    if not name:
        return "unknown_session"

    # 替换 <>:"/\|?* 以及控制字符
    # Replace <>:"/\|?* and control characters
    safe_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)

    # 进一步平滑：将连续下划线合并
    # Further normalize: merge consecutive underscores
    safe_name = re.sub(r"_+", "_", safe_name)

    # 移除首尾的下划线或点
    # Strip leading/trailing underscores or dots
    safe_name = safe_name.strip("_").strip(".")

    return safe_name or "default_session"


def generate_semantic_filename(task_id: str, tool_name: str, extension: str = "png") -> str:
    """
    [Rooster 语义化命名]：生成可读性极强的存证文件名。
    格式: YYYYMMDD_HHMMSS_[TaskId]_[ToolName].[ext]
    """
    from datetime import datetime

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")

    # 清洗关键字段
    # Sanitize key fields
    safe_task = sanitize_path_name(task_id)
    safe_tool = sanitize_path_name(tool_name)

    return f"{now_str}_{safe_task}_{safe_tool}.{extension}"
