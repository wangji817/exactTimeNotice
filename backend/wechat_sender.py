from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

from .config_manager import config_manager
from .models import BroadcastLog

WECHAT_PROCESS_NAMES = ("wechat.exe", "weixin.exe")


class WechatSender:
    """通过本机官方 PC 微信发群（兼容新版 Qt 微信，主要靠快捷键+坐标点击）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[BroadcastLog] = []
        self._max_logs = 200
        self._listeners: list[Callable[[BroadcastLog], None]] = []

    def on_log(self, callback: Callable[[BroadcastLog], None]) -> None:
        self._listeners.append(callback)

    def get_logs(self, limit: int = 50) -> list[BroadcastLog]:
        with self._lock:
            return list(reversed(self._logs[-limit:]))

    def send(
        self,
        feature: str,
        message: str,
        at_members: list[str] | None = None,
    ) -> BroadcastLog:
        settings = config_manager.get()
        group_name = (settings.wechat.group_name or "").strip()
        dry_run = settings.wechat.dry_run
        at_members = [m.strip() for m in (at_members or []) if m and m.strip()]

        full_message = message
        if at_members:
            ats = " ".join(f"@{m}" for m in at_members)
            full_message = f"{ats}\n{message}"

        detail = ""
        success = False

        if not group_name:
            detail = "未配置微信群名称"
            success = False
        elif dry_run:
            detail = f"[演练模式] 将发往群「{group_name}」"
            success = True
            print(f"[DRY-RUN][{feature}] -> {group_name}: {full_message}")
        else:
            try:
                with self._lock:
                    self._send_uia(group_name, message, at_members)
                detail = f"已发送至群「{group_name}」"
                success = True
                print(f"[SEND][{feature}] -> {group_name}: {full_message}")
            except Exception as exc:  # noqa: BLE001
                detail = str(exc)
                success = False
                print(f"[ERROR][{feature}] {detail}")

        log = BroadcastLog(
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            feature=feature,
            message=full_message,
            success=success,
            detail=detail,
        )
        with self._lock:
            self._logs.append(log)
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[-self._max_logs :]
        for listener in list(self._listeners):
            try:
                listener(log)
            except Exception:  # noqa: BLE001
                pass
        return log

    def _send_uia(
        self, group_name: str, message: str, at_members: list[str]
    ) -> None:
        import pyperclip
        import uiautomation as auto

        wx = _ensure_wechat_window(auto)
        hwnd = int(wx.NativeWindowHandle or 0)
        # 发送全程置顶，避免浏览器抢回焦点后把微信盖住、按键打到网页上
        _set_topmost(hwnd, True)
        send_ok = False
        try:
            _restore_and_foreground(hwnd)
            time.sleep(0.3)

            # 搜索并打开目标群（Qt 新版几乎无障碍树，不依赖 EditControl）
            auto.SendKeys("{Ctrl}f", waitTime=0.3)
            time.sleep(0.3)
            auto.SendKeys("{Ctrl}a", waitTime=0.05)
            auto.SendKeys("{Delete}", waitTime=0.05)
            pyperclip.copy(group_name)
            auto.SendKeys("{Ctrl}v", waitTime=0.25)
            time.sleep(0.7)
            auto.SendKeys("{Enter}", waitTime=0.5)
            time.sleep(0.55)

            # 搜群后浏览器常会抢焦点：再次置前，并点聊天区/输入区
            _restore_and_foreground(hwnd)
            _click_chat_area(wx, auto)
            time.sleep(0.2)
            _focus_chat_input(wx, auto)
            time.sleep(0.2)

            if not _is_foreground(hwnd):
                _restore_and_foreground(hwnd)
                _focus_chat_input(wx, auto)
                time.sleep(0.2)

            for member in at_members:
                auto.SendKeys("@", waitTime=0.15)
                pyperclip.copy(member)
                auto.SendKeys("{Ctrl}v", waitTime=0.2)
                time.sleep(0.45)
                auto.SendKeys("{Enter}", waitTime=0.25)

            pyperclip.copy(message)
            auto.SendKeys("{Ctrl}v", waitTime=0.3)
            time.sleep(0.25)
            auto.SendKeys("{Enter}", waitTime=0.3)
            time.sleep(0.35)
            send_ok = True
        finally:
            _set_topmost(hwnd, False)
            # 发送成功后用微信「显示/隐藏」快捷键收起窗口
            if send_ok:
                time.sleep(0.2)
                _toggle_wechat_window()

    def test_connection(self) -> dict:
        settings = config_manager.get()
        group_name = (settings.wechat.group_name or "").strip()
        if not group_name:
            return {"ok": False, "message": "请先填写微信群名称"}
        if settings.wechat.dry_run:
            return {
                "ok": True,
                "message": f"演练模式已开启，不会真实发消息。目标群：{group_name}",
            }
        try:
            import uiautomation as auto

            running = _wechat_process_running()
            wx = _find_wechat_window(auto)
            if wx is None and running:
                _toggle_wechat_window()
                time.sleep(0.8)
                wx = _find_wechat_window(auto)
            if wx is None:
                if running:
                    hotkey = _wake_hotkey()
                    return {
                        "ok": False,
                        "message": (
                            f"已检测到微信进程，但窗口未显示。"
                            f"请确认快捷键「{hotkey}」与微信设置一致，或手动打开主窗口"
                        ),
                    }
                return {
                    "ok": False,
                    "message": "未检测到微信进程，请先打开并登录官方 PC 微信",
                }
            cls = getattr(wx, "ClassName", "") or ""
            tip = "Qt新版" if "Qt" in cls else "经典版"
            return {
                "ok": True,
                "message": f"已检测到 PC 微信（{tip}，进程正常），目标群：{group_name}",
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"检测 PC 微信失败：{exc}"}


def _wake_hotkey() -> str:
    settings = config_manager.get()
    return (settings.wechat.wake_hotkey or "ctrl+alt+w").strip().lower()


def _wechat_process_running() -> bool:
    """检测 WeChat.exe / Weixin.exe 是否在运行。"""
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            creationflags=creationflags,
            text=True,
            encoding="gbk",
            errors="ignore",
        )
    except Exception:  # noqa: BLE001
        return False
    lower = out.lower()
    return any(name in lower for name in WECHAT_PROCESS_NAMES)


def _parse_hotkey(combo: str) -> list[int]:
    """解析 ctrl+alt+w 这类组合为虚拟键码列表。"""
    text = (combo or "").strip().lower().replace("-", "+")
    if not text:
        text = "ctrl+alt+w"
    vks: list[int] = []
    for part in text.split("+"):
        key = part.strip()
        if not key:
            continue
        if key in {"ctrl", "control"}:
            vks.append(0x11)
        elif key == "alt":
            vks.append(0x12)
        elif key == "shift":
            vks.append(0x10)
        elif key in {"win", "meta"}:
            vks.append(0x5B)
        elif len(key) == 1 and key.isalnum():
            vks.append(ord(key.upper()))
        else:
            raise ValueError(f"无法识别的快捷键：{combo}")
    if not vks:
        raise ValueError(f"快捷键为空：{combo}")
    return vks


def _send_hotkey(combo: str) -> None:
    import win32api
    import win32con

    vks = _parse_hotkey(combo)
    for vk in vks:
        win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.05)
    for vk in reversed(vks):
        win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def _toggle_wechat_window() -> None:
    """触发微信「显示/隐藏窗口」快捷键（默认 Ctrl+Alt+W）。"""
    try:
        _send_hotkey(_wake_hotkey())
    except Exception as exc:  # noqa: BLE001
        print(f"[Wechat] 快捷键唤起/隐藏失败：{exc}")


def _ensure_wechat_window(auto):
    """找到微信窗口；若仅有进程无窗口，则用快捷键唤起。"""
    wx = _find_wechat_window(auto)
    if wx is not None:
        return wx

    if not _wechat_process_running():
        raise RuntimeError("未检测到微信进程，请先打开并登录官方 PC 微信")

    hotkey = _wake_hotkey()
    print(f"[Wechat] 窗口未显示，尝试快捷键唤起：{hotkey}")
    _toggle_wechat_window()
    time.sleep(1.0)
    wx = _find_wechat_window(auto)
    if wx is not None:
        return wx

    # 再试一次（防止第一次仍处于隐藏态）
    _toggle_wechat_window()
    time.sleep(1.0)
    wx = _find_wechat_window(auto)
    if wx is not None:
        return wx

    raise RuntimeError(
        f"微信进程在运行，但无法用快捷键「{hotkey}」唤起窗口。"
        "请到微信「设置 → 快捷键」确认「显示/隐藏窗口」，或手动打开主窗口后重试"
    )


def _find_wechat_window(auto):
    """查找本机微信主窗口（兼容经典版与 Qt 新版）。"""
    candidates = [
        auto.WindowControl(searchDepth=1, ClassName="WeChatMainWndForPC"),
        auto.WindowControl(searchDepth=1, ClassName="Qt51514QWindowIcon", Name="微信"),
        auto.WindowControl(searchDepth=1, ClassName="Chrome_WidgetWin_0", Name="微信"),
        auto.WindowControl(searchDepth=1, Name="微信"),
        auto.WindowControl(searchDepth=1, Name="Weixin"),
    ]
    for win in candidates:
        try:
            if win.Exists(0, 0):
                return win
        except Exception:  # noqa: BLE001
            continue

    # 兜底：遍历顶层窗口，匹配名称含微信且可见
    try:
        root = auto.GetRootControl()
        for child in root.GetChildren():
            try:
                name = child.Name or ""
                if name in {"微信", "Weixin", "WeChat"} and child.Exists(0, 0):
                    return child
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return None


def _click_chat_area(wx, auto) -> None:
    """点击右侧聊天消息区，确保会话已激活（不要用 Esc，容易丢焦点）。"""
    rect = wx.BoundingRectangle
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    x = left + int(width * 0.68)
    y = top + int(height * 0.45)
    auto.Click(x, y, waitTime=0.2)


def _focus_chat_input(wx, auto) -> None:
    """聚焦聊天输入区：优先找可访问控件，失败则点窗口底部（Qt 新版常用）。"""
    edit = _find_edit_box(wx)
    if edit is not None:
        try:
            edit.Click(simulateMove=False)
            return
        except Exception:  # noqa: BLE001
            pass

    rect = wx.BoundingRectangle
    left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    # 输入框通常在聊天区底部偏右（避开左侧会话列表）
    x = left + int(width * 0.62)
    y = top + int(height * 0.90)
    auto.Click(x, y, waitTime=0.2)


def _is_foreground(hwnd: int) -> bool:
    import win32gui

    try:
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:  # noqa: BLE001
        return False


def _set_topmost(hwnd: int, enable: bool) -> None:
    import win32con
    import win32gui

    if not hwnd or not win32gui.IsWindow(hwnd):
        return
    flag = win32con.HWND_TOPMOST if enable else win32con.HWND_NOTOPMOST
    try:
        win32gui.SetWindowPos(
            hwnd,
            flag,
            0,
            0,
            0,
            0,
            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
    except Exception:  # noqa: BLE001
        pass


def _find_edit_box(wx):
    # 经典版可能有 Edit / Document；Qt 新版通常没有
    for factory in (
        lambda: wx.EditControl(searchDepth=20),
        lambda: wx.DocumentControl(searchDepth=20),
    ):
        try:
            ctrl = factory()
            if ctrl.Exists(0.5, 0):
                return ctrl
        except Exception:  # noqa: BLE001
            continue
    return None


def _restore_and_foreground(hwnd: int) -> None:
    """尽量把微信窗口置前。Windows 常拦截 SetForegroundWindow，失败时不抛错继续发。"""
    import ctypes

    import win32api
    import win32con
    import win32gui
    import win32process

    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("微信窗口句柄无效，请确认 PC 微信已打开")

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    else:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    if win32gui.GetForegroundWindow() == hwnd:
        return

    user32 = ctypes.windll.user32
    user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
    user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU up

    current_tid = win32api.GetCurrentThreadId()
    target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
    fg = win32gui.GetForegroundWindow()
    fg_tid = 0
    if fg:
        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)

    attached_fg = False
    attached_target = False
    try:
        if fg_tid and fg_tid != current_tid:
            attached_fg = bool(
                win32process.AttachThreadInput(current_tid, fg_tid, True)
            )
        if target_tid and target_tid != current_tid and target_tid != fg_tid:
            attached_target = bool(
                win32process.AttachThreadInput(current_tid, target_tid, True)
            )

        win32gui.BringWindowToTop(hwnd)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:  # noqa: BLE001
            try:
                user32.SetForegroundWindow(hwnd)
            except Exception:  # noqa: BLE001
                pass
        try:
            win32gui.SetActiveWindow(hwnd)
        except Exception:  # noqa: BLE001
            pass
    finally:
        if attached_target:
            win32process.AttachThreadInput(current_tid, target_tid, False)
        if attached_fg:
            win32process.AttachThreadInput(current_tid, fg_tid, False)

    time.sleep(0.25)


wechat_sender = WechatSender()
