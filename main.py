#!/usr/bin/env python3
"""jhh-ad-bot - 简幻欢看广告积分助手

为简幻欢小程序自动观看广告获取积分。

用法:
    python main.py                    # 默认配置启动
    python main.py -c config.yaml     # 指定配置文件
    python main.py --once             # 只执行一轮后停止
"""

import argparse
import logging
import os
import sys

import yaml

from core.engine import AdBotEngine


def resource_path(relative_path: str) -> str:
    """获取打包后资源文件的真实路径。

    PyInstaller 打包后文件解压在 sys._MEIPASS 临时目录，
    直接使用相对路径会找不到文件，需通过此函数转换。
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), relative_path)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="简幻欢看广告积分助手")
    parser.add_argument("-c", "--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--once", action="store_true", help="只执行一轮")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")
    args = parser.parse_args()

    # 日志配置
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # 加载配置（支持 PyInstaller 打包路径）
    config_path = resource_path(args.config)
    config = load_config(config_path)

    # 将所有模板路径解析为绝对路径（支持 PyInstaller 打包路径）
    for key, path in config.get("templates", {}).items():
        config["templates"][key] = resource_path(path)

    if args.once:
        config.setdefault("loop", {})["max_rounds"] = 1

    # 启动引擎
    engine = AdBotEngine(config)
    try:
        engine.run()
    except KeyboardInterrupt:
        engine.stop()
        print("\n用户中断，正在退出...")


if __name__ == "__main__":
    main()
