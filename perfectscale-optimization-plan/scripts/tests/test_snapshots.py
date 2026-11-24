"""Cluster snapshots from the S3 export: selection, pull command construction, digest contents."""
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from psplan import snapshots as S  # noqa: E402

TID = "11111111-2222-3333-4444-555555555555"
UID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CFG = {"clusters": [{"name": "prod-a", "uid": UID}],
       "snapshots": {"s3_prefix": "s3://example-bucket/raw", "tenant_id": TID}}


def hpa(name, mn, mx, metrics, target=None):
    return {"name": name, "namespace": "shop", "minReplicas": mn, "maxReplicas": mx,
            "scaleTarget": {"kind": "Deployment", "name": target or name}, "metrics": metrics}


def res(kind, util):
    return {"type": "Resource", "resource": {"name": kind, "target": {"type": "Utilization", "averageUtilization": util}}}


def doc(replicas=2, nodes=3):
    labels = {"app": "cart"}
    pod = {"name": "cart-abc-1", "namespace": "shop", "phase": "Running", "labels": labels,
           "ownerReferences": [{"kind": "ReplicaSet", "name": "cart-abc"}],
           "annotations": {"kubectl.kubernetes.io/last-applied-configuration": '{"env":[{"name":"DB_USER","value":"s3cr3t"}]}',
                           S.EVICT: "false"},
           "containers": [{"name": "app", "image": "registry.example/cart:1", "resources": {}}],
           "volumes": [{"name": "scratch", "emptyDir": "disk"}]}
    exporter = {"name": "ps-exp-1", "namespace": "perfectscale", "phase": "Running", "labels": {},
                "ownerReferences": [{"kind": "ReplicaSet", "name": "ps-exp-1"}],
                "containers": [{"name": "exporter", "image": "public.ecr.aws/perfectscale-io/psc-exporter:v1.1.43",
                                "resources": {"requests": {"cpuMillicores": 100}}}]}
    return {"cluster": {
        "pods": [pod, exporter],
        "replicaSets": [{"name": "cart-abc", "namespace": "shop", "ownerReferences": [{"kind": "Deployment", "name": "cart"}]}],
        "deployments": [{"name": "cart", "namespace": "shop", "replicas": replicas},
                        {"name": "search", "namespace": "shop", "replicas": 2},
                        {"name": "queue", "namespace": "shop", "replicas": 4}],
        "hpas": [hpa("cart", 3, 10, [res("cpu", 50), res("memory", 90)]),
                 hpa("search", 2, 2, [res("cpu", 90)]),
                 hpa("queue", 1, 8, [{"type": "External", "external": {"metric": {"name": "lag"},
                                                                      "target": {"type": "AverageValue", "averageValue": "450"}}}])],
        "pdbs": [{"name": "cart", "namespace": "shop", "selector": {"matchLabels": labels},
                  "minAvailable": {"type": 1, "strVal": "60%"}, "disruptionsAllowed": 0, "expectedPods": 2}],
        "nodes": [{"name": f"n{i}", "instanceType": "n4-standard-8",
                   "labels": {"cloud.google.com/gke-nodepool": "pool-a", "kubernetes.io/arch": "amd64"},
                   "nodeAutoscalerType": "cluster_autoscaler",
                   "allocatable": {"cpuMillicores": 8000, "memoryBytes": 32 << 30},
                   "allocated": {"cpuMillicores": 6000, "memoryBytes": 16 << 30}} for i in range(nodes)]},
        "metadata": {"status": {"fetch_nodeclaims": "crd_not_found"}, "summary": {"collection_timestamp": 1793641292}}}


def write_zip(path, d, extra=False):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("snapshot.json", json.dumps(d))
        if extra:
            z.writestr("other.json", "{}")


class Selection(unittest.TestCase):
    STAMPS = ["2026-11-01-01-41", "2026-11-01-23-41", "2026-11-05-03-41", "2026-11-12-01-41", "2026-11-12-03-41"]

    def test_latest(self):
        self.assertEqual(S.select(self.STAMPS, ("latest",)), ["2026-11-12-03-41"])

    def test_daily_keeps_newest_of_each_day(self):
        self.assertEqual(S.select(self.STAMPS, ("daily",)),
                         ["2026-11-01-23-41", "2026-11-05-03-41", "2026-11-12-03-41"])

    def test_week_is_seven_days_before_newest(self):
        # exactly 7 days back is excluded: a week of two-hourly snapshots is 84, not 85
        self.assertEqual(S.select(self.STAMPS, ("week",)), ["2026-11-12-01-41", "2026-11-12-03-41"])
        self.assertIn("2026-11-05-05-41", S.select(self.STAMPS + ["2026-11-05-05-41"], ("week",)))

    def test_all_and_unknown(self):
        self.assertEqual(S.select(self.STAMPS, ("all",)), self.STAMPS)
        with self.assertRaises(ValueError):
            S.select(self.STAMPS, ("hourly",))

    def test_listing_parse(self):
        text = ("2026-11-02 12:41:34  713166 raw/t/c/snapshots/year=2026/month=11/day=02/2026-11-02-17-41_v2.zip\n"
                "2026-11-02 12:41:34  10 raw/t/c/snapshots/year=2026/month=11/day=02/notes.txt\n")
        self.assertEqual(S.parse_listing(text),
                         [("2026-11-02-17-41", "raw/t/c/snapshots/year=2026/month=11/day=02/2026-11-02-17-41_v2.zip")])


