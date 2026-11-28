"""Tests for Entertainment frames that do not change anything.

A still picture (or a black screen) makes the TV stream the same colour 25-50
times per second. Sending every one of them keeps a small lamp busy with identical
commands, so a frame that turns into the command the lamp already got is skipped
(and only repeated every couple of seconds).
"""

import logging
from types import SimpleNamespace

import pytest

from emulated_hue.controllers import entertainment, esphome_direct, wiz_direct
from emulated_hue.controllers.entertainment import (
    COLOR_TYPE_XY_BR,
    REPEAT_REFRESH_SECONDS,
    EntertainmentAPI,
)

BLUE = (0.15, 0.06)
RED = (0.68, 0.31)


class _Sender:
    def __init__(self):
        self.delivers = True
        self.calls = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.delivers


def _stream(monkeypatch, light_conf):
    sender = _Sender()
    monkeypatch.setattr(esphome_direct, "async_send_light_state", sender)
    monkeypatch.setattr(wiz_direct, "async_send_light_state", sender)

    async def get_light_config(light_id):
        return light_conf

    api = object.__new__(EntertainmentAPI)
    api.ctl = SimpleNamespace(
        config_instance=SimpleNamespace(async_get_light_config=get_light_config)
    )
    api._timestamps = {}
    api._paused = {}
    api._last_sent = {}
    return api, sender


@pytest.fixture
def esphome(monkeypatch):
    return _stream(monkeypatch, {"esphome_host": "lamp.local", "entity_id": "light.lamp"})


@pytest.fixture
def wiz(monkeypatch):
    return _stream(monkeypatch, {"wiz_host": "bulb.local", "entity_id": "light.bulb"})


def _frame(xy, brightness):
    def two_bytes(value):
        return divmod(round(value * 65535), 256)

    return bytes([0, 0, 1, *two_bytes(xy[0]), *two_bytes(xy[1]), *two_bytes(brightness)])


async def _send(api, xy, brightness):
    await api._EntertainmentAPI__async_process_light_packet(
        _frame(xy, brightness), COLOR_TYPE_XY_BR
    )


async def test_a_still_picture_is_sent_once(esphome):
    api, sender = esphome
    for _ in range(50):
        await _send(api, BLUE, 0.6)
    assert len(sender.calls) == 1


async def test_a_black_screen_is_sent_once(esphome):
    api, sender = esphome
    for _ in range(50):
        await _send(api, BLUE, 0.0)
    assert len(sender.calls) == 1


async def test_a_change_is_sent_at_once(esphome):
    api, sender = esphome
    await _send(api, BLUE, 0.6)
    await _send(api, BLUE, 0.5)
    await _send(api, RED, 0.5)
    await _send(api, RED, 0.5)
    assert len(sender.calls) == 3


async def test_going_back_to_an_earlier_colour_is_a_change_too(esphome):
    api, sender = esphome
    await _send(api, BLUE, 0.6)
    await _send(api, RED, 0.6)
    await _send(api, BLUE, 0.6)
    assert len(sender.calls) == 3


async def test_the_same_frame_is_repeated_after_the_refresh_interval(esphome):
    api, sender = esphome
    await _send(api, BLUE, 0.6)
    frame_key, sent_at = api._last_sent["1"]
    api._last_sent["1"] = (frame_key, sent_at - REPEAT_REFRESH_SECONDS - 1)
    await _send(api, BLUE, 0.6)
    assert len(sender.calls) == 2


async def test_a_frame_the_lamp_did_not_get_is_not_remembered(esphome):
    api, sender = esphome
    await _send(api, BLUE, 0.6)  # delivered
    sender.delivers = False
    await _send(api, RED, 0.6)  # lost, so we no longer know what the lamp shows
    sender.delivers = True
    await _send(api, BLUE, 0.6)  # same as the first one, but it has to be sent again
    assert len(sender.calls) == 3


async def test_failed_deliveries_keep_being_retried(esphome):
    api, sender = esphome
    sender.delivers = False
    for _ in range(4):
        await _send(api, BLUE, 0.6)
    assert len(sender.calls) == 4


async def test_wiz_lights_get_no_identical_repeats_either(wiz):
    api, sender = wiz
    for _ in range(20):
        await _send(api, BLUE, 0.6)
    assert len(sender.calls) == 1
    await _send(api, RED, 0.6)
    assert len(sender.calls) == 2


async def test_frames_are_traced_in_the_debug_log_a_few_times_per_second(esphome, caplog):
    api, _ = esphome
    with caplog.at_level(logging.DEBUG, logger=entertainment.__name__):
        for _ in range(5):
            await _send(api, BLUE, 2 / 255)
    traces = [r.getMessage() for r in caplog.records if "Frame light 1" in r.getMessage()]
    assert len(traces) == 1  # the rest came within the same quarter second
    assert "xy=(0.150, 0.060) bri=0.0078" in traces[0]
    assert "level=" in traces[0]


async def test_no_trace_without_debug_logging(esphome, caplog):
    api, _ = esphome
    with caplog.at_level(logging.INFO, logger=entertainment.__name__):
        await _send(api, BLUE, 0.6)
    assert "Frame light" not in caplog.text
