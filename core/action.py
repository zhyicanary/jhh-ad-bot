"""动作执行模块 - 键鼠操控与窗口管理

跨平台实现：pyautogui (键鼠) + 窗口管理。
"""

import ctypes
import logging
import platform
import time
from ctypes import wintypes

from .utils import (
    _HAS_WIN32,
    enable_dpi_awareness,
    is_admin as _is_admin,
    find_window_by_keyword as _find_hwnd_by_keyword,
    SW_RESTORE,
)

logger = logging.getLogger(__name__)

# ── Win32 SendInput 鼠标点击 ──
_SENDINPUT_READY = False
if _HAS_WIN32:
    _INPUT_MOUSE = 0
    _MOUSEEVENTF_LEFTDOWN = 0x0002
    _MOUSEEVENTF_LEFTUP = 0x0004

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_uint32),
            ("dwFlags", ctypes.c_uint32),
            ("time", ctypes.c_uint32),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    class _INPUT(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_uint32),
            ("union", _INPUT_UNION),
        ]

    _SENDINPUT_READY = True

    # 设置 Win32 API 参数类型，避免 64 位 HWND 溢出
    _user32 = ctypes.windll.user32
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _user32.SetCursorPos.restype = wintypes.BOOL
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.c_void_p, ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]
    _user32.keybd_event.restype = None
    _user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL


# 启用 DPI 感知（模块加载时）
enable_dpi_awareness()


def click(x: int, y: int, duration: float = 0.1, clicks: int = 1) -> None:
    """使用 SendInput 模拟鼠标点击（系统级，最真实）。

    DPI 感知已启用，截图坐标与光标坐标系一致（物理像素），无需转换。
    移动光标时模拟轨迹，避免部分应用检测瞬移忽略点击。
    非 Windows 平台回退到 pyautogui。
    """
    if _SENDINPUT_READY:
        user32 = ctypes.windll.user32

        _move_with_trajectory(user32, x, y)
        time.sleep(0.08)

        for i in range(clicks):
            mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN, 0, None)
            inp = _INPUT(_INPUT_MOUSE, _INPUT_UNION(mi))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            time.sleep(0.03)

            mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP, 0, None)
            inp = _INPUT(_INPUT_MOUSE, _INPUT_UNION(mi))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            if i < clicks - 1:
                time.sleep(0.08)
        time.sleep(0.05)
        return

    import pyautogui
    pyautogui.moveTo(x, y, duration=duration)
    for _ in range(clicks):
        pyautogui.click()
        time.sleep(0.05)


def _move_with_trajectory(user32, target_x: int, target_y: int) -> None:
    """模拟鼠标从当前位置逐步移动到目标，避免瞬移被检测。"""
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    start_x, start_y = point.x, point.y

    dx = target_x - start_x
    dy = target_y - start_y
    distance = (dx * dx + dy * dy) ** 0.5

    if distance < 3:
        user32.SetCursorPos(target_x, target_y)
        return

    steps = min(max(int(distance / 20), 5), 15)
    for i in range(1, steps + 1):
        t = i / steps
        ease = 1 - (1 - t) ** 3
        cx = int(start_x + dx * ease)
        cy = int(start_y + dy * ease)
        user32.SetCursorPos(cx, cy)
        time.sleep(0.005)

    user32.SetCursorPos(target_x, target_y)


def wait(seconds: float) -> None:
    time.sleep(seconds)


def focus_window(title_keyword: str) -> bool:
    """尝试激活标题包含关键词的窗口（Windows 专属）。"""
    keywords = [k.strip() for k in title_keyword.split("|")] if "|" in title_keyword else [title_keyword]

    try:
        for kw in keywords:
            if _focus_windows(kw):
                return True
    except Exception:
        pass
    return False


# ── Windows 焦点管理 ──

_SW_MINIMIZE = 6
_HWND_TOP = 0
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_SHOWWINDOW = 0x0040
_FLASHW_ALL = 0x00000003
_FLASHW_TIMERNOFG = 0x0000000C


def _is_foreground(hwnd: int) -> bool:
    try:
        return ctypes.windll.user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _flash_taskbar(hwnd: int) -> None:
    try:
        user32 = ctypes.windll.user32

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("hwnd", wintypes.HWND),
                ("dwFlags", wintypes.DWORD),
                ("uCount", wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]

        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = wintypes.HWND(hwnd)
        info.dwFlags = _FLASHW_ALL | _FLASHW_TIMERNOFG
        info.uCount = 0
        info.dwTimeout = 0
        user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def _force_foreground(hwnd: int) -> bool:
    """强制将窗口置前（绕过 Windows 前台锁）。"""
    if not _HAS_WIN32:
        return False
    user32 = ctypes.windll.user32

    if _is_foreground(hwnd):
        return True

    hwnd_param = wintypes.HWND(hwnd)

    # 第 1 层：AttachThreadInput
    foreground_hwnd = user32.GetForegroundWindow()
    target_tid = user32.GetWindowThreadProcessId(hwnd_param, None)
    current_tid = user32.GetWindowThreadProcessId(foreground_hwnd, None)

    attached = False
    if target_tid != current_tid:
        attached = user32.AttachThreadInput(current_tid, target_tid, True) != 0

    user32.ShowWindow(hwnd_param, SW_RESTORE)
    user32.SetForegroundWindow(hwnd_param)
    user32.SetWindowPos(
        hwnd_param, wintypes.HWND(_HWND_TOP), 0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW,
    )

    if attached:
        user32.AttachThreadInput(current_tid, target_tid, False)

    if _is_foreground(hwnd):
        return True

    # 第 2 层：模拟 Alt 键
    user32.keybd_event(0x12, 0, 0, None)
    user32.keybd_event(0x12, 0, 2, None)
    time.sleep(0.05)

    user32.SetForegroundWindow(hwnd_param)
    user32.SetWindowPos(
        hwnd_param, wintypes.HWND(_HWND_TOP), 0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW,
    )

    if _is_foreground(hwnd):
        return True

    # 第 3 层：最小化再还原
    user32.ShowWindow(hwnd_param, _SW_MINIMIZE)
    time.sleep(0.1)
    user32.ShowWindow(hwnd_param, SW_RESTORE)
    user32.SetForegroundWindow(hwnd_param)

    return _is_foreground(hwnd)


def _focus_windows(keyword: str) -> bool:
    """Windows：按标题关键词查找窗口并强制置前。"""
    if not _HAS_WIN32:
        return False

    hwnd = _find_hwnd_by_keyword(keyword)
    if hwnd is None:
        logger.warning(f"未找到标题包含 '{keyword}' 的窗口")
        return False

    if _is_foreground(hwnd):
        return True

    if not _is_admin():
        logger.info("建议以管理员权限运行，窗口激活更可靠")

    ok = _force_foreground(hwnd)

    if ok:
        logger.info(f"已激活窗口 '{keyword}'")
    else:
        logger.warning(f"激活窗口 '{keyword}' 失败，请手动切换到微信窗口")
        _flash_taskbar(hwnd)

    return ok


def is_window_in_focus(title_keyword: str) -> bool:
    """检查指定关键词的窗口是否当前在前台。"""
    keywords = [k.strip() for k in title_keyword.split("|")] if "|" in title_keyword else [title_keyword]
    if not _HAS_WIN32:
        return False
    try:
        current = ctypes.windll.user32.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(current, buf, 256)
        current_title = buf.value
        for kw in keywords:
            if kw.lower() in current_title.lower():
                return True
    except Exception:
        pass
    return False
