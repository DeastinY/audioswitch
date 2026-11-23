"""audioswitch - hold a key to flip the default audio output between two sinks.

Runs in the system tray. Keys are read straight from evdev, so the hotkey works
on Wayland (the user must be in the `input` group). Also offers a sidetone slider
for USB headsets that expose an ALSA "Sidetone" control.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import evdev
from evdev import ecodes
from PyQt6.QtCore import QSocketNotifier, Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QMenu, QPushButton, QSlider, QSystemTrayIcon, QVBoxLayout, QWidget,
)

APP = "audioswitch"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP / "config.json"
AUTOSTART = Path.home() / ".config/autostart" / f"{APP}.desktop"
LAUNCHER = Path.home() / ".local/share/applications" / f"{APP}.desktop"
ICON = Path(__file__).with_name("icon.svg")
DEFAULTS = {"key": ecodes.KEY_MUTE, "hold": 0.8, "sink_a": "", "sink_b": "", "sidetone": None}


def run(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))


def desktop_entry(*args):
    """A .desktop file that starts this app, however it was installed."""
    command = shutil.which(APP) or f"{sys.executable} -m {APP}"
    return ("[Desktop Entry]\nType=Application\nName=Audio Switch\n"
            "Comment=Hold a key to switch audio output\n"
            f"Exec={' '.join([command, *args])}\nIcon={ICON}\n"
            "Categories=AudioVideo;Audio;Settings;\n")


def key_name(code):
    name = ecodes.KEY.get(code) or ecodes.BTN.get(code) or f"KEY_{code}"
    if isinstance(name, (list, tuple)):
        name = next((n for n in name if "MIN_INTERESTING" not in n), name[0])
    return name.removeprefix("KEY_").replace("_", " ").title()


# --- audio -----------------------------------------------------------------

def list_sinks():
    try:
        return [(s["name"], s["description"]) for s in json.loads(run("pactl", "-f", "json", "list", "sinks"))]
    except ValueError:
        return []


def toggle_sinks(cfg):
    """Switch default sink between A and B. Returns the description switched to, or an error string."""
    sinks = dict(list_sinks())
    a, b = cfg["sink_a"], cfg["sink_b"]
    if not a or not b or a == b:
        return None, "Choose two different outputs first"
    target = b if run("pactl", "get-default-sink").strip() == a else a
    if target not in sinks:
        return None, "Output not connected"
    run("pactl", "set-default-sink", target)
    for node, _ in playing_streams():
        run("pw-metadata", "-n", "default", str(node), "target.object", target)
    return sinks[target], None


def playing_streams():
    """(node id, sink index) for every playback stream."""
    try:
        streams = json.loads(run("pactl", "-f", "json", "list", "sink-inputs"))
        return [(int(s["properties"]["object.id"]), s["sink"]) for s in streams
                if "object.id" in s.get("properties", {})]
    except (ValueError, TypeError):
        return []


def force_move_stragglers(target):
    """Relink streams the session manager refused to move.

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


def kde_mute_shortcut(enabled):
    """Give the Volume Mute key back to KDE's own Mute action, or take it away.

    KDE mutes the moment the key goes down (and again on every auto-repeat), which
    flickers during a long hold. While this app owns the key it mutes on a short tap itself.
    """
    run("busctl", "--user", "call", "org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel",
        "setForeignShortcut", "asai", "4", "kmix", "mute", "Audio Volume", "Mute",
        *(["1", "16777329"] if enabled else ["0"]))


