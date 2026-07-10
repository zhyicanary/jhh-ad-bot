"""UI Automation 模块 - 通过无障碍接口直接操作 UI 元素。

使用 InvokePattern 直接调用按钮处理程序，完全绕过鼠标模拟。
对 Chromium 窗口（微信小程序）特别有效，因为标准 Win32 点击方法
无法穿透 Chromium 的输入管道。

核心原理：
  - ControlFromHandle(hwnd) 获取窗口的 UIA 元素
  - 遍历 UIA 树查找名称匹配的元素（包括 TextControl/GroupControl）
  - 过滤无效元素：BoundingRectangle 为 (0,0,0,0) 的不可见元素、
    名称远长于关键词的描述性文字、系统按钮（Minimize/Close 等）
  - 按控件类型优先级排序：ButtonControl > HyperlinkControl >
    GroupControl > ListItemControl > 其他 > TextControl
    （TextControl 排最后，因为它通常只是文字标签不是按钮）
  - InvokePattern.Invoke() 直接调用（首选）
  - LegacyIAccessiblePattern.DoDefaultAction() 用于无 InvokePattern
    的可点击元素（如 GroupControl 按钮）
  - ControlFromPoint(x, y) 用于无 accessible name 的元素
"""

import logging
from ctypes import wintypes

logger = logging.getLogger(__name__)

_UIA_AVAILABLE = False
try:
    import uiautomation as ua
    _UIA_AVAILABLE = True
    logger.info("uiautomation 模块就绪")
except ImportError:
    logger.warning("uiautomation 未安装，UIA 不可用，将回退到坐标点击")

# 系统按钮名称（标题栏按钮），搜索时需要跳过，防止误关窗口
_SYSTEM_BUTTON_NAMES = frozenset({
    "Minimize", "Maximize", "Close", "System",
    "最小化", "最大化", "关闭",
})

# 控件类型优先级（数字越小优先级越高）
# ButtonControl/HyperlinkControl 最可能有 InvokePattern
# GroupControl 可能是 Chromium 中的可点击 div
# TextControl 排最后，通常只是文字标签
_CONTROL_PRIORITY = {
    "ButtonControl": 0,
    "HyperlinkControl": 1,
    "GroupControl": 2,
    "ListItemControl": 3,
    "MenuItemControl": 4,
    "TabItemControl": 5,
    "CheckBoxControl": 6,
    "RadioButtonControl": 7,
    "ComboBoxControl": 8,
    "PaneControl": 9,
    "TextControl": 10,
}
_DEFAULT_PRIORITY = 5


def is_available() -> bool:
    """UIA 是否可用。"""
    return _UIA_AVAILABLE


def _is_system_button(elem) -> bool:
    """检查是否为窗口标题栏的系统按钮（Minimize/Maximize/Close 等）。"""
    try:
        name = (elem.Name or "").strip()
        ctype = elem.ControlTypeName
        if ctype in ("ButtonControl", "MenuItemControl"):
            if name in _SYSTEM_BUTTON_NAMES:
                return True
        try:
            parent = elem.GetParent()
            if parent and parent.ControlTypeName == "MenuBarControl":
                return True
        except Exception:
            pass
    except Exception:
        pass
    return False


def _has_valid_rect(elem) -> bool:
    """检查元素是否有有效的 BoundingRectangle（可见且非零大小）。"""
    try:
        rect = elem.BoundingRectangle
        return rect.width() > 0 and rect.height() > 0
    except Exception:
        return False


def _is_description_text(elem_name: str, keyword: str, exact: bool) -> bool:
    """检查元素名称是否为描述性文字而非按钮文字。"""
    if exact:
        return False
    if len(keyword) >= 10:
        return False
    if len(elem_name) > len(keyword) * 3:
        return True
    return False


def _control_priority(elem) -> int:
    """获取控件类型的优先级（数字越小越优先）。"""
    try:
        return _CONTROL_PRIORITY.get(elem.ControlTypeName, _DEFAULT_PRIORITY)
    except Exception:
        return _DEFAULT_PRIORITY


def _get_window(hwnd: int):
    """通过窗口句柄获取 UIA 元素。"""
    if not _UIA_AVAILABLE:
        return None
    try:
        return ua.ControlFromHandle(hwnd)
    except Exception as e:
        logger.debug(f"ControlFromHandle({hwnd}) 失败: {e}")
        return None


