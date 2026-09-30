"""SoundID computer ID (version 1), the password of downloaded ``*.swhp`` bodies.

    id = "g" + hex(SHA1(UTF-16LE(cpu + "2\\n" + disk + "3\\n")))

``disk`` is the serial number of the physical disk holding the Windows
directory.  When it is empty or ``0000_0000_0000_0000.``, the SMBIOS baseboard
serial number is used instead.  Each byte is widened to one UTF-16 unit, not
decoded.
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


@dataclass
class ComputerId:
    cpu: bytes
    disk_serial: bytes
    board_serial: bytes

    @property
    def source(self) -> bytes:
        s = self.cpu + b"2\n" if self.cpu else b""
        if self.disk_serial and self.disk_serial != INVALID_DISK_SERIAL:
            return s + self.disk_serial + b"3\n"
        if self.board_serial:
            return s + self.board_serial + b"3\n"
        raise ValueError("motherboard serial is empty and OS disk serial is empty or invalid")

    @property
    def value(self) -> str:
        return "g" + hashlib.sha1(self.source.decode("latin-1").encode("utf-16-le")).hexdigest()


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

    disk = b""
    windir = ctypes.create_unicode_buffer(260)
    if k32.GetWindowsDirectoryW(windir, 260):
        handle = k32.CreateFileW("\\\\.\\" + windir.value[:1] + ":", 0, 3, None, 3, 0, None)
        if handle not in (None, w.HANDLE(-1).value):
            try:
                disk = _disk_serial(ioctl, handle)
            finally:
                k32.CloseHandle(w.HANDLE(handle))

    board = b""
    size = k32.GetSystemFirmwareTable(0x52534D42, 0, None, 0)       # 'RSMB'
    if size:
        buf = ctypes.create_string_buffer(size)
        if k32.GetSystemFirmwareTable(0x52534D42, 0, buf, size):
            try:
                board = smbios_board_serial(buf.raw)
            except ValueError:
                pass
    return ComputerId(cpu, disk, board)


def _disk_serial(ioctl, handle) -> bytes:
    # A volume on a dynamic disk has no usable serial.
    layout = ioctl(handle, 0x70050, b"", 144 * 128 + 192)          # IOCTL_DISK_GET_DRIVE_LAYOUT_EX
    if layout is None:
        return b""
    count = struct.unpack_from("<I", layout, 4)[0]
    for i in range(count):
        entry = 48 + 144 * i
        style = struct.unpack_from("<I", layout, entry)[0]
        if style == 0 and layout[entry + 32] == _LDM_MBR_TYPE:
            return b""
        if style == 1 and layout[entry + 32:entry + 48] == _LDM_DATA_GUID:
            return b""
    query = struct.pack("<III", 0, 0, 0)                            # StorageDeviceProperty
    desc = ioctl(handle, 0x2D1400, query, 4096)                     # IOCTL_STORAGE_QUERY_PROPERTY
    if desc is None or len(desc) < 28:
        return b""
    offset = struct.unpack_from("<I", desc, 24)[0]                  # SerialNumberOffset
    if not offset:
        return b""
    return desc[offset:desc.index(b"\0", offset)]
