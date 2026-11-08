from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .broadcast_kinds import (
    event_active,
    kind_of,
    resolve_mentions,
    season_active,
)
from .config_manager import config_manager
from .models import AppSettings, TimedEventConfig
from .wechat_sender import wechat_sender

# Python weekday: Mon=0 ... Sun=6
# Config weekday: Mon=1 ... Sun=7
WEEKDAY_MAP = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


class BroadcastScheduler:
    def __init__(self) -> None:
        self.scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.reload(config_manager.get())
        config_manager.on_change(self.reload)
        self.scheduler.start()
        self._started = True
        print("[Scheduler] 已启动，配置变更将即时重载任务")

    def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False

    def reload(self, settings: AppSettings | None = None) -> None:
        settings = settings or config_manager.get()
        self.scheduler.remove_all_jobs()
        self._register_qiang_taizi(settings)
        self._register_jian_shou(settings)
        self._register_zhen_guo(settings)
        self._register_si_fang(settings)
        self._register_liu_kou(settings)
        self._register_diao_yu(settings)
        self._register_timed_event(
            settings, "群英会", "qun_ying_hui", settings.qun_ying_hui
        )
        self._register_timed_event(
            settings, "王城争锋", "wang_cheng", settings.wang_cheng
        )
        self._register_timed_event(
            settings, "天子战", "tian_zi_zhan", settings.tian_zi_zhan
        )
        print(
            f"[Scheduler] 任务已重载，当前任务数：{len(self.scheduler.get_jobs())}"
        )

    def list_jobs(self) -> list[dict]:
        jobs = []
        for job in self.scheduler.get_jobs():
            next_run = job.next_run_time
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": next_run.strftime("%Y-%m-%d %H:%M:%S")
                    if next_run
                    else None,
                }
            )
        jobs.sort(key=lambda x: x["next_run"] or "")
        return jobs

    # ---------- 抢台子（赛季类） ----------
    def _register_qiang_taizi(self, settings: AppSettings) -> None:
        cfg = settings.qiang_taizi
        if not cfg.enabled:
            return
        advance = cfg.advance_minutes
        lo = min(cfg.start_hour, cfg.monday_start_hour)
        hi = cfg.end_hour
        for hour in range(lo, hi + 1):
            fire_hour, fire_minute = _shift_back(hour, 0, advance)
            self.scheduler.add_job(
                self._fire_qiang_taizi,
                CronTrigger(
                    day_of_week="mon-sun",
                    hour=fire_hour,
                    minute=fire_minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=f"qiang_taizi_{hour}",
                name=f"抢台子-{hour:02d}:00",
                args=[hour],
                replace_existing=True,
                misfire_grace_time=60,
            )

    def _fire_qiang_taizi(self, target_hour: int) -> None:
        settings = config_manager.get()
        cfg = settings.qiang_taizi
        if not cfg.enabled:
            return
        ok, reason = season_active(settings)
        if not ok:
            print(f"[抢台子] 赛季门控：{reason}")
            return
        now = datetime.now()
        today = now.isoweekday()
        day_start = cfg.monday_start_hour if today == 1 else cfg.start_hour
        if target_hour < day_start or target_hour > cfg.end_hour:
            return
        if cfg.skip_sunday_24 and target_hour == 24 and now.weekday() == 6:
            print("[抢台子] 周日24点跳过播报")
            return
        if not _within_broadcast_window(settings, target_hour=target_hour):
            print(f"[抢台子] {target_hour:02d}:00 超出忙时窗口，跳过")
            return
        zg = settings.zhen_guo
        if (
            zg.enabled
            and today in (zg.weekdays or [])
            and target_hour in (zg.hours or [])
            and target_hour
            >= (zg.monday_start_hour if today == 1 else zg.start_hour)
        ):
            print(f"[抢台子] {target_hour:02d}:00 与镇国台子冲突，跳过")
            return
        msg = cfg.message.format(advance=cfg.advance_minutes, hour=target_hour)
        wechat_sender.send("抢台子", msg, at_members=resolve_mentions(cfg))

    # ---------- 坚守城池（普通类） ----------
    def _register_jian_shou(self, settings: AppSettings) -> None:
        cfg = settings.jian_shou
        if not cfg.enabled or not cfg.cities:
            return
        advance = cfg.advance_minutes
        for raw in cfg.times:
            parsed = _parse_hm(raw)
            if not parsed:
                continue
            hour, minute = parsed
            label = f"{hour:02d}:{minute:02d}"
            fire_hour, fire_minute = _shift_back(hour, minute, advance)
            self.scheduler.add_job(
                self._fire_jian_shou,
                CronTrigger(
                    day_of_week="mon-sun",
                    hour=fire_hour,
                    minute=fire_minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=f"jian_shou_{label}",
                name=f"坚守-{label}（提前{advance}分）",
                args=[hour, minute],
                replace_existing=True,
                misfire_grace_time=60,
            )

    def _fire_jian_shou(
        self, target_hour: int = 12, target_minute: int = 0
    ) -> None:
        settings = config_manager.get()
        cfg = settings.jian_shou
        if not cfg.enabled or not cfg.cities:
            return
        if not _within_broadcast_window(settings, target_hour=target_hour):
            print(
                f"[坚守] {target_hour:02d}:{target_minute:02d} 超出忙时窗口，跳过"
            )
            return
        cities = "、".join(cfg.cities)
        msg = cfg.message.format(
            cities=cities,
            advance=cfg.advance_minutes,
            time=f"{target_hour:02d}:{target_minute:02d}",
        )
        wechat_sender.send("坚守城池", msg, at_members=resolve_mentions(cfg))

    # ---------- 镇国台子（赛季类） ----------
    def _register_zhen_guo(self, settings: AppSettings) -> None:
        cfg = settings.zhen_guo
        if not cfg.enabled or not cfg.weekdays or not cfg.hours:
            return
        py_days = sorted(
            {WEEKDAY_MAP[d] for d in cfg.weekdays if d in WEEKDAY_MAP}
        )
        if not py_days:
            return
        day_of_week = ",".join(str(d) for d in py_days)
        advance = cfg.advance_minutes
        for hour in cfg.hours:
            if hour < 0 or hour > 23:
                continue
            fire_hour, fire_minute = _shift_back(hour, 0, advance)
            self.scheduler.add_job(
                self._fire_zhen_guo,
                CronTrigger(
                    day_of_week=day_of_week,
                    hour=fire_hour,
                    minute=fire_minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=f"zhen_guo_{hour}",
                name=f"镇国台子-{hour:02d}:00",
                args=[hour],
                replace_existing=True,
                misfire_grace_time=60,
            )

    def _fire_zhen_guo(self, target_hour: int) -> None:
        settings = config_manager.get()
        cfg = settings.zhen_guo
        if not cfg.enabled:
            return
        ok, reason = season_active(settings)
        if not ok:
            print(f"[镇国] 赛季门控：{reason}")
            return
        today = datetime.now().isoweekday()
        if today not in cfg.weekdays:
            return
        day_start = cfg.monday_start_hour if today == 1 else cfg.start_hour
        if target_hour < day_start:
            print(f"[镇国] 起始过滤，跳过 {target_hour:02d}:00")
            return
        if not _within_broadcast_window(settings, target_hour=target_hour):
            print(f"[镇国] {target_hour:02d}:00 超出忙时窗口，跳过")
            return
        msg = cfg.message.format(advance=cfg.advance_minutes, hour=target_hour)
        wechat_sender.send(
            "镇国台子", msg, at_members=resolve_mentions(cfg)
        )

    # ---------- 四方都城（赛季类） ----------
    def _register_si_fang(self, settings: AppSettings) -> None:
        cfg = settings.si_fang
        if not cfg.enabled or not cfg.weekdays:
            return
        has_weekday = any(d in cfg.weekdays for d in (1, 2, 3, 4, 5)) and bool(
            cfg.cities
        )
        has_weekend = any(d in cfg.weekdays for d in (6, 7)) and bool(
            cfg.weekend_cities
        )
        if not has_weekday and not has_weekend:
            return
        py_days = sorted(
            {WEEKDAY_MAP[d] for d in cfg.weekdays if d in WEEKDAY_MAP}
        )
        if not py_days:
            return
        day_of_week = ",".join(str(d) for d in py_days)
        fire_hour, fire_minute = _shift_back(
            cfg.hour, cfg.minute, cfg.advance_minutes
        )
        self.scheduler.add_job(
            self._fire_si_fang,
            CronTrigger(
                day_of_week=day_of_week,
                hour=fire_hour,
                minute=fire_minute,
                second=0,
                timezone="Asia/Shanghai",
            ),
            id="si_fang",
            name=(
                f"四方都城-{cfg.hour:02d}:{cfg.minute:02d}"
                f"（提前{cfg.advance_minutes}分）"
            ),
            replace_existing=True,
            misfire_grace_time=60,
        )

    def _fire_si_fang(self) -> None:
        settings = config_manager.get()
        cfg = settings.si_fang
        if not cfg.enabled:
            return
        ok, reason = season_active(settings)
        if not ok:
            print(f"[四方] 赛季门控：{reason}")
            return
        today = datetime.now().isoweekday()
        if today not in cfg.weekdays:
            return
        if not _within_broadcast_window(settings, target_hour=cfg.hour):
            print(f"[四方] {cfg.hour:02d}:{cfg.minute:02d} 超出忙时窗口，跳过")
            return
        if today in (6, 7):
            mode = cfg.weekend_mode
            city_list = [
                c.strip() for c in (cfg.weekend_cities or []) if c and c.strip()
            ]
        else:
            mode = cfg.mode
            city_list = [
                c.strip() for c in (cfg.cities or []) if c and c.strip()
            ]
        if not city_list:
            print(f"[四方都城] 星期{today} 未配置都城，跳过")
            return
        cities = "、".join(city_list)
        msg = cfg.message.format(
            mode=mode,
            cities=cities,
            advance=cfg.advance_minutes,
            hour=cfg.hour,
            minute=cfg.minute,
        )
        wechat_sender.send("四方都城", msg, at_members=resolve_mentions(cfg))

    # ---------- 流寇攻城（普通类） ----------
    def _register_liu_kou(self, settings: AppSettings) -> None:
        cfg = settings.liu_kou
        if not cfg.enabled or not cfg.times:
            return
        advance = cfg.advance_minutes
        for raw in cfg.times:
            parsed = _parse_hm(raw)
            if not parsed:
                continue
            hour, minute = parsed
            label = f"{hour:02d}:{minute:02d}"
            fire_hour, fire_minute = _shift_back(hour, minute, advance)
            self.scheduler.add_job(
                self._fire_liu_kou,
                CronTrigger(
                    day_of_week="mon-sun",
                    hour=fire_hour,
                    minute=fire_minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=f"liu_kou_{label}",
                name=f"流寇攻城-{label}（提前{advance}分）",
                args=[hour, minute],
                replace_existing=True,
                misfire_grace_time=60,
            )

    def _fire_liu_kou(self, target_hour: int, target_minute: int) -> None:
        settings = config_manager.get()
        cfg = settings.liu_kou
        if not cfg.enabled:
            return
        if not _within_broadcast_window(settings, target_hour=target_hour):
            print(
                f"[流寇] {target_hour:02d}:{target_minute:02d} 超出忙时窗口，跳过"
            )
            return
        label = f"{target_hour:02d}:{target_minute:02d}"
        msg = cfg.message.format(
            advance=cfg.advance_minutes,
            time=label,
            hour=target_hour,
            minute=target_minute,
        )
        wechat_sender.send("流寇攻城", msg, at_members=resolve_mentions(cfg))

    # ---------- 钓鱼（普通类） ----------
    def _register_diao_yu(self, settings: AppSettings) -> None:
        cfg = settings.diao_yu
        if not cfg.enabled or not cfg.times:
            return
        advance = cfg.advance_minutes
        for raw in cfg.times:
            parsed = _parse_hm(raw)
            if not parsed:
                continue
            hour, minute = parsed
            label = f"{hour:02d}:{minute:02d}"
            fire_hour, fire_minute = _shift_back(hour, minute, advance)
            self.scheduler.add_job(
                self._fire_diao_yu,
                CronTrigger(
                    day_of_week="mon-sun",
                    hour=fire_hour,
                    minute=fire_minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=f"diao_yu_{label}",
                name=f"钓鱼-{label}（提前{advance}分）",
                args=[hour, minute],
                replace_existing=True,
                misfire_grace_time=60,
            )

    def _fire_diao_yu(self, target_hour: int, target_minute: int) -> None:
        settings = config_manager.get()
        cfg = settings.diao_yu
        if not cfg.enabled:
            return
        if not _within_broadcast_window(settings, target_hour=target_hour):
            print(
                f"[钓鱼] {target_hour:02d}:{target_minute:02d} 超出忙时窗口，跳过"
            )
            return
        label = f"{target_hour:02d}:{target_minute:02d}"
        msg = cfg.message.format(
            advance=cfg.advance_minutes,
            time=label,
            hour=target_hour,
            minute=target_minute,
        )
        wechat_sender.send("钓鱼", msg, at_members=resolve_mentions(cfg))

    # ---------- 活动类（固定活动期，结束后无下一轮） ----------
    def _register_timed_event(
        self,
        settings: AppSettings,
        feature: str,
        key: str,
        cfg: TimedEventConfig,
    ) -> None:
        if not cfg.enabled or not cfg.weekdays:
            return
        # 活动期未配齐时仍注册 cron，触发时由 event_active 拦截并提示
        py_days = sorted(
            {WEEKDAY_MAP[d] for d in cfg.weekdays if d in WEEKDAY_MAP}
        )
        if not py_days:
            return
        day_of_week = ",".join(str(d) for d in py_days)
        fire_hour, fire_minute = _shift_back(
            cfg.hour, cfg.minute, cfg.advance_minutes
        )
        start = f"{cfg.hour:02d}:{cfg.minute:02d}"
        self.scheduler.add_job(
            self._fire_timed_event,
            CronTrigger(
                day_of_week=day_of_week,
                hour=fire_hour,
                minute=fire_minute,
                second=0,
                timezone="Asia/Shanghai",
            ),
            id=f"event_{key}",
            name=f"{feature}-{start}（提前{cfg.advance_minutes}分）",
            args=[key, feature],
            replace_existing=True,
            misfire_grace_time=60,
        )

    def _fire_timed_event(self, key: str, feature: str) -> None:
        settings = config_manager.get()
        cfg: TimedEventConfig | None = getattr(settings, key, None)
        if cfg is None or not cfg.enabled:
            return
        today = datetime.now().isoweekday()
        if today not in cfg.weekdays:
            return
        # 活动类才校验活动期；普通类（如天子战）按周循环，不依赖活动期
        if kind_of(feature) == "event":
            ok, reason = event_active(cfg)
            if not ok:
                print(f"[{feature}] 活动门控：{reason}")
                return
        if not _within_broadcast_window(settings, target_hour=cfg.hour):
            print(
                f"[{feature}] {cfg.hour:02d}:{cfg.minute:02d} 超出忙时窗口，跳过"
            )
            return
        start = f"{cfg.hour:02d}:{cfg.minute:02d}"
        end = f"{cfg.end_hour:02d}:{cfg.end_minute:02d}"
        msg = (cfg.message or f"{feature}还有 {{advance}} 分钟开赛").format(
            advance=cfg.advance_minutes,
            start=start,
            end=end,
            hour=cfg.hour,
            minute=cfg.minute,
        )
        wechat_sender.send(feature, msg, at_members=resolve_mentions(cfg))


def _within_broadcast_window(
    settings: AppSettings, *, target_hour: int
) -> bool:
    """按「活动整点」判断是否落在忙时窗口；24 点视为超出常规窗口。"""
    win = settings.broadcast_window
    if not win.enabled:
        return True
    if target_hour >= 24:
        return False
    start = min(win.start_hour, win.end_hour)
    end = max(win.start_hour, win.end_hour)
    return start <= target_hour <= end


def _parse_hm(value: str) -> tuple[int, int] | None:
    """解析 HH:MM，非法则返回 None。"""
    text = (value or "").strip()
    if ":" not in text:
        return None
    try:
        hour_s, minute_s = text.split(":", 1)
        hour = int(hour_s)
        minute = int(minute_s)
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour, minute


def _shift_back(hour: int, minute: int, advance: int) -> tuple[int, int]:
    """将 hour:minute 往前推 advance 分钟。hour 可为 24 表示次日 0 点。"""
    total = hour * 60 + minute - advance
    total = total % (24 * 60)
    return total // 60, total % 60


broadcast_scheduler = BroadcastScheduler()
