"""OCR 文字识别模块

使用 pytesseract + tesseract 引擎识别屏幕上的中文文字。
打包 exe 时内置便携版 tesseract + 简体中文语言包。
"""

import logging
import os
import sys

import cv2
import numpy as np
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

_TESSERACT_READY = False


def _find_tesseract() -> str | None:
    """查找 tesseract 可执行文件路径，兼容 PyInstaller 打包场景。"""
    # 1. PyInstaller 打包环境
    if hasattr(sys, "_MEIPASS"):
        candidates = [
            os.path.join(sys._MEIPASS, "tesseract", "tesseract.exe"),
            os.path.join(sys._MEIPASS, "tesseract_portable", "tesseract.exe"),
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

    # 2. 当前目录下的 tesseract_portable（开发测试用）
    local = os.path.join(os.path.dirname(__file__), "..", "tesseract_portable", "tesseract.exe")
    if os.path.isfile(local):
        return os.path.normpath(local)

    # 3. 系统 PATH 中的 tesseract
    return "tesseract"  # 交给系统去找


def _find_tessdata() -> str | None:
    """查找 tessdata 目录（含 chi_sim.traineddata）。"""
    if hasattr(sys, "_MEIPASS"):
        candidates = [
            os.path.join(sys._MEIPASS, "tesseract", "tessdata"),
            os.path.join(sys._MEIPASS, "tesseract_portable", "tessdata"),
        ]
        for d in candidates:
            if os.path.isdir(d) and os.path.exists(os.path.join(d, "chi_sim.traineddata")):
                return d

    local = os.path.join(os.path.dirname(__file__), "..", "tesseract_portable", "tessdata")
    if os.path.isdir(local) and os.path.exists(os.path.join(local, "chi_sim.traineddata")):
        return os.path.normpath(local)

    return None


def init_tesseract() -> bool:
    """初始化 tesseract 引擎，配置 pytesseract 路径。

    Returns:
        是否初始化成功。
    """
    global _TESSERACT_READY
    if _TESSERACT_READY:
        return True

    tesseract_cmd = _find_tesseract()
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    logger.info(f"tesseract 路径: {tesseract_cmd}")

    tessdata = _find_tessdata()
    if tessdata:
        os.environ["TESSDATA_PREFIX"] = tessdata
        logger.info(f"tessdata 路径: {tessdata}")

    try:
        pytesseract.get_tesseract_version()
        lang = "chi_sim"
        langs = pytesseract.get_languages()
        if lang not in langs:
            logger.warning(f"简体中文语言包 ({lang}) 未找到，OCR 中文可能无效")
            logger.info(f"可用语言: {langs}")
        else:
            logger.info(f"OCR 引擎就绪，支持中文")
        _TESSERACT_READY = True
        return True
    except Exception as e:
        logger.warning(f"tesseract 初始化失败: {e}")
        return False


def find_text(
    screen_bgr: np.ndarray,
    targets: list[str],
    region: tuple[int, int, int, int] | None = None,
    lang: str = "chi_sim",
) -> tuple[int, int, str] | None:
    """在截图中查找指定文字的位置。

    Args:
        screen_bgr: BGR 格式截图 (H, W, 3) numpy 数组。
        targets: 要查找的文字列表，如 ["关闭", "×", "跳过"]。
        region: 裁剪区域 (left, top, width, height)，None 为全屏。
        lang: OCR 语言代码。

    Returns:
        (center_x, center_y, 匹配到的文字) 找到则返回，否则 None。
    """
    if not _TESSERACT_READY:
        if not init_tesseract():
            return None

    # BGR → RGB → PIL
    img_rgb = cv2.cvtColor(screen_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(img_rgb)

    region_offset_x = 0
    region_offset_y = 0
    if region is not None:
        left, top, w, h = region
        pil_img = pil_img.crop((left, top, left + w, top + h))
        region_offset_x = left
        region_offset_y = top

    try:
        data = pytesseract.image_to_data(
            pil_img, lang=lang, output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        logger.debug(f"OCR 识别异常: {e}")
        return None

    n_boxes = len(data["text"])
    for i in range(n_boxes):
        text = data["text"][i].strip()
        if not text:
            continue

        for target in targets:
            if target in text:
                x = data["left"][i]
                y = data["top"][i]
                w = data["width"][i]
                h = data["height"][i]
                cx = x + w // 2 + region_offset_x
                cy = y + h // 2 + region_offset_y
                logger.debug(f"OCR 命中 '{target}' (实际文本: '{text}') @ ({cx}, {cy})")
                return (cx, cy, target)

    return None


def find_close_button(
    screen_bgr: np.ndarray,
    region: tuple[int, int, int, int] | None = None,
    lang: str = "chi_sim",
) -> tuple[int, int, str] | None:
    """专门在截图中查找广告关闭按钮文字。"""
    return find_text(
        screen_bgr,
        targets=["关闭", "×", "跳过", "关闭广告"],
        region=region,
        lang=lang,
    )
