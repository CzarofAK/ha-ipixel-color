"""Visual effects processor using PIL ImageFilter.

Provides various image effects that can be applied to text
and images before sending to the iPIXEL display.

No external dependencies - uses PIL's built-in filters.
"""
from __future__ import annotations

import logging
from typing import Callable

from PIL import Image, ImageFilter, ImageEnhance, ImageOps

_LOGGER = logging.getLogger(__name__)


# Effect definitions
# Each effect is a callable that takes an Image and returns an Image

def effect_none(image: Image.Image) -> Image.Image:
    """No effect - return original image."""
    return image


def effect_blur(image: Image.Image) -> Image.Image:
    """Apply blur effect."""
    return image.filter(ImageFilter.BLUR)


def effect_sharpen(image: Image.Image) -> Image.Image:
    """Apply sharpen effect."""
    return image.filter(ImageFilter.SHARPEN)


def effect_contour(image: Image.Image) -> Image.Image:
    """Apply contour/edge detection effect."""
    return image.filter(ImageFilter.CONTOUR)


def effect_edge_enhance(image: Image.Image) -> Image.Image:
    """Apply edge enhancement effect."""
    return image.filter(ImageFilter.EDGE_ENHANCE)


def effect_edge_enhance_more(image: Image.Image) -> Image.Image:
    """Apply stronger edge enhancement effect."""
    return image.filter(ImageFilter.EDGE_ENHANCE_MORE)


def effect_emboss(image: Image.Image) -> Image.Image:
    """Apply emboss effect."""
    return image.filter(ImageFilter.EMBOSS)


def effect_smooth(image: Image.Image) -> Image.Image:
    """Apply smoothing effect."""
    return image.filter(ImageFilter.SMOOTH)


def effect_detail(image: Image.Image) -> Image.Image:
    """Apply detail enhancement effect."""
    return image.filter(ImageFilter.DETAIL)


def effect_invert(image: Image.Image) -> Image.Image:
    """Invert colors."""
    if image.mode == "RGBA":
        r, g, b, a = image.split()
        rgb = Image.merge("RGB", (r, g, b))
        inverted = ImageOps.invert(rgb)
        r, g, b = inverted.split()
        return Image.merge("RGBA", (r, g, b, a))
    elif image.mode == "RGB":
        return ImageOps.invert(image)
    else:
        # Convert to RGB, invert, convert back
        rgb = image.convert("RGB")
        inverted = ImageOps.invert(rgb)
        return inverted.convert(image.mode)


def effect_grayscale(image: Image.Image) -> Image.Image:
    """Convert to grayscale."""
    return ImageOps.grayscale(image).convert(image.mode)


def effect_mirror(image: Image.Image) -> Image.Image:
    """Mirror horizontally."""
    return ImageOps.mirror(image)


def effect_flip(image: Image.Image) -> Image.Image:
    """Flip vertically."""
    return ImageOps.flip(image)


def effect_posterize(image: Image.Image) -> Image.Image:
    """Posterize (reduce colors) effect."""
    if image.mode == "RGBA":
        r, g, b, a = image.split()
        rgb = Image.merge("RGB", (r, g, b))
        posterized = ImageOps.posterize(rgb, 2)
        r, g, b = posterized.split()
        return Image.merge("RGBA", (r, g, b, a))
    elif image.mode == "RGB":
        return ImageOps.posterize(image, 2)
    else:
        return image


def effect_solarize(image: Image.Image) -> Image.Image:
    """Solarize effect."""
    if image.mode == "RGBA":
        r, g, b, a = image.split()
        rgb = Image.merge("RGB", (r, g, b))
        solarized = ImageOps.solarize(rgb, threshold=128)
        r, g, b = solarized.split()
        return Image.merge("RGBA", (r, g, b, a))
    elif image.mode == "RGB":
        return ImageOps.solarize(image, threshold=128)
    else:
        return image


def effect_high_contrast(image: Image.Image) -> Image.Image:
    """Increase contrast."""
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(2.0)


