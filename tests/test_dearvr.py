import struct
import tempfile
import unittest
from pathlib import Path

from helpers import csv_points
from testgen import common, sennheiser

from eqx import cli, dsp, formats, impulse
from eqx.autoeq import response
from eqx.convert import CONVERTERS
from eqx.convert.to_autoeq import DearvrHpcToAutoeq
from eqx.convert.to_fir import DearvrHpcToFir
from eqx.sennheiser import hpc
from eqx.wav import fir

ROOT = common.ROOT
HPC = ROOT / sennheiser.HPC_DIR / "hpc.dat"
REAL = ROOT / "real" / "Sennheiser" / "hpc.dat"
MINIMUM_TAPS = {
    44100: 4500,
    48000: 4800,
    88200: 9000,
    96000: 9600,
    176400: 18000,
    192000: 19200,
}


def gains(ir, rate, frequencies):
    return dsp.fir_gain_db(ir, rate, frequencies)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.library = hpc.load(HPC)

    def test_headphones(self):
        self.assertEqual(self.library.version, 1)
        self.assertEqual(
            [(h.id, h.name) for h in self.library.headphones],
            [(i, n) for i, n, _, _ in sennheiser.HEADPHONES],
        )
        for h, (_, _, phases, sides) in zip(
            self.library.headphones, sennheiser.HEADPHONES
        ):
            with self.subTest(h.name):
                self.assertEqual(
                    {(f.phase, f.sample_rate) for f in h.filters},
                    {(p, r) for p, rates in phases.items() for r in rates},
                )
                for f in h.filters:
                    self.assertEqual(f.taps, sennheiser.TAPS[f.phase])
                    self.assertEqual(f.channel_count, max(1, len(sides)))

    def test_samples(self):
        # Stored as float 32 bit.
        for h, (_, _, _, sides) in zip(self.library.headphones, sennheiser.HEADPHONES):
            for f in h.filters:
                want = sennheiser.impulse(sides, f.phase, f.sample_rate)
                for got, w in zip(f.channels(), want, strict=True):
                    self.assertEqual(
                        got,
                        list(
                            struct.unpack(f"<{len(w)}f", struct.pack(f"<{len(w)}f", *w))
                        ),
                    )

    def test_phase(self):
        h = self.library.headphone("Tilt Studio")
        self.assertEqual(h.phases, ["minimum", "linear"])
        self.assertEqual(hpc.peak(h.filter("minimum", 48000)), 0)
        self.assertEqual(hpc.peak(h.filter("linear", 48000)), 1024)
        ir = h.filter("linear", 48000).channels()[0]
        for k in range(1, 1025):
            self.assertAlmostEqual(ir[1024 - k], ir[1024 + k], places=6)

    def test_gain_follows_bells(self):
        f = self.library.headphone("Tilt Stereo").filter("minimum", 48000)
        frequencies = [200, 1000, 4000, 8000, 15000]
        for ir, side in zip(f.channels(), ("Left", "Right"), strict=True):
            want = [
                dsp.cascade_db(common.bells(side, 48000), x, 48000) for x in frequencies
            ]
            for g, w in zip(gains(ir, 48000, frequencies), want):
                self.assertAlmostEqual(g, w, delta=0.1)

    def test_select(self):
        self.assertEqual(self.library.headphone("  tilt STUDIO ").id, 0x3E22D390)
        with self.assertRaisesRegex(
            ValueError,
            "specify --headphone, one of: Flat Flat, Tilt Stereo, Tilt Studio",
        ):
            self.library.headphone(None)
        with self.assertRaisesRegex(ValueError, "no headphone 'x'"):
            self.library.headphone("x")
        h = self.library.headphone("Tilt Stereo")
        with self.assertRaisesRegex(
            ValueError, "no linear phase filter; available: minimum"
        ):
            h.filter("linear", 48000)
        with self.assertRaisesRegex(ValueError, "at 44100 Hz; available: 48000"):
            h.filter("minimum", 44100)

    def test_rejected(self):
        data = HPC.read_bytes()
        with self.assertRaisesRegex(ValueError, "no HPIR identifier"):
            hpc.read(data[:4] + b"XXXX" + data[8:])
        with self.assertRaisesRegex(ValueError, "outside the file"):
            hpc.read(data[: len(data) // 2])

    def test_unknown_phase(self):
        rate = [("u32", 48000), ("tables", [[("floats", [1.0])]])]
        groups = [[("u8", 5), ("tables", [rate])], [None, ("tables", [rate])]]
        data = sennheiser._Builder().finish(
            [
                ("u32", 1),
                ("tables", [[("u32", 7), ("string", "Odd"), ("tables", groups)]]),
            ],
            hpc.IDENTIFIER,
        )
        self.assertEqual(hpc.read(data).headphones[0].phases, ["minimum", "type 5"])


class FormatTests(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(formats.detect(HPC), "dearvr-hpc")
        self.assertTrue(formats.FORMATS["dearvr-hpc"].sniff(HPC.read_bytes()))
        self.assertIn(("dearvr-hpc", "fir"), CONVERTERS)
        self.assertIn(("dearvr-hpc", "autoeq"), CONVERTERS)

    def test_inspect(self):
        inspector = formats.FORMATS["dearvr-hpc"].inspector
        sections = inspector().inspect(HPC)
        self.assertEqual([s.title for s in sections], ["file", "library", "headphones"])
        self.assertEqual(
            [r[1] for r in sections[2].table.rows],
            ["Flat Flat", "Tilt Stereo", "Tilt Studio"],
        )
        self.assertEqual(
            sections[2].table.rows[2],
            ("3e22d390", "Tilt Studio", "minimum, linear", "44.1, 48 kHz"),
        )
        section = inspector(headphone="tilt studio", rate=44100).inspect(HPC)[-1]
        self.assertEqual(section.title, "headphone Tilt Studio")
        self.assertEqual(
            section.table.columns, ["frequency Hz", "minimum dB", "linear dB"]
        )
        self.assertIn(
            ("linear 44100 Hz", "2049 taps, 1 channel(s), peak sample 1024 (23.22 ms)"),
            section.fields,
        )
        flat = inspector(headphone="Flat Flat").inspect(HPC)[-1]
        self.assertTrue(all(abs(v) < 1e-6 for row in flat.table.rows for v in row[1:]))
        with self.assertRaisesRegex(ValueError, "at 44100 Hz"):
            inspector(headphone="Flat Flat", rate=44100).inspect(HPC)


class ConversionTests(unittest.TestCase):
    def test_fir(self):
        result = DearvrHpcToFir(headphone="tilt stereo").convert([HPC])
        self.assertEqual(result.name, "Tilt Stereo.wav")
        got = fir.read(result.data)
        f = hpc.load(HPC).headphone("Tilt Stereo").filter("minimum", 48000)
        self.assertEqual((got.sample_rate, got.channels), (48000.0, f.channels()))
        self.assertIn(
            "2 channel(s), 2048 taps at 48000 Hz, minimum phase, latency 0 samples "
            "(0.00 ms)",
            result.notes,
        )

    def test_fir_linear_pcm(self):
        result = DearvrHpcToFir(
            headphone="Tilt Studio", phase="linear", rate=44100, encoding="pcm24"
        ).convert([HPC])
        got = fir.read(result.data)
        self.assertEqual(
            (got.sample_rate, len(got.channels), got.taps), (44100.0, 1, 2049)
        )
        self.assertEqual(impulse.peak_index(got.channels[0]), 1024)
        self.assertTrue(any("latency 1024 samples" in n for n in result.notes))

    def test_autoeq(self):
        result = DearvrHpcToAutoeq(headphone="Tilt Studio").convert([HPC])
        self.assertEqual(result.name, "Tilt Studio.csv")
        f = hpc.load(HPC).headphone("Tilt Studio").filter("minimum", 48000)
        want = fir.response(hpc.as_fir(f), 0)
        for (gf, gv), (wf, wv) in zip(csv_points(result), want, strict=True):
            # CSV values have 2 decimals.
            self.assertAlmostEqual(gf, wf, delta=0.006)
            self.assertAlmostEqual(gv, wv, delta=0.006)

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "specify --headphone"):
            DearvrHpcToAutoeq().convert([HPC])
        with self.assertRaisesRegex(ValueError, "phase must be one of"):
            DearvrHpcToFir(headphone="Flat Flat", phase="mixed")
        with self.assertRaisesRegex(ValueError, "at 96000 Hz"):
            DearvrHpcToFir(headphone="Flat Flat", rate=96000).convert([HPC])

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.csv"
            self.assertEqual(
                cli.main(
                    [
                        "convert",
                        "-i",
                        str(HPC),
                        "-o",
                        str(out),
                        "--headphone",
                        "Flat Flat",
                    ]
                ),
                0,
            )
            points = response.read(out.read_text()).curve(response.RAW)
            self.assertTrue(all(abs(v) < 1e-6 for _, v in points))


@unittest.skipUnless(
    REAL.is_file(), "no dearVR MIX hpc.dat in testdata/real/Sennheiser"
)
class RealFileTests(unittest.TestCase):
    def test_library(self):
        library = hpc.load(REAL)
        self.assertEqual((library.version, len(library.headphones)), (1, 55))
        rates = [44100, 48000, 88200, 96000, 176400, 192000]
        for h in library.headphones:
            self.assertEqual((h.phases, h.rates), (["minimum", "linear"], rates))
            for f in h.filters:
                taps = (
                    MINIMUM_TAPS[f.sample_rate]
                    if f.phase == "minimum"
                    else f.sample_rate // 10 + 2
                )
                self.assertEqual((f.taps, f.channel_count), (taps, 1))
        f = library.headphone("Sennheiser HD 600")
        frequencies = [100, 1000, 3000, 10000, 20000]
        minimum = gains(f.filter("minimum", 48000).channels()[0], 48000, frequencies)
        linear = gains(f.filter("linear", 48000).channels()[0], 48000, frequencies)
        self.assertEqual(hpc.peak(f.filter("linear", 48000)), 2400)
        for a, b in zip(minimum, linear):
            self.assertAlmostEqual(a, b, delta=0.2)


if __name__ == "__main__":
    unittest.main()
