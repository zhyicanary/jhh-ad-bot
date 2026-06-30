"""状态机引擎 - 核心循环

状态流转：
  CHECK_AD -> WATCHING -> CLOSE_AD -> CHECK_AD ...
"""

import time
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from enum import Enum, auto

from .capture import screenshot
from .vision import match_template
from .action import click, wait as action_wait, focus_window
from . import ocr

logger = logging.getLogger(__name__)


class State(Enum):
    CHECK_AD = auto()    # 寻找"看广告"按钮
    WATCHING = auto()    # 正在观看广告（等待倒计时）
    CLOSE_AD = auto()    # 寻找关闭按钮
    STOP = auto()        # 停止


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
        win_cfg = config.get("window", {})
        self._win_keyword = win_cfg.get("title_keyword", "微信")
        self._auto_focus = win_cfg.get("auto_focus", True)

    def run(self) -> Stats:
        """启动主循环（阻塞运行）。"""
        logger.info("===== 广告机器人启动 =====")
        self.stats.start_time = time.time()

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
        """确保微信窗口在前台。"""
        if self._auto_focus:
            ok = focus_window(self._win_keyword)
            if ok:
                logger.debug(f"已激活窗口 '{self._win_keyword}'")
                action_wait(0.3)

    def _tick(self) -> None:
        """执行一次状态机步骤。"""
        timing = self.config.get("timing", {})
        match_cfg = self.config.get("matching", {})
        templates = self.config.get("templates", {})

        if self.state == State.CHECK_AD:
            self._handle_check_ad(timing, match_cfg, templates)

        elif self.state == State.WATCHING:
            self._handle_watching(timing)

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
        else:
            logger.info("  未找到广告按钮，等待下一轮...")
            self.stats.ad_skipped += 1
            action_wait(timing.get("check_interval", 2))

    def _handle_watching(self, timing: dict) -> None:
        """等待广告播放完毕。"""
        elapsed = time.time() - self._watch_start
        remain = timing.get("ad_watch_seconds", 30) - elapsed
        check_interval = timing.get("check_interval", 2)

        if remain <= 0:
            logger.info("  广告观看完毕，开始寻找关闭按钮...")
            self.state = State.CLOSE_AD
            self.stats.ad_watched += 1
        else:
            logger.info(f"  观看中... 剩余 {remain:.0f}s")
            action_wait(min(check_interval, remain))

    def _handle_close_ad(self, timing: dict, match_cfg: dict, templates: dict) -> None:
        """寻找并点击关闭按钮（OCR 优先，模板匹配回退）。"""
        logger.info("  正在寻找关闭按钮...")
        screen = screenshot()

        # ---- 方案一：OCR 文字识别找"关闭/×/跳过" ----
        if match_cfg.get("ocr_enabled", True):
            ocr_keywords = match_cfg.get("ocr_close_keywords", ["关闭", "×", "跳过", "关闭广告"])
            result = ocr.find_text(screen, ocr_keywords)
        else:
            result = None

        if result is not None:
            x, y, text = result
            logger.info(f"  OCR 找到 '{text}' 按钮 ({x}, {y})")
            self._ensure_focus()
            click(x, y)
            action_wait(timing.get("post_click_delay", 1.5))
            self.state = State.CHECK_AD
            return

        # ---- 方案二：模板匹配回退 ----
        threshold = match_cfg.get("close_confidence", 0.6)
        result = match_template(
            screen,
            templates.get("close_button", "templates/close_button.png"),
            threshold=threshold,
        )

        if result is not None:
            x, y, conf = result
            logger.info(f"  模板匹配找到关闭按钮 ({x}, {y}) 置信度: {conf:.2%}")
            self._ensure_focus()
            click(x, y)
            action_wait(timing.get("post_click_delay", 1.5))
            self.state = State.CHECK_AD
        else:
            logger.info("  未找到关闭按钮，重试中...")
            action_wait(timing.get("close_retry_interval", 3))
