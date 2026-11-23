"""POV automation kickoff: one self-contained HTML page per cluster that sets the scope of a
PerfectScale POV, from the day automation is switched on to the technical win.

The page ranks the cluster's top workloads by cost and resiliency in one table (model.pov_rank),
filters it by controller type, and generates PerfectScale automation Custom Resources, written to
the cost-optimization profile of PerfectScale's published examples
(github.com/perfectscale-io/perfectscale-io.github.io, charts/psc-autoscaler/examples), two
independent ways:

  workload CRDs  (bundle)    the cluster prerequisite + one file of WorkloadAutomationConfigs for
                             the ranked rows of the selected controller types
  namespace CRDs (bundle_ns) the cluster prerequisite + one file per ticked namespace: its
                             NamespaceAutomationConfig and a Disabled WorkloadAutomationConfig for
                             each workload that must not ride along (held out, stateful, JVM)

The cluster prerequisite is the one ClusterAutomationConfig a cluster needs, with every kind
Disabled at cluster scope so automation runs only where the other files enable it.

YAML is built here, in Python. The browser only reassembles pieces this module rendered: JS_BUILD
and JS_BUILD_NS are pure functions (no DOM) whose output tests compare byte-for-byte with bundle()
and bundle_ns(), so the files a customer downloads are the files the test suite checked.

Pieces are embedded as arrays of lines, not as strings with escaped newlines: an escaped newline
after a YAML key ("metadata:" + backslash-n) reads as a Windows drive path to the leak scanner in
verify.py, and the scanner is worth more than the shorter encoding.

Nothing is applied. The page writes text files; the customer reviews and applies them.
"""
import json
import re
import textwrap
from datetime import date

from .model import POLICY_UNDER, POV_CR_TYPES
from .render import CSS, JS, JS_NSROW, JS_RESIZE, e, fname, risk_text
from .util import human_list, long_date, plural, slug, usd

API = "perfectscale.io/v1"

# PerfectScale's own default for workloadMinWasteUSDPerMonth: automation skips a workload whose
# monthly waste is below it. A ranked row under it (one that ranks on risk, not savings) gets no
# change from a cost-only config, so it starts unticked; the risk is fixed on its own.
AUTOMATION_MIN_USD = 5

# Every generated line fits in WIDTH columns, yamllint's default line-length, so the files pass a
# stock yamllint/pre-commit gate in a customer's GitOps pipeline. The only lines that can exceed it
# are ones that carry a Kubernetes name longer than the room left for it.
WIDTH = 80


def _cm(ind, text):
    """A comment paragraph wrapped to WIDTH at this indent."""
    return "".join(" " * ind + "# " + x + "\n"
                   for x in textwrap.wrap(text, WIDTH - ind - 2, break_long_words=False, break_on_hyphens=False))


def _kv(ind, text, comment=None):
    """One YAML line. Its comment goes inline (two spaces before '#', per YAML convention) when the
    whole line fits in WIDTH, otherwise on the line(s) directly above it."""
    line = " " * ind + text
    if not comment:
        return line + "\n"
    if len(line) + 4 + len(comment) <= WIDTH:
        return f"{line}  # {comment}\n"
    return _cm(ind, comment) + line + "\n"


# The one line the user asked to carry a review comment: the full note wrapped directly above the
# key, plus a short inline marker that stays on the line itself so it is seen in a diff too.
KEEP_LIMIT_NOTE = ("REVIEW: keepLimit is false so PerfectScale can remove the CPU limit; a right-sized "
                   "CPU request is then not throttled by a leftover limit. Set it to true if a "
                   "ResourceQuota on limits.cpu, a LimitRange, or an internal policy requires every "
                   "container to keep a CPU limit.")
KEEP_LIMIT_LINE = "keepLimit: false  # REVIEW: see the note above before applying"

# Per-kind cautions, taken from PerfectScale's own docs and example files for that kind.
TYPE_NOTE = {
    "StatefulSet": ("StatefulSet: agree who watches restarts of production databases first - pods with "
                    "persistent volumes are more fragile - and if it runs on dedicated nodes, smaller "
                    "requests will not lower the node bill."),
    "DaemonSet": "DaemonSet: automated decrease-only - requests go down, never up.",
    "CronJob": "CronJob: running pods are not restarted; the next run picks up the change.",
    "Job": "Job: running pods are not restarted; only new pods pick up the change.",
}


def q(s):
    """A YAML double-quoted scalar. JSON string syntax is valid YAML, and quoting every name keeps
    a namespace such as "123" or "true" from being read back as a number or a boolean."""
    return json.dumps(str(s), ensure_ascii=False)


def cr_name(r):
    """metadata.name for a workload's config: kind-name, as a DNS-1123 subdomain. The kind is in the
    name because a Deployment and a StatefulSet can share a name in one namespace."""
    n = re.sub(r"[^a-z0-9.-]+", "-", f"{r['type']}-{r['name']}".lower())
    return re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", n[:253]) or "workload"


# PerfectScale's documented default wasteMaxAutomationFrequency per kind ("Automation configuration
# defaults", docs.perfectscale.io): StatefulSets and DaemonSets are resized far less often.
WASTE_FREQ = {"Deployment": "30m", "Rollout": "30m", "CronJob": "30m", "Job": "30m", "DaemonSet": "4h", "StatefulSet": "24h"}
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _operational(kind, ind):
    """The full `operational` body of PerfectScale's namespace/workload CR templates, every field
    explicit at its documented default except the cost-optimization choices (increases off) and
    keepLimit: false. Field placement is checked against the chart's CRD schemas
    (charts/psc-autoscaler/crds). Deprecated fields (maintenanceWindowIgnoredForResiliency,
    maxAutomationFrequency) are left out, as the docs direct."""
    rows = [
        (0, 'automationMode: "Enabled"', "automate this scope"),
        (0, 'automationStrategy: "inPlace"', 'resize pods in place; "evictMutate" recreates them'),
        (0, 'podResizePendingStrategy: "rollingRestart"',
         'when an in-place resize cannot fit: rolling restart, or "deleteRecreatePendingPod"'),
        (0, "duringRolloutInProgress: pause", "hold new recommendations while concurrent ReplicaSets exist"),
        (0, "timeConstraints:", None),
        (2, f'wasteMaxAutomationFrequency: "{WASTE_FREQ.get(kind, "30m")}"', "apply decreases at most this often"),
        (2, 'fixResiliencyMaxAutomationFrequency: "30m"', "apply increases at most this often"),
        (2, "maintenanceWindow:", "when automation may act; narrow to the customer's change window if required"),
        *(x for d in DAYS for x in ((4, f"{d}:", None), (6, '- "00:00-23:59"', None))),
        (2, "maintenanceWindowIgnoredMinResiliencyLevel: None", "lowest risk level allowed to act outside the window"),
        (0, "restrictions:", None),
        (2, "workloadMinWasteUSDPerMonth: 5", "act only when monthly waste exceeds $5"),
        (2, "cpuManagement:", None),
        (4, "request:", None),
        (6, "increaseEnabled: false", "cost optimization: never raise CPU requests"),
        (6, "decreaseEnabled: true", "lower CPU requests to remove waste"),
        (6, "minimumCores: -1", "floor for a recommendation; -1 = none"),
        (6, "maximumCores: -1", "ceiling for a recommendation; -1 = none"),
        (6, "cpuRunawayDetection:", None),
        (8, "maxCpuRequestIncreaseIterations:", None),
        (10, "daily: 3", "at most 3 CPU request increases per day"),
        (10, "weekly: 6", "at most 6 per week"),
        (4, "limit:", None),
        (6, None, None),                                   # keepLimit, with its review note
        (2, "memoryManagement:", None),
        (4, "request:", None),
        (6, "increaseEnabled: false", "cost optimization: never raise memory requests"),
        (6, "decreaseEnabled: true", "lower memory requests to remove waste"),
        (6, "minimumGiB: -1", "floor for a recommendation; -1 = none"),
        (6, "maximumGiB: -1", "ceiling for a recommendation; -1 = none"),
        (4, "limit:", None),
        (6, "increaseEnabled: false", "leave memory limits as they are"),
        (6, "decreaseEnabled: false", None),
        (6, "minimumGiB: -1", None),
        (6, "maximumGiB: -1", None),
        (6, "memoryLeakDetection:", None),
        (8, "maxMemoryIncreaseIterations:", None),
        (10, "daily: 3", "at most 3 memory limit increases per day"),
        (10, "weekly: 6", "at most 6 per week"),
    ]
    out = []
    for rel, text, comment in rows:
        if text is None:
            out.append(_cm(ind + rel, KEEP_LIMIT_NOTE) + " " * (ind + rel) + KEEP_LIMIT_LINE + "\n")
        else:
            out.append(_kv(ind + rel, text, comment))
    return "".join(out)


