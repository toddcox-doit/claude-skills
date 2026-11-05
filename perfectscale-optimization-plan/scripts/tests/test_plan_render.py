import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan.config import Config  # noqa: E402
from psplan import model, render, verify, diffplans  # noqa: E402
from psplan.actions import Plan  # noqa: E402


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        p = fixtures.make(cls.tmp)
        cls.cfg_path = p
        cls.cfg = Config.load(p)
        cls.F = model.load_all(cls.cfg, parallel=False)
        cls.S = Plan(cls.cfg, cls.F).build()
        cls.by = {a["key"]: a for a in cls.S["actions"]}

    def test_expected_rules_fire(self):
        for k in ("automation_proof", "oom_fix", "memory_limits", "stale_optout", "pdb", "job_grouping", "job_rightsize", "stateful_manual", "headroom_policy", "pool_machine_recs", "rerun"):
            self.assertIn(k, self.by)
        self.assertNotIn("autoprov_review", self.by)       # no auto-provisioned pools in this tenant

    def test_proof_cluster_is_the_one_with_audit_history(self):
        self.assertEqual(self.by["automation_proof"]["cluster"], "dev-b")

    def test_money_bases_not_mixed(self):
        self.assertEqual(self.by["oom_fix"]["savings_basis"], "resiliency_investment")
        r = self.by["oom_fix"]["range"]
        self.assertLessEqual(r["low"], r["likely"]); self.assertLessEqual(r["likely"], r["literal"])
        self.assertIsNone(self.by["oom_fix"]["roi_score"])

    def test_aws_wording_uses_karpenter_annotation(self):
        txt = " ".join(self.by["stale_optout"]["steps"])
        self.assertIn("karpenter.sh/do-not-disrupt", txt)
        self.assertNotIn("GKE", json.dumps(self.S))

    def test_dependencies_ordered_and_unblocker_promoted(self):
        A = self.S["actions"]; pos = {a["id"]: i for i, a in enumerate(A)}
        for a in A:
            for d in a["depends_on"]:
                self.assertLess(pos[d], pos[a["id"]])
        self.assertEqual(self.by["stale_optout"]["phase"], 1)

    def test_early_flag_and_scaling_in_summary(self):
        self.assertTrue(self.S["early"])
        self.assertGreater(self.S["run_rate_factor"]["dev-b"], 1)

    def test_effort_escalators_capped(self):
        from psplan.actions import effort
        self.assertEqual(effort("XS", ["a", "b", "c"])[0], "M")
        self.assertEqual(effort("L", ["a"])[0], "L")

    def test_render_and_verify_clean(self):
        files = render.write_all(self.cfg, self.S, self.F)
        names = {f.suffix for f in files}
        self.assertTrue({".md", ".html", ".json", ".csv"} <= names)
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertEqual(errs, [])

    def test_executive_summary_is_a_standard_output(self):
        files = render.write_all(self.cfg, self.S, self.F)
        if shutil.which("node") is None:
            self.skipTest("node not installed; executive summary degrades to a warning")
        summaries = sorted(f.name for f in files if "executive-summary" in f.name)
        # both formats, beside the plan, without a second command
        self.assertTrue(any(n.endswith(".html") for n in summaries), summaries)
        self.assertTrue(any(n.endswith(".docx") for n in summaries), summaries)
        h = [f for f in files if "executive-summary" in f.name and f.suffix == ".html"][0]
        self.assertGreater(h.stat().st_size, 500)

    def test_html_tables_carry_the_standard_controls(self):
        files = render.write_all(self.cfg, self.S, self.F)
        plan = [f for f in files if f.suffix == ".html" and "optimization-plan" in f.name][0]
        t = plan.read_text(encoding="utf-8")
        for needle in ("Show columns", "Autofit selected", "Reset to default",
                       "col-resize", "tableLayout='fixed'", "beforebegin"):
            self.assertIn(needle, t, needle)
        # headers must be free to wrap, or a narrowed column overlaps its neighbour
        self.assertIsNone(re.search(r"(?:^|[,{;}])\s*th\b[^{};]*\{[^{}]*white-space:\s*nowrap", t))

    def test_verifier_catches_table_control_regression(self):
        files = render.write_all(self.cfg, self.S, self.F)
        plan = [f for f in files if f.suffix == ".html" and "optimization-plan" in f.name][0]
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertEqual(errs, [])
        plan.write_text(plan.read_text(encoding="utf-8").replace("Autofit selected", "x"), encoding="utf-8")
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertTrue(any("column picker" in e for e in errs), errs)

    def test_verifier_catches_nowrap_headers(self):
        files = render.write_all(self.cfg, self.S, self.F)
        plan = [f for f in files if f.suffix == ".html" and "optimization-plan" in f.name][0]
        plan.write_text(plan.read_text(encoding="utf-8").replace(
            "th{cursor:pointer;position:relative}", "th{cursor:pointer;white-space:nowrap}"), encoding="utf-8")
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertTrue(any("header cells wrap" in e for e in errs), errs)

    def test_verifier_catches_tampering(self):
        files = render.write_all(self.cfg, self.S, self.F)
        S2 = json.loads(json.dumps(self.S, default=str))
        S2["totals"]["achievable_now_monthly"] += 500
        ok, errs = verify.verify(self.cfg, S2, self.F, files)
        self.assertTrue(any("achievable_now_monthly" in e for e in errs))
        md = [f for f in files if f.suffix == ".md"][0]
        md.write_text(md.read_text() + "\nThis is genuinely good.\n")
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertTrue(any("banned wording" in e for e in errs))

    def test_foreign_tenant_names_come_from_config(self):
        files = render.write_all(self.cfg, self.S, self.F)
        md = [f for f in files if f.suffix == ".md"][0]
        md.write_text(md.read_text() + "\nCarried over from the Northwind run.\n")
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertEqual([e for e in errs if "foreign tenant" in e], [])  # not configured => not scanned
        cfg2 = Config.load(self.cfg_path)
        cfg2["foreign_tenants"] = ["Northwind"]
        ok, errs = verify.verify(cfg2, self.S, self.F, files)
        self.assertTrue(any("foreign tenant name Northwind" in e for e in errs))

    def test_adoption_and_fit_split(self):
        ad = self.S["adoption"]
        self.assertEqual(ad["factor"], 0.7)
        self.assertAlmostEqual(ad["achievable_now"]["adjusted"], self.S["totals"]["achievable_now_monthly"] * 0.7, delta=1)
        self.assertEqual(ad["resiliency_investment"]["likely"], self.S["totals"]["resiliency_range"]["likely"])  # spend not haircut
        fs = self.S["fit_split"]
        self.assertEqual(fs["achievable_now"]["InfraFit"]["usd"], 0)
        self.assertEqual(fs["blocked_pending"]["PodFit"]["usd"], 0)
        self.assertEqual(self.by["pool_shrink_free"]["value_kind"], "ceiling")
        self.assertEqual(self.by["pool_machine_recs"]["value_kind"], "estimate")
        self.assertIn("ceiling", " ".join(self.S["adoption"]["blocked_pending"]["ceiling"]["note"].split()).lower())

    def test_run_plan_refuses_overwrite(self):
        import subprocess
        cfgp = self.tmp / "acme.tenant.json"
        render.write_all(self.cfg, self.S, self.F)
        r = subprocess.run([sys.executable, "-W", "ignore", str(HERE.parent / "run_plan.py"), "--config", str(cfgp), "--serial"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 3)
        r = subprocess.run([sys.executable, "-W", "ignore", str(HERE.parent / "run_plan.py"), "--config", str(cfgp), "--serial", "--overwrite"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_v4_sections(self):
        o = self.S["outlook"]
        self.assertLessEqual(o["low"]["usd"], o["planning"]["usd"]); self.assertLessEqual(o["planning"]["usd"], o["maximum"]["usd"])
        claimed = [d["usd"] for d in self.S["decision_detail"] if d["kind"] in ("saving", "idle ceiling") and d["usd"]]
        self.assertEqual(claimed, sorted(claimed, reverse=True))
        self.assertEqual(len(self.S["cluster_idle"]), 2)
        md = render.render_plan_md(self.cfg, self.S)
        self.assertNotIn("100% PodFit", md); self.assertNotIn("Origin by PerfectScale product", md)
        self.assertIn("Planning = (Achievable now", md)
        for h in ("Planning range", "Why each cluster's idle", "in proportion", "questions that unlock claimed savings"):
            self.assertIn(h, md)

    def test_html_adoption_control_and_js_syntax(self):
        import re, shutil, subprocess
        h = render.render_html(self.cfg, self.S, self.F)
        self.assertIn("id=adopt", h); self.assertIn("data-calc='ach_adj'", h); self.assertIn("id=plan", h)
        if shutil.which("node"):
            js = re.findall(r"<script>([\s\S]*?)</script>", h)[0]
            f = self.tmp / "page.js"; f.write_text(js)
            r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_diff_matches_by_action_type(self):
        js = self.cfg.out() / "plan.json"
        js.write_text(json.dumps(self.S, default=str))
        out = diffplans.diff(js, js)
        self.assertIn("0 actions unchanged" if False else "actions unchanged", out)
        self.assertIn("## New actions\n\n- none", out)


if __name__ == "__main__":
    unittest.main()
