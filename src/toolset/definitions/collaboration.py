from typing import Type, Optional
from pydantic import BaseModel, Field
import os
import logging
from toolset.base import Tool, ToolResult

logger = logging.getLogger(__name__)


class FeishuPushFileArgs(BaseModel):
    file_path: str = Field(..., description="本地文件的绝对路径。")
    description: Optional[str] = Field(None, description="对该文件的简短描述。")


class FeishuPushFileTool(Tool):
    name = "feishu_push_file"
    kit = "Network"
    description = "仅限飞书渠道：将本地文件推送至飞书对话框。非飞书渠道（网页/命令行）不可使用此工具，文件直接在本地查看即可。"
    args_schema: Type[BaseModel] = FeishuPushFileArgs
    workspace_dir: str = "."

    async def execute(self, args: FeishuPushFileArgs) -> ToolResult:
        file_path = args.file_path

        # ── 渠道守卫：仅允许飞书渠道调用 ──
        # ── Channel guard: only allow calls from Feishu channel ──
        session_id = self.context.get("session_id", "")
        if not session_id.startswith("feishu_"):
            return ToolResult.error(
                "❌ feishu_push_file 仅限飞书渠道使用。"
                "当前渠道不是飞书，请直接在本地查看生成的文件。"
            )

        # Safely get the injected workspace_dir
        # 安全获取注入的 workspace_dir
        ws_dir = getattr(self, "workspace_dir", ".")

        # --- Smart path resolution ---
        # 如果不是绝对路径，尝试与工作区目录拼接
        # If not absolute path, try joining with workspace directory
        if not os.path.isabs(file_path):
            potential_path = os.path.join(ws_dir, file_path)
            if os.path.exists(potential_path):
                file_path = potential_path
            elif os.path.exists(os.path.abspath(file_path)):
                file_path = os.path.abspath(file_path)

        # 1. Security filter: check for sensitive file extensions
        # 1. 安全过滤：检查敏感后缀
        sensitive_exts = {".py", ".env", ".json", ".yaml", ".yml", ".sh", ".bat", ".ps1"}
        _, ext = os.path.splitext(file_path.lower())
        if ext in sensitive_exts:
            return ToolResult.error(f"❌ 安全拒绝：系统禁止通过飞书发送 {ext} 类型的敏感代码或配置文件。")

        # 2. Check if file exists
        # 2. 检查文件是否存在
        if not os.path.exists(file_path):
            return ToolResult.error(f"❌ 找不到文件: {file_path}")

        # 3. Check file size (10MB limit)
        # 3. 检查大小 (10MB 限制)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        if file_size_mb > 10:
            return ToolResult.error(
                f"❌ 文件太重 ({file_size_mb:.1f}MB): 已超过飞书 10MB 的即时推送上限。请在本地查看。"
            )

        try:
            # 4. Get Feishu channel and push file
            # 4. 获取飞书通道并推送
            from channels.registry import ChannelRegistry

            feishu_channel = ChannelRegistry.get_instance().get_channel("feishu")

            if not feishu_channel:
                return ToolResult.error("❌ 飞书通道未启动，无法推送。")

            target_to = session_id.replace("feishu_", "")

            # 5. Smart detection: if image, prefer send_image API for inline display
            # 5. 智能识别：如果是图片，优先调用 send_image 接口以实现内联显示
            image_exts = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
            is_image = ext in image_exts

            if is_image:
                success = await feishu_channel.send_image(target_to, file_path)
            else:
                success = await feishu_channel.send_file(target_to, file_path)

            if success:
                mode_str = "图片预览" if is_image else "附件文件"
                return ToolResult.success(f"✅ 文件已成功以 {mode_str} 模式推送至飞书！({os.path.basename(file_path)})")
            else:
                return ToolResult.error("❌ 飞书上传接口调用失败，请检查网络或飞书应用权限（上传文件/发送消息）。")

        except Exception as e:
            logger.error(f"❌ 飞书推送工具异常: {e}")
            return ToolResult.error(f"❌ 推送失败: {str(e)}")


class EscalateArgs(BaseModel):
    blocker_reason: str = Field(
        ...,
        description="导致任务无法继续执行的致命死锁原因（如验证码屏蔽、目标文件已被删除下线等）。不要一遇到小错误就使用，仅用于结构性死锁。",
    )


class EscalateTool(Tool):
    name = "escalate_to_strategist"
    kit = "System"
    description = "当遇到当前子任务由于不可抗力彻底失败，且尝试了不同思路仍无法破局时调用。这将中止当前执行并强制唤醒最高战略官根据你提供的 blocker_reason 对剩余全局蓝图进行重规划。注意：这是最后的兜底求援手段。"
    args_schema: Type[BaseModel] = EscalateArgs

    async def execute(self, args: EscalateArgs) -> ToolResult:
        # This is a signal tool, intercepted by the outer loop
        # 这是一个信号工具，被外层循环捕获拦截
        return ToolResult.success(f"__ESCALATE_SIGNAL__: {args.blocker_reason}")