def ns_type_block(t):
    """One workloadTypes entry of a NamespaceAutomationConfig. Identical for every namespace, so the
    page embeds it once per kind and the browser appends the ones a namespace needs."""
    return ((_cm(6, TYPE_NOTE[t]) if t in TYPE_NOTE else "")
            + f"      {t}:\n        operational:\n" + _operational(t, 10))


NS_HEAD = (f"apiVersion: {API}\nkind: NamespaceAutomationConfig\nmetadata:\n"
           "  name: namespace-automation-config\n  namespace: __NS__\nspec:\n  automation:\n    workloadTypes:\n")


def namespace_doc(ns, types):
    return NS_HEAD.replace("__NS__", q(ns)) + "".join(ns_type_block(t) for t in types)


def workload_doc(r):
    t = r["type"]
    return ((_cm(0, TYPE_NOTE[t]) if t in TYPE_NOTE else "")
            + f"apiVersion: {API}\nkind: WorkloadAutomationConfig\nmetadata:\n"
            f"  name: {q(cr_name(r))}\n  namespace: {q(r['ns'])}\n"
            f"spec:\n  targetRef:\n    kind: {t}\n    name: {q(r['name'])}\n"
            "  automation:\n    operational:\n" + _operational(t, 6))


def cluster_doc():
    types = "".join(f"      {t}:\n        operational:\n          automationMode: \"Disabled\"\n" for t in POV_CR_TYPES)
    return (f"---\napiVersion: {API}\nkind: ClusterAutomationConfig\nmetadata:\n  name: cluster-automation-config\n"
            "spec:\n  automation:\n    operational:\n"
            + _kv(6, "stopAllAutomation: false", "global kill switch: true pauses all automation")
            + _kv(6, "cleanupAllAutomation: false", "true stops automation and reverts every change it made")
            + _cm(4, "Disabled at cluster scope on purpose: namespace settings override cluster settings and "
                     "workload settings override both, so automation runs only where the namespace and workload "
                     "files enable it - the POV scope, and nothing outside it.")
            + "    workloadTypes:\n" + types)


def _para(*texts):
    return "".join(_cm(0, t) for t in texts)


def file_heads(cluster, prepared):
    """Comment headers, wrapped to WIDTH. Values that vary per file (__NSNAME__, __TYPES__, __NEX__,
    __WLKIND__, __WLNAME__, __WLNS__, __WLRANK__) sit alone on short labelled lines, so filling them in
    (here in bundle()/bundle_ns(), and in the browser by JS_BUILD/JS_BUILD_NS) cannot push wrapped prose
    past WIDTH."""
    gen = f"# Cluster: {cluster}\n# From the POV automation kickoff page prepared {prepared}.\n"
    tail = ("# Nothing has been applied. Review, then apply with:\n#   kubectl apply -f <this file>\n"
            + _para("After applying, PerfectScale shows the merged result per workload (zoom-in, Automation "
                 "Config CR, mergedConfig) - check one workload there before widening the scope."))
    cost = _para("Cost-optimization profile: requests can only go down. Raising a request to fix a resiliency "
                 "risk is not enabled here; turn increaseEnabled on once the POV cost baseline is agreed, so "
                 "the savings and the reliability work are measured separately.")
    return {
        "cluster": ("# PerfectScale automation - cluster prerequisite\n" + gen
                    + _para("Required once per cluster, and only one is allowed per cluster. Skip this file if "
                            "the cluster already has one (kubectl get clusterautomationconfig): applying it over "
                            "an existing config with the same name replaces that config's settings.")
                    + tail),
        "nsfile": ("# PerfectScale automation - namespace scope, cost optimization\n"
                   "# Namespace: __NSNAME__\n# Enables: __TYPES__\n# Kept out: __NEX__\n" + gen
                   + _para("The config enables those kinds for every workload of them in this namespace, ranked "
                           "or not. Each kept-out workload has its own config below with automationMode "
                           "\"Disabled\" and the reason; workload settings override namespace settings.",
                           "A config of the same name from the workload files replaces an exclusion here, so "
                           "apply the namespace files first and the workload files after.")
                   + cost + tail),
        "wlfile": ("# PerfectScale automation - workload scope, cost optimization\n"
                   "# Workload: __WLNAME__\n# Kind: __WLKIND__\n# Namespace: __WLNS__\n"
                   "# Rank in the table: __WLRANK__\n" + gen
                   + _para("A workload config overrides any namespace config for this workload, so this file "
                           "alone automates only this workload. With namespace configs applied as well, apply "
                           "this file after them: it replaces a namespace exclusion of the same name.")
                   + cost + tail),
        # Added after a file's header when the reviewer ticked "HPA pinned" for its namespace on the page.
        "hpa_ns": ("# HPA min = max: confirmed on the kickoff page\n# HPA workloads now covered: __NHPA__\n"
                   + _para("The reviewer confirmed these HPAs are pinned; PerfectScale data cannot show it, as the "
                           "public API carries no HPA min/max. If one can scale, a smaller request moves its "
                           "scaling point: untick its confirmation and generate again.")),
        "hpa_wl": ("# HPA min = max: confirmed on the kickoff page\n"
                   + _para("The reviewer confirmed this workload's HPA is pinned; PerfectScale data cannot show it. "
                           "If it can scale, a smaller request moves its scaling point.")),
    }


def wl_file(r):
    """One file per workload: namespace first so a folder listing groups by owner."""
    return f"workloads/{r['ns']}-{cr_name(r)}-workload-automation-config.yaml"


def bundle(cluster, prepared, rows, selected, ticked=None, hpa_ok=()):
    """Workload scope: the cluster prerequisite, then one file per ranked workload that is both of a
    selected controller type and ticked (ticked = set of ranks; None = every row), in rank order.
    hpa_ok = namespaces whose pod CPU/memory HPAs the reviewer confirmed pinned on the page: their HPA
    workloads' files record that. [(filename, n_docs, text)]. JS_BUILD reproduces this exactly."""
    on, ok = set(selected), set(hpa_ok)
    rs = [r for r in rows if r["type"] in on and (ticked is None or r["rank"] in ticked)]
    heads = file_heads(cluster, prepared)
    out = [(f"{slug(cluster)}-cluster-automation-config.yaml", 1, heads["cluster"] + cluster_doc())]
    for r in rs:
        head = (heads["wlfile"].replace("__WLKIND__", r["type"]).replace("__WLNAME__", r["name"])
                .replace("__WLNS__", r["ns"]).replace("__WLRANK__", str(r["rank"])))
        if r.get("hpa_class") == "pod_resource" and r["ns"] in ok:
            head += heads["hpa_wl"]
        out.append((wl_file(r), 1, head + "---\n" + workload_doc(r)))
    return out


def pinned_line(names):
    """The tenant-config entry for namespaces confirmed pinned on the page, so the next plan run moves
    their savings out of the HPA action and into the automation waves. Kubernetes namespace names are
    DNS labels, so only a dot (never legal, but cheap to guard) needs escaping."""
    pats = ", ".join(json.dumps("^" + n.replace(".", "\\.") + "$") for n in names)
    return f'"hpa_pinned_namespaces": [{pats}]'


EXCL_REASON = {
    "held_out": "held out of automation in this tenant's configuration",
    "stateful": "stateful workload - automate it only with its owner and an agreed restart window",
    "jvm": "JVM workload - the heap flag has to move with the memory request",
    "hpa": "pod-level CPU/memory HPA - a smaller request moves its scaling point",
}


def exclusion_doc(ns, x):
    """A Disabled WorkloadAutomationConfig keeping one workload out of its namespace's config. Same
    metadata.name as workload_doc() would give it, so a later explicit choice in the workload file
    replaces the exclusion rather than conflicting with it."""
    return (_cm(0, f"Kept out of namespace automation: {EXCL_REASON[x['reason']]}.")
            + f"apiVersion: {API}\nkind: WorkloadAutomationConfig\nmetadata:\n"
            f"  name: {q(cr_name(x))}\n  namespace: {q(ns)}\n"
            f"spec:\n  targetRef:\n    kind: {x['type']}\n    name: {q(x['name'])}\n"
            "  automation:\n    operational:\n      automationMode: \"Disabled\"\n")


