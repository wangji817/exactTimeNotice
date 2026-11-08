from __future__ import annotations

import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
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
    "群英会",
    "王城争锋",
    "天子战",
    "钓鱼",
]

# 同一时刻多任务先入队，短时窗口内合并为一条再发
COALESCE_SECONDS = 2.5
# 微信 UI 自动化最长等待；超时必须释放，避免 preview 一直 pending
# OCR 首次加载模型较慢，预留更长时间
SEND_TIMEOUT_SECONDS = 45

_ocr_engine = None
_ocr_lock = threading.Lock()


def _apply_priority_rules(items: list[dict]) -> list[dict]:
    """抢台子与镇国台子同时触发时，只保留镇国台子。"""
    features = {it.get("feature") for it in items}
    if "镇国台子" in features and "抢台子" in features:
        print("[Wechat] 抢台子与镇国台子冲突，仅保留镇国台子")
        return [it for it in items if it.get("feature") != "抢台子"]
    return items


def _compose_batch(items: list[dict]) -> tuple[str, str, list[str]]:
    """将多条播报拼成一条：正文自带标题，块之间空一行。"""
    items = _apply_priority_rules(list(items))
    if not items:
        return "播报", "", []

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
            if name and name not in seen_at and name != "所有人":
                seen_at.add(name)
                at_all.append(name)

    if len(ordered) == 1:
        only = ordered[0]
        return only["feature"], only["message"], at_all

    blocks: list[str] = []
    for i, it in enumerate(ordered):
        if i > 0:
            blocks.append("")
        body = (it.get("message") or "").strip()
        if body:
            blocks.append(body)

    names = "、".join(it.get("feature") or "播报" for it in ordered)
    return f"合并播报（{names}）", "\n".join(blocks), at_all


