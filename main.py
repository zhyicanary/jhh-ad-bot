#!/usr/bin/env python3
"""jhh-ad-bot - 简幻欢自动化助手

自动完成简幻欢微信小程序的签到和观看广告任务：
  1. 激活微信窗口
  2. 关闭订阅提醒弹窗
  3. 签到
  4. 循环观看广告（点击→关闭插屏→等30s→关闭→领奖励）
  5. 达到每日上限后自动停止

用法:
    python main.py                    # 直接运行
    python main.py -c config.yaml     # 指定配置文件
    python main.py --once             # 只执行一轮后停止
    python main.py -v                 # 详细日志
    python main.py --tray             # 系统托盘模式（可选）
"""

import argparse
import ctypes
import logging
import os
import sys

import yaml


def resource_path(relative_path: str) -> str:
    """获取打包后资源文件的真实路径。

    PyInstaller 打包后文件解压在 sys._MEIPASS 临时目录，
    直接使用相对路径会找不到文件，需通过此函数转换。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def get_config_path(config_name: str = "config.yaml") -> str:
    """获取配置文件路径。

    优先级：
    1. exe 同目录（用户可修改）
    2. 打包内置的模板（sys._MEIPASS）

    如果 exe 同目录没有 config.yaml，从内置模板复制一份过去，
    这样用户可以直接在同目录修改配置。
    """
    # exe 所在目录（打包后）或脚本所在目录（开发时）
    if hasattr(sys, "frozen"):
        exe_dir = os.path.dirname(sys.executable)
    else:
        exe_dir = os.path.dirname(os.path.abspath(__file__))

    external_path = os.path.join(exe_dir, config_name)
    internal_path = resource_path(config_name)

    # exe 同目录有配置文件，直接用
    if os.path.exists(external_path):
        return external_path

    # 没有就从内置模板复制一份到 exe 同目录
    if os.path.exists(internal_path):
        import shutil
        try:
            shutil.copy2(internal_path, external_path)
            print(f"已生成默认配置文件: {external_path}")
            print(f"请修改此文件后重新运行程序。")
            return external_path
        except Exception:
            # 复制失败就直接用内置的
            return internal_path

    return external_path


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def is_admin() -> bool:
    """检查是否以管理员权限运行。"""
    if os.name != "nt":
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    """以管理员权限重启程序。"""
    if os.name != "nt" or is_admin():
        return False
    try:
        params = " ".join([f'"{arg}"' for arg in sys.argv])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
        sys.exit(0)
    except Exception:
        return False


def run_cli(config: dict):
    """直接运行模式。"""
    from core.engine import AdBotEngine
    from core import ocr

    # 设置控制台标题
    if os.name == "nt":
        ctypes.windll.kernel32.SetConsoleTitleW("简幻欢自动化助手")

    # 初始化 OCR
    if not ocr.init():
        print("警告: OCR 引擎初始化失败，部分功能可能不可用")

    engine = AdBotEngine(config)
    try:
        engine.run()
    except KeyboardInterrupt:
        engine.stop()
        print("\n用户中断，正在退出...")
    except Exception as e:
        print(f"\n运行出错: {e}")
        import traceback
        traceback.print_exc()

    # 运行结束后暂停，让用户看到结果
    print("\n" + "=" * 40)
    print(f"运行结束。共 {engine.stats.rounds} 轮, 观看广告 {engine.stats.ads_watched} 次, 跳过 {engine.stats.ads_skipped} 次")
    if os.name == "nt":
        input("\n按 Enter 退出...")


def run_tray(config: dict, config_path: str):
    """系统托盘模式运行。"""
    import tray
    app = tray.TrayApp(config, config_path)
    app.run()


def main():
    parser = argparse.ArgumentParser(description="简幻欢自动化助手 - 签到+看广告积分")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    parser.add_argument("--tray", action="store_true", help="系统托盘模式（可选）")
    parser.add_argument("--admin", action="store_true", help="请求管理员权限")
    args = parser.parse_args()

    # 默认直接运行，--tray 才走托盘模式
    use_tray = args.tray

    # 管理员权限
    if os.name == "nt" and not is_admin():
        # 打包后自动请求管理员权限
        if hasattr(sys, "frozen") or args.admin:
            run_as_admin()
            return

    # 日志配置
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 加载配置（exe 同目录优先，不存在则从内置模板生成）
    config_path = get_config_path(args.config) if args.config == "config.yaml" else args.config
    if not os.path.exists(config_path):
        print(f"配置文件不存在: {config_path}")
        if os.name == "nt":
            input("按 Enter 退出...")
        sys.exit(1)

    config = load_config(config_path)

    if args.once:
        config.setdefault("loop", {})["max_rounds"] = 1

    # 选择运行模式
    if use_tray:
        run_tray(config, config_path)
    else:
        run_cli(config)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        if os.name == "nt":
            input("\n程序出错，按 Enter 退出...")
        sys.exit(1)
