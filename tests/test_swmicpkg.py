import base64
import json
import unittest

from testgen import common

from eqx.convert.to_swmicpkg import RewcalToSwmicpkg
from eqx.convert.to_swproj import load_mic_profile
from eqx.rew import cal
from eqx.soundid import swmicpkg

TESTDATA = common.ROOT
PACKAGE = TESTDATA / "soundid/swmicpkg/TILT01.swmicpkg"
# Converted with the degrees_30 table of PACKAGE.
PROJECT = TESTDATA / "soundid/swproj/Bandpass.swproj"


def parse_rew(text):
    points = []
    for line in text.splitlines():
        try:
            points.append(tuple(float(v) for v in line.split()))
        except ValueError:
            continue
    return points


class PackageTests(unittest.TestCase):
    def test_plain_table(self):
        profile = swmicpkg.load(PACKAGE)
        self.assertEqual(
            (profile.name, profile.angle, len(profile.points)),
            ("TILT01", "degrees_0", 300),
        )
        self.assertEqual(profile.points[0], (20.0, -0.97))
        self.assertEqual(profile.points[-1], (20000.0, 1.92))

    def test_encrypted_table_matches_project(self):
        # The project holds the table decrypted from this package.
        self.assertEqual(
            swmicpkg.load(PACKAGE, "degrees_30").points,
            load_mic_profile(PROJECT).points,
        )

    def test_all_tables(self):
        for path in sorted((TESTDATA / "soundid/swmicpkg").glob("*.swmicpkg")):
            with self.subTest(path=path.name):
                profiles = swmicpkg.read_all(path.read_text(), path.stem)
                self.assertEqual(
                    [(p.angle, len(p.points)) for p in profiles],
                    [("degrees_0", 300), ("degrees_30", 300), ("degrees_90", 300)],
                )

    def test_corrupt_table_is_rejected(self):
        package = json.loads(PACKAGE.read_text())
        blob = base64.b64decode(package["degrees_30"])
        package["degrees_30"] = base64.b64encode(blob[:-1]).decode()
        with self.assertRaisesRegex(ValueError, "degrees_30 is not a valid table"):
            swmicpkg.read(json.dumps(package), "degrees_30")

    def test_missing_table_lists_available(self):
        with self.assertRaisesRegex(ValueError, "degrees_0, degrees_30, degrees_90"):
            swmicpkg.load(PACKAGE, "degrees_45")


class ProjectTests(unittest.TestCase):
    def test_tables_from_project(self):
        # The project holds one table; it needs no angle.
        profile = load_mic_profile(PROJECT)
        self.assertEqual(
            (profile.name, profile.angle, len(profile.points)),
            ("TILT01", "degrees_30", 300),
        )
        self.assertEqual(profile.points[-1], (20000.0, 0.96))

    def test_angle_of_single_table(self):
        with self.assertRaisesRegex(
            ValueError,
            r"one microphone table \(degrees_30\); "
            "--mic-curve applies only",
        ):
            load_mic_profile(PROJECT, "degrees_30")

    def test_angle_of_several_tables(self):
        self.assertEqual(load_mic_profile(PACKAGE).angle, "degrees_0")
        self.assertEqual(
            load_mic_profile(PACKAGE, "degrees_90").points,
            swmicpkg.load(PACKAGE, "degrees_90").points,
        )
        with self.assertRaisesRegex(
            ValueError,
            "no microphone 'degrees_45' table; "
            "available: degrees_0, degrees_30, degrees_90$",
        ):
            load_mic_profile(PACKAGE, "degrees_45")

    def test_tables_are_mic_response(self):
        # Off axis a microphone loses treble; an inverse table would rise instead.
        on_axis = dict(swmicpkg.load(PACKAGE).points)
        off_axis = dict(swmicpkg.load(PACKAGE, "degrees_90").points)
        self.assertLess(off_axis[20000.0], on_axis[20000.0] - 3)


class RewTests(unittest.TestCase):
    def test_points_written_unchanged(self):
        for angle, path in (
            ("degrees_0", PACKAGE),
            ("degrees_30", PROJECT),
            ("degrees_90", PACKAGE),
        ):
            with self.subTest(angle=angle):
                profile = load_mic_profile(path, None if path == PROJECT else angle)
                text = cal.write(profile, "SoundID")
                self.assertTrue(
                    text.startswith(f"* SoundID microphone TILT01 {angle}\n")
                )
                self.assertEqual(parse_rew(text), profile.points)

    def test_negative_zero(self):
        table = base64.b64encode(b"20.0\t-0.00\n1000.0\t-1.50\n").decode()
        profile = swmicpkg.read(json.dumps({"degrees_0": table}), name="X")
        self.assertEqual(
            cal.write(profile, "SoundID"),
            "* SoundID microphone X degrees_0\n20\t0\n1000\t-1.5\n",
        )


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        profiles = swmicpkg.read_all(PACKAGE.read_text(), "TILT01")
        data = swmicpkg.write(profiles)
        self.assertEqual(data, PACKAGE.read_bytes())
        package = json.loads(data)
        self.assertEqual(base64.b64decode(package["degrees_30"])[:16], bytes(16))
        with self.assertRaisesRegex(ValueError, "two degrees_0 tables"):
            swmicpkg.write(profiles[:1] * 2)

    def test_from_rewcal(self):
        tables = [
            TESTDATA / f"rew/rewcal/TILT01 {angle}.txt" for angle in swmicpkg.ANGLES
        ]
        result = RewcalToSwmicpkg(serial="X").convert(tables)
        self.assertEqual(result.name, "X.swmicpkg")
        got = swmicpkg.read_all(result.data.decode(), "X")
        want = swmicpkg.read_all(PACKAGE.read_text(), "TILT01")
        for a, b in zip(got, want, strict=True):
            self.assertEqual(a.angle, b.angle)
            self.assertEqual([f for f, _ in a.points], [f for f, _ in b.points])
            for (_, x), (_, y) in zip(a.points, b.points):
                self.assertAlmostEqual(x, y, delta=0.011)

    def test_missing_angles(self):
        result = RewcalToSwmicpkg().convert(
            [TESTDATA / "rew/rewcal/TILT01 sensitivity.cal"]
        )
        self.assertEqual(result.name, "TILT01 sensitivity.swmicpkg")
        self.assertIn(
            "degrees_0: TILT01 sensitivity.cal, 300 points; 1 other lines ignored",
            result.notes,
        )
        self.assertIn("degrees_90: copy of degrees_0", result.notes)
        profiles = swmicpkg.read_all(result.data.decode())
        self.assertEqual([p.angle for p in profiles], list(swmicpkg.ANGLES))
        self.assertEqual(profiles[2].points, profiles[0].points)


if __name__ == "__main__":
    unittest.main()