class WechatSender:
    """通过本机官方 PC 微信发群（兼容新版 Qt 微信，主要靠快捷键+坐标点击）。"""

    def __init__(self) -> None:
        # 日志锁与发送锁分离：发送卡住时不能拖死 /api/logs
        self._log_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._logs: list[BroadcastLog] = []
        self._max_logs = 200
        self._listeners: list[Callable[[BroadcastLog], None]] = []
        self._batch_lock = threading.Lock()
        self._pending: list[dict] = []
        self._flush_timer: threading.Timer | None = None
        self._send_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wx-send")

    def on_log(self, callback: Callable[[BroadcastLog], None]) -> None:
        self._listeners.append(callback)

    def get_logs(self, limit: int = 50) -> list[BroadcastLog]:
        with self._log_lock:
            return list(reversed(self._logs[-limit:]))

    def list_chat_windows(self) -> list[dict]:
        """列出微信顶层窗口（含独立聊天窗），供司令台展示链接状态。"""
        return _list_wechat_top_windows(visible_only=False)

    def send(
        self,
        feature: str,
        message: str,
        at_members: list[str] | None = None,
        *,
        immediate: bool = False,
        force_group: str | None = None,
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
            return self._deliver_items([item], force_group=force_group)

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

    def _append_log(self, log: BroadcastLog) -> BroadcastLog:
        with self._log_lock:
            self._logs.append(log)
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[-self._max_logs :]
        for listener in list(self._listeners):
            try:
                listener(log)
            except Exception:  # noqa: BLE001
                pass
        return log

    def _merge_at_everyone(self, at_members: list[str], enabled: bool) -> list[str]:
        names = [m for m in at_members if m]
        if not enabled:
            return names
        # 微信 @所有人：输入 @ 后选「所有人」
        if not any(_norm_chat_name(m) == _norm_chat_name("所有人") for m in names):
            names = ["所有人", *names]
        return names

    def probe_test_group(
        self, *, reason: str = "通道检测", skip_if_dry_run: bool = True
    ) -> BroadcastLog:
        """向测试群「三国测试组」发一条探测消息（不做二次探测、不 @所有人）。"""
        settings = config_manager.get()
        test_group = (settings.wechat.test_group_name or "三国测试组").strip()
        dry_run = settings.wechat.dry_run
        feature = f"测试组试发（{reason}）"
        message = (
            f"【司令台】{reason}：测试群「{test_group}」通道检测。"
            "若看到本消息，说明搜群/发送链路可用。"
        )

        if not test_group:
            return self._append_log(
                BroadcastLog(
                    time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    feature=feature,
                    message=message,
                    success=False,
                    detail="未配置测试群名称",
                )
            )

        if dry_run and skip_if_dry_run:
            detail = f"[演练模式] 跳过真实试发，目标测试群「{test_group}」"
            print(f"[DRY-RUN][{feature}] -> {test_group}:\n{message}")
            return self._append_log(
                BroadcastLog(
                    time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    feature=feature,
                    message=message,
                    success=True,
                    detail=detail,
                )
            )

        try:
            self._send_with_timeout(test_group, message, [])
            detail = f"测试群试发成功「{test_group}」"
            print(f"[PROBE-OK][{feature}] -> {test_group}")
            return self._append_log(
                BroadcastLog(
                    time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    feature=feature,
                    message=message,
                    success=True,
                    detail=detail,
                )
            )
        except Exception as exc:  # noqa: BLE001
            detail = f"测试群试发失败「{test_group}」：{exc}"
            print(f"[PROBE-FAIL][{feature}] {detail}")
            return self._append_log(
                BroadcastLog(
                    time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    feature=feature,
                    message=message,
                    success=False,
                    detail=detail,
                )
            )

    def _deliver_items(
        self, items: list[dict], *, force_group: str | None = None
    ) -> BroadcastLog:
        feature, body, at_members = _compose_batch(items)
        settings = config_manager.get()
        group_name = (force_group or settings.wechat.group_name or "").strip()
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
                self._send_with_timeout(group_name, body, at_members)
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
        return self._append_log(log)

    def _send_with_timeout(
        self, group_name: str, message: str, at_members: list[str]
    ) -> None:
        """在独立线程发送，超时则返回错误，避免 HTTP 一直 pending。"""
        if not self._send_lock.acquire(timeout=3):
            raise RuntimeError("上一条微信发送仍在进行，请稍后再试")

        def _run() -> None:
            try:
                self._send_uia(group_name, message, at_members)
            finally:
                self._send_lock.release()

        future = self._send_pool.submit(_run)
        try:
            future.result(timeout=SEND_TIMEOUT_SECONDS)
        except FuturesTimeoutError as exc:
            # 超时后尽力恢复微信界面，避免一直置顶/停在搜索层
            try:
                _recover_wechat_ui_after_failure()
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(
                f"发送超时（>{SEND_TIMEOUT_SECONDS}s）。"
                "请确认 PC 微信已打开、群名完全一致；若窗口显示异常可手动 Esc 退出搜索后再试"
            ) from exc

    def _send_uia(
        self, group_name: str, message: str, at_members: list[str]
    ) -> None:
        import uiautomation as auto

        print(f"[Wechat] 开始发送 -> 群「{group_name}」")
        wx = _ensure_wechat_window(auto)
        print("[Wechat] 已定位窗口，准备置前")
        try:
            hwnd = int(getattr(wx, "NativeWindowHandle", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"读取微信窗口句柄失败：{exc}") from exc
        if not hwnd:
            info = _inspect_wechat_ui()
            hwnd = int(info.get("hwnd") or 0)
        if not hwnd:
            raise RuntimeError("无法获取微信主窗口句柄，请先手动打开微信主界面")

        send_ok = False
        try:
            # 托盘隐藏时先用快捷键唤醒，并校验当前前台就是微信主窗口
            print("[Wechat] 确保窗口可见并识别前台是否为微信")
            hwnd = _ensure_wechat_visible(hwnd)
            time.sleep(0.25)
            fg = _assert_foreground_is_wechat(expected_hwnd=hwnd)
            print(
                f"[Wechat] 前台已确认为微信："
                f"hwnd={fg.get('hwnd')} title={fg.get('title')!r} "
                f"class={fg.get('class')!r} exe={fg.get('exe')}"
            )

            main_hwnd = hwnd
            main_wx = wx

            # 搜群不走 OCR。若点开后是独立聊天窗，用窗口标题模糊匹配群名即可确认
            print("[Wechat] 搜索并打开目标群（无 OCR）")
            _search_open_group_fast(main_wx, auto, main_hwnd, group_name)

            wx, hwnd, is_separate = _resolve_chat_target(
                auto, main_wx, main_hwnd, group_name
            )
            if is_separate:
                print(
                    f"[Wechat] 独立窗标题模糊匹配成功 → 确认为目标群，"
                    f"title={_window_title(hwnd)!r} hwnd={hwnd}"
                )
                _refocus_wechat(hwnd)
            else:
                print(
                    "[Wechat] 未出现独立聊天窗，回退主窗口会话发送"
                    "（主窗标题一般是「微信」，无法用标题校验群名）"
                )
                _leave_search_mode(auto, wx, hwnd)
                _refocus_wechat(hwnd)

            _click_chat_area(wx, auto, hwnd)
            _refocus_wechat(hwnd)
            time.sleep(0.15)

            print("[Wechat] 聚焦输入框并发送")
            if is_separate:
                _type_into_chat_window(
                    auto, hwnd, message, at_members, separate=True
                )
            else:
                _focus_chat_input(wx, auto, hwnd)
                _refocus_wechat(hwnd)
                time.sleep(0.15)

                if not _is_foreground(hwnd):
                    _refocus_wechat(hwnd)
                    _focus_chat_input(wx, auto, hwnd)
                    time.sleep(0.15)

                for member in at_members:
                    auto.SendKeys("@", waitTime=0.15)
                    _clipboard_copy(member)
                    auto.SendKeys("{Ctrl}v", waitTime=0.2)
                    time.sleep(0.45)
                    auto.SendKeys("{Enter}", waitTime=0.25)

                _clipboard_copy(message)
                auto.SendKeys("{Ctrl}v", waitTime=0.3)
                time.sleep(0.25)
                auto.SendKeys("{Enter}", waitTime=0.3)
                time.sleep(0.35)
            send_ok = True
            print(f"[Wechat] 发送步骤完成 -> 群「{group_name}」")
        finally:
            # 轻量收尾：少按 Esc，优先把焦点还回微信，避免界面像卡死
            try:
                target = hwnd if "hwnd" in locals() else 0
                if target:
                    _refocus_wechat(int(target))
                    auto.SendKeys("{Esc}", waitTime=0.08)
                    _refocus_wechat(int(target))
            except Exception:  # noqa: BLE001
                pass
            try:
                _set_topmost(hwnd, False)
                if "main_hwnd" in locals():
                    _set_topmost(main_hwnd, False)
            except Exception:  # noqa: BLE001
                pass
            if send_ok:
                print("[Wechat] 发送结束，已恢复普通窗口状态")
            else:
                print("[Wechat] 发送未完成，已尝试恢复微信界面")

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


def _norm_chat_name(name: str) -> str:
    return "".join((name or "").split()).casefold()


def _chat_title_matches(title: str, group_name: str) -> bool:
    t = _norm_chat_name(title)
    g = _norm_chat_name(group_name)
    if not t or not g:
        return False
    return g in t or t in g


def _list_wechat_top_windows(*, visible_only: bool = True) -> list[dict]:
    """枚举微信主程序顶层窗（含独立聊天窗），不依赖 OCR。"""
    import win32gui

    windows: list[dict] = []

    def _cb(hwnd, _) -> bool:  # noqa: ANN001
        try:
            if not win32gui.IsWindow(hwnd):
                return True
            exe = _exe_path_for_hwnd(hwnd)
            if not _is_wechat_main_exe(exe):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
            cls = win32gui.GetClassName(hwnd) or ""
            if any(x in cls for x in ("Tray", "Tool", "MessageWindow", "MMUIRender")):
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = max(right - left, 0)
            height = max(bottom - top, 0)
            if width < 120 or height < 120:
                return True
            visible = bool(win32gui.IsWindowVisible(hwnd)) and not win32gui.IsIconic(hwnd)
            if visible_only and not visible:
                return True
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "class": cls,
                    "visible": visible,
                    "width": width,
                    "height": height,
                }
            )
        except Exception:  # noqa: BLE001
            return True
        return True

    win32gui.EnumWindows(_cb, None)
    return windows


