try:
    import mss

    HAS_MSS = True
except ImportError:
    HAS_MSS = False
import ctypes
import platform
from typing import List, Dict, Any

_IS_WINDOWS = platform.system().lower() == "windows"

# Windows 专属依赖（非 Windows 平台优雅降级）
# Windows-only dependencies (graceful degradation on non-Windows)
if _IS_WINDOWS:
    try:
        import win32gui
        import win32process
        import win32con
        from ctypes import wintypes
        from pywinauto import Desktop

        HAS_WIN32 = True
    except ImportError:
        HAS_WIN32 = False
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
        HAS_DWM = True
    except OSError:
        HAS_DWM = False
else:
    HAS_WIN32 = False
    HAS_DWM = False

# DWM 常量
# DWM constants
DWMWA_CLOAKED = 14
DWMWA_EXTENDED_FRAME_BOUNDS = 9

from .geometry import get_true_window_rect, calculate_visible_part


class OmniVisionEngine:
    """Windows 全局 UIA 探测引擎 - 内部辅助类"""

    def _fallback_screen_res(self):
        try:
            import pyautogui

            self.res_x, self.res_y = pyautogui.size()
        except Exception:
            if _IS_WINDOWS:
                try:
                    user32 = ctypes.windll.user32
                    self.res_x = user32.GetSystemMetrics(0)
                    self.res_y = user32.GetSystemMetrics(1)
                except Exception:
                    self.res_x, self.res_y = 1920, 1080
            else:
                self.res_x, self.res_y = 1920, 1080

    def __init__(self, screen_res=None):
        if screen_res is None:
            if HAS_MSS:
                try:
                    with mss.mss() as sct:
                        monitor = sct.monitors[1]
                        self.res_x, self.res_y = monitor["width"], monitor["height"]
                except Exception:
                    self._fallback_screen_res()
            else:
                self._fallback_screen_res()
        else:
            self.res_x, self.res_y = screen_res

        self.scale = 1.0
        if _IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32
                logical_width = user32.GetSystemMetrics(0)
                self.scale = self.res_x / logical_width if logical_width > 0 else 1.0
            except Exception:
                pass

    def dump_uia_all(self) -> List[Dict[str, Any]]:
        if not _IS_WINDOWS or not HAS_WIN32:
            return []
        try:
            desktop = Desktop(backend="uia")
            seen_nodes = {}

            for win in desktop.windows():
                try:
                    if not win.is_visible():
                        continue
                    hwnd = win.handle

                    for i, ctrl in enumerate(win.descendants()):
                        if i > 2500:
                            break
                        try:
                            c_info = ctrl.element_info
                            c_type = str(c_info.control_type).split(".")[-1]
                            name = (ctrl.window_text() or c_info.name or "").strip()

                            r = ctrl.rectangle()
                            if r.width() <= 5 or r.height() <= 5:
                                continue
                            cx, cy = r.left + r.width() // 2, r.top + r.height() // 2

                            if cx < 0 or cy < 0 or cx > self.res_x or cy > self.res_y:
                                continue

                            grid_key = f"{cx // 15}_{cy // 15}"
                            node_data = {
                                "name": name or c_type,
                                "type": c_type,
                                "box": [r.left, r.top, r.right, r.bottom],
                                "center": [cx, cy],
                                "hwnd": hwnd,
                                "is_enabled": ctrl.is_enabled(),  # 补充状态
                                # Enabled state
                                "has_focus": ctrl.has_keyboard_focus(),  # 补充焦点
                                # Focus state
                                "_area": r.width() * r.height(),
                            }

                            if grid_key in seen_nodes:
                                if node_data["_area"] < seen_nodes[grid_key]["_area"]:
                                    seen_nodes[grid_key] = node_data
                            else:
                                seen_nodes[grid_key] = node_data
                        except Exception:
                            continue
                except Exception:
                    continue

            elements = list(seen_nodes.values())
            for e in elements:
                e.pop("_area", None)
            return elements
        except Exception:
            return []


