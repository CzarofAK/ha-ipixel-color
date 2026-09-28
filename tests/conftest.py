"""Test fixtures: simulated iPIXEL panels behind HA's Bluetooth stack.

FakePanel emulates the parts of the BLE protocol the integration relies on:
- device info query (08 00 01 80 ...) -> response with device type byte
- every write window gets an ACK frame (05 00 01 00 01), like the firmware
- power command (05 00 07 01 xx) updates FakePanel.power
- the link can be dropped (with or without disconnect callback)
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]

# Enable HA's event-loop protection like a real installation does, so
# blocking I/O inside the loop is reported ("Detected blocking call ...").
from homeassistant import block_async_io  # noqa: E402

import os  # noqa: E402

if os.environ.get("IPIXEL_NO_LOOP_PROTECTION") != "1":
    block_async_io._IN_TESTS = False  # also check open/scandir/listdir
    block_async_io.enable()

# device type byte -> panel size (pypixelcolor DEVICE_TYPE_MAP / LED_SIZE_MAP)
TYPE_96x16 = 132
CONNECT_DELAY = 0.3


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


class FakeBleakClient:
    def __init__(self, panel: "FakePanel", disconnected_callback):
        self.panel = panel
        self._cb = disconnected_callback
        self.is_connected = True
        self.mtu_size = 23
        self._notify = None
        self.writes: list[bytes] = []
        self._rx = b""

    async def start_notify(self, uuid, handler, **kwargs):
        self._notify = handler

    async def stop_notify(self, uuid):
        self._notify = None

    async def request_mtu(self, mtu):
        self.mtu_size = 247

    async def write_gatt_char(self, uuid, data, response=None):
        if not self.is_connected:
            from bleak.exc import BleakError

            raise BleakError("Not connected")
        data = bytes(data)
        self.writes.append(data)
        self.panel.log.append((time.monotonic(), data))
        await asyncio.sleep(0.002)
        # messages are length-prefixed (2 bytes LE, includes the prefix) and
        # may span several 244-byte writes
        self._rx += data
        while len(self._rx) >= 2:
            length = int.from_bytes(self._rx[:2], "little")
            if length < 4 or len(self._rx) < length:
                break
            msg, self._rx = self._rx[:length], self._rx[length:]
            self._handle_message(msg)

    def _handle_message(self, msg: bytes) -> None:
        if msg[:4] == bytes([8, 0, 1, 0x80]):
            self._emit(bytes([0x0B, 0, 1, 0x80, self.panel.device_type, 1, 2, 3, 4, 0, 255]))
            return
        if msg[:4] == bytes([5, 0, 7, 1]):
            self.panel.power = bool(msg[4])
        # bulk data window (text 00 01, image 02 00, GIF 03 00, mix 04 00, ...):
        # [len u16][type u16][option 00|02][total u32][crc u32][2 bytes][payload]
        if len(msg) > 15 and msg[4] in (0, 2):
            total = int.from_bytes(msg[5:9], "little")
            if 0 < total and len(msg) - 15 <= total:
                if msg[4] == 0:
                    self.panel.image_received = 0
                self.panel.image_received += len(msg) - 15
                self.panel.windows += 1
                if self.panel.image_received >= total:
                    self._emit(bytes([5, 0, 1, 0, 3]))  # final ACK
                    return
        self._emit(bytes([5, 0, 1, 0, 1]))  # window ACK

    def _emit(self, frame: bytes):
        if self._notify:
            asyncio.get_running_loop().call_soon(self._notify, None, bytearray(frame))

    async def disconnect(self):
        self.is_connected = False

    # test helper
    def drop(self, callback: bool = True):
        self.is_connected = False
        if callback and self._cb:
            self._cb(self)


class FakePanel:
    def __init__(self, address: str, device_type: int = TYPE_96x16):
        self.address = address
        self.device_type = device_type
        self.power: bool | None = None
        self.client: FakeBleakClient | None = None
        self.connects = 0
        self.log: list[tuple[float, bytes]] = []
        self.present = True
        self.image_received = 0
        self.windows = 0


@pytest.fixture
def panels():
    return {
        "AA:BB:CC:DD:EE:01": FakePanel("AA:BB:CC:DD:EE:01"),
        "AA:BB:CC:DD:EE:02": FakePanel("AA:BB:CC:DD:EE:02"),
    }


@pytest.fixture
def fake_ble(panels):
    """Patch HA bluetooth lookups and bleak-retry-connector."""

    def ble_device(hass, address, connectable=True):
        p = panels.get(address)
        if p and p.present:
            return SimpleNamespace(address=address, name=f"LED_BLE_{address[-2:]}")
        return None

    async def establish_connection(cls, device, name, disconnected_callback=None, **kw):
        p = panels[device.address]
        await asyncio.sleep(CONNECT_DELAY)
        p.connects += 1
        p.client = FakeBleakClient(p, disconnected_callback)
        return p.client

    base = "custom_components.ipixel_color.bluetooth.client"
    with (
        patch(f"{base}.bluetooth.async_ble_device_from_address", side_effect=ble_device),
        patch(
            f"{base}.bluetooth.async_address_present",
            side_effect=lambda h, a, connectable=True: panels[a].present,
        ),
        patch(f"{base}.bluetooth.async_register_callback", return_value=lambda: None),
        patch(f"{base}.bluetooth.async_rediscover_address", return_value=None),
        patch(f"{base}.establish_connection", side_effect=establish_connection),
        patch("custom_components.ipixel_color._async_register_frontend", return_value=None),
    ):
        yield panels
