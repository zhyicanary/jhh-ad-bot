# jhh-ad-bot — 简幻欢自动化助手

自动完成简幻欢微信小程序的**签到**和**观看广告**任务，获取积分。

## 工作流程

```
INIT (激活窗口)
  → DISMISS_SUBSCRIBE (关闭订阅提醒弹窗)
  → CHECK_IN (签到)
  → CLICK_AD (点击"观看广告")
      ↻ 处理插屏广告弹窗(×按钮)
  → WATCHING_AD (等待30秒)
      ↻ 检测"暂未获得奖励"弹窗 → 点击"继续"
  → CLOSE_AD (点击"关闭"退出广告)
  → WAITING_REWARD (等待"加载中"→"获得积分")
      ↑                              ↓
      └──────── 循环直到每日上限 ──────┘
```

## 下载使用

从 [Releases](https://github.com/zhyicanary/jhh-ad-bot/releases) 下载最新 `jhh-ad-bot.exe`：

1. 新建文件夹，放入 `jhh-ad-bot.exe` 和 `config.yaml`
2. 打开微信，进入简幻欢小程序
3. 双击 `jhh-ad-bot.exe` 运行
4. 保持微信窗口在前台

> 无需模板截图，全部通过 OCR 文字识别定位按钮。

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
# 目标窗口
window:
  title_keyword: "简幻欢|WeChatAppEx|微信"
  auto_focus: true

# 时间控制
timing:
  ad_watch_seconds: 32    # 广告观看时长(秒)
  ad_click_wait: 2        # 点击后等待(秒)
  check_interval: 2       # 检测间隔(秒)

# OCR 关键词（可按需调整）
matching:
  ad_keywords: ["观看广告", "看广告"]
  close_keywords: ["关闭", "关闭广告"]
  popup_close_keywords: ["×", "X"]
  max_ad_not_found: 5     # 连续未找到广告多少次后停止

# 循环
loop:
  max_rounds: 0           # 0=无限
```

## 技术架构

| 模块 | 作用 |
|------|------|
| `core/engine.py` | 8状态有限状态机，驱动完整自动化流程 |
| `core/ocr.py` | RapidOCR 文字识别，定位屏幕上的按钮文字 |
| `core/action.py` | Win32 SendInput 鼠标模拟 + DPI 感知 + 轨迹移动 |
| `core/capture.py` | mss 高性能屏幕截图 |

### 关键设计

- **全 OCR 识别**：不依赖模板图片，通过文字识别定位所有 UI 元素
- **DPI 感知**：启用 Per-Monitor DPI Awareness，截图与光标坐标系一致
- **鼠标轨迹模拟**：ease-out cubic 缓动移动，避免瞬移被检测忽略
- **点击验证**：关键步骤点击后截图验证，失败自动重试
- **插屏弹窗处理**：点击"观看广告"后检测插屏弹窗，自动关闭×再重新点击
- **广告中断保护**：30s观看期间检测"暂未获得奖励"弹窗，自动点击"继续"
- **每日上限检测**：连续多次找不到"观看广告"按钮时自动停止

## 构建 exe

推送 `v*` 标签触发 GitHub Actions 自动打包：

```bash
git tag v1.0.0
git push origin v1.0.0
```

## 项目结构

```
jhh-ad-bot/
├── main.py                  # 入口
├── config.yaml              # 配置文件
├── requirements.txt         # Python 依赖
├── core/
│   ├── engine.py            # 8状态机引擎
│   ├── ocr.py               # OCR 文字识别
│   ├── capture.py           # 屏幕截图
│   ├── action.py            # 鼠标/窗口操控
│   └── vision.py            # OpenCV 模板匹配(备用)
└── .github/workflows/
    └── build.yml            # 自动打包工作流
```
