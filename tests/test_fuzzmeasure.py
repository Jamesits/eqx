import math
import plistlib
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import gen_testdata
from eqx import caf, dsp, keyedarchive
from eqx.autoeq import response
from eqx.convert.from_autoeq import FUZZMEASURE_FLIGHT_M, AutoeqToFuzzmeasure, _log_resample
from eqx.convert.to_autoeq import FuzzmeasureToAutoeq
from eqx.keyedarchive import Instance, Ref
from eqx.rode import fuzzmeasure

FM_DIR = gen_testdata.ROOT / gen_testdata.FUZZMEASURE_DIR
FM4, FM3, FM2 = FM_DIR / "Fm4.fume4", FM_DIR / "Fm3.fume3", FM_DIR / "Fm2.fume"
CSV = gen_testdata.ROOT / gen_testdata.CSV_DIR


def _error(frequencies, db, expected, low=30.0, high=16000.0) -> float:
    return max(abs(a - b) for f, a, b in zip(frequencies, db, expected) if low <= f <= high)


def _speaker_db(name: str, rate: float, frequencies, mic: bool = False) -> list[float]:
    filters = gen_testdata.fm_filters(name, rate) + (gen_testdata.fm_mic(rate) if mic else [])
    return [dsp.cascade_db(filters, f, rate) + gen_testdata.FM_GAIN_DB[name] for f in frequencies]


def _record(**settings) -> fuzzmeasure.Record:
    ir = [0.0] * 64
    ir[3] = 1.0
    defaults = dict(title="X", sample_rate=48000, ir=ir, uuid="U", window=(0, 32, 0))
    return fuzzmeasure.Record(**{**defaults, **settings})


class KeyedArchiveTests(unittest.TestCase):
    def test_round_trip(self):
        when = datetime(2020, 5, 1, tzinfo=timezone.utc)
        top = {"s": "text", "n": Ref(3), "f": Ref(1.5), "b": Ref(True), "d": Ref(b"\x01\x02"),
               "list": [1, "a", None], "dict": {"k": [2.5]}, "date": when, "none": None,
               "obj": Instance("Thing", {"inline": 7, "raw": b"xy", "ref": Ref(7),
                                         "child": Instance("Other")},
                               ["Thing", "Base", "NSObject"])}
        got = keyedarchive.unarchive(keyedarchive.archive(top))
        self.assertEqual({k: v for k, v in got.items() if k != "obj"},
                         {"s": "text", "n": 3, "f": 1.5, "b": True, "d": b"\x01\x02",
                          "list": [1, "a", None], "dict": {"k": [2.5]}, "date": when,
                          "none": None})
        obj = got["obj"]
        self.assertEqual((obj.classname, obj.classes), ("Thing", ["Thing", "Base", "NSObject"]))
        self.assertEqual({k: v for k, v in obj.fields.items() if k != "child"},
                         {"inline": 7, "raw": b"xy", "ref": 7})
        self.assertEqual(obj.get("child").classname, "Other")

    def test_inline_and_reference(self):
        plist = plistlib.loads(keyedarchive.archive(
            {"o": Instance("T", {"inline": 7, "ref": Ref(7), "bytes": b"x", "data": Ref(b"x")})}))
        self.assertEqual(plist["$archiver"], "NSKeyedArchiver")
        self.assertEqual(plist["$objects"][0], "$null")
        obj = plist["$objects"][plist["$top"]["o"].data]
        self.assertEqual((obj["inline"], obj["bytes"]), (7, b"x"))
        self.assertIsInstance(obj["ref"], plistlib.UID)
        self.assertEqual(plist["$objects"][obj["data"].data], b"x")
        cls = plist["$objects"][obj["$class"].data]
        self.assertEqual(cls, {"$classes": ["T", "NSObject"], "$classname": "T"})

    def test_cycle(self):
        array = {"NS.objects": [plistlib.UID(1)], "$class": plistlib.UID(2)}
        data = plistlib.dumps({"$archiver": "NSKeyedArchiver", "$version": 100000,
                               "$top": {"root": plistlib.UID(1)},
                               "$objects": ["$null", array, {"$classname": "NSMutableArray"}]},
                              fmt=plistlib.FMT_BINARY)
        root = keyedarchive.unarchive(data)["root"]
        self.assertIs(root[0], root)
        self.assertIn("<cycle>", keyedarchive.describe(root))

    def test_rejects(self):
        for data in (b"", b"bplist00", plistlib.dumps({"a": 1}, fmt=plistlib.FMT_BINARY)):
            with self.subTest(data[:8]), self.assertRaisesRegex(ValueError, "not a keyed archive"):
                keyedarchive.unarchive(data)
        with self.assertRaisesRegex(TypeError, "cannot archive"):
            keyedarchive.archive({"x": object()})


