"""Govee LAN Segments: one Home Assistant light per segment of a Govee LAN device."""
from __future__ import annotations

import asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .protocol import CMD_PORT, ptreal_message

PLATFORMS = [Platform.LIGHT]


class GoveeSender:
    """Fire-and-forget UDP sender for one device. Never binds the reply port."""

    def __init__(self, host: str) -> None:
        self.host = host
        self._transport: asyncio.DatagramTransport | None = None

    async def async_start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, remote_addr=(self.host, CMD_PORT)
        )

    def send_frames(self, frames: list[bytes]) -> None:
        if self._transport is not None:
            self._transport.sendto(ptreal_message(frames))

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None


type GoveeSegmentsConfigEntry = ConfigEntry[GoveeSender]


async def async_setup_entry(hass: HomeAssistant, entry: GoveeSegmentsConfigEntry) -> bool:
    sender = GoveeSender(entry.data[CONF_HOST])
    await sender.async_start()
    entry.runtime_data = sender
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GoveeSegmentsConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.close()
    return unloaded
