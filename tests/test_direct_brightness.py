"""
Regression tests for how brightness is passed to the direct ESPHome path.

xy_brightness_to_rgb() already bakes the brightness into rgb, and ESPHome
multiplies its own brightness on top (before gamma). Sending both made a
Hue bri of 96 land at roughly 2% light instead of ~30%.
"""
from types import SimpleNamespace

from emulated_hue import const
from emulated_hue.controllers import esphome_direct
from emulated_hue.controllers.devices import RGBDevice
from emulated_hue.controllers.models import EntityState


class _FakeConfig:
    def get_storage_value(self, key, subkey=None, default=None):
        return {"esphome_host": "lamp.local"}


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


async def test_xy_frame_sends_esphome_brightness_one(monkeypatch):
    device, sent = _device(monkeypatch)
    assert await device._async_maybe_send_direct(_xy_state(96))
    assert sent["brightness"] == 1.0


async def test_dimmer_xy_frame_yields_dimmer_rgb(monkeypatch):
    device, sent = _device(monkeypatch)
    await device._async_maybe_send_direct(_xy_state(254))
    bright = max(sent["rgb"])
    await device._async_maybe_send_direct(_xy_state(60))
    assert max(sent["rgb"]) < bright


async def test_xy_frame_without_bri_uses_the_lights_known_brightness(monkeypatch):
    device, sent = _device(monkeypatch, known_brightness=60)
    await device._async_maybe_send_direct(_xy_state(None))
    dim = max(sent["rgb"])
    device._config_state = EntityState(brightness=254)
    await device._async_maybe_send_direct(_xy_state(None))
    assert dim < max(sent["rgb"])
