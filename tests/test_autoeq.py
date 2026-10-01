import math
import tempfile
import unittest
from pathlib import Path

from helpers import csv_points
from testgen import common, rew, soundid
from eqx import convert
from eqx.autoeq import response
from eqx.curve import interp
from eqx.model import standard_grid
from eqx.rew import cal, mdat
from eqx.soundid import crypto, peqb, swproj, targetpreset

ROOT = common.ROOT
CSV = ROOT / common.CSV_DIR
PROFILE = ROOT / soundid.PEQB_DIR / "Tilt Tilt Wired Average.swhp"
TOLERANCE = 0.005 + 1e-9                    # two decimals


def run(source, target, path, **options):
    paths = path if isinstance(path, list) else [path]
    return convert.CONVERTERS[source, target](**options).convert(paths)


class ReaderTests(unittest.TestCase):
    def test_autoeq_result_columns(self):
        r = response.read("frequency,raw,smoothed,error,equalization\n"
                          "20.00,-6.91,-6.92,-10.18,6.00\n20.20,-6.87,NaN,-10.1,5.9\n")
        self.assertEqual(list(r.columns), ["frequency", "raw", "smoothed", "error", "equalization"])
        self.assertEqual(r.curve("equalization"), [(20.0, 6.0), (20.2, 5.9)])
        with self.assertRaisesRegex(ValueError, "fewer than two"):
            r.curve("smoothed")
        with self.assertRaisesRegex(ValueError, "no 'target' column"):
            r.curve("target")

    def test_no_header(self):
        r = response.read("100 1.5\n20 -2\n")
        self.assertEqual(r.curve(), [(20.0, -2.0), (100.0, 1.5)])

    def test_decimal_comma(self):
        r = response.read("Frequency;dB\n20,5;-1,25\n40;2\n")
        self.assertEqual(r.curve(), [(20.5, -1.25), (40.0, 2.0)])

    def test_rew_export(self):
        text = ("* Measurement data measured by REW V5.40\n* Source: x, y\n"
                "* Freq(Hz), SPL(dB), Phase(degrees)\n"
                "2.929688, 71.123, -12.0\n5.859375, 72.5, -10.0\n")
        r = response.read(text)
        self.assertEqual(list(r.columns), ["frequency", "raw", "phase(degrees)"])
        self.assertEqual(r.curve(), [(2.929688, 71.123), (5.859375, 72.5)])

    def test_value_column_by_name(self):
        r = response.read("gain\tfreq\n1\t20\n2\t40\n")
        self.assertEqual(r.curve(), [(20.0, 1.0), (40.0, 2.0)])

    def test_rejects(self):
        for text, message in (("a,b\n", "no numeric rows"),
                              ("20,1\n30,2,3\n", "3 columns, expected 2"),
                              ("20,x\n30,1\n", "not a number"),
                              ("20\n30\n", "fewer than two columns"),
                              ("20;1\t2\n30;1\t2\n", "ambiguous")):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, message):
                response.read(text, "t")
        with self.assertRaisesRegex(ValueError, "duplicate frequency"):
            response.read("20,1\n20,2\n").curve()


class WriterTests(unittest.TestCase):
    def test_two_decimals(self):
        self.assertEqual(response.write([(20.0, -0.001), (1000.123, 1e-7), (20000, -12.345)]),
                         "frequency,raw\n20.00,0.00\n1000.12,0.00\n20000.00,-12.35\n")

    def test_round_trip(self):
        pts = [(f, round(math.sin(f), 2)) for f in standard_grid()]
        self.assertEqual(response.read(response.write(pts)).curve(),
                         [(round(f, 2), v) for f, v in pts])

    def test_rejects(self):
        for pts in ([(0.0, 1.0)], [(20.0, math.nan)], [(20.001, 0), (20.002, 0)]):
            with self.subTest(pts=pts), self.assertRaises(ValueError):
                response.write(pts)


