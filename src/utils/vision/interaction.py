import ctypes
import time
import logging
import platform
from typing import Tuple

_IS_WINDOWS = platform.system().lower() == "windows"

# 物理控制依赖
# Physical control dependencies
try:
    import pyautogui
    import pyperclip  # 统筹剪贴板输入
    # Clipboard input coordination

    HAS_LIBS = True
except (ImportError, KeyError, Exception):
    HAS_LIBS = False

# Windows 专属 API（非 Windows 平台跳过）
# Windows-only API (skipped on non-Windows platforms)
if _IS_WINDOWS:
    try:
        import win32api
        import win32con
        import win32gui

        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
else:
    HAS_WIN32 = False

logger = logging.getLogger("DesktopInteraction")


# 1. --- DPI 知晓初始化 (Windows 核心修复) ---
def init_dpi_awareness():
    """强制 Windows 意识到本进程的 DPI 属性，防止坐标偏移"""
    if platform.system().lower() != "windows":
        return
    try:
        # 尝试 SetProcessDpiAwareness (Windows 8.1+)
        # 1 = PROCESS_SYSTEM_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            # 回退到 SetProcessDPIAware (Windows Vista/7)
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception as e:
            logger.warning(f"DPI Awareness failed to initialize: {e}")


# 执行初始化
init_dpi_awareness()


