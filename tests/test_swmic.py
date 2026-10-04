import shutil
import tempfile
import unittest
from pathlib import Path

from testgen import common

from eqx import convert, formats
from eqx.convert.to_rewcal import SwmicToRewcal
from eqx.convert.to_swmicpkg import SwmicToSwmicpkg
from eqx.convert.to_swproj import load_mic_profile
from eqx.soundid import swmic, swmicpkg

TESTDATA = common.ROOT
DIR = TESTDATA / "soundid/swmic"
PLAIN = DIR / "TILT01_cal_0degree.txt"
SIDE_30 = DIR / "TILT01_cal_Sonarworks_30degree.swmic"
SIDE_90 = DIR / "TILT01_cal_Sonarworks_90degree.swmic"
PACKAGE = TESTDATA / "soundid/swmicpkg/TILT01.swmicpkg"
REAL = TESTDATA / "real" / "SoundID Reference" / "micprofiles"


class ReaderTests(unittest.TestCase):
    def test_tables_match_package(self):
        for path, encrypted in ((PLAIN, False), (SIDE_30, True), (SIDE_90, True)):
            with self.subTest(path.name):
                t = swmic.load(path)
                self.assertEqual((t.serial, t.encrypted), ("TILT01", encrypted))
                self.assertEqual(
                    t.profile.points, swmicpkg.load(PACKAGE, t.profile.angle).points
                )

    def test_name(self):
        self.assertEqual(
            swmic.parse_name("37K469_cal_Sonarworks_90degree"),
            ("37K469", "degrees_90"),
        )
        self.assertEqual(
            swmic.parse_name("A_cal_B_cal_0degree"), ("A_cal_B", "degrees_0")
        )
        self.assertEqual(swmic.parse_name("mic"), ("mic", "degrees_0"))

    def test_renamed_encrypted(self):
        t = swmic.read(SIDE_90.read_bytes(), "x")
        self.assertTrue(t.encrypted)
        self.assertEqual(t.profile.angle, "degrees_0")

    def test_corrupt(self):
        with self.assertRaisesRegex(ValueError, "cannot decrypt"):
            swmic.read(SIDE_30.read_bytes()[:-1], "x")


class DetectionTests(unittest.TestCase):
    def test_sniff(self):
        for path in DIR.iterdir():
            self.assertEqual(formats.detect(path), "swmic")
        self.assertTrue(swmic.sniff(PLAIN.read_bytes()))
        txt = [
            p
            for d in ("rew", "minidsp", "daytonaudio", "rogueamoeba", "soundid")
            for p in (TESTDATA / d).rglob("*.txt")
            if p.parent != DIR
        ]
        self.assertTrue(txt)
        for path in txt:
            with self.subTest(path.name):
                self.assertFalse(swmic.sniff(path.read_bytes()))

    def test_conversions(self):
        self.assertIs(convert.find("swmic", "swmicpkg"), SwmicToSwmicpkg)
        self.assertIs(convert.find("swmic", "rewcal"), SwmicToRewcal)


class ConversionTests(unittest.TestCase):
    def test_package(self):
        # Inputs in any order.
        result = SwmicToSwmicpkg().convert([SIDE_90, PLAIN, SIDE_30])
        self.assertEqual(result.name, "TILT01.swmicpkg")
        self.assertEqual(
            result.notes,
            [
                "degrees_90: TILT01_cal_Sonarworks_90degree.swmic, 300 points",
                "degrees_0: TILT01_cal_0degree.txt, 300 points",
                "degrees_30: TILT01_cal_Sonarworks_30degree.swmic, 300 points",
            ],
        )
        got = swmicpkg.read_all(result.data.decode(), "TILT01")
        want = swmicpkg.read_all(PACKAGE.read_text(), "TILT01")
        for a, b in zip(got, want, strict=True):
            self.assertEqual(a.angle, b.angle)
            for (fa, x), (fb, y) in zip(a.points, b.points, strict=True):
                self.assertEqual(fa, fb)
                # Resampled from rounded frequencies.
                self.assertAlmostEqual(x, y, delta=0.011)

    def test_missing_angles(self):
        result = SwmicToSwmicpkg(serial="X").convert([PLAIN])
        self.assertEqual(result.name, "X.swmicpkg")
        self.assertIn("degrees_90: copy of degrees_0", result.notes)
        with self.assertRaisesRegex(
            ValueError, "no degrees_0 table; add the TILT01_cal_0degree.txt file"
        ):
            SwmicToSwmicpkg().convert([SIDE_30])

    def test_unknown_angle(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TILT01_cal_45degree.txt"
            shutil.copy(PLAIN, path)
            with self.assertRaisesRegex(ValueError, "no degrees_45 table in a package"):
                SwmicToSwmicpkg().convert([PLAIN, path])

    def test_rewcal(self):
        result = SwmicToRewcal().convert([SIDE_90])
        self.assertEqual(result.name, "TILT01 degrees_90.txt")
        self.assertEqual(
            result.data,
            (TESTDATA / "rew/rewcal/TILT01 degrees_90.txt").read_bytes(),
        )

    def test_mic_profile(self):
        profile = load_mic_profile(SIDE_30)
        self.assertEqual((profile.name, profile.angle), ("TILT01", "degrees_30"))
        with self.assertRaisesRegex(ValueError, "--mic-curve applies only"):
            load_mic_profile(SIDE_30, "degrees_30")


@unittest.skipUnless(REAL.is_dir(), "no SoundID tables in testdata/real")
class RealFileTests(unittest.TestCase):
    def test_files(self):
        paths = sorted(REAL.rglob("*_cal_*degree.*"))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path.name):
                self.assertEqual(formats.detect(path), "swmic")
                t = swmic.load(path)
                self.assertEqual(t.encrypted, path.suffix == ".swmic")
                self.assertEqual(len(t.profile.points), swmicpkg.GRID_POINTS)
                if t.encrypted:
                    # Next to it: the same table decrypted.
                    plain = path.with_suffix(".txt")
                    if plain.exists():
                        self.assertEqual(
                            t.profile.points, swmic.load(plain).profile.points
                        )


if __name__ == "__main__":
    unittest.main()
