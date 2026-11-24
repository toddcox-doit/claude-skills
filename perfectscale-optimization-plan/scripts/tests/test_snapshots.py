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
        with self.assertRaisesRegex(SystemExit, "--bucket"):
            S.settings({"clusters": CFG["clusters"], "snapshots": {"tenant_id": TID}}, env={}, machine_file="/nonexistent")
        with self.assertRaisesRegex(SystemExit, "--tenant-id"):
            S.settings({"clusters": CFG["clusters"]}, prefix="s3://example-bucket/raw", env={})
        with self.assertRaises(SystemExit):
            S.settings(CFG, tenant_id="../x", env={})
        with self.assertRaises(SystemExit):
            S.settings(CFG, prefix="s3://b/raw; rm -rf /", env={})
        self.assertEqual(S.settings(CFG, env={})[2], [("prod-a", UID)])

    def test_prefix_from_env_then_machine_file(self):
        cfg = {"clusters": CFG["clusters"], "snapshots": {"tenant_id": TID}}
        with tempfile.TemporaryDirectory() as t:
            mf = Path(t) / "snapshots.json"
            S.save_machine_prefix("s3://machine-bucket/raw/", mf)
            self.assertEqual(oct(mf.stat().st_mode & 0o777), "0o600")
            self.assertEqual(S.settings(cfg, env={}, machine_file=mf)[0], "s3://machine-bucket/raw")
            self.assertEqual(S.settings(cfg, env={"PS_SNAPSHOT_PREFIX": "s3://env-bucket/raw"}, machine_file=mf)[0],
                             "s3://env-bucket/raw")
            self.assertEqual(S.settings(cfg, prefix="s3://arg-bucket/raw", env={}, machine_file=mf)[0], "s3://arg-bucket/raw")
            with self.assertRaises(SystemExit):
                S.save_machine_prefix("http://nope", mf)

    def test_months_and_window(self):
        from datetime import datetime, timezone
        d = lambda *x: datetime(*x, tzinfo=timezone.utc)  # noqa: E731
        self.assertEqual(S.months(d(2026, 11, 20), d(2027, 1, 3)), [("2026", "11"), ("2026", "12"), ("2027", "01")])
        st = ["2026-10-01-00-00", "2026-10-21-00-00", "2026-11-10-00-00", "2026-11-19-00-00"]
        self.assertEqual(S.window(st, 30), ["2026-10-21-00-00", "2026-11-10-00-00", "2026-11-19-00-00"])


class Pull(unittest.TestCase):
    def keys(self, days):
        return [f"raw/{TID}/{UID}/snapshots/year=2026/month={m}/day={dd}/2026-{m}-{dd}-{h}-41_v2.zip"
                for m, dd in days for h in ("01", "03")]

    def test_trailing_window_spans_months_and_skips_existing(self):
        from datetime import datetime, timezone
        by_month = {"10": self.keys([("10", "10"), ("10", "30")]), "11": self.keys([("11", "02")])}
        calls = []

        def run(cmd):
            calls.append(cmd)
            if cmd[2] == "ls":
                m = cmd[3].split("month=")[1][:2]
                return "\n".join(f"2026-11-02 00:00:00 700000 {k}" for k in by_month.get(m, []))
            return ""

        with tempfile.TemporaryDirectory() as t:
            dest = Path(t) / "data" / "snapshots" / "prod-a"
            dest.mkdir(parents=True)
            (dest / "2026-11-02-03-41_v2.zip").write_bytes(b"x")       # already here
            found = S.pull(CFG, t, 30, ("latest", "daily"), profile="ro", run=run, log=lambda *_: None,
                           now=datetime(2026, 11, 5, tzinfo=timezone.utc))
        ls = [c for c in calls if c[2] == "ls"]
        self.assertEqual([c[3].split("year=")[1] for c in ls], ["2026/month=10/", "2026/month=11/"])
        self.assertEqual(ls[0][-2:], ["--profile", "ro"])
        self.assertEqual(found["prod-a"][0], "2026-10-10-01-41")       # Oct 6..Nov 5 window keeps Oct 10, drops nothing older
        cps = [c[3].rsplit("/", 1)[1] for c in calls if c[2] == "cp"]
        self.assertEqual(cps, ["2026-10-10-03-41_v2.zip", "2026-10-30-03-41_v2.zip"])   # Nov 2's newest is local
        self.assertTrue(all(isinstance(c, list) for c in calls))           # never a shell string

    def test_old_snapshots_outside_window_are_ignored(self):
        from datetime import datetime, timezone
        keys = self.keys([("11", "01"), ("11", "20")])
        run = lambda c: "\n".join(f"x y 1 {k}" for k in keys) if c[2] == "ls" else ""  # noqa: E731
        with tempfile.TemporaryDirectory() as t:
            found = S.pull(CFG, t, 7, ("latest", "daily"), run=run, dry_run=True, log=lambda *_: None,
                           now=datetime(2026, 11, 21, tzinfo=timezone.utc))
        self.assertEqual(found["prod-a"], ["2026-11-20-01-41", "2026-11-20-03-41"])

    def test_dry_run_fetches_nothing_and_bad_days(self):
        calls = []
        with tempfile.TemporaryDirectory() as t:
            S.pull(CFG, t, run=lambda c: calls.append(c) or "", dry_run=True, log=lambda *_: None)
            with self.assertRaises(SystemExit):
                S.pull(CFG, t, 0, run=lambda c: "", log=lambda *_: None)
        self.assertTrue(calls and all(c[2] == "ls" for c in calls))


class Cli(unittest.TestCase):
    """One command: the tenant id is saved in the config the first time, then never asked again."""

    def test_tenant_id_saved_once(self):
        import subprocess
        with tempfile.TemporaryDirectory() as t:
            cfgp = Path(t) / "config" / "acme.tenant.json"
            cfgp.parent.mkdir()
            cfgp.write_text(json.dumps({"customer": "Acme", "clusters": CFG["clusters"], "profile": "acme"}))
            sys.path.insert(0, str(HERE.parent))
            import snapshots as cli
            raw = cli.save_tenant_id(cfgp, json.loads(cfgp.read_text()), TID.upper())
            saved = json.loads(cfgp.read_text())
            self.assertEqual(saved["snapshots"], {"tenant_id": TID})
            self.assertEqual(saved["profile"], "acme")
            self.assertIs(cli.save_tenant_id(cfgp, raw, TID), raw)
            with self.assertRaises(SystemExit):
                cli.save_tenant_id(cfgp, raw, "not-a-uuid")
            r = subprocess.run([sys.executable, str(HERE.parent / "snapshots.py"), "--config", str(cfgp), "--dry-run",
                                "--bucket", "s3://example-bucket/raw"], capture_output=True, text=True,
                               env={"HOME": t, "PATH": "/nonexistent"})
            self.assertTrue((Path(t) / ".perfectscale" / "snapshots.json").exists(), r.stderr)


class Digest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.d = Path(self.t.name) / "data" / "snapshots" / "prod-a"
        self.d.mkdir(parents=True)
        write_zip(self.d / "2026-11-02-17-41_v2.zip", doc(replicas=10, nodes=5))
        write_zip(self.d / "2026-11-03-17-41_v2.zip", doc(replicas=3, nodes=3))
        write_zip(self.d / "2026-09-01-17-41_v2.zip", doc(replicas=99, nodes=99))      # outside the 30-day window
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
        self.assertEqual(self.dg["trends"]["nodes"], [5, 3])           # the September snapshot is left out
        self.assertEqual(self.dg["window_days"], 30)

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
