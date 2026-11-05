"""Tenant config. One JSON file describes a run; nothing customer-specific lives in code."""
import copy
import json
import re
import sys
from pathlib import Path

# Config keys whose values are used as regexes downstream (re.compile'd in model.py/actions.py), as
# opposed to e.g. managed_namespaces which looks pattern-shaped but is actually matched by exact set
# membership, never compiled as a regex.
REGEX_KEYS = ("held_out_from_automation", "stateful_patterns", "jvm_patterns",
              "nonprod_patterns", "auto_provisioned_pool_patterns", "gpu_pool_patterns")

DEFAULTS = {
    "customer": None,
    "author": "the DoiT Solutions Engineering team",
    "prepared": None,                       # ISO date; default = today
    "profile": None,                        # pscli profile name (PERFECTSCALE_PROFILE)
    "cloud": None,                          # gcp | aws | azure ; read from collected cluster data if absent
    "clusters": [],                         # [{"name":..., "uid":...}]
    "data_dir": "data/plan-run",
    "out_dir": "reports/v2",
    "version_tag": "v2",
    "rerun_days": 90,
    "min_history_days": 14,                 # below this => "early baseline", run-rate scaling flagged
    "scale_to_days": 30,
    "status": None,                         # e.g. "Draft for review": adds a banner and a feedback section
    "adoption_factor": 0.7,                 # share of recommendations realistically adopted; drives the adoption view
    # -- scope / assumptions recorded at intake. null => stated as an assumption in the plan.
    "assumptions": {"scope": "entire cluster", "delivery": "automation", "gitops": None},
    "intake": {"change_freeze": None, "approval_lead_time": None, "namespace_owners": None},
    "foreign_tenants": [],                  # other customer names this working copy has handled; the
                                            # verifier fails the run if one appears in a rendered file
    "exclusions": [],                       # name/namespace substrings excluded from automation and KPIs
    "held_out_from_automation": [],         # extra regexes (workload name or namespace) kept out of the automation ask
    "stateful_patterns": ["elasticsearch", "opensearch", "kibana", "kafka", "strimzi", "zookeeper"],
    # Workloads on a managed runtime whose memory is set by the runtime, not the
    # container: lowering a request without moving the heap flag in the same
    # change causes OOMKills. Declared per tenant after asking - never inferred,
    # because the public API carries no image or runtime field. Matched on
    # workload name or namespace. See references/methodology.md.
    "jvm_patterns": [],
    "runtime_file": None,                   # optional JSON from a PodFit browser pass, if a Runtime column is available
    "nonprod_patterns": ["stage", "staging", "dev", "nonprod", "sbx", "sandbox", "test", "uat", "qa"],
    "managed_namespaces": [],               # extra namespaces treated as platform-managed
    "auto_provisioned_pool_patterns": ["^nap-"],   # regexes for short-lived provisioned pools (GKE NAP)
    "gpu_pool_patterns": ["gpu"],
    "thresholds": {
        "plain_job_group_min": 200,         # ungrouped Job workloads before grouping is recommended
        "plain_job_waste_min_usd": 50.0,    # ...and the waste they carry must be worth a rule (run-rate)
        "replicated_group_min": 25,         # namespaces one controller name must repeat across to be a grouping candidate
        "replicated_group_pot_min_usd": 100.0,  # ...and the savings that name carries must be worth a rule (run-rate)
        "pool_idle_min_usd": 50.0,          # long-lived pool idle (run-rate) worth its own action
        "gpu_idle_share_min": 0.25,
        "request_inflation_ratio": 5.0,     # requested/used CPU on a pool => request-inflation driven
        "oom_margin": 1.2,                  # p95 x margin = "likely" memory sizing
        "oom_low_margin": 1.1,              # p90 x margin = "low" sizing
    },
    "cluster_settings": {},                 # optional facts the API cannot see: {cluster: {"autoscaling_profile": "..."}}
    "eligibility_file": None,               # optional JSON from the PodFit browser pass (see references)
    "trends_csv": None,                     # optional daily cost-by-cluster CSV for seasonality check
    "previous_plan": None,                  # path to an earlier plan JSON, for the change log
}


def _validate_clusters(clusters):
    """Fail clearly at config-load time, naming the bad entry, instead of a raw KeyError deep inside
    collect.py the first time that cluster is touched."""
    for i, c in enumerate(clusters):
        name = c.get("name") if isinstance(c, dict) else None
        uid = c.get("uid") if isinstance(c, dict) else None
        if not isinstance(c, dict) or not name or not uid:
            missing = [k for k, v in (("name", name), ("uid", uid)) if not v]
            label = name or f"entry #{i}"
            raise SystemExit(
                f"config: clusters[{i}] ({label!r}) is missing required key(s) {missing} - "
                "every cluster entry needs both 'name' and 'uid' (cluster names are not unique; "
                "clusters must be addressed by uid, see references/methodology.md)")


def _validate_patterns(cfg):
    """Regex-shaped keys are used as regexes with zero validation downstream. Catch two classes of
    mistake at load time: (1) a pattern that doesn't even compile as a regex, and (2) a non-fatal but
    real footgun documented in references/tenant-config.md - an unanchored pattern such as "prod" that
    also matches "preprod", when the author likely meant an exact match ("^prod$").

    The anchoring warning is deliberately narrow so it doesn't nag about the library's own shipped
    defaults (e.g. "kafka", "gpu", "stage" in stateful_patterns/gpu_pool_patterns/nonprod_patterns -
    those are intentional, vetted, loose substring matches). It only fires for a *user-supplied*
    plain word/name-shaped pattern (no regex metacharacters at all, so it reads like a literal name
    rather than a deliberately crafted pattern) that carries no anchor on either end.
    """
    for key in REGEX_KEYS:
        defaults_for_key = DEFAULTS.get(key) or []
        for pat in cfg.get(key) or []:
            try:
                re.compile(pat)
            except re.error as e:
                raise SystemExit(f"config: '{key}' contains an invalid regex {pat!r}: {e}")
            if pat in defaults_for_key:
                continue
            if re.fullmatch(r"[A-Za-z0-9_-]+", pat) and not pat.startswith("^") and not pat.endswith("$"):
                print(f"config: warning: '{key}' pattern {pat!r} is unanchored and will match anywhere "
                      f"in the name (e.g. 'prod' also matches 'preprod'); use '^{pat}$' for an exact match "
                      "if that's what you meant", file=sys.stderr)


def _merge(base, over):
    # Deep copy, not dict(base): a shallow copy hands back the DEFAULTS nested dicts themselves, so a
    # caller adjusting one threshold on a loaded config silently rewrites the default for every config
    # loaded afterwards in the same process.
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    root: Path

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        with open(path) as fh:
            raw = json.load(fh)
        cfg = cls(_merge(DEFAULTS, raw))
        cfg.root = path.parent.parent if path.parent.name == "config" else path.parent
        if not cfg["customer"]:
            raise SystemExit("config: 'customer' is required")
        if not cfg["clusters"]:
            raise SystemExit("config: 'clusters' (name + uid) is required")
        _validate_clusters(cfg["clusters"])
        _validate_patterns(cfg)
        if not cfg["prepared"]:
            from datetime import date
            cfg["prepared"] = date.today().isoformat()
        return cfg

    def path(self, key):
        p = Path(self[key])
        return p if p.is_absolute() else (self.root / p)

    def data(self, cluster_name):
        return self.path("data_dir") / cluster_name

    def out(self):
        p = self.path("out_dir")
        p.mkdir(parents=True, exist_ok=True)
        return p
