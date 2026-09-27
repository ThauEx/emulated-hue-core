"""
Direct ESPHome native-API path for entertainment streaming.

Hue Entertainment mode calls into Home Assistant for every light on every
frame (25-50Hz), which normally goes through HA's websocket service-call
pipeline (state machine, event bus, then the ESPHome integration) before it
reaches the device. For lights that have esphome_host/esphome_port/
esphome_password set in their light config (in emulated_hue.json), we instead
talk to the device directly over the ESPHome native API - the same protocol
Home Assistant itself uses - which skips that pipeline and lets us apply our
own colour conversion instead of HA's generic xy->RGB conversion.

Callers should treat a False return as "not available right now" and fall
back to the existing Home Assistant path; nothing here raises.
"""
import logging

from aioesphomeapi import APIClient, APIConnectionError, ColorMode, LightInfo

LOGGER = logging.getLogger(__name__)

# One persistent connection per ESPHome host, reused across all entertainment
# frames for as long as the stream is active. Keyed by host so the same
# device is never dialed twice even if it appears under multiple light ids.
_clients: dict[str, APIClient] = {}
_light_keys: dict[str, int] = {}


async def _async_get_ready_client(
    host: str, port: int, password: str, object_id: str | None
) -> tuple[APIClient, int] | None:
    """Return a connected client plus the resolved light entity key."""
    client = _clients.get(host)
    if client is None:
        client = APIClient(host, port, password)
        _clients[host] = client

    if host not in _light_keys:
        try:
            if client.api_version is None:
                await client.connect(login=True)
            entities, _ = await client.list_entities_services()
        except APIConnectionError as err:
            LOGGER.warning("Could not connect to ESPHome device at %s: %s", host, err)
            _clients.pop(host, None)
            return None

        lights = [entity for entity in entities if isinstance(entity, LightInfo)]
        light = None
        if object_id:
            light = next((li for li in lights if li.object_id == object_id), None)
        if light is None and lights:
            if len(lights) > 1:
                LOGGER.warning(
                    "Multiple lights found on ESPHome device %s and no "
                    "esphome_object_id configured for this light, using the "
                    "first one found (%s)",
                    host,
                    lights[0].object_id,
                )
            light = lights[0]
        if light is None:
            LOGGER.warning("No light entity found on ESPHome device %s", host)
            return None
        _light_keys[host] = light.key

    return client, _light_keys[host]


async def async_send_color(
    *,
    host: str,
    port: int,
    password: str,
    object_id: str | None = None,
    rgb: tuple[float, float, float] | None = None,
    color_temperature: float | None = None,
    brightness: float | None = None,
) -> bool:
    """
    Send a colour directly to an ESPHome light.

    rgb channels and brightness are 0-1 floats, color_temperature is in mireds
    (matching what the ESPHome native API expects). Returns True on success,
    False if the device isn't reachable right now (caller should fall back).
    """
    ready = await _async_get_ready_client(host, port, password, object_id)
    if ready is None:
        return False
    client, key = ready

    color_mode = ColorMode.RGB if rgb is not None else ColorMode.COLOR_TEMPERATURE
    try:
        client.light_command(
            key=key,
            state=True,
            color_mode=color_mode,
            rgb=rgb,
            color_temperature=color_temperature,
            brightness=brightness,
            transition_length=0,
        )
    except APIConnectionError as err:
        LOGGER.warning("Lost connection to ESPHome device %s: %s", host, err)
        _clients.pop(host, None)
        _light_keys.pop(host, None)
        return False
    return True


async def async_close_all() -> None:
    """Disconnect all cached ESPHome connections. Call when entertainment mode stops."""
    for host, client in list(_clients.items()):
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort cleanup on shutdown
            LOGGER.debug(
                "Error disconnecting from ESPHome device %s", host, exc_info=True
            )
    _clients.clear()
    _light_keys.clear()
