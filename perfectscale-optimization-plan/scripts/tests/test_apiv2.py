"""Public API v2 enrichment: the client (credentials never leak, token wrapper, retries), the
collector step, the model merge (HPA class, automation status as eligibility, maturity), and the
HPA-aware waves on the POV kickoff page."""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan import apiv2, model, pov, render, verify  # noqa: E402
from psplan.actions import Plan  # noqa: E402
from psplan.config import Config  # noqa: E402

SECRET = "s3cr3t-value-never-printed"
TOKEN = "tok-value-never-printed"


def wid(name, ns, typ):
    return f"{ns}-{typ.lower()}-{name}"


def v2row(name, ns, typ, hpa=None, status="NotEnabled", mature=True, container_hpa=None):
    return {"id": wid(name, ns, typ), "name": name, "namespace": ns, "type": typ,
            "scaling": {"avgReplicas": 2, "hpaTargets": hpa or {}},
            "automation": {"automatedBy": "none", "status": status},
            "maturity": {"overall": {"isMature": mature, "minutesLeftToMature": 0 if mature else 600}},
            "containers": {"c": {"name": "c", **({"hpaTargets": container_hpa} if container_hpa else {})}}}


V2_PROD_A = [
    v2row("api-0", "payments", "Deployment", {"cpu": {"utilization": 70}}),                     # Hold
    v2row("api-1", "payments", "Deployment", {"kafka_consumergroup_lag": {"average": 100}}),    # Review (custom)
    v2row("api-2", "payments", "Deployment", container_hpa={"cpu": {"utilization": 60}}),       # Wave 1 (container)
    v2row("api-3", "payments", "Deployment", status="NotSupported"),                            # Review (not supported)
    v2row("api-4", "payments", "Deployment", mature=False),                                     # Wave 1, immature
    v2row("billing", "payments", "Deployment"),                                                 # Wave 1, no HPA
]


def e_html(x):
    import html
    return html.escape(x, quote=True)


