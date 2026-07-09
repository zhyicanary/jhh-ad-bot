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

# ── Win32 API 检测 ──
_HAS_WIN32 = platform.system() == "Windows" and hasattr(ctypes, "windll")

# ── Win32 SendInput 鼠标点击 ──
# 系统级输入模拟（SRA 方案），真实度最高
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


def _enable_dpi_awareness() -> None:
    """启用 DPI 感知，确保 SetCursorPos 使用与截图一致的坐标系。"""
    if not _HAS_WIN32:
        return
    try:
        # SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
    except (AttributeError, OSError):
        try:
            # 回退: SetProcessDpiAwareness(PER_MONITOR_AWARE)
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                # 最终回退: SetProcessDPIAware()
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


# 启动时立即设置 DPI 感知
_enable_dpi_awareness()


def _get_dpi_scale() -> float:
    """获取当前显示器的 DPI 缩放比例（1.0 = 100%，1.5 = 150%）。"""
    if not _HAS_WIN32:
        return 1.0
    try:
        user32 = ctypes.windll.user32
        # 获取实际 DPI
        hdc = user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        user32.ReleaseDC(0, hdc)
        return dpi / 96.0
    except Exception:
        return 1.0


def _physical_to_logical(x: int, y: int) -> tuple[int, int]:
    """将物理像素坐标（mss 截图坐标系）转换为逻辑像素坐标（SetCursorPos 坐标系）。"""
    scale = _get_dpi_scale()
    if scale == 1.0:
        return x, y
    return int(x / scale), int(y / scale)


def click(x: int, y: int, duration: float = 0.1, clicks: int = 1) -> None:
    """使用 SendInput 模拟鼠标点击（系统级，最真实）。

    自动处理 DPI 缩放：截图坐标（物理像素）→ 光标坐标（逻辑像素）。
    移动光标时模拟轨迹，避免部分应用检测瞬移忽略点击。
    非 Windows 平台回退到 pyautogui。
    """
    if _SENDINPUT_READY:
        user32 = ctypes.windll.user32

        # DPI 转换：截图是物理像素，SetCursorPos 需要逻辑像素
        lx, ly = _physical_to_logical(x, y)

        # 模拟鼠标移动轨迹（从当前位置逐步移动到目标）
        _move_with_trajectory(user32, lx, ly)
        time.sleep(0.08)  # 等光标稳定

        for i in range(clicks):
            mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTDOWN, 0, None)
            inp = _INPUT(_INPUT_MOUSE, _INPUT_UNION(mi))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            time.sleep(0.03)

            mi = _MOUSEINPUT(0, 0, 0, _MOUSEEVENTF_LEFTUP, 0, None)
            inp = _INPUT(_INPUT_MOUSE, _INPUT_UNION(mi))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
            if i < clicks - 1:
                time.sleep(0.08)  # 多次点击之间的间隔
        time.sleep(0.05)
        return

    # 非 Windows 回退
    import pyautogui
    pyautogui.moveTo(x, y, duration=duration)
    for _ in range(clicks):
        pyautogui.click()
        time.sleep(0.05)


def _move_with_trajectory(user32, target_x: int, target_y: int) -> None:
    """模拟鼠标从当前位置逐步移动到目标，避免瞬移被检测。"""
    # 获取当前位置
    point = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    start_x, start_y = point.x, point.y

    dx = target_x - start_x
    dy = target_y - start_y
    distance = (dx * dx + dy * dy) ** 0.5

    if distance < 3:
        # 距离很近，直接移动
        user32.SetCursorPos(target_x, target_y)
        return

    # 分步移动（约 10-15 步，每步 5-10ms）
    steps = min(max(int(distance / 20), 5), 15)
    for i in range(1, steps + 1):
        # 使用缓动函数让移动更自然
        t = i / steps
        ease = 1 - (1 - t) ** 3  # ease-out cubic
        cx = int(start_x + dx * ease)
        cy = int(start_y + dy * ease)
        user32.SetCursorPos(cx, cy)
        time.sleep(0.005)

    # 确保最终位置精确
    user32.SetCursorPos(target_x, target_y)


def set_target(keywords_str: str) -> None:
    """缓存目标窗口句柄（后续可配合窗口截图等使用）。"""
    global _target_hwnd
    if not _HAS_WIN32:
        return
    keywords = [k.strip() for k in keywords_str.split("|")] if "|" in keywords_str else [keywords_str]
    for kw in keywords:
        hwnd = _find_hwnd_by_keyword(kw)
        if hwnd:
            _target_hwnd = hwnd
            logger.debug(f"目标窗口: {kw} (hwnd={hwnd})")
            return
    _target_hwnd = None


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
