"""iPIXEL Color Bluetooth API client - Refactored version."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, TYPE_CHECKING

from homeassistant.core import callback

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .bluetooth.client import BluetoothClient
from .device.commands import (
    make_power_command,
    make_brightness_command,
)
from .device.clock import make_clock_mode_command, make_time_command
from .device.text import make_text_command
from .device.image import make_image_command
from .device.info import build_device_info_command, parse_device_response
from .display.text_renderer import render_text_to_png
from .exceptions import iPIXELConnectionError

_LOGGER = logging.getLogger(__name__)

DEVICE_INFO_RETRY_AFTER = 300  # seconds
UNSAFE_TEXT_ANIMATIONS = frozenset({3, 4})
DEFAULT_DEVICE_INFO: dict[str, Any] = {
    "width": 64,
    "height": 16,
    "device_type": 0,
    "device_type_str": "Unknown",
    "led_type": 0,
    "mcu_version": "Unknown",
    "wifi_version": "Unknown",
    "has_wifi": False,
    "password_flag": 255,
}


class iPIXELAPI:
    """iPIXEL Color device API client - simplified facade."""

    def __init__(self, hass: HomeAssistant, address: str) -> None:
        """Initialize the API client.

        Args:
            hass: Home Assistant instance
            address: Bluetooth MAC address
        """
        self._address = address
        self._bluetooth = BluetoothClient(hass, address)
        self._power_state = False
        # state HA wants the panel to be in (restored across restarts);
        # re-applied automatically after every (re)connect
        self.desired_power: bool | None = None
        self._listeners: list[Callable[[], None]] = []
        self._device_info: dict[str, Any] | None = None
        self._device_response: bytes | None = None
        self._device_info_failed_at = float("-inf")
        self._bluetooth.on_state_change = self._notify_listeners
        self._bluetooth.on_reconnected = self.apply_desired_power

    # ---------------------------------------------------------- listeners
    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --------------------------------------------------------- connection
    async def connect(self) -> bool:
        """Connect to the iPIXEL device."""
        return await self._bluetooth.connect(self._notification_handler)

    async def disconnect(self) -> None:
        """Disconnect from the device."""
        await self._bluetooth.disconnect()

    def start_keepalive(self) -> None:
        self._bluetooth.start_keepalive()

    @property
    def is_present(self) -> bool:
        return self._bluetooth.is_present

    # -------------------------------------------------------------- power
    async def set_power(self, on: bool) -> bool:
        """Set device power state (reconnects + retries internally)."""
        self.desired_power = on
        success = await self._bluetooth.send_command(make_power_command(on))
        if success:
            self._power_state = on
            _LOGGER.debug("%s power set to %s", self._address, "ON" if on else "OFF")
        self._notify_listeners()
        return success

    async def apply_desired_power(self) -> None:
        """Push the desired power state to the panel (after reconnect)."""
        if self.desired_power is None:
            return
        _LOGGER.debug("%s: re-applying power=%s", self._address, self.desired_power)
        await self.set_power(self.desired_power)
    
    async def set_brightness(self, brightness: int) -> bool:
        """Set device brightness level.
        
        Args:
            brightness: Brightness level from 1 to 100
            
        Returns:
            True if command was sent successfully
        """
        try:
            command = make_brightness_command(brightness)
            success = await self._bluetooth.send_command(command)
            
            if success:
                _LOGGER.debug("Brightness set to %d", brightness)
            else:
                _LOGGER.error("Failed to set brightness to %d", brightness)
            return success
            
        except ValueError as err:
            _LOGGER.error("Invalid brightness value: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Error setting brightness: %s", err)
            return False

    async def sync_time(self) -> bool:
        """Sync current time to the device.

        This is useful for keeping the clock display accurate,
        especially after the device has been running for a while.

        Returns:
            True if time was synced successfully
        """
        try:
            time_command = make_time_command()
            success = await self._bluetooth.send_command(time_command)

            if success:
                _LOGGER.debug("Time synchronized to device")
            else:
                _LOGGER.error("Failed to sync time")
            return success

        except Exception as err:
            _LOGGER.error("Error syncing time: %s", err)
            return False

    async def set_clock_mode(
        self,
        style: int = 1,
        date: str = "",
        show_date: bool = True,
        format_24: bool = True
    ) -> bool:
        """Set device to clock display mode.

        Args:
            style: Clock style (0-8)
            date: Date in DD/MM/YYYY format (defaults to today)
            show_date: Whether to show the date
            format_24: Whether to use 24-hour format

        Returns:
            True if command was sent successfully
        """
        try:
            # Set clock mode
            command = make_clock_mode_command(style, date, show_date, format_24)
            success = await self._bluetooth.send_command(command)

            if not success:
                _LOGGER.error("Failed to set clock mode")
                return False

            _LOGGER.info("Clock mode set: style=%d, 24h=%s, show_date=%s",
                       style, format_24, show_date)

            # Sync current time to the device
            time_success = await self.sync_time()
            if not time_success:
                _LOGGER.warning("Clock mode set but time sync failed")

            return success

        except ValueError as err:
            _LOGGER.error("Invalid clock mode parameters: %s", err)
            return False
        except Exception as err:
            _LOGGER.error("Error setting clock mode: %s", err)
            return False
    
    async def get_device_info(self) -> dict[str, Any] | None:
        """Query device information and store it.

        Retries up to 3 times. A failed query is NOT cached (so a later call
        can still obtain the real values), but is rate-limited to one retry
        cycle per DEVICE_INFO_RETRY_AFTER seconds to keep text updates fast.
        """
        if self._device_info is not None:
            return self._device_info

        now = time.monotonic()
        if now - self._device_info_failed_at < DEVICE_INFO_RETRY_AFTER:
            return dict(DEFAULT_DEVICE_INFO)

        for attempt in range(1, 4):
            try:
                response = await self._bluetooth.request(
                    build_device_info_command(), timeout=5.0
                )
                if not response:
                    raise iPIXELConnectionError("No response received")
                self._device_response = response
                self._device_info = parse_device_response(response)
                _LOGGER.info("Device info retrieved: %s", self._device_info)
                return self._device_info
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Failed to get device info (attempt %d/3): %s", attempt, err
                )
                if attempt < 3:
                    await asyncio.sleep(1)

        _LOGGER.error("Failed to get device info after 3 attempts, using defaults")
        self._device_info_failed_at = time.monotonic()
        return dict(DEFAULT_DEVICE_INFO)

    async def display_text(self, text: str, antialias: bool = True, font_size: float | None = None, font: str | None = None, line_spacing: int = 0, text_color: str = "ffffff", bg_color: str = "000000") -> bool:
        """Display text as image using PIL and pypixelcolor with color gradient mapping.

        Args:
            text: Text to display (supports multiline with \n)
            antialias: Enable text antialiasing for smoother rendering
            font_size: Fixed font size in pixels (can be fractional), or None for auto-sizing
            font: Font name from fonts/ folder, or None for default
            line_spacing: Additional spacing between lines in pixels
            text_color: Foreground/text color in hex format (e.g., 'ffffff')
            bg_color: Background color in hex format (e.g., '000000')
        """
        try:
            # Get device dimensions
            device_info = await self.get_device_info()
            width = device_info["width"]
            height = device_info["height"]

            # Render text to PNG with color gradient
            png_data = render_text_to_png(text, width, height, antialias, font_size, font, line_spacing, text_color, bg_color)

            # Generate image commands using pypixelcolor
            commands = make_image_command(
                image_bytes=png_data,
                file_extension=".png",
                resize_method="crop",
                device_info_dict=device_info
            )

            # Send all command frames
            for i, command in enumerate(commands):
                _LOGGER.debug(
                    "Sending pypixelcolor image frame %d/%d: %d bytes",
                    i + 1,
                    len(commands),
                    len(command)
                )
                success = await self._bluetooth.send_command(command)
                if not success:
                    _LOGGER.error("Failed to send image frame %d/%d", i + 1, len(commands))
                    return False

            _LOGGER.info(
                "Text rendered as image: '%s' (%dx%d, %d bytes PNG, %d frames)",
                text,
                width,
                height,
                len(png_data),
                len(commands)
            )
            return True

        except Exception as err:
            _LOGGER.error("Error displaying text: %s", err)
            return False

    async def display_text_pypixelcolor(
        self,
        text: str,
        color: str = "ffffff",
        bg_color: str | None = None,
        font: str = "CUSONG",
        animation: int = 0,
        speed: int = 80,
        rainbow_mode: int = 0
    ) -> bool:
        """Display text using pypixelcolor.

        Args:
            text: Text to display (supports emojis)
            color: Text color in hex format (e.g., 'ffffff')
            bg_color: Background color in hex format (e.g., '000000'), or None for transparent
            font: Font name ('CUSONG', 'SIMSUN', 'VCR_OSD_MONO') or file path
            animation: Animation type (0-7)
            speed: Animation speed (0-100)
            rainbow_mode: Rainbow mode (0-9)

        Returns:
            True if text was sent successfully
        """
        try:
            # Get device info for height
            device_info = await self.get_device_info()
            device_height = device_info["height"]

            # Text animations 3 and 4 boot-loop panels that are not 32x32.
            # pypixelcolor only checks this when it gets device_info, which
            # is not passed here -> guard explicitly (unknown size = unsafe).
            if int(animation) in UNSAFE_TEXT_ANIMATIONS and (
                device_info.get("width"), device_info.get("height")
            ) != (32, 32):
                _LOGGER.error(
                    "Text animation %s refused: boot-loops %sx%s panels, using 0",
                    animation, device_info.get("width"), device_info.get("height"),
                )
                animation = 0

            # Generate text commands using pypixelcolor
            commands = make_text_command(
                text=text,
                color=color,
                bg_color=bg_color,
                font=font,
                animation=animation,
                speed=speed,
                rainbow_mode=rainbow_mode,
                save_slot=0,
                device_height=device_height
            )

            # Send all command frames
            for i, command in enumerate(commands):
                _LOGGER.debug(
                    "Sending pypixelcolor text frame %d/%d: %d bytes",
                    i + 1,
                    len(commands),
                    len(command)
                )
                success = await self._bluetooth.send_command(command)
                if not success:
                    _LOGGER.error("Failed to send text frame %d/%d", i + 1, len(commands))
                    return False

            _LOGGER.info(
                "Pypixelcolor text sent: '%s' (color=%s, bg=%s, font=%s, anim=%d, speed=%d, frames=%d)",
                text,
                color,
                bg_color or "none",
                font,
                animation,
                speed,
                len(commands)
            )
            return True

        except Exception as err:
            _LOGGER.error("Error displaying pypixelcolor text: %s", err)
            return False

    def _notification_handler(self, sender: Any, data: bytearray) -> None:
        """Handle notifications from the device."""
        _LOGGER.debug("Notification from %s: %s", sender, data.hex())
    
    @property
    def is_connected(self) -> bool:
        """Return True if connected to device."""
        return self._bluetooth.is_connected
    
    @property
    def power_state(self) -> bool:
        """Return current power state."""
        return self._power_state
    
    @property
    def address(self) -> str:
        """Return device address."""
        return self._address


# Export at module level for convenience
__all__ = ["iPIXELAPI", "iPIXELError", "iPIXELConnectionError", "iPIXELTimeoutError"]
from .exceptions import iPIXELError, iPIXELConnectionError, iPIXELTimeoutError