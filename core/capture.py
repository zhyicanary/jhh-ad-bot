"""截图模块 - 跨平台屏幕截图

使用 mss 实现高性能截图，兼容 Linux / Windows / macOS。
"""

from typing import Tuple, Optional
import numpy as np
import mss
import mss.tools


def screenshot(region: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
    """截取屏幕指定区域，返回 BGR 格式的 numpy 数组。

    在 Windows 上自动启用 DPI 感知，确保截图分辨率与屏幕物理像素一致，
    使模板匹配坐标与 SetCursorPos 坐标系对齐。

    Args:
        region: (left, top, width, height)，None 表示全屏。

    Returns:
        shape (H, W, 3) 的 BGR numpy 数组。
    """
    # 确保 DPI 感知已启用（与 action.py 保持一致）
    import platform
    if platform.system() == "Windows":
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

    with mss.mss() as sct:
        if region is not None:
            left, top, width, height = region
            monitor = {"top": top, "left": left, "width": width, "height": height}
        else:
            # monitors[1] 是主显示器（monitors[0] 是虚拟全屏）
            monitor = sct.monitors[1]

        img = sct.grab(monitor)
        # mss 返回 BGRA，转为 numpy 后去掉 alpha 通道
        arr = np.array(img)
        return arr[:, :, :3]  # BGR


def screenshot_gray(region: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
    """截取屏幕并转为灰度图。

    Args:
        region: (left, top, width, height)，None 表示全屏。

    Returns:
        shape (H, W) 的单通道灰度 numpy 数组。
    """
    bgr = screenshot(region)
    import cv2
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
