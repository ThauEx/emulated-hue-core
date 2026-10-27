"""
Ingress web UI: direct-path light configuration and pairing.

Runs as its own small aiohttp app on HUE_INGRESS_PORT (see web.py), served to
the user through Home Assistant's sidebar via Supervisor's ingress proxy.
Lets a user assign esphome_host/wiz_host per light and trigger pairing mode,
writing straight into the same emulated_hue.json storage the rest of the app
already reads/writes - no add-on restart needed, unlike the Configuration tab.
"""
import json
import logging

from aiohttp import web

from emulated_hue.const import DEFAULT_THROTTLE_MS
from emulated_hue.controllers import Controller, esphome_direct, wiz_direct
from emulated_hue.controllers.color_utils import COLOR_PROFILES, apply_balance
from emulated_hue.utils import (
    ClassRouteTableDef,
    send_error_response,
    send_json_response,
)

LOGGER = logging.getLogger(__name__)

# pylint: disable=invalid-name
routes = ClassRouteTableDef()
# pylint: enable=invalid-name

DIRECT_PATH_FIELDS = (
    "esphome_host",
    "esphome_port",
    "esphome_password",
    "esphome_object_id",
    "wiz_host",
    "wiz_port",
)


def _parse_balance(raw: dict | None) -> dict:
    """Read a {"r", "g", "b"} percent dict from the browser, blanks meaning 100."""
    return {channel: int((raw or {}).get(channel) or 100) for channel in "rgb"}


def _store_color_balance(light_conf: dict, raw: dict | None) -> None:
    """Save the per-channel balance, dropping it again when it is all 100%."""
    balance = _parse_balance(raw)
    if all(value == 100 for value in balance.values()):
        light_conf.pop("color_balance", None)
    else:
        light_conf["color_balance"] = {
            channel: min(100, max(1, value)) for channel, value in balance.items()
        }


def _store_direct_target(
    light_conf: dict, direct_type: str, host: str, port: int | None, data: dict
) -> None:
    """Write the esphome/wiz host settings of a light into its config."""
    if direct_type == "esphome":
        light_conf["esphome_host"] = host
        if port:
            light_conf["esphome_port"] = port
        if data.get("password"):
            # A blank password field means "keep the existing one" - we
            # never send the real secret back to the browser to edit.
            light_conf["esphome_password"] = data["password"]
        if data.get("object_id"):
            light_conf["esphome_object_id"] = data["object_id"].strip()
    else:
        light_conf.pop("esphome_password", None)
        if direct_type == "wiz":
            light_conf["wiz_host"] = host
            if port:
                light_conf["wiz_port"] = port


