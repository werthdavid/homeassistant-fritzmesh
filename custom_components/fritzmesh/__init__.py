"""Fritz!Box Mesh custom component."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_USE_TLS,
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
)
from .coordinator import FritzMeshCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor"]
_CARD_URL = "/fritzmesh/fritzmesh-card.js"
_CARD_FILE = Path(__file__).parent / "www" / "fritzmesh-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the Lovelace card as a static resource (runs once at startup)."""
    if _CARD_FILE.exists():
        try:
            from homeassistant.components.http import StaticPathConfig
            from homeassistant.components.frontend import add_extra_js_url

            await hass.http.async_register_static_paths(
                [StaticPathConfig(_CARD_URL, str(_CARD_FILE), cache_headers=False)]
            )
            add_extra_js_url(hass, _CARD_URL)
            _LOGGER.debug("Registered fritzmesh-card at %s", _CARD_URL)
        except Exception:
            _LOGGER.warning(
                "Could not auto-register fritzmesh-card. "
                "Add '%s' manually as a Lovelace resource.",
                _CARD_URL,
            )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fritz!Box Mesh from a config entry."""
    coordinator = FritzMeshCoordinator(
        hass=hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data.get(CONF_USERNAME, ""),
        password=entry.data.get(CONF_PASSWORD, ""),
        use_tls=entry.data.get(CONF_USE_TLS, False),
        poll_interval=entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
