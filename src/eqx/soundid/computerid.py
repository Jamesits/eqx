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

SoundID Reference on macOS:

    id = "g" + hex(SHA1(UTF-32LE(cpu + "2\\n" + serial + "3\\n")))

``serial`` is ``IOPlatformSerialNumber``.  Each byte is sign-extended to one
UTF-32 unit.  An empty part is left out with its suffix.  ``cpu`` differs
between the arm64 and the x86_64 build; see ``mac_cpu_string`` and
``mac_x86_cpu_string``.

Sonarworks Reference 4 and 3 on macOS (x86_64 and i386 builds only):

    id = "g" + hex(SHA1(UTF-32LE(cpu + "2\\n" + serial + "3\\n")))     # 4
    id =       hex(SHA1(UTF-32LE(cpu + "2\\n" + serial + "3\\n")))     # 3

``cpu`` always comes from CPUID (``mac_cpuid_string``); on Apple silicon it is
what Rosetta reports.
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
    disk_serial: bytes  # empty on a dynamic disk
    board_serial: bytes
    volume_serial: bytes = b""  # decimal
    dynamic_disk: bool = False

    def _source(self, disk: bytes) -> bytes:
        s = self.cpu + b"2\n" if self.cpu else b""
        if disk and disk != INVALID_DISK_SERIAL:
            return s + disk + b"3\n"
        if self.board_serial:
            return s + self.board_serial + b"3\n"
        raise ValueError(
            "motherboard serial is empty and OS disk serial is empty or invalid"
        )

    @property
    def source(self) -> bytes:
        return self._source(self.disk_serial)

    @property
    def value(self) -> str:
        """The SoundID Reference ID."""
        return "g" + _hash(self.source)

    @property
    def sonarworks_reference4_value(self) -> str:
        return "g" + _hash(
            self._source(self.volume_serial if self.dynamic_disk else self.disk_serial)
        )

    @property
    def sonarworks_reference3_value(self) -> str:
        s = self.cpu + b"2\n" if self.cpu else b""
        if self.volume_serial:
            s += self.volume_serial + b"3\n"
        return _hash(s)

    def values(self) -> dict[str, str]:
        """{application: ID}; an ID that cannot be computed is left out."""
        out = {}
        for app, attr in zip(
            APPS,
            ("value", "sonarworks_reference4_value", "sonarworks_reference3_value"),
        ):
            try:
                out[app] = getattr(self, attr)
            except ValueError:
                pass
        return out

    def parts(self) -> list[tuple[str, str]]:
        return [
            ("cpu", self.cpu.decode("latin-1")),
            ("disk serial", repr(self.disk_serial.decode("latin-1"))),
            ("board serial", repr(self.board_serial.decode("latin-1"))),
            ("volume serial", repr(self.volume_serial.decode("latin-1"))),
            ("dynamic disk", str(self.dynamic_disk)),
        ]


# What the x86_64 build assumes for hw.cpufamily, by brand string.
_MAC_X86_FAMILIES = (
    (b"Apple M1", 0x1B588BB3, 0x1B588BB3, 0x1B588BB3),  # base, Pro, Max
    (b"Apple M2", 0xDA33D83D, 0xDA33D83D, 0xDA33D83D),
    (b"Apple M3", 0xFA33415E, 0x5F4DEA93, 0x72015832),
    (b"Apple M4", 0x6F5129AC, 0x17D5B93A, 0x17D5B93A),
)
_CPU_TYPE_ARM64, _CPU_SUBTYPE_ARM64E = 0x100000C, 2


@dataclass
class MacComputerId:
    cpu: bytes
    serial: bytes
    rosetta_cpu: bytes = b""  # cpu part of the x86_64 build on Apple silicon
    cpuid_cpu: bytes = b""  # cpu part of Sonarworks Reference 3 / 4

    @staticmethod
    def _value(cpu: bytes, serial: bytes) -> str:
        return "g" + _mac_hash(cpu, serial)

    @property
    def value(self) -> str:
        """The SoundID Reference ID."""
        return self._value(self.cpu, self.serial)

    def values(self) -> dict[str, str]:
        out = {"SoundID Reference": self.value}
        if self.rosetta_cpu:
            out["SoundID Reference (Rosetta)"] = self._value(
                self.rosetta_cpu, self.serial
            )
        if self.cpuid_cpu:
            out["Sonarworks Reference 4"] = self._value(self.cpuid_cpu, self.serial)
            # Sonarworks Reference 3 reads the serial into a 32-byte C string.
            out["Sonarworks Reference 3"] = _mac_hash(
                self.cpuid_cpu, self.serial if len(self.serial) < 32 else b""
            )
        return out

    def parts(self) -> list[tuple[str, str]]:
        out = [
            ("cpu", repr(self.cpu.rstrip(b"\0").decode("latin-1"))),
            ("serial", repr(self.serial.decode("latin-1"))),
        ]
        if self.rosetta_cpu:
            out.append(
                ("rosetta cpu", repr(self.rosetta_cpu.rstrip(b"\0").decode("latin-1")))
            )
        if self.cpuid_cpu and self.cpuid_cpu != self.cpu:
            out.append(("cpuid cpu", repr(self.cpuid_cpu.decode("latin-1"))))
        return out


