import math
import struct
import tempfile
import unittest
from pathlib import Path

from helpers import csv_points
from testgen import common, rationalacoustics as ra
from eqx import formats
from eqx.autoeq import response
from eqx.convert.to_autoeq import (SmaartAsciiToAutoeq, SmaartCurveToAutoeq, SmaartRefToAutoeq,
                                   SmaartSrfToAutoeq, SmaartTrfToAutoeq)
from eqx.convert.to_smaart_ascii import AutoeqToSmaartAscii
from eqx.convert.to_smaart_curve import AutoeqToSmaartCurve
from eqx.convert.to_smaart_trace import AutoeqToSmaartSrf, AutoeqToSmaartTrf
from eqx.curve import log_resample
from eqx.model import standard_grid
from eqx.rationalacoustics import ascii, crv, trace

ROOT = common.ROOT
TRACES = ROOT / ra.TRACE_DIR
TF_LEFT, TF_MTW, RTA = TRACES / "Tf Left.trf", TRACES / "Tf Mtw.trf", TRACES / "Rta.srf"
REF_LEFT, REF_RTA = TRACES / "Ref Left.ref", TRACES / "Ref Rta.ref"
TF_EXPORT = ROOT / ra.ASCII_DIR / "Tf export.txt"
RTA_EXPORT = ROOT / ra.ASCII_DIR / "Rta export.txt"
CURVES = ROOT / ra.CURVE_DIR
CSV = ROOT / common.CSV_DIR


def _phase(h: complex) -> float:
    return math.degrees(math.atan2(h.imag, h.real))


def _minimal(**settings) -> trace.Trace:
    d = trace.DataSet([0.0, 100.0, 200.0], [0.0, 1.0, 2.0], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0])
    return trace.Trace(**{**dict(kind=trace.TRANSFER_FUNCTION, sample_rate=400, fft=4, data=d),
                          **settings})


