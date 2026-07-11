"""状态机引擎 - 简幻欢自动化核心

核心设计：
  - 截图检测：使用 PrintWindow API 截取窗口内容，即使被遮挡也能获取正确图像
  - 点击操作：三层点击策略
    1. UIA 名称搜索 + InvokePattern — 对有 accessible name 的元素（最可靠）
    2. OCR 定位 + ControlFromPoint + InvokePattern — 对无 accessible name 的元素
    3. OCR 坐标点击 — 最后兜底（对 Chromium 窗口可能无效）
  - 状态检测：使用 OCR 识别界面文字，判断当前状态
  - 全 OCR 识别，不依赖模板图片
"""

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple

# 抑制 uiautomation 的 DEBUG 日志（COM 对象 Release 日志太多）
logging.getLogger("uiautomation").setLevel(logging.WARNING)

from . import ocr, uia
from .action import click, focus_window, is_window_in_focus
from .action import wait as action_wait
from .capture import capture_window, screenshot

logger = logging.getLogger(__name__)

_HAS_WIN32 = hasattr(ctypes, "windll")

# 设置 Win32 API 参数类型，避免 64 位 HWND 溢出
if _HAS_WIN32:
    _user32 = ctypes.windll.user32
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetForegroundWindow.argtypes = []
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL


class State(Enum):
    OPEN_WECHAT = auto()
    OPEN_MINI_PROGRAM = auto()
    INIT = auto()
    DISMISS_SUBSCRIBE = auto()
    CHECK_IN = auto()
    CLICK_AD = auto()
    DISMISS_INTERSTITIAL = auto()
    WATCHING_AD = auto()
    CLOSE_AD = auto()
    WAITING_REWARD = auto()
    STOP = auto()


@dataclass
class Stats:
    rounds: int = 0
    ad_watched: int = 0
    ad_skipped: int = 0
    start_time: float = 0.0

    def elapsed(self) -> float:
        return time.time() - self.start_time