def hpa_confirmed(n, hpa_ok):
    """The reviewer ticked "HPA pinned" for this namespace and it has HPA-held workloads to release."""
    return n["ns"] in set(hpa_ok) and bool(n.get("hpa_auto"))


def ns_auto(n, hpa_ok=()):
    """`auto` per kind, plus the HPA-held workloads when their HPAs are confirmed pinned."""
    if not hpa_confirmed(n, hpa_ok):
        return n["auto"]
    out = {t: dict(v) for t, v in n["auto"].items()}
    for t, v in n["hpa_auto"].items():
        a = out.setdefault(t, {"n": 0, "pot": 0.0, "risk": 0, "waste": 0.0})
        for k in ("n", "pot", "risk", "waste"):
            a[k] += v[k]
    return out


def ns_enabled(n, selected, hpa_ok=()):
    """The kinds a namespace's config enables: selected AND automatable in that namespace, so a
    namespace with no StatefulSets never gets StatefulSet automation switched on for later."""
    au = ns_auto(n, hpa_ok)
    return [t for t in POV_CR_TYPES if t in set(selected) and t in au]


def bundle_ns(cluster, prepared, namespaces, checked, selected, hpa_ok=()):
    """Namespace scope: the cluster prerequisite, then one file per checked namespace holding its
    NamespaceAutomationConfig and a Disabled config for each workload kept out of it. Platform-
    managed namespaces are never written, and a namespace with none of the selected kinds is
    skipped. A fully held namespace is written only once its HPAs are confirmed pinned (hpa_ok), and
    a confirmed namespace's HPA workloads ride along instead of being kept out. JS_BUILD_NS
    reproduces this exactly."""
    ck = set(checked)
    heads = file_heads(cluster, prepared)
    out = [(f"{slug(cluster)}-cluster-automation-config.yaml", 1, heads["cluster"] + cluster_doc())]
    for n in namespaces:
        if n["ns"] not in ck or n["managed"]:
            continue
        conf = hpa_confirmed(n, hpa_ok)
        if n.get("held") and not conf:
            continue
        en = ns_enabled(n, selected, hpa_ok)
        if not en:
            continue
        ex = [x for x in n["excl"] if x["type"] in en and not (conf and x["reason"] == "hpa")]
        nh = sum(1 for x in n["excl"] if conf and x["reason"] == "hpa" and x["type"] in en)
        head = (heads["nsfile"].replace("__NSNAME__", n["ns"]).replace("__TYPES__", ", ".join(en))
                .replace("__NEX__", str(len(ex))))
        if nh:
            head += heads["hpa_ns"].replace("__NHPA__", str(nh))
        text = head + "---\n" + namespace_doc(n["ns"], en) + "".join("---\n" + exclusion_doc(n["ns"], x) for x in ex)
        out.append((f"namespaces/{n['ns']}-namespace-automation-config.yaml", 1 + len(ex), text))
    return out


LIB = "skill/perfectscale-optimization-plan/scripts"   # where new_tenant.py init puts the library in a tenant folder


def rerun_commands(cfg):
    """The commands an SE runs from the tenant folder, with this tenant's config path filled in -
    relative, so the page carries no local filesystem path."""
    src = getattr(cfg, "src", None)
    try:
        rel = src.relative_to(cfg.root).as_posix() if src else None
    except ValueError:
        rel = None
    rel = rel or f"config/{slug(cfg['customer'])}.tenant.json"
    return {"pin": f"python3 {LIB}/run_plan.py --config {rel} --pin-hpa",
            "helper": f"python3 {LIB}/kickoff.py --config {rel}"}


def page_data(cluster, prepared, rows, namespaces=(), rerun=None):
    """Everything JS_BUILD/JS_BUILD_NS need, every text piece as a list of lines (see the module
    docstring). Platform-managed namespaces are left out: they cannot be generated."""
    L = lambda s: s.split("\n")  # noqa: E731
    return {
        "rerun": rerun or {"pin": f"python3 {LIB}/run_plan.py --config config/<tenant>.tenant.json --pin-hpa",
                           "helper": f"python3 {LIB}/kickoff.py --config config/<tenant>.tenant.json"},
        "slug": slug(cluster), "types": list(POV_CR_TYPES),
        "head": {k: L(v) for k, v in file_heads(cluster, prepared).items()},
        "cluster_doc": L(cluster_doc()), "ns_head": L(NS_HEAD),
        "type_block": {t: L(ns_type_block(t)) for t in POV_CR_TYPES},
        "rows": [{"rank": r["rank"], "ns": r["ns"], "name": r["name"], "type": r["type"], "file": wl_file(r),
                  "hpa_hold": r.get("hpa_class") == "pod_resource", "wl_doc": L(workload_doc(r))} for r in rows],
        # A fully held namespace is included only when its HPAs could be confirmed pinned on the page.
        "namespaces": [{"ns": n["ns"], "ns_q": q(n["ns"]), "auto": n["auto"], "hpa_auto": n.get("hpa_auto") or {},
                        "held": bool(n.get("held")), "held_pot": n.get("held_pot") or 0.0,
                        "excl": [{"type": x["type"], "hpa": x["reason"] == "hpa", "doc": L(exclusion_doc(n["ns"], x))} for x in n["excl"]]}
                       for n in namespaces if not n["managed"] and (not n.get("held") or n.get("hpa_auto"))],
    }


# Pure: no DOM, no globals beyond their arguments. JS_BUILD mirrors bundle() and JS_BUILD_NS mirrors
# bundle_ns() line for line; tests run both under node and compare the output byte-for-byte.
JS_BUILD = """function psBuild(D,sel,ticked,hpaok){
var J=function(a){return a.join('\\n')};
var on={};sel.forEach(function(t){on[t]=1});
var ok={};(hpaok||[]).forEach(function(n){ok[n]=1});
var tk=null;if(ticked){tk={};ticked.forEach(function(k){tk[k]=1})}
var out=[{name:D.slug+'-cluster-automation-config.yaml',docs:1,text:J(D.head.cluster)+J(D.cluster_doc)}];
D.rows.forEach(function(r){if(!on[r.type]||(tk&&!tk[r.rank]))return;
var head=J(D.head.wlfile).split('__WLKIND__').join(r.type).split('__WLNAME__').join(r.name).split('__WLNS__').join(r.ns).split('__WLRANK__').join(String(r.rank));
if(r.hpa_hold&&ok[r.ns])head+=J(D.head.hpa_wl);
out.push({name:r.file,docs:1,text:head+'---\\n'+J(r.wl_doc)})});
return out}"""

JS_BUILD_NS = """function psConf(n,ok){return !!ok[n.ns]&&Object.keys(n.hpa_auto||{}).length>0}
function psAuto(n,ok){if(!psConf(n,ok))return n.auto;var o={};
Object.keys(n.auto).forEach(function(t){var a=n.auto[t];o[t]={n:a.n,pot:a.pot,risk:a.risk,waste:a.waste}});
Object.keys(n.hpa_auto).forEach(function(t){var v=n.hpa_auto[t],a=o[t]||(o[t]={n:0,pot:0,risk:0,waste:0});a.n+=v.n;a.pot+=v.pot;a.risk+=v.risk;a.waste+=v.waste});
return o}
function psEnabled(D,n,sel,ok){var on={};sel.forEach(function(t){on[t]=1});var au=psAuto(n,ok||{});
return D.types.filter(function(t){return on[t]&&au[t]})}
function psPinnedLine(names){return '"hpa_pinned_namespaces": ['+names.map(function(n){return JSON.stringify('^'+n.split('.').join('\\\\.')+'$')}).join(', ')+']'}
function psBuildNs(D,checked,sel,hpaok){
var J=function(a){return a.join('\\n')};
var ck={};checked.forEach(function(n){ck[n]=1});
var ok={};(hpaok||[]).forEach(function(n){ok[n]=1});
var out=[{name:D.slug+'-cluster-automation-config.yaml',docs:1,text:J(D.head.cluster)+J(D.cluster_doc)}];
D.namespaces.forEach(function(n){if(!ck[n.ns])return;
var conf=psConf(n,ok);if(n.held&&!conf)return;
var en=psEnabled(D,n,sel,ok);if(!en.length)return;
var ex=n.excl.filter(function(x){return en.indexOf(x.type)>=0&&!(conf&&x.hpa)});
var nh=n.excl.filter(function(x){return conf&&x.hpa&&en.indexOf(x.type)>=0}).length;
var head=J(D.head.nsfile).split('__NSNAME__').join(n.ns).split('__TYPES__').join(en.join(', ')).split('__NEX__').join(String(ex.length));
if(nh)head+=J(D.head.hpa_ns).split('__NHPA__').join(String(nh));
var text=head+'---\\n'+J(D.ns_head).split('__NS__').join(n.ns_q)+en.map(function(t){return J(D.type_block[t])}).join('')
+ex.map(function(x){return '---\\n'+J(x.doc)}).join('');
out.push({name:'namespaces/'+n.ns+'-namespace-automation-config.yaml',docs:1+ex.length,text:text})});
return out}"""

