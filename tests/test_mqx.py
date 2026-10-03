import base64
import json
import math
import tempfile
import unittest
from pathlib import Path

from helpers import assert_filters
from testgen import audyssey, common, rew, soundid

from eqx import impulse
from eqx.audyssey import mqx
from eqx.autoeq import response
from eqx.convert import to_mqx, to_swproj
from eqx.convert.common import mic_response_db, profile_db
from eqx.convert.to_autoeq import MqxToAutoeq
from eqx.convert.to_mqx import AutoeqToMqx
from eqx.curve import resample
from eqx.soundid import swproj

MQX_DIR = common.ROOT / audyssey.MQX_DIR
PROJECT = MQX_DIR / "Mqx 5.1.mqx"
MIC = common.ROOT / soundid.MIC_DIR / "FLAT01.swmicpkg"
MIC_RESPONSE = common.ROOT / rew.CAL_DIR / "TILT01 degrees_0.txt"
FLAT_RESPONSE = common.ROOT / rew.CAL_DIR / "FLAT01 degrees_0.txt"
CSV = common.ROOT / common.CSV_DIR


def mic_db(profile, frequency: float) -> float:
    return resample(
        [f for f, _ in profile.points], [g for _, g in profile.points], [frequency]
    )[0]


def _json(path=PROJECT) -> dict:
    return json.loads(path.read_bytes())


