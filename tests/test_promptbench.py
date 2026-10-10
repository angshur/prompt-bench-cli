import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ["PROMPTBENCH_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

from promptbench import golden, graders, providers, report, runner, stats  # noqa: E402


class Graders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = golden.load()

    def test_golden_is_deterministic(self):
        path = os.path.join(golden.GOLDEN_DIR, "truth.json")
        with open(path) as f:
            before = f.read()
        golden.generate(seed=7)
        with open(path) as f:
            self.assertEqual(before, f.read())

    def test_good_mock_passes_every_case_and_prompt(self):
        for name in ("analyze_v1", "analyze_v3", "analyze_v6"):
            _, meta = runner.load_prompt(name)
            for c in self.cases:
                out = providers.call("mock:good", "", case=c, prompt_meta=meta)["output"]
                g = graders.grade(out, c, meta)
                self.assertTrue(g["passed"], (name, c["case_id"], g["checks"]))

    def test_lazy_mock_is_caught(self):
        fails = 0
        for c in self.cases:
            out = providers.call("mock:lazy", "", case=c, prompt_meta={})["output"]
            if not graders.grade(out, c, {})["passed"]:
                fails += 1
        self.assertGreaterEqual(fails, 9)   # wrong leader, no anomaly, no blanks flagged

    def test_anomaly_needs_campaign_and_date(self):
        c = [x for x in self.cases if x["case_id"] == "c02_spike"][0]
        self.assertFalse(graders.check_anomaly("Social looks fine.", c["truth"]))
        self.assertFalse(graders.check_anomaly("Spring Sale - Social had a spike.", c["truth"]))
        self.assertTrue(graders.check_anomaly("Spring Sale - Social spiked on March 9.", c["truth"]))
        self.assertTrue(graders.check_anomaly("Spring Sale - Social: 2026-03-09 cost spike", c["truth"]))

    def test_cpa_figure_must_match_truth(self):
        c = [x for x in self.cases if x["case_id"] == "c01_plain"][0]
        name = c["truth"]["best"]
        cpa = c["truth"]["campaigns"][name]["cpa"]
        self.assertTrue(graders.check_cpa_correct(f"{name}: ${cpa:.2f} per conversion", c["truth"]))
        self.assertFalse(graders.check_cpa_correct(f"{name}: ${cpa * 1.5:.2f} per conversion", c["truth"]))

    def test_missing_values_acknowledged(self):
        c = [x for x in self.cases if x["case_id"] == "c06_missing"][0]
        self.assertFalse(graders.check_missing("All good.", c["truth"]))
        self.assertTrue(graders.check_missing("Three conversion cells are blank.", c["truth"]))

    def test_sections_in_order(self):
        s = ["A", "B", "C"]
        self.assertTrue(graders.check_sections("a\nb\nc", s))
        self.assertFalse(graders.check_sections("a\nc\nb", s))


class Stats(unittest.TestCase):
    def test_wilson_bounds(self):
        lo, hi = stats.wilson(10, 10)
        self.assertLess(lo, 1.0)
        self.assertEqual(round(hi, 6), 1.0)
        self.assertEqual(stats.wilson(0, 0), (0.0, 0.0))

    def test_paired_detects_consistent_gain(self):
        a, b = [0.0] * 12, [1.0] * 12
        r = stats.paired_compare(a, b)
        self.assertLess(r["p_value"], 0.01)
        self.assertEqual(stats.verdict(r), "B is better")

    def test_paired_no_difference(self):
        r = stats.paired_compare([0.5, 1.0, 0.0, 1.0], [0.5, 1.0, 0.0, 1.0])
        self.assertEqual(r["p_value"], 1.0)
        self.assertIn("no reliable", stats.verdict(r))


class Pipeline(unittest.TestCase):
    def test_batch_report_compare_regress(self):
        good = runner.run_batch(["analyze_v1", "analyze_v6"], ["mock:good"], reps=2, label="t-good", log=lambda *_: None)
        lazy = runner.run_batch(["analyze_v1", "analyze_v6"], ["mock:lazy"], reps=2, label="t-lazy", log=lambda *_: None)
        self.assertIn("100%", report.summary(good))
        text, res = report.compare("analyze_v1", "analyze_v6", "mock:lazy", lazy)
        self.assertIn("Verdict", text)
        failed, _ = report.regress(good, good)
        self.assertFalse(failed)
        failed, text = report.regress(good, lazy)          # different models: no overlapping pair, so the gate refuses to pass
        self.assertTrue(failed)
        self.assertIn("No overlapping", text)


if __name__ == "__main__":
    unittest.main()
