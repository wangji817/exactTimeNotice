"""播报分类与时间轴门控（可扩展）。

- season：赛季期内才执行
- event：活动起止期内才执行（结束后不再轮转，靠新活动配置补充）
- normal：每周/每日固定循环，不依赖赛季
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

BroadcastKind = Literal["season", "event", "normal"]

# 功能名 → 类型；新增功能时在此登记即可扩展
FEATURE_KIND: dict[str, BroadcastKind] = {
    # 赛季类
    "抢台子": "season",
    "镇国台子": "season",
    "四方都城": "season",
    # 活动类
    "群英会": "event",
    "王城争锋": "event",
    # 普通类（周固定循环）
    "天子战": "normal",
    "坚守城池": "normal",
    "流寇攻城": "normal",
    "钓鱼": "normal",
}


def parse_local_dt(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def season_active(settings) -> tuple[bool, str]:
    """返回 (是否生效, 原因)。"""
    season = getattr(settings, "season", None)
    if season is None:
        return False, "未配置赛季"
    start = parse_local_dt(getattr(season, "starts_at", "") or "")
    end = parse_local_dt(getattr(season, "ends_at", "") or "")
    if not start or not end:
        return False, "赛季起止时间未配置完整"
    if end <= start:
        return False, "赛季结束时间须晚于开始时间"
    now = datetime.now()
    if now < start:
        return False, f"赛季未开始（{start.strftime('%Y-%m-%d %H:%M')} 起）"
    if now >= end:
        return False, f"赛季已结束（至 {end.strftime('%Y-%m-%d %H:%M')}）"
    return True, "赛季进行中"


def event_active(cfg) -> tuple[bool, str]:
    """活动类：必须配置起止；窗外不播，结束后无下一轮。"""
    start = parse_local_dt(getattr(cfg, "starts_at", "") or "")
    end = parse_local_dt(getattr(cfg, "ends_at", "") or "")
    if not start or not end:
        return False, "活动期未配置（需填写开始/结束时间）"
    if end <= start:
        return False, "活动结束时间须晚于开始时间"
    now = datetime.now()
    if now < start:
        return False, f"活动未开始（{start.strftime('%Y-%m-%d %H:%M')} 起）"
    if now >= end:
        return False, f"活动已结束（至 {end.strftime('%Y-%m-%d %H:%M')}）"
    return True, "活动进行中"


def kind_of(feature: str) -> BroadcastKind:
    return FEATURE_KIND.get(feature, "normal")


def resolve_mentions(cfg) -> list[str]:
    """根据 at_everyone / members 生成微信 @ 列表（可同时启用）。"""
    out: list[str] = []
    if getattr(cfg, "at_everyone", False):
        out.append("所有人")
    seen = set(out)
    for raw in getattr(cfg, "members", None) or []:
        name = (raw or "").strip()
        if not name or name in seen or name == "所有人":
            continue
        seen.add(name)
        out.append(name)
    return out
