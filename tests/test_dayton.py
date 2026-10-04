import tempfile
import unittest
from pathlib import Path

from testgen import common, daytonaudio

from eqx import convert, formats
from eqx.convert.to_swmicpkg import DaytonToSwmicpkg
from eqx.convert.to_swproj import load_mic_profile
from eqx.daytonaudio import mic
from eqx.rew import cal
from eqx.soundid import swmicpkg

TESTDATA = common.ROOT
DIR = TESTDATA / "daytonaudio/dayton"
USB, USB_90 = DIR / "1600042.txt", DIR / "1600042_90deg.txt"
OMNIMIC, ANALOG = DIR / "40k0042.omm", DIR / "99-00042.txt"
REAL = TESTDATA / "real" / "daytonaudio"


class ReaderTests(unittest.TestCase):
    def test_usb(self):
        m = mic.load(USB)
        self.assertEqual(
            (m.model, m.serial, m.sensitivity_db, m.sensitivity_unit, m.reference_hz),
            ("UMM-6 / iMM-6", "1600042", -18.5, "dBFS", None),
        )
        self.assertEqual(
            (m.profile.name, m.profile.angle, len(m.profile.points), len(m.phase)),
            ("1600042", "degrees_0", 97, 97),
        )
        self.assertEqual(m.profile.points[0][0], 4.6758)

    def test_omnimic(self):
        m = mic.load(OMNIMIC)
        self.assertEqual(
            (m.model, m.serial, m.sensitivity_db, len(m.profile.points)),
            ("OmniMic", "40k0042", -3.25, 464),
        )
        self.assertGreater(m.profile.points[-1][0], 39000)

    def test_analog(self):
        m = mic.load(ANALOG)
        self.assertEqual(
            (m.model, m.serial, m.sensitivity_db, m.sensitivity_unit, m.reference_hz),
            ("EMM-6", "99-00042", -40.5, "dBV", 1000.0),
        )
        self.assertEqual(m.sensitivity, "-40.5 dBV/Pa at 1000 Hz")
        self.assertEqual((len(m.profile.points), m.phase), (256, []))
        self.assertEqual(
            (m.profile.points[0][0], m.profile.points[-1][0]), (20.0, 20000.0)
        )

    def test_points_follow_curve(self):
        for path, sections, gain, phase in (
            (USB, daytonaudio.USB_SECTIONS["degrees_0"], 1e-3, 0.01),
            (USB_90, daytonaudio.USB_SECTIONS["degrees_90"], 1e-3, 0.01),
            (OMNIMIC, daytonaudio.OMNIMIC_SECTIONS, 1e-3, 0.01),
            (ANALOG, daytonaudio.ANALOG_SECTIONS, 0.06, None),
        ):
            m = mic.load(path)
            # Rounded frequencies.
            for f, g in m.profile.points:
                self.assertAlmostEqual(g, common.response(sections, f)[0], delta=gain)
            for f, p in m.phase:
                self.assertAlmostEqual(p, common.response(sections, f)[1], delta=phase)

    def test_angle(self):
        m = mic.load(USB_90)
        self.assertEqual((m.serial, m.profile.angle), ("1600042", "degrees_90"))
        m = mic.read(ANALOG.read_text(), "9957_90deg")
        self.assertEqual((m.serial, m.profile.angle), ("9957", "degrees_90"))

    def test_no_header(self):
        with self.assertRaisesRegex(ValueError, "no Dayton Audio header"):
            mic.read("20 0\n30 1\n", "x")

    def test_rew_reads_it(self):
        for path in (USB, OMNIMIC, ANALOG):
            profile, other = cal.load(path)
            self.assertEqual(profile.points, mic.load(path).profile.points)
            self.assertEqual(len(other), 1)


