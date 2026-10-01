"""Sonarworks Reference 3 and 4: projects, legacy PEQb versions, computer IDs."""

import hashlib
import math
import re
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import gen_testdata
from eqx.convert import fir as fir_convert
from eqx.convert import mdat_swproj, to_autoeq
from eqx.rew import mdat
from eqx.soundid import computerid, layout, peqb, playback, swmicpkg, swproj

TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
SONARWORKS_PROJ = TESTDATA / gen_testdata.SONARWORKS_PROJ_DIR
SONARWORKS_PEQB = TESTDATA / gen_testdata.SONARWORKS_PEQB_DIR
NS = swproj.NS
SPOT = gen_testdata.SONARWORKS_REFERENCE_SPOT


def eqb_offset(header: swproj.ProjectHeader, positive_only: bool) -> int:
    """Where a Reference plug-in reads the eqb part.  Sonarworks Reference 3 adds every
    earlier Size, -1 included; Sonarworks Reference 4 only sizes above 0."""
    offset = header.size
    for ptype, size in header.parts:
        if ptype == "eqb":
            return offset
        if size > 0 or not positive_only:
            offset += size
    raise ValueError("no eqb part")


def corrections(p: peqb.Peqb) -> dict:
    return {c.type_name: c for c in p.curves}


class ComputerIdTests(unittest.TestCase):
    CID = computerid.ComputerId(b"000929954", b"E823_8FA6.", b"BOARD", b"1242275901")

    def test_reference3(self):
        want = hashlib.sha1("0009299542\n12422759013\n".encode("utf-16-le")).hexdigest()
        self.assertEqual(self.CID.sonarworks_reference3_value, want)
        self.assertRegex(self.CID.sonarworks_reference3_value, r"^[0-9a-f]{40}$")
        no_volume = computerid.ComputerId(b"0009", b"", b"")
        self.assertEqual(no_volume.sonarworks_reference3_value,
                         hashlib.sha1("00092\n".encode("utf-16-le")).hexdigest())

    def test_reference4_dynamic_disk(self):
        self.assertEqual(self.CID.sonarworks_reference4_value, self.CID.value)
        dynamic = computerid.ComputerId(b"0009", b"", b"BOARD", b"77", dynamic_disk=True)
        self.assertEqual(dynamic.source, b"00092\nBOARD3\n")
        self.assertEqual(dynamic.sonarworks_reference4_value,
                         "g" + hashlib.sha1("00092\n773\n".encode("utf-16-le")).hexdigest())

    def test_values(self):
        values = self.CID.values()
        self.assertEqual(list(values), list(computerid.APPS))
        # Without a disk or board serial only the Sonarworks Reference 3 ID exists.
        self.assertEqual(list(computerid.ComputerId(b"0009", b"", b"", b"1").values()),
                         ["Sonarworks Reference 3"])

    def test_local_ids_are_tried(self):
        blob = (SONARWORKS_PEQB / "Tilt Tilt Wired Average.swhp").read_bytes()
        cid = computerid.ComputerId(b"0009", b"DISK", b"", b"1")
        with mock.patch.object(computerid, "local", return_value=cid):
            p, why = peqb.read_local(blob)
            self.assertFalse(p.decoded)
            self.assertIn(cid.sonarworks_reference3_value, why)
            with mock.patch.object(computerid.ComputerId, "sonarworks_reference3_value",
                                   gen_testdata.SONARWORKS_REFERENCE3_COMPUTER_ID):
                self.assertTrue(peqb.open_decoded(blob).decoded)


