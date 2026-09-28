"""Call every registered service once with plausible values from services.yaml.

Handlers of the merged feature set log errors instead of raising, so the
test fails on ERROR records from the integration as well as on exceptions.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from PIL import Image
from homeassistant.helpers import device_registry as dr

from custom_components.ipixel_color.const import DOMAIN

from .test_integration import A1, SUN_SVG, _setup

SERVICES_YAML = Path(__file__).parents[1] / "custom_components/ipixel_color/services.yaml"
SERVICES = yaml.safe_load(SERVICES_YAML.read_text())

# explicit values where a generic default is not meaningful
OVERRIDES = {
    "display_text": {"text": "Hallo"},
    "display_native_text": {"text": "Hallo"},
    "upload_gif": {"url": "http://example.invalid/x.gif"},
    "display_image_url": {"url": "http://example.invalid/x.png"},
    "display_image_raw_rgb_url": {"url": "http://example.invalid/x.png"},
    "display_gallery_asset": {"url": "http://example.invalid/x.gif"},
    "set_pixels": {"pixels": [{"x": 1, "y": 1, "color": "ff0000"}]},
    "set_pixels_batched": {"pixels": [{"x": 1, "y": 1, "color": "ff0000"}]},
    "display_layout": {"text": "Test", "icon": "mdi:weather-sunny"},
    "display_weather_clock": {"weather_entity": "weather.home"},
    "display_emoji": {"emoji": "🚨"},
    "display_local_gallery": {"size": "96x16", "filename": "ipixel_rc_eye_anim_l_16x96_1.gif"},
    "send_mix_data": {"hex_data": "80000000030000601000640000640000"},
    "display_mdi_icon": {"icon": "mdi:weather-sunny"},
}
# need network / external resources: only checked for "no crash"
NETWORK = {"upload_gif", "display_image_url", "display_image_raw_rgb_url", "display_gallery_asset"}
# own frame formats (alarm 00 80 ...) the simulated panel does not model:
# only checked for "no crash", not for a successful transfer
UNMODELLED = {"set_alarm_clock"}


def _value(field: dict):
    if "default" in field and field["default"] is not None:
        return field["default"]
    if "example" in field:
        ex = field["example"]
        try:
            return yaml.safe_load(ex) if isinstance(ex, str) else ex
        except yaml.YAMLError:
            return ex
    sel = field.get("selector") or {}
    if "number" in sel:
        return (sel["number"] or {}).get("min", 0)
    if "boolean" in sel:
        return False
    if "select" in sel:
        opt = sel["select"]["options"][0]
        return opt["value"] if isinstance(opt, dict) else opt
    if "color_rgb" in sel:
        return [255, 0, 0]
    if "text" in sel:
        return "x"
    return None


def _call_data(name: str, device_id: str) -> dict:
    data = {}
    for key, field in (SERVICES[name].get("fields") or {}).items():
        if key == "device_id":
            data[key] = device_id
        elif field.get("required"):
            v = _value(field)
            if v is not None:
                data[key] = v
    data.update(OVERRIDES.get(name, {}))
    return data


@pytest.mark.parametrize("service", sorted(SERVICES))
async def test_service_smoke(hass, mock_bluetooth, fake_ble, caplog, service):
    await _setup(hass)
    hass.states.async_set("weather.home", "rainy", {"temperature": 9})
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, A1)})
    buf = io.BytesIO()
    Image.new("RGBA", (72, 72), (0, 255, 0, 255)).save(buf, format="PNG")
    assert hass.services.has_service(DOMAIN, service), f"{service} in yaml but not registered"
    # preconditions for stateful services
    img = Path(hass.config.config_dir) / "www" / "display.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (96, 16), (0, 0, 255)).save(img)
    hass.config.allowlist_external_dirs = {str(img.parent)}
    extra = {}
    if service == "display_image_raw_rgb":
        extra["image_path"] = str(img)
    if service in ("trigger_schedule", "start_playlist", "set_playlist"):
        await hass.services.async_call(
            DOMAIN, "add_schedule", {"device_id": dev.id, "name": "a", "text": "A"}, blocking=True
        )
        entry_id = next(k for k, v in hass.data[DOMAIN].items() if getattr(v, "address", None) == A1)
        mgr = hass.data[DOMAIN][f"{entry_id}_schedule"]
        sid = next(iter(mgr._schedules))
        if service == "trigger_schedule":
            extra["schedule_id"] = sid
        else:
            await hass.services.async_call(
                DOMAIN, "set_playlist", {"device_id": dev.id, "schedule_ids": sid}, blocking=True
            )
    caplog.clear()
    caplog.set_level(logging.ERROR, logger="custom_components.ipixel_color")
    with (
        patch("custom_components.ipixel_color.device.mdi_icon.fetch_mdi_svg", AsyncMock(return_value=SUN_SVG)),
        patch("custom_components.ipixel_color.device.composer.fetch_mdi_svg", AsyncMock(return_value=SUN_SVG)),
        patch("custom_components.ipixel_color.display.emoji_renderer._fetch_emoji_png", AsyncMock(return_value=buf.getvalue())),
    ):
        await hass.services.async_call(DOMAIN, service, {**_call_data(service, dev.id), **extra}, blocking=True)
        await hass.async_block_till_done()
    errors = [r.getMessage() for r in caplog.records
              if r.levelno >= logging.ERROR and r.name.startswith("custom_components.ipixel_color")]
    if service in ("start_playlist",):
        # the playlist loop keeps running in the background; stop it
        await hass.services.async_call(DOMAIN, "stop_playlist", {"device_id": dev.id}, blocking=True)
    if service in NETWORK or service in UNMODELLED:
        return
    assert not errors, errors