class TraceReaderTests(unittest.TestCase):
    def test_transfer_function(self):
        t = trace.load(TF_LEFT)
        self.assertEqual((t.kind, t.sample_rate, t.fft, t.name, t.measurement),
                         (trace.TRANSFER_FUNCTION, 48000, 4096, "Left", "Main L"))
        self.assertEqual((t.mea_device, t.mea_channel, t.ref_channel, t.mea_info, t.ref_info),
                         ("Interface A", "1", "2", "Mic", "Loopback"))
        self.assertEqual((t.time_ms, t.app_version, t.window, t.averaging, t.average_type),
                         (ra.TIME_MS, trace.APP_VERSION, "Hann", "Fixed 16", 1))
        self.assertEqual((t.delay_ms, t.magnitude_threshold_db, t.extended), (8.75, -60.0, 1))
        self.assertEqual([len(a) for a in t.lir], [ra.LIR_LENGTH] * 2)
        self.assertEqual(len(t.data.frequencies), 2049)
        self.assertEqual(t.data.frequencies[1], 48000 / 4096)
        self.assertEqual(len(t.mtw.frequencies), len(ra.MTW_FREQUENCIES))
        self.assertIsNotNone(t.data.mic_correction)
        self.assertIsNotNone(t.mtw.mic_correction)

    def test_points_follow_the_analytic_response(self):
        t = trace.load(TF_LEFT)
        # MTW frequencies are stored as float32.
        for mtw, places in ((False, 9), (True, 4)):
            columns, rows = trace.rows(t, mtw)
            self.assertEqual(columns, ["frequency Hz", "dB", "phase deg", "coherence"])
            for f, db, phase, coherence in rows:
                h = ra.tf_point("Left", f, 48000)
                self.assertAlmostEqual(db, 20 * math.log10(abs(h)), places=places)
                self.assertAlmostEqual(phase, _phase(h), places=places)
                self.assertAlmostEqual(coherence, ra.coherence(f), places=places)

    def test_bins_without_data(self):
        t = trace.load(TF_LEFT)
        blank = [f for f, re in zip(t.data.frequencies, t.data.real) if re == trace.BLANK]
        self.assertEqual(blank, [f for f in t.data.frequencies
                                 if ra.coherence(f) < ra.THRESHOLD])
        self.assertEqual(len(blank), 5)
        _, rows = trace.rows(t)
        self.assertEqual(rows[0][0], 6 * 48000 / 4096)
        self.assertEqual(len(rows), 2048 - 5)

    def test_mtw_main_set(self):
        t = trace.load(TF_MTW)
        self.assertEqual((t.fft, trace.fft_text(t), t.mtw), (trace.FFT_MTW, "MTW", None))
        self.assertEqual(t.data.frequencies, [struct.unpack("<f", struct.pack("<f", f))[0]
                                              for f in ra.MTW_FREQUENCIES])
        columns, rows = trace.rows(t)
        self.assertEqual(columns, ["frequency Hz", "dB", "phase deg"])
        self.assertEqual(len(rows), 240)
        with self.assertRaisesRegex(ValueError, "no MTW data set"):
            trace.rows(t, mtw=True)

    def test_spectrum(self):
        t = trace.load(RTA)
        self.assertEqual((t.kind, t.sample_rate, t.fft, t.calibration_db, t.peak_hold_s),
                         (trace.SPECTRUM, 44100, 4096, ra.RTA_CALIBRATION_DB, 3))
        self.assertTrue(t.peak_hold_averaged)
        columns, rows = trace.rows(t)
        self.assertEqual(columns, ["frequency Hz", "dB", "peak dB"])
        self.assertEqual(len(rows), 2048)
        for f, db, peak in rows:
            self.assertAlmostEqual(db, ra.rta_db(f, 44100), places=9)
            self.assertAlmostEqual(peak, db + ra.RTA_PEAK_DB, places=9)
        calibrated = trace.curve(t, calibrated=True)
        self.assertAlmostEqual(calibrated[0][1], rows[0][1] + ra.RTA_CALIBRATION_DB, places=9)

    def test_layout(self):
        data = TF_LEFT.read_bytes()
        self.assertEqual(data[:8], b"JACKREF!")
        self.assertEqual(struct.unpack_from("<fI", data, 8), (1.0, 16))
        self.assertEqual(struct.unpack_from("<fI", data, 16), (2.0, trace.TRANSFER_FUNCTION))
        self.assertEqual(struct.unpack_from("<i", data, 16 + 580)[0], 2049)
        self.assertEqual(data[680:688], b"MTWBEAST")
        self.assertEqual(data[716:724], b"MICCORRT")
        self.assertEqual(data[736:744], b"EXTNDBLE")
        offsets = struct.unpack_from("<8I", data, 16 + 632)
        n = 8 * 2049
        self.assertEqual(offsets[:5], (0, 752, 752 + n, 752 + 2 * n, 752 + 3 * n))
        self.assertEqual(offsets[5], 0)
        self.assertEqual(offsets[6:], (752 + 4 * n, 752 + 4 * n + 8 * ra.LIR_LENGTH))
        # Spectrum data follows the EXTNDBLE chunk; peak hold the levels.
        data = RTA.read_bytes()
        self.assertEqual(data[680:688], b"EXTNDBLE")
        self.assertEqual(struct.unpack_from("<8I", data, 16 + 632)[1::4], (696, 696 + 8 * 2049))
        # An MTW main set stores its frequencies first.
        self.assertEqual(struct.unpack_from("<2I", TF_MTW.read_bytes(), 16 + 632),
                         (696, 696 + 4 * 241))

    def test_rejects(self):
        good = TF_MTW.read_bytes()
        cases = [
            (trace.LEGACY_MAGIC + good[8:], "not a Smaart reference file"),
            (b"JACKRAF!" + good[8:], "not a Smaart trace"),
            (good[:16 + 100], "truncated header"),
            (good[:16] + struct.pack("<f", 1.0) + good[20:], "header version 1 is not supported"),
            (good[:20] + struct.pack("<I", 7) + good[24:], "unknown trace kind 7"),
            (good[:16 + 340] + struct.pack("<i", trace.FFT_FPPO) + good[16 + 344:], "FPPO"),
            (good[:-8], "imag outside the file"),
        ]
        for data, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                trace.read(data)