class PeqbTests(unittest.TestCase):
    def test_reference3_profile(self):
        blob = (SONARWORKS_PEQB / "Tilt Tilt Wired Average.swhp").read_bytes()
        p = peqb.open_decoded(blob, gen_testdata.SONARWORKS_REFERENCE3_COMPUTER_ID)
        self.assertEqual(len(p.curves), 7)
        self.assertEqual(p.parameters["Headphone_Calibration"], "true")
        with self.assertRaises(ValueError):
            peqb.open_decoded(blob, gen_testdata.COMPUTER_ID)

    def test_legacy_versions(self):
        want = gen_testdata.sonarworks_reference_curves()
        for name, version, ntypes in (("Tilt 2.0.13.30", (2, 0, 13, 30), 2),
                                      ("Tilt 2.0.14.10", (2, 0, 14, 10), 4),
                                      ("Tilt 2.1.3.14", (2, 1, 3, 14), 4),
                                      ("Tilt PEQB", (), 2)):
            with self.subTest(name):
                p = peqb.read((SONARWORKS_PEQB / f"{name}.eqb").read_bytes())
                self.assertEqual((p.version, p.encrypted, p.trailing), (version, False, b""))
                self.assertEqual([c.curve_type for c in p.curves], [1, 2, 3, 4][:ntypes])
                c = corrections(p)
                for side in gen_testdata.SIDES:
                    curve = c[f"Correction{side}"]
                    self.assertAlmostEqual(curve.transfer, SPOT[side][1])
                    self.assertEqual(curve.delay_ms, SPOT[side][0])
                    if version:
                        self.assertEqual(curve.points, want[side][1])
        self.assertEqual(peqb.read((SONARWORKS_PEQB / "Tilt 2.1.3.14.eqb").read_bytes()).parameters,
                         {"META_SonarworksCalibrated": "true"})

    def test_legacy_grid(self):
        p = peqb.read((SONARWORKS_PEQB / "Tilt PEQB.eqb").read_bytes())
        grid = [f for f, _, _ in p.curves[0].points]
        self.assertEqual(len(grid), 355)
        self.assertAlmostEqual(grid[0], 20 + 21980 / 357 ** 2)
        self.assertAlmostEqual(grid[-1], 20 + 21980 * (355 / 357) ** 2)
        self.assertEqual(peqb.Peqb((), False, 4, None, 0).version_str, "PEQB")

    def test_write_v1_versions(self):
        curves = [peqb.Curve(1, [(20.0, 1.0, 0.0)], transfer=-1.0, delay_ms=2.0)]
        v0 = peqb.write_v1(curves, version=(3, 0, 0, 0))
        self.assertEqual(v0[:8], b"PEQb\x03\x00\x00\x00")
        self.assertEqual(peqb.write_v1(curves)[:9], b"PEQb\x03\x00\x00\x01\x00")
        p = peqb.read(v0)
        self.assertEqual((p.header_size, p.trailing), (8, b""))
        self.assertEqual((p.curves[0].transfer, p.curves[0].delay_ms), (-1.0, 2.0))
        with self.assertRaises(ValueError):
            peqb.write_v1(curves, version=(3, 0, 0, 2))


class ProjectReaderTests(unittest.TestCase):
    def test_reference4_measure(self):
        p = swproj.SwProj.open(SONARWORKS_PROJ / "Tilt Sonarworks Reference 4.swproj")
        self.assertEqual(p.header.version, "3.0.0.0")
        with self.assertRaisesRegex(ET.ParseError, "unbound prefix"):
            ET.fromstring(p.xml)
        curves = swproj.measurement_curves(p)
        want = gen_testdata.sonarworks_reference_curves()
        self.assertEqual(curves, {side: want[side][0] for side in gen_testdata.SIDES})
        transfers = {swproj._curve_params(c).get("Transfer")
                     for c in p.tree().findall("s:Curves/s:Curve", NS)}
        self.assertEqual(transfers, {None, "-0.500000", "0.000000"})
        self.assertEqual(swproj.mic_profiles(p), [])

    def test_reference3(self):
        p = swproj.SwProj.open(SONARWORKS_PROJ / "Tilt Sonarworks Reference 3.swproj")
        self.assertEqual([t for t, _ in p.header.parts], ["eqb", "swproj"])
        self.assertEqual(p.eqb.version, (3, 0, 0, 1))
        self.assertTrue(p.eqb.trailing and not p.eqb.trailing.strip(b"\0"))
        self.assertEqual(set(swproj.measurement_curves(p)), {"Left", "Right"})

    def test_playback_channels(self):
        for name in ("Tilt Sonarworks Reference 3", "Tilt Sonarworks Reference 4", "Bandpass"):
            with self.subTest(name):
                channels = playback.speaker_channels(swproj.SwProj.open(SONARWORKS_PROJ / f"{name}.swproj"))
                self.assertEqual([(c.index, c.name, c.group) for c in channels],
                                 [(0, "Left", "Front"), (1, "Right", "Front")])
                self.assertEqual([(c.delay_ms, c.gain_db) for c in channels],
                                 [(0.0, -0.5), (0.15, 0.0)])
                for c in channels:
                    self.assertEqual(len(c.measurement), 355)

    def test_missing_measurement_is_negated_correction(self):
        p = peqb.read((SONARWORKS_PEQB / "Tilt 2.0.13.30.eqb").read_bytes())
        left = playback.sonarworks_reference_channels(p.curves)[0]
        self.assertEqual(left.measurement, [(f, -r) for f, r in left.correction])
        with self.assertRaises(ValueError):
            playback.sonarworks_reference_channels(p.curves[:1])

    def test_fir_and_autoeq(self):
        path = SONARWORKS_PROJ / "Tilt Sonarworks Reference 4.swproj"
        result = fir_convert.SwprojToFir().convert(path)
        self.assertIn("Right listening spot: 6 samples, +0.00 dB", result.notes)
        result = to_autoeq.SwprojToAutoeq(channel="right").convert(path)
        self.assertEqual(result.name, "Tilt Sonarworks Reference 4 Right.csv")


