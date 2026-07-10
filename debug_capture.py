#!/usr/bin/env python3
"""诊断工具 v6 - 验证 OCR 修复后能否正确识别"""

import ctypes
import sys
import os
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.capture import capture_window
from core import ocr
import cv2


def main():
    user32 = ctypes.windll.user32
    target_hwnd = None

    def callback(hwnd, _lparam):
        nonlocal target_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        if "简幻欢" in buf.value:
            target_hwnd = hwnd
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            print(f"找到窗口: hwnd={hwnd} rect=({rect.left},{rect.top},{rect.right-rect.left},{rect.bottom-rect.top})")
        return True

    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(enum_cb(callback), 0)

    if target_hwnd is None:
        print("未找到简幻欢窗口!")
        return

    print("\n用 PrintWindow 截取...")
    img = capture_window(target_hwnd)
    if img is None:
        print("截图失败!")
        return
    print(f"  尺寸: {img.shape[1]}x{img.shape[0]}")

    out_dir = os.path.dirname(os.path.abspath(__file__))
    cv2.imwrite(os.path.join(out_dir, "debug_window.png"), img)

    print("\n初始化 OCR...")
    if not ocr.init():
        print("OCR 初始化失败!")
        return

    # 用修复后的 find_text 测试
    print("\n=== 测试 find_text ===")
    test_keywords = ["订阅提醒", "好的", "不再提示", "不再提醒", "签到", "观看广告", "关闭", "简幻欢"]
    for kw in test_keywords:
        result = ocr.find_text(img, [kw])
        if result:
            print(f"  ✅ '{kw}' → ({result[0]}, {result[1]})")
        else:
            print(f"  ❌ '{kw}' 未找到")

    print("\n诊断完成!")


if __name__ == "__main__":
    main()