class TraceWriterTests(unittest.TestCase):
    def test_generated_files_round_trip(self):
        for path in [*TRACES.glob("*.trf"), *TRACES.glob("*.srf")]:
            with self.subTest(path.name):
                self.assertEqual(trace.write(trace.read(path.read_bytes())), path.read_bytes())

    def test_live_averages(self):
        for kind in (trace.LIVE_SPECTRUM, trace.LIVE_TRANSFER_FUNCTION):
            t = _minimal(kind=kind)
            self.assertEqual(trace.read(trace.write(t)).kind, kind)

    def test_strings_are_cut(self):
        t = trace.read(trace.write(_minimal(name="é" * 20, comment="x" * 200)))
        self.assertEqual((t.name, t.comment), ("é" * 16, "x" * 128))

    def test_rejects(self):
        cases = [
            (_minimal(data=trace.DataSet([0.0, 1.0], [0.0], [0.0, 0.0], [0.0, 0.0])),
             "equal length"),
            (_minimal(kind=trace.SPECTRUM, fft=trace.FFT_FPPO), "needs an FFT size"),
            (_minimal(lir=([0.0], [0.0, 1.0])), "Live IR arrays differ"),
        ]
        for t, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                trace.write(t)

    def test_minimum_phase(self):
        sections = [common.peak(1000, 6, 1.0), common.peak(6000, -4, 2.0)]
        points = [(f, common.response(sections, f)[0]) for f in standard_grid()]
        d = trace.transfer_function(points, 48000, 32768)
        for i in range(1, len(d.frequencies), 97):
            f = d.frequencies[i]
            self.assertAlmostEqual(20 * math.log10(math.hypot(d.real[i], d.imag[i])),
                                   d.magnitude[i], places=6)
            if 100 <= f <= 5000:
                self.assertAlmostEqual(d.magnitude[i], common.response(sections, f)[0],
                                       delta=0.01)
                self.assertAlmostEqual(math.degrees(math.atan2(d.imag[i], d.real[i])),
                                       common.response(sections, f)[1], delta=1.0)
        self.assertEqual(d.coherence, [1.0] * len(d.frequencies))
        with self.assertRaisesRegex(ValueError, "power of two"):
            trace.transfer_function(points, 48000, 1000)


