import math
import unittest

from helpers import FREQUENCIES, analytic
from testgen import common, rew, rogueamoeba, soundid
from eqx import dsp, formats
from eqx.autoeq import response
from eqx.convert import CONVERTERS
from eqx.convert.to_soundsource import AutoeqToSoundsource
from eqx.convert.to_autoeq import SoundsourceToAutoeq
from eqx.model import Correction, Peq
from eqx.rogueamoeba import soundsource

ROOT = common.ROOT
TILT = ROOT / rogueamoeba.SOUNDSOURCE_DIR / "Tilt - Flat.txt"
CSV = ROOT / common.CSV_DIR / "Bass and treble.csv"
# Rogue Amoeba's sample profile, first lines.
SAMPLE = """Preamp: -7.9 dB
Filter 1: ON PK Fc 210 Hz Gain -4.9 dB Q 0.46
Filter 2: ON PK Fc 753 Hz Gain 4.2 dB Q 1.88"""


def peqs(text: str) -> list[Peq]:
    return soundsource.read(text).corrections[0].peqs


class ReaderTests(unittest.TestCase):
    def test_sample(self):
        c = soundsource.read(SAMPLE).corrections[0]
        self.assertEqual((c.channel, c.gain_db), ("Both", -7.9))
        self.assertEqual(c.peqs, [Peq(210, -4.9, 0.46), Peq(753, 4.2, 1.88)])

    def test_generated(self):
        c = soundsource.load(TILT).corrections[0]
        self.assertEqual(c.gain_db, -1.5)
        want = [g - 1.5 for g in analytic("Left")]
        for got, w in zip(c.response(FREQUENCIES), want, strict=True):
            self.assertAlmostEqual(got, w, places=9)

    def test_types(self):
        text = "\n".join(f"Filter: ON {token} Fc 1000 Hz Gain 3 dB Q 0.7"
                         for token in ("pk", "Bell", "LSC", "low_shelf", "HS", "HPF", "LP",
                                       "AP", "NO"))
        self.assertEqual([p.kind for p in peqs(text)],
                         ["bell", "bell", "low-shelf", "low-shelf", "high-shelf", "high-pass",
                          "low-pass", "all-pass", "notch"])
        with self.assertRaisesRegex(ValueError, "not a valid filter"):
            soundsource.read("Filter 1: ON BP Fc 1000 Hz Gain 3 dB Q 0.7")

    def test_grammar(self):
        text = ("Preamp:-3 dB\r\n# Filter 1: ON PK Fc 50 Hz Gain 1 dB Q 1\r\n"
                "filter 2 : on pk fc 500 q 2,5 gain -1,5\r\n"
                "Filter 3: OFF PK Fc 2000 Hz Gain 2 dB\r\n"
                "Preamp: -2 dB\r\nPreamp: -4 dB\r\n")
        export = soundsource.read(text)
        c = export.corrections[0]
        self.assertEqual(c.gain_db, -2)       # the first well-formed preamp
        self.assertEqual(c.peqs, [Peq(500, -1.5, 2.5), Peq(2000, 2, 1)])
        # SoundSource compares "ON" case-sensitively.
        self.assertIn(("OFF filters (applied)", 2), export.fields)
        self.assertEqual(soundsource.read("Filter 1: ON PK Fc 100").corrections[0].gain_db, 0)

    def test_clamps(self):
        self.assertEqual(peqs("Filter 1: ON PK Fc 10 Hz Gain 1 dB Q 0.1\n"
                              "Filter 2: ON PK Fc 30000 Hz Gain 40 dB Q 50"),
                         [Peq(20, 1, 0.4), Peq(20000, 40, 50)])

    def test_slope_shelf(self):
        (p,) = peqs("Filter 1: ON LS 12dB Fc 100 Hz Gain 6 dB Q 3")
        want = dsp.shelf(False, 100, 6, 10 ** (12 / 40))
        got = p.biquad()
        for f in FREQUENCIES:
            self.assertAlmostEqual(dsp.cascade_db([got], f, dsp.PEQ_SAMPLE_RATE),
                                   dsp.cascade_db([want], f, dsp.PEQ_SAMPLE_RATE), places=9)
        # 3 dB is SoundSource's smallest slope; slope tokens leave other filters alone.
        self.assertEqual(soundsource._slope_q(6, 0), soundsource._slope_q(6, 3))
        self.assertEqual(peqs("Filter 1: ON PK 6dB Fc 100 Gain 6 Q 3")[0].q, 3)
        with self.assertRaisesRegex(ValueError, "not a valid filter"):
            soundsource.read("Filter 1: ON LS 24dB Fc 100 Gain 6")

    def test_rejected(self):
        for text, message in (("Preamp: -1 dB\n", "no filter"),
                              ("\n".join(["Filter: ON PK Fc 100"] * 33), "33 filters")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                soundsource.read(text)

    def test_notch_and_all_pass(self):
        notch, all_pass = peqs("Filter: ON NO Fc 1000 Q 2\nFilter: ON AP Fc 1000 Q 2")
        self.assertLess(Correction("x", peqs=[notch]).response([999])[0], -40)
        for db in Correction("x", peqs=[all_pass]).response(FREQUENCIES):
            self.assertAlmostEqual(db, 0, places=9)


class DetectTests(unittest.TestCase):
    def test_by_content(self):
        self.assertEqual(formats.detect(TILT), "soundsource")
        self.assertFalse(soundsource.sniff(b"Preamp: -1 dB\n"))
        self.assertFalse(soundsource.sniff((ROOT / rew.CAL_DIR /
                                            "Bass and treble.txt").read_bytes()))
        for path in (ROOT / soundid.EXPORT_TXT_DIR).glob("*.txt"):
            self.assertFalse(soundsource.sniff(path.read_bytes()))


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        filters = [Peq(40, 3, 0.7, "low-shelf"), Peq(500.5, -2.5, 1.25),
                   Peq(9000, 1, 0.8, "high-shelf"),
                   Peq(20, 0, 0.71, "high-pass"), Peq(20000, 0, 0.71, "low-pass"),
                   Peq(3000, 0, 4, "notch"), Peq(100, 0, 1, "all-pass")]
        text = soundsource.write(Correction("Both", -6.25, peqs=filters))
        self.assertTrue(text.startswith(
            "Preamp: -6.25 dB\nFilter 1: ON LS Fc 40 Hz Gain 3 dB Q 0.7\n"))
        c = soundsource.read(text).corrections[0]
        self.assertEqual((c.gain_db, c.peqs), (-6.25, filters))
        self.assertEqual(soundsource.write(soundsource.load(TILT).corrections[0]),
                         TILT.read_text())

    def test_rejected(self):
        for c, message in ((Correction("x", peqs=[Peq(1000, 1, 1)] * 33), "33 filters"),
                           (Correction("x"), "at least one filter"),
                           (Correction("x", delay_ms=1, peqs=[Peq(1000, 1, 1)]), "no delay"),
                           (Correction("x", points=[(100, 1)]), "parametric filters only"),
                           (Correction("x", peqs=[Peq(10, 1, 1)]), "20-20000 Hz"),
                           (Correction("x", peqs=[Peq(100, 1, 0.3)]), "Q >= 0.4"),
                           (Correction("x", peqs=[Peq(100, 1, 1, "band-pass")]),
                            "no band-pass filter")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                soundsource.write(c)


class ConvertTests(unittest.TestCase):
    def test_registered(self):
        self.assertIs(CONVERTERS["autoeq", "soundsource"], AutoeqToSoundsource)
        self.assertIs(CONVERTERS["soundsource", "autoeq"], SoundsourceToAutoeq)

    def test_from_autoeq(self):
        result = AutoeqToSoundsource().convert([CSV])
        self.assertEqual(result.name, "Bass and treble.txt")
        c = soundsource.read(result.data.decode()).corrections[0]
        self.assertEqual(len(c.peqs), 10)
        points = [(f, v) for f, v in response.load(CSV).curve() if 20 <= f <= 20000]
        for (f, v), got in zip(points, c.response([f for f, _ in points])):
            self.assertAlmostEqual(got, v, delta=0.3)
        fewer = soundsource.read(AutoeqToSoundsource(filters=3).convert([CSV]).data.decode())
        self.assertLessEqual(len(fewer.corrections[0].peqs), 3)
        for bad in (0, 33):
            with self.assertRaisesRegex(ValueError, "--filters must be 1-32"):
                AutoeqToSoundsource(filters=bad)

    def test_to_autoeq(self):
        result = SoundsourceToAutoeq().convert([TILT])
        self.assertEqual(result.name, "Tilt - Flat.csv")
        points = response.load(ROOT / common.CSV_DIR / "Tilt - Flat.csv").curve()
        want = soundsource.load(TILT).corrections[0].response([f for f, _ in points])
        for (_, v), w in zip(points, want, strict=True):
            self.assertTrue(math.isclose(v, w, abs_tol=0.01))


if __name__ == "__main__":
    unittest.main()
