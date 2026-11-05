import copy
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
from psplan.util import usd  # noqa: E402


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
        for k in ("automation_proof", "oom_fix", "memory_limits", "stale_optout", "pdb", "unevictable_affinity", "job_grouping", "job_rightsize",
                   "stateful_manual", "heldout_manual", "headroom_policy", "pool_machine_recs", "rerun",
                   "autoscaler_labels", "pool_flex", "jvm_rightsize", "gpu_cap", "underprov_other", "local_storage", "default_policy", "commitments"):
            self.assertIn(k, self.by)
        self.assertNotIn("autoprov_review", self.by)       # no auto-provisioned pools in this tenant

    def test_proof_cluster_is_the_one_with_audit_history(self):
        self.assertEqual(self.by["automation_proof"]["cluster"], "dev-b")

    def test_replicated_grouping_claims_no_dollars_of_its_own(self):
        a = self.by["replicated_grouping"]
        self.assertEqual(a["action_type"], "custom_grouping")
        self.assertEqual(a["savings_monthly_usd"], 0)       # the money is counted in the automation waves
        self.assertEqual(a["effort_tier"], "S")             # catalog floor, no escalator applies
        self.assertEqual(a["owner_role"], "ps-csm")
        self.assertEqual(a["change_risk"], "low")
        self.assertEqual(a["phase"], 1)
        self.assertTrue(a["is_unblocker"])

    def test_replicated_grouping_precedes_the_automation_waves(self):
        A = self.S["actions"]; pos = {x["id"]: i for i, x in enumerate(A)}
        g = self.by["replicated_grouping"]
        waves = [self.by[k] for k in ("automation_w2", "automation_w3") if k in self.by]
        self.assertTrue(waves, "fixture should produce at least one automation wave")
        for w in waves:
            self.assertIn(g["id"], w["depends_on"])         # catalog edge: group first, then one policy
            self.assertLess(pos[g["id"]], pos[w["id"]])
            self.assertIn("grouping rule first", " ".join(w["steps"]))
        # the proof action is deliberately left free of it: a few controllers need no grouping
        self.assertNotIn(g["id"], self.by["automation_proof"]["depends_on"])

    def test_replicated_grouping_names_the_bursty_shape_and_the_real_prefix(self):
        a = self.by["replicated_grouping"]
        self.assertIn("inferred - cause not confirmed", a["evidence"])   # the shape claim is labelled
        self.assertIn("tenant-", " ".join(a["steps"]))                   # filter written on the observed prefix
        self.assertIn("app-main", a["title"])

    def test_replicated_savings_counted_once(self):
        # the copies' dollars reach the plan through the automation lane; grouping must not re-add them
        tot = sum(x["savings_monthly_usd"] or 0 for x in self.S["actions"] if x["savings_basis"] == "achievable_now")
        self.assertLessEqual(round(tot), self.S["pod"]["potential"])

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

    # ---- item 8: headline totals are independently recomputed from the rendered plan JSON on disk,
    # not only checked for self-consistency against the in-memory S that produced it
    def test_verifier_recomputes_headline_from_rendered_json_on_disk(self):
        files = render.write_all(self.cfg, self.S, self.F)
        plan_json = [f for f in files if f.suffix == ".json" and "optimization-plan" in f.name][0]
        data = json.loads(plan_json.read_text(encoding="utf-8"))
        data["totals"]["achievable_now_monthly"] += 999999  # corrupt only the on-disk headline
        plan_json.write_text(json.dumps(data), encoding="utf-8")  # action list is untouched
        # the in-memory S handed to verify() is still the original, untampered plan - the existing
        # self-consistency check (S against itself) would pass; only the independent recompute against
        # the actual written artifact catches this.
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertTrue(any("rendered plan JSON" in e and "achievable_now_monthly" in e for e in errs), errs)

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

    def test_leak_scan_exempts_exact_customer_name_only_not_substring(self):
        # a past tenant "Acme" must still be flagged for a *different* current customer whose name merely
        # contains "Acme" as a substring ("Acme Industries") - substring containment wrongly exempted it.
        files = render.write_all(self.cfg, self.S, self.F)
        md = [f for f in files if f.suffix == ".md"][0]
        md.write_text(md.read_text() + "\nLegacy data carried over from Acme.\n")
        cfg2 = Config.load(self.cfg_path)
        cfg2["customer"] = "Acme Industries"
        cfg2["foreign_tenants"] = ["Acme"]
        ok, errs = verify.verify(cfg2, self.S, self.F, files)
        self.assertTrue(any("foreign tenant name Acme" in e for e in errs), errs)

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

    def test_diff_matches_by_natural_key_not_rank(self):
        # Three same-type actions on three different clusters; the global $-rank order within that
        # action_type changes between the old and new run (C overtakes A and B), but A and B are each
        # individually *unchanged* under their own identity. Rank-based zip pairing (the old behaviour)
        # would mismatch all three against each other and report false changes; natural_key-based
        # pairing must recognize A and B as unchanged and only flag C as genuinely changed. Also covers
        # a true add and a true removal.
        old_actions = [
            {"id": "A1", "title": "Rightsize A", "natural_key": "clusterX:ruleA:rightsize", "savings_monthly_usd": 1000, "phase": 1, "effort_tier": "S", "savings_basis": "achievable_now"},
            {"id": "B1", "title": "Rightsize B", "natural_key": "clusterY:ruleB:rightsize", "savings_monthly_usd": 500, "phase": 1, "effort_tier": "S", "savings_basis": "achievable_now"},
            {"id": "C1", "title": "Rightsize C", "natural_key": "clusterZ:ruleC:rightsize", "savings_monthly_usd": 200, "phase": 1, "effort_tier": "S", "savings_basis": "achievable_now"},
            {"id": "D1", "title": "Dropped rule", "natural_key": "clusterZ:ruleD:other", "savings_monthly_usd": 300, "phase": 2, "effort_tier": "M", "savings_basis": "achievable_now"},
        ]
        new_actions = [
            {"id": "A2", "title": "Rightsize A", "natural_key": "clusterX:ruleA:rightsize", "savings_monthly_usd": 1000, "phase": 1, "effort_tier": "S", "savings_basis": "achievable_now"},
            {"id": "B2", "title": "Rightsize B", "natural_key": "clusterY:ruleB:rightsize", "savings_monthly_usd": 500, "phase": 1, "effort_tier": "S", "savings_basis": "achievable_now"},
            {"id": "C2", "title": "Rightsize C", "natural_key": "clusterZ:ruleC:rightsize", "savings_monthly_usd": 1500, "phase": 1, "effort_tier": "S", "savings_basis": "achievable_now"},
            {"id": "E2", "title": "New rule", "natural_key": "clusterW:ruleE:new_type", "savings_monthly_usd": 400, "phase": 1, "effort_tier": "XS", "savings_basis": "achievable_now"},
        ]
        pairs, added, removed = diffplans.pair(old_actions, new_actions)
        self.assertEqual({o["id"] for o, n in pairs}, {"A1", "B1", "C1"})
        self.assertEqual([a["id"] for a in added], ["E2"])
        self.assertEqual([a["id"] for a in removed], ["D1"])
        by_old_id = {o["id"]: (o, n) for o, n in pairs}
        oA, nA = by_old_id["A1"]
        self.assertEqual(nA["id"], "A2")
        self.assertEqual(oA["savings_monthly_usd"], nA["savings_monthly_usd"])
        oB, nB = by_old_id["B1"]
        self.assertEqual(nB["id"], "B2")
        self.assertEqual(oB["savings_monthly_usd"], nB["savings_monthly_usd"])
        oC, nC = by_old_id["C1"]
        self.assertEqual(nC["id"], "C2")
        self.assertNotEqual(oC["savings_monthly_usd"], nC["savings_monthly_usd"])

        op, npth = self.tmp / "old.json", self.tmp / "new.json"
        op.write_text(json.dumps({"totals": {}, "actions": old_actions}))
        npth.write_text(json.dumps({"totals": {}, "actions": new_actions}))
        out = diffplans.diff(op, npth)
        self.assertIn("2 actions unchanged in value, phase, effort and title.", out)
        self.assertIn("C1 Rightsize C", out)   # the genuinely changed action is reported
        self.assertIn("## New actions\n\n- E2 New rule (400/mo)", out)
        self.assertIn("## Dropped actions\n\n- D1 Dropped rule (300/mo)", out)
        self.assertNotIn("A1 Rightsize A | B2", out)  # no cross-wired rank-based mismatch
        self.assertNotIn("B1 Rightsize B | A2", out)

    def test_diff_self_compare_is_a_clean_no_op(self):
        js = self.cfg.out() / "plan.json"
        js.write_text(json.dumps(self.S, default=str))
        out = diffplans.diff(js, js)
        self.assertIn(f"{len(self.S['actions'])} actions unchanged in value, phase, effort and title.", out)
        self.assertIn("## New actions\n\n- none", out)
        self.assertIn("## Dropped actions\n\n- none", out)

    def test_unblocker_value_never_in_dollar_column(self):
        unblockers = [a for a in self.S["actions"] if not a["savings_monthly_usd"] and a.get("released_value")]
        self.assertTrue(unblockers, "fixture should produce at least one unblocker action")
        md = render.render_plan_md(self.cfg, self.S)
        h = render.render_html(self.cfg, self.S, self.F)
        for a in unblockers:
            # money() must never surface released_value as this row's own dollar figure
            self.assertEqual(render.money(a), "—")
            tag = f"releases {usd(a['released_value'])}/mo"
            self.assertIn(tag, md)
            self.assertIn(tag, h)

    def test_dangling_depends_on_does_not_crash_render(self):
        S2 = copy.deepcopy(self.S)
        acts = S2["actions"]
        target = next((a for a in acts if a["depends_on"]), acts[0])
        target["depends_on"].append("ZZZ-nonexistent")
        # None of these should raise, and the bogus id must not leak into the diagram.
        h = render.render_html(self.cfg, S2, self.F)
        self.assertNotIn("ZZZ-nonexistent", h)
        md = render.render_plan_md(self.cfg, S2)
        self.assertIsInstance(md, str)
        made = render.render_tables(self.cfg, S2, self.F, self.cfg.out())
        self.assertTrue(any(f.suffix == ".xlsx" for f in made))

    def test_risk_icon_and_word_paired(self):
        md = render.render_plan_md(self.cfg, self.S)
        h = render.render_html(self.cfg, self.S, self.F)
        levels = {a["change_risk"] for a in self.S["actions"]}
        self.assertTrue(levels)
        for level in levels:
            expected = render.RISK_ICON[level]
            self.assertIn(expected, md, level)
            self.assertIn(expected, h, level)

    def test_annual_run_rate_near_headline_totals(self):
        t = self.S["totals"]
        md = render.render_plan_md(self.cfg, self.S)
        for key in ("achievable_now_monthly", "blocked_pending_monthly"):
            annual = usd(t[key] * 12)
            self.assertIn(f"{annual}/yr run-rate", md, key)
            self.assertIn("illustrative", md)

    # ---- item 1: the change-freeze / approval-lead-time escalator actually escalates effort
    def test_change_freeze_escalates_automation_effort(self):
        cfg2 = Config.load(self.cfg_path)
        cfg2["intake"]["change_freeze"] = True
        S2 = Plan(cfg2, self.F).build()
        by2 = {a["key"]: a for a in S2["actions"]}
        self.assertIn("automation_w2", self.by)
        self.assertIn("automation_w2", by2)
        tiers = ["XS", "S", "M", "L"]
        before = tiers.index(self.by["automation_w2"]["effort_tier"])
        after = tiers.index(by2["automation_w2"]["effort_tier"])
        self.assertGreater(after, before, "change_freeze=True on a prod cluster should raise the effort tier")
        self.assertIn("change-managed", by2["automation_w2"]["effort_rationale"])
        self.assertNotIn("change-managed", self.by["automation_w2"]["effort_rationale"])

    # ---- item 2: strict anti-/node-affinity produces an unevictable_antiaffinity action
    def test_unevictable_affinity_rule_fires(self):
        a = self.by["unevictable_affinity"]
        self.assertEqual(a["action_type"], "unevictable_antiaffinity")
        self.assertEqual(a["savings_basis"], "blocked_pending")
        # fixture: 5 anti_affinity + 2 node_affinity pods, both on karp-general
        self.assertIn("7 pods", a["scope"])
        self.assertIn("anti-affinity", a["scope"])
        self.assertIn("node affinity", a["scope"])
        self.assertIn("unevictable_antiaffinity", a["evidence"])

    # ---- item 4: held-out-but-not-stateful gets rightsize_manual at the lighter floor, not rightsize_stateful
    def test_heldout_non_stateful_uses_rightsize_manual(self):
        held = self.by["heldout_manual"]
        stateful = self.by["stateful_manual"]
        self.assertEqual(held["action_type"], "rightsize_manual")
        self.assertEqual(stateful["action_type"], "rightsize_stateful")
        self.assertEqual(held["effort_tier"], "S")
        self.assertEqual(stateful["effort_tier"], "M")
        self.assertEqual(held["change_risk"], "medium")
        self.assertEqual(stateful["change_risk"], "high")

    # ---- item 5: a stable natural_key, unique across clusters and deterministic across re-runs
    def test_natural_key_present_unique_and_deterministic(self):
        A = self.S["actions"]
        for a in A:
            self.assertIn("natural_key", a)
            self.assertTrue(a["natural_key"])
        # two different clusters' automation waves get different natural_keys
        self.assertNotEqual(self.by["automation_proof"]["natural_key"], self.by["automation_w2"]["natural_key"])
        prod_a_uid = next(f["uid"] for f in self.F if f["name"] == "prod-a")
        self.assertIn(prod_a_uid, self.by["automation_w2"]["natural_key"])
        # re-running the same rule set on the same facts reproduces the same keys
        S2 = Plan(self.cfg, self.F).build()
        keys1 = [a["natural_key"] for a in self.S["actions"]]
        keys2 = [a["natural_key"] for a in S2["actions"]]
        self.assertEqual(sorted(keys1), sorted(keys2))
        # same action_type, different node groups (item 6) -> different natural_keys
        free = [a for a in A if a["key"] == "pool_shrink_free"]
        self.assertGreaterEqual(len(free), 2)
        self.assertEqual(len({a["natural_key"] for a in free}), len(free))

    # ---- item 6: per-node-group pool actions, not one tenant-wide aggregate
    def test_pool_actions_are_per_node_group(self):
        free = [a for a in self.S["actions"] if a["key"] == "pool_shrink_free"]
        recs = [a for a in self.S["actions"] if a["key"] == "pool_machine_recs"]
        # fixture has 3 distinct free-bucket node groups (karp-batch, karp-gpu, karp-unknown-pinned) with
        # different blocker profiles, and 2 distinct machine-recs node groups across 2 clusters
        self.assertEqual(len(free), 3)
        self.assertEqual(len(recs), 2)
        pool_ids = {a["scope"].split("`")[1] for a in free}
        self.assertEqual(pool_ids, {"karp-batch", "karp-gpu", "karp-unknown-pinned"})
        by_pool = {a["scope"].split("`")[1]: a for a in free}
        self.assertEqual(by_pool["karp-batch"]["savings_monthly_usd"], 900)
        self.assertEqual(by_pool["karp-gpu"]["savings_monthly_usd"], 1400)
        self.assertEqual(by_pool["karp-unknown-pinned"]["savings_monthly_usd"], 80)
        # karp-general carries PDB + opt-out + affinity blockers and has a machine-type rec: its
        # pool_machine_recs action must depend on the (per-group-relevant) stale_optout action
        general = next(a for a in recs if "karp-general" in a["scope"])
        self.assertTrue(general["depends_on"])
        dep_keys = {next(x["key"] for x in self.S["actions"] if x["id"] == d) for d in general["depends_on"]}
        self.assertIn("stale_optout", dep_keys)
        # karp-dev (dev-b) has no blockers at all, so its machine-type action has no dependency
        devrec = next(a for a in recs if "karp-dev" in a["scope"])
        self.assertEqual(devrec["depends_on"], [])

    def test_pool_blocked_vs_free_classification_threshold(self):
        # karp-general's blockers (9 pdb + 4 opt-out + 5 anti + 2 node-affinity = 20) would clear the
        # blocked-bucket threshold (>=5) on blocker count alone, but it carries a machine-type recommendation,
        # so the rec branch wins and it never becomes a pool_shrink_blocked/free action.
        self.assertFalse(any("karp-general" in a["scope"] for a in self.S["actions"] if a["key"] in ("pool_shrink_blocked", "pool_shrink_free")))
        self.assertTrue(any("karp-general" in a["scope"] for a in self.S["actions"] if a["key"] == "pool_machine_recs"))
        # karp-batch has 3 blockers (< 5) and no rec -> free, not blocked
        self.assertTrue(any(a["key"] == "pool_shrink_free" and "karp-batch" in a["scope"] for a in self.S["actions"]))
        self.assertFalse(any(a["key"] == "pool_shrink_blocked" and "karp-batch" in a["scope"] for a in self.S["actions"]))

    # ---- item 7: presence + dollar-value coverage for previously-untested rules
    def test_autoscaler_labels_rule(self):
        a = self.by["autoscaler_labels"]
        self.assertEqual(a["action_type"], "other")
        self.assertIn("rationale", a)
        self.assertIn("1 of", a["scope"])

    def test_pool_flex_rule(self):
        a = self.by["pool_flex"]
        self.assertEqual(a["action_type"], "autoscaler_tune_karpenter")   # aws fixture
        self.assertIn("80", a["scope"].replace(",", ""))

    def test_jvm_rule(self):
        a = self.by["jvm_rightsize"]
        self.assertEqual(a["action_type"], "rightsize_runtime_managed")
        self.assertIn("1 workloads", a["scope"])
        self.assertIn("jvmsvc-cache", a["scope"])

    def test_gpu_rule(self):
        a = self.by["gpu_cap"]
        self.assertEqual(a["action_type"], "gpu_cap")
        self.assertGreater(a["cluster"].count("prod-a"), 0)

    def test_underprov_rule(self):
        a = self.by["underprov_other"]
        self.assertEqual(a["action_type"], "rightsize_underprovisioned")
        self.assertEqual(a["savings_basis"], "resiliency_investment")
        self.assertGreater(a["range"]["literal"], 0)
        self.assertLessEqual(a["range"]["low"], a["range"]["likely"])
        self.assertLessEqual(a["range"]["likely"], a["range"]["literal"])

    def test_local_storage_rule(self):
        a = self.by["local_storage"]
        self.assertEqual(a["action_type"], "unevictable_localpv")
        self.assertIn("3 pods", a["scope"])

    def test_governance_default_policy_and_commitments(self):
        dp = self.by["default_policy"]
        self.assertEqual(dp["action_type"], "policy_default_new_ns")
        self.assertTrue(dp["depends_on"])
        c = self.by["commitments"]
        self.assertEqual(c["action_type"], "commitment_realignment")
        self.assertTrue(c["depends_on"])


if __name__ == "__main__":
    unittest.main()
