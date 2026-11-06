from __future__ import annotations

import json
import threading
from pathlib import Path

from .models import AppSettings

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.json"


class ConfigManager:
    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._settings = self._load()
        self._listeners: list = []

    def _load(self) -> AppSettings:
        if not self.path.exists():
            settings = AppSettings()
            self._write(settings)
            return settings
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppSettings.model_validate(data)

    def _write(self, settings: AppSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                settings.model_dump(),
                f,
                ensure_ascii=False,
                indent=2,
            )

    def get(self) -> AppSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, settings: AppSettings) -> AppSettings:
        with self._lock:
            self._settings = settings.model_copy(deep=True)
            self._write(self._settings)
            snapshot = self._settings.model_copy(deep=True)
        for listener in list(self._listeners):
            try:
                listener(snapshot)
            except Exception as exc:  # noqa: BLE001
                print(f"[ConfigManager] listener error: {exc}")
        return snapshot

    def patch(self, partial: dict) -> AppSettings:
        with self._lock:
            current = self._settings.model_dump()
            merged = _deep_merge(current, partial)
            updated = AppSettings.model_validate(merged)
            self._settings = updated
            self._write(self._settings)
            snapshot = self._settings.model_copy(deep=True)
        for listener in list(self._listeners):
            try:
                listener(snapshot)
            except Exception as exc:  # noqa: BLE001
                print(f"[ConfigManager] listener error: {exc}")
        return snapshot

    def on_change(self, callback) -> None:
        self._listeners.append(callback)


def _deep_merge(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in patch.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


config_manager = ConfigManager()
