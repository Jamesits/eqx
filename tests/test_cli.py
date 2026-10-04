import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from testgen import common

from eqx import cli, convert, formats, graph
from eqx.fileformat import Format, Inspector
from eqx.report import Table
from eqx.rew import cal
from eqx.soundid import swmicpkg, swproj

TESTDATA = common.ROOT
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
    "soundid-export-biquad-json": TESTDATA
    / "soundid/soundid-export-biquad-json/Tilt Fluid.bin",
    "soundid-export-peq-json": TESTDATA
    / "soundid/soundid-export-peq-json/Tilt Grace.bin",
    "soundid-export-biquad-xml": TESTDATA
    / "soundid/soundid-export-biquad-xml/Tilt.adam",
    "soundid-export-lvnd": TESTDATA
    / "soundid/soundid-export-lvnd/Tilt_192000_Left.bin",
    "soundid-export-txt": TESTDATA / "soundid/soundid-export-txt/Tilt - Flat.txt",
    "tmreq": TESTDATA / "rme/tmreq/Tilt - Flat.tmreq",
    "soundsource": TESTDATA / "rogueamoeba/soundsource/Tilt - Flat.txt",
    "arcx": TESTDATA / "ik/arcx/Arc.arcXs",
    "fir": TESTDATA / "fir/wav/Tilt Tilt Wired Average.wav",
    "targetcurve": TESTDATA / "dirac/targetcurve/Tilt.targetcurve",
    "dirac-filter": TESTDATA / "dirac/dirac-filter/FIIR signed.bin",
    "mqx": TESTDATA / "audyssey/mqx/Mqx 5.1.mqx",
    "fuzzmeasure": TESTDATA / "rode/fuzzmeasure/Fm4.fume4",
    "smaart-trf": TESTDATA / "rationalacoustics/smaart-trace/Tf Left.trf",
    "smaart-srf": TESTDATA / "rationalacoustics/smaart-trace/Rta.srf",
    "smaart-ref": TESTDATA / "rationalacoustics/smaart-trace/Ref Left.ref",
    "smaart-ascii": TESTDATA / "rationalacoustics/smaart-ascii/Tf export.txt",
    "smaart-curve": TESTDATA / "rationalacoustics/smaart-curve/Haystack.crv",
    "dearvr-hpc": TESTDATA / "sennheiser/dearvr-hpc/hpc.dat",
    "drc": TESTDATA / "drc/pcm/Room Left filter.pcm",
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
    with (
        contextlib.redirect_stderr(err),
        unittest.TestCase().assertRaises(SystemExit) as ctx,
    ):
        run(*argv)
    return ctx.exception.code, err.getvalue()


def one_of(err):
    """The values of an error's ``(one of: ...)`` list."""
    return set(re.search(r"\(one of: ([^)]*)\)", err)[1].split(", "))


class FormatTests(unittest.TestCase):
    def test_detect(self):
        for name, want in (
            ("a.swproj", "swproj"),
            ("A.SWHP", "peqb"),
            ("a.eqb", "peqb"),
            ("a.mdat", "mdat"),
            ("a.cal", "rewcal"),
            ("a.CSV", "autoeq"),
            ("a.adam", "soundid-export-biquad-xml"),
            ("a.tmreq", "tmreq"),
            ("a.arcXs", "arcx"),
            ("a.arcXa", "arcx"),
            ("a.WAV", "fir"),
            ("a.MQX", "mqx"),
            ("a.fume4", "fuzzmeasure"),
            ("a.Fume3", "fuzzmeasure"),
            ("a.fume", "fuzzmeasure"),
            ("a.trf", "smaart-trf"),
            ("a.SRF", "smaart-srf"),
            ("a.ref", "smaart-ref"),
            ("a.crv", "smaart-curve"),
            ("hpc.DAT", "dearvr-hpc"),
        ):
            self.assertEqual(formats.detect(Path(name)), want)
        with self.assertRaises(ValueError):
            formats.detect(Path("a.xyz"))

    def test_detect_shared_extension(self):
        with with_other_txt():
            with self.assertRaisesRegex(
                ValueError,
                "ambiguous.*rewcal, soundid-export-txt, soundsource, othertxt",
            ):
                formats.detect(Path("a.txt"))
            self.assertEqual(
                formats.detect(Path("a.txt"), ("rewcal", "swproj")), "rewcal"
            )
            self.assertEqual(formats.detect(Path("a.cal")), "rewcal")

    def test_every_converter_has_formats(self):
        for source, target in convert.CONVERTERS:
            self.assertIn(source, formats.FORMATS)
            self.assertIn(target, formats.FORMATS)

    def test_find_converter(self):
        self.assertIs(
            convert.find("swmicpkg"), convert.CONVERTERS["swmicpkg", "rewcal"]
        )
        with self.assertRaisesRegex(
            ValueError, "3 converters from mdat; specify the target"
        ):
            convert.find("mdat")
        with self.assertRaisesRegex(ValueError, "no converter from mdat to rewcal"):
            convert.find("mdat", "rewcal")


