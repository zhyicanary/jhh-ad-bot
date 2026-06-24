"""图像识别模块 - OpenCV 模板匹配

在屏幕截图中定位按钮/图标。
"""

from typing import Tuple, Optional, List
import os
import cv2
import numpy as np


def load_template(path: str, grayscale: bool = True) -> np.ndarray:
    """加载模板图片。

    Args:
        path: 模板图片路径。
        grayscale: 是否转灰度。

    Returns:
        numpy 数组，灰度图 (H, W) 或彩色图 (H, W, 3)。
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"模板文件不存在: {path}")
    img = cv2.imread(path)
    if img is None:
        raise ValueError(f"无法读取模板图片: {path}")
    if grayscale:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def match_template(
    screenshot_arr: np.ndarray,
    template_path: str,
    threshold: float = 0.75,
    scale_steps: int = 5,
    scale_range: Tuple[float, float] = (0.8, 1.2),
) -> Optional[Tuple[int, int, float]]:
    """
    在截图中查找模板图片，返回最佳匹配的中心坐标和置信度。

    支持多尺度匹配（缩放模板），应对不同分辨率/缩放比例。

    Args:
        screenshot_arr: 截图（BGR 或灰度 numpy 数组）。
        template_path: 模板图片路径。
        threshold: 最低置信度阈值 (0-1)，低于此值视为未找到。
        scale_steps: 多尺度匹配的缩放步数。
        scale_range: 缩放范围 (min, max)。

    Returns:
        若找到: (center_x, center_y, confidence)
        未找到: None
    """
    template = load_template(template_path)
    screen_gray: np.ndarray = _ensure_gray(screenshot_arr)

    best_match = None
    best_val = 0.0

    for scale in np.linspace(scale_range[0], scale_range[1], scale_steps):
        scaled = _resize_template(template, scale)
        if scaled.shape[0] > screen_gray.shape[0] or scaled.shape[1] > screen_gray.shape[1]:
            continue  # 缩放后比屏幕还大，跳过

        result = cv2.matchTemplate(screen_gray, scaled, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > best_val:
            best_val = max_val
            h, w = scaled.shape
            center_x = max_loc[0] + w // 2
            center_y = max_loc[1] + h // 2
            best_match = (center_x, center_y, max_val)

    if best_match is None or best_match[2] < threshold:
        return None
    return best_match


def match_all(
    screenshot_arr: np.ndarray,
    template_path: str,
    threshold: float = 0.75,
) -> List[Tuple[int, int, float]]:
    """查找截图中所有匹配的模板位置。

    Returns:
        [(center_x, center_y, confidence), ...] 按置信度降序。
    """
    template = load_template(template_path)
    screen_gray = _ensure_gray(screenshot_arr)

    result = cv2.matchTemplate(screen_gray, template, cv2.TM_CCOEFF_NORMED)
    locations = np.where(result >= threshold)

    matches = []
    h, w = template.shape
    for pt in zip(*locations[::-1]):  # (x, y)
        confidence = result[pt[1], pt[0]]
        center_x = pt[0] + w // 2
        center_y = pt[1] + h // 2
        matches.append((center_x, center_y, float(confidence)))

    # 降序排列，去重叠（NMS）
    matches.sort(key=lambda m: m[2], reverse=True)
    return _non_max_suppression(matches, w, h)


def _ensure_gray(img: np.ndarray) -> np.ndarray:
    """确保图片为灰度图。"""
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize_template(template: np.ndarray, scale: float) -> np.ndarray:
    """按比例缩放模板。"""
    new_w = int(template.shape[1] * scale)
    new_h = int(template.shape[0] * scale)
    return cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_LINEAR)


def _non_max_suppression(
    matches: List[Tuple[int, int, float]],
    template_w: int,
    template_h: int,
    iou_threshold: float = 0.3,
) -> List[Tuple[int, int, float]]:
    """NMS 去重：重叠度高的匹配只保留置信度最高的。"""
    if not matches:
        return []

    boxes = [
        (x - template_w // 2, y - template_h // 2,
         x + template_w // 2, y + template_h // 2, confidence)
        for x, y, confidence in matches
    ]

    keep = []
    while boxes:
        best = boxes.pop(0)
        keep.append(best)
        boxes = [
            b for b in boxes
            if _iou(best, b) < iou_threshold
        ]

    return [
        ((best[0] + best[2]) // 2, (best[1] + best[3]) // 2, best[4])
        for best in keep
    ]


def _iou(a, b) -> float:
    """计算两个边界框的 IoU。"""
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / float(area_a + area_b - inter + 1e-6)