class CafTests(unittest.TestCase):
    def test_round_trip(self):
        data = caf.write(44100, [0.5, -0.25, 1.0])
        self.assertEqual(data[:8], b"caff\x00\x01\x00\x00")
        self.assertEqual(caf.read(data), (44100.0, [[0.5, -0.25, 1.0]]))

    def _caf(self, flags: int, bits: int, channels: int, samples: bytes, size: int | None = None):
        desc = struct.pack(">d4s5I", 48000.0, b"lpcm", flags, channels * bits // 8, 1, channels,
                           bits)
        body = bytes(4) + samples
        return (b"caff\x00\x01\x00\x00" + b"desc" + struct.pack(">q", 32) + desc
                + b"free" + struct.pack(">q", 4) + bytes(4)
                + b"data" + struct.pack(">q", len(body) if size is None else size) + body)

    def test_formats(self):
        rate, channels = caf.read(self._caf(caf.LITTLE_ENDIAN, 16, 2,
                                            struct.pack("<4h", 16384, -32768, 0, 8192)))
        self.assertEqual((rate, channels), (48000.0, [[0.5, 0.0], [-1.0, 0.25]]))
        _, channels = caf.read(self._caf(caf.FLOAT, 64, 1, struct.pack(">2d", 0.1, 0.2), size=-1))
        self.assertEqual(channels, [[0.1, 0.2]])
        _, channels = caf.read(self._caf(0, 24, 1, b"\x40\x00\x00"))
        self.assertEqual(channels, [[0.5]])

    def test_rejects(self):
        with self.assertRaisesRegex(ValueError, "not a CAF"):
            caf.read(b"RIFF....")
        with self.assertRaisesRegex(ValueError, "unsupported"):
            caf.read(self._caf(caf.FLOAT, 16, 1, bytes(2)))
        with self.assertRaisesRegex(ValueError, "no desc or data"):
            caf.read(b"caff\x00\x01\x00\x00")


