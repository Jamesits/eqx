import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import gen_testdata
from eqx import dsp, formats
from eqx.convert import to_autoeq
from eqx.model import Correction, Peq
from eqx.rme import tmreq
from eqx.soundid import (export_biquad_json, export_biquad_xml, export_lvnd, export_partners,
                         export_peq_json, export_txt)

ROOT = gen_testdata.ROOT
BIQUAD_JSON, PEQ_JSON, BIQUAD_XML = export_biquad_json.ID, export_peq_json.ID, export_biquad_xml.ID
LVND, TXT = export_lvnd.FORMAT.id, export_txt.FORMAT.id
FILES = {
    BIQUAD_JSON: [ROOT / gen_testdata.BIQUAD_JSON_DIR / n for n in ("Tilt Fluid.bin",
                                                                   "Tilt MERGING.bin")],
    PEQ_JSON: [ROOT / gen_testdata.PEQ_JSON_DIR / n for n in ("Tilt Grace.bin", "Tilt Lynx.bin")],
    BIQUAD_XML: [ROOT / gen_testdata.BIQUAD_XML_DIR / "Tilt.adam"],
    LVND: [ROOT / gen_testdata.LVND_DIR / n for n in ("Tilt_192000_Left.bin",
                                                     "Tilt_192000_Right.bin")],
    TXT: [ROOT / gen_testdata.EXPORT_TXT_DIR / n for n in ("Tilt - Flat.txt",
                                                          "Tilt - Flat - 8 - PEQ.txt")],
    "tmreq": [ROOT / gen_testdata.TMREQ_DIR / "Tilt - Flat.tmreq"],
}
FREQUENCIES = [20, 60, 250, 1000, 4000, 8000, 16000]
REAL = ROOT / "real"


def analytic(side: str, rate: float = dsp.PEQ_SAMPLE_RATE) -> list[float]:
    return [dsp.cascade_db(gen_testdata.bells(side, rate), f, rate) for f in FREQUENCIES]


class ContainerTests(unittest.TestCase):
    def test_adam_key_is_hashed(self):
        adam = next(p for p in export_partners.PARTNERS if p.id == "adam")
        self.assertEqual(adam.key, hashlib.sha256(b"6ABE9E4A59EB434B997B4B6FD2B1055F").hexdigest())

    def test_partner_format(self):
        for kind in (BIQUAD_JSON, PEQ_JSON, BIQUAD_XML):
            for path in FILES[kind]:
                with self.subTest(path.name):
                    self.assertEqual(export_partners.partner_format(path.read_bytes()), kind)

    def test_serial_number(self):
        self.assertEqual(export_partners.serial_number("000042"), "A000042")
        self.assertEqual(export_partners.serial_number(" a000042"), "A000042")
        for bad in ("A12345", "B000042", "A00004x"):
            with self.assertRaises(ValueError):
                export_partners.serial_number(bad)

    def test_merging_serial(self):
        data = FILES[BIQUAD_JSON][1].read_bytes()
        self.assertEqual(export_partners.find_serial(data), gen_testdata.MERGING_SERIAL)
        self.assertEqual(export_biquad_json.read(data, "000042").corrections[0].channel, "Left")
        with self.assertRaisesRegex(ValueError, "not for serial number A000043"):
            export_biquad_json.read(data, "A000043")
        with self.assertRaisesRegex(ValueError, "key is not known"):
            export_partners.open_export(data, search=False)

    def test_unknown_key(self):
        data = export_partners.encrypt("other key", b'{"a": 1}', bytes(16))
        with self.assertRaises(ValueError):
            export_partners.open_export(data, search=False)

    def test_wrong_reader(self):
        with self.assertRaisesRegex(ValueError, "not a parametric EQ JSON export"):
            export_peq_json.load(FILES[BIQUAD_JSON][0])


