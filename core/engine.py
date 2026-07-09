"""状态机引擎 - 简幻欢自动化核心

完整工作流：
  1. 激活微信 + 简幻欢窗口
  2. 关闭订阅提醒弹窗（首次）
  3. 签到（首次）
  4. 循环: 点击观看广告 → 关闭插屏弹窗 → 等30s → 关闭广告 → 领奖励 → 再次观看
  5. 达到每日上限后停止

状态流转：
  INIT → DISMISS_SUBSCRIBE → CHECK_IN → CLICK_AD → WATCHING_AD → CLOSE_AD → WAITING_REWARD
                                         ↑                                        ↓
                                         └────────────────────────────────────────┘
                                                              (或 → STOP 达到上限)
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, Optional, Tuple

from . import ocr
from .action import click, focus_window, is_window_in_focus, set_target
from .action import wait as action_wait
from .capture import screenshot

logger = logging.getLogger(__name__)


class State(Enum):
    """自动化状态。"""
    INIT = auto()                # 初始化：激活窗口
    DISMISS_SUBSCRIBE = auto()   # 关闭订阅提醒弹窗
    CHECK_IN = auto()            # 签到
    CLICK_AD = auto()            # 点击"观看广告"
    WATCHING_AD = auto()         # 观看广告(30s)
    CLOSE_AD = auto()            # 关闭广告
    WAITING_REWARD = auto()      # 等待奖励
    STOP = auto()                # 停止


@dataclass
class Stats:
    """运行统计。"""
    rounds: int = 0              # 完整循环次数
    ad_watched: int = 0          # 成功观看广告次数
    ad_skipped: int = 0          # 未找到广告的次数
    start_time: float = 0.0

    def elapsed(self) -> float:
        return time.time() - self.start_time


class AdBotEngine:
    """简幻欢广告机器人主引擎。

    使用方式:
        engine = AdBotEngine(config)
        engine.run()
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.state = State.INIT
        self.stats = Stats()
        self._stop_requested = False
        self._watch_start: float = 0.0
        self._ad_not_found_count: int = 0

        # ── 窗口配置 ──
        win_cfg = config.get("window", {})
        self._win_keyword = win_cfg.get("title_keyword", "简幻欢|WeChatAppEx|微信")
        self._auto_focus = win_cfg.get("auto_focus", True)

        # ── 时间配置 ──
        t = config.get("timing", {})
        self._t_init = t.get("init_wait", 3)
        self._t_ad_click = t.get("ad_click_wait", 2)
        self._t_ad_watch = t.get("ad_watch_seconds", 32)
        self._t_check_interval = t.get("check_interval", 2)
        self._t_close_wait = t.get("close_ad_wait", 2)
        self._t_reward_wait = t.get("reward_wait", 3)

        # ── 识别关键词 ──
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

        # ── 循环配置 ──
        loop_cfg = config.get("loop", {})
        self._max_rounds = loop_cfg.get("max_rounds", 0)

    # ──────────────────────────────────────────────
    # 主循环
    # ──────────────────────────────────────────────

    def run(self) -> Stats:
        """启动主循环（阻塞运行）。"""
        logger.info("===== 简幻欢广告机器人启动 =====")
        self.stats.start_time = time.time()

        # 初始化 OCR
        if not ocr.init():
            logger.error("OCR 引擎初始化失败，无法运行。请安装 rapidocr-onnxruntime")
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
        """请求停止（从外部调用）。"""
        self._stop_requested = True

    def _tick(self) -> None:
        """执行一次状态机步骤。"""
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
    # 辅助方法
    # ──────────────────────────────────────────────

    def _ensure_focus(self) -> None:
        """确保窗口在前台。"""
        if not self._auto_focus:
            return
        if is_window_in_focus(self._win_keyword):
            return
        logger.info("窗口不在前台，尝试激活...")
        for attempt in range(5):
            focus_window(self._win_keyword)
            action_wait(2)
            if is_window_in_focus(self._win_keyword):
                logger.info("窗口已回到前台")
                set_target(self._win_keyword)
                return
        logger.warning("窗口激活超时，强制继续")

    def _find_text(self, keywords: list[str], region=None) -> Optional[Tuple[int, int, str]]:
        """OCR 查找文字（单次截图），返回 (x, y, text) 或 None。"""
        screen = screenshot()
        return ocr.find_text(screen, keywords, region=region)

    def _find_and_click(
        self, keywords: list[str], region=None, wait_after: float = 1.0
    ) -> Optional[Tuple[int, int, str]]:
        """OCR 查找文字并点击，返回 (x, y, text) 或 None。"""
        result = self._find_text(keywords, region=region)
        if result is None:
            return None
        x, y, text = result
        logger.info(f"  找到 '{text}' @ ({x}, {y})")
        self._ensure_focus()
        click(x, y, clicks=1)
        action_wait(wait_after)
        return (x, y, text)

    def _wait_for_text(
        self, keywords: list[str], timeout: float = 10, interval: float = 1.0, region=None
    ) -> bool:
        """等待文字出现，返回是否找到。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._find_text(keywords, region=region) is not None:
                return True
            action_wait(interval)
        return False

    def _wait_for_text_gone(
        self, keywords: list[str], timeout: float = 10, interval: float = 1.0, region=None
    ) -> bool:
        """等待文字消失，返回是否消失。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._find_text(keywords, region=region) is None:
                return True
            action_wait(interval)
        return False

    def _close_popup_x(self) -> bool:
        """检测并关闭覆盖屏幕的弹窗（×按钮）。

        检测逻辑：如果"观看广告"不可见，可能被弹窗覆盖。
        优先用 OCR 查找 ×，找不到则点击右上角。

        Returns:
            True 表示尝试关闭了弹窗。
        """
        # 如果"观看广告"仍可见，说明没有弹窗
        if self._find_text(self._kw_ad) is not None:
            return False

        # 尝试 OCR 找 ×（搜索屏幕上半部分）
        screen = screenshot()
        sh, sw = screen.shape[:2]
        top_region = (0, 0, sw, sh // 3)
        x_result = ocr.find_text(screen, self._kw_popup_x, region=top_region)

        if x_result is not None:
            x, y, text = x_result
            logger.info(f"  关闭弹窗 '{text}' @ ({x}, {y})")
            self._ensure_focus()
            click(x, y, clicks=1)
        else:
            # OCR 找不到 ×，回退到右上角
            logger.info(f"  OCR未找到×，点击右上角 ({sw - 30}, 15)")
            self._ensure_focus()
            click(sw - 30, 15, clicks=1)

        action_wait(1.5)
        return True

    # ──────────────────────────────────────────────
    # 状态处理
    # ──────────────────────────────────────────────

    def _handle_init(self) -> None:
        """初始化：激活窗口。"""
        logger.info("[INIT] 激活窗口...")
        self._ensure_focus()
        action_wait(self._t_init)

        if is_window_in_focus(self._win_keyword):
            logger.info("窗口已就绪")
        else:
            logger.warning("未找到目标窗口，请确保微信和简幻欢已打开")

        self.state = State.DISMISS_SUBSCRIBE

    def _handle_dismiss_subscribe(self) -> None:
        """关闭订阅提醒弹窗（仅首次）。"""
        logger.info("[订阅提醒] 检测订阅弹窗...")

        # 短暂检测，3秒内没发现就跳过
        found = self._wait_for_text(self._kw_subscribe, timeout=3, interval=1.0)

        if found:
            # 优先点击"好的"，其次"不再提示"
            result = self._find_and_click(
                ["好的", "不再提示", "不再提醒"], wait_after=2.0
            )
            if result:
                logger.info(f"  订阅弹窗已关闭 (点击了 '{result[2]}')")
            else:
                logger.warning("  发现订阅弹窗但未能点击按钮，继续执行")
        else:
            logger.info("  未发现订阅弹窗，跳过")

        self.state = State.CHECK_IN

    def _handle_check_in(self) -> None:
        """签到。"""
        logger.info("[签到] 查找签到按钮...")

        result = self._find_and_click(self._kw_checkin, wait_after=2.0)
        if result is not None:
            logger.info("  签到完成")
            # 可能有签到结果弹窗，尝试关闭
            action_wait(1.0)
            self._find_and_click(self._kw_dismiss, wait_after=1.0)
        else:
            logger.info("  未找到签到按钮，可能已签到")

        self.state = State.CLICK_AD

    def _handle_click_ad(self) -> None:
        """点击"观看广告"，自动处理插屏弹窗，直到广告开始播放。

        流程：
          1. 检测当前界面状态（有广告按钮？有关闭按钮？）
          2. 有"关闭"→ 广告已开始 → WATCHING_AD
          3. 有"观看广告"→ 点击它 → 回到步骤1
          4. 都没有 → 可能被弹窗覆盖 → 关闭× → 回到步骤1
          5. 多次找不到"观看广告"→ 达到上限 → STOP
        """
        for attempt in range(4):
            logger.info(f"[观看广告] 第{attempt + 1}次尝试...")

            # 截图分析当前状态
            has_ad = self._find_text(self._kw_ad) is not None
            has_close = self._find_text(self._kw_close) is not None

            # ── 广告已开始播放 ──
            if has_close:
                logger.info("  广告已开始播放（检测到关闭按钮）")
                self.state = State.WATCHING_AD
                self._watch_start = time.time()
                self._ad_not_found_count = 0
                return

            # ── 主界面，点击"观看广告" ──
            if has_ad:
                result = self._find_and_click(self._kw_ad, wait_after=self._t_ad_click)
                if result is None:
                    logger.info("  按钮消失，重新检测")
                    continue
                # 点击后继续循环，重新检测状态
                continue

            # ── 界面被覆盖，尝试关闭弹窗 ──
            # 先检查是否达到上限
            if self._find_text(self._kw_limit):
                logger.info("  检测到今日上限提示，停止")
                self.state = State.STOP
                return

            logger.info("  界面被覆盖，尝试关闭弹窗")
            self._close_popup_x()
            # 继续循环，重新检测状态

        # 4次尝试后
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
        """观看广告（等待30秒）。

        期间检测"暂未获得奖励"弹窗（误点关闭导致），自动点击"继续"。
        """
        elapsed = time.time() - self._watch_start

        if elapsed >= self._t_ad_watch:
            logger.info(f"  广告观看完成 ({elapsed:.0f}s)")
            self.stats.ad_watched += 1
            self.state = State.CLOSE_AD
            return

        remain = int(self._t_ad_watch - elapsed)
        logger.info(f"  广告播放中... 剩余 {remain}s")

        # 检测"暂未获得奖励"弹窗（安全网）
        if self._find_text(self._kw_interrupt):
            logger.warning("  检测到'暂未获得奖励'弹窗，点击'继续'")
            self._find_and_click(self._kw_continue, wait_after=1.0)

        action_wait(self._t_check_interval)

    def _handle_close_ad(self) -> None:
        """关闭广告。"""
        logger.info("[关闭广告] 查找关闭按钮...")

        result = self._find_and_click(self._kw_close, wait_after=self._t_close_wait)

        if result is None:
            # 关闭按钮可能已消失（广告自动关闭），或位置特殊
            logger.info("  未找到关闭按钮，尝试右上角")
            screen = screenshot()
            sh, sw = screen.shape[:2]
            self._ensure_focus()
            click(sw - 30, 15, clicks=1)
            action_wait(self._t_close_wait)

        # 验证广告是否已关闭
        if self._find_text(self._kw_close):
            logger.warning("  关闭按钮仍在，重试")
            self._find_and_click(self._kw_close, wait_after=self._t_close_wait)

        self.state = State.WAITING_REWARD

    def _handle_waiting_reward(self) -> None:
        """等待奖励（加载中 → 获得积分成功）。"""
        logger.info("[等待奖励] 等待加载完成...")

        # 等待"加载中"出现并消失
        if self._wait_for_text(self._kw_loading, timeout=5, interval=0.5):
            logger.info("  加载中...")
            self._wait_for_text_gone(self._kw_loading, timeout=15, interval=0.5)
            logger.info("  加载完成")

        # 等待积分到账提示
        if self._wait_for_text(self._kw_reward, timeout=8, interval=1.0):
            logger.info("  ★ 获得观看积分！")
            action_wait(2)  # 等提示消失
        else:
            logger.info("  未检测到积分提示，继续循环")

        self.stats.rounds += 1
        logger.info(f"  本轮完成 (第{self.stats.rounds}轮)")

        # 回到点击广告状态，继续循环
        self.state = State.CLICK_AD
