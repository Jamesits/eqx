import unittest

from testgen import common, dirac
from eqx.autoeq import response
from eqx.convert.to_autoeq import TargetcurveToAutoeq
from eqx.convert.to_targetcurve import AutoeqToTargetcurve
from eqx.model import standard_grid
from eqx.dirac import targetcurve
from eqx.dirac.targetcurve import TargetCurve

ROOT = common.ROOT
TILT = ROOT / dirac.TARGETCURVE_DIR / "Tilt.targetcurve"
CSV = ROOT / common.CSV_DIR / "Bass and treble.csv"


class ReaderTests(unittest.TestCase):
    def test_generated(self):
        c = targetcurve.load(TILT)
        self.assertEqual((c.name, c.device_name, c.low_hz, c.high_hz), ("Tilt", "", 20, 20000))
        self.assertEqual(c.breakpoints[0], (20, 6))
        c.validate()

    def test_grammar(self):
        # CRLF, extra spaces, breakpoints end at the first other line, limits
        # are searched after it.
        text = ("NAME\r\nx\r\nBREAKPOINTS\r\n  20   1 \r\n1000 0\r\nnote\r\n5 5\r\n"
                "LOWLIMITHZ\r\n30\r\nother\r\nHIGHLIMITHZ\r\n18000\r\n")
        c = targetcurve.read(text)
        self.assertEqual(c.breakpoints, [(20, 1), (1000, 0)])
        self.assertEqual((c.name, c.low_hz, c.high_hz), ("x", 30, 18000))

    def test_header_lines_are_exact(self):
        with self.assertRaisesRegex(ValueError, "no BREAKPOINTS"):
            targetcurve.read("breakpoints\n20 0\n")
        # " LOWLIMITHZ" is not the key: the defaults apply.
        c = targetcurve.read("BREAKPOINTS\n20 0\n LOWLIMITHZ\n30\nHIGHLIMITHZ\n40\n")
        self.assertEqual((c.low_hz, c.high_hz), (10, 24000))

    def test_limits_need_both(self):
        c = targetcurve.read("BREAKPOINTS\n20 0\nLOWLIMITHZ\n30\nHIGHLIMITHZ\nx\n")
        self.assertEqual((c.low_hz, c.high_hz), (10, 24000))

    def test_validate(self):
        for points, low, high, message in (
                ([], 10, 100, "no breakpoints"),
                ([(-1, 0), (10, 0)], 10, 100, "negative"),
                ([(100, 0), (100, 1)], 10, 100, "not increasing"),
                ([(100, 0)], 100, 100, "limits"),
                ([(0, 0), (24000, 0)], 10, 100, "no breakpoint inside")):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    TargetCurve("", "", points, low, high).validate()
        # The high limit is clamped to 24 kHz first.
        TargetCurve("", "", [(100, 0)], 23000, 30000).validate()

    def test_extended(self):
        c = TargetCurve("", "", [(20, 2), (1000, -1)])
        self.assertEqual(c.extended(), [(0, 2), (20, 2), (1000, -1), (24000, -1)])

    def test_response(self):
        c = TargetCurve("", "", [(100, 0), (10000, -4)])
        for got, want in zip(c.response([10, 100, 1000, 10000, 20000]), [0, 0, -2, -4, -4],
                             strict=True):
            self.assertAlmostEqual(got, want, places=12)


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        c = TargetCurve("Name", "Device", [(20, 1.5), (1000, 0), (20000, -2.25)], 20, 20000)
        self.assertEqual(targetcurve.read(targetcurve.write(c)), c)

    def test_stored_file_is_rewritten(self):
        data = TILT.read_bytes()
        self.assertEqual(targetcurve.write(targetcurve.read(data.decode())).encode(), data)

    def test_number_format(self):
        c = TargetCurve("", "", [(12345.678, -0.0), (1e6, 1e-7)])
        self.assertIn("12345.7 0\n1e+06 1e-07\n", targetcurve.write(c))

    def test_rejected(self):
        with self.assertRaisesRegex(ValueError, "line break"):
            targetcurve.write(TargetCurve("a\nb", "", [(20, 0)]))
        with self.assertRaisesRegex(ValueError, "not increasing"):
            targetcurve.write(TargetCurve("", "", [(20, 0), (20, 1)]))


class ConversionTests(unittest.TestCase):
    def test_to_autoeq(self):
        points = response.read(TargetcurveToAutoeq().convert([TILT]).data.decode()).curve()
        want = targetcurve.load(TILT).response([f for f, _ in points])
        for (_, got), w in zip(points, want, strict=True):
            self.assertAlmostEqual(got, w, delta=0.01)      # CSV: 2 decimals

    def test_from_autoeq(self):
        result = AutoeqToTargetcurve(tolerance_db=0.1, low_hz=20, high_hz=20000).convert([CSV])
        c = targetcurve.read(result.data.decode())
        self.assertEqual((c.name, c.low_hz, c.high_hz), ("Bass and treble", 20, 20000))
        self.assertLess(len(c.breakpoints), 40)
        grid = standard_grid()
        want = response.load(CSV).curve()
        wanted = dict(want)
        for f, got in zip(grid, c.response(grid)):
            if f in wanted:
                self.assertAlmostEqual(got, wanted[f], delta=0.11)

    def test_simplify(self):
        points = [(f, 0.0) for f in (10, 20, 40, 80)]
        self.assertEqual(targetcurve.simplify(points, 0.01), [(10, 0.0), (80, 0.0)])
        points[2] = (40, 1.0)
        self.assertEqual(targetcurve.simplify(points, 0.6), [(10, 0.0), (40, 1.0), (80, 0.0)])
        self.assertEqual(targetcurve.simplify(points, 0.4), points)


if __name__ == "__main__":
    unittest.main()
