"""The one UDP socket on port 4002, shared by every config entry.

Govee devices send all replies to port 4002 on the sender, so a host can only
have one listener. Commands go out from the same socket, and replies are routed
back to waiting requests by source IP and cmd.

The socket is bound without SO_REUSEADDR/SO_REUSEPORT on purpose: if another
integration (such as govee_light_local) already holds the port, binding fails
loudly instead of the kernel silently splitting replies between the two.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
import logging
import socket
from typing import Any

from homeassistant.components import network
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .protocol import CMD_PORT, MULTICAST_ADDR, REPLY_PORT, SCAN_PORT, parse, scan_message

_LOGGER = logging.getLogger(__name__)

_DATA_LISTENER = "listener"
_DATA_REFS = "listener_refs"
_DATA_LOCK = "listener_lock"


class GoveeListener(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self._transport: asyncio.DatagramTransport | None = None
        self._waiters: dict[tuple[str, str], list[asyncio.Future[dict[str, Any]]]] = {}
        self._scan_callbacks: list[Callable[[str, dict[str, Any]], None]] = []

    async def async_start(self, port: int = REPLY_PORT) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setblocking(False)
            sock.bind(("0.0.0.0", port))
        except OSError:
            sock.close()
            raise
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(lambda: self, sock=sock)

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        for waiters in self._waiters.values():
            for fut in waiters:
                fut.cancel()
        self._waiters.clear()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if (parsed := parse(data)) is None:
            _LOGGER.debug("Ignoring non-Govee datagram from %s", addr[0])
            return
        cmd, payload = parsed
        ip = addr[0]
        if cmd == "scan":
            ip = payload.get("ip") or ip
            for scan_callback in list(self._scan_callbacks):
                scan_callback(ip, payload)
        for fut in self._waiters.pop((ip, cmd), []):
            if not fut.done():
                fut.set_result(payload)

    def send(self, host: str, payload: bytes, port: int = CMD_PORT) -> None:
        if self._transport is None:
            raise RuntimeError("Govee listener is closed")
        self._transport.sendto(payload, (host, port))

    async def async_request(
        self, host: str, payload: bytes, reply_cmd: str, timeout: float, port: int = CMD_PORT
    ) -> dict[str, Any]:
        """Send `payload` and wait for a `reply_cmd` reply from `host`.

        Raises TimeoutError if nothing arrives in time.
        """
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        waiters = self._waiters.setdefault((host, reply_cmd), [])
        waiters.append(fut)
        try:
            self.send(host, payload, port)
            async with asyncio.timeout(timeout):
                return await fut
        finally:
            if fut in waiters:
                waiters.remove(fut)

    async def async_scan(self, targets: Iterable[str], timeout: float) -> dict[str, dict[str, Any]]:
        """Send a scan to each target address; return replies by device id."""
        found: dict[str, dict[str, Any]] = {}

        def on_scan(ip: str, payload: dict[str, Any]) -> None:
            if device_id := payload.get("device"):
                found[device_id] = {**payload, "ip": ip}

        self._scan_callbacks.append(on_scan)
        try:
            for target in targets:
                try:
                    self.send(target, scan_message(), SCAN_PORT)
                except OSError as err:  # e.g. an unroutable broadcast address
                    _LOGGER.debug("Scan to %s failed: %s", target, err)
            await asyncio.sleep(timeout)
        finally:
            self._scan_callbacks.remove(on_scan)
        return found


async def async_discovery_targets(hass: HomeAssistant) -> list[str]:
    """Multicast plus each enabled adapter's subnet broadcast. Some networks
    drop the multicast scan but pass the broadcast one."""
    broadcasts = await network.async_get_ipv4_broadcast_addresses(hass)
    return [MULTICAST_ADDR, *sorted(str(addr) for addr in broadcasts if not addr.is_loopback)]


async def async_get_listener(hass: HomeAssistant) -> GoveeListener:
    """Return the shared listener, starting it on first use. Raises OSError if
    port 4002 is taken."""
    data = hass.data.setdefault(DOMAIN, {})
    lock = data.setdefault(_DATA_LOCK, asyncio.Lock())
    async with lock:
        if _DATA_LISTENER not in data:
            listener = GoveeListener()
            await listener.async_start()
            data[_DATA_LISTENER] = listener
            data[_DATA_REFS] = 0
        data[_DATA_REFS] += 1
        return data[_DATA_LISTENER]


def async_release_listener(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN, {})
    if _DATA_LISTENER not in data:
        return
    data[_DATA_REFS] -= 1
    if data[_DATA_REFS] <= 0:
        data.pop(_DATA_LISTENER).close()
        data.pop(_DATA_REFS)
