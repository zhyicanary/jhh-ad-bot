"""公共工具模块 — 提取各文件重复的辅助函数。

包含：管理员权限检测、资源路径、配置加载、窗口枚举、DPI 感知。
"""

import ctypes
import logging
import os
import platform
import sys
from ctypes import wintypes
from typing import Optional, List

logger = logging.getLogger(__name__)

# ── Win32 API 检测（统一判定方式）──
_HAS_WIN32 = platform.system() == "Windows" and hasattr(ctypes, "windll")

# ── Win32 常量 ──
SW_RESTORE = 9
SW_SHOW = 5


def is_admin() -> bool:
    """检查是否以管理员权限运行。"""
    if not _HAS_WIN32:
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin() -> bool:
    """以管理员权限重启程序。返回 False 表示无需重启或重启失败。"""
    if not _HAS_WIN32 or is_admin():
        return False
    try:
        params = " ".join([f'"{arg}"' for arg in sys.argv])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)
    except Exception:
        return False


def resource_path(relative_path: str) -> str:
    """获取打包后资源文件的真实路径。

    PyInstaller 打包后文件解压在 sys._MEIPASS 临时目录，
    直接使用相对路径会找不到文件，需通过此函数转换。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def load_config(path: str) -> dict:
    """加载 YAML 配置文件。"""
    import yaml
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def enable_dpi_awareness() -> None:
    """启用 DPI 感知，确保 SetCursorPos 使用与截图一致的坐标系。"""
    if not _HAS_WIN32:
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)
        )
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


def find_windows_by_keywords(
    keywords: List[str],
    visible_only: bool = True,
) -> List[int]:
    """枚举窗口，返回标题包含任一关键词的窗口句柄列表。

    Args:
        keywords: 关键词列表，标题子串匹配（不区分大小写）。
        visible_only: True=只返回可见窗口，False=包含隐藏窗口。

    Returns:
        匹配的窗口句柄列表，按枚举顺序（通常为 Z 序）。
    """
    if not _HAS_WIN32:
        return []

    user32 = ctypes.windll.user32
    found: List[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value
        if title:
            title_lower = title.lower()
            for kw in keywords:
                if kw.lower() in title_lower:
                    found.append(hwnd)
                    break
        return True

    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(enum_cb(callback), 0)
    return found


def find_window_by_keyword(
    keyword: str,
    visible_only: bool = True,
) -> Optional[int]:
    """枚举窗口，返回标题包含关键词的第一个窗口句柄。"""
    results = find_windows_by_keywords([keyword], visible_only=visible_only)
    return results[0] if results else None


def find_windows_by_title(
    keywords: List[str],
) -> tuple[list[int], list[int]]:
    """枚举窗口，返回 (可见匹配, 隐藏匹配) 两个列表。

    用于微信等场景：优先用可见窗口，找不到才用隐藏窗口（托盘）。
    """
    if not _HAS_WIN32:
        return [], []

    user32 = ctypes.windll.user32
    visible: List[int] = []
    hidden: List[int] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value
        if not title:
            return True
        title_lower = title.lower()
        for kw in keywords:
            if kw.lower() in title_lower:
                if user32.IsWindowVisible(hwnd):
                    visible.append(hwnd)
                else:
                    hidden.append(hwnd)
                break
        return True

    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(enum_cb(callback), 0)
    return visible, hidden
