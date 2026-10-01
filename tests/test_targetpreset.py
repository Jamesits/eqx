import json
import unittest

from testgen import common
from eqx.soundid import targetpreset

TESTDATA = common.ROOT
PRESETS = sorted((TESTDATA / "soundid/targetpreset").glob("*.json"))


class TargetPresetTests(unittest.TestCase):
    def test_testdata_presets_parse(self):
        self.assertTrue(PRESETS)
        for path in PRESETS:
            with self.subTest(path=path.name):
                p = targetpreset.load(path)
                self.assertEqual(p.name, path.stem)
                self.assertTrue(p.filter_groups)
                for g in p.filter_groups:
                    self.assertIn(g.cutoff.flip_state, targetpreset.FLIP_STATES)
                    self.assertLess(g.cutoff.left_freq, g.cutoff.right_freq)
                    for f in g.filters:
                        self.assertIn(f.type, targetpreset.FILTER_TYPES)

    def test_values(self):
        p = targetpreset.load(TESTDATA / "soundid/targetpreset/Bass and treble.json")
        (g,) = p.filter_groups
        self.assertEqual(g.cutoff, targetpreset.Cutoff(True, "inner", 30.0, 16000.0))
        self.assertEqual(len(g.filters), 4)
        self.assertEqual(g.filters[2], targetpreset.Filter(
            id=3, type="bell", enabled=False, frequency=5000.0, gain=2.0, q=3.0, color_id=2))

    def test_integer_numbers_accepted(self):
        doc = {"name": "n", "filterGroups": [{
            "cutoff": {"enabled": False, "flipState": "outer", "leftFreq": 20, "rightFreq": 200},
            "filters": []}]}
        p = targetpreset.read(json.dumps(doc))
        self.assertEqual(p.filter_groups[0].cutoff.right_freq, 200.0)
        self.assertIsInstance(p.filter_groups[0].cutoff.right_freq, float)

    def test_invalid(self):
        filt = {"colorId": 0, "enabled": True, "frequency": 50.0, "gain": 0.0,
                "id": 1, "q": 1.0, "type": "bell"}
        cutoff = {"enabled": True, "flipState": "inner", "leftFreq": 20.0, "rightFreq": 2e4}
        cases = {
            "not json": "{",
            "not object": "[]",
            "no name": {"filterGroups": []},
            "no groups": {"name": "n"},
            "bool as number": {"name": "n", "filterGroups": [
                {"cutoff": {**cutoff, "leftFreq": True}, "filters": []}]},
            "bool as int": {"name": "n", "filterGroups": [
                {"cutoff": cutoff, "filters": [{**filt, "id": True}]}]},
            "string gain": {"name": "n", "filterGroups": [
                {"cutoff": cutoff, "filters": [{**filt, "gain": "1"}]}]},
            "missing q": {"name": "n", "filterGroups": [
                {"cutoff": cutoff, "filters": [{k: v for k, v in filt.items() if k != "q"}]}]},
            "nan": '{"name":"n","filterGroups":[{"cutoff":{"enabled":true,"flipState":"inner",'
                   '"leftFreq":NaN,"rightFreq":1.0},"filters":[]}]}',
        }
        for label, doc in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                targetpreset.read(doc if isinstance(doc, str) else json.dumps(doc))


if __name__ == "__main__":
    unittest.main()
