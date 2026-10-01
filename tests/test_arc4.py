import math
import unittest

import gen_testdata
from eqx import dsp, formats
from eqx.convert import arc4_swproj
from eqx.ik import arc4, pak

ANALYSIS = gen_testdata.ROOT / gen_testdata.ARC4_DIR / "Arc4.arc4a"
MIC = gen_testdata.ROOT / gen_testdata.MIC_DIR / "FLAT01.swmicpkg"


def _entries() -> dict[str, bytes]:
    return pak.read(ANALYSIS.read_bytes())[1]


class ReaderTests(unittest.TestCase):
    def test_file(self):
        a = arc4.load(ANALYSIS)
        self.assertEqual(formats.detect(ANALYSIS), "arc4")
        self.assertEqual((a.sample_rate, a.fft_size), (48000, gen_testdata.ARC4_FFT_SIZE))
        self.assertEqual(a.steps, {0: (2, 2), 1: (2, 2)})
        self.assertEqual(a.info.get("SelectedMicType"), "MEMS")
        self.assertEqual(a.channel("right"), 1)
        with self.assertRaisesRegex(ValueError, "no Center channel"):
            a.channel("Center")

    def test_rejected(self):
        entries = _entries()
        info = entries["info.xml"]
        odd = gen_testdata.fir.write(gen_testdata.fir.Fir(48000, [[1.0] * 5]))
        short = gen_testdata.fir.write(gen_testdata.fir.Fir(48000, [[1.0] * 8]))
        for bad, message in (
                ({**entries, "info.xml": info.replace(b'"4.0.0"', b'"3.9.9"')}, "older than"),
                ({**entries, "info.xml": info.replace(b'Version="4.0.0" ', b"")}, "older than"),
                ({**entries, "info.xml": info.replace(gen_testdata.ARC4_ID.encode() + b'"/>',
                                                      b'"/>')}, "no SHA"),
                ({k: v for k, v in entries.items() if k != "ch1.wav"}, "no ch1.wav"),
                ({**entries, "ch0.wav": odd}, "5 values"),
                ({**entries, "ch1.wav": short}, "differ in length"),
                ({k: v for k, v in entries.items() if k != "info.xml"}, "no info.xml")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                arc4.read(pak.write(bad))
        # A newer version is read.
        arc4.read(pak.write({**entries,
                                          "info.xml": info.replace(b'"4.0.0"', b'"4.10.0"')}))


class ResponseTests(unittest.TestCase):
    def test_power(self):
        self.assertEqual(arc4.power([1.0, 2.0, 3.0, 4.0, 5.0, 0.0]), [1.0, 25.0, 25.0, 4.0])

    def test_generated(self):
        # The response is the bells' up to the normalisation offset, which
        # makes the mean power of the 40 Hz-10 kHz bins 1.
        a = arc4.load(ANALYSIS)
        n, rate = a.fft_size, a.sample_rate
        for c, side in enumerate(arc4.CHANNELS):
            with self.subTest(side):
                p = arc4.power(a.spectra[c])
                band = p[int(40 * n / rate):int(10000 * n / rate) + 1]
                self.assertAlmostEqual(sum(band) / len(band), 1, places=5)
                filters = gen_testdata.bells(side, rate)
                offsets = [db - dsp.cascade_db(filters, f, rate)
                           for f, db in zip(*arc4.response(a, c)) if 30 <= f <= 16000]
                self.assertLess(max(offsets) - min(offsets), 0.1)
                self.assertLess(abs(offsets[0]), 3)

    def test_swproj(self):
        target, measurements = arc4_swproj.Arc4ToSwproj(mic_profile=MIC).measurements(ANALYSIS)
        self.assertEqual(target.name, "2.0 (Stereo)")
        self.assertEqual([(m.index, m.channel, m.name) for m in measurements],
                         [(0, "Left", "Left Arc4"), (1, "Right", "Right Arc4")])
        self.assertTrue(all(g == 0 for m in measurements for g in m.group_delay))
        self.assertTrue(all(math.isfinite(r) for m in measurements for r in m.response))


if __name__ == "__main__":
    unittest.main()
