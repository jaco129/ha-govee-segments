"""One device on the LAN, and the coordinator that polls its devStatus."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .listener import GoveeListener, async_discovery_targets
from .protocol import SCAN_PORT, ptreal_message, scan_message, status_message

_LOGGER = logging.getLogger(__name__)

POLL_INTERVAL = timedelta(seconds=10)
REPLY_TIMEOUT = 1.5
SCAN_TIMEOUT = 2.0
# A power cycle silences the device for ~20 s, so two missed polls is the most we
# can wait and still notice one.
MISSES_BEFORE_UNAVAILABLE = 2


class GoveeDevice:
    def __init__(self, listener: GoveeListener, host: str, device_id: str) -> None:
        self.listener = listener
        self.host = host
        self.device_id = device_id

    def send(self, payload: bytes) -> None:
        self.listener.send(self.host, payload)

    def send_frames(self, frames: list[bytes]) -> None:
        self.send(ptreal_message(frames))

    async def async_status(self) -> dict[str, Any]:
        return await self.listener.async_request(self.host, status_message(), "devStatus", REPLY_TIMEOUT)

    async def async_scan(self) -> dict[str, Any]:
        """Unicast scan; returns the scan reply (sku, firmware) or raises TimeoutError."""
        return await self.listener.async_request(
            self.host, scan_message(), "scan", SCAN_TIMEOUT, port=SCAN_PORT
        )


class GoveeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls devStatus. Data is the last devStatus reply (whole fixture only)."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device: GoveeDevice) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {device.host}",
            update_interval=POLL_INTERVAL,
        )
        self.device = device
        self._misses = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            status = await self.device.async_status()
        except TimeoutError:
            self._misses += 1
            if self._misses < MISSES_BEFORE_UNAVAILABLE and self.data is not None:
                return self.data
            await self._async_find_moved_device()
            raise UpdateFailed(f"No reply from {self.device.host}") from None
        self._misses = 0
        return status

    async def _async_find_moved_device(self) -> None:
        """If the device answers a broadcast scan from a new IP, follow it there."""
        found = await self.device.listener.async_scan(
            await async_discovery_targets(self.hass), SCAN_TIMEOUT
        )
        if (reply := found.get(self.device.device_id)) is None or reply["ip"] == self.device.host:
            return
        _LOGGER.info("Govee %s moved from %s to %s", self.device.device_id, self.device.host, reply["ip"])
        self.device.host = reply["ip"]
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_HOST: reply["ip"]}
        )