class ReaderTests(unittest.TestCase):
    def assert_close(self, got, want, places=6):
        for g, w in zip(got, want, strict=True):
            self.assertAlmostEqual(g, w, places=places)

    def test_biquad_formats(self):
        for module, path, rates in ((export_biquad_json, FILES[BIQUAD_JSON][0], (96000, 192000)),
                                    (export_biquad_json, FILES[BIQUAD_JSON][1], (44100, 48000)),
                                    (export_biquad_xml, FILES[BIQUAD_XML][0], (44100, 48000))):
            export = module.load(path)
            self.assertEqual([(c.channel, c.sample_rate) for c in export.corrections],
                             [(s, r) for r in rates for s in ("Left", "Right")])
            for c in export.corrections:
                with self.subTest(f"{path.name} {c.channel} {c.sample_rate:g}"):
                    self.assertEqual(len(c.biquads), 3)
                    self.assert_close(c.response(FREQUENCIES), analytic(c.channel, c.sample_rate))

    def test_parametric_formats(self):
        for path in FILES[PEQ_JSON] + FILES[TXT][1:] + FILES["tmreq"]:
            loader = {".bin": export_peq_json.load, ".txt": export_txt.load,
                      ".tmreq": tmreq.load}[path.suffix]
            export = loader(path)
            self.assertEqual([c.channel for c in export.corrections], ["L", "R"])
            for c, side in zip(export.corrections, ("Left", "Right")):
                with self.subTest(f"{path.name} {side}"):
                    self.assert_close(c.response(FREQUENCIES), analytic(side), 2)

    def test_graphic_text(self):
        export = export_txt.load(FILES[TXT][0])
        self.assertIn(("Target mode", "Flat"), export.fields)
        c = export.corrections[0]
        self.assertEqual([f for f, _ in c.points], list(gen_testdata.THIRD_OCTAVES))
        want = dsp.cascade_db(gen_testdata.bells("Left"), 1000, dsp.PEQ_SAMPLE_RATE)
        self.assertAlmostEqual(c.response([1000])[0], want, delta=0.05)

    def test_tmreq_band_types(self):
        preset = FILES["tmreq"][0].read_text()
        for n, code in ((1, 1), (8, 1), (9, 3)):
            name = "REQ Band1Type" if n == 1 else f"REQ Band{n} Type"
            preset = preset.replace(f'"{name}" v="0.00,"', f'"{name}" v="{code}.00,"')
        peqs = tmreq.read(preset).corrections[0].peqs
        self.assertEqual([p.kind for p in peqs],
                         ["low-shelf"] + ["bell"] * 6 + ["high-shelf", "high-pass"])
        self.assertEqual(tmreq.read(preset.replace('v="3.00,"', 'v="2.00,"'))
                         .corrections[0].peqs[8].kind, "low-pass")

    def test_filter_kinds(self):
        low, high = Peq(100, 6, 0.7, "low-shelf"), Peq(100, 6, 0.7, "high-shelf")
        self.assertAlmostEqual(low.biquad().db(10, dsp.PEQ_SAMPLE_RATE), 6, places=1)
        self.assertAlmostEqual(high.biquad().db(10000, dsp.PEQ_SAMPLE_RATE), 6, places=1)
        hp, lp = Peq(1000, 0, 0.7071, "high-pass"), Peq(1000, 0, 0.7071, "low-pass")
        for p in (hp, lp):
            self.assertAlmostEqual(p.biquad().db(1000, dsp.PEQ_SAMPLE_RATE), -3.01, places=1)
        self.assertLess(hp.biquad().db(100, dsp.PEQ_SAMPLE_RATE), -35)
        self.assertLess(lp.biquad().db(10000, dsp.PEQ_SAMPLE_RATE), -35)

    def test_lvnd(self):
        left, right = (export_lvnd.load(p).corrections[0] for p in FILES[LVND])
        self.assertEqual((left.channel, left.gain_db, left.delay_ms), ("Left", -1.5, 0.5))
        self.assertEqual((right.channel, right.gain_db), ("Right", 0.0))
        want = [g - 1.5 for g in analytic("Left", export_lvnd.SAMPLE_RATE)]
        for got, w in zip(left.response(FREQUENCIES), want):
            self.assertAlmostEqual(got, w, delta=0.05)       # float32 coefficients

    def test_lvnd_field(self):
        for value in (0, 1, 0x3F800000, 0xFFFFFFFF):
            self.assertEqual(export_lvnd.decode_field(export_lvnd.encode_field(value)), value)
        with self.assertRaises(ValueError):
            export_lvnd.decode_field(b"\x80\x80\x00\x80\x80")

    def test_lvnd_checksum(self):
        data = bytearray(FILES[LVND][0].read_bytes())
        data[export_lvnd.HEADER.size + 2] ^= 0x01
        with self.assertRaisesRegex(ValueError, "bad checksum"):
            export_lvnd.read(bytes(data))
        with self.assertRaisesRegex(ValueError, "not an LVND file"):
            export_lvnd.read(b"XXXX" + bytes(data[4:]))

    def test_rejected_input(self):
        peq = FILES[TXT][1].read_text().replace("Parametric Eq 2",
                                                                "Low Shelf 2")
        with self.assertRaisesRegex(ValueError, "unsupported filter type 'Low Shelf 2'"):
            export_txt.read(peq)
        with self.assertRaisesRegex(ValueError, "unknown table columns"):
            export_txt.read("Preset name: x\nL channel calibration:\n|Freq|Q|\n")
        preset = FILES["tmreq"][0].read_text().replace('"REQ Band8 Type" v="0.00,"',
                                                       '"REQ Band8 Type" v="7.00,"')
        with self.assertRaisesRegex(ValueError, "unknown band 8 type 7"):
            tmreq.read(preset)
        with self.assertRaisesRegex(ValueError, "missing 'Chan Gain'"):
            tmreq.read(FILES["tmreq"][0].read_text().replace("Chan Gain", "Other"))
        plain = export_partners.open_export(FILES[PEQ_JSON][0].read_bytes())[0]
        root = json.loads(plain)
        root["channels"][0]["peqFilters"][0]["type"] = "LowShelf"
        blob = export_partners.encrypt(export_partners.PARTNERS[2].key, json.dumps(root).encode())
        with self.assertRaisesRegex(ValueError, "unsupported filter type 'LowShelf'"):
            export_peq_json.read(blob)


