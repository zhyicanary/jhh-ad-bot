#!/usr/bin/env python3
"""快速测试 OCR 返回结构"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import ocr
import cv2

# 加载截图
img = cv2.imread(os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_window.png"))
print(f"图片尺寸: {img.shape}")

if not ocr.init():
    print("OCR 初始化失败")
    sys.exit(1)

result = ocr._ENGINE(img)

print(f"\n返回类型: {type(result)}")
print(f"返回长度: {len(result) if result else 0}")

if result:
    for i, item in enumerate(result):
        print(f"\n--- result[{i}] ---")
        print(f"  类型: {type(item)}")
        if isinstance(item, list):
            print(f"  长度: {len(item)}")
            if len(item) > 0:
                print(f"  [0] 类型: {type(item[0])}")
                print(f"  [0] 内容: {item[0]}")
                if len(item) > 1:
                    print(f"  [1] 类型: {type(item[1])}")
                    print(f"  [1] 内容: {item[1]}")
        else:
            print(f"  内容: {item}")
