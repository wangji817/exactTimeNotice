from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config_manager import config_manager
from .models import AppSettings
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

    # ---------- 抢台子 ----------
    def _register_qiang_taizi(self, settings: AppSettings) -> None:
        cfg = settings.qiang_taizi
        if not cfg.enabled:
            return
        advance = cfg.advance_minutes
        for hour in range(cfg.start_hour, cfg.end_hour + 1):
            # 目标整点 hour:00，提前 advance 分钟播报
            fire_hour, fire_minute = _shift_back(hour, 0, advance)
            job_id = f"qiang_taizi_{hour}"
            self.scheduler.add_job(
                self._fire_qiang_taizi,
                CronTrigger(
                    day_of_week="mon-sun",
                    hour=fire_hour,
                    minute=fire_minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=job_id,
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
        now = datetime.now()
        # 周日 24 点（即周日 23:55 触发的目标整点 24）不播报
        if cfg.skip_sunday_24 and target_hour == 24 and now.weekday() == 6:
            print("[抢台子] 周日24点跳过播报")
            return
        msg = cfg.message.format(advance=cfg.advance_minutes, hour=target_hour)
        wechat_sender.send("抢台子", msg)

    # ---------- 坚守城池 ----------
    def _register_jian_shou(self, settings: AppSettings) -> None:
        cfg = settings.jian_shou
        if not cfg.enabled or not cfg.cities:
            return
        for raw in cfg.times:
            parsed = _parse_hm(raw)
            if not parsed:
                continue
            hour, minute = parsed
            label = f"{hour:02d}:{minute:02d}"
            self.scheduler.add_job(
                self._fire_jian_shou,
                CronTrigger(
                    day_of_week="mon-sun",
                    hour=hour,
                    minute=minute,
                    second=0,
                    timezone="Asia/Shanghai",
                ),
                id=f"jian_shou_{label}",
                name=f"坚守-{label}",
                replace_existing=True,
                misfire_grace_time=60,
            )

    def _fire_jian_shou(self) -> None:
        settings = config_manager.get()
        cfg = settings.jian_shou
        if not cfg.enabled or not cfg.cities:
            return
        cities = "】【".join(cfg.cities)
        msg = cfg.message.format(cities=cities)
        wechat_sender.send("坚守城池", msg)

    # ---------- 镇国台子 ----------
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
        # 双重校验星期
        today = datetime.now().isoweekday()  # Mon=1 ... Sun=7
        if today not in cfg.weekdays:
            return
        msg = cfg.message.format(advance=cfg.advance_minutes, hour=target_hour)
        wechat_sender.send("镇国台子", msg, at_members=cfg.members)

    # ---------- 四方都城 ----------
    def _register_si_fang(self, settings: AppSettings) -> None:
        cfg = settings.si_fang
        if not cfg.enabled or not cfg.weekdays or not cfg.cities:
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
        if not cfg.enabled or not cfg.cities:
            return
        today = datetime.now().isoweekday()
        if today not in cfg.weekdays:
            return
        cities = "】【".join(cfg.cities)
        msg = cfg.message.format(
            mode=cfg.mode,
            cities=cities,
            advance=cfg.advance_minutes,
            hour=cfg.hour,
            minute=cfg.minute,
        )
        wechat_sender.send("四方都城", msg)

    # ---------- 流寇攻城 ----------
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
        label = f"{target_hour:02d}:{target_minute:02d}"
        msg = cfg.message.format(
            advance=cfg.advance_minutes,
            time=label,
            hour=target_hour,
            minute=target_minute,
        )
        wechat_sender.send("流寇攻城", msg)


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
    # 24:00 往前推后仍落在当天；负数则回到前一天对应时刻（cron 按当天时刻触发）
    total = total % (24 * 60)
    return total // 60, total % 60


broadcast_scheduler = BroadcastScheduler()
