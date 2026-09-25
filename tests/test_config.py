import json

import pytest

from deutsch_overlay.config import ConfigError, Settings, load_settings, save_settings


def test_missing_settings_file_uses_private_local_defaults(tmp_path):
    settings = load_settings(tmp_path / "settings.json")
    assert settings.mode == "local"
    assert settings.language_lock == "auto"
    assert settings.compare_original is False
    assert settings.online_minutes_limit == 30
    assert settings.overlay_position == "bottom-center"
    assert settings.background_color == "#000000"
    assert settings.primary_color == "#FFFFFF"


def test_settings_round_trip_omits_credentials(tmp_path):
    path = tmp_path / "settings.json"
    settings = Settings(
        mode="online",
        language_lock="de",
        compare_original=True,
        output_device_id="speaker-1",
        overlay_x=42,
        overlay_y=88,
        overlay_position="custom",
        overlay_width=750,
        font_size=26,
        opacity=0.8,
        fade_seconds=4.0,
        online_minutes_limit=12,
        background_color="#112233",
        primary_color="#F0F0F0",
        secondary_color="#AABBCC",
        border_color="#FFCC00",
        border_width=2,
        corner_radius=12,
        padding_horizontal=20,
        padding_vertical=10,
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
        {"opacity": -0.1},
        {"fade_seconds": -1},
        {"online_minutes_limit": 0},
        {"overlay_width": 10},
        {"overlay_position": "outside"},
        {"overlay_position": []},
        {"background_color": "red; border: 10px"},
        {"primary_color": "#FFF"},
        {"border_width": 9},
        {"corner_radius": -1},
        {"padding_horizontal": 41},
        {"padding_vertical": 25},
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


def test_old_saved_coordinates_remain_custom_position(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"overlay_x": 42, "overlay_y": 88}', encoding="utf-8")
    settings = load_settings(path)
    assert settings.overlay_position == "custom"
    assert (settings.overlay_x, settings.overlay_y) == (42, 88)


def test_legacy_background_opacity_keeps_same_visible_alpha(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"opacity": 0.82}', encoding="utf-8")
    settings = load_settings(path)
    assert settings.opacity == pytest.approx(0.82 * 220 / 255, abs=0.0001)
    assert settings.background_color == "#000000"


def test_background_can_be_fully_transparent():
    assert Settings(opacity=0).opacity == 0
