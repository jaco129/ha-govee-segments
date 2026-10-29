"""Govee LAN API messages, including per-segment ("per-light") control.

Pure Python with no Home Assistant imports, so it can be tested on its own and
offered upstream later. Confirmed on an H7094 (wifiSoft 1.06.24), see FINDINGS.md
in the research project:

- Commands are UDP JSON sent to device_ip:4003; scan goes to port 4001. Replies
  always come back to port 4002 on the sender.
- `ptReal` carries a list of 20-byte BLE frames, all applied in order. It is
  write-only: read frames (0xAA) get no reply over LAN.
- Segment colour:     33 05 15 01 RR GG BB 00 00 00 00 00 MASK_LO MASK_HI .. XOR
- Segment brightness: 33 05 15 02 PCT MASK_LO MASK_HI .. XOR   (PCT 0-100, absolute;
  0 is a minimum glow, not off)
- A segment is switched off by setting its colour to 000000.
- Scenes: optional a3 parameter packets, then 33 05 04 CODE_LO CODE_HI.
- devStatus reports the whole fixture only. Segment frames leave it alone, except
  that it can pick up a segment colour later; white (kelvin) mode reads as colour 0/0/0.
"""
from __future__ import annotations

import base64
import json
from typing import Any

CMD_PORT = 4003
SCAN_PORT = 4001
REPLY_PORT = 4002
MULTICAST_ADDR = "239.255.255.250"

# Number of addressable lights per SKU; light n (1-based, nearest the control box
# first) is segment bit n-1. Only models confirmed on real hardware belong here.
SEGMENTS_BY_SKU = {"H7094": 4}

KELVIN_RANGE = (2000, 9000)  # clean whites across this range on an H7094

_FRAME_PAYLOAD = 19


def _frame(payload: bytes) -> bytes:
    """Zero-pad to 19 bytes and append the XOR of those 19."""
    if len(payload) > _FRAME_PAYLOAD:
        raise ValueError(f"frame payload is {len(payload)} bytes; max {_FRAME_PAYLOAD}")
    body = payload.ljust(_FRAME_PAYLOAD, b"\x00")
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


def scene_frames(code: int, param: bytes = b"") -> list[bytes]:
    """Frames that start a scene from Govee's per-model effect library.

    `param` is the entry's base64-decoded `scenceParam` (empty for built-in
    scenes). It goes first as a run of a3 frames: the first carries the frame
    count, the last is indexed ff. Same encoding as wez/govee2mqtt and
    govee-local-api; confirmed on an H7094 with Aurora (13089).
    """
    if not 0 <= code <= 0xFFFF:
        raise ValueError(f"scene code {code} out of range 0-65535")
    frames = []
    if param:
        stream = bytearray(b"\xa3\x00\x01\x00\x02")
        count = 0
        last_index = 1
        for byte in param:
            if len(stream) % _FRAME_PAYLOAD == 0:
                count += 1
                stream.append(0xA3)
                last_index = len(stream)
                stream.append(count)
            stream.append(byte)
        stream[last_index] = 0xFF
        stream[3] = count + 1
        frames = [
            _frame(bytes(stream[i : i + _FRAME_PAYLOAD])) for i in range(0, len(stream), _FRAME_PAYLOAD)
        ]
    frames.append(_frame(b"\x33\x05\x04" + code.to_bytes(2, "little")))
    return frames


def message(cmd: str, data: dict[str, Any]) -> bytes:
    return json.dumps({"msg": {"cmd": cmd, "data": data}}, separators=(",", ":")).encode()


def ptreal_message(frames: list[bytes]) -> bytes:
    return message("ptReal", {"command": [base64.b64encode(f).decode() for f in frames]})


def scan_message() -> bytes:
    return message("scan", {"account_topic": "reserve"})


def status_message() -> bytes:
    return message("devStatus", {})


def turn_message(on: bool) -> bytes:
    return message("turn", {"value": 1 if on else 0})


def brightness_message(percent: int) -> bytes:
    if not 1 <= percent <= 100:
        raise ValueError(f"brightness {percent} out of range 1-100")
    return message("brightness", {"value": percent})


def kelvin_message(kelvin: int) -> bytes:
    return message("colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin})


def parse(datagram: bytes) -> tuple[str, dict[str, Any]] | None:
    """Return (cmd, data) for a Govee reply, or None if it isn't one."""
    try:
        msg = json.loads(datagram)["msg"]
        cmd, data = msg["cmd"], msg["data"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(cmd, str) or not isinstance(data, dict):
        return None
    return cmd, data