def _find_separate_chat_window(group_name: str) -> dict | None:
    """按窗口标题模糊匹配独立聊天窗（含任务栏最小化态）。"""
    import win32gui

    target = (group_name or "").strip()
    if not target:
        return None

    candidates: list[dict] = []

    def _cb(hwnd, _) -> bool:  # noqa: ANN001
        try:
            if not win32gui.IsWindow(hwnd):
                return True
            exe = _exe_path_for_hwnd(hwnd)
            if not _is_wechat_main_exe(exe):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            if not title or title in {"微信", "Weixin", "WeChat"}:
                return True
            if "登录" in title:
                return True
            cls = win32gui.GetClassName(hwnd) or ""
            if any(x in cls for x in ("Tray", "Tool", "MessageWindow", "MMUIRender")):
                return True
            if not _chat_title_matches(title, target):
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = max(right - left, 0)
            height = max(bottom - top, 0)
            visible = bool(win32gui.IsWindowVisible(hwnd))
            iconic = bool(win32gui.IsIconic(hwnd))
            # 独立聊天窗标题匹配即可；最小化时尺寸可能只有任务栏条
            candidates.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "class": cls,
                    "visible": visible,
                    "iconic": iconic,
                    "width": width,
                    "height": height,
                }
            )
        except Exception:  # noqa: BLE001
            return True
        return True

    win32gui.EnumWindows(_cb, None)
    if not candidates:
        return None
    target_n = _norm_chat_name(target)
    candidates.sort(
        key=lambda w: (
            0 if _norm_chat_name(w.get("title") or "") == target_n else 1,
            0 if not w.get("iconic") else 1,
            -(w.get("width") or 0) * (w.get("height") or 0),
        )
    )
    return candidates[0]


def _wake_wechat_if_needed() -> None:
    """仅在找不到任何可还原的独立/主窗时才按快捷键；避免把已打开窗藏掉。"""
    import win32gui

    # 有标题匹配的独立窗（哪怕最小化）→ 直接还原即可，不用 Ctrl+Alt+W
    for w in _list_wechat_top_windows(visible_only=False):
        title = (w.get("title") or "").strip()
        hwnd = int(w.get("hwnd") or 0)
        if not hwnd:
            continue
        if title and title not in {"微信", "Weixin", "WeChat"} and "登录" not in title:
            if win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd):
                print("[Wechat] 已有独立聊天窗（可还原），跳过快捷键唤醒")
                return
        if title in {"微信", "Weixin", "WeChat"} and (
            win32gui.IsWindowVisible(hwnd) or win32gui.IsIconic(hwnd)
        ):
            # 主窗存在即可；真正发送时会优先找独立窗
            if _list_wechat_top_windows(visible_only=True):
                print("[Wechat] 已有可见微信窗，跳过快捷键唤醒")
                return
    print("[Wechat] 无可用窗，尝试快捷键唤醒")
    _toggle_wechat_window()


def _key_tap(vk: int) -> None:
    import win32api
    import win32con

    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)


def _key_chord(modifiers: list[int], vk: int) -> None:
    import win32api
    import win32con

    for m in modifiers:
        win32api.keybd_event(m, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, 0, 0)
    time.sleep(0.02)
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
    for m in reversed(modifiers):
        win32api.keybd_event(m, 0, win32con.KEYEVENTF_KEYUP, 0)


