"""Tests for emulated_hue.controllers.color_utils."""
from emulated_hue.controllers.color_utils import (
    COLOR_PROFILES,
    gamut_for,
    hs_to_rgb,
    xy_brightness_to_rgb,
)


def test_xy_brightness_to_rgb_y_zero_returns_black():
    assert xy_brightness_to_rgb(0.3, 0.0, 1.0) == (0.0, 0.0, 0.0)


def test_xy_brightness_to_rgb_stays_in_range():
    for x, y in [(0.3, 0.3), (0.6, 0.35), (0.15, 0.06), (0.17, 0.7)]:
        r, g, b = xy_brightness_to_rgb(x, y, 1.0)
        assert 0.0 <= r <= 1.0
        assert 0.0 <= g <= 1.0
        assert 0.0 <= b <= 1.0


def test_xy_brightness_to_rgb_custom_gamut_differs_from_default():
    default = xy_brightness_to_rgb(0.4, 0.4, 1.0)
    identity_gamut = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    custom = xy_brightness_to_rgb(0.4, 0.4, 1.0, identity_gamut)
    assert default != custom


def test_hs_to_rgb_red():
    r, g, b = hs_to_rgb(0, 100)
    assert r == 1.0
    assert g == 0.0
    assert b == 0.0


def test_hs_to_rgb_zero_saturation_is_white():
    assert hs_to_rgb(123, 0) == (1.0, 1.0, 1.0)


def test_hs_to_rgb_wraps_hue_above_360():
    assert hs_to_rgb(360, 100) == hs_to_rgb(0, 100)


def test_hs_to_rgb_clamps_out_of_range_saturation():
    # Negative/>100 saturation shouldn't raise or go out of 0-1 range.
    r, g, b = hs_to_rgb(210, 150)
    assert 0.0 <= r <= 1.0
    assert 0.0 <= g <= 1.0
    assert 0.0 <= b <= 1.0


def test_srgb_profile_gives_orange_less_green_than_the_wide_default():
    wide = xy_brightness_to_rgb(0.54, 0.41, 0.8)
    srgb = xy_brightness_to_rgb(0.54, 0.41, 0.8, COLOR_PROFILES["srgb"])
    assert srgb[1] / srgb[0] < wide[1] / wide[0]


def test_gamut_for_defaults_to_none():
    assert gamut_for({}) is None


def test_gamut_for_resolves_a_named_profile():
    assert gamut_for({"color_profile": "srgb"}) == COLOR_PROFILES["srgb"]


def test_gamut_for_prefers_a_hand_set_matrix_over_the_profile():
    custom = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    assert gamut_for({"esphome_gamut": custom, "color_profile": "srgb"}) == custom


def test_color_balance_scales_channels_and_clamps():
    from emulated_hue.controllers.color_utils import apply_balance

    conf = {"color_balance": {"r": 100, "g": 50, "b": 100}}
    assert apply_balance((1.0, 0.8, 0.2), conf) == (1.0, 0.4, 0.2)


def test_color_balance_absent_leaves_rgb_untouched():
    from emulated_hue.controllers.color_utils import apply_balance

    assert apply_balance((0.3, 0.6, 0.9), {}) == (0.3, 0.6, 0.9)
    assert apply_balance(None, {"color_balance": {"g": 50}}) is None
