"""Listener tests: reply routing, timeouts, scans. Uses a fake transport, no sockets."""
import asyncio
import json
from unittest.mock import MagicMock

import pytest

from custom_components.govee_segments.listener import GoveeListener


def reply(cmd, data):
    return json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()


@pytest.fixture
def listener():
    lst = GoveeListener()
    lst._transport = MagicMock()
    yield lst
    lst.close()


async def test_request_gets_matching_reply(listener):
    task = asyncio.create_task(listener.async_request("192.0.2.1", b"req", "devStatus", 1))
    await asyncio.sleep(0)
    listener._transport.sendto.assert_called_once_with(b"req", ("192.0.2.1", 4003))
    listener.datagram_received(reply("devStatus", {"onOff": 0}), ("192.0.2.9", 4003))  # other device
    listener.datagram_received(reply("scan", {}), ("192.0.2.1", 4003))  # other cmd
    listener.datagram_received(b"garbage", ("192.0.2.1", 4003))
    assert not task.done()
    listener.datagram_received(reply("devStatus", {"onOff": 1}), ("192.0.2.1", 4003))
    assert await task == {"onOff": 1}


async def test_request_times_out(listener):
    with pytest.raises(TimeoutError):
        await listener.async_request("192.0.2.1", b"req", "devStatus", 0.01)
    assert listener._waiters[("192.0.2.1", "devStatus")] == []


async def test_scan_collects_replies_by_device_id(listener):
    task = asyncio.create_task(listener.async_scan(["239.255.255.250", "192.0.2.255"], 0.05))
    await asyncio.sleep(0)
    assert listener._transport.sendto.call_count == 2
    listener.datagram_received(reply("scan", {"ip": "192.0.2.7", "device": "A", "sku": "H7094"}), ("192.0.2.7", 4001))
    listener.datagram_received(reply("scan", {"device": "B", "sku": "H7094"}), ("192.0.2.8", 4001))
    found = await task
    assert found["A"]["ip"] == "192.0.2.7"
    assert found["B"]["ip"] == "192.0.2.8"  # falls back to the sender address


async def test_scan_reply_also_answers_unicast_scan_request(listener):
    task = asyncio.create_task(listener.async_request("192.0.2.7", b"scan", "scan", 1, port=4001))
    await asyncio.sleep(0)
    listener.datagram_received(reply("scan", {"ip": "192.0.2.7", "device": "A"}), ("192.0.2.7", 4001))
    assert (await task)["device"] == "A"
