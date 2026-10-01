"""Sonarworks computer IDs, the passwords of downloaded ``*.swhp`` bodies.

SoundID Reference and Sonarworks Reference 4 (version 1):

    id = "g" + hex(SHA1(UTF-16LE(cpu + "2\\n" + disk + "3\\n")))

``disk`` is the serial number of the physical disk holding the Windows
directory.  When it is empty or ``0000_0000_0000_0000.``, the SMBIOS baseboard
serial number is used instead.  On a dynamic disk SoundID has no disk serial;
Sonarworks Reference 4 uses the volume serial number.

Sonarworks Reference 3:

    id = hex(SHA1(UTF-16LE(cpu + "2\\n" + volume + "3\\n")))

``volume`` is the decimal volume serial number of the Windows drive.  Each byte
is widened to one UTF-16 unit, not decoded.
"""

from __future__ import annotations

import hashlib
import struct
import sys
from dataclasses import dataclass

INVALID_DISK_SERIAL = b"0000_0000_0000_0000."

# GPT partition type of a dynamic-disk (LDM) data partition.
_LDM_DATA_GUID = bytes.fromhex("a0609baf3114624fbc683311714a69ad")
_LDM_MBR_TYPE = 0x42


APPS = ("SoundID Reference", "Sonarworks Reference 4", "Sonarworks Reference 3")


def _hash(source: bytes) -> str:
    return hashlib.sha1(source.decode("latin-1").encode("utf-16-le")).hexdigest()


@dataclass
class ComputerId:
    cpu: bytes
    disk_serial: bytes                  # empty on a dynamic disk
    board_serial: bytes
    volume_serial: bytes = b""          # decimal
    dynamic_disk: bool = False

    def _source(self, disk: bytes) -> bytes:
        s = self.cpu + b"2\n" if self.cpu else b""
        if disk and disk != INVALID_DISK_SERIAL:
            return s + disk + b"3\n"
        if self.board_serial:
            return s + self.board_serial + b"3\n"
        raise ValueError("motherboard serial is empty and OS disk serial is empty or invalid")

    @property
    def source(self) -> bytes:
        return self._source(self.disk_serial)

    @property
    def value(self) -> str:
        """The SoundID Reference ID."""
        return "g" + _hash(self.source)

    @property
    def sonarworks_reference4_value(self) -> str:
        return "g" + _hash(self._source(self.volume_serial if self.dynamic_disk
                                        else self.disk_serial))

    @property
    def sonarworks_reference3_value(self) -> str:
        s = self.cpu + b"2\n" if self.cpu else b""
        if self.volume_serial:
            s += self.volume_serial + b"3\n"
        return _hash(s)

    def values(self) -> dict[str, str]:
        """{application: ID}; an ID that cannot be computed is left out."""
        out = {}
        for app, attr in zip(APPS, ("value", "sonarworks_reference4_value", "sonarworks_reference3_value")):
            try:
                out[app] = getattr(self, attr)
            except ValueError:
                pass
        return out


def cpu_string(arch: int, level: int, revision: int) -> bytes:
    """Decimal concatenation of the ``SYSTEM_INFO`` processor fields.

    Only x86 uses ``wProcessorLevel``; every other architecture gets family 0
    (or 1 for IA-64), so on x64 the model and stepping are always 0.
    """
    family = level if arch == 0 else int(arch == 6)
    model = stepping = 0
    if family in (3, 4):
        if revision >> 8 == 0xFF:
            model, stepping = ((revision & 0xFF) >> 4) - 10, revision & 0xF
        else:
            stepping = (revision >> 8) + 65
    elif family in (5, 6, 15):
        model, stepping = revision >> 8, revision & 0xFF
    return f"{family}{model & 0xFFFF}{stepping & 0xFFFF}{arch}{revision}".encode()


def smbios_board_serial(table: bytes) -> bytes:
    """Serial number string of the first type-2 (baseboard) structure."""
    end = 8 + struct.unpack_from("<I", table, 4)[0]
    p = 8
    while p + 4 <= end:
        stype, length = table[p], table[p + 1]
        q = p + length
        strings = []
        while q < end and table[q] != 0:
            z = table.index(b"\0", q)
            strings.append(table[q:z])
            q = z + 1
        q += 1 if strings else 2
        if stype == 2:
            index = table[p + 7]
            if index == 0 or index > len(strings):
                raise ValueError("motherboard serial not available")
            return strings[index - 1]
        if stype == 127:
            break
        p = q
    raise ValueError("motherboard serial not found in the SMBIOS table")


