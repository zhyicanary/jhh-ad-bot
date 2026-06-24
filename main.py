#!/usr/bin/env python3
"""wechat-ad-bot - 微信小程序广告自动化

用法:
    python main.py                    # 默认配置启动
    python main.py -c config.yaml     # 指定配置文件
    python main.py --once             # 只执行一轮后停止
"""

import argparse
import logging
import sys
import yaml

from core.engine import AdBotEngine


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="微信小程序广告自动化")
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

    # 加载配置
    config = load_config(args.config)
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
