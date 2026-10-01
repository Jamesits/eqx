import base64
import json
import math
import tempfile
import unittest
from pathlib import Path

import gen_testdata
from eqx import dsp
from eqx.audyssey import mqx
from eqx.autoeq import response
from eqx.convert import mqx_swproj
from eqx.convert.from_autoeq import MQX_FLIGHT, AutoeqToMqx
from eqx.convert.mdat_swproj import mic_response_db
from eqx.convert.to_autoeq import MqxToAutoeq
from eqx.ik import arcx

MQX_DIR = gen_testdata.ROOT / gen_testdata.MQX_DIR
PROJECT = MQX_DIR / "Mqx 5.1.mqx"
MIC = gen_testdata.ROOT / gen_testdata.MIC_DIR / "FLAT01.swmicpkg"
MIC_RESPONSE = gen_testdata.ROOT / gen_testdata.CAL_DIR / "TILT01 degrees_0.txt"
CSV = gen_testdata.ROOT / gen_testdata.CSV_DIR


def _json(path=PROJECT) -> dict:
    return json.loads(path.read_bytes())


class ReaderTests(unittest.TestCase):
    def test_project(self):
        m = mqx.load(PROJECT)
        self.assertEqual([c.designation for c in m.channels], ["FL", "FR", "C", "SLA", "SRA",
                                                               "SW1"])
        self.assertEqual(m.channels[2].name, "Center")
        self.assertEqual((len(m.positions), len(m.recordings), m.trailing), (3, 18, 0))
        self.assertEqual({len(r.ir) for r in m.recordings}, {mqx.IR_LENGTH})
        disabled = [(m.designation(r.channel), m.positions.index(r.position))
                    for r in m.recordings if not r.enabled]
        self.assertEqual(disabled, [gen_testdata.MQX_DISABLED])
        for name, index in (("FL", 0), ("front right", 1), ("Left", 0), ("RIGHT", 1),
                            ("Subwoofer 1", 5)):
            self.assertEqual(m.channel(name), index)
        with self.assertRaisesRegex(ValueError, "no SBL speaker; available: FL, FR, C"):
            m.channel("SBL")

    def test_delay(self):
        m = mqx.load(PROJECT)
        for r in m.recordings:
            designation = m.designation(r.channel)
            if designation != "SW1":
                self.assertAlmostEqual(mqx.delay_ms(r),
                                       gen_testdata.MQX_FLIGHT[designation] / 48, places=9)

    def test_targets(self):
        targets = mqx.load(PROJECT).targets
        self.assertEqual([t.kind for t in targets], ["base", "modifier", "biquad", "tilt",
                                                     "custom"])
        self.assertEqual([t.describe() for t in targets], [
            "Theater High Frequency Rolloff 1", "Midrange Compensation",
            "2nd order Low Shelf w/ Q 60 Hz, 3 dB, Q 0.7", "tilt -0.5 dB/octave at 1000 Hz",
            "custom curve, 4 points"])
        self.assertEqual((targets[0].reference, targets[0].flat), (True, False))
        self.assertEqual(targets[2].channels, [mqx.channel_guid("FL"), mqx.channel_guid("FR")])
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
        self.assertEqual({c.designation for c in m.channels}, set(gen_testdata.MQX_FLIGHT))
        self.assertEqual(m.targets, [])

    def test_rejected(self):
        data = _json()
        bad_data = json.loads(json.dumps(data))
        bad_data["_measurements"][0]["Data"] = "not base64!"
        odd = json.loads(json.dumps(data))
        odd["_measurements"][0]["Data"] = base64.b64encode(b"\0" * 6).decode()
        item = json.loads(json.dumps(data))
        item["TargetCurveSet"][0]["_itemString"] = "x"
        for bad, message in ((b"\xff\xfe", "not UTF-8"),
                             (b"[1]", "no _measurements or _channelDataMap"),
                             (b'{"_measurements": [', "not a MultEQ-X project"),
                             (json.dumps(bad_data).encode(), "Data is not base64"),
                             (json.dumps(odd).encode(), "6 bytes of Data"),
                             (json.dumps(item).encode(), "cannot parse _itemString 'x'")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                mqx.read(bad)


class ResponseTests(unittest.TestCase):
    def assert_analytic(self, m, designation, gains, low, high, position=None, delta=0.1):
        c = m.channel(designation)
        mean = 10 * math.log10(sum(10 ** (g / 10) for g in gains) / len(gains))
        filters = gen_testdata.mqx_filters(designation)
        for f, db, _ in zip(*mqx.response(m, c, position)):
            if low <= f <= high:
                self.assertAlmostEqual(db, dsp.cascade_db(filters, f, mqx.SAMPLE_RATE) + mean,
                                       delta=delta, msg=f"{designation} {f:g} Hz")

    def test_generated(self):
        m = mqx.load(PROJECT)
        gains = gen_testdata.MQX_POSITION_GAINS
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
        with self.assertRaisesRegex(ValueError, "position 3 does not exist; positions: 0-2"):
            mqx.response(m, 0, 3)
        data = _json()
        data["_measurements"] = [x for x in data["_measurements"]
                                 if x["ChannelGuid"] != mqx.channel_guid("FR")
                                 or x["PositionGuid"] == mqx.guid("position", "0")]
        for x in data["_measurements"]:
            x["Enabled"] = x["ChannelGuid"] != mqx.channel_guid("FR")
        m = mqx.read(json.dumps(data).encode())
        with self.assertRaisesRegex(ValueError, "FR has no measurement at position 1"):
            mqx.response(m, 1, 1)
        with self.assertRaisesRegex(ValueError, "FR has no enabled measurement"):
            mqx.response(m, 1)


class SwprojTests(unittest.TestCase):
    def test_surround(self):
        target, measurements = mqx_swproj.MqxToSwproj(mic_profile=MIC).measurements(PROJECT)
        self.assertEqual(target.name, "5.1")
        self.assertEqual([(m.index, m.channel) for m in measurements],
                         list(enumerate(["Left", "Right", "Center", "Low freq. effects",
                                         "Left Surround", "Right Surround"])))
        self.assertEqual(measurements[3].name, "SW1 Mqx 5.1")

    def test_layouts(self):
        self.assertEqual(mqx_swproj.soundid_layout(["FL", "FR"]).name, "2.0 (Stereo)")
        self.assertEqual(mqx_swproj.soundid_layout(
            ["FL", "C", "FR", "SW1", "SLA", "SRA", "SBL", "SBR", "FHL", "FHR", "RHL", "RHR"]).name,
            "7.1.4")
        for designations, message in ((["FL", "FR", "CH"], "no SoundID channel for CH"),
                                      (["FL", "FR", "FHL", "TFL"], "same SoundID channel twice"),
                                      (["FL", "FR", "SB"], "no SoundID layout")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                mqx_swproj.soundid_layout(designations)

    def test_disabled_channel(self):
        data = _json()
        data["_channelDataMap"][mqx.channel_guid("SW1")]["Calibration"]["IsEnabled"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.mqx"
            path.write_bytes(json.dumps(data).encode())
            target, _ = mqx_swproj.MqxToSwproj(mic_profile=MIC).measurements(path)
        self.assertEqual(target.name, "5.0")


class WriterTests(unittest.TestCase):
    def test_round_trip(self):
        m = mqx.load(PROJECT)
        channels = [(c.designation, [r.ir for r in m.recordings if r.channel == c.guid])
                    for c in m.channels]
        data = mqx.write(channels[::-1], m.targets)
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        b = mqx.read(data)
        self.assertEqual([c.designation for c in b.channels], [c.designation for c in m.channels])
        self.assertEqual([(r.channel, r.position, r.ir) for r in b.recordings],
                         [(r.channel, r.position, r.ir) for r in m.recordings])
        self.assertEqual(b.targets, m.targets)
        sw1 = b.channels[5].data
        self.assertEqual((sw1["Calibration"]["SpeakerSize"], sw1["TargetCurveCutoff"]["Mode"]),
                         ("Subwoofer", "Disabled"))
        self.assertEqual(b.channels[0].data["Calibration"]["SpeakerSize"], "Small")
        self.assertAlmostEqual(b.channels[0].data["Calibration"]["DistanceMilliseconds"], 8.75)

    def test_rejected(self):
        ir = [0.0] * mqx.IR_LENGTH
        for channels, message in (([("FL", [ir]), ("FR", [ir]), ("SBL", [ir])], "of: FL, FR"),
                                  ([("FL", [ir]), ("FL", [ir])], "distinct"),
                                  ([("FL", [ir]), ("C", [ir])], "needs the FL and FR"),
                                  ([("FL", [ir]), ("FR", [ir, ir])], "same number of positions"),
                                  ([("FL", [ir]), ("FR", [ir[1:]])], "16384 samples")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                mqx.write(channels)

    def test_from_autoeq(self):
        left, right = CSV / "Bandpass Left.csv", CSV / "Bandpass Right.csv"
        result = AutoeqToMqx().convert([left, right])
        self.assertEqual(result.name, "Bandpass Left.mqx")
        m = mqx.read(result.data)
        self.assertEqual(([c.designation for c in m.channels], len(m.positions)),
                         (["FL", "FR"], 1))
        self.assertEqual(m.channels[0].data["Calibration"]["SpeakerSize"], "Large")
        lead = mqx.SYSTEM_DELAY + MQX_FLIGHT
        self.assertEqual(arcx.peak_index(m.recordings[0].ir), lead)
        self.assertEqual(m.recordings[0].ir[:lead], [0.0] * lead)
        self.assertEqual(m.targets, list(mqx.DEFAULT_TARGETS))
        offsets = []
        for c, path in enumerate((left, right)):
            points = [(f, v) for f, v in response.load(path).curve() if 30 <= f <= 16000]
            _, db, _ = mqx.response(m, c, frequencies=[f for f, _ in points])
            offsets.append(db[0] - points[0][1])
            for (f, v), got in zip(points, db):
                self.assertAlmostEqual(got - v, offsets[0], delta=0.2, msg=f"{f} Hz")
        one = mqx.read(AutoeqToMqx().convert([left]).data)
        self.assertEqual(one.recordings[0].ir, one.recordings[1].ir)

    def test_mic_response(self):
        # Written with the mic response added; subtracted again, the curve remains.
        path = MQX_DIR / "Room Left.mqx"
        m = mqx.load(path)
        plain = mqx.read(AutoeqToMqx().convert([CSV / "Room Left.csv",
                                                CSV / "Room Right.csv"]).data)
        frequencies = [f for f in arcx.log_grid(mqx.SAMPLE_RATE) if 30 <= f <= 16000]
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

if __name__ == "__main__":
    unittest.main()