def effect_low_contrast(image: Image.Image) -> Image.Image:
    """Decrease contrast."""
    enhancer = ImageEnhance.Contrast(image)
    return enhancer.enhance(0.5)


def effect_brighten(image: Image.Image) -> Image.Image:
    """Increase brightness."""
    enhancer = ImageEnhance.Brightness(image)
    return enhancer.enhance(1.5)


def effect_darken(image: Image.Image) -> Image.Image:
    """Decrease brightness."""
    enhancer = ImageEnhance.Brightness(image)
    return enhancer.enhance(0.5)


def effect_saturate(image: Image.Image) -> Image.Image:
    """Increase saturation."""
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    enhancer = ImageEnhance.Color(image)
    return enhancer.enhance(2.0)


def effect_desaturate(image: Image.Image) -> Image.Image:
    """Decrease saturation."""
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    enhancer = ImageEnhance.Color(image)
    return enhancer.enhance(0.5)


# === Shader-inspired procedural effects (ported from ipixel-shader GLSL) ===

import math
import colorsys


def generate_plasma_wave(width: int, height: int, time: float = 0.0) -> Image.Image:
    """Generate a plasma wave pattern frame.

    Based on the shader.frag example from ipixel-shader project.
    Uses multi-frequency sine waves for psychedelic color patterns.

    Args:
        width: Image width
        height: Image height
        time: Animation time parameter (0.0 to animate)

    Returns:
        PIL Image with plasma wave pattern
    """
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    for x in range(width):
        for y in range(height):
            # Normalize coordinates
            uvX = x / width
            uvY = y / height

            # Multi-frequency sine wave combination
            v = (math.sin(uvX * 10.0 + time)
                 + math.sin(uvY * 10.0 + time)
                 + math.sin((uvX + uvY) * 10.0 + time)
                 + math.sin(math.sqrt((uvX - 0.5) ** 2 + (uvY - 0.5) ** 2) * 20.0 - time * 2.0))

            # Color cycling with phase offsets
            r = int((math.sin(v * math.pi) * 0.5 + 0.5) * 255)
            g = int((math.sin(v * math.pi + 2.094) * 0.5 + 0.5) * 255)
            b = int((math.sin(v * math.pi + 4.188) * 0.5 + 0.5) * 255)

            pixels[x, y] = (r, g, b)

    return img


def generate_radial_pulse(width: int, height: int, time: float = 0.0) -> Image.Image:
    """Generate a radial pulse pattern frame.

    Creates concentric rings emanating from center.

    Args:
        width: Image width
        height: Image height
        time: Animation time parameter

    Returns:
        PIL Image with radial pulse pattern
    """
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    centerX = width / 2
    centerY = height / 2

    for x in range(width):
        for y in range(height):
            dx = x - centerX
            dy = y - centerY
            dist = math.sqrt(dx * dx + dy * dy)

            # Create expanding rings
            wave = math.sin(dist * 0.8 - time * 3.0) * 0.5 + 0.5
            pulse = math.sin(time * 2.0) * 0.3 + 0.7

            # Color based on distance and time
            hue = (dist / 20 + time * 0.5) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.8, wave * pulse)

            pixels[x, y] = (int(r * 255), int(g * 255), int(b * 255))

    return img


def generate_hypnotic(width: int, height: int, time: float = 0.0) -> Image.Image:
    """Generate a hypnotic spiral pattern frame.

    Args:
        width: Image width
        height: Image height
        time: Animation time parameter

    Returns:
        PIL Image with hypnotic spiral pattern
    """
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    centerX = width / 2
    centerY = height / 2

    for x in range(width):
        for y in range(height):
            dx = x - centerX
            dy = y - centerY
            dist = math.sqrt(dx * dx + dy * dy)
            angle = math.atan2(dy, dx)

            # Spiral pattern
            spiral = math.sin(angle * 4.0 + dist * 0.5 - time * 2.0)
            intensity = spiral * 0.5 + 0.5

            # Pulsing colors
            r = int(intensity * (math.sin(time) * 0.5 + 0.5) * 255)
            g = int(intensity * (math.sin(time + 2.094) * 0.5 + 0.5) * 255)
            b = int(intensity * (math.sin(time + 4.188) * 0.5 + 0.5) * 255)

            pixels[x, y] = (r, g, b)

    return img


