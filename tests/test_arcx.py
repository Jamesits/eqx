import cmath
import math
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from helpers import assert_filters
from testgen import common, ik, soundid
from eqx import dsp, impulse
from eqx.autoeq import response
from eqx.convert import to_swproj
from eqx.convert.to_arcx import AutoeqToArcx
from eqx.ik import arcx, pak
from eqx.soundid import layout

ARCX_DIR = common.ROOT / ik.ARCX_DIR
SESSION = ARCX_DIR / "Arc.arcXs"
ANALYSIS = ARCX_DIR / "Arc.arcXa"
SUB = ARCX_DIR / "Arc Sub.arcXs"
SURROUND = ARCX_DIR / "Arc 5.1.arcXs"
MIC = common.ROOT / soundid.MIC_DIR / "FLAT01.swmicpkg"


def _pak(version: int, entries: dict[str, bytes]) -> bytes:
    if version == 3:
        return pak.write(entries)
    number = "<II" if version == 1 else "<QQ"
    header = b"IKMPAK" + struct.pack("<II", version, len(entries))
    table_size = sum(len(n) + 1 + struct.calcsize(number) for n in entries)
    offset, table, body = len(header) + table_size, b"", b""
    for name, data in entries.items():
        table += name.encode() + b"\0" + struct.pack(number, offset + len(body), len(data))
        body += data
    return header + table + body


