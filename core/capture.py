"""截图模块 v2 - 支持窗口专属截图

使用 Win32 PrintWindow API 直接截取窗口内容，
即使窗口被其他窗口遮挡也能获取正确图像。

在非 Windows 平台回退到 mss 全屏截图。
"""

from typing import Tuple, Optional
import platform
import numpy as np

# Windows 专用
_HAS_WIN32 = platform.system() == "Windows"
if _HAS_WIN32:
    import ctypes
    from ctypes import wintypes


def _enable_dpi_awareness() -> None:
    """启用 DPI 感知。"""
    if not _HAS_WIN32:
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


_enable_dpi_awareness()


def _setup_argtypes():
    """设置 Win32 API 函数的参数类型，避免 64 位 HWND 溢出。

    ctypes 默认把整数参数当 c_int（32 位），但 64 位 Windows 的 HWND
    是 64 位指针，值可能超过 32 位整数范围导致 OverflowError。
    """
    if not _HAS_WIN32:
        return
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # HWND 参数必须是 wintypes.HWND (c_void_p 在 64 位上是 8 字节)
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL

    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC

    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL

    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC

    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP

    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ

    gdi32.BitBlt.argtypes = [
        wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
    ]
    gdi32.BitBlt.restype = wintypes.BOOL

    # GetDIBits: 不设置 argtypes，让 ctypes 自动转换
    # （第6参数是 LPBITMAPINFO，传 byref(BITMAPINFOHEADER) 时自动转换最可靠）
    gdi32.GetDIBits.restype = ctypes.c_int

    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL


_setup_argtypes()


def capture_window(hwnd: int) -> Optional[np.ndarray]:
    """使用 PrintWindow 截取指定窗口的内容。

    即使窗口被遮挡也能获取正确图像。

    Args:
        hwnd: 窗口句柄

    Returns:
        BGR numpy 数组，或 None（失败时）
    """
    if not _HAS_WIN32 or not hwnd:
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # 获取窗口尺寸
    rect = wintypes.RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return None

    width = rect.right - rect.left
    height = rect.bottom - rect.top

    if width <= 0 or height <= 0:
        return None

    # 创建设备上下文
    hwnd_dc = user32.GetWindowDC(wintypes.HWND(hwnd))
    if not hwnd_dc:
        return None

    mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    gdi32.SelectObject(mfc_dc, bitmap)

    # PrintWindow: PW_RENDERFULLCONTENT = 2 (支持渲染完整内容)
    result = user32.PrintWindow(wintypes.HWND(hwnd), mfc_dc, 2)

    if result != 1:
        # 回退: PW_CLIENTONLY = 0
        result = user32.PrintWindow(wintypes.HWND(hwnd), mfc_dc, 0)

    if result != 1:
        # PrintWindow 失败，回退到 BitBlt
        gdi32.BitBlt(mfc_dc, 0, 0, width, height, hwnd_dc, 0, 0, 0x00CC0020)  # SRCCOPY

    # 读取位图数据
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.UINT),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.UINT),
            ("biSizeImage", wintypes.UINT),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.UINT),
            ("biClrImportant", wintypes.UINT),
        ]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height  # 负值 = top-down
    bi.biPlanes = 1
    bi.biBitCount = 32
    bi.biCompression = 0  # BI_RGB

    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mfc_dc, bitmap, 0, height, buf, ctypes.byref(bi), 0)

    # 清理资源
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mfc_dc)
    user32.ReleaseDC(wintypes.HWND(hwnd), hwnd_dc)

    # 转换为 numpy 数组
    arr = np.frombuffer(buf.raw, dtype=np.uint8).reshape((height, width, 4))
    return arr[:, :, :3]  # BGRA → BGR


def screenshot(region: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
    """截取屏幕指定区域，返回 BGR 格式的 numpy 数组。

    Args:
        region: (left, top, width, height)，None 表示全屏。

    Returns:
        shape (H, W, 3) 的 BGR numpy 数组。
    """
    import mss

    with mss.mss() as sct:
        if region is not None:
            left, top, width, height = region
            monitor = {"top": top, "left": left, "width": width, "height": height}
        else:
            monitor = sct.monitors[1]

        img = sct.grab(monitor)
        arr = np.array(img)
        return arr[:, :, :3]  # BGR


def screenshot_gray(region: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
    """截取屏幕并转为灰度图。"""
    bgr = screenshot(region)
    import cv2
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
