import struct
import tempfile
import unittest
from pathlib import Path

import gen_testdata
from eqx import dsp, formats, protobuf
from eqx.autoeq import response
from eqx.convert.dirac import DiracFilterToAutoeq, DiracFilterToFir, FirToDiracFilter
from eqx.convert.mdat_swproj import resample
from eqx.dirac import filterslot, playback
from eqx.wav import fir

ROOT = gen_testdata.ROOT
DIR = ROOT / gen_testdata.DIRAC_FILTER_DIR
FIIR = DIR / "FIIR.bin"
SIGNED = DIR / "FIIR signed.bin"
DRFIR = DIR / "Bass and treble.bin"
CSV = ROOT / gen_testdata.CSV_DIR / "Bass and treble.csv"


def encoded(slot: dict) -> bytes:
    """Decoding drops proto3 defaults; compare slots by their encoding."""
    return protobuf.encode(filterslot.SCHEMA, "FilterSlot", slot)


def section_ir(n: int) -> list[float]:
    s = gen_testdata.FIIR_SECTION
    y = []
    for i in range(n):
        x = s["b0"] if i == 0 else s["b1"] if i == 1 else 0.0
        y.append(x - s["a1"] * (y[i - 1] if i > 0 else 0) - s["a2"] * (y[i - 2] if i > 1 else 0))
    return y


class ProtobufTests(unittest.TestCase):
    SCHEMA = {
        "M": {"i": (1, "int32", ""), "f": (2, "float", ""), "s": (3, "string", ""),
              "r": (4, "float", "repeated"), "m": (5, "N", "map:int32"),
              "n": (6, "N", "repeated"), "u": (7, "uint64", "")},
        "N": {"v": (1, "int32", "")},
    }

    def test_encoding(self):
        data = protobuf.encode(self.SCHEMA, "M", {
            "i": -1, "f": 0.0, "s": "é", "r": [1.0, 2.0], "m": {0: {}}, "n": [{"v": 3}],
            "u": 2 ** 64 - 1})
        want = (b"\x08" + b"\xff" * 9 + b"\x01"           # negative int32: 10-byte varint
                + b"\x1a\x02" + "é".encode()               # 0.0 is left out
                + b"\x22\x08" + struct.pack("<2f", 1, 2)   # packed
                + b"\x2a\x04\x08\x00\x12\x00"              # map entry keeps both fields
                + b"\x32\x02\x08\x03"
                + b"\x38" + b"\xff" * 9 + b"\x01")
        self.assertEqual(data, want)
        self.assertEqual(protobuf.decode(self.SCHEMA, "M", data), {
            "i": -1, "s": "é", "r": [1.0, 2.0], "m": {0: {}}, "n": [{"v": 3}],
            "u": 2 ** 64 - 1})

    def test_negative_zero_is_kept(self):
        self.assertEqual(protobuf.encode(self.SCHEMA, "M", {"f": -0.0}),
                         b"\x15" + struct.pack("<f", -0.0))

    def test_unpacked_and_unknown(self):
        data = b"\x25" + struct.pack("<f", 1.5) + b"\x25" + struct.pack("<f", 2.5) + b"\x48\x05"
        self.assertEqual(protobuf.decode(self.SCHEMA, "M", data), {"r": [1.5, 2.5]})

    def test_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown fields: x"):
            protobuf.encode(self.SCHEMA, "M", {"x": 1})
        with self.assertRaisesRegex(ValueError, "truncated"):
            protobuf.decode(self.SCHEMA, "M", b"\x1a\x05ab")


class ContainerTests(unittest.TestCase):
    def test_unsigned(self):
        data = FIIR.read_bytes()
        f = filterslot.read(data)
        self.assertEqual((f.version, f.signature), (2, None))
        self.assertEqual(encoded(f.slot), encoded(gen_testdata.fiir_slot()))
        self.assertEqual(filterslot.write(f.slot), data)

    def test_version_1(self):
        data = FIIR.read_bytes()
        f = filterslot.read(data[:8] + struct.pack("<I", 1) + data[16:])
        self.assertEqual((f.version, encoded(f.slot)), (1, encoded(gen_testdata.fiir_slot())))

    def test_signed(self):
        f = filterslot.read(SIGNED.read_bytes())
        self.assertEqual(encoded(f.slot), encoded(gen_testdata.fiir_slot()))
        self.assertEqual(f.signature.key_id, 1)
        self.assertEqual(f.signature.signed[:4], struct.pack(">I", 1))
        self.assertFalse(f.signature.verify())

    def test_rejected(self):
        data = FIIR.read_bytes()
        for bad, message in ((b"XARD" + data[4:], "incorrect format"),
                             (data[:4] + b"XTRP" + data[8:], "incorrect product"),
                             (data[:8] + struct.pack("<I", 3) + data[12:], "incorrect version"),
                             (data[:12] + struct.pack("<I", 1) + data[16:], "signature error"),
                             (data[:16] + b"\x0a\x7f", "Failed to parse")):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    filterslot.read(bad)

    def test_sniff(self):
        self.assertEqual(formats.detect(FIIR), "dirac-filter")
        self.assertEqual(formats.detect(DRFIR), "dirac-filter")


