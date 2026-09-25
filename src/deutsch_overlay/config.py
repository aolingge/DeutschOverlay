"""Validated application settings, excluding credentials and captured content."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """The settings file or a settings value is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    mode: str = "local"
    language_lock: str = "auto"
    compare_original: bool = False
    output_device_id: str | None = None
    overlay_x: int | None = None
    overlay_y: int | None = None
    overlay_position: str = "bottom-center"
    overlay_width: int = 840
    font_size: int = 28
    opacity: float = 0.82
    fade_seconds: float = 3.0
    online_minutes_limit: int = 30

    def __post_init__(self) -> None:
        if self.mode not in {"local", "online"}:
            raise ConfigError("mode must be local or online")
        if self.language_lock not in {"auto", "de", "en", "zh"}:
            raise ConfigError("language_lock must be auto, de, en, or zh")
        if type(self.compare_original) is not bool:
            raise ConfigError("compare_original must be true or false")
        if self.output_device_id is not None and not isinstance(self.output_device_id, str):
            raise ConfigError("output_device_id must be text or null")
        for name in ("overlay_x", "overlay_y"):
            value = getattr(self, name)
            if value is not None and type(value) is not int:
                raise ConfigError(f"{name} must be an integer or null")
        if type(self.overlay_position) is not str or self.overlay_position not in {
            "top-left", "top-center", "top-right",
            "middle-left", "middle-center", "middle-right",
            "bottom-left", "bottom-center", "bottom-right", "custom",
        }:
            raise ConfigError("overlay_position is invalid")
        if type(self.overlay_width) is not int or not 240 <= self.overlay_width <= 3840:
            raise ConfigError("overlay_width must be between 240 and 3840")
        if type(self.font_size) is not int or not 12 <= self.font_size <= 72:
            raise ConfigError("font_size must be between 12 and 72")
        if isinstance(self.opacity, bool) or not isinstance(self.opacity, (float, int)) or not 0.1 <= self.opacity <= 1:
            raise ConfigError("opacity must be between 0.1 and 1")
        if isinstance(self.fade_seconds, bool) or not isinstance(self.fade_seconds, (float, int)) or not 0 <= self.fade_seconds <= 30:
            raise ConfigError("fade_seconds must be between 0 and 30")
        if type(self.online_minutes_limit) is not int or not 1 <= self.online_minutes_limit <= 1440:
            raise ConfigError("online_minutes_limit must be between 1 and 1440")


def load_settings(path: Path) -> Settings:
    """Load settings, returning defaults only when no file exists."""
    if not path.exists():
        return Settings()
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("settings file could not be read") from exc
    if not isinstance(data, dict):
        raise ConfigError("settings file must contain an object")
    if "overlay_position" not in data and data.get("overlay_x") is not None and data.get("overlay_y") is not None:
        data["overlay_position"] = "custom"
    try:
        return Settings(**data)
    except TypeError as exc:
        raise ConfigError("settings file contains unsupported fields") from exc


def save_settings(path: Path, settings: Settings) -> None:
    """Replace the settings file atomically without storing credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
            suffix=".tmp", delete=False,
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(asdict(settings), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
