"""OCR 文字识别模块

使用 RapidOCR（基于 ONNX Runtime），离线运行，准确率高。
模型文件自动内置于 rapidocr_onnxruntime 包中，无需额外下载。
"""

import logging
import sys

logger = logging.getLogger(__name__)

_RAPIDOCR_AVAILABLE = False
_ENGINE = None


def init() -> bool:
    """初始化 RapidOCR 引擎。

    Returns:
        True 表示可用。
    """
    global _RAPIDOCR_AVAILABLE, _ENGINE
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
        _RAPIDOCR_AVAILABLE = True
        logger.info("RapidOCR 引擎就绪")
        return True
    except ImportError:
        logger.warning("rapidocr_onnxruntime 未安装，OCR 不可用")
    except Exception as e:
        logger.warning(f"RapidOCR 初始化失败: {e}")
    return False


def _parse_result(result):
    """解析 RapidOCR 返回值，统一为 (boxes, txts) 格式。

    RapidOCR 不同版本返回格式不同：
    - namedtuple: result.boxes / result.txts / result.scores
    - tuple: result[0] = [[box, text, score], ...], result[1] = [elapse, ...]
    """
    if result is None:
        return [], []

    # 方式1: namedtuple 属性访问
    try:
        boxes = result.boxes
        txts = result.txts
        if boxes is not None and txts is not None:
            return boxes, txts
    except AttributeError:
        pass

    # 方式2: tuple 格式
    # result[0] = [[[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], 'text', score], ...]
    # result[1] = [elapse_total, elapse_det, ...]
    if isinstance(result, tuple) and len(result) >= 1:
        raw_list = result[0]
        if raw_list is None:
            return [], []

        boxes = []
        txts = []
        for item in raw_list:
            # item = [[[x1,y1],...], 'text', score]
            if len(item) >= 2:
                boxes.append(item[0])  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                txts.append(item[1])   # 'text'
        return boxes, txts

    return [], []


def find_text(
    screen_bgr,
    targets: list[str],
    region: tuple[int, int, int, int] | None = None,
    lang: str = "ch",
) -> tuple[int, int, str] | None:
    """在截图中查找指定文字的位置。

    Args:
        screen_bgr: BGR 格式截图 (H, W, 3) numpy 数组。
        targets: 要查找的文字列表，如 ["关闭", "×", "跳过"]。
        region: 裁剪区域 (left, top, width, height)，None 为全屏。
        lang: 语言，RapidOCR 默认支持中英文混合，忽略此参数。

    Returns:
        (center_x, center_y, 匹配到的文字) 或 None。
    """
    global _ENGINE
    if not _RAPIDOCR_AVAILABLE or _ENGINE is None:
        logger.debug("RapidOCR 不可用")
        return None

    _ = lang

    region_ox = 0
    region_oy = 0

    img = screen_bgr
    if region is not None:
        left, top, w, h = region
        img = screen_bgr[top : top + h, left : left + w]
        region_ox = left
        region_oy = top

    try:
        result = _ENGINE(img)
    except Exception as e:
        logger.debug(f"RapidOCR 识别异常: {e}")
        return None

    boxes, txts = _parse_result(result)

    if not boxes or not txts:
        return None

    # 第一轮：精确匹配（优先）
    for box, text in zip(boxes, txts):
        if not text or not isinstance(text, str):
            continue
        for target in targets:
            if text.strip() == target:
                x1, y1 = box[0]
                x3, y3 = box[2]
                cx = int((x1 + x3) / 2) + region_ox
                cy = int((y1 + y3) / 2) + region_oy
                logger.info(f"OCR 命中 '{target}' (精确: '{text}') @ ({cx}, {cy})")
                return (cx, cy, target)

    # 第二轮：子串匹配（兜底）
    for box, text in zip(boxes, txts):
        if not text or not isinstance(text, str):
            continue
        for target in targets:
            if target in text:
                x1, y1 = box[0]
                x3, y3 = box[2]
                cx = int((x1 + x3) / 2) + region_ox
                cy = int((y1 + y3) / 2) + region_oy
                logger.info(f"OCR 命中 '{target}' (子串: '{text}') @ ({cx}, {cy})")
                return (cx, cy, target)

    return None


def find_close_button(
    screen_bgr,
    region: tuple[int, int, int, int] | None = None,
) -> tuple[int, int, str] | None:
    """专门在截图中查找广告关闭按钮文字。"""
    return find_text(
        screen_bgr,
        targets=["关闭", "×", "跳过", "关闭广告"],
        region=region,
    )