def _click_screen(x: int, y: int) -> None:
    import win32api
    import win32con

    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.05)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.03)
    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def _type_into_chat_window(
    auto,
    hwnd: int,
    message: str,
    at_members: list[str],
    *,
    separate: bool,
) -> None:
    """在指定聊天窗输入并发送；不取消置顶、不隐藏。"""
    import win32con
    import win32gui

    _ = auto  # 保留参数兼容；独立窗路径用 win32 点击/按键更稳
    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("聊天窗口句柄无效")

    _restore_and_foreground(hwnd)
    time.sleep(0.2)
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
        _restore_and_foreground(hwnd)

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)

    if separate:
        # 独立窗输入区在底部工具栏上方（约 78%~84%），勿点最底排图标
        msg_x, msg_y = left + int(width * 0.50), top + int(height * 0.40)
        ix, iy = left + int(width * 0.48), top + int(height * 0.80)
        send_x, send_y = left + int(width * 0.90), top + int(height * 0.88)
    else:
        msg_x, msg_y = left + int(width * 0.68), top + int(height * 0.45)
        ix, iy = left + int(width * 0.62), top + int(height * 0.86)
        send_x, send_y = left + int(width * 0.90), top + int(height * 0.92)

    _click_screen(msg_x, msg_y)
    time.sleep(0.12)
    if not _is_foreground(hwnd):
        _restore_and_foreground(hwnd)
        time.sleep(0.12)
    _click_screen(ix, iy)
    time.sleep(0.2)

    for member in at_members or []:
        if not _is_foreground(hwnd):
            _restore_and_foreground(hwnd)
            _click_screen(ix, iy)
            time.sleep(0.15)
        _clipboard_copy("@")
        _key_chord([0x11], ord("V"))
        time.sleep(0.12)
        _clipboard_copy(member)
        _key_chord([0x11], ord("V"))
        time.sleep(0.5)
        _key_tap(0x0D)
        time.sleep(0.25)

    if not _is_foreground(hwnd):
        _restore_and_foreground(hwnd)
        _click_screen(ix, iy)
        time.sleep(0.15)

    _clipboard_copy(message or "")
    time.sleep(0.08)
    _key_chord([0x11], ord("V"))
    time.sleep(0.35)
    _key_tap(0x0D)
    time.sleep(0.2)
    if not _is_foreground(hwnd):
        _restore_and_foreground(hwnd)
        time.sleep(0.1)
    _click_screen(send_x, send_y)
    time.sleep(0.3)


def _control_from_hwnd(auto, hwnd: int):
    """用句柄包装窗口，避免 uiautomation 全局搜索卡死。"""
    if not hwnd:
        return None
    try:
        import win32gui

        if not win32gui.IsWindow(hwnd):
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        ctrl = auto.ControlFromHandle(hwnd)
        if ctrl is not None:
            return ctrl
    except Exception:  # noqa: BLE001
        pass
    try:
        # 部分版本接口名不同
        return auto.GetControlFromHandle(hwnd)
    except Exception:  # noqa: BLE001
        return None


