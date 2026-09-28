"""Animation controller for draw_visuals service.

Manages animation loops with FPS control and frame timing.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from ..const import FPS_MIN, FPS_MAX, FPS_DEFAULT

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from ..api import iPIXELAPI
    from .visual_renderer import VisualRenderer, PreparedElement

_LOGGER = logging.getLogger(__name__)


class AnimationController:
    """Manages animation loops with FPS control.

    Handles rendering and sending frames to the device at a target FPS,
    with support for frame diffing to reduce BLE traffic.
    """

    def __init__(
        self,
        hass: "HomeAssistant",
        api: "iPIXELAPI",
        renderer: "VisualRenderer"
    ) -> None:
        """Initialize animation controller.

        Args:
            hass: Home Assistant instance
            api: iPIXEL API client
            renderer: Visual renderer instance
        """
        self._hass = hass
        self._api = api
        self._renderer = renderer
        self._running = False
        self._task: asyncio.Task | None = None
        self._elements: list["PreparedElement"] = []
        self._bg_color: tuple[int, int, int] = (0, 0, 0)
        self._fps: int = FPS_DEFAULT
        self._brightness: int = 100
        self._start_time: float = 0.0

    @property
    def is_running(self) -> bool:
        """Check if animation is currently running."""
        return self._running and self._task is not None

    async def start(
        self,
        elements: list["PreparedElement"],
        bg_color: tuple[int, int, int],
        fps: int = FPS_DEFAULT,
        brightness: int = 100
    ) -> None:
        """Start rendering elements.

        If elements require animation, starts an animation loop.
        Otherwise, renders a single static frame.

        Args:
            elements: List of prepared visual elements
            bg_color: Background RGB color
            fps: Target frames per second (clamped to FPS_MIN-FPS_MAX)
            brightness: Display brightness 1-100
        """
        # Stop any existing animation first
        await self.stop()

        self._elements = elements
        self._bg_color = bg_color
        self._fps = max(FPS_MIN, min(fps, FPS_MAX))
        self._brightness = brightness
        self._start_time = time.time()
        self._running = True

        # Clear frame cache to ensure first frame is sent
        self._api.clear_frame_cache()

        # Check if animation is needed
        if self._renderer.detect_animation(elements):
            _LOGGER.info(
                "Starting animation loop: %d elements, %d FPS",
                len(elements), self._fps
            )
            self._task = self._hass.async_create_task(
                self._animation_loop(),
                name="ipixel_animation_loop"
            )
        else:
            # Static render - single frame
            _LOGGER.info(
                "Rendering static frame: %d elements",
                len(elements)
            )
            await self._render_and_send(0.0)
            self._running = False

    async def stop(self) -> None:
        """Stop the animation loop."""
        self._running = False

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        _LOGGER.debug("Animation stopped")

    async def _animation_loop(self) -> None:
        """Main animation loop with frame timing."""
        frame_interval = 1.0 / self._fps
        frame_count = 0

        try:
            while self._running:
                loop_start = time.time()
                frame_time = loop_start - self._start_time

                # Render and send frame
                success = await self._render_and_send(frame_time)

                if not success:
                    _LOGGER.warning(
                        "Frame %d failed to send, waiting before retry",
                        frame_count
                    )
                    await asyncio.sleep(1.0)
                    continue

                frame_count += 1

                # Calculate sleep time to maintain target FPS
                elapsed = time.time() - loop_start
                sleep_time = max(0.01, frame_interval - elapsed)
                await asyncio.sleep(sleep_time)

        except asyncio.CancelledError:
            _LOGGER.debug("Animation loop cancelled after %d frames", frame_count)
            raise

        except Exception as err:
            _LOGGER.error("Animation loop error after %d frames: %s", frame_count, err)

        finally:
            self._running = False
            _LOGGER.info("Animation loop ended after %d frames", frame_count)

    async def _render_and_send(self, frame_time: float) -> bool:
        """Render frame and send to device.

        Args:
            frame_time: Current time in seconds since animation start

        Returns:
            True if frame was sent successfully
        """
        try:
            # Render frame
            frame = self._renderer.render_frame(
                self._elements,
                self._bg_color,
                frame_time
            )

            # Send with frame diffing
            return await self._api.display_frame_with_diff(
                frame,
                self._brightness
            )

        except Exception as err:
            _LOGGER.error("Error rendering/sending frame: %s", err)
            return False

    def update_elements(self, elements: list["PreparedElement"]) -> None:
        """Update elements without restarting animation.

        Useful for live updates to content while keeping animation running.

        Args:
            elements: New list of prepared elements
        """
        self._elements = elements

    def update_background(self, bg_color: tuple[int, int, int]) -> None:
        """Update background color without restarting.

        Args:
            bg_color: New background RGB color
        """
        self._bg_color = bg_color

    def update_brightness(self, brightness: int) -> None:
        """Update brightness without restarting.

        Args:
            brightness: New brightness 1-100
        """
        self._brightness = max(1, min(100, brightness))
