"""Savings goal vs what the data supports: target parsing, the three numbers, the verdict, the
config guard, and the executive-summary section (shown only when a goal is configured)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan import goal, model, render, verify  # noqa: E402
from psplan.actions import Plan  # noqa: E402
from psplan.config import Config  # noqa: E402

TITLE = "Savings goal vs what the data supports"


class Parse(unittest.TestCase):
    def test_dollars_ranges_and_percent(self):
        self.assertEqual(goal.parse_target({"monthly_usd": 9000}, "x"), ("usd", 9000.0, 9000.0))
        self.assertEqual(goal.parse_target({"monthly_usd": [20000, 15000]}, "x"), ("usd", 15000.0, 20000.0))
        self.assertEqual(goal.parse_target({"pct": 20}, "x"), ("pct", 20.0, 20.0))

    def test_bad_targets_fail_clearly(self):
        for bad in ({}, {"monthly_usd": 1, "pct": 2}, {"monthly_usd": -5}, {"monthly_usd": [1, 2, 3]},
                    {"pct": 150}, {"monthly_usd": "9k"}, 9000):
            with self.assertRaises(SystemExit):
                goal.parse_target(bad, "x")

    def test_verdict_bands(self):
        # conservative 70, realistic 150, maximum 200
        self.assertEqual(goal.verdict(70, 70, 150, 200), "supported")
        self.assertEqual(goal.verdict(120, 70, 150, 200), "stretch")
        self.assertEqual(goal.verdict(180, 70, 150, 200), "unlikely")
        self.assertEqual(goal.verdict(201, 70, 150, 200), "not_supported")


class WithFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cfg_path = fixtures.make(cls.tmp)
        cls.cfg = Config.load(cls.cfg_path)
        cls.F = model.load_all(cls.cfg, parallel=False)
        cls.names = [f["name"] for f in cls.F]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def cfg_with(self, g, **extra):
        c = Config(dict(self.cfg)); c.root = self.cfg.root
        c["savings_goal"] = g
        c.update(extra)
        goal.validate(c)
        return c

    def test_three_numbers_come_from_the_facts(self):
        f = self.F[0]
        c = self.cfg_with({"clusters": {f["name"]: {"monthly_usd": 1}}})
        r = goal.goal_check(c, self.F)["rows"][0]
        self.assertEqual(r["rightsizing"], round(f["pot"]))
        self.assertEqual(r["idle"], round(f["pools"]["idle"]))
        self.assertEqual(r["conservative"], round(f["pot"] * c["adoption_factor"]))
        self.assertEqual(r["realistic"], round(f["pot"] + 0.5 * f["pools"]["idle"]))
        self.assertEqual(r["maximum"], round(f["pot"] + f["pools"]["idle"]))
        self.assertEqual(r["verdict"], "supported")
        self.assertIn("conservative case", r["sentence"])

    def test_an_impossible_goal_is_not_supported_and_says_by_how_much(self):
        f = self.F[0]
        big = round(f["pot"] + f["pools"]["idle"]) + 1000
        c = self.cfg_with({"clusters": {f["name"]: {"monthly_usd": big}}})
        G = goal.goal_check(c, self.F)
        r = G["rows"][0]
        self.assertEqual(r["verdict"], "not_supported")
        self.assertIn("short", r["sentence"])
        self.assertIn("not supported", G["takeaway"])

    def test_percent_goal_uses_node_cost_unless_a_baseline_is_given(self):
        f = self.F[0]
        c = self.cfg_with({"clusters": {f["name"]: {"pct": 10}}})
        r = goal.goal_check(c, self.F)["rows"][0]
        self.assertEqual(r["goal_low"], round(f["pools"]["cost"] * 0.1))
        self.assertEqual(r["baseline_from"], "PerfectScale-measured node cost")
        c = self.cfg_with({"clusters": {f["name"]: {"pct": 10, "baseline_usd": 50000}}})
        r = goal.goal_check(c, self.F)["rows"][0]
        self.assertEqual((r["goal_low"], r["baseline_from"]), (5000, "agreed baseline"))

    def test_several_clusters_get_a_summed_total(self):
        g = {"clusters": {n: {"monthly_usd": [100, 200]} for n in self.names}}
        G = goal.goal_check(self.cfg_with(g), self.F)
        self.assertEqual(len(G["rows"]), len(self.names))
        if len(self.names) > 1:
            self.assertEqual(G["total"]["goal_low"], 100 * len(self.names))
            self.assertEqual(G["total"]["maximum"], round(sum(f["pot"] + f["pools"]["idle"] for f in self.F)))

    def test_idle_share_moves_the_realistic_case(self):
        f = self.F[0]
        c = self.cfg_with({"clusters": {f["name"]: {"monthly_usd": 1}}}, goal_idle_share=0.25)
        r = goal.goal_check(c, self.F)["rows"][0]
        self.assertEqual(r["realistic"], round(f["pot"] + 0.25 * f["pools"]["idle"]))

    def test_config_guard_names_the_mistake(self):
        with self.assertRaises(SystemExit) as cm:
            self.cfg_with({"clusters": {"no-such-cluster": {"monthly_usd": 1}}})
        self.assertIn("no-such-cluster", str(cm.exception))
        with self.assertRaises(SystemExit):
            self.cfg_with({"source": "x"})
        with self.assertRaises(SystemExit):
            self.cfg_with({"clusters": {self.names[0]: {"monthly_usd": 1}}}, goal_idle_share=2)

    def test_no_goal_no_block(self):
        self.assertIsNone(goal.goal_check(self.cfg, self.F))


class Summary(unittest.TestCase):
    """End to end: the executive summary shows the section with one bar per goal cluster, and the
    verifier passes; without a goal the section is absent."""

    def run_plan(self, g):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        cfg = Config.load(fixtures.make(tmp))
        if g:
            cfg["savings_goal"] = g(cfg)
            goal.validate(cfg)
        F = model.load_all(cfg, parallel=False)
        S = Plan(cfg, F).build()
        files = render.write_all(cfg, S, F)
        es = [p for p in files if p.name.startswith(fname_prefix(cfg)) and p.suffix == ".html"]
        return cfg, S, F, files, es

    def setUp(self):
        if shutil.which("node") is None:
            self.skipTest("node not installed; executive summary degrades to a warning")

    def test_section_shown_with_a_bar_per_cluster(self):
        cfg, S, F, files, es = self.run_plan(lambda c: {
            "source": "POV plan", "clusters": {x["name"]: {"monthly_usd": 1000} for x in c["clusters"]}})
        t = es[0].read_text()
        self.assertIn(f"<h2>{TITLE}</h2>", t)
        self.assertEqual(t.count("class=\"grow"), len(S["goal"]["rows"]) + (1 if S["goal"]["total"] else 0))
        self.assertIn("Goal: POV plan.", t)
        ok, errs = verify.verify(cfg, S, F, files)
        self.assertEqual(errs, [])
        self.assertTrue(any("verdict" in m for m in ok))

    def test_section_absent_without_a_goal(self):
        cfg, S, F, files, es = self.run_plan(None)
        self.assertNotIn(TITLE, es[0].read_text())
        self.assertIsNone(S["goal"])
        _, errs = verify.verify(cfg, S, F, files)
        self.assertEqual(errs, [])


def fname_prefix(cfg):
    return render.fname(cfg, "executive-summary", "html").rsplit("-executive-summary", 1)[0] + "-executive-summary"


if __name__ == "__main__":
    unittest.main()