def _ensure_wechat_window(auto):
    """找到已登录的微信主窗口；若仅有进程无窗口，则用快捷键唤起。"""
    print("[Wechat] 探测窗口…")
    info = _inspect_wechat_ui()
    print(
        "[Wechat] 探测结果："
        f"logged_in={info.get('logged_in')} visible={info.get('visible')} "
        f"hidden={info.get('hidden')} hwnd={info.get('hwnd')} "
        f"title={info.get('title')!r} class={info.get('class')!r}"
    )
    if info.get("login_page") or (info.get("visible") and not info.get("logged_in")):
        raise RuntimeError("微信未登录，仍在登录页，请先扫码/登录成功后再发送")

    wx = _find_wechat_window(auto, preferred_hwnd=int(info.get("hwnd") or 0))
    if wx is not None:
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
    wx = _find_wechat_window(auto, preferred_hwnd=int(info.get("hwnd") or 0))
    if wx is not None:
        return wx

    # 再试一次（防止第一次仍处于隐藏态）
    _toggle_wechat_window()
    time.sleep(1.0)
    info = _inspect_wechat_ui()
    if info.get("login_page") or (info.get("visible") and not info.get("logged_in")):
        raise RuntimeError("微信未登录，仍在登录页，请先扫码/登录成功后再发送")
    wx = _find_wechat_window(auto, preferred_hwnd=int(info.get("hwnd") or 0))
    if wx is not None:
        return wx

    raise RuntimeError(
        f"微信进程在运行，但无法用快捷键「{hotkey}」唤起窗口。"
        "请先手动打开微信主窗口（不要只留在托盘/图片预览），"
        "并到微信「设置 → 快捷键」确认「显示/隐藏窗口」后重试"
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
            "hwnd": 0,
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
        "hwnd": int(pick.get("hwnd") or 0),
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


def _find_wechat_window(auto, preferred_hwnd: int = 0):
    """查找本机微信主窗口。优先 Win32 句柄，避免 UIA 全局搜索卡死。"""
    try:
        auto.SetGlobalSearchTimeout(0.5)
    except Exception:  # noqa: BLE001
        pass

    if preferred_hwnd:
        wx = _control_from_hwnd(auto, preferred_hwnd)
        if wx is not None:
            print(f"[Wechat] 使用 Win32 句柄包装窗口 hwnd={preferred_hwnd}")
            return wx

    # 再扫一遍取主窗句柄
    info = _inspect_wechat_ui()
    hwnd = int(info.get("hwnd") or 0)
    if hwnd and hwnd != preferred_hwnd:
        wx = _control_from_hwnd(auto, hwnd)
        if wx is not None:
            print(f"[Wechat] 使用探测句柄包装窗口 hwnd={hwnd}")
            return wx

    # 最后才走名称搜索（可能慢，但有短超时）
    candidates = [
        ("WeChatMainWndForPC", None),
        ("Qt51514QWindowIcon", "微信"),
        ("Chrome_WidgetWin_0", "微信"),
        (None, "微信"),
        (None, "Weixin"),
    ]
    for cls_name, win_name in candidates:
        try:
            kwargs = {"searchDepth": 1}
            if cls_name:
                kwargs["ClassName"] = cls_name
            if win_name:
                kwargs["Name"] = win_name
            win = auto.WindowControl(**kwargs)
            if win.Exists(0.2, 0):
                print(f"[Wechat] UIA 命中窗口 class={cls_name} name={win_name}")
                return win
        except Exception:  # noqa: BLE001
            continue
    return None


def _window_rect(wx, hwnd: int = 0) -> tuple[int, int, int, int]:
    """优先 Win32 取矩形，避免 UIA BoundingRectangle 卡住。"""
    import win32gui

    handle = hwnd
    if not handle:
        try:
            handle = int(getattr(wx, "NativeWindowHandle", 0) or 0)
        except Exception:  # noqa: BLE001
            handle = 0
    if handle and win32gui.IsWindow(handle):
        left, top, right, bottom = win32gui.GetWindowRect(handle)
        return left, top, right, bottom
    rect = wx.BoundingRectangle
    return rect.left, rect.top, rect.right, rect.bottom


def _norm_chat_name(value: str) -> str:
    return (value or "").strip().replace(" ", "").replace("\u3000", "")


def _chat_title_matches(found: str, target: str) -> bool:
    """独立窗标题 ↔ 目标群名：全等、包含、或「群名(人数)」变体。"""
    a = _norm_chat_name(found)
    b = _norm_chat_name(target)
    if not a or not b:
        return False
    if a == b:
        return True
    # 模糊：标题包含群名，或群名包含标题（防过短误伤：较短一方至少 2 字）
    if len(b) >= 2 and b in a:
        return True
    if len(a) >= 2 and a in b and len(a) >= min(4, len(b)):
        return True
    # 微信常见：群名(12) / 群名（12）
    for left, right in (("(", ")"), ("（", "）")):
        prefix = b + left
        if a.startswith(prefix) and a.endswith(right) and len(a) <= len(prefix) + 6:
            return True
    return False


def _get_ocr_engine():
    """懒加载 OCR，避免进程启动即加载模型。"""
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            print("[Wechat] 正在加载 OCR 模型（首次较慢）…")
            from rapidocr_onnxruntime import RapidOCR

            _ocr_engine = RapidOCR()
            print("[Wechat] OCR 模型已就绪")
        return _ocr_engine


def _header_capture_box(hwnd: int) -> tuple[int, int, int, int]:
    """右侧聊天顶栏截图区域（避开左侧会话列表）。"""
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    return (
        left + int(width * 0.30),
        top + 6,
        right - 12,
        top + int(height * 0.14),
    )


def _search_result_capture_box(hwnd: int) -> tuple[int, int, int, int]:
    """左侧搜索结果列表区域（二次确认用）。"""
    import win32gui

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    return (
        left + int(width * 0.02),
        top + int(height * 0.10),
        left + int(width * 0.32),
        top + int(height * 0.52),
    )


def _click_left_search(wx, auto, hwnd: int = 0) -> None:
    """点击左侧顶部搜索框（比仅 Ctrl+F 更贴近新版微信操作）。"""
    left, top, right, bottom = _window_rect(wx, hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    x = left + int(width * 0.14)
    y = top + int(height * 0.065)
    auto.Click(x, y, waitTime=0.25)
    time.sleep(0.15)


def _click_first_search_result(wx, auto, hwnd: int = 0) -> None:
    """单击左侧搜索结果第一条（不要再回车，避免拆成独立窗）。"""
    left, top, right, bottom = _window_rect(wx, hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    x = left + int(width * 0.14)
    y = top + int(height * 0.18)
    auto.Click(x, y, waitTime=0.25)


def _type_search_keyword(auto, keyword: str) -> None:
    auto.SendKeys("{Ctrl}a", waitTime=0.05)
    auto.SendKeys("{Delete}", waitTime=0.05)
    _clipboard_copy(keyword)
    auto.SendKeys("{Ctrl}v", waitTime=0.25)


def _window_title(hwnd: int) -> str:
    import win32gui

    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:  # noqa: BLE001
        return ""




def _resolve_chat_target(auto, main_wx, main_hwnd: int, group_name: str):
    """搜群点击后：用独立窗标题模糊匹配群名确认目标（无需 OCR）。

    多等几轮：独立窗有时稍晚才出现。匹配成功则改发该窗。
    """
    sep = None
    for i in range(6):
        time.sleep(0.25 if i else 0.35)
        # 前台若已是独立窗且标题匹配，直接用
        fg = _foreground_window_info()
        fg_title = str(fg.get("title") or "")
        if (
            fg.get("is_wechat")
            and fg_title not in {"微信", "Weixin", "WeChat", ""}
            and _chat_title_matches(fg_title, group_name)
        ):
            sep = {
                "hwnd": int(fg.get("hwnd") or 0),
                "title": fg_title,
                "class": fg.get("class") or "",
            }
            print(f"[Wechat] 前台独立窗标题匹配：{fg_title!r}")
            break
        sep = _find_separate_chat_window(group_name)
        if sep:
            print(
                f"[Wechat] 枚举到独立聊天窗 title={sep.get('title')!r} "
                f"class={sep.get('class')!r} hwnd={sep.get('hwnd')}"
            )
            break

    if not sep or not sep.get("hwnd"):
        return main_wx, main_hwnd, False

    sep_hwnd = int(sep["hwnd"])
    sep_wx = _control_from_hwnd(auto, sep_hwnd)
    if sep_wx is None:
        sep_wx = main_wx
    return sep_wx, sep_hwnd, True


def _refocus_wechat(hwnd: int) -> None:
    """把前台抢回微信。搜索/点击后浏览器常会抢焦点，导致界面像卡住。"""
    if not hwnd:
        return
    try:
        _restore_and_foreground(hwnd)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.08)


def _search_open_group_fast(
    wx, auto, hwnd: int, group_name: str
) -> None:
    """快速搜群：输入群名 → 稍等结果 → 单击第一条。全程不 OCR，避免卡屏。"""
    target = (group_name or "").strip()
    if not target:
        raise RuntimeError("目标群名为空，已取消发送")

    _refocus_wechat(hwnd)
    _click_left_search(wx, auto, hwnd)
    _refocus_wechat(hwnd)
    auto.SendKeys("{Ctrl}f", waitTime=0.12)
    time.sleep(0.08)
    _type_search_keyword(auto, target)
    # 只等结果列表出现，不做截图识别
    time.sleep(0.55)
    _refocus_wechat(hwnd)
    print(f"[Wechat] 单击打开搜索首条「{target}」")
    _click_first_search_result(wx, auto, hwnd)
    time.sleep(0.2)
    _refocus_wechat(hwnd)
    time.sleep(0.15)
    _refocus_wechat(hwnd)


def _ocr_region_texts(hwnd: int, box: tuple[int, int, int, int], label: str) -> list[str]:
    """对指定屏幕区域做 OCR。"""
    import numpy as np
    from PIL import ImageGrab

    if box[2] <= box[0] or box[3] <= box[1]:
        return []
    print(f"[Wechat] OCR {label} 区域 {box}")
    image = ImageGrab.grab(bbox=box)
    arr = np.array(image)
    engine = _get_ocr_engine()
    result, _elapse = engine(arr)
    texts: list[str] = []
    for row in result or []:
        if not row or len(row) < 2:
            continue
        text = str(row[1] or "").strip()
        if text:
            texts.append(text)
    print(f"[Wechat] OCR {label} 结果：{texts[:8]}")
    return texts


def _ocr_search_result_texts(hwnd: int) -> list[str]:
    return _ocr_region_texts(hwnd, _search_result_capture_box(hwnd), "搜索结果")


def _confirm_group_by_second_search(
    wx, auto, hwnd: int, group_name: str
) -> None:
    """兼容旧调用：改为快速搜群，不再 OCR。"""
    _search_open_group_fast(wx, auto, hwnd, group_name)


def _collect_chat_header_names(wx, hwnd: int) -> list[str]:
    """采集右侧会话顶栏附近可见名称（Win32/UIA，新版微信通常为空）。"""
    import win32gui

    left, top, right, bottom = _window_rect(wx, hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    zone_l = left + int(width * 0.28)
    zone_t = top
    zone_r = right
    zone_b = top + int(height * 0.22)

    names: list[str] = []
    seen: set[str] = set()

    def _add(text: str) -> None:
        text = (text or "").strip()
        if not text or text in seen:
            return
        if text in {"微信", "Weixin", "WeChat", "搜索", "Search", "聊天信息"}:
            return
        if len(text) > 64:
            return
        seen.add(text)
        names.append(text)

    def _enum(child, _):  # noqa: ANN001
        try:
            if not win32gui.IsWindowVisible(child):
                return True
            cl, ct, cr, cb = win32gui.GetWindowRect(child)
            cx = (cl + cr) // 2
            cy = (ct + cb) // 2
            if not (zone_l <= cx <= zone_r and zone_t <= cy <= zone_b):
                return True
            _add(win32gui.GetWindowText(child) or "")
        except Exception:  # noqa: BLE001
            return True
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _enum, None)
    except Exception:  # noqa: BLE001
        pass

    try:
        import uiautomation as auto

        try:
            auto.SetGlobalSearchTimeout(0.25)
        except Exception:  # noqa: BLE001
            pass

        def _walk(ctrl, depth: int) -> None:
            if depth > 8 or len(names) >= 20:
                return
            try:
                name = (getattr(ctrl, "Name", None) or "").strip()
                if name:
                    rect = ctrl.BoundingRectangle
                    cx = (int(rect.left) + int(rect.right)) // 2
                    cy = (int(rect.top) + int(rect.bottom)) // 2
                    if zone_l <= cx <= zone_r and zone_t <= cy <= zone_b:
                        _add(name)
            except Exception:  # noqa: BLE001
                pass
            try:
                for child in ctrl.GetChildren():
                    _walk(child, depth + 1)
                    if len(names) >= 20:
                        return
            except Exception:  # noqa: BLE001
                return

        _walk(wx, 0)
    except Exception:  # noqa: BLE001
        pass

    return names


def _ocr_chat_header_texts(hwnd: int) -> list[str]:
    """截取聊天顶栏做 OCR，适配 Qt 新版微信自绘界面。"""
    import numpy as np
    import win32gui
    from PIL import ImageGrab

    if not hwnd or not win32gui.IsWindow(hwnd):
        return []

    box = _header_capture_box(hwnd)
    if box[2] <= box[0] or box[3] <= box[1]:
        return []

    print(f"[Wechat] OCR 截取顶栏区域 {box}")
    image = ImageGrab.grab(bbox=box)
    arr = np.array(image)
    engine = _get_ocr_engine()
    result, _elapse = engine(arr)
    texts: list[str] = []
    for row in result or []:
        if not row or len(row) < 2:
            continue
        text = str(row[1] or "").strip()
        if text:
            texts.append(text)
    print(f"[Wechat] OCR 结果：{texts[:8]}")
    return texts


def _assert_current_chat_is_target(wx, auto, hwnd: int, group_name: str) -> None:
    """确认当前会话就是目标群；否则抛错，禁止继续往输入框发消息。"""
    target = (group_name or "").strip()
    if not target:
        raise RuntimeError("目标群名为空，已取消发送")

    # 关掉仍可能开着的搜索浮层，再点一下聊天区
    try:
        auto.SendKeys("{Esc}", waitTime=0.15)
        time.sleep(0.2)
    except Exception:  # noqa: BLE001
        pass
    _click_chat_area(wx, auto, hwnd)
    time.sleep(0.25)

    matched = False
    hints: list[str] = []

    # 1) 经典版：精确同名控件
    try:
        auto.SetGlobalSearchTimeout(0.4)
    except Exception:  # noqa: BLE001
        pass
    try:
        exact = wx.Control(searchDepth=14, Name=target)
        if exact.Exists(0.5, 0):
            matched = True
            hints.append(target)
    except Exception:  # noqa: BLE001
        pass

    # 2) Win32 / UIA 顶栏文本
    if not matched:
        hints.extend(_collect_chat_header_names(wx, hwnd))
        for hint in hints:
            if _chat_title_matches(hint, target):
                matched = True
                break

    # 不再用 OCR 顶栏校验：会卡 CPU，微信界面像死机

    if not matched:
        shown = "、".join(hints[:6]) if hints else "未读到会话标题"
        raise RuntimeError(
            f"当前会话不是目标群「{target}」（检测到：{shown}），已取消发送，避免发错群"
        )
    print(f"[Wechat] 校验通过：当前会话为目标群「{target}」")


def _click_chat_area(wx, auto, hwnd: int = 0) -> None:
    """点击右侧聊天消息区，确保会话已激活（不要用 Esc，容易丢焦点）。"""
    left, top, right, bottom = _window_rect(wx, hwnd)
    width = max(right - left, 1)
    height = max(bottom - top, 1)
    x = left + int(width * 0.68)
    y = top + int(height * 0.45)
    auto.Click(x, y, waitTime=0.2)


def _focus_chat_input(wx, auto, hwnd: int = 0) -> None:
    """聚焦聊天输入区：Qt 新版直接点底部，避免深层 UIA 搜索卡死。"""
    left, top, right, bottom = _window_rect(wx, hwnd)
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
    """设置/取消置顶。发送流程默认不再置顶，仅用于清理历史异常状态。"""
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


def _foreground_window_info() -> dict:
    """读取当前前台窗口的 hwnd / 标题 / 类名 / 进程名。"""
    import win32gui

    hwnd = 0
    try:
        hwnd = int(win32gui.GetForegroundWindow() or 0)
    except Exception:  # noqa: BLE001
        hwnd = 0
    if not hwnd:
        return {
            "hwnd": 0,
            "title": "",
            "class": "",
            "exe": "",
            "is_wechat": False,
            "is_main": False,
        }
    try:
        title = win32gui.GetWindowText(hwnd) or ""
        cls = win32gui.GetClassName(hwnd) or ""
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width = max(right - left, 0)
        height = max(bottom - top, 0)
    except Exception:  # noqa: BLE001
        title, cls, width, height = "", "", 0, 0
    exe = _exe_path_for_hwnd(hwnd)
    exe_name = (exe or "").replace("\\", "/").lower().rsplit("/", 1)[-1]
    is_wechat = _is_wechat_main_exe(exe)
    is_main = bool(
        is_wechat
        and _window_looks_logged_in(cls, title, width, height)
        and not _window_looks_login_page(cls, title, width, height)
    )
    # 尺寸够大的微信主程序窗也认（Qt 标题常为「微信」）
    if is_wechat and not is_main and width >= 520 and height >= 480:
        if title in {"微信", "Weixin", "WeChat"} or "Qt" in cls or "WeChatMain" in cls:
            is_main = True
    return {
        "hwnd": hwnd,
        "title": title,
        "class": cls,
        "exe": exe_name,
        "width": width,
        "height": height,
        "is_wechat": is_wechat,
        "is_main": is_main,
    }


def _assert_foreground_is_wechat(*, expected_hwnd: int = 0) -> dict:
    """断言当前前台窗口是微信主界面；否则抛错，避免按键打到浏览器等其它程序。"""
    info = _foreground_window_info()
    if info.get("is_wechat") and info.get("is_main"):
        # 若已知目标句柄，允许前台是同一进程下的主窗（句柄偶发变化时仍通过）
        if expected_hwnd and info.get("hwnd") != expected_hwnd:
            print(
                f"[Wechat] 前台 hwnd={info.get('hwnd')} "
                f"与预期 {expected_hwnd} 不同，但已确认为微信主窗，继续"
            )
        return info
    raise RuntimeError(
        "快捷键唤醒后，当前前台不是微信主窗口"
        f"（前台：title={info.get('title')!r} class={info.get('class')!r} "
        f"exe={info.get('exe') or 'unknown'}）。"
        "请手动点开微信主界面后再试，并确认显示/隐藏快捷键未被占用"
    )


def _ensure_wechat_visible(hwnd: int) -> int:
    """快捷键唤醒 + 置前，并确认前台是微信；返回可用主窗 hwnd。"""
    info = _inspect_wechat_ui()
    if info.get("hwnd"):
        hwnd = int(info.get("hwnd") or hwnd)

    if info.get("hidden") or not info.get("visible"):
        print("[Wechat] 窗口隐藏，使用显示/隐藏快捷键唤醒")
        _toggle_wechat_window()
        time.sleep(1.0)
        info2 = _inspect_wechat_ui()
        if info2.get("hwnd"):
            hwnd = int(info2.get("hwnd") or hwnd)
        if info2.get("hidden") or not info2.get("visible"):
            _toggle_wechat_window()
            time.sleep(0.8)
            info3 = _inspect_wechat_ui()
            if info3.get("hwnd"):
                hwnd = int(info3.get("hwnd") or hwnd)

    _restore_and_foreground(hwnd)
    _set_topmost(hwnd, False)
    time.sleep(0.25)

    # 第一次识别前台
    fg = _foreground_window_info()
    if fg.get("is_wechat") and fg.get("is_main"):
        return int(fg.get("hwnd") or hwnd)

    print(
        f"[Wechat] 前台尚非微信（title={fg.get('title')!r} exe={fg.get('exe')}），重试"
    )
    info4 = _inspect_wechat_ui()
    if info4.get("hwnd"):
        hwnd = int(info4.get("hwnd") or hwnd)

    # 已显示但未到前台：只置前，不要再按显示/隐藏（否则可能把微信藏掉）
    if info4.get("visible") and not info4.get("hidden"):
        _restore_and_foreground(hwnd)
        _set_topmost(hwnd, False)
        time.sleep(0.3)
    else:
        _toggle_wechat_window()
        time.sleep(0.9)
        info5 = _inspect_wechat_ui()
        if info5.get("hwnd"):
            hwnd = int(info5.get("hwnd") or hwnd)
        if info5.get("hidden") or not info5.get("visible"):
            _toggle_wechat_window()
            time.sleep(0.8)
            info6 = _inspect_wechat_ui()
            if info6.get("hwnd"):
                hwnd = int(info6.get("hwnd") or hwnd)
        _restore_and_foreground(hwnd)
        _set_topmost(hwnd, False)
        time.sleep(0.3)

    _assert_foreground_is_wechat(expected_hwnd=hwnd)
    fg2 = _foreground_window_info()
    return int(fg2.get("hwnd") or hwnd)


def _leave_search_mode(auto, wx, hwnd: int) -> None:
    """轻量退出搜索浮层：只按一次 Esc，再点聊天区（多 Esc 容易把界面弄僵）。"""
    _refocus_wechat(hwnd)
    try:
        auto.SendKeys("{Esc}", waitTime=0.12)
        time.sleep(0.15)
    except Exception:  # noqa: BLE001
        pass
    _refocus_wechat(hwnd)
    try:
        _click_chat_area(wx, auto, hwnd)
        time.sleep(0.12)
    except Exception:  # noqa: BLE001
        pass
    _refocus_wechat(hwnd)


def _recover_wechat_ui_after_failure() -> None:
    """超时/失败后的尽力恢复：取消置顶 + 快捷键不会主动隐藏。"""
    info = _inspect_wechat_ui()
    hwnd = int(info.get("hwnd") or 0)
    if not hwnd:
        return
    print(f"[Wechat] 异常恢复：取消置顶 hwnd={hwnd}")
    _set_topmost(hwnd, False)
    try:
        _restore_and_foreground(hwnd)
    except Exception:  # noqa: BLE001
        pass
    # 无法在此可靠拿到 auto/wx，多发 Esc 也无窗口上下文；用户可手动 Esc
    try:
        import uiautomation as auto

        auto.SendKeys("{Esc}", waitTime=0.1)
        auto.SendKeys("{Esc}", waitTime=0.1)
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


def _clipboard_copy(text: str) -> None:
    """写入剪贴板；失败则抛错。避免依赖可能卡住的外部状态。"""
    import pyperclip

    pyperclip.copy(text or "")


def _restore_and_foreground(hwnd: int) -> None:
    """尽量把微信窗口置前。

    不用 AttachThreadInput：与 Chrome/DevTools 同机时容易死锁，
    表现为 /api/broadcast/preview 一直 pending。
    """
    import ctypes

    import win32con
    import win32gui

    if not hwnd or not win32gui.IsWindow(hwnd):
        raise RuntimeError("微信窗口句柄无效，请确认 PC 微信已打开")

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    else:
        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    if win32gui.GetForegroundWindow() == hwnd:
        return

    user32 = ctypes.windll.user32
    # Alt 键技巧：降低 SetForegroundWindow 被系统拦截的概率
    user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
    user32.keybd_event(0x12, 0, 2, 0)  # VK_MENU up

    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:  # noqa: BLE001
        pass
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

    time.sleep(0.25)


wechat_sender = WechatSender()
