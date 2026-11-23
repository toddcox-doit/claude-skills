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
from .render import CSS, JS, JS_COLLAPSE, JS_NSROW, JS_RESIZE, e, fname, risk_text
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
            + "    workloadTypes:\n" + types
            + "    hpa:\n      operational:\n        automationMode: \"Disabled\"\n")


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
        # Added after a file's header when it covers workloads whose HPA scales on pod CPU or memory.
        "hpa_on": ("# HPA automation: Enabled for __NHPA__\n"
                   + _para("The prerequisites were confirmed on the kickoff page: PerfectScale automation agent "
                           "v1.0.71 or later and exporter v1.1.28 or later. HPA automation keeps minReplicas and "
                           "the CPU and memory triggers aligned as requests change, acts only on workloads "
                           "running a revision PerfectScale automation created, and never changes maxReplicas "
                           "or scaling behaviour.")),
        "hpa_off": ("# HPA automation: not included for __NHPA__\n"
                    + _para("Tick the agent and exporter prerequisites in the kickoff page's HPA automation "
                            "section to include it. Until then, review these HPAs' triggers after the new "
                            "requests roll out: PerfectScale sizes the request from the trigger, but nothing "
                            "keeps minReplicas or the triggers aligned.")),
    }


def wl_file(r):
    """One file per workload: namespace first so a folder listing groups by owner."""
    return f"workloads/{r['ns']}-{cr_name(r)}-workload-automation-config.yaml"


HPA_DEFAULTS = {"min_floor": 2, "min_max": None, "cpu_min": 80, "cpu_max": None, "mem_min": 80, "mem_max": None}


def hpa_block(opts, ind=4):
    """The hpa block of an Automation CR, at spec.automation level (4 spaces in both the namespace and
    the workload docs). Ranges follow PerfectScale's HPA automation docs: each group has enabled/min
    and an optional max. JS_HPA builds the same text in the browser from the page's inputs."""
    sp = " " * ind

    def rng(name, mn, mx, cm):
        return (f"{sp}      {name}:\n{sp}        enabled: true\n{sp}        min: {mn}  # {cm}\n"
                + (f"{sp}        max: {mx}\n" if mx else ""))
    return (f"{sp}hpa:\n{sp}  operational:\n{sp}    automationMode: \"Enabled\"  # PerfectScale HPA automation\n"
            f"{sp}    restrictions:\n"
            + rng("minReplicas", opts["min_floor"], opts.get("min_max"), "lowest minReplicas PerfectScale writes")
            + rng("cpuTrigger", opts["cpu_min"], opts.get("cpu_max"), "lowest CPU trigger (%) it writes")
            + rng("memoryTrigger", opts["mem_min"], opts.get("mem_max"), "lowest memory trigger (%) it writes"))


def _hpa_args(hpa):
    """hpa = {"on": [namespaces with HPA automation ticked], "opts": restriction values, or None while the
    prerequisites are not confirmed}. None (the default) leaves files without any HPA note."""
    if not hpa:
        return set(), None, False
    return set(hpa.get("on") or ()), hpa.get("opts"), True


def bundle(cluster, prepared, rows, selected, ticked=None, hpa=None):
    """Workload scope: the cluster prerequisite, then one file per ranked workload that is both of a
    selected controller type and ticked (ticked = set of ranks; None = every row), in rank order.
    A workload whose HPA scales on pod CPU/memory, in a namespace with HPA automation ticked, gets
    the hpa block once the prerequisites are confirmed, and a note either way. [(filename, n_docs,
    text)]. JS_BUILD reproduces this exactly from the pieces in the page."""
    on = set(selected)
    hon, opts, noted = _hpa_args(hpa)
    rs = [r for r in rows if r["type"] in on and (ticked is None or r["rank"] in ticked)]
    heads = file_heads(cluster, prepared)
    out = [(f"{slug(cluster)}-cluster-automation-config.yaml", 1, heads["cluster"] + cluster_doc())]
    for r in rs:
        head = (heads["wlfile"].replace("__WLKIND__", r["type"]).replace("__WLNAME__", r["name"])
                .replace("__WLNS__", r["ns"]).replace("__WLRANK__", str(r["rank"])))
        doc = workload_doc(r)
        if noted and r.get("hpa_class") == "pod_resource" and r["ns"] in hon:
            head += heads["hpa_on" if opts else "hpa_off"].replace("__NHPA__", "this workload")
            if opts:
                doc += hpa_block(opts)
        out.append((wl_file(r), 1, head + "---\n" + doc))
    return out


