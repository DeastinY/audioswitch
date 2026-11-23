# audioswitch

[![check](https://github.com/DeastinY/audioswitch/actions/workflows/check.yml/badge.svg)](https://github.com/DeastinY/audioswitch/actions/workflows/check.yml)
[![PyPI](https://img.shields.io/pypi/v/audioswitch)](https://pypi.org/project/audioswitch/)
![python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![linux](https://img.shields.io/badge/Linux-PipeWire-FCC624?logo=linux&logoColor=black)
![windows](https://img.shields.io/badge/Windows-10%20%2F%2011-0078D6)
![vibecoded](https://img.shields.io/badge/vibecoded-with%20Claude-d97757)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> **Disclaimer:** this project was written with AI assistance (Claude). It is a small personal tool; review it before relying on it.

Hold a key to flip your audio output between two devices — for example, hold the
mute button for a second to jump between speakers and a headset. A short tap of
the same key still mutes.

A small tray app with a settings window:

- **Hotkey** — click and press any key; the hold time is adjustable.
- **Output A / B** — the two devices to switch between. Playing streams move along.
- **Sidetone** (Linux) — on/off and level for headsets that expose a `Sidetone` mixer control.
- **Start at login**.

| | Linux | Windows |
|---|---|---|
| Hold-to-switch hotkey, tap to mute | yes | yes |
| Moves streams an app pinned to a device (e.g. a browser call) | yes | no |
| Sidetone slider | yes | not yet |
| Status | used daily | checked in CI only, not yet on real hardware |

## Install

**Linux or Windows, with [uv](https://docs.astral.sh/uv/):**

```sh
uv tool install audioswitch
audioswitch --install-launcher   # optional: add it to the application / Start menu
audioswitch
```

Update with `uv tool upgrade audioswitch`, remove with `uv tool uninstall audioswitch`.

**Windows, without installing anything:** download `audioswitch.exe` from the
[latest release](https://github.com/DeastinY/audioswitch/releases/latest) and run it.

**Arch Linux:** a `PKGBUILD` is in [`packaging/aur`](packaging/aur).

The app starts itself at login from the first run on; untick "Start at login" in
its window if you do not want that.

### Linux requirements

- PipeWire with `pipewire-pulse` and WirePlumber, and the tools `pactl`,
  `pw-metadata`, `pw-link`, `pw-dump` (packages `pipewire`, `libpulse` on Arch).
- `amixer` (`alsa-utils`) for the sidetone slider.
- Read access to `/dev/input/event*` for the hotkey. Usually that means being in
  the `input` group: `sudo usermod -aG input $USER`, then log out and back in.
  Note that this lets your user's programs read all keyboard input.

### Windows requirements

Windows 10 or 11. Nothing else; uv fetches Python and the dependencies.

## Which headsets and speakers work?

**Switching** works with any two outputs your system lists: USB headsets, USB
DACs and speakers, onboard audio, HDMI, Bluetooth.

**The sidetone slider** works with headsets that implement mic monitoring as a
standard USB audio control, which Linux shows as a mixer control named
`Sidetone`. To check yours:

```sh
amixer -c <card number> scontrols | grep -i sidetone   # card numbers: cat /proc/asound/cards
```

| Headset | Sidetone slider | Source |
|---|---|---|
| Sony INZONE H6 Air (USB audio box) | works | tested |
| Sony INZONE H3, INZONE E9 | expected | same plain-USB-audio design as the H6 Air; untested |
| Corsair HS80, HS70 (wired), Virtuoso | reported to expose sidetone in alsamixer | [HeadsetControl README](https://github.com/Sapd/HeadsetControl) |

Headsets that set sidetone over a proprietary USB channel instead (many
SteelSeries Arctis, Logitech G and wireless Corsair models) do not show the
control; use [HeadsetControl](https://github.com/Sapd/HeadsetControl) for those.
Switching still works with them.

If your headset works, an issue or pull request adding it to the table is welcome.

## Usage

```sh
audioswitch            # open the settings window (starts the tray app if needed)
audioswitch --hidden   # start in the tray only
audioswitch --toggle   # switch output now
```

Closing the window keeps the app in the tray; quit from the tray menu.

## How it works

- **Linux:** keys are read from evdev, so the hotkey works on Wayland and X11
  alike. Switching sets the default sink, retargets every playing stream, and
  rewires by hand any stream the session manager refuses to move.
- **Linux, KDE Plasma:** if the hotkey is the Volume Mute key, the app unassigns
  Plasma's own Mute shortcut while it runs and mutes on a short tap itself.
  Otherwise Plasma would mute the moment the key goes down and flicker during a
  long hold. The shortcut is handed back when the app quits. On other desktops a
  long hold of the mute key also toggles mute; pick a different key there.
- **Windows:** a low-level keyboard hook watches the key and swallows the mute
  key so a long hold does not mute; a short tap is replayed so Windows mutes as
  usual. The default device is changed through the same unofficial interface
  other device switchers use (via [pycaw](https://github.com/AndreMiras/pycaw)).

Settings live in `~/.config/audioswitch/config.json` (Linux) or
`%APPDATA%\audioswitch\config.json` (Windows).

## Releasing

Bump `version` in `pyproject.toml`, commit, then `git tag vX.Y.Z && git push --tags`.
The release workflow builds `audioswitch.exe` and creates the GitHub release; it
also publishes to PyPI and the AUR once those are enabled (see the comments in
[`workflow.yaml`](.github/workflows/workflow.yaml)).

## License

MIT
