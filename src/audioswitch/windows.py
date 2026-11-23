"""Windows backend: low-level keyboard hook for keys, Core Audio (pycaw) for devices.

Sidetone is not implemented here yet.
"""
import ctypes
import os
import shutil
import sys
import warnings
import winreg
from ctypes import wintypes
from pathlib import Path

from comtypes import COMError
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QSystemTrayIcon
from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
from pycaw.pycaw import AudioUtilities

APP = "audioswitch"
ICON = Path(__file__).with_name("icon.svg")
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / APP
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTE_KEY = 0xAD  # VK_VOLUME_MUTE
NO_SIDETONE = "not on Windows yet"

KEY_NAMES = {
    0xAD: "Mute", 0xAE: "Volume Down", 0xAF: "Volume Up", 0xB0: "Next Track",
    0xB1: "Previous Track", 0xB2: "Stop", 0xB3: "Play/Pause", 0x13: "Pause",
    0x91: "Scroll Lock", 0x2C: "Print Screen", 0x2D: "Insert", 0x14: "Caps Lock",
    **{0x70 + i: f"F{i + 1}" for i in range(24)},
}

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HMODULE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

_swallow_mute = False


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


def key_name(code):
    if code in KEY_NAMES:
        return KEY_NAMES[code]
    buf = ctypes.create_unicode_buffer(64)
    scan = user32.MapVirtualKeyW(code, 0)
    if scan and user32.GetKeyNameTextW(scan << 16, buf, 64):
        return buf.value
    return f"Key 0x{code:02X}"


# --- audio -----------------------------------------------------------------

def list_sinks():
    """[(id, description)] of the connected outputs."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        devices = AudioUtilities.GetAllDevices(data_flow=EDataFlow.eRender.value,
                                               device_state=DEVICE_STATE.ACTIVE.value)
    return [(d.id, d.FriendlyName or d.id) for d in devices]


def default_sink():
    try:
        device = AudioUtilities.GetSpeakers()
    except (OSError, COMError):  # no output device at all
        return ""
    return device.id if device else ""


def switch_to(target):
    # Streams that follow the default device move by themselves on Windows.
    AudioUtilities.SetDefaultDevice(
        target, roles=[ERole.eConsole, ERole.eMultimedia, ERole.eCommunications])


def settle(target):
    """Nothing to do: Windows offers no way to move streams an app pinned to a device."""


def toggle_mute():
    """Replay the mute key so Windows mutes and shows its own volume flyout.

    Injected events are ignored by our hook, so this does not loop.
    """
    user32.keybd_event(MUTE_KEY, 0, 0, 0)
    user32.keybd_event(MUTE_KEY, 0, 2, 0)  # KEYEVENTF_KEYUP


def claim_mute_key(claimed):
    """While claimed, the hook swallows the physical mute key and the app mutes on a short tap."""
    global _swallow_mute
    _swallow_mute = claimed


def notify(text, tray):
    tray.showMessage("Audio output", text, QSystemTrayIcon.MessageIcon.NoIcon, 2000)


# --- sidetone (not implemented) ---------------------------------------------

def sidetone_device():
    return None


def get_sidetone(device):
    return 0, False


def set_sidetone(device, percent, on):
    pass


# --- start at login / launcher ---------------------------------------------

def _command(*args):
    exe = shutil.which(APP)
    base = f'"{exe}"' if exe else f'"{sys.executable}" -m {APP}'
    return " ".join([base, *args])


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP, 0, winreg.REG_SZ, _command("--hidden"))
        else:
            try:
                winreg.DeleteValue(key, APP)
            except OSError:
                pass


def install_launcher():
    """Create a Start Menu shortcut."""
    import comtypes.client
    menu = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
    path = menu / "Audio Switch.lnk"
    shortcut = comtypes.client.CreateObject("WScript.Shell").CreateShortcut(str(path))
    exe = shutil.which(APP)
    shortcut.TargetPath = exe or sys.executable
    shortcut.Arguments = "" if exe else f"-m {APP}"
    shortcut.Save()
    return path


# --- hotkey ----------------------------------------------------------------

class KeyWatcher:
    """Global low-level keyboard hook. Must be created on the GUI thread (it needs a message loop)."""

    def __init__(self, on_key):
        self.on_key = on_key  # callback(code, pressed)
        self.held = set()
        self.proc = HOOKPROC(self.hook)  # keep a reference so it is not garbage collected
        self.handle = user32.SetWindowsHookExW(13, self.proc, kernel32.GetModuleHandleW(None), 0)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def hook(self, n_code, w_param, l_param):
        if n_code == 0:
            event = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not event.flags & 0x10:  # skip injected events (see toggle_mute)
                code = event.vkCode
                if w_param in (0x100, 0x104):  # key down; auto-repeat sends it again
                    if code not in self.held:
                        self.held.add(code)
                        self.emit(code, True)
                elif w_param in (0x101, 0x105):
                    self.held.discard(code)
                    self.emit(code, False)
                if _swallow_mute and code == MUTE_KEY:
                    return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def emit(self, code, pressed):
        # Windows drops hooks that are slow to return, so do the work after returning.
        QTimer.singleShot(0, lambda: self.on_key(code, pressed))
