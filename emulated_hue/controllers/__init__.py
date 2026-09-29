"""Controllers for emulated_hue."""
import asyncio

from . import esphome_direct, scheduler, wiz_direct
from .config import Config, DirectPathConfig
from .homeassistant import HomeAssistantController
from .models import Controller
from .scheduler import add_scheduler, remove_scheduler  # noqa


async def async_start(
    url,
    token,
    data_path,
    http_port,
    https_port,
    use_default_ports,
    direct_path: DirectPathConfig | None = None,
) -> Controller:
    """Initialize all controllers."""
    ctl = Controller()
    ctl.loop = asyncio.get_event_loop()
    # the HA client is initialized in the async_start because it needs a running loop
    ctl.controller_hass = HomeAssistantController(url=url, token=token)
    ctl.config_instance = Config(
        ctl,
        data_path,
        http_port,
        https_port,
        use_default_ports,
        direct_path,
    )
    await ctl.controller_hass.connect()
    return ctl


async def async_stop(ctl: Controller) -> None:
    """Shutdown all controllers."""
    await scheduler.async_stop()
    await esphome_direct.async_close_all()
    await wiz_direct.async_close_all()
    try:
        await ctl.controller_hass.disconnect()
    except AttributeError:
        # controller_hass is not initialized if home assistant isn't connected
        pass
