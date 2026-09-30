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
            computerid.ComputerId(b"0009", b"", b"").source

    def test_cpu_string(self):
        self.assertEqual(computerid.cpu_string(9, 25, 0x2102), b"00098450")   # x64
        self.assertEqual(computerid.cpu_string(0, 6, 0x2102), b"633208450")  # x86

    def test_smbios_board_serial(self):
        bios = bytes([0, 4, 0, 0]) + b"\0\0"                         # no strings
        board = bytes([2, 8, 1, 0, 1, 2, 3, 2]) + b"Maker\0SN123\0\0"
        self.assertEqual(computerid.smbios_board_serial(smbios(bios, board)), b"SN123")
        with self.assertRaises(ValueError):
            computerid.smbios_board_serial(smbios(bios))

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_local(self):
        cid = computerid.local()
        self.assertRegex(cid.value, r"^g[0-9a-f]{40}$")


class SwhpDecryptTests(unittest.TestCase):
    def test_round_trip(self):
        cid = "g" + "0" * 40
        body = struct.pack("<I", 1) + struct.pack("<IddI", 1, 0.5, 2.0, 1) \
            + struct.pack("<3d", 20.0, 1.0, 0.001) + peqb._write_map([("META_IsAverage", "1")])
        key = crypto.swhp_key(cid)
        blob = peqb.MAGIC + bytes((3, 0, 0, 1, 1)) + crypto.encrypt(key, peqb.BODY_HEADER + body)
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
