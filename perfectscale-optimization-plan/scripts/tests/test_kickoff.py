"""One-command re-runs (versioning, run_plan --pin-hpa / --next-version) and the kickoff.py helper
behind the kickoff page's "Update the plan with these confirmations" button."""
import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan import versioning  # noqa: E402

RUN_PLAN = HERE.parent / "run_plan.py"
HPA_NS = "platform"


def tenant(tmp, hpa=True):
    """A tenant folder laid out like a real one: config/<x>.tenant.json, reports/<version>/. With hpa,
    every workload in HPA_NS gets a pod CPU/memory HPA so the kickoff page offers a confirmation."""
    src = Path(fixtures.make(tmp))
    (tmp / "config").mkdir()
    cfgp = tmp / "config" / "acme.tenant.json"
    c = json.loads(src.read_text())
    c.update(out_dir="reports/v2", version_tag="v2")
    cfgp.write_text(json.dumps(c, indent=2)); src.unlink()
    if hpa:
        data = Path(c["data_dir"]) / "prod-a"
        rows = [json.loads(l) for l in (data / "workloads-all.jsonl").read_text().splitlines() if l.strip()]
        v2 = [{"id": r["id"], "name": r["name"], "namespace": r["namespace"], "type": r["type"],
               "scaling": {"avgReplicas": 2, "hpaTargets": {"cpu": {"utilization": 90}}},
               "automation": {"automatedBy": "none", "status": "NotEnabled"},
               "maturity": {"overall": {"isMature": True, "minutesLeftToMature": 0}}, "containers": {}}
              for r in rows if r["namespace"] == HPA_NS]
        (data / "workloads-v2.json").write_text(json.dumps({"data": v2, "meta": {}}))
    return cfgp


def run(cfgp, *args):
    return subprocess.run([sys.executable, str(RUN_PLAN), "--config", str(cfgp), *args],
                          cwd=cfgp.parent.parent, capture_output=True, text=True)


