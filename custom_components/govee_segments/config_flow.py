"""Config flow: discover devices on the LAN, or enter an IP, and confirm with a scan.

Entries are keyed by the device's permanent id from its scan reply, so an IP
change doesn't create a duplicate.
"""
from __future__ import annotations

import ipaddress
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import CONF_DEVICE_ID, CONF_SKU, DOMAIN
from .coordinator import SCAN_TIMEOUT, GoveeDevice
from .listener import async_discovery_targets, async_get_listener, async_release_listener
from .protocol import SEGMENTS_BY_SKU

CONF_DEVICE = "device"
MANUAL = "manual"


class GoveeSegmentsConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._discovered: dict[str, dict[str, Any]] = {}

    async def _async_scan(self, host: str | None = None) -> dict[str, dict[str, Any]]:
        """Scan the LAN (or just `host`). Returns replies by device id; {} on no reply."""
        listener = await async_get_listener(self.hass)
        try:
            if host is None:
                return await listener.async_scan(await async_discovery_targets(self.hass), SCAN_TIMEOUT)
            try:
                reply = await GoveeDevice(listener, host, "").async_scan()
            except TimeoutError:
                return {}
            return {reply["device"]: {**reply, "ip": host}}
        finally:
            async_release_listener(self.hass)

    def _create(self, reply: dict[str, Any]) -> ConfigFlowResult:
        sku = reply["sku"]
        return self.async_create_entry(
            title=f"Govee {sku}",
            data={CONF_HOST: reply["ip"], CONF_SKU: sku, CONF_DEVICE_ID: reply["device"]},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            if user_input[CONF_DEVICE] == MANUAL:
                return await self.async_step_manual()
            reply = self._discovered[user_input[CONF_DEVICE]]
            await self.async_set_unique_id(reply["device"])
            self._abort_if_unique_id_configured(updates={CONF_HOST: reply["ip"]})
            return self._create(reply)

        try:
            found = await self._async_scan()
        except OSError:
            return self.async_abort(reason="port_in_use")
        configured = self._async_current_ids(include_ignore=False)
        self._discovered = {
            device_id: reply
            for device_id, reply in found.items()
            if reply.get("sku") in SEGMENTS_BY_SKU and device_id not in configured
        }
        if not self._discovered:
            return await self.async_step_manual()
        options = {
            device_id: f"{reply['sku']} at {reply['ip']}" for device_id, reply in self._discovered.items()
        }
        options[MANUAL] = "Enter an IP address"
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema({vol.Required(CONF_DEVICE): vol.In(options)})
        )

    async def _async_validate_host(
        self, user_input: dict[str, Any], errors: dict[str, str]
    ) -> dict[str, Any] | None:
        host = user_input[CONF_HOST].strip()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            errors[CONF_HOST] = "invalid_host"
            return None
        try:
            found = await self._async_scan(host)
        except OSError:
            errors["base"] = "port_in_use"
            return None
        if not found:
            errors["base"] = "cannot_connect"
            return None
        reply = next(iter(found.values()))
        if reply.get("sku") not in SEGMENTS_BY_SKU:
            errors["base"] = "unsupported"
            return None
        return reply

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None and (reply := await self._async_validate_host(user_input, errors)):
            await self.async_set_unique_id(reply["device"])
            self._abort_if_unique_id_configured(updates={CONF_HOST: reply["ip"]})
            return self._create(reply)
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
            description_placeholders={"models": ", ".join(sorted(SEGMENTS_BY_SKU))},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None and (reply := await self._async_validate_host(user_input, errors)):
            await self.async_set_unique_id(reply["device"])
            self._abort_if_unique_id_mismatch(reason="wrong_device")
            return self.async_update_reload_and_abort(entry, data_updates={CONF_HOST: reply["ip"]})
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str}),
            errors=errors,
        )
