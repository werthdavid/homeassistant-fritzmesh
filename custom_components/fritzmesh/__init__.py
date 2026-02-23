"""Fritz!Box Mesh custom component.

Integration life-cycle
──────────────────────
1. async_setup()        – Called once when Home Assistant loads the domain
                          (even before any config entry exists).  We use this
                          hook to register the Lovelace card JS file as a
                          static HTTP resource so users don't have to add it
                          manually in the HA dashboard.

2. async_setup_entry()  – Called once per config entry (i.e. once per
                          Fritz!Box the user has configured).  Creates the
                          FritzMeshCoordinator, does the first data refresh,
                          then forwards setup to each platform (sensor,
                          binary_sensor).

3. async_unload_entry() – Called when the user removes the integration or HA
                          shuts down.  Tears down all platform entities and
                          removes the coordinator from hass.data.
"""
from __future__ import annotations

import logging
from pathlib import Path

import homeassistant.helpers.config_validation as cv
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

# Required by HACS/hassfest: integrations with async_setup must declare a
# CONFIG_SCHEMA. Since this integration is configured only via config entries,
# we use the helper that enforces exactly that.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

# Platforms that this integration provides entities for.
# Each string must match a Python module of the same name in this package.
PLATFORMS = ["sensor", "binary_sensor"]

# URL at which the Lovelace card JavaScript will be served by the HA HTTP
# server.  Must be unique across all installed custom cards.
_CARD_URL = "/fritzmesh/fritzmesh-card.js"

# Absolute path to the bundled JS file that ships with this integration.
# Path(__file__) resolves to this __init__.py, so .parent is the package
# folder, and "www/" is the subfolder that HA conventionally uses for
# front-end resources.
_CARD_FILE = Path(__file__).parent / "www" / "fritzmesh-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the Lovelace card as a static resource (runs once at startup).

    Home Assistant calls this function exactly once for the entire domain,
    regardless of how many config entries exist.  We use it only to serve
    the front-end card JS file; the real work happens in async_setup_entry.

    By registering the JS via add_extra_js_url() we inject it into every
    Lovelace dashboard automatically, which means users don't need to add
    the resource manually in the dashboard settings.

    Falls back gracefully (with a warning) if the new StaticPathConfig API
    isn't available in older HA versions.
    """
    if _CARD_FILE.exists():
        try:
            # StaticPathConfig was introduced in HA 2024.x.  We import it
            # here (not at module level) so that the integration can still
            # load on slightly older versions — the except block handles it.
            from homeassistant.components.http import StaticPathConfig
            from homeassistant.components.frontend import add_extra_js_url

            # Register the local file to be served at _CARD_URL.
            # cache_headers=False means the browser will always re-check
            # for updates instead of caching the file indefinitely.
            await hass.http.async_register_static_paths(
                [StaticPathConfig(_CARD_URL, str(_CARD_FILE), cache_headers=False)]
            )

            # Tell the Lovelace front-end to load this JS on every page load.
            add_extra_js_url(hass, _CARD_URL)
            _LOGGER.debug("Registered fritzmesh-card at %s", _CARD_URL)
        except Exception:
            # Non-fatal: the card simply won't auto-load.  The user can add
            # the resource manually via Settings → Dashboards → Resources.
            _LOGGER.warning(
                "Could not auto-register fritzmesh-card. "
                "Add '%s' manually as a Lovelace resource.",
                _CARD_URL,
            )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fritz!Box Mesh from a config entry.

    This function runs once for each Fritz!Box the user has configured.

    Steps:
      1. Instantiate FritzMeshCoordinator with the stored credentials.
      2. Trigger the first data refresh (blocking until data is available or
         an exception is raised — HA will retry the entry on failure).
      3. Store the coordinator in hass.data so that platform modules can
         retrieve it by entry_id.
      4. Forward setup to every platform so their entities are created.
    """
    # Build the coordinator that will periodically poll the Fritz!Box.
    # All connection parameters come from the config entry that was created
    # by config_flow.py when the user completed the setup wizard.
    coordinator = FritzMeshCoordinator(
        hass=hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data.get(CONF_USERNAME, ""),
        password=entry.data.get(CONF_PASSWORD, ""),
        use_tls=entry.data.get(CONF_USE_TLS, False),
        poll_interval=entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )

    # Perform the very first fetch synchronously (from HA's perspective).
    # If this raises, HA will mark the entry as failed and retry later.
    await coordinator.async_config_entry_first_refresh()

    # Store the coordinator keyed by entry_id so each platform module can
    # look it up with:  hass.data[DOMAIN][entry.entry_id]
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Delegate entity creation to sensor.py and binary_sensor.py.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Called when the user removes the integration or when HA shuts down.
    We let each platform unregister its entities first, then clean up
    the coordinator from hass.data.
    """
    # Ask every platform to unload its entities.  Returns True only if all
    # platforms unloaded without error.
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove the coordinator from the shared data store so it can be
        # garbage-collected.
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
