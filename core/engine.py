"""状态机引擎 - 简幻欢自动化核心

完整工作流：
  1. 激活微信 + 简幻欢窗口
  2. 关闭订阅提醒弹窗（首次）
  3. 签到（首次）
  4. 循环: 点击观看广告 → 关闭插屏弹窗 → 等30s → 关闭广告 → 领奖励 → 再次观看
  5. 达到每日上限后停止

核心设计：
  - 所有截图只截取目标窗口区域，不会误识别其他窗口内容
  - OCR 坐标是窗口相对坐标，点击时自动加上窗口偏移转为屏幕绝对坐标
  - 全 OCR 识别，不依赖模板图片
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple

from . import ocr
from .action import click, focus_window, get_window_rect, is_window_in_focus, set_target
from .action import wait as action_wait
from .capture import screenshot

logger = logging.getLogger(__name__)


class State(Enum):
    """自动化状态。"""
    INIT = auto()
    DISMISS_SUBSCRIBE = auto()
    CHECK_IN = auto()
    CLICK_AD = auto()
    WATCHING_AD = auto()
    CLOSE_AD = auto()
    WAITING_REWARD = auto()
    STOP = auto()


@dataclass
class Stats:
    """运行统计。"""
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
        self.state = State.INIT
        self.stats = Stats()
        self._stop_requested = False
        self._watch_start: float = 0.0
        self._ad_not_found_count: int = 0
        self._win_offset: Tuple[int, int] = (0, 0)

        win_cfg = config.get("window", {})
        self._win_keyword = win_cfg.get("title_keyword", "简幻欢|WeChatAppEx|微信")
        self._auto_focus = win_cfg.get("auto_focus", True)

        t = config.get("timing", {})
        self._t_init = t.get("init_wait", 3)
        self._t_ad_click = t.get("ad_click_wait", 2)
        self._t_ad_watch = t.get("ad_watch_seconds", 32)
        self._t_check_interval = t.get("check_interval", 2)
        self._t_close_wait = t.get("close_ad_wait", 2)
        self._t_reward_wait = t.get("reward_wait", 3)

        m = config.get("matching", {})
        self._kw_subscribe = m.get("subscribe_keywords", ["订阅提醒", "好的", "不再提示", "不再提醒"])
        self._kw_checkin = m.get("checkin_keywords", ["签到"])
        self._kw_ad = m.get("ad_keywords", ["观看广告", "看广告"])
        self._kw_close = m.get("close_keywords", ["关闭", "关闭广告"])
        self._kw_interrupt = m.get("interrupt_keywords", ["暂未获得奖励", "继续", "放弃"])
        self._kw_continue = m.get("continue_keywords", ["继续"])
        self._kw_reward = m.get("reward_keywords", ["获得观看积分", "积分成功", "观看积分"])
        self._kw_loading = m.get("loading_keywords", ["加载中"])
        self._kw_limit = m.get("limit_keywords", ["今日", "上限", "次数已用完", "已达上限", "已用完"])
        self._kw_popup_x = m.get("popup_close_keywords", ["×", "X", "✕", "✖"])
        self._kw_dismiss = m.get("dismiss_keywords", ["确定", "知道了", "好的"])
        self._max_ad_not_found = m.get("max_ad_not_found", 5)

        loop_cfg = config.get("loop", {})
        self._max_rounds = loop_cfg.get("max_rounds", 0)

    # ──────────────────────────────────────────────
    # 主循环
    # ──────────────────────────────────────────────

    def run(self) -> Stats:
        logger.info("===== 简幻欢广告机器人启动 =====")
        self.stats.start_time = time.time()

        if not ocr.init():
            logger.error("OCR 引擎初始化失败，无法运行")
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
            State.INIT: self._handle_init,
            State.DISMISS_SUBSCRIBE: self._handle_dismiss_subscribe,
            State.CHECK_IN: self._handle_check_in,
            State.CLICK_AD: self._handle_click_ad,
            State.WATCHING_AD: self._handle_watching_ad,
            State.CLOSE_AD: self._handle_close_ad,
            State.WAITING_REWARD: self._handle_waiting_reward,
        }
        handler = handlers.get(self.state)
        if handler:
            handler()

    # ──────────────────────────────────────────────
    # 窗口截图 + 坐标转换（核心）
    # ──────────────────────────────────────────────

    def _capture(self):
        """截取目标窗口区域。

        只截取小程序窗口，不会截到其他窗口的内容。
        同时记录窗口偏移，供 _click_win 使用。
        """
        rect = get_window_rect()
        if rect is not None:
            left, top, w, h = rect
            self._win_offset = (left, top)
            logger.info(f"  截图窗口区域: left={left} top={top} {w}x{h}")
            img = screenshot(region=(left, top, w, h))
            logger.debug(f"  截图尺寸: {img.shape}")
            return img
        else:
            # 找不到窗口，回退全屏
            self._win_offset = (0, 0)
            logger.warning("  未找到目标窗口! 使用全屏截图")
            img = screenshot()
            logger.debug(f"  全屏截图尺寸: {img.shape}")
            return img

    def _click_win(self, x: int, y: int, clicks: int = 1, wait_after: float = 0) -> None:
        """点击窗口相对坐标（OCR 坐标 + 窗口偏移 = 屏幕绝对坐标）。"""
        ox, oy = self._win_offset
        screen_x = ox + x
        screen_y = oy + y
        logger.info(f"  点击: window({x},{y}) → screen({screen_x},{screen_y})")
        click(screen_x, screen_y, clicks=clicks)
        if wait_after > 0:
            action_wait(wait_after)

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _ensure_focus(self) -> None:
        """确保窗口在前台并设置 _target_hwnd。"""
        if not self._auto_focus:
            # 即使不自动激活，也要设置 target
            set_target(self._win_keyword)
            return

        # 窗口已在前台 → 设置 target 后返回
        if is_window_in_focus(self._win_keyword):
            set_target(self._win_keyword)
            return

        logger.info("窗口不在前台，尝试激活...")
        for attempt in range(5):
            focus_window(self._win_keyword)
            action_wait(2)
            if is_window_in_focus(self._win_keyword):
                logger.info("窗口已回到前台")
                set_target(self._win_keyword)
                return

        # 激活超时，最后尝试一次 set_target
        logger.warning("窗口激活超时，尝试设置 target...")
        set_target(self._win_keyword)

    def _find_text(self, keywords: list[str], region=None) -> Optional[Tuple[int, int, str]]:
        """OCR 查找文字（窗口截图），返回窗口相对坐标或 None。"""
        screen = self._capture()
        result = ocr.find_text(screen, keywords, region=region)
        if result is None:
            logger.info(f"  OCR 未匹配: {keywords}")
        else:
            logger.info(f"  OCR 命中: '{result[2]}' @ ({result[0]}, {result[1]})")
        return result

    def _find_and_click(
        self, keywords: list[str], region=None, wait_after: float = 1.0
    ) -> Optional[Tuple[int, int, str]]:
        """OCR 查找文字并点击。"""
        result = self._find_text(keywords, region=region)
        if result is None:
            return None
        x, y, text = result
        self._ensure_focus()
        self._click_win(x, y, clicks=1, wait_after=wait_after)
        return (x, y, text)

    def _wait_for_text(
        self, keywords: list[str], timeout: float = 10, interval: float = 1.0, region=None
    ) -> bool:
        """等待文字出现。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._find_text(keywords, region=region) is not None:
                return True
            action_wait(interval)
        return False

    def _wait_for_text_gone(
        self, keywords: list[str], timeout: float = 10, interval: float = 1.0, region=None
    ) -> bool:
        """等待文字消失。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._find_text(keywords, region=region) is None:
                return True
            action_wait(interval)
        return False

    def _close_popup_x(self) -> bool:
        """检测并关闭覆盖窗口的弹窗（×按钮）。"""
        if self._find_text(self._kw_ad) is not None:
            return False

        screen = self._capture()
        sh, sw = screen.shape[:2]
        top_region = (0, 0, sw, sh // 3)
        x_result = ocr.find_text(screen, self._kw_popup_x, region=top_region)

        if x_result is not None:
            x, y, text = x_result
            logger.info(f"  关闭弹窗 '{text}' @ window({x}, {y})")
            self._ensure_focus()
            self._click_win(x, y, clicks=1)
        else:
            logger.info(f"  OCR未找到×，点击窗口右上角 ({sw - 30}, 15)")
            self._ensure_focus()
            self._click_win(sw - 30, 15, clicks=1)

        action_wait(1.5)
        return True

    # ──────────────────────────────────────────────
    # 状态处理
    # ──────────────────────────────────────────────

    def _handle_init(self) -> None:
        logger.info("[INIT] 激活窗口...")
        self._ensure_focus()
        action_wait(self._t_init)

        # 验证窗口是否就绪
        rect = get_window_rect()
        if rect:
            logger.info(f"窗口已就绪: {rect[2]}x{rect[3]} @ ({rect[0]}, {rect[1]})")
        else:
            logger.warning("未找到目标窗口! 请确保微信和简幻欢已打开")

        self.state = State.DISMISS_SUBSCRIBE

    def _handle_dismiss_subscribe(self) -> None:
        logger.info("[订阅提醒] 检测订阅弹窗...")

        found = self._wait_for_text(self._kw_subscribe, timeout=5, interval=1.0)

        if found:
            result = self._find_and_click(
                ["好的", "不再提示", "不再提醒"], wait_after=2.0
            )
            if result:
                logger.info(f"  订阅弹窗已关闭 (点击了 '{result[2]}')")
            else:
                logger.warning("  发现订阅弹窗但未能点击按钮")
        else:
            logger.info("  未发现订阅弹窗，跳过")

        self.state = State.CHECK_IN

    def _handle_check_in(self) -> None:
        logger.info("[签到] 查找签到按钮...")

        result = self._find_and_click(self._kw_checkin, wait_after=2.0)
        if result is not None:
            logger.info("  签到完成")
            action_wait(1.0)
            self._find_and_click(self._kw_dismiss, wait_after=1.0)
        else:
            logger.info("  未找到签到按钮，可能已签到")

        self.state = State.CLICK_AD

    def _handle_click_ad(self) -> None:
        for attempt in range(4):
            logger.info(f"[观看广告] 第{attempt + 1}次尝试...")

            has_ad = self._find_text(self._kw_ad) is not None
            has_close = self._find_text(self._kw_close) is not None

            if has_close:
                logger.info("  广告已开始播放（检测到关闭按钮）")
                self.state = State.WATCHING_AD
                self._watch_start = time.time()
                self._ad_not_found_count = 0
                return

            if has_ad:
                result = self._find_and_click(self._kw_ad, wait_after=self._t_ad_click)
                if result is None:
                    continue
                continue

            if self._find_text(self._kw_limit):
                logger.info("  检测到今日上限提示，停止")
                self.state = State.STOP
                return

            logger.info("  界面被覆盖，尝试关闭弹窗")
            self._close_popup_x()

        self._ad_not_found_count += 1
        logger.info(
            f"  本轮未成功开始广告 "
            f"({self._ad_not_found_count}/{self._max_ad_not_found})"
        )

        if self._ad_not_found_count >= self._max_ad_not_found:
            logger.info("  连续多次未找到观看广告，认为已达每日上限")
            self.state = State.STOP
        else:
            self.stats.ad_skipped += 1
            action_wait(self._t_check_interval)

    def _handle_watching_ad(self) -> None:
        elapsed = time.time() - self._watch_start

        if elapsed >= self._t_ad_watch:
            logger.info(f"  广告观看完成 ({elapsed:.0f}s)")
            self.stats.ad_watched += 1
            self.state = State.CLOSE_AD
            return

        remain = int(self._t_ad_watch - elapsed)
        logger.info(f"  广告播放中... 剩余 {remain}s")

        if self._find_text(self._kw_interrupt):
            logger.warning("  检测到'暂未获得奖励'弹窗，点击'继续'")
            self._find_and_click(self._kw_continue, wait_after=1.0)

        action_wait(self._t_check_interval)

    def _handle_close_ad(self) -> None:
        logger.info("[关闭广告] 查找关闭按钮...")

        result = self._find_and_click(self._kw_close, wait_after=self._t_close_wait)

        if result is None:
            logger.info("  未找到关闭按钮，尝试窗口右上角")
            screen = self._capture()
            sh, sw = screen.shape[:2]
            self._ensure_focus()
            self._click_win(sw - 30, 15, clicks=1, wait_after=self._t_close_wait)

        if self._find_text(self._kw_close):
            logger.warning("  关闭按钮仍在，重试")
            self._find_and_click(self._kw_close, wait_after=self._t_close_wait)

        self.state = State.WAITING_REWARD

    def _handle_waiting_reward(self) -> None:
        logger.info("[等待奖励] 等待加载完成...")

        if self._wait_for_text(self._kw_loading, timeout=5, interval=0.5):
            logger.info("  加载中...")
            self._wait_for_text_gone(self._kw_loading, timeout=15, interval=0.5)
            logger.info("  加载完成")

        if self._wait_for_text(self._kw_reward, timeout=8, interval=1.0):
            logger.info("  ★ 获得观看积分！")
            action_wait(2)
        else:
            logger.info("  未检测到积分提示，继续循环")

        self.stats.rounds += 1
        logger.info(f"  本轮完成 (第{self.stats.rounds}轮)")

        self.state = State.CLICK_AD
