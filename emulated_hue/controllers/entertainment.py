"""Experimental support for Hue Entertainment API."""

# https://developers.meethue.com/develop/hue-entertainment/philips-hue-entertainment-api/
import asyncio
import logging
import os
import time

from emulated_hue.controllers import esphome_direct, wiz_direct
from emulated_hue.controllers.color_utils import (
    apply_balance,
    gamut_for,
    lamp_brightness,
    xy_brightness_to_rgb,
)
from emulated_hue.controllers.devices import async_get_device

from .models import Controller

LOGGER = logging.getLogger(__name__)

# with debug logging, show at most this many frames per second for each light
TRACE_FRAMES_PER_SECOND = 4
# A frame that turns into the same command as the last one is not sent again,
# a still picture would otherwise keep the lamp busy with 20-50 identical
# commands per second (each one restarts its transition and is broadcast as a
# state change to every connected client). It is repeated this often anyway, in
# case somebody else changed the lamp in between.
REPEAT_REFRESH_SECONDS = 2.0

COLOR_TYPE_RGB = "RGB"
COLOR_TYPE_XY_BR = "XY Brightness"
HASS_SENSOR = "binary_sensor.emulated_hue_entertainment_active"


if os.path.isfile("/usr/local/opt/openssl@1.1/bin/openssl"):
    OPENSSL_BIN = "/usr/local/opt/openssl@1.1/bin/openssl"
elif os.path.isfile("C:/Program Files/Git/usr/bin/openssl.exe"):
    OPENSSL_BIN = "C:/Program Files/Git/usr/bin/openssl.exe"
else:
    OPENSSL_BIN = "openssl"


def chunked(size, source):
    """Helpermethod to get chunks of size x from a bytes source."""
    for i in range(0, len(source), size):
        yield source[i : i + size]


