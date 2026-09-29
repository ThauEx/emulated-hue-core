"""Tests for emulated_hue.controllers.wiz_direct's pure helper functions."""
from emulated_hue.controllers.wiz_direct import (
    _color_temperature_to_pilot_params,
    _rgb_to_pilot_params,
)


def test_rgb_to_pilot_params_normalizes_peak_to_255():
    params = _rgb_to_pilot_params((0.5, 0.25, 0.0), brightness=None)
    assert params["r"] == 255
    assert params["g"] == 128
    assert params["b"] == 0


def test_rgb_to_pilot_params_defaults_to_full_dimming_without_brightness():
    params = _rgb_to_pilot_params((1.0, 0.0, 0.0), brightness=None)
    assert params["dimming"] == 100


def test_rgb_to_pilot_params_uses_brightness_for_dimming():
    params = _rgb_to_pilot_params((1.0, 0.0, 0.0), brightness=0.5)
    assert params["dimming"] == 50


def test_rgb_to_pilot_params_clamps_dimming_floor():
    # WiZ rejects/clamps below 10 - a near-zero brightness must not send 0/1.
    params = _rgb_to_pilot_params((1.0, 0.0, 0.0), brightness=0.02)
    assert params["dimming"] == 10


def test_rgb_to_pilot_params_handles_black():
    params = _rgb_to_pilot_params((0.0, 0.0, 0.0), brightness=1.0)
    assert (params["r"], params["g"], params["b"]) == (0, 0, 0)


def test_color_temperature_to_pilot_params_converts_mireds_to_kelvin():
    # 250 mireds -> 4000K
    params = _color_temperature_to_pilot_params(250, brightness=None)
    assert params["temp"] == 4000
    assert "dimming" not in params


def test_color_temperature_to_pilot_params_clamps_to_supported_range():
    assert _color_temperature_to_pilot_params(1000, brightness=None)["temp"] == 2200
    assert _color_temperature_to_pilot_params(50, brightness=None)["temp"] == 6500


def test_color_temperature_to_pilot_params_includes_dimming_when_given():
    params = _color_temperature_to_pilot_params(250, brightness=0.75)
    assert params["dimming"] == 75
