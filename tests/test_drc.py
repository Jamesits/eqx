import os
import shutil
import statistics
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from testgen import common, drc, ik, rew, soundid

from eqx import cli, dsp, formats
from eqx.convert import CONVERTERS
from eqx.convert.common import drc_input
from eqx.convert.to_drc import MdatToDrc
from eqx.convert.to_swproj import (
    DRC_OPTIONS,
    Arc4ToSwproj,
    ArcxToSwproj,
    AutoeqToSwproj,
    DrcToSwproj,
    MdatToSwproj,
    MqxToSwproj,
    load_mic_profile,
)
from eqx.curve import log_resample
from eqx.drc import pcm, run
from eqx.model import standard_grid
from eqx.rew import mdat
from eqx.soundid import speakerproject, swproj

ROOT = common.ROOT
ROOM = ROOT / rew.MDAT_DIR / "Room.mdat"
LEFT_ONLY = ROOT / rew.MDAT_DIR / "Left only.mdat"
FILTERS = [ROOT / drc.PCM_DIR / f"Room {side} filter.pcm" for side in ("Left", "Right")]
TILT = ROOT / soundid.MIC_DIR / "TILT01.swmicpkg"
# Every speaker project conversion that designs a correction, with its inputs.
SPEAKER_PROJECTS = [
    (MdatToSwproj, [ROOM]),
    (
        AutoeqToSwproj,
        [ROOT / common.CSV_DIR / f"Room {side}.csv" for side in ("Left", "Right")],
    ),
    (Arc4ToSwproj, [ROOT / ik.ARC4_DIR / "Arc4.arc4a"]),
    (ArcxToSwproj, [ROOT / ik.ARCX_DIR / "Arc 5.1.arcXs"]),
    (MqxToSwproj, [ROOT / "audyssey" / "mqx" / "Mqx 5.1.mqx"]),
]
REAL = ROOT / "real" / "drc"
REAL_DRC = shutil.which(str(REAL / "drc")) or shutil.which("drc")
# Every file in one directory; the DRC distribution layout (the Debian
# package, extracted or installed).
REAL_CONFIGS = [
    c
    for c in (
        REAL / "cfg48" / "normal-48.0.drc",
        *(
            root / "config" / "48.0 kHz" / "normal-48.0.drc"
            for root in (
                REAL / "deb" / "data" / "usr" / "share" / "drc",
                Path("/usr/share/drc"),
            )
        ),
    )
    if c.is_file()
]
# Set in CI where DRC is installed: fail instead of skipping.
REQUIRE_DRC = os.environ.get("EQX_REQUIRE_DRC") == "1"


def fake_run(calls: list, ir: list[float]):
    """A ``run.run`` that records its calls and gives ``ir``."""

    def fake(samples, config, program):
        calls.append((samples, config, program))
        return ir, ""

    return fake


def bells_db(side: str, frequencies) -> list[float]:
    biquads = common.bells(side, drc.RATE)
    return [dsp.cascade_db(biquads, f, drc.RATE) for f in frequencies]


class PcmTests(unittest.TestCase):
    def test_round_trip(self):
        samples = [0.0, 1.0, -0.5, 0.25]
        data = pcm.write(samples)
        self.assertEqual(data, struct.pack("<4f", *samples))
        self.assertEqual(pcm.read(data), samples)

    def test_rejected(self):
        for data in (b"", b"\0\0\0", struct.pack("<f", float("nan"))):
            with self.subTest(data=data), self.assertRaises(ValueError):
                pcm.read(data)

    def test_detected(self):
        self.assertEqual(formats.detect(FILTERS[0]), "drc")

    def test_response(self):
        # The gain of the bells less 6 dB; the delay before the peak is left out.
        samples = pcm.load(FILTERS[0])
        band = [f for f in standard_grid() if 100 <= f <= 15000]
        db, gd = pcm.response(samples, drc.RATE, band)
        for f, got, want in zip(band, db, bells_db("Left", band)):
            self.assertAlmostEqual(got, want + drc.GAIN_DB, delta=0.3, msg=f)
        self.assertLess(max(abs(v) for v in gd), 0.005)
        with self.assertRaises(ValueError):
            pcm.response(samples, 44000, standard_grid())

    def test_inspect(self):
        sections = pcm.PcmInspector(rate=drc.RATE).inspect(FILTERS[0])
        fields = dict(sections[1].fields)
        self.assertEqual(fields["samples"], drc.TAPS)
        self.assertEqual(fields["peak sample"], drc.DELAY)
        self.assertEqual(
            sections[1].table.columns, ["frequency Hz", "dB", "group delay s"]
        )
        self.assertEqual(cli.main(["inspect", str(FILTERS[0]), "--rate", "44100"]), 0)


