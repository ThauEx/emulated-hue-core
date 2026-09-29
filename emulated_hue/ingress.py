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

from emulated_hue.controllers import Controller
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

    async def async_index(self, request: web.Request) -> web.Response:
        """Serve the ingress single-page UI."""
        return web.Response(text=INDEX_HTML, content_type="text/html")

    @routes.get("/api/lights")
    async def async_get_lights(self, request: web.Request) -> web.Response:
        """Return every configured light with its current direct-path settings."""
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
                    "name": conf.get("name") or conf.get("entity_id", ""),
                    "type": direct_type,
                    "host": conf.get("wiz_host") or conf.get("esphome_host", ""),
                    "port": conf.get("wiz_port") or conf.get("esphome_port", ""),
                    "object_id": conf.get("esphome_object_id", ""),
                    "esphome_password_set": bool(conf.get("esphome_password")),
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

        for field in DIRECT_PATH_FIELDS:
            if field != "esphome_password":
                light_conf.pop(field, None)

        host = (data.get("host") or "").strip()
        port = data.get("port") or None
        if direct_type == "esphome":
            light_conf["esphome_host"] = host
            if port:
                light_conf["esphome_port"] = int(port)
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
                    light_conf["wiz_port"] = int(port)

        await self.ctl.config_instance.async_set_storage_value(
            "lights", light_id, light_conf
        )
        return send_json_response({"success": True})

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

<script>
const api = (path, opts) => fetch(path, opts).then(r => r.json());

function fieldsHtml(light) {
  const showEsphome = light.type === 'esphome';
  const showWiz = light.type === 'wiz';
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
    </div>`;
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
