---
title: 技术栈与核心原理
icon: microchip
order: 1
author: Shasnow
---

# SRA 技术栈与核心原理

本文档深入讲解 SRA 实现游戏自动化的核心技术原理，包括截图、图像识别、OCR 文字识别、模拟操作以及窗口激活等关键环节。

## 一句话概括

SRA **不是**用 AI / 深度学习来做概率识别。它的核心是 **OpenCV 模板匹配 + RapidOCR 文字识别 + Win32 API 模拟操作**，通过"截图 → 找图/找字 → 点击 → 等待变化 → 再截图"这样的循环来实现自动化。

---

## 完整技术栈一览

| 层级 | 技术方案 | 用途 |
|---|---|---|
| 开发语言 | **Python 3.12+**（后端核心）、**C# .NET**（GUI 前端） | 跨模块逻辑编排 |
| 图像识别 | **OpenCV 模板匹配**（`matchTemplate`） | 在截图中定位预存的按钮/图标模板 |
| 文字识别 | **RapidOCR**（ONNX Runtime） | 识别屏幕上动态出现的文字 |
| 截图 | **Win32 API**（`BitBlt` / `PrintWindow`） | 捕获游戏窗口图像 |
| 窗口操作 | **Win32 API**（`FindWindow`、`SetForegroundWindow`、`ShowWindow`） | 查找、激活、置前游戏窗口 |
| 模拟输入 | **Win32 `SendInput` / `mouse_event` / `keybd_event`** | 模拟鼠标点击和键盘按键 |
| 图形界面 | **PySide6（Qt）** | 桌面 GUI |
| 文档系统 | **VuePress + VuePress Theme Hope** | 本文档站 |

---

## 核心流程：截图 → 识别 → 操作

SRA 的所有自动化任务，本质都是一个无限循环：

```
┌─────────────────────────────────────────────────────┐
│                    任务循环                            │
│                                                      │
│   ① 激活窗口 → ② 截图 → ③ 识别目标 → ④ 执行操作     │
│                                ↓                     │
│   ⑤ 等待界面变化（再截图 → 再识别 → ...）            │
│                                ↓                     │
│   ⑥ 重复直到任务完成或被停止                          │
└─────────────────────────────────────────────────────┘
```

---

## ① 窗口激活（强制前台）

### 为什么需要强制前台

Windows 系统限制：只有**前台窗口**才能正常接收模拟输入和完成截图。SRA 在执行任务前，必须确保游戏窗口处于活动状态。

### 技术实现

```
┌──────────────────────────────────────────────┐
│              窗口激活流程                       │
│                                                │
│  FindWindow("崩坏：星穹铁道")                   │
│        ↓ （获取窗口句柄 HWND）                   │
│  is_window_active()                            │
│        ↓ （检查是否已是前台）                     │
│  SetForegroundWindow(hwnd)                     │
│        ↓                                       │
│  辅助手段（如被 Windows 阻止时）：                │
│    • ShowWindow(hwnd, SW_RESTORE)              │
│    • SetWindowPos(hwnd, HWND_TOP, ...)         │
│    • AttachThreadInput(...) 绕过前台锁          │
│        ↓                                       │
│  失败降级 → 任务栏图标闪烁，提示用户手动激活      │
└──────────────────────────────────────────────┘
```

### 权限要求

- SRA **必须**以**管理员权限**运行（`--no-admin` 可禁用但功能受限）
- 原因：Windows 禁止低权限进程将高权限窗口置前
- 声明在文档中：*"SRA 拥有多种敏感动作（包括但不限于获取管理员权限、读写注册表、操作屏幕）"*

### 已知限制

当用户正在操作 SRA 窗口（而非游戏窗口）时，Windows 的**前台锁机制**（Foreground Lock）会拒绝 `SetForegroundWindow` 调用，此时游戏窗口只能在任务栏闪烁，等待用户手动点击激活。这是一个 Windows 安全设计，无法绕过。

---

## ② 截图

### 实现方式

`IOperator.screenshot()` 方法封装了底层截图逻辑：

| 方式 | 适用场景 |
|---|---|
| `BitBlt`（Win32 GDI） | 窗口处于前台时最常用，快速 |
| `PrintWindow`（Win32） | 窗口被遮挡时备用 |
| DXGI（DirectX） | 某些特殊渲染模式 |

截图支持指定区域裁剪（归一化坐标 `0~1`）：
```python
# 只截取屏幕的左上四分之一区域
screenshot(from_x=0, from_y=0, to_x=0.5, to_y=0.5)
```

---

## ③ 图像识别

### ❌ SRA 没有用 AI / 深度学习

这是最常见的一个误解。SRA 做的不是"识别出截图上有什么物体"（那是 AI 目标检测），而是**找一张小图在大图的哪个位置**（模板匹配）。

### ✅ OpenCV 模板匹配（`cv2.matchTemplate`）