# Store-only ZIP writer (no compression, so no library): local headers, central directory, end
# record. Enough for three small text files to arrive as one download.
JS_ZIP = """function psZip(files){
var T=psZip.t;if(!T){T=psZip.t=[];for(var n=0;n<256;n++){var c=n;for(var k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;T[n]=c>>>0}}
function crc(u){var c=0xFFFFFFFF;for(var i=0;i<u.length;i++)c=T[(c^u[i])&255]^(c>>>8);return(c^0xFFFFFFFF)>>>0}
var enc=new TextEncoder(),parts=[],cd=[],off=0,cdl=0;
files.forEach(function(f){var nm=enc.encode(f.name),d=enc.encode(f.text),cr=crc(d);
var h=new DataView(new ArrayBuffer(30));h.setUint32(0,0x04034b50,true);h.setUint16(4,20,true);h.setUint16(6,0x0800,true);
h.setUint16(12,0x21,true);h.setUint32(14,cr,true);h.setUint32(18,d.length,true);h.setUint32(22,d.length,true);h.setUint16(26,nm.length,true);
parts.push(h.buffer,nm,d);
var c=new DataView(new ArrayBuffer(46));c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x0800,true);
c.setUint16(14,0x21,true);c.setUint32(16,cr,true);c.setUint32(20,d.length,true);c.setUint32(24,d.length,true);c.setUint16(28,nm.length,true);c.setUint32(42,off,true);
cd.push(c.buffer,nm);cdl+=46+nm.length;off+=30+nm.length+d.length});
var z=new DataView(new ArrayBuffer(22));z.setUint32(0,0x06054b50,true);z.setUint16(8,files.length,true);z.setUint16(10,files.length,true);z.setUint32(12,cdl,true);z.setUint32(16,off,true);
return new Blob(parts.concat(cd,[z.buffer]),{type:'application/zip'})}"""