def generate_lava(width: int, height: int, time: float = 0.0) -> Image.Image:
    """Generate a lava/magma flow pattern frame.

    Args:
        width: Image width
        height: Image height
        time: Animation time parameter

    Returns:
        PIL Image with lava pattern
    """
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    for x in range(width):
        for y in range(height):
            uvX = x / width
            uvY = y / height

            # Multiple noise layers for organic movement
            n1 = math.sin(uvX * 8.0 + time * 0.7) * math.cos(uvY * 6.0 + time * 0.5)
            n2 = math.sin(uvX * 12.0 - time * 0.3) * math.sin(uvY * 10.0 + time * 0.8)
            n3 = math.cos((uvX + uvY) * 5.0 + time)

            value = (n1 + n2 + n3 + 3) / 6

            # Lava color palette
            if value < 0.3:
                r, g, b = int(value * 3 * 100), 0, 0
            elif value < 0.6:
                r = int(100 + (value - 0.3) * 3 * 155)
                g = int((value - 0.3) * 3 * 100)
                b = 0
            else:
                r = 255
                g = int(100 + (value - 0.6) * 2.5 * 155)
                b = int((value - 0.6) * 2.5 * 100)

            pixels[x, y] = (min(255, r), min(255, g), min(255, b))

    return img


def generate_aurora(width: int, height: int, time: float = 0.0) -> Image.Image:
    """Generate a northern lights (aurora) pattern frame.

    Args:
        width: Image width
        height: Image height
        time: Animation time parameter

    Returns:
        PIL Image with aurora pattern
    """
    import random
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    # Seed for consistent star positions
    random.seed(42)
    stars = [(random.randint(0, width-1), random.randint(0, height-1))
             for _ in range(int(width * height * 0.02))]

    for x in range(width):
        for y in range(height):
            uvX = x / width
            uvY = y / height

            # Vertical wave bands
            wave1 = math.sin(uvX * 6.0 + time) * 0.3
            wave2 = math.sin(uvX * 4.0 - time * 0.7) * 0.2
            wave3 = math.sin(uvX * 8.0 + time * 1.3) * 0.15

            waveLine = 0.5 + wave1 + wave2 + wave3
            distFromWave = abs(uvY - waveLine)

            # Fade based on distance from wave
            intensity = max(0, 1 - distFromWave * 4)
            glow = intensity ** 1.5

            # Aurora colors
            colorShift = math.sin(uvX * 3.0 + time * 0.5)
            r = int(glow * (0.2 + colorShift * 0.3) * 255)
            g = int(glow * (0.8 + math.sin(time + uvX) * 0.2) * 255)
            b = int(glow * (0.6 + colorShift * 0.4) * 255)

            pixels[x, y] = (min(255, r), min(255, g), min(255, b))

    # Add twinkling stars in dark areas
    star_brightness = (math.sin(time * 3) * 0.5 + 0.5) * 180
    for sx, sy in stars:
        if pixels[sx, sy][1] < 50:  # Only in dark areas
            pixels[sx, sy] = (int(star_brightness), int(star_brightness), int(star_brightness * 0.9))

    return img


# Shader effect generators registry
SHADER_GENERATORS = {
    "plasma_wave": generate_plasma_wave,
    "radial_pulse": generate_radial_pulse,
    "hypnotic": generate_hypnotic,
    "lava": generate_lava,
    "aurora": generate_aurora,
}


def generate_shader_frame(
    effect_name: str,
    width: int,
    height: int,
    time: float = 0.0
) -> Image.Image | None:
    """Generate a single frame of a shader-inspired effect.

    Args:
        effect_name: Name of the shader effect
        width: Image width
        height: Image height
        time: Animation time parameter (increment for animation)

    Returns:
        PIL Image with the effect, or None if effect not found
    """
    generator = SHADER_GENERATORS.get(effect_name.lower())
    if generator:
        try:
            return generator(width, height, time)
        except Exception as err:
            _LOGGER.warning("Shader effect '%s' failed: %s", effect_name, err)
            return None
    return None


