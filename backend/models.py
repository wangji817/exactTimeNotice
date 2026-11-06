from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class AuthConfig(BaseModel):
    """本机入口会话配置（页面不再暴露，一键进入即可）。"""

    server_port: int = 8765


class WechatConfig(BaseModel):
    group_name: str = ""
    dry_run: bool = True
    # 与微信「设置 → 快捷键 → 显示/隐藏窗口」保持一致，默认 Ctrl+Alt+W
    wake_hotkey: str = "ctrl+alt+w"


class QiangTaiziConfig(BaseModel):
    enabled: bool = True
    advance_minutes: int = Field(default=5, ge=0, le=59)
    start_hour: int = Field(default=9, ge=0, le=24)
    end_hour: int = Field(default=24, ge=0, le=24)
    skip_sunday_24: bool = True
    message: str = "⏰ 抢台子提醒：还有 {advance} 分钟到整点，请准备抢台！"


class JianShouConfig(BaseModel):
    enabled: bool = True
    # 播报时刻，格式 HH:MM；默认 12:00、12:30
    times: list[str] = Field(default_factory=lambda: ["12:00", "12:30"])
    cities: list[str] = Field(default_factory=lambda: ["益州", "扬州", "兖州"])
    message: str = "🛡️ 坚守提醒：请坚守【{cities}】！"

    @model_validator(mode="before")
    @classmethod
    def _migrate_hours(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        times = data.get("times")
        if not times:
            # 旧版 hours=[12,13] 统一迁到新默认 12:00 / 12:30
            data["times"] = ["12:00", "12:30"]
        data.pop("hours", None)
        return data


class ZhenGuoConfig(BaseModel):
    enabled: bool = False
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    hours: list[int] = Field(default_factory=lambda: [20])
    advance_minutes: int = Field(default=5, ge=0, le=59)
    members: list[str] = Field(default_factory=list)
    message: str = "🏯 镇国台子提醒：还有 {advance} 分钟到整点，请相关成员就位！"


class SiFangConfig(BaseModel):
    enabled: bool = True
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    hour: int = Field(default=13, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    mode: Literal["进攻", "防守"] = "进攻"
    cities: list[str] = Field(
        default_factory=lambda: ["许昌", "成都", "长沙", "建业"]
    )
    message: str = "⚔️ {mode}四方都城：【{cities}】！"


class AppSettings(BaseModel):
    auth: AuthConfig = Field(default_factory=AuthConfig)
    wechat: WechatConfig = Field(default_factory=WechatConfig)
    qiang_taizi: QiangTaiziConfig = Field(default_factory=QiangTaiziConfig)
    jian_shou: JianShouConfig = Field(default_factory=JianShouConfig)
    zhen_guo: ZhenGuoConfig = Field(default_factory=ZhenGuoConfig)
    si_fang: SiFangConfig = Field(default_factory=SiFangConfig)


class BroadcastLog(BaseModel):
    time: str
    feature: str
    message: str
    success: bool
    detail: str = ""