JS_UI = """(function(){
var D=JSON.parse(document.getElementById('povdata').textContent);
var box=document.getElementById('typemenu'),btn=document.getElementById('typebtn');
if(!box)return;
var NS={};D.namespaces.forEach(function(n){NS[n.ns]=n});
var cbs=[].slice.call(box.querySelectorAll('input[type=checkbox]'));
// Managed rows are never selectable; a held row's checkbox is enabled only while its HPAs are confirmed.
var nck=[].slice.call(document.querySelectorAll('#povns tr:not([data-managed]) input.nsck'));
var hck=[].slice.call(document.querySelectorAll('#povns input.hpack'));
var wck=[].slice.call(document.querySelectorAll('table.povgrp input.wlck'));
document.querySelectorAll('#povns tr[data-held]').forEach(function(tr){tr.dataset.why=tr.cells[3].textContent});
function vis(c){var tr=c.closest('tr');return !tr.hidden&&!tr.classList.contains('off')}
function sel(){return cbs.filter(function(c){return c.checked}).map(function(c){return c.value})}
function hpaok(){return hck.filter(function(c){return c.checked}).map(function(c){return c.value})}
function okmap(){var o={};hpaok().forEach(function(n){o[n]=1});return o}
// Only what is on screen counts: a row hidden by the controller-type filter keeps its tick for when
// the type comes back, but is never generated and never counted.
function ticked(){return nck.filter(function(c){return c.checked&&!c.disabled&&vis(c)}).map(function(c){return c.value})}
function wticked(){return wck.filter(function(c){return c.checked&&vis(c)}).map(function(c){return +c.value})}
function m(v){return '$'+Math.round(v).toLocaleString('en-US')}
function pl(n,w){return n+' '+w+(n===1?'':'s')}
var last=null,inview={};
function key(kind){return (kind==='wl'?'wl|'+sel().join(',')+'|'+wticked().join(','):'ns|'+sel().join(',')+'|'+ticked().join(','))+'|hpa:'+hpaok().join(',')}
function stale(){var o=document.getElementById('out');if(last&&!o.hidden)document.getElementById('stale').hidden=key(last)===o.dataset.key}
function apply(){
 var s=sel(),on={};s.forEach(function(t){on[t]=1});
 var shown=0,n=0,pot=0,risk=0,nns={},G={};inview={};
 document.querySelectorAll('table.povgrp tbody tr[data-type]').forEach(function(tr){
  var show=!!on[tr.dataset.type],g=tr.closest('table').dataset.g,a=G[g]||(G[g]={n:0,pot:0,t:0});
  tr.classList.toggle('off',!show);
  var det=tr.parentNode.querySelector('tr.nsdet[data-for="'+tr.dataset.ns+'"]');if(det)det.classList.toggle('off',!show);
  if(!show)return;shown++;a.n++;a.pot+=+tr.dataset.pot;
  if(tr.querySelector('input.wlck').checked){a.t++;n++;pot+=+tr.dataset.pot;if(+tr.dataset.sev>=3)risk++;nns[tr.dataset.n]=1;inview[tr.dataset.n]=(inview[tr.dataset.n]||0)+1}});
 document.querySelectorAll('.gsum').forEach(function(el){var a=G[el.dataset.g]||{n:0,pot:0,t:0};
  el.textContent=a.n?(pl(a.n,'workload')+' \u00b7 '+m(a.pot)+'/mo potential savings \u00b7 '+a.t+' ticked'):'None of the selected controller types'});
 var k=Object.keys(nns).length;
 document.getElementById('typesum').textContent=s.length===cbs.length?'All ('+s.length+')':(s.length?s.join(', '):'None');
 document.getElementById('scope').textContent=shown?(n+' of '+pl(shown,'workload')+' ticked across '+pl(k,'namespace')+' \\u00b7 '+m(pot)+'/mo potential savings \\u00b7 '+risk+' at High or Critical risk'):'No ranked workloads match. Pick at least one controller type.';
 document.getElementById('genwl').disabled=!n;
 var nShown=0,nGen=0,npot=0,nex=0,ok=okmap();
 document.querySelectorAll('#povns tbody tr').forEach(function(tr){
  if(tr.dataset.managed){tr.hidden=!tr.dataset.types.split(',').some(function(t){return on[t]});return}
  var x=NS[tr.dataset.n];if(!x)return;
  var conf=psConf(x,ok),nc=tr.querySelector('input.nsck'),c=tr.cells;
  if(x.held&&!conf){
   nc.disabled=true;nc.checked=false;tr.classList.add('held');
   tr.hidden=!tr.dataset.types.split(',').some(function(t){return on[t]});
   c[2].textContent='0';c[3].textContent=tr.dataset.why;c[4].textContent=m(x.held_pot)+'/mo on hold';c[5].textContent='-';
   c[6].textContent=x.excl.length;c[7].textContent='-';return}
  nc.disabled=false;tr.classList.remove('held');
  var au=psAuto(x,ok),en=psEnabled(D,x,s,ok);tr.hidden=!en.length;if(!en.length)return;nShown++;
  var w=0,p=0,r=0;en.forEach(function(t){var a=au[t];w+=a.n;p+=a.pot;r+=a.risk});
  var ex=x.excl.filter(function(y){return en.indexOf(y.type)>=0&&!(conf&&y.hpa)}).length,iv=inview[x.ns]||0;
  c[2].textContent=w;c[2].dataset.v=w;c[3].textContent=en.join(', ');
  c[4].textContent=m(p)+'/mo';c[4].dataset.v=p;c[5].textContent=r||'-';c[5].dataset.v=r;
  c[6].textContent=ex||'-';c[6].dataset.v=ex;c[7].textContent=iv||'-';c[7].dataset.v=iv;
  if(tr.querySelector('input.nsck').checked){nGen++;npot+=p;nex+=ex}});
 document.getElementById('nsscope').textContent=nShown?(nGen?(nGen+' of '+pl(nShown,'namespace')+' ticked \\u00b7 '+pl(nGen,'file')+' to write \\u00b7 '+m(npot)+'/mo potential savings covered \\u00b7 '+pl(nex,'workload')+' kept out'):'No namespace ticked.'):'No namespace runs the selected controller types.';
 document.getElementById('genns').disabled=!nGen;
 var hl=hpaok(),hb=document.getElementById('hpabar');
 if(hb){hb.hidden=!hl.length;
  document.getElementById('hpaline').textContent=hl.length?('HPA confirmed pinned in '+pl(hl.length,'namespace')+' ('+hl.join(', ')+
   '). The files you generate here already reflect it. To also count it in the plan and executive summary, update the plan:'):'';
  document.getElementById('hpacmd').textContent=hl.length?(D.rerun.pin+' '+hl.join(' ')):''}
 stale()}
cbs.concat(nck,wck).forEach(function(c){c.addEventListener('change',apply)});
// Confirming a namespace's HPAs pinned is the go-ahead for its held workloads: the namespace is ticked
// for generation and its Hold rows are ticked in the ranked table. Unticking takes both back.
hck.forEach(function(h){h.addEventListener('change',function(){
 var ns=h.value,on=h.checked,nc=h.closest('tr').querySelector('input.nsck');
 if(on){nc.disabled=false;nc.checked=true}else if(NS[ns]&&NS[ns].held){nc.checked=false}
 wck.forEach(function(c){var r=c.closest('tr');if(r.dataset.hold&&r.dataset.n===ns){c.checked=on;r.classList.toggle('hpaok',on)}});
 apply()})});
var hc=document.getElementById('hpacopy');if(hc)hc.addEventListener('click',function(){copy(D.rerun.pin+' '+hpaok().join(' '),hc)});
var hh=document.getElementById('hpahelper');if(hh)hh.textContent=D.rerun.helper;
// Opened through kickoff.py (the local helper injects PS_HELPER): one click saves the confirmations,
// renders the next version and opens it. Without the helper the command above does the same.
var H=window.PS_HELPER,run=document.getElementById('hparun');
if(H&&run){
 document.querySelector('.hparunp').hidden=false;document.querySelector('.hpacmdp').hidden=true;document.querySelector('.hpahint').hidden=true;
 run.addEventListener('click',function(){
  var ns=hpaok(),st=document.getElementById('hpastatus');if(!ns.length)return;
  run.disabled=true;st.textContent='Updating the plan, executive summary and kickoff pages...';
  function fail(msg){run.disabled=false;st.textContent='Update failed: '+msg}
  fetch(H.api+'pin-hpa',{method:'POST',headers:{'Content-Type':'application/json','X-PS-Token':H.token},
   body:JSON.stringify({namespaces:ns,page:location.pathname})})
  .then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j})})
  .then(function(j){(function poll(){
   fetch(H.api+'job/'+j.job,{headers:{'X-PS-Token':H.token}}).then(function(r){return r.json()}).then(function(s){
    if(s.state==='running'){st.textContent='Updating... '+(s.step||'');setTimeout(poll,1500);return}
    if(s.state==='done'){st.textContent='Done: '+s.version+' is ready. Opening it...';location.href=s.url;return}
    fail(s.error||'see the terminal running kickoff.py')},function(){fail('lost contact with kickoff.py - is it still running?')})})()})
  .catch(function(e){fail(e.message)})})}
function all(list,v){list.forEach(function(c){if((vis(c)&&!c.disabled)||!v)c.checked=v});apply()}
document.getElementById('tall').addEventListener('click',function(){cbs.forEach(function(c){c.checked=true});apply()});
document.getElementById('tnone').addEventListener('click',function(){cbs.forEach(function(c){c.checked=false});apply()});
document.querySelectorAll('button.gsel').forEach(function(b){b.addEventListener('click',function(){
 var t=document.querySelector('table.povgrp[data-g="'+b.dataset.g+'"]');
 all([].slice.call(t.querySelectorAll('input.wlck')),b.dataset.v==='1')})});
document.getElementById('nsall').addEventListener('click',function(){all(nck,true)});
document.getElementById('nsnone').addEventListener('click',function(){all(nck,false)});
document.getElementById('nsmatch').addEventListener('click',function(){nck.forEach(function(c){c.checked=!c.disabled&&!!inview[c.value]});apply()});
btn.addEventListener('click',function(ev){ev.stopPropagation();var o=box.hidden;box.hidden=!o;btn.setAttribute('aria-expanded',o?'true':'false')});
document.addEventListener('click',function(ev){if(!box.hidden&&!box.contains(ev.target)&&ev.target!==btn){box.hidden=true;btn.setAttribute('aria-expanded','false')}});
document.addEventListener('keydown',function(ev){if(ev.key==='Escape'&&!box.hidden){box.hidden=true;btn.setAttribute('aria-expanded','false');btn.focus()}});
function save(name,blob){var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name.split('/').pop();document.body.appendChild(a);a.click();
 setTimeout(function(){URL.revokeObjectURL(a.href);a.remove()},1000)}
function copy(text,b){function done(){var t=b.textContent;b.textContent='Copied';setTimeout(function(){b.textContent=t},1200)}
 if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(text).then(done,fallback)}else fallback();
 function fallback(){var ta=document.createElement('textarea');ta.value=text;document.body.appendChild(ta);ta.select();try{document.execCommand('copy');done()}catch(e){}ta.remove()}}
var files=[],zipname='';
function show(kind,title,what,zn){
 var list=document.getElementById('files');list.textContent='';
 files.forEach(function(f){var d=document.createElement('div');d.className='file';
  var h=document.createElement('div');h.className='fh';
  var b=document.createElement('b');b.textContent=f.name;h.appendChild(b);
  var c=document.createElement('span');c.className='mut';c.textContent=' '+pl(f.docs,'document')+' ';h.appendChild(c);
  var dl=document.createElement('button');dl.type='button';dl.textContent='Download';dl.addEventListener('click',function(){save(f.name,new Blob([f.text],{type:'application/x-yaml'}))});
  var cp=document.createElement('button');cp.type='button';cp.textContent='Copy';cp.addEventListener('click',function(){copy(f.text,cp)});
  h.appendChild(dl);h.appendChild(cp);d.appendChild(h);
  var det=document.createElement('details'),sm=document.createElement('summary');sm.textContent='Preview';det.appendChild(sm);
  var pre=document.createElement('pre');pre.textContent=f.text;det.appendChild(pre);d.appendChild(det);list.appendChild(d)});
 last=kind;zipname=zn;
 var out=document.getElementById('out');out.hidden=false;out.dataset.key=key(kind);document.getElementById('stale').hidden=true;
 document.getElementById('outtitle').textContent=title;document.getElementById('outsel').textContent=what;
 out.scrollIntoView({behavior:'smooth',block:'start'})}
function hnote(){var h=hpaok();return h.length?' HPA confirmed pinned: '+h.join(', ')+'.':''}
document.getElementById('genwl').addEventListener('click',function(){var s=sel();files=psBuild(D,s,wticked(),hpaok());if(files.length<2)return;
 show('wl','Generated workload CRDs','Controller types: '+s.join(', ')+'. '+pl(files.length-1,'ticked workload')+', one file each.'+hnote(),D.slug+'-workload-automation-crds.zip')});
document.getElementById('genns').addEventListener('click',function(){var s=sel();files=psBuildNs(D,ticked(),s,hpaok());if(files.length<2)return;
 show('ns','Generated namespace CRDs','Controller types: '+s.join(', ')+'. '+pl(files.length-1,'ticked namespace')+', one file each.'+hnote(),D.slug+'-namespace-automation-crds.zip')});
document.getElementById('zip').addEventListener('click',function(){if(files.length)save(zipname,psZip(files))});
apply()})()"""