class HueIngressEndpoints:
    """Ingress panel endpoints."""

    def __init__(self, ctl: Controller):
        """Initialize the ingress endpoints."""
        self.ctl = ctl

    @property
    def route(self):
        """Return routes for the ingress app."""
        if not len(routes):
            # A manual route must be registered first so len(routes) is
            # already non-zero by the time add_class_routes() introspects
            # this class - inspect.getmembers() would otherwise re-enter
            # this same property while evaluating it, and recurse forever
            # (see apiv2.py for the same fix applied to the same pitfall).
            routes.add_manual_route("GET", "/", self.async_index)
            routes.add_class_routes(self)
        return routes

    async def async_stop(self):
        """Stop the ingress endpoints (nothing to clean up)."""

    def _friendly_name(self, entity_id: str) -> str | None:
        """Return the Home Assistant friendly name of an entity, if it has one."""
        state = self.ctl.controller_hass.get_entity_state(entity_id) or {}
        return state.get("attributes", {}).get("friendly_name")

    async def async_index(self, request: web.Request) -> web.Response:
        """Serve the ingress single-page UI."""
        return web.Response(text=INDEX_HTML, content_type="text/html")

    @routes.get("/api/lights")
    async def async_get_lights(self, request: web.Request) -> web.Response:
        """Return every configured light with its current direct-path settings."""
        # A light only gets a storage entry once something asks for it, which
        # normally is a Hue client listing the lights - so a lamp that was just
        # added to Home Assistant would be missing here. Create the entries up
        # front, exactly as that client listing would.
        for entity_id in self.ctl.controller_hass.get_entities():
            await self.ctl.config_instance.async_entity_id_to_light_id(entity_id)
        lights = await self.ctl.config_instance.async_get_storage_value(
            "lights", default={}
        )
        result = []
        for light_id, conf in lights.items():
            if not conf.get("enabled", True):
                continue
            direct_type = ""
            if conf.get("wiz_host"):
                direct_type = "wiz"
            elif conf.get("esphome_host"):
                direct_type = "esphome"
            result.append(
                {
                    "light_id": light_id,
                    "entity_id": conf.get("entity_id", ""),
                    "name": conf.get("name")
                    or self._friendly_name(conf.get("entity_id", ""))
                    or conf.get("entity_id", ""),
                    "type": direct_type,
                    "host": conf.get("wiz_host") or conf.get("esphome_host", ""),
                    "port": conf.get("wiz_port") or conf.get("esphome_port", ""),
                    "object_id": conf.get("esphome_object_id", ""),
                    "esphome_password_set": bool(conf.get("esphome_password")),
                    "max_transition_ms": conf.get("max_transition_ms", ""),
                    "throttle": conf.get("throttle", DEFAULT_THROTTLE_MS),
                    "color_profile": conf.get("color_profile", ""),
                    "color_balance": {
                        channel: conf.get("color_balance", {}).get(channel, 100)
                        for channel in "rgb"
                    },
                }
            )
        result.sort(key=lambda item: int(item["light_id"]))
        return send_json_response(result)

    @routes.post("/api/lights/{light_id}")
    async def async_set_light(self, request: web.Request) -> web.Response:
        """Set (or clear) a single light's direct-path target."""
        light_id = request.match_info["light_id"]
        try:
            data = json.loads(await request.text())
        except ValueError:
            return send_error_response(request.path, "invalid json", 2)

        try:
            light_conf = await self.ctl.config_instance.async_get_light_config(light_id)
        except Exception:  # noqa: BLE001 - unknown light_id, report cleanly
            return send_error_response(request.path, "unknown light_id", 3)

        direct_type = data.get("type") or ""
        if direct_type not in ("", "esphome", "wiz"):
            return send_error_response(request.path, "invalid type", 4)
        color_profile = data.get("color_profile") or ""
        if color_profile and color_profile not in COLOR_PROFILES:
            return send_error_response(request.path, "invalid color_profile", 6)
        if color_profile:
            light_conf["color_profile"] = color_profile
        else:
            light_conf.pop("color_profile", None)

        try:
            _store_color_balance(light_conf, data.get("color_balance"))
        except (TypeError, ValueError):
            return send_error_response(
                request.path, "color_balance values must be numbers", 7
            )

        for field in DIRECT_PATH_FIELDS:
            if field != "esphome_password":
                light_conf.pop(field, None)

        host = (data.get("host") or "").strip()
        port = data.get("port") or None
        max_transition = data.get("max_transition_ms")
        throttle = data.get("throttle")
        try:
            port = int(port) if port else None
            max_transition = (
                int(max_transition) if max_transition not in (None, "") else None
            )
            throttle = (
                int(throttle) if throttle not in (None, "") else DEFAULT_THROTTLE_MS
            )
        except (TypeError, ValueError):
            return send_error_response(
                request.path, "port/throttle/max_transition_ms must be numbers", 5
            )
        if max_transition is None:
            light_conf.pop("max_transition_ms", None)
        else:
            light_conf["max_transition_ms"] = max(0, max_transition)
        light_conf["throttle"] = max(0, throttle)

        _store_direct_target(light_conf, direct_type, host, port, data)

        await self.ctl.config_instance.async_set_storage_value(
            "lights", light_id, light_conf
        )
        return send_json_response({"success": True})

    @routes.post("/api/lights/{light_id}/test")
    async def async_test_light(self, request: web.Request) -> web.Response:
        """Show a test colour on a light (calibration), or switch it off."""
        try:
            data = json.loads(await request.text())
            light_conf = await self.ctl.config_instance.async_get_light_config(
                request.match_info["light_id"]
            )
            rgb = data.get("rgb")
            if rgb is not None:
                rgb = tuple(min(255, max(0, int(c))) / 255 for c in rgb)
                rgb = apply_balance(
                    rgb, {"color_balance": _parse_balance(data.get("color_balance"))}
                )
        except Exception:  # noqa: BLE001 - bad json/values/unknown light
            return send_error_response(request.path, "invalid request", 2)

        if light_conf.get("esphome_host"):
            sent = await esphome_direct.async_send_light_state(
                host=light_conf["esphome_host"],
                port=light_conf.get("esphome_port") or esphome_direct.DEFAULT_PORT,
                credential=light_conf.get("esphome_password", ""),
                object_id=light_conf.get("esphome_object_id"),
                power=rgb is not None,
                rgb=rgb,
                brightness=1.0,
            )
        elif light_conf.get("wiz_host"):
            sent = await wiz_direct.async_send_light_state(
                host=light_conf["wiz_host"],
                port=light_conf.get("wiz_port") or wiz_direct.DEFAULT_PORT,
                power=rgb is not None,
                rgb=rgb,
                brightness=1.0,
            )
        else:
            return send_error_response(request.path, "light has no direct path", 8)
        return send_json_response({"success": bool(sent)})

    @routes.get("/api/pairing")
    async def async_get_pairing(self, request: web.Request) -> web.Response:
        """Return current pairing (link mode) status."""
        return send_json_response(
            {
                "enabled": self.ctl.config_instance.link_mode_enabled,
                "seconds_remaining": self.ctl.config_instance.link_mode_seconds_remaining,
            }
        )

    @routes.post("/api/pairing")
    async def async_post_pairing(self, request: web.Request) -> web.Response:
        """Enable pairing (link) mode for 5 minutes."""
        await self.ctl.config_instance.async_enable_link_mode()
        return send_json_response(
            {
                "enabled": True,
                "seconds_remaining": self.ctl.config_instance.link_mode_seconds_remaining,
            }
        )


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Emulated Hue</title>
<style>
  :root {
    --bg: #0b0b12;
    --card: #17141f;
    --card-border: #2a2438;
    --accent1: #ff5da2;
    --accent2: #7c4dff;
    --text: #f2f1f7;
    --text-dim: #a7a3b8;
    --ok: #35d68a;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 16px;
  }
  h1 {
    font-size: 20px;
    font-weight: 600;
    margin: 0 0 4px;
    background: linear-gradient(90deg, var(--accent1), var(--accent2));
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    display: inline-block;
  }
  .subtitle { color: var(--text-dim); font-size: 13px; margin: 0 0 20px; }
  .panel {
    background: var(--card);
    border: 1px solid var(--card-border);
    border-radius: 14px;
    padding: 16px;
    margin-bottom: 16px;
  }
  .panel h2 {
    font-size: 14px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-dim);
    margin: 0 0 12px;
  }
  .pairing-row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  button {
    background: linear-gradient(90deg, var(--accent1), var(--accent2));
    border: none;
    color: white;
    font-weight: 600;
    font-size: 14px;
    padding: 10px 18px;
    border-radius: 999px;
    cursor: pointer;
  }
  button:disabled { opacity: 0.5; cursor: default; }
  button.secondary {
    background: transparent;
    border: 1px solid var(--card-border);
    color: var(--text);
  }
  .status-pill {
    font-size: 12px;
    padding: 4px 10px;
    border-radius: 999px;
    background: rgba(53, 214, 138, 0.15);
    color: var(--ok);
  }
  .light-card {
    display: grid;
    grid-template-columns: 1fr;
    gap: 10px;
    background: #1d1929;
    border: 1px solid var(--card-border);
    border-radius: 12px;
    padding: 14px;
    margin-bottom: 12px;
  }
  .light-title { display: flex; align-items: center; gap: 10px; }
  .bulb-dot {
    width: 12px; height: 12px; border-radius: 50%;
    background: var(--card-border);
    box-shadow: 0 0 0 0 transparent;
  }
  .bulb-dot.active {
    background: var(--accent1);
    box-shadow: 0 0 10px 2px rgba(255, 93, 162, 0.6);
  }
  .light-name { font-weight: 600; }
  .light-entity { color: var(--text-dim); font-size: 12px; }
  .balance { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4px; }
  .wand { display: flex; gap: 6px; }
  .balance-field { grid-column: span 2; }
  details.tuning { margin-top: 2px; }
  details.tuning summary { cursor: pointer; font-size: 12px; color: var(--text-dim); margin-bottom: 8px; }
  .tuning-summary { opacity: 0.7; margin-left: 6px; }
  .wand button { padding: 4px 10px; font-size: 16px; }
  #calib { display: none; position: fixed; inset: 0; z-index: 10; }
  #calib.open { display: block; }
  #calib-sheet {
    position: absolute; left: 0; right: 0; bottom: 0;
    background: rgba(16, 14, 23, 0.92);
    padding: 14px 16px 18px;
    border-radius: 16px 16px 0 0;
  }
  #calib-swatches { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
  .swatch { width: 34px; height: 34px; border-radius: 50%; border: 2px solid #fff6; padding: 0; }
  .swatch.on { border-color: #fff; }
  .calib-slider { display: grid; grid-template-columns: 18px 1fr 38px; gap: 8px; align-items: center; margin: 6px 0; font-size: 13px; }
  .calib-slider input { padding: 0; }
  #calib-hint { font-size: 12px; color: var(--text-dim); margin: 0 0 8px; }
  .fields { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; }
  label { font-size: 11px; color: var(--text-dim); display: block; margin-bottom: 3px; }
  select, input {
    width: 100%;
    background: #100e17;
    border: 1px solid var(--card-border);
    color: var(--text);
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 13px;
  }
  .row-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 2px; }
  .save-msg { font-size: 12px; color: var(--ok); opacity: 0; transition: opacity 0.3s; }
  .save-msg.show { opacity: 1; }
  .empty { color: var(--text-dim); font-size: 13px; }
</style>
</head>
<body>
  <h1>Emulated Hue</h1>
  <p class="subtitle">Direct-path light configuration &amp; pairing</p>

  <div class="panel">
    <h2>Pairing</h2>
    <div class="pairing-row">
      <button id="pair-btn">Enable pairing mode</button>
      <span id="pair-status" class="status-pill" style="display:none"></span>
    </div>
  </div>

  <div class="panel">
    <h2>Lights</h2>
    <div id="lights-container"><p class="empty">Loading&hellip;</p></div>
  </div>

<div id="calib">
  <div id="calib-sheet">
    <p id="calib-hint">Hold the phone next to the lamp and drag the sliders until both colours look the same. Ambilight+Hue must be off on the TV.</p>
    <div id="calib-swatches"></div>
    <div class="calib-slider"><span>R</span><input type="range" min="1" max="100" data-ch="r"><span></span></div>
    <div class="calib-slider"><span>G</span><input type="range" min="1" max="100" data-ch="g"><span></span></div>
    <div class="calib-slider"><span>B</span><input type="range" min="1" max="100" data-ch="b"><span></span></div>
    <div class="row-actions">
      <button class="secondary" id="calib-cancel">Cancel</button>
      <button id="calib-apply">Use these values</button>
    </div>
  </div>
</div>

<script>
const api = (path, opts) => fetch(path, opts).then(r => r.json());

function fieldsHtml(light) {
  const showEsphome = light.type === 'esphome';
  const showWiz = light.type === 'wiz';
  const balance = light.color_balance;
  const summary = [
    light.color_profile === 'srgb' ? 'sRGB' : 'Wide',
    `balance ${balance.r}/${balance.g}/${balance.b}`,
    `${light.throttle} ms`,
  ].join(' \u00b7 ');
  return `
    <div class="fields">
      <div>
        <label>Type</label>
        <select data-field="type">
          <option value="" ${light.type === '' ? 'selected' : ''}>None (Home Assistant)</option>
          <option value="esphome" ${showEsphome ? 'selected' : ''}>ESPHome</option>
          <option value="wiz" ${showWiz ? 'selected' : ''}>WiZ</option>
        </select>
      </div>
      <div>
        <label>Host / IP</label>
        <input data-field="host" value="${light.host || ''}" placeholder="192.168.1.50">
      </div>
      <div>
        <label>Port</label>
        <input data-field="port" value="${light.port || ''}" placeholder="(default)">
      </div>
      <div class="esphome-only" style="${showEsphome ? '' : 'display:none'}">
        <label>Object ID</label>
        <input data-field="object_id" value="${light.object_id || ''}" placeholder="optional">
      </div>
      <div class="esphome-only" style="${showEsphome ? '' : 'display:none'}">
        <label>Password / Noise PSK</label>
        <input data-field="password" type="password" placeholder="${light.esphome_password_set ? '(unchanged)' : ''}">
      </div>
    </div>
    <details class="tuning">
      <summary>Tuning <span class="tuning-summary">${summary}</span></summary>
      <div class="fields">
        <div>
          <label>Color profile</label>
          <select data-field="color_profile">
            <option value="" ${light.color_profile === '' ? 'selected' : ''}>Wide (Hue, default)</option>
            <option value="srgb" ${light.color_profile === 'srgb' ? 'selected' : ''}>sRGB</option>
          </select>
        </div>
        <div class="balance-field">
          <label>Color balance R / G / B (%)</label>
          <div class="wand">
            <div class="balance">
              <input data-field="balance_r" inputmode="numeric" value="${balance.r}" placeholder="100">
              <input data-field="balance_g" inputmode="numeric" value="${balance.g}" placeholder="100">
              <input data-field="balance_b" inputmode="numeric" value="${balance.b}" placeholder="100">
            </div>
            <button class="secondary" data-action="calibrate" title="Calibrate with test colours">&#129668;</button>
          </div>
        </div>
        <div>
          <label>Throttle (ms)</label>
          <input data-field="throttle" inputmode="numeric" value="${light.throttle}" placeholder="150">
        </div>
        <div>
          <label>Max. transition (ms)</label>
          <input data-field="max_transition_ms" inputmode="numeric" value="${light.max_transition_ms === '' ? '' : light.max_transition_ms}" placeholder="no limit">
        </div>
      </div>
    </details>`;
}

function renderLights(lights) {
  const container = document.getElementById('lights-container');
  if (!lights.length) {
    container.innerHTML = '<p class="empty">No lights found yet - they appear here once Home Assistant lights have been discovered.</p>';
    return;
  }
  container.innerHTML = '';
  for (const light of lights) {
    const card = document.createElement('div');
    card.className = 'light-card';
    card.dataset.lightId = light.light_id;
    card.innerHTML = `
      <div class="light-title">
        <span class="bulb-dot ${light.type ? 'active' : ''}"></span>
        <div>
          <div class="light-name">${light.name || light.entity_id}</div>
          <div class="light-entity">${light.entity_id} &middot; id ${light.light_id}</div>
        </div>
      </div>
      ${fieldsHtml(light)}
      <div class="row-actions">
        <span class="save-msg">Saved</span>
        <button class="secondary" data-action="save">Save</button>
      </div>
    `;
    const typeSelect = card.querySelector('[data-field="type"]');
    typeSelect.addEventListener('change', () => {
      const isEsphome = typeSelect.value === 'esphome';
      card.querySelectorAll('.esphome-only').forEach(el => {
        el.style.display = isEsphome ? '' : 'none';
      });
    });
    card.querySelector('[data-action="save"]').addEventListener('click', () => saveLight(card));
    card.querySelector('[data-action="calibrate"]').addEventListener('click', () => openCalibration(card));
    container.appendChild(card);
  }
}

async function saveLight(card) {
  const lightId = card.dataset.lightId;
  const get = (field) => card.querySelector(`[data-field="${field}"]`)?.value || '';
  const payload = {
    type: get('type'),
    host: get('host'),
    port: get('port'),
    object_id: get('object_id'),
    password: get('password'),
    max_transition_ms: get('max_transition_ms'),
    throttle: get('throttle'),
    color_profile: get('color_profile'),
    color_balance: {r: get('balance_r'), g: get('balance_g'), b: get('balance_b')},
  };
  await api(`api/lights/${lightId}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });
  const msg = card.querySelector('.save-msg');
  msg.classList.add('show');
  setTimeout(() => msg.classList.remove('show'), 1500);
  const dot = card.querySelector('.bulb-dot');
  dot.classList.toggle('active', !!payload.type);
}

const CALIB_COLORS = ['#ff6a00', '#ffc400', '#ffa000', '#ffd9a0', '#ff0000', '#00c800', '#0000ff', '#00d0d0', '#d000d0'];
const calib = {card: null, rgb: null, timer: null};

const hexToRgb = (hex) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16));
const calibBalance = () => Object.fromEntries(
  [...document.querySelectorAll('#calib [data-ch]')].map(el => [el.dataset.ch, el.value]));

function calibSend(rgb) {
  api(`api/lights/${calib.card.dataset.lightId}/test`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({rgb, color_balance: calibBalance()}),
  });
}

function calibShow(hex) {
  calib.rgb = hexToRgb(hex);
  document.getElementById('calib').style.background = hex;
  document.querySelectorAll('.swatch').forEach(el => el.classList.toggle('on', el.dataset.hex === hex));
  calibSend(calib.rgb);
}

async function openCalibration(card) {
  // The test colour goes to the saved host, so store the card first.
  await saveLight(card);
  calib.card = card;
  for (const ch of 'rgb') {
    const value = card.querySelector(`[data-field="balance_${ch}"]`).value || 100;
    const slider = document.querySelector(`#calib [data-ch="${ch}"]`);
    slider.value = value;
    slider.nextElementSibling.textContent = value;
  }
  const swatches = document.getElementById('calib-swatches');
  if (!swatches.children.length) {
    for (const hex of CALIB_COLORS) {
      const btn = document.createElement('button');
      btn.className = 'swatch';
      btn.dataset.hex = hex;
      btn.style.background = hex;
      btn.addEventListener('click', () => calibShow(hex));
      swatches.appendChild(btn);
    }
  }
  document.getElementById('calib').classList.add('open');
  calibShow(CALIB_COLORS[0]);
}

function closeCalibration(apply) {
  document.getElementById('calib').classList.remove('open');
  if (apply) {
    const balance = calibBalance();
    for (const ch of 'rgb') {
      calib.card.querySelector(`[data-field="balance_${ch}"]`).value = balance[ch];
    }
    saveLight(calib.card);
  }
  api(`api/lights/${calib.card.dataset.lightId}/test`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({}),
  });
}

