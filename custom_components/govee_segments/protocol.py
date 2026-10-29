"""Govee LAN API frames for per-segment ("per-light") control.

Pure Python with no Home Assistant imports, so it can be tested on its own and
offered upstream later. Confirmed on an H7094 (wifiSoft 1.06.24), see FINDINGS.md
in the research project:

- Commands are UDP JSON sent to device_ip:4003. This module never listens for
  replies, so it can share a host with govee_light_local (which owns UDP 4002).
- `ptReal` carries a list of 20-byte BLE frames, all applied in order.
- Segment colour:     33 05 15 01 RR GG BB 00 00 00 00 00 MASK_LO MASK_HI .. XOR
- Segment brightness: 33 05 15 02 PCT MASK_LO MASK_HI .. XOR   (PCT 0-100; 0 is a
  minimum glow, not off)
- A segment is switched off by setting its colour to 000000.
"""
from __future__ import annotations

import base64
import json

CMD_PORT = 4003

# Number of addressable lights per SKU; light n (1-based, nearest the control box
# first) is segment bit n-1. Only models confirmed on real hardware belong here.
SEGMENTS_BY_SKU = {"H7094": 4}


def _frame(payload: bytes) -> bytes:
    """Zero-pad to 19 bytes and append the XOR of those 19."""
    if len(payload) > 19:
        raise ValueError(f"frame payload is {len(payload)} bytes; max 19")
    body = payload.ljust(19, b"\x00")
    check = 0
    for b in body:
        check ^= b
    return body + bytes([check])


def _mask(segments: list[int]) -> bytes:
    bits = 0
    for seg in segments:
        if not 0 <= seg < 15:
            raise ValueError(f"segment {seg} out of range 0-14")
        bits |= 1 << seg
    return bits.to_bytes(2, "little")


def color_frame(segments: list[int], rgb: tuple[int, int, int]) -> bytes:
    # 33 05 15 01 | RR GG BB | 00 00 (colour temp) | 00 00 00 (CT rgb) | mask lo, mask hi
    return _frame(b"\x33\x05\x15\x01" + bytes(rgb) + b"\x00" * 5 + _mask(segments))


def brightness_frame(segments: list[int], percent: int) -> bytes:
    if not 0 <= percent <= 100:
        raise ValueError(f"brightness {percent} out of range 0-100")
    return _frame(b"\x33\x05\x15\x02" + bytes([percent]) + _mask(segments))


def off_frame(segments: list[int]) -> bytes:
    return color_frame(segments, (0, 0, 0))


def ptreal_message(frames: list[bytes]) -> bytes:
    command = [base64.b64encode(f).decode() for f in frames]
    msg = {"msg": {"cmd": "ptReal", "data": {"command": command}}}
    return json.dumps(msg, separators=(",", ":")).encode()
