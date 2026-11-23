# audioswitch

> **Disclaimer:** this project was written with AI assistance (Claude). It is a small personal tool; review it before relying on it.

Hold a key to flip your audio output between two devices — for example, hold the
mute button for a second to jump between speakers and a headset. A short tap of
the same key still mutes.

A small tray app with a settings window:

- **Hotkey** — click and press any key; the hold time is adjustable.
- **Output A / B** — the two devices to switch between. Playing streams move
  along, including ones an app has pinned to a device (e.g. a call in a browser).
- **Sidetone** — on/off and level, for USB headsets that expose a `Sidetone`
  mixer control (e.g. Sony INZONE H6 Air).
- **Start at login**.

Currently Linux only (PipeWire). A Windows version is planned.

## Install

With [uv](https://docs.astral.sh/uv/):

```sh
uv tool install git+https://github.com/DeastinY/audioswitch
audioswitch --install-launcher   # optional: add it to the application menu
audioswitch
```

Update with `uv tool upgrade audioswitch`, remove with `uv tool uninstall audioswitch`.

### Requirements

- PipeWire with `pipewire-pulse` and WirePlumber, and the tools `pactl`,
  `pw-metadata`, `pw-link`, `pw-dump` (packages `pipewire`, `libpulse` on Arch).
- `amixer` (`alsa-utils`) for the sidetone slider.
- Read access to `/dev/input/event*` for the hotkey. Usually that means being in
  the `input` group: `sudo usermod -aG input $USER`, then log out and back in.
  Note that this lets your user's programs read all keyboard input.

## Usage

```sh
audioswitch            # open the settings window (starts the tray app if needed)
audioswitch --hidden   # start in the tray only
audioswitch --toggle   # switch output now
```

Closing the window keeps the app in the tray; quit from the tray menu.

## How it works

- Keys are read from evdev, so the hotkey works on Wayland and X11 alike.
- If the hotkey is the Volume Mute key on KDE Plasma, the app unassigns Plasma's
  own Mute shortcut while it runs and mutes on a short tap itself. Otherwise
  Plasma would mute the moment the key goes down and flicker during a long hold.
  The shortcut is handed back when the app quits. On other desktops a long hold
  of the mute key will also toggle mute; pick a different key there.
- Switching sets the default sink, retargets every playing stream, and rewires
  by hand any stream the session manager refuses to move.

Settings live in `~/.config/audioswitch/config.json`.

## License

MIT