```
截图中某块区域 vs 预存的模板图片
              ↓
逐像素滑动计算相关性（归一化相关系数）
              ↓
找到分数最高且 ≥ confidence 的位置
              ↓
返回 Box (left, top, width, height)
```

具体方法：

| 方法 | 作用 | 返回 |
|---|---|---|
| `locate("button.png")` | 查找单张模板图 | `Box \| None` |
| `locate_all("icon.png")` | 查找所有匹配位置 | `list[Box]` |
| `locate_any(["a.png","b.png"])` | 多图同时查找，返回最先匹配到的 | `(index, Box)` |

`confidence`（置信度阈值，默认 `0.9`）是模板匹配的相似度门槛，**不是 AI 概率**。

### 模板图片来源

所有模板图片（按钮、图标、UI 元素等）都是**预先截取并存储在 `res/` 目录下**的静态 PNG 图片。SRA 不支持也不需要通过"截图然后分析概率"来做判断。

---

## ④ OCR 文字识别

### RapidOCR

对于没有固定模板的文字内容，使用 **RapidOCR**（基于 ONNX Runtime）：

| 方法 | 作用 |
|---|---|
| `ocr()` | 识别区域内的全部文字 |
| `ocr_match("开始挑战")` | 查找指定文字的位置 |
| `ocr_match_any(["领取","完成"])` | 查找任意指定文字 |

OCR 在以下场景中使用：
- 识别界面上的数字（体力值、等级）
- 读取动态文字内容（登录提示、任务说明）
- 缓存/购买界面中的文本按钮

---

## ⑤ 模拟操作

定位到目标后，通过 **Win32 `SendInput`** 执行系统级模拟操作：

| 方法 | 技术底层 | 用途 |
|---|---|---|
| `click_point(x, y)` | `SendInput` 模拟鼠标 | 点击坐标 |
| `click_box(box)` | 计算 Box 中心 + `SendInput` | 点击识别到的区域 |
| `click_img("btn.png")` | 找图 + 点击一步完成 | 快捷操作 |
| `press_key("enter")` | `SendInput` 模拟键盘 | 键盘操作 |
| `drag_to(x1,y1,x2,y2)` | 鼠标按下 → 移动 → 释放 | 拖拽操作 |
| `copy()` / `paste()` | 剪贴板 API | 文本复制粘贴 |

---

## ⑥ 等待 / 轮询机制

为了应对游戏界面加载延迟、动画过渡等不确定性，SRA 采用**轮询等待**模式：

```python
# 逻辑等价于：
def wait_img(template, timeout=10, interval=0.5):
    start = time.time()
    while time.time() - start < timeout:
        box = locate(template)  # 截图 + 模板匹配
        if box:
            return box
        sleep(interval)
    return None
```

常见等待方法：

| 方法 | 作用 |
|---|---|
| `wait_img("loading_done.png")` | 等待某张图片出现 |
| `wait_any_img(["win.png","lose.png"])` | 等待多张图片中的任意一张 |
| `wait_ocr("加载完成")` | 等待某段文字出现 |
| `wait_any([func1, func2])` | 通用条件等待 |
| `do_while(action, condition)` | 条件满足时重复执行 |

这些等待方法是整个自动化系统能稳定运行的关键。

---

## 完整任务示例

一个典型的清体力任务流程：

```python
class TrailblazePowerTask(BaseTask):
    def run(self) -> bool:
        # ① 激活游戏窗口
        if not self.operator.is_window_active():
            self.operator.activate_window()  # SetForegroundWindow

        # ② 等待进入游戏主界面
        box = self.operator.wait_img("main_menu.png", timeout=15)
        if not box:
            return False

        # ③ 点击"挑战"按钮
        self.operator.click_img("challenge_button.png")

        # ④ 等待战斗开始
        box = self.operator.wait_img("in_battle.png", timeout=10)
        if not box:
            return False

        # ⑤ 战斗循环（自动战斗，等待结束）
        self.operator.wait_img("battle_end.png", timeout=180)
        self.operator.click_img("confirm_reward.png")

        # ⑥ 继续下一次...
        return True
```

---

## 总结：SRA 不是什么，是什么

| ❌ 误解 | ✅ 事实 |
|---|---|
| 用 AI / 深度学习识别截图 | 用 OpenCV 模板匹配 + RapidOCR |
| "概率识别"（像图像分类那样） | 像素级的相关性匹配，`confidence` 是相似度门槛 |
| 识别屏幕上的任意物体 | 只能匹配预先准备好的模板图片 |
| 不需要模板就能操作 | 每个可识别的 UI 元素都需要一张对应的模板图 |
| 普通权限就能稳定运行 | 需要管理员权限才能强制窗口前台 |

SRA 本质上是一个**经典的计算机视觉 + GUI 自动化工具**，与 PyAutoGUI、SikuliX、AutoIt 等属于同一类技术路线。它的优势不在于"智能"，而在于对《崩坏：星穹铁道》这个特定游戏的深度适配和稳定可靠的轮询调度机制。
