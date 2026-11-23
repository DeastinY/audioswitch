"""Linux backend: evdev for keys, PipeWire for audio, ALSA for sidetone."""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import evdev
from evdev import ecodes
from PyQt6.QtCore import QSocketNotifier, QTimer

APP = "audioswitch"
ICON = Path(__file__).with_name("icon.svg")
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
AUTOSTART = Path.home() / ".config/autostart" / f"{APP}.desktop"
LAUNCHER = Path.home() / ".local/share/applications" / f"{APP}.desktop"
MUTE_KEY = ecodes.KEY_MUTE
NO_SIDETONE = "no device"


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def key_name(code):
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or f"KEY_{code}"
    if isinstance(name, (list, tuple)):
        name = next((n for n in name if "MIN_INTERESTING" not in n), name[0])
    return name.removeprefix("KEY_").replace("_", " ").title()


# --- audio -----------------------------------------------------------------

def list_sinks():
    """[(id, description)] of the connected outputs."""
    try:
        return [(s["name"], s["description"]) for s in json.loads(run("pactl", "-f", "json", "list", "sinks"))]
    except ValueError:
        return []


def default_sink():
    return run("pactl", "get-default-sink").strip()


def playing_streams():
    """(node id, sink index) for every playback stream."""
    try:
        streams = json.loads(run("pactl", "-f", "json", "list", "sink-inputs"))
        return [(int(s["properties"]["object.id"]), s["sink"]) for s in streams
                if "object.id" in s.get("properties", {})]
    except (ValueError, TypeError):
        return []


def switch_to(target):
    run("pactl", "set-default-sink", target)
    for node, _ in playing_streams():
        run("pw-metadata", "-n", "default", str(node), "target.object", target)


def settle(target):
    """Relink streams the session manager refused to move; call shortly after switch_to.

    Apps that pick an output device themselves (e.g. a Teams call in Firefox) are
    pinned to it, so their ports have to be rewired by hand.
    """
    try:
        sink_index = next(s["index"] for s in json.loads(run("pactl", "-f", "json", "list", "sinks"))
                          if s["name"] == target)
        stuck = [node for node, sink in playing_streams() if sink != sink_index]
        if not stuck:
            return
        objs = json.loads(run("pw-dump"))
    except (ValueError, StopIteration):
        return

    def ports(node, direction):
        return {o["info"]["props"].get("audio.channel"): o["id"] for o in objs
                if o["type"].endswith("Port") and o["info"]["direction"] == direction
                and o["info"]["props"].get("node.id") == node}

    sink_node = next((o["id"] for o in objs if o["type"].endswith("Node")
                      and o["info"]["props"].get("node.name") == target), None)
    inputs = ports(sink_node, "input")
    if not inputs:
        return
    for node in stuck:
        for o in objs:
            if o["type"].endswith("Link") and o["info"]["output-node-id"] == node:
                run("pw-link", "-d", str(o["id"]))
        for channel, port in ports(node, "output").items():
            for dest in ([inputs[channel]] if channel in inputs else inputs.values()):
                run("pw-link", str(port), str(dest))


def toggle_mute():
    """Toggle mute on the default output and show the desktop's on-screen indicator."""
    run("pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle")
    muted = "yes" in run("pactl", "get-sink-mute", "@DEFAULT_SINK@")
    run("busctl", "--user", "call", "org.kde.plasmashell", "/org/kde/osdService",
        "org.kde.osdService", "showText", "ss",
        "audio-volume-muted" if muted else "audio-volume-high", "Muted" if muted else "Unmuted")


def claim_mute_key(claimed):
    """Take the Volume Mute key away from KDE's own Mute action, or give it back.

    KDE mutes the moment the key goes down (and again on every auto-repeat), which
    flickers during a long hold. While this app owns the key it mutes on a short tap itself.
    """
    run("busctl", "--user", "call", "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel",
        "setForeignShortcut", "asai", "4", "kmix", "mute", "Audio Volume", "Mute",
        *(["0"] if claimed else ["1", "16777329"]))


def notify(text, tray):
    subprocess.Popen(["notify-send", "-a", "Audio Switch", "-i", str(ICON), "-t", "2000",
                      "Audio output", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# --- sidetone --------------------------------------------------------------

def sidetone_device():
    """Index of the first ALSA card with a Sidetone control, or None."""
    try:
        cards = re.findall(r"^\s*(\d+)\s+\[", Path("/proc/asound/cards").read_text(), re.M)
    except OSError:
        return None
    for card in cards:
        if "'Sidetone'" in run("amixer", "-c", card, "scontrols"):
            return card
    return None


def get_sidetone(card):
    out = run("amixer", "-c", card, "sget", "Sidetone")
    pct = re.search(r"\[(\d+)%\]", out)
    return (int(pct.group(1)) if pct else 0), "[off]" not in out


def set_sidetone(card, percent, on):
    run("amixer", "-c", card, "sset", "Sidetone", f"{percent}%", "on" if on else "off")


# --- start at login / launcher ---------------------------------------------

def desktop_entry(*args):
    """A .desktop file that starts this app, however it was installed."""
    command = shutil.which(APP) or f"{sys.executable} -m {APP}"
    return ("[Desktop Entry]\nType=Application\nName=Audio Switch\n"
            "Comment=Hold a key to switch audio output\n"
            f"Exec={' '.join([command, *args])}\nIcon={ICON}\n"
            "Categories=AudioVideo;Audio;Settings;\n")


def autostart_enabled():
    return AUTOSTART.exists()


def set_autostart(enabled):
    if enabled:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text(desktop_entry("--hidden"))
    else:
        AUTOSTART.unlink(missing_ok=True)


def install_launcher():
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(desktop_entry())
    return LAUNCHER


# --- hotkey ----------------------------------------------------------------

class KeyWatcher:
    """Reads key events from every keyboard-like evdev device."""

    def __init__(self, on_key):
        self.on_key = on_key          # callback(code, pressed)
        self.devices = {}             # path -> (device, notifier)
        self.rescan_timer = QTimer()
        self.rescan_timer.timeout.connect(self.rescan)
        self.rescan_timer.start(3000)
        self.rescan()

    def rescan(self):
        paths = set(evdev.list_devices())
        for path in list(self.devices):
            if path not in paths:
                self.drop(path)
        for path in paths - set(self.devices):
            try:
                dev = evdev.InputDevice(path)
                if ecodes.EV_KEY not in dev.capabilities():
                    dev.close()
                    continue
            except OSError:
                continue
            notifier = QSocketNotifier(dev.fd, QSocketNotifier.Type.Read)
            notifier.activated.connect(lambda *_, p=path: self.read(p))
            self.devices[path] = (dev, notifier)

    def drop(self, path):
        dev, notifier = self.devices.pop(path)
        notifier.setEnabled(False)
        notifier.deleteLater()
        try:
            dev.close()
        except OSError:
            pass

    def read(self, path):
        if path not in self.devices:
            return
        try:
            for event in self.devices[path][0].read():
                is_mouse = ecodes.BTN_MOUSE <= event.code <= ecodes.BTN_TASK
                if event.type == ecodes.EV_KEY and event.value in (0, 1) and not is_mouse:
                    self.on_key(event.code, event.value == 1)
        except BlockingIOError:
            pass
        except OSError:
            self.drop(path)