class TraceConversionTests(unittest.TestCase):
    def test_to_autoeq(self):
        result = SmaartTrfToAutoeq().convert([TF_LEFT])
        self.assertEqual(result.name, "Tf Left.csv")
        self.assertIn("transfer function 'Left', 4k data, dB", result.notes[-1])
        rows = trace.rows(trace.load(TF_LEFT))[1]
        got = csv_points(result)
        self.assertEqual(len(got), len(rows))
        for (f, v), row in zip(got, rows):
            self.assertAlmostEqual(f, row[0], delta=0.0051)
            self.assertAlmostEqual(v, row[1], delta=0.0051)
        self.assertEqual(len(csv_points(SmaartTrfToAutoeq(mtw=True).convert([TF_LEFT]))),
                         len(trace.rows(trace.load(TF_LEFT), mtw=True)[1]))
        with self.assertRaisesRegex(ValueError, "spectrum traces only"):
            SmaartTrfToAutoeq(calibrated=True).convert([TF_LEFT])

    def test_spectrum_to_autoeq(self):
        result = SmaartSrfToAutoeq(calibrated=True).convert([RTA])
        self.assertIn("dB + calibration offset 100 dB", result.notes[-1])
        f, v = csv_points(result)[100]
        self.assertAlmostEqual(v, ra.rta_db(f, 44100) + 100, delta=0.01)

    def test_from_autoeq(self):
        source = CSV / "Bandpass Left.csv"
        result = AutoeqToSmaartTrf(fft=4096).convert([source])
        self.assertEqual(result.name, "Bandpass Left.trf")
        self.assertIn("2049 bins, FFT 4096 at 48000 Hz", result.notes[0])
        t = trace.read(result.data)
        self.assertEqual((t.name, t.comment), ("Bandpass Left", "Bandpass Left.csv, column raw"))
        points = response.load(source).curve()
        _, rows = trace.rows(t)
        expected = log_resample(points, [r[0] for r in rows])
        self.assertLess(max(abs(r[1] - e) for r, e in zip(rows, expected)), 1e-9)
        for kwargs, message in (({"fft": 1000}, "power of two"), ({"rate": 0}, "whole number")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                AutoeqToSmaartTrf(**kwargs)


class SpectrumWriterTests(unittest.TestCase):
    def test_from_autoeq(self):
        source = CSV / "Bandpass Left.csv"
        result = AutoeqToSmaartSrf(fft=4096, calibration_db=100).convert([source])
        self.assertEqual(result.name, "Bandpass Left.srf")
        self.assertIn("2049 bins, FFT 4096 at 48000 Hz, calibration offset 100 dB",
                      result.notes[0])
        t = trace.read(result.data)
        self.assertEqual((t.kind, t.calibration_db), (trace.SPECTRUM, 100.0))
        got = trace.curve(t, calibrated=True)
        expected = log_resample(response.load(source).curve(), [f for f, _ in got])
        self.assertLess(max(abs(v - e) for (_, v), e in zip(got, expected)), 1e-9)
        self.assertAlmostEqual(trace.curve(t)[0][1], got[0][1] - 100, places=9)


class ReferenceFileTests(unittest.TestCase):
    def test_transfer_function(self):
        t = trace.load(REF_LEFT)
        self.assertEqual((t.kind, t.reference_file, t.name, t.comment, t.app_version[:2]),
                         (trace.TRANSFER_FUNCTION, True, "Ref Left", "Left, Smaart 6", (5, 0)))
        self.assertEqual((t.sample_rate, t.fft, t.window, t.averaging, t.average_type,
                          t.delay_ms), (48000, 2048, "Hann", "16", 0, 8.75))
        columns, rows = trace.rows(t)
        self.assertEqual(columns, ["frequency Hz", "dB", "phase deg", "coherence"])
        # Bins below the coherence threshold have no data.
        self.assertEqual(rows[0][0], 3 * 48000 / 2048)
        self.assertEqual(len(rows), 1024 - 2)
        for f, db, phase, coherence in rows:
            h = ra.tf_point("Left", f, 48000)
            self.assertAlmostEqual(db, 20 * math.log10(abs(h)), places=4)
            self.assertAlmostEqual(phase, _phase(h), places=3)
            self.assertAlmostEqual(coherence, ra.coherence(f), places=6)

    def test_spectrum(self):
        t = trace.load(REF_RTA)
        self.assertEqual((t.kind, t.app_version[:2], t.window, t.averaging),
                         (trace.SPECTRUM, (4, 0), "Hamming", "4"))
        _, rows = trace.rows(t)
        self.assertEqual(len(rows), 1024)
        for f, db in rows:
            self.assertAlmostEqual(db, ra.rta_db(f, 44100), places=4)

    def test_rejects(self):
        good = REF_RTA.read_bytes()
        at = trace.LEGACY_PREFIX
        cases = [
            (good[:8] + b"XYZ" + good[11:], "not a Smaart reference file"),
            (good[:at] + b"ABCD" + good[at + 4:], "no REFH header"),
            (good[:at + 10] + struct.pack("<H", 8) + good[at + 12:], "8-byte values"),
            (good[:at + 16] + struct.pack("<H", trace.FFT_FPPO) + good[at + 18:], "FPPO"),
            (good[:-8], "truncated 4.5D chunk"),
            (good[:at + trace.LEGACY_HEADER], "no magnitude chunk"),
        ]
        for data, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                trace.read(data)

    def test_conversions(self):
        result = SmaartRefToAutoeq().convert([REF_LEFT])
        self.assertEqual(result.name, "Ref Left.csv")
        self.assertIn("transfer function 'Ref Left', 2k data, dB", result.notes[-1])
        self.assertEqual(len(csv_points(result)), 1022)
        # Written back as a trace, as Smaart converts reference files on import.
        for path in (REF_LEFT, REF_RTA):
            t = trace.load(path)
            self.assertEqual(trace.rows(trace.read(trace.write(t))), trace.rows(t))


class AsciiTests(unittest.TestCase):
    def test_transfer_function_export(self):
        table = ascii.load(TF_EXPORT)
        self.assertEqual([t.name for t in table.traces], ["Left", "Right"])
        self.assertEqual(table.traces[1].columns, [ascii.MAGNITUDE, ascii.PHASE, ascii.COHERENCE])
        self.assertEqual(table.traces[0].kind, "transfer function")
        self.assertEqual(len(table.frequencies), 512)
        self.assertEqual(table.traces[0].values[0][0], None)
        for k, side in enumerate(("Left", "Right")):
            t = table.traces[k]
            for f, db, phase in list(zip(table.frequencies, *t.values[:2]))[1:]:
                h = ra.tf_point(side, f, 48000)
                self.assertAlmostEqual(db, 20 * math.log10(abs(h)), delta=0.005)
                self.assertAlmostEqual(phase, _phase(h), delta=0.005)

    def test_spectrum_export(self):
        table = ascii.load(RTA_EXPORT)
        t = table.traces[0]
        self.assertEqual((t.name, t.kind, t.columns),
                         ("Rta", "spectrum", [ascii.LEVEL, ascii.PEAK]))
        self.assertEqual(len(table.frequencies), 1024)
        f, level, peak = table.frequencies[9], t.values[0][9], t.values[1][9]
        self.assertAlmostEqual(f, 10 * 44100 / 2048, places=5)
        self.assertAlmostEqual(level, ra.rta_db(f, 44100), delta=0.005)
        self.assertAlmostEqual(peak, level + ra.RTA_PEAK_DB, delta=0.01)

    def test_trace_lookup(self):
        table = ascii.load(TF_EXPORT)
        self.assertEqual((table.trace(None), table.trace("1"), table.trace("RIGHT")), (0, 1, 1))
        with self.assertRaisesRegex(ValueError, "trace 2 does not exist"):
            table.trace(2)
        with self.assertRaisesRegex(ValueError, "no trace 'Sub'"):
            table.trace("Sub")

    def test_rejects(self):
        cases = [("20\t1\n30\t2\n", "no 'Frequency \\(Hz\\)' header"),
                 ("Frequency (Hz)\tMagnitude (dB)\n20\n", "1 columns, expected 2"),
                 ("Frequency (Hz)\tMagnitude (dB)\n20\tx\n", "not a number"),
                 ("Frequency (Hz)\tMagnitude (dB)\n", "no rows")]
        for text, message in cases:
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                ascii.read(text, "x")

    def test_write_round_trip(self):
        points = [(20.0, 1.25), (1000.0, -0.5), (20000.0, 3.0)]
        text = ascii.write(points, "Curve")
        self.assertTrue(text.startswith("*\tCurve\r\n* Frequency (Hz)\tMagnitude (dB)\r\n"))
        table = ascii.read(text)
        self.assertEqual(table.traces[0].name, "Curve")
        self.assertEqual(table.curve(0), points)

    def test_detection(self):
        for path in (TF_EXPORT, RTA_EXPORT, ROOT / ra.ASCII_DIR / "Bass and treble.txt"):
            self.assertEqual(formats.detect(path), "smaart-ascii")
        others = [p for p in ROOT.rglob("*.txt") if ra.ASCII_DIR not in p.as_posix()]
        self.assertTrue(others)
        for path in others:
            with self.subTest(path.name):
                self.assertFalse(ascii.sniff(path.read_bytes()))

    def test_conversions(self):
        result = SmaartAsciiToAutoeq(trace="right").convert([TF_EXPORT])
        self.assertEqual(result.name, "Tf export Right.csv")
        self.assertIn("trace 'Right', column Magnitude (dB)", result.notes[-1])
        self.assertEqual(len(csv_points(result)), 511)
        source = CSV / "Bass and treble.csv"
        result = AutoeqToSmaartAscii().convert([source])
        self.assertEqual(result.name, "Bass and treble.txt")
        got = ascii.read(result.data.decode()).curve(0)
        self.assertEqual([round(v, 2) for _, v in got],
                         [round(v, 2) for _, v in response.load(source).curve()])


class CurveTests(unittest.TestCase):
    def test_transfer_function_curve(self):
        c = crv.load(CURVES / "Haystack.crv")
        self.assertEqual((c.name, c.kind, c.number("Tolerance"), c.problems()),
                         ("Haystack", "transfer function", 3.0, []))
        self.assertEqual(c.points, [(30.0, 9.0), (250.0, 0.0), (20000.0, -2.0)])

    def test_spectrum_curve(self):
        c = crv.load(CURVES / "House.crv")
        self.assertEqual((c.kind, c.number("Band"), c.problems()), ("spectrum", 3.0, []))
        # Sorted; the repeated 1 kHz row is ignored.
        self.assertEqual(c.points, [(31.5, 85.0), (125.0, 80.0), (1000.0, 75.0), (8000.0, 68.0),
                                    (16000.0, 62.0)])

    def test_microphone_curve(self):
        c = crv.load(CURVES / "Mic.crv")
        self.assertEqual((c.kind, c.header, len(c.points)), ("table", {}, 62))
        self.assertEqual(c.other, ["* Mic TILT03", '"Sens Factor =-12dB, SERNO: TILT03"'])
        self.assertEqual(c.points[0], (10.0, -8.0387))

    def test_problems(self):
        cases = [
            ("Color: FF000000\nShow: 1\nType: TF\n\n20\t0\n200\t1\n", ["no Line width"]),
            ("Line: 2\nType: TF\nBand: 3\n20\t0\n200\t1\n", ["no hex Color",
                                                              "Band in a transfer function curve"]),
            ("Line: 2\nColor: FF000000\nOffset: 1\n20\t0\n200\t1\n",
             ["no Band", "Offset or Tolerance in a spectrum curve"]),
        ]
        for text, problems in cases:
            with self.subTest(problems):
                self.assertEqual(crv.read(text).problems(), problems)
        with self.assertRaisesRegex(ValueError, "fewer than two curve points"):
            crv.read("Line: 2\n20\t1\n0\t2\n", "x")

    def test_write(self):
        points = [(20.0, 1.5), (31.5, -0.25), (20000.0, 0.0)]
        for band, kind in ((None, "transfer function"), (3, "spectrum")):
            with self.subTest(kind):
                c = crv.read(crv.write(points, band))
                self.assertEqual((c.kind, c.points, c.problems()), (kind, points, []))
        self.assertIn("\r\n\r\n20\t1.5\r\n", crv.write(points))
        with self.assertRaisesRegex(ValueError, "band must be one of"):
            crv.write(points, 2)

    def test_conversions(self):
        result = SmaartCurveToAutoeq().convert([CURVES / "Haystack.crv"])
        self.assertEqual((result.name, result.notes[-1]),
                         ("Haystack.csv", "transfer function curve"))
        self.assertEqual(csv_points(result), [(30.0, 9.0), (250.0, 0.0), (20000.0, -2.0)])
        source = CSV / "Bass and treble.csv"
        result = AutoeqToSmaartCurve(tolerance_db=0.2, band=6).convert([source])
        c = crv.read(result.data.decode())
        self.assertEqual((c.kind, c.number("Band")), ("spectrum", 6.0))
        grid = standard_grid()
        for f, _ in c.points:
            self.assertTrue(any(abs(f / g - 1) < 1e-5 for g in grid), f)
        error = max(abs(a - b) for a, b in zip(log_resample(c.points, grid),
                                               log_resample(response.load(source).curve(), grid)))
        self.assertLessEqual(error, 0.2)
        self.assertIn(f"error {error:.2f} dB max", result.notes[0])
        with self.assertRaisesRegex(ValueError, "--tolerance-db must be >= 0"):
            AutoeqToSmaartCurve(tolerance_db=-1)

    def test_offset_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.crv"
            path.write_text("Line: 2\nColor: FF000000\nType: TF\nOffset: 2.5\n20\t0\n200\t1\n")
            self.assertIn("Offset 2.5 dB not applied",
                          SmaartCurveToAutoeq().convert([path]).notes[-1])


if __name__ == "__main__":
    unittest.main()