def notify(text):
    subprocess.Popen(["notify-send", "-a", "Audio Switch", "-i", str(ICON),
                      "-t", "2000", "Audio output", text],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def sidetone_card():
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
                if event.type == ecodes.EV_KEY and event.value in (0, 1):
                    self.on_key(event.code, event.value == 1)
        except BlockingIOError:
            pass
        except OSError:
            self.drop(path)


# --- UI --------------------------------------------------------------------

class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.capturing = False
        self.fired = False
        self.card = None
        self.setWindowTitle("Audio Switch")
        self.setWindowIcon(QIcon(str(ICON)))

        self.key_button = QPushButton()
        self.key_button.clicked.connect(self.start_capture)
        self.hold = QDoubleSpinBox(minimum=0.2, maximum=5.0, singleStep=0.1, suffix=" s")
        self.hold.setValue(self.cfg["hold"])
        self.hold.valueChanged.connect(self.changed)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_button, 1)
        key_row.addWidget(QLabel("hold for"))
        key_row.addWidget(self.hold)

        self.sink_a, self.sink_b = QComboBox(), QComboBox()
        for combo in (self.sink_a, self.sink_b):
            combo.activated.connect(self.changed)

        self.sidetone = QSlider(Qt.Orientation.Horizontal, minimum=0, maximum=100)
        self.sidetone_on = QCheckBox("On")
        self.sidetone_label = QLabel()
        self.sidetone.valueChanged.connect(self.sidetone_changed)
        self.sidetone_on.toggled.connect(self.sidetone_changed)
        side_row = QHBoxLayout()
        side_row.addWidget(self.sidetone_on)
        side_row.addWidget(self.sidetone, 1)
        side_row.addWidget(self.sidetone_label)

        self.autostart = QCheckBox("Start at login")
        self.autostart.setChecked(AUTOSTART.exists())
        self.autostart.toggled.connect(self.set_autostart)
        switch_now = QPushButton("Switch now")
        switch_now.clicked.connect(self.switch)
        self.status = QLabel()
        bottom = QHBoxLayout()
        bottom.addWidget(self.autostart)
        bottom.addStretch(1)
        bottom.addWidget(switch_now)

        form = QFormLayout()
        form.addRow("Hotkey", key_row)
        form.addRow("Output A", self.sink_a)
        form.addRow("Output B", self.sink_b)
        form.addRow("Sidetone", side_row)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addLayout(bottom)
        self.setMinimumWidth(460)

        self.hold_timer = QTimer(singleShot=True)
        self.hold_timer.timeout.connect(self.long_press)
        self.watcher = KeyWatcher(self.on_key)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(3000)
        self.show_key()
        self.refresh()

        self.tray = QSystemTrayIcon(QIcon(str(ICON)), self)
        menu = QMenu()
        for text, slot in (("Switch output", self.switch), ("Settings…", self.reveal),
                           ("Quit", QApplication.quit)):
            action = QAction(text, self)
            action.triggered.connect(slot)
            menu.addAction(action)
        self.tray.setContextMenu(menu)
        self.tray.setToolTip("Audio Switch")
        self.tray.activated.connect(
            lambda reason: reason == QSystemTrayIcon.ActivationReason.Trigger and self.reveal())
        self.tray.show()

    # -- state ---------------------------------------------------------------

    def refresh(self):
        """Pick up newly connected sinks and the sidetone device."""
        sinks = list_sinks()
        for combo, key in ((self.sink_a, "sink_a"), (self.sink_b, "sink_b")):
            if combo.view().isVisible():
                continue
            names = [combo.itemData(i) for i in range(combo.count())]
            wanted = [n for n, _ in sinks]
            if self.cfg[key] and self.cfg[key] not in wanted:
                sinks_for = sinks + [(self.cfg[key], "(disconnected) " + self.cfg[key])]
            else:
                sinks_for = sinks
            if names != [n for n, _ in sinks_for]:
                combo.clear()
                for name, desc in sinks_for:
                    combo.addItem(desc, name)
            index = combo.findData(self.cfg[key])
            combo.setCurrentIndex(index)

        card = sidetone_card()
        if card != self.card:
            self.card = card
            if card is not None and self.cfg["sidetone"] is not None:
                set_sidetone(card, *self.cfg["sidetone"])
        for widget in (self.sidetone, self.sidetone_on):
            widget.setEnabled(card is not None)
        if card is None:
            self.sidetone_label.setText("no device")
        elif not self.sidetone.isSliderDown():
            percent, on = get_sidetone(card)
            for widget, value in ((self.sidetone, percent), (self.sidetone_on, on)):
                widget.blockSignals(True)
                widget.setValue(value) if widget is self.sidetone else widget.setChecked(value)
                widget.blockSignals(False)
            self.sidetone_label.setText(f"{percent}%")

    def changed(self, *_):
        self.cfg["hold"] = round(self.hold.value(), 2)
        self.cfg["sink_a"] = self.sink_a.currentData() or ""
        self.cfg["sink_b"] = self.sink_b.currentData() or ""
        save_config(self.cfg)

    def sidetone_changed(self, *_):
        if self.card is None:
            return
        percent, on = self.sidetone.value(), self.sidetone_on.isChecked()
        set_sidetone(self.card, percent, on)
        self.sidetone_label.setText(f"{percent}%")
        self.cfg["sidetone"] = [percent, on]
        save_config(self.cfg)

    def set_autostart(self, enabled):
        if enabled:
            AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            AUTOSTART.write_text(desktop_entry("--hidden"))
        else:
            AUTOSTART.unlink(missing_ok=True)

    # -- hotkey --------------------------------------------------------------

    def show_key(self):
        self.key_button.setText(key_name(self.cfg["key"]))

    def start_capture(self):
        self.capturing = True
        self.key_button.setText("Press a key…")

    def on_key(self, code, pressed):
        if self.capturing:
            if pressed and code != ecodes.BTN_LEFT:
                self.capturing = False
                self.cfg["key"] = code
                save_config(self.cfg)
                self.show_key()
                kde_mute_shortcut(code != ecodes.KEY_MUTE)
            return
        if code != self.cfg["key"]:
            return
        if pressed:
            self.fired = False
            self.hold_timer.start(int(self.cfg["hold"] * 1000))
        else:
            self.hold_timer.stop()
            if not self.fired and code == ecodes.KEY_MUTE:
                toggle_mute()  # short tap: this app does the muting, see kde_mute_shortcut

    def long_press(self):
        self.fired = True
        self.switch()

    def switch(self):
        target, error = toggle_sinks(self.cfg)
        if not error:
            name = run("pactl", "get-default-sink").strip()
            QTimer.singleShot(500, lambda: force_move_stragglers(name))
        self.status.setText(error or f"Now playing on {target}")
        notify(error or target)

    # -- window --------------------------------------------------------------

    def reveal(self):
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        self.capturing = False
        self.show_key()
        event.ignore()
        self.hide()


def main():
    args = sys.argv[1:]
    if "--install-launcher" in args:
        LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHER.write_text(desktop_entry())
        print(f"wrote {LAUNCHER}")
        return 0
    socket = QLocalSocket()
    socket.connectToServer(APP)
    if socket.waitForConnected(300):  # already running: hand the request over
        socket.write(b"toggle" if "--toggle" in args else b"show")
        socket.waitForBytesWritten(500)
        return 0

    app = QApplication(sys.argv)
    app.setApplicationName(APP)
    app.setDesktopFileName(APP)
    app.setQuitOnLastWindowClosed(False)
    window = Window()
    kde_mute_shortcut(window.cfg["key"] != ecodes.KEY_MUTE)
    app.aboutToQuit.connect(lambda: kde_mute_shortcut(True))
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: app.quit())

    QLocalServer.removeServer(APP)
    server = QLocalServer()

    def incoming():
        conn = server.nextPendingConnection()
        conn.waitForReadyRead(500)
        window.switch() if bytes(conn.readAll()) == b"toggle" else window.reveal()
        conn.close()

    server.newConnection.connect(incoming)
    server.listen(APP)

    if "--toggle" in args:
        window.switch()
    elif "--hidden" not in args:
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