def get_shader_effect_names() -> list[str]:
    """Get list of available shader effect names."""
    return list(SHADER_GENERATORS.keys())


# Effect registry
EFFECTS: dict[str, Callable[[Image.Image], Image.Image]] = {
    "none": effect_none,
    "blur": effect_blur,
    "sharpen": effect_sharpen,
    "contour": effect_contour,
    "edge_enhance": effect_edge_enhance,
    "edge_enhance_more": effect_edge_enhance_more,
    "emboss": effect_emboss,
    "smooth": effect_smooth,
    "detail": effect_detail,
    "invert": effect_invert,
    "grayscale": effect_grayscale,
    "mirror": effect_mirror,
    "flip": effect_flip,
    "posterize": effect_posterize,
    "solarize": effect_solarize,
    "high_contrast": effect_high_contrast,
    "low_contrast": effect_low_contrast,
    "brighten": effect_brighten,
    "darken": effect_darken,
    "saturate": effect_saturate,
    "desaturate": effect_desaturate,
}

# List of effect names for UI
EFFECT_NAMES = list(EFFECTS.keys())


def apply_effect(image: Image.Image, effect_name: str) -> Image.Image:
    """Apply a named effect to an image.

    Args:
        image: PIL Image to process
        effect_name: Name of effect to apply

    Returns:
        Processed image (or original if effect not found)
    """
    effect_func = EFFECTS.get(effect_name.lower(), effect_none)
    try:
        return effect_func(image)
    except Exception as err:
        _LOGGER.warning("Effect '%s' failed: %s", effect_name, err)
        return image


def apply_effects(image: Image.Image, effect_names: list[str]) -> Image.Image:
    """Apply multiple effects in sequence.

    Args:
        image: PIL Image to process
        effect_names: List of effect names to apply in order

    Returns:
        Processed image
    """
    result = image
    for name in effect_names:
        result = apply_effect(result, name)
    return result


def get_effect_names() -> list[str]:
    """Get list of available effect names."""
    return EFFECT_NAMES.copy()


class EffectProcessor:
    """Stateful effect processor for consistent effect application.

    Allows setting a default effect that will be applied to all images.
    """

    def __init__(self, default_effect: str = "none") -> None:
        """Initialize effect processor.

        Args:
            default_effect: Default effect name to apply
        """
        self._default_effect = default_effect
        self._enabled = True

    @property
    def default_effect(self) -> str:
        """Get default effect name."""
        return self._default_effect

    @default_effect.setter
    def default_effect(self, value: str) -> None:
        """Set default effect name."""
        if value.lower() in EFFECTS:
            self._default_effect = value.lower()
        else:
            _LOGGER.warning("Unknown effect '%s', using 'none'", value)
            self._default_effect = "none"

    @property
    def enabled(self) -> bool:
        """Check if effects are enabled."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Enable or disable effects."""
        self._enabled = value

    def process(self, image: Image.Image) -> Image.Image:
        """Apply the default effect to an image.

        Args:
            image: PIL Image to process

        Returns:
            Processed image (or original if disabled)
        """
        if not self._enabled or self._default_effect == "none":
            return image
        return apply_effect(image, self._default_effect)

    def process_with(
        self,
        image: Image.Image,
        effect_name: str | None = None
    ) -> Image.Image:
        """Apply a specific effect (or default) to an image.

        Args:
            image: PIL Image to process
            effect_name: Effect to apply (uses default if None)

        Returns:
            Processed image
        """
        name = effect_name or self._default_effect
        if not self._enabled or name == "none":
            return image
        return apply_effect(image, name)


# Global effect processor instance
_global_processor: EffectProcessor | None = None


def get_effect_processor() -> EffectProcessor:
    """Get the global effect processor instance."""
    global _global_processor
    if _global_processor is None:
        _global_processor = EffectProcessor()
    return _global_processor
