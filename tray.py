#!/usr/bin/env python3
"""jhh-ad-bot 系统托盘应用

后台运行，右下角托盘图标，右键菜单操作：
  - 启动自动化
  - 停止自动化
  - 打开微信
  - 只执行一轮
  - 查看日志
  - 退出

引擎在独立线程中运行，不阻塞托盘 UI。
"""

import ctypes
import logging
import os
import subprocess
import sys
import threading
from typing import Optional

import yaml
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# Windows API
_HAS_WIN32 = hasattr(ctypes, "windll")

# 自提权
def _is_admin() -> bool:
    if not _HAS_WIN32:
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _run_as_admin():
    """以管理员权限重启程序。"""
    if not _HAS_WIN32 or _is_admin():
        return False
    try:
        params = " ".join([f'"{arg}"' for arg in sys.argv])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)
    except Exception:
        return False


def resource_path(relative_path: str) -> str:
    """获取打包后资源文件的真实路径。"""
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def open_wechat() -> bool:
    """打开微信客户端。"""
    if not _HAS_WIN32:
        return False
    user32 = ctypes.windll.user32
    from ctypes import wintypes

    # 先检查微信是否已经在运行
    keywords = ["微信", "WeChat"]
    found_hwnd = None

    def callback(hwnd, _lparam):
        nonlocal found_hwnd
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd) + 1
        if length <= 1:
            return True
        buf = ctypes.create_unicode_buffer(length)
        user32.GetWindowTextW(hwnd, buf, length)
        title = buf.value
        for kw in keywords:
            if kw in title:
                found_hwnd = hwnd
                return False  # 找到了就停止
        return True

    enum_cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(enum_cb(callback), 0)

    if found_hwnd:
        # 微信已运行，激活它
        user32.ShowWindow(found_hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(found_hwnd)
        logger.info(f"微信已激活 (hwnd={found_hwnd})")
        return True

    # 微信未运行，尝试启动
    wechat_paths = [
        os.path.expandvars(r"%PROGRAMFILES%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%PROGRAMFILES(X86)%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%LOCALAPPDATA%\Tencent\WeChat\WeChat.exe"),
        os.path.expandvars(r"%APPDATA%\Tencent\WeChat\WeChat.exe"),
    ]

    for path in wechat_paths:
        if os.path.exists(path):
            subprocess.Popen([path])
            logger.info(f"微信已启动: {path}")
            return True

    # 尝试通过注册表查找
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
                        subprocess.Popen([exe_path])
                        logger.info(f"微信已启动: {exe_path}")
                        return True
                except (FileNotFoundError, OSError):
                    pass
    except ImportError:
        pass

    logger.warning("未找到微信安装路径，请手动打开微信")
    return False


def create_icon_image(status: str = "idle") -> Image.Image:
    """创建托盘图标。

    状态颜色：
      - idle: 灰色（待机）
      - running: 绿色（运行中）
      - error: 红色（错误）
    """
    colors = {
        "idle": "#888888",
        "running": "#22c55e",
        "error": "#ef4444",
    }
    color = colors.get(status, "#888888")

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 画一个圆角矩形背景
    draw.rounded_rectangle([4, 4, 60, 60], radius=12, fill=color)

    # 画一个 "J" 字母（简幻欢）
    draw.text((22, 12), "J", fill="white")

    return img


class TrayApp:
    """系统托盘应用。"""

    def __init__(self, config: dict, config_path: str):
        self.config = config
        self.config_path = config_path
        self.engine: Optional["AdBotEngine"] = None
        self.engine_thread: Optional[threading.Thread] = None
        self.is_running = False
        self.status = "idle"  # idle, running, error
        self.log_lines: list[str] = []
        self._log_handler = None

        # 延迟导入 pystray
        try:
            import pystray
            self.pystray = pystray
        except ImportError:
            logger.error("pystray 未安装，请运行: pip install pystray Pillow")
            raise

    def _setup_logging(self):
        """设置日志，同时输出到文件和内存缓冲区。"""
        class MemoryHandler(logging.Handler):
            def __init__(self, app):
                super().__init__()
                self.app = app

            def emit(self, record):
                msg = self.format(record)
                self.app.log_lines.append(msg)
                # 保留最近 500 行
                if len(self.app.log_lines) > 500:
                    self.app.log_lines = self.app.log_lines[-500:]

        handler = MemoryHandler(self)
        handler.setLevel(logging.INFO)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        self._log_handler = handler

    def _on_start(self, icon=None, item=None):
        """启动自动化。"""
        if self.is_running:
            return
        self.is_running = True
        self.status = "running"
        icon.icon = create_icon_image("running")
        icon.title = "简幻欢自动化 - 运行中"

        # 在新线程中运行引擎
        self.engine_thread = threading.Thread(target=self._run_engine, daemon=True)
        self.engine_thread.start()

    def _on_start_once(self, icon=None, item=None):
        """只执行一轮。"""
        if self.is_running:
            return
        self.config.setdefault("loop", {})["max_rounds"] = 1
        self._on_start(icon, item)

    def _on_stop(self, icon=None, item=None):
        """停止自动化。"""
        if self.engine:
            self.engine.stop()
        self.is_running = False
        self.status = "idle"
        if icon:
            icon.icon = create_icon_image("idle")
            icon.title = "简幻欢自动化 - 待机"

    def _on_open_wechat(self, icon=None, item=None):
        """打开微信。"""
        open_wechat()

    def _on_show_log(self, icon=None, item=None):
        """显示日志窗口。"""
        log_text = "\n".join(self.log_lines[-100:]) if self.log_lines else "暂无日志"
        # 简单的弹出窗口显示日志
        if _HAS_WIN32:
            ctypes.windll.user32.MessageBoxW(
                0, log_text, "简幻欢自动化 - 日志", 0x00001000 | 0x00040000  # MB_TOPMOST | MB_SETFOREGROUND
            )

    def _on_quit(self, icon=None, item=None):
        """退出。"""
        if self.engine:
            self.engine.stop()
        self.is_running = False
        if icon:
            icon.stop()

    def _on_config(self, icon=None, item=None):
        """打开配置窗口。"""
        import threading
        thread = threading.Thread(target=self._show_config_dialog, daemon=True)
        thread.start()

    def _show_config_dialog(self):
        """显示配置编辑窗口（tkinter）。"""
        try:
            import tkinter as tk
            from tkinter import ttk, filedialog, messagebox
        except ImportError:
            if _HAS_WIN32:
                ctypes.windll.user32.MessageBoxW(
                    0, "tkinter 未安装，无法显示配置窗口", "错误", 0x10
                )
            return

        root = tk.Tk()
        root.title("简幻欢自动化 - 配置")
        root.geometry("500x450")
        root.resizable(False, False)

        # 读取当前配置
        cfg = self.config

        # ── 微信路径 ──
        frm_wechat = ttk.LabelFrame(root, text="微信配置", padding=10)
        frm_wechat.pack(fill="x", padx=10, pady=5)

        wechat_path = tk.StringVar(value=cfg.get("wechat", {}).get("path", ""))
        ttk.Label(frm_wechat, text="WeChat.exe 路径:").grid(row=0, column=0, sticky="w")
        entry_wechat = ttk.Entry(frm_wechat, textvariable=wechat_path, width=40)
        entry_wechat.grid(row=0, column=1, padx=5)

        def browse_wechat():
            path = filedialog.askopenfilename(
                title="选择 WeChat.exe",
                filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")]
            )
            if path:
                wechat_path.set(path)

        ttk.Button(frm_wechat, text="浏览...", command=browse_wechat).grid(row=0, column=2)

        # ── 时间控制 ──
        frm_timing = ttk.LabelFrame(root, text="时间控制（秒）", padding=10)
        frm_timing.pack(fill="x", padx=10, pady=5)

        ad_watch = tk.StringVar(value=str(cfg.get("timing", {}).get("ad_watch_seconds", 32)))
        ttk.Label(frm_timing, text="广告观看时长:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(frm_timing, textvariable=ad_watch, width=10).grid(row=0, column=1, pady=2)
        ttk.Label(frm_timing, text="（建议32秒）").grid(row=0, column=2, sticky="w")

        check_interval = tk.StringVar(value=str(cfg.get("timing", {}).get("check_interval", 2)))
        ttk.Label(frm_timing, text="检测间隔:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(frm_timing, textvariable=check_interval, width=10).grid(row=1, column=1, pady=2)

        # ── 关键词 ──
        frm_kw = ttk.LabelFrame(root, text="关键词配置", padding=10)
        frm_kw.pack(fill="x", padx=10, pady=5)

        kw_ad = tk.StringVar(value="|".join(cfg.get("matching", {}).get("ad_keywords", ["观看广告"])))
        ttk.Label(frm_kw, text="观看广告:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(frm_kw, textvariable=kw_ad, width=30).grid(row=0, column=1, pady=2)

        kw_close = tk.StringVar(value="|".join(cfg.get("matching", {}).get("close_keywords", ["关闭"])))
        ttk.Label(frm_kw, text="关闭广告:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(frm_kw, textvariable=kw_close, width=30).grid(row=1, column=1, pady=2)

        kw_continue = tk.StringVar(value="|".join(cfg.get("matching", {}).get("continue_keywords", ["继续"])))
        ttk.Label(frm_kw, text="继续观看:").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(frm_kw, textvariable=kw_continue, width=30).grid(row=2, column=1, pady=2)

        kw_limit = tk.StringVar(value="|".join(cfg.get("matching", {}).get("limit_keywords", ["到达上限", "今日上限"])))
        ttk.Label(frm_kw, text="今日上限:").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ttk.Entry(frm_kw, textvariable=kw_limit, width=30).grid(row=3, column=1, pady=2)

        # ── 循环 ──
        frm_loop = ttk.LabelFrame(root, text="循环控制", padding=10)
        frm_loop.pack(fill="x", padx=10, pady=5)

        max_rounds = tk.StringVar(value=str(cfg.get("loop", {}).get("max_rounds", 0)))
        ttk.Label(frm_loop, text="循环次数 (0=无限):").grid(row=0, column=0, sticky="w", padx=5)
        ttk.Entry(frm_loop, textvariable=max_rounds, width=10).grid(row=0, column=1)

        # ── 按钮 ──
        frm_btn = ttk.Frame(root)
        frm_btn.pack(fill="x", padx=10, pady=10)

        def save_config():
            """保存配置到 config.yaml。"""
            try:
                cfg.setdefault("wechat", {})["path"] = wechat_path.get()
                cfg.setdefault("timing", {})["ad_watch_seconds"] = int(ad_watch.get())
                cfg.setdefault("timing", {})["check_interval"] = float(check_interval.get())
                cfg.setdefault("matching", {})["ad_keywords"] = [k.strip() for k in kw_ad.get().split("|") if k.strip()]
                cfg.setdefault("matching", {})["close_keywords"] = [k.strip() for k in kw_close.get().split("|") if k.strip()]
                cfg.setdefault("matching", {})["continue_keywords"] = [k.strip() for k in kw_continue.get().split("|") if k.strip()]
                cfg.setdefault("matching", {})["limit_keywords"] = [k.strip() for k in kw_limit.get().split("|") if k.strip()]
                cfg.setdefault("loop", {})["max_rounds"] = int(max_rounds.get())

                with open(self.config_path, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

                self.config = cfg
                messagebox.showinfo("成功", "配置已保存！")
                root.destroy()
            except Exception as e:
                messagebox.showerror("错误", f"保存失败: {e}")

        ttk.Button(frm_btn, text="保存", command=save_config).pack(side="right", padx=5)
        ttk.Button(frm_btn, text="取消", command=root.destroy).pack(side="right", padx=5)

        root.mainloop()

    def _run_engine(self):
        """在子线程中运行引擎。"""
        try:
            from core.engine import AdBotEngine
            from core import ocr

            if not ocr.init():
                logger.error("OCR 引擎初始化失败")
                self.status = "error"
                return

            self.engine = AdBotEngine(self.config)
            logger.info("===== 自动化引擎启动 =====")
            self.engine.run()
            logger.info("===== 自动化引擎停止 =====")
        except Exception as e:
            logger.error(f"引擎运行错误: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_running = False
            self.status = "idle"

    def run(self):
        """启动托盘应用。"""
        self._setup_logging()
        logger.info("简幻欢自动化托盘应用启动")

        # 创建菜单
        menu = self.pystray.Menu(
            self.pystray.MenuItem("启动自动化", self._on_start, enabled=lambda item: not self.is_running),
            self.pystray.MenuItem("只执行一轮", self._on_start_once, enabled=lambda item: not self.is_running),
            self.pystray.MenuItem("停止", self._on_stop, enabled=lambda item: self.is_running),
            self.pystray.Menu.SEPARATOR,
            self.pystray.MenuItem("配置", self._on_config),
            self.pystray.MenuItem("打开微信", self._on_open_wechat),
            self.pystray.MenuItem("查看日志", self._on_show_log),
            self.pystray.Menu.SEPARATOR,
            self.pystray.MenuItem("退出", self._on_quit),
        )

        # 创建托盘图标
        icon = self.pystray.Icon(
            "jhh-ad-bot",
            create_icon_image("idle"),
            "简幻欢自动化 - 待机",
            menu,
        )

        # 检查 pystray 后端
        try:
            icon.run()
        except Exception as e:
            logger.error(f"托盘运行错误: {e}")
            # 如果托盘失败，回退到 CLI 模式
            print(f"托盘启动失败: {e}")
            print("回退到命令行模式...")
            self._on_start()


def main():
    """托盘应用入口。"""
    # 自提权（Windows）
    if _HAS_WIN32 and not _is_admin():
        logger.info("请求管理员权限...")
        if _run_as_admin():
            return

    # 日志基础配置
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 加载配置
    config_path = resource_path("config.yaml")
    if not os.path.exists(config_path):
        print(f"配置文件不存在: {config_path}")
        input("按 Enter 退出...")
        sys.exit(1)

    config = load_config(config_path)

    # 启动托盘应用
    app = TrayApp(config, config_path)
    app.run()


if __name__ == "__main__":
    main()