def _wav(fmt: bytes, data: bytes) -> bytes:
    return (b"RIFF" + struct.pack("<I", 20 + len(fmt) + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data"
            + struct.pack("<I", len(data)) + data)


def _entries(path) -> dict[str, bytes]:
    return pak.read(path.read_bytes())[1]


class PakTests(unittest.TestCase):
    ENTRIES = {"a.xml": b"<a/>", "dir/b.bin": b"\x00\x01\x02", "empty": b""}

    def test_versions(self):
        for version in (1, 2, 3):
            with self.subTest(version):
                self.assertEqual(pak.read(_pak(version, self.ENTRIES)), (version, self.ENTRIES))

    def test_rejected(self):
        data = _pak(3, self.ENTRIES)
        for bad, message in ((b"IKMPAX" + data[6:], "no IKMPAK header"),
                             (data[:6] + struct.pack("<I", 4) + data[10:], "version 4"),
                             (data[:40], "truncated"),
                             (data[:-1], "outside the file")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                pak.read(bad)

    def test_backslash_names(self):
        data = _pak(2, {"ch0\\ch0p0_ir.wav": b"x"})
        self.assertEqual(pak.read(data)[1], {"ch0/ch0p0_ir.wav": b"x"})


class WavTests(unittest.TestCase):
    SAMPLES = [0.0, 0.5, -0.25, -1.0]

    def test_encodings(self):
        pcm = lambda bits: _wav(struct.pack("<HHIIHH", 1, 1, 48000, 0, bits // 8, bits),
                                b"".join(int(s * 2 ** (bits - 1)).to_bytes(bits // 8, "little",
                                                                          signed=True)
                                         for s in self.SAMPLES))
        floats = struct.pack("<4f", *self.SAMPLES)
        extensible = (struct.pack("<HHIIHHHHI", 0xFFFE, 1, 48000, 0, 4, 32, 22, 32, 4)
                      + struct.pack("<H", 3) + bytes(14))
        files = {"pcm16": pcm(16), "pcm24": pcm(24), "pcm32": pcm(32),
                 "float": _wav(struct.pack("<HHIIHH", 3, 1, 48000, 0, 4, 32), floats),
                 "extensible": _wav(extensible, floats)}
        for name, data in files.items():
            with self.subTest(name):
                self.assertEqual(arcx.read_wav(data), (48000.0, self.SAMPLES))

    def test_stereo_rejected(self):
        data = _wav(struct.pack("<HHIIHH", 3, 2, 48000, 0, 8, 32), bytes(16))
        with self.assertRaisesRegex(ValueError, "2 channels"):
            arcx.read_wav(data)


class ResponseTests(unittest.TestCase):
    def test_fft(self):
        x = [math.sin(0.3 * i) + 0.1 * i for i in range(16)]
        want = [sum(v * cmath.exp(-2j * math.pi * k * i / 16) for i, v in enumerate(x))
                for k in range(16)]
        for got, ref in zip(dsp.fft(x), want):
            self.assertAlmostEqual(abs(got - ref), 0, places=9)
        with self.assertRaises(ValueError):
            dsp.fft([1, 2, 3])

    def test_pure_delay(self):
        ir = [0.0] * 4096
        ir[100] = 0.5
        for power, delay in impulse.point_bands(ir, 48000, impulse.log_grid(48000)):
            self.assertAlmostEqual(10 * math.log10(power), 20 * math.log10(0.5), places=6)
            self.assertAlmostEqual(delay, 0, places=9)

    def test_grid(self):
        grid = impulse.log_grid(48000)
        self.assertEqual((len(grid), grid[0]), (479, 20.0))
        self.assertLessEqual(grid[-1], 20000)
        self.assertLess(impulse.log_grid(16000)[-1], 8000)

    def assert_analytic(self, a, speaker, gains, low, high, delta=0.1):
        point = None if len(gains) > 1 else ik.ARCX_POINT_GAINS.index(gains[0])
        assert_filters(self, arcx.response(a, a.channel(speaker), point),
                       ik.arcx_filters(speaker, a.sample_rate), a.sample_rate, gains, low, high,
                       delta, speaker)

    def test_generated(self):
        a = arcx.load(SESSION)
        for speaker in ("Left", "Right"):
            with self.subTest(speaker):
                self.assert_analytic(a, speaker, ik.ARCX_POINT_GAINS, 30, 16000)
                self.assert_analytic(a, speaker, [ik.ARCX_POINT_GAINS[2]], 30, 16000)
        sub = arcx.load(SUB)
        self.assertEqual(sub.sample_rate, 44100)
        # The low-pass slope bends between the 5.4 Hz bins that are interpolated.
        self.assert_analytic(sub, "Subwoofer", [0.0], 30, 2000, delta=0.2)


class ReaderTests(unittest.TestCase):
    def test_files(self):
        session, analysis, sub = arcx.load(SESSION), arcx.load(ANALYSIS), arcx.load(SUB)
        self.assertEqual(session.speakers, ["Left", "Right"])
        self.assertEqual([len(c) for c in session.channels], [3, 3])
        self.assertIsNone(analysis.session)
        self.assertEqual(analysis.speakers, ["Left", "Right"])
        self.assertEqual(analysis.channels[1][2].ir, session.channels[1][2].ir)
        self.assertEqual(sub.speakers, ["Left", "Right", "Subwoofer"])
        self.assertEqual(sub.channel("subwoofer"), 2)
        self.assertEqual((session.layout, analysis.layout, sub.layout), (1, 1, 2))
        self.assertEqual(arcx.load(SURROUND).speakers,
                         ["Left", "Right", "Center", "Subwoofer", "LeftRearSurround",
                          "RightRearSurround"])

    def test_speaker_names(self):
        info = ET.fromstring('<SerializedMeasure Layout="5.1"/>')
        session = ET.fromstring('<Session Layout="4"/>')
        self.assertEqual(arcx.speaker_names(session, info, 6),
                         ["Left", "Right", "Center", "Subwoofer", "LeftRearSurround",
                          "RightRearSurround"])
        self.assertEqual(arcx.speaker_names(None, info, 6)[2], "Center")
        self.assertEqual(arcx.speaker_names(None, ET.fromstring("<SerializedMeasure/>"), 2),
                         ["Left", "Right"])
        self.assertEqual(arcx.speaker_names(session, info, 3),
                         ["Channel 0", "Channel 1", "Channel 2"])
        self.assertEqual(arcx.layout_id(session, info, 6), 4)
        self.assertEqual(arcx.layout_id(ET.fromstring('<Session Layout="99"/>'), info, 6), 4)
        self.assertIsNone(arcx.layout_id(session, info, 3))

    def test_rejected(self):
        entries = _entries(SESSION)
        missing = dict(entries)
        del missing["ch1/ch1p2_cc.wav"]
        info = entries["info.xml"].replace(b'Version="5.0.0"', b'Version="6.0.0"')
        for bad, message in (({**entries, "info.xml": info}, "version '6.0.0'"),
                             (missing, "no ch1/ch1p2_cc.wav"),
                             ({"info.xml": entries["info.xml"]}, "no channels"),
                             ({k: v for k, v in entries.items() if k != "info.xml"},
                              "no info.xml")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                arcx.read(pak.write(bad))

    def test_sample_rate_mismatch(self):
        entries = _entries(SESSION)
        entries["info.xml"] = entries["info.xml"].replace(b'SampleRate="48000.0"',
                                                          b'SampleRate="44100.0"')
        with self.assertRaisesRegex(ValueError, "48000 Hz, the analysis is 44100 Hz"):
            arcx.read(pak.write(entries))


class SwprojTests(unittest.TestCase):
    def test_layouts(self):
        # Every ARC X layout maps to a SoundID layout with the same speakers.
        for arc_id, (target_id, shorts) in to_swproj.ARCX_LAYOUTS.items():
            with self.subTest(arcx.LAYOUTS[arc_id][0]):
                target = layout.LAYOUTS[target_id]
                self.assertEqual(len(shorts), len(arcx.LAYOUTS[arc_id][1]))
                self.assertEqual(sorted(shorts), sorted(c.short for c in target.channels))

    def test_surround(self):
        target, measurements = to_swproj.ArcxToSwproj(mic_profile=MIC).measurements(SURROUND)
        self.assertEqual(target.name, "5.1")
        self.assertEqual([(m.index, m.channel) for m in measurements],
                         list(enumerate(["Left", "Right", "Center", "Low freq. effects",
                                         "Left Surround", "Right Surround"])))
        self.assertEqual(measurements[3].name, "Subwoofer Arc 5.1")

    def test_reordered(self):
        # ARC X records the 9.1.6 wides after the rear surrounds; SoundID before
        # the surrounds.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Arc 9.1.6.arcXs"
            path.write_bytes(ik.write_arcx(8, 1, 48000, True))
            target, measurements = to_swproj.ArcxToSwproj(mic_profile=MIC).measurements(path)
        self.assertEqual(target.name, "9.1.6 Overhead")
        self.assertEqual([m.channel for m in measurements], [c.name for c in target.channels])
        self.assertEqual(measurements[4].name, "LeftWide Arc 9.1.6")

    def test_unknown_layout(self):
        entries = _entries(SUB)
        del entries["session.xml"]
        entries["info.xml"] = entries["info.xml"].replace(b'Layout="Stereo + Sub"', b'Layout=""')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.arcXa"
            path.write_bytes(pak.write(entries))
            with self.assertRaisesRegex(ValueError, "unknown ARC X layout of 3 channels"):
                to_swproj.ArcxToSwproj(mic_profile=MIC).measurements(path)


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        a = arcx.load(SUB)
        for session in (True, False):
            with self.subTest(session=session):
                b = arcx.read(arcx.write(a.sample_rate, a.channels, a.layout, session))
                self.assertEqual((b.layout, b.speakers, b.channels), (a.layout, a.speakers,
                                                                      a.channels))
                self.assertEqual(b.session is not None, session)
        for args, message in (((48000.0, a.channels, 9), "unknown ARC X layout 9"),
                              ((48000.0, a.channels[:2], 2), "Stereo \\+ Sub has 3 speakers"),
                              ((48000.0, [[], [], []], 2), "same number of points")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                arcx.write(*args)

    def test_from_autoeq(self):
        csv = common.ROOT / common.CSV_DIR
        left, right = csv / "Bandpass Left.csv", csv / "Bandpass Right.csv"
        result = AutoeqToArcx().convert([left, right])
        self.assertEqual(result.name, "Bandpass Left.arcXs")
        a = arcx.read(result.data)
        self.assertEqual((a.sample_rate, a.speakers, len(a.channels[0])),
                         (48000.0, ["Left", "Right"], 1))
        self.assertEqual(impulse.peak_index(a.channels[0][0].cc), 150)
        offsets = []
        for c, path in enumerate((left, right)):
            points = [(f, v) for f, v in response.load(path).curve() if 30 <= f <= 16000]
            _, db, _ = arcx.response(a, c, frequencies=[f for f, _ in points])
            offsets.append(db[0] - points[0][1])
            for (f, v), got in zip(points, db):
                self.assertAlmostEqual(got - v, offsets[0], delta=0.2, msg=f"{f} Hz")
        analysis = AutoeqToArcx(rate=44100, session=False).convert([left])
        self.assertEqual(analysis.name, "Bandpass Left.arcXa")
        a = arcx.read(analysis.data)
        self.assertEqual((a.session, a.sample_rate, a.speakers), (None, 44100.0,
                                                                   ["Left", "Right"]))
        self.assertEqual(a.channels[0], a.channels[1])
        with self.assertRaisesRegex(ValueError, "sample rate must be one of: 44100, 48000"):
            AutoeqToArcx(rate=96000)


if __name__ == "__main__":
    unittest.main()
