import unittest

from testgen import common

from eqx.soundid import crypto, peqb, swproj

TESTDATA = common.ROOT
PROJECTS = sorted((TESTDATA / "soundid/swproj").glob("*.swproj"))
PROFILES = sorted((TESTDATA / "soundid/peqb").glob("*.swhp"))
SWPROJ_PASSWORD = common.SWPROJ_PASSWORD.encode()


class CryptoTests(unittest.TestCase):
    def test_default_key(self):
        self.assertEqual(crypto.derive_key().hex(), "3c07ebb62cc0c7860c2614d3f1b942de")

    def test_round_trip(self):
        key = crypto.derive_key(b"secret")
        for size in (0, 1, 15, 16, 17, 100):
            data = bytes(range(size))
            with self.subTest(size=size):
                self.assertEqual(crypto.decrypt(key, crypto.encrypt(key, data)), data)

    def test_wrong_key(self):
        # Fixed IV: with a random one, a wrong key still yields valid padding
        # about 1 time in 256.
        blob = crypto.encrypt(crypto.derive_key(b"a"), b"x" * 40, iv=bytes(16))
        with self.assertRaises(ValueError):
            crypto.decrypt(crypto.derive_key(b"b"), blob)


class PeqbTests(unittest.TestCase):
    def test_round_trip(self):
        curves = [
            peqb.Curve(
                9, [(20.0, -1.5, None), (40.0, 2.25, None)], {"ChannelIndex": "0"}
            ),
            peqb.Curve(1, [(20.0, 1.5, 0.5), (40.0, -2.25, -0.25)]),
            peqb.Curve(11),
        ]
        p = peqb.read(peqb.write(curves, {"k": "v"}))
        self.assertEqual(p.version, (3, 0, 0, 2))
        self.assertTrue(p.decoded)
        self.assertEqual(p.trailing, b"")
        self.assertEqual(p.parameters, {"k": "v"})
        self.assertEqual([c.flags for c in p.curves], [0b1011, 0b0111, 0])
        for want, got in zip(curves, p.curves):
            self.assertEqual(
                (got.curve_type, got.points, got.parameters),
                (want.curve_type, want.points, want.parameters),
            )

    def test_encrypted_without_key_is_not_decoded(self):
        for path in PROFILES:
            with self.subTest(path=path.name):
                p = peqb.read(path.read_bytes())
                self.assertEqual(p.decoded, not p.encrypted)
                if p.encrypted:
                    self.assertEqual(p.ciphertext_len % 16, 0)

    def test_decrypt_profiles(self):
        self.assertTrue(PROFILES)
        key = crypto.swhp_key(common.COMPUTER_ID)
        for path in PROFILES:
            with self.subTest(path=path.name):
                blob = path.read_bytes()
                p = peqb.read(blob, key)
                self.assertTrue(p.decoded)
                self.assertEqual(p.trailing, b"")
                self.assertEqual(
                    [c.curve_type for c in p.curves], [1, 2, 5, 6, 6, 7, 7]
                )
                self.assertEqual(p.parameters["Headphone_Calibration"], "true")
                band = float(p.parameters["META_FixedErrorBandRange"])
                left = p.curves[0].points
                for i, sign in ((3, -1), (4, 1), (5, -1), (6, 1)):
                    for (f, r, g), (bf, br, bg) in zip(left, p.curves[i].points):
                        self.assertEqual((bf, bg), (f, g))
                        self.assertAlmostEqual(br, r + sign * band, places=9)
                if p.encrypted:
                    with self.assertRaises(ValueError):
                        peqb.read(blob, crypto.swhp_key("g" + "1" * 40))


class SwprojTests(unittest.TestCase):
    def test_testdata_projects_parse(self):
        self.assertTrue(PROJECTS)
        for path in PROJECTS:
            with self.subTest(path=path.name):
                blob = path.read_bytes()
                protected = swproj.parse_header(blob).password_protected
                proj = swproj.SwProj(blob, SWPROJ_PASSWORD if protected else None)
                root = proj.tree()
                self.assertTrue(root.findall("s:Curves/s:Curve", swproj.NS))
                self.assertEqual(proj.eqb.trailing, b"")
                self.assertTrue(proj.eqb.curves)

    def test_round_trip(self):
        xml = b'<Project xmlns="http://www.sonarworks.com"><Name>x</Name></Project>'
        eqb = peqb.write([peqb.Curve(9, [(20.0, 0.0, 0.0)])])
        proj = swproj.SwProj(swproj.write(xml, eqb))
        self.assertEqual(proj.xml, xml)
        self.assertEqual(proj.part("eqb"), eqb)
        self.assertFalse(proj.header.password_protected)
        self.assertEqual(proj.header.parts, [("swproj", -1), ("eqb", len(eqb))])

    def test_password(self):
        blob = swproj.write(b"<Project/>", password=b"pw")
        self.assertTrue(swproj.SwProj(blob).header.password_protected)
        with self.assertRaises(ValueError):
            _ = swproj.SwProj(blob).xml
        self.assertEqual(swproj.SwProj(blob, b"pw").xml, b"<Project/>")


if __name__ == "__main__":
    unittest.main()
