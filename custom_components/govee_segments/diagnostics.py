"""Diagnostics download: what the integration knows and what the device last said."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import GoveeSegmentsConfigEntry
from .const import CONF_DEVICE_ID

# Users post these publicly; the IP and device id identify their network and device.
TO_REDACT = {CONF_HOST, CONF_DEVICE_ID, "ip", "device"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GoveeSegmentsConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "scan": async_redact_data(data.scan, TO_REDACT),
        "last_dev_status": data.coordinator.data,
        "last_update_success": data.coordinator.last_update_success,
        "store": data.store.as_dict() if data.store is not None else None,
        "scenes": len(data.store.scenes) if data.store is not None else 0,
    }