class HpaAutomation(unittest.TestCase):
    """Workloads whose HPA scales on pod CPU/memory are automated with PerfectScale's HPA automation
    alongside (PerfectScale sizes the request from the trigger, then keeps the HPA aligned), not held
    back. The fixture gives every workload in NS a pod CPU and memory HPA at 90%."""
    NS = "platform"

    @classmethod
    def build(cls, pinned=None):
        tmp = Path(tempfile.mkdtemp())
        cls._tmps.append(tmp)
        p = fixtures.make(tmp)
        if pinned is not None:
            c = json.loads(Path(p).read_text()); c["hpa_pinned_namespaces"] = pinned
            Path(p).write_text(json.dumps(c))
        cfg = Config.load(p)
        rows = [json.loads(l) for l in (cfg.data("prod-a") / "workloads-all.jsonl").read_text().splitlines() if l.strip()]
        v2 = [{"id": r["id"], "name": r["name"], "namespace": r["namespace"], "type": r["type"],
               "scaling": {"avgReplicas": 2, "hpaTargets": {"cpu": {"utilization": 90}, "memory": {"utilization": 90}}},
               "automation": {"automatedBy": "none", "status": "NotEnabled"},
               "maturity": {"overall": {"isMature": True, "minutesLeftToMature": 0}}, "containers": {}}
              for r in rows if r["namespace"] == cls.NS]
        assert v2, "fixture must have workloads in the chosen namespace"
        (cfg.data("prod-a") / "workloads-v2.json").write_text(json.dumps({"data": v2, "meta": {}}))
        F = model.load_all(cfg, parallel=False)
        return cfg, next(f for f in F if f["name"] == "prod-a"), F

    @classmethod
    def setUpClass(cls):
        cls._tmps = []
        cls.cfg, cls.prod, cls.F = cls.build()
        cls.cfg_p, cls.prod_p, cls.F_p = cls.build(pinned=[f"^{cls.NS}$"])
        cls.S = Plan(cls.cfg, cls.F).build()

    @classmethod
    def tearDownClass(cls):
        for t in cls._tmps:
            shutil.rmtree(t, ignore_errors=True)

    def ns(self, f):
        return next((n for n in f["pov"]["namespaces"] if n["ns"] == self.NS), None)

    # ---- model
    def test_hpa_workloads_ride_along_in_their_namespace(self):
        n = self.ns(self.prod)
        self.assertFalse(n["held"]); self.assertTrue(n["auto"])
        self.assertNotIn("hpa", {x["reason"] for x in n["excl"]})
        self.assertGreater(n["hpa"]["n"], 0)
        self.assertEqual(n["hpa"]["n"], len(n["hpa"]["items"]))
        sc = self.prod["pov"]["hpa_scope"]
        self.assertIn(self.NS, sc["namespaces"])
        self.assertEqual(sc["n"], sum((x.get("hpa") or {}).get("n", 0) for x in self.prod["pov"]["namespaces"]))
        self.assertTrue(all(r["wave"] == "1h" for r in self.prod["pov"]["rows"] if r["ns"] == self.NS))

    def test_hpa_analysis_follows_perfectscales_rules(self):
        def c(name, cpu_req, cpu_lim, mem_req, mem_lim):
            return {"name": name, "resources": {"cpu": {"request": {"recommendedCores": cpu_req}, "limit": {"recommendedCores": cpu_lim}},
                                                "memory": {"request": {"recommendedMiB": mem_req}, "limit": {"recommendedMiB": mem_lim}}}}
        def run(cpu, mem, conts, avg=2, mx=2):
            t = {}
            if cpu is not None: t["cpu"] = {"utilization": cpu}
            if mem is not None: t["memory"] = {"utilization": mem}
            r2 = {"scaling": {"hpaTargets": t}, "containers": {x["name"]: x for x in conts}}
            return model.hpa_analysis(r2, {"replicas_counts": {"avg_count": avg, "max_count": mx}})
        text = lambda a: " | ".join(f"{f['kind']}:{f['text']}" for f in a["findings"])  # noqa: E731
        low = run(60, None, [c("app", 1, 0, 1000, 0)])
        self.assertIn("change:Raise the CPU trigger from 60% to 80%", text(low))
        self.assertEqual(low["findings"][0]["short"], "CPU trigger 60% \u2192 80%")     # one-line label for rollups
        cap = run(120, 90, [c("app", 1, 1, 1000, 1000)])                  # usable: CPU 90%, memory 81%
        self.assertEqual(cap["usable"], {"cpu": 90, "memory": 81})
        self.assertIn("change:Lower the CPU trigger from 120% to 90%", text(cap))
        self.assertIn("change:Lower the memory trigger from 90% to 81%", text(cap))
        tight = run(90, None, [c("app", 1, 0.8, 1000, 0)])                 # usable 72% < 80%: reported only
        self.assertIn("report:CPU trigger 90% cannot fire before the limit", text(tight))
        nolim = run(200, None, [c("app", 1, 0, 1000, 0)])
        self.assertIn("report:CPU trigger 200% with no limit", text(nolim))
        ok = run(90, 90, [c("app", 1, 0, 1000, 0)])
        self.assertEqual(ok["findings"], [])
        multi = run(90, None, [c("app", 1, 0, 1, 0), c("log-shipper", 0.1, 0, 1, 0), c("istio-proxy", 0.1, 0, 1, 0)])
        self.assertIn("Move the pod-level trigger to the application container (2 declared containers", text(multi))
        self.assertEqual(multi["containers"], ["app", "log-shipper"])     # injected istio-proxy ignored
        self.assertIn("min:Replicas held at 4", text(run(90, None, [c("app", 1, 0, 1, 0)], avg=4, mx=4)))
        self.assertNotIn("min:", text(run(90, None, [c("app", 1, 0, 1, 0)], avg=2, mx=2)))   # 2 is already the floor

    # ---- plan
    def test_waves_count_hpa_savings_and_the_action_carries_none(self):
        by = {a["key"]: a for a in self.S["actions"]}
        a = by["hpa_automation"]
        self.assertEqual((a["action_type"], a["savings_monthly_usd"], a["phase"]), ("hpa_automation_enable", 0, 1))
        self.assertIn(by["automation_proof"]["id"], a["depends_on"])
        self.assertIn("v1.0.71", " ".join(a["steps"])); self.assertIn("v1.1.28", " ".join(a["steps"]))
        self.assertIn("HPA tab", " ".join(a["steps"]))
        self.assertIn("never changes them", " ".join(a["steps"]))
        L = self.prod["lanes"]["controller"]
        pc = Plan(self.cfg, self.F)
        base = L["pot_eligible"] if L["pot_eligible"] is not None else L["pot"]
        self.assertEqual(pc.pot_ctl(self.prod)[0], base)                    # nothing subtracted for HPAs any more
        self.assertNotIn("hpa_hold", by)
        waves = [x for x in self.S["actions"] if x["key"] in ("automation_proof", "automation_w2", "automation_w3")]
        self.assertTrue(any("HPA automation" in " ".join(w["steps"]) for w in waves))

    def test_achievable_total_is_the_same_whether_or_not_hpas_are_pinned(self):
        pinned = Plan(self.cfg_p, self.F_p).build()
        self.assertEqual(self.S["totals"]["achievable_now_monthly"], pinned["totals"]["achievable_now_monthly"])
        self.assertNotIn("hpa_automation", {a["key"] for a in pinned["actions"]})   # pinned HPAs need no HPA automation

    def test_hpa_decision_and_the_gated_split(self):
        pot = sum(f["lanes"]["controller"]["pot_hpa"] for f in self.F)
        d = next(x for x in self.S["decision_detail"] if x["text"].startswith("Can PerfectScale manage the HPAs"))
        self.assertEqual(d["usd"], round(pot))
        self.assertIn("v1.0.71", d["text"])
        self.assertEqual(self.S["automation_gated"]["hpa_usd"], round(pot))

    def test_exec_summary_decision_names_hpa_automation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        import copy, subprocess
        S = copy.deepcopy(self.S)
        S["automation_gated"].update(usd=10000, share=0.9, hpa_usd=8000)
        p = self._tmps[0] / "es.json"; out = self._tmps[0] / "es.html"
        p.write_text(json.dumps(S))
        r = subprocess.run([node, str(HERE.parent / "exec_summary.js"), str(p), str(out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = out.read_text(encoding="utf-8")
        self.assertIn("with HPA automation alongside: $10,000 a month of the savings below waits on it, $8,000 of that on workloads whose HPAs scale on CPU or memory.", t)
        self.assertNotIn("pinned", t)

    # ---- CRDs
    def test_namespace_file_gets_the_hpa_block_only_when_ready_and_ticked(self):
        nss = self.prod["pov"]["namespaces"]; n = self.ns(self.prod); types = list(n["auto"])
        opts = dict(pov.HPA_DEFAULTS, cpu_max=150, min_max=5)
        on = pov.bundle_ns("prod-a", "2026-11-20", nss, [self.NS], types, hpa={"on": [self.NS], "opts": opts})[1][2]
        self.assertIn(f"# HPA automation: Enabled for {n['hpa']['n']} workload", on)
        doc = on.split("kind: NamespaceAutomationConfig", 1)[1].split("\n---", 1)[0]
        for line in ('    hpa:', '      operational:', '        automationMode: "Enabled"  # PerfectScale HPA automation',
                     '          minReplicas:', '            min: 2  # lowest minReplicas PerfectScale writes', '            max: 5',
                     '          cpuTrigger:', '            min: 80  # lowest CPU trigger (%) it writes', '            max: 150',
                     '          memoryTrigger:'):
            self.assertIn(line + "\n", doc + "\n")
        self.assertEqual(doc.count("max:"), 2)                               # memory max left unset
        self.assertTrue(all(len(l) <= pov.WIDTH for l in on.splitlines()))
        off = pov.bundle_ns("prod-a", "2026-11-20", nss, [self.NS], types, hpa={"on": [], "opts": opts})[1][2]
        self.assertNotIn("HPA automation", off)                              # unticked: left alone, no note
        try:
            import yaml
        except ImportError:
            return
        d = [x for x in yaml.safe_load_all(on) if x][0]
        self.assertEqual(d["spec"]["automation"]["hpa"]["operational"]["restrictions"]["cpuTrigger"], {"enabled": True, "min": 80, "max": 150})

    def test_cluster_prerequisite_keeps_hpa_automation_off_at_cluster_scope(self):
        self.assertIn('    hpa:\n      operational:\n        automationMode: "Disabled"\n', pov.cluster_doc())

    def test_workload_files_get_the_block_for_hpa_rows_only(self):
        rows = self.prod["pov"]["rows"]
        h = next(r for r in rows if r["hpa_class"] == "pod_resource")
        o = next(r for r in rows if r["hpa_class"] != "pod_resource")
        arg = {"on": [h["ns"], o["ns"]], "opts": pov.HPA_DEFAULTS}
        self.assertIn("    hpa:", pov.bundle("prod-a", "2026-11-20", rows, [h["type"]], {h["rank"]}, hpa=arg)[1][2])
        self.assertNotIn("HPA automation", pov.bundle("prod-a", "2026-11-20", rows, [o["type"]], {o["rank"]}, hpa=arg)[1][2])

    def test_browser_builders_match_python_with_hpa_automation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        import subprocess
        nss = self.prod["pov"]["namespaces"]; rows = self.prod["pov"]["rows"]
        D = pov.page_data("prod-a", "2026-11-20", rows, nss)
        allns = [n["ns"] for n in nss]; sel = list(model.POV_CR_TYPES); ranks = [r["rank"] for r in rows]
        full = dict(pov.HPA_DEFAULTS, min_max=4, cpu_max=140, mem_max=120)
        hpas = [None, {"on": [self.NS], "opts": dict(pov.HPA_DEFAULTS)}, {"on": [self.NS], "opts": None}, {"on": allns, "opts": full}, {"on": [], "opts": full}]
        src = (pov.JS_HPA + ";" + pov.JS_BUILD + ";" + pov.JS_BUILD_NS + ";var D=" + json.dumps(D) + ";var H=" + json.dumps(hpas)
               + ";var o={ns:H.map(function(h){return psBuildNs(D," + json.dumps(allns) + "," + json.dumps(sel) + ",h)}),"
               + "wl:H.map(function(h){return psBuild(D," + json.dumps(sel) + "," + json.dumps(ranks) + ",h)})};process.stdout.write(JSON.stringify(o))")
        got = json.loads(subprocess.run([node, "-e", src], capture_output=True, text=True, check=True).stdout)
        for h, js in zip(hpas, got["ns"]):
            py = pov.bundle_ns("prod-a", "2026-11-20", nss, allns, sel, hpa=h)
            self.assertEqual([[f["name"], f["docs"], f["text"]] for f in js], [list(x) for x in py], f"ns {h}")
        for h, js in zip(hpas, got["wl"]):
            py = pov.bundle("prod-a", "2026-11-20", rows, sel, set(ranks), hpa=h)
            self.assertEqual([[f["name"], f["docs"], f["text"]] for f in js], [list(x) for x in py], f"wl {h}")

    # ---- page
    def test_page_has_the_hpa_section_inputs_and_optional_script(self):
        files = render.write_all(self.cfg, self.S, self.F)
        t = next(p for p in files if "-prod-a-pov-automation-kickoff" in p.name).read_text(encoding="utf-8")
        for x in ("<h2>HPA automation</h2>", "<h3>What HPA automation will change</h3>", "<h3>What stays with the owners</h3>",
                  "id=hpaagent", "id=hpaexporter", "id=hpak133", "id=hpaminfloor value='2'", "id=hpacpumin value='80'",
                  "id=hpamemmin value='80'", "id=hpacpumax value=''", f"class=hpaon value='{self.NS}' checked",
                  "If the customer agrees to share their HPA and PDB settings", "kubectl get hpa,pdb --all-namespaces -o json",
                  "kubectl version", "grep -i perfectscale"):
            self.assertIn(x, t)
        for gone in ("hpack", "HPA pinned (min = max)", "Update the plan with these confirmations", "data-g=hold"):
            self.assertNotIn(gone, t)
        # By namespace and Findings by workload are collapsed rollups; a row expands to its notes.
        sec = t.split("<h2>HPA automation</h2>", 1)[1].split("<details class=hpascript>", 1)[0]
        self.assertIn("<details class=hparoll><summary>By namespace (", sec)
        has_f = any(x["findings"] for x in self.ns(self.prod)["hpa"]["items"])
        self.assertEqual("<details class=hparoll><summary>Findings by workload (" in sec, has_f)   # only when there is something to say
        self.assertNotIn("<details class=hparoll open", sec)
        byns = sec.split("By namespace (", 1)[1].split("</details>", 1)[0]
        self.assertIn(f"class=nstog aria-expanded=false>{self.NS}</button>", byns)
        self.assertRegex(byns, r"<tr class=nsdet data-for='h\d+' hidden>")

        # Generated output: one rollup per CRD type, Karpenter and bin packing ready for when they exist.
        self.assertIn("<div id=outgroups></div>", t)
        for title in ("Pod automation - workload CRDs", "Pod automation - namespace CRDs", "'hpa','HPA automation'", "'karpenter','Karpenter'", "'binpacking','Bin packing'"):
            self.assertIn(title, t)
        self.assertLess(t.index("<h2>Namespace scope</h2>"), t.index("<h2>HPA automation</h2>"))
        ok, errs = verify.verify(self.cfg, self.S, self.F, files)
        self.assertEqual(errs, [])
        self.assertTrue(any("HPA automation section with prerequisites" in m for m in ok))

    def test_pinned_override_takes_workloads_out_of_hpa_automation(self):
        rows = [r for r in self.prod_p["pov"]["rows"] if r["ns"] == self.NS]
        self.assertTrue(rows)
        self.assertTrue(all(r["hpa_class"] == "pinned" and r["wave"] in ("1", "1b") for r in rows))
        self.assertTrue(all("pinned (min = max, confirmed)" in r["hpa_detail"] for r in rows))
        self.assertFalse((self.ns(self.prod_p).get("hpa") or {}).get("n"))
        self.assertNotIn(self.NS, self.prod_p["pov"]["hpa_scope"]["namespaces"])

    def test_pinned_override_touches_only_listed_namespaces(self):
        before = {r["name"]: r["hpa_class"] for r in self.prod["pov"]["rows"] if r["ns"] != self.NS}
        after = {r["name"]: r["hpa_class"] for r in self.prod_p["pov"]["rows"] if r["ns"] != self.NS}
        self.assertEqual({k: before[k] for k in before.keys() & after.keys()}, {k: after[k] for k in before.keys() & after.keys()})


class FakeResp(io.BytesIO):
    def __init__(self, status, body):
        super().__init__(json.dumps(body).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def fake_opener(responses, seen):
    """responses: list of (status, body) consumed in order; seen collects (method, url, headers, body)."""
    def opener(req, timeout=None):
        seen.append((req.get_method(), req.full_url, dict(req.header_items()), req.data))
        status, body = responses.pop(0)
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "err", {}, io.BytesIO(b"{}"))
        return FakeResp(status, body)
    return opener


class Client(unittest.TestCase):
    def test_token_is_read_from_the_data_wrapper_and_sent_as_bearer(self):
        seen = []
        api = apiv2.PublicApi(("cid", SECRET), opener=fake_opener(
            [(200, {"data": {"access_token": TOKEN, "expires_in": 3600}}), (200, {"data": [], "meta": {}})], seen))
        self.assertEqual(api.workloads_v2("uid-1"), {"data": [], "meta": {}})
        self.assertTrue(seen[0][1].endswith("/public/v1/auth/public_auth"))
        self.assertIn("/public/v2/clusters/uid-1/workloads?period=P30D", seen[1][1])
        self.assertEqual(seen[1][2].get("Authorization"), f"Bearer {TOKEN}")

    def test_failures_never_carry_a_credential(self):
        seen = []
        api = apiv2.PublicApi(("cid", SECRET), opener=fake_opener([(403, {})], seen))
        with self.assertRaises(apiv2.ApiError) as cm:
            api.workloads_v2("uid-1")
        self.assertNotIn(SECRET, str(cm.exception))
        self.assertNotIn(SECRET, repr(api))

    def test_rate_limits_are_retried(self):
        seen = []
        api = apiv2.PublicApi(("cid", SECRET), backoff=0, opener=fake_opener(
            [(200, {"access_token": TOKEN}), (429, {}), (200, {"data": [1]})], seen))
        self.assertEqual(api.workloads_v2("u")["data"], [1])

    def test_credentials_file_must_be_owner_only(self):
        d = Path(tempfile.mkdtemp())
        f = d / "x.env"
        f.write_text(f"PS_ID=cid\nPS_SECRET={SECRET}\n")
        os.chmod(f, 0o644)
        creds, why = apiv2.load_credentials({"api_credentials_file": str(f)})
        self.assertIsNone(creds)
        self.assertIn("chmod 600", why)
        self.assertNotIn(SECRET, why)
        os.chmod(f, 0o600)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PERFECTSCALE_CLIENT_ID", None); os.environ.pop("PERFECTSCALE_CLIENT_SECRET", None)
            self.assertEqual(apiv2.load_credentials({"api_credentials_file": str(f)})[0], ("cid", SECRET))
        shutil.rmtree(d)

    def test_environment_variables_take_precedence(self):
        with mock.patch.dict(os.environ, {"PERFECTSCALE_CLIENT_ID": "e", "PERFECTSCALE_CLIENT_SECRET": "f"}):
            self.assertEqual(apiv2.load_credentials({"api_credentials_file": "/nope"})[0], ("e", "f"))


class Enriched(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cfg = Config.load(fixtures.make(cls.tmp))
        (cls.cfg.data("prod-a") / "workloads-v2.json").write_text(json.dumps({"data": V2_PROD_A, "meta": {}}))
        cls.F = model.load_all(cls.cfg, parallel=False)
        cls.prod = next(f for f in cls.F if f["name"] == "prod-a")
        cls.dev = next(f for f in cls.F if f["name"] == "dev-b")
        cls.rows = {r["name"]: r for r in cls.prod["pov"]["rows"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_hpa_classes_and_detail(self):
        r = self.rows
        self.assertEqual((r["api-0"]["hpa_class"], r["api-0"]["hpa_detail"]), ("pod_resource", "CPU 70%"))
        self.assertEqual(r["api-1"]["hpa_class"], "custom")
        self.assertEqual(r["api-2"]["hpa_class"], "container")
        self.assertEqual(r["billing"]["hpa_class"], "none")
        self.assertIsNone(r["api-5"]["hpa_class"])                    # not in the v2 file: unknown

    def test_waves(self):
        r = self.rows
        self.assertEqual(r["api-0"]["wave"], "1h")                     # pod CPU HPA: with HPA automation alongside
        self.assertEqual(r["api-1"]["wave"], "Review")
        self.assertEqual(r["api-3"]["wave"], "Review")                # automation not supported
        self.assertEqual(r["api-2"]["wave"], "1")
        self.assertEqual(r["billing"]["wave"], "1")
        self.assertTrue(r["api-4"]["immature"])
        sts = next(x for x in self.rows.values() if x["type"] == "StatefulSet")
        self.assertEqual(sts["wave"], "1b")

    def test_hpa_row_is_automated_with_hpa_automation(self):
        r = self.rows["api-0"]
        self.assertIn("HPA on pod CPU/memory: switch on HPA automation with it", pov.review_notes(r))
        self.assertEqual(pov.default_ticked(r), r["waste"] >= pov.AUTOMATION_MIN_USD)
        self.assertEqual(pov.wave_label(r), "Wave 1 + HPA automation")

    def test_automation_status_becomes_eligibility_without_a_browser_file(self):
        self.assertEqual(self.prod["eligibility"]["source"], "api_v2")
        self.assertIn("NotSupported", self.prod["eligibility"]["by_status"])
        self.assertFalse(self.dev["eligibility"]["provided"])          # no v2 file for dev-b
        self.assertTrue(self.prod["api_v2"]["provided"])

    def test_pod_level_hpa_rides_along_with_an_hpa_block(self):
        nss = self.prod["pov"]["namespaces"]
        ns = next(n for n in nss if n["ns"] == "payments")
        self.assertNotIn("api-0", {x["name"] for x in ns["excl"]})      # no longer kept out
        self.assertIn("api-0", {i["name"] for i in ns["hpa"]["items"]})
        on = pov.bundle_ns("prod-a", "2026-11-20", nss, ["payments"], ["Deployment"], hpa={"on": ["payments"], "opts": pov.HPA_DEFAULTS})[1][2]
        self.assertIn("# HPA automation: Enabled for 1 workload with a pod CPU/memory HPA", on)
        self.assertIn('    hpa:\n      operational:\n        automationMode: "Enabled"', on)
        off = pov.bundle_ns("prod-a", "2026-11-20", nss, ["payments"], ["Deployment"], hpa={"on": ["payments"], "opts": None})[1][2]
        self.assertIn("# HPA automation: not included for 1 workload", off)
        self.assertNotIn("    hpa:", off)
        bare = pov.bundle_ns("prod-a", "2026-11-20", nss, ["payments"], ["Deployment"])[1][2]
        self.assertNotIn("HPA automation", bare)

    def test_page_ticks_hpa_rows_reviews_start_unticked_and_verify_passes(self):
        S = Plan(self.cfg, self.F).build()
        files = render.write_all(self.cfg, S, self.F)
        page = next(p for p in files if "-prod-a-pov-automation-kickoff" in p.name).read_text()
        for name in ("api-1", "api-3"):
            self.assertNotIn(f"class=wlck value='{self.rows[name]['rank']}' checked", page, name)
        self.assertIn(f"class=wlck value='{self.rows['billing']['rank']}' checked", page)
        self.assertIn("public API v2", page)
        # Each wave group holds only its own rows; HPA rows sit under "Ready with HPA automation".
        self.assertIn("<details class=wgrp data-g=hpa>", page)
        grp = page.split("<details class=wgrp data-g=hpa>", 1)[1].split("</details>", 1)[0]
        self.assertIn(">api-0</button>", grp)
        self.assertNotIn(">billing</button>", grp)
        self.assertNotIn("data-g=hold", page)
        self.assertIn("HPA triggers: CPU 70%", page)                      # full triggers in the detail row
        self.assertIn("Raise the CPU trigger from 70% to 80%", page)       # a finding worked out from the data
        fbw = page.split("<details class=hparoll><summary>Findings by workload (", 1)[1].split("</details>", 1)[0]
        self.assertIn("<td class=wnote>CPU trigger 70% \u2192 80%</td>", fbw)   # summary column: the short label
        self.assertRegex(fbw, r"<tr class=nsdet data-for='f\d+' hidden>.*Raise the CPU trigger from 70% to 80%")   # full note on click
        dev_page = next(p for p in files if "-dev-b-pov-automation-kickoff" in p.name).read_text()
        self.assertIn("HPA data was not collected", dev_page)
        ok, errs = verify.verify(self.cfg, S, self.F, files)
        self.assertEqual(errs, [])
        self.assertTrue(any("no workload is put on Hold" in m for m in ok))

    def test_v2_file_change_invalidates_the_model_cache(self):
        c = self.cfg.data("prod-a")
        files = [c / n for n in ("summary.json", "workloads-all.jsonl", "workloads-v2.json")]
        before = model.fingerprint(self.cfg, {"name": "prod-a"}, files)
        (c / "workloads-v2.json").write_text(json.dumps({"data": V2_PROD_A[:1], "meta": {}}))
        try:
            self.assertNotEqual(before, model.fingerprint(self.cfg, {"name": "prod-a"}, files))
        finally:
            (c / "workloads-v2.json").write_text(json.dumps({"data": V2_PROD_A, "meta": {}}))


class CollectorStep(unittest.TestCase):
    def test_step_writes_the_file_and_never_prints_credentials(self):
        from psplan.collect import Collector
        tmp = Path(tempfile.mkdtemp())
        cfg = Config.load(fixtures.make(tmp))
        cfg["data_dir"] = str(tmp / "collected"); cfg["pscli"] = str(HERE / "fake_pscli.py")
        with mock.patch.dict(os.environ, {"PERFECTSCALE_CLIENT_ID": "cid", "PERFECTSCALE_CLIENT_SECRET": SECRET}):
            logs = []
            c = Collector(cfg, log=logs.append)
        seen = []
        c.api.opener = fake_opener([(200, {"data": {"access_token": TOKEN}}), (200, {"data": V2_PROD_A, "meta": {}})], seen)
        d = cfg.data("prod-a"); d.mkdir(parents=True, exist_ok=True)
        manifest = {}
        out = io.StringIO()
        with redirect_stdout(out):
            c._v2_workloads(d, manifest, "prod-a", "uid-a")
        self.assertEqual(len(json.loads((d / "workloads-v2.json").read_text())["data"]), len(V2_PROD_A))
        self.assertTrue(manifest["workloads_v2"]["ok"])
        text = "\n".join(logs) + out.getvalue() + json.dumps(manifest)
        self.assertNotIn(SECRET, text); self.assertNotIn(TOKEN, text)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_without_credentials_the_step_is_skipped_not_failed(self):
        from psplan.collect import Collector
        tmp = Path(tempfile.mkdtemp())
        cfg = Config.load(fixtures.make(tmp))
        cfg["pscli"] = str(HERE / "fake_pscli.py"); cfg["api_credentials_file"] = str(tmp / "missing.env")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PERFECTSCALE_CLIENT_ID", None); os.environ.pop("PERFECTSCALE_CLIENT_SECRET", None)
            logs = []
            c = Collector(cfg, log=logs.append)
        self.assertIsNone(c.api)
        d = tmp / "d"; d.mkdir()
        c._v2_workloads(d, {}, "prod-a", "uid-a")
        self.assertEqual(c.failures, [])
        self.assertTrue(any("[skip]" in m for m in logs))
        shutil.rmtree(tmp, ignore_errors=True)


class HpaSnapshot(unittest.TestCase):
    """A cluster snapshot digest (scripts/snapshots.py) beside the tenant data: an HPA pinned at
    min == max is treated as fixed replicas, minReplicas above 2 becomes an exact change, a workload
    often at maxReplicas is reported, and the page shows what the snapshot confirmed."""
    NS = HpaAutomation.NS

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cfg = Config.load(fixtures.make(cls.tmp))
        rows = [json.loads(l) for l in (cfg.data("prod-a") / "workloads-all.jsonl").read_text().splitlines() if l.strip()]
        ns = [r for r in rows if r["namespace"] == cls.NS]
        assert len(ns) >= 3, "fixture needs three workloads in the namespace"
        v2 = [{"id": r["id"], "name": r["name"], "namespace": r["namespace"], "type": r["type"],
               "scaling": {"avgReplicas": 2, "hpaTargets": {"cpu": {"utilization": 90}, "memory": {"utilization": 90}}},
               "automation": {"automatedBy": "none", "status": "NotEnabled"},
               "maturity": {"overall": {"isMature": True, "minutesLeftToMature": 0}}, "containers": {}} for r in ns]
        (cfg.data("prod-a") / "workloads-v2.json").write_text(json.dumps({"data": v2, "meta": {}}))
        cls.pinned, cls.high, cls.busy = ns[0], ns[1], ns[2]
        key = lambda r: f"{r['type']}/{r['namespace']}/{r['name']}"  # noqa: E731
        hpa = [{"namespace": cls.NS, "hpa": "a", "target": key(cls.pinned), "min": 3, "max": 3, "pinned": True, "pdbs": [],
                "other_metrics": [], "trend": None},
               {"namespace": cls.NS, "hpa": "b", "target": key(cls.high), "min": 5, "max": 12, "pinned": False,
                "pdbs": ["b-pdb"], "other_metrics": [], "trend": {"samples": 4, "at_max_pct": 0}},
               {"namespace": cls.NS, "hpa": "c", "target": key(cls.busy), "min": 2, "max": 4, "pinned": False, "pdbs": [],
                "other_metrics": [], "trend": {"samples": 4, "at_max_pct": 50}}]
        dg = {"cluster": "prod-a", "latest": "2026-11-24-21-00", "snapshots": ["2026-11-24-21-00"] * 4, "hpa": hpa,
              "prerequisites": {"exporter": "v1.1.43", "exporter_ok": True, "agent": None, "agent_ok": False},
              "summary": {}}
        sp = model.snapshot_path(cfg, "prod-a")
        sp.parent.mkdir(parents=True)
        sp.write_text(json.dumps(dg))
        cls.cfg, cls.F = cfg, model.load_all(cfg, parallel=False)
        cls.prod = next(f for f in cls.F if f["name"] == "prod-a")
        cls.S = Plan(cfg, cls.F).build()
        cls.files = files = render.write_all(cfg, cls.S, cls.F)
        cls.page = next(p for p in files if "-prod-a-pov-automation-kickoff" in p.name).read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def row(self, r):
        return next(x for x in self.prod["pov"]["rows"] if x["ns"] == self.NS and x["name"] == r["name"])

    def item(self, r):
        n = next(n for n in self.prod["pov"]["namespaces"] if n["ns"] == self.NS)
        return next(i for i in n["hpa"]["items"] if i["name"] == r["name"])

    def test_pinned_hpa_is_fixed_replicas(self):
        self.assertEqual(self.row(self.pinned)["hpa_class"], "pinned")
        self.assertNotEqual(self.row(self.pinned)["wave"], "1h")
        self.assertIn("cluster snapshot", self.row(self.pinned)["hpa_detail"])
        self.assertEqual(self.row(self.high)["wave"], "1h")

    def test_exact_min_replicas_and_max_report(self):
        f = self.item(self.high)["findings"]
        self.assertIn("Lower minReplicas from 5 to 2, or to what PodDisruptionBudget b-pdb needs (maxReplicas 12 stays)",
                      [x["text"] for x in f])
        self.assertFalse(any("hint" in x["short"] for x in f))
        busy = [x for x in self.item(self.busy)["findings"] if x["kind"] == "report"]
        self.assertTrue(any("At maxReplicas (4) in 50% of 4 cluster snapshots" in x["text"] for x in busy))
        self.assertEqual(self.item(self.high)["replicas"]["max"], 12)

    def test_page_shows_snapshot_and_ticks_what_it_confirms(self):
        t = self.page
        self.assertIn("From the cluster snapshot of 2026-11-24 21:00 UTC: exporter v1.1.43 (meets v1.1.28)", t)
        self.assertIn("no PerfectScale automation agent found", t)
        self.assertIn("id=hpaexporter checked", t)
        self.assertNotIn("id=hpaagent checked", t)
        self.assertIn("<th>minReplicas to lower</th>", t)
        self.assertIn("minReplicas 5, maxReplicas 12; PDB b-pdb", t)
        self.assertIn("1 HPA pinned at minReplicas = maxReplicas is treated as fixed replicas", t)
        self.assertTrue(*verify.verify(self.cfg, self.S, self.F, self.files))

    def test_cache_invalidated_when_digest_changes(self):
        sp = model.snapshot_path(self.cfg, "prod-a")
        files = [sp]
        a = model.fingerprint(self.cfg, self.cfg["clusters"][0], files)
        sp.write_text(sp.read_text() + " ")
        self.assertNotEqual(a, model.fingerprint(self.cfg, self.cfg["clusters"][0], files))


if __name__ == "__main__":
    unittest.main()