EXCL_REASON = {
    "held_out": "held out of automation in this tenant's configuration",
    "stateful": "stateful workload - automate it only with its owner and an agreed restart window",
    "jvm": "JVM workload - the heap flag has to move with the memory request",
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


def ns_enabled(n, selected):
    """The kinds a namespace's config enables: selected AND automatable in that namespace, so a
    namespace with no StatefulSets never gets StatefulSet automation switched on for later."""
    return [t for t in POV_CR_TYPES if t in set(selected) and t in n["auto"]]


def bundle_ns(cluster, prepared, namespaces, checked, selected, hpa=None):
    """Namespace scope: the cluster prerequisite, then one file per checked namespace holding its
    NamespaceAutomationConfig and a Disabled config for each workload kept out of it. Platform-
    managed and fully kept-out namespaces are never written, and a namespace with none of the
    selected kinds is skipped. A namespace with workloads whose HPA scales on pod CPU/memory and HPA
    automation ticked gets the hpa block in the same config - one NamespaceAutomationConfig per
    namespace, so it cannot be a second file - once the prerequisites are confirmed. JS_BUILD_NS
    reproduces this exactly."""
    ck = set(checked)
    hon, opts, noted = _hpa_args(hpa)
    heads = file_heads(cluster, prepared)
    out = [(f"{slug(cluster)}-cluster-automation-config.yaml", 1, heads["cluster"] + cluster_doc())]
    for n in namespaces:
        if n["ns"] not in ck or n["managed"] or n.get("held"):
            continue
        en = ns_enabled(n, selected)
        if not en:
            continue
        ex = [x for x in n["excl"] if x["type"] in en]
        head = (heads["nsfile"].replace("__NSNAME__", n["ns"]).replace("__TYPES__", ", ".join(en))
                .replace("__NEX__", str(len(ex))))
        doc = namespace_doc(n["ns"], en)
        nh = (n.get("hpa") or {}).get("n", 0)
        if noted and nh and n["ns"] in hon:
            head += heads["hpa_on" if opts else "hpa_off"].replace("__NHPA__", plural(nh, "workload") + " with a pod CPU/memory HPA")
            if opts:
                doc += hpa_block(opts)
        text = head + "---\n" + doc + "".join("---\n" + exclusion_doc(n["ns"], x) for x in ex)
        out.append((f"namespaces/{n['ns']}-namespace-automation-config.yaml", 1 + len(ex), text))
    return out


def page_data(cluster, prepared, rows, namespaces=()):
    """Everything JS_BUILD/JS_BUILD_NS need, every text piece as a list of lines (see the module
    docstring). Platform-managed and fully kept-out namespaces are left out: they cannot be generated."""
    L = lambda s: s.split("\n")  # noqa: E731
    return {
        "slug": slug(cluster), "types": list(POV_CR_TYPES),
        "head": {k: L(v) for k, v in file_heads(cluster, prepared).items()},
        "cluster_doc": L(cluster_doc()), "ns_head": L(NS_HEAD),
        "type_block": {t: L(ns_type_block(t)) for t in POV_CR_TYPES},
        "hpa_defaults": HPA_DEFAULTS,
        "rows": [{"rank": r["rank"], "ns": r["ns"], "name": r["name"], "type": r["type"], "file": wl_file(r),
                  "hpa": r.get("hpa_class") == "pod_resource", "wl_doc": L(workload_doc(r))} for r in rows],
        "namespaces": [{"ns": n["ns"], "ns_q": q(n["ns"]), "auto": n["auto"], "hpa_n": (n.get("hpa") or {}).get("n", 0),
                        "excl": [{"type": x["type"], "doc": L(exclusion_doc(n["ns"], x))} for x in n["excl"]]}
                       for n in namespaces if not n["managed"] and not n.get("held")],
    }


# Pure: no DOM, no globals beyond their arguments. JS_BUILD mirrors bundle() and JS_BUILD_NS mirrors
# bundle_ns() line for line; tests run both under node and compare the output byte-for-byte.
# Lines are built as a list and joined, never written as a key followed by an escaped newline: that
# pattern reads as a Windows drive path to verify.py's leak scanner (see the module docstring).
JS_HPA = """function psHpa(o){var sp='    ',L=[sp+'hpa',sp+'  operational',sp+'    automationMode: "Enabled"  # PerfectScale HPA automation',sp+'    restrictions'];
L[0]+=':';L[1]+=':';L[3]+=':';
function rng(nm,mn,mx,cm){L.push(sp+'      '+nm+':');L.push(sp+'        enabled: true');L.push(sp+'        min: '+mn+'  # '+cm);if(mx)L.push(sp+'        max: '+mx)}
rng('minReplicas',o.min_floor,o.min_max,'lowest minReplicas PerfectScale writes');
rng('cpuTrigger',o.cpu_min,o.cpu_max,'lowest CPU trigger (%) it writes');
rng('memoryTrigger',o.mem_min,o.mem_max,'lowest memory trigger (%) it writes');
return L.join(String.fromCharCode(10))+String.fromCharCode(10)}
function psPl(n,w){return n+' '+w+(n===1?'':'s')}"""

JS_BUILD = """function psBuild(D,sel,ticked,hpa){
var J=function(a){return a.join('\\n')};
var on={};sel.forEach(function(t){on[t]=1});
var hon={};((hpa&&hpa.on)||[]).forEach(function(n){hon[n]=1});var opts=hpa?hpa.opts:null;
var tk=null;if(ticked){tk={};ticked.forEach(function(k){tk[k]=1})}
var out=[{name:D.slug+'-cluster-automation-config.yaml',docs:1,text:J(D.head.cluster)+J(D.cluster_doc)}];
D.rows.forEach(function(r){if(!on[r.type]||(tk&&!tk[r.rank]))return;
var head=J(D.head.wlfile).split('__WLKIND__').join(r.type).split('__WLNAME__').join(r.name).split('__WLNS__').join(r.ns).split('__WLRANK__').join(String(r.rank));
var doc=J(r.wl_doc);
if(hpa&&r.hpa&&hon[r.ns]){head+=J(D.head[opts?'hpa_on':'hpa_off']).split('__NHPA__').join('this workload');if(opts)doc+=psHpa(opts)}
out.push({name:r.file,docs:1,text:head+'---\\n'+doc})});
return out}"""

JS_BUILD_NS = """function psEnabled(D,n,sel){var on={};sel.forEach(function(t){on[t]=1});
return D.types.filter(function(t){return on[t]&&n.auto[t]})}
function psBuildNs(D,checked,sel,hpa){
var J=function(a){return a.join('\\n')};
var ck={};checked.forEach(function(n){ck[n]=1});
var hon={};((hpa&&hpa.on)||[]).forEach(function(n){hon[n]=1});var opts=hpa?hpa.opts:null;
var out=[{name:D.slug+'-cluster-automation-config.yaml',docs:1,text:J(D.head.cluster)+J(D.cluster_doc)}];
D.namespaces.forEach(function(n){if(!ck[n.ns])return;
var en=psEnabled(D,n,sel);if(!en.length)return;
var ex=n.excl.filter(function(x){return en.indexOf(x.type)>=0});
var head=J(D.head.nsfile).split('__NSNAME__').join(n.ns).split('__TYPES__').join(en.join(', ')).split('__NEX__').join(String(ex.length));
var doc=J(D.ns_head).split('__NS__').join(n.ns_q)+en.map(function(t){return J(D.type_block[t])}).join('');
if(hpa&&n.hpa_n&&hon[n.ns]){head+=J(D.head[opts?'hpa_on':'hpa_off']).split('__NHPA__').join(psPl(n.hpa_n,'workload')+' with a pod CPU/memory HPA');if(opts)doc+=psHpa(opts)}
var text=head+'---\\n'+doc+ex.map(function(x){return '---\\n'+J(x.doc)}).join('');
out.push({name:'namespaces/'+n.ns+'-namespace-automation-config.yaml',docs:1+ex.length,text:text})});
return out}"""

# Pure line diff for CRD history: an LCS walk with two lines of context around each change, and a
# file-level comparison of two sets. Newlines come from String.fromCharCode(10) so no escaped newline
# ever follows a key in the page (verify.py's leak scanner reads that as a drive path).
JS_DIFF = """function psDiff(a,b){var NL=String.fromCharCode(10),A=a.split(NL),B=b.split(NL),n=A.length,m=B.length,i,j;
if(n*m>4000000)return [['!','too large to diff here ('+n+' and '+m+' lines)']];
var L=[];for(i=0;i<=n;i++)L.push(new Uint32Array(m+1));
for(i=n-1;i>=0;i--)for(j=m-1;j>=0;j--)L[i][j]=A[i]===B[j]?L[i+1][j+1]+1:Math.max(L[i+1][j],L[i][j+1]);
var out=[];i=0;j=0;
while(i<n&&j<m){if(A[i]===B[j]){out.push([' ',A[i]]);i++;j++}else if(L[i+1][j]>=L[i][j+1]){out.push(['-',A[i]]);i++}else{out.push(['+',B[j]]);j++}}
while(i<n)out.push(['-',A[i++]]);while(j<m)out.push(['+',B[j++]]);
var keep=out.map(function(){return false});
out.forEach(function(x,k){if(x[0]!==' ')for(var d=-2;d<=2;d++)if(out[k+d])keep[k+d]=true});
var res=[],gap=0;out.forEach(function(x,k){if(keep[k]){if(gap){res.push(['~',gap+' unchanged lines']);gap=0}res.push(x)}else gap++});
if(gap)res.push(['~',gap+' unchanged lines']);return res}
function psFileDiff(oldF,newF){var o={},n={},r={added:[],removed:[],changed:[]};
oldF.forEach(function(f){o[f.name]=f.text});newF.forEach(function(f){n[f.name]=f.text});
Object.keys(n).sort().forEach(function(k){if(!(k in o))r.added.push(k);else if(o[k]!==n[k])r.changed.push(k)});
Object.keys(o).sort().forEach(function(k){if(!(k in n))r.removed.push(k)});r.o=o;r.n=n;return r}"""

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
// Managed and fully kept-out rows are never selectable.
var nck=[].slice.call(document.querySelectorAll('#povns tr:not([data-managed]) input.nsck'));
var hon=[].slice.call(document.querySelectorAll('#povns input.hpaon'));
var wck=[].slice.call(document.querySelectorAll('table.povgrp input.wlck'));
function vis(c){var tr=c.closest('tr');return !tr.hidden&&!tr.classList.contains('off')}
function sel(){return cbs.filter(function(c){return c.checked}).map(function(c){return c.value})}
// HPA automation: the namespaces with it ticked, and the restrictions from the HPA automation section.
// Until both prerequisites are ticked (or while a restriction is invalid) files say it is not included.
var HP={agent:document.getElementById('hpaagent'),exporter:document.getElementById('hpaexporter')};
function hpaNs(){return hon.filter(function(c){return c.checked}).map(function(c){return c.value})}
function num(id){var el=document.getElementById(id);if(!el)return {v:null,ok:true};var t=el.value.trim();if(!t)return {v:null,ok:true};
 var v=Number(t);return {v:v,ok:Number.isInteger(v)&&v>=1}}
function hpaOpts(){var d=D.hpa_defaults,err='',o={},
 f={min_floor:'hpaminfloor',min_max:'hpaminmax',cpu_min:'hpacpumin',cpu_max:'hpacpumax',mem_min:'hpamemmin',mem_max:'hpamemmax'};
 Object.keys(f).forEach(function(k){var r=num(f[k]);if(!r.ok)err='Use whole numbers of 1 or more.';
  o[k]=r.v===null||!r.ok?(k.slice(-4)==='_max'?null:d[k]):r.v});
 [['min_floor','min_max','minReplicas'],['cpu_min','cpu_max','CPU trigger'],['mem_min','mem_max','memory trigger']].forEach(function(q){
  if(o[q[1]]!==null&&o[q[0]]>o[q[1]])err='The '+q[2]+' minimum cannot be above its maximum.'});
 var e=document.getElementById('hpaerr');if(e)e.textContent=err;return err?null:o}
function hpaArg(){if(!hon.length)return null;var ready=!!(HP.agent&&HP.agent.checked&&HP.exporter&&HP.exporter.checked);
 var o=hpaOpts();return {on:hpaNs(),opts:ready?o:null}}
// Only what is on screen counts: a row hidden by the controller-type filter keeps its tick for when
// the type comes back, but is never generated and never counted.
function ticked(){return nck.filter(function(c){return c.checked&&!c.disabled&&vis(c)}).map(function(c){return c.value})}
function wticked(){return wck.filter(function(c){return c.checked&&vis(c)}).map(function(c){return +c.value})}
function m(v){return '$'+Math.round(v).toLocaleString('en-US')}
function pl(n,w){return n+' '+w+(n===1?'':'s')}
var last=null,inview={};
function key(kind){return (kind==='wl'?'wl|'+sel().join(',')+'|'+wticked().join(','):'ns|'+sel().join(',')+'|'+ticked().join(','))+'|hpa:'+JSON.stringify(hpaArg())}
// Generated output: one rollup per CRD type, each with its own files, download and stale flag. A type
// appears once something generates it; KINDS fixes the order and the titles for the ones to come.
var OUT={},KINDS=[['wl','Pod automation - workload CRDs'],['ns','Pod automation - namespace CRDs'],['hpa','HPA automation'],
 ['karpenter','Karpenter'],['binpacking','Bin packing']];
// The HPA group reviews the pod automation files, so it is out of date whenever one of them is.
function isStale(k){var g=OUT[k];if(!g)return false;if(k==='hpa')return isStale('wl')||isStale('ns');return key(k)!==g.key}
function stale(){KINDS.forEach(function(k){var el=document.querySelector('.outgrp[data-k="'+k[0]+'"] .gstale');if(el)el.hidden=!isStale(k[0])})}
function apply(){
 var s=sel(),on={};s.forEach(function(t){on[t]=1});
 var shown=0,n=0,pot=0,risk=0,nns={},G={};inview={};
 document.querySelectorAll('table.povgrp tbody tr[data-type]').forEach(function(tr){
  var shown1=!!on[tr.dataset.type],g=tr.closest('table').dataset.g,a=G[g]||(G[g]={n:0,pot:0,t:0});
  tr.classList.toggle('off',!shown1);
  var det=tr.parentNode.querySelector('tr.nsdet[data-for="'+tr.dataset.ns+'"]');if(det)det.classList.toggle('off',!shown1);
  if(!shown1)return;shown++;a.n++;a.pot+=+tr.dataset.pot;
  if(tr.querySelector('input.wlck').checked){a.t++;n++;pot+=+tr.dataset.pot;if(+tr.dataset.sev>=3)risk++;nns[tr.dataset.n]=1;inview[tr.dataset.n]=(inview[tr.dataset.n]||0)+1}});
 document.querySelectorAll('.gsum').forEach(function(el){var a=G[el.dataset.g]||{n:0,pot:0,t:0};
  el.textContent=a.n?(pl(a.n,'workload')+' \u00b7 '+m(a.pot)+'/mo potential savings \u00b7 '+a.t+' ticked'):'None of the selected controller types'});
 var k=Object.keys(nns).length;
 document.getElementById('typesum').textContent=s.length===cbs.length?'All ('+s.length+')':(s.length?s.join(', '):'None');
 document.getElementById('scope').textContent=shown?(n+' of '+pl(shown,'workload')+' ticked across '+pl(k,'namespace')+' \\u00b7 '+m(pot)+'/mo potential savings \\u00b7 '+risk+' at High or Critical risk'):'No ranked workloads match. Pick at least one controller type.';
 document.getElementById('genwl').disabled=!n;
 var nShown=0,nGen=0,npot=0,nex=0;
 document.querySelectorAll('#povns tbody tr').forEach(function(tr){
  if(tr.dataset.managed){tr.hidden=!tr.dataset.types.split(',').some(function(t){return on[t]});return}
  var x=NS[tr.dataset.n];if(!x)return;
  var en=psEnabled(D,x,s),c=tr.cells;tr.hidden=!en.length;if(!en.length)return;nShown++;
  var w=0,p=0,r=0;en.forEach(function(t){var a=x.auto[t];w+=a.n;p+=a.pot;r+=a.risk});
  var ex=x.excl.filter(function(y){return en.indexOf(y.type)>=0}).length,iv=inview[x.ns]||0;
  c[2].textContent=w;c[2].dataset.v=w;c[3].textContent=en.join(', ');
  c[4].textContent=m(p)+'/mo';c[4].dataset.v=p;c[5].textContent=r||'-';c[5].dataset.v=r;
  c[6].textContent=ex||'-';c[6].dataset.v=ex;c[7].textContent=iv||'-';c[7].dataset.v=iv;
  if(tr.querySelector('input.nsck').checked){nGen++;npot+=p;nex+=ex}});
 document.getElementById('nsscope').textContent=nShown?(nGen?(nGen+' of '+pl(nShown,'namespace')+' ticked \\u00b7 '+pl(nGen,'file')+' to write \\u00b7 '+m(npot)+'/mo potential savings covered \\u00b7 '+pl(nex,'workload')+' kept out'):'No namespace ticked.'):'No namespace runs the selected controller types.';
 document.getElementById('genns').disabled=!nGen;
 var hs=document.getElementById('hpastate');
 if(hs){var a=hpaArg();hs.textContent=!a?'':(a.opts?('Generated files will include HPA automation in '+pl(a.on.length,'namespace')+
  ' with HPA automation ticked, and in the workload files of their HPA workloads.'):
  'HPA automation is not included in generated files until both prerequisites are ticked'+(document.getElementById('hpaerr').textContent?' and the restrictions are valid':'')+'.')}
 stale()}
cbs.concat(nck,wck).forEach(function(c){c.addEventListener('change',apply)});
hon.forEach(function(c){c.addEventListener('change',apply)});
[HP.agent,HP.exporter,document.getElementById('hpak133')].concat([].slice.call(document.querySelectorAll('input.hpain'))).forEach(function(el){
 if(el)el.addEventListener(el.type==='checkbox'?'change':'input',apply)});
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
function fileRow(f,body){var d=document.createElement('div');d.className='file';
 var h=document.createElement('div');h.className='fh';
 var b=document.createElement('b');b.textContent=f.name;h.appendChild(b);
 var c=document.createElement('span');c.className='mut';c.textContent=' '+pl(f.docs,'document')+' ';h.appendChild(c);
 if(!body){var dl=document.createElement('button');dl.type='button';dl.textContent='Download';dl.addEventListener('click',function(){save(f.name,new Blob([f.text],{type:'application/x-yaml'}))});
  var cp=document.createElement('button');cp.type='button';cp.textContent='Copy';cp.addEventListener('click',function(){copy(f.text,cp)});h.appendChild(dl);h.appendChild(cp)}
 d.appendChild(h);
 var det=document.createElement('details'),sm=document.createElement('summary');sm.textContent=body?'Show the hpa block':'Preview';det.appendChild(sm);
 var pre=document.createElement('pre');pre.textContent=body||f.text;det.appendChild(pre);d.appendChild(det);return d}
// The hpa block is merged into the pod automation files (one config per namespace or workload), so the
// HPA group reviews those files rather than offering a second, conflicting download.
function hpaGroup(){var src=[];['wl','ns'].forEach(function(k){if(OUT[k])OUT[k].files.forEach(function(f){
  if(f.text.indexOf('# HPA automation:')>=0)src.push({f:f,from:k})})});
 if(!src.length){delete OUT.hpa;return}
 var on=src.filter(function(x){return x.f.text.indexOf('# HPA automation: Enabled')>=0});
 OUT.hpa={review:true,key:key('hpa'),at:new Date(),files:src.map(function(x){return x.f}),src:src,
  what:on.length?(pl(on.length,'file')+' carry the hpa block, applied with the pod automation file it sits in. Nothing separate to download.'):
   'Not included yet: tick both prerequisites in the HPA automation section, then generate again.'}}
function block(t){var NL=String.fromCharCode(10),i=t.indexOf('    hpa:'+NL);if(i<0)return null;var j=t.indexOf(NL+'---',i);return t.slice(i,j<0?t.length:j)}
function renderOut(openK){var box=document.getElementById('outgroups'),was={};
 [].slice.call(box.querySelectorAll('.outgrp')).forEach(function(d){was[d.dataset.k]=d.open});box.textContent='';
 KINDS.forEach(function(k){var g=OUT[k[0]];if(!g)return;
  var d=document.createElement('details');d.className='outgrp';d.dataset.k=k[0];d.open=k[0]===openK||!!was[k[0]];
  var s=document.createElement('summary');var t=document.createElement('span');t.className='gt';t.textContent=k[1];s.appendChild(t);
  var sub=document.createElement('span');sub.className='mut gsub';
  sub.textContent=' '+(g.review?pl(g.src.filter(function(x){return block(x.f.text)}).length,'file')+' with the hpa block':pl(g.files.length,'file'))+
   ' \u00b7 generated '+g.at.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});s.appendChild(sub);
  var st=document.createElement('span');st.className='warn gstale';st.hidden=true;st.textContent=' The selection has changed since - generate again.';s.appendChild(st);
  if(g.saved)sub.textContent+=g.saved.error?(' \u00b7 not saved: '+g.saved.error):(g.saved.unchanged?(' \u00b7 unchanged since '+g.saved.id):(' \u00b7 saved as '+g.saved.id));
  d.appendChild(s);var p=document.createElement('p');p.textContent=g.what;d.appendChild(p);
  if(!g.review){var z=document.createElement('button');z.type='button';z.textContent='Download these ('+pl(g.files.length,'file')+', .zip)';
   z.addEventListener('click',function(){save(g.zn,psZip(g.files))});var zp=document.createElement('p');zp.appendChild(z);d.appendChild(zp);
   g.files.forEach(function(f){d.appendChild(fileRow(f))})}
  else g.src.forEach(function(x){var b=block(x.f.text);if(b)d.appendChild(fileRow(x.f,b))});
  box.appendChild(d)});
 var out=document.getElementById('out'),h=document.getElementById('outtitle'),any=!!Object.keys(OUT).length;out.hidden=!any;
 if(h){h.hidden=!any;if(any&&openK&&h.getAttribute('aria-expanded')==='false')h.click()}
 stale()}
