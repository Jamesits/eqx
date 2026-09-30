import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eqx import cli, convert, formats
from eqx.fileformat import Format, Inspector
from eqx.rew import cal
from eqx.soundid import swmicpkg, swproj

TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
PACKAGE = TESTDATA / "soundid/swmicpkg/TILT01.swmicpkg"
MDAT = TESTDATA / "rew/mdat/Room.mdat"
SAMPLES = {
    "swproj": TESTDATA / "soundid/swproj/Bandpass.swproj",
    "peqb": TESTDATA / "soundid/peqb/Tilt Tilt Wired Average.swhp",
    "swmicpkg": PACKAGE,
    "targetpreset": TESTDATA / "soundid/targetpreset/Bass and treble.json",
    "mdat": MDAT,
    "rewcal": TESTDATA / "rew/rewcal/TILT01 sensitivity.cal",
    "autoeq": TESTDATA / "autoeq/csv/Bass and treble.csv",
    "soundid-export-biquad-json": TESTDATA / "soundid/soundid-export-biquad-json/Tilt Fluid.bin",
    "soundid-export-peq-json": TESTDATA / "soundid/soundid-export-peq-json/Tilt Grace.bin",
    "soundid-export-biquad-xml": TESTDATA / "soundid/soundid-export-biquad-xml/Tilt.adam",
    "soundid-export-lvnd": TESTDATA / "soundid/soundid-export-lvnd/Tilt_192000_Left.bin",
    "soundid-export-txt": TESTDATA / "soundid/soundid-export-txt/Tilt - Flat.txt",
    "tmreq": TESTDATA / "rme/tmreq/Tilt - Flat.tmreq",
}

# A second format sharing ``.txt`` with ``rewcal``.
OTHER_TXT = Format("othertxt", (".txt",), "another text format", Inspector)


def with_other_txt():
    return mock.patch.dict(formats.FORMATS, {OTHER_TXT.id: OTHER_TXT})


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue()


def run_error(*argv):
    err = io.StringIO()
    with contextlib.redirect_stderr(err), unittest.TestCase().assertRaises(SystemExit) as ctx:
        run(*argv)
    return ctx.exception.code, err.getvalue()


class FormatTests(unittest.TestCase):
    def test_detect(self):
        for name, want in (("a.swproj", "swproj"), ("A.SWHP", "peqb"), ("a.eqb", "peqb"),
                           ("a.mdat", "mdat"), ("a.cal", "rewcal"), ("a.CSV", "autoeq"),
                           ("a.adam", "soundid-export-biquad-xml"), ("a.tmreq", "tmreq")):
            self.assertEqual(formats.detect(Path(name)), want)
        with self.assertRaises(ValueError):
            formats.detect(Path("a.dat"))

    def test_detect_shared_extension(self):
        with with_other_txt():
            with self.assertRaisesRegex(ValueError,
                                        "ambiguous.*rewcal, soundid-export-txt, othertxt"):
                formats.detect(Path("a.txt"))
            self.assertEqual(formats.detect(Path("a.txt"), ("rewcal", "swproj")), "rewcal")
            self.assertEqual(formats.detect(Path("a.cal")), "rewcal")

    def test_every_converter_has_formats(self):
        for source, target in convert.CONVERTERS:
            self.assertIn(source, formats.FORMATS)
            self.assertIn(target, formats.FORMATS)

    def test_find_converter(self):
        self.assertIs(convert.find("swmicpkg"), convert.CONVERTERS["swmicpkg", "rewcal"])
        with self.assertRaisesRegex(ValueError, "2 converters from mdat; specify the target"):
            convert.find("mdat")
        with self.assertRaisesRegex(ValueError, "no converter from mdat to rewcal"):
            convert.find("mdat", "rewcal")


