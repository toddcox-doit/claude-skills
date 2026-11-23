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


class HpaHeldNamespace(unittest.TestCase):
    """A namespace whose every workload sits behind a pod CPU/memory HPA: before, the picker dropped
    it with no reason on the page (it hid a tenant's largest namespace). Now it is listed greyed and
    counted with the way out, and hpa_pinned_namespaces releases it once the owner confirms min == max."""
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

    @classmethod
    def tearDownClass(cls):
        for t in cls._tmps:
            shutil.rmtree(t, ignore_errors=True)

    def ns(self, f):
        return next((n for n in f["pov"]["namespaces"] if n["ns"] == self.NS), None)

    def test_fully_held_namespace_is_listed_not_dropped(self):
        n = self.ns(self.prod)
        self.assertIsNotNone(n, "a namespace held entirely by HPAs must still be listed")
        self.assertTrue(n["held"]); self.assertEqual(n["auto"], {})
        self.assertIn("hpa", n["held_reasons"])
        hh = self.prod["pov"]["hpa_held"]
        self.assertIn(self.NS, hh["namespaces"]); self.assertGreater(hh["n"], 0)

    def test_page_shows_held_row_and_how_to_release_it_and_verifies(self):
        S = Plan(self.cfg, self.F).build()
        files = render.write_all(self.cfg, S, self.F)
        t = next(p for p in files if "-prod-a-pov-automation-kickoff" in p.name).read_text(encoding="utf-8")
        self.assertIn(f"class=nsck value='{self.NS}' disabled", t)
        self.assertIn("Held: pod CPU/memory HPA on every workload", t)
        self.assertIn("hpa_pinned_namespaces", t)
        D = json.loads(t.split("id=povdata>")[1].split("</script>")[0].replace("<\\/", "</"))
        self.assertNotIn(self.NS, {n["ns"] for n in D["namespaces"]})      # never generatable
        ok, errs = verify.verify(self.cfg, S, self.F, files)
        self.assertEqual(errs, [])

    def test_pinned_override_releases_the_namespace(self):
        rows = [r for r in self.prod_p["pov"]["rows"] if r["ns"] == self.NS]
        self.assertTrue(rows, "fixture namespace should rank once released")
        self.assertTrue(all(r["hpa_class"] == "pinned" and r["wave"] in ("1", "1b") for r in rows))
        self.assertTrue(all("pinned (min = max, confirmed)" in r["hpa_detail"] for r in rows))
        self.assertIn("HPA pinned (min = max, confirmed): a resize cannot make it scale", pov.review_notes(rows[0]))
        n = self.ns(self.prod_p)
        self.assertFalse(n["held"]); self.assertTrue(n["auto"])
        self.assertNotIn(self.NS, self.prod_p["pov"]["hpa_held"]["namespaces"])

    # ---- the plan agrees with the kickoff page: HPA-held savings move out of the waves
    def test_plan_carries_hpa_held_savings_in_their_own_action(self):
        S = Plan(self.cfg, self.F).build()
        by = {a["key"]: a for a in S["actions"]}
        a = by["hpa_hold"]
        L = self.prod["lanes"]["controller"]
        self.assertGreater(L["n_hpa_hold"], 0)
        self.assertAlmostEqual(a["savings_monthly_usd"], sum(f["lanes"]["controller"]["pot_hpa_hold"] for f in self.F), places=6)
        self.assertEqual(a["action_type"], "hpa_confirm_pinned")
        self.assertEqual(a["savings_basis"], "achievable_now")
        self.assertIn(a["confidence"], ("low", "medium"))                 # never high: min/max is unseen
        self.assertIn(by["automation_proof"]["id"], a["depends_on"])
        self.assertIn("hpa_pinned_namespaces", " ".join(a["steps"]))
        self.assertIn(self.NS, a["title"] + a["scope"] + a["evidence"] + " ".join(a["steps"]))

    def test_waves_no_longer_count_hpa_held_savings(self):
        S = Plan(self.cfg, self.F).build()
        waves = [a for a in S["actions"] if a["key"] in ("automation_proof", "automation_w2", "automation_w3")]
        held_uids = {f["uid"] for f in self.F if f["lanes"]["controller"].get("pot_hpa_hold", 0) >= 1}
        for w in waves:
            for f in self.F:
                if f["uid"] in w["_uids" if "_uids" in w else "cluster_uids"] and f["uid"] in held_uids:
                    L = f["lanes"]["controller"]
                    self.assertLessEqual(w["savings_monthly_usd"], L["pot"] - L["pot_hpa_hold"] + 1e-6, w["id"])
        proof = next(w for w in waves if w["key"] == "automation_proof")
        if next(f for f in self.F if f["name"] == proof["cluster"])["lanes"]["controller"].get("n_hpa_hold"):
            self.assertIn("pod CPU/memory HPAs held out", proof["scope"])

    def test_achievable_total_is_moved_not_changed(self):
        held = Plan(self.cfg, self.F).build()
        pinned = Plan(self.cfg_p, self.F_p).build()
        self.assertEqual(held["totals"]["achievable_now_monthly"], pinned["totals"]["achievable_now_monthly"])
        self.assertNotIn("hpa_hold", {a["key"] for a in pinned["actions"]})   # released: nothing left to confirm
        self.assertIn("hpa_hold", {a["key"] for a in held["actions"]})

    def test_hpa_question_is_a_decision_valued_at_the_held_savings(self):
        S = Plan(self.cfg, self.F).build()
        a = next(x for x in S["actions"] if x["key"] == "hpa_hold")
        d = next(x for x in S["decision_detail"] if x["text"].startswith("Are the HPAs"))
        self.assertEqual(d["usd"], round(a["savings_monthly_usd"]))
        self.assertEqual(d["group"], "Intent")
        self.assertEqual(S["automation_gated"]["hpa_usd"], round(a["savings_monthly_usd"]))
        self.assertNotIn("hpa_usd", {k for k, v in Plan(self.cfg_p, self.F_p).build()["automation_gated"].items() if v})

    def test_exec_summary_splits_the_automation_decision_when_hpas_hold_the_money(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        import copy, subprocess
        S = copy.deepcopy(Plan(self.cfg, self.F).build())
        S["automation_gated"].update(usd=10000, share=0.9, hpa_usd=8000)    # force the dominant case
        p = self._tmps[0] / "es.json"; out = self._tmps[0] / "es.html"
        p.write_text(json.dumps(S))
        r = subprocess.run([node, str(HERE.parent / "exec_summary.js"), str(p), str(out)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        t = out.read_text(encoding="utf-8")
        self.assertIn("automation alone reaches $2,000 a month, and $8,000 a month more needs that answer.", t)
        self.assertNotIn("waiting on that single decision", t)
        S["automation_gated"]["hpa_usd"] = 0                                # no HPA hold: the old wording stands
        p.write_text(json.dumps(S))
        subprocess.run([node, str(HERE.parent / "exec_summary.js"), str(p), str(out)], check=True, capture_output=True)
        self.assertIn("waiting on that single decision", out.read_text(encoding="utf-8"))

    def test_pinned_override_touches_only_listed_namespaces(self):
        # The default fixture's own HPA-free namespaces are untouched either way.
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
        self.assertEqual(r["api-0"]["wave"], "Hold")
        self.assertEqual(r["api-1"]["wave"], "Review")
        self.assertEqual(r["api-3"]["wave"], "Review")                # automation not supported
        self.assertEqual(r["api-2"]["wave"], "1")
        self.assertEqual(r["billing"]["wave"], "1")
        self.assertTrue(r["api-4"]["immature"])
        sts = next(x for x in self.rows.values() if x["type"] == "StatefulSet")
        self.assertEqual(sts["wave"], "1b")

    def test_constant_replicas_on_a_held_row_is_a_hint_not_a_promotion(self):
        r = dict(self.rows["api-0"])
        self.assertEqual(r["wave"], "Hold")
        r["replicas"], r["replicas_max"] = 2, 2
        self.assertIn("may be pinned", " ".join(pov.review_notes(r)))
        self.assertFalse(pov.default_ticked(r))
        r["replicas_max"] = 5
        self.assertNotIn("may be pinned", " ".join(pov.review_notes(r)))

    def test_automation_status_becomes_eligibility_without_a_browser_file(self):
        self.assertEqual(self.prod["eligibility"]["source"], "api_v2")
        self.assertIn("NotSupported", self.prod["eligibility"]["by_status"])
        self.assertFalse(self.dev["eligibility"]["provided"])          # no v2 file for dev-b
        self.assertTrue(self.prod["api_v2"]["provided"])

    def test_pod_level_hpa_is_kept_out_of_its_namespace_config(self):
        ns = next(n for n in self.prod["pov"]["namespaces"] if n["ns"] == "payments")
        self.assertIn(("api-0", "hpa"), {(x["name"], x["reason"]) for x in ns["excl"]})
        files = pov.bundle_ns("prod-a", "2026-11-20", self.prod["pov"]["namespaces"], ["payments"], ["Deployment"])
        self.assertIn("pod-level CPU/memory HPA", files[1][2])

    def test_page_holds_and_reviews_start_unticked_and_verify_passes(self):
        S = Plan(self.cfg, self.F).build()
        files = render.write_all(self.cfg, S, self.F)
        page = next(p for p in files if "-prod-a-pov-automation-kickoff" in p.name).read_text()
        for name in ("api-0", "api-1", "api-3"):
            self.assertNotIn(f"class=wlck value='{self.rows[name]['rank']}' checked", page, name)
        self.assertIn(f"class=wlck value='{self.rows['billing']['rank']}' checked", page)
        self.assertIn("public API v2", page)
        # Each wave group holds only its own rows, Review and Hold start collapsed.
        self.assertIn("<details class=wgrp data-g=hold>", page)
        hold = page.split("<details class=wgrp data-g=hold>", 1)[1].split("</details>", 1)[0]
        self.assertIn(">api-0</button>", hold)
        self.assertNotIn(">billing</button>", hold)
        self.assertIn("HPA triggers: CPU 70%", page)                      # full triggers in the detail row
        dev_page = next(p for p in files if "-dev-b-pov-automation-kickoff" in p.name).read_text()
        self.assertIn("HPA data was not collected", dev_page)
        ok, errs = verify.verify(self.cfg, S, self.F, files)
        self.assertEqual(errs, [])
        self.assertTrue(any("no Hold or Review workload" in m for m in ok))

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


if __name__ == "__main__":
    unittest.main()
