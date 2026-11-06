from __future__ import annotations

import subprocess
import threading
import time
from datetime import datetime
from typing import Callable

from .config_manager import config_manager
from .models import BroadcastLog

WECHAT_PROCESS_NAMES = ("wechat.exe", "weixin.exe")

# 合并播报时的稳定排序
FEATURE_ORDER = [
    "抢台子",
    "坚守城池",
    "镇国台子",
    "四方都城",
    "流寇攻城",
]

# 同一时刻多任务先入队，短时窗口内合并为一条再发
COALESCE_SECONDS = 2.5


def _compose_batch(items: list[dict]) -> tuple[str, str, list[str]]:
    """将多条播报拼成一条可读消息：分区标题 + 空行间隔。"""
    if not items:
        return "播报", "", []

    # 按功能固定顺序；同名按入队顺序
    order_index = {name: i for i, name in enumerate(FEATURE_ORDER)}
    indexed = list(enumerate(items))
    indexed.sort(
        key=lambda pair: (
            order_index.get(pair[1].get("feature") or "", 100),
            pair[0],
        )
    )
    ordered = [it for _, it in indexed]

    at_all: list[str] = []
    seen_at: set[str] = set()
    for it in ordered:
        for name in it.get("at_members") or []:
            if name and name not in seen_at:
                seen_at.add(name)
                at_all.append(name)

    if len(ordered) == 1:
        only = ordered[0]
        return only["feature"], only["message"], at_all

    blocks: list[str] = ["📢 定时播报合集", ""]
    for i, it in enumerate(ordered):
        if i > 0:
            blocks.append("")
            blocks.append("────────────")
            blocks.append("")
        feature = it.get("feature") or "播报"
        body = (it.get("message") or "").strip()
        blocks.append(f"【{feature}】")
        blocks.append("")
        blocks.append(body)

    names = "、".join(it.get("feature") or "播报" for it in ordered)
    return f"合并播报（{names}）", "\n".join(blocks), at_all