class InspectTests(unittest.TestCase):
    def test_every_format(self):
        for kind, path in SAMPLES.items():
            with self.subTest(kind=kind):
                code, out = run("inspect", path)
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith(f"== file\n  name"))

    def test_preview_and_full(self):
        _, preview = run("inspect", PACKAGE)
        _, full = run("inspect", PACKAGE, "--full")
        self.assertIn("(295 of 300 rows hidden; --full shows all)", preview)
        self.assertNotIn("hidden", full)
        self.assertGreater(len(full.splitlines()), 900)

    def test_raw_in_full_only(self):
        _, preview = run("inspect", SAMPLES["swproj"])
        _, full = run("inspect", SAMPLES["swproj"], "--full")
        self.assertNotIn("-- raw", preview)
        self.assertIn("<ProjectHeader", full)

    def test_format_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "package.bin"
            path.write_bytes(PACKAGE.read_bytes())
            code, out = run("inspect", path, "--format", "swmicpkg")
            self.assertEqual(code, 0)
            self.assertIn("== table degrees_90", out)

    def test_option_of_other_format_is_rejected(self):
        code, err = run_error("inspect", PACKAGE, "--password", "x")
        self.assertEqual(code, 1)
        self.assertIn("--password does not apply to swmicpkg", err)

    def test_swproj_xml(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "p.xml"
            run("inspect", SAMPLES["swproj"], "--xml", out)
            self.assertEqual(out.read_bytes(), swproj.SwProj.open(SAMPLES["swproj"]).xml)


class ConvertTests(unittest.TestCase):
    def test_swmicpkg_to_rewcal_default_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / PACKAGE.name
            src.write_bytes(PACKAGE.read_bytes())
            code, _ = run("convert", src, "--angle", "degrees_90")
            self.assertEqual(code, 0)
            profile, other = cal.load(Path(tmp) / "TILT01 degrees_90.txt")
            self.assertEqual(profile.points, swmicpkg.load(PACKAGE, "degrees_90").points)
            self.assertEqual(other, ["* SoundID microphone TILT01 degrees_90"])
            _, out = run("inspect", Path(tmp) / "TILT01 degrees_90.txt")
            self.assertIn("  points : 300", out)

    def test_mdat_to_swproj(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.swproj"
            code, text = run("convert", MDAT, "-o", out, "--mic-profile", PACKAGE,
                             "--max-boost-db", "6")
            self.assertEqual(code, 0)
            self.assertIn("maximum boost: 6 dB", text)
            self.assertEqual(len(swproj.SwProj.open(out).eqb.curves), 4)

    def test_target_from_output_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, err = run_error("convert", MDAT, "-o", Path(tmp) / "x.cal")
        self.assertIn("no converter from mdat to rewcal", err)

    def test_shared_output_extension(self):
        with tempfile.TemporaryDirectory() as tmp, with_other_txt():
            out = Path(tmp) / "x.txt"
            code, _ = run("convert", PACKAGE, "-o", out)
            self.assertEqual(code, 0)
            self.assertEqual(cal.load(out)[0].points, swmicpkg.load(PACKAGE).points)

    def test_shared_input_extension(self):
        with with_other_txt():
            code, err = run_error("inspect", SAMPLES["rewcal"].with_suffix(".txt"))
        self.assertIn("ambiguous", err)

    def test_mic_profile_format_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "mic.dat"
            profile.write_bytes(PACKAGE.read_bytes())
            out = Path(tmp) / "x.swproj"
            code, err = run_error("convert", MDAT, "-o", out, "--mic-profile", profile)
            self.assertIn("cannot detect the format of 'mic.dat'", err)
            code, text = run("convert", MDAT, "-o", out, "--mic-profile", profile,
                             "--mic-profile-format", "swmicpkg")
            self.assertEqual(code, 0)
            self.assertIn("mic table: mic degrees_0", text)

    def test_option_of_other_pair_is_rejected(self):
        code, err = run_error("convert", PACKAGE, "--mic-angle", "degrees_0")
        self.assertIn("--mic-angle does not apply to swmicpkg -> rewcal", err)

    def test_autoeq(self):
        csv = SAMPLES["autoeq"]
        code, err = run_error("convert", csv)
        self.assertIn("3 converters from autoeq", err)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run("convert", csv, "-o", Path(tmp) / "x.swhp", "--make", "M")
            self.assertEqual(code, 0)
            self.assertIn("M Bass and treble; 355 points", text)
            code, _ = run("convert", SAMPLES["peqb"], "-o", Path(tmp) / "y.csv",
                          "--computer-id", "g" + "0" * 40, "--channel", "right")
            self.assertEqual(code, 0)
            _, out = run("inspect", Path(tmp) / "y.csv")
            self.assertIn("  points  : 355", out)

    def test_device_export(self):
        merging = TESTDATA / "soundid/soundid-export-biquad-json/Tilt MERGING.bin"
        _, out = run("inspect", merging, "--serial-number", "000042", "--full")
        self.assertIn("MERGING+ANUBIS key, serial number A000042", out)
        self.assertIn('"channel_config"', out)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run("convert", merging, "-o", Path(tmp) / "x.csv", "--sample-rate",
                             "44100", "--channel", "right")
            self.assertEqual(code, 0)
            self.assertIn("Right correction at 44100 Hz, dB", text)
            code, err = run_error("convert", SAMPLES["soundid-export-peq-json"], "-o",
                                  Path(tmp) / "x.csv",
                                  "--sample-rate", "48000")
            self.assertIn("--sample-rate does not apply to soundid-export-peq-json -> autoeq", err)

    def test_required_option(self):
        code, err = run_error("convert", MDAT, "--to", "swproj")
        self.assertIn("mdat -> swproj needs --mic-profile", err)


if __name__ == "__main__":
    unittest.main()
