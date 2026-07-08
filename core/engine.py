"""状态机引擎 - 核心循环

状态流转：
  CHECK_AD -> WATCHING -> CLOSE_AD -> CHECK_AD ...
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional

from . import ocr
from .action import click, focus_window, is_window_in_focus, set_target
from .action import wait as action_wait
from .capture import screenshot
from .vision import match_template

logger = logging.getLogger(__name__)


class State(Enum):
    CHECK_AD = auto()  # 寻找"看广告"按钮
    WATCHING = auto()  # 正在观看广告（实时检测画面变化）
    CLOSE_AD = auto()  # 寻找关闭按钮
    STOP = auto()  # 停止


@dataclass
class Stats:
    """运行统计。"""

    rounds: int = 0
    ad_watched: int = 0
    ad_skipped: int = 0  # 没找到广告按钮的次数
    start_time: float = 0.0

    def elapsed(self) -> float:
        return time.time() - self.start_time


class AdBotEngine:
    """广告机器人主引擎。

    使用方式:
        engine = AdBotEngine(config)
        engine.run()
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.state = State.CHECK_AD
        self.stats = Stats()
        self._watch_start: float = 0.0
        self._stop_requested = False
        self._last_screen = None
        self._still_count = 0
        win_cfg = config.get("window", {})
        self._win_keyword = win_cfg.get("title_keyword", "微信")
        self._auto_focus = win_cfg.get("auto_focus", True)

    def run(self) -> Stats:
        """启动主循环（阻塞运行）。"""
        logger.info("===== 广告机器人启动 =====")
        self.stats.start_time = time.time()

        # 初始化 RapidOCR
        ocr.init()

        # 激活微信窗口
        self._ensure_focus()
        action_wait(1.0)

        loop_cfg = self.config.get("loop", {})
        max_rounds = loop_cfg.get("max_rounds", 0)

        while not self._stop_requested:
            if max_rounds > 0 and self.stats.rounds >= max_rounds:
                logger.info("已达最大循环次数，停止。")
                break

            self._tick()
            self.stats.rounds += 1

        logger.info(f"===== 停止 =====")
        logger.info(
            f"统计: 共 {self.stats.rounds} 轮, "
            f"观看广告 {self.stats.ad_watched} 次, "
            f"未找到广告 {self.stats.ad_skipped} 次, "
            f"耗时 {self.stats.elapsed():.1f}s"
        )
        return self.stats

    def stop(self) -> None:
        """请求停止（从外部调用，下次循环生效）。"""
        self._stop_requested = True

    def _ensure_focus(self) -> None:
        """阻塞等待窗口回到前台，不等到不继续。"""
        if not self._auto_focus:
            return

        # 已经在焦点 → 直接继续
        if is_window_in_focus(self._win_keyword):
            set_target(self._win_keyword)
            return

        logger.info(f"窗口 '{self._win_keyword}' 不在前台，尝试激活...")

        for attempt in range(10):  # 最多等约 30 秒
            # 先尝试强制激活
            focus_window(self._win_keyword)

            # 等一会再检查
            action_wait(2)

            if is_window_in_focus(self._win_keyword):
                logger.info(f"窗口 '{self._win_keyword}' 已回到前台")
                set_target(self._win_keyword)  # 缓存 hwnd 给 PostMessage 用
                return

            if attempt == 2:
                logger.warning(f"窗口 '{self._win_keyword}' 未响应，请手动切换到微信窗口")

        logger.warning(f"等待窗口 '{self._win_keyword}' 超时，强制继续")

    def _tick(self) -> None:
        """执行一次状态机步骤。"""
        # 执行任何操作前先确保窗口在前台
        self._ensure_focus()

        timing = self.config.get("timing", {})
        match_cfg = self.config.get("matching", {})
        templates = self.config.get("templates", {})

        if self.state == State.CHECK_AD:
            self._handle_check_ad(timing, match_cfg, templates)

        elif self.state == State.WATCHING:
            self._handle_watching(timing, match_cfg)

        elif self.state == State.CLOSE_AD:
            self._handle_close_ad(timing, match_cfg, templates)

    # ---- 各状态处理 ----

    def _handle_check_ad(self, timing: dict, match_cfg: dict, templates: dict) -> None:
        """寻找'看广告'按钮。"""
        logger.info(f"[第{self.stats.rounds + 1}轮] 正在寻找'看广告'按钮...")
        screen = screenshot()
        threshold = match_cfg.get("confidence_threshold", 0.75)
        scale_steps = match_cfg.get("scale_steps", 5)
        scale_range = tuple(match_cfg.get("scale_range", [0.8, 1.2]))

        result = match_template(
            screen,
            templates.get("ad_button", "templates/ad_button.png"),
            threshold=threshold,
            scale_steps=scale_steps,
            scale_range=scale_range,
        )

        if result is not None:
            x, y, conf = result
            logger.info(f"  找到广告按钮 ({x}, {y}) 置信度: {conf:.2%}")
            self._ensure_focus()
            click(x, y)
            action_wait(timing.get("post_click_delay", 1.5))
            self.state = State.WATCHING
            self._watch_start = time.time()
            self._last_screen = None
            self._still_count = 0
        else:
            logger.info("  未找到广告按钮，等待下一轮...")
            self.stats.ad_skipped += 1
            action_wait(timing.get("check_interval", 2))

    def _calculate_screen_diff(self, prev, curr) -> float:
        """计算两帧屏幕截图的平均像素差异（0-255）。"""
        import cv2
        prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY)
        return cv2.absdiff(prev_gray, curr_gray).mean()

    def _handle_watching(self, timing: dict, match_cfg: dict) -> None:
        """等待广告播放完毕（实时检测画面变化）。"""
        elapsed = time.time() - self._watch_start
        check_interval = timing.get("check_interval", 2)
        max_seconds = timing.get("ad_max_seconds", 35)
        min_seconds = timing.get("ad_min_seconds", 8)

        # 超时保护 → 强制关闭
        if elapsed > max_seconds:
            logger.info("  广告播放超时，强制进入关闭阶段")
            action_wait(timing.get("ad_end_wait", 3))
            self.state = State.CLOSE_AD
            return

        # 最小观看时间内不做画面检测（防止点击没反应就跳过）
        if elapsed < min_seconds:
            remain = int(min_seconds - elapsed)
            logger.info(f"  广告播放中，还需 {remain}s 开始检测")
            action_wait(check_interval)
            return

        # 画面变化检测
        if match_cfg.get("screen_diff_enabled", True):
            screen = screenshot()
            if self._last_screen is not None:
                diff = self._calculate_screen_diff(self._last_screen, screen)
                threshold = match_cfg.get("screen_diff_threshold", 5.0)
                still_limit = match_cfg.get("screen_diff_still_count", 3)

                if diff < threshold:
                    self._still_count += 1
                    logger.info(f"  画面静止 ({diff:.1f}) 第{self._still_count}次")

                    if self._still_count >= still_limit:
                        logger.info("  广告播放完毕（画面静止），进入关闭阶段")
                        self.stats.ad_watched += 1
                        action_wait(timing.get("ad_end_wait", 3))
                        self.state = State.CLOSE_AD
                        return
                else:
                    if self._still_count > 0:
                        logger.debug(f"  画面恢复变化 ({diff:.1f}), 重置静止计数")
                    self._still_count = 0

            self._last_screen = screen

        action_wait(check_interval)

    def _handle_close_ad(self, timing: dict, match_cfg: dict, templates: dict) -> None:
        """寻找并点击关闭按钮（仅 OCR 文字识别）。"""
        logger.info("  正在寻找关闭按钮...")
        screen = screenshot()
        sh, sw = screen.shape[:2]

        # 关闭按钮通常在右上角，只扫这个区域加速
        region = (sw * 3 // 4, 0, sw // 4, sh // 4)

        ocr_cfg = match_cfg.get("ocr_close", {})
        keywords = ocr_cfg.get("keywords", ["关闭", "×", "跳过", "关闭广告"])
        result = ocr.find_text(screen, keywords, region=region)

        if result is not None:
            x, y, text = result
            logger.info(f"  找到关闭按钮 '{text}' ({x}, {y})")
            self._ensure_focus()
            click(x, y)
            action_wait(timing.get("post_close_delay", 1.5))
            self.state = State.CHECK_AD
        else:
            logger.info("  未找到关闭按钮，重试中...")
            action_wait(timing.get("close_retry_interval", 3))