class WechatSender:
    """通过本机官方 PC 微信发群（兼容新版 Qt 微信，主要靠快捷键+坐标点击）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._logs: list[BroadcastLog] = []
        self._max_logs = 200
        self._listeners: list[Callable[[BroadcastLog], None]] = []
        self._batch_lock = threading.Lock()
        self._pending: list[dict] = []
        self._flush_timer: threading.Timer | None = None

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
        *,
        immediate: bool = False,
    ) -> BroadcastLog:
        """发送群消息。

        定时任务默认 short-window 合并：同一时刻多条会拼成一条再发。
        手动试发等场景传 immediate=True，立即单发。
        """
        at_members = [m.strip() for m in (at_members or []) if m and m.strip()]
        item = {
            "feature": (feature or "").strip() or "播报",
            "message": (message or "").strip(),
            "at_members": at_members,
        }
        if immediate:
            return self._deliver_items([item])

        with self._batch_lock:
            self._pending.append(item)
            if self._flush_timer is not None:
                self._flush_timer.cancel()
            timer = threading.Timer(COALESCE_SECONDS, self._flush_pending)
            timer.daemon = True
            self._flush_timer = timer
            timer.start()
            queued = len(self._pending)

        # 不写正式日志，等合并窗口结束后统一落库
        return BroadcastLog(
            time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            feature=item["feature"],
            message=item["message"],
            success=True,
            detail=f"已加入合并队列（当前 {queued} 条，{COALESCE_SECONDS}s 内一并发送）",
        )

    def _flush_pending(self) -> None:
        with self._batch_lock:
            items = list(self._pending)
            self._pending.clear()
            self._flush_timer = None
        if not items:
            return
        self._deliver_items(items)

    def _deliver_items(self, items: list[dict]) -> BroadcastLog:
        feature, body, at_members = _compose_batch(items)
        settings = config_manager.get()
        group_name = (settings.wechat.group_name or "").strip()
        dry_run = settings.wechat.dry_run

        full_message = body
        if at_members:
            ats = " ".join(f"@{m}" for m in at_members)
            full_message = f"{ats}\n\n{body}"

        detail = ""
        success = False

        if not group_name:
            detail = "未配置微信群名称"
            success = False
        elif dry_run:
            detail = f"[演练模式] 将发往群「{group_name}」"
            success = True
            print(f"[DRY-RUN][{feature}] -> {group_name}:\n{full_message}")
        else:
            try:
                with self._lock:
                    self._send_uia(group_name, body, at_members)
                detail = f"已发送至群「{group_name}」"
                success = True
                print(f"[SEND][{feature}] -> {group_name}:\n{full_message}")
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

    def probe_status(self, *, try_wake: bool = False) -> dict:
        """检测 PC 微信进程/窗口/是否已登录，可选尝试快捷键唤醒。"""
        hotkey = _wake_hotkey()
        try:
            process = _wechat_process_running()
            info = _inspect_wechat_ui()
            window = bool(info.get("visible"))
            logged_in = bool(info.get("logged_in"))
            login_page = bool(info.get("login_page"))
            hidden = bool(info.get("hidden"))
            woke = False
            edition = info.get("edition") or ""

            # 进程在但主窗隐藏：尝试唤醒以确认是否真已登录（仍无主窗则保持原状）
            if try_wake and process and (not window or (not logged_in and not login_page)):
                _toggle_wechat_window()
                time.sleep(1.0)
                info = _inspect_wechat_ui()
                window = bool(info.get("visible"))
                logged_in = bool(info.get("logged_in"))
                login_page = bool(info.get("login_page"))
                hidden = bool(info.get("hidden"))
                edition = info.get("edition") or edition
                woke = window
                # 探测后收起，避免入口页把微信一直留在前台
                if window:
                    time.sleep(0.25)
                    _toggle_wechat_window()
                    # 收起后按隐藏态再读一次，避免把「已登录但托盘」判成未登录
                    info = _inspect_wechat_ui()
                    window = bool(info.get("visible"))
                    logged_in = bool(info.get("logged_in"))
                    login_page = bool(info.get("login_page"))
                    hidden = bool(info.get("hidden"))
                    edition = info.get("edition") or edition

            if process and logged_in and window:
                level = "ok"
                title = "PC 微信已就绪"
                detail = f"已登录{('（' + edition + '）') if edition else ''}，可进入司令台"
                if woke:
                    detail += f"；已用快捷键「{hotkey}」成功唤醒"
            elif process and logged_in and not window:
                # 主界面仍在（托盘隐藏），视为已登录可进入
                level = "ok"
                title = "PC 微信已登录（窗口已隐藏）"
                detail = (
                    f"检测到后台主窗口{('（' + edition + '）') if edition else ''}，"
                    f"已收起到托盘。可直接进入；发送时会用「{hotkey}」自动唤起"
                )
                hidden = True
            elif process and login_page:
                level = "err"
                title = "微信未登录"
                detail = "微信已打开，但仍在登录页。请先扫码/登录成功后再进入"
            elif process and not window:
                level = "warn"
                title = "微信进程在运行，窗口未显示"
                detail = (
                    f"未能确认是否已登录。可点「尝试唤醒」（快捷键 {hotkey}）。"
                    "请确认与微信「设置 → 快捷键 → 显示/隐藏窗口」一致"
                )
            else:
                level = "err"
                title = "未检测到 PC 微信"
                detail = "请先打开并登录官方 PC 微信后再进入司令台"

            return {
                "ok": level == "ok",
                "level": level,
                "process": process,
                "window": window,
                "logged_in": logged_in,
                "login_page": login_page,
                "hidden": hidden,
                "woke": woke,
                "wake_hotkey": hotkey,
                "edition": edition,
                "title": title,
                "detail": detail,
                "message": f"{title}：{detail}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "level": "err",
                "process": False,
                "window": False,
                "logged_in": False,
                "login_page": False,
                "hidden": False,
                "woke": False,
                "wake_hotkey": hotkey,
                "edition": "",
                "title": "检测失败",
                "detail": str(exc),
                "message": f"检测 PC 微信失败：{exc}",
            }

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
        probe = self.probe_status(try_wake=True)
        if not probe.get("ok"):
            return {"ok": False, "message": probe.get("message") or "微信不可用"}
        edition = probe.get("edition") or "PC微信"
        return {
            "ok": True,
            "message": f"已检测到 {edition}，目标群：{group_name}",
        }


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
    """找到已登录的微信主窗口；若仅有进程无窗口，则用快捷键唤起。"""
    info = _inspect_wechat_ui()
    if info.get("login_page") or (info.get("visible") and not info.get("logged_in")):
        raise RuntimeError("微信未登录，仍在登录页，请先扫码/登录成功后再发送")

    wx = _find_wechat_window(auto)
    if wx is not None:
        # 再次确认不是登录小窗
        try:
            cls = getattr(wx, "ClassName", "") or ""
            name = getattr(wx, "Name", "") or ""
            rect = wx.BoundingRectangle
            width = max(int(rect.right - rect.left), 0)
            height = max(int(rect.bottom - rect.top), 0)
            if not _window_looks_logged_in(cls, name, width, height):
                raise RuntimeError("微信未登录，仍在登录页，请先扫码/登录成功后再发送")
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001
            pass
        return wx

    if not _wechat_process_running():
        raise RuntimeError("未检测到微信进程，请先打开并登录官方 PC 微信")

    hotkey = _wake_hotkey()
    print(f"[Wechat] 窗口未显示，尝试快捷键唤起：{hotkey}")
    _toggle_wechat_window()
    time.sleep(1.0)
    info = _inspect_wechat_ui()
    if info.get("login_page") or (info.get("visible") and not info.get("logged_in")):
        raise RuntimeError("微信未登录，仍在登录页，请先扫码/登录成功后再发送")
    wx = _find_wechat_window(auto)
    if wx is not None:
        return wx

    # 再试一次（防止第一次仍处于隐藏态）
    _toggle_wechat_window()
    time.sleep(1.0)
    info = _inspect_wechat_ui()
    if info.get("login_page") or (info.get("visible") and not info.get("logged_in")):
        raise RuntimeError("微信未登录，仍在登录页，请先扫码/登录成功后再发送")
    wx = _find_wechat_window(auto)
    if wx is not None:
        return wx

    raise RuntimeError(
        f"微信进程在运行，但无法用快捷键「{hotkey}」唤起窗口。"
        "请到微信「设置 → 快捷键」确认「显示/隐藏窗口」，或手动打开主窗口后重试"
    )


def _exe_path_for_hwnd(hwnd) -> str:
    import win32api
    import win32process

    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        handle = win32api.OpenProcess(0x0410, False, pid)
        try:
            return win32process.GetModuleFileNameEx(handle, 0) or ""
        finally:
            win32api.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return ""


def _is_wechat_main_exe(exe: str) -> bool:
    name = (exe or "").replace("\\", "/").lower().rsplit("/", 1)[-1]
    return name in {"wechat.exe", "weixin.exe"}


def _inspect_wechat_ui() -> dict:
    """通过 Win32 枚举识别微信窗口（含托盘隐藏的主窗），判断是否已登录。"""
    import win32gui

    windows: list[dict] = []
    has_tray = False

    def _cb(hwnd, _):  # noqa: ANN001
        nonlocal has_tray
        try:
            if not win32gui.IsWindow(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            cls = win32gui.GetClassName(hwnd) or ""
            exe = _exe_path_for_hwnd(hwnd)
            # 托盘消息窗：说明客户端在跑，且通常已进入会话态
            if "WxTrayIcon" in cls and _is_wechat_main_exe(exe):
                has_tray = True
            # 只认主程序窗口，忽略 WeChatAppEx / 插件 / IME 等
            if not _is_wechat_main_exe(exe):
                return True
            if not (
                title in {"微信", "Weixin", "WeChat"}
                or "登录" in title
                or cls
                in {
                    "WeChatMainWndForPC",
                    "WeChatLoginWndForPC",
                    "Qt51514QWindowIcon",
                    "Qt51514QWindowToolSaveBits",
                }
                or "WeChatMain" in cls
                or "WeChatLogin" in cls
            ):
                return True
            # 过滤托盘/工具类 Qt 窗，避免当成主界面
            if "Tray" in cls or "Tool" in cls or "MessageWindow" in cls:
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = max(right - left, 0)
            height = max(bottom - top, 0)
            if width < 80 or height < 80:
                return True
            visible = bool(win32gui.IsWindowVisible(hwnd)) and not win32gui.IsIconic(hwnd)
            is_login = _window_looks_login_page(cls, title, width, height)
            is_main = (not is_login) and _window_looks_logged_in(cls, title, width, height)
            windows.append(
                {
                    "hwnd": hwnd,
                    "class": cls,
                    "title": title,
                    "visible": visible,
                    "width": width,
                    "height": height,
                    "is_login": is_login,
                    "is_main": is_main,
                    "logged_in": is_main,
                }
            )
        except Exception:  # noqa: BLE001
            return True
        return True

    win32gui.EnumWindows(_cb, None)

    mains = [w for w in windows if w["is_main"]]
    logins = [w for w in windows if w["is_login"]]
    visible_mains = [w for w in mains if w["visible"]]
    visible_logins = [w for w in logins if w["visible"]]

    # 有主窗（哪怕 Invisible/托盘隐藏）→ 已登录；仅有登录窗 → 未登录
    logged_in = bool(mains) or (has_tray and not logins)
    login_page = bool(logins) and not bool(mains)
    visible = bool(visible_mains) or (bool(visible_logins) and not logged_in)
    hidden = logged_in and not bool(visible_mains)

    pick = (visible_mains or mains or visible_logins or logins or windows[:1] or [None])[0]
    if not pick:
        return {
            "visible": False,
            "logged_in": False,
            "login_page": False,
            "hidden": False,
            "edition": "",
            "class": "",
            "title": "",
            "width": 0,
            "height": 0,
        }

    cls = pick.get("class") or ""
    edition = "Qt新版" if "Qt" in cls else "经典版"
    return {
        "visible": visible,
        "logged_in": logged_in,
        "login_page": login_page,
        "hidden": hidden,
        "edition": edition,
        "class": cls,
        "title": pick.get("title") or "",
        "width": pick.get("width") or 0,
        "height": pick.get("height") or 0,
    }


def _window_looks_login_page(cls: str, title: str, width: int, height: int) -> bool:
    """明确的登录页特征。"""
    cls = cls or ""
    title = title or ""
    cls_l = cls.lower()
    if "WeChatLoginWndForPC" in cls or "login" in cls_l:
        return True
    if "登录" in title:
        return True
    # 小尺寸「微信/Weixin」窗常见于登录框；主界面明显更大
    if title in {"微信", "Weixin", "WeChat"} and (width < 520 or height < 480):
        return True
    return False


def _window_looks_logged_in(cls: str, title: str, width: int, height: int) -> bool:
    """区分登录页与主界面（含已隐藏但仍存在的主窗）。"""
    cls = cls or ""
    title = title or ""
    if _window_looks_login_page(cls, title, width, height):
        return False
    # 经典主窗口
    if cls == "WeChatMainWndForPC" or "WeChatMain" in cls:
        return True
    # Qt 新版主窗口：标题为微信且尺寸达到主界面
    if cls.startswith("Qt51514QWindow") and title in {"微信", "Weixin", "WeChat"}:
        return width >= 520 and height >= 480
    if width >= 520 and height >= 480 and title in {"微信", "Weixin", "WeChat"}:
        return True
    return False


def _find_wechat_window(auto):
    """查找本机微信主窗口（优先已登录主界面；兼容经典版与 Qt 新版）。"""
    # 缩短超时，避免探测卡住
    try:
        auto.SetGlobalSearchTimeout(1.0)
    except Exception:  # noqa: BLE001
        pass

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
                cls = getattr(win, "ClassName", "") or ""
                name = getattr(win, "Name", "") or ""
                rect = win.BoundingRectangle
                width = max(int(rect.right - rect.left), 0)
                height = max(int(rect.bottom - rect.top), 0)
                # 发送时也尽量避开登录页小窗
                if _window_looks_logged_in(cls, name, width, height):
                    return win
                # 若只有登录窗，先返回给上层做状态判断
                if "login" in cls.lower() or "登录" in name or width < 520:
                    return win
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