class ReaderTests(unittest.TestCase):
    def test_fume4(self):
        d = fuzzmeasure.load(FM4)
        self.assertEqual((d.kind, d.version), (".fume4", "3.0"))
        self.assertEqual([r.title for r in d.records], ["Left", "Right", "Sub"])
        self.assertEqual([r.sample_rate for r in d.records], [48000, 48000, 44100])
        self.assertEqual([r.window for r in d.records],
                         [(-512, 16384, 0), (0, 16384, 0), (-4096, 12288, 5)])
        self.assertEqual({r.version for r in d.records}, {1})
        self.assertEqual(set(d.entries), {fuzzmeasure.TOP_LEVEL, *(f"{r.uuid}.caf"
                                                                   for r in d.records)})
        left, right, sub = d.records
        self.assertEqual((left.notes, left.date, left.color),
                         ("Left speaker, 3 m", fuzzmeasure.EPOCH, fuzzmeasure.COLORS[0]))
        self.assertEqual((right.use_spl, right.spl_reference), (True, -20.0))
        self.assertTrue(right.use_calibration)
        self.assertEqual((right.calibration.name, right.calibration.serial), ("TILT01 mic",
                                                                              "TILT01"))
        self.assertEqual(len(right.calibration.points), 67)
        self.assertTrue(sub.normalized)
        self.assertIsNone(sub.calibration)
        for r in (left, right):
            self.assertEqual(r.peak_index(), gen_testdata.FM_FLIGHT[r.title])
        self.assertEqual({r.sweep["Name"] for r in d.records}, {"Untitled"})

    def test_fume3(self):
        d = fuzzmeasure.load(FM3)
        self.assertEqual((d.kind, d.version), (".fume3", "3.0"))
        left, right = d.records
        self.assertEqual({r.version for r in d.records}, {0})
        self.assertEqual((left.sample_rate, left.averages, left.color), (44100, 4, (0.0, 0.0, 0.0)))
        # A linear SPL reference of 1 is 0 dB; FuzzMeasure 3 has no switch.
        self.assertEqual((left.spl_reference, left.use_spl), (0.0, False))
        self.assertEqual((left.calibration.name, left.use_calibration), ("TILT01 mic", True))
        self.assertIsNone(right.calibration)
        self.assertEqual((left.start_hz, left.end_hz), (20.0, 20000.0))
        # FuzzMeasure 4 loads these with the unkeyed decodeObject: key $0.
        plist = plistlib.loads((FM3 / left.uuid).read_bytes())
        self.assertEqual(list(plist["$top"]), ["$0"])

    def test_fume2(self):
        d = fuzzmeasure.load(FM2)
        self.assertEqual((d.kind, d.version, d.entries), (".fume", "2.0", {}))
        left, right = d.records
        self.assertEqual((len(left.ir), left.window[2], left.compatibility), (8192, 4, False))
        # FuzzMeasure 1 data (bare big-endian floats) is decoded in compatibility mode.
        self.assertEqual((right.compatibility, right.fft_length), (True, 4096))
        self.assertEqual(left.fft_length, fuzzmeasure.MIN_FFT)
        self.assertEqual(right.peak_index(), gen_testdata.FM_FLIGHT["Right"])

    def test_record_lookup(self):
        d = fuzzmeasure.load(FM4)
        for key, index in ((None, 0), ("2", 2), ("right", 1), ("SUB", 2), (1, 1)):
            self.assertEqual(d.record(key), index)
        with self.assertRaisesRegex(ValueError, "measurement 3 does not exist; measurements: 0-2"):
            d.record("3")
        with self.assertRaisesRegex(ValueError, "no measurement 'Centre'; available: 'Left'"):
            d.record("Centre")

    def test_rejects(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "x.fume4"
            package.mkdir()
            with self.assertRaisesRegex(ValueError, "no TopLevel.dat"):
                fuzzmeasure.load(package)
            (package / fuzzmeasure.TOP_LEVEL).write_bytes(keyedarchive.archive({"x": 1}))
            with self.assertRaisesRegex(ValueError, "no MeasurementRecords"):
                fuzzmeasure.load(package)
            record = Instance("SMUGMeasurementRecord", {"version": Ref(1), "impulseUUID": "AB",
                                                        "sampleRate": Ref(48000)})
            (package / fuzzmeasure.TOP_LEVEL).write_bytes(keyedarchive.archive(
                {"MeasurementRecords": [record]}))
            with self.assertRaisesRegex(ValueError, "impulse response AB is not in the document"):
                fuzzmeasure.load(package)
        with self.assertRaisesRegex(ValueError, "not a keyed archive"):
            fuzzmeasure.read(b"not a document")


class ResponseTests(unittest.TestCase):
    def test_fume4(self):
        left, right, sub = fuzzmeasure.load(FM4).records
        f, db, _ = fuzzmeasure.response(left)
        self.assertEqual((f[0], round(f[-1])), (20.0, 19897))
        self.assertLess(_error(f, db, _speaker_db("Left", 48000, f)), 0.1)
        f, db, _ = fuzzmeasure.response(right)
        self.assertLess(_error(f, db, _speaker_db("Right", 48000, f)), 0.1)
        f, raw, _ = fuzzmeasure.response(right, calibration=False)
        self.assertLess(_error(f, raw, _speaker_db("Right", 48000, f, mic=True)), 0.1)
        f, spl, _ = fuzzmeasure.response(right, spl=True)
        self.assertEqual([round(a - b, 9) for a, b in zip(spl, db)], [114.0] * len(f))
        # Normalized: the impulse response is scaled to a peak of 1.
        f, db, _ = fuzzmeasure.response(sub)
        peak = max(abs(v) for v in sub.ir)
        expected = [v - 20 * math.log10(peak) for v in _speaker_db("Sub", 44100, f)]
        self.assertLess(_error(f, db, expected, high=2000.0), 0.1)

    def test_fume3_and_fume2(self):
        for r in fuzzmeasure.load(FM3).records:
            f, db, _ = fuzzmeasure.response(r)
            self.assertLess(_error(f, db, _speaker_db(r.title, 44100, f)), 0.1)
        right = fuzzmeasure.load(FM2).records[1]
        f, db, _ = fuzzmeasure.response(right)
        self.assertLess(_error(f, db, _speaker_db("Right", 48000, f), low=200.0), 0.1)

    def test_frequency_range(self):
        f, _, _ = fuzzmeasure.response(_record(start_hz=100.0, end_hz=1000.0))
        self.assertTrue(100 <= f[0] < 102 and 980 < f[-1] <= 1000)
        with self.assertRaisesRegex(ValueError, "no frequency in 5-10 Hz"):
            fuzzmeasure.response(_record(start_hz=5.0, end_hz=10.0))

    def test_window_ranges(self):
        ir = [float(i) for i in range(16)]
        for window, expected in (((2, 5, 0), [2, 3, 4]), ((-3, -1, 0), [13, 14]),
                                 ((-2, 2, 0), [14, 15, 0, 1])):
            self.assertEqual(fuzzmeasure.windowed(_record(ir=ir, window=window)), expected)
        for window in ((0, 0, 0), (4, 2, 0), (0, 9, 0)):
            with self.subTest(window), self.assertRaisesRegex(ValueError, "empty analysis window"):
                fuzzmeasure.windowed(_record(ir=ir, window=window))
        self.assertEqual(fuzzmeasure.windowed(_record(ir=[0.0, -4.0, 2.0, 0.0], window=(0, 2, 0),
                                                      normalized=True)), [0.0, -1.0])

    def test_window_shapes(self):
        ones = [1.0] * 5
        hamming = fuzzmeasure.shape_window(ones, 1)
        self.assertAlmostEqual(hamming[0], 0.08)
        self.assertAlmostEqual(hamming[2], 1.0)
        hann = fuzzmeasure.shape_window(ones, 3)
        self.assertEqual([round(v, 9) for v in hann], [0.0, 0.5, 1.0, 0.5, 0.0])
        half = fuzzmeasure.shape_window(ones, 4)
        self.assertGreater(half[0], 0.95)
        self.assertEqual(sorted(half, reverse=True), half)
        bingham = fuzzmeasure.shape_window([1.0] * 20, 5)
        self.assertEqual(bingham[2:18], [1.0] * 16)
        self.assertEqual(bingham[:2], bingham[:-3:-1])
        self.assertLess(bingham[0], bingham[1])
        half_bingham = fuzzmeasure.shape_window([1.0] * 20, 6)
        self.assertEqual(half_bingham[:16], [1.0] * 16)
        self.assertEqual(sorted(half_bingham[16:], reverse=True), half_bingham[16:])
        self.assertEqual(fuzzmeasure.shape_window(ones, 0), ones)

    def test_spline(self):
        xs, ys = [10.0, 100.0, 1000.0], [1.0, 2.0, 3.0]
        got = fuzzmeasure.spline(xs, [2 * x for x in xs], [10.0, 55.0, 1000.0, 5.0, 2000.0])
        self.assertEqual(got[3:], [None, None])
        for g, x in zip(got[:3], (10.0, 55.0, 1000.0)):
            self.assertAlmostEqual(g, 2 * x)
        self.assertEqual(fuzzmeasure.spline(xs, ys, xs), ys)


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        calibration = fuzzmeasure.Calibration("Mic", [(10.0, 1.0), (20000.0, 2.0)],
                                              [(10.0, 0.0), (20000.0, 0.0)], "S1", -42.0, "C1")
        records = [_record(title="A", uuid="UA", window=(-4, 30, 2), notes="n", start_hz=10.0,
                           end_hz=20000.0, calibration=calibration, use_calibration=True,
                           use_spl=True, spl_reference=-12.5, color=(0.25, 0.5, 1.0)),
                   _record(title="B", uuid="UB", sample_rate=44100, normalized=True)]
        files = fuzzmeasure.write(records)
        self.assertEqual(set(files), {"TopLevel.dat", "UA.caf", "UB.caf"})
        self.assertEqual(caf.read(files["UB.caf"]), (44100.0, [records[1].ir]))
        d = fuzzmeasure.read_package(files)
        a, b = d.records
        self.assertEqual((a.title, a.uuid, a.window, a.notes, a.start_hz, a.end_hz),
                         ("A", "UA", (-4, 30, 2), "n", 10.0, 20000.0))
        self.assertEqual((a.use_spl, a.spl_reference, a.color), (True, -12.5, (0.25, 0.5, 1.0)))
        self.assertEqual(a.calibration, calibration)
        self.assertEqual((b.sample_rate, b.normalized, b.calibration, b.version), (44100, True,
                                                                                  None, 1))
        self.assertEqual([(g.classname, g.fields) for g in d.top["Graphs"]],
                         [("FuzzMeasureMagnitudeResponseGraph", {})])
        self.assertEqual(d.top["ColorIndex"], 2)

    def test_encoding(self):
        """Keys and value kinds as FuzzMeasure 4 encodes them."""
        plist = plistlib.loads(fuzzmeasure.write([_record()])[fuzzmeasure.TOP_LEVEL])
        objects = plist["$objects"]
        resolve = lambda v: objects[v.data] if isinstance(v, plistlib.UID) else v
        self.assertEqual(resolve(plist["$top"]["FUMEVersion"]), "3.0")
        record = resolve(resolve(plist["$top"]["MeasurementRecords"])["NS.objects"][0])
        self.assertEqual(resolve(resolve(record["$class"])["$classname"]), "SMUGMeasurementRecord")
        for key in ("version", "sampleRate", "SPLReferenceLevel", "normalized", "speedOfSound"):
            self.assertIsInstance(record[key], plistlib.UID, key)
        window = resolve(record["impulseResponseWindow"])
        self.assertEqual((resolve(window["Version"]), window["Begin"], window["End"],
                          window["Type"]), ("2.0", 0, 32, 0))
        color = resolve(record["plotColor"])
        self.assertEqual((color["NSColorSpace"], color["NSRGB"]), (1, b"0.85 0.15 0.15\0"))

    def test_rejects(self):
        with self.assertRaisesRegex(ValueError, "needs a measurement"):
            fuzzmeasure.write([])
        with self.assertRaisesRegex(ValueError, "its own UUID"):
            fuzzmeasure.write([_record(), _record()])
        with self.assertRaisesRegex(ValueError, "no samples or no sample rate"):
            fuzzmeasure.write([_record(ir=[])])


class ConversionTests(unittest.TestCase):
    def test_to_autoeq(self):
        result = FuzzmeasureToAutoeq().convert([FM4])
        self.assertEqual(result.name, "Fm4 Left.csv")
        result = FuzzmeasureToAutoeq(measurement="Right", spl=True).convert([FM4])
        self.assertIn("dB SPL, minus microphone calibration 'TILT01 mic'", result.notes[1])
        result = FuzzmeasureToAutoeq(measurement="1", mic_calibration=False).convert([FM4])
        self.assertIn("'TILT01 mic' not applied", result.notes[1])
        points = response.read(result.data.decode()).curve(response.RAW)
        f = [x for x, _ in points]
        self.assertLess(_error(f, [v for _, v in points], _speaker_db("Right", 48000, f, True)),
                        0.1)

    def test_output_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "x.fume4"
            package.mkdir()
            for name, data in fuzzmeasure.write([_record(title="L/R: 1")]).items():
                (package / name).write_bytes(data)
            self.assertEqual(FuzzmeasureToAutoeq().convert([package]).name, "x L_R_ 1.csv")

    def test_from_autoeq(self):
        """The SPL graph follows the curves; the magnitude is at 0 dB in the mid band."""
        paths = [CSV / "Bandpass Left.csv", CSV / "Bandpass Right.csv"]
        result = AutoeqToFuzzmeasure().convert(paths)
        self.assertEqual(result.name, "Bandpass Left.fume4")
        d = fuzzmeasure.read_package(result.data)
        self.assertEqual([r.title for r in d.records], ["Bandpass Left", "Bandpass Right"])
        for r, path in zip(d.records, paths):
            self.assertEqual(r.peak_index(), round(FUZZMEASURE_FLIGHT_M
                                                   / fuzzmeasure.SPEED_OF_SOUND * 48000))
            curve = response.load(path).curve(response.RAW)
            f, db, _ = fuzzmeasure.response(r, spl=True)
            self.assertLess(_error(f, db, _log_resample(curve, f)), 0.2)
            self.assertEqual((r.start_hz, r.end_hz), (curve[0][0], min(curve[-1][0], 24000)))
        f, db, _ = fuzzmeasure.response(d.records[0])
        band = sorted(v for x, v in zip(f, db) if 200 <= x <= 10000)
        self.assertLess(abs(band[len(band) // 2]), 0.5)
        d = fuzzmeasure.read_package(AutoeqToFuzzmeasure(rate=44100).convert(paths[:1]).data)
        self.assertEqual([(r.title, r.sample_rate) for r in d.records], [("Bandpass Left", 44100)])

    def test_from_autoeq_rejects(self):
        with self.assertRaisesRegex(ValueError, "whole number"):
            AutoeqToFuzzmeasure(rate=44100.5)
        path = CSV / "Bandpass Left.csv"
        with self.assertRaisesRegex(ValueError, "distinct names"):
            AutoeqToFuzzmeasure().convert([path, path])


if __name__ == "__main__":
    unittest.main()