class ProjectWriterTests(unittest.TestCase):
    def setUp(self):
        self.project = swproj.SwProj.open(SONARWORKS_PROJ / "Bandpass.swproj")

    def test_header(self):
        h = self.project.header
        self.assertEqual((h.version, h.supported_versions), ("3.0.0.0", []))
        self.assertEqual([t for t, _ in h.parts], ["eqb", "swproj"])
        for positive_only in (False, True):
            offset = eqb_offset(h, positive_only)
            self.assertEqual(self.project.blob[offset:offset + 8], b"PEQb\x03\x00\x00\x00")

    def test_soundid_header_breaks_reference3(self):
        h = swproj.SwProj.open(TESTDATA / "soundid/swproj/Bandpass.swproj").header
        offset = eqb_offset(h, positive_only=False)
        self.assertNotEqual(self.project.blob[offset:offset + 4], b"PEQb")
        self.assertEqual(h.size - 1, offset)

    def test_eqb(self):
        eqb = self.project.eqb
        self.assertEqual([c.curve_type for c in eqb.curves], [1, 2, 3, 4])
        c = corrections(eqb)
        self.assertEqual((c["CorrectionLeft"].transfer, c["CorrectionLeft"].delay_ms), (-0.5, 0.0))
        self.assertEqual((c["CorrectionRight"].transfer, c["CorrectionRight"].delay_ms), (0.0, 0.15))
        xml = {cv.findtext("s:CurveType", namespaces=NS): (swproj._curve_params(cv),
                                                            swproj._points(cv.find("s:Points", NS)))
               for cv in self.project.tree().findall("s:Curves/s:Curve", NS)}
        for name, curve in c.items():
            self.assertEqual(curve.points, xml[name][1])
        self.assertEqual(xml["CorrectionRight"][0],
                         {"Delay": "0.00015", "Frequency": mdat_swproj.GRID_NAME, "Transfer": "0.0"})
        self.assertNotIn("ChannelName", xml["MeasurementLeft"][0])

    def test_xml(self):
        raw = self.project.xml.decode()
        for absent in ("ChannelLayout", "VersionHistory", "TestSignalConfig", "ChannelIndex",
                       "<CurveType>Measurement<"):
            self.assertNotIn(absent, raw)
        self.assertEqual(len(re.findall("<CurveType>Measurement(Left|Right)<", raw)), 2)

    def test_stereo_only(self):
        measurements = mdat.load(TESTDATA / "rew/mdat/Left only.mdat")
        profile = swmicpkg.load(TESTDATA / "soundid/swmicpkg/FLAT01.swmicpkg")
        with self.assertRaisesRegex(ValueError, "stereo"):
            mdat_swproj.convert(measurements, profile, "x", app="sonarworks-reference")
        both = mdat.load(TESTDATA / "rew/mdat/Flat.mdat")
        with self.assertRaisesRegex(ValueError, "stereo"):
            mdat_swproj.check_app(both, layout.LAYOUTS[11], "sonarworks-reference")
        mdat_swproj.check_app(both, layout.STEREO, "sonarworks-reference")
        with self.assertRaises(ValueError):
            mdat_swproj.MdatToSwproj(mic_profile=Path("x"), app="reference")

    def test_same_correction_as_soundid(self):
        soundid = swproj.SwProj.open(TESTDATA / "soundid/swproj/Bandpass.swproj")
        ref = {c.name: c.correction for c in playback.speaker_channels(self.project)}
        for c in playback.speaker_channels(soundid):
            for (f, a), (_, b) in zip(c.correction, ref[c.name]):
                self.assertTrue(math.isclose(a, b, abs_tol=1e-5), (c.name, f, a, b))


if __name__ == "__main__":
    unittest.main()
