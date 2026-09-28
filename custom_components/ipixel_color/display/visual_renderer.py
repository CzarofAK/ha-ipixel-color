"""Visual element rendering engine for draw_visuals service.

Supports rendering multiple visual elements (text, textscroll, textlong,
icon, image, pixels) onto a single canvas with animation support.

Reference: UnexpectedMatrixPixels implementation
"""
from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from PIL import Image, ImageDraw, ImageFont

from ..color import hex_to_rgb
from ..fonts import get_font_path
from ..const import (
    ELEMENT_TEXT,
    ELEMENT_TEXTSCROLL,
    ELEMENT_TEXTLONG,
    ELEMENT_ICON,
    ELEMENT_IMAGE,
    ELEMENT_PIXELS,
)
from .font_cache import get_font_cache, FontCache

if TYPE_CHECKING:
    from aiohttp import ClientSession

_LOGGER = logging.getLogger(__name__)


@dataclass
class PreparedElement:
    """Base class for prepared visual elements."""

    type: str
    x: int = 0
    y: int = 0
    color: tuple[int, int, int] = (255, 255, 255)
    raw_data: dict = field(default_factory=dict)


@dataclass
class PreparedText(PreparedElement):
    """Prepared static text element."""

    type: str = ELEMENT_TEXT
    content: str = ""
    font_path: str | None = None
    font_size: int = 8
    spacing: int = 0
    # Pre-rendered image
    rendered_image: Image.Image | None = None


@dataclass
class PreparedTextScroll(PreparedElement):
    """Prepared scrolling text element."""

    type: str = ELEMENT_TEXTSCROLL
    content: str = ""
    font_path: str | None = None
    font_size: int = 8
    spacing: int = 0
    speed: float = 50.0  # pixels per second
    # Pre-rendered strip image (full text width)
    rendered_strip: Image.Image | None = None
    strip_width: int = 0


@dataclass
class PreparedTextLong(PreparedElement):
    """Prepared long text with word wrap and vertical scrolling."""

    type: str = ELEMENT_TEXTLONG
    content: str = ""
    font_path: str | None = None
    font_size: int = 8
    spacing: int = 0
    speed: float = 2.0  # seconds to hold each line
    scroll_duration: float = 0.5  # seconds for scroll animation
    direction: str = "up"  # up, down, left, right
    # Pre-computed
    wrapped_lines: list[str] = field(default_factory=list)
    rendered_lines: list[Image.Image] = field(default_factory=list)
    line_height: int = 8
    is_animated: bool = False


@dataclass
class PreparedIcon(PreparedElement):
    """Prepared MDI icon element."""

    type: str = ELEMENT_ICON
    icon_name: str = ""
    size: int = 16
    # Pre-rendered icon
    rendered_image: Image.Image | None = None


@dataclass
class PreparedImage(PreparedElement):
    """Prepared image element."""

    type: str = ELEMENT_IMAGE
    source: str = ""
    width: int | None = None
    height: int | None = None
    # Pre-loaded image
    loaded_image: Image.Image | None = None


@dataclass
class PreparedPixels(PreparedElement):
    """Prepared raw pixels element."""

    type: str = ELEMENT_PIXELS
    pixels: list[dict] = field(default_factory=list)


