"""截图模块 - 跨平台屏幕截图

使用 mss 实现高性能截图，兼容 Linux / Windows / macOS。
"""

from typing import Tuple, Optional
import numpy as np
import mss
import mss.tools


def screenshot(region: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
    """截取屏幕指定区域，返回 BGR 格式的 numpy 数组。

    Args:
        region: (left, top, width, height)，None 表示全屏。

    Returns:
        shape (H, W, 3) 的 BGR numpy 数组。
    """
    with mss.mss() as sct:
        if region is not None:
            left, top, width, height = region
            monitor = {"top": top, "left": left, "width": width, "height": height}
        else:
            monitor = sct.monitors[1]  # 主显示器

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
