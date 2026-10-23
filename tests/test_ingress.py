"""Tests for the ingress panel's light list."""
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from emulated_hue import ingress as ingress_module
from emulated_hue.controllers.config import Config
from emulated_hue.ingress import HueIngressEndpoints


class FakeHass:
    def __init__(self, entities):
        self.entities = entities

    def get_entities(self):
        return list(self.entities)

    def get_entity_state(self, entity_id):
        return {"attributes": {"friendly_name": self.entities[entity_id]}}


class FakeCtl:
    loop = None


@pytest.fixture(autouse=True)
def _reset_route_table():
    ingress_module.routes._items.clear()
    yield


@pytest.fixture
async def client(tmp_path):
    ctl = FakeCtl()
    ctl.config_instance = Config(ctl, str(tmp_path), 80, 443, False)
    ctl.controller_hass = FakeHass({"light.old": "Old Lamp"})
    app = web.Application()
    app.add_routes(HueIngressEndpoints(ctl).route)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client, ctl


async def test_lights_added_to_home_assistant_show_up_without_a_hue_client(client):
    test_client, ctl = client
    ctl.controller_hass.entities["light.new"] = "New Lamp"

    response = await test_client.get("/api/lights")
    lights = await response.json()

    names = {light["entity_id"]: light["name"] for light in lights}
    assert names == {"light.old": "Old Lamp", "light.new": "New Lamp"}


async def test_listing_twice_does_not_duplicate_lights(client):
    test_client, _ = client
    await test_client.get("/api/lights")
    response = await test_client.get("/api/lights")
    assert len(await response.json()) == 1
