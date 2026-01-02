from __future__ import annotations

from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from .const import (
    CONF_NUMBERS,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_SELECTS,
    CONF_SENSORS,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_USERNAME,
    DOMAIN,
)


class ClimatixGenericConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        # Minimal UI flow: connection only. Entities are typically configured via YAML import.
        errors: Dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(f"{DOMAIN}:{user_input[CONF_HOST]}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=f"Climatix ({user_input[CONF_HOST]})", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                vol.Optional(CONF_PIN, default=DEFAULT_PIN): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SEC): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_import(self, user_input: Dict[str, Any]):
        # Import from YAML so the integration becomes a config entry.
        host = user_input[CONF_HOST]
        await self.async_set_unique_id(f"{DOMAIN}:{host}")
        self._abort_if_unique_id_configured()

        # Store entire YAML block (including entity configs) in the config entry.
        data = {
            CONF_HOST: user_input.get(CONF_HOST),
            CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
            CONF_USERNAME: user_input.get(CONF_USERNAME, DEFAULT_USERNAME),
            CONF_PASSWORD: user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            CONF_PIN: user_input.get(CONF_PIN, DEFAULT_PIN),
            CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC),
            CONF_SENSORS: list(user_input.get(CONF_SENSORS, []) or []),
            CONF_NUMBERS: list(user_input.get(CONF_NUMBERS, []) or []),
            CONF_SELECTS: list(user_input.get(CONF_SELECTS, []) or []),
        }
        return self.async_create_entry(title=f"Climatix ({host})", data=data)
