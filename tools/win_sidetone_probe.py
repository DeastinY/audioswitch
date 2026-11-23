"""Read-only probe: print where Windows exposes a headset's sidetone (mic monitor) control.

Run on Windows with the headset connected:

    uv run --with pycaw tools/win_sidetone_probe.py > probe.txt

It only reads; nothing is changed. The output shows, for every playback device, the
chain of parts behind it and any volume/mute controls, so the sidetone slider can
be implemented for Windows. The interface definitions below are written from the
Windows SDK header (devicetopology.h) and have not been run before; if the script
crashes, the traceback is the useful output.
"""
import ctypes
from ctypes import HRESULT, POINTER, c_float, c_uint, c_void_p
from ctypes.wintypes import BOOL, DWORD, LPCWSTR, LPWSTR

import comtypes
from comtypes import COMMETHOD, GUID, IUnknown
from pycaw.constants import DEVICE_STATE, EDataFlow
from pycaw.pycaw import AudioUtilities


class IPartsList(IUnknown):
    _iid_ = GUID("{6DAA848C-5EB0-45CC-AEA5-998A2CDA1FFB}")


class IDeviceTopology(IUnknown):
    _iid_ = GUID("{2A07407E-6497-4A18-9787-32F79BD0D98F}")


class IConnector(IUnknown):
    _iid_ = GUID("{9C2C4058-23F5-41DE-877A-DF3AF236A09E}")


class IPart(IUnknown):
    _iid_ = GUID("{AE2DE0E4-5BCA-4F2D-AA46-5D13F8FDB3A9}")


IConnector._methods_ = [
    COMMETHOD([], HRESULT, "GetType", (["out"], POINTER(c_uint), "pType")),
    COMMETHOD([], HRESULT, "GetDataFlow", (["out"], POINTER(c_uint), "pFlow")),
    COMMETHOD([], HRESULT, "ConnectTo", (["in"], POINTER(IConnector), "pConnectTo")),
    COMMETHOD([], HRESULT, "Disconnect"),
    COMMETHOD([], HRESULT, "IsConnected", (["out"], POINTER(BOOL), "pbConnected")),
    COMMETHOD([], HRESULT, "GetConnectedTo", (["out"], POINTER(POINTER(IConnector)), "ppConTo")),
    COMMETHOD([], HRESULT, "GetConnectorIdConnectedTo", (["out"], POINTER(LPWSTR), "ppwstrConnectorId")),
    COMMETHOD([], HRESULT, "GetDeviceIdConnectedTo", (["out"], POINTER(LPWSTR), "ppwstrDeviceId")),
]

IPart._methods_ = [
    COMMETHOD([], HRESULT, "GetName", (["out"], POINTER(LPWSTR), "ppwstrName")),
    COMMETHOD([], HRESULT, "GetLocalId", (["out"], POINTER(c_uint), "pnId")),
    COMMETHOD([], HRESULT, "GetGlobalId", (["out"], POINTER(LPWSTR), "ppwstrGlobalId")),
    COMMETHOD([], HRESULT, "GetPartType", (["out"], POINTER(c_uint), "pPartType")),
    COMMETHOD([], HRESULT, "GetSubType", (["out"], POINTER(GUID), "pSubType")),
    COMMETHOD([], HRESULT, "GetControlInterfaceCount", (["out"], POINTER(c_uint), "pCount")),
    COMMETHOD([], HRESULT, "GetControlInterface", (["in"], c_uint, "nIndex"),
              (["out"], POINTER(POINTER(IUnknown)), "ppInterfaceDesc")),
    COMMETHOD([], HRESULT, "EnumPartsIncoming", (["out"], POINTER(POINTER(IPartsList)), "ppParts")),
    COMMETHOD([], HRESULT, "EnumPartsOutgoing", (["out"], POINTER(POINTER(IPartsList)), "ppParts")),
    COMMETHOD([], HRESULT, "GetTopologyObject", (["out"], POINTER(POINTER(IDeviceTopology)), "ppTopology")),
    COMMETHOD([], HRESULT, "Activate", (["in"], DWORD, "dwClsContext"), (["in"], POINTER(GUID), "refiid"),
              (["out"], POINTER(c_void_p), "ppvObject")),
    COMMETHOD([], HRESULT, "RegisterControlChangeCallback", (["in"], POINTER(GUID), "riid"),
              (["in"], c_void_p, "pNotify")),
    COMMETHOD([], HRESULT, "UnregisterControlChangeCallback", (["in"], c_void_p, "pNotify")),
]

IPartsList._methods_ = [
    COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_uint), "pCount")),
    COMMETHOD([], HRESULT, "GetPart", (["in"], c_uint, "nIndex"), (["out"], POINTER(POINTER(IPart)), "ppPart")),
]

