"""动作执行模块 - 键鼠操控与窗口管理

跨平台实现：pyautogui (键鼠) + 窗口管理。
"""

import ctypes
import logging
import platform
import subprocess
import time
from ctypes import wintypes
from typing import Tuple

logger = logging.getLogger(__name__)


def click(x: int, y: int, duration: float = 0.1) -> None:
    """移动鼠标到 (x, y) 并点击。"""
    import pyautogui
    pyautogui.moveTo(x, y, duration=duration)
    pyautogui.click()


def scroll(x: int, y: int, clicks: int = -3) -> None:
    """在 (x, y) 位置滚动鼠标滚轮。负值向下滚动。"""
    import pyautogui
    pyautogui.moveTo(x, y, duration=0.05)
    pyautogui.scroll(clicks)


def wait(seconds: float) -> None:
    """等待指定秒数。"""
    time.sleep(seconds)


def focus_window(title_keyword: str) -> bool:
    """尝试激活标题包含关键词的窗口。

    支持按优先级匹配多个关键词（用 | 分隔），
    如 "简幻欢|微信" 会先找简幻欢，找不到再找微信。

    Returns:
        是否成功找到并激活窗口。
    """
    # 拆分成多个关键词，按优先级依次尝试
    keywords = [k.strip() for k in title_keyword.split("|")] if "|" in title_keyword else [title_keyword]

    system = platform.system()
    try:
        for kw in keywords:
            if system == "Windows":
                ok = _focus_windows(kw)
            elif system == "Linux":
                ok = _focus_linux(kw)
            elif system == "Darwin":
                ok = _focus_macos(kw)
            else:
                ok = False
            if ok:
                return True
    except Exception:
        pass
    return False


def _focus_linux(keyword: str) -> bool:
    """xdotool 激活窗口。"""
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", keyword],
            capture_output=True, text=True, timeout=3,
        )
        window_ids = result.stdout.strip().splitlines()
        if not window_ids or not window_ids[0]:
            return False
        wid = window_ids[0]
        subprocess.run(
            ["xdotool", "windowactivate", wid],
            capture_output=True, timeout=3,
        )
        return True
    except FileNotFoundError:
        return False


def _focus_macos(keyword: str) -> bool:
    """macOS AppleScript 激活窗口。"""
    script = f'''
    tell application "System Events"
        set frontmost of process "{keyword}" to true
    end tell
    '''
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, timeout=5,
    )
    return result.returncode == 0


# ──────────────────────────────────────────────
# Windows 焦点管理（Win32 API 直调，不依赖 pygetwindow）
# ──────────────────────────────────────────────

_HAS_WIN32 = platform.system() == "Windows" and hasattr(ctypes, "windll")

# Win32 常量
_SW_RESTORE = 9
_SW_SHOW = 5
_HWND_TOP = 0
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_SHOWWINDOW = 0x0040
_FLASHW_ALL = 0x00000003
_FLASHW_TIMERNOFG = 0x0000000C


def _is_admin() -> bool:
    """检查当前进程是否以管理员权限运行。"""
    if not _HAS_WIN32:
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _find_hwnd_by_keyword(keyword: str) -> int | None:
    """枚举所有可见窗口，返回标题包含关键词的第一个窗口句柄。"""
    if not _HAS_WIN32:
        return None
    user32 = ctypes.windll.user32
    found = []

    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value
        if title and keyword.lower() in title.lower():
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_cb(callback), 0)
    return found[0] if found else None


def _is_foreground(hwnd: int) -> bool:
    """检查窗口句柄是否已是当前前台窗口。"""
    try:
        return ctypes.windll.user32.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _flash_taskbar(hwnd: int) -> None:
    """闪烁任务栏按钮，引导用户手动激活。"""
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
        info.hwnd = hwnd
        info.dwFlags = _FLASHW_ALL | _FLASHW_TIMERNOFG
        info.uCount = 0
        info.dwTimeout = 0
        user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def _force_foreground(hwnd: int) -> bool:
    """强制将窗口置前（绕过 Windows 前台锁）。

    多层回退：
        1. AttachThreadInput（绕过前台锁）
        2. Alt 键模拟（改变前台锁上下文）
        3. 最小化再还原（触发窗口重新激活）
    """
    if not _HAS_WIN32:
        return False
    user32 = ctypes.windll.user32

    # 已经是前台 → 不用操作
    if _is_foreground(hwnd):
        return True

    # ---- 第 1 层：AttachThreadInput ---- 
    foreground_hwnd = user32.GetForegroundWindow()
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    current_tid = user32.GetWindowThreadProcessId(foreground_hwnd, None)

    attached = False
    if target_tid != current_tid:
        attached = user32.AttachThreadInput(current_tid, target_tid, True) != 0

    user32.ShowWindow(hwnd, _SW_RESTORE)
    ok = user32.SetForegroundWindow(hwnd)
    user32.SetWindowPos(
        hwnd, _HWND_TOP, 0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW,
    )

    if attached:
        user32.AttachThreadInput(current_tid, target_tid, False)

    if _is_foreground(hwnd):
        return True

    # ---- 第 2 层：模拟 Alt 键（改变前台锁上下文） ----
    user32.keybd_event(0x12, 0, 0, 0)  # Alt down
    user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    time.sleep(0.05)

    ok = user32.SetForegroundWindow(hwnd)
    user32.SetWindowPos(
        hwnd, _HWND_TOP, 0, 0, 0, 0,
        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_SHOWWINDOW,
    )

    if _is_foreground(hwnd):
        return True

    # ---- 第 3 层：最小化再还原 ----
    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    time.sleep(0.1)
    user32.ShowWindow(hwnd, _SW_RESTORE)
    ok = user32.SetForegroundWindow(hwnd)

    return _is_foreground(hwnd)


def _focus_windows(keyword: str) -> bool:
    """Windows：按标题关键词查找窗口并强制置前。

    完整流程：
        1. 查找窗口句柄
        2. 检查是否已是前台（跳过无事可做）
        3. 检查管理员权限（非必需但会影响成功率）
        4. 强制置前（绕过前台锁）
        5. 验证结果，失败则闪烁任务栏
    """
    if not _HAS_WIN32:
        return False

    hwnd = _find_hwnd_by_keyword(keyword)
    if hwnd is None:
        logger.warning(f"未找到标题包含 '{keyword}' 的窗口")
        return False

    # 已经是前台 → 无需操作
    if _is_foreground(hwnd):
        logger.debug(f"窗口 '{keyword}' 已在焦点")
        return True

    # 管理员提示（仅日志，不阻止执行）
    if not _is_admin():
        logger.info("建议以管理员权限运行，窗口激活更可靠")

    # 强制置前
    ok = _force_foreground(hwnd)

    if ok:
        logger.info(f"已激活窗口 '{keyword}'")
    else:
        logger.warning(f"激活窗口 '{keyword}' 失败，请手动切换到微信窗口")
        _flash_taskbar(hwnd)

    return ok


def is_window_in_focus(title_keyword: str) -> bool:
    """检查指定关键词的窗口是否当前在前台。

    支持 | 分隔的多个关键词，任一匹配即可。
    """
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


def get_screen_size() -> Tuple[int, int]:
    """获取屏幕分辨率。"""
    import pyautogui
    return pyautogui.size()
