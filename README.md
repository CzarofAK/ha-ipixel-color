# iPIXEL Color - Home Assistant Integration

A Home Assistant custom integration for iPIXEL Color LED matrix displays via Bluetooth.
These displays have been recently available as B.K. Light LED Pixel Board from Action and thus get increasing popularity.

> **About this fork** — based on [cagcoach/ha-ipixel-color](https://github.com/cagcoach/ha-ipixel-color),
> with a robust Bluetooth layer (keepalive, reconnect, per-panel lock, reliable power state,
> several panels switching together) and the features of other forks merged in:
> [ahzs645](https://github.com/ahzs645/ha-ipixel-color) (most features below, Lovelace cards),
> [tigers75](https://github.com/tigers75/ha-ipixel-color) (MDI icons, layouts),
> [gokberj](https://github.com/gokberj/ha-ipixel-color) (weather clock),
> [bastooky](https://github.com/bastooky/ha-ipixel-color) (emoji, dimension override),
> [arcdrake22](https://github.com/arcdrake22/ha-ipixel-color),
> [nielsmaerten](https://github.com/nielsmaerten/ha-ipixel-color) and
> [MobilGame06](https://github.com/MobilGame06/ha-ipixel-color) (fixes).
> See [CHANGELOG.md](CHANGELOG.md).

## Features

- **Multiple Display Modes**: Text Image, Native Text, Clock, GIF, and Rhythm modes
- **RGB Color Support**: Separate text and background colors via RGB light entities
- **Clock Display**: 9 different clock styles with automatic time synchronization
- **Rich Text Display**: Custom fonts, sizes, multiline text with `\n`, antialiasing
- **Template Support**: Use Home Assistant variables like `{{ states('sensor.temperature') }}°C`
- **Font Management**: Load TTF/OTF fonts from `fonts/` folder
- **Brightness Control**: Adjustable display brightness (1-100)
- **Orientation Control**: Rotate display (0°, 90°, 180°, 270°)
- **Rhythm/Music Visualizer**: Audio-reactive display with 5 visual styles
- **Direct Pixel Control**: Set individual LED pixels via service calls
- **Digital Signage**: Playlists, time slots, power scheduling
- **Lovelace Card**: Built-in visual control card
- **Auto/Manual Updates**: Choose automatic updates or manual refresh
- **State Persistence**: Settings preserved across HA restarts
- **Bluetooth Proxy Support**: Compatible with Bluetooth proxy devices
- **Auto-discovery**: Finds iPIXEL devices automatically via Bluetooth
- **Robust connection**: optional keepalive, automatic reconnect, power state restored and re-applied
- **MDI icons & layouts**: any Home Assistant icon, free composition of icons, image and texts
- **Weather clock** for 96×16 panels, **any emoji** via Twemoji

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click on the three dots in the top right corner
3. Select **Custom repositories**
4. Add the repository URL: `https://github.com/CzarofAK/ha-ipixel-color`
5. Select **Integration** as the category
6. Click **Add**
7. Search for "iPIXEL Color" in HACS and install it (pick the latest release;
   the default branch is only for testing)
8. Restart Home Assistant
9. Add the integration via Settings → Devices & Services → Add Integration

### Manual Installation

1. Copy `custom_components/ipixel_color` to your HA `custom_components` directory
2. Restart Home Assistant
3. Add integration via Settings → Devices & Services → Add Integration

### Optional: Custom Fonts

Place `.ttf`/`.otf` font files in the `fonts/` folder within the integration directory for additional font options.

## Versioning

This fork follows [Semantic Versioning](https://semver.org/) and continues the
numbering of the upstream releases: it is based on
[cagcoach/ha-ipixel-color `v0.2.0`](https://github.com/cagcoach/ha-ipixel-color/releases/tag/v0.2.0)
(16 Dec 2025, the last upstream release; its `manifest.json` still says
`0.1.0`), so this fork starts at `0.2.1`. Should upstream publish further
releases, the same number here does not mean the same content — compare the
[CHANGELOG](CHANGELOG.md).

- `0.x`: features and changes in minor versions, fixes in patch versions;
  breaking changes are possible in minor versions and are listed in the CHANGELOG
- `1.0.0` once the feature set is verified on real hardware

Releases are tagged `vX.Y.Z` and published as GitHub releases, which HACS
shows as versions.

## Entities

Once configured, you'll get these entities:

**Display Control:**
- `select.{device}_mode` - Display mode (textimage, text, clock)
- `text.{device}_display` - Enter text with templates and `\n` for newlines
- `switch.{device}_power` - Turn display on/off
- `number.{device}_brightness` - Display brightness level (1-100)

**Text Appearance:**
- `select.{device}_font` - Choose from available fonts
- `number.{device}_font_size` - Font size (0=auto, supports decimals like 12.5)
- `number.{device}_line_spacing` - Spacing between lines (0-20px)
- `switch.{device}_antialiasing` - Smooth vs sharp text
- `light.{device}_text_color` - RGB text color
- `light.{device}_background_color` - RGB background color

**Clock Mode:**
- `select.{device}_clock_style` - Clock style (0-8)
- `switch.{device}_clock_24h_format` - 24-hour time format
- `switch.{device}_clock_show_date` - Show date below time

**Update Control:**
- `switch.{device}_auto_update` - Auto-update on changes
- `button.{device}_update_display` - Manual refresh

**Device Info:**
- `sensor.{device}_width` - Display width in pixels
- `sensor.{device}_height` - Display height in pixels
- `sensor.{device}_device_type` - Device model information

## Template Examples

```jinja2
Time: {{ now().strftime('%H:%M') }}
Temp: {{ states('sensor.temperature') | round(1) }}°C
{% if is_state('sun.sun', 'above_horizon') %}Day{% else %}Night{% endif %}
```

## Quick Start

**Text Mode:**
1. Select mode: `textimage` (for RGB colors) or `text` (native)
2. Set text: `"Hello\nWorld"`
3. Choose text and background colors using light entities
4. Select font and size (or use auto-sizing)
5. Toggle auto-update ON or use manual update button

**Clock Mode:**
1. Select mode: `clock`
2. Choose clock style (0-8)
3. Set 24-hour format and date display preferences
4. Time syncs automatically

**Templates:**
- Templates update automatically with sensor changes when auto-update is ON

## Font Management

- Place `.ttf`/`.otf` files in `fonts/` folder
- Restart HA to see new fonts in dropdown
- Recommended: pixel fonts like 5x5.ttf, 7x7.ttf

## Safety Notes

These displays store content in SPI flash and re-read it at every boot, so a
bad write can leave the device unable to start.

- **Text animations 3 and 4 boot-loop non-32×32 panels.** They are blocked by
  this integration and omitted from the service pickers. Recovery from a boot
  loop means racing a clear command into a very short window at power-on, so
  don't try to send them via `send_raw_command` either.
- **Test content before writing it to a slot.** If a payload displays correctly
  without `buffer_slot`, it is safe to save. A corrupt payload written to a slot
  is replayed on every boot.
- **Destructive or locking commands are not exposed as services** in this fork:
  `set_default_mode` and `erase_data` erase every saved slot and the device
  settings, `set_password` can lock you out, `send_raw_command` bypasses all
  checks. To blank the screen, use `ipixel_color.clear_pixels` or turn off the
  screen switch — both are non-destructive.

## Options

Settings → Devices & Services → iPIXEL Color → Configure (per panel):

- **Keep connection open** (default on): commands execute immediately and
  several panels switch at the same moment. Occupies one Bluetooth proxy
  connection slot per panel, and the phone app cannot connect meanwhile.
- **Override panel dimensions**: for panels that report the wrong size.

## Troubleshooting

**Device not found / won't connect**

The panel accepts only one Bluetooth connection at a time, and it stops
advertising entirely while something is connected to it. This is the most common
cause of discovery failures:

1. Force-close the official iPIXEL Color app on every phone in range (leaving it
   backgrounded is often enough to hold the connection).
2. If the panel was paired to a phone, unpair it there.
3. Power-cycle the panel and retry discovery in Home Assistant.

Only one controller can drive the display — pick either Home Assistant or the
phone app, not both.

**Other issues**

- Enable debug logging: `custom_components.ipixel_color: debug`
- Check auto-update is ON or use manual update button
- Verify templates in Developer Tools → Template
- Ensure device is in Bluetooth range

## Lovelace Card

The integration includes a built-in Lovelace card for visual control. After installation, add the resource to your Lovelace configuration:

```yaml
resources:
  - url: /ipixel_color/ipixel-display-card.js
    type: module
```

Then add the card to your dashboard:

```yaml
type: custom:ipixel-display-card
entity: text.ipixel_living_room_text
name: Living Room Display
resolution: 64x16
show_header: true
show_display: true
show_controls: true
show_quick_actions: true
```

**Card Features:**
- Display preview with LED matrix visualization
- Quick actions: Power, Clear, Clock, Sync Time
- Text input with effects (scroll, blink, breeze, snow, laser)
- Brightness and orientation controls
- Playlist management
- Power schedule configuration

## Services

The integration provides these services for automation:

| Service | Description |
|---------|-------------|
| `ipixel_color.display_text` | Display text with effects and colors |
| `ipixel_color.set_brightness` | Set brightness level (1-100) |
| `ipixel_color.set_clock_mode` | Enable clock display with style options |
| `ipixel_color.sync_time` | Sync current time to device |
| `ipixel_color.upload_gif` | Upload and display GIF animation |
| `ipixel_color.set_pixel` | Set a single pixel color |
| `ipixel_color.set_pixels` | Set multiple pixels (batch) |
| `ipixel_color.clear_pixels` | Clear the display |
| `ipixel_color.show_slot` | Display content from stored slot |
| `ipixel_color.delete_slot` | Delete stored slot content |
| `ipixel_color.create_playlist` | Create content playlist |
| `ipixel_color.start_playlist` | Start playlist playback |
| `ipixel_color.stop_playlist` | Stop playlist |
| `ipixel_color.add_schedule` | Add scheduled display item |
| `ipixel_color.set_power_schedule` | Configure auto on/off times |
| `ipixel_color.add_time_slot` | Schedule playlist for specific times |
| `ipixel_color.display_mdi_icon` | Show a Material Design Icon |
| `ipixel_color.display_layout` | Compose icons, an image and texts (scroll/blink) |
| `ipixel_color.display_weather_clock` | Weather icon, date, time, temperature (96×16) |
| `ipixel_color.display_emoji` | Show any emoji (Twemoji) |
| `ipixel_color.set_countdown_timer` / `set_stopwatch` / `set_scoreboard` | Native timer, stopwatch, scoreboard |
| `ipixel_color.display_gallery_asset` / `display_border` | Bundled gallery images and animated borders |

All services are listed with their fields in Developer Tools → Actions.

## Status

| Feature | Status |
|---------|--------|
| ✅ Text Display (3 modes) | Complete |
| ✅ RGB Colors | Complete |
| ✅ Clock Mode (9 styles) | Complete |
| ✅ Custom Fonts | Complete |
| ✅ Templates | Complete |
| ✅ State Persistence | Complete |
| ✅ Brightness Control | Complete |
| ✅ Orientation Control | Complete |
| ✅ Rhythm/Music Mode | Complete |
| ✅ Pixel Control | Complete |
| ✅ Digital Signage | Complete |
| ✅ Lovelace Card | Complete |
| ✅ GIF Animations | Complete |
| ✅ MDI Icons / Layouts | Complete |
| ✅ Weather Clock (96×16) | Complete |
| ✅ Emoji (Twemoji) | Complete |
| ✅ Robust connection / keepalive | Complete |
| 🔄 Hardware verification of all services | In Progress |
| 🔄 Animated Variable-Width Fonts | Planned |

Complete means implemented and covered by the automated tests (real Home
Assistant core, simulated panels). See the CHANGELOG for what is not yet
verified on hardware.

## Technical

- Requires: Home Assistant 2024.12+ and HACS (tested with 2026.2)

## Acknowledgments

Thanks to the authors of the forks listed at the top, whose work is merged here
with their commits and co-author credits preserved.

Special thanks to the authors of [pypixelcolor](https://github.com/lucagoc/pypixelcolor) for their excellent library that powers the core functionality of this integration. Their work in reverse-engineering the iPIXEL protocol has been invaluable.

## License

This project is licensed under the GNU General Public License v3.0 - see the LICENSE file for details.

## Debugging and Logs

To troubleshoot issues such as unexpected reboots or missing messages, enable detailed logging for the integration:

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.ipixel_color: debug
```

After restarting Home Assistant, view the logs via **Settings → System → Logs**. Look for entries prefixed with `custom_components.ipixel_color`, which include API calls, template rendering, and any warnings about validation limits.

You can also monitor the raw data sent to the device by enabling the `pypixelcolor` logger:

```yaml
logger:
  logs:
    pypixelcolor: debug
```

These logs show the exact command payloads, which helps identify problematic parameters that could cause a reboot.
