"""
Support for the Hue CLIP v2 API - Entertainment resources only.

Modern Hue clients (including current-generation TV firmware) set up
Entertainment (DTLS/UDP) streaming through this API rather than the legacy
v1 "type: Entertainment" group mechanism (see README/DOCS for why this
exists). This is a thin routing/JSON-shape layer over the *same* v1 storage
and Entertainment engine (controllers/entertainment.py,
controllers/config.py's start_entertainment/stop_entertainment) - no
parallel data model, no new persisted state. v2 ids are derived
deterministically from existing v1 ids (uuid5), so there's nothing to keep
in sync.

Deliberately out of scope: /eventstream/clip/v2 (SSE) - confirmed against a
reference implementation that Entertainment start/stop has no dependency on
it; it's only used by clients for general UI push updates. Also out of
scope: rooms/zones/scenes/buttons/sensors v2 resources - not needed for
Entertainment.
"""
import functools
import logging
import uuid

from aiohttp import web

from emulated_hue.controllers import Controller
from emulated_hue.utils import ClassRouteTableDef, send_json_response

LOGGER = logging.getLogger(__name__)

_NAMESPACE = uuid.NAMESPACE_URL


def _v2_id(*parts: str) -> str:
    """Deterministic v2 UUID for a v1 object - no new persisted state needed."""
    return str(uuid.uuid5(_NAMESPACE, "-".join(parts)))


def _envelope(data) -> dict:
    """Wrap a resource or list of resources in CLIP v2's {errors, data} shape."""
    return {"errors": [], "data": data if isinstance(data, list) else [data]}


def _error(description: str) -> dict:
    return {"errors": [{"description": description}], "data": []}


def check_request_v2(func):
    """Validate the hue-application-key header (the same token v1 calls "username")."""

    @functools.wraps(func)
    async def wrapped_func(cls: "HueApiV2Endpoints", request: web.Request):
        token = request.headers.get("hue-application-key", "")
        if not token or not await cls.ctl.config_instance.async_get_user(token):
            LOGGER.debug("[%s] Invalid hue-application-key", request.remote)
            return send_json_response(_error("unauthorized user"))
        if request.method in ("PUT", "POST"):
            try:
                request_data = await request.json()
            except ValueError:
                return send_json_response(_error("body contains invalid json"))
            return await func(cls, request, request_data, token)
        return await func(cls, request, token)

    return wrapped_func


# pylint: disable=invalid-name
routes = ClassRouteTableDef()
# pylint: enable=invalid-name


