"""
Regression tests for Config's storage get/set semantics.

get_storage_value() must hand back a deep copy, not a live reference into
the storage tree - otherwise a caller that fetches a dict, mutates it in
place, then passes that same object to async_set_storage_value() makes the
change-detection compare the object to itself (always equal), silently
skipping the save. Real callers doing exactly this: ingress.py's per-light
save, and devices.py's own state persistence.
"""
import pytest

from emulated_hue.controllers.config import Config


class FakeCtl:
    """Minimal stand-in - Config only needs a truthy object here."""

    loop = None


@pytest.fixture
def config(tmp_path):
    return Config(FakeCtl(), str(tmp_path), 80, 443, False)


async def test_get_storage_value_returns_a_copy_not_a_live_reference(config):
    await config.async_set_storage_value("lights", "1", {"entity_id": "light.x"})
    await config._saver_task  # drain the save task the line above scheduled

    light_conf = await config.async_get_light_config("1")
    light_conf["esphome_host"] = "192.168.1.50"  # mutate the returned dict only

    fresh = await config.async_get_light_config("1")
    assert "esphome_host" not in fresh


async def test_set_storage_value_schedules_a_save_for_a_mutated_fetched_copy(config):
    await config.async_set_storage_value("lights", "1", {"entity_id": "light.x"})
    await config._saver_task
    config._saver_task = None

    light_conf = await config.async_get_light_config("1")
    light_conf["esphome_host"] = "192.168.1.50"
    await config.async_set_storage_value("lights", "1", light_conf)

    assert config._saver_task is not None
    config._saver_task.cancel()
