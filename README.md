# 简幻欢自动化助手 (jhh-ad-bot)

自动完成简幻欢微信小程序的签到和观看广告任务，通过 UI Automation + OCR 识别界面元素，模拟鼠标操作完成全流程自动化。

## 功能

- 自动打开微信客户端（支持配置路径 / 全盘搜索）
- 自动通过微信搜索框打开简幻欢小程序
- 自动关闭订阅提醒弹窗
- 自动点击签到标签导航
- 自动点击观看广告，循环执行直到每日上限
- 自动处理插屏弹窗广告（点 × 关闭）
- 自动处理广告中断弹窗（点"继续"继续观看）
- 自动等待奖励冒泡文字后继续下一轮
- 系统托盘后台运行，GUI 配置窗口

## 快速开始

### 方式一：打包版（推荐）

1. 从 [Releases](https://github.com/zhyicanary/jhh-ad-bot/releases) 下载 `jhh-ad-bot.exe`
2. 放到一个空文件夹中
3. 双击运行（会弹出 UAC 请求管理员权限）
4. 首次运行会在 exe 同目录生成 `config.yaml`，按需修改
5. 右下角托盘图标右键 → "启动自动化"

### 方式二：源码运行

```bash
# 安装依赖
pip install -r requirements.txt

# CLI 模式
python main.py

# 托盘模式
python main.py --tray

# 只执行一轮
python main.py --once

# 请求管理员权限
python main.py --admin
```

## 配置

首次运行自动在 exe 同目录（或源码根目录）生成 `config.yaml`：

```yaml
# 微信配置
wechat:
  path: ""    # WeChat.exe 路径，留空则自动搜索

# 时间控制
timing:
  ad_watch_seconds: 32    # 广告观看时长（秒）
  check_interval: 2       # 检测间隔（秒）

# 关键词
matching:
  ad_keywords: ["观看广告"]
  close_keywords: ["关闭"]
  continue_keywords: ["继续"]
  limit_keywords: ["到达上限", "今日上限"]

# 循环控制
loop:
  max_rounds: 0    # 0=无限循环
```

也可以通过托盘右键菜单 → "配置" 在 GUI 窗口中修改。

## 状态机流程

程序运行时按以下状态顺序执行：

```
OPEN_WECHAT          打开微信客户端
       ↓
OPEN_MINI_PROGRAM    搜索并打开简幻欢小程序
       ↓
INIT                 激活目标窗口
       ↓
DISMISS_SUBSCRIBE    关闭订阅提醒弹窗
       ↓
CHECK_IN             点击"签到"标签导航
       ↓
CLICK_AD             点击"观看广告"
       ↓
DISMISS_INTERSTITIAL 处理插屏弹窗广告（点 ×）
       ↓
WATCHING_AD          观看广告（等待 30s）
       ↓               ↑ 中断弹窗点"继续"
CLOSE_AD             关闭广告
       ↓
WAITING_REWARD       等待"加载中"+"获得签到奖励"
       ↓
CLICK_AD             ← 循环回到观看广告
       ↓
STOP                 检测到"今日上限"，退出
```

## 技术架构

| 模块 | 技术 | 说明 |
|------|------|------|
| `core/engine.py` | 11 状态有限状态机 | 驱动完整自动化流程 |
| `core/ocr.py` | RapidOCR (ONNX Runtime) | 离线文字识别，精确匹配优先 |
| `core/action.py` | Win32 SendInput | 鼠标轨迹模拟，DPI 感知 |
| `core/capture.py` | PrintWindow API | 窗口专属截图（被遮挡也能截取） |
| `core/uia.py` | UI Automation | 无障碍接口直接调用 InvokePattern |
| `tray.py` | pystray + tkinter | 系统托盘 + GUI 配置窗口 |

### 三层点击策略

1. **UIA 名称搜索 + InvokePattern** — 对 Chromium 窗口最可靠，不需要元素可见
2. **OCR 定位 + ControlFromPoint** — 对无 accessible name 的元素
3. **OCR 坐标点击** — SendInput 模拟鼠标，兜底方案

## 打包

```bash
# Windows
build.bat

# 或手动打包
pyinstaller --onefile --name "jhh-ad-bot" --uac-admin \
    --add-data "config.yaml;." \
    --collect-all cv2 --collect-all rapidocr_onnxruntime \
    --collect-all uiautomation --collect-all comtypes --collect-all pystray \
    main.py
```

推送 `v*` 标签会触发 GitHub Actions 自动打包并发布 Release。

## 托盘菜单

| 菜单项 | 功能 |
|--------|------|
| 启动自动化 | 后台线程运行引擎，图标变绿 |
| 只执行一轮 | 跑一轮后自动停止 |
| 停止 | 停止引擎，图标变灰 |
| 配置 | 弹出 GUI 窗口编辑配置 |
| 打开微信 | 手动打开微信客户端 |
| 查看日志 | 弹窗显示最近 100 行日志 |
| 退出 | 停止引擎并退出 |

托盘图标颜色：灰色=待机，绿色=运行中，红色=错误。

## 项目结构

```
jhh-ad-bot/
├── core/
│   ├── engine.py          # 状态机引擎
│   ├── ocr.py             # OCR 文字识别
│   ├── capture.py         # 屏幕截图
│   ├── action.py          # 鼠标/窗口操控
│   └── uia.py             # UI Automation 接口
├── main.py                # 入口文件
├── tray.py                # 系统托盘应用
├── config.yaml            # 配置文件
├── requirements.txt       # Python 依赖
├── build.bat              # Windows 打包脚本
└── .github/workflows/
    └── build.yml          # GitHub Actions 自动打包
```

## 依赖

- Python 3.10+
- Windows 10/11
- opencv-python, rapidocr-onnxruntime, uiautomation, pystray 等