def local() -> ComputerId:
    """Collect the ID parts of this machine (Windows only)."""
    if sys.platform != "win32":
        raise OSError("the computer ID can only be read on Windows")
    import ctypes
    from ctypes import wintypes as w

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SystemInfo(ctypes.Structure):
        _fields_ = [
            ("wProcessorArchitecture", w.WORD), ("wReserved", w.WORD),
            ("dwPageSize", w.DWORD), ("lpMinimumApplicationAddress", ctypes.c_void_p),
            ("lpMaximumApplicationAddress", ctypes.c_void_p),
            ("dwActiveProcessorMask", ctypes.c_size_t), ("dwNumberOfProcessors", w.DWORD),
            ("dwProcessorType", w.DWORD), ("dwAllocationGranularity", w.DWORD),
            ("wProcessorLevel", w.WORD), ("wProcessorRevision", w.WORD),
        ]

    si = SystemInfo()
    k32.GetNativeSystemInfo(ctypes.byref(si))
    cpu = cpu_string(si.wProcessorArchitecture, si.wProcessorLevel, si.wProcessorRevision)

    k32.CreateFileW.restype = w.HANDLE
    k32.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                w.DWORD, w.DWORD, w.HANDLE]
    k32.DeviceIoControl.argtypes = [w.HANDLE, w.DWORD, ctypes.c_void_p, w.DWORD,
                                    ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
                                    ctypes.c_void_p]

    def ioctl(handle, code, inbuf: bytes, size: int) -> bytes | None:
        out = ctypes.create_string_buffer(size)
        got = w.DWORD()
        if not k32.DeviceIoControl(handle, code, inbuf or None, len(inbuf), out, size,
                                   ctypes.byref(got), None):
            return None
        return out.raw[:got.value]

    disk, dynamic, volume = b"", False, b""
    windir = ctypes.create_unicode_buffer(260)
    if k32.GetWindowsDirectoryW(windir, 260):
        handle = k32.CreateFileW("\\\\.\\" + windir.value[:1] + ":", 0, 3, None, 3, 0, None)
        if handle not in (None, w.HANDLE(-1).value):
            try:
                disk, dynamic = _disk_serial(ioctl, handle)
            finally:
                k32.CloseHandle(w.HANDLE(handle))
        serial = w.DWORD()
        if k32.GetVolumeInformationW(windir.value[:1] + ":\\", None, 0, ctypes.byref(serial),
                                     None, None, None, 0):
            volume = str(serial.value).encode()

    board = b""
    size = k32.GetSystemFirmwareTable(0x52534D42, 0, None, 0)       # 'RSMB'
    if size:
        buf = ctypes.create_string_buffer(size)
        if k32.GetSystemFirmwareTable(0x52534D42, 0, buf, size):
            try:
                board = smbios_board_serial(buf.raw)
            except ValueError:
                pass
    return ComputerId(cpu, disk, board, volume, dynamic)


def _disk_serial(ioctl, handle) -> tuple[bytes, bool]:
    """(serial, on a dynamic disk); a volume on a dynamic disk has no usable serial."""
    layout = ioctl(handle, 0x70050, b"", 144 * 128 + 192)          # IOCTL_DISK_GET_DRIVE_LAYOUT_EX
    if layout is None:
        return b"", False
    count = struct.unpack_from("<I", layout, 4)[0]
    for i in range(count):
        entry = 48 + 144 * i
        style = struct.unpack_from("<I", layout, entry)[0]
        if style == 0 and layout[entry + 32] == _LDM_MBR_TYPE:
            return b"", True
        if style == 1 and layout[entry + 32:entry + 48] == _LDM_DATA_GUID:
            return b"", True
    query = struct.pack("<III", 0, 0, 0)                            # StorageDeviceProperty
    desc = ioctl(handle, 0x2D1400, query, 4096)                     # IOCTL_STORAGE_QUERY_PROPERTY
    if desc is None or len(desc) < 28:
        return b"", False
    offset = struct.unpack_from("<I", desc, 24)[0]                  # SerialNumberOffset
    if not offset:
        return b"", False
    return desc[offset:desc.index(b"\0", offset)], False