class EntertainmentAPI:
    """Handle UDP socket for HUE Entertainment (streaming mode)."""

    def __init__(self, ctl: Controller, group_details: dict, user_details: dict):
        """Initialize the class."""
        self.ctl: Controller = ctl
        self.group_details = group_details
        self._interrupted = False
        self._socket_daemon = None
        self._timestamps = {}
        # light id -> frames skipped since its direct path went down
        self._paused: dict[str, int] = {}
        # light id -> (what we last sent it, when)
        self._last_sent: dict[str, tuple[tuple[int, ...], float]] = {}
        self._prev_data = {}
        self._user_details = user_details
        self._received_first_frame = False
        self.ctl.loop.create_task(self.async_run())

        self._pkt_header_begin_size = 9  # HueStream
        self._pkt_header_protocol_size = 7  # protocol version, sequence
        self._pkt_header_uuid_size = 36
        self._pkt_light_data_size = 9 * 20  # 20 channels max
        self._max_pkt_size = (
            self._pkt_header_begin_size
            + self._pkt_header_protocol_size
            + self._pkt_header_uuid_size
            + self._pkt_light_data_size
        )

        num_lights = len(self.group_details["lights"])
        self._likely_pktsize = 16 + (9 * num_lights)

    async def async_run(self):
        """Run the server."""
        # MDTLS + PSK is not supported very well in native python
        # As a (temporary?) workaround we rely on the OpenSSL executable which is
        # very well supported on all platforms.
        LOGGER.info("Start HUE Entertainment Service on UDP port 2100.")
        await self.ctl.controller_hass.set_state(
            HASS_SENSOR, "on", {"room": self.group_details["name"]}
        )
        args = [
            OPENSSL_BIN,
            "s_server",
            "-dtls",
            "-accept",
            "2100",
            "-nocert",
            "-psk_identity",
            self._user_details["username"],
            "-psk",
            self._user_details["clientkey"],
            "-quiet",
        ]
        # NOTE: enable stdin is required for openssl, even if we do not use it.
        self._socket_daemon = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            limit=self._max_pkt_size,
        )
        buffer = b""
        while not self._interrupted:
            # Once the client starts streaming, it will pass in packets
            # at a rate between 25 and 50 packets per second !

            # Prevent buffer overflow, keep 2 packets max
            buffer = buffer[-((self._max_pkt_size + self._pkt_header_begin_size) * 2) :]
            buffer += await self._socket_daemon.stdout.read(self._likely_pktsize)
            pkts = buffer.split(b"HueStream")
            if len(pkts) > 1:
                pkt = None
                for pkt in pkts[:-1]:
                    pkt = b"HueStream" + pkt
                    await self.__process_packet(pkt)
                buffer = pkts[-1]
                # adjust next read to match packet size
                if (guess := len(pkt) - len(buffer)) > 0:
                    self._likely_pktsize = guess

    def stop(self):
        """Stop the Entertainment service."""
        self._interrupted = True
        if self._socket_daemon:
            self._socket_daemon.kill()
        self.ctl.loop.create_task(self.ctl.controller_hass.set_state(HASS_SENSOR, "off"))
        LOGGER.info("HUE Entertainment Service stopped.")

    async def __process_packet(self, packet: bytes) -> None:
        # Ignore first header message
        if len(packet) < self._pkt_header_begin_size + self._pkt_header_protocol_size:
            return

        if not self._received_first_frame:
            # "Start HUE Entertainment Service" only means our DTLS listener
            # came up - it says nothing about whether the client actually
            # completed the handshake and is sending real frames. This
            # confirms real streamed data is arriving, not just a listening
            # socket.
            LOGGER.info(
                "Entertainment stream: first frame received for group %s",
                self.group_details.get("name"),
            )
            self._received_first_frame = True

        version = packet[9]
        color_space = COLOR_TYPE_RGB if packet[14] == 0 else COLOR_TYPE_XY_BR
        lights_data = packet[16:] if version == 1 else packet[52:]
        tasks = []
        # issue command to all lights
        for light_data in chunked(9, lights_data):
            tasks.append(self.__async_process_light_packet(light_data, color_space))
        await asyncio.gather(*tasks)

    def _trace_frame(self, light_id: str, frame: str, rgb: tuple[float, float, float]) -> None:
        """Log what a frame turns into for the lamp, a few frames per second."""
        now = time.monotonic()
        key = f"trace:{light_id}"
        if now - self._timestamps.get(key, 0.0) < 1 / TRACE_FRAMES_PER_SECOND:
            return
        self._timestamps[key] = now
        LOGGER.debug(
            "Frame light %s: %s -> rgb=(%.3f, %.3f, %.3f) level=%.3f",
            light_id,
            frame,
            *rgb,
            lamp_brightness(rgb),
        )

    def _is_repeat(self, light_id: str, frame_key: tuple[int, ...], now: float) -> bool:
        """Return whether the light already got exactly this frame a moment ago."""
        last = self._last_sent.get(light_id)
        return last is not None and last[0] == frame_key and now - last[1] < REPEAT_REFRESH_SECONDS

    def _track_delivery(self, light_id: str, frame_key: tuple[int, ...], delivered: bool) -> None:
        """Remember what a light got, and log when its direct path stops and resumes."""
        if delivered:
            self._last_sent[light_id] = (frame_key, time.monotonic())
            if (skipped := self._paused.pop(light_id, None)) is not None:
                LOGGER.info(
                    "Direct path for light %s is back after %s skipped frames", light_id, skipped
                )
            return
        # whatever the lamp shows now is unknown, so the next frame must be sent
        self._last_sent.pop(light_id, None)
        if light_id not in self._paused:
            self._paused[light_id] = 1
            LOGGER.warning(
                "Direct path for light %s is down, skipping its frames until it is back "
                "(Home Assistant is not used while streaming)",
                light_id,
            )
        else:
            self._paused[light_id] += 1

    async def __async_process_light_packet(self, light_data, color_space):
        """Process an incoming stream message."""
        # 16-bit big-endian channel id, not light_data[1] + light_data[2]
        # (which silently collapses distinct ids whenever the high byte is
        # nonzero - harmless below 256 channels, wrong above it).
        light_id = str((light_data[1] << 8) | light_data[2])
        light_conf = await self.ctl.config_instance.async_get_light_config(light_id)

        if color_space == COLOR_TYPE_RGB:
            red = (light_data[3] * 256 + light_data[4]) / 65535
            green = (light_data[5] * 256 + light_data[6]) / 65535
            blue = (light_data[7] * 256 + light_data[8]) / 65535
            rgb = (red, green, blue)
            brightness = sum(rgb) / 3
            if LOGGER.isEnabledFor(logging.DEBUG):
                self._trace_frame(light_id, f"rgb=({red:.4f}, {green:.4f}, {blue:.4f})", rgb)
        else:
            x = float((light_data[3] * 256 + light_data[4]) / 65535)
            y = float((light_data[5] * 256 + light_data[6]) / 65535)
            brightness = (light_data[7] * 256 + light_data[8]) / 65535
            rgb = xy_brightness_to_rgb(x, y, brightness, gamut_for(light_conf))
            if LOGGER.isEnabledFor(logging.DEBUG):
                self._trace_frame(light_id, f"xy=({x:.3f}, {y:.3f}) bri={brightness:.4f}", rgb)

        # Set per-light via the ingress config panel (see ingress.py) -
        # esphome_host/wiz_host being unset just means "use the normal
        # Home Assistant path" for this light.
        direct_rgb = apply_balance(rgb, light_conf)
        esphome_host = light_conf.get("esphome_host")
        wiz_host = light_conf.get("wiz_host")
        # A light with a direct target is streamed to only through that path. A
        # frame that can't be delivered is skipped instead of being sent through
        # Home Assistant: that would hit the lamp from a second side (plus the
        # reconnect) while it is already struggling, with a different brightness
        # mapping, which looks like flicker.
        if esphome_host:
            # The HA path honors the light's `throttle` via the device layer,
            # but this path bypasses that - without it a 25-50Hz stream hits
            # a small ESP8266 with every frame (plus a state broadcast per
            # frame to each connected API client).
            now = time.monotonic()
            throttle_ms = light_conf.get("throttle") or 0
            if now - self._timestamps.get(light_id, 0.0) < throttle_ms / 1000:
                return
            level = lamp_brightness(rgb)
            frame_key = tuple(round(c * 255) for c in (*direct_rgb, level))
            if self._is_repeat(light_id, frame_key, now):
                return
            self._timestamps[light_id] = now
            self._track_delivery(
                light_id,
                frame_key,
                await esphome_direct.async_send_light_state(
                    host=esphome_host,
                    port=light_conf.get("esphome_port") or esphome_direct.DEFAULT_PORT,
                    credential=light_conf.get("esphome_password", ""),
                    object_id=light_conf.get("esphome_object_id"),
                    rgb=direct_rgb,
                    # The lamp only keeps the ratio between the rgb channels, so
                    # the frame's level goes in the brightness field.
                    brightness=level,
                    # Let the lamp fade across the gap between accepted frames
                    # instead of stepping from colour to colour.
                    transition_ms=throttle_ms,
                    # a stale frame is worth nothing once the connection is back
                    wait_for_connection=False,
                ),
            )
            return

        if wiz_host:
            frame_key = tuple(round(c * 255) for c in (*direct_rgb, brightness))
            if self._is_repeat(light_id, frame_key, time.monotonic()):
                return
            self._track_delivery(
                light_id,
                frame_key,
                await wiz_direct.async_send_light_state(
                    host=wiz_host,
                    port=light_conf.get("wiz_port") or wiz_direct.DEFAULT_PORT,
                    rgb=direct_rgb,
                    brightness=brightness,
                ),
            )
            return

        # No direct target configured for this light: the normal Home Assistant path.
        entity_id = light_conf["entity_id"]
        device = await async_get_device(self.ctl, entity_id)
        call = device.new_control_state()
        call.set_power_state(True)
        if color_space == COLOR_TYPE_RGB:
            call.set_rgb(*(int(c * 255) for c in rgb))
            call.set_brightness(int(brightness * 255))
        else:
            call.set_xy(x, y)
            call.set_brightness(int(brightness * 255))
        call.set_transition_ms(0, respect_throttle=True)
        await call.async_execute()
