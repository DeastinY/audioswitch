"""audioswitch - hold a key to flip the default audio output between two devices.

Runs in the system tray. Everything that touches the operating system lives in a
backend module (linux.py / windows.py); this file is the shared UI and logic.
"""
import json
import signal
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QMenu, QPushButton, QSlider, QSystemTrayIcon, QVBoxLayout, QWidget,
)

if sys.platform == "win32":
    from . import windows as backend
else:
    from . import linux as backend

APP = "audioswitch"
CONFIG = backend.CONFIG_DIR / "config.json"
DEFAULTS = {"key": backend.MUTE_KEY, "hold": 0.8, "sink_a": "", "sink_b": "", "sidetone": None}


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


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self.capturing = False
        self.fired = False
        self.sidetone_device = None
        icon = QIcon(str(backend.ICON))
        self.setWindowTitle("Audio Switch")
        self.setWindowIcon(icon)

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
        self.autostart.setChecked(backend.autostart_enabled())
        self.autostart.toggled.connect(backend.set_autostart)
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
        self.watcher = backend.KeyWatcher(self.on_key)
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(3000)
        self.show_key()
        self.refresh()

        self.tray = QSystemTrayIcon(icon, self)
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
        """Pick up newly connected outputs and the sidetone device."""
        sinks = backend.list_sinks()
        known = [name for name, _ in sinks]
        for combo, key in ((self.sink_a, "sink_a"), (self.sink_b, "sink_b")):
            if combo.view().isVisible():
                continue
            wanted = list(sinks)
            if self.cfg[key] and self.cfg[key] not in known:
                wanted.append((self.cfg[key], "(disconnected) " + self.cfg[key]))
            if [combo.itemData(i) for i in range(combo.count())] != [name for name, _ in wanted]:
                combo.clear()
                for name, desc in wanted:
                    combo.addItem(desc, name)
            combo.setCurrentIndex(combo.findData(self.cfg[key]))

        device = backend.sidetone_device()
        if device != self.sidetone_device:
            self.sidetone_device = device
            if device is not None and self.cfg["sidetone"] is not None:
                backend.set_sidetone(device, *self.cfg["sidetone"])
        self.sidetone.setEnabled(device is not None)
        self.sidetone_on.setEnabled(device is not None)
        if device is None:
            self.sidetone_label.setText(backend.NO_SIDETONE)
        elif not self.sidetone.isSliderDown():
            percent, on = backend.get_sidetone(device)
            self.sidetone.blockSignals(True)
            self.sidetone_on.blockSignals(True)
            self.sidetone.setValue(percent)
            self.sidetone_on.setChecked(on)
            self.sidetone.blockSignals(False)
            self.sidetone_on.blockSignals(False)
            self.sidetone_label.setText(f"{percent}%")

    def changed(self, *_):
        self.cfg["hold"] = round(self.hold.value(), 2)
        self.cfg["sink_a"] = self.sink_a.currentData() or ""
        self.cfg["sink_b"] = self.sink_b.currentData() or ""
        save_config(self.cfg)

    def sidetone_changed(self, *_):
        if self.sidetone_device is None:
            return
        percent, on = self.sidetone.value(), self.sidetone_on.isChecked()
        backend.set_sidetone(self.sidetone_device, percent, on)
        self.sidetone_label.setText(f"{percent}%")
        self.cfg["sidetone"] = [percent, on]
        save_config(self.cfg)

    # -- hotkey --------------------------------------------------------------

    def show_key(self):
        self.key_button.setText(backend.key_name(self.cfg["key"]))

    def start_capture(self):
        self.capturing = True
        self.key_button.setText("Press a key…")

    def on_key(self, code, pressed):
        if self.capturing:
            if pressed:
                self.capturing = False
                self.cfg["key"] = code
                save_config(self.cfg)
                self.show_key()
                backend.claim_mute_key(code == backend.MUTE_KEY)
            return
        if code != self.cfg["key"]:
            return
        if pressed:
            self.fired = False
            self.hold_timer.start(int(self.cfg["hold"] * 1000))
        else:
            self.hold_timer.stop()
            if not self.fired and code == backend.MUTE_KEY:
                backend.toggle_mute()  # short tap: this app does the muting, see claim_mute_key

    def long_press(self):
        self.fired = True
        self.switch()

    def switch(self):
        sinks = dict(backend.list_sinks())
        a, b = self.cfg["sink_a"], self.cfg["sink_b"]
        target = b if backend.default_sink() == a else a
        if not a or not b or a == b:
            message = "Choose two different outputs first"
        elif target not in sinks:
            message = "Output not connected"
        else:
            backend.switch_to(target)
            QTimer.singleShot(500, lambda: backend.settle(target))
            message = sinks[target]
        self.status.setText(message)
        backend.notify(message, self.tray)

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
        print(f"wrote {backend.install_launcher()}")
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
    backend.claim_mute_key(window.cfg["key"] == backend.MUTE_KEY)
    app.aboutToQuit.connect(lambda: backend.claim_mute_key(False))
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: app.quit())

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