class FilterTests(unittest.TestCase):
    # Reference points of SoundID's own curve display.
    CASES = [
        (("low-shelf", 200, -2, 1), 20.0, -1.9997981786727905),
        (("low-shelf", 200, -2, 1), 632.4555053710938, -0.019931750372052193),
        (("low-shelf", 41.04457079819126, 15.39906103286385, 1), 20.0, 14.19563102722168),
        (("bell", 1000, 3, 2.6), 644.9180908203125, 0.4621490240097046),
        (("bell", 6500, 4, 1.4), 20000.0, 0.031821757555007935),
    ]

    def test_soundid_curves(self):
        for (kind, f0, gain, q), f, want in self.CASES:
            with self.subTest(kind=kind, f0=f0, f=f):
                flt = targetpreset.Filter(1, kind, True, f0, gain, q, 0)
                self.assertAlmostEqual(targetpreset.filter_response(flt, f), want, places=5)

    def test_high_shelf_mirrors_low_shelf(self):
        high = targetpreset.Filter(1, "high-shelf", True, 1000, 6, 1, 0)
        self.assertAlmostEqual(targetpreset.filter_response(high, 20), 0, places=2)
        self.assertAlmostEqual(targetpreset.filter_response(high, 20000), 6, places=1)

    def test_unknown_type(self):
        with self.assertRaisesRegex(ValueError, "unknown type"):
            targetpreset.filter_response(targetpreset.Filter(1, "notch", True, 1, 1, 1, 0), 1)


class ToAutoeqTests(unittest.TestCase):
    def test_mdat(self):
        path = ROOT / rew.MDAT_DIR / "Room.mdat"
        right = mdat.load(path)[1]
        got = csv_points(run("mdat", "autoeq", path, channel="right"))
        self.assertEqual(len(got), len(right.frequencies))
        for (f, v), want in zip(got, right.response):
            self.assertLess(abs(v - want), TOLERANCE)
        with self.assertRaisesRegex(ValueError, "no Right channel; available: Left"):
            run("mdat", "autoeq", ROOT / rew.MDAT_DIR / "Left only.mdat",
                channel="right")

    def test_swproj(self):
        path = ROOT / soundid.PROJ_DIR / "Bandpass.swproj"
        want = swproj.measurement_curves(swproj.SwProj.open(path))["Right"]
        got = csv_points(run("swproj", "autoeq", path, channel="right"))
        self.assertTrue(all(abs(v - w[1]) < TOLERANCE for (_, v), w in zip(got, want)))

    def test_headphone_response_is_negated_correction(self):
        key = crypto.swhp_key(common.COMPUTER_ID)
        correction = peqb.read(PROFILE.read_bytes(), key).curves[0].points
        got = csv_points(run("peqb", "autoeq", PROFILE, computer_id=common.COMPUTER_ID))
        self.assertEqual(len(got), 355)
        self.assertTrue(all(abs(v + c[1]) < TOLERANCE for (_, v), c in zip(got, correction)))

    def test_project_export_uses_measurement(self):
        eqb = ROOT / soundid.PEQB_DIR / "Flat.eqb"
        result = run("peqb", "autoeq", eqb, channel="right")
        self.assertIn("Right measurement", result.notes[-1])

    def test_encrypted_without_matching_key(self):
        with self.assertRaisesRegex(ValueError, "wrong computer ID"):
            run("peqb", "autoeq", PROFILE, computer_id="g" + "1" * 40)

    def test_target_preset(self):
        path = ROOT / soundid.PRESET_DIR / "Bass and treble.json"
        got = dict(csv_points(run("targetpreset", "autoeq", path)))
        # +4 dB low shelf, -2 dB high shelf; the disabled bell is left out.
        self.assertAlmostEqual(got[20.0], 4.0, delta=0.05)
        self.assertAlmostEqual(got[22000.0], -2.0, delta=0.05)
        flat = run("targetpreset", "autoeq", ROOT / soundid.PRESET_DIR / "Flat.json")
        self.assertTrue(all(v == 0 for _, v in csv_points(flat)))


