"""Bluetooth client management for iPIXEL Color devices.

Patched version (robust connection handling):
- one asyncio.Lock per device: connect + GATT I/O are never interleaved
- real link state (BleakClient.is_connected) instead of a stale flag
- automatic reconnect + one retry per command
- keepalive: reconnects in the background as soon as the panel advertises
  again (or every KEEPALIVE_INTERVAL), so a switch command finds an open
  link and both panels react at the same moment
- notifications are enabled exactly once per connection (no stop/start
  dance per command, which was slow and raced with parallel commands)
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import WRITE_UUID, NOTIFY_UUID
from ..exceptions import iPIXELConnectionError

_LOGGER = logging.getLogger(__name__)

RESPONSE_TIMEOUT = 2.0
KEEPALIVE_INTERVAL = timedelta(seconds=60)
RECONNECT_BACKOFF = (1, 3, 5, 10, 30, 60)


class BluetoothClient:
    """Manages Bluetooth connection and communication."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self._hass = hass
        self._address = address
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._notification_handler: Callable | None = None
        self._response_event: asyncio.Event | None = None
        self._last_response: bytes | None = None
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self._backoff_idx = 0
        self._unsub: list[Callable[[], None]] = []
        # async hook, called (outside the lock) after a background reconnect
        self.on_reconnected: Callable[[], Awaitable[None]] | None = None
        # sync hook, called whenever link state changes
        self.on_state_change: Callable[[], None] | None = None

    # ------------------------------------------------------------------ state
    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def address(self) -> str:
        return self._address

    @property
    def is_present(self) -> bool:
        """Device currently seen by any adapter / proxy."""
        return bluetooth.async_address_present(
            self._hass, self._address, connectable=True
        )

    # --------------------------------------------------------- notifications
    def _handle_notify(self, sender: Any, data: bytearray) -> None:
        self._last_response = bytes(data)
        if self._response_event is not None:
            self._response_event.set()
        if self._notification_handler:
            try:
                self._notification_handler(sender, data)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Notification handler failed")

    @callback
    def _disconnected_callback(self, client: BleakClientWithServiceCache) -> None:
        if client is not self._client:
            return  # stale client object, ignore
        _LOGGER.warning("iPIXEL %s disconnected", self._address)
        if self.on_state_change:
            self.on_state_change()
        self._schedule_reconnect()

    # ------------------------------------------------------------ connection
    async def _ensure_connected(self) -> None:
        """Connect if needed. Caller must hold self._lock."""
        if self.is_connected:
            return

        # drop a dead client object cleanly
        if self._client is not None:
            old, self._client = self._client, None
            try:
                await old.disconnect()
            except Exception:  # noqa: BLE001
                pass

        ble_device = await self._get_ble_device()
        if not ble_device:
            raise iPIXELConnectionError(
                f"Device {self._address} not found (powered off / out of range?)"
            )

        _LOGGER.debug("Connecting to %s", self._address)
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                ble_device.name or "iPIXEL Display",
                disconnected_callback=self._disconnected_callback,
                max_attempts=3,
            )
            self._client = client
            # bleak >= 1.0 on BlueZ uses AcquireNotify by default, which the
            # panel rejects ("Notify acquired"); force StartNotify. Other
            # backends (ESPHome proxy) ignore the bluez kwarg.
            await client.start_notify(
                NOTIFY_UUID, self._handle_notify, bluez={"use_start_notify": True}
            )
        except Exception as err:  # noqa: BLE001
            await self._force_drop()
            raise iPIXELConnectionError(f"Connection failed: {err}") from err

        self._backoff_idx = 0
        _LOGGER.info("Connected to iPIXEL %s", self._address)
        if self.on_state_change:
            self.on_state_change()

    async def _get_ble_device(self):
        """Return the BLEDevice; trigger rediscovery if HA lost track of it.

        After a link drop HA's Bluetooth manager may briefly not report the
        device as connectable although it is advertising again.
        """
        dev = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if dev or not hasattr(bluetooth, "async_rediscover_address"):
            return dev
        _LOGGER.debug("%s not in Bluetooth cache, triggering rediscovery", self._address)
        bluetooth.async_rediscover_address(self._hass, self._address)
        for _ in range(3):
            await asyncio.sleep(1.0)
            dev = bluetooth.async_ble_device_from_address(
                self._hass, self._address, connectable=True
            )
            if dev:
                return dev
        return None

    async def connect(
        self, notification_handler: Callable[[Any, bytearray], None] | None = None
    ) -> bool:
        """Connect (idempotent, locked)."""
        self._closing = False
        if notification_handler is not None:
            self._notification_handler = notification_handler
        async with self._lock:
            await self._ensure_connected()
        return True

    async def disconnect(self) -> None:
        """Disconnect and stop keepalive."""
        self._closing = True
        self.stop_keepalive()
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        async with self._lock:
            await self._force_drop()

    async def _force_drop(self) -> None:
        """Drop the current link. Caller holds lock."""
        old, self._client = self._client, None
        if old:
            try:
                await old.disconnect()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------- I/O
    async def request(
        self, command: bytes, timeout: float = RESPONSE_TIMEOUT
    ) -> bytes | None:
        """Send command, return first notification received (or None).

        Reconnects and retries once on failure.
        Raises iPIXELConnectionError if the device can't be reached.
        """
        async with self._lock:
            last_err: Exception | None = None
            for attempt in (1, 2):
                try:
                    await self._ensure_connected()
                    self._response_event = asyncio.Event()
                    self._last_response = None
                    _LOGGER.debug("%s TX: %s", self._address, command.hex())
                    await self._client.write_gatt_char(WRITE_UUID, command)
                    if timeout > 0:
                        try:
                            await asyncio.wait_for(
                                self._response_event.wait(), timeout=timeout
                            )
                        except asyncio.TimeoutError:
                            _LOGGER.debug("%s: no response within %.1fs",
                                          self._address, timeout)
                    return self._last_response
                except iPIXELConnectionError as err:
                    last_err = err
                    if attempt == 1:
                        await asyncio.sleep(0.5)
                except Exception as err:  # noqa: BLE001  (BleakError, EOF, ...)
                    last_err = err
                    _LOGGER.warning(
                        "%s: write failed (attempt %d): %s", self._address, attempt, err
                    )
                    await self._force_drop()
                finally:
                    self._response_event = None
            raise iPIXELConnectionError(str(last_err))

    async def send_command(self, command: bytes) -> bool:
        """Send command. True on success, False on failure (API compatible)."""
        try:
            await self.request(command)
            return True
        except iPIXELConnectionError as err:
            _LOGGER.error("Failed to send command to %s: %s", self._address, err)
            return False

    # ------------------------------------------------------------- keepalive
    def start_keepalive(self) -> None:
        """Keep the link open: reconnect on advertisement / periodically."""
        self.stop_keepalive()
        self._closing = False

        @callback
        def _adv(service_info, change) -> None:  # noqa: ANN001
            if not self.is_connected:
                self._schedule_reconnect(immediate=True)

        self._unsub.append(
            bluetooth.async_register_callback(
                self._hass,
                _adv,
                bluetooth.BluetoothCallbackMatcher(
                    address=self._address, connectable=True
                ),
                bluetooth.BluetoothScanningMode.PASSIVE,
            )
        )

        @callback
        def _tick(_now) -> None:  # noqa: ANN001
            if not self.is_connected:
                self._schedule_reconnect(immediate=True)

        self._unsub.append(
            async_track_time_interval(self._hass, _tick, KEEPALIVE_INTERVAL)
        )

    def stop_keepalive(self) -> None:
        while self._unsub:
            self._unsub.pop()()

    @callback
    def _schedule_reconnect(self, immediate: bool = False) -> None:
        if self._closing:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        # backoff also applies to advertisement triggers -> no connect storm
        delay = RECONNECT_BACKOFF[min(self._backoff_idx, len(RECONNECT_BACKOFF) - 1)]
        if immediate and self._backoff_idx == 0:
            delay = 0
        self._reconnect_task = self._hass.async_create_background_task(
            self._reconnect(delay), f"ipixel_reconnect_{self._address}"
        )

    async def _reconnect(self, delay: float) -> None:
        if delay:
            await asyncio.sleep(delay)
        if self._closing or self.is_connected:
            return
        try:
            async with self._lock:
                if self.is_connected:  # someone else was faster
                    return
                await self._ensure_connected()
        except iPIXELConnectionError as err:
            self._backoff_idx += 1
            _LOGGER.debug("Background reconnect %s failed: %s", self._address, err)
            # adv callback / tick trigger the next attempt anyway
            return
        if self.on_reconnected:
            try:
                await self.on_reconnected()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("on_reconnected hook failed")