POV_CSS = """.pd{position:relative;display:inline-block}
.ctl .cfg{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:4px 10px;cursor:pointer}
.ctl .cfg:hover,.ctl .cfg[aria-expanded=true]{border-color:var(--acc);color:var(--acc)}
.go{font:inherit;font-weight:600;color:#fff;background:var(--acc);border:1px solid var(--acc);border-radius:6px;padding:4px 12px;cursor:pointer;margin-left:8px}
.go:disabled{opacity:.45;cursor:not-allowed}
.ctl #scope,#nsscope{margin-left:10px}
.nsbar{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:10px 0}
.nsbar button:not(.go){font:inherit;font-size:13px;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:2px 9px;cursor:pointer}
.nsbar button:not(.go):hover{border-color:var(--acc);color:var(--acc)}
#povns tr.mgd td{color:var(--mut)}
#povns tr.held td{color:var(--mut)}
#povns tr.held td:last-child{color:var(--fg)}
#povns .hpalb{white-space:nowrap;cursor:pointer;font-size:13px}
.hpabar{font-size:13px;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 10px;margin:0 0 10px;word-break:break-all}
.hpabar p{margin:4px 0}
.hpabar code{font-size:12px;background:var(--bg,#f6f7f9);padding:1px 4px;border-radius:4px}
.hpabar button:not(.go){font:inherit;font-size:13px;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:2px 9px;cursor:pointer;margin-left:6px}
.hpabar .go{margin-left:0}
table.povgrp tr.hpaok td.wnote::before{content:"HPA confirmed pinned. ";color:var(--fg);font-weight:600}
/* HPA metric names are single tokens up to ~80 characters (org_eclipse_jetty_..._utilization). Unbroken,
   the browser sizes the whole column to the longest one and squeezes the rest; breaking anywhere lets it
   collapse to a sliver. So: break only after underscores (<wbr>), and keep a readable minimum width. */
td.hpa{min-width:170px}
details.wgrp{border:0;border-top:1px solid var(--line);border-radius:0;margin:14px 0 0;padding:8px 0 0}
details.wgrp>summary{list-style-position:outside;font-weight:400;padding:2px 0}
details.wgrp .gt{font-weight:600;font-size:16px}
details.wgrp .gsub{font-size:13px}
details.wgrp .gsum{display:block;font-size:13px;margin:2px 0 0 0}
.gbar{display:flex;gap:8px;margin:8px 0 4px}
.gbar button{font:inherit;font-size:12px;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:2px 9px;cursor:pointer}
.gbar button:hover{border-color:var(--acc);color:var(--acc)}
table.povgrp td:first-child,table.povgrp th:first-child{width:28px}
table.povgrp .wsub{font-size:12px;color:var(--mut)}
table.povgrp button.nstog{font-weight:600}
table.povgrp td.wnote{font-size:13px;color:var(--mut)}
table.povgrp tr.nsdet>td{font-size:13px;color:var(--mut);background:var(--card)}
tr.off{display:none}
#files{max-height:70vh;overflow:auto}
.menu .cnt{color:var(--mut);margin-left:auto;padding-left:10px}
tr[hidden]{display:none}
.file{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:10px 0}
.file .fh{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.file button,#zip{font:inherit;font-size:13px;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:6px;padding:2px 9px;cursor:pointer}
.file button:hover,#zip:hover{border-color:var(--acc);color:var(--acc)}
.file details{border:0;padding:0;margin:6px 0 0}
pre{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px;overflow:auto;max-height:420px;font-size:12px;line-height:1.45}
.warn{border-left:3px solid var(--acc);padding:4px 10px;margin:8px 0}
ol.steps li{margin:4px 0}"""


WAVE_ORDER = {"1": 1, "1b": 2, "Review": 3, "Hold": 4}
WAVE_TEXT = {"1": "Wave 1", "1b": "Wave 1b", "Review": "Review", "Hold": "Hold"}


def wave_label(r):
    return WAVE_TEXT.get(r.get("wave") or "1", "Wave 1")


def hpa_text(r):
    c = r.get("hpa_class")
    if c is None:
        return "Unknown"
    if c == "none":
        return "None"
    return r.get("hpa_detail") or c


def default_ticked(r):
    """Pre-ticked only when automation can move it safely now: Wave 1 or 1b, and enough waste to
    clear PerfectScale's automation minimum. Hold and Review rows start unticked."""
    return (r.get("wave") or "1") in ("1", "1b") and r["waste"] >= AUTOMATION_MIN_USD


def _wave_legend(f, pv):
    rows = pv.get("rows") or []
    c = {k: sum(1 for r in rows if (r.get("wave") or "1") == k) for k in WAVE_ORDER}
    src = ("HPA triggers come from PerfectScale's public API v2. It carries no HPA min/max replicas, so an HPA "
           "pinned at fixed replicas also shows as Hold; once the owner confirms min = max, tick HPA pinned for its "
           "namespace under Namespace scope and its Hold rows join the scope."
           if (f.get("api_v2") or {}).get("provided") else
           "HPA data was not collected for this run, so every row's HPA reads Unknown and nothing is held for its "
           "HPA. Store API credentials (scripts/set_api_credentials.sh) and re-collect to sort rows by HPA.")
    return ("<p class=mut><b>Waves:</b> "
            f"Wave 1 ({c['1']}) - no HPA, or an HPA triggered on the container itself. "
            f"Wave 1b ({c['1b']}) - stateful or mesh components; give them extra headroom. "
            f"Review ({c['Review']}) - HPA on custom metrics only, or automation not supported. "
            f"Hold ({c['Hold']}) - HPA on pod CPU/memory: a smaller request moves its scaling point. "
            "Review and Hold start collapsed and unticked. " + e(src) + "</p>")


GROUPS = (("ready", "Ready to automate", "Wave 1 and 1b: no HPA, a container-level trigger, or extra headroom", ("1", "1b"), True),
          ("review", "Review", "HPA on custom metrics only, or automation not supported", ("Review",), False),
          ("hold", "Hold", "HPA on pod CPU/memory: a smaller request moves its scaling point", ("Hold",), False))


def hpa_short(r):
    """One line for the table: the first trigger plus a count; the full list is in the detail row."""
    c = r.get("hpa_class")
    if c in (None, "none"):
        return hpa_text(r)
    parts = (r.get("hpa_detail") or c).split(", ")
    return parts[0] + (f" +{len(parts) - 1} more" if len(parts) > 1 else "")


def table_notes(r):
    """review_notes minus what the group header already says, so a Hold row does not repeat 'Hold'."""
    return [n for n in review_notes(r) if not n.startswith(("Hold:", "Review:"))]


def _detail_text(r):
    bits = [f"Rank {r['rank']}, score {r['score']:.0f}", f"Cost {usd(r['cost'])}/mo",
            f"Resiliency spend {usd(r['res_likely'])}/mo (likely)" if r["res_likely"] else "No resiliency spend",
            f"Top issue: {r['issue']}",
            f"CPU {r['cpu_cur']:.2f} to {r['cpu_rec']:.2f} cores, memory {r['mem_cur_gib']:.1f} to {r['mem_rec_gib']:.1f} GiB (all replicas)",
            f"Replicas avg {r['replicas']:g}" + (f", max {r['replicas_max']:g}" if r.get("replicas_max") is not None else "")]
    if r.get("hpa_class") not in (None, "none"):
        bits.append(f"HPA triggers: {r.get('hpa_detail') or r['hpa_class']}")
    bits += [n for n in review_notes(r) if n.startswith(("Hold:", "Review:"))]
    return " · ".join(bits)


def _wave_groups(rows):
    """One collapsible section per group, each a small sortable table. Workload names toggle a detail
    row (the plan's namespace-appendix mechanism, so sorting keeps a detail row under its workload)."""
    P = []
    for key, title, sub, waves, opened in GROUPS:
        rs = [r for r in rows if (r.get("wave") or "1") in waves]
        if not rs:
            continue
        P.append(f"<details class=wgrp data-g={key}{' open' if opened else ''}><summary><span class=gt>{e(title)}</span> "
                 f"<span class='mut gsub'>{e(sub)}</span> <span class='mut gsum' data-g={key}></span></summary>"
                 f"<div class=gbar><button type=button class=gsel data-g={key} data-v=1>Select all</button>"
                 f"<button type=button class=gsel data-g={key} data-v=0>Clear</button></div>"
                 f"<div class=wrap><table class='sort povgrp nrsz' data-g={key}><thead><tr><th aria-label='In scope'></th>"
                 "<th>Workload</th><th>Savings/mo</th><th>Risk</th><th>HPA</th><th>Note</th></tr></thead><tbody>")
        for r in rs:
            k = f"r{r['rank']}"
            notes = table_notes(r)
            hold = " data-hold=1" if r.get("hpa_class") == "pod_resource" else ""
            P.append(f"<tr data-type='{e(r['type'])}' data-ns='{k}' data-n='{e(r['ns'])}' data-pot='{r['pot']:.2f}' data-sev='{r['risk_sev']}'{hold}>"
                     f"<td><input type=checkbox class=wlck value='{r['rank']}'{' checked' if default_ticked(r) else ''} aria-label='Include {e(r['name'])}'></td>"
                     f"<td data-v='{e(r['name'])}'><button type=button class=nstog aria-expanded=false>{e(r['name'])}</button>"
                     f"<div class=wsub>{e(r['ns'])} · {e(r['type'])}</div></td>"
                     f"<td class=num data-v='{r['pot']:.2f}'>{usd(r['pot'])}</td>"
                     f"<td data-v='{r['risk_sev']}'>{e(risk_text(r['risk'])) if r['risk_sev'] else '-'}</td>"
                     f"<td class=hpa>{e(hpa_short(r)).replace('_', '_<wbr>')}</td>"
                     f"<td class=wnote>{e('; '.join(notes)) if notes else ''}</td></tr>"
                     f"<tr class=nsdet data-for='{k}' hidden><td></td><td colspan=5>{e(_detail_text(r)).replace('_', '_<wbr>')}</td></tr>")
        P.append("</tbody></table></div></details>")
    return "".join(P)


