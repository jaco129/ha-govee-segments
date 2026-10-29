"""Config flow: the user enters the device's IP and model.

There is no discovery or connection check on purpose. Both would need replies on
UDP 4002, which govee_light_local already holds on the same host.
"""
from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import CONF_SKU, DOMAIN
from .protocol import SEGMENTS_BY_SKU


class GoveeSegmentsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                ipaddress.ip_address(host)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            else:
                await self.async_set_unique_id(host)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Govee {user_input[CONF_SKU]}",
                    data={CONF_HOST: host, CONF_SKU: user_input[CONF_SKU]},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_SKU, default="H7094"): vol.In(sorted(SEGMENTS_BY_SKU)),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
