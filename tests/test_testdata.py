import tempfile
import unittest
from pathlib import Path

import gen_testdata
from testgen import common, soundid
from eqx.soundid import crypto, peqb, swproj

ROOT = common.ROOT


def _project(path: Path, password: bytes | None = None) -> tuple:
    p = swproj.SwProj.open(path, password)
    return p.header.text, p.part("eqb"), p.xml


class GeneratedTestdataTests(unittest.TestCase):
    def test_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            written = gen_testdata.generate(tmp)
            stored = {p.relative_to(ROOT)
                      for d in ("audyssey", "autoeq", "dirac", "fir", "ik", "minidsp", "rationalacoustics", "rew", "rme", "rode",
                                "rogueamoeba", "sonarworks-reference", "soundid")
                      for p in (ROOT / d).rglob("*") if p.is_file()}
            self.assertEqual({p.relative_to(tmp) for p in written}, stored)
            for path in written:
                rel = path.relative_to(tmp)
                with self.subTest(rel.as_posix()):
                    if rel.suffix == ".swproj":
                        # gzip bytes depend on the zlib build; compare the content.
                        password = (common.SWPROJ_PASSWORD.encode()
                                    if "password" in rel.name else None)
                        self.assertEqual(_project(path, password),
                                         _project(ROOT / rel, password))
                    else:
                        self.assertEqual(path.read_bytes(), (ROOT / rel).read_bytes())

    def test_conversions_match(self):
        for source, target, options in gen_testdata.CONVERSIONS:
            with self.subTest(target):
                result = gen_testdata.run_conversion(ROOT, source, target, options)
                self.assertEqual(result.name, Path(target).name)
                if target.endswith(".swproj"):
                    got = swproj.SwProj(result.data)
                    self.assertEqual((got.header.text, got.part("eqb"), got.xml),
                                     _project(ROOT / target))
                elif isinstance(result.data, dict):
                    self.assertEqual(result.data, {p.name: p.read_bytes()
                                                   for p in (ROOT / target).iterdir()})
                else:
                    self.assertEqual(result.data, (ROOT / target).read_bytes())

    def test_flat_profiles_are_flat(self):
        p = swproj.SwProj.open(ROOT / soundid.PROJ_DIR / "Flat.swproj")
        for curve in p.eqb.curves:
            self.assertTrue(all(abs(r) < 1e-3 for _, r, _ in curve.points))
        key = crypto.swhp_key(common.COMPUTER_ID)
        p = peqb.read((ROOT / soundid.PEQB_DIR / "Flat Flat Wired.swhp").read_bytes(), key)
        self.assertTrue(all(r == 0 for _, r, _ in p.curves[0].points))


if __name__ == "__main__":
    unittest.main()
