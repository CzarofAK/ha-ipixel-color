"""End-to-end tests against a real Home Assistant core with simulated panels."""
from __future__ import annotations

import asyncio

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache,
)

from custom_components.ipixel_color.const import DOMAIN

A1, A2 = "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"


async def _setup(hass: HomeAssistant, options=None):
    entries = []
    for addr, name in ((A1, "front left"), (A2, "front right")):
        e = MockConfigEntry(
            domain=DOMAIN,
            unique_id=addr,
            data={"address": addr, "name": name},
            options=options or {},
        )
        e.add_to_hass(hass)
        entries.append(e)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    for e in entries:
        assert e.state.name == "LOADED", e.state
    return entries


def _power_entity(hass, addr):
    reg = er.async_get(hass)
    return reg.async_get_entity_id("switch", DOMAIN, f"{addr}_power")


async def test_setup_reads_96x16(hass, mock_bluetooth, fake_ble):
    entries = await _setup(hass)
    for e in entries:
        api = hass.data[DOMAIN][e.entry_id]
        info = await api.get_device_info()
        assert (info["width"], info["height"]) == (96, 16)
    # services from the merged feature set are registered, risky ones are not
    services = hass.services.async_services()[DOMAIN]
    for s in ("display_text", "upload_gif", "set_countdown_timer", "display_gallery_asset"):
        assert s in services
    for s in ("erase_data", "set_default_mode", "send_raw_command", "set_password"):
        assert s not in services


async def test_both_panels_switch_together(hass, mock_bluetooth, fake_ble):
    await _setup(hass)
    e1, e2 = _power_entity(hass, A1), _power_entity(hass, A2)
    t0 = len(fake_ble[A1].log), len(fake_ble[A2].log)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": [e1, e2]}, blocking=True
    )
    assert fake_ble[A1].power is False and fake_ble[A2].power is False
    assert hass.states.get(e1).state == STATE_OFF
    assert hass.states.get(e2).state == STATE_OFF
    w1 = [t for t, d in fake_ble[A1].log[t0[0]:] if d[:4] == bytes([5, 0, 7, 1])]
    w2 = [t for t, d in fake_ble[A2].log[t0[1]:] if d[:4] == bytes([5, 0, 7, 1])]
    assert abs(w1[0] - w2[0]) < 0.05, "power commands should go out together"


async def test_silent_link_loss_self_heals(hass, mock_bluetooth, fake_ble):
    await _setup(hass)
    e1 = _power_entity(hass, A1)
    fake_ble[A1].client.drop(callback=False)  # link dead, nobody told us
    await hass.services.async_call("switch", "turn_off", {"entity_id": e1}, blocking=True)
    assert fake_ble[A1].power is False
    assert hass.states.get(e1).state == STATE_OFF
    assert fake_ble[A1].connects == 2


async def test_keepalive_reconnects_and_reapplies(hass, mock_bluetooth, fake_ble):
    await _setup(hass)
    e1 = _power_entity(hass, A1)
    await hass.services.async_call("switch", "turn_off", {"entity_id": e1}, blocking=True)
    fake_ble[A1].client.drop(callback=True)
    fake_ble[A1].power = True  # panel rebooted -> default on
    await asyncio.sleep(1.8)
    await hass.async_block_till_done()
    assert fake_ble[A1].connects == 2
    assert fake_ble[A1].power is False, "desired state must be re-applied"
    assert hass.states.get(e1).attributes["connected"] is True


async def test_connect_failure_raises_and_applies_later(hass, mock_bluetooth, fake_ble):
    """Panel advertises but the link can't be opened: error + deferred apply."""
    from unittest.mock import patch

    from homeassistant.exceptions import HomeAssistantError

    await _setup(hass, options={"keep_connected": False})
    e1 = _power_entity(hass, A1)
    fake_ble[A1].client.drop(callback=False)

    async def fail(*a, **k):
        raise TimeoutError("connect timeout")

    base = "custom_components.ipixel_color.bluetooth.client"
    with patch(f"{base}.establish_connection", side_effect=fail):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "switch", "turn_off", {"entity_id": e1}, blocking=True
            )
    st = hass.states.get(e1)
    assert st.state == STATE_ON, "state must stay at last confirmed value"
    assert st.attributes["desired_power"] is False
    # link works again -> next command/reconnect applies the desired state
    api = next(v for v in hass.data[DOMAIN].values() if getattr(v, "address", None) == A1)
    await api.apply_desired_power()
    assert fake_ble[A1].power is False
    assert hass.states.get(e1).state == STATE_OFF


async def test_restore_after_restart(hass, mock_bluetooth, fake_ble):
    reg = er.async_get(hass)
    for addr in (A1, A2):
        reg.async_get_or_create("switch", DOMAIN, f"{addr}_power", suggested_object_id=f"p{addr[-1]}")
    mock_restore_cache(hass, [State("switch.p1", STATE_OFF), State("switch.p2", STATE_ON)])
    await _setup(hass)
    await hass.async_block_till_done()
    assert hass.states.get("switch.p1").state == STATE_OFF
    assert fake_ble[A1].power is False
    assert hass.states.get("switch.p2").state == STATE_ON
    assert fake_ble[A2].power is True


