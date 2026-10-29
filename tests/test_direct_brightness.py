"""
Tests for how brightness is passed to the direct ESPHome path.

ESPHome keeps only the ratio between the rgb channels and discards their
overall level, so the Ambilight/Hue brightness has to travel in the separate
brightness field - otherwise every colour would show at full brightness.
"""
from types import SimpleNamespace

from emulated_hue import const
from emulated_hue.controllers import esphome_direct
from emulated_hue.controllers.devices import RGBDevice
from emulated_hue.controllers.models import EntityState


class _FakeConfig:
    light_conf = {"esphome_host": "lamp.local"}

    def get_storage_value(self, key, subkey=None, default=None):
        return self.light_conf


def _device(monkeypatch, known_brightness=100):
    sent = {}

    async def fake_send(**kwargs):
        sent.update(kwargs)
        return True

    monkeypatch.setattr(esphome_direct, "async_send_light_state", fake_send)
    device = object.__new__(RGBDevice)
    device._light_id = "1"
    device.ctl = SimpleNamespace(config_instance=_FakeConfig())
    device._config_state = EntityState(brightness=known_brightness)
    return device, sent


def _xy_state(brightness):
    return EntityState(
        power_state=True,
        color_mode=const.HASS_COLOR_MODE_XY,
        xy_color=(0.3127, 0.329),
        brightness=brightness,
    )


async def test_xy_frame_level_goes_into_the_brightness_field(monkeypatch):
    device, sent = _device(monkeypatch)
    assert await device._async_maybe_send_direct(_xy_state(254))
    bright = sent["brightness"]
    assert await device._async_maybe_send_direct(_xy_state(60))
    assert sent["brightness"] < bright
    assert sent["brightness"] == max(sent["rgb"])


async def test_balance_does_not_change_the_brightness(monkeypatch):
    device, sent = _device(monkeypatch)
    await device._async_maybe_send_direct(_xy_state(150))
    plain = sent["brightness"]
    device.ctl.config_instance.light_conf = {
        "esphome_host": "lamp.local",
        "color_balance": {"r": 100, "g": 50, "b": 50},
    }
    await device._async_maybe_send_direct(_xy_state(150))
    assert sent["brightness"] == plain


async def test_black_frame_keeps_a_brightness_floor(monkeypatch):
    device, sent = _device(monkeypatch)
    await device._async_maybe_send_direct(_xy_state(1))
    assert sent["brightness"] >= 0.02


async def test_xy_frame_without_bri_uses_the_lights_known_brightness(monkeypatch):
    device, sent = _device(monkeypatch, known_brightness=60)
    await device._async_maybe_send_direct(_xy_state(None))
    dim = max(sent["rgb"])
    device._config_state = EntityState(brightness=254)
    await device._async_maybe_send_direct(_xy_state(None))
    assert dim < max(sent["rgb"])
