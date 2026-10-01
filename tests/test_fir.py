import math
import struct
import tempfile
import unittest
from pathlib import Path

from helpers import csv_points
from testgen import common, rew, soundid

from eqx import dsp
from eqx.convert import AutoeqToFir, FirToAutoeq, MdatToSwproj, PeqbToFir, SwprojToFir
from eqx.curve import log_resample
from eqx.model import standard_grid
from eqx.soundid import crypto, peqb, playback
from eqx.wav import fir

ROOT = common.ROOT
TILT = ROOT / soundid.PEQB_DIR / "Tilt Tilt Wired Average.swhp"
ROOM_LEFT = ROOT / common.CSV_DIR / "Room Left.csv"
ROOM_RIGHT = ROOT / common.CSV_DIR / "Room Right.csv"
ROOM_PROJECT = ROOT / soundid.PROJ_DIR / "Room.swproj"
SURROUND = ROOT / soundid.PROJ_DIR / "Arc 5.1.swproj"
GRID = standard_grid()
SAMPLES = [0.5, -0.25, 0.0, 0.75]


def _wav(fmt: bytes, data: bytes, extra: bytes = b"") -> bytes:
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra
    body += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


def _fmt(code: int, channels: int, bits: int, rate: int = 48000) -> bytes:
    return struct.pack("<HHIIHH", code, channels, rate, 0, channels * bits // 8, bits)


def _pcm(values, bits: int) -> bytes:
    scale = 2 ** (bits - 1)
    return b"".join(
        round(v * scale).to_bytes(bits // 8, "little", signed=True) for v in values
    )


def _tilt() -> peqb.Peqb:
    return peqb.read(TILT.read_bytes(), crypto.swhp_key(common.COMPUTER_ID))


def _interp_log(points, f):
    return log_resample(points, [f])[0]


class WavTests(unittest.TestCase):
    def test_encodings(self):
        floats = struct.pack("<4f", *SAMPLES)
        extensible = (
            struct.pack("<HHIIHHHHI", fir.EXTENSIBLE, 1, 48000, 0, 4, 32, 22, 32, 0)
            + struct.pack("<H", fir.IEEE_FLOAT)
            + bytes(14)
        )
        cases = {
            "PCM 16 bit": _wav(_fmt(1, 1, 16), _pcm(SAMPLES, 16)),
            "PCM 24 bit": _wav(_fmt(1, 1, 24), _pcm(SAMPLES, 24)),
            "PCM 32 bit": _wav(_fmt(1, 1, 32), _pcm(SAMPLES, 32)),
            "float 32 bit": _wav(
                _fmt(3, 1, 32), floats, b"LIST\x03\x00\x00\x00abc\x00"
            ),
            "float 64 bit": _wav(_fmt(3, 1, 64), struct.pack("<4d", *SAMPLES)),
            "extensible": _wav(extensible, floats),
        }
        for name, data in cases.items():
            with self.subTest(name):
                f = fir.read(data)
                self.assertEqual((f.sample_rate, f.channels), (48000.0, [SAMPLES]))

    def test_channels_interleaved(self):
        f = fir.read(_wav(_fmt(3, 2, 32), struct.pack("<4f", *SAMPLES)))
        self.assertEqual(f.channels, [[0.5, 0.0], [-0.25, 0.75]])

    def test_round_trip(self):
        f = fir.Fir(96000.0, [SAMPLES, SAMPLES[::-1]])
        self.assertEqual(fir.read(fir.write(f)), f)

    def test_rejected(self):
        good = _wav(_fmt(3, 1, 32), struct.pack("<4f", *SAMPLES))
        for data, message in (
            (b"RIFX" + good[4:], "not a WAV"),
            (good[:36], "no fmt or data"),
            (_wav(_fmt(1, 1, 8), bytes(4)), "unsupported WAV encoding 1, 8"),
            (_wav(_fmt(3, 0, 32), bytes(4)), "0 channels"),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                fir.read(data)
        with self.assertRaisesRegex(ValueError, "one non-zero length"):
            fir.write(fir.Fir(48000.0, [[1.0], [1.0, 0.0]]))

    def test_pcm(self):
        f = fir.Fir(48000.0, [SAMPLES, SAMPLES[::-1]])
        for encoding, bits in (("pcm16", 16), ("pcm24", 24), ("pcm32", 32)):
            with self.subTest(encoding):
                data = fir.write(f, encoding)
                self.assertEqual(
                    struct.unpack_from("<HHIIHH", data, 20),
                    (fir.PCM, 2, 48000, 48000 * bits // 4, bits // 4, bits),
                )
                got = fir.read(data)
                self.assertEqual(got.encoding, f"PCM {bits} bit")
                for a, b in zip(got.channels, f.channels):
                    for x, y in zip(a, b):
                        self.assertAlmostEqual(x, y, delta=2 ** (1 - bits))
        self.assertEqual(
            fir.write(fir.Fir(48000.0, [[1.0, -1.0]]), "pcm16")[-4:],
            struct.pack("<hh", 32767, -32767),
        )
        with self.assertRaisesRegex(
            ValueError, r"\+0.83 dB re full scale; pcm24 would clip"
        ):
            fir.write(fir.Fir(48000.0, [[1.1, 0.0]]), "pcm24")
        with self.assertRaisesRegex(ValueError, "encoding must be one of"):
            fir.write(f, "pcm8")


class DesignTests(unittest.TestCase):
    def test_hermite(self):
        xs, ys = [0.0, 1.0, 2.5, 4.0], [1.0, -2.0, 0.5, 3.0]
        for got, want in zip(dsp.hermite(xs, ys, xs), ys):
            self.assertAlmostEqual(got, want)
        self.assertEqual(dsp.hermite(xs, ys, [-1.0, 5.0]), [1.0, 3.0])
        line = dsp.hermite([0.0, 1.0, 3.0], [1.0, 3.0, 7.0], [0.5, 2.0])
        for got, want in zip(line, [2.0, 5.0]):
            self.assertAlmostEqual(got, want)
        # The slope at a point is the secant of its neighbours: a plateau overshoots.
        self.assertGreater(
            max(
                dsp.hermite(
                    [0.0, 1.0, 2.0, 10.0],
                    [0.0, 11.0, 12.0, 12.0],
                    [1.5 + i / 100 for i in range(100)],
                )
            ),
            12.0,
        )
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            dsp.hermite([0.0, 0.0], [1.0, 2.0], [0.0])

    def test_ifft(self):
        x = [1.0, -2.0, 0.5, 3.0, 0.0, 1.5, -1.0, 2.0]
        for got, want in zip(dsp.ifft(dsp.fft(x)), x):
            self.assertAlmostEqual(got.real, want)
            self.assertAlmostEqual(got.imag, 0.0)

    def test_soundid_lengths(self):
        for rate, minimum, linear in (
            (44100, 3763, 4001),
            (48000, 4096, 4353),
            (96000, 8192, 8707),
            (192000, 16384, 17415),
        ):
            with self.subTest(rate):
                self.assertEqual(dsp.fir_taps(rate, "minimum"), minimum)
                self.assertEqual(dsp.fir_taps(rate, "linear"), linear)

    def test_flat_is_an_impulse(self):
        for phase, peak in (("minimum", 0), ("linear", 2176)):
            with self.subTest(phase):
                ir = dsp.design_fir([20.0, 20000.0], [0.0, 0.0], 48000, phase)
                self.assertAlmostEqual(ir[peak], 1.0)
                self.assertLess(
                    max(abs(v) for i, v in enumerate(ir) if i != peak), 1e-9
                )

    def test_gain_follows_curve(self):
        grid = [20.0 * 1000 ** (i / 200) for i in range(201)]
        curve = [
            6 * math.exp(-0.5 * math.log2(f / 1000) ** 2) - 3 * math.log2(f / 20) / 10
            for f in grid
        ]
        check = [f for f in grid if 100 <= f <= 15000]
        for phase in dsp.PHASES:
            with self.subTest(phase):
                ir = dsp.design_fir(grid, curve, 48000, phase)
                got = dsp.fir_gain_db(ir, 48000, check)
                want = dsp.hermite(
                    [math.log(f) for f in grid], curve, [math.log(f) for f in check]
                )
                self.assertLess(max(abs(g - w) for g, w in zip(got, want)), 0.05)

    def test_phase(self):
        grid, curve = [20.0, 200.0, 2000.0, 20000.0], [6.0, -3.0, 4.0, 0.0]
        ir = dsp.design_fir(grid, curve, 48000, "minimum")
        energy = sum(v * v for v in ir)
        self.assertEqual(max(range(len(ir)), key=lambda i: abs(ir[i])), 0)
        self.assertGreater(sum(v * v for v in ir[:400]) / energy, 0.99)
        ir = dsp.design_fir(grid, curve, 48000, "linear", 1001)
        self.assertEqual(len(ir), 1001)
        self.assertLess(max(abs(a - b) for a, b in zip(ir, ir[::-1])), 1e-12)

    def test_edges(self):
        ir = dsp.design_fir([20.0, 20000.0], [6.0, 6.0], 48000, "minimum")
        self.assertAlmostEqual(dsp.fir_gain_db(ir, 48000, [0.0])[0], 6.0, places=3)
        ir = dsp.design_fir([20.0, 20000.0], [6.0, 6.0], 48000, "minimum", edge_db=0.0)
        self.assertLess(dsp.fir_gain_db(ir, 48000, [0.0])[0], 5.5)
        self.assertAlmostEqual(dsp.fir_gain_db(ir, 48000, [1000.0])[0], 6.0, places=2)

    def test_rejected(self):
        with self.assertRaisesRegex(ValueError, "odd"):
            dsp.design_fir([20.0, 20000.0], [0.0, 0.0], 48000, "linear", 1000)
        with self.assertRaisesRegex(ValueError, "phase"):
            dsp.fir_taps(48000, "mixed")


class PlaybackTests(unittest.TestCase):
    def test_frame_caps_boost(self):
        p = _tilt()
        frame = [
            (f, r) for c in p.curves if c.type_name == "Frame" for f, r, _ in c.points
        ]
        curves = playback.headphone_curves(p)
        for side in playback.SIDES:
            correction = [
                (f, r)
                for c in p.curves
                if c.type_name == f"Correction{side}"
                for f, r, _ in c.points
            ]
            for (f, got), (_, want) in zip(curves[side], correction):
                self.assertAlmostEqual(got, min(want, _interp_log(frame, f)))
        # The frame is 0 dB at 20 Hz and 22 kHz; a cut stays.
        self.assertTrue(any(g < 0 for _, g in curves["Left"]))
        self.assertLessEqual(curves["Left"][0][1], 0.0)
        self.assertLessEqual(curves["Left"][-1][1], 0.0)

    def test_without_frame(self):
        p = _tilt()
        p.curves = [c for c in p.curves if c.type_name != "Frame"]
        correction = [
            (f, r)
            for c in p.curves
            if c.type_name == "CorrectionLeft"
            for f, r, _ in c.points
        ]
        self.assertEqual(playback.headphone_curves(p)["Left"], correction)

    def test_safe_headroom(self):
        self.assertEqual(
            playback.safe_headroom_db(
                {"Left": [(1, -2.0), (2, 3.5)], "Right": [(1, 4.0)]}
            ),
            -4.0,
        )
        self.assertEqual(playback.safe_headroom_db({"Left": [(1, -2.0)]}), 0.0)
        p = _tilt()
        on, gain = playback.headphone_fir(p)
        off, zero = playback.headphone_fir(p, safe_headroom=False)
        self.assertEqual(zero, 0.0)
        self.assertLess(gain, 0.0)
        self.assertAlmostEqual(on[0][0], off[0][0] * 10 ** (gain / 20))


class ConversionTests(unittest.TestCase):
    def test_peqb_to_fir(self):
        result = PeqbToFir(computer_id=common.COMPUTER_ID).convert([TILT])
        f = fir.read(result.data)
        self.assertEqual(
            (result.name, f.sample_rate, len(f.channels), f.taps),
            ("Tilt Tilt Wired Average.wav", 48000.0, 2, 4096),
        )
        curves = playback.headphone_curves(_tilt())
        gain = playback.safe_headroom_db(curves)
        self.assertIn(f"gain {gain:.2f} dB", result.notes)
        # The short filter smooths the bass; the band above follows the curve.
        for c, side in enumerate(playback.SIDES):
            for freq, got in fir.response(f, c):
                if 100 <= freq <= 15000:
                    self.assertAlmostEqual(
                        got, _interp_log(curves[side], freq) + gain, delta=0.1
                    )

    def test_peqb_to_fir_options(self):
        result = PeqbToFir(
            "linear", 96000, computer_id=common.COMPUTER_ID, safe_headroom=False
        ).convert([TILT])
        f = fir.read(result.data)
        self.assertEqual((f.sample_rate, f.taps), (96000.0, 8707))
        self.assertEqual(max(range(f.taps), key=lambda i: abs(f.channels[0][i])), 4353)
        self.assertIn("gain 0.00 dB", result.notes)
        for args, message in (
            (("mixed",), "phase"),
            (("minimum", 0), "sample rate"),
            (("linear", 48000, 1000), "odd"),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                PeqbToFir(*args)

    def test_autoeq_to_fir(self):
        mono = fir.read(AutoeqToFir().convert([ROOM_LEFT]).data)
        self.assertEqual((len(mono.channels), mono.taps), (1, 4096))
        result = AutoeqToFir(phase="linear", taps=2001).convert([ROOM_LEFT, ROOM_RIGHT])
        stereo = fir.read(result.data)
        self.assertEqual(
            (result.name, len(stereo.channels), stereo.taps), ("Room Left.wav", 2, 2001)
        )
        self.assertNotEqual(stereo.channels[0], stereo.channels[1])
        # Room Left is dB SPL: the filter is far above full scale.
        result = AutoeqToFir(encoding="pcm24").convert([ROOM_LEFT])
        pcm = fir.read(result.data)
        peak = max(abs(v) for v in mono.channels[0])
        self.assertEqual(pcm.encoding, "PCM 24 bit")
        self.assertIn(
            f"scaled by {-20 * math.log10(peak):.2f} dB to fit pcm24", result.notes[-1]
        )
        self.assertLess(
            max(abs(a - b / peak) for a, b in zip(pcm.channels[0], mono.channels[0])),
            1e-6,
        )
        with self.assertRaisesRegex(ValueError, "--encoding must be one of"):
            AutoeqToFir(encoding="mp3")

    def test_fir_to_autoeq(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.wav"
            path.write_bytes(fir.write(fir.Fir(44100.0, [[0.5] + [0.0] * 99])))
            result = FirToAutoeq().convert([path])
            self.assertEqual(result.name, "f Left.csv")
            points = csv_points(result)
            self.assertEqual(len(points), 355)
            self.assertTrue(all(abs(g + 6.02) < 0.01 for _, g in points))
            with self.assertRaisesRegex(ValueError, "no Right channel"):
                FirToAutoeq("right").convert([path])


def _hp(f, f0, order=2):
    return -10 * math.log10(1 + (f0 / f) ** (2 * order))


def _lp(f, f0, order=2):
    return -10 * math.log10(1 + (f / f0) ** (2 * order))


def _channel(index, name, measurement, correction):
    return playback.SpeakerChannel(
        index,
        name,
        "Front",
        False,
        [(f, measurement(f)) for f in GRID],
        [(f, correction(f)) for f in GRID],
        0.0,
        0.0,
    )


class SpeakerTests(unittest.TestCase):
    RULE = playback.SPEAKER_RULE

    def rolloffs(self, measurement):
        points = [(f, measurement(f)) for f in GRID]
        level = playback.level_db(points, self.RULE.level_hz)
        return (
            playback.low_rolloff(points, level, self.RULE.low_search_hz),
            playback.high_rolloff(
                points, level, self.RULE.high_search_hz, self.RULE.high_slope
            ),
        )

    def test_rolloff(self):
        self.assertEqual(self.rolloffs(lambda f: 0.0), (20.0, 22000.0))
        # Half an octave above the -12 dB point (35.7 Hz), a quarter below 19.6 kHz.
        low, high = self.rolloffs(lambda f: _hp(f, 70) + _lp(f, 10000))
        self.assertAlmostEqual(low, 70 / 14.85**0.25 * 2**0.5, delta=1.5)
        self.assertAlmostEqual(high, 10000 * 14.85**0.25 / 2**0.25, delta=300)
        # The level is the 200 Hz-10 kHz average: an offset changes nothing.
        self.assertEqual(
            self.rolloffs(lambda f: _hp(f, 70) - 10),
            self.rolloffs(lambda f: _hp(f, 70)),
        )

    def test_limit_points(self):
        points = playback.limit_points(50.0, 16000.0, 12.0, 48.0)
        self.assertEqual(points[0], (20.0, 0.0))
        self.assertEqual(points[1], (50.0, 0.0))
        self.assertEqual(points[-1], (22000.0, 0.0))
        self.assertEqual(max(v for _, v in points), 12.0)
        self.assertTrue(
            all(v == 0.0 for _, v in playback.limit_points(50.0, 16000.0, 0.0, 48.0))
        )
        # Extended moves the low roll-off half an octave down.
        self.assertAlmostEqual(
            playback.limit_points(50.0, 16000.0, 6.0, 48.0, 12.0)[1][0], 50 / 2**0.5
        )

    def test_limit(self):
        curves = playback.speaker_curves(
            [
                _channel(0, "Left", lambda f: 0.0, lambda f: 20.0),
                _channel(1, "Right", lambda f: 0.0, lambda f: -6.0),
            ]
        )
        boost = [v for _, v in curves[0]]
        self.assertEqual((boost[0], boost[-1]), (0.0, 0.0))
        # SoundID's safe headroom for this curve is -12.144 dB: the plateau overshoots.
        self.assertAlmostEqual(max(boost), 12.144, places=3)
        self.assertAlmostEqual(playback.safe_headroom_db(curves), -12.144, places=3)
        self.assertTrue(all(v == -6.0 for _, v in curves[1]))
        reduced = playback.speaker_curves(
            [_channel(0, "Left", lambda f: 0.0, lambda f: 20.0)], 6.0
        )
        self.assertLess(max(v for _, v in reduced[0]), 6.2)
        with self.assertRaisesRegex(ValueError, "12, 6 or 0"):
            playback.speaker_curves(
                [_channel(0, "Left", lambda f: 0.0, lambda f: 0.0)], 3.0
            )

    def test_group_follows_narrowest_channel(self):
        curves = playback.speaker_curves(
            [
                _channel(0, "Left", lambda f: _hp(f, 70, 4), lambda f: 0.0),
                _channel(1, "Right", lambda f: 0.0, lambda f: 0.0),
            ]
        )
        right = dict(curves[1])
        for f in GRID:
            if f < 40:
                self.assertAlmostEqual(right[f], _hp(f, 70, 4), delta=0.2)
        self.assertTrue(all(v == 0.0 for _, v in curves[0]))
        self.assertEqual(right[GRID[200]], 0.0)

    def test_spot_samples(self):
        for ms, samples in (
            (0.14583332813344896, 5),
            (1.0, 47),
            (0.5, 23),
            (0.03, 0),
            (0.0, 0),
        ):
            self.assertEqual(playback.spot_samples(ms, 48000), samples)

    def test_swproj_to_fir(self):
        result = SwprojToFir(safe_headroom=False).convert([ROOM_PROJECT])
        f = fir.read(result.data)
        self.assertEqual((result.name, len(f.channels), f.taps), ("Room.wav", 2, 4096))
        self.assertIn(
            "channels: Left, Right; gain 0.00 dB; limits: 12 dB, low neutral, "
            "high neutral",
            result.notes,
        )
        surround = fir.read(SwprojToFir("linear").convert([SURROUND]).data)
        self.assertEqual((len(surround.channels), surround.taps), (6, 4353))
        with self.assertRaisesRegex(ValueError, "12, 6 or 0"):
            SwprojToFir(limit_correction=3)
        with self.assertRaisesRegex(ValueError, "--limit-low"):
            SwprojToFir(limit_low="wide")

    def test_listening_spot(self):
        mic = ROOT / soundid.MIC_DIR / "FLAT01.swmicpkg"
        project = (
            MdatToSwproj(
                mic_profile=mic, spot_delay_ms=["Right=1"], spot_gain_db=["Left=1.5"]
            )
            .convert([ROOT / rew.MDAT_DIR / "Room.mdat"])
            .data
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "spot.swproj"
            path.write_bytes(project)
            on = fir.read(SwprojToFir(safe_headroom=False).convert([path]).data)
            off = fir.read(
                SwprojToFir(safe_headroom=False, listening_spot=False)
                .convert([path])
                .data
            )
        self.assertEqual(on.taps, off.taps + 47)
        self.assertEqual(on.channels[1][:47], [0.0] * 47)
        # The louder channel is at 0 dB; the other channel is 1.5 dB down.
        self.assertAlmostEqual(on.channels[0][0], off.channels[0][0], places=6)
        self.assertAlmostEqual(
            on.channels[1][47], off.channels[1][0] * 10 ** (-1.5 / 20), places=6
        )


if __name__ == "__main__":
    unittest.main()
