from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AuthConfig(BaseModel):
    """本机入口会话配置（页面不再暴露，一键进入即可）。"""

    server_port: int = 8765


class WechatConfig(BaseModel):
    group_name: str = ""
    test_group_name: str = "三国测试组"
    dry_run: bool = True
    wake_hotkey: str = "ctrl+alt+w"


class BroadcastWindowConfig(BaseModel):
    enabled: bool = True
    start_hour: int = Field(default=9, ge=0, le=23)
    end_hour: int = Field(default=23, ge=0, le=23)


class SeasonConfig(BaseModel):
    """赛季时间轴：赛季类播报仅在 [starts_at, ends_at) 内执行。"""

    name: str = "S2"
    season_days: int = Field(default=45, ge=1, le=365)
    phase_days: int = Field(default=15, ge=1, le=365)
    starts_at: str = ""
    ends_at: str = ""


class MentionFields(BaseModel):
    """所有播报通用：可选 @所有人 / @部分成员。"""

    at_everyone: bool = False
    members: list[str] = Field(default_factory=list)


class QiangTaiziConfig(MentionFields):
    enabled: bool = False
    advance_minutes: int = Field(default=5, ge=0, le=59)
    start_hour: int = Field(default=9, ge=0, le=24)
    monday_start_hour: int = Field(default=10, ge=0, le=24)
    end_hour: int = Field(default=23, ge=0, le=24)
    skip_sunday_24: bool = True
    message: str = "⏰ 抢台子提醒\n还有 {advance} 分钟到整点，请主公准备上线～"


class JianShouConfig(MentionFields):
    enabled: bool = False
    advance_minutes: int = Field(default=5, ge=0, le=59)
    times: list[str] = Field(default_factory=lambda: ["12:00", "12:30"])
    cities: list[str] = Field(default_factory=lambda: ["益州", "扬州", "兖州"])
    message: str = "🛡️ 坚守城池\n还有 {advance} 分钟，本时段请守好：{cities}"

    @model_validator(mode="before")
    @classmethod
    def _migrate_hours(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if not data.get("times"):
            data["times"] = ["12:00", "12:30"]
        data.pop("hours", None)
        return data


class ZhenGuoConfig(MentionFields):
    enabled: bool = False
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])
    hours: list[int] = Field(default_factory=lambda: [9, 10, 11, 12, 13])
    start_hour: int = Field(default=9, ge=0, le=23)
    monday_start_hour: int = Field(default=10, ge=0, le=23)
    advance_minutes: int = Field(default=5, ge=0, le=59)
    message: str = "🏯 镇国台子\n还有 {advance} 分钟，相关主公请就位～"


class SiFangConfig(MentionFields):
    enabled: bool = False
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5, 6, 7])
    hour: int = Field(default=13, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    advance_minutes: int = Field(default=5, ge=0, le=59)
    mode: Literal["进攻", "防守"] = "进攻"
    cities: list[str] = Field(
        default_factory=lambda: ["许昌", "成都", "长沙", "建业"]
    )
    weekend_mode: Literal["进攻", "防守"] = "防守"
    weekend_cities: list[str] = Field(default_factory=lambda: ["洛阳"])
    message: str = "⚔️ 四方都城 · {mode}\n目标 {cities}，还有 {advance} 分钟"

    @model_validator(mode="before")
    @classmethod
    def _migrate_weekend(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "weekend_cities" not in data and data.get("weekend_city"):
            city = str(data.get("weekend_city") or "").strip()
            data["weekend_cities"] = [city] if city else ["洛阳"]
        data.pop("weekend_city", None)
        if "weekend_mode" not in data:
            data["weekend_mode"] = "防守"
        return data


class LiuKouConfig(MentionFields):
    enabled: bool = False
    times: list[str] = Field(default_factory=lambda: ["10:00", "15:00"])
    advance_minutes: int = Field(default=5, ge=0, le=59)
    message: str = "🏴 流寇攻城\n还有 {advance} 分钟，请注意守城～"


class DiaoYuConfig(MentionFields):
    """普通类：钓鱼（每日固定时刻循环）。"""

    enabled: bool = True
    times: list[str] = Field(default_factory=lambda: ["18:00"])
    advance_minutes: int = Field(default=5, ge=0, le=59)
    message: str = "🎣 钓鱼提醒\n还有 {advance} 分钟（{time}），别忘了上线钓鱼～"


class TimedEventConfig(MentionFields):
    """活动类：周期时刻 + 活动起止期；期外不播，结束后无自动下一轮。"""

    enabled: bool = False
    weekdays: list[int] = Field(default_factory=list)
    hour: int = Field(default=20, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    end_hour: int = Field(default=20, ge=0, le=23)
    end_minute: int = Field(default=30, ge=0, le=59)
    advance_minutes: int = Field(default=5, ge=0, le=59)
    starts_at: str = ""
    ends_at: str = ""
    message: str = ""


class AppSettings(BaseModel):
    auth: AuthConfig = Field(default_factory=AuthConfig)
    wechat: WechatConfig = Field(default_factory=WechatConfig)
    broadcast_window: BroadcastWindowConfig = Field(
        default_factory=BroadcastWindowConfig
    )
    season: SeasonConfig = Field(default_factory=SeasonConfig)
    qiang_taizi: QiangTaiziConfig = Field(default_factory=QiangTaiziConfig)
    jian_shou: JianShouConfig = Field(default_factory=JianShouConfig)
    zhen_guo: ZhenGuoConfig = Field(default_factory=ZhenGuoConfig)
    si_fang: SiFangConfig = Field(default_factory=SiFangConfig)
    liu_kou: LiuKouConfig = Field(default_factory=LiuKouConfig)
    diao_yu: DiaoYuConfig = Field(default_factory=DiaoYuConfig)
    qun_ying_hui: TimedEventConfig = Field(
        default_factory=lambda: TimedEventConfig(
            enabled=True,
            weekdays=[7],
            hour=20,
            minute=0,
            end_hour=20,
            end_minute=30,
            advance_minutes=5,
            message=(
                "🏆 群英会\n"
                "还有 {advance} 分钟开赛（{start}–{end}）\n"
                "请主公准时到场～"
            ),
        )
    )
    wang_cheng: TimedEventConfig = Field(
        default_factory=lambda: TimedEventConfig(
            enabled=True,
            weekdays=[2, 4],
            hour=21,
            minute=0,
            end_hour=21,
            end_minute=30,
            advance_minutes=5,
            message=(
                "🏰 王城争锋\n"
                "还有 {advance} 分钟开赛（{start}–{end}）\n"
                "请队伍成员准时上线～"
            ),
        )
    )
    tian_zi_zhan: TimedEventConfig = Field(
        default_factory=lambda: TimedEventConfig(
            enabled=True,
            weekdays=[3, 7],
            hour=21,
            minute=0,
            end_hour=22,
            end_minute=0,
            advance_minutes=5,
            message=(
                "👑 天子战\n"
                "还有 {advance} 分钟开战（{start}–{end}）\n"
                "请主公做好准备～"
            ),
        )
    )


class BroadcastLog(BaseModel):
    time: str
    feature: str
    message: str
    success: bool
    detail: str = ""
