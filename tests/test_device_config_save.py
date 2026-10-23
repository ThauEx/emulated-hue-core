"""
A device object only holds a snapshot of its light's config from when it was
created. Settings changed afterwards (e.g. in the ingress panel) must neither
be wiped when the device saves its state, nor be ignored when it reads them.
"""
import copy
from types import SimpleNamespace

from emulated_hue.controllers.devices import OnOffDevice


class FakeConfig:
    def __init__(self, lights):
        self.lights = lights

    def get_storage_value(self, key, subkey=None, default=None):
        return copy.deepcopy(self.lights.get(subkey, default))

    async def async_set_storage_value(self, key, subkey, value):
        self.lights[subkey] = copy.deepcopy(value)


def _device(stored):
    config = FakeConfig({"5": stored})
    device = object.__new__(OnOffDevice)
    device.ctl = SimpleNamespace(config_instance=config)
    device._light_id = "5"
    device._throttle_ms = 150
    # The snapshot taken at creation time - before the panel set anything.
    device._config = {"entity_id": "light.x", "state": {"brightness": 10}}
    return device, config


async def test_saving_state_keeps_settings_changed_since_the_snapshot():
    device, config = _device(
        {
            "entity_id": "light.x",
            "esphome_host": "192.168.1.50",
            "max_transition_ms": 0,
            "state": {"brightness": 10},
        }
    )
    device._config["state"] = {"brightness": 99}

    await device._async_save_config()

    stored = config.lights["5"]
    assert stored["state"] == {"brightness": 99}
    assert stored["esphome_host"] == "192.168.1.50"
    assert stored["max_transition_ms"] == 0


async def test_settings_changed_since_the_snapshot_are_read_live():
    device, config = _device({"entity_id": "light.x", "throttle": 150})
    assert device.throttle_ms == 150
    assert device.max_transition_ms is None

    config.lights["5"]["throttle"] = 40
    config.lights["5"]["max_transition_ms"] = 100

    assert device.throttle_ms == 40
    assert device.max_transition_ms == 100
