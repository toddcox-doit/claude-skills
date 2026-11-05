import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan.config import Config  # noqa: E402
from psplan import model  # noqa: E402
from psplan.collect import Collector  # noqa: E402


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
        self.assertEqual(a["lanes"]["held_out"]["n"], 0)    # nothing held out by default

        # "payments" matches no workload name in the fixture, only a namespace.
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

    def test_resiliency_ranges_ordered(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        r = a["res_oom"]
        self.assertLessEqual(r["low"], r["likely"])
        self.assertLessEqual(r["likely"], r["literal"])
        self.assertGreater(r["literal"], 0)
        self.assertGreater(a["res_oom_top_share"], 0.9)     # one workload dominates

    def test_pool_classes_and_cross_link(self):
        a, _ = model.load_all(self.cfg, parallel=False)
        self.assertEqual(a["pools"]["ap_n"], 0)             # no auto-provisioned patterns configured
        self.assertEqual(a["pools"]["ll_n"], 3)
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


if __name__ == "__main__":
    unittest.main()
