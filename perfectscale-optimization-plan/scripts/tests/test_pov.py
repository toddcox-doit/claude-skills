"""POV automation kickoff: ranking (model.pov_rank), CR generation (pov.py), the browser builder's
parity with the Python one, and the rendered page."""
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
import fixtures  # noqa: E402
from psplan.config import Config  # noqa: E402
from psplan import model, pov, render, verify  # noqa: E402
from psplan.actions import Plan  # noqa: E402

try:
    import yaml
except ImportError:  # optional: structural YAML checks skip without PyYAML
    yaml = None

NODE = shutil.which("node")


def run_node(src):
    r = subprocess.run([NODE, "-e", src], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.cfg = Config.load(fixtures.make(cls.tmp))
        cls.F = model.load_all(cls.cfg, parallel=False)
        cls.by = {f["name"]: f for f in cls.F}
        cls.prod = cls.by["prod-a"]
        cls.rows = cls.prod["pov"]["rows"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ------------------------------------------------------------ ranking
    def test_ranked_rows_exclude_what_automation_cannot_or_should_not_touch(self):
        lanes = {r["lane"] for r in self.rows}
        self.assertNotIn("managed", lanes)                     # kube-system add-ons
        self.assertNotIn("held_out", lanes)                    # manual-hold-svc
        self.assertTrue(all(r["type"] in model.POV_CR_TYPES for r in self.rows))
        names = {r["name"] for r in self.rows}
        self.assertNotIn("metrics-server", names)              # managed, even though it carries an OOM risk
        self.assertNotIn("manual-hold-svc", names)
        self.assertFalse(any(n.startswith("airflow-task-") for n in names))   # GroupedJob

    def test_every_exclusion_is_counted_not_dropped(self):
        h = self.prod["pov"]["held"]
        self.assertEqual(h["managed"]["n"], 4)                 # 3 coredns + metrics-server
        self.assertEqual(h["held_out"]["n"], 1)
        self.assertEqual(h["unsupported_type"]["n"], 6)
        self.assertEqual(self.prod["pov"]["held_types"], {"GroupedJob": 6})

    def test_top_n_and_rank_order(self):
        self.assertEqual(len(self.rows), 50)                   # prod-a has more candidates than the default 50
        self.assertEqual([r["rank"] for r in self.rows], list(range(1, 51)))
        scores = [r["score"] for r in self.rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(r["score"] > 0 for r in self.rows))

    def test_score_blends_savings_and_risk(self):
        top_pot = max(r["pot"] for r in self.rows)              # run-rate scaled, same factor for every row
        ingest = next(r for r in self.rows if r["name"] == "ingest")
        self.assertEqual(ingest["risk"], "high")
        self.assertTrue(ingest["oom"])
        self.assertGreater(ingest["res_likely"], 0)
        want = round(100 * (0.6 * ingest["pot"] / top_pot + 0.4 * 3 / 4), 1)
        self.assertAlmostEqual(ingest["score"], want, places=1)
        # Risk lifts a workload with modest savings into the scope: ingest (savings 30, OOM) ranks
        # near the top, while the quiet svc-eh-* cohort (savings 20, no risk) falls below the cut.
        self.assertLessEqual(ingest["rank"], 20)
        self.assertFalse(any(r["name"].startswith("svc-eh-") for r in self.rows))

    def test_risk_sev_never_reads_oom_below_high(self):
        w = fixtures.wl("x", "ns", "Deployment", 1, 1, 1, 30, cont_inds=[fixtures.ind("OOM", "risk", 1)])
        self.assertEqual(model.risk_sev(w), 3)
        w = fixtures.wl("x", "ns", "Deployment", 1, 1, 1, 30, cont_inds=[fixtures.ind("CpuThrottling", "risk", 4)])
        self.assertEqual(model.risk_sev(w), 4)

    def test_top_n_and_weight_are_configurable(self):
        ws = []
        for x in fixtures.build_dev_b():
            x["_lane"] = "controller"; x["_jvm"] = False; x["_hpa"] = None
            ws.append(x)
        out = model.pov_rank(ws, lambda v: v, self.cfg["thresholds"], top_n=3, savings_weight=1.0)
        self.assertEqual(len(out["rows"]), 3)
        self.assertEqual(out["rows"][0]["name"], "search-es")   # largest savings, weight all on savings
        self.assertEqual(out["rows"][0]["score"], 100.0)

    # ------------------------------------------------------------ CR content
    def test_keep_limit_false_carries_a_review_comment_on_and_above_the_line(self):
        doc = pov.workload_doc(self.rows[0])
        lines = doc.split("\n")
        i = next(i for i, x in enumerate(lines) if x.strip().startswith("keepLimit:"))
        self.assertEqual(lines[i].strip(), "keepLimit: false  # REVIEW: see the note above before applying")
        self.assertIn("  # REVIEW", lines[i])                   # two spaces before an inline comment
        j = i
        while lines[j - 1].strip().startswith("#"):
            j -= 1
        block = " ".join(x.strip().lstrip("# ") for x in lines[j:i])  # the wrapped note directly above
        self.assertTrue(block.startswith("REVIEW: keepLimit is false"))
        self.assertIn("ResourceQuota on limits.cpu", block)

    @staticmethod
    def TEMPLATE_OPERATIONAL(waste_freq):
        """PerfectScale's namespace/workload CR template operational block (docs.perfectscale.io,
        "Configuring Automation for a workload with CRD"), at its documented defaults, with the two
        deliberate changes: increases off (cost optimization) and keepLimit false. Deprecated fields
        are absent."""
        win = {d: ["00:00-23:59"] for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
        return {
            "automationMode": "Enabled", "automationStrategy": "inPlace", "podResizePendingStrategy": "rollingRestart",
            "duringRolloutInProgress": "pause",
            "timeConstraints": {"wasteMaxAutomationFrequency": waste_freq, "fixResiliencyMaxAutomationFrequency": "30m",
                                "maintenanceWindow": win, "maintenanceWindowIgnoredMinResiliencyLevel": "None"},
            "restrictions": {
                "workloadMinWasteUSDPerMonth": 5,
                "cpuManagement": {"request": {"increaseEnabled": False, "decreaseEnabled": True, "minimumCores": -1, "maximumCores": -1,
                                              "cpuRunawayDetection": {"maxCpuRequestIncreaseIterations": {"daily": 3, "weekly": 6}}},
                                  "limit": {"keepLimit": False}},
                "memoryManagement": {"request": {"increaseEnabled": False, "decreaseEnabled": True, "minimumGiB": -1, "maximumGiB": -1},
                                     "limit": {"increaseEnabled": False, "decreaseEnabled": False, "minimumGiB": -1, "maximumGiB": -1,
                                               "memoryLeakDetection": {"maxMemoryIncreaseIterations": {"daily": 3, "weekly": 6}}}},
            },
        }

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_namespace_cr_carries_the_full_block_per_kind_at_its_default_frequency(self):
        d = yaml.safe_load(pov.namespace_doc("shop", list(model.POV_CR_TYPES)))
        wt = d["spec"]["automation"]["workloadTypes"]
        for kind, freq in pov.WASTE_FREQ.items():
            self.assertEqual(wt[kind]["operational"], self.TEMPLATE_OPERATIONAL(freq), kind)
        self.assertEqual(pov.WASTE_FREQ["DaemonSet"], "4h")

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_workload_cr_is_the_published_cost_saving_shape(self):
        sts = next(r for r in self.rows if r["type"] == "StatefulSet")
        d = yaml.safe_load(pov.workload_doc(sts))
        self.assertEqual(d["apiVersion"], "perfectscale.io/v1")
        self.assertEqual(d["kind"], "WorkloadAutomationConfig")
        self.assertEqual(d["metadata"]["namespace"], sts["ns"])
        self.assertEqual(d["spec"]["targetRef"], {"kind": "StatefulSet", "name": sts["name"]})
        self.assertEqual(d["spec"]["automation"]["operational"], self.TEMPLATE_OPERATIONAL("24h"))   # StatefulSet default
        self.assertTrue(pov.workload_doc(sts).startswith("# StatefulSet:"))   # the published caution travels with it

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_namespace_cr_enables_only_the_given_kinds(self):
        d = yaml.safe_load(pov.namespace_doc("payments", ["Deployment", "CronJob"]))
        self.assertEqual(d["kind"], "NamespaceAutomationConfig")
        self.assertEqual(d["metadata"], {"name": "namespace-automation-config", "namespace": "payments"})
        wt = d["spec"]["automation"]["workloadTypes"]
        self.assertEqual(set(wt), {"Deployment", "CronJob"})
        for t in wt.values():
            self.assertEqual(t["operational"]["automationMode"], "Enabled")
            self.assertIs(t["operational"]["restrictions"]["cpuManagement"]["limit"]["keepLimit"], False)

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_cluster_prerequisite_scopes_nothing_on_by_itself(self):
        d = yaml.safe_load(pov.cluster_doc())
        self.assertEqual(d["kind"], "ClusterAutomationConfig")
        self.assertIs(d["spec"]["automation"]["operational"]["stopAllAutomation"], False)
        wt = d["spec"]["automation"]["workloadTypes"]
        self.assertEqual(set(wt), set(model.POV_CR_TYPES))
        self.assertTrue(all(v["operational"]["automationMode"] == "Disabled" for v in wt.values()))

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_names_that_look_like_numbers_or_booleans_stay_strings(self):
        r = {"name": "true", "ns": "123", "type": "Deployment"}
        d = yaml.safe_load(pov.workload_doc(r))
        self.assertEqual(d["metadata"]["namespace"], "123")
        self.assertEqual(d["spec"]["targetRef"]["name"], "true")
        self.assertEqual(yaml.safe_load(pov.namespace_doc("123", ["Deployment"]))["metadata"]["namespace"], "123")

    @unittest.skipIf(shutil.which("yamllint") is None, "yamllint not installed")
    def test_every_generated_file_passes_default_yamllint(self):
        """yamllint's default rules (80-column lines, document start, comment spacing, indentation)
        are what a customer's GitOps pre-commit gate runs; every file either generator writes must pass."""
        allt = list(model.POV_CR_TYPES)
        nss = self.mixed_ns()["namespaces"] + self.prod["pov"]["namespaces"]
        files = (pov.bundle("prod-a", "2026-11-20", self.rows, allt)
                 + pov.bundle_ns("prod-a", "2026-11-20", nss, [n["ns"] for n in nss], allt)[1:])
        d = self.tmp / "lint"
        for i, (name, _, text) in enumerate(files):
            (d / f"{i:03d}.yaml").parent.mkdir(parents=True, exist_ok=True)
            (d / f"{i:03d}.yaml").write_text(text)
        r = subprocess.run(["yamllint", "-d", "default", "-f", "parsable", str(d)], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "", r.stdout[:2000])
        self.assertTrue(any("Disabled" in t for _, _, t in files))        # exclusion docs were linted too

    def test_cr_name_is_a_dns_subdomain_and_keeps_kinds_apart(self):
        a = pov.cr_name({"type": "Deployment", "name": "api"})
        b = pov.cr_name({"type": "StatefulSet", "name": "api"})
        self.assertNotEqual(a, b)
        long = pov.cr_name({"type": "Deployment", "name": "x" * 300 + "-"})
        self.assertLessEqual(len(long), 253)
        self.assertRegex(long, r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_workload_bundle_one_file_per_workload_of_the_selected_types(self):
        files = pov.bundle("prod-a", "2026-11-20", self.rows, ["StatefulSet"])
        sts = [r for r in self.rows if r["type"] == "StatefulSet"]
        self.assertEqual(files[0][0], "prod-a-cluster-automation-config.yaml")
        self.assertEqual([n for n, _, _ in files[1:]], [pov.wl_file(r) for r in sts])          # rank order
        for (name, docs, text), r in zip(files[1:], sts):
            d = [x for x in yaml.safe_load_all(text) if x]
            self.assertEqual((docs, len(d)), (1, 1))
            self.assertEqual(d[0]["kind"], "WorkloadAutomationConfig")
            self.assertEqual(d[0]["spec"]["targetRef"], {"kind": "StatefulSet", "name": r["name"]})
            self.assertTrue(name.startswith(f"workloads/{r['ns']}-statefulset-"))
            self.assertIn(f"# Rank in the table: {r['rank']}\n", text)
            self.assertNotIn("__", text)                         # every token filled

    def test_unticked_workloads_get_no_file(self):
        dep = [r for r in self.rows if r["type"] == "Deployment"]
        keep = {dep[0]["rank"], dep[2]["rank"]}
        files = pov.bundle("prod-a", "2026-11-20", self.rows, ["Deployment"], ticked=keep)
        self.assertEqual([n for n, _, _ in files[1:]], [pov.wl_file(dep[0]), pov.wl_file(dep[2])])
        # a ticked row of an unselected type is still not generated
        sts = next(r for r in self.rows if r["type"] == "StatefulSet")
        self.assertEqual(len(pov.bundle("prod-a", "2026-11-20", self.rows, ["Deployment"], ticked={sts["rank"]})), 1)
        self.assertEqual(len({n for n, _, _ in pov.bundle("prod-a", "2026-11-20", self.rows, list(model.POV_CR_TYPES))}),
                         len(self.rows) + 1)                     # file names never collide

    # ------------------------------------------------------------ namespace scope
    def mixed_ns(self):
        """One namespace holding automatable, held-out, stateful and JVM workloads, plus a managed
        namespace and one with nothing automatable - the cases the fixture tenant keeps apart."""
        ws = []
        for name, typ, lane, jvm, pot in (("api", "Deployment", "controller", False, 40), ("cron", "CronJob", "controller", False, 5),
                                           ("hold", "Deployment", "held_out", False, 30), ("db", "StatefulSet", "stateful", False, 20),
                                           ("javasvc", "Deployment", "controller", True, 25), ("ds", "DaemonSet", "controller", False, 3)):
            x = fixtures.wl(name, "shop", typ, 100, pot, pot, 30); x.update(_lane=lane, _jvm=jvm, _hpa=None); ws.append(x)
        x = fixtures.wl("coredns", "kube-system", "Deployment", 10, 5, 5, 30); x.update(_lane="managed", _jvm=False, _hpa=None); ws.append(x)
        x = fixtures.wl("only-held", "vault", "Deployment", 10, 5, 5, 30); x.update(_lane="held_out", _jvm=False, _hpa=None); ws.append(x)
        return model.pov_namespaces(ws, lambda v: v)

    def test_namespace_breakdown_lists_exclusions_and_skips_empty_namespaces(self):
        out = self.mixed_ns()
        by = {n["ns"]: n for n in out["namespaces"]}
        # vault's only workload is held out by the tenant config: listed, held, after the automatable
        # ones and before managed - no longer dropped into an unexplained count.
        self.assertEqual(list(by), ["shop", "vault", "kube-system"])
        self.assertTrue(by["vault"]["held"]); self.assertFalse(by["shop"]["held"])
        self.assertEqual(by["vault"]["held_reasons"], {"held_out": 1})
        self.assertEqual(by["vault"]["auto"], {})
        self.assertEqual(out["ns_nothing_to_automate"], 0)
        shop = by["shop"]
        self.assertEqual(set(shop["auto"]), {"Deployment", "CronJob", "DaemonSet"})
        self.assertEqual(shop["auto"]["Deployment"]["n"], 1)
        self.assertEqual({(x["name"], x["reason"]) for x in shop["excl"]},
                         {("hold", "held_out"), ("db", "stateful"), ("javasvc", "jvm")})
        self.assertTrue(by["kube-system"]["managed"])

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_namespace_bundle_one_file_each_with_exclusions_disabled(self):
        ns = self.mixed_ns()["namespaces"]
        files = pov.bundle_ns("prod-a", "2026-11-20", ns, ["shop", "kube-system"], ["Deployment", "StatefulSet"])
        self.assertEqual([n for n, _, _ in files], ["prod-a-cluster-automation-config.yaml",
                                                    "namespaces/shop-namespace-automation-config.yaml"])   # managed never written
        _, docs, text = files[1]
        d = [x for x in yaml.safe_load_all(text) if x]
        self.assertEqual(docs, len(d))
        self.assertEqual(d[0]["kind"], "NamespaceAutomationConfig")
        self.assertEqual(d[0]["metadata"]["namespace"], "shop")
        # StatefulSet was selected but shop has no automatable one (db is stateful): not enabled.
        self.assertEqual(set(d[0]["spec"]["automation"]["workloadTypes"]), {"Deployment"})
        ex = d[1:]
        self.assertEqual({x["spec"]["targetRef"]["name"] for x in ex}, {"hold", "javasvc"})   # db's kind is not enabled
        self.assertTrue(all(x["spec"]["automation"]["operational"]["automationMode"] == "Disabled" for x in ex))
        self.assertEqual({x["metadata"]["name"] for x in ex}, {pov.cr_name({"type": "Deployment", "name": n}) for n in ("hold", "javasvc")})
        self.assertIn("# Kept out: 2\n", text)
        self.assertNotIn("__", text)

    def test_namespace_with_none_of_the_selected_kinds_is_skipped(self):
        ns = self.mixed_ns()["namespaces"]
        files = pov.bundle_ns("prod-a", "2026-11-20", ns, ["shop"], ["Job"])
        self.assertEqual(len(files), 1)                           # prerequisite only

    @unittest.skipIf(NODE is None, "node not installed")
    def test_browser_namespace_builder_matches_python_byte_for_byte(self):
        nss = self.mixed_ns()["namespaces"] + self.prod["pov"]["namespaces"]
        D = pov.page_data("prod-a", "2026-11-20", self.rows, nss)
        allns = [n["ns"] for n in nss]
        cases = [(allns, list(model.POV_CR_TYPES)), (["shop", "payments"], ["Deployment", "StatefulSet"]),
                 (["shop"], ["DaemonSet"]), ([], ["Deployment"])]
        src = (pov.JS_BUILD_NS + ";var D=" + json.dumps(D) + ";var out=" + json.dumps(cases)
               + ".map(function(c){return psBuildNs(D,c[0],c[1])});process.stdout.write(JSON.stringify(out))")
        got = json.loads(run_node(src))
        for (ck, sel), js_files in zip(cases, got):
            py = pov.bundle_ns("prod-a", "2026-11-20", nss, ck, sel)
            self.assertEqual([[f["name"], f["docs"], f["text"]] for f in js_files], [list(x) for x in py], f"case {ck} {sel}")

    def test_fixture_namespace_picker_covers_namespaces_the_table_never_reaches(self):
        listed = {n["ns"] for n in self.prod["pov"]["namespaces"] if not n["managed"]}
        in_table = {r["ns"] for r in self.rows}
        self.assertIn("airflow", listed - in_table)               # 250 bare Jobs: never ranked, still a namespace
        # streaming holds only stateful-pattern brokers: ranked one by one, but nothing a namespace
        # config should switch on. It is listed as held (greyed, with the reason) and never generatable.
        st = next(n for n in self.prod["pov"]["namespaces"] if n["ns"] == "streaming")
        self.assertTrue(st["held"]); self.assertEqual(set(st["held_reasons"]), {"stateful"})
        self.assertIn("streaming", in_table & listed)
        self.assertNotIn("streaming", {n["ns"] for n in pov.page_data("prod-a", "2026-11-20", self.rows, self.prod["pov"]["namespaces"])["namespaces"]})
        self.assertIn("kube-system", {n["ns"] for n in self.prod["pov"]["namespaces"] if n["managed"]})

    # ------------------------------------------------------------ browser parity
    @unittest.skipIf(NODE is None, "node not installed")
    def test_browser_builder_matches_python_byte_for_byte(self):
        D = pov.page_data("prod-a", "2026-11-20", self.rows)
        odd = [r["rank"] for r in self.rows if r["rank"] % 2]
        cases = [(list(model.POV_CR_TYPES), None), (["Deployment"], None), (["StatefulSet", "Deployment"], odd),
                 (["CronJob"], None), (["Deployment"], [])]
        src = (pov.JS_BUILD + ";var D=" + json.dumps(D) + ";var out=" + json.dumps(cases)
               + ".map(function(c){return psBuild(D,c[0],c[1])});process.stdout.write(JSON.stringify(out))")
        got = json.loads(run_node(src))
        for (s, tk), js_files in zip(cases, got):
            py = pov.bundle("prod-a", "2026-11-20", self.rows, s, None if tk is None else set(tk))
            self.assertEqual([[f["name"], f["docs"], f["text"]] for f in js_files], [list(x) for x in py], f"case {s} {tk}")

    @unittest.skipIf(NODE is None, "node not installed")
    def test_browser_zip_opens_with_the_same_files(self):
        files = [{"name": n, "text": t} for n, _, t in pov.bundle("prod-a", "2026-11-20", self.rows, ["Deployment"])]
        src = (pov.JS_ZIP + ";psZip(" + json.dumps(files) + ").arrayBuffer().then(function(b){"
               "process.stdout.write(Buffer.from(b).toString('base64'))})")
        import base64
        z = zipfile.ZipFile(io.BytesIO(base64.b64decode(run_node(src))))
        self.assertIsNone(z.testzip())
        self.assertEqual({n: z.read(n).decode() for n in z.namelist()}, {f["name"]: f["text"] for f in files})

    # ------------------------------------------------------------ rendered page
    def test_one_page_per_cluster_that_passes_verify(self):
        S = Plan(self.cfg, self.F).build()
        files = render.write_all(self.cfg, S, self.F)
        pages = [p for p in files if "pov-automation-kickoff" in p.name]
        self.assertEqual(len(pages), len(self.F))
        ok, errs = verify.verify(self.cfg, S, self.F, files)
        self.assertEqual(errs, [])
        self.assertTrue(any("keepLimit false with an inline REVIEW" in m for m in ok))
        t = next(p for p in pages if "-prod-a-" in p.name).read_text()
        self.assertIn("Generate workload CRDs", t)
        self.assertIn("Generate namespace CRDs", t)
        self.assertIn("class=nsck value='kube-system' disabled", t)
        self.assertIn("class=nsck value='payments' checked", t)          # in the ranked table: ticked by default
        cut = next(n["ns"] for n in self.prod["pov"]["namespaces"]
                   if not n["managed"] and n["ns"] not in {r["ns"] for r in self.rows})
        self.assertRegex(t, rf"class=nsck value='{re.escape(cut)}' aria-label")   # listed, not ticked
        self.assertIn("id=typemenu", t)
        for k in ("Deployment", "StatefulSet"):
            self.assertIn(f"value='{k}' checked", t)
        self.assertNotIn("value='GroupedJob'", t)
        self.assertNotRegex(t, r"<tr data-type='[^']*' data-n='kube-system'")   # no ranked kube-system row
        self.assertIn("Kept out of this table", t)
        self.assertIn("GroupedJob (6)", t)
        self.assertEqual(t.count("<tr data-type="), 50)
        self.assertEqual(len(re.findall(r"class=wlck value='\d+' checked", t)), 50)   # every ranked row ticked by default
        # Everything starts collapsed: every wave group, and every section (rolled up by JS_COLLAPSE,
        # which must run after JS_RESIZE so column widths are measured while still visible).
        self.assertIn("<details class=wgrp data-g=ready>", t)
        self.assertNotRegex(t, r"<details class=wgrp[^>]* open>")
        self.assertIn("every section starts collapsed", t)
        self.assertLess(t.index("var MIN=44"), t.index("every section starts collapsed"))
        self.assertIn("beforeprint", t)
        self.assertIn("class=gsel data-g=ready data-v=1", t)              # per-group Select all
        self.assertEqual(len(re.findall(r"<tr class=nsdet data-for='r\d+' hidden>", t)), 50)   # a detail row per workload
        self.assertNotIn("line-through", t)

    def test_verify_catches_a_page_that_drifts_from_the_model(self):
        S = Plan(self.cfg, self.F).build()
        files = render.write_all(self.cfg, S, self.F)
        page = next(p for p in files if "pov-automation-kickoff" in p.name and "-prod-a-" in p.name)
        good = page.read_text()
        try:
            page.write_text(good.replace("keepLimit: false  # REVIEW", "keepLimit: false", 1))
            _, errs = verify.verify(self.cfg, S, self.F, files)
            self.assertTrue(any("keepLimit false" in m for m in errs))
            page.write_text(re.sub(r"<tr data-type=[^>]*>.*?</tr>", "", good, count=1, flags=re.S))
            _, errs = verify.verify(self.cfg, S, self.F, files)
            self.assertTrue(any("exactly the 50 ranked rows" in m for m in errs))
            page.write_text(good.replace("class=nsck value='kube-system' disabled", "class=nsck value='kube-system'"))
            _, errs = verify.verify(self.cfg, S, self.F, files)
            self.assertTrue(any("checkbox disabled" in m for m in errs))
        finally:
            page.write_text(good)

    def test_xlsx_carries_the_pov_sheet(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl not installed")
        S = Plan(self.cfg, self.F).build()
        files = render.write_all(self.cfg, S, self.F)
        wb = load_workbook(next(p for p in files if p.suffix == ".xlsx"))
        ws = wb["POV kickoff"]
        self.assertEqual([c.value for c in ws[1]], pov.POV_COLS)
        n = sum(len(f["pov"]["rows"]) for f in self.F)
        self.assertEqual(ws.max_row - 1, n)

    def test_rows_under_the_automation_minimum_start_unticked(self):
        f = json.loads(json.dumps(self.prod))
        low = f["pov"]["rows"][-1]
        low["waste"] = pov.AUTOMATION_MIN_USD - 1
        t = pov.render_pov_html(self.cfg, f)
        self.assertRegex(t, rf"class=wlck value='{low['rank']}' aria-label")             # not checked
        self.assertRegex(t, rf"class=wlck value='{f['pov']['rows'][0]['rank']}' checked")
        self.assertIn("automation skips it", " ".join(pov.review_notes(low)))
        # a namespace whose only waste is under the minimum is listed but not pre-ticked
        n = next(x for x in f["pov"]["namespaces"] if not x["managed"] and x["ns"] == low["ns"])
        for a in n["auto"].values():
            a["waste"] = 0.0
        t = pov.render_pov_html(self.cfg, f)
        self.assertRegex(t, rf"class=nsck value='{re.escape(low['ns'])}' aria-label")

    def test_indicator_labels_keep_acronyms_whole(self):
        self.assertEqual(model.humanize("HPAAtMaxReplicasObserved"), "HPA at max replicas")
        self.assertEqual(model.humanize("HPAMinEqualsMax"), "HPA min equals max")
        self.assertEqual(model.humanize("PodsPendingObserved"), "Pods pending observed")

    def test_empty_cluster_page_says_so_and_offers_no_generator(self):
        f = dict(self.by["dev-b"]); f["pov"] = dict(f["pov"], rows=[])
        t = pov.render_pov_html(self.cfg, f)
        self.assertIn("nothing to scope here yet", t)
        self.assertNotIn("id=gen", t)


if __name__ == "__main__":
    unittest.main()