class DetectionTests(unittest.TestCase):
    def test_sniff(self):
        for path in DIR.iterdir():
            self.assertEqual(formats.detect(path), "dayton")
            self.assertTrue(mic.sniff(path.read_bytes()))
        for path in (
            *(TESTDATA / "minidsp/umik").iterdir(),
            TESTDATA / "rew/rewcal/TILT01 degrees_0.txt",
            TESTDATA / "rogueamoeba/soundsource/Tilt - Flat.txt",
            TESTDATA / "soundid/soundid-export-txt/Tilt - Flat.txt",
            TESTDATA / "rationalacoustics/smaart-ascii/Tf export.txt",
        ):
            with self.subTest(path.name):
                self.assertNotEqual(formats.detect(path), "dayton")
                self.assertFalse(mic.sniff(path.read_bytes()))

    def test_extension(self):
        # The same layout as a Dayton Audio USB file; .cal is REW only.
        path = TESTDATA / "rew/rewcal/TILT01 sensitivity.cal"
        self.assertTrue(mic.sniff(path.read_bytes()))
        self.assertEqual(formats.detect(path), "rewcal")

    def test_only_conversion(self):
        self.assertIs(convert.find("dayton"), DaytonToSwmicpkg)


class SwmicpkgTests(unittest.TestCase):
    def test_package(self):
        # Inputs in any order.
        result = DaytonToSwmicpkg().convert([USB_90, USB])
        self.assertEqual(result.name, "1600042.swmicpkg")
        self.assertIn(
            "UMM-6 / iMM-6 1600042, sensitivity -18.5 dBFS (not stored)", result.notes
        )
        self.assertIn("phase not stored", result.notes)
        self.assertIn("degrees_30: copy of degrees_0", result.notes)
        profiles = {
            p.angle: p for p in swmicpkg.read_all(result.data.decode(), "1600042")
        }
        self.assertEqual(list(profiles), list(swmicpkg.ANGLES))
        self.assertEqual(profiles["degrees_30"].points, profiles["degrees_0"].points)
        # The 90 degree table loses treble.
        self.assertLess(
            profiles["degrees_90"].points[-1][1],
            profiles["degrees_0"].points[-1][1] - 3,
        )

    def test_analog(self):
        result = DaytonToSwmicpkg().convert([ANALOG])
        self.assertEqual(result.name, "99-00042.swmicpkg")
        self.assertIn(
            "EMM-6 99-00042, sensitivity -40.5 dBV (not stored)", result.notes
        )
        self.assertNotIn("phase not stored", result.notes)
        points = swmicpkg.read_all(result.data.decode())[0].points
        for f, g in points:
            if 20 <= f <= 20000:
                self.assertAlmostEqual(
                    g, common.response(daytonaudio.ANALOG_SECTIONS, f)[0], delta=0.1
                )

    def test_rejected(self):
        with self.assertRaisesRegex(
            ValueError, "different microphones: 1600042, 40k0042"
        ):
            DaytonToSwmicpkg().convert([USB, OMNIMIC])
        with self.assertRaisesRegex(ValueError, "two degrees_0 tables"):
            DaytonToSwmicpkg().convert([USB, USB])
        with self.assertRaisesRegex(
            ValueError, "no degrees_0 table; add the 1600042.txt"
        ):
            DaytonToSwmicpkg().convert([USB_90])


class MicProfileTests(unittest.TestCase):
    def test_load(self):
        for path in (USB, OMNIMIC, ANALOG):
            self.assertEqual(
                load_mic_profile(path).points, mic.load(path).profile.points
            )
        self.assertEqual(load_mic_profile(USB_90).angle, "degrees_90")
        with self.assertRaisesRegex(
            ValueError, "--mic-curve applies only to profiles with several tables"
        ):
            load_mic_profile(USB_90, "degrees_90")

    def test_format_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mic.dat"
            path.write_bytes(OMNIMIC.read_bytes())
            self.assertEqual(load_mic_profile(path, kind="dayton").name, "40k0042")


@unittest.skipUnless(
    REAL.is_dir(), "no Dayton Audio files in testdata/real/daytonaudio"
)
class RealFileTests(unittest.TestCase):
    def test_files(self):
        paths = sorted(REAL.iterdir())
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path.name):
                self.assertEqual(formats.detect(path), "dayton")
                m = mic.load(path)
                self.assertEqual(m.profile.angle, "degrees_0")
                if m.reference_hz is None:
                    self.assertEqual(len(m.phase), len(m.profile.points))
                else:
                    self.assertEqual((m.model, m.serial), ("EMM-6", path.stem))
                # The gain is 0 dB at about 1 kHz.
                gain = min(m.profile.points, key=lambda p: abs(p[0] - 1000))[1]
                self.assertAlmostEqual(gain, 0, delta=0.25)


if __name__ == "__main__":
    unittest.main()
