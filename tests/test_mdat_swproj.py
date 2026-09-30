import base64
import gzip
import json
import math
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from eqx.convert import mdat_swproj as convert
from eqx.model import Measurement
from eqx.rew import mdat
from eqx.soundid import layout, swmicpkg, swproj

TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
NS = swproj.NS
GRID = [20.0, 50.0, 61.0, 100.0, 1000.0, 10000.0, 19900.0, 21000.0, 22000.0]
FLAT_MIC = [(20.0, 0.0), (22000.0, 0.0)]


def measurement(level, channel="Left", index=None):
    return Measurement(
        channel=channel,
        index=int(channel == "Right") if index is None else index,
        frequencies=GRID,
        response=[level] * len(GRID),
        group_delay=[0.002] * len(GRID),
    )


def prepare(measurements, profile=FLAT_MIC, **settings):
    return convert.prepare_speaker_curves(measurements, GRID, profile, **settings)[0]


class LevelTests(unittest.TestCase):
    GRID = [200.0 * 1.1 ** i for i in range(40)]  # 200 Hz .. ~8 kHz, all in the level band

    def test_median_ignores_notches(self):
        # Deep notches within the clip budget (2 of 40 points) do not move the level.
        response = [80.0] * 38 + [30.0] * 2
        self.assertEqual(convert.estimate_reference_spl([response], self.GRID), 80.0)

    def test_boost_cap_lowers_level(self):
        # 20 % of the points are 20 dB low: the 5 % quantile is 60 dB, so the
        # level drops to 72 dB, where those points need exactly the cap.
        response = [80.0] * 32 + [60.0] * 8
        self.assertEqual(convert.estimate_reference_spl([response], self.GRID), 72.0)
        self.assertEqual(
            convert.estimate_reference_spl([response], self.GRID, clip_fraction=0.25), 80.0)

    def test_channels_are_pooled(self):
        left, right = [80.0] * 40, [86.0] * 40
        self.assertEqual(convert.estimate_reference_spl([left, right], self.GRID), 83.0)

    def test_level_band(self):
        grid = [100.0, 1000.0, 5000.0, 15000.0]
        self.assertEqual(convert.estimate_reference_spl([[0.0, 80.0, 80.0, 99.0]], grid), 80.0)
        # The level band is limited to the correction band.
        self.assertEqual(convert.estimate_reference_spl(
            [[0.0, 80.0, 90.0, 99.0]], grid, low_cutoff_hz=2000.0), 90.0)


