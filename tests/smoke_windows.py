"""Smoke test for the Windows backend; run on a Windows machine or CI runner.

Audio actions are stubbed out, so this does not change the default device or mute.
"""
import sys
import time

from PyQt6.QtWidgets import QApplication

from audioswitch import app as A

b = A.backend
print("outputs:", b.list_sinks())
print("default:", repr(b.default_sink()))
print("mute key:", b.key_name(b.MUTE_KEY), "| F13:", b.key_name(0x7C), "| A:", b.key_name(0x41))

calls = []
b.switch_to = lambda target: calls.append("switch")
b.toggle_mute = lambda: calls.append("mute")
b.notify = lambda *args: None
A.save_config = lambda cfg: None

qa = QApplication(sys.argv)
window = A.Window()  # installs the keyboard hook
assert window.watcher.handle, "hook not installed"


def spin(seconds):
    end = time.time() + seconds
    while time.time() < end:
        qa.processEvents()
        time.sleep(0.01)


# Injected key events must be ignored by the hook (that is how toggle_mute avoids looping).
seen = []
window.watcher.on_key = lambda code, pressed: seen.append(code)
b.user32.keybd_event(0x7C, 0, 0, 0)
b.user32.keybd_event(0x7C, 0, 2, 0)
spin(0.5)
assert seen == [], seen

key = window.cfg["key"]
window.on_key(key, True)
window.on_key(key, False)
assert calls == ["mute"], calls
calls.clear()
window.on_key(key, True)
spin(window.cfg["hold"] + 0.5)
window.on_key(key, False)
assert calls in (["switch"], []), calls  # no switch if fewer than two outputs are configured

b.set_autostart(True)
assert b.autostart_enabled()
b.set_autostart(False)
assert not b.autostart_enabled()
print("ok")
