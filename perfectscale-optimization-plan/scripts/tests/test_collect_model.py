import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan.config import Config  # noqa: E402
from psplan import model  # noqa: E402
from psplan.collect import Collector, SustainedRateLimiting, is_rate_limited  # noqa: E402
from psplan.util import cores, gib  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src"
        fixtures.make(self.src)
        self.cfg = Config.load(self.src / "acme.tenant.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestModel(Base):
    def test_window_and_scaling(self):
        a, b = model.load_all(self.cfg, parallel=False)
        self.assertFalse(a["early"])                       # 35 days of history
        self.assertAlmostEqual(a["factor"], 1.0)            # never scale a full window
        self.assertTrue(b["early"])                         # 8 days
        self.assertAlmostEqual(b["factor"], 30 / b["window_days"], places=3)
        self.assertAlmostEqual(b["cost"], b["cost_o"] * b["factor"], places=3)

    def test_reconciles_to_summary(self):
        for f in model.load_all(self.cfg, parallel=False):
            self.assertNotIn("summary_mismatch", f["flags"])
            self.assertAlmostEqual(f["cost_o"], f["summary"]["total_cost"], places=2)

    def test_lanes(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["lanes"]["managed"]["n"], 3)     # kube-system
        self.assertEqual(a["lanes"]["stateful"]["n"], 7)    # kafka x4 + elasticsearch x3
        self.assertGreater(a["lanes"]["job"]["n"], 250)
        self.assertEqual(a["primary_framework"], "airflow")

    def test_grouping_counts_uncontrolled_pods_not_just_jobs(self):
        self.assertIn("<none>", model.UNGROUPED_JOB_TYPES)   # CI build agents
        self.assertIn("Job", model.UNGROUPED_JOB_TYPES)
        self.assertNotIn("CronJob", model.UNGROUPED_JOB_TYPES)   # one durable workload
        self.assertFalse(model.UNGROUPED_JOB_TYPES & model.GROUPED_TYPES)

    def test_is_synth_drops_placeholders_and_collapsed_aggregates(self):
        synth = [
            {"name": "__deleted-namespaces__", "namespace": "prod", "type": "Deployment"},
            {"name": "api", "namespace": "__deleted-namespaces__", "type": "Deployment"},
            {"name": "Collapsed Ephemeral Pods", "namespace": "EphemeralPods", "type": "-"},
        ]
        for w in synth:
            self.assertTrue(model.is_synth(w), w["name"])
        real = [
            {"name": "api", "namespace": "prod", "type": "Deployment"},
            {"name": "build-pod-x1y2", "namespace": "jenkins", "type": "<none>"},
            {"name": "nightly", "namespace": "prod", "type": "CronJob"},
        ]
        for w in real:
            self.assertFalse(model.is_synth(w), w["name"])

    def test_held_out_matches_namespace_as_well_as_name(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["lanes"]["held_out"]["n"], 1)    # fixture's held-out-but-not-stateful workload (manual-hold-svc)

        # "payments" matches no workload name in the fixture, only a namespace; this override replaces the
        # fixture's default held_out_from_automation entirely, so manual-hold-svc (namespace "finance") is not counted here.
        self.cfg["held_out_from_automation"] = ["^payments$"]
        b, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(b["lanes"]["held_out"]["n"], 17)   # every workload in that namespace
        self.assertEqual(b["n_workloads"], a["n_workloads"])  # reclassified, not dropped

    def test_held_out_still_matches_workload_name(self):
        self.cfg["held_out_from_automation"] = ["^kafka-broker-"]
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["lanes"]["held_out"]["n"], 4)
        self.assertEqual(a["lanes"]["stateful"]["n"], 3)    # elasticsearch only; kafka moved

    def test_grouping_recommended_only_when_many_plain_jobs(self):
        a, b = model.load_all(self.cfg, parallel=False)
        self.assertTrue(a["grouping"]["recommend"])         # 250 plain jobs >= threshold 200
        self.assertFalse(b["grouping"]["recommend"])

    def test_replicated_groups_found_with_prefix_and_sample(self):
        a, b = model.load_all(self.cfg, parallel=False)
        g = next(x for x in a["replicated"]["groups"] if x["name"] == "app-main")
        self.assertEqual(g["ns_count"], 40)                 # 40 tenant-NNN namespaces
        self.assertEqual(g["ns_prefix"], "tenant-")
        self.assertAlmostEqual(g["pot"], 320.0, places=2)   # 40 x 8.0, full window so unscaled
        self.assertTrue(g["throttled"])
        self.assertGreater(g["cpu_p100"], g["cpu_p95"] * 10)    # dormant and bursty
        self.assertEqual(b["replicated"]["n_groups"], 0)    # dev-b has one copy of each name

    def test_replicated_group_needs_both_spread_and_money(self):
        self.cfg["thresholds"]["replicated_group_min"] = 41         # 40 namespaces is now one short
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["replicated"]["n_groups"], 0)
        self.cfg["thresholds"]["replicated_group_min"] = 25
        self.cfg["thresholds"]["replicated_group_pot_min_usd"] = 321.0   # $320 is now too little to be worth a rule
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["replicated"]["n_groups"], 0)

    def test_replicated_lane_is_controllers_only(self):
        # held out by name => not an automation-lane candidate, so not a grouping candidate either
        self.cfg["held_out_from_automation"] = ["^app-main$"]
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["replicated"]["n_groups"], 0)

    def test_common_ns_prefix_cuts_at_a_segment_boundary(self):
        # the common characters run past the segment boundary ("tenant-3d"); the cut pulls them back
        self.assertEqual(model.common_ns_prefix({"tenant-3dscan", "tenant-3dprint", "tenant-3dweb"}), "tenant-")
        self.assertIsNone(model.common_ns_prefix({"payments", "search", "streaming"}))
        self.assertIsNone(model.common_ns_prefix({"solo-ns"}))       # one namespace is not a group
        self.assertIsNone(model.common_ns_prefix({"ab-one", "ab-two"}))   # stem too short to filter on

    def test_resiliency_ranges_ordered(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        r = a["res_oom"]
        self.assertLessEqual(r["low"], r["likely"])
        self.assertLessEqual(r["likely"], r["literal"])
        self.assertGreater(r["literal"], 0)
        self.assertGreater(a["res_oom_top_share"], 0.9)     # one workload dominates

    def test_namespace_resiliency_rollup(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        data_ns = next(n for n in a["namespaces"] if n["ns"] == "data")
        self.assertEqual(data_ns["risk_n"], 2)   # ingest (OOM) + worker-low, both cost_increase > 0
        self.assertEqual(data_ns["oom_n"], 1)
        self.assertGreater(data_ns["res_likely"], 0)
        self.assertLessEqual(data_ns["res_likely"], data_ns["res_literal"])
        # reconciles to the same population as the cluster-wide resiliency figures
        self.assertEqual(sum(n["risk_n"] for n in a["namespaces"]), a["res_oom"]["n"] + a["res_other"]["n"])
        self.assertAlmostEqual(sum(n["res_likely"] for n in a["namespaces"]), a["res_oom"]["likely"] + a["res_other"]["likely"], delta=1)

    def test_pool_classes_and_cross_link(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["pools"]["ap_n"], 0)             # no auto-provisioned patterns configured
        self.assertEqual(a["pools"]["ll_n"], 9)   # 4 original + 5 Karpenter structural-diff fixtures
        gpu = [p for p in a["pools"]["long_lived"] if p["id"] == "karp-gpu"][0]
        self.assertTrue(gpu["gpu"])
        self.assertGreater(a["pools"]["gpu_idle_share"], 0.2)

    def test_unevictable_patterns(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        codes = a["unevictable"]["codes"]
        self.assertEqual(codes["pod_disruption_budget"]["all"], 9)
        self.assertEqual(codes["opt_out_annotation"]["all"], 4)
        self.assertEqual(a["optout"]["controller_pods"], 4)

    def test_audit(self):
        a, b = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["audit"]["n"], 0)
        self.assertEqual(b["audit"]["n"], 10)
        self.assertEqual(b["audit"]["inplace"], 8)

    def test_exclusions_dropped_and_counted(self):
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["exclusions"] = ["kafka"]
        a, _ = model.load_all(cfg, parallel=False)
        self.assertEqual(a["n_excluded"], 4)
        self.assertGreater(a["excluded_cost"], 0)

    def test_cache_roundtrip(self):
        a1 = model.load_all(self.cfg, parallel=False)
        a2 = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a1[0]["_fingerprint"], a2[0]["_fingerprint"])

    def test_hpa_file_absent_is_unconfirmed(self):
        # Optional enrichment (see references/eligibility-enrichment.md): absent => unconfirmed, same
        # "optional enrichment, absent = unconfirmed" shape as eligibility_file.
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertFalse(a["hpa"]["provided"])
        self.assertEqual(a["hpa"]["n_hpa"], 0)
        self.assertEqual(a["hpa"]["workloads"], [])

    def test_hpa_file_marks_matching_workload(self):
        # "ingest"/"data" on prod-a is the fixture's dominant OOM workload (see test_resiliency_ranges_ordered).
        hpa_path = self.tmp / "hpa.json"
        hpa_path.write_text(json.dumps({fixtures.UIDS["prod-a"]: {"data-deployment-ingest": {"hasHpa": True, "cpuTriggerPct": 70}}}))
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["hpa_file"] = str(hpa_path)
        a, b = model.load_all(cfg, parallel=False)
        self.assertTrue(a["hpa"]["provided"])
        self.assertEqual(a["hpa"]["n_hpa"], 1)
        self.assertEqual(a["hpa"]["workloads"][0]["name"], "ingest")
        self.assertEqual(a["hpa"]["workloads"][0]["namespace"], "data")
        # a cluster with no entry in hpa_file still reports "provided: False" for itself, not a crash
        self.assertFalse(b["hpa"]["provided"])


class TestCollector(Base):
    def run_collect(self, **env):
        out = self.tmp / "collected"
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["data_dir"] = str(out)
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        os.chmod(cfg["pscli"], 0o755)
        os.environ["FAKE_PS_SRC"] = str(self.src)
        for k, v in env.items():
            os.environ[k] = v
        log = []
        try:
            c = Collector(cfg, log=log.append, min_interval=0, backoff=0)
            ok = c.run()
        finally:
            for k in env:
                os.environ.pop(k, None)
        return out, ok, log

    def test_collects_everything(self):
        out, ok, log = self.run_collect()
        self.assertTrue(ok, log)
        for n in ("prod-a", "dev-b"):
            for f in ("summary.json", "workloads-all.jsonl", "nodegroups.jsonl", "unevictable-full.jsonl", "audit.jsonl", "cluster.json", "collect-manifest.json"):
                self.assertTrue((out / n / f).exists(), f"{n}/{f}")
        # and the model can read what the collector wrote
        cfg = Config.load(self.src / "acme.tenant.json"); cfg["data_dir"] = str(out)
        self.assertEqual(len(model.load_all(cfg, parallel=False)), 2)

    def test_manifest_write_is_atomic_and_valid_json(self):
        # item 6 regression guard: the manifest is written via tmp-then-rename like the collector's
        # other writes, so it is always valid, loadable JSON (no truncated-file risk from a plain
        # json.dump straight to the final path). A true crash-mid-write simulation is awkward to do
        # cleanly here and was not separately exercised.
        out, ok, log = self.run_collect()
        for n in ("prod-a", "dev-b"):
            mpath = out / n / "collect-manifest.json"
            self.assertFalse((out / n / "collect-manifest.json.tmp").exists())
            manifest = json.loads(mpath.read_text())
            self.assertIn("workloads", manifest)
            self.assertTrue(manifest["workloads"]["ok"])

    def test_retries_on_429(self):
        out, ok, log = self.run_collect(FAKE_429_FIRST_N="3")
        self.assertTrue(ok, log)
        self.assertTrue(any("rate-limited" in l for l in log))

    def test_incremental_skip(self):
        out, ok, log = self.run_collect()
        cfg = Config.load(self.src / "acme.tenant.json"); cfg["data_dir"] = str(out); cfg["pscli"] = str(HERE / "fake_pscli.py")
        os.environ["FAKE_PS_SRC"] = str(self.src)
        log2 = []
        Collector(cfg, log=log2.append, min_interval=0, backoff=0).run()
        self.assertTrue(sum("[skip]" in l for l in log2) >= 10, log2)
        self.assertFalse(any("[ok]" in l for l in log2))

    def test_unevictable_fallback(self):
        out, ok, log = self.run_collect(FAKE_UNEV_FAIL="1")
        self.assertTrue(ok, log)
        self.assertTrue((out / "prod-a" / "unevictable-topgroups.jsonl").exists())
        self.assertFalse((out / "prod-a" / "unevictable-full.jsonl").exists() and (out / "prod-a" / "unevictable-full.jsonl").stat().st_size > 0)
        cfg = Config.load(self.src / "acme.tenant.json"); cfg["data_dir"] = str(out)
        a = model.load_all(cfg, parallel=False)[0]
        self.assertTrue(a["unevictable"]["partial"])
        self.assertEqual(a["unevictable"]["codes"]["pod_disruption_budget"]["all"], 9)
        manifest = json.loads((out / "prod-a" / "collect-manifest.json").read_text())
        self.assertTrue(manifest["unevictable_top"]["ok"])

    def test_unevictable_total_fallback_failure_is_not_ok(self):
        # cluster-wide AND every per-node-group pull fails: this must NOT be indistinguishable from a
        # clean "zero unevictable pods" answer - it has to surface as incomplete collection.
        out, ok, log = self.run_collect(FAKE_UNEV_FAIL_ALL="1")
        self.assertFalse(ok, log)
        manifest = json.loads((out / "prod-a" / "collect-manifest.json").read_text())
        self.assertFalse(manifest["unevictable_top"]["ok"])
        self.assertEqual(manifest["unevictable_top"]["groups"], manifest["unevictable_top"]["failed_groups"])
        self.assertGreater(manifest["unevictable_top"]["groups"], 0)
        self.assertTrue(any("FAIL" in l and "unevictable" in l for l in log), log)

    # ---- item 3: structured 429 detection, never substring-matched free text
    def test_backoff_is_exponential_with_cap(self):
        out = self.tmp / "collected2"
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["data_dir"] = str(out)
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        os.chmod(cfg["pscli"], 0o755)
        os.environ["FAKE_PS_SRC"] = str(self.src)
        os.environ["FAKE_429_FIRST_N"] = "3"
        try:
            log = []
            c = Collector(cfg, log=log.append, only=["prod-a"], min_interval=0, backoff=5, max_retries=5)
            with patch("psplan.collect.time.sleep"):  # don't actually wait out the computed backoff
                ok = c.run()
        finally:
            os.environ.pop("FAKE_PS_SRC", None)
            os.environ.pop("FAKE_429_FIRST_N", None)
        self.assertTrue(ok, log)
        waits = [int(m.group(1)) for l in log for m in [re.search(r"pause (\d+)s", l)] if m]
        self.assertEqual(waits, [5, 10, 20], log)  # backoff * 2**(n-1): genuinely exponential, not linear

    def test_backoff_capped_at_max(self):
        out = self.tmp / "collected3"
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["data_dir"] = str(out)
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        os.chmod(cfg["pscli"], 0o755)
        os.environ["FAKE_PS_SRC"] = str(self.src)
        os.environ["FAKE_429_FIRST_N"] = "3"
        try:
            log = []
            c = Collector(cfg, log=log.append, only=["prod-a"], min_interval=0, backoff=50, max_retries=5)
            with patch("psplan.collect.time.sleep"):  # don't actually wait out the computed backoff
                ok = c.run()
        finally:
            os.environ.pop("FAKE_PS_SRC", None)
            os.environ.pop("FAKE_429_FIRST_N", None)
        self.assertTrue(ok, log)
        waits = [int(m.group(1)) for l in log for m in [re.search(r"pause (\d+)s", l)] if m]
        self.assertEqual(waits, [50, 60, 60], log)  # 50*2, 50*4 both exceed the 60s cap

    def test_circuit_breaker_trips_on_sustained_throttling(self):
        out = self.tmp / "collected4"
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["data_dir"] = str(out)
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        os.chmod(cfg["pscli"], 0o755)
        os.environ["FAKE_PS_SRC"] = str(self.src)
        os.environ["FAKE_429_FIRST_N"] = "1000"  # every call is rate-limited, forever
        try:
            log = []
            c = Collector(cfg, log=log.append, only=["prod-a"], min_interval=0, backoff=0, max_retries=1, circuit_breaker=2)
            ok = c.run()
        finally:
            os.environ.pop("FAKE_PS_SRC", None)
            os.environ.pop("FAKE_429_FIRST_N", None)
        self.assertFalse(ok, log)
        self.assertTrue(any("ABORTED" in l and "rate limit" in l for l in log), log)
        self.assertTrue(any("circuit breaker" in f for f in c.failures), c.failures)

    def test_rate_limit_detection_structured_not_substring(self):
        # a genuine structured RATE_LIMITED envelope must trigger detection
        self.assertTrue(is_rate_limited('{"error":{"code":"RATE_LIMITED","message":"status 429"}}'))
        self.assertTrue(is_rate_limited("some preamble\nRATE_LIMITED\ntrailer"))
        # a dollar figure containing "429" must never false-trigger it, whether free text or in a
        # structured envelope describing an unrelated error
        self.assertFalse(is_rate_limited("budget exceeded: cost is $429.10 this month"))
        self.assertFalse(is_rate_limited('{"error":{"code":"BUDGET_EXCEEDED","message":"cost is $429.10"}}'))
        self.assertFalse(is_rate_limited(""))
        self.assertFalse(is_rate_limited(None))

    def test_rate_limit_not_triggered_by_dollar_figure_end_to_end(self):
        out = self.tmp / "collected5"
        cfg = Config.load(self.src / "acme.tenant.json")
        cfg["data_dir"] = str(out)
        cfg["pscli"] = str(HERE / "fake_pscli.py")
        os.chmod(cfg["pscli"], 0o755)
        os.environ["FAKE_PS_SRC"] = str(self.src)
        os.environ["FAKE_COST_ERROR"] = "1"
        try:
            log = []
            c = Collector(cfg, log=log.append, only=["prod-a"], min_interval=0, backoff=0, max_retries=5)
            ok = c.run()
        finally:
            os.environ.pop("FAKE_PS_SRC", None)
            os.environ.pop("FAKE_COST_ERROR", None)
        self.assertFalse(ok, log)  # fails fast rather than retrying 5 times as if rate-limited
        self.assertFalse(any("rate-limited" in l for l in log), log)
        self.assertTrue(any("FAIL" in l and "429.10" in l for l in log), log)


class TestConfigValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "src"
        fixtures.make(self.src)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_cfg(self, **overrides):
        raw = json.loads((self.src / "acme.tenant.json").read_text())
        raw.update(overrides)
        p = self.src / "variant.tenant.json"
        p.write_text(json.dumps(raw))
        return p

    def test_loaded_config_does_not_share_nested_defaults(self):
        # A shallow merge handed back the DEFAULTS dicts themselves, so adjusting one threshold on a
        # loaded config rewrote the default for every config loaded afterwards in the same process.
        from psplan.config import DEFAULTS
        before = DEFAULTS["thresholds"]["pool_idle_min_usd"]
        a = Config.load(self.src / "acme.tenant.json")
        a["thresholds"]["pool_idle_min_usd"] = before + 1234
        a["stateful_patterns"].append("not-a-real-engine")
        b = Config.load(self.src / "acme.tenant.json")
        self.assertEqual(b["thresholds"]["pool_idle_min_usd"], before)
        self.assertEqual(DEFAULTS["thresholds"]["pool_idle_min_usd"], before)
        self.assertNotIn("not-a-real-engine", b["stateful_patterns"])

    def test_cluster_missing_uid_raises_clear_config_error(self):
        p = self._write_cfg(clusters=[{"name": "prod-a"}])
        with self.assertRaises(SystemExit) as cm:
            Config.load(p)
        msg = str(cm.exception)
        self.assertIn("uid", msg)
        self.assertIn("prod-a", msg)

    def test_cluster_missing_name_raises_clear_config_error(self):
        p = self._write_cfg(clusters=[{"uid": "11111111-aaaa-4aaa-8aaa-111111111111"}])
        with self.assertRaises(SystemExit) as cm:
            Config.load(p)
        self.assertIn("name", str(cm.exception))

    def test_invalid_regex_pattern_raises_clear_config_error(self):
        p = self._write_cfg(held_out_from_automation=["foo(bar"])
        with self.assertRaises(SystemExit) as cm:
            Config.load(p)
        msg = str(cm.exception)
        self.assertIn("held_out_from_automation", msg)
        self.assertIn("foo(bar", msg)

    def test_unanchored_pattern_triggers_warning_not_failure(self):
        p = self._write_cfg(held_out_from_automation=["prod"])
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            cfg = Config.load(p)  # must not raise - this is a warning, not an error
        self.assertEqual(cfg["held_out_from_automation"], ["prod"])
        warning = buf.getvalue()
        self.assertIn("held_out_from_automation", warning)
        self.assertIn("prod", warning)
        self.assertIn("unanchored", warning)

    def test_shipped_default_patterns_do_not_warn(self):
        # stateful_patterns/nonprod_patterns/gpu_pool_patterns ship deliberately unanchored keyword
        # lists; untouched defaults must not generate noise.
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            Config.load(self.src / "acme.tenant.json")
        self.assertNotIn("stateful_patterns", buf.getvalue())
        self.assertNotIn("nonprod_patterns", buf.getvalue())
        self.assertNotIn("gpu_pool_patterns", buf.getvalue())

    def test_native_vpa_defaults_to_none(self):
        # Unset, like gitops: an open assumption, never guessed into a fact.
        cfg = Config.load(self.src / "acme.tenant.json")
        self.assertIsNone(cfg["assumptions"]["native_vpa"])

    def test_native_vpa_set_value_is_readable_from_loaded_config(self):
        p = self._write_cfg(assumptions={"native_vpa": "on the checkout namespace"})
        cfg = Config.load(p)
        self.assertEqual(cfg["assumptions"]["native_vpa"], "on the checkout namespace")
        # merging a partial assumptions override must not clobber the other assumptions keys
        self.assertIn("scope", cfg["assumptions"])
        self.assertIn("delivery", cfg["assumptions"])


class TestQuantityFormatting(unittest.TestCase):
    """A small non-zero request must never render as '0'.

    Printing a 0.2-core request as '0 cores' states that the workload requests
    nothing, which is a different finding from the one the data supports.
    """

    def test_small_non_zero_never_renders_as_zero(self):
        for v in (0.2, 0.05, 0.011, 0.5, 0.99):
            for fn in (cores, gib):
                self.assertNotEqual(fn(v), "0", f"{fn.__name__}({v})")

    def test_exact_zero_is_zero(self):
        self.assertEqual(cores(0), "0")
        self.assertEqual(cores(0.0), "0")
        self.assertEqual(cores(None), "0")
        self.assertEqual(gib(0), "0")

    def test_below_floor_shows_a_bound(self):
        self.assertEqual(cores(0.004), "<0.01")
        self.assertEqual(cores(-0.004), "-<0.01")

    def test_precision_falls_away_as_the_value_grows(self):
        self.assertEqual(cores(0.25), "0.25")
        self.assertEqual(cores(2.5), "2.5")
        self.assertEqual(cores(42.4), "42")
        self.assertEqual(cores(1536.7), "1,537")

    def test_rounding_cannot_cross_into_zero(self):
        # 0.49 rounds to 0 at .0f; the helper must keep it visible
        self.assertEqual(cores(0.49), "0.49")


class TestKarpenterDiff(unittest.TestCase):
    """Unit tests for model.diff_karpenter_config - the structural diff behind the
    pool_karpenter_tuning action, exercised directly against minimal NodePool CR fragments
    rather than through a full cluster fixture."""

    def np(self, consolidation_policy="WhenEmpty", consolidate_after="5m", cpu_limit="64", memory_limit=None, requirements=None):
        spec = {"disruption": {"consolidationPolicy": consolidation_policy, "consolidateAfter": consolidate_after},
                "limits": {"cpu": cpu_limit}, "template": {"spec": {"requirements": requirements or []}}}
        if memory_limit is not None:
            spec["limits"]["memory"] = memory_limit
        return {"spec": spec}

    def req(self, key, values):
        return {"key": key, "operator": "In", "values": values}

    def kinds(self, changes):
        return {c["kind"] for c in changes}

    def test_no_diff_on_identical_configs(self):
        cur = self.np()
        self.assertEqual(model.diff_karpenter_config(cur, self.np()), [])

    def test_consolidation_policy_change(self):
        c = model.diff_karpenter_config(self.np(consolidation_policy="WhenEmpty"), self.np(consolidation_policy="WhenEmptyOrUnderutilized"))
        self.assertEqual(self.kinds(c), {"consolidation_policy"})
        row = c[0]
        self.assertEqual((row["from"], row["to"]), ("WhenEmpty", "WhenEmptyOrUnderutilized"))

    def test_consolidate_after_change(self):
        c = model.diff_karpenter_config(self.np(consolidate_after="5m"), self.np(consolidate_after="30m"))
        self.assertEqual(self.kinds(c), {"consolidate_after"})
        self.assertEqual((c[0]["from"], c[0]["to"]), ("5m", "30m"))

    def test_limits_cpu_near_exhaustion(self):
        c = model.diff_karpenter_config(self.np(cpu_limit="64"), self.np(cpu_limit="128"))
        self.assertEqual(self.kinds(c), {"limits_cpu"})
        self.assertEqual((c[0]["from"], c[0]["to"]), ("64", "128"))

    def test_limits_memory_newly_added_is_a_valid_change(self):
        cur = self.np()  # no memory limit set at all
        rec = self.np(memory_limit="384Gi")
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"limits_memory"})
        self.assertIsNone(c[0]["from"])
        self.assertEqual(c[0]["to"], "384Gi")

    def test_static_nodepool_detection_same_key_broadened(self):
        cur = self.np(requirements=[self.req("node.kubernetes.io/instance-type", ["m5.xlarge"])])
        rec = self.np(requirements=[self.req("node.kubernetes.io/instance-type", ["m5.xlarge", "m5.2xlarge", "m5.4xlarge", "m5.large"])])
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"static_nodepool_detection"})

    def test_instance_type_pinning_prevention_key_dropped_for_broader_selector(self):
        cur = self.np(requirements=[self.req("node.kubernetes.io/instance-type", ["m5.xlarge"])])
        rec = self.np(requirements=[self.req("karpenter.k8s.aws/instance-family", ["m5", "m6i", "m7i"])])
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"instance_type_pinning_prevention", "instance_family_modernization"})
        pin = next(x for x in c if x["kind"] == "instance_type_pinning_prevention")
        self.assertEqual(pin["from"], ["m5.xlarge"])
        self.assertIsNone(pin["to"])

    def test_instance_family_modernization(self):
        cur = self.np(requirements=[self.req("karpenter.k8s.aws/instance-family", ["m5"])])
        rec = self.np(requirements=[self.req("karpenter.k8s.aws/instance-family", ["m7i", "m7a"])])
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"instance_family_modernization"})
        self.assertEqual((c[0]["from"], c[0]["to"]), (["m5"], ["m7a", "m7i"]))

    def test_instance_category_change(self):
        cur = self.np(requirements=[self.req("karpenter.k8s.aws/instance-category", ["c"])])
        rec = self.np(requirements=[self.req("karpenter.k8s.aws/instance-category", ["c", "m"])])
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"instance_category_change"})

    def test_instance_generation_flexibility(self):
        cur = self.np(requirements=[self.req("karpenter.k8s.aws/instance-generation", ["5"])])
        rec = self.np(requirements=[])
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"instance_generation_flexibility"})
        self.assertEqual(c[0]["from"], ["5"])
        self.assertIsNone(c[0]["to"])

    def test_multiple_independent_kinds_in_one_diff(self):
        cur = self.np(consolidation_policy="WhenEmpty", cpu_limit="64")
        rec = self.np(consolidation_policy="WhenEmptyOrUnderutilized", cpu_limit="128")
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"consolidation_policy", "limits_cpu"})

    # ---- defensive handling: never raise on an unexpected shape, just skip that diff
    def test_non_dict_config_returns_empty(self):
        self.assertEqual(model.diff_karpenter_config("not-a-dict", self.np()), [])
        self.assertEqual(model.diff_karpenter_config(self.np(), None), [])
        self.assertEqual(model.diff_karpenter_config(None, None), [])

    def test_requirements_not_a_list_is_skipped_not_raised(self):
        cur = self.np(requirements="not-a-list")
        rec = self.np(requirements=[self.req("karpenter.k8s.aws/instance-category", ["c"])])
        # must not raise; the malformed side just contributes no requirements
        c = model.diff_karpenter_config(cur, rec)
        self.assertEqual(self.kinds(c), {"instance_category_change"})

    def test_malformed_requirement_entry_is_dropped(self):
        cur = self.np(requirements=["not-a-dict", {"key": None, "values": ["x"]}, {"key": "k", "values": "not-a-list"}])
        rec = self.np(requirements=[])
        # none of the malformed entries should surface as a change or raise
        self.assertEqual(model.diff_karpenter_config(cur, rec), [])


if __name__ == "__main__":
    unittest.main()