def _mac_hash(cpu: bytes, serial: bytes) -> str:
    s = (cpu + b"2\n" if cpu else b"") + (serial + b"3\n" if serial else b"")
    units = struct.pack(f"<{len(s)}i", *(b - 256 if b >= 0x80 else b for b in s))
    return hashlib.sha1(units).hexdigest()


def _mac_brand(brand: bytes | None) -> bytes:
    """``machdep.cpu.brand_string`` as SoundID holds it; None if sysctl failed.

    SoundID keeps the whole zeroed 1024-byte buffer, NULs included.
    """
    return b"Unknown" if brand is None else brand[:1023].ljust(1024, b"\0")


def mac_cpu_string(
    brand: bytes | None, family: int, subfamily: int, cputype: int, cpusubtype: int
) -> bytes:
    """cpu part of the arm64 build from the brand string and the ``hw.cpu*`` sysctls.

    A brand with "Apple M" but not "Apple M1" to "Apple M4" is used as is.
    """
    s = _mac_brand(brand)
    if b"Apple M" in s and not any(b"Apple M%d" % n in s for n in range(1, 5)):
        return s
    return f"{family}{subfamily}{cputype}{cpusubtype}".encode()


def mac_x86_cpu_string(brand: bytes | None, signature: int = 0) -> bytes:
    """cpu part of the x86_64 build (Intel Macs and Rosetta).

    ``signature`` is CPUID leaf 1 EAX (``machdep.cpu.signature``).  On Apple
    silicon the build fakes the arm64 sysctls from the brand string.
    """
    s = _mac_brand(brand)
    variant = 5 if b"Max" in s else 4 if b"Pro" in s else 2
    for name, base, pro, max_ in _MAC_X86_FAMILIES:
        if name in s:
            family = pro if b"Pro" in s else max_ if b"Max" in s else base
            return f"{family}{variant}{_CPU_TYPE_ARM64}{_CPU_SUBTYPE_ARM64E}".encode()
    if b"Apple M" in s:
        return s
    return mac_cpuid_string(signature)


def mac_cpuid_string(signature: int) -> bytes:
    """cpu part from CPUID leaf 1 EAX: family, model, stepping, EAX, cut to 10 characters."""
    eax = signature & 0xFFFFFFFF
    return f"{(eax >> 8) & 0xF}{(eax >> 4) & 0xF}{eax & 0xF}{eax}"[:10].encode()


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


