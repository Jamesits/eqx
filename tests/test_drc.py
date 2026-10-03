import shutil
import statistics
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from testgen import common, drc, rew, soundid

from eqx import cli, dsp, formats
from eqx.convert import CONVERTERS
from eqx.convert.to_drc import MdatToDrc
from eqx.convert.to_swproj import DrcToSwproj, MdatToSwproj, load_mic_profile
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
REAL = ROOT / "real" / "drc"
REAL_DRC = shutil.which(str(REAL / "drc"))
REAL_CONFIG = REAL / "cfg48" / "normal-48.0.drc"


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
            config.write_text("BCSampleRate = 48000\n")
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
            MdatToDrc(mic_angle="degrees_30")
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


@unittest.skipUnless(REAL_DRC and REAL_CONFIG.is_file(), "DRC not in testdata/real/drc")
class RealDrcTests(unittest.TestCase):
    def test_mdat_to_swproj(self):
        # Running DRC from the conversion equals the manual run.
        auto = MdatToSwproj(drc_config=REAL_CONFIG, drc=REAL_DRC).convert([ROOM])
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
                        REAL_CONFIG.name,
                    ],
                    cwd=REAL_CONFIG.parent,
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
        rate = run.config_rate(REAL_CONFIG)
        c = MdatToSwproj(drc_config=REAL_CONFIG, drc=REAL_DRC)
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
