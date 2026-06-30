"""OCR 文字识别模块

使用 Windows.Media.Ocr API（WinRT），通过 winocr 包调用。
仅支持 Windows 10+，系统内置无需额外安装。
"""

import logging
import sys

logger = logging.getLogger(__name__)

_WINOCR_AVAILABLE = False

try:
    import winocr
    _WINOCR_AVAILABLE = True
except ImportError:
    logger.warning("winocr 未安装，OCR 功能不可用")


def init() -> bool:
    """检查 Windows OCR 是否可用。

    Returns:
        True 表示可用。
    """
    if not _WINOCR_AVAILABLE:
        logger.warning("winocr 未安装，OCR 功能不可用")
        return False

    if sys.platform != "win32":
        logger.warning("Windows OCR 仅支持 Windows")
        return False

    try:
        # 快速验证是否能创建 OCR 引擎
        winocr.recognize_cv2_sync.__doc__  # 只是检查模块是否正常
        logger.info("Windows OCR 引擎就绪")
        return True
    except Exception as e:
        logger.warning(f"Windows OCR 不可用: {e}")
        return False


def find_text(
    screen_bgr,
    targets: list[str],
    region: tuple[int, int, int, int] | None = None,
    lang: str = "zh-Hans-CN",
) -> tuple[int, int, str] | None:
    """在截图中查找指定文字的位置。

    Args:
        screen_bgr: BGR 格式截图 (H, W, 3) numpy 数组。
        targets: 要查找的文字列表，如 ["关闭", "×", "跳过"]。
        region: 裁剪区域 (left, top, width, height)，None 为全屏。
        lang: 语言代码，默认简体中文。

    Returns:
        (center_x, center_y, 匹配到的文字) 或 None。
    """
    if not _WINOCR_AVAILABLE:
        logger.debug("OCR 不可用（winocr 未安装）")
        return None

    if sys.platform != "win32":
        return None

    region_ox = 0
    region_oy = 0

    # 裁剪区域
    img = screen_bgr
    if region is not None:
        left, top, w, h = region
        img = screen_bgr[top : top + h, left : left + w]
        region_ox = left
        region_oy = top

    try:
        result = winocr.recognize_cv2_sync(img, lang)
    except Exception as e:
        logger.debug(f"OCR 识别异常: {e}")
        return None

    # 遍历识别的文字行和单词
    for line in result.get("lines", []):
        for word in line.get("words", []):
            text = word.get("text", "").strip()
            if not text:
                continue

            for target in targets:
                if target in text:
                    rect = word.get("bounding_rect", {})
                    cx = rect.get("x", 0) + rect.get("width", 0) // 2 + region_ox
                    cy = rect.get("y", 0) + rect.get("height", 0) // 2 + region_oy
                    logger.debug(f"OCR 命中 '{target}' (实际: '{text}') @ ({cx}, {cy})")
                    return (cx, cy, target)

    return None


def find_close_button(
    screen_bgr,
    region: tuple[int, int, int, int] | None = None,
    lang: str = "zh-Hans-CN",
) -> tuple[int, int, str] | None:
    """专门在截图中查找广告关闭按钮文字。"""
    return find_text(
        screen_bgr,
        targets=["关闭", "×", "跳过", "关闭广告"],
        region=region,
        lang=lang,
    )