async def test_dimension_override(hass, mock_bluetooth, fake_ble):
    fake_ble[A1].device_type = 131  # firmware claims 64x16
    await _setup(hass, options={"override_dimensions": True, "panel_width": 96, "panel_height": 16})
    api = next(iter(v for k, v in hass.data[DOMAIN].items() if getattr(v, "address", None) == A1))
    info = await api.get_device_info()
    assert (info["width"], info["height"]) == (96, 16)


async def test_gif_upload_windowed(hass, mock_bluetooth, fake_ble):
    """A large GIF goes out in <=244 byte chunks, several 12 KB windows, ACKed."""
    import io
    import random

    from PIL import Image

    entries = await _setup(hass)
    api = hass.data[DOMAIN][entries[0].entry_id]
    rnd = random.Random(1)
    frames = [
        Image.frombytes("RGB", (96, 16), bytes(rnd.getrandbits(8) for _ in range(96 * 16 * 3)))
        for _ in range(20)
    ]
    buf = io.BytesIO()
    frames[0].save(buf, format="GIF", save_all=True, append_images=frames[1:], duration=100)
    assert len(buf.getvalue()) > 24 * 1024
    writes_before = len(fake_ble[A1].client.writes)
    ok = await api.display_image_url_bytes(buf.getvalue())
    assert ok
    new = fake_ble[A1].client.writes[writes_before:]
    assert max(len(w) for w in new) <= 244
    assert sum(len(w) for w in new) > 24 * 1024
    assert fake_ble[A1].windows >= 3, "several 12 KB windows, each ACKed"


async def test_text_animation_guard(hass, mock_bluetooth, fake_ble):
    entries = await _setup(hass)
    api = hass.data[DOMAIN][entries[0].entry_id]
    before = len(fake_ble[A1].log)
    ok = await api.display_text_pypixelcolor("hi", animation=3)
    assert not ok
    assert len(fake_ble[A1].log) == before, "boot-loop animation must never be sent"


SUN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    '<path d="M12 7a5 5 0 1 1 0 10a5 5 0 0 1 0-10z"/></svg>'
)


async def test_mdi_icon_and_layout(hass, mock_bluetooth, fake_ble):
    """tigers75 features: MDI icon + composed layout with scrolling text."""
    from unittest.mock import AsyncMock, patch

    await _setup(hass)
    reg = __import__("homeassistant.helpers.device_registry", fromlist=["x"]).async_get(hass)
    dev = reg.async_get_device(identifiers={(DOMAIN, A1)})
    with patch(
        "custom_components.ipixel_color.device.mdi_icon.fetch_mdi_svg",
        AsyncMock(return_value=SUN_SVG),
    ), patch(
        "custom_components.ipixel_color.device.composer.fetch_mdi_svg",
        AsyncMock(return_value=SUN_SVG),
    ):
        w0 = fake_ble[A1].windows
        await hass.services.async_call(
            DOMAIN, "display_mdi_icon",
            {"device_id": dev.id, "icon": "mdi:weather-sunny", "color": [255, 200, 0]},
            blocking=True,
        )
        assert fake_ble[A1].windows == w0 + 1
        await hass.services.async_call(
            DOMAIN, "display_layout",
            {
                "device_id": dev.id,
                "icon": "mdi:weather-sunny", "icon_size": 16,
                "text": "Beckenried 21°C sehr langer Text", "text_x": 18, "text_y": 4,
                "text_font": "5x5", "text_size": 5, "text_scroll": True, "text_wrap": False,
            },
            blocking=True,
        )
        assert fake_ble[A1].windows > w0 + 1
    # scrolling text -> GIF (type byte 0x03)
    gif_windows = [d for _, d in fake_ble[A1].log if len(d) > 4 and d[2] == 3 and d[3] == 0]
    assert gif_windows, "scrolling layout must be sent as GIF"


async def test_weather_clock(hass, mock_bluetooth, fake_ble):
    """gokberj feature: weather clock rendered for 96x16 and sent."""
    from homeassistant.helpers import device_registry as dr

    await _setup(hass)
    hass.states.async_set("weather.forecast_home", "partlycloudy", {"temperature": 14.6})
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, A2)})
    w0 = fake_ble[A2].windows
    await hass.services.async_call(
        DOMAIN, "display_weather_clock",
        {"device_id": dev.id, "weather_entity": "weather.forecast_home", "language": "de"},
        blocking=True,
    )
    assert fake_ble[A2].windows == w0 + 1


async def test_emoji(hass, mock_bluetooth, fake_ble):
    """bastooky feature: arbitrary emoji via Twemoji (download mocked)."""
    import io
    from unittest.mock import AsyncMock, patch

    from homeassistant.helpers import device_registry as dr
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (72, 72), (255, 0, 0, 255)).save(buf, format="PNG")
    await _setup(hass)
    dev = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, A1)})
    w0 = fake_ble[A1].windows
    with patch(
        "custom_components.ipixel_color.display.emoji_renderer._fetch_emoji_png",
        AsyncMock(return_value=buf.getvalue()),
    ) as fetch:
        await hass.services.async_call(
            DOMAIN, "display_emoji", {"device_id": dev.id, "emoji": "🚨"}, blocking=True
        )
    fetch.assert_awaited_once()
    assert fetch.await_args.args[1] == "1f6a8"
    assert fake_ble[A1].windows == w0 + 1
