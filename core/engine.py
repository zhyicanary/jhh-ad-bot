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
from .action import click, focus_window
from .action import wait as action_wait
from .capture import screenshot
from .vision import match_template

logger = logging.getLogger(__name__)


class State(Enum):
    CHECK_AD = auto()  # 寻找"看广告"按钮
    WATCHING = auto()  # 正在观看广告（等待倒计时）
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
        win_cfg = config.get("window", {})
        self._win_keyword = win_cfg.get("title_keyword", "微信")
        self._auto_focus = win_cfg.get("auto_focus", True)
        self._close_retry = 0  # 关闭按钮重试计数

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
            logger.info("  广告观看完毕，等待弹窗消失...")
            action_wait(timing.get("ad_end_wait", 3))
            logger.info("  开始寻找关闭按钮...")
            self.state = State.CLOSE_AD
            self._close_step = 0
            self.stats.ad_watched += 1
        else:
            logger.info(f"  观看中... 剩余 {remain:.0f}s")
            action_wait(min(check_interval, remain))

    def _handle_close_ad(self, timing: dict, match_cfg: dict, templates: dict) -> None:
        """关闭广告：盲点右上角 → 检查看广告按钮回来没 → 不行再模板匹配。"""
        if not getattr(self, "_close_step", None):
            self._close_step = 0

        ad_button_tpl = templates.get("ad_button", "templates/ad_button.png")
        ad_threshold = match_cfg.get("confidence_threshold", 0.75)
        screen = screenshot()
        sh, sw = screen.shape[:2]

        # 先检查：看广告按钮回来没？
        if match_template(screen, ad_button_tpl, threshold=ad_threshold):
            logger.info("  广告已关闭，看广告按钮重新出现")
            self._close_step = 0
            self._close_retry = 0
            self.state = State.CHECK_AD
            return

        # ---- 方案一：盲点右上角关闭区域 ----
        blind_positions = [
            (sw - 100, 100),
            (sw - 100, 200),
            (sw - 200, 100),
            (sw - 150, 150),
        ]

        if self._close_step < len(blind_positions) * 2:
            pos_idx = self._close_step % len(blind_positions)
            x, y = blind_positions[pos_idx]
            logger.info(f"  盲点关闭位置 ({x}, {y}) [第{self._close_step + 1}次]")
            self._ensure_focus()
            click(x, y)
            self._close_step += 1
            action_wait(timing.get("post_close_delay", 1.5))
            return

        # ---- 方案二：模板匹配回退 ----
        logger.info("  盲点未成功，尝试模板匹配...")
        threshold = match_cfg.get("close_confidence", 0.6)
        result = match_template(
            screen,
            templates.get("close_button", "templates/close_button.png"),
            threshold=threshold,
        )

        if result is not None:
            x, y, conf = result
            logger.info(f"  模板匹配找到关闭按钮 ({x}, {y}) 置信度: {conf:.2%}")
            self._close_step = 0
            self._close_retry = 0
            self._ensure_focus()
            click(x, y)
            action_wait(timing.get("post_close_delay", 1.5))
            self.state = State.CHECK_AD
            return

        logger.info("  未找到关闭按钮，重试中...")
        action_wait(timing.get("close_retry_interval", 3))
