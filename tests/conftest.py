"""Shared fixtures: a fake LAN listener standing in for the real UDP socket."""
from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

HOST = "192.0.2.10"
DEVICE_ID = "AA:BB:CC:DD:EE:FF:00:11"
SCAN = {
    "ip": HOST,
    "device": DEVICE_ID,
    "sku": "H7094",
    "bleVersionHard": "3.05.00",
    "bleVersionSoft": "1.00.21",
    "wifiVersionHard": "1.04.01",
    "wifiVersionSoft": "1.06.24",
}
STATUS_ON = {"onOff": 1, "brightness": 100, "color": {"r": 255, "g": 0, "b": 0}, "colorTemInKelvin": 0}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeListener:
    """Records every message sent and answers scan and devStatus like an H7094."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, int, dict[str, Any]]] = []
        self.devices: dict[str, dict[str, Any]] = {HOST: dict(SCAN)}  # by current IP
        self.status: dict[str, Any] = dict(STATUS_ON)
        self.online = True

    def send(self, host: str, payload: bytes, port: int = 4003) -> None:
        self.sent.append((host, port, json.loads(payload)["msg"]))

    async def async_request(self, host, payload, reply_cmd, timeout, port=4003):
        self.send(host, payload, port)
        if not self.online or host not in self.devices:
            raise TimeoutError
        if reply_cmd == "scan":
            return dict(self.devices[host])
        return dict(self.status)

    async def async_scan(self, targets, timeout):
        if not self.online:
            return {}
        return {d["device"]: {**d, "ip": ip} for ip, d in self.devices.items()}

    # --- assertions helpers ---

    def take(self) -> list[tuple[str, Any]]:
        """Commands sent since the last take(), ignoring polls and scans.

        ptReal becomes ("frames", [bytes, ...]); others ("turn", data) etc.
        """
        out = []
        for _host, _port, msg in self.sent:
            if msg["cmd"] in ("devStatus", "scan"):
                continue
            if msg["cmd"] == "ptReal":
                out.append(("frames", [base64.b64decode(f) for f in msg["data"]["command"]]))
            else:
                out.append((msg["cmd"], msg["data"]))
        self.sent.clear()
        return out


@pytest.fixture
def fake_listener():
    listener = FakeListener()
    targets = AsyncMock(return_value=["239.255.255.250", "192.0.2.255"])
    get = AsyncMock(return_value=listener)
    with (
        patch("custom_components.govee_segments.async_get_listener", get),
        patch("custom_components.govee_segments.async_release_listener"),
        patch("custom_components.govee_segments.async_discovery_targets", targets),
        patch("custom_components.govee_segments.coordinator.async_discovery_targets", targets),
        patch("custom_components.govee_segments.config_flow.async_get_listener", get),
        patch("custom_components.govee_segments.config_flow.async_release_listener"),
        patch("custom_components.govee_segments.config_flow.async_discovery_targets", targets),
    ):
        yield listener