class ProcessingTests(unittest.TestCase):
    def test_estimated_reference(self):
        curves, reference = convert.prepare_speaker_curves([measurement(77.0)], GRID, FLAT_MIC)
        self.assertEqual(reference, 77.0)
        self.assertEqual(curves["Left"].response, [0.0] * len(GRID))
        self.assertEqual(curves["Left"].correction, [0.0] * len(GRID))

    def test_mic_calibration_covers_entire_range_before_normalizing(self):
        # A microphone response of +3 dB brings 102 dB SPL to the reference.
        curves = prepare(
            [measurement(102.0), measurement(105.0, "Right")],
            [(20.0, 3.0), (22000.0, 3.0)],
            reference_spl=99.0,
        )
        self.assertEqual(curves["Left"].response, [0.0] * len(GRID))
        self.assertEqual(curves["Left"].correction, [0.0] * len(GRID))
        # One shared SPL reference preserves the original 3 dB channel offset,
        # including the frequencies outside the speaker correction band.
        self.assertEqual(curves["Right"].response, [3.0] * len(GRID))
        self.assertEqual(curves["Right"].correction[4], -3.0)
        self.assertEqual(curves["Right"].group_delay, [0.002] * len(GRID))

    def test_no_eq_or_phase_correction_outside_band(self):
        curve = prepare([measurement(95.0)], reference_spl=105.0)["Left"]
        self.assertEqual(curve.correction, [0, 0, 0, 10, 10, 10, 0, 0, 0])
        self.assertEqual(curve.correction_group_delay, [0, 0, 0, -0.002, -0.002, -0.002, 0, 0, 0])
        # Check between grid samples as well: the cutoff anchors prevent a
        # ramp towards the first corrected sample from leaking out of band.
        for frequency in (20, 55, 59.999, 60, 20000, 20000.001, 20500, 22000):
            self.assertEqual(convert.interp(GRID, curve.correction, frequency), 0.0)
            self.assertEqual(convert.interp(GRID, curve.correction_group_delay, frequency), 0.0)

    def test_boost_cap_and_custom_settings(self):
        for level, expected in ((42.0, 12.0), (99.0, 6.0), (105.0, 0.0), (125.0, -20.0)):
            with self.subTest(level=level):
                curve = prepare([measurement(level)], reference_spl=105.0)["Left"]
                self.assertEqual(curve.correction[4], expected)
        curve = prepare(
            [measurement(90.0)],
            reference_spl=100.0, max_boost_db=6.0,
            low_cutoff_hz=500.0, high_cutoff_hz=20000.0,
        )["Left"]
        self.assertEqual(curve.response, [-10.0] * len(GRID))
        self.assertEqual(curve.correction[3], 0.0)  # 100 Hz is below the new cutoff.
        self.assertEqual(curve.correction[5], 6.0)

    def test_lfe(self):
        lfe = "Low freq. effects"
        curves, reference = convert.prepare_speaker_curves(
            [measurement(80.0), measurement(60.0, lfe, 3)], GRID, FLAT_MIC,
            lfe=[lfe], lfe_high_cutoff_hz=1000.0)
        # The LFE level is left out of the reference estimate.
        self.assertEqual(reference, 80.0)
        # 100 Hz is corrected; the LFE band ends at 1 kHz.
        self.assertEqual(curves[lfe].correction[3], 12.0)
        self.assertEqual(curves[lfe].correction[4:], [0.0] * 5)
        self.assertEqual(curves["Left"].correction, [0.0] * len(GRID))

    def test_invalid_processing_options(self):
        cases = (
            {"reference_spl": math.nan}, {"reference_spl": math.inf},
            {"low_cutoff_hz": 0}, {"high_cutoff_hz": 50},
            {"high_cutoff_hz": math.inf}, {"max_boost_db": -1}, {"max_boost_db": 12.1},
            {"max_boost_db": math.nan}, {"low_cutoff_hz": 500, "high_cutoff_hz": 1001},
            {"clip_fraction": -0.1}, {"clip_fraction": 1.1}, {"clip_fraction": math.nan},
            {"lfe_high_cutoff_hz": 0}, {"lfe_high_cutoff_hz": math.nan},
            {"lfe": ["Left"], "lfe_high_cutoff_hz": 61},
        )
        for settings in cases:
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                prepare([measurement(105.0)], **settings)


