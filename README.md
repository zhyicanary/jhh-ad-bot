# jhh-ad-bot — 简幻欢看广告积分助手

自动观看简幻欢微信小程序中的广告，获取积分。

## 原理

通过 OpenCV 模板匹配识别屏幕上的按钮，模拟鼠标点击自动完成"看广告→等待→关闭"循环。

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│ CHECK_AD ├────>│ WATCHING ├────>│ CLOSE_AD │
│ 找广告按钮 │     │ 等待播放  │     │ 找关闭按钮 │
└──────────┘     └──────────┘     └──────────┘
       ^                              │
       └──────────────────────────────┘
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 准备模板截图

将按钮截图放到 `templates/` 目录：

| 文件 | 说明 |
|------|------|
| `templates/ad_button.png` | "看广告"按钮截图 |
| `templates/close_button.png` | 广告关闭按钮截图 |

> 截取模板时尽量只截取按钮本身，背景越干净匹配效果越好。

### 3. 运行

```bash
python main.py                    # 默认配置，持续运行
python main.py --once             # 只执行一轮
python main.py -v                 # 开启详细日志
python main.py -c myconfig.yaml   # 使用自定义配置
```

## 配置说明

编辑 `config.yaml` 调整参数：

```yaml
templates:
  ad_button: "templates/ad_button.png"     # 广告按钮模板路径
  close_button: "templates/close_button.png" # 关闭按钮模板路径

timing:
  ad_watch_seconds: 30        # 每次观看广告时长（秒）
  check_interval: 2           # 检测间隔（秒）
  post_click_delay: 1.5       # 点击后等待渲染（秒）
  close_retry_interval: 3     # 关闭按钮重试间隔（秒）

matching:
  confidence_threshold: 0.75  # 模板匹配置信度（0-1）
  scale_steps: 5              # 多尺度匹配步数
  scale_range: [0.8, 1.2]    # 缩放范围

window:
  title_keyword: "微信"        # 目标窗口标题关键词
  auto_focus: true             # 自动激活窗口

loop:
  max_rounds: 0                # 最大循环次数（0=无限）
  stop_on_template: ""         # 检测到此模板时停止
```

## 打包 exe

推送 `v*` 标签触发 GitHub Actions 自动打包：

```bash
git tag v1.0.0
git push origin v1.0.0
```

打包产物会发布到 GitHub Releases。

## 项目结构

```
jhh-ad-bot/
├── main.py                  # 入口
├── config.yaml              # 配置文件
├── requirements.txt         # Python 依赖
├── core/
│   ├── engine.py            # 状态机引擎
│   ├── capture.py           # 截图模块
│   ├── vision.py            # 图像识别模块
│   └── action.py            # 鼠标/窗口操控模块
├── templates/               # 模板图片目录
└── .github/workflows/
    └── build.yml            # 自动打包工作流
```
