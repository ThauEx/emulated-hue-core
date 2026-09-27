"""Tests for emulated_hue.controllers.esphome_direct's pure helper functions."""
from emulated_hue.controllers.esphome_direct import (
    ColorMode,
    _cold_warm_fractions,
    _pick_color_mode_and_extra,
    _split_credential,
)


def test_split_credential_recognizes_noise_psk():
    # A real 32-byte base64-encoded Noise PSK.
    key = "I286nN1GmSpBsmSjtDaarvm242DOVyKI+uskwtpF6sE="
    assert _split_credential(key) == (None, key)


def test_split_credential_recognizes_legacy_password():
    assert _split_credential("220190") == ("220190", None)


def test_split_credential_empty():
    assert _split_credential("") == (None, None)
    assert _split_credential(None) == (None, None)


def test_split_credential_rejects_malformed_base64_as_password():
    # Not valid base64 at all -> treated as a plaintext password, not crash.
    assert _split_credential("not-base64!!") == ("not-base64!!", None)


def test_cold_warm_fractions_at_endpoints():
    assert _cold_warm_fractions(150, 150, 370) == (1.0, 0.0)
    assert _cold_warm_fractions(370, 150, 370) == (0.0, 1.0)


def test_cold_warm_fractions_midpoint():
    cold, warm = _cold_warm_fractions(260, 150, 370)
    assert abs(cold - 0.5) < 0.01
    assert abs(warm - 0.5) < 0.01


def test_cold_warm_fractions_clamps_out_of_range():
    assert _cold_warm_fractions(50, 150, 370) == (1.0, 0.0)
    assert _cold_warm_fractions(1000, 150, 370) == (0.0, 1.0)


def test_pick_color_mode_plain_rgb_device():
    supported = frozenset({ColorMode.RGB, ColorMode.COLOR_TEMPERATURE})
    mode, extra = _pick_color_mode_and_extra(supported, (1.0, 0.0, 0.0), None)
    assert mode == ColorMode.RGB
    assert extra == {}


def test_pick_color_mode_combined_rgbww_device_zeroes_whites():
    """Regression test: rgb on a combined-mode light must not leak white."""
    supported = frozenset({ColorMode.RGB_COLD_WARM_WHITE})
    mode, extra = _pick_color_mode_and_extra(supported, (1.0, 0.0, 0.0), None)
    assert mode == ColorMode.RGB_COLD_WARM_WHITE
    assert extra == {"cold_white": 0.0, "warm_white": 0.0}


def test_pick_color_mode_color_temp_on_combined_device_computes_blend():
    supported = frozenset({ColorMode.RGB_COLD_WARM_WHITE})
    mode, extra = _pick_color_mode_and_extra(
        supported, None, 260, min_mireds=150, max_mireds=370
    )
    assert mode == ColorMode.RGB_COLD_WARM_WHITE
    assert extra["rgb"] == (0.0, 0.0, 0.0)
    assert extra["color_temperature"] is None
    assert abs(extra["cold_white"] - 0.5) < 0.01
    assert abs(extra["warm_white"] - 0.5) < 0.01


def test_pick_color_mode_no_color_change_returns_none():
    supported = frozenset({ColorMode.RGB_COLD_WARM_WHITE})
    mode, extra = _pick_color_mode_and_extra(supported, None, None)
    assert mode is None
    assert extra == {}
