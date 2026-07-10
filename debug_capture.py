#!/usr/bin/env python3
"""诊断工具 - 查看机器人到底看到了什么

运行后会：
  1. 打印所有标题包含关键词的窗口
  2. 截全屏并保存到 debug_screenshot.png
  3. 运行 OCR 打印所有识别到的文字及坐标
"""

import ctypes
import platform
import sys
import os
from ctypes import wintypes

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.capture import screenshot
from core import ocr


def dump_windows():
    """枚举所有可见窗口，打印标题包含关键词的。"""
    if platform.system() != "Windows":
        print("非 Windows 平台")
        return

    user32 = ctypes.windll.user32
    keywords = ["简幻欢", "WeChatAppEx", "微信", "WeChat"]

    print("=" * 60)
    print("窗口枚举结果:")
    print("=" * 60)

    # 也打印前台窗口
    fg = user32.GetForegroundWindow()
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(fg, buf, 256)
    print(f"  当前前台窗口: hwnd={fg}, title='{buf.value}'")

    found = []

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value

        for kw in keywords:
            if kw.lower() in title.lower():
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                print(f"  hwnd={hwnd} title='{title}' rect=({rect.left},{rect.top},{rect.right-rect.left},{rect.bottom-rect.top})")
                found.append((hwnd, title, rect))
                break
        return True

    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(enum_cb(callback), 0)

    if not found:
        print("  未找到任何匹配窗口!")
    print()


def dump_screenshot():
    """截全屏并保存。"""
    print("=" * 60)
    print("全屏截图:")
    print("=" * 60)

    screen = screenshot()
    h, w = screen.shape[:2]
    print(f"  截图尺寸: {w}x{h}")

    # 保存截图
    import cv2
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_screenshot.png")
    cv2.imwrite(out_path, screen)
    print(f"  已保存到: {out_path}")
    print()


def dump_ocr():
    """OCR 识别全屏所有文字。"""
    print("=" * 60)
    print("OCR 识别结果（全屏）:")
    print("=" * 60)

    screen = screenshot()

    if not ocr.init():
        print("  OCR 引擎初始化失败!")
        return

    try:
        result = ocr._ENGINE(screen)
    except Exception as e:
        print(f"  OCR 识别异常: {e}")
        return

    if result is None:
        print("  OCR 无结果")
        return

    try:
        boxes = result.boxes
        txts = result.txts
    except AttributeError:
        boxes, txts = result[0], result[1]

    if boxes is None or txts is None or len(boxes) == 0:
        print("  OCR 未识别到任何文字")
        return

    print(f"  共识别到 {len(txts)} 条文字:")
    for i, (box, text) in enumerate(zip(boxes, txts)):
        x1, y1 = box[0]
        x3, y3 = box[2]
        cx = int((x1 + x3) / 2)
        cy = int((y1 + y3) / 2)
        print(f"    [{i:2d}] ({cx:4d}, {cy:4d}) '{text}'")

    print()
    print("提示: 如果上方有'观看广告'/'签到'/'关闭'等文字，")
    print("      说明 OCR 能识别到，问题在坐标转换或点击环节。")
    print("      如果没有这些文字，说明小程序界面没有显示，或 OCR 识别有问题。")


if __name__ == "__main__":
    dump_windows()
    dump_screenshot()
    dump_ocr()
    input("\n按 Enter 退出...")