class PlaybackTests(unittest.TestCase):
    def test_fiir(self):
        slot = gen_testdata.fiir_slot()
        ir, cross = playback.impulse_response(slot, 0, 48000)
        tail = section_ir(len(ir) - 3)
        want = [1.0, 0.5, 0.25] + tail
        self.assertEqual(cross, 0)
        for got, w in zip(ir, want, strict=True):
            self.assertAlmostEqual(got, w, places=12)
        self.assertLess(abs(ir[-1]), 1e-9 * max(map(abs, tail)) * 2)

    def test_gain_delay_and_cross_terms(self):
        slot = gen_testdata.fiir_slot()
        ir, cross = playback.impulse_response(slot, 1, 48000)
        gain = 10 ** (-6 / 20)
        self.assertEqual(cross, 1)
        self.assertEqual(ir[:24], [0.0] * 24)       # 0.5 ms at 48 kHz
        want = [gain * v for v in [1.0, 1.0, 0.25] + section_ir(2)]
        for got, w in zip(ir[24:29], want, strict=True):
            self.assertAlmostEqual(got, w, places=12)

    def test_unsupported(self):
        slot = gen_testdata.fiir_slot()
        with self.assertRaisesRegex(ValueError, "no 44100 Hz filter; available: 48000"):
            playback.impulse_response(slot, 0, 44100)
        slot["sections"][0]["filter_type"] = filterslot.FILTER_TYPES.index("MULTI_RATE_FILTER")
        with self.assertRaisesRegex(ValueError, "MULTI_RATE_FILTER live filters"):
            playback.impulse_response(slot, 0, 48000)
        slot["sections"][0]["disposition"] = 2
        with self.assertRaisesRegex(ValueError, "0 live sections"):
            playback.impulse_response(slot, 0, 48000)

    def test_chain(self):
        c = playback.chain()
        self.assertAlmostEqual(sum(c), 1, delta=0.003)
        self.assertEqual(max(range(len(c)), key=lambda i: c[i]), playback.CHAIN_LATENCY)
        for i in range(len(c)):                     # linear phase
            self.assertAlmostEqual(c[i], c[2 * playback.CHAIN_LATENCY - i], places=12)

    def test_drfir(self):
        cell = {"drfir": {"fir_hi": {"fir_taps": [0.5, 0.25]}, "fir_lo": {"fir_taps": [0, 2.0]},
                          "delay_hi": 60}}
        slot = {"sections": {0: {"disposition": 1, "filter_type": 3,
                                 "rates": {48000: {"cells": [cell]}}}}}
        ir, _ = playback.impulse_response(slot, 0, 48000)
        want = [2.0 * v for v in [0.0] * 16 + playback.chain()]
        want[8] += 0.5
        want[9] += 0.25
        for got, w in zip(ir, want, strict=True):
            self.assertAlmostEqual(got, w, places=12)

    def test_design(self):
        target = dsp.design_fir([20, 60, 200, 2000, 20000], [6, -8, 0, 2, -4], 44100, "minimum",
                                16384)
        d = playback.design(target, 44100)
        self.assertEqual((len(d.fir_hi), len(d.fir_lo)), (playback.TAPS, playback.TAPS))
        self.assertLess(d.lost, 1e-6)
        slot = playback.dual_rate_slot("x", ["Mono"], {44100: [d]})
        ir, _ = playback.impulse_response(slot, 0, 44100)
        want = [0.0] * playback.LATENCY + target
        error = sum((g - (want[i] if i < len(want) else 0.0)) ** 2 for i, g in enumerate(ir))
        self.assertLess(error / sum(v * v for v in target), 1e-6)


class ConversionTests(unittest.TestCase):
    def test_to_autoeq_follows_source(self):
        result = DiracFilterToAutoeq(speaker="right", rate=44100).convert([DRFIR])
        self.assertEqual(result.name, "Bass and treble Right.csv")
        got = response.read(result.data.decode()).curve()
        source = response.load(CSV).curve()
        want = resample([f for f, _ in source], [v for _, v in source], [f for f, _ in got])
        self.assertLess(max(abs(g - w) for (_, g), w in zip(got, want)), 0.05)

    def test_to_fir(self):
        f = fir.read(DiracFilterToFir().convert([FIIR]).data)
        self.assertEqual((f.sample_rate, len(f.channels)), (48000, 2))
        ir, _ = playback.impulse_response(gen_testdata.fiir_slot(), 1, 48000)
        for got, w in zip(f.channels[1], ir):
            self.assertAlmostEqual(got, w, places=6)

    def test_speaker(self):
        with self.assertRaisesRegex(ValueError, "no speaker 'Center'; outputs: Left, Right"):
            DiracFilterToAutoeq(speaker="Center").convert([DRFIR])
        with self.assertRaisesRegex(ValueError, "--rate must be one of"):
            DiracFilterToAutoeq(rate=96000)

    def test_from_fir(self):
        wav = ROOT / gen_testdata.FIR_DIR / "Room.wav"
        source = fir.load(wav)
        slot = filterslot.read(FirToDiracFilter().convert([wav]).data).slot
        self.assertEqual(sorted(playback.live_section(slot)["rates"]), list(playback.RATES))
        rate = round(source.sample_rate)
        ir, _ = playback.impulse_response(slot, 1, rate)
        want = [0.0] * playback.LATENCY + source.channels[1]
        error = sum((g - (want[i] if i < len(want) else 0.0)) ** 2 for i, g in enumerate(ir))
        self.assertLess(error / sum(v * v for v in source.channels[1]), 1e-6)
        other = 44100 if rate != 44100 else 48000
        ir, _ = playback.impulse_response(slot, 0, other)
        freqs = [50, 200, 1000, 5000]
        got = dsp.fir_gain_db(ir, other, freqs)
        want = dsp.fir_gain_db(source.channels[0], source.sample_rate, freqs)
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, delta=0.1)

    def test_from_fir_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "x.wav"
            wav.write_bytes(fir.write(fir.Fir(96000.0, [[1.0]])))
            with self.assertRaisesRegex(ValueError, "a slot takes 32000, 44100, 48000 Hz"):
                FirToDiracFilter().convert([wav])


if __name__ == "__main__":
    unittest.main()
