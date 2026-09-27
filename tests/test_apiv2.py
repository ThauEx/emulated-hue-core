"""End-to-end tests for the CLIP v2 API (emulated_hue.apiv2), against a real
aiohttp app with fake storage/entertainment - no real Home Assistant or
ESPHome device needed.
"""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import emulated_hue.apiv2 as apiv2_module
from emulated_hue.apiv2 import HueApiV2Endpoints, _v2_id

TOKEN = "tok123"


@pytest.fixture(autouse=True)
def _reset_route_table():
    """The route table is a module-level singleton (mirrors v1's real
    production design, where there's only ever one instance per process) -
    without clearing it, a second test's fresh HueApiV2Endpoints instance
    would silently reuse the first test's already-registered handlers.
    """
    apiv2_module.routes._items.clear()
    yield


class FakeConfig:
    """Minimal stand-in for controllers.config.Config."""

    bridge_id = "001788FFFE123456"
    bridge_name = "Test Bridge"
    definitions = {"bridge": {"swversion": "1948086000"}}

    def __init__(self):
        self._lights = {
            "1": {
                "name": "bkl1253",
                "config": {"archetype": "sultanbulb"},
                "state": {"power_state": True},
            }
        }
        self._groups = {}
        self._users = {TOKEN: {"username": TOKEN, "clientkey": "AABBCC"}}
        self.entertainment_active = False
        self.started = []
        self.stopped = 0

    async def async_get_user(self, token):
        return self._users.get(token)

    async def async_get_storage_value(self, key, subkey=None, default=None):
        store = {"lights": self._lights, "groups": self._groups}.get(key, {})
        if subkey:
            return store.get(subkey, default)
        return store or default

    async def async_set_storage_value(self, key, subkey, value):
        store = {"lights": self._lights, "groups": self._groups}[key]
        store[subkey] = value

    async def async_delete_storage_value(self, key, subkey=None):
        store = {"lights": self._lights, "groups": self._groups}[key]
        store.pop(subkey, None)

    def start_entertainment(self, group_conf, user_data):
        self.started.append((group_conf, user_data))
        return True

    def stop_entertainment(self):
        self.stopped += 1


class FakeCtl:
    def __init__(self):
        self.config_instance = FakeConfig()


@pytest.fixture
async def client():
    ctl = FakeCtl()
    v2 = HueApiV2Endpoints(ctl)
    app = web.Application()
    app.add_routes(v2.route)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client, ctl


HEADERS = {"hue-application-key": TOKEN}


async def test_missing_auth_header_is_rejected(client):
    test_client, _ = client
    resp = await test_client.get("/clip/v2/resource/light", headers={})
    body = await resp.json()
    assert body["errors"]
    assert body["data"] == []


async def test_list_lights_reflects_v1_storage(client):
    test_client, _ = client
    resp = await test_client.get("/clip/v2/resource/light", headers=HEADERS)
    body = await resp.json()
    assert not body["errors"]
    assert len(body["data"]) == 1
    assert body["data"][0]["id_v1"] == "/lights/1"
    assert body["data"][0]["metadata"]["name"] == "bkl1253"


async def test_entertainment_configuration_full_lifecycle(client):
    test_client, ctl = client
    light_ent_rid = _v2_id("entertainment-light", "1")

    # starts out empty
    resp = await test_client.get(
        "/clip/v2/resource/entertainment_configuration", headers=HEADERS
    )
    assert (await resp.json())["data"] == []

    # create, referencing the light's entertainment resource
    create_body = {
        "metadata": {"name": "Living room"},
        "configuration_type": "screen",
        "channels": [
            {
                "channel_id": 0,
                "position": {"x": 0, "y": 0, "z": 0},
                "members": [
                    {
                        "service": {"rid": light_ent_rid, "rtype": "entertainment"},
                        "index": 0,
                    }
                ],
            }
        ],
    }
    resp = await test_client.post(
        "/clip/v2/resource/entertainment_configuration",
        json=create_body,
        headers=HEADERS,
    )
    body = await resp.json()
    assert not body["errors"]
    rid = body["data"][0]["rid"]
    assert ctl.config_instance._groups, "creating should persist a v1 group"
    # real Hue bridges reserve 200+ for entertainment groups
    assert "200" in ctl.config_instance._groups

    # fetch it back
    resp = await test_client.get(
        f"/clip/v2/resource/entertainment_configuration/{rid}", headers=HEADERS
    )
    body = await resp.json()
    resource = body["data"][0]
    assert resource["id_v1"] == "/groups/200"
    assert resource["status"] == "inactive"
    assert resource["light_services"][0]["rid"] == _v2_id("light", "1")

    # start -> must reuse the existing v1 entertainment engine
    resp = await test_client.put(
        f"/clip/v2/resource/entertainment_configuration/{rid}",
        json={"action": "start"},
        headers=HEADERS,
    )
    assert not (await resp.json())["errors"]
    assert ctl.config_instance.started, "start_entertainment should be called"

    resp = await test_client.get(
        f"/clip/v2/resource/entertainment_configuration/{rid}", headers=HEADERS
    )
    resource = (await resp.json())["data"][0]
    assert resource["status"] == "active"
    assert resource["active_streamer"]["rid"] == TOKEN

    # stop
    resp = await test_client.put(
        f"/clip/v2/resource/entertainment_configuration/{rid}",
        json={"action": "stop"},
        headers=HEADERS,
    )
    assert not (await resp.json())["errors"]
    assert ctl.config_instance.stopped == 1

    # delete
    resp = await test_client.delete(
        f"/clip/v2/resource/entertainment_configuration/{rid}", headers=HEADERS
    )
    assert not (await resp.json())["errors"]
    assert not ctl.config_instance._groups


async def test_get_unknown_entertainment_configuration_is_not_found(client):
    test_client, _ = client
    resp = await test_client.get(
        "/clip/v2/resource/entertainment_configuration/does-not-exist",
        headers=HEADERS,
    )
    body = await resp.json()
    assert body["errors"]
    assert body["data"] == []


async def test_resource_list_aggregates_everything(client):
    test_client, _ = client
    resp = await test_client.get("/clip/v2/resource", headers=HEADERS)
    body = await resp.json()
    # bridge, bridge device, entertainment-bridge, light, light-device,
    # entertainment-light = 6 with one light and no entertainment configs yet
    assert len(body["data"]) == 6