function show(kind,files,what,zn){OUT[kind]={files:files,what:what,zn:zn,key:key(kind),at:new Date()};hpaGroup();renderOut(kind);saveSet(kind);
 document.querySelector('.outgrp[data-k="'+kind+'"]').scrollIntoView({behavior:'smooth',block:'start'})}
function hnote(){var a=hpaArg();return !a?'':(a.opts?' HPA automation included for '+pl(a.on.length,'namespace')+'.':' HPA automation not included: prerequisites not confirmed.')}
document.getElementById('genwl').addEventListener('click',function(){var s=sel(),fs=psBuild(D,s,wticked(),hpaArg());if(fs.length<2)return;
 show('wl',fs,'Controller types: '+s.join(', ')+'. '+pl(fs.length-1,'ticked workload')+', one file each, plus the cluster prerequisite.'+hnote(),D.slug+'-workload-automation-crds.zip')});
document.getElementById('genns').addEventListener('click',function(){var s=sel(),fs=psBuildNs(D,ticked(),s,hpaArg());if(fs.length<2)return;
 show('ns',fs,'Controller types: '+s.join(', ')+'. '+pl(fs.length-1,'ticked namespace')+', one file each, plus the cluster prerequisite.'+hnote(),D.slug+'-namespace-automation-crds.zip')});