class ProjectTests(unittest.TestCase):
    def test_supplied_measurement_round_trip(self):
        profile = swmicpkg.load(TESTDATA / "soundid/swmicpkg/TILT01.swmicpkg")
        result = convert.convert(mdat.load(TESTDATA / "rew/mdat/Room.mdat"), profile, "Room")
        proj = swproj.SwProj(result.data)
        eqb = proj.eqb
        self.assertEqual(eqb.version, (3, 0, 0, 2))
        self.assertFalse(eqb.encrypted)
        self.assertEqual(eqb.parameters, {})
        self.assertEqual(eqb.trailing, b"")
        curves = eqb.curves
        self.assertEqual([c.curve_type for c in curves], [9, 1, 9, 2])
        self.assertEqual([c.flags for c in curves], [11, 15, 11, 15])

        # Decrypt independently of eqx.soundid.crypto, with the key from the
        # format documentation.
        payload = proj.part("swproj")
        decryptor = Cipher(
            algorithms.AES(bytes.fromhex("3c07ebb62cc0c7860c2614d3f1b942de")),
            modes.CBC(payload[:16]),
        ).decryptor()
        padded = decryptor.update(payload[16:]) + decryptor.finalize()
        pad = padded[-1]
        self.assertTrue(1 <= pad <= 16)
        self.assertEqual(padded[-pad:], bytes([pad]) * pad)
        root = ET.fromstring(gzip.decompress(padded[:-pad]))
        self.assertEqual(root.findtext("s:Name", namespaces=NS), "Room")

        xml_curves = root.findall("s:Curves/s:Curve", NS)
        self.assertEqual(len(xml_curves), 5)
        for binary_curve, xml_curve in zip(curves, xml_curves):
            self.assertEqual(binary_curve.type_name, xml_curve.findtext("s:CurveType", namespaces=NS))
            points = xml_curve.findall("s:Points/l:list/s:AflPoint", NS)
            self.assertEqual(len(points), 355)
            self.assertEqual(len(binary_curve.points), 355)
            for (frequency, gain, delay), point in zip(binary_curve.points, points):
                self.assertAlmostEqual(frequency, float(point.findtext("s:Frequency", namespaces=NS)), delta=0.002)
                self.assertAlmostEqual(gain, float(point.findtext("s:Response", namespaces=NS)), delta=1e-5)
                if binary_curve.curve_type in (1, 2):
                    self.assertAlmostEqual(delay, float(point.findtext("s:GroupDelay", namespaces=NS)), delta=1e-6)
                    self.assertLessEqual(gain, 12.0)
                    if frequency < 60 or frequency > 20000:
                        self.assertEqual((gain, delay), (0.0, 0.0))
            if binary_curve.curve_type in (1, 2):
                self.assertEqual(max(p[1] for p in binary_curve.points), 12.0)

        room = root.find("s:RoomMeasurementCollections/s:RoomMeasurementCollection", NS)
        self.assertIsNotNone(room.find("s:Name", NS))
        raw_data = root.findall(".//s:RawData", NS)
        self.assertEqual(len(raw_data), 2)
        self.assertTrue(all(not item.text for item in raw_data))
        # The stored mic curve is the complete original calibration table.
        self.assertEqual(xml_curves[-1].findtext("s:Name", namespaces=NS), "TILT01 degrees_0")
        mic_points = xml_curves[-1].findall("s:Points/l:list/s:AflPoint", NS)
        self.assertEqual(
            [(float(p.findtext("s:Frequency", namespaces=NS)), float(p.findtext("s:Response", namespaces=NS))) for p in mic_points],
            profile.points,
        )

    def test_multichannel(self):
        target = layout.LAYOUTS[11]
        channels = [(c.name, i) for i, c in enumerate(target.channels)]
        measurements = [measurement(80.0, name, i) for name, i in channels]
        result = convert.convert(measurements, swmicpkg.load(
            TESTDATA / "soundid/swmicpkg/FLAT01.swmicpkg"), "5.1", layout=target)
        proj = swproj.SwProj(result.data)
        self.assertEqual([c.curve_type for c in proj.eqb.curves],
                         [9, 1, 9, 2, 9, 10, 9, 10, 9, 10, 9, 10])
        self.assertEqual([c.parameters["ChannelGroup"] for c in proj.eqb.curves[::2]],
                         ["Front", "Front", "Center", "Sub", "Surround", "Surround"])
        root = proj.tree()
        room = root.find("s:RoomMeasurementCollections/s:RoomMeasurementCollection", NS)
        params = {kv.findtext("a:Key", namespaces=NS): kv.findtext("a:Value", namespaces=NS)
                  for kv in room.findall("s:Parameters/a:KeyValueOfstringstring", NS)}
        self.assertEqual(params["ChannelLayout"], "11")
        self.assertEqual([e.text for e in room.findall(".//s:RoomPoint/s:Channel", NS)],
                         ["Left", "Right", "Other", "Other", "Other", "Other"])
        self.assertEqual(list(swproj.measurement_curves(proj)), [n for n, _ in channels])
        point = room.find("s:Measurements/s:RoomMeasurement", NS)
        config = {kv.findtext("a:Key", namespaces=NS): kv.findtext("a:Value", namespaces=NS)
                  for kv in point.findall("s:Parameters/a:KeyValueOfstringstring", NS)}
        signal = json.loads(base64.b64decode(config["TestSignalConfig"]))
        self.assertEqual(signal["lfeChannelMap"], [False, False, False, True, False, False])
        # The mic table is the only Correction curve without a channel.
        self.assertEqual([p.angle for p in swproj.mic_profiles(proj)], ["degrees_0"])

    def test_layout_checked(self):
        profile = swmicpkg.load(TESTDATA / "soundid/swmicpkg/FLAT01.swmicpkg")
        for measurements, message in (
                ([measurement(80.0, "Center", 2)], r"layout 2.0 \(Stereo\) has no channel 2"),
                ([measurement(80.0, "Center", 1)], "channel 1 of layout .* is Right"),
                ([measurement(80.0), measurement(80.0)], "channel 0 measured twice")):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                convert.convert(measurements, profile, "x")


if __name__ == "__main__":
    unittest.main()
