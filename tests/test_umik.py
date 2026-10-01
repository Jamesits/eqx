import tempfile
import unittest
from pathlib import Path

from testgen import common, minidsp

from eqx import convert, formats
from eqx.convert.to_swmicpkg import UmikToSwmicpkg
from eqx.convert.to_swproj import load_mic_profile
from eqx.minidsp import umik
from eqx.rew import cal
from eqx.soundid import swmicpkg

TESTDATA = common.ROOT
DIR = TESTDATA / "minidsp/umik"
UMIK1, UMIK1_90 = DIR / "7000042.txt", DIR / "7000042_90deg.txt"
UMIK2, UMIK2_90 = DIR / "8100042.txt", DIR / "8100042_90deg.txt"


class ReaderTests(unittest.TestCase):
    def test_umik1(self):
        u = umik.load(UMIK1)
        self.assertEqual(
            (u.model, u.serial, u.sensitivity_db, u.analog_gain_db),
            ("UMIK-1", "7000042", -5.5, None),
        )
        self.assertEqual(
            (u.profile.name, u.profile.angle, len(u.profile.points)),
            ("7000042", "degrees_0", 615),
        )
        self.assertEqual(u.profile.points[0][0], 10.054)
        self.assertEqual(u.profile.points[-1][0], 20016.816)

    def test_umik2(self):
        u = umik.load(UMIK2)
        self.assertEqual(
            (u.model, u.serial, u.sensitivity_db, u.analog_gain_db),
            ("UMIK-2", "8100042", -14.5, 18.0),
        )
        self.assertEqual(len(u.profile.points), 615)

    def test_points_follow_curve(self):
        for path, angle in ((UMIK1, "degrees_0"), (UMIK2_90, "degrees_90")):
            sections = minidsp.UMIK_SECTIONS[angle]
            for f, g in umik.load(path).profile.points:
                self.assertAlmostEqual(
                    g, common.response(sections, f)[0], delta=1e-3
                )  # rounded frequencies

    def test_angle(self):
        self.assertEqual(umik.load(UMIK1_90).profile.angle, "degrees_90")
        self.assertEqual(umik.load(UMIK2_90).profile.angle, "degrees_90")
        # By the header line alone, and by the name alone.
        text = UMIK1_90.read_text()
        self.assertEqual(umik.read(text, "x").profile.angle, "degrees_90")
        self.assertEqual(
            umik.read(UMIK1.read_text(), "x_90deg").profile.angle, "degrees_90"
        )

    def test_header_variants(self):
        u = umik.read(
            "Sens Factor = +1.25 dB, AGain = 0dB, SERNO: 8000001\n20 0\n30 1\n"
        )
        self.assertEqual(
            (u.sensitivity_db, u.analog_gain_db, u.serial), (1.25, 0.0, "8000001")
        )
        self.assertEqual(
            umik.read('"Sens Factor =.5dB, SERNO: 9000001"\n20 0\n30 1\n').model,
            "unknown",
        )

    def test_no_header(self):
        with self.assertRaisesRegex(ValueError, "no UMIK header"):
            umik.read("20 0\n30 1\n", "x")

    def test_rew_reads_it(self):
        # The same table as a REW calibration file; REW skips the header lines.
        profile, other = cal.load(UMIK2_90)
        self.assertEqual(profile.points, umik.load(UMIK2_90).profile.points)
        self.assertEqual(len(other), 2)


class DetectionTests(unittest.TestCase):
    def test_sniff(self):
        for path in DIR.iterdir():
            self.assertEqual(formats.detect(path), "umik")
        for path in (
            TESTDATA / "rew/rewcal/TILT01 degrees_0.txt",
            TESTDATA / "rogueamoeba/soundsource/Tilt - Flat.txt",
            TESTDATA / "soundid/soundid-export-txt/Tilt - Flat.txt",
        ):
            self.assertNotEqual(formats.detect(path), "umik")
            self.assertFalse(umik.sniff(path.read_bytes()))

    def test_only_conversion(self):
        self.assertIs(convert.find("umik"), UmikToSwmicpkg)


class SwmicpkgTests(unittest.TestCase):
    def test_package(self):
        # Inputs in any order.
        result = UmikToSwmicpkg().convert([UMIK1_90, UMIK1])
        self.assertEqual(result.name, "7000042.swmicpkg")
        self.assertIn(
            "UMIK-1 7000042, sensitivity -5.5 dBFS (not stored)", result.notes
        )
        self.assertIn("degrees_30: copy of degrees_0", result.notes)
        profiles = {
            p.angle: p for p in swmicpkg.read_all(result.data.decode(), "7000042")
        }
        self.assertEqual(list(profiles), list(swmicpkg.ANGLES))
        self.assertEqual(profiles["degrees_30"].points, profiles["degrees_0"].points)
        for angle, path in (("degrees_0", UMIK1), ("degrees_90", UMIK1_90)):
            points = profiles[angle].points
            self.assertEqual(
                [f for f, _ in points], [round(f, 1) for f in swmicpkg.grid()]
            )
            sections = minidsp.UMIK_SECTIONS[angle]
            for f, g in points:
                self.assertAlmostEqual(g, common.response(sections, f)[0], delta=0.02)
        # The 90 degree table loses treble.
        self.assertLess(
            profiles["degrees_90"].points[-1][1],
            profiles["degrees_0"].points[-1][1] - 3,
        )

    def test_zero_only(self):
        result = UmikToSwmicpkg(serial="S").convert([UMIK2])
        self.assertEqual(result.name, "S.swmicpkg")
        profiles = swmicpkg.read_all(result.data.decode())
        self.assertEqual(profiles[2].points, profiles[0].points)

    def test_rejected(self):
        with self.assertRaisesRegex(
            ValueError, "different microphones: 7000042, 8100042"
        ):
            UmikToSwmicpkg().convert([UMIK1, UMIK2])
        with self.assertRaisesRegex(ValueError, "two degrees_0 tables"):
            UmikToSwmicpkg().convert([UMIK1, UMIK1])
        with self.assertRaisesRegex(
            ValueError, "no degrees_0 table; add the 7000042.txt"
        ):
            UmikToSwmicpkg().convert([UMIK1_90])


class MicProfileTests(unittest.TestCase):
    def test_load(self):
        profile = load_mic_profile(UMIK1)
        self.assertEqual((profile.name, profile.angle), ("7000042", "degrees_0"))
        self.assertEqual(
            load_mic_profile(UMIK1_90).points, umik.load(UMIK1_90).profile.points
        )
        with self.assertRaisesRegex(
            ValueError, "--mic-angle applies only to profiles with several tables"
        ):
            load_mic_profile(UMIK1_90, "degrees_90")

    def test_format_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mic.dat"
            path.write_bytes(UMIK2.read_bytes())
            self.assertEqual(load_mic_profile(path, kind="umik").name, "8100042")


if __name__ == "__main__":
    unittest.main()