def review_notes(r):
    """What to agree with the workload's owner before it is automated - the things a cost-only CR
    does not handle on its own. Short labels: the waves legend above the table carries the full
    reasoning, and a long note in a narrow column makes every row tall. Shared with the XLSX sheet."""
    n = []
    if r["type"] == "StatefulSet" or r["lane"] == "stateful":
        n.append("Stateful: agree a restart window")
    if r["jvm"]:
        n.append("JVM: move the heap flag with memory")
    if (r["policy"] or "").lower() in POLICY_UNDER and r["risk_sev"] >= 3:
        n.append("MaxSavings policy on an at-risk workload")
    if r.get("hpa_class") == "pinned":
        n.append("HPA pinned (min = max, confirmed): a resize cannot make it scale")
    elif r.get("hpa_class") == "pod_resource":
        n.append("Hold: pod CPU/memory HPA")
        if r.get("replicas_max") is not None and r["replicas_max"] <= r["replicas"]:
            # Constant replicas is a hint, not proof: an HPA that simply never crossed its target looks
            # the same, and a smaller request could make it scale. Only min == max makes it safe.
            n.append(f"replicas held at {r['replicas']:g} - may be pinned; check HPA min/max")
    elif r.get("hpa_class") == "custom":
        n.append("Review: custom-metric HPA - confirm with owner")
    elif r.get("hpa"):
        n.append("HPA: check the trigger first")
    if r.get("auto_status") == "NotSupported":
        n.append("Automation not supported")
    if r.get("immature"):
        n.append("Recommendation not mature yet")
    if r.get("wave") == "1b" and r["type"] != "StatefulSet" and r["lane"] != "stateful":
        n.append("Mesh component: extra headroom")
    if r["type"] == "DaemonSet":
        n.append("Decrease-only")
    if r["waste"] < AUTOMATION_MIN_USD:
        n.append(f"Under ${AUTOMATION_MIN_USD}/mo waste: automation skips it; fix the risk separately")
    return n


def _held_lines(pv):
    h = pv.get("held") or {}
    out = []
    mg = h.get("managed")
    if mg and mg["n"]:
        out.append(f"{plural(mg['n'], 'workload')} in platform-managed namespaces (kube-system and similar), "
                   f"{usd(mg['pot'])}/mo of potential savings. PerfectScale does not automate kube-system by default, "
                   "and a platform add-on is reconciled by the platform itself; these stay out of the POV scope.")
    ho = h.get("held_out")
    if ho and ho["n"]:
        out.append(f"{plural(ho['n'], 'workload')} held out of automation in this tenant's configuration, "
                   f"{usd(ho['pot'])}/mo of potential savings.")
    hh = pv.get("hpa_held") or {}
    if hh.get("n"):
        names = hh.get("namespaces") or []
        shown = human_list(names[:4] + ([f"{len(names) - 4} more"] if len(names) > 4 else []))
        out.append(f"{plural(hh['n'], 'workload')} on hold because an HPA scales them on pod CPU or memory, "
                   f"{usd(hh['pot'])}/mo of potential savings, in {plural(len(names), 'namespace')}: {shown}. "
                   "A smaller request moves the HPA's scaling point. They stay in the ranked table, unticked, and out of "
                   "namespace configs. If the owner confirms an HPA is pinned (minReplicas equals maxReplicas), list its "
                   "namespace under hpa_pinned_namespaces in the tenant config and regenerate this page.")
    ut = h.get("unsupported_type")
    if ut and ut["n"]:
        kinds = human_list([f"{k} ({v:,})" for k, v in (pv.get("held_types") or {}).items()][:6])
        out.append(f"{plural(ut['n'], 'workload')} of kinds an automation CR cannot target directly - {kinds} - "
                   f"{usd(ut['pot'])}/mo of potential savings. Group them with a custom grouping rule first; "
                   "automation then targets the grouped type.")
    return out


def render_pov_html(cfg, f):
    pv = f.get("pov") or {"rows": [], "top_n": cfg["pov_top_n"], "savings_weight": cfg["pov_savings_weight"], "pool_n": 0}
    rows = pv["rows"]
    cluster = f["name"]
    prepared = cfg["prepared"]
    sw = round(pv["savings_weight"] * 100)
    title = f"{cluster}: POV automation kickoff"
    P = [f"<main><h1>{e(title)}</h1>"
         f"<p class=mut>{e(cfg['customer'])}. Prepared {e(long_date(date.fromisoformat(prepared)))} by {e(cfg['author'])}. "
         f"Data window: {f['window_days']:.0f} days.</p>"]
    if f.get("early"):
        P.append(f"<p class=warn>Early baseline: under {cfg['min_history_days']} days of data. Rankings and savings are provisional; "
                 "re-run before the scope is signed off.</p>")
    P.append("<p>This page sets the scope of the PerfectScale POV on this cluster, from the day automation is switched on to the "
             f"technical win. The table ranks the {len(rows)} workloads with the most to gain from automation by one score, "
             f"0 to 100: {sw}% potential savings, relative to the largest in this cluster, and {100 - sw}% risk severity "
             "(Low, Medium, High, Critical). Pick the controller types in scope, then generate PerfectScale automation "
             "configs two independent ways: <b>workload CRDs</b> for the ranked workloads in the filtered table, or "
             "<b>namespace CRDs</b> for the namespaces you tick below, one file per namespace.</p>")
    namespaces = pv.get("namespaces") or []
    if not rows:
        P.append("<p><b>No workload in this cluster has potential savings or an open risk that automation can act on.</b> "
                 "There is nothing to scope here yet.</p>")
    else:
        counts = {}
        for r in rows:
            counts[r["type"]] = counts.get(r["type"], 0) + 1
        # Kinds in the table, plus kinds only a namespace config would reach (a DaemonSet that never
        # ranked): leaving those out of the pull-down would make them impossible to enable.
        present = [t for t in POV_CR_TYPES if t in counts or any(t in n["auto"] for n in namespaces if not n["managed"])]
        menu = "".join(f"<label><input type=checkbox value='{e(t)}' checked>{e(t)}<span class=cnt>{counts.get(t, 0)} in table</span></label>"
                       for t in present)
        P.append("<div class=ctl><span class=pd><button type=button id=typebtn class=cfg aria-haspopup=true aria-expanded=false>"
                 f"Controller types: <span id=typesum>All ({len(present)})</span> &#9662;</button>"
                 f"<div id=typemenu class=menu hidden><p class=ttl>Controller types in scope</p>{menu}"
                 "<div class=acts><button type=button id=tall>Select all</button><button type=button id=tnone>Clear</button></div></div></span>"
                 "<button type=button id=genwl class=go>Generate workload CRDs</button><span id=scope class=mut></span></div>")
        P.append(f"<h2>Top {len(rows)} workloads by cost and resiliency</h2>"
                 "<p class=mut>Grouped by what automation can do now. Untick anything the customer does not want automated; "
                 "only ticked rows of the selected controller types get a file, one per workload. Click a workload for its "
                 "details. Savings and resiliency spend are monthly run-rates and are never added together.</p>"
                 + _wave_legend(f, pv))
        P.append(_wave_groups(rows))
        P.append(_ns_section(pv, namespaces, {r["ns"] for r in rows}))
        P.append("<section id=out hidden><h2 id=outtitle>Generated CRDs</h2>"
                 "<p><span id=outsel></span> <span id=stale class=warn hidden>The selection has changed since these were "
                 "generated - generate again.</span></p>"
                 "<ol class=steps><li>Confirm the PerfectScale automation agent is installed on the cluster.</li>"
                 "<li>Apply the cluster prerequisite only if the cluster has no ClusterAutomationConfig yet.</li>"
                 "<li>Namespace files automate whole namespaces, each with its kept-out workloads disabled; workload files "
                 "automate only the ticked workloads, one file each. Using both: apply the namespace files first, then the "
                 "workload files, so a workload ticked in the table replaces its namespace exclusion.</li>"
                 "<li>Every <code>keepLimit: false</code> line carries a REVIEW comment: confirm no ResourceQuota, LimitRange or "
                 "policy requires a CPU limit before applying.</li></ol>"
                 "<div id=files></div><p><button type=button id=zip>Download all (.zip)</button></p></section>")
    held = _held_lines(pv)
    if held:
        P.append("<h2>Kept out of this table</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in held) + "</ul>")
    P.append("<p class=fn>Read-only for the cluster: this page was generated from PerfectScale data and applies nothing. Opened "
             "with kickoff.py, its update button regenerates these reports and nothing else. The configs follow the "
             "cost-optimization profile of PerfectScale's published automation examples, with keepLimit set to false for review.</p></main>")
    data = json.dumps(page_data(cluster, prepared, rows, namespaces, rerun=rerun_commands(cfg)), ensure_ascii=False).replace("</", "<\\/")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}\n{POV_CSS}</style></head><body>{''.join(P)}"
            f"<script type=application/json id=povdata>{data}</script>"
            f"<script>{JS};{JS_RESIZE};{JS_NSROW};{JS_BUILD};{JS_BUILD_NS};{JS_ZIP};{JS_UI}</script></body></html>")