// Download all: every group's files once each (the cluster prerequisite appears in both pod groups).
document.getElementById('zip').addEventListener('click',function(){var seen={},every=[];
 KINDS.forEach(function(k){var g=OUT[k[0]];if(g&&!g.review)g.files.forEach(function(f){if(!seen[f.name]){seen[f.name]=1;every.push(f)}})});
 if(every.length)save(D.slug+'-automation-crds.zip',psZip(every))});
// ---- CRD history. Needs the launcher (kickoff.py injects PS_HELPER and stores each set under crds/).
var H=window.PS_HELPER,PAGE=location.pathname,HIST=[],NL=String.fromCharCode(10),KN={wl:'Workload CRDs',ns:'Namespace CRDs'};
function api(path,opt){opt=opt||{};opt.headers=Object.assign({'X-PS-Token':H.token},opt.headers||{});
 return fetch(H.api+path,opt).then(function(r){return r.json().then(function(j){if(!r.ok)throw new Error(j.error||('HTTP '+r.status));return j})})}
function qs(o){return Object.keys(o).map(function(k){return k+'='+encodeURIComponent(o[k])}).join('&')}
function post(path,body){return api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}
function getSet(id){return api('crd-set?'+qs({page:PAGE,id:id})).then(function(m){return m.files.map(function(f){return {name:f.name,docs:f.docs,text:m.texts[f.name]}})})}
function saveSet(kind){var g=OUT[kind];if(!H||!g)return;
 post('crd-save',{page:PAGE,kind:kind,files:g.files.map(function(f){return {name:f.name,text:f.text}}),meta:{types:sel(),files:g.files.length,hpa:hpaArg()}})
 .then(function(r){g.saved=r;renderOut();loadHistory()},function(e){g.saved={error:e.message};renderOut()})}
