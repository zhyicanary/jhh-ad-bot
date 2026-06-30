# jhh-ad-bot — 简幻欢看广告积分助手

自动观看简幻欢微信小程序中的广告，获取积分。

## 下载使用（推荐）

从 [Releases](https://github.com/zhyicanary/jhh-ad-bot/releases) 下载最新 `jhh-ad-bot.exe`：

1. 新建一个文件夹，把 `jhh-ad-bot.exe` 放进去
2. 在同目录下创建 `templates/` 文件夹
3. **准备模板截图**（最重要的一步）：
   - 打开微信 → 打开简幻欢小程序 → 到"看广告"按钮出现的界面
   - 截图 → 只把按钮本身裁切出来 → 保存为 `templates/ad_button.png`
   - 播放广告后关闭按钮出现时同样操作 → 保存为 `templates/close_button.png`
4. 在同目录下放 `config.yaml`（可改配置）
5. 双击 exe 运行，微信窗口保持在前台

> 模板图必须是**你自己电脑上截的**，背景越干净匹配效果越好。

## 原理

通过 OpenCV 模板匹配识别屏幕上的按钮，模拟鼠标点击自动完成"看广告→等待→关闭"循环。

```
┌───────────┐     ┌───────────┐     ┌───────────┐
│ CHECK_AD  │────>│  WATCHING │────>│  CLOSE_AD │
│ 找广告按钮  │     │  等待播放   │     │ 找关闭按钮  │
└───────────┘     └───────────┘     └───────────┘
       ^                                  │
       └──────────────────────────────────┘
```

## 从源码运行

```bash
pip install -r requirements.txt
python main.py                    # 持续运行
python main.py --once             # 只执行一轮
python main.py -v                 # 详细日志
```

## 配置说明

编辑 `config.yaml` 调整参数：

```yaml
templates:
  ad_button: "templates/ad_button.png"       # 广告按钮模板路径
  close_button: "templates/close_button.png" # 关闭按钮模板路径

timing:
  ad_watch_seconds: 30          # 每次观看广告时长（秒）
  check_interval: 2             # 检测间隔（秒）
  post_click_delay: 1.5         # 点击后等待渲染（秒）
  close_retry_interval: 3       # 关闭按钮重试间隔（秒）

matching:
  confidence_threshold: 0.75    # 模板匹配置信度（0-1）
  close_confidence: 0.6         # 关闭按钮模板匹配阈值（回退方案）
  scale_steps: 5                # 多尺度匹配步数
  scale_range: [0.8, 1.2]      # 缩放范围
  ocr_enabled: true             # 启用 Windows 原生 OCR（优先于模板匹配）
  ocr_close_keywords: ["关闭", "×", "跳过", "关闭广告"] # OCR 查找的关键词

window:
  title_keyword: "微信"          # 目标窗口标题关键词
  auto_focus: true               # 每次点击前自动激活窗口

loop:
  max_rounds: 0                  # 最大循环次数（0=无限）
```

## 构建 exe

推送 `v*` 标签触发 GitHub Actions 自动打包：

```bash
git tag v0.0.9
git push origin v0.0.9
```

打包产物自动发布到 GitHub Releases。

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
