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


async def test_color_profile_is_saved_and_returned(client):
    test_client, _ = client
    lights = await (await test_client.get("/api/lights")).json()
    light_id = lights[0]["light_id"]

    response = await test_client.post(
        f"/api/lights/{light_id}", json={"type": "", "color_profile": "srgb"}
    )
    assert (await response.json())["success"]

    lights = await (await test_client.get("/api/lights")).json()
    assert lights[0]["color_profile"] == "srgb"


async def test_unknown_color_profile_is_rejected(client):
    test_client, _ = client
    lights = await (await test_client.get("/api/lights")).json()
    light_id = lights[0]["light_id"]

    response = await test_client.post(
        f"/api/lights/{light_id}", json={"type": "", "color_profile": "neon"}
    )
    assert "error" in (await response.text())


async def test_color_balance_is_saved_and_defaults_are_dropped(client):
    test_client, _ = client
    lights = await (await test_client.get("/api/lights")).json()
    light_id = lights[0]["light_id"]
    assert lights[0]["color_balance"] == {"r": 100, "g": 100, "b": 100}

    await test_client.post(
        f"/api/lights/{light_id}",
        json={"type": "", "color_balance": {"r": "100", "g": "80", "b": ""}},
    )
    lights = await (await test_client.get("/api/lights")).json()
    assert lights[0]["color_balance"] == {"r": 100, "g": 80, "b": 100}

    await test_client.post(
        f"/api/lights/{light_id}",
        json={"type": "", "color_balance": {"r": "100", "g": "100", "b": "100"}},
    )
    lights = await (await test_client.get("/api/lights")).json()
    assert lights[0]["color_balance"] == {"r": 100, "g": 100, "b": 100}
