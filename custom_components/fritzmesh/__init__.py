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
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.lovelace import MODE_STORAGE, LovelaceData
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_USE_TLS,
    CONF_POLL_INTERVAL,
    CONF_DEBUG_MODE,
    CONF_DEBUG_USE_JSON,
    CONF_DEBUG_JSON_PATH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_DEBUG_MODE,
    DEFAULT_DEBUG_USE_JSON,
    DEFAULT_DEBUG_JSON_PATH,
)
from .coordinator import FritzMeshCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = ["sensor", "binary_sensor"]

_CARD_STATIC_URL = "/fritzmesh/fritzmesh-card.js"
_CARD_VERSION = "1.9.3"
_CARD_URL = f"{_CARD_STATIC_URL}?v={_CARD_VERSION}"
_CARD_FILE = Path(__file__).parent / "www" / "fritzmesh-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Serve the Lovelace card JS and register it as a module resource.

    Why we defer resource registration
    ────────────────────────────────────
    add_extra_js_url() injects a plain <script> tag into HA's HTML head.
    Lovelace knows nothing about it, so it never calls
    customElements.whenDefined() before trying to render cards — causing a
    "Configuration error" on every cold (uncached) page load.

    The Lovelace Resources API (Settings → Dashboards → Resources) does
    trigger whenDefined(), but hass.data["lovelace"]["resources"] is only
    populated after lovelace's own async_setup completes.  We therefore
    schedule registration for EVENT_HOMEASSISTANT_STARTED, by which time
    all core components including lovelace are ready.

    On the very first HA start the resource entry is created in storage.
    Every subsequent start it already exists, so the duplicate guard is a
    no-op and no extra write is performed.
    """
    if not _CARD_FILE.exists():
        _LOGGER.warning("fritzmesh-card.js not found at %s", _CARD_FILE)
        return True

    # Step 1: serve the file over HTTP (needed regardless of registration method).
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(_CARD_STATIC_URL, str(_CARD_FILE), cache_headers=False)]
        )
    except Exception as exc:
        _LOGGER.warning(
            "Could not register static path for fritzmesh-card (%s). "
            "Add '%s' manually as a Lovelace resource.",
            exc, _CARD_URL,
        )
        return True

    # Step 2: register as a Lovelace module resource once HA has started.
    async def _register_resource(_event=None) -> None:
        await _async_register_lovelace_resource_when_ready(hass, _CARD_URL)

    if hass.is_running:
        # Integration was loaded after HA started (e.g. via developer tools).
        hass.async_create_task(_register_resource())
    else:
        # Normal startup path: lovelace is not ready yet; wait for it.
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _register_resource)

    return True


async def _async_register_lovelace_resource(hass: HomeAssistant, url: str) -> bool:
    """Add *url* as a Lovelace module resource if it is not already present.

    Returns True when the resource is (now) registered via the Lovelace
    storage API, False when the collection is unavailable (YAML mode) or an
    unexpected error occurs.
    """
    try:
        lovelace: LovelaceData | None = hass.data.get("lovelace")
        resources = lovelace.resources if lovelace else None

        if lovelace is None or resources is None:
            _LOGGER.debug(
                "Lovelace resource collection not available "
                "(YAML mode or lovelace not yet loaded)"
            )
            return False

        # Guard against duplicate entries accumulating across HA restarts.
        for item in resources.async_items():
            if _strip_query(item.get("url", "")) == _strip_query(url):
                _LOGGER.debug("fritzmesh-card already in Lovelace resources, skipping")
                return True

        await resources.async_create_item({"res_type": "module", "url": url})
        return True

    except Exception as exc:
        _LOGGER.debug("Could not register Lovelace resource: %s", exc)
        return False


async def _async_register_lovelace_resource_when_ready(
    hass: HomeAssistant, url: str
) -> None:
    """Register Lovelace resource after resource storage has loaded.

    In storage mode, Lovelace resources can still be loading when HA has
    already reached EVENT_HOMEASSISTANT_STARTED, so retry until loaded.
    """
    lovelace: LovelaceData | None = hass.data.get("lovelace")
    if lovelace is None:
        add_extra_js_url(hass, url)
        _LOGGER.info(
            "fritzmesh-card: Lovelace not available. "
            "Add '%s' as a module resource for reliable loading.",
            url,
        )
        return

    resource_mode = getattr(lovelace, "resource_mode", getattr(lovelace, "mode", None))
    if resource_mode != MODE_STORAGE:
        # Lovelace is in YAML mode (resource collection is read-only).
        add_extra_js_url(hass, url)
        _LOGGER.info(
            "fritzmesh-card: Lovelace is in YAML mode. "
            "Add '%s' as a module resource for reliable loading.",
            url,
        )
        return

    async def _check_resources_loaded(_now) -> None:
        resources = lovelace.resources
        if resources and getattr(resources, "loaded", True):
            if await _async_register_lovelace_resource(hass, url):
                _LOGGER.debug(
                    "fritzmesh-card registered as Lovelace module resource at %s",
                    url,
                )
            return

        _LOGGER.debug("Lovelace resources not loaded yet, retrying in 5 seconds")
        async_call_later(hass, 5, _check_resources_loaded)

    await _check_resources_loaded(0)


def _strip_query(url: str) -> str:
    """Return URL path without query params."""
    return url.split("?", 1)[0]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fritz!Box Mesh from a config entry."""
    coordinator = FritzMeshCoordinator(
        hass=hass,
        host=entry.data[CONF_HOST],
        port=entry.data[CONF_PORT],
        username=entry.data.get(CONF_USERNAME, ""),
        password=entry.data.get(CONF_PASSWORD, ""),
        use_tls=entry.data.get(CONF_USE_TLS, False),
        poll_interval=entry.options.get(
            CONF_POLL_INTERVAL,
            entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        ),
        debug_mode=entry.options.get(
            CONF_DEBUG_MODE,
            entry.data.get(CONF_DEBUG_MODE, DEFAULT_DEBUG_MODE),
        ),
        debug_use_json=entry.options.get(
            CONF_DEBUG_USE_JSON,
            entry.data.get(CONF_DEBUG_USE_JSON, DEFAULT_DEBUG_USE_JSON),
        ),
        debug_json_path=entry.options.get(
            CONF_DEBUG_JSON_PATH,
            entry.data.get(CONF_DEBUG_JSON_PATH, DEFAULT_DEBUG_JSON_PATH),
        ),
    )

    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
