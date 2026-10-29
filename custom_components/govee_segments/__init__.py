"""Govee LAN Segments: Govee multi-light fixtures as one Home Assistant light per segment."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir

from .const import CONF_DEVICE_ID, CONFLICTING_DOMAIN, DOMAIN, ISSUE_PORT_CONFLICT
from .coordinator import GoveeCoordinator, GoveeDevice
from .listener import async_discovery_targets, async_get_listener, async_release_listener

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.LIGHT]


@dataclass
class GoveeRuntimeData:
    device: GoveeDevice
    coordinator: GoveeCoordinator
    scan: dict[str, Any]  # last scan reply: sku and firmware versions
    store: Any = None  # light.SegmentStore, set by the light platform


type GoveeSegmentsConfigEntry = ConfigEntry[GoveeRuntimeData]


@callback
def async_update_port_issue(hass: HomeAssistant, bind_error: OSError | None) -> None:
    """Raise a repair issue when port 4002 is taken, naming govee_light_local if it's set up."""
    if bind_error is None:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_PORT_CONFLICT)
        return
    conflict = bool(hass.config_entries.async_entries(CONFLICTING_DOMAIN))
    ir.async_create_issue(
        hass,
        DOMAIN,
        ISSUE_PORT_CONFLICT,
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key="port_conflict_govee_light_local" if conflict else "port_conflict",
        translation_placeholders={"error": str(bind_error)},
    )


async def async_setup_entry(hass: HomeAssistant, entry: GoveeSegmentsConfigEntry) -> bool:
    try:
        listener = await async_get_listener(hass)
    except OSError as err:
        async_update_port_issue(hass, err)
        raise ConfigEntryNotReady(f"UDP port 4002 is in use: {err}") from err
    async_update_port_issue(hass, None)
    entry.async_on_unload(lambda: async_release_listener(hass))

    device = GoveeDevice(listener, entry.data[CONF_HOST], entry.data[CONF_DEVICE_ID])
    try:
        scan = await device.async_scan()
    except TimeoutError:
        # Maybe it moved to a new IP: look for its device id on the whole subnet.
        found = await listener.async_scan(await async_discovery_targets(hass), 2.0)
        if (scan := found.get(device.device_id)) is None:
            raise ConfigEntryNotReady(f"No reply from {device.host}") from None
        device.host = scan["ip"]
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_HOST: device.host})

    coordinator = GoveeCoordinator(hass, entry, device)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = GoveeRuntimeData(device, coordinator, scan)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeSegmentsConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 (0.1.0) was keyed by IP. v2 is keyed by the device's permanent id, which
    takes one scan of the stored IP to learn."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        host = entry.data[CONF_HOST]
        try:
            listener = await async_get_listener(hass)
        except OSError as err:
            async_update_port_issue(hass, err)
            _LOGGER.error("Can't migrate %s: UDP port 4002 is in use (%s)", entry.title, err)
            return False
        try:
            scan = await GoveeDevice(listener, host, "").async_scan()
        except TimeoutError:
            _LOGGER.error(
                "Can't migrate %s: no reply from %s. Make sure the lights are powered, then restart",
                entry.title,
                host,
            )
            return False
        finally:
            async_release_listener(hass)
        device_id = scan["device"]

        @callback
        def rekey(entity: er.RegistryEntry) -> dict[str, str] | None:
            if entity.unique_id.startswith(f"{host}_"):
                return {"new_unique_id": device_id + entity.unique_id[len(host) :]}
            return None

        await er.async_migrate_entries(hass, entry.entry_id, rekey)
        dev_reg = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
            if (DOMAIN, host) in device.identifiers:
                dev_reg.async_update_device(device.id, new_identifiers={(DOMAIN, device_id)})
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_DEVICE_ID: device_id},
            unique_id=device_id,
            version=2,
        )
        _LOGGER.info("Migrated %s to device id %s", entry.title, device_id)
    return True