function mk(t,c,x){var e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function mkBtn(t,f){var b=mk('button',null,t);b.type='button';b.addEventListener('click',f);return b}
function when(iso){var d=new Date(iso);return d.toLocaleDateString([],{day:'numeric',month:'short'})+' '+d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})}
// The set in the cluster: the latest one marked handed to the customer, else the latest saved.
function current(kind){var s=HIST.filter(function(x){return x.kind===kind});return s.filter(function(x){return x.handed})[0]||s[0]}
function showDiff(box,oldF,newF,label){box.textContent='';var r=psFileDiff(oldF,newF);
 box.appendChild(mk('p','mut',label+': '+pl(r.added.length,'file')+' added, '+pl(r.changed.length,'file')+' changed, '+pl(r.removed.length,'file')+' removed.'));
 r.added.forEach(function(k){box.appendChild(mk('div','dadd','+ '+k))});r.removed.forEach(function(k){box.appendChild(mk('div','ddel','- '+k))});
 r.changed.forEach(function(k){var d=mk('details');d.appendChild(mk('summary',null,'~ '+k));var pre=mk('pre','diff');
  psDiff(r.o[k],r.n[k]).forEach(function(x){var g=x[0]==='~'||x[0]==='!';pre.appendChild(mk('span',{'+':'da','-':'dd'}[x[0]]||(g?'dg':''),(g?'... '+x[1]:x[0]+' '+x[1])+NL))});
  d.appendChild(pre);box.appendChild(d)})}
function rollback(box,s){var cur=current(s.kind);box.textContent='';
 if(!cur||cur.id===s.id){box.appendChild(mk('p',null,'This is the current set of its kind: the latest marked handed to the customer, or the latest saved.'));return}
 api('crd-rollback?'+qs({page:PAGE,id:s.id,from:cur.id})).then(function(r){
  box.appendChild(mk('p',null,'To '+(s.id<cur.id?'go back':'move forward')+' from '+cur.id+(cur.handed?' (handed to the customer)':' (latest saved)')+' to this set:'));var ol=mk('ol','steps');
  ol.appendChild(mk('li',null,'Download this set and apply it with kubectl apply -f: namespace files first, then workload files.'));
  var li=mk('li',null,r.delete.length?('Delete the '+pl(r.delete.length,'object')+' that '+cur.id+' has and this set does not. Applying files never removes them:'):'Nothing to delete: '+cur.id+' has no object this set lacks.');
  if(r.delete.length){li.appendChild(mk('pre','diff',r.delete.join(NL)));var cb=mkBtn('Copy commands',function(){copy(r.delete.join(NL),cb)});li.appendChild(cb)}
  ol.appendChild(li);
  ol.appendChild(mk('li',null,'To also undo the resource changes automation already made, use the PerfectScale switches in the ClusterAutomationConfig: stopAllAutomation: true pauses automation, cleanupAllAutomation: true reverts every change it made.'));
  box.appendChild(ol)},function(e){box.appendChild(mk('p','warn','Rollback view failed: '+e.message))})}
function setRow(s,list){var row=mk('div','crdset'),hd=mk('div','crdh'),box=mk('div','crdbox'),c=s.changes;
 hd.appendChild(mk('b',null,when(s.created)));
 hd.appendChild(mk('span','mut',' '+s.id+' \u00b7 plan '+((s.meta||{}).plan_version||'?')+' \u00b7 '+pl(s.files.length,'file')+' \u00b7 '+
  (c?('+'+c.added.length+' ~'+c.changed.length+' -'+c.removed.length+' files vs previous'):'first set')+' '));
 if(s.handed)hd.appendChild(mk('span','badge','Handed to the customer '+when(s.handed_at)));
 var row2=mk('div','crdacts');
 row2.appendChild(mkBtn('Download (.zip)',function(){getSet(s.id).then(function(f){save('crd-'+s.id+'.zip',psZip(f))})}));
 if(s.previous)row2.appendChild(mkBtn('Diff vs previous',function(){Promise.all([getSet(s.previous),getSet(s.id)]).then(function(r){showDiff(box,r[0],r[1],'Changes since '+s.previous)})}));
 if(OUT[s.kind])row2.appendChild(mkBtn('Diff vs on screen',function(){getSet(s.id).then(function(f){showDiff(box,f,OUT[s.kind].files,'From this set to what is generated on screen')})}));
 row2.appendChild(mkBtn(s.handed?'Unmark handed':'Mark handed to the customer',function(){post('crd-mark',{page:PAGE,id:s.id,handed:!s.handed}).then(loadHistory)}));
 row2.appendChild(mkBtn('Roll back to this',function(){rollback(box,s)}));
 row.appendChild(hd);row.appendChild(row2);row.appendChild(box);list.appendChild(row)}
function loadHistory(){var L=document.getElementById('crdlist');if(!H||!L)return;
 api('crd-history?'+qs({page:PAGE})).then(function(r){HIST=r.sets;L.textContent='';
  document.getElementById('crdhelp').textContent='Every CRD set generated on this page is saved under crds/'+r.cluster+'/ in the tenant folder and kept for good. '+
   'The last three of each kind are shown first. Mark the set you hand to the customer, so a rollback knows what is in the cluster.';
  if(!HIST.length){L.appendChild(mk('p','mut','No sets saved yet: generate workload or namespace CRDs above.'));return}
  ['ns','wl'].forEach(function(k){var s=HIST.filter(function(x){return x.kind===k});if(!s.length)return;
   var d=mk('details','crdkind');d.open=true;d.appendChild(mk('summary',null,KN[k]+' ('+s.length+' saved)'));var list=mk('div');d.appendChild(list);
   s.slice(0,3).forEach(function(x){setRow(x,list)});
   if(s.length>3){var more=mkBtn('Show all '+s.length,function(){list.textContent='';s.forEach(function(x){setRow(x,list)});more.remove()});d.appendChild(more)}
   L.appendChild(d)})},function(e){L.textContent='';L.appendChild(mk('p','warn','Could not load the CRD history: '+e.message))})}