class HueApiV2Endpoints:
    """Hue CLIP v2 endpoints (Entertainment-focused subset)."""

    def __init__(self, ctl: Controller):
        """Initialize the v2 api."""
        self.ctl = ctl

    @property
    def route(self):
        """Return routes for external access."""
        if not len(routes):
            # Register one manual route before add_class_routes(): its
            # getmembers() scan touches this very property again while
            # building its member list (before its own loop body runs), and
            # without at least one route already registered the recursive
            # `if not len(routes):` check never sees a change - see apiv1.py
            # which uses the same defensive ordering.
            routes.add_manual_route("GET", "/clip/v2", self.async_unknown_request)
            routes.add_class_routes(self)
        return routes

    async def async_stop(self):
        """Stop the v2 api."""

    async def async_unknown_request(self, request: web.Request):
        """Handle requests to unimplemented v2 paths."""
        return send_json_response(_error("resource not available"))

    # -- resource builders (read-only projections of existing v1 storage) --

    def _bridge_device_id(self) -> str:
        return _v2_id("device", "bridge")

    async def _async_bridge_resource(self) -> dict:
        cfg = self.ctl.config_instance
        return {
            "id": _v2_id("bridge", cfg.bridge_id),
            "id_v1": "/config",
            "type": "bridge",
            "owner": {"rid": self._bridge_device_id(), "rtype": "device"},
            "bridge_id": cfg.bridge_id.lower(),
            "time_zone": {"time_zone": "UTC"},
        }

    async def _async_bridge_device_resource(self) -> dict:
        cfg = self.ctl.config_instance
        return {
            "id": self._bridge_device_id(),
            "id_v1": "/config",
            "type": "device",
            "product_data": {
                "model_id": "BSB002",
                "manufacturer_name": "Signify Netherlands B.V.",
                "product_name": "Hue Bridge",
                "product_archetype": "bridge_v2",
                "certified": True,
                "software_version": cfg.definitions.get("bridge", {}).get(
                    "swversion", ""
                ),
            },
            "metadata": {"name": cfg.bridge_name, "archetype": "bridge_v2"},
            "services": [{"rid": _v2_id("bridge", cfg.bridge_id), "rtype": "bridge"}],
        }

    async def _async_light_ids(self) -> dict:
        return await self.ctl.config_instance.async_get_storage_value(
            "lights", default={}
        )

    async def _async_groups(self) -> dict:
        return await self.ctl.config_instance.async_get_storage_value(
            "groups", default={}
        )

    async def _async_light_resource(self, light_id: str, light_conf: dict) -> dict:
        state = light_conf.get("state") or {}
        return {
            "id": _v2_id("light", light_id),
            "id_v1": f"/lights/{light_id}",
            "type": "light",
            "owner": {"rid": _v2_id("device", light_id), "rtype": "device"},
            "metadata": {
                "name": light_conf.get("name") or "",
                "archetype": "sultan_bulb",
            },
            "on": {"on": bool(state.get("power_state", True))},
        }

    async def _async_light_device_resource(
        self, light_id: str, light_conf: dict
    ) -> dict:
        archetype = (light_conf.get("config") or {}).get("archetype", "sultanbulb")
        return {
            "id": _v2_id("device", light_id),
            "id_v1": f"/lights/{light_id}",
            "type": "device",
            "product_data": {
                "model_id": archetype,
                "manufacturer_name": "Signify Netherlands B.V.",
                "product_name": "Hue light",
                "certified": True,
            },
            "metadata": {
                "name": light_conf.get("name") or "",
                "archetype": "sultan_bulb",
            },
            "services": [
                {"rid": _v2_id("light", light_id), "rtype": "light"},
                {
                    "rid": _v2_id("entertainment-light", light_id),
                    "rtype": "entertainment",
                },
            ],
        }

    async def _async_entertainment_light_resource(self, light_id: str) -> dict:
        return {
            "id": _v2_id("entertainment-light", light_id),
            "id_v1": f"/lights/{light_id}",
            "type": "entertainment",
            "equalizer": True,
            "proxy": False,
            "renderer": True,
            "renderer_reference": {"rid": _v2_id("light", light_id), "rtype": "light"},
            "owner": {"rid": _v2_id("device", light_id), "rtype": "device"},
            "segments": {"configurable": False},
        }

    async def _async_entertainment_bridge_resource(self) -> dict:
        return {
            "id": _v2_id("entertainment", "bridge"),
            "id_v1": "/config",
            "type": "entertainment",
            "equalizer": False,
            "proxy": True,
            "renderer": False,
            "max_streams": 1,
            "owner": {"rid": self._bridge_device_id(), "rtype": "device"},
        }

    async def _async_entertainment_configuration_resource(
        self, group_id: str, group_conf: dict
    ) -> dict:
        stream = group_conf.get("stream") or {}
        light_ids = group_conf.get("lights", [])
        active = bool(stream.get("active"))
        channels = [
            {
                "channel_id": index,
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "members": [
                    {
                        "service": {
                            "rid": _v2_id("entertainment-light", light_id),
                            "rtype": "entertainment",
                        },
                        "index": 0,
                    }
                ],
            }
            for index, light_id in enumerate(light_ids)
        ]
        result = {
            "id": _v2_id("entertainment_configuration", group_id),
            "id_v1": f"/groups/{group_id}",
            "type": "entertainment_configuration",
            "metadata": {"name": group_conf.get("name") or ""},
            "configuration_type": "screen",
            "status": "active" if active else "inactive",
            "stream_proxy": {
                "mode": "auto",
                "node": {"rid": self._bridge_device_id(), "rtype": "device"},
            },
            "channels": channels,
            "locations": {"service_locations": []},
            "light_services": [
                {"rid": _v2_id("light", light_id), "rtype": "light"}
                for light_id in light_ids
            ],
        }
        if active and stream.get("owner"):
            result["active_streamer"] = {"rid": stream["owner"], "rtype": "auth_v1"}
        return result

    async def _async_light_id_from_entertainment_rid(self, rid: str) -> str | None:
        for light_id in await self._async_light_ids():
            if _v2_id("entertainment-light", light_id) == rid:
                return light_id
        return None

    async def _async_find_group_by_v2_id(self, v2_resource_id: str):
        for group_id, group_conf in (await self._async_groups()).items():
            if _v2_id("entertainment_configuration", group_id) == v2_resource_id:
                return group_id, group_conf
        return None, None

    # -- routes --

    @routes.get("/clip/v2/resource")
    @check_request_v2
    async def async_get_all_resources(self, request: web.Request, token: str):
        """Return every resource this API exposes (minimal Entertainment-only subset)."""
        data = [
            await self._async_bridge_resource(),
            await self._async_bridge_device_resource(),
            await self._async_entertainment_bridge_resource(),
        ]
        lights = await self._async_light_ids()
        for light_id, light_conf in lights.items():
            data.append(await self._async_light_resource(light_id, light_conf))
            data.append(await self._async_light_device_resource(light_id, light_conf))
            data.append(await self._async_entertainment_light_resource(light_id))
        for group_id, group_conf in (await self._async_groups()).items():
            if "stream" in group_conf:
                data.append(
                    await self._async_entertainment_configuration_resource(
                        group_id, group_conf
                    )
                )
        return send_json_response(_envelope(data))

    @routes.get("/clip/v2/resource/bridge")
    @check_request_v2
    async def async_get_bridges(self, request: web.Request, token: str):
        return send_json_response(_envelope(await self._async_bridge_resource()))

    @routes.get("/clip/v2/resource/bridge/{id}")
    @check_request_v2
    async def async_get_bridge(self, request: web.Request, token: str):
        return send_json_response(_envelope(await self._async_bridge_resource()))

    @routes.get("/clip/v2/resource/device")
    @check_request_v2
    async def async_get_devices(self, request: web.Request, token: str):
        data = [await self._async_bridge_device_resource()]
        for light_id, light_conf in (await self._async_light_ids()).items():
            data.append(await self._async_light_device_resource(light_id, light_conf))
        return send_json_response(_envelope(data))

    @routes.get("/clip/v2/resource/light")
    @check_request_v2
    async def async_get_lights(self, request: web.Request, token: str):
        data = [
            await self._async_light_resource(light_id, light_conf)
            for light_id, light_conf in (await self._async_light_ids()).items()
        ]
        return send_json_response(_envelope(data))

    @routes.get("/clip/v2/resource/light/{id}")
    @check_request_v2
    async def async_get_light(self, request: web.Request, token: str):
        resource_id = request.match_info["id"]
        for light_id, light_conf in (await self._async_light_ids()).items():
            if _v2_id("light", light_id) == resource_id:
                return send_json_response(
                    _envelope(await self._async_light_resource(light_id, light_conf))
                )
        return send_json_response(_error("not found"))

    @routes.get("/clip/v2/resource/entertainment")
    @check_request_v2
    async def async_get_entertainment_resources(self, request: web.Request, token: str):
        data = [await self._async_entertainment_bridge_resource()]
        for light_id in await self._async_light_ids():
            data.append(await self._async_entertainment_light_resource(light_id))
        return send_json_response(_envelope(data))

    @routes.get("/clip/v2/resource/entertainment_configuration")
    @check_request_v2
    async def async_get_entertainment_configurations(
        self, request: web.Request, token: str
    ):
        data = [
            await self._async_entertainment_configuration_resource(group_id, group_conf)
            for group_id, group_conf in (await self._async_groups()).items()
            if "stream" in group_conf
        ]
        return send_json_response(_envelope(data))

    @routes.get("/clip/v2/resource/entertainment_configuration/{id}")
    @check_request_v2
    async def async_get_entertainment_configuration(
        self, request: web.Request, token: str
    ):
        group_id, group_conf = await self._async_find_group_by_v2_id(
            request.match_info["id"]
        )
        if group_conf is None:
            return send_json_response(_error("not found"))
        return send_json_response(
            _envelope(
                await self._async_entertainment_configuration_resource(
                    group_id, group_conf
                )
            )
        )

    @routes.post("/clip/v2/resource/entertainment_configuration")
    @check_request_v2
    async def async_create_entertainment_configuration(
        self, request: web.Request, request_data: dict, token: str
    ):
        light_ids = []
        for channel in request_data.get("channels", []):
            for member in channel.get("members", []):
                rid = (member.get("service") or {}).get("rid")
                light_id = rid and await self._async_light_id_from_entertainment_rid(
                    rid
                )
                if light_id and light_id not in light_ids:
                    light_ids.append(light_id)

        groups = await self._async_groups()
        next_id = str(max((int(k) for k in groups), default=199) + 1)
        group_conf = {
            "name": (request_data.get("metadata") or {}).get("name", ""),
            "class": "Other",
            "type": "Entertainment",
            "lights": light_ids,
            "sensors": [],
            "action": {"on": False},
            "state": {"any_on": False, "all_on": False},
            "stream": {"active": False, "proxymode": "auto", "proxynode": "/bridge"},
        }
        await self.ctl.config_instance.async_set_storage_value(
            "groups", next_id, group_conf
        )
        resource_id = _v2_id("entertainment_configuration", next_id)
        return send_json_response(
            {
                "errors": [],
                "data": [{"rid": resource_id, "rtype": "entertainment_configuration"}],
            }
        )

    @routes.put("/clip/v2/resource/entertainment_configuration/{id}")
    @check_request_v2
    async def async_update_entertainment_configuration(
        self, request: web.Request, request_data: dict, token: str
    ):
        group_id, group_conf = await self._async_find_group_by_v2_id(
            request.match_info["id"]
        )
        if group_conf is None:
            return send_json_response(_error("not found"))

        if "metadata" in request_data and "name" in request_data["metadata"]:
            group_conf["name"] = request_data["metadata"]["name"]

        action = request_data.get("action")
        if action == "start":
            user_data = await self.ctl.config_instance.async_get_user(token)
            self.ctl.config_instance.start_entertainment(group_conf, user_data)
            group_conf["stream"] = {
                **group_conf.get("stream", {}),
                "active": True,
                "owner": token,
                "proxymode": "auto",
                "proxynode": "/bridge",
            }
        elif action == "stop":
            self.ctl.config_instance.stop_entertainment()
            group_conf["stream"] = {**group_conf.get("stream", {}), "active": False}

        await self.ctl.config_instance.async_set_storage_value(
            "groups", group_id, group_conf
        )
        return send_json_response(
            {
                "errors": [],
                "data": [
                    {
                        "rid": request.match_info["id"],
                        "rtype": "entertainment_configuration",
                    }
                ],
            }
        )

    @routes.delete("/clip/v2/resource/entertainment_configuration/{id}")
    @check_request_v2
    async def async_delete_entertainment_configuration(
        self, request: web.Request, token: str
    ):
        group_id, group_conf = await self._async_find_group_by_v2_id(
            request.match_info["id"]
        )
        if group_conf is None:
            return send_json_response(_error("not found"))
        await self.ctl.config_instance.async_delete_storage_value("groups", group_id)
        return send_json_response(
            {
                "errors": [],
                "data": [
                    {
                        "rid": request.match_info["id"],
                        "rtype": "entertainment_configuration",
                    }
                ],
            }
        )
