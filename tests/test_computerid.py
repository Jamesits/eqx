import hashlib
import struct
import sys
import unittest

from eqx.soundid import computerid, crypto, peqb


def smbios(*structures: bytes) -> bytes:
    data = b"".join(structures)
    return struct.pack("<BBBBI", 0, 3, 4, 0, len(data)) + data


class ComputerIdTests(unittest.TestCase):
    def test_value(self):
        cid = computerid.ComputerId(b"000929954", b"E823_8FA6.", b"BOARD")
        source = "0009299542\nE823_8FA6.3\n"
        want = "g" + hashlib.sha1(source.encode("utf-16-le")).hexdigest()
        self.assertEqual(cid.value, want)
        self.assertEqual(len(cid.value), 41)

    def test_board_serial_fallback(self):
        for disk in (b"", computerid.INVALID_DISK_SERIAL):
            with self.subTest(disk=disk):
                cid = computerid.ComputerId(b"0009", disk, b"BOARD")
                self.assertEqual(cid.source, b"00092\nBOARD3\n")
        with self.assertRaises(ValueError):
            _ = computerid.ComputerId(b"0009", b"", b"").source

    def test_cpu_string(self):
        self.assertEqual(computerid.cpu_string(9, 25, 0x2102), b"00098450")  # x64
        self.assertEqual(computerid.cpu_string(0, 6, 0x2102), b"633208450")  # x86

    def test_smbios_board_serial(self):
        bios = bytes([0, 4, 0, 0]) + b"\0\0"  # no strings
        board = bytes([2, 8, 1, 0, 1, 2, 3, 2]) + b"Maker\0SN123\0\0"
        self.assertEqual(computerid.smbios_board_serial(smbios(bios, board)), b"SN123")
        with self.assertRaises(ValueError):
            computerid.smbios_board_serial(smbios(bios))

    def test_mac_value(self):
        cid = computerid.MacComputerId(b"4587877634167772282", b"C02XK0AAJG5H")
        source = "45878776341677722822\nC02XK0AAJG5H3\n"
        self.assertEqual(
            cid.value, "g" + hashlib.sha1(source.encode("utf-32-le")).hexdigest()
        )
        self.assertEqual(
            computerid.MacComputerId(b"", b"SN").value,
            "g" + hashlib.sha1("SN3\n".encode("utf-32-le")).hexdigest(),
        )
        # bytes are sign-extended
        self.assertEqual(
            computerid.MacComputerId(b"\xe9", b"").value,
            "g" + hashlib.sha1(b"\xe9\xff\xff\xff2\0\0\0\n\0\0\0").hexdigest(),
        )

    def test_mac_reference_values(self):
        cpuid = computerid.mac_cpuid_string(0x906EA)
        self.assertEqual(cpuid, b"6141059159")
        values = computerid.MacComputerId(
            b"cpu", b"C02XK0AAJG5H", cpuid_cpu=cpuid
        ).values()
        h = hashlib.sha1("61410591592\nC02XK0AAJG5H3\n".encode("utf-32-le")).hexdigest()
        self.assertEqual(values["Sonarworks Reference 4"], "g" + h)
        self.assertEqual(values["Sonarworks Reference 3"], h)
        self.assertNotIn(
            "Sonarworks Reference 4", computerid.MacComputerId(b"cpu", b"SN").values()
        )
        # a serial of 32 bytes or more does not fit Sonarworks Reference 3's buffer
        values = computerid.MacComputerId(b"", b"S" * 32, cpuid_cpu=b"1").values()
        self.assertEqual(
            values["Sonarworks Reference 3"],
            hashlib.sha1("12\n".encode("utf-32-le")).hexdigest(),
        )

    def test_mac_cpu_string(self):
        self.assertEqual(
            computerid.mac_cpu_string(b"Apple M1 Pro", 0x1B588BB3, 4, 0x100000C, 2),
            b"4587877634167772282",
        )
        self.assertEqual(
            computerid.mac_cpu_string(b"Apple M3", 0xFA33415E, 2, 0x100000C, 2),
            b"41976630702167772282",
        )
        m5 = computerid.mac_cpu_string(b"Apple M5", 1, 2, 3, 4)
        self.assertEqual(m5, b"Apple M5".ljust(1024, b"\0"))
        self.assertEqual(computerid.mac_cpu_string(None, 1, 2, 3, 4), b"1234")

    def test_mac_x86_cpu_string(self):
        x86 = computerid.mac_x86_cpu_string
        self.assertEqual(x86(b"Apple M1"), b"4587877632167772282")
        self.assertEqual(x86(b"Apple M1 Max"), b"4587877635167772282")
        self.assertEqual(x86(b"Apple M2 Pro"), b"36608307814167772282")
        self.assertEqual(x86(b"Apple M3 Pro"), b"15989418434167772282")
        self.assertEqual(x86(b"Apple M3 Max"), b"19126907385167772282")
        self.assertEqual(x86(b"Apple M4 Max"), b"3998825545167772282")
        self.assertEqual(x86(b"Apple M5"), b"Apple M5".ljust(1024, b"\0"))
        intel = b"Intel(R) Core(TM) i7-8700B CPU @ 3.20GHz"
        self.assertEqual(x86(intel, 0x906EA), b"6141059159")  # "61410591594" cut
        self.assertEqual(x86(intel, 0x6FB), b"615111787")
        self.assertEqual(x86(None), b"0000")

    @unittest.skipUnless(sys.platform in ("win32", "darwin"), "Windows or macOS only")
    def test_local(self):
        cid = computerid.local()
        self.assertRegex(cid.value, r"^g[0-9a-f]{40}$")


class SwhpDecryptTests(unittest.TestCase):
    def test_round_trip(self):
        cid = "g" + "0" * 40
        body = (
            struct.pack("<I", 1)
            + struct.pack("<IddI", 1, 0.5, 2.0, 1)
            + struct.pack("<3d", 20.0, 1.0, 0.001)
            + peqb._write_map([("META_IsAverage", "1")])
        )
        key = crypto.swhp_key(cid)
        blob = (
            peqb.MAGIC
            + bytes((3, 0, 0, 1, 1))
            + crypto.encrypt(key, peqb.BODY_HEADER + body)
        )
        p = peqb.read(blob, key)
        self.assertTrue(p.decoded)
        self.assertEqual(p.trailing, b"")
        self.assertEqual(p.parameters, {"META_IsAverage": "1"})
        self.assertEqual(p.curves[0].points, [(20.0, 1.0, 0.001)])
        self.assertEqual((p.curves[0].transfer, p.curves[0].delay_ms), (0.5, 2.0))
        with self.assertRaises(ValueError):
            peqb.read(blob, crypto.swhp_key("g" + "1" * 40))


if __name__ == "__main__":
    unittest.main()
