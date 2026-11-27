"""Tests for the Home Assistant compatibility fixes (kelvin, light ids, colour precision)."""

import logging
from types import SimpleNamespace

import pytest

from emulated_hue import apiv1, const
from emulated_hue.controllers.config import Config
from emulated_hue.controllers.devices import (
    BrightnessDevice,
    CTDevice,
    DeviceProperties,
    RGBDevice,
)
from emulated_hue.controllers.models import EntityState
from emulated_hue.utils import kelvin_to_mired

# what Home Assistant 2026 reports for a colour temperature light (WiZ bulb)
HASS_CT_LIGHT = {
    "state": "on",
    "attributes": {
        "min_color_temp_kelvin": 2202,
        "max_color_temp_kelvin": 6535,
        "supported_color_modes": ["color_temp", "hs", "xy"],
        "color_mode": "color_temp",
        "brightness": 120,
        "color_temp_kelvin": 3773,
        "friendly_name": "WiZ A.E27",
    },
}


def _ct_device(hass_state):
    device = object.__new__(CTDevice)
    device._hass_state_dict = hass_state
    return device


@pytest.mark.parametrize(
    ("kelvin", "mired"), [(6535, 153), (2202, 454), (2500, 400), (4000, 250), (3773, 265)]
)
def test_kelvin_and_mired_convert_into_each_other(kelvin, mired):
    assert kelvin_to_mired(kelvin) == mired
    assert abs(kelvin_to_mired(mired) - kelvin) < 25  # rounding, mireds are coarse


@pytest.mark.parametrize("value", [None, 0, -5])
def test_a_missing_or_invalid_value_has_no_mired_equivalent(value):
    assert kelvin_to_mired(value) is None


def test_colour_temperature_state_and_range_are_read_from_the_kelvin_attributes():
    device = _ct_device(HASS_CT_LIGHT)
    assert device.min_mireds == 153
    assert device.max_mireds == 454
    state = device._update_device_state()
    assert state.color_temp == 265
    assert state.color_mode == "color_temp"
    assert state.brightness == 120


def test_a_light_in_a_colour_mode_has_no_colour_temperature_yet():
    attributes = {**HASS_CT_LIGHT["attributes"], "color_temp_kelvin": None, "color_mode": "hs"}
    device = _ct_device({"state": "on", "attributes": attributes})
    assert device._update_device_state().color_temp is None


def test_home_assistant_versions_that_still_report_mireds_keep_working():
    legacy = {
        "state": "on",
        "attributes": {"color_temp": 300, "min_mireds": 153, "max_mireds": 500},
    }
    device = _ct_device(legacy)
    assert (device.min_mireds, device.max_mireds) == (153, 500)
    assert device._update_device_state().color_temp == 300


def test_kelvin_wins_over_legacy_attributes_and_missing_ones_give_none():
    attributes = {**HASS_CT_LIGHT["attributes"], "min_mireds": 1, "max_mireds": 2, "color_temp": 3}
    device = _ct_device({"state": "on", "attributes": attributes})
    assert (device.min_mireds, device.max_mireds) == (153, 454)
    assert _ct_device({"state": "on", "attributes": {}}).min_mireds is None


def test_a_colour_temperature_command_goes_to_home_assistant_in_kelvin():
    state = EntityState(color_mode=const.HASS_COLOR_MODE_COLOR_TEMP, color_temp=400, brightness=90)
    data = state.to_hass_data()
    assert data["color_temp_kelvin"] == 2500
    assert "color_temp" not in data
    assert data["brightness"] == 90


def test_colour_temperature_commands_compare_in_the_same_unit():
    one = EntityState(color_mode=const.HASS_COLOR_MODE_COLOR_TEMP, color_temp=400)
    same = EntityState(color_mode=const.HASS_COLOR_MODE_COLOR_TEMP, color_temp=400)
    other = EntityState(color_mode=const.HASS_COLOR_MODE_COLOR_TEMP, color_temp=300)
    assert one == same
    assert one != other


def test_hue_and_saturation_keep_their_fraction():
    control = object.__new__(RGBDevice.RGBControl)
    control._control_state = EntityState()
    control.set_hue_sat(26.94369, 39.14412)
    assert control._control_state.hue_saturation == (26.944, 39.144)


@pytest.fixture
def config(tmp_path):
    class FakeCtl:
        loop = None

    return Config(FakeCtl(), str(tmp_path), 80, 443, False)


async def _add_lights(config, ids):
    for light_id in ids:
        await config.async_set_storage_value(
            "lights", str(light_id), {"entity_id": f"light.{light_id}"}
        )


async def test_a_new_light_gets_the_lowest_free_id(config):
    await _add_lights(config, [1, 2, 5])
    assert await config.async_entity_id_to_light_id("light.new") == "3"
    assert await config.async_entity_id_to_light_id("light.new2") == "4"
    assert await config.async_entity_id_to_light_id("light.new3") == "6"


async def test_the_first_light_gets_id_1_and_known_lights_keep_theirs(config):
    assert await config.async_entity_id_to_light_id("light.first") == "1"
    assert await config.async_entity_id_to_light_id("light.first") == "1"
    assert await config.async_entity_id_to_light_id("light.second") == "2"


async def test_ids_above_the_client_limit_are_warned_about(config, caplog):
    await _add_lights(config, range(1, const.HUE_CLIENT_MAX_LIGHT_ID + 1))
    with caplog.at_level(logging.WARNING):
        assert await config.async_entity_id_to_light_id("light.over") == str(
            const.HUE_CLIENT_MAX_LIGHT_ID + 1
        )
    assert "only list lights up to id 63" in caplog.text


async def test_ids_within_the_limit_do_not_warn(config, caplog):
    await _add_lights(config, [1, 2])
    with caplog.at_level(logging.WARNING):
        await config.async_entity_id_to_light_id("light.fine")
    assert "only list lights" not in caplog.text


@pytest.mark.parametrize(
    ("hass_brightness", "hue_bri"), [(255, 254), (254, 254), (100, 100), (1, 1)]
)
async def test_brightness_reported_to_hue_clients_stops_at_254(
    monkeypatch, hass_brightness, hue_bri
):
    device = object.__new__(BrightnessDevice)
    device._config_state = EntityState(brightness=hass_brightness)
    device._name = "Lamp"
    device._unique_id = "00:11"
    device._device = DeviceProperties(None, None, None, None, None)
    device._hass_state_dict = {"attributes": {}}

    async def fake_get_device(ctl, entity_id):
        return device

    monkeypatch.setattr(apiv1, "async_get_device", fake_get_device)
    ctl = SimpleNamespace(
        config_instance=SimpleNamespace(
            definitions={
                "lights": {
                    "Dimmable light": {
                        "manufacturername": "Signify",
                        "modelid": "LWB010",
                        "productname": "Hue white lamp",
                        "swversion": "1.0",
                    }
                }
            }
        )
    )
    api = apiv1.HueApiV1Endpoints(ctl)
    light = await api._HueApiV1Endpoints__async_entity_to_hue("light.lamp")
    assert light["state"]["bri"] == hue_bri