HELD_REASON = {"hpa": "pod CPU/memory HPA", "stateful": "stateful", "jvm": "JVM heap",
               "held_out": "held out in the tenant config"}


def _ns_section(pv, namespaces, in_table):
    """The namespace picker: every namespace with something a namespace config can automate, ticked
    by default when the ranked table touches it; platform-managed namespaces listed last, greyed
    out and never selectable. Figures are for all controller types here and are recomputed in the
    browser for the selected ones."""
    P = ["<h2>Namespace scope</h2><p class=mut>Tick the namespaces to automate as a whole. Each gets its own file with "
         "one NamespaceAutomationConfig enabling the selected controller types it runs, covering every workload of those "
         "types, ranked or not. Workloads that should not ride along - held out in this tenant's configuration, stateful, "
         "JVM, or behind an HPA that scales on pod CPU or memory - get a Disabled workload config in the same file (Kept out). "
         "A namespace where every workload is kept out is listed greyed with the reason, not selectable. Only namespaces "
         f"that run one of the selected controller types are shown. Ticked by default: the namespaces the ranked table "
         f"touches with at least ${AUTOMATION_MIN_USD}/mo of waste to act on.</p>"
         "<p class=mut><b>HPA pinned (min = max):</b> tick it once the namespace owner confirms its pod CPU/memory HPAs have "
         "minReplicas equal to maxReplicas. The namespace becomes selectable with those workloads covered instead of kept "
         "out, its Hold rows in the ranked table are ticked, and every generated file records the confirmation. Untick to "
         "undo. To count them in the plan and executive summary as well, use the update button (or command) that appears.</p>"
         "<div class=nsbar><button type=button id=nsall>Select all</button><button type=button id=nsnone>Clear</button>"
         "<button type=button id=nsmatch>Match the table</button>"
         "<button type=button id=genns class=go>Generate namespace CRDs</button><span id=nsscope class=mut></span></div>"
         "<div id=hpabar class=hpabar hidden><p id=hpaline></p>"
         "<p class=hparunp hidden><button type=button id=hparun class=go>Update the plan with these confirmations</button> "
         "<span id=hpastatus class=mut></span></p>"
         "<p class=hpacmdp>Or run this in a terminal, in the tenant folder: <code id=hpacmd></code> "
         "<button type=button id=hpacopy>Copy command</button></p>"
         "<p class='hpahint mut'>For a one-click update button instead, open this page with "
         "<code id=hpahelper></code></p></div>"
         "<div class=wrap><table id=povns class=sort><thead><tr><th>In scope</th><th>Namespace</th><th>Workloads covered</th>"
         "<th>Controller types enabled</th><th>Potential savings</th><th>At High or Critical risk</th><th>Kept out</th>"
         "<th>Ticked in ranked table</th><th>HPA pinned (min = max)</th></tr></thead><tbody>"]

    def hpa_cell(n, k):
        if not n.get("hpa_auto"):
            return "<td>-</td>"
        nh = sum(v["n"] for v in n["hpa_auto"].values())
        return (f"<td><label class=hpalb><input type=checkbox class=hpack value='{k}' "
                f"aria-label='HPAs in {k} confirmed pinned (min = max)'> {plural(nh, 'workload')}</label></td>")
    for n in namespaces:
        k = e(n["ns"])
        if n["managed"]:
            P.append(f"<tr class=mgd data-managed=1 data-types='{e(','.join(n['types']))}' data-n='{k}'><td><input type=checkbox class=nsck value='{k}' disabled "
                     f"aria-label='{k} (platform-managed, not selectable)'></td><td>{k}</td><td>-</td>"
                     "<td>Platform-managed: not automated</td><td>-</td><td>-</td><td>-</td><td>-</td><td>-</td></tr>")
            continue
        if n.get("held"):
            nex = len(n["excl"])
            why = "; ".join(f"{HELD_REASON.get(k, k)} ({v})" if len(n["held_reasons"]) > 1 else HELD_REASON.get(k, k)
                            for k, v in n["held_reasons"].items())
            # Held with nothing an HPA confirmation could release: the greyed managed path (shown while one
            # of its kinds is selected, never recomputed, never generated). Held behind HPAs: a live row the
            # page script enables when "HPA pinned" is ticked.
            attrs = (f"class=held data-held=1 data-types='{e(','.join(n['types']))}'" if n.get("hpa_auto")
                     else f"class=mgd data-managed=1 data-types='{e(','.join(n['types']))}'")
            P.append(f"<tr {attrs} data-n='{k}'><td><input type=checkbox class=nsck value='{k}' disabled "
                     f"aria-label='{k} (every workload kept out, not selectable)'></td><td>{k}</td><td class=num data-v='0'>0</td>"
                     f"<td>Held: {e(why)} on every workload</td><td class=num data-v='{n['held_pot']:.2f}'>{usd(n['held_pot'])}/mo on hold</td>"
                     f"<td>-</td><td class=num data-v='{nex}'>{nex}</td><td class=num>-</td>{hpa_cell(n, k)}</tr>")
            continue
        w = sum(t["n"] for t in n["auto"].values()); r = sum(t["risk"] for t in n["auto"].values())
        tick = n["ns"] in in_table and sum(t.get("waste", 0.0) for t in n["auto"].values()) >= AUTOMATION_MIN_USD
        P.append(f"<tr data-n='{k}'><td><input type=checkbox class=nsck value='{k}'{' checked' if tick else ''} "
                 f"aria-label='Include {k}'></td><td>{k}</td>"
                 f"<td class=num data-v='{w}'>{w}</td><td>{e(', '.join(t for t in POV_CR_TYPES if t in n['auto']))}</td>"
                 f"<td class=num data-v='{n['pot']:.2f}'>{usd(n['pot'])}/mo</td><td class=num data-v='{r}'>{r or '-'}</td>"
                 f"<td class=num data-v='{len(n['excl'])}'>{len(n['excl']) or '-'}</td><td class=num>-</td>{hpa_cell(n, k)}</tr>")
    P.append("</tbody></table></div>")
    if pv.get("ns_nothing_to_automate"):
        P.append(f"<p class=fn>{plural(pv['ns_nothing_to_automate'], 'other namespace')} not listed: everything in them is "
                 "of a kind a namespace config cannot enable.</p>")
    return "".join(P)


def pov_fname(cfg, f):
    return fname(cfg, f"{f['name']}-pov-automation-kickoff", "html")


POV_COLS = ["cluster", "rank", "wave", "workload", "namespace", "controller_type", "hpa", "monthly_cost",
            "potential_savings", "risk", "top_issue", "resiliency_likely", "score", "review_before_automating"]


def pov_sheet_rows(facts):
    for f in facts:
        for r in (f.get("pov") or {}).get("rows", []):
            yield [f["name"], r["rank"], wave_label(r), r["name"], r["ns"], r["type"], hpa_text(r), round(r["cost"], 2), round(r["pot"], 2),
                   r["risk"], r["issue"], round(r["res_likely"], 2), r["score"], "; ".join(review_notes(r))]
