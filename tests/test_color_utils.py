"""Tests for emulated_hue.controllers.color_utils."""
from emulated_hue.controllers.color_utils import hs_to_rgb, xy_brightness_to_rgb


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
