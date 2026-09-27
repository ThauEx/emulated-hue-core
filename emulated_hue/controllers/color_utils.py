"""
Colour conversion helpers for the entertainment streaming path.

Home Assistant would otherwise convert the CIE xy values a Hue Entertainment
stream sends into RGB itself, using a generic sRGB gamut assumption that
doesn't match any particular LED strip's real output. Doing the conversion
here instead lets the direct ESPHome path (see esphome_direct.py) use a
gamut tuned to the actual light - the same thing a real Hue light does
internally with its own factory-calibrated gamut.
"""

# Default XYZ->RGB matrix, the one most open source Hue client
# implementations use for a typical wide-gamut RGB strip. Override per light
# via the esphome_gamut light-config field once a strip has been
# colour-calibrated for better accuracy.
_DEFAULT_XYZ_TO_RGB = (
    (1.656492, -0.354851, -0.255038),
    (-0.707196, 1.655397, 0.036152),
    (0.051713, -0.121364, 1.011530),
)


def _gamma_correct(component: float) -> float:
    """Apply sRGB gamma companding to a linear colour component."""
    if component <= 0.0031308:
        return 12.92 * component
    return 1.055 * (component ** (1.0 / 2.4)) - 0.055


def xy_brightness_to_rgb(
    x: float, y: float, brightness: float, gamut: tuple | None = None
) -> tuple[float, float, float]:
    """
    Convert CIE xy + brightness (0-1) to 0-1 RGB.

    Mirrors the conversion a real Hue light does internally. `gamut`, if
    given, is a 3x3 XYZ->RGB matrix (same shape as the default) tuned to a
    specific light; otherwise a generic wide-gamut default is used.
    """
    if y <= 0:
        return (0.0, 0.0, 0.0)
    matrix = gamut or _DEFAULT_XYZ_TO_RGB
    capital_y = brightness
    capital_x = (capital_y / y) * x
    capital_z = (capital_y / y) * (1.0 - x - y)

    rgb = [
        row[0] * capital_x + row[1] * capital_y + row[2] * capital_z for row in matrix
    ]
    rgb = [_gamma_correct(c) for c in rgb]

    highest = max([*rgb, 1.0])
    if highest > 1.0:
        rgb = [c / highest for c in rgb]

    return tuple(max(0.0, c) for c in rgb)