class AdBotEngine:
    """简幻欢广告机器人主引擎。"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.state = State.OPEN_WECHAT
        self.stats = Stats()
        self._stop_requested = False
        self._watch_start: float = 0.0
        self._ad_not_found_count: int = 0
        self._target_hwnd: int | None = None
        self._wechat_hwnd: int | None = None  # 微信主窗口 hwnd，跨状态复用
        self._win_rect: Tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, w, h

        win_cfg = config.get("window", {})
        self._win_keyword = win_cfg.get("title_keyword", "简幻欢|WeChatAppEx|微信")
        self._auto_focus = win_cfg.get("auto_focus", True)

        t = config.get("timing", {})
        self._t_init = t.get("init_wait", 3)
        self._t_ad_click = t.get("ad_click_wait", 2)
        self._t_ad_watch = t.get("ad_watch_seconds", 32)
        self._t_check_interval = t.get("check_interval", 2)
        self._t_close_wait = t.get("close_ad_wait", 2)

        m = config.get("matching", {})
        self._kw_subscribe = m.get("subscribe_keywords", ["订阅提醒", "好的", "不再提示", "不再提醒"])
        self._kw_checkin = m.get("checkin_keywords", ["签到"])
        self._kw_ad = m.get("ad_keywords", ["观看广告", "看广告"])
        self._kw_close = m.get("close_keywords", ["关闭", "关闭广告"])
        self._kw_interrupt = m.get("interrupt_keywords", ["暂未获得奖励", "继续", "放弃"])
        self._kw_continue = m.get("continue_keywords", ["继续"])
        self._kw_reward = m.get("reward_keywords", ["获得观看积分", "积分成功", "观看积分"])
        self._kw_loading = m.get("loading_keywords", ["加载中"])
        self._kw_limit = m.get("limit_keywords", ["次数已用完", "已达上限", "已用完", "今日上限"])
        self._max_ad_not_found = m.get("max_ad_not_found", 5)

        loop_cfg = config.get("loop", {})
        self._max_rounds = loop_cfg.get("max_rounds", 0)

        if uia.is_available():
            logger.info("UI Automation 可用，将优先使用 InvokePattern 点击")
        else:
            logger.warning("UI Automation 不可用，将使用坐标点击（可能对小程序无效）")

    # ──────────────────────────────────────────────
    # 主循环
    # ──────────────────────────────────────────────

    def run(self) -> Stats:
        logger.info("===== 简幻欢广告机器人启动 =====")
        self.stats.start_time = time.time()

        if not ocr.init():
            logger.error("OCR 引擎初始化失败")
            self.state = State.STOP
            return self.stats

        while not self._stop_requested and self.state != State.STOP:
            if self._max_rounds > 0 and self.stats.rounds >= self._max_rounds:
                logger.info("已达最大循环次数，停止。")
                break
            self._tick()

        logger.info("===== 停止 =====")
        logger.info(
            f"统计: 共 {self.stats.rounds} 轮, "
            f"观看广告 {self.stats.ad_watched} 次, "
            f"跳过 {self.stats.ad_skipped} 次, "
            f"耗时 {self.stats.elapsed():.1f}s"
        )
        return self.stats

    def stop(self) -> None:
        self._stop_requested = True

    def _tick(self) -> None:
        handlers = {
            State.OPEN_WECHAT: self._handle_open_wechat,
            State.OPEN_MINI_PROGRAM: self._handle_open_mini_program,
            State.INIT: self._handle_init,
            State.DISMISS_SUBSCRIBE: self._handle_dismiss_subscribe,
            State.CHECK_IN: self._handle_check_in,
            State.CLICK_AD: self._handle_click_ad,
            State.DISMISS_INTERSTITIAL: self._handle_dismiss_interstitial,
            State.WATCHING_AD: self._handle_watching_ad,
            State.CLOSE_AD: self._handle_close_ad,
            State.WAITING_REWARD: self._handle_waiting_reward,
        }
        handler = handlers.get(self.state)
        if handler:
            handler()

    # ──────────────────────────────────────────────
    # 窗口管理 + 截图 + 坐标转换（核心）
    # ──────────────────────────────────────────────

    def _find_target_hwnd(self) -> int | None:
        """枚举窗口找到简幻欢的 hwnd。"""
        if not _HAS_WIN32:
            return None

        user32 = ctypes.windll.user32
        keywords = [k.strip() for k in self._win_keyword.split("|")]
        found = None

        def callback(hwnd, _lparam):
            nonlocal found
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
                    if found is None or "简幻欢" in title:
                        found = hwnd
                    break
            return True

        enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(enum_cb(callback), 0)

        # 回退: 前台窗口
        if found is None:
            fg = user32.GetForegroundWindow()
            if fg:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(fg, buf, 256)
                for kw in keywords:
                    if kw.lower() in buf.value.lower():
                        found = fg
                        break

        return found

    def _find_wechat_window(self) -> int | None:
        """枚举窗口找到微信的 hwnd。

        支持新版微信 Weixin、旧版 WeChat、中文微信。
        """
        if not _HAS_WIN32:
            return None

        user32 = ctypes.windll.user32
        wechat_keywords = ["微信", "WeChat", "Weixin"]
        found = []

        def callback(hwnd, _lparam):
            # 不检查 IsWindowVisible — 微信最小化到托盘时窗口隐藏但仍然存在
            length = user32.GetWindowTextLengthW(hwnd) + 1
            if length <= 1:
                return True
            buf = ctypes.create_unicode_buffer(length)
            user32.GetWindowTextW(hwnd, buf, length)
            title = buf.value
            for kw in wechat_keywords:
                if kw in title:
                    found.append(hwnd)
                    break
            return True

        enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(enum_cb(callback), 0)
        return found[0] if found else None

    def _set_clipboard_text(self, text: str) -> None:
        """用 Win32 API 直接设置剪贴板文本（避免 clip 命令编码问题）。"""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # 显式声明参数和返回值类型，避免 OverflowError
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

        user32.OpenClipboard(0)
        user32.EmptyClipboard()

        # CF_UNICODETEXT = 13
        data = text + "\0"
        data_bytes = data.encode("utf-16-le")
        h_global = kernel32.GlobalAlloc(0x0042, len(data_bytes))  # GMEM_MOVEABLE | GMEM_ZEROINIT
        if not h_global:
            user32.CloseClipboard()
            return
        locked = kernel32.GlobalLock(h_global)
        if not locked:
            user32.CloseClipboard()
            return
        ctypes.memmove(locked, data_bytes, len(data_bytes))
        kernel32.GlobalUnlock(h_global)
        user32.SetClipboardData(13, h_global)  # CF_UNICODETEXT
        user32.CloseClipboard()

    def _update_win_rect(self) -> None:
        """更新目标窗口的屏幕坐标。"""
        if not _HAS_WIN32 or self._target_hwnd is None:
            return
        try:
            user32 = ctypes.windll.user32
            rect = wintypes.RECT()
            user32.GetWindowRect(wintypes.HWND(self._target_hwnd), ctypes.byref(rect))
            self._win_rect = (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        except Exception as e:
            logger.debug(f"GetWindowRect 失败: {e}")

    def _ensure_window(self) -> bool:
        """确保有有效的目标窗口。窗口关闭时自动重新查找。"""
        user32 = ctypes.windll.user32
        # 检查现有 hwnd 是否仍然有效
        if self._target_hwnd is not None:
            if not user32.IsWindow(wintypes.HWND(self._target_hwnd)):
                logger.warning("  目标窗口已关闭，尝试重新查找...")
                self._target_hwnd = None
        # 查找窗口
        if self._target_hwnd is None:
            self._target_hwnd = self._find_target_hwnd()
        if self._target_hwnd is None:
            return False
        self._update_win_rect()
        return True

    def _capture(self):
        """截取目标窗口内容（PrintWindow），即使被遮挡也能截到。"""
        # 确保有有效 hwnd
        if not self._ensure_window():
            logger.warning("  无法截图：目标窗口不存在")
            return None

        img = capture_window(self._target_hwnd)
        if img is not None:
            return img

        logger.warning("PrintWindow 失败，回退全屏截图")
        self._win_rect = (0, 0, 0, 0)
        return screenshot()

    def _click_win(self, x: int, y: int, clicks: int = 1, wait_after: float = 0) -> None:
        """点击窗口相对坐标（OCR 坐标 + 窗口偏移 = 屏幕绝对坐标）。"""
        ox, oy = self._win_rect[0], self._win_rect[1]
        screen_x = ox + x
        screen_y = oy + y
        logger.info(f"  坐标点击: win({x},{y}) -> screen({screen_x},{screen_y})")
        click(screen_x, screen_y, clicks=clicks)
        if wait_after > 0:
            action_wait(wait_after)

    def _uia_click(self, keywords: list[str], wait_after: float = 1.0, exact: bool = False) -> Optional[str]:
        """使用 UI Automation 查找并点击元素。

        Args:
            keywords: 关键词列表
            wait_after: 点击后等待时间
            exact: True=精确匹配名称（用于标签导航），False=子串匹配

        Returns:
            匹配到的关键词，或 None
        """
        if self._target_hwnd is None:
            self._target_hwnd = self._find_target_hwnd()

        if self._target_hwnd is None:
            return None

        for kw in keywords:
            if uia.find_and_invoke(self._target_hwnd, kw, exact=exact):
                logger.info(f"  UIA 名称搜索命中: '{kw}'" + (" (精确匹配)" if exact else ""))
                if wait_after > 0:
                    action_wait(wait_after)
                return kw

        return None

    def _ensure_focus(self) -> None:
        """确保窗口在前台。"""
        if not self._auto_focus:
            return

        # 确保有 hwnd
        if self._target_hwnd is None:
            self._target_hwnd = self._find_target_hwnd()

        if self._target_hwnd and is_window_in_focus(self._win_keyword):
            return

        logger.info("窗口不在前台，尝试激活...")
        for attempt in range(5):
            focus_window(self._win_keyword)
            action_wait(1.5)
            if is_window_in_focus(self._win_keyword):
                logger.info("窗口已回到前台")
                self._target_hwnd = self._find_target_hwnd()
                return

        logger.warning("窗口激活超时，继续执行（UIA 可在后台操作）")

    # ──────────────────────────────────────────────
    # OCR + UIA 混合辅助
    # ──────────────────────────────────────────────

    def _find_text(self, keywords: list[str], region=None) -> Optional[Tuple[int, int, str]]:
        """截图 + OCR 查找文字，返回窗口相对坐标。"""
        screen = self._capture()
        if screen is None:
            return None
        result = ocr.find_text(screen, keywords, region=region)
        if result is None:
            logger.info(f"  OCR 未匹配: {keywords}")
        else:
            logger.info(f"  OCR 命中: '{result[2]}' @ win({result[0]}, {result[1]})")
        return result

    def _has_text(self, keywords: list[str]) -> bool:
        """检查界面上是否存在指定文字（OCR）。"""
        return self._find_text(keywords) is not None

    def _has_uia(self, keywords: list[str]) -> bool:
        """检查 UIA 树中是否存在指定元素。"""
        if self._target_hwnd is None:
            self._target_hwnd = self._find_target_hwnd()
        if self._target_hwnd is None:
            return False
        for kw in keywords:
            if uia.exists(self._target_hwnd, kw):
                return True
        return False

    def _ocr_and_invoke(
        self, keywords: list[str], region=None, wait_after: float = 1.0
    ) -> Optional[Tuple[int, int, str]]:
        """OCR 定位文字 -> ControlFromPoint 获取 UIA 元素 -> InvokePattern 调用。

        用于处理没有 accessible name 的元素：先用 OCR 找到文字位置，
        再用 UIA ControlFromPoint 获取该位置的元素并向上查找可调用的祖先。

        Returns:
            (x, y, text) 成功时返回，None 表示失败
        """
        result = self._find_text(keywords, region=region)
        if result is None:
            return None

        x, y, text = result

        # 转换为屏幕绝对坐标
        ox, oy = self._win_rect[0], self._win_rect[1]
        screen_x = ox + x
        screen_y = oy + y

        # 用 ControlFromPoint 获取元素并调用
        if uia.invoke_at_point(screen_x, screen_y):
            logger.info(f"  UIA Point 调用成功: '{text}' @ screen({screen_x},{screen_y})")
            if wait_after > 0:
                action_wait(wait_after)
            return (x, y, text)

        return None

    def _find_and_click(
        self, keywords: list[str], region=None, wait_after: float = 1.0, exact: bool = False
    ) -> Optional[Tuple[int, int, str]]:
        """查找文字并点击 — 三层策略。

        策略1: UIA 名称搜索 + InvokePattern/DoDefaultAction（最可靠）
        策略2: OCR 定位 + ControlFromPoint + InvokePattern
        策略3: OCR 坐标点击（兜底，用 SendInput 模拟鼠标）

        Args:
            exact: True=UIA精确匹配名称，False=子串匹配
        """
        # 策略1: UIA 名称搜索
        uia_result = self._uia_click(keywords, wait_after=wait_after, exact=exact)
        if uia_result is not None:
            return (0, 0, uia_result)

        # 策略2: OCR + ControlFromPoint
        logger.info(f"  UIA 名称搜索未命中，尝试 OCR + ControlFromPoint: {keywords}")
        point_result = self._ocr_and_invoke(keywords, region=region, wait_after=wait_after)
        if point_result is not None:
            return point_result

        # 策略3: OCR 坐标点击（兜底）
        logger.info(f"  ControlFromPoint 未命中，尝试坐标点击: {keywords}")
        result = self._find_text(keywords, region=region)
        if result is None:
            logger.info(f"  OCR 也未找到: {keywords}")
            return None
        x, y, text = result
        self._ensure_focus()
        self._click_win(x, y, clicks=1, wait_after=wait_after)
        return (x, y, text)

    def _navigate_to_tab(self, keywords: list[str], wait_after: float = 2.0) -> Optional[str]:
        """导航到指定标签页。

        先尝试精确匹配（避免误点），失败后用子串匹配扩大搜索范围。
        只匹配可交互元素（ButtonControl 等），TextControl 会被自动过滤。
        """
        logger.info(f"  尝试导航到标签: {keywords}")
        # 先精确匹配
        result = self._uia_click(keywords, wait_after=wait_after, exact=True)
        if result is not None:
            return result
        # 精确匹配失败，尝试子串匹配
        logger.info(f"  精确匹配未命中，尝试子串匹配: {keywords}")
        return self._uia_click(keywords, wait_after=wait_after, exact=False)

    def _wait_for_text(
        self, keywords: list[str], timeout: float = 10, interval: float = 1.0, region=None
    ) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._find_text(keywords, region=region) is not None:
                return True
            action_wait(interval)
        return False

    # ──────────────────────────────────────────────
    # 状态处理
    # ──────────────────────────────────────────────

    def _handle_open_wechat(self) -> None:
        """打开微信客户端。

        1. 检查微信是否已在运行（枚举窗口找"微信"/"WeChat"）
        2. 如果在运行，激活窗口
        3. 如果没运行，从常见路径和注册表查找 WeChat.exe 并启动
        4. 等待微信窗口出现
        """
        logger.info("[打开微信] 检查微信是否在运行...")

        if not _HAS_WIN32:
            logger.error("  非 Windows 平台，跳过")
            self.state = State.INIT
            return

        user32 = ctypes.windll.user32
        wechat_hwnd = self._find_wechat_window()

        if wechat_hwnd:
            logger.info(f"  微信已在运行 (hwnd={wechat_hwnd})，激活窗口")
            self._wechat_hwnd = wechat_hwnd
            # SW_RESTORE = 9，从最小化/托盘恢复窗口
            user32.ShowWindow(wintypes.HWND(wechat_hwnd), 9)
            action_wait(0.5)
            user32.SetForegroundWindow(wintypes.HWND(wechat_hwnd))
            action_wait(3.0)
            self.state = State.OPEN_MINI_PROGRAM
            return

        # 微信没运行，启动它
        logger.info("  微信未运行，尝试启动...")
        import os
        import subprocess

        # 1. 优先使用配置文件中指定的路径
        wechat_exe = None
        cfg_path = self.config.get("wechat", {}).get("path", "")
        if cfg_path and os.path.exists(cfg_path):
            wechat_exe = cfg_path
            logger.info(f"  使用配置的微信路径: {wechat_exe}")

        # 2. 搜索常见路径
        if wechat_exe is None:
            wechat_paths = [
                os.path.expandvars(r"%PROGRAMFILES%\Tencent\WeChat\WeChat.exe"),
                os.path.expandvars(r"%PROGRAMFILES(X86)%\Tencent\WeChat\WeChat.exe"),
                os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\WeChat.exe"),
                os.path.expandvars(r"%APPDATA%\Tencent\WeChat\WeChat.exe"),
            ]
            for path in wechat_paths:
                if os.path.exists(path):
                    wechat_exe = path
                    break

        # 3. 从注册表查找
        if wechat_exe is None:
            try:
                import winreg
                for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
                    for subkey in [r"SOFTWARE\Tencent\WeChat", r"SOFTWARE\WOW6432Node\Tencent\WeChat"]:
                        try:
                            key = winreg.OpenKey(hive, subkey)
                            install_path, _ = winreg.QueryValueEx(key, "InstallPath")
                            winreg.CloseKey(key)
                            exe_path = os.path.join(install_path, "WeChat.exe")
                            if os.path.exists(exe_path):
                                wechat_exe = exe_path
                                break
                        except (FileNotFoundError, OSError):
                            pass
                    if wechat_exe:
                        break
            except ImportError:
                pass

        # 4. 全盘搜索 WeChat.exe（扫描所有盘符的 Tencent 目录）
        if wechat_exe is None:
            logger.info("  常见路径未找到，全盘搜索 WeChat.exe...")
            for drive in ["C", "D", "E", "F", "G"]:
                drive_root = f"{drive}:\\"
                if not os.path.exists(drive_root):
                    continue
                # 常见安装目录
                search_dirs = [
                    os.path.join(drive_root, "Tencent", "WeChat", "WeChat.exe"),
                    os.path.join(drive_root, "Program Files", "Tencent", "WeChat", "WeChat.exe"),
                    os.path.join(drive_root, "Program Files (x86)", "Tencent", "WeChat", "WeChat.exe"),
                    os.path.join(drive_root, "WeChat", "WeChat.exe"),
                ]
                for path in search_dirs:
                    if os.path.exists(path):
                        wechat_exe = path
                        break
                if wechat_exe:
                    break
                # 深度搜索：遍历盘符下的 Tencent 文件夹
                tencent_dir = os.path.join(drive_root, "Tencent")
                if os.path.isdir(tencent_dir):
                    for root, dirs, files in os.walk(tencent_dir):
                        if "WeChat.exe" in files:
                            wechat_exe = os.path.join(root, "WeChat.exe")
                            break
                        # 只搜一层
                        if root.count(os.sep) > 4:
                            dirs.clear()
                    if wechat_exe:
                        break

        if wechat_exe:
            logger.info(f"  启动微信: {wechat_exe}")
            subprocess.Popen([wechat_exe])
            # 等待微信窗口出现（最多 30 秒）
            for i in range(60):
                action_wait(0.5)
                wechat_hwnd = self._find_wechat_window()
                if wechat_hwnd:
                    logger.info(f"  微信窗口已出现 (hwnd={wechat_hwnd})")
                    self._wechat_hwnd = wechat_hwnd
                    # 显示并激活窗口
                    user32.ShowWindow(wintypes.HWND(wechat_hwnd), 9)  # SW_RESTORE
                    user32.SetForegroundWindow(wintypes.HWND(wechat_hwnd))
                    action_wait(2.0)
                    # 尝试点"进入微信"按钮
                    self._click_enter_wechat(wechat_hwnd)
                    # 等待微信主界面加载（检测"进入微信"按钮消失）
                    logger.info("  等待微信主界面加载...")
                    for wait_i in range(10):
                        action_wait(1.0)
                        # 重新截图检测，如果"进入微信"还在说明还在登录页
                        self._target_hwnd = wechat_hwnd
                        self._update_win_rect()
                        if self._has_text(["进入微信", "Enter Weixin"]):
                            logger.info(f"  仍在登录页面，继续等待... ({wait_i+1}/10)")
                            continue
                        else:
                            logger.info("  微信主界面已加载")
                            break
                    self.state = State.OPEN_MINI_PROGRAM
                    return
            logger.error("  微信启动超时（30秒未出现窗口）")
            self.state = State.OPEN_MINI_PROGRAM
        else:
            logger.error("  未找到微信安装路径！")
            logger.error("  请在托盘右键菜单'配置'中设置微信路径")
            logger.error("  或手动打开微信后重新运行程序")
            self.state = State.STOP

    def _click_enter_wechat(self, wechat_hwnd: int) -> None:
        """点击微信登录页面的"进入微信"按钮。

        微信是原生窗口，UIA Invoke 返回成功但实际无效，
        直接用 OCR 找按钮位置然后 SendInput 坐标点击。
        跳过 _find_and_click 的 UIA 策略，避免浪费时间。
        """
        logger.info("  查找'进入微信'按钮...")
        self._target_hwnd = wechat_hwnd
        self._update_win_rect()

        keywords = ["进入微信", "Enter Weixin", "登录", "Login"]
        # 直接 OCR 定位 + 坐标点击（不走 UIA，微信原生窗口 UIA 无效）
        result = self._find_text(keywords)
        if result:
            x, y, text = result
            self._ensure_focus()
            self._click_win(x, y, clicks=1, wait_after=1.0)
            logger.info(f"  已点击'{text}'")
        else:
            logger.warning("  未找到'进入微信'按钮，可能已自动登录")

    def _handle_open_mini_program(self) -> None:
        """通过微信搜索框打开简幻欢小程序。

        流程：
        1. 找到微信主窗口并激活（复用 _handle_open_wechat 找到的 hwnd）
        2. 用 Ctrl+F 打开搜索框
        3. 输入"简幻欢"
        4. 等待搜索结果
        5. 点击搜索结果中的小程序
        6. 等待小程序窗口出现
        """
        logger.info("[打开小程序] 通过搜索打开简幻欢小程序...")

        if not _HAS_WIN32:
            self.state = State.INIT
            return

        user32 = ctypes.windll.user32

        # 1. 复用之前找到的微信窗口，避免 _find_wechat_window 因 IsWindowVisible 返回错误窗口
        wechat_hwnd = self._wechat_hwnd
        if wechat_hwnd is None or not user32.IsWindow(wintypes.HWND(wechat_hwnd)):
            logger.info("  窗口句柄无效，重新搜索微信窗口...")
            for i in range(5):
                wechat_hwnd = self._find_wechat_window()
                if wechat_hwnd:
                    break
                logger.info(f"  等待微信窗口出现... ({i+1}/5)")
                action_wait(2.0)
            if not wechat_hwnd:
                logger.error("  微信窗口未找到，跳过")
                self.state = State.INIT
                return
            self._wechat_hwnd = wechat_hwnd

        # 2. 只在窗口不在前台时才激活（避免二次激活导致焦点错乱）
        if not is_window_in_focus("微信|WeChat|Weixin"):
            logger.info(f"  激活微信窗口 (hwnd={wechat_hwnd})")
            user32.ShowWindow(wintypes.HWND(wechat_hwnd), 9)  # SW_RESTORE
            user32.SetForegroundWindow(wintypes.HWND(wechat_hwnd))
            action_wait(2.0)
        else:
            logger.info(f"  微信窗口已在前台 (hwnd={wechat_hwnd})")

        # 3. 用 OCR 找搜索框并点击（新版微信 Ctrl+F 不是搜索快捷键）
        logger.info("  查找搜索框...")
        self._target_hwnd = wechat_hwnd
        self._update_win_rect()

        import pyautogui
        search_keywords = ["搜索", "Search", "查找"]
        search_result = self._find_text(search_keywords)
        if search_result:
            x, y, text = search_result
            logger.info(f"  找到搜索框 '{text}' @ ({x},{y})，点击")
            self._ensure_focus()
            self._click_win(x, y, clicks=1, wait_after=2.0)
        else:
            # 兜底：点击微信窗口顶部中间区域（搜索框通常在那里）
            logger.warning("  OCR 未找到搜索框，尝试点击顶部区域")
            self._ensure_focus()
            # 微信窗口顶部偏中间位置
            rect = self._win_rect
            if rect:
                cx = rect[0] + (rect[2] - rect[0]) // 2
                cy = rect[1] + 40  # 顶部偏下 40 像素
                self._click_win(cx, cy, clicks=1, wait_after=2.0)
            else:
                logger.error("  无法获取窗口位置")
                self.state = State.INIT
                return

        # 4. 输入"简幻欢"（用 ctypes 直接设置剪贴板）
        logger.info("  输入'简幻欢'...")
        self._set_clipboard_text("简幻欢")
        pyautogui.hotkey("ctrl", "v")
        action_wait(2.0)

        # 5. 等待搜索结果，按回车搜索
        logger.info("  按回车搜索...")
        pyautogui.press("enter")
        action_wait(3.0)

        # 6. 查找搜索结果中的"简幻欢"并点击
        logger.info("  查找搜索结果中的小程序...")
        self._target_hwnd = wechat_hwnd
        self._update_win_rect()

        # 微信是原生窗口，UIA Invoke 返回成功但实际无效。
        # 直接用 OCR 找"简幻欢"并坐标点击。
        # OCR 精确匹配优先（先找 text=="简幻欢" 的，而非"简幻欢小程序"等）
        result = self._find_text(["简幻欢"])
        if result:
            x, y, text = result
            logger.info(f"  OCR 找到'{text}' @ ({x},{y})，点击")
            self._ensure_focus()
            self._click_win(x, y, clicks=1, wait_after=5.0)
            logger.info("  已点击搜索结果中的'简幻欢'")
            self.state = State.INIT
            return

        logger.warning("  搜索结果中未找到'简幻欢'，尝试回退...")
        # 按Esc关闭搜索
        pyautogui.press("escape")
        action_wait(1.0)
        self.state = State.INIT

    def _handle_init(self) -> None:
        logger.info("[INIT] 激活窗口...")
        self._target_hwnd = self._find_target_hwnd()
        if self._target_hwnd:
            # 先检查窗口是否最小化（-32000 表示最小化）
            self._update_win_rect()
            if self._win_rect[0] <= -32000:
                logger.info("  窗口已最小化，先还原...")
                ctypes.windll.user32.ShowWindow(wintypes.HWND(self._target_hwnd), 9)  # SW_RESTORE
                action_wait(1.0)
            # 激活窗口后再更新 rect（激活前坐标可能不正确）
            self._ensure_focus()
            self._update_win_rect()
            logger.info(f"  目标窗口: hwnd={self._target_hwnd} rect={self._win_rect}")
            action_wait(self._t_init)
            self.state = State.DISMISS_SUBSCRIBE
        else:
            logger.error("  未找到目标窗口！请确保简幻欢小程序已打开。")
            logger.error("  等待 10 秒后重试...")
            action_wait(10)
            self._target_hwnd = self._find_target_hwnd()
            if self._target_hwnd:
                self._update_win_rect()
                self._ensure_focus()
                action_wait(self._t_init)
                self.state = State.DISMISS_SUBSCRIBE
            else:
                logger.error("  仍未找到窗口，停止运行。")
                self.state = State.STOP

    def _handle_dismiss_subscribe(self) -> None:
        logger.info("[订阅提醒] 检测订阅弹窗...")
        # 只检测 3 秒（不是 5 秒），快速跳过
        found = self._wait_for_text(self._kw_subscribe, timeout=3, interval=1.0)
        if found:
            result = self._find_and_click(["好的", "不再提示", "不再提醒"], wait_after=2.0)
            if result:
                logger.info(f"  订阅弹窗已关闭 (点击了 '{result[2]}')")
            else:
                logger.warning("  发现订阅弹窗但未能点击按钮")
        else:
            logger.info("  未发现订阅弹窗，跳过")
        self.state = State.CHECK_IN

    def _handle_check_in(self) -> None:
        """点击"签到"标签导航到签到页面。

        注意：只点"签到"标签导航，不点"开始签到"。
        签到页面上同时有"开始签到"、"签到常见问题"、"观看广告"，
        点"开始签到"可能导致页面跳转，误触"签到常见问题"。
        """
        logger.info("[签到] 点击签到标签导航到签到页面...")
        nav_result = self._navigate_to_tab(self._kw_checkin, wait_after=2.0)
        if nav_result is not None:
            logger.info(f"  已导航到签到页面 (点击了 '{nav_result}')")
        else:
            logger.info("  未找到签到标签，可能已在签到页面")
        action_wait(1.0)
        self.state = State.CLICK_AD

    def _handle_click_ad(self) -> None:
        """点击"观看广告"按钮。

        点击后进入 DISMISS_INTERSTITIAL 状态：
        - 如果出现插屏弹窗广告，点"X"关掉
        - 如果没有插屏，直接进入观看广告
        """
        # 确保窗口存在
        if not self._ensure_window():
            logger.warning("[观看广告] 目标窗口不存在，等待重试...")
            action_wait(self._t_check_interval)
            return

        for attempt in range(5):
            logger.info(f"[观看广告] 第{attempt + 1}次尝试...")

            # 签到后页面需要几秒加载，第一次多等一会
            if attempt == 0:
                action_wait(2.0)

            # 如果已经在播放广告（检测到关闭按钮），直接进入观看状态
            has_close = self._has_text(self._kw_close)
            if has_close:
                logger.info("  广告已开始播放（检测到关闭按钮）")
                self.state = State.WATCHING_AD
                self._watch_start = time.time()
                self._ad_not_found_count = 0
                return

            # 点击"观看广告"
            result = self._find_and_click(self._kw_ad, wait_after=self._t_ad_click)
            if result is not None:
                # 点击成功，进入插屏广告处理状态
                logger.info("  已点击'观看广告'，进入插屏广告处理")
                self.state = State.DISMISS_INTERSTITIAL
                return

            # 没找到，检查上限
            if self._has_text(self._kw_limit):
                logger.info("  检测到今日上限提示，停止")
                self.state = State.STOP
                return

            # 等待重试
            logger.info(f"  未找到观看广告，等待重试...")
            action_wait(self._t_check_interval)

        self._ad_not_found_count += 1
        logger.info(f"  本轮未成功 ({self._ad_not_found_count}/{self._max_ad_not_found})")
        if self._ad_not_found_count >= self._max_ad_not_found:
            logger.info("  连续多次未找到观看广告，认为已达每日上限")
            self.state = State.STOP
        else:
            self.stats.ad_skipped += 1
            action_wait(self._t_check_interval)

    def _handle_dismiss_interstitial(self) -> None:
        """处理插屏弹窗广告。

        点击"观看广告"后可能出现一个插屏弹窗广告（不是30秒视频广告），
        页面上有一个"X"按钮，点掉它后才会开始真正的30秒视频广告。

        判断逻辑：
        - 检测到"关闭"按钮 → 30秒视频广告已开始，直接进入 WATCHING_AD
        - 检测到弹窗"×"/"X"/"跳过" → 插屏广告，点掉它
        - 都没检测到 → 等待几秒后重试
        - 等待超时 → 可能没有插屏广告，直接进入 WATCHING_AD
        """
        if not self._ensure_window():
            logger.warning("[插屏广告] 目标窗口不存在")
            self.state = State.CLICK_AD
            return

        for attempt in range(5):
            logger.info(f"[插屏广告] 第{attempt + 1}次检测...")

            # 1. 检测30秒视频广告是否已开始（有"关闭"按钮）
            if self._has_text(self._kw_close):
                logger.info("  30秒视频广告已开始，进入观看状态")
                self.state = State.WATCHING_AD
                self._watch_start = time.time()
                self._ad_not_found_count = 0
                return

            # 2. 检测插屏弹窗广告（有×/X/跳过等按钮）
            popup_keywords = ["×", "✕", "✖", "跳过", "关闭广告", "X"]
            has_popup = self._has_text(popup_keywords) or self._has_uia(popup_keywords)
            if has_popup:
                logger.info("  检测到插屏弹窗广告，尝试关闭")
                # 先用 UIA 搜索×按钮
                for kw in ["×", "✕", "✖", "跳过", "关闭广告"]:
                    result = self._find_and_click([kw], wait_after=2.0)
                    if result:
                        logger.info(f"  已关闭插屏广告 (点击了 '{result[2]}')")
                        action_wait(2.0)
                        # 关闭后重新检测，可能视频广告开始
                        if self._has_text(self._kw_close):
                            logger.info("  30秒视频广告已开始，进入观看状态")
                            self.state = State.WATCHING_AD
                            self._watch_start = time.time()
                            self._ad_not_found_count = 0
                            return
                        # 没有关闭按钮，可能需要再点"观看广告"
                        self.state = State.CLICK_AD
                        return
                logger.warning("  检测到弹窗但未能关闭")
            else:
                logger.info("  未检测到插屏广告，等待...")

            action_wait(self._t_check_interval)

        # 超时：可能没有插屏广告，直接进入观看状态检测
        logger.info("  插屏广告处理超时，进入观看状态检测")
        self.state = State.WATCHING_AD
        self._watch_start = time.time()
        self._ad_not_found_count = 0

    def _handle_watching_ad(self) -> None:
        """观看广告中，处理中断弹窗。

        广告播放30s期间，如果不小心点了关闭按钮，会弹出弹窗：
        "暂未获得奖励"（或类似文字），有两个选项：放弃 / 继续。
        点击"继续"继续观看。即使误点了"放弃"，也继续循环。
        """
        elapsed = time.time() - self._watch_start
        if elapsed >= self._t_ad_watch:
            logger.info(f"  广告观看完成 ({elapsed:.0f}s)")
            self.stats.ad_watched += 1
            self.state = State.CLOSE_AD
            return
        remain = int(self._t_ad_watch - elapsed)
        logger.info(f"  广告播放中... 剩余 {remain}s")
        # 检测中断弹窗（不小心点了关闭）
        if self._has_text(self._kw_interrupt):
            logger.warning("  检测到'暂未获得奖励'弹窗，点击'继续'")
            result = self._find_and_click(self._kw_continue, wait_after=1.0)
            if result is None:
                # "继续"没找到，可能弹窗只有"放弃" — 不点击，继续等待
                logger.warning("  未找到'继续'按钮，不点击'放弃'，继续等待")
        action_wait(self._t_check_interval)

    def _handle_close_ad(self) -> None:
        """关闭广告（30s观看完成后正常关闭）。"""
        logger.info("[关闭广告] 查找关闭按钮...")
        result = self._find_and_click(self._kw_close, wait_after=self._t_close_wait)
        if result is None:
            logger.info("  未找到关闭按钮，重试中...")
            action_wait(self._t_check_interval)
        if self._has_text(self._kw_close):
            logger.warning("  关闭按钮仍在，重试")
            self._find_and_click(self._kw_close, wait_after=self._t_close_wait)
        self.state = State.WAITING_REWARD

    def _handle_waiting_reward(self) -> None:
        """等待奖励冒泡文字（加载中 → 获得签到奖励）。

        冒泡文字总共就1-2秒，快速检测后直接继续循环。
        """
        logger.info("[等待奖励] 等待加载完成...")
        # 加载中+获得签到奖励总共1-2秒，快速检测后直接继续
        self._wait_for_text(self._kw_loading, timeout=1, interval=0.3)
        self._wait_for_text(self._kw_reward, timeout=2, interval=0.3)
        logger.info("  继续循环")
        self.stats.rounds += 1
        logger.info(f"  本轮完成 (第{self.stats.rounds}轮)")
        self.state = State.CLICK_AD
