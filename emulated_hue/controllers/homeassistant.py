"""Controller for Home Assistant communication."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import ClientSession
from hass_client import HomeAssistantClient
from hass_client.exceptions import CannotConnect

from emulated_hue.const import (
    HASS_ATTR_ENTITY_ID,
    HASS_DOMAIN_HOMEASSISTANT,
    HASS_DOMAIN_PERSISTENT_NOTIFICATION,
    HASS_SERVICE_PERSISTENT_NOTIFICATION_CREATE,
    HASS_SERVICE_PERSISTENT_NOTIFICATION_DISMISS,
    HASS_SERVICE_TURN_OFF,
    HASS_SERVICE_TURN_ON,
)

LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10
RECONNECT_DELAY = 10
EVENT_STATE_CHANGED = "state_changed"

StateChangedCallback = Callable[[str, dict], Awaitable[None]]


def _api_urls(url: str) -> tuple[str, str]:
    """Return the REST and websocket API urls for a Home Assistant base url."""
    base = url.rstrip("/").removesuffix("/api/websocket").removesuffix("/api")
    rest_url = f"{base}/api"
    # http -> ws, https -> wss
    return rest_url, f"ws{rest_url.removeprefix('http')}/websocket"


class HomeAssistantController:
    """Home Assistant connection with a local state and registry cache."""

    def __init__(self, url: str, token: str | None) -> None:
        """Create the controller, must be called with a running loop."""
        self._rest_url, self._websocket_url = _api_urls(url)
        self._token = token or os.environ.get("HASSIO_TOKEN")
        self._session = ClientSession()
        self._client: HomeAssistantClient | None = None
        self._listener: asyncio.Task | None = None
        self._supervisor: asyncio.Task | None = None
        self._stopping = False
        self._tasks: set[asyncio.Task] = set()
        self._callbacks: dict[str, list[StateChangedCallback]] = {}
        self._states: dict[str, dict] = {}
        self._area_registry: dict[str, dict] = {}
        self._device_registry: dict[str, dict] = {}
        self._entity_registry: dict[str, dict] = {}

    # connection handling

    async def connect(self) -> None:
        """Connect and load all states, keeping the connection up afterwards."""
        await self._connect_and_load()
        self._supervisor = asyncio.create_task(self._supervise_connection())

    async def disconnect(self) -> None:
        """Stop reconnecting and close the connection."""
        self._stopping = True
        if self._supervisor:
            self._supervisor.cancel()
            await asyncio.gather(self._supervisor, return_exceptions=True)
        if self._client:
            await self._client.disconnect()
        await self._session.close()

    async def _connect_and_load(self) -> None:
        """Open a new connection, subscribe to state changes and load the caches."""
        client = HomeAssistantClient(self._websocket_url, self._token, self._session)
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                await client.connect()
        except TimeoutError as err:
            raise CannotConnect(err) from err
        self._client = client
        self._listener = asyncio.create_task(client.start_listening())
        try:
            await client.subscribe_events(self._handle_event, EVENT_STATE_CHANGED)
            states, areas, devices, entities = await asyncio.gather(
                client.get_states(),
                client.get_area_registry(),
                client.get_device_registry(),
                client.get_entity_registry(),
            )
        except BaseException:
            await client.disconnect()
            raise
        self._states = {state["entity_id"]: state for state in states}
        self._area_registry = {area["area_id"]: area for area in areas}
        self._device_registry = {device["id"]: device for device in devices}
        self._entity_registry = {entity["entity_id"]: entity for entity in entities}

    async def _supervise_connection(self) -> None:
        """Reconnect whenever the connection is lost."""
        while not self._stopping:
            await asyncio.wait({self._listener})
            if self._stopping:
                return
            LOGGER.warning(
                "Connection to Home Assistant lost, reconnecting every %s seconds...",
                RECONNECT_DELAY,
            )
            while not self._stopping:
                await asyncio.sleep(RECONNECT_DELAY)
                try:
                    await self._connect_and_load()
                except Exception as err:  # noqa: BLE001
                    LOGGER.debug("Reconnecting to Home Assistant failed: %s", err)
                else:
                    break

    # state cache

    def _handle_event(self, event: dict) -> None:
        """Update the state cache and notify the callbacks of the entity."""
        data = event["data"]
        entity_id = data["entity_id"]
        if data["new_state"] is None:
            self._states.pop(entity_id, None)
        else:
            self._states[entity_id] = data["new_state"]
        for callback in self._callbacks.get(entity_id, []):
            task = asyncio.create_task(callback(event["event_type"], data))
            self._tasks.add(task)
            task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        """Forget a finished callback task and log its failure."""
        self._tasks.discard(task)
        if not task.cancelled() and (err := task.exception()):
            LOGGER.error("State changed callback failed: %s", err, exc_info=err)

    def register_state_changed_callback(
        self, callback: StateChangedCallback, entity_id: str
    ) -> Callable[[], None]:
        """
        Register callback to notify of state change event on an entity.

            :param callback: The callback to call when the state changes.
            :param entity_id: The ID of the entity.
            :return: A callable to remove the callback.
        """
        self._callbacks.setdefault(entity_id, []).append(callback)

        def remove_callback() -> None:
            self._callbacks[entity_id].remove(callback)

        return remove_callback

    def get_entity_state(self, entity_id: str) -> dict | None:
        """
        Get the state of an entity in Home Assistant.

            :param entity_id: The ID of the entity.
        """
        return self._states.get(entity_id)

    def get_entities(self, domain: str = "light") -> list[str]:
        """
        Get entity_ids of a domain in Home Assistant.

            :param domain: The domain of the entities.
            :return: A list of entity IDs.
        """
        return [entity_id for entity_id in self._states if entity_id.startswith(f"{domain}.")]

    def get_device_attributes(self, device_id: str) -> dict:
        """Get the attributes of a device in Home Assistant."""
        return self._device_registry.get(device_id, {})

    def get_device_id_from_entity_id(self, entity_id: str) -> str | None:
        """Get the device ID from an entity ID."""
        return self._entity_registry.get(entity_id, {}).get("device_id")

    async def async_get_area_entities(self, domain_filter: list | None = None) -> dict[str, dict]:
        """
        Get all areas mapped to entities contained. Excludes disabled entities.

            :return: A dictionary of devices in the area. {area_id: {name: str, entities:[entity_ids]}}
        """
        domain_prefixes = tuple(domain_filter or ["light."])
        result = {
            area_id: {**area, "entities": []} for area_id, area in self._area_registry.items()
        }
        for entity in self._entity_registry.values():
            if entity.get("disabled_by"):
                # do not include disabled devices
                continue
            entity_id = entity["entity_id"]
            # only include devices that are matched by the filter
            if not entity_id.startswith(domain_prefixes):
                continue
            # an entity inherits the area of its device unless it has an own one
            device = self._device_registry.get(entity.get("device_id"), {})
            area_id = entity.get("area_id") or device.get("area_id")
            if area_id in result:
                result[area_id]["entities"].append(entity_id)
        return result

    # commands

    async def async_turn_off(self, entity_id: str) -> None:
        """
        Turn off a generic entity in Home Assistant.

            :param entity_id: The ID of the entity.
        """
        data = {HASS_ATTR_ENTITY_ID: entity_id}
        await self._client.call_service(HASS_DOMAIN_HOMEASSISTANT, HASS_SERVICE_TURN_OFF, data)

    async def async_turn_on(self, entity_id: str, data: dict) -> None:
        """
        Turn on a generic entity in Home Assistant.

            :param entity_id: The ID of the entity.
            :param data: The service data.
        """
        data[HASS_ATTR_ENTITY_ID] = entity_id
        await self._client.call_service(HASS_DOMAIN_HOMEASSISTANT, HASS_SERVICE_TURN_ON, data)

    async def async_create_notification(
        self,
        msg: str,
        notification_id: str,
        title: str = "Emulated Hue Bridge",
    ) -> None:
        """
        Create a notification in Home Assistant.

            :param msg: The message to display in the notification.
            :param notification_id: The ID of the notification.
            :param title: The title of the notification.
        """
        await self._client.call_service(
            HASS_DOMAIN_PERSISTENT_NOTIFICATION,
            HASS_SERVICE_PERSISTENT_NOTIFICATION_CREATE,
            {
                "notification_id": notification_id,
                "title": title,
                "message": msg,
            },
        )

    async def async_dismiss_notification(self, notification_id: str) -> None:
        """
        Dismisses a notification in Home Assistant.

            :param notification_id: The ID of the notification.
        """
        await self._client.call_service(
            HASS_DOMAIN_PERSISTENT_NOTIFICATION,
            HASS_SERVICE_PERSISTENT_NOTIFICATION_DISMISS,
            {"notification_id": notification_id},
        )

    async def set_state(
        self, entity_id: str, new_state: str, state_attributes: dict | None = None
    ) -> Any:
        """
        Set the state of an entity (only the state machine, no device is controlled).

            :param entity_id: Entity id to set state for.
            :param new_state: The new state.
            :param state_attributes: Optional dict with attributes (e.g. { name: 'Cool entity' }).
        """
        async with self._session.post(
            f"{self._rest_url}/states/{entity_id}",
            headers={"Authorization": f"Bearer {self._token}"},
            json={"state": new_state, "attributes": state_attributes or {}},
        ) as response:
            if response.status >= 400:
                LOGGER.warning(
                    "Setting the state of %s failed: HTTP %s", entity_id, response.status
                )
                return None
            return await response.json()
