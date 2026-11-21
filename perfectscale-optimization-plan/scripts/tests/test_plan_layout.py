"""Plan output layout and reading aids: the JSON lives in data/ and the CSV is a no-openpyxl
fallback, every section opens collapsed, the Actions table filters (opening on Phase 1), and the
bin-packing automation action that lets the node autoscaler turn smaller pods into fewer nodes."""
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan import model, render, verify  # noqa: E402
from psplan.actions import Plan  # noqa: E402
from psplan.config import Config  # noqa: E402


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cfg = Config.load(fixtures.make(cls.tmp))
        cls.F = model.load_all(cls.cfg, parallel=False)
        cls.S = Plan(cls.cfg, cls.F).build()
        cls.files = render.write_all(cls.cfg, cls.S, cls.F)
        cls.html_path = next(p for p in cls.files if p.name.endswith(".html") and "optimization-plan" in p.name)
        cls.html = cls.html_path.read_text()
        cls.by = {a["id"]: a for a in cls.S["actions"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---- 1. files
    def test_plan_json_is_in_data_and_feeds_the_summary(self):
        js = [p for p in self.files if p.suffix == ".json"]
        self.assertEqual([p.parent.name for p in js], ["data"])
        self.assertFalse(list(self.cfg.out().glob("*.json")))           # nothing machine-only at the top level

    def test_no_duplicate_csv_when_the_workbook_is_written(self):
        try:
            import openpyxl  # noqa: F401
        except ImportError:
            self.skipTest("openpyxl missing: the CSV is the fallback")
        self.assertFalse([p for p in self.files if p.suffix == ".csv"])
        self.assertTrue([p for p in self.files if p.suffix == ".xlsx"])

    # ---- 2. collapsed
    def test_every_section_starts_collapsed(self):
        m = re.search(r"var CLOSED=/(.*?)/;", self.html)
        rx = re.compile(m.group(1).replace("\\s\\S", "\\s\\S"))
        for h in re.findall(r"<h2>([^<]+)</h2>", self.html):
            self.assertTrue(rx.match(h), h)

    # ---- 3. bin-packing
    def test_binpacking_unblocks_the_pool_actions_in_phase_1(self):
        b = next(a for a in self.S["actions"] if a["key"] == "binpacking")
        self.assertEqual((b["phase"], b["is_unblocker"], b["action_type"]), (1, True, "binpacking_automation"))
        self.assertEqual(b["topic"], "Node pools and autoscaling")
        shrink = [a for a in self.S["actions"] if a["key"] in ("pool_shrink_blocked", "pool_shrink_free")]
        self.assertTrue(shrink)
        self.assertTrue(all(b["id"] in a["depends_on"] for a in shrink))
        self.assertGreater(b["released_value"], 0)
        self.assertIn("Karpenter", b["title"])                           # fixture pools run Karpenter
        steps = " ".join(b["steps"])
        self.assertIn("DryRun", steps)
        self.assertIn("NodeGroupAutomationConfig", steps)
        self.assertIn("bill only falls when nodes leave", b["evidence"])

    def test_no_binpacking_without_idle_pools(self):
        p = Plan(self.cfg, self.F)
        p.build()
        self.assertTrue(any(a["key"] == "binpacking" for a in p.A))
        q = Plan(self.cfg, self.F)
        q.A, q.claimed_pools = [], set()
        q.r_binpacking()
        self.assertFalse(q.A)

    # ---- 4. filters
    def test_actions_table_filters_open_on_phase_1(self):
        self.assertIn("<div class=afilt>", self.html)
        rows = re.findall(r"<tr data-phase='(\d)' data-topic='([^']+)' data-lane='([^']+)' data-cl='([^']+)'>", self.html)
        self.assertEqual(len(rows), len(self.S["actions"]))
        pm = re.search(r"<div class=menu data-f=phase hidden>(.*?)</div></div>", self.html, re.S).group(1)
        self.assertEqual(re.findall(r"value='(\d+)' checked", pm), ["1"])
        tm = re.search(r"<div class=menu data-f=topic hidden>(.*?)</div></div>", self.html, re.S).group(1)
        self.assertIn("Node pools and autoscaling", tm)
        ok, errs = verify.verify(self.cfg, self.S, self.F, self.files)
        self.assertEqual(errs, [])
        self.assertTrue(any("opens on Phase 1 only" in m for m in ok))

    def test_verify_catches_a_filter_that_opens_on_everything(self):
        good = self.html
        try:
            self.html_path.write_text(good.replace("value='2'>", "value='2' checked>", 1))
            _, errs = verify.verify(self.cfg, self.S, self.F, self.files)
            self.assertTrue(any("Phase 1 only" in m for m in errs))
        finally:
            self.html_path.write_text(good)

    def test_cluster_filter_matches_whole_names(self):
        facts = [{"name": "prod"}, {"name": "prod-a"}]
        self.assertEqual(render.action_clusters({"cluster": "prod-a"}, facts), ["prod-a"])
        self.assertEqual(render.action_clusters({"cluster": "prod and prod-a"}, facts), ["prod", "prod-a"])
        self.assertEqual(render.action_clusters({"cluster": "tenant-wide"}, facts), [render.TENANT_WIDE])


if __name__ == "__main__":
    unittest.main()
