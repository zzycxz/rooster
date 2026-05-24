import pytest
import base64
from unittest.mock import AsyncMock, patch
from toolset.definitions.visual_control import (
    DesktopSnapTool,
    DesktopGroundingScanTool,
    DesktopClickTool,
    DesktopTypeTool,
    DesktopReadScreenTool,
    DesktopActTool,
)


class TestVisionCrossPlatform:
    """测试视觉控制工具在跨平台（Windows 和 macOS）下的兼容性与正常加载"""

    def test_tools_platform_support(self):
        """验证所有视觉工具是否同时支持 Windows 和 Darwin 平台"""
        tools = [
            DesktopSnapTool,
            DesktopGroundingScanTool,
            DesktopClickTool,
            DesktopTypeTool,
            DesktopReadScreenTool,
            DesktopActTool,
        ]
        for tool_cls in tools:
            # 确保工具的 platforms 声明中同时包含了 Windows 和 Darwin
            assert "Windows" in tool_cls.platforms
            assert "Darwin" in tool_cls.platforms

    @pytest.mark.asyncio
    @patch("toolset.definitions.visual_control.DesktopController")
    async def test_desktop_snap_run(self, mock_controller, tmp_path):
        """测试 desktop_snap 截图工具的运行与跨平台降级机制"""
        # 模拟 DesktopController.get_screenshot 返回成功状态与 base64
        mock_controller.get_screenshot = AsyncMock(
            return_value={
                "status": "success",
                "base64": base64.b64encode(b"dummy_png_bytes").decode(),
                "size": [1920, 1080],
            }
        )

        tool = DesktopSnapTool()
        save_path = str(tmp_path / "desktop_snap.png")
        result = await tool.run(save_path=save_path)

        # 验证是否正确调用了底层控制器进行物理截图
        mock_controller.get_screenshot.assert_called_once()
        assert save_path in result or "evidence" in result

    @pytest.mark.asyncio
    @patch("toolset.definitions.visual_control.DesktopController")
    async def test_desktop_act_click(self, mock_controller):
        """测试 desktop_act 单击/双击行为在模拟环境中的正常派发"""
        mock_controller.perform_click = AsyncMock(return_value={"status": "success"})

        tool = DesktopActTool()
        result = await tool.run(action="click", x=100, y=200)

        mock_controller.perform_click.assert_called_once_with(100, 200, double=False)
        assert "单击 (100, 200) 成功" in result

    @pytest.mark.asyncio
    @patch("toolset.definitions.visual_control.DesktopController")
    async def test_desktop_act_type(self, mock_controller):
        """测试 desktop_act 输入文本行为在模拟环境中的正常派发"""
        mock_controller.perform_type = AsyncMock(return_value={"status": "success"})

        tool = DesktopActTool()
        result = await tool.run(action="type", text="Hello Rooster")

        mock_controller.perform_type.assert_called_once_with("Hello Rooster")
        assert "已输入文字" in result