IDeviceTopology._methods_ = [
    COMMETHOD([], HRESULT, "GetConnectorCount", (["out"], POINTER(c_uint), "pCount")),
    COMMETHOD([], HRESULT, "GetConnector", (["in"], c_uint, "nIndex"),
              (["out"], POINTER(POINTER(IConnector)), "ppConnector")),
    COMMETHOD([], HRESULT, "GetSubunitCount", (["out"], POINTER(c_uint), "pCount")),
    COMMETHOD([], HRESULT, "GetSubunit", (["in"], c_uint, "nIndex"), (["out"], POINTER(POINTER(IUnknown)), "ppSubunit")),
    COMMETHOD([], HRESULT, "GetPartById", (["in"], c_uint, "nId"), (["out"], POINTER(POINTER(IPart)), "ppPart")),
    COMMETHOD([], HRESULT, "GetDeviceId", (["out"], POINTER(LPWSTR), "ppwstrDeviceId")),
    COMMETHOD([], HRESULT, "GetSignalPath", (["in"], POINTER(IPart), "pIPartFrom"), (["in"], POINTER(IPart), "pIPartTo"),
              (["in"], BOOL, "bRejectMixedPaths"), (["out"], POINTER(POINTER(IPartsList)), "ppParts")),
]


class IPerChannelDbLevel(IUnknown):
    _iid_ = GUID("{C2F8E001-F205-4BC9-99BC-C13B1E048CCB}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetChannelCount", (["out"], POINTER(c_uint), "pcChannels")),
        COMMETHOD([], HRESULT, "GetLevelRange", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfMin"),
                  (["out"], POINTER(c_float), "pfMax"), (["out"], POINTER(c_float), "pfStep")),
        COMMETHOD([], HRESULT, "GetLevel", (["in"], c_uint, "nChannel"), (["out"], POINTER(c_float), "pfLevelDB")),
        COMMETHOD([], HRESULT, "SetLevel", (["in"], c_uint, "nChannel"), (["in"], c_float, "fLevelDB"),
                  (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetLevelUniform", (["in"], c_float, "fLevelDB"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "SetLevelAllChannels", (["in"], POINTER(c_float), "aLevelsDB"), (["in"], c_uint, "cChannels"),
                  (["in"], POINTER(GUID), "pguidEventContext")),
    ]


class IAudioVolumeLevel(IPerChannelDbLevel):
    _iid_ = GUID("{7FB7B48F-531D-44A2-BCB3-5AD5A134B3DC}")
    _methods_ = []


class IAudioMute(IUnknown):
    _iid_ = GUID("{DF45AEEA-B74A-4B6B-AFAD-2366B6AA012E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "SetMute", (["in"], BOOL, "bMuted"), (["in"], POINTER(GUID), "pguidEventContext")),
        COMMETHOD([], HRESULT, "GetMute", (["out"], POINTER(BOOL), "pbMuted")),
    ]


def activate(part, interface):
    try:
        pointer = part.Activate(comtypes.CLSCTX_ALL, ctypes.byref(interface._iid_))
        return ctypes.cast(pointer, POINTER(interface)) if pointer else None
    except comtypes.COMError:
        return None


def describe(part):
    kind = "connector" if part.GetPartType() == 0 else "subunit"
    line = f"{kind} #{part.GetLocalId()} {part.GetName()!r} subtype={part.GetSubType()}"
    volume = activate(part, IAudioVolumeLevel)
    if volume:
        low, high, step = volume.GetLevelRange(0)
        line += f" | VOLUME channels={volume.GetChannelCount()} range={low:.1f}..{high:.1f} dB step={step:.2f} now={volume.GetLevel(0):.1f} dB"
    mute = activate(part, IAudioMute)
    if mute:
        line += f" | MUTE now={bool(mute.GetMute())}"
    return line


def walk(part, depth, seen):
    key = part.GetGlobalId()
    print("  " * depth + describe(part) + (" (seen above)" if key in seen else ""))
    if key in seen or depth > 12:
        return
    seen.add(key)
    try:
        incoming = part.EnumPartsIncoming()
    except comtypes.COMError:
        return  # nothing feeds this part
    for i in range(incoming.GetCount()):
        walk(incoming.GetPart(i), depth + 1, seen)


def main():
    devices = AudioUtilities.GetAllDevices(data_flow=EDataFlow.eRender.value,
                                           device_state=DEVICE_STATE.ACTIVE.value)
    for device in devices:
        print(f"\n=== {device.FriendlyName} ===")
        try:
            topology = device._dev.Activate(IDeviceTopology._iid_, comtypes.CLSCTX_ALL, None)
            topology = topology.QueryInterface(IDeviceTopology)
            endpoint_connector = topology.GetConnector(0)
            adapter_connector = endpoint_connector.GetConnectedTo()
            walk(adapter_connector.QueryInterface(IPart), 1, set())
        except Exception as error:  # keep going: the other devices may still be informative
            print(f"  failed: {error!r}")


if __name__ == "__main__":
    main()
