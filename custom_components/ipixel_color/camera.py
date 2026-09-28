"""Camera platform for iPIXEL Color live display preview.

Shows the last frame sent to the display, providing a live preview
of what's currently being shown on the device.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import iPIXELAPI
from .const import DOMAIN, CONF_ADDRESS, CONF_NAME

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the iPIXEL Camera entity."""
    address = entry.data[CONF_ADDRESS]
    name = entry.data[CONF_NAME]
    api = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([iPIXELCamera(api, entry, address, name)])


class iPIXELCamera(Camera):
    """Camera entity showing last frame sent to display.

    This camera provides a live preview of what's currently being
    displayed on the iPIXEL device. It retrieves the last frame
    sent via the draw_visuals service or any other display command.
    """

    _attr_is_streaming = False
    _attr_supported_features = 0

    def __init__(
        self,
        api: iPIXELAPI,
        entry: ConfigEntry,
        address: str,
        name: str
    ) -> None:
        """Initialize camera.

        Args:
            api: iPIXEL API client
            entry: Config entry
            address: Device Bluetooth address
            name: Device name
        """
        super().__init__()
        self._api = api
        self._entry = entry
        self._address = address

        self._attr_name = "Display Preview"
        self._attr_unique_id = f"{address}_camera_preview"

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, address)},
            name=name,
            manufacturer="iPIXEL",
            model="LED Matrix Display",
        )

    async def async_camera_image(
        self,
        width: int | None = None,
        height: int | None = None
    ) -> bytes | None:
        """Return last frame as PNG image.

        This is called by Home Assistant when the camera image is requested,
        such as when viewing the camera in the dashboard or taking a snapshot.

        Args:
            width: Optional desired width (ignored, uses native resolution)
            height: Optional desired height (ignored, uses native resolution)

        Returns:
            PNG image bytes or None if no frame available
        """
        return self._api.get_last_frame_png()

    @property
    def frame_interval(self) -> float:
        """Return interval between frames.

        This determines how often the camera preview refreshes.
        A shorter interval provides more responsive preview but
        increases network traffic.
        """
        return 0.1  # 10 FPS refresh rate for preview

    @property
    def available(self) -> bool:
        """Return True if camera is available.

        Camera is always available as it shows cached frames,
        even if the device is temporarily disconnected.
        """
        return True
