"""Bluetooth client management for iPIXEL Color devices.

Combines
- the robust connection handling of this fork (per-device lock, real link
  state, reconnect + retry, keepalive, single notification subscription,
  BlueZ StartNotify, rediscovery), and
- the windowed transport interface of ahzs645/ha-ipixel-color
  (send_command / send_plan returning CommandResult, 244-byte chunks,
  per-window ACK, response handlers, MTU 512 request, device info cached
  at connect).

Only ONE notification subscription exists per connection. ACK frames and
command responses are dispatched from it, so there is no stop/start_notify
per command (which was slow and raced with parallel commands).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import timedelta
from typing import Any, Awaitable, Callable, Optional, TYPE_CHECKING

from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

from homeassistant.components import bluetooth
from homeassistant.core import callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

try:
    from pypixelcolor.lib.transport.send_plan import SendPlan, single_window_plan
    from pypixelcolor.lib.command_result import CommandResult
    from pypixelcolor.lib.device_info import DeviceInfo
except ImportError:  # pragma: no cover
    SendPlan = None
    single_window_plan = None
    CommandResult = None
    DeviceInfo = None

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import WRITE_UUID, NOTIFY_UUID, BLE_REQUESTED_MTU
from ..exceptions import iPIXELConnectionError
from ..device.info import build_device_info_command, handle_device_info_response

_LOGGER = logging.getLogger(__name__)

DEFAULT_ACK_TIMEOUT = 30.0
RESPONSE_TIMEOUT = 5.0
KEEPALIVE_INTERVAL = timedelta(seconds=60)
RECONNECT_BACKOFF = (1, 3, 5, 10, 30, 60)


class _TransferState:
    """ACK / response events of the transfer currently in progress."""

    def __init__(self) -> None:
        self.window = asyncio.Event()
        self.final = asyncio.Event()
        self.response = asyncio.Event()
        self.response_data: bytes | None = None
        self.expect_response = False

    def reset_window(self) -> None:
        self.window.clear()


class BluetoothClient:
    """Manages Bluetooth connection and communication."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        self._hass = hass
        self._address = address
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._xfer: _TransferState | None = None
        self._device_info: Optional[DeviceInfo] = None
        self._closing = False
        self._reconnect_task: asyncio.Task | None = None
        self._backoff_idx = 0
        self._unsub: list[Callable[[], None]] = []
        # (width, height) from the options flow; overrides firmware values
        self.dimension_override: tuple[int, int] | None = None
        # extra listener for raw notifications (debugging, services)
        self.notification_handler: Callable[[Any, bytearray], None] | None = None
        # async hook, called (outside the lock) after a background reconnect
        self.on_reconnected: Callable[[], Awaitable[None]] | None = None
        # sync hook, called whenever link state changes
        self.on_state_change: Callable[[], None] | None = None
        # "release for app": HA drops the link and stays off it until this
        # loop time, so the phone app can connect (panel takes ONE link)
        self._released_until: float = 0.0
        self._release_unsub: Callable[[], None] | None = None

    # ------------------------------------------------------------------ state
    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def address(self) -> str:
        return self._address

    @property
    def device_info(self) -> Optional[DeviceInfo]:
        return self._device_info

    @property
    def is_present(self) -> bool:
        """Device currently seen by any adapter / proxy."""
        return bluetooth.async_address_present(
            self._hass, self._address, connectable=True
        )

    # --------------------------------------------------------- notifications
    def _handle_notify(self, sender: Any, data: bytearray) -> None:
        data = bytes(data)
        _LOGGER.debug("%s RX: %s", self._address, data.hex())
        xfer = self._xfer
        if xfer is not None:
            # same semantics as pypixelcolor: for a command with a response
            # handler the first frame is the response; ACK frames
            # (05 xx xx xx code) additionally drive the window/final events
            if xfer.expect_response and not xfer.response.is_set():
                xfer.response_data = data
                xfer.response.set()
            if len(data) >= 5 and data[0] == 0x05:
                if data[4] in (0, 1):
                    xfer.window.set()
                elif data[4] == 3:
                    xfer.window.set()
                    xfer.final.set()
        if self.notification_handler:
            try:
                self.notification_handler(sender, bytearray(data))
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

    async def _ensure_connected(self) -> None:
        """Connect if needed. Caller must hold self._lock."""
        if self.is_connected:
            return

        await self._force_drop()

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

        await self._request_mtu()
        self._backoff_idx = 0
        _LOGGER.info("Connected to iPIXEL %s", self._address)

        if self._device_info is None:
            await self._load_device_info()

        if self.on_state_change:
            self.on_state_change()

    async def _request_mtu(self) -> None:
        """Request MTU 512 like the official app (non-fatal)."""
        try:
            if self._client.mtu_size < BLE_REQUESTED_MTU and hasattr(
                self._client, "request_mtu"
            ):
                await self._client.request_mtu(BLE_REQUESTED_MTU)
                _LOGGER.debug("%s MTU negotiated to %d", self._address, self._client.mtu_size)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s MTU negotiation failed (non-fatal): %s", self._address, err)

    async def _load_device_info(self) -> None:
        """Query device info (3 attempts). Caller holds lock, link is up."""
        last_err: Exception | None = None
        for attempt in range(1, 4):
            try:
                plan = single_window_plan(
                    "get_device_info",
                    build_device_info_command(),
                    requires_ack=False,
                    response_handler=handle_device_info_response,
                )
                result = await self._send_plan_locked(plan, RESPONSE_TIMEOUT)
                if result.success and result.data is not None:
                    self._device_info = self._apply_override(result.data)
                    _LOGGER.info(
                        "%s device info: %sx%s (type %s)",
                        self._address,
                        self._device_info.width,
                        self._device_info.height,
                        self._device_info.led_type,
                    )
                    return
                last_err = RuntimeError(result.message or "no device info")
            except Exception as err:  # noqa: BLE001
                last_err = err
            _LOGGER.warning(
                "%s: device info attempt %d/3 failed: %s", self._address, attempt, last_err
            )
            await asyncio.sleep(1)

        if self.dimension_override:
            w, h = self.dimension_override
            _LOGGER.warning(
                "%s: using configured dimensions %dx%d without device info", self._address, w, h
            )
            self._device_info = DeviceInfo(
                device_type=0, mcu_version="Unknown", wifi_version="Unknown",
                width=w, height=h, has_wifi=False, password_flag=255,
            )
            return
        await self._force_drop()
        raise iPIXELConnectionError(f"Device info not available: {last_err}")

    def _apply_override(self, info: DeviceInfo) -> DeviceInfo:
        if not self.dimension_override:
            return info
        w, h = self.dimension_override
        if (info.width, info.height) != (w, h):
            _LOGGER.info(
                "%s: firmware reports %dx%d, using configured %dx%d",
                self._address, info.width, info.height, w, h,
            )
        return dataclasses.replace(info, width=w, height=h)

    async def connect(self, notification_handler: Callable | None = None) -> DeviceInfo:
        """Connect (idempotent, locked). Returns the cached DeviceInfo."""
        self._closing = False
        if notification_handler is not None:
            self.notification_handler = notification_handler
        async with self._lock:
            await self._ensure_connected()
        return self._device_info

    async def disconnect(self) -> None:
        """Disconnect and stop keepalive."""
        self._closing = True
        self.stop_keepalive()
        if self._release_unsub:  # unload during an app release
            self._release_unsub()
            self._release_unsub = None
        self._released_until = 0.0
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
    async def _send_plan_locked(self, plan: SendPlan, ack_timeout: float) -> CommandResult:
        """Send all windows of a plan. Caller holds lock, link is up."""
        xfer = _TransferState()
        xfer.expect_response = plan.response_handler is not None
        self._xfer = xfer
        try:
            for win in plan.windows:
                xfer.reset_window()
                for pos in range(0, len(win.data), plan.chunk_size):
                    await self._client.write_gatt_char(
                        WRITE_UUID, win.data[pos:pos + plan.chunk_size], response=True
                    )
                if plan.ack_policy.ack_per_window and win.requires_ack:
                    try:
                        await asyncio.wait_for(xfer.window.wait(), ack_timeout)
                    except asyncio.TimeoutError:
                        return CommandResult(
                            success=False, message="no window ACK from device"
                        )
            if plan.ack_policy.ack_final:
                try:
                    await asyncio.wait_for(xfer.final.wait(), ack_timeout)
                except asyncio.TimeoutError:
                    return CommandResult(success=False, message="no final ACK from device")

            if plan.response_handler is not None:
                try:
                    await asyncio.wait_for(xfer.response.wait(), ack_timeout)
                except asyncio.TimeoutError:
                    return CommandResult(success=False, message="no response from device")
                data = await plan.response_handler(self._client, xfer.response_data)
                return CommandResult(success=True, data=data)
            return CommandResult(success=True)
        finally:
            self._xfer = None

    async def send_plan(
        self, plan: SendPlan, ack_timeout: float = DEFAULT_ACK_TIMEOUT
    ) -> CommandResult:
        """Send a pypixelcolor SendPlan.

        Reconnects if needed and retries the whole plan once after a link
        error. Raises iPIXELConnectionError if the device can't be reached.
        """
        if SendPlan is None:
            raise ImportError("pypixelcolor library is not installed")
        if self.is_released:
            # do NOT grab the link back while the app is using it
            raise iPIXELConnectionError("released for app, not connecting")
        async with self._lock:
            last_err: Exception | None = None
            for attempt in (1, 2):
                try:
                    await self._ensure_connected()
                    _LOGGER.debug("%s: sending plan '%s'", self._address, plan.id)
                    return await self._send_plan_locked(plan, ack_timeout)
                except iPIXELConnectionError as err:
                    last_err = err
                    if attempt == 1:
                        await asyncio.sleep(0.5)
                except Exception as err:  # noqa: BLE001  (BleakError, EOF, ...)
                    last_err = err
                    _LOGGER.warning(
                        "%s: plan '%s' failed (attempt %d): %s",
                        self._address, plan.id, attempt, err,
                    )
                    await self._force_drop()
            raise iPIXELConnectionError(str(last_err))

    async def send_command(
        self,
        plan_id: str,
        data: bytes,
        response_handler: Optional[Callable[[Any, bytes], Awaitable[Any]]] = None,
        requires_ack: bool = False,
    ) -> CommandResult:
        """Send a single command (ahzs645 interface).

        Returns CommandResult; connection failures are returned as
        success=False instead of raising, like before.
        """
        if single_window_plan is None:
            raise ImportError("pypixelcolor library is not installed")
        plan = single_window_plan(
            plan_id=plan_id,
            data=data,
            requires_ack=requires_ack,
            response_handler=response_handler,
        )
        try:
            return await self.send_plan(plan)
        except iPIXELConnectionError as err:
            _LOGGER.error("%s: '%s' failed: %s", self._address, plan_id, err)
            return CommandResult(success=False, message=str(err))

    # ------------------------------------------------------- release for app
    @property
    def is_released(self) -> bool:
        return self._released_until > self._hass.loop.time()

    @property
    def released_seconds_left(self) -> int:
        return max(0, int(self._released_until - self._hass.loop.time()))

    async def release(self, seconds: float) -> None:
        """Drop the link and stay off it for `seconds` (phone app access).

        Keepalive callbacks stay registered but are blocked via `_closing`;
        commands sent meanwhile fail instead of reconnecting. When the time
        is up (or end_release() is called) the link is re-established and
        on_reconnected re-applies HA's desired power state.
        """
        self._released_until = self._hass.loop.time() + seconds
        self._closing = True
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._release_unsub:
            self._release_unsub()
        self._release_unsub = async_call_later(
            self._hass, seconds, self._release_timer_done
        )
        async with self._lock:
            await self._force_drop()
        _LOGGER.info("%s: released for app for %ds", self._address, int(seconds))
        if self.on_state_change:
            self.on_state_change()

    @callback
    def _release_timer_done(self, _now) -> None:  # noqa: ANN001
        self._release_unsub = None
        self._hass.async_create_background_task(
            self.end_release(), f"ipixel_end_release_{self._address}"
        )

    async def end_release(self) -> None:
        """Take the link back now (also called when the release expires)."""
        if self._release_unsub:
            self._release_unsub()
            self._release_unsub = None
        was_released = self._released_until > 0
        self._released_until = 0.0
        self._closing = False
        if not was_released:
            return
        _LOGGER.info("%s: app release ended, reconnecting", self._address)
        if self.on_state_change:
            self.on_state_change()
        self._backoff_idx = 0
        self._schedule_reconnect(immediate=True)

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