class Versioning(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_next_version(self):
        self.assertEqual(versioning.next_version("v5.3"), "v5.4")
        self.assertEqual(versioning.next_version("v5"), "v6")
        self.assertEqual(versioning.next_version("v2.9"), "v2.10")
        self.assertEqual(versioning.next_version("draft"), "draft.1")

    def test_pin_saves_anchored_patterns_once_and_bumps_the_version(self):
        cfgp = tenant(self.tmp, hpa=False)
        r = run(cfgp); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        ch = versioning.bump(cfgp, pin=["payments", "payments"])
        c = json.loads(cfgp.read_text())
        self.assertEqual(c["hpa_pinned_namespaces"], ["^payments$"])
        self.assertEqual((ch["version"], c["version_tag"], c["out_dir"]), ("v3", "v3", "reports/v3"))
        self.assertTrue(c["previous_plan"].startswith("reports/v2/") and c["previous_plan"].endswith("-v2.json"))
        self.assertTrue((self.tmp / ch["backup"]).is_file())
        self.assertEqual(json.loads((self.tmp / "config" / "acme.tenant.v2.json").read_text())["version_tag"], "v2")
        ch2 = versioning.bump(cfgp, pin=["payments", "data"])               # already saved: not duplicated
        self.assertEqual(ch2["pinned_added"], ["data"])
        self.assertEqual(json.loads(cfgp.read_text())["hpa_pinned_namespaces"], ["^payments$", "^data$"])

    def test_invalid_names_change_nothing(self):
        cfgp = tenant(self.tmp, hpa=False)
        before = cfgp.read_text()
        for bad in (["Payments"], ["a;b"], ["^x$"], ["../etc"], [""], ["a" * 64]):
            with self.assertRaises(ValueError):
                versioning.bump(cfgp, pin=bad)
        self.assertEqual(cfgp.read_text(), before)
        self.assertFalse((self.tmp / "config" / "acme.tenant.v2.json").exists())

    def test_never_writes_into_an_existing_version_folder(self):
        cfgp = tenant(self.tmp, hpa=False)
        (self.tmp / "reports" / "v3").mkdir(parents=True)
        self.assertEqual(versioning.bump(cfgp)["version"], "v4")

    def test_fixture_layout_with_absolute_out_dir(self):
        p = Path(fixtures.make(self.tmp))                                    # config at the root, absolute out_dir, no tag
        ch = versioning.bump(p)
        self.assertEqual(ch["previous_version"], "v2")                       # the library default
        self.assertEqual(json.loads(p.read_text())["version_tag"], "v3")


class RunPlanPin(unittest.TestCase):
    def test_pin_hpa_renders_the_next_version_with_the_namespace_released(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cfgp = tenant(tmp)
            r = run(cfgp); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            v2 = json.loads(next((tmp / "reports" / "v2").glob("**/*-optimization-plan-*-v2.json")).read_text())
            self.assertIn("hpa_automation", {a["key"] for a in v2["actions"]})       # unpinned: HPA automation needed
            r = run(cfgp, "--pin-hpa", HPA_NS)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f"HPA confirmed pinned: {HPA_NS}", r.stdout)
            self.assertIn("version v2 -> v3", r.stdout)
            out = tmp / "reports" / "v3"
            self.assertTrue(list(out.glob("*-change-log-vs-previous-*-v3.md")))
            plan = json.loads(next(out.glob("**/*-optimization-plan-*-v3.json")).read_text())
            self.assertNotIn("hpa_automation", {a["key"] for a in plan["actions"]})   # pinned: no HPA automation needed
            self.assertTrue(list((tmp / "reports" / "v2").glob("*")))           # the old version is untouched
            bad = run(cfgp, "--pin-hpa", "Not_A_Namespace")
            self.assertEqual(bad.returncode, 2); self.assertIn("not a Kubernetes namespace name", bad.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class Helper(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import kickoff
        cls.kickoff = kickoff
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cfgp = tenant(cls.tmp)
        r = run(cls.cfgp); assert r.returncode == 0, r.stdout + r.stderr
        cls.h = kickoff.Helper(cls.cfgp)
        threading.Thread(target=cls.h.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.h.httpd.shutdown(); cls.h.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, host=None, token=True, origin=None):
        c = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=30)
        hd = {"Host": host or f"127.0.0.1:{self.h.port}"}
        if token:
            hd["X-PS-Token"] = self.h.token if token is True else token
        if origin:
            hd["Origin"] = origin
        data = None
        if body is not None:
            data = json.dumps(body).encode(); hd["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=hd)
        r = c.getresponse(); out = r.read(); c.close()
        return r.status, dict(r.getheaders()), out

    def page(self):
        return next(p for p in self.h.pages() if "prod-a" in p.name)

    def test_root_redirects_and_page_carries_the_token(self):
        s, hd, _ = self.req("GET", "/", token=False)
        self.assertEqual(s, 302)
        s, _, body = self.req("GET", hd["Location"], token=False)
        self.assertEqual(s, 200)
        self.assertIn(f'"token": "{self.h.token}"'.encode(), body)
        self.assertIn(b"window.PS_HELPER=", body.split(b"</head>")[0])          # in <head>: set before the page script runs
        self.assertNotIn(self.h.token.encode(), self.page().read_bytes())          # never written to disk

    def test_rejects_foreign_host_token_origin_and_paths(self):
        p = self.h.url_for(self.page())
        good = {"namespaces": [HPA_NS], "page": p}
        self.assertEqual(self.req("GET", p, host="evil.example:80", token=False)[0], 403)
        self.assertEqual(self.req("POST", "/api/pin-hpa", good, token=False)[0], 403)
        self.assertEqual(self.req("POST", "/api/pin-hpa", good, token="nope")[0], 403)
        self.assertEqual(self.req("POST", "/api/pin-hpa", good, origin="https://evil.example")[0], 403)
        self.assertEqual(self.req("POST", "/api/pin-hpa", {"namespaces": ["a;rm -rf"], "page": p})[0], 400)
        self.assertEqual(self.req("POST", "/api/pin-hpa", {"namespaces": ["payments"], "page": p})[0], 400)   # not offered on the page
        self.assertEqual(self.req("POST", "/api/pin-hpa", {"namespaces": [HPA_NS], "page": "/../config/acme.tenant.json"})[0], 400)
        self.assertEqual(self.req("GET", "/../config/acme.tenant.json", token=False)[0], 404)
        self.assertEqual(self.req("DELETE", "/api/pin-hpa")[0], 405)
        self.h.busy = True
        try:
            self.assertEqual(self.req("POST", "/api/pin-hpa", good)[0], 409)
        finally:
            self.h.busy = False

    def test_zz_update_runs_and_points_at_the_new_version(self):
        p = self.h.url_for(self.page())
        s, _, body = self.req("POST", "/api/pin-hpa", {"namespaces": [HPA_NS], "page": p})
        self.assertEqual(s, 202, body)
        job = json.loads(body)["job"]
        for _ in range(240):
            s, _, b = self.req("GET", f"/api/job/{job}")
            st = json.loads(b)
            if st["state"] != "running":
                break
            time.sleep(0.25)
        self.assertEqual(st["state"], "done", st)
        self.assertEqual(st["version"], "v3")
        self.assertIn("/v3/", st["url"]); self.assertIn("prod-a", st["url"])
        self.assertEqual(self.req("GET", st["url"], token=False)[0], 200)
        self.assertEqual(json.loads(self.cfgp.read_text())["hpa_pinned_namespaces"], [f"^{HPA_NS}$"])


class FindConfig(unittest.TestCase):
    """Commands work from any folder: a tenant's copy of the library knows its tenant."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.t = self.tmp / "Acme_analysis"
        self.script = self.t / "skill" / "perfectscale-optimization-plan" / "scripts" / "kickoff.py"
        self.script.parent.mkdir(parents=True)
        (self.t / "config").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_finds_the_one_config_and_ignores_version_backups(self):
        (self.t / "config" / "acme.tenant.json").write_text("{}")
        (self.t / "config" / "acme.tenant.v5.3.json").write_text("{}")
        self.assertEqual(versioning.find_config(self.script), (self.t / "config" / "acme.tenant.json").resolve())

    def test_none_or_several_configs_explain_what_to_do(self):
        with self.assertRaisesRegex(SystemExit, "new_tenant.py config"):
            versioning.find_config(self.script)
        (self.t / "config" / "a.tenant.json").write_text("{}"); (self.t / "config" / "b.tenant.json").write_text("{}")
        with self.assertRaisesRegex(SystemExit, "config/a.tenant.json, config/b.tenant.json"):
            versioning.find_config(self.script)

    def test_installed_copy_asks_for_a_tenant(self):
        installed = self.tmp / "skills" / "perfectscale-optimization-plan" / "scripts" / "kickoff.py"
        with self.assertRaisesRegex(SystemExit, "installed copy of the skill"):
            versioning.find_config(installed)

    def test_relative_config_resolves_from_the_tenant_folder_too(self):
        (self.t / "config" / "a.tenant.json").write_text("{}"); (self.t / "config" / "b.tenant.json").write_text("{}")
        import os
        here = os.getcwd()
        os.chdir(self.tmp)                                   # not the tenant folder
        try:
            self.assertEqual(versioning.find_config(self.script, "config/b.tenant.json"), (self.t / "config" / "b.tenant.json").resolve())
            with self.assertRaisesRegex(SystemExit, "no config at"):
                versioning.find_config(self.script, "config/nope.tenant.json")
        finally:
            os.chdir(here)


class TenantFolder(unittest.TestCase):
    """A real tenant folder from new_tenant.py init: run from anywhere, and the double-click launcher."""
    NEW_TENANT = HERE.parent / "new_tenant.py"

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.t = cls.tmp / "Acme_analysis"
        r = subprocess.run([sys.executable, str(cls.NEW_TENANT), "init", "--customer", "Acme", "--dir", str(cls.t)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        src = Path(fixtures.make(cls.tmp / "fx"))
        c = json.loads(src.read_text()); c.update(out_dir="reports/v2", version_tag="v2")
        (cls.t / "config" / "acme.tenant.json").write_text(json.dumps(c, indent=2))
        cls.copy = cls.t / "skill" / "perfectscale-optimization-plan" / "scripts"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_run_plan_from_another_folder_without_config(self):
        r = subprocess.run([sys.executable, str(self.copy / "run_plan.py")], cwd=self.tmp, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(list((self.t / "reports" / "v2").glob("*-optimization-plan-*-v2.md")))

    def test_installed_skill_without_config_says_which_copy_to_use(self):
        r = subprocess.run([sys.executable, str(RUN_PLAN)], cwd=self.tmp, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("installed copy of the skill", r.stderr)

    def test_launcher_is_written_executable_and_starts_kickoff_from_the_tenant_folder(self):
        import os
        import stat
        L = self.t / "Open kickoff pages.command"
        self.assertTrue(L.is_file())
        self.assertTrue(L.stat().st_mode & stat.S_IXUSR)
        self.assertEqual(subprocess.run(["bash", "-n", str(L)]).returncode, 0)
        real = (self.copy / "kickoff.py").read_text()
        try:   # stand in for the helper: report where it was started and with what
            (self.copy / "kickoff.py").write_text("import json,os,sys;print(json.dumps({'cwd':os.getcwd(),'args':sys.argv[1:]}))\n")
            r = subprocess.run(["bash", str(L)], cwd="/", capture_output=True, text=True, env=dict(os.environ))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            got = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(Path(got["cwd"]).resolve(), self.t.resolve())
            self.assertEqual(got["args"], [])
        finally:
            (self.copy / "kickoff.py").write_text(real)

    def test_launcher_subcommand_adds_it_to_an_existing_folder(self):
        L = self.t / "Open kickoff pages.command"
        L.unlink()
        r = subprocess.run([sys.executable, str(self.NEW_TENANT), "launcher", "--dir", str(self.t)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(L.is_file())
        r = subprocess.run([sys.executable, str(self.NEW_TENANT), "launcher", "--dir", str(self.tmp)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)                 # not a tenant folder


def cr(kind, name, ns=None, extra=""):
    meta = f'  name: "{name}"\n' + (f'  namespace: "{ns}"\n' if ns else "")
    return f"apiVersion: perfectscale.io/v1\nkind: {kind}\nmetadata:\n{meta}spec:\n  automation: {{}}\n{extra}"


def crd_set(nss, wl_extra=""):
    """A namespace CRD set like the page generates: the cluster prerequisite plus one file per namespace."""
    fs = [{"name": "acme-prod-a-cluster-automation-config.yaml", "text": "---\n" + cr("ClusterAutomationConfig", "cluster-automation-config")}]
    for n in nss:
        fs.append({"name": f"namespaces/{n}-namespace-automation-config.yaml",
                   "text": "---\n" + cr("NamespaceAutomationConfig", "namespace-automation-config", n, wl_extra)})
    return fs


class CrdStore(unittest.TestCase):
    """CRD history: append-only, every set kept, identical sets not stored twice, exact rollback deletes."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_save_history_changes_and_rollback_deletes(self):
        from datetime import datetime, timezone
        from psplan import crdstore as C
        t = lambda h: datetime(2026, 11, 23, h, 0, 0, tzinfo=timezone.utc)  # noqa: E731
        a = C.save(self.root, "acme-prod-a", "ns", crd_set(["shop", "pay"]), {"types": ["Deployment"]}, now=t(9))
        self.assertFalse(a["unchanged"]); self.assertEqual(a["id"], "20261123-090000-ns")
        self.assertTrue((self.root / "crds" / "acme-prod-a" / a["id"] / "namespaces" / "shop-namespace-automation-config.yaml").is_file())
        same = C.save(self.root, "acme-prod-a", "ns", crd_set(["shop", "pay"]), now=t(10))
        self.assertEqual(same, {"id": a["id"], "unchanged": True})                    # identical: not stored twice
        b = C.save(self.root, "acme-prod-a", "ns", crd_set(["shop", "pay", "risk"], "  # tweak\n"), now=t(11))
        c = C.save(self.root, "acme-prod-a", "ns", crd_set(["shop"]), now=t(12))
        h = C.history(self.root, "acme-prod-a")
        self.assertEqual([x["id"] for x in h], [c["id"], b["id"], a["id"]])          # newest first
        mb = next(x for x in h if x["id"] == b["id"])
        self.assertEqual(mb["previous"], a["id"])
        self.assertEqual(mb["changes"]["added"], ["namespaces/risk-namespace-automation-config.yaml"])
        self.assertEqual(len(mb["changes"]["changed"]), 2)                            # shop and pay picked up the tweak
        mc = next(x for x in h if x["id"] == c["id"])
        self.assertEqual(sorted(o[1] for o in mc["removed_objects"]), ["pay", "risk"])
        self.assertNotIn("objects", mc)                                                # the listing stays light
        # Rolling back from c (1 namespace) to b (3) deletes nothing; from b to a deletes risk's config only.
        gb, ga, gc = (C.get(self.root, "acme-prod-a", x["id"]) for x in (b, a, c))
        self.assertEqual(C.rollback_deletes(gb["objects"], gc["objects"]), [])
        self.assertEqual(C.rollback_deletes(ga["objects"], gb["objects"]),
                         ["kubectl delete namespaceautomationconfig namespace-automation-config -n risk"])
        self.assertIn("apiVersion: perfectscale.io/v1", gb["texts"]["namespaces/risk-namespace-automation-config.yaml"])
        r = C.mark(self.root, "acme-prod-a", b["id"], True)
        self.assertTrue(r["handed"]); self.assertTrue(r["handed_at"])
        self.assertTrue(next(x for x in C.history(self.root, "acme-prod-a") if x["id"] == b["id"])["handed"])
        self.assertFalse(C.mark(self.root, "acme-prod-a", b["id"], False)["handed"])

    def test_cluster_prerequisite_is_never_in_a_delete_list(self):
        from psplan import crdstore as C
        cur = [["ClusterAutomationConfig", "", "cluster-automation-config"], ["NamespaceAutomationConfig", "x", "namespace-automation-config"]]
        self.assertEqual(C.rollback_deletes([], cur), ["kubectl delete namespaceautomationconfig namespace-automation-config -n x"])

    def test_only_sets_the_page_generates_are_accepted(self):
        from psplan import crdstore as C
        ok = crd_set(["shop"])
        bad = [("ns", [{"name": "../x.yaml", "text": ok[0]["text"]}]),
               ("ns", [{"name": "/etc/passwd", "text": ok[0]["text"]}]),
               ("ns", [{"name": "namespaces/../../x.yaml", "text": ok[0]["text"]}]),
               ("ns", [{"name": "notes.txt", "text": ok[0]["text"]}]),
               ("ns", [{"name": ok[1]["name"], "text": "hello"}]),               # not a PerfectScale CR
               ("ns", [ok[1], ok[1]]),                                           # duplicate name
               ("rm", ok), ("ns", [])]
        for kind, files in bad:
            with self.assertRaises(ValueError, msg=str(files)[:80]):
                C.save(self.root, "acme-prod-a", kind, files)
        for slug in ("../x", "Acme", ""):
            with self.assertRaises(ValueError):
                C.save(self.root, slug, "ns", ok)
        with self.assertRaises(ValueError):
            C.get(self.root, "acme-prod-a", "../../config")
        self.assertFalse((self.root / "crds").exists() and any((self.root / "crds").rglob("*.yaml")))


class CrdHistoryApi(unittest.TestCase):
    """The helper's history endpoints: token and page required, cluster always from the page."""
    @classmethod
    def setUpClass(cls):
        import kickoff
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cfgp = tenant(cls.tmp)
        r = run(cls.cfgp); assert r.returncode == 0, r.stdout + r.stderr
        cls.h = kickoff.Helper(cls.cfgp)
        threading.Thread(target=cls.h.httpd.serve_forever, daemon=True).start()
        cls.page = cls.h.url_for(next(p for p in cls.h.pages() if "prod-a" in p.name))

    @classmethod
    def tearDownClass(cls):
        cls.h.httpd.shutdown(); cls.h.httpd.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(self, method, path, body=None, token=True):
        c = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=30)
        hd = {"Host": f"127.0.0.1:{self.h.port}"}
        if token:
            hd["X-PS-Token"] = self.h.token
        data = json.dumps(body).encode() if body is not None else None
        if data:
            hd["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=hd)
        r = c.getresponse(); out = r.read(); c.close()
        return r.status, json.loads(out or b"{}")

    def test_save_list_get_mark_and_rollback_through_the_helper(self):
        from urllib.parse import quote
        q = "page=" + quote(self.page)
        s, r = self.req("POST", "/api/crd-save", {"page": self.page, "kind": "ns", "files": crd_set(["shop", "pay"]), "meta": {"types": ["Deployment"]}})
        self.assertEqual(s, 200, r); first = r["id"]
        slug = self.h.page_data(self.h.file_for(self.page))["slug"]
        self.assertTrue((self.tmp / "crds" / slug / first / "manifest.json").is_file())   # filed under the page's cluster
        s, r = self.req("POST", "/api/crd-save", {"page": self.page, "kind": "ns", "files": crd_set(["shop", "pay"])})
        self.assertEqual(r, {"id": first, "unchanged": True})
        import time as _t; _t.sleep(1.1)
        s, r = self.req("POST", "/api/crd-save", {"page": self.page, "kind": "ns", "files": crd_set(["shop", "pay", "risk"])})
        second = r["id"]
        s, hist = self.req("GET", "/api/crd-history?" + q)
        self.assertEqual(s, 200); self.assertEqual([x["id"] for x in hist["sets"]][:2], [second, first])
        self.assertEqual(hist["sets"][0]["meta"]["plan_version"], "v2")                       # the helper adds the plan version
        s, one = self.req("GET", f"/api/crd-set?{q}&id={first}")
        self.assertEqual(sorted(one["texts"]), sorted(f["name"] for f in crd_set(["shop", "pay"])))
        s, rb = self.req("GET", f"/api/crd-rollback?{q}&id={first}&from={second}")
        self.assertEqual(rb["delete"], ["kubectl delete namespaceautomationconfig namespace-automation-config -n risk"])
        s, mk = self.req("POST", "/api/crd-mark", {"page": self.page, "id": first, "handed": True})
        self.assertEqual(s, 200); self.assertTrue(mk["handed"])

    def test_refuses_without_token_page_or_with_bad_files(self):
        good = {"page": self.page, "kind": "ns", "files": crd_set(["shop"])}
        self.assertEqual(self.req("POST", "/api/crd-save", good, token=False)[0], 403)
        self.assertEqual(self.req("GET", "/api/crd-history?page=x", token=False)[0], 403)
        self.assertEqual(self.req("POST", "/api/crd-save", dict(good, page="/../config/acme.tenant.json"))[0], 400)
        self.assertEqual(self.req("POST", "/api/crd-save", dict(good, files=[{"name": "../../evil.yaml", "text": good["files"][0]["text"]}]))[0], 400)
        self.assertEqual(self.req("GET", "/api/crd-set?page=" + self.page + "&id=../../config")[0], 400)
        self.assertFalse(any((self.tmp / "crds").rglob("evil.yaml")) if (self.tmp / "crds").exists() else False)


class PageScriptNames(unittest.TestCase):
    def test_no_function_is_shadowed_by_a_variable_of_the_same_name(self):
        # A `var btn=...` and a `function btn(){}` in the same scope: the var wins at runtime, and every
        # call to the function throws - silently, inside a promise. It once emptied the CRD history.
        import re
        sys.path.insert(0, str(HERE.parent))
        from psplan import pov
        js = pov.JS_UI
        funcs = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", js))
        vars_ = set(re.findall(r"\bvar\s+([A-Za-z_$][\w$]*)", js)) | set(re.findall(r",\s*([A-Za-z_$][\w$]*)\s*=(?!=)", js))
        self.assertEqual(funcs & vars_, set())


class DiffJs(unittest.TestCase):
    def test_line_and_file_diff(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        sys.path.insert(0, str(HERE.parent))
        from psplan import pov
        a = "\n".join(f"line {i}" for i in range(20)); b = a.replace("line 10", "line ten")
        src = (pov.JS_DIFF + ";var o={d:psDiff(" + json.dumps(a) + "," + json.dumps(b) + "),f:psFileDiff("
               + json.dumps([{"name": "x", "text": "1"}, {"name": "y", "text": "2"}]) + ","
               + json.dumps([{"name": "x", "text": "1"}, {"name": "y", "text": "3"}, {"name": "z", "text": "4"}])
               + ")};delete o.f.o;delete o.f.n;process.stdout.write(JSON.stringify(o))")
        got = json.loads(subprocess.run([node, "-e", src], capture_output=True, text=True, check=True).stdout)
        self.assertIn(["-", "line 10"], got["d"]); self.assertIn(["+", "line ten"], got["d"])
        self.assertEqual(got["d"][0], ["~", "8 unchanged lines"])                       # context, not the whole file
        self.assertEqual(got["f"], {"added": ["z"], "removed": [], "changed": ["y"]})


if __name__ == "__main__":
    unittest.main()