class ReaderTests(unittest.TestCase):
    def test_project(self):
        m = mqx.load(PROJECT)
        self.assertEqual(
            [c.designation for c in m.channels], ["FL", "FR", "C", "SLA", "SRA", "SW1"]
        )
        self.assertEqual(m.channels[2].name, "Center")
        self.assertEqual((len(m.positions), len(m.recordings), m.trailing), (3, 18, 0))
        self.assertEqual({len(r.ir) for r in m.recordings}, {mqx.IR_LENGTH})
        disabled = [
            (m.designation(r.channel), m.positions.index(r.position))
            for r in m.recordings
            if not r.enabled
        ]
        self.assertEqual(disabled, [audyssey.MQX_DISABLED])
        for name, index in (
            ("FL", 0),
            ("front right", 1),
            ("Left", 0),
            ("RIGHT", 1),
            ("Subwoofer 1", 5),
        ):
            self.assertEqual(m.channel(name), index)
        with self.assertRaisesRegex(ValueError, "no SBL speaker; available: FL, FR, C"):
            m.channel("SBL")

    def test_delay(self):
        m = mqx.load(PROJECT)
        for r in m.recordings:
            designation = m.designation(r.channel)
            if designation != "SW1":
                self.assertAlmostEqual(
                    mqx.delay_ms(r), audyssey.MQX_FLIGHT[designation] / 48, places=9
                )

    def test_targets(self):
        targets = mqx.load(PROJECT).targets
        self.assertEqual(
            [t.kind for t in targets], ["base", "modifier", "biquad", "tilt", "custom"]
        )
        self.assertEqual(
            [t.describe() for t in targets],
            [
                "Theater High Frequency Rolloff 1",
                "Midrange Compensation",
                "2nd order Low Shelf w/ Q 60 Hz, 3 dB, Q 0.7",
                "tilt -0.5 dB/octave at 1000 Hz",
                "custom curve, 4 points",
            ],
        )
        self.assertEqual((targets[0].reference, targets[0].flat), (True, False))
        self.assertEqual(
            targets[2].channels, [mqx.channel_guid("FL"), mqx.channel_guid("FR")]
        )
        self.assertEqual(targets[3].excluded, [mqx.channel_guid("SW1")])
        points = targets[4].points()
        self.assertEqual(points[1], (1000.0, 0.0))
        self.assertAlmostEqual(points[2][1], 20 * math.log10(0.5))

    def test_trailing_data(self):
        # MultEQ-X overwrites a longer project without truncating the file.
        tail = b'}\r\n  "PersistantProjectDatas": {}\r\n}'
        m = mqx.read(PROJECT.read_bytes() + tail)
        self.assertEqual(m.trailing, len(tail.strip()))
        self.assertEqual(len(m.recordings), 18)

    def test_old_project(self):
        # Projects of older versions lack the newer keys.
        data = _json()
        for key in ("OrderedChannelGuids", "TargetCurveSet", "CalibrationSettings"):
            del data[key]
        m = mqx.read(json.dumps(data).encode("utf-8-sig"))
        self.assertEqual({c.designation for c in m.channels}, set(audyssey.MQX_FLIGHT))
        self.assertEqual(m.targets, [])

    def test_rejected(self):
        data = _json()
        bad_data = json.loads(json.dumps(data))
        bad_data["_measurements"][0]["Data"] = "not base64!"
        odd = json.loads(json.dumps(data))
        odd["_measurements"][0]["Data"] = base64.b64encode(b"\0" * 6).decode()
        item = json.loads(json.dumps(data))
        item["TargetCurveSet"][0]["_itemString"] = "x"
        for bad, message in (
            (b"\xff\xfe", "not UTF-8"),
            (b"[1]", "no _measurements or _channelDataMap"),
            (b'{"_measurements": [', "not a MultEQ-X project"),
            (json.dumps(bad_data).encode(), "Data is not base64"),
            (json.dumps(odd).encode(), "6 bytes of Data"),
            (json.dumps(item).encode(), "cannot parse _itemString 'x'"),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                mqx.read(bad)


class ResponseTests(unittest.TestCase):
    def assert_analytic(
        self, m, designation, gains, low, high, position=None, delta=0.1
    ):
        assert_filters(
            self,
            mqx.response(m, m.channel(designation), position),
            audyssey.mqx_filters(designation),
            mqx.SAMPLE_RATE,
            gains,
            low,
            high,
            delta,
            designation,
        )

    def test_generated(self):
        m = mqx.load(PROJECT)
        gains = audyssey.MQX_POSITION_GAINS
        for designation in ("FL", "FR", "SLA"):
            with self.subTest(designation):
                self.assert_analytic(m, designation, gains, 30, 16000)
                self.assert_analytic(m, designation, [gains[2]], 30, 16000, position=2)
        # The disabled measurement is left out of the average, not of --position.
        self.assert_analytic(m, "C", gains[:2], 30, 16000)
        self.assert_analytic(m, "C", [gains[2]], 30, 16000, position=2)
        self.assert_analytic(m, "SW1", gains, 30, 2000, delta=0.2)

    def test_positions(self):
        m = mqx.load(PROJECT)
        with self.assertRaisesRegex(
            ValueError, "position 3 does not exist; positions: 0-2"
        ):
            mqx.response(m, 0, 3)
        data = _json()
        data["_measurements"] = [
            x
            for x in data["_measurements"]
            if x["ChannelGuid"] != mqx.channel_guid("FR")
            or x["PositionGuid"] == mqx.guid("position", "0")
        ]
        for x in data["_measurements"]:
            x["Enabled"] = x["ChannelGuid"] != mqx.channel_guid("FR")
        m = mqx.read(json.dumps(data).encode())
        with self.assertRaisesRegex(ValueError, "FR has no measurement at position 1"):
            mqx.response(m, 1, 1)
        with self.assertRaisesRegex(ValueError, "FR has no enabled measurement"):
            mqx.response(m, 1)


class SwprojTests(unittest.TestCase):
    def test_surround(self):
        target, measurements = to_swproj.MqxToSwproj(mic_profile=MIC).measurements(
            PROJECT
        )
        self.assertEqual(target.name, "5.1")
        self.assertEqual(
            [(m.index, m.channel) for m in measurements],
            list(
                enumerate(
                    [
                        "Left",
                        "Right",
                        "Center",
                        "Low freq. effects",
                        "Left Surround",
                        "Right Surround",
                    ]
                )
            ),
        )
        self.assertEqual(measurements[3].name, "SW1 Mqx 5.1")

    def test_layouts(self):
        self.assertEqual(to_swproj.mqx_layout(["FL", "FR"]).name, "2.0 (Stereo)")
        self.assertEqual(
            to_swproj.mqx_layout(
                [
                    "FL",
                    "C",
                    "FR",
                    "SW1",
                    "SLA",
                    "SRA",
                    "SBL",
                    "SBR",
                    "FHL",
                    "FHR",
                    "RHL",
                    "RHR",
                ]
            ).name,
            "7.1.4",
        )
        for designations, message in (
            (["FL", "FR", "CH"], "no SoundID channel for CH"),
            (["FL", "FR", "FHL", "TFL"], "same SoundID channel twice"),
            (["FL", "FR", "SB"], "no SoundID layout"),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                to_swproj.mqx_layout(designations)

    def test_disabled_channel(self):
        data = _json()
        data["_channelDataMap"][mqx.channel_guid("SW1")]["Calibration"]["IsEnabled"] = (
            False
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mqx"
            path.write_bytes(json.dumps(data).encode())
            target, _ = to_swproj.MqxToSwproj(mic_profile=MIC).measurements(path)
        self.assertEqual(target.name, "5.0")

    def test_default_mic_profile(self):
        acm = mqx.generic_mic()
        self.assertEqual(
            (acm.name, acm.angle, len(acm.points)), ("ACM1HB", "degrees_90", 1239)
        )
        self.assertAlmostEqual(mic_db(acm, 18114.3418), -7.366)
        flat = to_swproj.MqxToSwproj(mic_profile=MIC).convert([PROJECT])
        default = to_swproj.MqxToSwproj().convert([PROJECT])
        self.assertIn("mic table: ACM1HB degrees_90 (default)", default.notes[0])
        flat_curves = swproj.measurement_curves(swproj.SwProj(flat.data))
        default_curves = swproj.measurement_curves(swproj.SwProj(default.data))
        # The same reference level would leave the difference equal to the
        # ACM1HB table; normalisation shifts it by one offset per project.
        offsets = [
            d[1] - f[1] + mic_db(acm, f[0])
            for channel in flat_curves
            for f, d in zip(flat_curves[channel], default_curves[channel])
            if 20 <= f[0] <= 20000
        ]
        self.assertLess(max(offsets) - min(offsets), 0.01)

    def test_default_mic_profile_other_mic(self):
        data = _json()
        data["_measurements"][0]["MicCorrectionName"] = "ACM1X"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mqx"
            path.write_bytes(json.dumps(data).encode())
            with self.assertRaisesRegex(ValueError, "ACM1X.*give its --mic-profile"):
                to_swproj.MqxToSwproj().convert([path])


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        m = mqx.load(PROJECT)
        channels = [
            (c.designation, [r.ir for r in m.recordings if r.channel == c.guid])
            for c in m.channels
        ]
        data = mqx.write(channels[::-1], m.targets)
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        b = mqx.read(data)
        self.assertEqual(
            [c.designation for c in b.channels], [c.designation for c in m.channels]
        )
        self.assertEqual(
            [(r.channel, r.position, r.ir) for r in b.recordings],
            [(r.channel, r.position, r.ir) for r in m.recordings],
        )
        self.assertEqual(b.targets, m.targets)
        sw1 = b.channels[5].data
        self.assertEqual(
            (sw1["Calibration"]["SpeakerSize"], sw1["TargetCurveCutoff"]["Mode"]),
            ("Subwoofer", "Disabled"),
        )
        self.assertEqual(b.channels[0].data["Calibration"]["SpeakerSize"], "Small")
        self.assertAlmostEqual(
            b.channels[0].data["Calibration"]["DistanceMilliseconds"], 8.75
        )

    def test_rejected(self):
        ir = [0.0] * mqx.IR_LENGTH
        for channels, message in (
            ([("FL", [ir]), ("FR", [ir]), ("SBL", [ir])], "of: FL, FR"),
            ([("FL", [ir]), ("FL", [ir])], "distinct"),
            ([("FL", [ir]), ("C", [ir])], "needs the FL and FR"),
            ([("FL", [ir]), ("FR", [ir, ir])], "same number of positions"),
            ([("FL", [ir]), ("FR", [ir[1:]])], "16384 samples"),
        ):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                mqx.write(channels)

    def test_from_autoeq(self):
        left, right = CSV / "Bandpass Left.csv", CSV / "Bandpass Right.csv"
        result = AutoeqToMqx(mic_response=FLAT_RESPONSE).convert([left, right])
        self.assertEqual(result.name, "Bandpass Left.mqx")
        m = mqx.read(result.data)
        self.assertEqual(
            ([c.designation for c in m.channels], len(m.positions)), (["FL", "FR"], 1)
        )
        self.assertEqual(m.channels[0].data["Calibration"]["SpeakerSize"], "Large")
        lead = mqx.SYSTEM_DELAY + to_mqx.FLIGHT
        self.assertEqual(impulse.peak_index(m.recordings[0].ir), lead)
        self.assertEqual(m.recordings[0].ir[:lead], [0.0] * lead)
        self.assertEqual(m.targets, list(mqx.DEFAULT_TARGETS))
        offsets = []
        for c, path in enumerate((left, right)):
            points = [
                (f, v) for f, v in response.load(path).curve() if 30 <= f <= 16000
            ]
            _, db, _ = mqx.response(m, c, frequencies=[f for f, _ in points])
            offsets.append(db[0] - points[0][1])
            for (f, v), got in zip(points, db):
                self.assertAlmostEqual(got - v, offsets[0], delta=0.2, msg=f"{f} Hz")
        one = mqx.read(AutoeqToMqx(mic_response=FLAT_RESPONSE).convert([left]).data)
        self.assertEqual(one.recordings[0].ir, one.recordings[1].ir)

    def test_mic_response(self):
        # Written with the mic response added; subtracted again, the curve remains.
        path = MQX_DIR / "Room Left.mqx"
        m = mqx.load(path)
        plain = mqx.read(
            AutoeqToMqx(mic_response=FLAT_RESPONSE)
            .convert([CSV / "Room Left.csv", CSV / "Room Right.csv"])
            .data
        )
        frequencies = [f for f in impulse.log_grid(mqx.SAMPLE_RATE) if 30 <= f <= 16000]
        mic = mic_response_db(MIC_RESPONSE, frequencies)
        self.assertGreater(max(mic) - min(mic), 3)
        _, raw, _ = mqx.response(m, 0, frequencies=frequencies)
        _, want, _ = mqx.response(plain, 0, frequencies=frequencies)
        offset = raw[0] - mic[0] - want[0]
        for f, r, g, w in zip(frequencies, raw, mic, want):
            self.assertAlmostEqual(r - g - w, offset, delta=0.1, msg=f"{f} Hz")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.csv"
            out.write_bytes(MqxToAutoeq(mic_response=MIC_RESPONSE).convert([path]).data)
            got = response.load(out).curve()
        grid, raw, _ = mqx.response(m, 0)
        for (f, v), r, g in zip(got, raw, mic_response_db(MIC_RESPONSE, grid)):
            self.assertAlmostEqual(v, r - g, delta=0.006)

    def test_default_mic_response(self):
        # Without --mic-response the generic ACM1HB is subtracted.
        m = mqx.load(PROJECT)
        grid, raw, _ = mqx.response(m, 0)
        result = MqxToAutoeq().convert([PROJECT])
        self.assertIn("minus the generic ACM1HB", result.notes[1])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.csv"
            out.write_bytes(result.data)
            got = response.load(out).curve()
        for (f, v), r, g in zip(got, raw, profile_db(mqx.generic_mic(), grid)):
            self.assertAlmostEqual(v, r - g, delta=0.006)
        # Without --mic-response the generic ACM1HB is added.
        csv = CSV / "Bandpass Left.csv"
        frequencies = [f for f in impulse.log_grid(mqx.SAMPLE_RATE) if 30 <= f <= 16000]
        _, flat, _ = mqx.response(
            mqx.read(AutoeqToMqx(mic_response=FLAT_RESPONSE).convert([csv]).data),
            0,
            frequencies=frequencies,
        )
        _, default, _ = mqx.response(
            mqx.read(AutoeqToMqx().convert([csv]).data), 0, frequencies=frequencies
        )
        mic = profile_db(mqx.generic_mic(), frequencies)
        offset = default[0] - flat[0] - mic[0]
        # Up to 5 kHz: above it the response's band averaging smooths the
        # jagged table.
        for f, d, w, g in zip(frequencies, default, flat, mic):
            if f <= 5000:
                self.assertAlmostEqual(d - w - g, offset, delta=0.2, msg=f"{f} Hz")
        data = _json()
        data["_measurements"][0]["MicCorrectionName"] = "ACM1X"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mqx"
            path.write_bytes(json.dumps(data).encode())
            with self.assertRaisesRegex(ValueError, "ACM1X.*give its --mic-response"):
                MqxToAutoeq().convert([path])


if __name__ == "__main__":
    unittest.main()
