import ctypes
from typing import Tuple, List, Optional
import platform

_IS_WINDOWS = platform.system().lower() == "windows"

if _IS_WINDOWS:
    try:
        from ctypes import wintypes
        import win32gui

        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
else:
    HAS_WIN32 = False

# Win32 API Constants
DWMWA_EXTENDED_FRAME_BOUNDS = 9


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("top", ctypes.c_int), ("right", ctypes.c_int), ("bottom", ctypes.c_int)]


def get_true_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """
    使用 DWM API 获取窗口真正的物理边界 (去掉透明阴影)。
    非 Windows 平台直接返回 (0,0,0,0) 占位。
    """
    if not _IS_WINDOWS:
        return (0, 0, 0, 0)
    rect = RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd),
            ctypes.wintypes.DWORD(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        if HAS_WIN32:
            r = win32gui.GetWindowRect(hwnd)
            return r
        return (0, 0, 0, 0)


def calculate_visible_part(
    target_box: List[int], obstruction_boxes: List[Tuple[int, int, int, int]]
) -> Optional[List[int]]:
    """
    几何裁剪算法：
    计算 target_box 在被一系列 obstruction_boxes 遮盖后，剩余的最大可见矩形。
    target_box: [x1, y1, x2, y2]
    """
    cur_x1, cur_y1, cur_x2, cur_y2 = target_box
    original_area = (cur_x2 - cur_x1) * (cur_y2 - cur_y1)

    if original_area <= 0:
        return None

    # 由于计算“任意多个矩形叠加后的剩余最大化内接矩形”计算量极大，
    # 我们采用“贪婪扣除”策略。如果被上方任意一个窗口大比例遮挡中心
    # 或者遮盖面积过大，直接标记为不可见。

    for ox1, oy1, ox2, oy2 in obstruction_boxes:
        # 计算交集区域
        ix1 = max(cur_x1, ox1)
        iy1 = max(cur_y1, oy1)
        ix2 = min(cur_x2, ox2)
        iy2 = min(cur_y2, oy2)

        if ix2 > ix1 and iy2 > iy1:
            # 存在重叠。计算重叠面积
            inter_area = (ix2 - ix1) * (iy2 - iy1)

            # 如果遮挡超过 80% (对于小按钮更严格)，判定为失能
            if inter_area / original_area > 0.8:
                return None

            # 策略：如果遮挡的是边缘，尝试收缩边界
            if ix1 <= cur_x1 and ix2 >= cur_x2:  # 情况 A: 横向全切，垂直收缩
                if oy1 <= cur_y1:
                    cur_y1 = max(cur_y1, oy2)
                else:
                    cur_y2 = min(cur_y2, oy1)
            elif iy1 <= cur_y1 and iy2 >= cur_y2:  # 情况 B: 垂直全切，横向收缩
                if ox1 <= cur_x1:
                    cur_x1 = max(cur_x1, ox2)
                else:
                    cur_x2 = min(cur_x2, ox1)

    # 最终完整度检查
    new_area = (cur_x2 - cur_x1) * (cur_y2 - cur_y1)
    if new_area < original_area * 0.3 or (cur_x2 - cur_x1) < 10 or (cur_y2 - cur_y1) < 10:
        return None

    return [cur_x1, cur_y1, cur_x2, cur_y2]
