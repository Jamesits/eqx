import statistics
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from testgen import audyssey, common, drc, ik, rationalacoustics, rew, rode, soundid

from eqx import cli, convert
from eqx.convert import CONVERTERS
from eqx.convert.common import drc_input, median_level
from eqx.convert.to_autoeq import MEASUREMENTS, AutoeqToAutoeq, MdatToAutoeq
from eqx.convert.to_fir import AutoeqToFir, DrcToFir, SwprojToFir
from eqx.convert.to_soundsource import DrcToSoundsource
from eqx.convert.to_swproj import flat_mic_profile
from eqx.convert.to_tmreq import DrcToTmreq
from eqx.convert.with_drc import drc_filter
from eqx.curve import log_resample
from eqx.drc import pcm, run
from eqx.model import standard_grid
from eqx.rew import mdat
from eqx.rme import tmreq
from eqx.wav import fir

ROOT = common.ROOT
ROOM = ROOT / rew.MDAT_DIR / "Room.mdat"
FILTERS = [ROOT / drc.PCM_DIR / f"Room {side} filter.pcm" for side in ("Left", "Right")]
TILT = ROOT / soundid.MIC_DIR / "TILT01.swmicpkg"
TRACES = ROOT / rationalacoustics.TRACE_DIR
# A sample of every measurement source; two files: left then right.
SAMPLES = {
    "autoeq": [
        ROOT / common.CSV_DIR / f"Room {side}.csv" for side in ("Left", "Right")
    ],
    "mdat": [ROOM],
    "arcx": [ROOT / ik.ARCX_DIR / "Arc 5.1.arcXs"],
    "arc4": [ROOT / ik.ARC4_DIR / "Arc4.arc4a"],
    "mqx": [ROOT / audyssey.MQX_DIR / "Mqx 5.1.mqx"],
    "swproj": [ROOT / soundid.PROJ_DIR / "Room.swproj"],
    "fuzzmeasure": [ROOT / rode.FUZZMEASURE_DIR / "Fm4.fume4"],
    "smaart-trf": [TRACES / "Tf Left.trf"],
    "smaart-srf": [TRACES / "Rta.srf"],
    "smaart-ref": [TRACES / "Ref Left.ref"],
    "smaart-ascii": [ROOT / rationalacoustics.ASCII_DIR / "Tf export.txt"],
}
TARGETS = {"autoeq": ".csv", "fir": ".wav", "tmreq": ".tmreq", "soundsource": ".txt"}
STEREO = ("fir", "tmreq")
LEVEL_BAND = [i for i, f in enumerate(standard_grid()) if 200 <= f <= 10000]


def fake_run(calls: list, ir: list[float]):
    def fake(samples, config, program):
        calls.append((samples, config, program))
        return ir, ""

    return fake


class DrcTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.config = self.tmp / "c.drc"
        self.config.write_text("BCSampleRate = 48000\n")
        self.ir = pcm.load(FILTERS[0])

    def stubbed(self, calls: list):
        return mock.patch.object(run, "run", fake_run(calls, self.ir))


class FilterTests(unittest.TestCase):
    def test_level(self):
        # Without a measurement the filter's 200 Hz-10 kHz median is 0 dB;
        # with one, the corrected curve's.
        ir = pcm.load(FILTERS[0])
        f = drc_filter(ir, drc.RATE)
        self.assertAlmostEqual(
            statistics.median(f.correction[i][1] for i in LEVEL_BAND), 0, places=9
        )
        gain = 10 ** (f.level_db / 20)
        self.assertEqual(f.fir(), [v * gain for v in ir])
        tilt = [(20.0, -6.0), (20000.0, 6.0)]
        f = drc_filter(ir, drc.RATE, tilt)
        grid = standard_grid()
        corrected = [m + c for m, (_, c) in zip(log_resample(tilt, grid), f.correction)]
        self.assertAlmostEqual(
            statistics.median(corrected[i] for i in LEVEL_BAND), 0, places=9
        )

    def test_rate(self):
        with self.assertRaisesRegex(ValueError, "44100"):
            drc_filter(pcm.load(FILTERS[0]), 32000)


class RegistryTests(unittest.TestCase):
    def test_pairs(self):
        sources = {r.source for r in MEASUREMENTS}
        self.assertEqual(sources, set(SAMPLES))
        for source in sources:
            for target in TARGETS:
                self.assertIn((source, target), CONVERTERS)
        for target in TARGETS:
            self.assertIn(("drc", target), CONVERTERS)

    def test_default_target(self):
        # The DRC outputs need --to; a source keeps its single other converter.
        self.assertIs(convert.find("smaart-trf"), CONVERTERS["smaart-trf", "autoeq"])
        self.assertIs(convert.find("drc"), CONVERTERS["drc", "swproj"])