class Settings(unittest.TestCase):
    def test_requires_prefix_and_uuid(self):
        with self.assertRaises(SystemExit):
            S.settings({"clusters": CFG["clusters"]})
        with self.assertRaises(SystemExit):
            S.settings({**CFG, "snapshots": {"s3_prefix": "s3://example-bucket/raw", "tenant_id": "../x"}})
        with self.assertRaises(SystemExit):
            S.settings({**CFG, "snapshots": {"s3_prefix": "s3://b/raw; rm -rf /", "tenant_id": TID}})
        self.assertEqual(S.settings(CFG)[2], [("prod-a", UID)])


class Pull(unittest.TestCase):
    def test_commands_and_skip_existing(self):
        keys = [f"raw/{TID}/{UID}/snapshots/year=2026/month=11/day=0{d}/2026-11-0{d}-0{h}-41_v2.zip"
                for d in (1, 2) for h in (1, 3)]
        calls = []

        def run(cmd):
            calls.append(cmd)
            return "\n".join(f"2026-11-02 00:00:00 700000 {k}" for k in keys) if cmd[2] == "ls" else ""

        with tempfile.TemporaryDirectory() as t:
            dest = Path(t) / "data" / "snapshots" / "prod-a"
            dest.mkdir(parents=True)
            (dest / "2026-11-02-03-41_v2.zip").write_bytes(b"x")       # already here
            n = S.pull(CFG, t, "2026-11", ("latest", "daily"), profile="ro", run=run, log=lambda *_: None)
        self.assertEqual(calls[0], ["aws", "s3", "ls", f"s3://example-bucket/raw/{TID}/{UID}/snapshots/year=2026/month=11/",
                                    "--recursive", "--profile", "ro"])
        cps = [c for c in calls if c[2] == "cp"]
        self.assertEqual(n, 1)
        self.assertEqual(cps[0][3], f"s3://example-bucket/{keys[1]}")       # day 1's newest; day 2's is local
        self.assertTrue(all(isinstance(c, list) for c in calls))           # never a shell string

    def test_dry_run_fetches_nothing_and_bad_month(self):
        calls = []
        with tempfile.TemporaryDirectory() as t:
            S.pull(CFG, t, "2026-11", run=lambda c: calls.append(c) or "", dry_run=True, log=lambda *_: None)
            with self.assertRaises(SystemExit):
                S.pull(CFG, t, "2026-13", run=lambda c: "", log=lambda *_: None)
        self.assertEqual([c[2] for c in calls], ["ls"])


class Digest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name) / "data" / "snapshots" / "prod-a"
        self.d.mkdir(parents=True)
        write_zip(self.d / "2026-11-02-17-41_v2.zip", doc(replicas=10, nodes=5))
        write_zip(self.d / "2026-11-03-17-41_v2.zip", doc(replicas=3, nodes=3))
        self.dg = S.digest(self.t.name, "prod-a", log=lambda *_: None)
        self.text = (self.d / "digest.json").read_text()

    def tearDown(self):
        self.t.cleanup()

    def test_no_annotations_or_env_values(self):
        for s in ("last-applied", "DB_USER", "s3cr3t", "registry.example"):
            self.assertNotIn(s, self.text)

    def test_hpa_findings(self):
        h = {x["hpa"]: x for x in self.dg["hpa"]}
        self.assertEqual(h["cart"]["changes"], ["cpu trigger 50% -> 80%", "minReplicas 3 -> 2 (or the PDB floor)"])
        self.assertEqual(h["cart"]["pdbs"], ["cart"])
        self.assertTrue(h["search"]["pinned"])
        self.assertEqual(h["search"]["changes"], [])
        self.assertEqual(h["queue"]["other_metrics"], ["lag"])
        self.assertEqual(h["queue"]["changes"], [])

    def test_trends_and_latest(self):
        self.assertEqual(self.dg["latest"], "2026-11-03-17-41")
        t = self.dg["trends"]["hpa"]["Deployment/shop/cart"]
        self.assertEqual((t["low"], t["high"], t["at_max_pct"], t["at_min_pct"]), (3, 10, 50, 50))
        self.assertEqual(self.dg["trends"]["nodes"], [5, 3])

    def test_prereqs_pools_and_blockers(self):
        pr = self.dg["prerequisites"]
        self.assertEqual((pr["exporter"], pr["exporter_ok"], pr["agent_seen"]), ("v1.1.43", True, False))
        self.assertEqual(self.dg["pools"]["pool-a"]["nodes"], 3)
        self.assertFalse(self.dg["summary"]["karpenter"])
        self.assertIn("Deployment/shop/cart", self.dg["drain_blockers"])
        self.assertEqual(self.dg["no_requests"], {"Deployment/shop/cart": ["app"]})
        self.assertEqual(self.dg["pdbs"][0]["minAvailable"], "60%")

    def test_report_never_lists_pinned_hpas_as_busy(self):
        r = S.report(self.dg)
        self.assertNotIn("search", r.split("maxReplicas:")[-1] if "maxReplicas:" in r else "")
        self.assertIn("cart 50%", r)

    def test_zip_with_two_documents_refused(self):
        p = self.d / "2026-11-04-17-41_v2.zip"
        write_zip(p, doc(), extra=True)
        with self.assertRaises(ValueError):
            S.load(p)


if __name__ == "__main__":
    unittest.main()
