"""
Direct WiZ UDP path for light control.

Like esphome_direct.py, this exists to skip Home Assistant's service-call
pipeline for lights that have wiz_host/wiz_port set (in their light config in
emulated_hue.json, or as an add-on-wide default), talking straight to the
device over WiZ's own UDP control protocol (plain JSON over port 38899, the
same one Home Assistant's own WiZ integration and pywizlight use - no
authentication).

Sends are fire-and-forget: Entertainment streams frames at 25-50Hz, and a
real WiFi UDP round-trip is on the order of 100ms+, so waiting for the
device's ack on every frame would make this slower than the Home Assistant
path it's replacing. We only wait for a response once per host, to confirm
the device is actually reachable (mirrors esphome_direct's connect step).

WiZ's protocol treats r/g/b as absolute 0-255 output levels and `dimming` as
a separate 0-100 overall-brightness scaler applied on top - two independent
axes, same as ESPHome's rgb (colour ratio) + brightness (level) split. Some
callers (entertainment frames, xy-derived colour) already bake brightness
into their rgb tuple; others (hue/sat) don't. Rather than track that per
caller, we always normalize the incoming rgb to peak channel = 1.0 before
sending, and let the separate `brightness` argument be the sole source of
`dimming` - consistent regardless of which caller baked what into rgb.

Callers should treat a False return as "not available right now" and fall
back to the existing Home Assistant path; nothing here raises.
"""
import asyncio
import json
import logging
import socket

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 38899
_PROBE_TIMEOUT = 2.0
# WiZ rejects/clamps dimming below this - matches the app's own slider floor.
_MIN_DIMMING = 10

# Hosts confirmed reachable so far - once probed successfully we assume the
# device stays up, since fire-and-forget sends can't detect it going away.
_reachable_hosts: set[str] = set()
# Hosts we've already logged as inactive, so a still-unreachable device
# doesn't spam a warning/info line on every single request.
_logged_inactive: set[str] = set()
# Single-slot holder (mutated in place, keyed by None) instead of a bare
# module global, matching how esphome_direct caches its own state in dicts.
_transports: dict[None, asyncio.DatagramTransport] = {}


def _log_inactive(host: str) -> None:
    if host not in _logged_inactive:
        LOGGER.info(
            "Direct WiZ path inactive for %s - falling back to Home Assistant",
            host,
        )
        _logged_inactive.add(host)


def _probe_sync(host: str, port: int) -> bool:
    """Blocking one-shot reachability probe; only ever run in a thread."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(_PROBE_TIMEOUT)
            sock.sendto(
                json.dumps({"method": "getPilot", "params": {}}).encode(),
                (host, port),
            )
            sock.recvfrom(4096)
        return True
    except OSError:
        return False


async def _async_ensure_reachable(host: str, port: int) -> bool:
    """Probe a WiZ device once per host; cache success, retry on failure."""
    if host in _reachable_hosts:
        return True
    reachable = await asyncio.get_running_loop().run_in_executor(
        None, _probe_sync, host, port
    )
    if reachable:
        _reachable_hosts.add(host)
        _logged_inactive.discard(host)
        LOGGER.info("Direct WiZ path active for %s", host)
    else:
        _log_inactive(host)
    return reachable


async def _async_get_transport() -> asyncio.DatagramTransport:
    if None not in _transports:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("0.0.0.0", 0)
        )
        _transports[None] = transport
    return _transports[None]


def _rgb_to_pilot_params(
    rgb: tuple[float, float, float], brightness: float | None
) -> dict:
    peak = max(rgb)
    if peak > 0:
        r, g, b = (round(channel / peak * 255) for channel in rgb)
    else:
        r = g = b = 0
    level = brightness if brightness is not None else 1.0
    return {"r": r, "g": g, "b": b, "dimming": max(_MIN_DIMMING, round(level * 100))}


def _color_temperature_to_pilot_params(
    color_temperature: float, brightness: float | None
) -> dict:
    kelvin = round(1_000_000 / color_temperature) if color_temperature else 4000
    kelvin = max(2200, min(kelvin, 6500))  # WiZ RGBW Tunable's supported range
    params = {"temp": kelvin}
    if brightness is not None:
        params["dimming"] = max(_MIN_DIMMING, round(brightness * 100))
    return params


async def async_send_light_state(
    *,
    host: str,
    port: int = DEFAULT_PORT,
    power: bool = True,
    rgb: tuple[float, float, float] | None = None,
    color_temperature: float | None = None,
    brightness: float | None = None,
) -> bool:
    """
    Send a light state directly to a WiZ light over its UDP control protocol.

    rgb channels and brightness are 0-1 floats, color_temperature is in
    mireds - same convention as esphome_direct.async_send_light_state, so
    both can be called with the same arguments. Returns True once the packet
    is sent to a host confirmed reachable, False if the device isn't
    reachable right now (caller should fall back).
    """
    if not await _async_ensure_reachable(host, port):
        return False

    params: dict = {"state": power}
    if power:
        if rgb is not None:
            params.update(_rgb_to_pilot_params(rgb, brightness))
        elif color_temperature is not None:
            params.update(
                _color_temperature_to_pilot_params(color_temperature, brightness)
            )
        elif brightness is not None:
            params["dimming"] = max(_MIN_DIMMING, round(brightness * 100))

    transport = await _async_get_transport()
    transport.sendto(
        json.dumps({"method": "setPilot", "params": params}).encode(), (host, port)
    )
    return True


async def async_close_all() -> None:
    """Close the shared WiZ UDP transport. Call on app shutdown."""
    transport = _transports.pop(None, None)
    if transport is not None:
        transport.close()
    _reachable_hosts.clear()
    _logged_inactive.clear()