class DesktopInteraction:
    """
    [Rooster 神经末梢] 桌面底层交互驱动。
    负责所有物理坐标换算、平滑移动以及稳定输入。
    """

    @staticmethod
    def get_scaling_factor() -> float:
        """获取当前主显示器的缩放比例 (例如 1.5 代表 150%)"""
        if platform.system().lower() != "windows":
            return 1.0
        try:
            # 获取物理高度与逻辑高度的比值
            # Get ratio of physical to logical height
            logical_height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
            # 物理高度需要通过更加底层的方法获取
            # Physical height requires lower-level API to obtain
            hDC = win32gui.GetDC(0)
            physical_height = win32api.GetDeviceCaps(hDC, win32con.VERTRES)
            win32gui.ReleaseDC(0, hDC)
            return physical_height / logical_height
        except Exception:
            return 1.0

    @classmethod
    def coordinate_to_physical(cls, x: int, y: int) -> Tuple[int, int]:
        """将逻辑坐标转换为物理点击坐标"""
        factor = cls.get_scaling_factor()
        return int(x * factor), int(y * factor)

    @staticmethod
    def is_admin() -> bool:
        """检查当前进程是否具有管理员权限"""
        if not _IS_WINDOWS:
            import os as _os

            return _os.geteuid() == 0
        try:
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            return False

    @staticmethod
    def _force_focus(x: int, y: int):
        """探测坐标处的窗口并尝试强制唤醒"""
        if platform.system().lower() != "windows":
            return
        try:
            hwnd = win32gui.WindowFromPoint((x, y))
            if hwnd:
                # 首先尝试温和唤醒
                # First try gentle wake-up
                root_hwnd = win32gui.GetAncestor(hwnd, win32con.GA_ROOT)
                if win32gui.GetForegroundWindow() != root_hwnd:
                    logger.info(f"窗口未聚焦，尝试强力激活 HWND: {root_hwnd}")
                    # 使用常用技巧绕过 SetForegroundWindow 限制
                    # Use common trick to bypass SetForegroundWindow restriction
                    win32gui.ShowWindow(root_hwnd, win32con.SW_RESTORE)
                    ctypes.windll.user32.SetForegroundWindow(root_hwnd)
                    time.sleep(0.1)
        except Exception as e:
            logger.debug(f"Focus window failed: {e}")

    @classmethod
    def click(cls, x: int, y: int, double: bool = False, button: str = "left"):
        """物理点击 (支持拟人化抖动与窗口自动换算)"""
        try:
            # 1. 点击前尝试唤醒该位置的窗口
            # 1. Try to wake up the window at this position before clicking
            cls._force_focus(x, y)

            # 2. 注入拟人化微小偏移 (±1像素)
            # 2. Inject human-like micro-offset (±1 pixel)
            import random

            offset_x = x + random.choice([-1, 0, 1])
            offset_y = y + random.choice([-1, 0, 1])

            # 3. 拟人化按下时长处理
            # 3. Human-like press duration
            hold_time = random.uniform(0.04, 0.09)  # 模拟真实点击的按下-抬起间隔
            # Simulate real click press-release interval

            old_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                # 自动化流程中临时关闭 FAILSAFE，避免鼠标恰在角落导致整段点击失败
                # Temporarily disable FAILSAFE during automation to prevent abort when mouse is in corner
                pyautogui.FAILSAFE = False
                if double:
                    pyautogui.click(offset_x, offset_y, clicks=2, interval=0.12, button=button)
                else:
                    pyautogui.mouseDown(offset_x, offset_y, button=button)
                    time.sleep(hold_time)
                    pyautogui.mouseUp(offset_x, offset_y, button=button)
            except Exception:
                # pyautogui 异常时，回退到 Win32 原生点击（仅限 Windows）
                # On pyautogui failure, fallback to Win32 native click (Windows only)
                if _IS_WINDOWS and HAS_WIN32:
                    try:
                        win32api.SetCursorPos((offset_x, offset_y))
                        if button == "right":
                            down, up = win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP
                        else:
                            down, up = win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP
                        taps = 2 if double else 1
                        for _ in range(taps):
                            win32api.mouse_event(down, 0, 0, 0, 0)
                            time.sleep(hold_time)
                            win32api.mouse_event(up, 0, 0, 0, 0)
                            if taps == 2:
                                time.sleep(0.10)
                    except Exception as e2:
                        raise RuntimeError(f"pyautogui+win32 click failed: {e2}")
                else:
                    raise
            finally:
                pyautogui.FAILSAFE = old_fs

            logger.info(f"🖱️  Click at ({offset_x}, {offset_y}) - hold: {hold_time:.3f}s")
        except Exception as e:
            logger.error(f"Click failed: {e}")

    @classmethod
    def drag_to(cls, start_x: int, start_y: int, end_x: int, end_y: int, duration: float = 0.8):
        """物理拖拽 (支持窗口唤醒与平滑轨迹)"""
        try:
            cls._force_focus(start_x, start_y)
            pyautogui.moveTo(start_x, start_y, duration=0.2)
            # 使用 dragTo 模拟按住移动
            # Use dragTo to simulate press-and-move
            pyautogui.dragTo(end_x, end_y, duration=duration, button="left", mouseDownUp=True)
            logger.info(f"🖱️  Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})")
        except Exception as e:
            logger.error(f"Drag failed: {e}")

    @classmethod
    def press_keys(cls, *keys: str):
        """拟人化按键组合"""
        try:
            import random

            def _send_keys():
                if len(keys) > 1:
                    # 组合键增加随机时序
                    # Add random timing for key combos
                    for k in keys:
                        pyautogui.keyDown(k)
                        time.sleep(random.uniform(0.02, 0.05))
                    for k in reversed(keys):
                        pyautogui.keyUp(k)
                        time.sleep(random.uniform(0.01, 0.03))
                    logger.info(f"⌨️  Hotkeys: {'+'.join(keys)}")
                else:
                    # 单键点击
                    # Single key press
                    hold = random.uniform(0.03, 0.08)
                    pyautogui.keyDown(keys[0])
                    time.sleep(hold)
                    pyautogui.keyUp(keys[0])
                    logger.info(f"⌨️  Key: {keys[0]} - hold: {hold:.3f}s")

            old_fs = getattr(pyautogui, "FAILSAFE", True)
            try:
                pyautogui.FAILSAFE = False
                _send_keys()
            finally:
                pyautogui.FAILSAFE = old_fs
        except Exception as e:
            logger.error(f"Hotkey failed: {e}")

    @classmethod
    def type_text(cls, text: str, fallback_to_clipboard: bool = True):
        """全场景稳定文字输入"""
        if not text:
            return
        try:
            use_clipboard = fallback_to_clipboard and (len(text) > 5 or any(ord(c) > 127 for c in text))
            if use_clipboard:
                pyperclip.copy(text)
                time.sleep(0.1)
                if _IS_WINDOWS:
                    pyautogui.hotkey("ctrl", "v")
                else:
                    pyautogui.hotkey("command", "v")
                time.sleep(0.2)
            else:
                pyautogui.typewrite(text, interval=0.03)
            logger.info(f"⌨️  Typed text (via_clipboard: {use_clipboard})")
        except Exception as e:
            logger.error(f"Type failed: {e}")

    @classmethod
    def scroll(cls, direction: str = "down", amount: int = 600):
        """物理滚动控制"""
        try:
            val = -amount if direction == "down" else amount
            pyautogui.scroll(val)
            logger.info(f"📜 Scrolled {direction} {amount}")
        except Exception as e:
            logger.error(f"Scroll failed: {e}")

    @classmethod
    def move_to(cls, x: int, y: int, smooth: bool = True):
        """移动鼠标"""
        try:
            pyautogui.moveTo(x, y, duration=0.2 if smooth else 0)
        except Exception as e:
            logger.error(f"Move failed: {e}")

    @staticmethod
    def capture_screen():
        """物理抓取当前屏幕。优先 pyautogui；若 Win32 GDI 上下文不可用则回退 mss。"""
        try:
            if not HAS_LIBS:
                raise ImportError("Physical libraries not loaded")
            import pyautogui

            return pyautogui.screenshot()
        except Exception as e:
            logger.warning(f"pyautogui.screenshot 失败，切换 mss fallback: {e}")
            try:
                import mss
                from PIL import Image as PILImage

                with mss.mss() as sct:
                    monitor = sct.monitors[1]  # 主显示器
                    raw = sct.grab(monitor)
                    img = PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    return img
            except Exception as e2:
                logger.error(f"mss fallback 也失败: {e2}")
                raise e2


# 简单的环境自检
# Simple environment self-check
if __name__ == "__main__":
    init_dpi_awareness()
    print(f"Current Scaling Factor: {DesktopInteraction.get_scaling_factor():.2f}")