def find_and_invoke(hwnd: int, name: str, exact: bool = False) -> bool:
    """查找名称匹配的元素并调用。

    过滤规则：
      - 跳过系统按钮（Minimize/Maximize/Close）
      - 跳过 BoundingRectangle 为 (0,0,0,0) 的不可见元素
      - 跳过名称远长于关键词的描述性文字（非精确匹配时）

    排序规则：
      - 按控件类型优先级排序（ButtonControl > HyperlinkControl >
        GroupControl > ... > TextControl）
      - 同类型按名称长度排序（短的优先）

    调用策略（按优先级）：
      1. InvokePattern.Invoke() — 对 ButtonControl/HyperlinkControl 最可靠
      2. LegacyIAccessiblePattern.DoDefaultAction() — 对 GroupControl 等
         无 InvokePattern 的可点击元素；TextControl 仅当有 DefaultAction 时
      3. Click() — 模拟鼠标点击（仅当元素有有效 BoundingRectangle 时）

    Args:
        hwnd: 目标窗口句柄
        name: 要匹配的元素名称
        exact: True=精确匹配, False=子串匹配

    Returns:
        True 表示成功调用了某个操作
    """
    if not _UIA_AVAILABLE:
        return False

    win = _get_window(hwnd)
    if win is None:
        logger.warning(f"UIA: 无法获取窗口元素 (hwnd={hwnd})")
        return False

    matched = _find_elements_by_name(win, name, exact)
    if not matched:
        logger.info(f"UIA: 未找到元素包含 '{name}'")
        return False

    # 按控件类型优先级 + 名称长度排序
    matched.sort(key=lambda e: (_control_priority(e), len(e.Name or "")))

    for i, e in enumerate(matched):
        logger.info(f"  匹配#{i}: '{e.Name}' ({e.ControlTypeName}) prio={_control_priority(e)}")

    # 策略1: 优先尝试 InvokePattern
    for elem in matched:
        if _try_invoke(elem):
            logger.info(f"UIA Invoke: '{elem.Name}' ({elem.ControlTypeName})")
            return True

    # 策略2: 尝试 LegacyIAccessiblePattern
    # 对非 TextControl 直接调用 DoDefaultAction（不检查 DefaultAction）
    # 对 TextControl 只在有 DefaultAction 时才调用（避免误操作描述文字）
    for elem in matched:
        ctype = ""
        try:
            ctype = elem.ControlTypeName
        except Exception:
            pass
        if ctype == "TextControl":
            if _try_legacy_safe(elem):
                logger.info(f"UIA Legacy(safe): '{elem.Name}' ({ctype})")
                return True
        else:
            if _try_legacy(elem):
                logger.info(f"UIA Legacy: '{elem.Name}' ({ctype})")
                return True

    # 策略3: 尝试 Click（模拟鼠标，仅对有有效 BoundingRectangle 的元素）
    for elem in matched:
        if _try_click(elem):
            logger.info(f"UIA Click: '{elem.Name}' ({elem.ControlTypeName})")
            return True

    logger.warning(f"UIA: {len(matched)} 个匹配元素均无法调用")
    return False


def invoke_at_point(screen_x: int, screen_y: int) -> bool:
    """在屏幕坐标处获取 UIA 元素并调用。

    用于处理没有 accessible name 的元素：先用 OCR 定位文字位置，
    然后通过 ControlFromPoint 获取该位置的 UIA 元素，再向上查找
    有 InvokePattern 或可调用 LegacyIAccessiblePattern 的祖先。

    Args:
        screen_x: 屏幕绝对 X 坐标
        screen_y: 屏幕绝对 Y 坐标

    Returns:
        True 表示成功调用了某个操作
    """
    if not _UIA_AVAILABLE:
        return False

    try:
        point = wintypes.POINT(screen_x, screen_y)
        elem = ua.ControlFromPoint(point)
    except Exception as e:
        logger.debug(f"ControlFromPoint({screen_x},{screen_y}) 失败: {e}")
        return False

    if elem is None:
        logger.debug(f"ControlFromPoint({screen_x},{screen_y}) 返回 None")
        return False

    elem_name = elem.Name or ""
    elem_type = elem.ControlTypeName
    logger.info(f"UIA Point: ({screen_x},{screen_y}) -> '{elem_name}' ({elem_type})")

    # 收集从当前元素到根的祖先链（最多 10 层）
    ancestors = []
    current = elem
    for _ in range(10):
        if current is None:
            break
        ancestors.append(current)
        try:
            current = current.GetParent()
        except Exception:
            break

    # 按控件类型优先级排序祖先链
    ancestors.sort(key=lambda e: _control_priority(e))

    # 策略1: 尝试 InvokePattern
    for a in ancestors:
        if _is_system_button(a):
            continue
        if _try_invoke(a):
            name = a.Name or ""
            logger.info(f"UIA Point Invoke: '{name}' ({a.ControlTypeName})")
            return True

    # 策略2: 尝试 LegacyIAccessiblePattern
    for a in ancestors:
        if _is_system_button(a):
            continue
        ctype = ""
        try:
            ctype = a.ControlTypeName
        except Exception:
            pass
        if ctype == "TextControl":
            if _try_legacy_safe(a):
                logger.info(f"UIA Point Legacy(safe): '{a.Name}' ({ctype})")
                return True
        else:
            if _try_legacy(a):
                logger.info(f"UIA Point Legacy: '{a.Name}' ({ctype})")
                return True

    logger.info(f"UIA Point: ({screen_x},{screen_y}) 向上 {len(ancestors)} 层均无可调用元素")
    return False


