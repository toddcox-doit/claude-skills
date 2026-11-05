"""Tenant config. One JSON file describes a run; nothing customer-specific lives in code."""
import json
from pathlib import Path

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


def _merge(base, over):
    out = dict(base)
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