loadHistory();
apply()})()"""

# Sections roll up via JS_COLLAPSE, which runs after JS_RESIZE (and after JS_UI has counted every row)
# so column widths are measured while everything is still visible. Printing opens the wave groups too:
# a closed <details> prints as its summary line only.
JS_PRINT = """addEventListener('beforeprint',function(){document.querySelectorAll('details.wgrp,details.hparoll,details.hpascript,details.outgrp').forEach(function(d){d.open=true})})"""

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
.hpapre label,.hpagrid label{display:block;font-size:14px;margin:4px 0}
.hpagrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:6px 16px;margin:6px 0}
.hpagrid .hpaf span{display:block;font-size:13px;color:var(--mut)}
.hpagrid input{font:inherit;width:7em;padding:2px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.hpastate{font-size:13px;font-weight:600}
details.hparoll>summary,details.outgrp>summary{cursor:pointer;font-weight:600}
details.outgrp>summary .gsub,details.outgrp>summary .gstale{font-weight:400}
table.hpaft{table-layout:fixed}table.hpaft th:nth-child(1){width:28%}table.hpaft th:nth-child(2){width:18%}
table.hpaft th:nth-child(3){width:16%}table.hpaft td.wnote{font-size:13px;color:var(--mut)}table.hpaft td.hpa{font-size:13px}
.crdset{border-top:1px solid var(--line);padding:6px 0}.crdacts button,.crdbox button{font:inherit;font-size:12px;margin:4px 6px 0 0;cursor:pointer}
.badge{display:inline-block;font-size:12px;background:var(--card);border:1px solid var(--acc);color:var(--acc);border-radius:10px;padding:0 8px;margin-left:6px}
pre.diff{font-size:12px;white-space:pre-wrap;word-break:break-all;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 8px;max-height:420px;overflow:auto}
pre.diff .da,.dadd{color:#1a7f37}pre.diff .dd,.ddel{color:#cf222e}pre.diff .dg{color:var(--mut)}
details.crdkind>summary{cursor:pointer;font-weight:600}
ul.hpal{margin:0;padding-left:18px}ul.hpal li.hk-report{color:var(--mut)}ul.hpal li.hk-min{font-style:italic}
details.hpascript pre{white-space:pre-wrap;word-break:break-all;background:var(--card);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px}
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


WAVE_ORDER = {"1": 1, "1b": 2, "1h": 3, "Review": 4}
WAVE_TEXT = {"1": "Wave 1", "1b": "Wave 1b", "1h": "Wave 1 + HPA automation", "Review": "Review"}


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
    """Pre-ticked when automation can move it now: Wave 1, 1b or 1h (with HPA automation alongside),
    and enough waste to clear PerfectScale's automation minimum. Review rows start unticked."""
    return (r.get("wave") or "1") in ("1", "1b", "1h") and r["waste"] >= AUTOMATION_MIN_USD


def _wave_legend(f, pv):
    rows = pv.get("rows") or []
    c = {k: sum(1 for r in rows if (r.get("wave") or "1") == k) for k in WAVE_ORDER}
    src = ("HPA triggers come from PerfectScale's public API v2, which carries no HPA min/max replicas; the HPA "
           "automation section below covers what that means for minReplicas."
           if (f.get("api_v2") or {}).get("provided") else
           "HPA data was not collected for this run, so every row's HPA reads Unknown. Store API credentials "
           "(scripts/set_api_credentials.sh) and re-collect to sort rows by HPA.")
    return ("<p class=mut><b>Waves:</b> "
            f"Wave 1 ({c['1']}) - no HPA, or an HPA triggered on the container itself. "
            f"Wave 1b ({c['1b']}) - stateful or mesh components; give them extra headroom. "
            f"Wave 1 + HPA automation ({c['1h']}) - an HPA on pod CPU/memory: PerfectScale sizes the request from the "
            "trigger, and its HPA automation keeps the HPA aligned (see HPA automation below). "
            f"Review ({c['Review']}) - HPA on custom metrics only, or automation not supported; starts unticked. " + e(src) + "</p>")


# Every group starts collapsed, like every section of the page: the reader opens what they came for.
GROUPS = (("ready", "Ready to automate", "Wave 1 and 1b: no HPA, a container-level trigger, or extra headroom", ("1", "1b"), False),
          ("hpa", "Ready with HPA automation", "HPA on pod CPU/memory: automate with PerfectScale's HPA automation alongside", ("1h",), False),
          ("review", "Review", "HPA on custom metrics only, or automation not supported", ("Review",), False),
)


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
        n.append("HPA on pod CPU/memory: switch on HPA automation with it")
        n += [x.get("short") or x["text"] for x in ((r.get("hpa_info") or {}).get("findings") or []) if x["kind"] != "min"]
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
        P.append(_hpa_section(f, pv, namespaces))
        # Its own top-level heading so it rolls up like every section and is never hidden inside
        # another one; hidden until something is generated, then opened.
        P.append("<h2 id=outtitle hidden>Generated CRDs</h2><div id=out hidden>"
                 "<p class=mut>Each kind of config you generate gets its own section, with its own download. Sections appear as "
                 "you generate them: pod automation (workload and namespace CRDs), HPA automation (reviewed here, applied as "
                 "part of the pod automation files), and Karpenter or bin packing configs where this page offers them.</p>"
                 "<ol class=steps><li>Confirm the PerfectScale automation agent is installed on the cluster.</li>"
                 "<li>Apply the cluster prerequisite only if the cluster has no ClusterAutomationConfig yet.</li>"
                 "<li>Namespace files automate whole namespaces, each with its kept-out workloads disabled; workload files "
                 "automate only the ticked workloads, one file each. Using both: apply the namespace files first, then the "
                 "workload files, so a workload ticked in the table replaces its namespace exclusion.</li>"
                 "<li>Every <code>keepLimit: false</code> line carries a REVIEW comment: confirm no ResourceQuota, LimitRange or "
                 "policy requires a CPU limit before applying.</li></ol>"
                 "<div id=outgroups></div><p><button type=button id=zip>Download all (.zip)</button></p></div>")
        P.append("<h2>CRD history</h2><p class=mut id=crdhelp>Saved CRD sets need this page opened with the launcher: "
                 "double-click <b>Open kickoff pages.command</b> in the tenant folder. Opened that way, every set generated "
                 "here is saved under crds/ in the tenant folder and kept for good, and this section lists the last three "
                 "of each kind with a download, a diff, a handed-to-customer mark and a rollback view.</p><div id=crdlist></div>")
    held = _held_lines(pv)
    if held:
        P.append("<h2>Kept out of this table</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in held) + "</ul>")
    P.append("<p class=fn>Read-only: this page was generated from PerfectScale data and applies nothing to the cluster. The configs "
             "follow the cost-optimization profile of PerfectScale's published automation examples, with keepLimit set to false "
             "for review, and the hpa block of its HPA automation docs.</p></main>")
    data = json.dumps(page_data(cluster, prepared, rows, namespaces), ensure_ascii=False).replace("</", "<\\/")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>{CSS}\n{POV_CSS}</style></head><body>{''.join(P)}"
            f"<script type=application/json id=povdata>{data}</script>"
            f"<script>{JS};{JS_RESIZE};{JS_NSROW};{JS_HPA};{JS_BUILD};{JS_BUILD_NS};{JS_ZIP};{JS_DIFF};{JS_UI};{JS_COLLAPSE};{JS_PRINT}</script></body></html>")