def local() -> ComputerId | MacComputerId:
    """Collect the ID parts of this machine (Windows or macOS)."""
    if sys.platform == "darwin":
        return _local_mac()
    if sys.platform != "win32":
        raise OSError("the computer ID can only be read on Windows or macOS")
    import ctypes
    from ctypes import wintypes as w

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class SystemInfo(ctypes.Structure):
        _fields_ = [
            ("wProcessorArchitecture", w.WORD),
            ("wReserved", w.WORD),
            ("dwPageSize", w.DWORD),
            ("lpMinimumApplicationAddress", ctypes.c_void_p),
            ("lpMaximumApplicationAddress", ctypes.c_void_p),
            ("dwActiveProcessorMask", ctypes.c_size_t),
            ("dwNumberOfProcessors", w.DWORD),
            ("dwProcessorType", w.DWORD),
            ("dwAllocationGranularity", w.DWORD),
            ("wProcessorLevel", w.WORD),
            ("wProcessorRevision", w.WORD),
        ]

    si = SystemInfo()
    k32.GetNativeSystemInfo(ctypes.byref(si))
    cpu = cpu_string(
        si.wProcessorArchitecture, si.wProcessorLevel, si.wProcessorRevision
    )

    k32.CreateFileW.restype = w.HANDLE
    k32.CreateFileW.argtypes = [
        w.LPCWSTR,
        w.DWORD,
        w.DWORD,
        ctypes.c_void_p,
        w.DWORD,
        w.DWORD,
        w.HANDLE,
    ]
    k32.DeviceIoControl.argtypes = [
        w.HANDLE,
        w.DWORD,
        ctypes.c_void_p,
        w.DWORD,
        ctypes.c_void_p,
        w.DWORD,
        ctypes.POINTER(w.DWORD),
        ctypes.c_void_p,
    ]

    def ioctl(handle, code, inbuf: bytes, size: int) -> bytes | None:
        out = ctypes.create_string_buffer(size)
        got = w.DWORD()
        if not k32.DeviceIoControl(
            handle, code, inbuf or None, len(inbuf), out, size, ctypes.byref(got), None
        ):
            return None
        return out.raw[: got.value]

    disk, dynamic, volume = b"", False, b""
    windir = ctypes.create_unicode_buffer(260)
    if k32.GetWindowsDirectoryW(windir, 260):
        handle = k32.CreateFileW(
            "\\\\.\\" + windir.value[:1] + ":", 0, 3, None, 3, 0, None
        )
        if handle not in (None, w.HANDLE(-1).value):
            try:
                disk, dynamic = _disk_serial(ioctl, handle)
            finally:
                k32.CloseHandle(w.HANDLE(handle))
        serial = w.DWORD()
        if k32.GetVolumeInformationW(
            windir.value[:1] + ":\\", None, 0, ctypes.byref(serial), None, None, None, 0
        ):
            volume = str(serial.value).encode()

    board = b""
    size = k32.GetSystemFirmwareTable(0x52534D42, 0, None, 0)  # 'RSMB'
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
    layout = ioctl(
        handle, 0x70050, b"", 144 * 128 + 192
    )  # IOCTL_DISK_GET_DRIVE_LAYOUT_EX
    if layout is None:
        return b"", False
    count = struct.unpack_from("<I", layout, 4)[0]
    for i in range(count):
        entry = 48 + 144 * i
        style = struct.unpack_from("<I", layout, entry)[0]
        if style == 0 and layout[entry + 32] == _LDM_MBR_TYPE:
            return b"", True
        if style == 1 and layout[entry + 32 : entry + 48] == _LDM_DATA_GUID:
            return b"", True
    query = struct.pack("<III", 0, 0, 0)  # StorageDeviceProperty
    desc = ioctl(handle, 0x2D1400, query, 4096)  # IOCTL_STORAGE_QUERY_PROPERTY
    if desc is None or len(desc) < 28:
        return b"", False
    offset = struct.unpack_from("<I", desc, 24)[0]  # SerialNumberOffset
    if not offset:
        return b"", False
    return desc[offset : desc.index(b"\0", offset)], False


def _local_mac() -> MacComputerId:
    import ctypes
    import re
    import subprocess

    libc = ctypes.CDLL(None)
    libc.sysctlbyname.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]

    def sysctl(name: str, size: int) -> bytes | None:
        buf = ctypes.create_string_buffer(size + 1)
        n = ctypes.c_size_t(size)
        if libc.sysctlbyname(name.encode(), buf, ctypes.byref(n), None, 0):
            return None
        return buf.raw[: n.value]

    def u32(name: str) -> int:
        v = sysctl(name, 4)
        return struct.unpack("<I", v)[0] if v and len(v) == 4 else 0

    brand = sysctl("machdep.cpu.brand_string", 1023)
    if brand is not None:
        brand = brand.split(b"\0", 1)[0]
    if not u32("hw.optional.arm64"):
        signature = u32("machdep.cpu.signature")
        cpu, rosetta = mac_x86_cpu_string(brand, signature), b""
        cpuid = mac_cpuid_string(signature)
    else:
        rosetta = mac_x86_cpu_string(brand)
        # Under Rosetta the hw.cpu* sysctls describe the emulated x86 CPU; the
        # x86_64 build's guess from the brand string is the best available.
        translated = u32("sysctl.proc_translated")
        cpu = (
            rosetta
            if translated
            else mac_cpu_string(
                brand,
                u32("hw.cpufamily"),
                u32("hw.cpusubfamily"),
                u32("hw.cputype"),
                u32("hw.cpusubtype"),
            )
        )
        cpuid = _rosetta_cpuid(u32("machdep.cpu.signature") if translated else 0)

    serial = b""
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            check=True,
        ).stdout
        m = re.search(rb'"IOPlatformSerialNumber" = "([^"]*)"', out)
        if m and m.group(1).isascii():
            serial = m.group(1)
    except (OSError, subprocess.CalledProcessError):
        pass
    return MacComputerId(cpu, serial, rosetta, cpuid)


def _rosetta_cpuid(signature: int) -> bytes:
    """CPUID cpu part an x86_64 process sees under Rosetta; empty if unknown.

    A native process asks an x86_64 ``sysctl`` for the signature.
    """
    import subprocess

    if not signature:
        try:
            out = subprocess.run(
                ["arch", "-x86_64", "/usr/sbin/sysctl", "-n", "machdep.cpu.signature"],
                capture_output=True,
                check=True,
            ).stdout
            signature = int(out)
        except (OSError, ValueError, subprocess.CalledProcessError):
            return b""
    return mac_cpuid_string(signature)