class SelectTests(unittest.TestCase):
    def test_sample_rate(self):
        fluid, merging = (export_biquad_json.load(p) for p in FILES[BIQUAD_JSON])
        self.assertEqual(fluid.select("Right").sample_rate, 96000)
        self.assertEqual(merging.select("Left").sample_rate, 48000)
        self.assertEqual(merging.select("Left", 44100).sample_rate, 44100)
        with self.assertRaisesRegex(ValueError, "no 88200 Hz filters; available: 44100, 48000"):
            merging.select("Left", 88200)

    def test_channel(self):
        export = tmreq.load(FILES["tmreq"][0])
        self.assertEqual(export.select("Right").channel, "R")
        with self.assertRaisesRegex(ValueError, "no Center channel; available: L, R"):
            export.select("Center")

    def test_correction_sum(self):
        c = Correction("x", gain_db=1.0, peqs=[Peq(1000, 3, 1)], points=[(100, 2), (10000, 4)])
        self.assertAlmostEqual(c.response([1000])[0], 1 + 3 + 3)
        with self.assertRaisesRegex(ValueError, "unsupported filter type 'notch'"):
            Correction("x", peqs=[Peq(1000, 3, 1, "notch")]).response([1000])


class DetectTests(unittest.TestCase):
    def test_by_content(self):
        for kind, paths in FILES.items():
            for path in paths:
                with self.subTest(path.name):
                    self.assertEqual(formats.detect(path), kind)
        self.assertEqual(formats.detect(ROOT / "rew/rewcal/TILT01 degrees_0.txt"), "rewcal")

    def test_unknown_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.txt"
            path.write_text("no numbers here\n")
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                formats.detect(path)


class ConvertTests(unittest.TestCase):
    def test_every_format(self):
        converters = {c.source: c for c in (
            to_autoeq.SoundidExportBiquadJsonToAutoeq, to_autoeq.SoundidExportPeqJsonToAutoeq,
            to_autoeq.SoundidExportBiquadXmlToAutoeq, to_autoeq.SoundidExportLvndToAutoeq,
            to_autoeq.SoundidExportTxtToAutoeq,
            to_autoeq.TmreqToAutoeq)}
        for kind, paths in FILES.items():
            for path in paths:
                with self.subTest(path.name):
                    options = {} if kind == LVND else {"channel": "right"}
                    result = converters[kind](**options).convert([path])
                    side = "Left" if path.stem.endswith("Left") else "Right"
                    name = f"{path.stem}.csv" if kind == LVND else f"{path.stem} Right.csv"
                    self.assertEqual(result.name, name)
                    self.assertIn(f"{side} correction", result.notes[1])

    def test_sample_rate_option(self):
        converter = to_autoeq.SoundidExportBiquadJsonToAutoeq(sample_rate=192000)
        result = converter.convert([FILES[BIQUAD_JSON][0]])
        self.assertEqual(result.notes[1], "Left correction at 192000 Hz, dB")


@unittest.skipUnless(REAL.is_dir(), "no SoundID exports in testdata/real")
class RealExportTests(unittest.TestCase):
    """Exports of one SoundID profile to every partner, if present."""

    def test_every_export_reads(self):
        paths = [p for d in ("ADAM Audio", "Dolby Atmos Renderer", "Fluid Audio", "Grace Design",
                             "Lynx", "MERGING", "RME", "SPQ DSP", "Wayne Jones AUDIO")
                 for p in sorted((REAL / d).glob("*"))]
        if not paths:
            self.skipTest("no exports")
        for path in paths:
            with self.subTest(path.name):
                kind = formats.detect(path)
                self.assertTrue(formats.FORMATS[kind].inspector().inspect(path))

    def test_same_filters(self):
        """Fluid, MERGING and Wayne Jones get the same 192 kHz biquads."""
        fluid = REAL / "Fluid Audio/L lumi smol spkr Sep 30 - Flat.bin"
        wayne = REAL / "Wayne Jones AUDIO/L lumi smol spkr Sep 30_192000_Left.bin"
        if not (fluid.exists() and wayne.exists()):
            self.skipTest("no Fluid Audio or Wayne Jones export")
        a = export_biquad_json.load(fluid).select("Left", 192000).response(FREQUENCIES)
        b = export_lvnd.load(wayne).corrections[0].response(FREQUENCIES)
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, delta=0.1)


if __name__ == "__main__":
    unittest.main()