def exists(hwnd: int, name: str) -> bool:
    """检查 UIA 树中是否存在名称匹配的元素。

    不检查 BoundingRectangle — InvokePattern 可以调用不可见的元素。
    只过滤系统按钮和描述性文字。
    """
    if not _UIA_AVAILABLE:
        return False

    win = _get_window(hwnd)
    if win is None:
        return False

    found = [False]

    def walk(elem, depth=0):
        if found[0] or depth > 25:
            return
        if not _is_system_button(elem):
            elem_name = elem.Name or ""
            if name in elem_name and not _is_description_text(elem_name, name, False):
                found[0] = True
                return
        for child in elem.GetChildren():
            if found[0]:
                return
            walk(child, depth + 1)

    walk(win)
    return found[0]


def list_elements(hwnd: int, max_count: int = 50) -> list[tuple[str, str, bool]]:
    """列出窗口中所有有名称的 UIA 元素（调试用）。"""
    if not _UIA_AVAILABLE:
        return []

    win = _get_window(hwnd)
    if win is None:
        return []

    result = []

    def walk(elem, depth=0):
        if len(result) >= max_count or depth > 25:
            return
        name = elem.Name or ""
        ctype = elem.ControlTypeName
        if name.strip():
            has_invoke = False
            try:
                pat = elem.GetInvokePattern()
                has_invoke = pat is not None
            except Exception:
                pass
            result.append((name, ctype, has_invoke))
        for child in elem.GetChildren():
            walk(child, depth + 1)

    walk(win)
    return result


# ── 内部方法 ──

def _find_elements_by_name(win_elem, name: str, exact: bool) -> list:
    """在 UIA 树中查找名称匹配的元素。

    不检查 BoundingRectangle — InvokePattern 可以调用不可见的元素
    （如页面底部需要滚动才能看到的按钮）。
    只过滤系统按钮和描述性文字。
    """
    matched = []

    def walk(elem, depth=0):
        if depth > 25 or len(matched) >= 10:
            return
        if not _is_system_button(elem):
            elem_name = elem.Name or ""
            if exact:
                if elem_name == name:
                    matched.append(elem)
            else:
                if name in elem_name and not _is_description_text(elem_name, name, exact):
                    matched.append(elem)
        for child in elem.GetChildren():
            walk(child, depth + 1)

    walk(win_elem)
    return matched


def _try_invoke(elem) -> bool:
    """尝试 InvokePattern.Invoke()。"""
    try:
        pat = elem.GetInvokePattern()
        if pat:
            pat.Invoke()
            return True
    except Exception:
        pass
    return False


def _try_legacy_safe(elem) -> bool:
    """尝试 LegacyIAccessiblePattern.DoDefaultAction()。

    只在 CurrentDefaultAction 非空时才调用，避免对描述性文字
    误调 DoDefaultAction（它对不可点击的文字也会返回成功但无效）。
    用于 TextControl 等可能为纯文字的元素。
    """
    try:
        pat = elem.GetLegacyIAccessiblePattern()
        if pat:
            default_action = pat.CurrentDefaultAction
            if default_action and default_action.strip():
                logger.debug(f"  Legacy default action: '{default_action}' on '{elem.Name}'")
                pat.DoDefaultAction()
                return True
    except Exception:
        pass
    return False


def _try_legacy(elem) -> bool:
    """尝试 LegacyIAccessiblePattern.DoDefaultAction()（不检查 DefaultAction）。

    用于 GroupControl 等非 TextControl 元素，即使 CurrentDefaultAction
    为空也尝试调用（Chromium 中的可点击 div 可能有 onclick 处理程序）。
    """
    try:
        pat = elem.GetLegacyIAccessiblePattern()
        if pat:
            pat.DoDefaultAction()
            return True
    except Exception:
        pass
    return False


def _try_click(elem) -> bool:
    """尝试 Click()（模拟鼠标点击元素中心）。

    只在元素有有效 BoundingRectangle 时才点击，
    避免 Click() 对 (0,0,0,0) 的不可见元素静默返回成功。
    """
    if not _has_valid_rect(elem):
        return False
    try:
        elem.Click()
        return True
    except Exception:
        pass
    return False
