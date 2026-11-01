"""Tests for the Home Assistant controller against a fake Home Assistant."""

import asyncio

import pytest
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from hass_client.exceptions import AuthenticationFailed, CannotConnect

from emulated_hue.controllers import homeassistant
from emulated_hue.controllers.homeassistant import HomeAssistantController

LIGHT = {"entity_id": "light.lamp", "state": "on", "attributes": {"friendly_name": "Lamp"}}
SWITCH = {"entity_id": "switch.plug", "state": "off", "attributes": {}}


class FakeHomeAssistant:
    """Speaks just enough of the websocket and REST API for the controller."""

    def __init__(self):
        self.states = [LIGHT, SWITCH]
        self.areas = [
            {"area_id": "kitchen", "name": "Kitchen"},
            {"area_id": "attic", "name": "Attic"},
        ]
        self.devices = [
            {"id": "dev1", "area_id": "kitchen", "identifiers": [["esphome", "lamp"]]},
        ]
        self.entities = [
            {"entity_id": "light.lamp", "device_id": "dev1", "area_id": None, "disabled_by": None},
            {
                "entity_id": "light.off",
                "device_id": None,
                "area_id": "attic",
                "disabled_by": "user",
            },
            {
                "entity_id": "light.own",
                "device_id": "dev1",
                "area_id": "attic",
                "disabled_by": None,
            },
            {
                "entity_id": "switch.plug",
                "device_id": None,
                "area_id": "kitchen",
                "disabled_by": None,
            },
        ]
        self.calls = []
        self.posted = []
        self.connections = 0
        self.sockets = []
        self.subscription_id = None
        self.reject_auth = False

    def app(self):
        app = web.Application()
        app.router.add_get("/api/websocket", self.websocket)
        app.router.add_post("/api/states/{entity_id}", self.post_state)
        return app

    async def post_state(self, request):
        self.posted.append((request.match_info["entity_id"], await request.json(), request.headers))
        return web.json_response({"ok": True})

    async def websocket(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.connections += 1
        self.sockets.append(ws)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.10.4"})
        await ws.receive_json()
        if self.reject_auth:
            await ws.send_json({"type": "auth_invalid", "message": "nope"})
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.10.4"})
        registries = {
            "get_states": self.states,
            "config/area_registry/list": self.areas,
            "config/device_registry/list": self.devices,
            "config/entity_registry/list": self.entities,
        }
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            command = msg.json()
            if command["type"] == "subscribe_events":
                self.subscription_id = command["id"]
                result = None
            elif command["type"] == "call_service":
                self.calls.append(command)
                result = {"context": {}}
            else:
                result = registries[command["type"]]
            await ws.send_json(
                {"id": command["id"], "type": "result", "success": True, "result": result}
            )
        return ws

    async def send_state_changed(self, new_state, entity_id=None):
        await self.sockets[-1].send_json(
            {
                "id": self.subscription_id,
                "type": "event",
                "event": {
                    "event_type": "state_changed",
                    "data": {
                        "entity_id": entity_id or new_state["entity_id"],
                        "new_state": new_state,
                    },
                },
            }
        )


@pytest.fixture
async def hass(monkeypatch):
    monkeypatch.setattr(homeassistant, "RECONNECT_DELAY", 0.05)
    fake = FakeHomeAssistant()
    async with TestServer(fake.app()) as server:
        fake.url = str(server.make_url(""))
        yield fake


@pytest.fixture
async def controller(hass):
    ctl = HomeAssistantController(hass.url, "secret")
    await ctl.connect()
    yield ctl
    await ctl.disconnect()


async def eventually(condition, timeout=2.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


def test_api_urls_for_the_supported_url_forms():
    api_urls = homeassistant._api_urls
    assert api_urls("http://supervisor/core") == (
        "http://supervisor/core/api",
        "ws://supervisor/core/api/websocket",
    )
    assert api_urls("https://ha.example.com:8123/") == (
        "https://ha.example.com:8123/api",
        "wss://ha.example.com:8123/api/websocket",
    )
    assert api_urls("http://ha:8123/api/websocket")[1] == "ws://ha:8123/api/websocket"


async def test_states_and_registries_are_loaded_on_connect(controller):
    assert controller.get_entity_state("light.lamp") == LIGHT
    assert controller.get_entity_state("light.missing") is None
    assert controller.get_entities() == ["light.lamp"]
    assert controller.get_entities("switch") == ["switch.plug"]
    assert controller.get_device_id_from_entity_id("light.lamp") == "dev1"
    assert controller.get_device_id_from_entity_id("light.missing") is None
    assert controller.get_device_attributes("dev1")["area_id"] == "kitchen"
    assert controller.get_device_attributes("unknown") == {}


async def test_entities_are_assigned_to_the_area_of_entity_or_device(controller):
    areas = await controller.async_get_area_entities()
    assert areas["kitchen"]["name"] == "Kitchen"
    # light.lamp inherits the kitchen from its device, light.own overrides it,
    # light.off is disabled and switch.plug is filtered out by the domain
    assert areas["kitchen"]["entities"] == ["light.lamp"]
    assert areas["attic"]["entities"] == ["light.own"]

    areas = await controller.async_get_area_entities(["switch."])
    assert areas["kitchen"]["entities"] == ["switch.plug"]


async def test_area_lookup_does_not_alter_the_registry(controller):
    await controller.async_get_area_entities()
    assert "entities" not in controller._area_registry["kitchen"]


async def test_state_changed_updates_the_cache_and_calls_the_callback(hass, controller):
    seen = []

    async def callback(event_type, data):
        seen.append((event_type, data["entity_id"], data["new_state"]["state"]))

    controller.register_state_changed_callback(callback, "light.lamp")
    other = []

    async def other_callback(event_type, data):
        other.append(data)

    controller.register_state_changed_callback(other_callback, "light.other")

    await hass.send_state_changed({**LIGHT, "state": "off"})
    await eventually(lambda: seen)

    assert seen == [("state_changed", "light.lamp", "off")]
    assert controller.get_entity_state("light.lamp")["state"] == "off"
    assert other == []


async def test_removed_callback_is_not_called_again(hass, controller):
    seen = []

    async def callback(event_type, data):
        seen.append(data)

    remove = controller.register_state_changed_callback(callback, "light.lamp")
    remove()
    await hass.send_state_changed({**LIGHT, "state": "off"})
    await eventually(lambda: controller.get_entity_state("light.lamp")["state"] == "off")
    assert seen == []


async def test_removed_entity_is_dropped_from_the_cache(hass, controller):
    await hass.send_state_changed(None, "light.lamp")
    await eventually(lambda: controller.get_entity_state("light.lamp") is None)
    assert controller.get_entities() == []


async def test_a_failing_callback_does_not_stop_the_state_updates(hass, controller):
    async def broken(event_type, data):
        raise RuntimeError("boom")

    controller.register_state_changed_callback(broken, "light.lamp")
    await hass.send_state_changed({**LIGHT, "state": "off"})
    await hass.send_state_changed({**LIGHT, "state": "on"})
    await eventually(lambda: not controller._tasks)
    assert controller.get_entity_state("light.lamp")["state"] == "on"


async def test_turn_on_and_off_call_the_homeassistant_services(hass, controller):
    await controller.async_turn_on("light.lamp", {"brightness": 100})
    await controller.async_turn_off("light.lamp")

    on, off = hass.calls
    assert (on["domain"], on["service"]) == ("homeassistant", "turn_on")
    assert on["service_data"] == {"brightness": 100, "entity_id": "light.lamp"}
    assert (off["domain"], off["service"]) == ("homeassistant", "turn_off")
    assert off["service_data"] == {"entity_id": "light.lamp"}


async def test_notifications_use_the_persistent_notification_service(hass, controller):
    await controller.async_create_notification("pair now", "hue_link")
    await controller.async_dismiss_notification("hue_link")

    create, dismiss = hass.calls
    assert (create["domain"], create["service"]) == ("persistent_notification", "create")
    assert create["service_data"] == {
        "notification_id": "hue_link",
        "title": "Emulated Hue Bridge",
        "message": "pair now",
    }
    assert dismiss["service"] == "dismiss"


async def test_set_state_posts_to_the_rest_api_with_the_token(hass, controller):
    await controller.set_state("sensor.hue", "on", {"room": "Living"})

    entity_id, body, headers = hass.posted[0]
    assert entity_id == "sensor.hue"
    assert body == {"state": "on", "attributes": {"room": "Living"}}
    assert headers["Authorization"] == "Bearer secret"


async def test_reconnects_and_reloads_after_the_connection_dropped(hass, controller):
    hass.states = [{**LIGHT, "state": "off"}]
    await hass.sockets[-1].close()
    await eventually(lambda: hass.connections == 2)
    await eventually(lambda: controller.get_entity_state("light.lamp")["state"] == "off")
    assert controller.get_entities("switch") == []

    # the subscription is active again on the new connection
    await hass.send_state_changed({**LIGHT, "state": "on"})
    await eventually(lambda: controller.get_entity_state("light.lamp")["state"] == "on")
    await controller.async_turn_off("light.lamp")
    assert hass.calls[-1]["service"] == "turn_off"


async def test_connect_fails_when_the_token_is_rejected(hass):
    hass.reject_auth = True
    ctl = HomeAssistantController(hass.url, "wrong")
    with pytest.raises(AuthenticationFailed):
        await ctl.connect()
    await ctl.disconnect()


async def test_connect_fails_when_home_assistant_is_unreachable():
    ctl = HomeAssistantController("http://127.0.0.1:1", "secret")
    with pytest.raises(CannotConnect):
        await ctl.connect()
    await ctl.disconnect()


async def test_disconnect_does_not_try_to_reconnect(hass):
    ctl = HomeAssistantController(hass.url, "secret")
    await ctl.connect()
    await ctl.disconnect()
    await asyncio.sleep(0.2)
    assert hass.connections == 1