class VisualRenderer:
    """Renders visual elements to PIL images with animation support."""

    def __init__(
        self,
        width: int,
        height: int,
        font_cache: FontCache | None = None,
        session: Any = None
    ) -> None:
        """Initialize renderer.

        Args:
            width: Display width in pixels
            height: Display height in pixels
            font_cache: Font cache instance (uses global if None)
            session: aiohttp ClientSession for fetching images
        """
        self._width = width
        self._height = height
        self._font_cache = font_cache or get_font_cache()
        self._session = session
        # Character mask cache: (font_path, char, size) -> (mask, width)
        self._char_mask_cache: dict[tuple[str, str, int], tuple[Image.Image, int]] = {}

    @property
    def width(self) -> int:
        """Display width."""
        return self._width

    @property
    def height(self) -> int:
        """Display height."""
        return self._height

    async def prepare_elements(
        self,
        elements: list[dict],
        session: Any = None
    ) -> list[PreparedElement]:
        """Parse and pre-process elements before animation loop.

        Pre-renders static content, loads images, computes text wrapping.

        Args:
            elements: List of element dictionaries from service call
            session: aiohttp ClientSession for fetching images

        Returns:
            List of PreparedElement instances ready for rendering
        """
        if session:
            self._session = session

        prepared: list[PreparedElement] = []

        for el in elements:
            el_type = el.get("type", ELEMENT_TEXT)
            try:
                if el_type == ELEMENT_TEXT:
                    prepared.append(self._prepare_text(el))
                elif el_type == ELEMENT_TEXTSCROLL:
                    prepared.append(self._prepare_textscroll(el))
                elif el_type == ELEMENT_TEXTLONG:
                    prepared.append(self._prepare_textlong(el))
                elif el_type == ELEMENT_ICON:
                    prepared.append(self._prepare_icon(el))
                elif el_type == ELEMENT_IMAGE:
                    img_el = await self._prepare_image(el)
                    prepared.append(img_el)
                elif el_type == ELEMENT_PIXELS:
                    prepared.append(self._prepare_pixels(el))
                else:
                    _LOGGER.warning("Unknown element type: %s", el_type)
            except Exception as err:
                _LOGGER.error("Error preparing element %s: %s", el_type, err)

        return prepared

    def detect_animation(self, elements: list[PreparedElement]) -> bool:
        """Check if any element requires animation.

        Args:
            elements: List of prepared elements

        Returns:
            True if animation loop is needed
        """
        for el in elements:
            if isinstance(el, PreparedTextScroll):
                return True  # textscroll always animates
            if isinstance(el, PreparedTextLong) and el.is_animated:
                return True  # textlong animates if multi-line

        return False

    def render_frame(
        self,
        elements: list[PreparedElement],
        bg_color: tuple[int, int, int],
        frame_time: float
    ) -> Image.Image:
        """Render a single frame with all elements at current time.

        Args:
            elements: List of prepared elements
            bg_color: Background RGB color
            frame_time: Current time in seconds (for animations)

        Returns:
            PIL Image with rendered frame
        """
        # Create canvas with background
        canvas = Image.new("RGB", (self._width, self._height), bg_color)

        # Render each element in order (painter's algorithm)
        for el in elements:
            try:
                if isinstance(el, PreparedText):
                    self._render_text(canvas, el)
                elif isinstance(el, PreparedTextScroll):
                    self._render_textscroll(canvas, el, frame_time)
                elif isinstance(el, PreparedTextLong):
                    self._render_textlong(canvas, el, frame_time)
                elif isinstance(el, PreparedIcon):
                    self._render_icon(canvas, el)
                elif isinstance(el, PreparedImage):
                    self._render_image(canvas, el)
                elif isinstance(el, PreparedPixels):
                    self._render_pixels(canvas, el)
            except Exception as err:
                _LOGGER.error("Error rendering element %s: %s", el.type, err)

        return canvas

    def frame_to_png(self, frame: Image.Image) -> bytes:
        """Convert frame to PNG bytes.

        Args:
            frame: PIL Image

        Returns:
            PNG bytes
        """
        buffer = io.BytesIO()
        frame.save(buffer, format="PNG")
        return buffer.getvalue()

    # --- Element Preparation Methods ---

    def _parse_color(self, color_val: Any) -> tuple[int, int, int]:
        """Parse color from various formats to RGB tuple."""
        if isinstance(color_val, (list, tuple)) and len(color_val) >= 3:
            return (int(color_val[0]), int(color_val[1]), int(color_val[2]))
        if isinstance(color_val, str):
            try:
                return hex_to_rgb(color_val)
            except (ValueError, IndexError):
                pass
        return (255, 255, 255)  # default white

    def _get_font_path(self, font_name: str | None) -> str | None:
        """Get font path from font name."""
        if not font_name:
            return None
        path = get_font_path(font_name)
        return str(path) if path else None

    def _prepare_text(self, el: dict) -> PreparedText:
        """Prepare static text element."""
        content = str(el.get("content", ""))
        font_name = el.get("font")
        font_path = self._get_font_path(font_name)
        font_size = int(el.get("font_size", 8))
        spacing = int(el.get("spacing", 0))
        color = self._parse_color(el.get("color", "ffffff"))
        x = int(el.get("x", 0))
        y = int(el.get("y", 0))

        # Pre-render the text
        rendered = self._render_text_to_image(
            content, font_path, font_size, spacing, color
        )

        return PreparedText(
            type=ELEMENT_TEXT,
            x=x,
            y=y,
            color=color,
            content=content,
            font_path=font_path,
            font_size=font_size,
            spacing=spacing,
            rendered_image=rendered,
            raw_data=el,
        )

    def _prepare_textscroll(self, el: dict) -> PreparedTextScroll:
        """Prepare scrolling text element."""
        content = str(el.get("content", ""))
        font_name = el.get("font")
        font_path = self._get_font_path(font_name)
        font_size = int(el.get("font_size", 8))
        spacing = int(el.get("spacing", 0))
        color = self._parse_color(el.get("color", "ffffff"))
        y = int(el.get("y", 0))
        speed = float(el.get("speed", 50))  # pixels per second

        # Pre-render the full text strip
        rendered_strip = self._render_text_to_image(
            content, font_path, font_size, spacing, color
        )

        return PreparedTextScroll(
            type=ELEMENT_TEXTSCROLL,
            x=0,  # x is controlled by animation
            y=y,
            color=color,
            content=content,
            font_path=font_path,
            font_size=font_size,
            spacing=spacing,
            speed=speed,
            rendered_strip=rendered_strip,
            strip_width=rendered_strip.width if rendered_strip else 0,
            raw_data=el,
        )

    def _prepare_textlong(self, el: dict) -> PreparedTextLong:
        """Prepare long text with word wrap."""
        content = str(el.get("content", ""))
        font_name = el.get("font")
        font_path = self._get_font_path(font_name)
        font_size = int(el.get("font_size", 8))
        spacing = int(el.get("spacing", 0))
        color = self._parse_color(el.get("color", "ffffff"))
        x = int(el.get("x", 0))
        y = int(el.get("y", 0))
        speed = float(el.get("speed", 2.0))  # hold duration in seconds
        scroll_duration = float(el.get("scroll_duration", 0.5))
        direction = el.get("direction", "up")

        # Calculate max width for wrapping
        max_width = self._width - x

        # Wrap text to lines
        if font_path:
            wrapped_lines = self._font_cache.wrap_text_to_lines(
                content, font_path, font_size, max_width, spacing
            )
        else:
            # Simple word wrap without font measurements
            wrapped_lines = self._simple_word_wrap(content, max_width // 6)

        # Pre-render each line
        rendered_lines = []
        for line in wrapped_lines:
            img = self._render_text_to_image(line, font_path, font_size, spacing, color)
            rendered_lines.append(img)

        # Determine line height
        line_height = font_size + 2

        # Only animated if multiple lines
        is_animated = len(wrapped_lines) > 1

        return PreparedTextLong(
            type=ELEMENT_TEXTLONG,
            x=x,
            y=y,
            color=color,
            content=content,
            font_path=font_path,
            font_size=font_size,
            spacing=spacing,
            speed=speed,
            scroll_duration=scroll_duration,
            direction=direction,
            wrapped_lines=wrapped_lines,
            rendered_lines=rendered_lines,
            line_height=line_height,
            is_animated=is_animated,
            raw_data=el,
        )

    def _prepare_icon(self, el: dict) -> PreparedIcon:
        """Prepare MDI icon element."""
        icon_name = el.get("icon", "mdi:help")
        if icon_name.startswith("mdi:"):
            icon_name = icon_name[4:]

        size = int(el.get("size", 16))
        color = self._parse_color(el.get("color", "ffffff"))
        x = int(el.get("x", 0))
        y = int(el.get("y", 0))

        # Icon rendering would require MDI font - placeholder for now
        # Create a colored square as placeholder
        rendered = Image.new("RGBA", (size, size), (*color, 255))

        return PreparedIcon(
            type=ELEMENT_ICON,
            x=x,
            y=y,
            color=color,
            icon_name=icon_name,
            size=size,
            rendered_image=rendered,
            raw_data=el,
        )

    async def _prepare_image(self, el: dict) -> PreparedImage:
        """Prepare image element (async for URL loading)."""
        source = el.get("source", "")
        x = int(el.get("x", 0))
        y = int(el.get("y", 0))
        width = el.get("width")
        height = el.get("height")

        loaded_image = None

        if source:
            if source.startswith(("http://", "https://")):
                # Load from URL
                loaded_image = await self._load_image_from_url(source)
            else:
                # Load from local path
                loaded_image = self._load_image_from_path(source)

        # Resize if dimensions specified
        if loaded_image and (width or height):
            target_w = int(width) if width else loaded_image.width
            target_h = int(height) if height else loaded_image.height
            loaded_image = loaded_image.resize(
                (target_w, target_h), Image.Resampling.NEAREST
            )

        return PreparedImage(
            type=ELEMENT_IMAGE,
            x=x,
            y=y,
            source=source,
            width=int(width) if width else None,
            height=int(height) if height else None,
            loaded_image=loaded_image,
            raw_data=el,
        )

    def _prepare_pixels(self, el: dict) -> PreparedPixels:
        """Prepare raw pixels element."""
        pixels_data = el.get("data", [])
        x = int(el.get("x", 0))
        y = int(el.get("y", 0))

        # Parse pixel data
        parsed_pixels = []
        for p in pixels_data:
            px = int(p.get("x", 0))
            py = int(p.get("y", 0))
            color = self._parse_color(p.get("color", "ffffff"))
            parsed_pixels.append({"x": px, "y": py, "color": color})

        return PreparedPixels(
            type=ELEMENT_PIXELS,
            x=x,
            y=y,
            pixels=parsed_pixels,
            raw_data=el,
        )

    # --- Rendering Methods ---

    def _render_text_to_image(
        self,
        text: str,
        font_path: str | None,
        font_size: int,
        spacing: int,
        color: tuple[int, int, int]
    ) -> Image.Image:
        """Render text to an RGBA image with transparent background."""
        if not text:
            return Image.new("RGBA", (1, font_size), (0, 0, 0, 0))

        # Measure text width
        if font_path:
            total_width = self._font_cache.measure_text_width(
                text, font_path, font_size, spacing
            )
        else:
            total_width = len(text) * (font_size // 2)

        total_width = max(1, total_width)

        # Create transparent image
        img = Image.new("RGBA", (total_width, font_size), (0, 0, 0, 0))

        if font_path:
            # Render using font cache character by character
            x = 0
            for char in text:
                char_img, char_width = self._font_cache.get_char_mask(
                    char, font_path, font_size
                )
                if char_img:
                    # Colorize the character
                    colored = self._colorize_mask(char_img, color)
                    img.paste(colored, (x, 0), colored)
                x += char_width + spacing
        else:
            # Fallback to PIL default font
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.load_default()
            except Exception:
                font = None
            draw.text((0, 0), text, font=font, fill=(*color, 255))

        return img

    def _colorize_mask(
        self,
        mask: Image.Image,
        color: tuple[int, int, int]
    ) -> Image.Image:
        """Colorize a white-on-transparent mask image."""
        if mask.mode != "RGBA":
            mask = mask.convert("RGBA")

        # Create colored version
        colored = Image.new("RGBA", mask.size, (*color, 255))

        # Use mask's alpha channel
        r, g, b, a = mask.split()
        colored.putalpha(a)

        return colored

    def _render_text(self, canvas: Image.Image, el: PreparedText) -> None:
        """Render static text onto canvas."""
        if el.rendered_image:
            self._paste_rgba(canvas, el.rendered_image, el.x, el.y)

    def _render_textscroll(
        self,
        canvas: Image.Image,
        el: PreparedTextScroll,
        frame_time: float
    ) -> None:
        """Render scrolling text onto canvas."""
        if not el.rendered_strip or el.strip_width < 1:
            return

        # Calculate scroll offset based on time
        total_distance = self._width + el.strip_width
        offset = (frame_time * el.speed) % total_distance
        x = int(self._width - offset)

        self._paste_rgba(canvas, el.rendered_strip, x, el.y)

    def _render_textlong(
        self,
        canvas: Image.Image,
        el: PreparedTextLong,
        frame_time: float
    ) -> None:
        """Render long text with scrolling between lines."""
        if not el.rendered_lines:
            return

        if len(el.rendered_lines) == 1 or not el.is_animated:
            # Single line - just render it
            self._paste_rgba(canvas, el.rendered_lines[0], el.x, el.y)
            return

        # Multi-line animation
        cycle_time = el.speed + el.scroll_duration
        total_time = cycle_time * len(el.rendered_lines)

        current_time = frame_time % total_time
        line_idx = int(current_time / cycle_time)
        time_in_phase = current_time % cycle_time

        next_idx = (line_idx + 1) % len(el.rendered_lines)

        if time_in_phase < el.speed:
            # Holding current line
            self._paste_rgba(canvas, el.rendered_lines[line_idx], el.x, el.y)
        else:
            # Animating to next line
            progress = (time_in_phase - el.speed) / el.scroll_duration
            progress = min(1.0, max(0.0, progress))

            if el.direction == "up":
                offset = int(progress * el.line_height)
                self._paste_rgba(
                    canvas, el.rendered_lines[line_idx], el.x, el.y - offset
                )
                self._paste_rgba(
                    canvas, el.rendered_lines[next_idx],
                    el.x, el.y + el.line_height - offset
                )
            elif el.direction == "down":
                offset = int(progress * el.line_height)
                self._paste_rgba(
                    canvas, el.rendered_lines[line_idx], el.x, el.y + offset
                )
                self._paste_rgba(
                    canvas, el.rendered_lines[next_idx],
                    el.x, el.y - el.line_height + offset
                )
            elif el.direction == "left":
                offset = int(progress * self._width)
                self._paste_rgba(
                    canvas, el.rendered_lines[line_idx], el.x - offset, el.y
                )
                self._paste_rgba(
                    canvas, el.rendered_lines[next_idx],
                    el.x + self._width - offset, el.y
                )
            elif el.direction == "right":
                offset = int(progress * self._width)
                self._paste_rgba(
                    canvas, el.rendered_lines[line_idx], el.x + offset, el.y
                )
                self._paste_rgba(
                    canvas, el.rendered_lines[next_idx],
                    el.x - self._width + offset, el.y
                )

    def _render_icon(self, canvas: Image.Image, el: PreparedIcon) -> None:
        """Render MDI icon onto canvas."""
        if el.rendered_image:
            self._paste_rgba(canvas, el.rendered_image, el.x, el.y)

    def _render_image(self, canvas: Image.Image, el: PreparedImage) -> None:
        """Render image onto canvas."""
        if el.loaded_image:
            img = el.loaded_image
            if img.mode == "RGBA":
                self._paste_rgba(canvas, img, el.x, el.y)
            else:
                canvas.paste(img, (el.x, el.y))

    def _render_pixels(self, canvas: Image.Image, el: PreparedPixels) -> None:
        """Render raw pixels onto canvas."""
        for pixel in el.pixels:
            px = el.x + pixel["x"]
            py = el.y + pixel["y"]
            if 0 <= px < self._width and 0 <= py < self._height:
                canvas.putpixel((px, py), pixel["color"])

    def _paste_rgba(
        self,
        canvas: Image.Image,
        overlay: Image.Image,
        x: int,
        y: int
    ) -> None:
        """Paste RGBA image onto RGB canvas with alpha blending."""
        if overlay.mode != "RGBA":
            overlay = overlay.convert("RGBA")

        # Handle negative coordinates and clipping
        if x < 0:
            overlay = overlay.crop((-x, 0, overlay.width, overlay.height))
            x = 0
        if y < 0:
            overlay = overlay.crop((0, -y, overlay.width, overlay.height))
            y = 0

        # Clip to canvas bounds
        if x + overlay.width > self._width:
            overlay = overlay.crop((0, 0, self._width - x, overlay.height))
        if y + overlay.height > self._height:
            overlay = overlay.crop((0, 0, overlay.width, self._height - y))

        if overlay.width > 0 and overlay.height > 0:
            canvas.paste(overlay, (x, y), overlay)

    # --- Helper Methods ---

    def _simple_word_wrap(self, text: str, chars_per_line: int) -> list[str]:
        """Simple word wrap without font measurements."""
        words = text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            if current_length + len(word) + 1 <= chars_per_line or not current_line:
                current_line.append(word)
                current_length += len(word) + 1
            else:
                lines.append(" ".join(current_line))
                current_line = [word]
                current_length = len(word)

        if current_line:
            lines.append(" ".join(current_line))

        return lines if lines else [text]

    async def _load_image_from_url(self, url: str) -> Image.Image | None:
        """Load image from URL."""
        if not self._session:
            _LOGGER.warning("No HTTP session available for URL loading")
            return None

        try:
            async with self._session.get(url, timeout=10) as response:
                if response.status == 200:
                    data = await response.read()
                    return Image.open(io.BytesIO(data)).convert("RGBA")
        except Exception as err:
            _LOGGER.warning("Failed to load image from %s: %s", url, err)

        return None

    def _load_image_from_path(self, path: str) -> Image.Image | None:
        """Load image from local path."""
        try:
            return Image.open(path).convert("RGBA")
        except Exception as err:
            _LOGGER.warning("Failed to load image from %s: %s", path, err)
            return None
