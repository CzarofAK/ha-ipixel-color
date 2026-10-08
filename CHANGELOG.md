# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.3] - 2026-10-08

### Fixed
- The final power re-apply 3 s after setup (0.3.2) never ran: it was
  scheduled through a plain lambda, which Home Assistant executes in a worker
  thread, so the task was destroyed ("Task was destroyed but it is
  pending"). The coroutine function is now passed directly.

## [0.3.2] - 2026-10-08

### Fixed
- Panels switched on after every Home Assistant restart while HA showed them
  off. Cause: the fun-mode switch sent "fun mode off" during restore, right
  after the power switch had restored "off" -- and that command powers the
  panel on. Fun mode is now only pushed at startup if it was on, and the
  restored power state is re-applied last (3 s after platform setup), so no
  startup command can leave the panel on against HA's state.

## [0.3.1] - 2026-10-08

### Added
- Buttons **Release for App** and **Reconnect** per panel: with "keep
  connected" HA holds the panel's single BLE link permanently, which locks
  out the phone app. Release drops the link for 10 minutes; meanwhile HA
  does not reconnect and commands fail instead of grabbing the link back.
  When the time is up (or Reconnect is pressed) HA reconnects and
  re-applies its desired power state. Switch attributes
  `released_for_app` / `release_seconds_left` show the state.

## [0.3.0] - 2026-09-28

Merge of the features of other forks onto the robust Bluetooth base of 0.2.1.
Authors' commits and co-author credits are preserved.

### Added
- Feature set of [ahzs645/ha-ipixel-color](https://github.com/ahzs645/ha-ipixel-color)
  (merged with full history): GIF/image upload with windowed transport and ACKs,
  gallery, emoji and animated border assets, countdown / timer / stopwatch /
  scoreboard / alarm clock, playlists and schedules, pixel drawing and visuals,
  native text, rhythm/EQ modes, slot management, Lovelace cards, media player
  and camera (preview) entities, corrected protocol wire formats
- `display_mdi_icon`, `display_layout`: Material Design Icons and composed
  layouts with scrolling/blinking elements (from tigers75), font Lepidos (CC0)
- `display_weather_clock` for 96x16 panels (from gokberj), with en/de/tr
  labels and optional 180 deg rotation
- `display_emoji`: any emoji via Twemoji, cached (from bastooky)
- Option to override the panel dimensions reported by the firmware (from bastooky)
- Integration tests against a real Home Assistant core with simulated panels,
  plus a smoke test that calls every service once

### Changed
- Bluetooth client implements the windowed `send_plan`/`send_command`
  interface on top of the robust connection handling (lock, reconnect,
  keepalive, single notification subscription); device info is read once
  at connect (3 attempts) and survives reconnects
- Destructive / locking services are not registered: `set_default_mode`,
  `erase_data`, `send_raw_command`, `set_password`, `verify_password`;
  the raw command field was removed from the controls card

### Fixed
- Camera entity failed to load on current HA (`supported_features` int)
- Clock 24h switch state was never restored (method defined twice)
- Textimage mode: panel firmware fonts mapped to a host font, font sizes
  below 4 px auto-fit (from arcdrake22)
- `set_clock_mode` failed with "Year must be between 0 and 99" since 0.2.1
  (the HA-local date was passed with a 4-digit year; pypixelcolor expects DD/MM/YY)
- `pypixelcolor` pinned to `<0.5`: 0.5.0 removed the panel fonts CUSONG,
  SIMSUN and VCR_OSD_MONO, so fresh installs could not display text
- `add_schedule` always crashed (passed a removed `time_trigger` field)
- `display_image_raw_rgb` needed `aiofiles`, which is not installed; now read
  in the executor and restricted to `allowlist_external_dirs`
- `draw_visuals` animations and playlist loops ran as tracked tasks, which
  blocks HA startup / `async_block_till_done`; now background tasks
- Font glyph cache is memory-only: the disk cache did blocking file I/O in
  the event loop and wrote into the integration directory

## [0.2.1] - 2026-09-28

First version of this fork, based on upstream release
[v0.2.0](https://github.com/cagcoach/ha-ipixel-color/releases/tag/v0.2.0)
(16 Dec 2025; not listed below, see upstream). `manifest.json` said
0.1.1 by mistake.

### Fixed
- BLE connection: stale connection flag, missing reconnect and interleaved
  GATT operations caused lost commands and wrong power state, especially
  with several panels switched together
- Power switch state is now the last confirmed state, restored after HA
  restart and re-applied to the panel after every reconnect
- Blocking font directory scan in the event loop
- Device info: 3 attempts, unknown defaults are not cached
- Text animations 3/4 are refused on non-32x32 panels (boot loop)
- Notifications on BlueZ with bleak >= 1.0 ("Notify acquired")
- Rediscovery when HA lost the device right after a link drop
- Clock time/date use the Home Assistant time zone

### Added
- Background keepalive (reconnect on advertisement / every 60 s)
- Option "Keep connection open" (default on)
- Power switch attributes `connected` and `desired_power`

## [0.1.0] - 2024-11-19

### Added
- Initial release of iPIXEL Color Home Assistant integration
- Bluetooth auto-discovery of iPIXEL devices (`LED_BLE_*` pattern)
- Basic power on/off control via switch entity
- Manual device configuration as fallback
- Proper Home Assistant device registry integration
- Connection management with error handling
- Configuration flow with discovery and manual entry options
- English translations and UI strings

### Technical Details
- Implements core Bluetooth protocol commands based on reverse-engineered documentation
- Uses `bleak` library for cross-platform Bluetooth Low Energy communication
- Follows Home Assistant integration best practices
- Power commands: `[5, 0, 7, 1, 1]` (on) / `[5, 0, 7, 1, 0]` (off)
- Bluetooth UUIDs:
  - Write: `0000fa02-0000-1000-8000-00805f9b34fb`
  - Notify: `0000fa03-0000-1000-8000-00805f9b34fb`

### Limitations
- Only basic power control in this version
- No brightness, color, or image upload features yet
- Single switch entity per device

### Coming in Future Versions
- v0.2.0: Brightness control and basic light entity
- v0.3.0: RGB color control and display modes
- v0.4.0: Image/GIF upload and media player entity
- v1.0.0: Complete feature set and HACS submission