class InspectTests(unittest.TestCase):
    def test_every_format(self):
        for kind, path in SAMPLES.items():
            with self.subTest(kind=kind):
                code, out = run("inspect", path)
                self.assertEqual(code, 0)
                self.assertTrue(out.startswith("== file\n  name"))

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

    def test_graph(self):
        _, auto = run("inspect", PACKAGE)
        _, off = run("inspect", PACKAGE, "--graph", "off")
        _, on = run("inspect", PACKAGE, "--graph", "on")
        self.assertNotIn("-- graph", auto)
        self.assertEqual(auto, off)
        self.assertEqual(on.count("-- graph: gain dB"), 3)
        self.assertIn("1k", on)
        self.assertNotIn("\x1b[", on)

    def test_inspector_option(self):
        _, out = run(
            "inspect",
            SAMPLES["dearvr-hpc"],
            "--headphone",
            "Tilt Studio",
            "--graph",
            "on",
        )
        self.assertIn("== headphone Tilt Studio", out)
        self.assertIn("-- graph: linear dB", out)

    def test_graph_of_filters(self):
        # A filter table is drawn as its response; a biquad table too.
        for kind in ("tmreq", "soundid-export-biquad-json"):
            with self.subTest(kind=kind):
                _, out = run("inspect", SAMPLES[kind], "--graph", "on")
                self.assertIn("-- graph: gain dB", out)

    def test_graph_curves(self):
        table = Table(
            ["frequency Hz", "dB", "group delay s"],
            [
                (0.0, 1.0, 0.0),
                (20.0, 2.0, 0.0),
                (None, None, 0.0),
                (100.0, float("nan"), 0.0),
                (1000.0, 3.0, 0.0),
            ],
        )
        self.assertEqual(graph.curves(table), [("dB", [(20.0, 2.0), (1000.0, 3.0)])])
        self.assertEqual(
            graph.curves(
                Table(["type", "frequency Hz", "gain dB"], [("bell", 100.0, 1.0)] * 2)
            ),
            [],
        )
        self.assertEqual(
            graph.curves(Table(["frequency Hz", "name"], [(1.0, "a")] * 2)), []
        )

    def test_graph_flat(self):
        text = graph.plot([(20.0, 0.0), (20000.0, 0.0)], 60, 10)
        self.assertIn("1.0", text)
        self.assertIn("-1.0", text)

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
            self.assertEqual(
                out.read_bytes(), swproj.SwProj.open(SAMPLES["swproj"]).xml
            )


