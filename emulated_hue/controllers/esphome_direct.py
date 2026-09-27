"""
Direct ESPHome native-API path for light control.

Both Hue Entertainment streaming and plain classic-API light control
normally go through Home Assistant's websocket service-call pipeline (state
machine, event bus, then the ESPHome integration) before reaching the
device. For lights that have esphome_host/esphome_port/esphome_password set
(in their light config in emulated_hue.json, or as an add-on-wide default),
we instead talk to the device directly over the ESPHome native API - the
same protocol Home Assistant itself uses - which skips that pipeline and
lets us apply our own colour conversion instead of HA's generic xy->RGB
conversion.

Connections are cached per host for the lifetime of the process (closed via
async_close_all() on app shutdown) since both the entertainment path and
the classic per-light path share them.

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
# Hosts we've already logged as inactive, so a still-unreachable device
# doesn't spam a warning/info line on every single request.
_logged_inactive: set[str] = set()


def _log_inactive(host: str) -> None:
    if host not in _logged_inactive:
        LOGGER.info(
            "Direct ESPHome path inactive for %s - falling back to Home Assistant",
            host,
        )
        _logged_inactive.add(host)


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
            _log_inactive(host)
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
            _log_inactive(host)
            return None
        _light_keys[host] = light.key
        _logged_inactive.discard(host)
        LOGGER.info(
            "Direct ESPHome path active for %s (light %s)",
            host,
            light.object_id,
        )

    return client, _light_keys[host]


async def async_send_light_state(
    *,
    host: str,
    port: int,
    password: str,
    object_id: str | None = None,
    power: bool = True,
    rgb: tuple[float, float, float] | None = None,
    color_temperature: float | None = None,
    brightness: float | None = None,
) -> bool:
    """
    Send a light state directly to an ESPHome light.

    rgb channels and brightness are 0-1 floats, color_temperature is in mireds
    (matching what the ESPHome native API expects). Only one of rgb /
    color_temperature should be given; omit both for a plain on/off or
    brightness-only change. Returns True on success, False if the device
    isn't reachable right now (caller should fall back).
    """
    ready = await _async_get_ready_client(host, port, password, object_id)
    if ready is None:
        return False
    client, key = ready

    color_mode = None
    if rgb is not None:
        color_mode = ColorMode.RGB
    elif color_temperature is not None:
        color_mode = ColorMode.COLOR_TEMPERATURE
    try:
        client.light_command(
            key=key,
            state=power,
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
        _log_inactive(host)
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
    _logged_inactive.clear()
