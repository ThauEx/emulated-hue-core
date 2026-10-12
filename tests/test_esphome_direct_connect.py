"""Tests for esphome_direct's connection handling (serialization + backoff)."""
import asyncio

import pytest
from aioesphomeapi import APIConnectionError, ColorMode, LightInfo

from emulated_hue.controllers import esphome_direct


class FakeClient:
    """Stand-in for aioesphomeapi.APIClient that counts connect attempts."""

    instances: list["FakeClient"] = []
    fail = False

    def __init__(self, host, port, password=None, noise_psk=None):
        self.api_version = None
        self.connects = 0
        FakeClient.instances.append(self)

    async def connect(self, login=False):
        self.connects += 1
        await asyncio.sleep(0.05)  # long enough for concurrent callers to pile up
        if FakeClient.fail:
            raise APIConnectionError("down")
        self.api_version = (1, 10)

    async def list_entities_services(self):
        light = LightInfo(
            object_id="lamp",
            key=1,
            name="lamp",
            supported_color_modes=[ColorMode.RGB],
            min_mireds=153.0,
            max_mireds=500.0,
        )
        return [light], []


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch):
    FakeClient.instances = []
    FakeClient.fail = False
    monkeypatch.setattr(esphome_direct, "APIClient", FakeClient)
    yield
    esphome_direct._clients.clear()
    esphome_direct._light_keys.clear()
    esphome_direct._light_caps.clear()
    esphome_direct._logged_inactive.clear()
    esphome_direct._connect_locks.clear()
    esphome_direct._retry_after.clear()


async def test_concurrent_frames_trigger_a_single_connect():
    results = await asyncio.gather(
        *(
            esphome_direct._async_get_ready_client("lamp.local", 6053, "", None)
            for _ in range(10)
        )
    )
    assert all(result is not None for result in results)
    assert sum(client.connects for client in FakeClient.instances) == 1


async def test_failed_connect_backs_off_instead_of_retrying_every_frame():
    FakeClient.fail = True
    for _ in range(5):
        assert (
            await esphome_direct._async_get_ready_client("lamp.local", 6053, "", None)
            is None
        )
    assert sum(client.connects for client in FakeClient.instances) == 1


async def test_retries_again_once_backoff_has_elapsed():
    FakeClient.fail = True
    assert (
        await esphome_direct._async_get_ready_client("lamp.local", 6053, "", None)
        is None
    )
    FakeClient.fail = False
    esphome_direct._retry_after["lamp.local"] = 0.0
    assert (
        await esphome_direct._async_get_ready_client("lamp.local", 6053, "", None)
        is not None
    )
