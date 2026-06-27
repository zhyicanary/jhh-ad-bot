"""动作执行模块 - 键鼠操控与窗口管理

跨平台实现：pyautogui (键鼠) + 平台窗口工具。
"""
··
import time
import subprocess
import platform
from typing import Tuple, Optional


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

    Returns:
        是否成功找到并激活窗口。
    """
    system = platform.system()
    try:
        if system == "Linux":
            return _focus_linux(title_keyword)
        elif system == "Windows":
            return _focus_windows(title_keyword)
        elif system == "Darwin":
            return _focus_macos(title_keyword)
    except Exception:
        pass
    return False


def _focus_linux(keyword: str) -> bool:
    """xdotool 激活窗口。"""
    try:
        result = subprocess.run(
            ["xdotool", "search", "--name", keyword],
            capture_output=True, text=True, timeout=3
        )
        window_ids = result.stdout.strip().splitlines()
        if not window_ids or not window_ids[0]:
            return False
        wid = window_ids[0]
        subprocess.run(
            ["xdotool", "windowactivate", wid],
            capture_output=True, timeout=3
        )
        return True
    except FileNotFoundError:
        # xdotool 未安装
        return False


def _focus_windows(keyword: str) -> bool:
    """Windows 激活窗口。"""
    import pygetwindow as gw
    for win in gw.getWindowsWithTitle(keyword):
        if keyword.lower() in win.title.lower():
            win.activate()
            return True
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
        capture_output=True, timeout=5
    )
    return result.returncode == 0


def get_screen_size() -> Tuple[int, int]:
    """获取屏幕分辨率。"""
    import pyautogui
    return pyautogui.size()