class MeasurementTests(DrcTestCase):
    def test_every_pair(self):
        # One DRC run per channel; the output named " DRC".  The input design
        # is slow and checked by test_input.
        for source, paths in SAMPLES.items():
            for target, extension in TARGETS.items():
                with self.subTest(f"{source} -> {target}"):
                    cls = CONVERTERS[source, target]
                    calls = []
                    with (
                        self.stubbed(calls),
                        mock.patch.object(
                            run, "input_ir", lambda points, rate, level: [rate]
                        ),
                    ):
                        result = cls(drc_config=self.config, drc="my-drc").convert(
                            paths[: cls.inputs]
                        )
                    stereo = target in STEREO and (len(paths) == 2 or cls.inputs == 1)
                    self.assertEqual(len(calls), 2 if stereo else 1)
                    for samples, config, program in calls:
                        self.assertEqual(samples, [48000])
                        self.assertEqual(config, self.config)
                        self.assertEqual(program, "my-drc")
                    self.assertTrue(result.name.endswith(" DRC" + extension))
                    self.assertTrue(
                        any(
                            n.startswith("DRC: c.drc at 48000 Hz") for n in result.notes
                        )
                    )

    def test_input(self):
        # The DRC input of mdat -> drc; left and right of one file.
        measurements = mdat.load(ROOM)
        calls = []
        with self.stubbed(calls):
            CONVERTERS["mdat", "fir"](drc_config=self.config).convert([ROOM])
        for (samples, _, _), m in zip(calls, measurements):
            self.assertEqual(samples, drc_input(m, flat_mic_profile(), 48000))
        calls = []
        with self.stubbed(calls):
            MdatToAutoeq(channel="right", drc_config=self.config).convert([ROOM])
        self.assertEqual(
            calls[0][0], drc_input(measurements[1], flat_mic_profile(), 48000)
        )

    def test_correction(self):
        # The corrected measurement has a 0 dB median.
        with self.stubbed([]):
            result = MdatToAutoeq(drc_config=self.config).convert([ROOM])
        self.assertEqual(result.name, "Room Left DRC.csv")
        correction = [
            float(line.split(",")[1]) for line in result.data.decode().splitlines()[1:]
        ]
        m = mdat.load(ROOM)[0]
        points = [(f, v) for f, v in zip(m.frequencies, m.response) if f > 0]
        level = median_level([("", points)])
        measured = log_resample(points, standard_grid())
        self.assertAlmostEqual(
            statistics.median(measured[i] - level + correction[i] for i in LEVEL_BAND),
            0,
            # The CSV is rounded.
            places=2,
        )

    def test_one_input_both_channels(self):
        # One curve: TotalMix gets it on both channels; the FIR is mono.
        csv = SAMPLES["autoeq"][0]
        with self.stubbed([]):
            preset = CONVERTERS["autoeq", "tmreq"](drc_config=self.config).convert(
                [csv]
            )
            wav = AutoeqToFir(drc_config=self.config).convert([csv])
        left, right = tmreq.read(preset.data.decode()).corrections
        self.assertEqual(left.peqs, right.peqs)
        self.assertEqual(len(fir.read(wav.data).channels), 1)

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "--drc-config is required"):
            CONVERTERS["mdat", "fir"]()
        with self.assertRaisesRegex(ValueError, "--drc-config is required"):
            AutoeqToAutoeq().convert(SAMPLES["autoeq"][:1])
        with self.assertRaisesRegex(ValueError, "--mic-profile need --drc-config"):
            MdatToAutoeq(mic_profile=TILT)
        with self.assertRaisesRegex(ValueError, "--drc need --drc-config"):
            AutoeqToFir(drc="drc")
        with self.assertRaisesRegex(ValueError, "--phase do not apply"):
            AutoeqToFir(drc_config=self.config, phase="linear")
        with self.assertRaisesRegex(ValueError, "--rate do not apply"):
            SwprojToFir(drc_config=self.config, rate=44100)

    def test_cli(self):
        out = self.tmp / "out.tmreq"
        args = ["convert", "-i", str(ROOM), "-o", str(out)]
        args += ["--drc-config", str(self.config), "--mic-profile", str(TILT)]
        with self.stubbed([]):
            self.assertEqual(cli.main(args), 0)
            want = CONVERTERS["mdat", "tmreq"](
                drc_config=self.config, mic_profile=TILT
            ).convert([ROOM])
        self.assertEqual(out.read_bytes(), want.data)


class FilterOutputTests(unittest.TestCase):
    def test_fir(self):
        result = DrcToFir().convert(FILTERS)
        self.assertEqual(result.name, "Room Left filter DRC.wav")
        w = fir.read(result.data)
        self.assertEqual(w.sample_rate, drc.RATE)
        for channel, path in zip(w.channels, FILTERS):
            f = drc_filter(pcm.load(path), drc.RATE)
            for got, want in zip(channel, f.fir()):
                self.assertAlmostEqual(got, want, places=6)

    def test_curves(self):
        result = CONVERTERS["drc", "autoeq"]().convert(FILTERS[:1])
        values = [
            float(line.split(",")[1]) for line in result.data.decode().splitlines()[1:]
        ]
        self.assertAlmostEqual(
            statistics.median(values[i] for i in LEVEL_BAND), 0, places=5
        )
        left, right = tmreq.read(
            DrcToTmreq().convert(FILTERS[:1]).data.decode()
        ).corrections
        self.assertEqual(left.peqs, right.peqs)
        self.assertTrue(DrcToSoundsource(filters=5).convert(FILTERS[:1]).data)

    def test_errors(self):
        with self.assertRaises(ValueError):
            DrcToFir(rate=32000).convert(FILTERS)
        with self.assertRaises(ValueError):
            DrcToSoundsource(filters=0)
        with self.assertRaises(ValueError):
            DrcToFir(encoding="pcm8")
