"""Config flow for Fritz!Box Mesh integration."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_USE_TLS,
    CONF_POLL_INTERVAL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_USE_TLS,
    DEFAULT_POLL_INTERVAL,
)
from .fritz_mesh import FritzMeshFetcher

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_USERNAME, default=""): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
        vol.Required(CONF_USE_TLS, default=DEFAULT_USE_TLS): bool,
        vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): int,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict) -> None:
    """Validate credentials by attempting a real connection."""
    fetcher = FritzMeshFetcher(
        address=data[CONF_HOST],
        port=data[CONF_PORT],
        user=data.get(CONF_USERNAME, ""),
        password=data.get(CONF_PASSWORD, ""),
        use_tls=data.get(CONF_USE_TLS, False),
    )
    await hass.async_add_executor_job(fetcher.fetch)


class FritzMeshConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a UI config flow for Fritz!Box Mesh."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            try:
                await _validate_input(self.hass, user_input)
            except Exception as err:
                _LOGGER.exception("Validation error: %s", err)
                err_str = str(err).lower()
                if any(kw in err_str for kw in ("auth", "password", "401", "403")):
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=user_input[CONF_HOST],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
