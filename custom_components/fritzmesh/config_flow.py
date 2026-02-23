"""Config flow for Fritz!Box Mesh integration.

Home Assistant uses "config flows" to drive the UI wizard that appears when
a user clicks "Add Integration".  This module defines the single-step form
that collects Fritz!Box connection details, validates them by attempting a
real connection, and stores the result as a ConfigEntry.

Flow steps
──────────
  async_step_user  – The only step: show a form, validate credentials,
                     create the entry on success.

Error handling
──────────────
  "cannot_connect" – Network unreachable, wrong port, etc.
  "invalid_auth"   – HTTP 401/403 or keyword "auth" in the exception message.
  "unknown"        – Anything else (shown for unexpected exceptions).
"""
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

# ── Input schema ────────────────────────────────────────────────────────────
# voluptuous schema used to:
#   a) validate types submitted via the UI form, and
#   b) tell the HA frontend which fields to render and what defaults to show.
#
# vol.Required  → the field is mandatory; the form won't submit without it.
# vol.Optional  → the field may be left blank (empty string is fine for creds
#                 when the Fritz!Box has no password set).
STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        # Credentials are optional because some Fritz!Box units ship without a
        # password on the local network.
        vol.Optional(CONF_USERNAME, default=""): str,
        vol.Optional(CONF_PASSWORD, default=""): str,
        vol.Required(CONF_USE_TLS, default=DEFAULT_USE_TLS): bool,
        vol.Required(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): int,
    }
)


async def _validate_input(hass: HomeAssistant, data: dict) -> None:
    """Validate credentials by attempting a real connection to the Fritz!Box.

    Constructs a FritzMeshFetcher with the user-supplied values and calls
    fetch() in an executor thread (because fritzconnection is synchronous).
    Raises an exception on any failure so the caller can map it to an error
    key shown in the form.

    Args:
        hass: The Home Assistant instance (needed for async_add_executor_job).
        data: Dict matching STEP_USER_SCHEMA keys with user-supplied values.

    Raises:
        Exception: Any exception raised by FritzMeshFetcher.fetch() propagates
                   up; the caller distinguishes auth errors from network errors.
    """
    fetcher = FritzMeshFetcher(
        address=data[CONF_HOST],
        port=data[CONF_PORT],
        user=data.get(CONF_USERNAME, ""),
        password=data.get(CONF_PASSWORD, ""),
        use_tls=data.get(CONF_USE_TLS, False),
    )
    # fritzconnection performs blocking socket I/O, so we run it in a
    # thread-pool executor to avoid blocking the HA event loop.
    await hass.async_add_executor_job(fetcher.fetch)


class FritzMeshConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a UI config flow for Fritz!Box Mesh.

    HA discovers this class via the `domain=DOMAIN` class argument and
    routes the "Add Integration" wizard to it.

    VERSION controls the config-entry schema version.  Bump this when
    migrating stored config data (requires an async_migrate_entry handler).
    """

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        """Handle the initial setup step shown to the user.

        This method is called twice:
          1. With user_input=None  → render the blank form.
          2. With user_input=<dict> → validate the submitted values.

        On success  → create the config entry and finish the flow.
        On failure  → re-show the form with an inline error message.

        Args:
            user_input: None on first load; a dict of form values on submit.

        Returns:
            A FlowResult from one of:
              self.async_show_form()     – display (or re-display) the form
              self.async_create_entry()  – save the entry and close the flow
              self.async_abort()         – abort (e.g. already configured)
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            # Use the hostname as a unique ID so that the same Fritz!Box
            # cannot be added twice.  async_abort() is called automatically
            # if the ID is already registered.
            await self.async_set_unique_id(user_input[CONF_HOST])
            self._abort_if_unique_id_configured()

            try:
                # Attempt a real connection so that bad credentials are caught
                # here rather than after the entry is created.
                await _validate_input(self.hass, user_input)
            except Exception as err:
                _LOGGER.exception("Validation error: %s", err)
                err_str = str(err).lower()
                # Heuristic: if the error message contains auth-related words
                # or HTTP 401/403 codes, treat it as an authentication failure.
                if any(kw in err_str for kw in ("auth", "password", "401", "403")):
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            else:
                # Validation succeeded → persist the entry.
                # The title appears in the integrations list in HA's UI.
                return self.async_create_entry(
                    title=user_input[CONF_HOST],
                    data=user_input,
                )

        # Render (or re-render with errors) the configuration form.
        # `errors` is empty on first load; on re-render it contains keys like
        # {"base": "cannot_connect"} which HA maps to strings.json entries.
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