class EliteEngine:
    """OmniVision V3.9 精英探测引擎 - Rooster 集成版"""

    def __init__(self):
        self.uia_engine = OmniVisionEngine()

    def is_window_cloaked(self, hwnd) -> bool:
        if not _IS_WINDOWS or not HAS_DWM:
            return False
        cloaked = wintypes.DWORD()
        res = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), wintypes.DWORD(DWMWA_CLOAKED), ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return res == 0 and cloaked.value != 0

    def dump(self) -> List[Dict[str, Any]]:
        """执行全精英扫描逻辑（仅 Windows 可用）"""
        if not _IS_WINDOWS or not HAS_WIN32:
            return []
        # 1. 抓取前台上下文与任务栏
        # 1. Capture foreground context and taskbar
        fg_hwnd = win32gui.GetForegroundWindow()
        if not fg_hwnd:
            return []

        _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
        fg_class = win32gui.GetClassName(fg_hwnd)
        is_focus_on_desktop = fg_class in ["Progman", "WorkerW"]

        tray_hwnd = win32gui.FindWindow("Shell_TrayWnd", None)
        active_root_hwnds = {fg_hwnd}
        if tray_hwnd:
            active_root_hwnds.add(tray_hwnd)

        # 2. 抓取遮挡层（所有在 fg_hwnd 之上的可见窗口）
        # 2. Capture obstruction layer (all visible windows above fg_hwnd)
        obstruction_boxes = []
        curr = fg_hwnd
        while True:
            curr = win32gui.GetWindow(curr, win32con.GW_HWNDPREV)
            if not curr:
                break
            if win32gui.IsWindowVisible(curr) and not self.is_window_cloaked(curr):
                rect = get_true_window_rect(curr)
                if (rect[2] - rect[0]) > 10 and (rect[3] - rect[1]) > 10:
                    obstruction_boxes.append(rect)

        def enum_win(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and not self.is_window_cloaked(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                cls = win32gui.GetClassName(hwnd)
                if pid == fg_pid and cls not in ["Progman", "WorkerW"]:
                    active_root_hwnds.add(hwnd)
                if is_focus_on_desktop and cls in ["Progman", "WorkerW"]:
                    active_root_hwnds.add(hwnd)
                if cls.startswith("#32768"):
                    active_root_hwnds.add(hwnd)

        win32gui.EnumWindows(enum_win, None)

        # 3. 深度探测并执行白名单过滤
        # 3. Deep probe and whitelist filtering
        raw_nodes = self.uia_engine.dump_uia_all()
        root_to_stats = {h: {"interactive": 0, "nodes": []} for h in active_root_hwnds}

        for node in raw_nodes:
            node_hwnd = int(node.get("hwnd", 0))
            if not node_hwnd:
                continue

            root_win = win32gui.GetAncestor(node_hwnd, 2)
            if root_win not in active_root_hwnds:
                continue

            # 几何可见性判定
            # Geometric visibility check
            box = node["box"]
            visible_box = calculate_visible_part(box, obstruction_boxes)
            if not visible_box:
                continue

            node["box"] = visible_box
            node["center"] = [
                visible_box[0] + (visible_box[2] - visible_box[0]) // 2,
                visible_box[1] + (visible_box[3] - visible_box[1]) // 2,
            ]

            node["source"] = "ROOSTER-VISION-ELITE-V3.9"
            c_type = node["type"]
            is_container = c_type in {
                "Pane",
                "Group",
                "List",
                "Tab",
                "ToolBar",
                "Window",
                "Custom",
                "Document",
                "Table",
                "Tree",
                "MenuBar",
            }
            node["is_container"] = is_container

            if root_win in root_to_stats:
                if not is_container:
                    root_to_stats[root_win]["interactive"] += 1
                root_to_stats[root_win]["nodes"].append(node)

        # 4. 黑盒补偿协议
        # 4. Black-box compensation protocol
        filtered_nodes = []
        compensate_idx = 0
        for root_win, stats in root_to_stats.items():
            nodes = stats["nodes"]
            if nodes and stats["interactive"] < 20:
                cls = win32gui.GetClassName(root_win)
                if cls != "Shell_TrayWnd" and not cls.startswith("#32768"):
                    best_root = None
                    max_area = -1
                    for n in nodes:
                        if n["type"] in ["Pane", "Window", "Group", "Custom"]:
                            w, h = n["box"][2] - n["box"][0], n["box"][3] - n["box"][1]
                            if w * h > max_area:
                                max_area = w * h
                                best_root = n
                    if best_root:
                        best_root["force_draw"] = True
                        best_root["is_container"] = False
                        best_root["_id"] = f"W{compensate_idx}"
                        t = win32gui.GetWindowText(root_win).strip()
                        best_root["name"] = f"[{t if t else 'App Window'}]"
                        compensate_idx += 1
            filtered_nodes.extend(nodes)

        return filtered_nodes