class RunTests(unittest.TestCase):
    def test_input_taps(self):
        self.assertEqual(run.input_taps(44100), 65536)
        self.assertEqual(run.input_taps(48000), 65536)
        self.assertEqual(run.input_taps(96000), 131072)

    def test_input_ir(self):
        # Minimum phase, the gain of the points less the level.
        points = [
            (f, v) for f, v in zip(standard_grid(), bells_db("Left", standard_grid()))
        ]
        ir = run.input_ir(points + [(30000, 0.0)], 48000, level_db=3.0)
        self.assertEqual(len(ir), 65536)
        self.assertLess(max(range(len(ir)), key=lambda i: abs(ir[i])), 10)
        band = [f for f in standard_grid() if 30 <= f <= 15000]
        for f, got, (_, want) in zip(
            band,
            dsp.fir_gain_db(ir, 48000, band),
            [p for p in points if 30 <= p[0] <= 15000],
        ):
            self.assertAlmostEqual(got, want - 3.0, delta=0.1, msg=f)
        with self.assertRaises(ValueError):
            run.input_ir([(30000, 0.0)], 48000)

    def test_config_value(self):
        text = (
            "PSPointsFile = a.txt\n"
            'PSPointsFile = "my # file.txt" ; comment\n'
            "  MCFilterType=L# comment\n"
        )
        self.assertEqual(run.config_value(text, "PSPointsFile"), "my # file.txt")
        self.assertEqual(run.config_value(text, "MCFilterType"), "L")
        self.assertIsNone(run.config_value(text, "MCPointsFile"))

    def test_config_files(self):
        # The base directory first, then next to the configuration, then the
        # distribution's target and mic directories.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config" / "48.0 kHz" / "normal-48.0.drc"
            target = root / "target" / "48.0 kHz" / "pa-48.0.txt"
            mic = root / "mic" / "wm-61a.txt"
            for path in (config, target, mic):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("")
            self.assertEqual(run.config_file("pa-48.0.txt", config), target.resolve())
            self.assertEqual(run.config_file("wm-61a.txt", config), mic.resolve())
            self.assertIsNone(run.config_file("bk-48.0.txt", config))
            near = config.parent / "pa-48.0.txt"
            near.write_text("")
            self.assertEqual(run.config_file("pa-48.0.txt", config), near.resolve())
            base = f"{root}/target/48.0 kHz/"
            self.assertEqual(
                run.config_file("pa-48.0.txt", config, base), target.resolve()
            )
            self.assertEqual(run.config_file(str(mic), config), mic.resolve())

            # Passed to DRC; the mic file only with the mic stage on.
            text = "PSPointsFile = pa-48.0.txt\nMCPointsFile = ecm8000.txt\n"
            self.assertEqual(
                run._read_files(text + "MCFilterType = N\n", config),
                [f"--PSPointsFile={near.resolve()}"],
            )
            with self.assertRaisesRegex(ValueError, "MCPointsFile 'ecm8000.txt'"):
                run._read_files(text + "MCFilterType = M\n", config)
            with self.assertRaisesRegex(ValueError, "PSPointsFile 'bk.txt'"):
                run._read_files("PSPointsFile = bk.txt\n", config)

    def test_config_rate(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.drc"
            path.write_text("# BCSampleRate = 1\nBCSampleRate = 48000 # rate\n")
            self.assertEqual(run.config_rate(path), 48000)
            path.write_text("BCInFile = rs.pcm\n")
            with self.assertRaises(ValueError):
                run.config_rate(path)

    def test_run(self):
        # The input, the output, the working directory and the overrides.
        calls = []

        def fake(args, cwd, **kwargs):
            values = dict(a[2:].split("=", 1) for a in args[1:-1])
            calls.append((args, cwd, pcm.load(values["BCInFile"])))
            Path(values["PSOutFile"]).write_bytes(pcm.write([0.5, 0.25]))
            return subprocess.CompletedProcess(args, 0, "done\n", "")

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "c.drc"
            config.write_text("BCSampleRate = 48000\nPSPointsFile = pa.txt\n")
            points = Path(tmp) / "pa.txt"
            points.write_text("")
            with (
                mock.patch.object(run.shutil, "which", return_value="/bin/drc"),
                mock.patch.object(run.subprocess, "run", fake),
            ):
                ir, log = run.run([1.0, 0.0], config)
            self.assertEqual(ir, [0.5, 0.25])
            self.assertEqual(log, "done\n")
            args, cwd, given = calls[0]
            self.assertEqual(given, [1.0, 0.0])
            self.assertEqual(cwd, config.resolve().parent)
            self.assertEqual(args[0], "/bin/drc")
            self.assertEqual(args[-1], str(config.resolve()))
            for option in (
                "--BCBaseDir=",
                "--BCInFileType=F",
                "--BCImpulseCenterMode=A",
                "--PSOutFileType=F",
                "--TCOutFile=",
                "--MSOutFile=",
                f"--PSPointsFile={points.resolve()}",
            ):
                self.assertIn(option, args)

    def test_run_errors(self):
        failed = lambda args, **kwargs: subprocess.CompletedProcess(
            args, 1, "", "Unable to open input file\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "c.drc"
            with self.assertRaisesRegex(ValueError, "configuration"):
                run.run([1.0], config)
            config.write_text("BCSampleRate = 48000\n")
            with self.assertRaisesRegex(ValueError, "not found"):
                run.run([1.0], config, str(Path(tmp) / "missing"))
            with (
                mock.patch.object(run.shutil, "which", return_value="/bin/drc"),
                mock.patch.object(run.subprocess, "run", failed),
                self.assertRaisesRegex(ValueError, "(?s)exit status 1.*Unable to open"),
            ):
                run.run([1.0], config)


class MdatToDrcTests(unittest.TestCase):
    def test_registered(self):
        self.assertIs(CONVERTERS["mdat", "drc"], MdatToDrc)
        self.assertIs(CONVERTERS["drc", "swproj"], DrcToSwproj)

    def test_convert(self):
        result = MdatToDrc(channel="right").convert([ROOM])
        self.assertEqual(result.name, "Room Right.pcm")
        ir = pcm.read(result.data)
        self.assertEqual(len(ir), 65536)
        # The measurement less its level-band median.
        m = mdat.load(ROOM)[1]
        band = [f for f in standard_grid() if 200 <= f <= 10000]
        got = dsp.fir_gain_db(ir, 48000, band)
        want = log_resample(
            [(f, v) for f, v in zip(m.frequencies, m.response) if f > 0], band
        )
        median = statistics.median(want)
        self.assertLess(max(abs(g - (w - median)) for g, w in zip(got, want)), 0.5)

    def test_mic_profile(self):
        # The microphone table is subtracted.
        plain = pcm.read(MdatToDrc().convert([ROOM]).data)
        tilt = pcm.read(MdatToDrc(mic_profile=TILT).convert([ROOM]).data)
        frequencies = [40.0, 1000.0, 15000.0]
        table = log_resample(load_mic_profile(TILT).points, frequencies)
        a = dsp.fir_gain_db(plain, 48000, frequencies)
        b = dsp.fir_gain_db(tilt, 48000, frequencies)
        # Up to the level shift of the median.
        shift = b[1] - a[1] + table[1]
        for x, y, t in zip(a, b, table):
            self.assertAlmostEqual(y - x, shift - t, delta=0.2)

    def test_errors(self):
        with self.assertRaises(ValueError):
            MdatToDrc(channel="right").convert([LEFT_ONLY])
        with self.assertRaises(ValueError):
            MdatToDrc(mic_curve="degrees_30")
        with self.assertRaises(ValueError):
            MdatToDrc(rate=0)


class DrcToSwprojTests(unittest.TestCase):
    def test_correction(self):
        # Correction = the filter gain moved so that the corrected response has
        # a 0 dB median; capped and band limited as without DRC.
        result = DrcToSwproj(mdat=ROOM).convert(FILTERS)
        self.assertEqual(result.name, "Room DRC.swproj")
        grid = standard_grid()
        measurements = mdat.load(ROOM)
        filters = {
            m.channel: pcm.response(pcm.load(p), drc.RATE, grid)
            for m, p in zip(measurements, FILTERS)
        }
        curves, _ = speakerproject.prepare_speaker_curves(
            measurements,
            grid,
            [(20.0, 0.0), (20000.0, 0.0)],
            filters=filters,
        )
        for m in measurements:
            c = curves[m.channel]
            gain, gd = filters[m.channel]
            inside = [
                i
                for i, f in enumerate(grid)
                if speakerproject.DEFAULT_LOW_CUTOFF_HZ
                < f
                < speakerproject.DEFAULT_HIGH_CUTOFF_HZ
            ]
            offsets = {round(c.correction[i] - gain[i], 9) for i in inside[1:-1]}
            self.assertEqual(len(offsets), 1)
            self.assertEqual(c.correction[0], 0.0)
            self.assertEqual(c.correction_group_delay[0], 0.0)
            for i in inside[1:-1]:
                self.assertAlmostEqual(c.correction_group_delay[i], gd[i])
            corrected = [
                r + k
                for f, r, k in zip(grid, c.response, c.correction)
                if 200 <= f <= 10000
            ]
            self.assertAlmostEqual(statistics.median(corrected), 0.0, places=9)

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "--mdat"):
            DrcToSwproj()
        with self.assertRaisesRegex(ValueError, "one filter for each"):
            DrcToSwproj(mdat=ROOM).convert(FILTERS[:1])
        with self.assertRaises(ValueError):
            DrcToSwproj(mdat=ROOM, rate=32000).convert(FILTERS)
        with self.assertRaisesRegex(ValueError, "--drc-config"):
            MdatToSwproj(drc="drc")

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.swproj"
            args = ["convert", "-i", str(FILTERS[0]), "-i", str(FILTERS[1])]
            args += ["--mdat", str(ROOM), "-o", str(out)]
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(
                out.read_bytes(), DrcToSwproj(mdat=ROOM).convert(FILTERS).data
            )


class SpeakerProjectDrcTests(unittest.TestCase):
    def test_options(self):
        for cls, _ in SPEAKER_PROJECTS:
            for option in DRC_OPTIONS:
                self.assertIn(option, cls.options, cls.__name__)
        for option in DRC_OPTIONS:
            self.assertNotIn(option, DrcToSwproj.options)

    def test_convert(self):
        # DRC runs once per measurement on its input (the conversion's
        # microphone table) at the configuration's rate; its filter replaces
        # the inverse response.
        filter_ir = pcm.load(FILTERS[0])
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "c.drc"
            config.write_text("BCSampleRate = 48000\n")
            for cls, paths in SPEAKER_PROJECTS:
                with self.subTest(cls.__name__):
                    calls = []
                    c = cls(drc_config=config, drc="my-drc")
                    with mock.patch.object(run, "run", fake_run(calls, filter_ir)):
                        result = c.convert(paths)
                    _, measurements = c.measurements(*paths)
                    profile = c.default_mic_profile(*paths)
                    self.assertEqual(len(calls), len(measurements))
                    for (ir, cfg, executable), m in zip(calls, measurements):
                        self.assertEqual(ir, drc_input(m, profile, 48000))
                        self.assertEqual(cfg, config)
                        self.assertEqual(executable, "my-drc")
                    self.assertIn("DRC: c.drc at 48000 Hz", result.notes)
                    self.assertNotEqual(
                        swproj.SwProj(result.data).part("eqb"),
                        swproj.SwProj(cls().convert(paths).data).part("eqb"),
                    )

    def test_errors(self):
        for cls, _ in SPEAKER_PROJECTS:
            with self.assertRaisesRegex(ValueError, "--drc-config"):
                cls(drc="drc")

    def test_cli(self):
        cls, paths = SPEAKER_PROJECTS[1]
        filter_ir = pcm.load(FILTERS[0])
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "c.drc"
            config.write_text("BCSampleRate = 48000\n")
            out = Path(tmp) / "out.swproj"
            args = ["convert", "--to", "swproj", "-o", str(out)]
            args += [a for p in paths for a in ("-i", str(p))]
            args += ["--drc-config", str(config), "--drc", "my-drc"]
            with mock.patch.object(run, "run", return_value=(filter_ir, "")):
                self.assertEqual(cli.main(args), 0)
                want = cls(drc_config=config, drc="my-drc").convert(paths).data
            self.assertEqual(out.read_bytes(), want)


def target_file(config: Path) -> Path:
    text = config.read_text(encoding="latin-1")
    return run.config_file(run.config_value(text, "PSPointsFile"), config)


class RealDrcTests(unittest.TestCase):
    def setUp(self):
        if not (REAL_DRC and REAL_CONFIGS):
            if REQUIRE_DRC:
                self.fail("DRC or its 48 kHz configuration not found")
            self.skipTest("DRC not in testdata/real/drc or installed")
        self.config = REAL_CONFIGS[0]

    def test_mdat_to_swproj(self):
        # Running DRC from the conversion equals the manual run.
        auto = MdatToSwproj(drc_config=self.config, drc=REAL_DRC).convert([ROOM])
        with tempfile.TemporaryDirectory() as tmp:
            filters = []
            for channel in ("left", "right"):
                source = Path(tmp) / f"{channel}.pcm"
                target = Path(tmp) / f"{channel} filter.pcm"
                source.write_bytes(MdatToDrc(channel=channel).convert([ROOM]).data)
                subprocess.run(
                    [
                        REAL_DRC,
                        f"--BCInFile={source}",
                        f"--PSOutFile={target}",
                        "--TCOutFile=",
                        # DRC finds the target only in its working directory.
                        f"--PSPointsFile={target_file(self.config)}",
                        self.config.name,
                    ],
                    cwd=self.config.parent,
                    check=True,
                    capture_output=True,
                )
                filters.append(target)
            manual = DrcToSwproj(mdat=ROOM).convert(filters)
        # The same curves; the project names differ.
        self.assertEqual(
            swproj.SwProj(auto.data).part("eqb"), swproj.SwProj(manual.data).part("eqb")
        )

    def test_flatter(self):
        # The corrected response deviates less from flat than the measurement.
        grid = standard_grid()
        measurements = mdat.load(ROOM)
        profile = load_mic_profile(TILT)
        rate = run.config_rate(self.config)
        c = MdatToSwproj(drc_config=self.config, drc=REAL_DRC)
        filters, notes = c.filters(measurements, profile, ROOM)
        self.assertIn(f"at {rate} Hz", notes[0])
        curves, _ = speakerproject.prepare_speaker_curves(
            measurements, grid, profile.points, filters=filters
        )
        for channel, cv in curves.items():
            band = [i for i, f in enumerate(grid) if 100 <= f <= 10000]
            raw = statistics.pstdev(cv.response[i] for i in band)
            fixed = statistics.pstdev(cv.response[i] + cv.correction[i] for i in band)
            self.assertLess(fixed, raw / 2, channel)

    def test_speaker_projects(self):
        # Every speaker project conversion runs DRC.
        for cls, paths in SPEAKER_PROJECTS:
            with self.subTest(cls.__name__):
                result = cls(drc_config=self.config, drc=REAL_DRC).convert(paths)
                self.assertTrue(any(n.startswith("DRC: ") for n in result.notes))

    def test_outputs(self):
        # Every output converts; the corrected curve deviates less from flat
        # than the measurement.
        for target in ("autoeq", "fir", "tmreq", "soundsource"):
            with self.subTest(target):
                c = CONVERTERS["mdat", target](drc_config=self.config, drc=REAL_DRC)
                result = c.convert([ROOM])
                if target != "autoeq":
                    continue
                grid = standard_grid()
                correction = [
                    float(line.split(",")[1])
                    for line in result.data.decode().splitlines()[1:]
                ]
                m = mdat.load(ROOM)[0]
                measured = log_resample(list(zip(m.frequencies, m.response)), grid)
                band = [i for i, f in enumerate(grid) if 100 <= f <= 10000]
                raw = statistics.pstdev(measured[i] for i in band)
                fixed = statistics.pstdev(measured[i] + correction[i] for i in band)
                self.assertLess(fixed, raw / 2)

    def test_distribution_layout(self):
        # A configuration apart from its target file gives the same filters.
        want = MdatToSwproj(drc_config=self.config, drc=REAL_DRC).convert([ROOM])
        for config in REAL_CONFIGS[1:]:
            with self.subTest(config=config):
                got = MdatToSwproj(drc_config=config, drc=REAL_DRC).convert([ROOM])
                self.assertEqual(
                    swproj.SwProj(got.data).part("eqb"),
                    swproj.SwProj(want.data).part("eqb"),
                )
