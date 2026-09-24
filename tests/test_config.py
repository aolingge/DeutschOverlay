import json

import pytest

from deutsch_overlay.config import ConfigError, Settings, load_settings, save_settings


def test_missing_settings_file_uses_private_local_defaults(tmp_path):
    settings = load_settings(tmp_path / "settings.json")
    assert settings.mode == "local"
    assert settings.language_lock == "auto"
    assert settings.compare_original is False
    assert settings.online_minutes_limit == 30


def test_settings_round_trip_omits_credentials(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(
        mode="online",
        language_lock="de",
        compare_original=True,
        output_device_id="speaker-1",
        overlay_x=42,
        overlay_y=88,
        overlay_width=750,
        font_size=26,
        opacity=0.8,
        fade_seconds=4.0,
        online_minutes_limit=12,
    )
    save_settings(path, settings)
    assert load_settings(path) == settings
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "key" not in data
    assert "password" not in data


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "automatic"},
        {"language_lock": "fr"},
        {"opacity": 0},
        {"fade_seconds": -1},
        {"online_minutes_limit": 0},
        {"overlay_width": 10},
    ],
)
def test_invalid_settings_are_rejected(kwargs):
    with pytest.raises(ConfigError):
        Settings(**kwargs)


def test_invalid_json_is_not_silently_replaced(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ConfigError, match="settings file"):
        load_settings(path)


def test_unknown_settings_are_rejected(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"credential": "should-not-load"}', encoding="utf-8")
    with pytest.raises(ConfigError, match="unsupported"):
        load_settings(path)