document.querySelectorAll('#calib [data-ch]').forEach(slider => {
  slider.addEventListener('input', () => {
    slider.nextElementSibling.textContent = slider.value;
    clearTimeout(calib.timer);
    calib.timer = setTimeout(() => calibSend(calib.rgb), 80);
  });
});
document.getElementById('calib-cancel').addEventListener('click', () => closeCalibration(false));
document.getElementById('calib-apply').addEventListener('click', () => closeCalibration(true));

async function loadLights() {
  const lights = await api('api/lights');
  renderLights(lights);
}

let pairingTimer = null;
function renderPairing(status) {
  const btn = document.getElementById('pair-btn');
  const pill = document.getElementById('pair-status');
  clearInterval(pairingTimer);
  if (status.enabled && status.seconds_remaining > 0) {
    btn.disabled = true;
    pill.style.display = '';
    let remaining = status.seconds_remaining;
    const tick = () => {
      pill.textContent = `Pairing active - ${remaining}s`;
      remaining -= 1;
      if (remaining < 0) {
        btn.disabled = false;
        pill.style.display = 'none';
        clearInterval(pairingTimer);
      }
    };
    tick();
    pairingTimer = setInterval(tick, 1000);
  } else {
    btn.disabled = false;
    pill.style.display = 'none';
  }
}

document.getElementById('pair-btn').addEventListener('click', async () => {
  const status = await api('api/pairing', {method: 'POST'});
  renderPairing(status);
});

api('api/pairing').then(renderPairing);
loadLights();
</script>
</body>
</html>
"""
