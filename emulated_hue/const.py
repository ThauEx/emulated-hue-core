"""Emulated HUE Bridge for HomeAssistant - constants."""

# Prevent overloading home assistant / implementation
# Will not be respected when using udp
DEFAULT_THROTTLE_MS = 150
BRIGHTNESS_THROTTLE_THRESHOLD = 255 / 4
ENTERTAINMENT_UPDATE_STATE_UPDATE_RATE = 1000

CONFIG_WRITE_DELAY_SECONDS = 10

DEFAULT_TRANSITION_SECONDS = 0.4

HASS_ATTR_BRIGHTNESS = "brightness"
HASS_ATTR_COLOR_TEMP = "color_temp"
# Home Assistant only reports and accepts kelvin for the colour temperature now,
# the Hue API uses mireds.
HASS_ATTR_COLOR_TEMP_KELVIN = "color_temp_kelvin"
HASS_ATTR_MIN_COLOR_TEMP_KELVIN = "min_color_temp_kelvin"
HASS_ATTR_MAX_COLOR_TEMP_KELVIN = "max_color_temp_kelvin"
HASS_ATTR_XY_COLOR = "xy_color"
HASS_ATTR_HS_COLOR = "hs_color"
HASS_ATTR_RGB_COLOR = "rgb_color"
HASS_ATTR_EFFECT = "effect"
HASS_ATTR_TRANSITION = "transition"
HASS_ATTR_FLASH = "flash"

# Deprecated Bitfield features
HASS_SUPPORT_BRIGHTNESS = 1
HASS_SUPPORT_COLOR_TEMP = 2
HASS_SUPPORT_EFFECT = 4  # unused
HASS_SUPPORT_FLASH = 8  # unused
HASS_SUPPORT_COLOR = 16
HASS_SUPPORT_TRANSITION = 32  # unused
HASS_SUPPORT_WHITE_VALUE = 128  # unused

# New color modes
# https://github.com/home-assistant/core/blob/2b3148296c7af2dd381b48bd6c5aa2af5fdfac1b/homeassistant/components/light/__init__.py#L55
HASS_COLOR_MODE = "color_mode"
HASS_COLOR_MODE_UNKNOWN = "unknown"  # Ambiguous color mode
HASS_COLOR_MODE_ONOFF = "onoff"  # Must be the only supported mode
HASS_COLOR_MODE_BRIGHTNESS = "brightness"  # Must be the only supported mode
HASS_COLOR_MODE_COLOR_TEMP = "color_temp"
HASS_COLOR_MODE_HS = "hs"
HASS_COLOR_MODE_XY = "xy"
HASS_COLOR_MODE_RGB = "rgb"
HASS_COLOR_MODE_RGBW = "rgbw"
HASS_COLOR_MODE_RGBWW = "rgbww"
HASS_COLOR_MODE_WHITE = "white"  # Must *NOT* be the only supported mode

HASS_ATTR = "attributes"
HASS_ATTR_ENTITY_ID = "entity_id"
HASS_ATTR_SUPPORTED_FEATURES = "supported_features"
HASS_ATTR_SUPPORTED_COLOR_MODES = "supported_color_modes"
HASS_SERVICE_TURN_OFF = "turn_off"
HASS_SERVICE_TURN_ON = "turn_on"
HASS_STATE_OFF = "off"
HASS_STATE_ON = "on"
HASS_STATE_UNAVAILABLE = "unavailable"
HASS_DOMAIN_LIGHT = "light"

HASS_ATTR_BRI_MIN = 1  # Brightness

# Hue API states
HUE_ATTR_ON = "on"
HUE_ATTR_BRI = "bri"
HUE_ATTR_COLORMODE = "colormode"
HUE_ATTR_HUE = "hue"
HUE_ATTR_SAT = "sat"
HUE_ATTR_CT = "ct"
HUE_ATTR_HS = "hs"
HUE_ATTR_XY = "xy"
HUE_ATTR_EFFECT = "effect"
HUE_ATTR_TRANSITION = "transitiontime"
HUE_ATTR_ALERT = "alert"

# Hue API min/max values - https://developers.meethue.com/develop/hue-api/lights-api/
HUE_ATTR_BRI_MIN = 1  # Brightness
HUE_ATTR_BRI_MAX = 254
HUE_ATTR_HUE_MIN = 0  # Hue
HUE_ATTR_HUE_MAX = 65535
HUE_ATTR_SAT_MIN = 0  # Saturation
HUE_ATTR_SAT_MAX = 254
HUE_ATTR_CT_MIN = 153  # Color temp
HUE_ATTR_CT_MAX = 500

HASS = "hass"
HUE = "hue"

HUE_HTTP_PORT = 80
HUE_HTTPS_PORT = 443
# Not user-configurable - must match the "ingress_port" hardcoded in the
# add-on's config.json, since Supervisor's ingress proxy needs to know it
# ahead of time (it's not something we can pass in via options/env).
HUE_INGRESS_PORT = 8880
# Some clients (a Philips TV doing Ambilight+Hue was observed) only list lights
# up to this id, so new lights get the lowest free id and a warning above it.
HUE_CLIENT_MAX_LIGHT_ID = 63
# The panel can change light targets and enable pairing, so it only answers
# Supervisor's ingress proxy (172.30.32.2) and the local machine, never the LAN.
# Override with the HUE_INGRESS_ALLOWED_NETWORKS environment variable (comma
# separated networks) when running outside the Supervisor, e.g. behind your own proxy.
HUE_INGRESS_ALLOWED_NETWORKS = "172.30.32.2/32,127.0.0.0/8,::1/128"
# Must match the "slug" in the add-on's config.json - Home Assistant's
# frontend resolves this stable path to whatever the add-on's actual
# (session-specific) ingress URL currently is.
HUE_INGRESS_PANEL_PATH = "/hassio/ingress/emulated_hue_direct"

# New const
HASS_DOMAIN_HOMEASSISTANT = "homeassistant"
HASS_DOMAIN_PERSISTENT_NOTIFICATION = "persistent_notification"
HASS_SERVICE_PERSISTENT_NOTIFICATION_CREATE = "create"
HASS_SERVICE_PERSISTENT_NOTIFICATION_DISMISS = "dismiss"