HELD_REASON = {"stateful": "stateful", "jvm": "JVM heap",
               "held_out": "held out in the tenant config"}


def _ns_section(pv, namespaces, in_table):
    """The namespace picker: every namespace with something a namespace config can automate, ticked
    by default when the ranked table touches it; platform-managed namespaces listed last, greyed
    out and never selectable. Figures are for all controller types here and are recomputed in the
    browser for the selected ones."""
    P = ["<h2>Namespace scope</h2><p class=mut>Tick the namespaces to automate as a whole. Each gets its own file with "
         "one NamespaceAutomationConfig enabling the selected controller types it runs, covering every workload of those "
         "types, ranked or not. Workloads that should not ride along - held out in this tenant's configuration, stateful, "
         "or JVM - get a Disabled workload config in the same file (Kept out). A namespace where every workload is kept "
         "out is listed greyed with the reason, not selectable. Only namespaces that run one of the selected controller "
         f"types are shown. Ticked by default: the namespaces the ranked table touches with at least ${AUTOMATION_MIN_USD}/mo "
         "of waste to act on.</p>"
         "<p class=mut><b>HPA automation:</b> ticked for every namespace with workloads whose HPA scales on pod CPU or "
         "memory. Its file then also carries the hpa block, set in the HPA automation section below. Untick it to leave a "
         "namespace's HPAs alone.</p>"
         "<div class=nsbar><button type=button id=nsall>Select all</button><button type=button id=nsnone>Clear</button>"
         "<button type=button id=nsmatch>Match the table</button>"
         "<button type=button id=genns class=go>Generate namespace CRDs</button><span id=nsscope class=mut></span></div>"
         "<div class=wrap><table id=povns class=sort><thead><tr><th>In scope</th><th>Namespace</th><th>Workloads covered</th>"
         "<th>Controller types enabled</th><th>Potential savings</th><th>At High or Critical risk</th><th>Kept out</th>"
         "<th>Ticked in ranked table</th><th>HPA automation</th></tr></thead><tbody>"]

    def hpa_cell(n, k):
        nh = (n.get("hpa") or {}).get("n", 0)
        if not nh:
            return "<td>-</td>"
        return (f"<td><label class=hpalb><input type=checkbox class=hpaon value='{k}' checked "
                f"aria-label='HPA automation for {k}'> {plural(nh, 'workload')}</label></td>")
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
            # The greyed managed path: shown while one of its kinds is selected, never recomputed, never generated.
            P.append(f"<tr class=mgd data-managed=1 data-types='{e(','.join(n['types']))}' data-n='{k}'><td><input type=checkbox class=nsck value='{k}' disabled "
                     f"aria-label='{k} (every workload kept out, not selectable)'></td><td>{k}</td><td class=num data-v='0'>0</td>"
                     f"<td>Held: {e(why)} on every workload</td><td class=num data-v='{n['held_pot']:.2f}'>{usd(n['held_pot'])}/mo on hold</td>"
                     f"<td>-</td><td class=num data-v='{nex}'>{nex}</td><td class=num>-</td><td>-</td></tr>")
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


def hpa_script(cluster):
    """Read-only commands that would give the exact HPA and PDB settings, the Kubernetes version and the
    PerfectScale agent/exporter versions - shown, never required: the page works without them."""
    fn = f"hpa-pdb-{slug(cluster)}.json"
    return [
        ("Every HPA and PodDisruptionBudget: minReplicas, maxReplicas, triggers, scaling behaviour and the PDB floors",
         f"kubectl get hpa,pdb --all-namespaces -o json > {fn}"),
        ("The Kubernetes version: 1.33 or later lets HPA automation move a pod-level trigger to the app container",
         "kubectl version"),
        ("The PerfectScale automation agent and exporter versions (v1.0.71 and v1.1.28 or later)",
         "kubectl get deploy,daemonset --all-namespaces -o jsonpath='{range .items[*]}{.metadata.namespace} "
         "{.metadata.name} {.spec.template.spec.containers[*].image}{\"\\n\"}{end}' | grep -i perfectscale"),
    ]