class ConvertTests(unittest.TestCase):
    def test_swmicpkg_to_rewcal_default_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / PACKAGE.name
            src.write_bytes(PACKAGE.read_bytes())
            code, _ = run(
                "convert", "-i", src, "--to", "rewcal", "--curve", "degrees_90"
            )
            self.assertEqual(code, 0)
            profile, other = cal.load(Path(tmp) / "TILT01 degrees_90.txt")
            self.assertEqual(
                profile.points, swmicpkg.load(PACKAGE, "degrees_90").points
            )
            self.assertEqual(other, ["* SoundID microphone TILT01 degrees_90"])
            _, out = run("inspect", Path(tmp) / "TILT01 degrees_90.txt")
            self.assertIn("  points : 300", out)

    def test_mdat_to_swproj(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.swproj"
            code, text = run(
                "convert",
                "-i",
                MDAT,
                "-o",
                out,
                "--mic-profile",
                PACKAGE,
                "--max-boost-db",
                "6",
            )
            self.assertEqual(code, 0)
            self.assertIn("maximum boost: 6 dB", text)
            self.assertEqual(len(swproj.SwProj.open(out).eqb.curves), 4)

    def test_target_from_output_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            _code, err = run_error("convert", "-i", MDAT, "-o", Path(tmp) / "x.cal")
        self.assertIn("no converter from mdat to rewcal", err)

    def test_shared_output_extension(self):
        with tempfile.TemporaryDirectory() as tmp, with_other_txt():
            out = Path(tmp) / "x.txt"
            code, _ = run("convert", "-i", PACKAGE, "-o", out)
            self.assertEqual(code, 0)
            self.assertEqual(cal.load(out)[0].points, swmicpkg.load(PACKAGE).points)

    def test_shared_input_extension(self):
        with with_other_txt():
            _code, err = run_error("inspect", SAMPLES["rewcal"].with_suffix(".txt"))
        self.assertIn("ambiguous", err)

    def test_mic_profile_format_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "mic.xyz"
            profile.write_bytes(PACKAGE.read_bytes())
            out = Path(tmp) / "x.swproj"
            code, err = run_error(
                "convert", "-i", MDAT, "-o", out, "--mic-profile", profile
            )
            self.assertIn("cannot detect the format of 'mic.xyz'", err)
            code, text = run(
                "convert",
                "-i",
                MDAT,
                "-o",
                out,
                "--mic-profile",
                profile,
                "--mic-profile-format",
                "swmicpkg",
            )
            self.assertEqual(code, 0)
            self.assertIn("mic table: mic degrees_0", text)

    def test_option_of_other_pair_is_rejected(self):
        _code, err = run_error(
            "convert", "-i", PACKAGE, "--to", "rewcal", "--mic-curve", "degrees_0"
        )
        self.assertIn("--mic-curve does not apply to swmicpkg -> rewcal", err)

    def test_autoeq(self):
        csv = SAMPLES["autoeq"]
        code, err = run_error("convert", "-i", csv)
        self.assertIn("17 converters from autoeq", err)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert", "-i", csv, "-o", Path(tmp) / "x.swhp", "--make", "M"
            )
            self.assertEqual(code, 0)
            self.assertIn("M Bass and treble; 355 points", text)
            code, _ = run(
                "convert",
                "-i",
                SAMPLES["peqb"],
                "-o",
                Path(tmp) / "y.csv",
                "--computer-id",
                "g" + "0" * 40,
                "--channel",
                "right",
            )
            self.assertEqual(code, 0)
            _, out = run("inspect", Path(tmp) / "y.csv")
            self.assertIn("  points  : 355", out)

    def test_writers(self):
        csv = SAMPLES["autoeq"]
        with tempfile.TemporaryDirectory() as tmp:
            for name, want in (
                ("x.mdat", "mdat"),
                ("x.tmreq", "tmreq"),
                ("x.arcXa", "arcx"),
            ):
                with self.subTest(name):
                    code, _text = run("convert", "-i", csv, "-o", Path(tmp) / name)
                    self.assertEqual(code, 0)
                    self.assertEqual(formats.detect(Path(tmp) / name), want)
                    self.assertEqual(run("inspect", Path(tmp) / name)[0], 0)
            # .txt is rewcal or soundsource; --to picks one, an existing file its content.
            out = Path(tmp) / "x.txt"
            code, err = run_error("convert", "-i", csv, "-o", out)
            self.assertIn("specify --to", err)
            self.assertTrue({"rewcal", "soundsource"} <= one_of(err))
            self.assertEqual(
                run("convert", "-i", csv, "-o", out, "--to", "soundsource")[0], 0
            )
            self.assertEqual(formats.detect(out), "soundsource")
            self.assertEqual(run("convert", "-i", csv, "-o", out)[0], 0)
            self.assertEqual(formats.detect(out), "soundsource")
            # --to is required without -o, even for the only conversion of rewcal.
            table = Path(tmp) / "t.cal"
            table.write_bytes(SAMPLES["rewcal"].read_bytes())
            code, err = run_error("convert", "-i", table, "--serial", "S1")
            self.assertIn("1 converter from rewcal; specify --to", err)
            # The default name is the serial.
            code, _text = run(
                "convert", "-i", table, "--to", "swmicpkg", "--serial", "S1"
            )
            self.assertEqual(code, 0)
            self.assertEqual(swmicpkg.load(Path(tmp) / "S1.swmicpkg").name, "S1")

    def test_device_export(self):
        merging = TESTDATA / "soundid/soundid-export-biquad-json/Tilt MERGING.bin"
        _, out = run("inspect", merging, "--serial-number", "000042", "--full")
        self.assertIn("MERGING+ANUBIS key, serial number A000042", out)
        self.assertIn('"channel_config"', out)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert",
                "-i",
                merging,
                "-o",
                Path(tmp) / "x.csv",
                "--sample-rate",
                "44100",
                "--channel",
                "right",
            )
            self.assertEqual(code, 0)
            self.assertIn("Right correction at 44100 Hz, dB", text)
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["soundid-export-peq-json"],
                "-o",
                Path(tmp) / "x.csv",
                "--sample-rate",
                "48000",
            )
            self.assertIn(
                "--sample-rate does not apply to soundid-export-peq-json -> autoeq", err
            )

    def test_arcx(self):
        code, err = run_error("convert", "-i", SAMPLES["arcx"])
        self.assertIn("5 converters from arcx", err)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert",
                "-i",
                SAMPLES["arcx"],
                "-o",
                Path(tmp) / "x.csv",
                "--speaker",
                "right",
                "--point",
                "1",
            )
            self.assertEqual(code, 0)
            self.assertIn("Right response, point 1", text)
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["arcx"],
                "-o",
                Path(tmp) / "x.csv",
                "--speaker",
                "Center",
            )
            self.assertIn("no Center speaker; available: Left, Right", err)
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["arcx"],
                "-o",
                Path(tmp) / "x.csv",
                "--point",
                "3",
            )
            self.assertIn("point 3 does not exist; points: 0-2", err)
            code, text = run(
                "convert",
                "-i",
                SAMPLES["arcx"],
                "-o",
                Path(tmp) / "x.swproj",
                "--mic-profile",
                PACKAGE,
                "--point",
                "0",
            )
            self.assertEqual(code, 0)
            self.assertIn("measurements: 2", text)
            surround = TESTDATA / "ik/arcx/Arc 5.1.arcXs"
            code, text = run(
                "convert",
                "-i",
                surround,
                "-o",
                Path(tmp) / "x.swproj",
                "--mic-profile",
                PACKAGE,
                "--lfe-high-cutoff-hz",
                "100",
            )
            self.assertEqual(code, 0)
            self.assertIn("layout: 5.1; measurements: 6", text)
            self.assertIn("LFE correction band: 60-100 Hz", text)
            code, text = run(
                "convert",
                "-i",
                Path(tmp) / "x.swproj",
                "-o",
                Path(tmp) / "x.csv",
                "--speaker",
                "left surround",
            )
            self.assertEqual(code, 0)
            self.assertIn("Left Surround measurement", text)
            code, err = run_error(
                "convert",
                "-i",
                Path(tmp) / "x.swproj",
                "-o",
                Path(tmp) / "x.csv",
                "--speaker",
                "Left Wide",
            )
            self.assertIn("no Left Wide channel; available: Left, Right, Center", err)

    def test_mqx(self):
        code, err = run_error("convert", "-i", SAMPLES["mqx"])
        self.assertIn("5 converters from mqx", err)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert",
                "-i",
                SAMPLES["mqx"],
                "-o",
                Path(tmp) / "x.csv",
                "--speaker",
                "center",
                "--position",
                "2",
            )
            self.assertEqual(code, 0)
            self.assertIn(
                "C response, position 2, dB re full scale, minus the generic ACM1HB",
                text,
            )
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["mqx"],
                "-o",
                Path(tmp) / "x.csv",
                "--point",
                "1",
            )
            self.assertIn("--point does not apply to mqx -> autoeq", err)
            code, text = run(
                "convert",
                "-i",
                SAMPLES["mqx"],
                "-o",
                Path(tmp) / "x.swproj",
                "--mic-profile",
                PACKAGE,
            )
            self.assertEqual(code, 0)
            self.assertIn("layout: 5.1; measurements: 6", text)
            code, text = run(
                "convert",
                "-i",
                TESTDATA / "autoeq/csv/Room Left.csv",
                "-o",
                Path(tmp) / "x.mqx",
            )
            self.assertEqual(code, 0)
            self.assertIn("FL, FR, 1 position", text)

    def test_fuzzmeasure(self):
        for name in ("Fm4.fume4", "Fm3.fume3", "Fm2.fume"):
            with self.subTest(name):
                code, text = run("inspect", TESTDATA / "rode/fuzzmeasure" / name)
                self.assertEqual(code, 0)
                self.assertIn("== measurement 1 Right", text)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert",
                "-i",
                SAMPLES["fuzzmeasure"],
                "-o",
                Path(tmp) / "x.csv",
                "--measurement",
                "right",
                "--no-mic-calibration",
                "--spl",
            )
            self.assertEqual(code, 0)
            self.assertIn(
                "'Right', dB SPL, microphone calibration 'TILT01 mic' not applied", text
            )
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["fuzzmeasure"],
                "--to",
                "autoeq",
                "--measurement",
                "9",
            )
            self.assertIn("measurement 9 does not exist", err)
            inputs = [
                TESTDATA / "autoeq/csv" / f"Room {side}.csv"
                for side in ("Left", "Right")
            ]
            output = Path(tmp) / "x.fume4"
            for _ in range(2):
                code, text = run(
                    "convert", "-i", inputs[0], "-i", inputs[1], "-o", output
                )
                self.assertEqual(code, 0)
            self.assertIn("2 measurements, 48000 Hz", text)
            self.assertEqual(len(list(output.iterdir())), 3)
            code, text = run("inspect", output)
            self.assertIn("== measurement 1 Room Right", text)
            (Path(tmp) / "y.fume4").write_bytes(b"")
            code, err = run_error(
                "convert", "-i", inputs[0], "-o", Path(tmp) / "y.fume4"
            )
            self.assertIn("exists and is not a directory", err)

    def test_smaart(self):
        code, text = run("inspect", TESTDATA / "rationalacoustics/smaart-trace/Rta.srf")
        self.assertEqual(code, 0)
        self.assertIn("calibration offset dB  : 100", text)
        csv = SAMPLES["autoeq"]
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert",
                "-i",
                SAMPLES["smaart-trf"],
                "-o",
                Path(tmp) / "x.csv",
                "--mtw",
            )
            self.assertEqual(code, 0)
            self.assertIn("MTW data", text)
            code, text = run(
                "convert",
                "-i",
                SAMPLES["smaart-ascii"],
                "-o",
                Path(tmp) / "y.csv",
                "--trace",
                "1",
            )
            self.assertIn("trace 'Right'", text)
            code, text = run(
                "convert", "-i", csv, "-o", Path(tmp) / "x.trf", "--fft", "1024"
            )
            self.assertIn("513 bins, FFT 1024 at 48000 Hz, minimum phase", text)
            # The output extension selects the trace kind.
            code, text = run(
                "convert",
                "-i",
                csv,
                "-o",
                Path(tmp) / "x.srf",
                "--fft",
                "1024",
                "--calibration-db",
                "90",
            )
            self.assertIn("calibration offset 90 dB", text)
            code, text = run(
                "convert",
                "-i",
                Path(tmp) / "x.srf",
                "-o",
                Path(tmp) / "s.csv",
                "--calibrated",
            )
            self.assertIn("dB + calibration offset 90 dB", text)
            code, err = run_error(
                "convert", "-i", Path(tmp) / "x.srf", "--to", "autoeq", "--mtw"
            )
            self.assertIn("--mtw", err)
            code, text = run(
                "convert", "-i", SAMPLES["smaart-ref"], "-o", Path(tmp) / "r.csv"
            )
            self.assertEqual(code, 0)
            code, text = run(
                "convert", "-i", csv, "-o", Path(tmp) / "x.crv", "--band", "3"
            )
            self.assertIn("spectrum, 1/3 octave target curve", text)
            code, err = run_error("convert", "-i", csv, "-o", Path(tmp) / "x.txt")
            self.assertIn("specify --to", err)
            self.assertTrue({"rewcal", "soundsource"} <= one_of(err))
            code, text = run(
                "convert", "-i", csv, "-o", Path(tmp) / "x.txt", "--to", "smaart-ascii"
            )
            self.assertEqual(code, 0)
            # The existing output is read as a Smaart table.
            code, text = run(
                "convert", "-i", Path(tmp) / "x.txt", "-o", Path(tmp) / "z.csv"
            )
            self.assertIn("column Magnitude (dB)", text)

    def test_fir(self):
        code, err = run_error("convert", "-i", SAMPLES["peqb"])
        self.assertIn("2 converters from peqb", err)
        _, out = run("inspect", SAMPLES["fir"])
        self.assertIn("  taps           : 4093", out)
        self.assertIn("== channel Right", out)
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert",
                "-i",
                SAMPLES["peqb"],
                "-o",
                Path(tmp) / "x.wav",
                "--computer-id",
                "g" + "0" * 40,
                "--phase",
                "linear",
                "--rate",
                "44100",
                "--no-safe-headroom",
            )
            self.assertEqual(code, 0)
            self.assertIn(
                "2 channel(s), 4001 taps at 44100 Hz, linear phase, latency 2000", text
            )
            self.assertIn("gain 0.00 dB", text)
            code, text = run(
                "convert",
                "-i",
                SAMPLES["autoeq"],
                "-o",
                Path(tmp) / "y.wav",
                "--taps",
                "1024",
            )
            self.assertEqual(code, 0)
            self.assertIn("1 channel(s), 1024 taps at 48000 Hz, minimum phase", text)
            code, text = run(
                "convert", "-i", Path(tmp) / "y.wav", "-o", Path(tmp) / "y.csv"
            )
            self.assertEqual(code, 0)
            self.assertIn("Left gain of 1024 taps at 48000 Hz, dB", text)
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["autoeq"],
                "-o",
                Path(tmp) / "y.wav",
                "--phase",
                "linear",
                "--taps",
                "1024",
            )
            self.assertIn("odd for linear phase", err)
            code, err = run_error(
                "convert",
                "-i",
                SAMPLES["autoeq"],
                "-o",
                Path(tmp) / "y.wav",
                "--grid-factor",
                "0",
            )
            self.assertIn("grid-factor", err)
            code, text = run(
                "convert",
                "-i",
                SAMPLES["autoeq"],
                "-o",
                Path(tmp) / "y.wav",
                "--phase",
                "linear",
                "--rate",
                "96000",
                "--nyquist-notch",
                "--grid-factor",
                "1",
            )
            self.assertEqual(code, 0)
            self.assertIn("8707 taps at 96000 Hz, linear phase, latency 4353", text)
            code, text = run(
                "convert",
                "-i",
                SAMPLES["swproj"],
                "-o",
                Path(tmp) / "z.wav",
                "--limit-correction",
                "6",
                "--limit-low",
                "extended",
                "--no-listening-spot",
            )
            self.assertEqual(code, 0)
            self.assertIn("limits: 6 dB, low extended, high neutral", text)

    def test_multiple_inputs(self):
        left, right = (
            TESTDATA / "autoeq/csv/Room Left.csv",
            TESTDATA / "autoeq/csv/Room Right.csv",
        )
        with tempfile.TemporaryDirectory() as tmp:
            code, text = run(
                "convert", "-i", left, "-i", right, "-o", Path(tmp) / "x.wav"
            )
            self.assertEqual(code, 0)
            self.assertIn("2 channel(s)", text)
        code, err = run_error(
            "convert", "-i", left, "-i", right, "-i", left, "--to", "fir"
        )
        self.assertIn("autoeq -> fir takes 1 to 2 input files, got 3", err)
        code, err = run_error("convert", "-i", PACKAGE, "-i", PACKAGE, "--to", "rewcal")
        self.assertIn("swmicpkg -> rewcal takes 1 input file, got 2", err)
        code, err = run_error("convert", "-i", left, "-i", MDAT, "--to", "fir")
        self.assertIn("inputs of different formats: autoeq, mdat", err)

    def test_help(self):
        code, text = run("convert", "--help")
        self.assertEqual(code, 0)
        self.assertIn("mdat -> swproj: ", text)
        self.assertIn("swmicpkg -> rewcal: ", text)
        self.assertNotIn("--mic-profile", text)

        # Both formats known: the options of the pair only.
        _code, text = run("convert", "-i", MDAT, "-o", "x.swproj", "-h")
        self.assertIn("mdat -> swproj options:", text)
        self.assertIn("--mic-profile", text)
        self.assertNotIn("--column", text)
        self.assertNotIn("conversions:", text)
        _code, text = run("convert", "--from", "autoeq", "--to", "peqb", "-h")
        self.assertIn("autoeq -> peqb options:", text)
        self.assertNotIn("--mic-profile", text)

        # The output format not determined: the targets of the input.
        _code, text = run("convert", "-i", MDAT, "-h")
        to_values = text[text.index("--to values:") :]
        self.assertIn("  swproj ", to_values)
        self.assertIn("  autoeq ", to_values)
        self.assertNotIn("--from values", text)
        self.assertNotIn("--mic-profile", text)

        # A shared extension: the sources with it; no extension: all sources.
        _code, text = run("convert", "-i", "x.txt", "-h")
        from_values = text[text.index("--from values for 'x.txt':") :]
        self.assertIn("  rewcal ", from_values)
        self.assertIn("  soundsource ", from_values)
        self.assertNotIn("  mdat ", from_values)
        _code, text = run("convert", "-i", "x", "--to", "fir", "-h")
        from_values = text[text.index("--from values for 'x':") :]
        self.assertIn("  autoeq ", from_values)
        self.assertIn("  peqb ", from_values)
        self.assertNotIn("  rewcal ", from_values)
        self.assertNotIn("--to values", text)
        _code, text = run("convert", "-i", MDAT, "-o", "x", "-h")
        self.assertIn("--to values for 'x':", text)

    def test_undetected_format(self):
        _code, err = run_error("convert", "-i", "x.txt")
        self.assertIn("cannot detect the format of 'x.txt'; specify --from", err)
        values = one_of(err)
        self.assertTrue({"rewcal", "soundsource"} <= values)
        self.assertNotIn("mdat", values)
        _code, err = run_error("convert", "-i", MDAT, "-o", "x")
        self.assertIn("cannot detect the format of 'x'; specify --to", err)
        values = one_of(err)
        self.assertTrue({"swproj", "autoeq"} <= values)
        self.assertNotIn("rewcal", values)
        _code, err = run_error("convert", "--to", "fir")
        self.assertIn("-i/--input is required", err)

    def test_default_mic_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.swproj"
            code, text = run("convert", "-i", MDAT, "-o", out)
        self.assertEqual(code, 0)
        self.assertIn("mic table: Flat degrees_0 (default)", text)
        _code, err = run_error(
            "convert", "-i", MDAT, "--to", "swproj", "--mic-curve", "degrees_30"
        )
        self.assertIn("--mic-profile-format and --mic-curve need --mic-profile", err)


if __name__ == "__main__":
    unittest.main()
