"""Tests for streaming Entertainment frames to lights with a direct target.

A light with an ESPHome or WiZ target is only ever streamed to through that
path: a frame that can't be delivered is skipped, it does not go through Home
Assistant (which would hit an already struggling lamp from a second side).
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from emulated_hue.controllers import entertainment, esphome_direct, wiz_direct
from emulated_hue.controllers.entertainment import COLOR_TYPE_XY_BR, EntertainmentAPI

FRAME = bytes([0, 0, 1, 0x4C, 0xCD, 0x54, 0x7B, 0x80, 0x00])  # light 1, xy (0.3, 0.33), bri 0.5


class _Config:
    def __init__(self, light_conf):
        self.light_conf = light_conf

    async def async_get_light_config(self, light_id):
        return self.light_conf


class _Sender:
    """Stands in for a direct send function, delivering or not as told."""

    def __init__(self):
        self.delivers = True
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.delivers


@pytest.fixture
def stream(monkeypatch):
    sender = _Sender()
    device = MagicMock()
    device.new_control_state.return_value.async_execute = AsyncMock()
    get_device = AsyncMock(return_value=device)
    monkeypatch.setattr(esphome_direct, "async_send_light_state", sender)
    monkeypatch.setattr(wiz_direct, "async_send_light_state", sender)
    monkeypatch.setattr(entertainment, "async_get_device", get_device)

    def make(light_conf):
        api = object.__new__(EntertainmentAPI)
        api.ctl = SimpleNamespace(config_instance=_Config(light_conf))
        api._timestamps = {}
        api._paused = {}
        api._last_sent = {}
        return api

    return make, sender, get_device


async def _frame(api):
    await api._EntertainmentAPI__async_process_light_packet(FRAME, COLOR_TYPE_XY_BR)


async def test_a_delivered_frame_goes_directly_and_never_waits_for_a_reconnect(stream):
    make, sender, get_device = stream
    api = make({"esphome_host": "lamp.local", "entity_id": "light.lamp"})
    await _frame(api)
    assert len(sender.calls) == 1
    assert sender.calls[0]["wait_for_connection"] is False
    get_device.assert_not_called()


async def test_an_undelivered_frame_is_skipped_instead_of_using_home_assistant(stream):
    make, sender, get_device = stream
    sender.delivers = False
    api = make({"esphome_host": "lamp.local", "entity_id": "light.lamp"})
    for _ in range(3):
        await _frame(api)
    assert len(sender.calls) == 3
    get_device.assert_not_called()
    assert api._paused == {"1": 3}


async def test_wiz_lights_are_not_sent_through_home_assistant_either(stream):
    make, sender, get_device = stream
    sender.delivers = False
    api = make({"wiz_host": "bulb.local", "entity_id": "light.bulb"})
    await _frame(api)
    get_device.assert_not_called()
    assert api._paused == {"1": 1}


async def test_the_outage_is_logged_once_and_the_recovery_with_the_skipped_frames(stream, caplog):
    make, sender, _ = stream
    api = make({"esphome_host": "lamp.local", "entity_id": "light.lamp"})
    sender.delivers = False
    with caplog.at_level(logging.INFO):
        for _ in range(4):
            await _frame(api)
        sender.delivers = True
        await _frame(api)
        await _frame(api)
    down = [r for r in caplog.records if "is down" in r.message]
    back = [r for r in caplog.records if "is back after" in r.message]
    assert len(down) == 1
    assert len(back) == 1
    assert "after 4 skipped frames" in back[0].message
    assert api._paused == {}


async def test_a_light_without_a_direct_target_still_uses_home_assistant(stream):
    make, sender, get_device = stream
    api = make({"entity_id": "light.plain"})
    await _frame(api)
    assert sender.calls == []
    get_device.assert_awaited_once()