def _hpa_section(f, pv, namespaces):
    """HPA automation: what PerfectScale's HPA automation will change on this cluster, worked out from
    the data with its documented rules (model.hpa_analysis), and the inputs the generated hpa blocks
    need. Built for a customer who will not run a script: prerequisites are ticked after asking,
    restrictions start at PerfectScale's defaults, and PodFit's HPA tab is the place for the exact
    minReplicas. The read-only commands are shown, folded away, in case the customer agrees later."""
    hs = [n for n in namespaces if (n.get("hpa") or {}).get("n")]
    if not hs:
        return ("<h2>HPA automation</h2><p class=mut>No workload in this cluster has an HPA that scales on pod CPU or "
                "memory, so there is nothing for PerfectScale's HPA automation to manage here.</p>")
    items = [(n["ns"], i) for n in hs for i in n["hpa"]["items"]]
    kinds = lambda i: {x["kind"] for x in i["findings"]}  # noqa: E731
    n_all = sum(n["hpa"]["n"] for n in hs)
    pot = sum(n["hpa"]["pot"] for n in hs)
    ch = [x for x in items if "change" in kinds(x[1])]
    rp = [x for x in items if "report" in kinds(x[1])]
    mn = [x for x in items if "min" in kinds(x[1])]
    mv = [x for x in items if any("application container" in y["text"] for y in x[1]["findings"])]
    P = [f"<h2>HPA automation</h2><p>{plural(n_all, 'workload')} in this cluster {'has' if n_all == 1 else 'have'} an HPA that "
         f"scales on pod CPU or memory, carrying {usd(pot)}/mo of potential savings. PerfectScale sizes their request from the "
         "HPA trigger (about 1.25x observed usage at an 80% trigger), so they are automated with the rest. PerfectScale's "
         "HPA automation, switched on alongside, then keeps minReplicas and the CPU and memory triggers aligned as the "
         "requests change. It never changes maxReplicas or scaling behaviour.</p>"]
    will = []
    if ch:
        will.append(f"Change a CPU or memory trigger on {plural(len(ch), 'workload')}: raise one under 80% to 80%, or lower one "
                    "above the highest it can reach before the limit"
                    + (f" (including {plural(len(mv), 'pod-level trigger')} to move to the application container, on "
                       "Kubernetes 1.33 or later)" if mv else "") + ".")
    else:
        will.append("Leave every trigger as it is: from the data, each CPU and memory trigger is between 80% and the highest "
                    "it can reach with the recommended requests and limits.")
    will.append("Lower minReplicas to 2 (or to what a single PodDisruptionBudget needs) where it sits higher. The public API "
                "does not carry each HPA's minReplicas, so PodFit's HPA tab shows the exact value"
                + (f"; {plural(len(mn), 'workload')} held replicas flat at 3 or more all window, the likeliest candidates." if mn else "."))
    own = ["maxReplicas and scaling behaviour: PerfectScale never changes them. A workload that stays at maxReplicas is a "
           "scaling question, not a sizing one."]
    if rp:
        own.append(f"{plural(len(rp), 'workload')} with a finding PerfectScale reports but will not change - listed under "
                   "Findings by workload.")
    P.append("<h3>What HPA automation will change</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in will) + "</ul>"
             "<h3>What stays with the owners</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in own) + "</ul>")
    P.append("<h3>Prerequisites</h3><p class=mut>Ask the customer, then tick. Generated files include the hpa block only "
             "once both required boxes are ticked.</p><div class=hpapre>"
             "<label><input type=checkbox id=hpaagent> PerfectScale automation agent v1.0.71 or later on this cluster (required)</label>"
             "<label><input type=checkbox id=hpaexporter> PerfectScale exporter v1.1.28 or later (required)</label>"
             "<label><input type=checkbox id=hpak133> Kubernetes 1.33 or later (optional: lets a pod-level trigger move to "
             "the application container; otherwise it stays pod-level)</label></div>"
             "<p class=mut>Pod automation must cover these workloads too: HPA automation acts only on a revision PerfectScale "
             "automation created. The files generated here do both.</p><p id=hpastate class=hpastate></p>")
    d = HPA_DEFAULTS

    def inp(id_, label, val, hint):
        v = "" if val is None else str(val)
        return (f"<label class=hpaf><span>{e(label)}</span><input type=number min=1 step=1 class=hpain id={id_} "
                f"value='{v}' placeholder='{e(hint)}'></label>")
    P.append("<h3>Restrictions for the generated configs</h3><p class=mut>PerfectScale's defaults. Change them only if the "
             "customer wants a higher floor or a cap; leave a maximum blank for none.</p><div class=hpagrid>"
             + inp("hpaminfloor", "Lowest minReplicas to write", d["min_floor"], "2")
             + inp("hpaminmax", "Highest minReplicas to write", d["min_max"], "none")
             + inp("hpacpumin", "Lowest CPU trigger (%)", d["cpu_min"], "80")
             + inp("hpacpumax", "Highest CPU trigger (%)", d["cpu_max"], "none")
             + inp("hpamemmin", "Lowest memory trigger (%)", d["mem_min"], "80")
             + inp("hpamemmax", "Highest memory trigger (%)", d["mem_max"], "none")
             + "</div><p id=hpaerr class=warn></p>")
    def short(i, k=3):
        """One line per workload: its findings' short labels, the first k, then a count."""
        sh = [x.get("short") or x["text"] for x in i["findings"]]
        return " \u00b7 ".join(sh[:k]) + (f" \u00b7 +{len(sh) - k} more" if len(sh) > k else "")

    def hpa_line(detail):
        parts = [x.strip() for x in (detail or "").split(",") if x.strip()]
        return (parts[0] + (f" +{len(parts) - 1}" if len(parts) > 1 else "")) if parts else "-"

    rows = []
    for j, n in enumerate(hs):
        it = n["hpa"]["items"]
        c = sum(1 for i in it if "change" in kinds(i)); r = sum(1 for i in it if "report" in kinds(i))
        m = sum(1 for i in it if "min" in kinds(i))
        withf = [i for i in it if i["findings"]]
        k = f"h{j}"
        note = ("<ul class=hpal>" + "".join(f"<li><b>{e(i['name'])}</b>: {e(short(i, 6))}</li>" for i in withf[:60]) + "</ul>"
                + (f"<p class=mut>+{len(withf) - 60} more workloads with findings.</p>" if len(withf) > 60 else "")
                + (f"<p class=mut>{plural(len(it) - len(withf), 'workload')} with nothing to change.</p>" if len(it) > len(withf) else "")
                if withf else f"<p class=mut>Nothing to change on its {plural(len(it), 'workload')}: every trigger is in range.</p>")
        rows.append(f"<tr data-ns='{k}'><td data-v='{e(n['ns'])}'><button type=button class=nstog aria-expanded=false>{e(n['ns'])}</button></td>"
                    f"<td class=num data-v='{n['hpa']['n']}'>{n['hpa']['n']}</td>"
                    f"<td class=num data-v='{n['hpa']['pot']:.2f}'>{usd(n['hpa']['pot'])}/mo</td>"
                    f"<td class=num data-v='{c}'>{c or '-'}</td><td class=num data-v='{r}'>{r or '-'}</td>"
                    f"<td class=num data-v='{m}'>{m or '-'}</td></tr>"
                    f"<tr class=nsdet data-for='{k}' hidden><td colspan=6>{note}</td></tr>")
    P.append(f"<details class=hparoll><summary>By namespace ({len(hs)})</summary>"
             "<p class=mut>Click a namespace for what changes on each of its workloads. Switch a namespace's HPA automation on "
             "or off under Namespace scope.</p>"
             "<div class=wrap><table class='sort nrsz'><thead><tr><th>Namespace</th><th>HPA workloads</th><th>Potential savings</th>"
             "<th>Triggers to change</th><th>Reported only</th><th>minReplicas hints</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div></details>")
    fi = [(ns, i) for ns, i in items if i["findings"]]
    if fi:
        frows = []
        for j, (ns, i) in enumerate(fi[:300]):
            k = f"f{j}"
            frows.append(f"<tr data-ns='{k}'><td data-v='{e(i['name'])}'><button type=button class=nstog aria-expanded=false>{e(i['name'])}</button></td>"
                         f"<td>{e(ns)}</td><td class=hpa>{e(hpa_line(i['detail']))}</td><td class=wnote>{e(short(i))}</td></tr>"
                         f"<tr class=nsdet data-for='{k}' hidden><td></td><td colspan=3>"
                         f"<p class=mut>HPA triggers: {e(i['detail'] or '-')}</p><ul class=hpal>"
                         + "".join(f"<li class='hk-{x['kind']}'>{e(x['text'])}</li>" for x in i["findings"]) + "</ul></td></tr>")
        P.append(f"<details class=hparoll><summary>Findings by workload ({len(fi)})</summary>"
                 "<p class=mut>Click a workload for the full notes.</p><div class=wrap><table class='sort nrsz hpaft'>"
                 "<thead><tr><th>Workload</th><th>Namespace</th><th>HPA</th><th>What PerfectScale will do or report</th></tr></thead><tbody>"
                 + "".join(frows) + "</tbody></table></div>"
                 + (f"<p class=mut>Showing the first 300 of {len(fi)}.</p>" if len(fi) > 300 else "") + "</details>")
    P.append("<details class=hpascript><summary>If the customer agrees to share their HPA and PDB settings</summary>"
             "<p>Not needed for anything on this page. Read-only commands, run by someone with access to the cluster, that "
             "give the exact values the public API does not: each HPA's minReplicas and maxReplicas, the PodDisruptionBudget "
             "floor for minReplicas, the Kubernetes version and the PerfectScale agent and exporter versions. Share the "
             "output with your PerfectScale contact to confirm the prerequisites and minReplicas changes before rollout.</p><ol>"
             + "".join(f"<li>{e(t)}<pre><code>{e(c)}</code></pre></li>" for t, c in hpa_script(f["name"])) + "</ol></details>")
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