class FromAutoeqTests(unittest.TestCase):
    def test_rewcal(self):
        path = CSV / "Bass and treble.csv"
        profile, other = cal.read(run("autoeq", "rewcal", path).data.decode())
        want = response.load(path).curve()
        self.assertEqual(len(profile.points), len(want))
        for (f, v), (wf, wv) in zip(profile.points, want):
            # REW calibration values have 6 significant digits.
            self.assertAlmostEqual(f / wf, 1, places=5)
            self.assertEqual(v, wv)
        self.assertEqual(other, ["* AutoEq microphone Bass and treble"])

    def test_headphone_round_trip(self):
        path = CSV / "Tilt Tilt Wired Average Left.csv"
        result = run("autoeq", "peqb", path, reference_db=0.0)
        p = peqb.read(result.data)
        self.assertEqual((p.version, p.encrypted), ((3, 0, 0, 1), False))
        self.assertEqual([c.type_name for c in p.curves],
                         ["CorrectionLeft", "CorrectionRight", "Frame", "LeftErrorBand",
                          "LeftErrorBand", "RightErrorBand", "RightErrorBand"])
        self.assertEqual(p.parameters["META_Model"], path.stem)
        want = peqb.read(PROFILE.read_bytes(), crypto.swhp_key(common.COMPUTER_ID))
        for got, orig in zip(p.curves[0].points, want.curves[0].points):
            self.assertAlmostEqual(got[0], orig[0])
            # Rounded values at rounded frequencies.
            self.assertLess(abs(got[1] - orig[1]), 2 * TOLERANCE)
        band = float(p.parameters["META_FixedErrorBandRange"])
        for (_, lo, _), (_, c, _) in zip(p.curves[3].points, p.curves[0].points):
            self.assertAlmostEqual(lo, c - band)

    def test_headphone_reference_and_target(self):
        path = CSV / "Bass and treble.csv"
        # Response equal to the target: no correction.
        p = peqb.read(run("autoeq", "peqb", path, target_curve=path).data)
        self.assertTrue(all(abs(r) < 1e-12 for _, r, _ in p.curves[0].points))
        # Default reference: the 1 kHz level maps to 0 dB.
        p = peqb.read(run("autoeq", "peqb", path).data)
        grid = [f for f, _, _ in p.curves[0].points]
        values = [r for _, r, _ in p.curves[0].points]
        self.assertAlmostEqual(interp(grid, values, 1000.0), 0.0, places=9)

    def test_boost_cap(self):
        for f, want in ((0, 0), (20, 6), (40, 12), (1000, 12), (20000, 12), (21000, 6),
                        (22000, 0), (23000, 0)):
            with self.subTest(f=f):
                self.assertAlmostEqual(peqb.headphone_boost_cap(f), want)
        # Bass and treble: +4 dB bass, so the correction is -4 dB there; nothing is capped
        # except at 22 kHz, where the cap is 0 dB and the correction +2 dB.
        notes = run("autoeq", "peqb", CSV / "Bass and treble.csv").notes
        self.assertTrue(notes[1].endswith("; 1 points above SoundID's boost cap"), notes[1])

    def test_right_side(self):
        left, right = CSV / "Room Left.csv", CSV / "Room Right.csv"
        p = peqb.read(run("autoeq", "peqb", [left, right]).data)
        self.assertNotEqual(p.curves[0].points, p.curves[1].points)

    def test_speaker_project(self):
        left, right = CSV / "Room Left.csv", CSV / "Room Right.csv"
        mic = ROOT / soundid.MIC_DIR / "FLAT01.swmicpkg"
        project = swproj.SwProj(run("autoeq", "swproj", [left, right], mic_profile=mic,
                                    reference_spl=80.0).data)
        curves = swproj.measurement_curves(project)
        self.assertEqual(list(curves), ["Left", "Right"])
        want = response.load(right).curve()
        wf, wv = [f for f, _ in want], [v for _, v in want]
        for f, r, gd in curves["Right"]:
            self.assertAlmostEqual(r, interp(wf, wv, f) - 80.0, places=6)
            self.assertEqual(gd, 0.0)
        single = swproj.SwProj(run("autoeq", "swproj", left, mic_profile=mic).data)
        self.assertEqual(len(single.eqb.curves), 2)

    def test_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.csv"
            path.write_text("frequency,raw,equalization\n20,1,-1\n40,2,-2\n")
            profile, _ = cal.read(run("autoeq", "rewcal", path, column="equalization")
                                  .data.decode())
            self.assertEqual(profile.points, [(20.0, -1.0), (40.0, -2.0)])


if __name__ == "__main__":
    unittest.main()
