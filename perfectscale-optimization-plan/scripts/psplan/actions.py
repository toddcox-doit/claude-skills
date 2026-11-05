"""Action engine: turns cluster facts into a phased, scored, dependency-ordered plan.

Everything is rule-driven over N clusters; every number comes from `facts`. A rule fires only when the data
says it applies, so the same code produces a 2-cluster GKE plan and a 13-cluster Karpenter plan.

Money bases (never summed with each other):
  achievable_now        PerfectScale-computed potential savings on workloads
  blocked_pending       idle cost / estimated saving on long-lived node pools that something is holding up
  resiliency_investment spend (not saving) to fix under-provisioned workloads; carried as a low/likely/literal range
"""
import collections
import math
import re
from datetime import date, timedelta

from .model import short_name
from .util import human_list, pct, roi_score, usd

TIERS = ["XS", "S", "M", "L"]
# Which PerfectScale product a recommendation comes from. PodFit = workload requests/automation; InfraFit = node pools and what blocks node scale-down.
FIT = {"automation_proof": "PodFit", "automation_w2": "PodFit", "automation_w3": "PodFit", "memory_limits": "PodFit", "oom_fix": "PodFit", "job_grouping": "PodFit",
       "replicated_grouping": "PodFit",
       "job_rightsize": "PodFit", "stateful_manual": "PodFit", "heldout_manual": "PodFit", "underprov_other": "PodFit", "headroom_policy": "PodFit", "default_policy": "PodFit",
       "stale_optout": "InfraFit", "pdb": "InfraFit", "unevictable_affinity": "InfraFit", "local_storage": "InfraFit", "pool_shrink_blocked": "InfraFit", "pool_shrink_free": "InfraFit",
       "pool_machine_recs": "InfraFit", "autoprov_review": "InfraFit", "gpu_cap": "InfraFit", "commitments": "InfraFit", "review_cadence": "Governance", "rerun": "Governance",
       "pool_flex": "InfraFit", "autoscaler_labels": "InfraFit", "jvm_rightsize": "PodFit"}
# Question groups for the customer conversation, matched on the start of each question.
DECISION_GROUPS = [("Who owns", "Ownership"), ("Are the minimum sizes", "Ownership"), ("Are the do-not-evict", "Intent"), ("Does every environment", "Intent"),
                   ("Is the headroom", "Intent"), ("The workloads kept out", "Intent"), ("Is automation enabled", "Access and constraints"), ("Do the node pools", "Ownership"),
                   ("Which of these workloads", "Intent")]
# How the dollar figure was produced: computed = PerfectScale workload savings; estimate = PerfectScale machine-type estimate;
# ceiling = a pool's total idle cost (assumes idle falls to zero, which never fully happens); spend = money to add.
KIND = {"pool_machine_recs": "estimate", "pool_shrink_blocked": "ceiling", "pool_shrink_free": "ceiling", "oom_fix": "spend", "underprov_other": "spend"}
CLOUD = {
    "gcp": {"commit": "committed-use discounts", "gpu_note": "GPU-attached node pools",
            "autoscaler_review": "the cluster autoscaling profile (balanced versus optimize-utilization), node auto-provisioning resource limits and allowed machine families"},
    "aws": {"commit": "Savings Plans or reserved instances", "gpu_note": "GPU node groups",
            "autoscaler_review": "the Karpenter NodePool limits, allowed instance families and consolidation policy (or, on Cluster Autoscaler, the expander and scale-down settings)"},
    "azure": {"commit": "reservations or savings plans", "gpu_note": "GPU node pools",
              "autoscaler_review": "the node pool autoscaler profile and the VM sizes allowed"},
    "unknown": {"commit": "committed-use discounts, Savings Plans or reservations", "gpu_note": "GPU node pools",
                "autoscaler_review": "the autoscaler configuration and the machine shapes it is allowed to choose"},
}
FW_WHERE = {
    "dagster": ("the op or job's Kubernetes configuration, set through the run launcher tags", "Dagster"),
    "airflow": ("the task's pod override (KubernetesPodOperator or executor config)", "Airflow"),
    "argo-workflows": ("the workflow template's container resources", "Argo Workflows"),
    "spark": ("the Spark executor and driver resource settings", "Spark"),
    "ray": ("the Ray cluster or job resource spec", "Ray"),
    "generic": ("the Job spec's resources in your manifests or Helm values", "batch"),
}


def effort(base, esc):
    i = TIERS.index(base)
    esc = esc[:2]
    if not esc:
        return base, f"{base} (catalog), no escalators"
    parts = [f"{base} (catalog)"] + [f"{TIERS[min(i + k + 1, 3)]}: {r}" for k, r in enumerate(esc)]
    return TIERS[min(i + len(esc), 3)], " -> ".join(parts)


class Plan:
    def __init__(self, cfg, facts, trends=None):
        self.cfg, self.F = cfg, facts
        self.by = {f["name"]: f for f in facts}
        self.trends = trends or {}
        self.A = []
        self.n = 0
        T = cfg["thresholds"]
        self.T = T
        self.cloud = self._cloud()
        self.CL = CLOUD.get(self.cloud, CLOUD["unknown"])
        gitops = (cfg["assumptions"] or {}).get("gitops")
        self.gitops = gitops or "your GitOps / pull-request flow"
        self.early = any(f["early"] for f in facts)
        self.has_elig = any(f["eligibility"].get("provided") for f in facts)
        self.total_idle = sum(f["pools"]["idle"] for f in facts)
        self.total_node_cost = sum(f["pools"]["cost"] for f in facts)
        self.pod_cost = sum(f["cost"] for f in facts)
        self.pod_waste = sum(f["waste"] for f in facts)
        self.pod_pot = sum(f["pot"] for f in facts)
        self.window = max(f["window_days"] for f in facts)
        self.claimed_pools = set()
        self.fw = collections.Counter()
        for f in facts:
            self.fw.update(f["frameworks"])
        self.primary_fw = (self.fw.most_common(1)[0][0] if self.fw else "generic")

    def _cloud(self):
        c = collections.Counter(f["cloud"] for f in self.F)
        return c.most_common(1)[0][0]

    # ------------------------------------------------------------------ helpers
    def add(self, key, **k):
        self.n += 1
        k.setdefault("depends_on", [])
        k.setdefault("unblocks", [])
        k.setdefault("owner_hint", [])
        k.setdefault("gated_on_automation", False)
        k.setdefault("lane", "config")
        k["key"], k["id"] = key, f"A{self.n}"
        k["fit"] = FIT.get(key, "Governance")
        k["value_kind"] = KIND.get(key, "computed")
        if "obs" not in k:
            k["obs"] = k.get("savings_monthly_usd", 0) / (self.F[0]["factor"] or 1) if k.get("savings_monthly_usd") else 0
        # Natural-key identity for a downstream diff tool to match actions across re-runs by what they
        # actually are, not by type+rank. `_uids` (cluster uid(s) in scope) and `_resource` (a resource
        # identifier narrower than "the whole cluster", e.g. a node group id) are transient: a rule passes
        # whatever identity it already has to hand, and `finish()` turns them into `natural_key` then pops them.
        k["_nk_uids"] = k.pop("_uids", None)
        k["_nk_resource"] = k.pop("_resource", None)
        self.A.append(k)
        return k

    def has(self, key):
        return any(a["key"] == key for a in self.A)

    def names(self, fs):
        return human_list([f["name"] for f in fs])

    def pick_proof(self):
        audited = [f for f in self.F if f["audit"]["n"] > 0]
        if audited:
            return max(audited, key=lambda f: f["audit"]["n"]), True
        rx = re.compile("|".join(self.cfg["nonprod_patterns"]), re.I) if self.cfg["nonprod_patterns"] else None
        np_ = [f for f in self.F if rx and rx.search(f["name"])]
        if np_:
            return max(np_, key=lambda f: f["lanes"]["controller"]["pot"]), False
        return min(self.F, key=lambda f: f["cost"]), False

    def cross_team(self, owners_lists):
        flat = {o for l in owners_lists for o in l if o}
        return len(flat) >= 2

    def pot_ctl(self, f):
        L = f["lanes"]["controller"]
        return (L["pot_eligible"], L["pot_eligible_o"]) if L["pot_eligible"] is not None else (L["pot"], L["pot_o"])

    # ------------------------------------------------------------------ rules
    def build(self):
        for rule in (self.r_autoscaler_labels, self.r_pool_flex, self.r_replicated_grouping, self.r_automation,
                     self.r_memory_limits, self.r_oom, self.r_jvm,
                     self.r_grouping, self.r_stale_optout, self.r_pdb, self.r_unevictable_affinity,
                     self.r_job_rightsize, self.r_stateful, self.r_pool_actions, self.r_autoprov, self.r_gpu, self.r_underprov,
                     self.r_headroom, self.r_local_storage, self.r_governance):
            rule()
        return self.finish()

    # ---- automation waves
    def r_automation(self):
        proof, proven = self.pick_proof()
        with_pot = [f for f in self.F if self.pot_ctl(f)[0] > 0]
        if not with_pot:
            return
        elig_note = "" if self.has_elig else " (eligibility unconfirmed)"
        conf = "high" if self.has_elig else "medium"
        esc_unconf = [] if self.has_elig else ["raised one tier because the API cannot confirm per-workload automation status (inferred - cause not confirmed)"]
        held = []
        for f in self.F:
            if f["lanes"]["managed"]["n"]:
                held.append(f"{f['lanes']['managed']['n']} platform-managed add-ons ({usd(f['lanes']['managed']['pot'])}/mo)")
        stateful_n = sum(f["lanes"]["stateful"]["n"] for f in self.F)
        hold_txt = "Hold out the platform-managed add-ons (kube-system and similar - the platform reconciles those manifests, so any change is reverted)"
        if stateful_n:
            hold_txt += f" and the {stateful_n} stateful search and streaming workloads (see the manual right-sizing action)"
        hold_txt += "."
        if proven:
            a = proof["audit"]
            ptitle = f"Widen automation beyond {proof['name']}, where it has already run"
            pev = (f"Automation has already run on {proof['name']}: {a['n']} actions" + (f", {pct(a['inplace'], a['n'])} of them in-place resizes" if a["inplace"] else "") +
                   f". That is the proof cluster - the remaining work is widening it, not switching it on.")
        else:
            ptitle = f"Confirm automation is on, then enable it on {proof['name']}'s long-running controllers"
            pev = (f"The automation audit log is empty on every cluster in scope and the oldest has {self.window:.0f} days of history, so nothing has run yet. "
                   f"{proof['name']} is the lowest-risk place to prove the loop" + (" (non-production by name)" if re.search("|".join(self.cfg['nonprod_patterns']), proof['name'], re.I) else " (smallest by cost)") + ".")
        p_pot, p_obs = self.pot_ctl(proof)
        top = [c["name"] for c in proof["controllers"][:3]]
        pa = self.add("automation_proof", action_type="automation_enable_policy", title=ptitle, cluster=proof["name"],
                      scope=f"{proof['lanes']['controller']['n']} long-running controllers; managed add-ons held out",
                      savings_monthly_usd=p_pot, obs=p_obs, savings_basis="achievable_now", confidence=conf, lane="toggle", gated_on_automation=True,
                      effort_tier=effort("XS", esc_unconf)[0], effort_rationale=effort("XS", esc_unconf)[1],
                      change_risk="low", owner_role="ps-admin", phase=1, _uids=[proof["uid"]],
                      owner_hint=[], steps=[
                          f"Open the Automation page for {proof['name']}. If it offers settings, automation is available and this is a toggle; if it offers 'Request Access', that is the first thing to resolve.",
                          hold_txt,
                          f"Enable automation on two or three low-risk controllers first - {human_list(top)}." if top else "Enable automation on two or three low-risk controllers first.",
                          "Let one full optimization window run, then widen to the rest of the cluster's controllers.",
                          "Verify: a new entry appears in the automation audit log within 24h, and it shows in-place resizes rather than evictions."],
                      verification="New automation audit-log entries appear within 24h; in-place resizes, not evictions",
                      evidence=pev + f" {usd(p_pot)}/mo of its computed savings is on long-running controllers" + (f"; a further {usd(proof['lanes']['managed']['pot'])}/mo sits on platform-managed add-ons that are not claimed." if proof["lanes"]["managed"]["pot"] >= 1 else "."))
        rest = sorted([f for f in with_pot if f["name"] != proof["name"]], key=lambda f: -self.pot_ctl(f)[0])
        if rest:
            tot = sum(self.pot_ctl(f)[0] for f in rest)
            w2, acc = [], 0
            for f in rest:
                if len(w2) >= 2 or (acc >= 0.7 * tot and w2):
                    break
                w2.append(f); acc += self.pot_ctl(f)[0]
            w3 = [f for f in rest if f not in w2]
            prev = "automation_proof"
            for label, grp, ph, key in (("Wave 2", w2, 2, "automation_w2"), ("Wave 3", w3, 2, "automation_w3")):
                if not grp:
                    continue
                pot = sum(self.pot_ctl(f)[0] for f in grp)
                obs = sum(self.pot_ctl(f)[1] for f in grp)
                nctl = sum(f["lanes"]["controller"]["n"] for f in grp)
                multi = len(grp) > 1
                esc = list(esc_unconf)
                if key == "automation_w3" and len(grp) > 2:
                    esc.append(f"{len(grp)} separate cluster policies with a soak period between them")
                if self.cfg["intake"].get("change_freeze") and any(re.search("|".join(self.cfg["nonprod_patterns"]), f["name"], re.I) is None for f in grp):
                    esc.append("change-managed - a change freeze or approval lead time was reported at intake for a production cluster in this wave")
                t, r = effort("XS", esc)
                prio = ", ".join(f"{f['name']} ({usd(self.pot_ctl(f)[0])}/mo)" for f in grp)
                # Catalog dependency edge: custom_grouping -> automation_enable_policy. Group first so
                # the repeated copies are one policy decision instead of thousands. The proof action is
                # deliberately left free of it: proving the loop on a few controllers needs no grouping.
                wave_deps = [prev] + (["replicated_grouping"] if self.has("replicated_grouping") else [])
                group_step = ("Apply the custom grouping rule first, so the repeated workload copies arrive as one policy decision rather than thousands."
                              if self.has("replicated_grouping") else None)
                self.add(key, action_type="automation_enable_policy", title=f"{label} - enable automation on {self.names(grp)}",
                         cluster=self.names(grp), scope=f"{nctl} long-running controllers across {len(grp)} cluster{'s' if multi else ''}; managed add-ons held out",
                         savings_monthly_usd=pot, obs=obs, savings_basis="achievable_now", confidence=conf, lane="toggle", gated_on_automation=True,
                         effort_tier=t, effort_rationale=r, change_risk="medium", owner_role="ps-admin", phase=ph, depends_on=wave_deps,
                         _uids=sorted({f["uid"] for f in grp}),
                         steps=[x for x in [f"Confirm {self.by[proof['name']]['name']} (or the previous wave) has run clean for at least 48 hours.",
                                group_step,
                                hold_txt,
                                f"Enable in descending order of value: {prio}.",
                                "Watch the execution mode on the first window. Expect in-place resizes; if you see evictions instead, pause and re-check.",
                                "Verify: pod waste falls on the enabled controllers and the audit log shows in-place resizes."] if x],
                         verification="Pod waste falls on enabled controllers; audit log shows in-place resizes",
                         evidence=f"{usd(pot)}/mo of computed savings on long-running controllers across {self.names(grp)}, "
                                  f"{pct(pot, self.pod_pot)} of everything PerfectScale found in scope" +
                                  (" - small beside the job opportunity, which is why automation is not the main lever on this tenant." if self.job_pot_total() > pot * 2 else "."))
                prev = key

    def job_pot_total(self):
        return sum(f["lanes"]["job"]["pot"] for f in self.F)

    # ---- memory limits
    def r_memory_limits(self):
        fs = [f for f in self.F if f["memlimit_sev2_n"] > 0]
        if not fs:
            return
        n = sum(f["memlimit_sev2_n"] for f in fs)
        rows = [dict(r, cluster=f["name"]) for f in fs for r in f["memlimit_sev2"]]
        rows.sort(key=lambda r: -r["waste"])
        jobs = sum(1 for r in rows if r["type"] in ("GroupedJob", "GroupedPod", "Job", "CronJob"))
        job_led = jobs >= len(rows) / 2
        where, fwname = FW_WHERE.get(self.primary_fw, FW_WHERE["generic"])
        owners = [f["lanes"]["job" if job_led else "controller"]["owners"] for f in fs]
        t, r = effort("S", ["cross-team - the limits are owned by several teams"] if self.cross_team(owners) else [])
        self.add("memory_limits", action_type="memory_limit_set", title=f"Set memory limits on the {n} flagged workloads",
                 cluster=self.names(fs), scope=f"{n} workloads with a high-severity missing memory limit",
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high", effort_tier=t, effort_rationale=r, change_risk="medium",
                 owner_role="app-team", phase=1, lane="config", owner_hint=sorted({o for l in owners for o in l})[:4],
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the count: {n} workloads report no memory limit at high severity. The largest by waste are {human_list([short_name(x['name']) for x in rows[:4]])}.",
                        (f"For {fwname} workers the limit is set in {where}, not in a Deployment manifest. Find where that is defined and change it in Git through {self.gitops}."
                         if job_led else f"Set the limits in the owning manifests or Helm values through {self.gitops}."),
                        "Size the limit from observed peaks plus margin, not from the request. Several of these workloads already show memory peaks far above their request.",
                        "Verify: the missing-limit indicator clears and no new OOM indicators appear within 7 days."],
                 verification="MemLimitNotSet indicator clears; no new OOM indicators in 7 days",
                 evidence="Without a memory limit PerfectScale cannot act on the memory dimension of these workloads, so any right-sizing that follows is CPU-only until this is closed.")

    # ---- OOM
    def r_oom(self):
        fs = [f for f in self.F if f["res_oom"]["n"] > 0]
        if not fs:
            return
        lit = sum(f["res_oom"]["literal"] for f in fs); lik = sum(f["res_oom"]["likely"] for f in fs); low = sum(f["res_oom"]["low"] for f in fs)
        lik_o = sum(f["res_oom"]["likely_o"] for f in fs)
        n = sum(f["res_oom"]["n"] for f in fs)
        lead = max(fs, key=lambda f: f["res_oom"]["literal"])
        top = lead["res_oom_list"][0]
        share = lead.get("res_oom_top_share", 0)
        job_led = lead["res_oom_list"][0]["name"] and (lead["lanes"]["job"]["n"] > 0 and any(
            r["type"] in ("GroupedJob", "GroupedPod", "Job") for r in lead["res_oom_list"][:3]))
        owners = [f["res_oom_owners"] for f in fs]
        t, r = effort("S", ["cross-team - the workloads belong to several teams"] if self.cross_team(owners) else [])
        steps = [f"Confirm it is still happening: PerfectScale flags OOM on {n} workloads. Start with {human_list([short_name(x['name']) for x in lead['res_oom_list'][:4]])}.",
                 f"Note the shape of the problem: {short_name(top['name'])} requests {top['mem_cur_gib']:.0f} GiB while observed memory reaches {top['mem_p100_gib']:.0f} GiB at peak ({top['mem_p95_gib']:.0f} GiB at p95). "
                 "A request this far below peak means the scheduler packs these pods as if they were small." + (" Grouped job data aggregates concurrent pods, so read these as the shape of the problem rather than a per-pod figure." if job_led else ""),
                 f"Size to p95 plus 20% margin first - that is the {usd(lik)}/mo 'likely' figure. PerfectScale's literal recommendation ({top['mem_rec_gib']:.0f} GiB for the largest) covers peak and is the {usd(lit)}/mo ceiling. Give work that truly needs peak memory a dedicated high-memory tag, then re-measure.",
                 "This is spend, not saving. It appears in the resiliency column and must not be netted against the savings elsewhere in this plan.",
                 "Verify: the OOM indicator clears and no memory-attributed restarts appear within 7 days."]
        self.add("oom_fix", action_type="oom_remediate", title="Raise memory requests on the workloads being OOM-killed",
                 cluster=self.names(fs), scope=f"{n} workloads flagged for OOM; largest is {short_name(top['name'])}",
                 savings_monthly_usd=lik, obs=lik_o, savings_basis="resiliency_investment", confidence="medium", effort_tier=t, effort_rationale=r,
                 change_risk="critical", owner_role="app-team", phase=1, lane="config", owner_hint=sorted({o for l in owners for o in l})[:4],
                 _uids=sorted({f["uid"] for f in fs}),
                 range={"low": low, "likely": lik, "literal": lit}, steps=steps,
                 verification="OOM indicators clear; no memory-attributed restarts in 7 days",
                 evidence=(f"OOM is flagged on {n} workloads. " + (f"One group ({short_name(top['name'])}) is {share * 100:.0f}% of the literal figure, which is why the literal number is a ceiling to size down from, not a budget request. " if share > 0.5 else "") +
                           f"Range: {usd(low)} low, {usd(lik)} likely, {usd(lit)} literal per month. Only critical-severity item in the plan, so it sits in Phase 1 regardless of cost."))

    # ---- grouping
    def r_grouping(self):
        fs = [f for f in self.F if f["grouping"]["recommend"]]
        if not fs:
            return
        tmpl = next((f for f in self.F if f["grouping"]["grouped_n"] > 0), None)
        n = sum(f["grouping"]["plain_job_n"] for f in fs)
        waste = sum(f["grouping"]["plain_job_waste"] for f in fs)
        pre = collections.Counter()
        for f in fs:
            pre.update(f["grouping"]["prefixes"])
        pr = sum(f["pr_env_labels"] for f in fs)
        where_txt = (f"Copy {tmpl['name']}'s grouping filter (it already collapses the equivalent pods into {tmpl['grouping']['grouped_n']} grouped workloads)" if tmpl
                     else "Create a custom grouping rule on the job name pattern")
        ns_share = max((n_["waste"] for f in fs for n_ in f["namespaces"][:1]), default=0)
        self.add("job_grouping", action_type="custom_grouping", title=f"Group the one-off job workloads on {self.names(fs)}" + (f" the way {tmpl['name']} already does" if tmpl else ""),
                 cluster=self.names(fs), scope=f"{n:,} job workloads" + (f" across {max(f['grouping']['code_locations'] for f in fs)} code locations" if any(f['grouping']['code_locations'] for f in fs) else ""),
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high", effort_tier="S", effort_rationale="S (catalog), no escalators - a grouping rule in the PerfectScale console, applied in place",
                 change_risk="low", owner_role="ps-admin", phase=1, lane="toggle", _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the shape: {human_list([f'{v:,} {k}' for k, v in pre.most_common(3)])} workloads each appear individually" + (f"; {tmpl['name']} collapses the equivalent pods." if tmpl else "."),
                        where_txt + ", and strip any per-pull-request suffix so every environment of a code location lands in one group.",
                        "Let one full window accumulate under the new groups.",
                        "Verify: grouped job workloads appear and their potential savings reads above $0."],
                 verification="Grouped job workloads appear with non-zero potential savings",
                 evidence=f"{n:,} job workloads carry {usd(waste)}/mo of waste and currently produce {usd(sum(f['grouping']['plain_job_pot'] for f in fs))}/mo of potential savings, because each run is too short-lived to learn from." +
                          (f" {pr:,} workloads carry pull-request environment labels." if pr else "") + " No dollar claim: the figure appears once grouping lets PerfectScale compute it.")

    # ---- replicated controller grouping
    def r_replicated_grouping(self):
        """One controller name repeated across many namespaces is one decision, not N.

        Carries no dollars of its own. The savings already sit in the controller lane and are
        claimed by the automation waves, so attributing them here too would count the same
        dollar twice. Its value is the automation it makes tractable, which is what
        `is_unblocker` and the released figure express - and why the evidence states the
        covered amount in words rather than putting it in the money column.
        """
        fs = [f for f in self.F if f["replicated"]["n_groups"]]
        if not fs:
            return
        agg = {}
        for f in fs:
            for g in f["replicated"]["groups"]:
                a = agg.setdefault(g["name"], {"name": g["name"], "n": 0, "ns": 0, "pot": 0.0, "pot_o": 0.0,
                                               "clusters": [], "types": set(), "sample": g})
                a["n"] += g["n"]; a["ns"] += g["ns_count"]; a["pot"] += g["pot"]; a["pot_o"] += g["pot_o"]
                a["clusters"].append(f["name"]); a["types"].update(g["types"])
                if g["pot"] > a["sample"]["pot"]:
                    a["sample"] = g
        G = sorted(agg.values(), key=lambda g: -g["pot"])
        inst = sum(g["n"] for g in G)
        pot = sum(g["pot"] for g in G)
        ctl = sum(f["lanes"]["controller"]["pot"] for f in self.F)
        top = G[0]
        s = top["sample"]
        prefix = s["ns_prefix"]
        where = (f"a namespace filter on `{prefix}*` plus the workload name" if prefix
                 else "a filter on the workload name, since the namespaces share no common stem")
        # Dormant-and-bursty is the characteristic shape of a per-tenant copy, and the reason
        # grouping plus automation beats a single static cut: only one of the two re-sizes when
        # a tenant wakes up. Stated only when the sample actually shows it.
        bursty = s["cpu_p100"] > max(s["cpu_p95"] * 10, 0.5)
        steps = [f"Confirm the shape: `{top['name']}` runs in {top['ns']:,} namespaces across {human_list(sorted(set(top['clusters'])))} "
                 f"and carries {usd(top['pot'])}/mo of PerfectScale's computed savings - {pct(top['pot'], ctl)} of everything in the automation lane.",
                 f"Create a custom grouping rule in the PerfectScale console using {where}, so the copies collapse into one workload with one policy.",
                 "Check the collapsed group's recommendation against a few individual copies before relying on it: grouping is only safe where the copies really are the same shape."]
        if len(G) > 1:
            more = human_list([f"{g['name']} ({g['ns']:,} namespaces)" for g in G[1:4]])
            steps.append(f"Repeat for the remaining candidates: {more}.")
        steps.append(f"Let one full window accumulate under the new group{'s' if len(G) > 1 else ''}.")
        steps.append("Verify: the grouped workload appears with a single recommendation and the individual copies no longer each carry their own.")
        ev = (f"{inst:,} workload copies share {len(G)} name{'s' if len(G) > 1 else ''} across {sum(g['ns'] for g in G):,} namespaces, carrying {usd(pot)}/mo "
              f"of computed savings - {pct(pot, ctl)} of the automation lane. No dollar claim of its own: that money is already counted in the automation actions. "
              f"What grouping changes is the number of decisions it takes to capture it, from {inst:,} to {len(G)}.")
        if bursty:
            ev += (f" The copies are mostly dormant and burst when used - the largest reaches {s['cpu_p100']:.1f} cores against {s['cpu_p95']:.2f} at p95"
                   + (" and already shows CPU throttling" if s["throttled"] else "") +
                   " - so automation with in-place resizing fits them better than one static cut (inferred - cause not confirmed).")
        t, r = effort("S", [])
        self.add("replicated_grouping", action_type="custom_grouping",
                 title=f"Group the {top['ns']:,} copies of `{top['name']}` into one policy" + (f", and {len(G) - 1} more repeated workload{'s' if len(G) > 2 else ''}" if len(G) > 1 else ""),
                 cluster=self.names(fs),
                 scope=(f"{inst:,} copies of {len(G)} repeated workload names across {sum(g['ns'] for g in G):,} namespaces" if len(G) > 1
                        else f"{inst:,} copies of `{top['name']}` across {top['ns']:,} namespaces"),
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high", effort_tier=t,
                 effort_rationale=r + " - a grouping rule in the PerfectScale console, applied in place",
                 change_risk="low", owner_role="ps-csm", phase=1, lane="toggle", is_unblocker=True,
                 _uids=sorted({f["uid"] for f in fs}), _resource=top["name"],
                 steps=steps,
                 verification="The grouped workload carries one recommendation; the individual copies no longer each carry their own",
                 evidence=ev)

    # ---- stale opt-out
    def r_stale_optout(self):
        fs = [f for f in self.F if f["optout"]["controller_pods"] > 0]
        if not fs:
            return
        pods = sum(f["optout"]["controller_pods"] for f in fs)
        names = set(); pools = []
        for f in fs:
            names |= set(f["optout"]["controller_names"]); pools += [(f["name"], p) for p in f["optout"]["pools"]]
        det = (fs[0]["optout"]["annotation"] or "")
        karp = "karpenter" in det.lower()
        ann = "karpenter.sh/do-not-disrupt" if karp else "cluster-autoscaler.kubernetes.io/safe-to-evict"
        ann_val = '"true"' if karp else '"false"'
        owners = [f["lanes"]["controller"]["owners"] for f in fs]
        t, r = effort("S", [])
        self.add("stale_optout", action_type="unevictable_karpenter_optout", title="Remove stale do-not-evict annotations from long-running controllers",
                 cluster=self.names(fs), scope=f"{pods} pods across {len(names)} controllers on long-lived node pools",
                 savings_monthly_usd=0, savings_basis="blocked_pending", confidence="high", effort_tier=t,
                 effort_rationale=r + "; applying it rolls the affected pods once, like any normal deploy", change_risk="low", owner_role="app-team", phase=2, lane="config",
                 owner_hint=sorted({o for l in owners for o in l})[:3], _pools=pools, _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the blockers: {pods} running controller pods carry `{ann}: {ann_val}` and sit on long-lived pools. PerfectScale rates removal low risk, high confidence.",
                        f"Find where the annotation is set - usually one shared Helm value or base manifest managed through {self.gitops}.",
                        "Remove it for long-running controllers. Leave it on running batch jobs: evicting those kills the run.",
                        "Apply on the lowest-risk cluster first, in a quiet window, and watch node count for 48 hours before the rest.",
                        "Verify: the do-not-evict blocker count on the affected pools falls and node count begins tracking demand."],
                 verification="Do-not-evict annotation count on long-lived pools falls; node count tracks demand",
                 evidence=f"{pods} controller pods on long-lived pools carry a do-not-evict annotation (running jobs, which should keep it, are not counted). This does not save money by itself - it is what lets the node-pool actions below remove nodes.")

    # ---- PDB
    def r_pdb(self):
        fs = [f for f in self.F if f["unevictable"]["codes"].get("pod_disruption_budget", {}).get("all", 0) > 0]
        if not fs:
            return
        n = sum(f["unevictable"]["codes"]["pod_disruption_budget"]["all"] for f in fs)
        ns = collections.Counter()
        for f in fs:
            ns.update(f["unevictable"]["codes"]["pod_disruption_budget"]["ns"])
        t, r = effort("S", ["cross-team - the affected pods sit in namespaces owned by several teams"] if len(ns) >= 2 else [])
        self.add("pdb", action_type="unevictable_pdb", title="Relax over-restrictive PodDisruptionBudgets blocking node drain",
                 cluster=self.names(fs), scope=f"{n} pods in {len(ns)} namespaces", savings_monthly_usd=0, savings_basis="blocked_pending",
                 confidence="high", effort_tier=t, effort_rationale=r, change_risk="medium", owner_role="app-team", phase=1, lane="config",
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the blockers: {n} pods report a PodDisruptionBudget as the reason node drain is blocked, mostly in {human_list(list(ns)[:3])}.",
                        "For each budget, check whether minAvailable equals the replica count. That is the common failure and it means no pod may ever be evicted.",
                        "Set minAvailable to replicas-1, or use maxUnavailable: 1, so the autoscaler has room to work while availability is preserved.",
                        "Leave any budget protecting a single-replica stateful service alone - fix the replica count first or accept the pin.",
                        "Verify: the PodDisruptionBudget blocker count falls toward zero on the affected pools."],
                 verification="Unevictable pod count with reason PodDisruptionBudget falls toward zero",
                 evidence=f"{n} pods cannot be drained because of their disruption budget. This does not save money by itself - it is what allows node consolidation to actually execute.")

    # ---- strict pod / node affinity
    def r_unevictable_affinity(self):
        """Strict anti-affinity and strict node affinity both block drain the same way a PDB does. The
        catalog has one type for this (unevictable_antiaffinity) - there is no separate node-affinity entry,
        so both reason codes are scored and reported under it, and the evidence says so explicitly."""
        def cnt(f, code):
            return f["unevictable"]["codes"].get(code, {}).get("all", 0)
        fs = [f for f in self.F if cnt(f, "anti_affinity") + cnt(f, "node_affinity") > 0]
        if not fs:
            return
        n_anti = sum(cnt(f, "anti_affinity") for f in fs)
        n_node = sum(cnt(f, "node_affinity") for f in fs)
        n = n_anti + n_node
        ns = collections.Counter()
        for f in fs:
            for code in ("anti_affinity", "node_affinity"):
                ns.update(f["unevictable"]["codes"].get(code, {}).get("ns", {}))
        which = human_list([x for x in ([f"{n_anti} behind a strict anti-affinity rule"] if n_anti else []) +
                             ([f"{n_node} behind a strict node affinity rule"] if n_node else [])])
        t, r = effort("M", ["cross-team - the affected pods sit in namespaces owned by several teams"] if len(ns) >= 2 else [])
        self.add("unevictable_affinity", action_type="unevictable_antiaffinity", title="Relax strict pod affinity rules blocking node drain",
                 cluster=self.names(fs), scope=f"{n} pods in {len(ns)} namespaces ({which})", savings_monthly_usd=0, savings_basis="blocked_pending",
                 confidence="high", effort_tier=t, effort_rationale=r, change_risk="medium", owner_role="app-team", phase=2, lane="config",
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the blockers: {n} pods report a strict pod or node affinity rule as the reason node drain is blocked, mostly in {human_list(list(ns)[:3])}.",
                        "For anti-affinity, check whether `requiredDuringSchedulingIgnoredDuringExecution` is doing HA work that `preferredDuringSchedulingIgnoredDuringExecution` would do just as well, or widen the topology key.",
                        "For node affinity, check whether the required node selector can be loosened, or whether a correctly-sized dedicated pool is the better fix.",
                        "Get sign-off from the owning team before relaxing either rule - anti-affinity in particular is usually a deliberate HA choice, so this is visibility plus a decision, not a unilateral change.",
                        "Verify: the anti-affinity / node-affinity blocker count on the affected pools falls."],
                 verification="Unevictable pod count with reason anti-affinity or node affinity falls",
                 evidence=f"{n} pods cannot be drained because of a strict pod or node affinity rule ({which}). This does not save money by itself - it is what allows node consolidation to actually execute. "
                          "The catalog defines one action type for strict affinity (unevictable_antiaffinity); node affinity has no separate entry, so both reason codes are carried under it here.")

    # ---- jobs (config lane)
    def r_job_rightsize(self):
        fs = [f for f in self.F if f["lanes"]["job"]["pot"] > 0.5]
        if not fs:
            return
        pot = sum(f["lanes"]["job"]["pot"] for f in fs); obs = sum(f["lanes"]["job"]["pot_o"] for f in fs)
        n = sum(f["lanes"]["job"]["n"] for f in fs)
        top = sorted([dict(r, cluster=f["name"]) for f in fs for r in f["lanes"]["job"]["top"]], key=lambda r: -r["pot"])
        top8 = sum(r["pot"] for r in top[:8])
        where, fwname = FW_WHERE.get(self.primary_fw, FW_WHERE["generic"])
        owners = [f["lanes"]["job"]["owners"] for f in fs]
        t, r = effort("S", ["cross-team - per-job resource settings live with several teams (inferred - cause not confirmed)"] if self.cross_team(owners) else [])
        deps = [k for k in ("oom_fix", "memory_limits") if self.has(k)]
        waste = sum(f["lanes"]["job"]["waste"] for f in fs)
        steps = [f"Confirm the set: {n} job workloads carrying {usd(pot)}/mo of computed savings, {pct(pot, self.pod_pot)} of everything PerfectScale found in scope.",
                 "Work largest first - " + "; ".join(f"{short_name(x['name'])} ({x['cpu_cur']:.0f} to {x['cpu_rec']:.1f} cores, {usd(x['pot'])}/mo)" for x in top[:4]) + ".",
                 f"Change one job's resource settings at a time ({where}) through {self.gitops} and watch one full run cycle before the next."]
        if deps:
            steps.append("Finish the memory actions first: shrinking CPU on a group while its memory is still under-provisioned just moves the problem.")
        steps.append("Verify: potential savings on the group falls, with no new OOM or throttling indicators after two run cycles.")
        self.add("job_rightsize", action_type="rightsize_manual", title=f"Apply PerfectScale's {fwname} job recommendations in the run configuration, largest groups first",
                 cluster=self.names(fs), scope=f"{n} job workloads; the top 8 groups hold {pct(top8, pot)} of the opportunity",
                 savings_monthly_usd=pot, obs=obs, savings_basis="achievable_now", confidence="medium", effort_tier=effort("S", ["cross-team - per-job resource settings live with several teams (inferred - cause not confirmed)"] if self.cross_team(owners) else [])[0],
                 effort_rationale=r if not self.cross_team(owners) else effort("S", ["cross-team - per-job resource settings live with several teams (inferred - cause not confirmed)"])[1],
                 change_risk="medium", owner_role="app-team", phase=2, depends_on=deps, lane="config",
                 owner_hint=sorted({o for l in owners for o in l})[:4], steps=steps, _uids=sorted({f["uid"] for f in fs}),
                 verification="Potential savings on changed groups falls; no new OOM or throttling indicators",
                 evidence=f"Job pods are {n} workloads and {usd(waste)}/mo of waste. They live for minutes, so the durable fix is in the run configuration rather than a controller toggle. "
                          f"Medium confidence because the figure comes from about {self.window:.0f} days of batch history and load can vary with delivery cycles.")

    # ---- stateful / held-out manual right-sizing
    def r_stateful(self):
        # The stateful lane is genuinely stateful (Kafka/Strimzi, search, a database) and floors at the
        # catalog's rightsize_stateful (M, high risk). The held-out lane is whatever the customer asked to
        # keep out of automation - often not stateful at all - and belongs to the lighter rightsize_manual
        # (S, medium risk). Conflating the two inflates effort and depresses ROI on the held-out set for no reason.
        for lane, key, title, why, atype, floor, risk in (
                ("stateful", "stateful_manual", "Right-size stateful search and streaming workloads by hand", "stateful data path - these stay out of automation", "rightsize_stateful", "M", "high"),
                ("held_out", "heldout_manual", "Right-size the workloads you asked to keep out of automation, by hand", "held out of automation at your instruction", "rightsize_manual", "S", "medium")):
            fs = [f for f in self.F if f["lanes"][lane]["pot"] > 0.5]
            if not fs:
                continue
            pot = sum(f["lanes"][lane]["pot"] for f in fs); obs = sum(f["lanes"][lane]["pot_o"] for f in fs)
            n = sum(f["lanes"][lane]["n"] for f in fs)
            nm = human_list(sorted({x for f in fs for x in f["stateful"]["names"]})[:4]) if lane == "stateful" else "the held-out set"
            t, r = effort(floor, [])
            self.add(key, action_type=atype, title=title, cluster=self.names(fs), scope=f"{n} workloads ({nm})",
                     savings_monthly_usd=pot, obs=obs, savings_basis="achievable_now", confidence="high", effort_tier=t, effort_rationale=r + f" - {why}",
                     change_risk=risk, owner_role="app-team", phase=2, lane="manual", owner_hint=sorted({o for f in fs for o in f["lanes"][lane]["owners"]})[:4],
                     _uids=sorted({f["uid"] for f in fs}),
                     steps=[f"Confirm the set: {n} workloads carrying {usd(pot)}/mo of PerfectScale recommendations.",
                            "Every change here is a manifest or Helm values change reviewed by the owning team; nothing is applied automatically.",
                            "Start with the largest on the biggest clusters, one tier at a time, and let each settle before the next.",
                            "Heap sizing usually tracks the memory request, so check that JVM settings move with it.",
                            "Verify: pod waste falls on these workloads with no new risk indicators within 14 days."],
                     verification="Pod waste falls; no new risk indicators in 14 days",
                     evidence=f"{usd(pot)}/mo ({pct(pot, self.pod_pot)} of the opportunity) sits on workloads kept out of automation. The money is still reachable - it costs manual effort instead of a toggle.")


    # ---- node pools that never flex, and autoscalers we cannot see
    def r_autoscaler_labels(self):
        """Pools reporting an unknown autoscaler are a visibility defect on the
        customer's side, and they gate everything InfraFit can conclude."""
        fs = [f for f in self.F if f["pools"]["unknown_autoscaler_n"]]
        if not fs:
            return
        n = sum(f["pools"]["unknown_autoscaler_n"] for f in fs)
        tot = sum(f["pools"]["n"] for f in self.F)
        cost = sum(f["pools"]["unknown_autoscaler_cost"] for f in fs)
        share = n / tot if tot else 0
        t, r = effort("XS", [])
        self.add("autoscaler_labels", action_type="other",
                 rationale="Not a PodFit/InfraFit finding from the catalog: the autoscaler type is unreadable, which is a data-visibility "
                           "defect on the customer's collection setup, not a capacity or right-sizing action. Scored like a labelling fix "
                           "(XS, platform-owned) because that is the actual remediation.",
                 title=f"Label the {n} node pools reporting no autoscaler",
                 cluster=self.names(fs), scope=f"{n} of {tot} pools, carrying {usd(cost)}/mo of node cost",
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high",
                 effort_tier=t, effort_rationale=r + " - a labelling fix, not a capacity change",
                 change_risk="low", owner_role="platform", phase=1, lane="config", is_unblocker=True,
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the gap: {n} of {tot} node pools report their autoscaler as unknown, covering {usd(cost)}/mo of node spend.",
                        "This is a discovery problem, not a capacity one: without the autoscaler type PerfectScale cannot tell whether a pool is able to consolidate, so idle on these pools cannot be acted on with confidence.",
                        "Check that the cluster's autoscaler is deployed with the labels PerfectScale reads, and that the agent can see the node group or NodePool objects.",
                        f"Re-check after the next collection: {CLOUD.get(self.cloud, CLOUD['unknown'])['autoscaler_review']} becomes reviewable once the type is known.",
                        "Verify: the unknown-autoscaler pool count falls to zero."],
                 verification="Pools reporting autoscaler 'unknown' falls to zero",
                 evidence=(f"{n} of {tot} pools ({pct(n, tot)}) report no autoscaler type, across {usd(cost)}/mo of node spend. "
                           "Every InfraFit conclusion about whether a pool can shrink is weaker until this is fixed. "
                           "High priority because it is cheap to fix and it gates a large share of the node-side opportunity."))

    def r_pool_flex(self):
        """Right-sizing pods on a pool whose node count never moves frees
        headroom, not money. Name those pools before claiming the saving."""
        fs = [f for f in self.F if f["pools"]["pinned_n"]]
        if not fs:
            return
        n = sum(f["pools"]["pinned_n"] for f in fs)
        tot = sum(f["pools"]["n"] for f in self.F)
        idle = sum(f["pools"]["pinned_idle"] for f in fs)
        if idle < self.T["pool_idle_min_usd"]:
            return
        worst = sorted([p for f in fs for p in f["pools"]["pinned"]], key=lambda p: -p["idle"])[:4]
        where = human_list([f"`{p['id']}` ({p['nodes_min']:.0f} nodes, {usd(p['idle'])}/mo idle)" for p in worst])
        t, r = effort("S", [])
        atype = "autoscaler_tune_karpenter" if self.cloud == "aws" else "autoscaler_tune_ca"
        self.add("pool_flex", action_type=atype,
                 title=f"Confirm whether {n} never-flexing node pools are allowed to consolidate",
                 cluster=self.names(fs), scope=f"{n} of {tot} pools holding {usd(idle)}/mo of idle",
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high",
                 effort_tier=t, effort_rationale=r + " - a configuration question, answered before any resizing (below the catalog's M floor for the tuning action itself, which this precedes)",
                 change_risk="low", owner_role="platform", phase=1, lane="config", is_unblocker=True,
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the observation: {n} pools held the same node count for the whole window - {where}.",
                        "Check each one's minimum size and its consolidation setting. A pool pinned at its minimum, or set to consolidate only when a node is completely empty, will not give a node back when pods shrink.",
                        "Where the pin is deliberate (licensing, quorum, warm capacity), record it and stop claiming workload savings against that pool.",
                        "Where it is not, lower the minimum or allow consolidation on underutilised nodes, then re-measure.",
                        "Verify: node counts on these pools vary with load in the next collection."],
                 verification="Pinned pools show a node count that moves with load",
                 evidence=(f"{n} of {tot} pools never changed node count over the observed window and hold {usd(idle)}/mo of idle "
                           f"({pct(idle, self.total_idle)} of all idle). Right-sizing the pods on them frees headroom but no nodes, so the saving "
                           "does not reach the invoice until the pool can shed one. Observed from node counts, not read from the pool's "
                           "configuration - the public API does not expose a consolidation policy, so the cause is inferred - cause not confirmed."))

    # ---- managed-runtime workloads
    def r_jvm(self):
        """Workloads the customer has declared as JVM-managed: same owners, same
        lane, different change procedure."""
        fs = [f for f in self.F if f.get("jvm", {}).get("n")]
        if not fs:
            return
        n = sum(f["jvm"]["n"] for f in fs)
        pot = sum(f["jvm"]["pot"] for f in fs)
        inaut = sum(f["jvm"]["pot_in_automation"] for f in fs)
        oom = sum(f["jvm"]["oom"] for f in fs)
        names = human_list(sorted({x for f in fs for x in f["jvm"]["names"]})[:4])
        t, r = effort("M", ["raised one tier because the runtime sets its own memory, so each change is two changes"])
        self.add("jvm_rightsize", action_type="rightsize_runtime_managed",
                 title=f"Right-size the {n} managed-runtime workloads with their heap settings, not alone",
                 cluster=self.names(fs), scope=f"{n} workloads ({names})",
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="medium",
                 effort_tier=t, effort_rationale=r, change_risk="high", owner_role="app-team", phase=2, lane="manual",
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the set: {n} workloads you identified as running on a managed runtime, carrying {usd(pot)}/mo of PerfectScale recommendations"
                        + (f", of which {usd(inaut)}/mo sits in the automation ask" if inaut else "") + ".",
                        "Lower the memory request and the runtime's own heap setting in the same change. A container limit the runtime does not read is not a limit it will respect, and a request cut on its own ends in an OOMKill rather than a saving.",
                        "Prefer a percentage-of-container setting over a fixed heap size, so the two cannot drift apart again.",
                        "Treat CPU separately and gently: these runtimes compile as they warm up, so an aggressive trim shows up as slow starts and deploy-time latency, not as steady-state error.",
                        "Give each change a longer settling window than the rest of the plan before judging it."],
                 verification="Waste falls on these workloads with no new OOM or restart indicators after 14 days",
                 evidence=(f"{n} workloads ({usd(pot)}/mo of recommendations" + (f", {oom} already OOM-flagged" if oom else "") + ") run on a runtime that "
                           "manages its own memory. The dollar figure is deliberately not claimed here: it is already counted in the lane each workload "
                           "belongs to, and this action changes how those changes are made rather than adding new savings. "
                           "Membership is declared by you, not inferred - the API exposes no runtime or image."))

    # ---- node pools
    def r_pool_actions(self):
        """One action per node group, not one pooled tenant-wide action. `by_pool` already carries
        each node group's own blocker-code breakdown (model.py keys unevictable pods by their node group),
        so each pool action's `depends_on` is wired to the unevictable action(s) that actually touch *that*
        node group - the catalog's dependency-edges rule ('unevictable_* on a node group unblocks
        autoscaler_tune_*/nodegroup_consolidate on that group'), not a blanket edge to every blocker tenant-wide.
        The upstream unevictable_pdb/stale_optout/unevictable_antiaffinity actions themselves remain tenant-wide
        aggregates - model.py's reason-code grouping for those rules does not carry node-group identity forward
        into the action record, only `by_pool` (used here) does - so the edge is per-group but its target is not."""
        thr = self.T["pool_idle_min_usd"]
        rec_rows, blocked_rows, free_rows = [], [], []
        for f in self.F:
            by_pool = f["unevictable"]["by_pool"]
            for p in f["pools"]["long_lived"]:
                codes = by_pool.get(p["id"], {})
                n_blk = sum(codes.values())
                row = dict(p, cluster=f["name"], uid=f["uid"], blockers=n_blk, codes=codes, cloud=f["cloud"])
                if p["rec"] and p["rec"]["save"] >= thr / 4:
                    rec_rows.append(row)
                elif p["idle"] >= thr:
                    (blocked_rows if n_blk >= 5 else free_rows).append(row)
        opt = next((a for a in self.A if a["key"] == "stale_optout"), None)
        optpools = set(opt["_pools"]) if opt else set()

        def group_deps(row):
            codes = row.get("codes", {})
            deps = []
            if codes.get("pod_disruption_budget") and self.has("pdb"):
                deps.append("pdb")
            if codes.get("opt_out_annotation") and self.has("stale_optout"):
                deps.append("stale_optout")
            if (codes.get("anti_affinity") or codes.get("node_affinity")) and self.has("unevictable_affinity"):
                deps.append("unevictable_affinity")
            # local_storage is deliberately not wired in here: it is a Phase 3 migration (effort L), and a
            # Phase 2 pool action cannot depend on a later-phase prerequisite - see the phase invariant in finish().
            return deps

        blocked_rows.sort(key=lambda r: -r["idle"])
        for r in blocked_rows:
            self.add("pool_shrink_blocked", action_type="nodepool_shrink", title=f"Let the `{r['id']}` node pool shrink",
                     cluster=r["cluster"], scope=f"{r['cluster']} `{r['id']}` - {r['blockers']} blocking pods",
                     savings_monthly_usd=r["idle"], obs=r["idle_o"], savings_basis="blocked_pending", confidence="high", effort_tier="M",
                     effort_rationale="M (catalog), no escalators - a node pool change owned by the platform team" + ("; no freeze or approval lead time reported" if not self.cfg["intake"].get("change_freeze") else ""),
                     change_risk="high", owner_role="platform", phase=2, depends_on=group_deps(r), lane="platform", _uids=[r["uid"]], _resource=r["id"],
                     steps=[f"Confirm the picture: pods on `{r['id']}` request ~{r['cpu_req']:.0f} cores on average and use ~{r['cpu_used']:.0f}, on a pool that never drops below {r['nodes_min']:.0f} nodes of {r['machine']}.",
                            f"Finish the blocker actions on `{r['id']}` first. Of what remains, system pods (DNS, event exporters, static pods) are expected; leave those.",
                            "Right-size the requests on this pool, then lower the pool minimum or move to a smaller machine shape.",
                            f"Check {self.CL['autoscaler_review']} - these decide how fast empty nodes leave.",
                            "Verify: node count tracks demand and idle cost on this pool falls."],
                     verification="Node count tracks demand; idle $ on this pool falls",
                     evidence=f"{r['cluster']} `{r['id']}` carries {usd(r['idle'])}/mo of idle against {usd(r['cost'])}/mo of node cost, blocked by {r['blockers']} pods. Idle pool - the upper bound of what shrinking can reclaim - not a computed saving.")
            self.claimed_pools.add((r["cluster"], r["id"]))

        rec_rows.sort(key=lambda r: -r["rec"]["save"])
        for r in rec_rows:
            deps = ["stale_optout"] if opt and (r["cluster"], r["id"]) in optpools else []
            self.add("pool_machine_recs", action_type="nodepool_machine_type", title=f"Apply PerfectScale's machine-type recommendation on {r['cluster']} `{r['id']}`",
                     cluster=r["cluster"], scope=f"{r['cluster']} `{r['id']}` ({r['machine']} to {r['rec']['to']})",
                     savings_monthly_usd=r["rec"]["save"], obs=r["rec"]["save_o"], savings_basis="blocked_pending", confidence="high", effort_tier="S",
                     effort_rationale="S (catalog), no escalators - a machine-type field on existing pools",
                     change_risk="high", owner_role="platform", phase=2, depends_on=deps, lane="platform", _uids=[r["uid"]], _resource=r["id"],
                     steps=[f"Confirm the recommendation: {r['cluster']} `{r['id']}` {usd(r['rec']['save'])}/mo ({r['rec']['pct']:.0f}%).",
                            "Read the rationale attached to the recommendation in the InfraFit view before applying it, and confirm the target family fits the workloads on this pool.",
                            "Apply in a quiet window. A change that replaces nodes should land after the blocker actions on this node group.",
                            "Verify: idle cost falls on this pool after the change date."],
                     verification="Per-pool idle $ falls after the change date",
                     evidence=f"{r['cluster']} `{r['id']}`: {usd(r['rec']['save'])}/mo of estimated savings moving {r['machine']} to {r['rec']['to']} - PerfectScale's own estimate, scaled to a 30-day run-rate.")
            self.claimed_pools.add((r["cluster"], r["id"]))

        free_rows.sort(key=lambda r: -r["idle"])
        for r in free_rows:
            bursty = (r["nodes_max"] or 0) >= 3 * max(r["nodes_avg"] or 1, 1)
            self.add("pool_shrink_free", action_type="nodepool_shrink", title=f"Scale down idle long-lived node pool `{r['id']}`",
                     cluster=r["cluster"], scope=f"{r['cluster']} `{r['id']}`", savings_monthly_usd=r["idle"], obs=r["idle_o"], savings_basis="blocked_pending",
                     confidence="medium", effort_tier="S", effort_rationale="S (catalog), no escalators - pool minimum and maximum settings",
                     change_risk="medium", owner_role="platform", phase=2, depends_on=group_deps(r), lane="platform", _uids=[r["uid"]], _resource=r["id"],
                     steps=[f"Confirm the usage: `{r['id']}` pods request ~{r['cpu_req']:.0f} cores and use ~{r['cpu_used']:.1f}.",
                            (f"Check what this pool is for. It scales up in bursts (peaks of {r['nodes_max']:.0f} nodes) and sits idle between them, so the question is how quickly empty nodes leave and what the floor should be." if bursty else "Check the floor this pool is held at."),
                            "Lower the minimum node count and tighten the scale-down delay.",
                            "Verify: node count falls back to the floor between bursts and idle cost falls."],
                     verification="Node count returns to the lowered floor between bursts; idle $ falls",
                     evidence=f"{r['cluster']} `{r['id']}`: {usd(r['idle'])}/mo of idle with {r['blockers']} blocking pod{'s' if r['blockers'] != 1 else ''} and no machine-type recommendation. Idle pool, not a computed saving. Medium confidence: a bursty pool makes a short window over- or under-state it.")
            self.claimed_pools.add((r["cluster"], r["id"]))

    # ---- auto-provisioned pools
    def r_autoprov(self):
        fs = [f for f in self.F if f["pools"]["ap_n"] > 0]
        ap_idle = sum(f["pools"]["ap_idle"] for f in fs)
        if not fs or ap_idle < max(0.1 * self.total_idle, 500):
            return
        lead = max(fs, key=lambda f: f["pools"]["ap_idle"])
        infl = sum(f["shapes_inflated_idle_share"] * f["pools"]["ap_idle"] for f in fs) / ap_idle
        shapes = lead["shapes"][:3]
        med = lead["pools"]["ap_median_wall_min"]
        settings = [self.cfg["cluster_settings"].get(f["name"], {}) for f in fs]
        prof = next((s.get("autoscaling_profile") for s in settings if s.get("autoscaling_profile")), None)
        deps = []
        tot = {k: sum(f["ap_totals"][k] for f in fs) for k in ("cpu_req", "cpu_used", "mem_req", "mem_used")}
        cr = tot["cpu_req"] / tot["cpu_used"] if tot["cpu_used"] else 0
        mr = tot["mem_req"] / tot["mem_used"] if tot["mem_used"] else 0
        jr = {k: sum(f["job_req_vs_rec"][k] for f in fs) for k in ("cpu_cur", "cpu_rec", "mem_cur_gib", "mem_rec_gib")}
        recs_up = jr["cpu_rec"] > jr["cpu_cur"] or jr["mem_rec_gib"] > jr["mem_cur_gib"]
        cause = (f"Measured: on these pools, pods request on average {cr:.1f}x the CPU and {mr:.1f}x the memory they use (time-averaged), and {pct(infl, 1)} of the idle sits on shapes where CPU requests are 5x or more of use. "
                 + (f"Not shown by the data: that lowering requests would shrink the shapes. PerfectScale's own recommendations for job workloads (grouped and ungrouped), taken together, would move CPU from {jr['cpu_cur']:,.0f} to {jr['cpu_rec']:,.0f} cores and memory from {jr['mem_cur_gib']:,.0f} to {jr['mem_rec_gib']:,.0f} GiB, because they protect peaks. "
                    "Inferred, not confirmed: nodes are sized for job peaks and hold far less most of the time." if recs_up else
                    f"If requests were lowered to PerfectScale's recommendations, CPU would move from {jr['cpu_cur']:,.0f} to {jr['cpu_rec']:,.0f} cores and memory from {jr['mem_cur_gib']:,.0f} to {jr['mem_rec_gib']:,.0f} GiB, so smaller shapes are plausible (inferred, not confirmed)."))
        self.add("autoprov_review", action_type="autoprov_review", title="Review node auto-provisioning settings for the batch platform",
                 cluster=self.names(fs), scope=f"{sum(f['pools']['ap_n'] for f in fs):,} short-lived auto-provisioned pools",
                 savings_monthly_usd=0, savings_basis="blocked_pending", confidence="medium", effort_tier="M", effort_rationale="M (catalog), no escalators - cluster-level autoscaler configuration owned by the platform team",
                 change_risk="high", owner_role="platform", phase=2, depends_on=deps, lane="platform", _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the shape of the problem: auto-provisioned pools carry {usd(ap_idle)}/mo of idle, {pct(ap_idle, self.total_idle)} of all idle in scope. The median pool runs about {med:.0f} minutes of wall-clock time.",
                        "Look at the machine shapes chosen: " + ", ".join(f"{s['machine']} ({s['groups']} pools, {usd(s['idle'])}/mo)" for s in shapes) + f" lead on {lead['name']}. " + cause,
                        "Find out, before changing anything: how many jobs share a node, how long a node lives compared with the job it was created for, and the memory-to-CPU ratio of the shapes chosen. Those three facts decide whether the answer is shared pools, a dedicated high-memory pool, or lower memory peaks in the jobs themselves.",
                        f"Then review {self.CL['autoscaler_review']}" + (f". Current autoscaling profile on record: {prof}." if prof else " (the API cannot see these settings, so confirm them on the call)."),
                        "Verify: idle cost by machine shape falls after each change."],
                 verification="Idle $ by machine shape falls on auto-provisioned pools",
                 evidence=f"This is the largest idle pool in the plan - {usd(ap_idle)}/mo - and it is deliberately not claimed: PerfectScale has no computed saving for pools that live for minutes, and the cause is not yet established: it could be pod requests, how jobs are packed onto nodes, or autoscaler settings, and it is not a per-pool change. "
                          f"{usd(sum(f['pools']['ap_rec_total'] for f in fs))}/mo of per-pool recommendations exist on these pools and are not counted either, because they do not survive the pool being deleted.")

    # ---- GPU
    def r_gpu(self):
        fs = [f for f in self.F if f["pools"]["gpu_n"] and f["pools"]["gpu_idle_share"] >= self.T["gpu_idle_share_min"] and f["pools"]["gpu_idle"] >= self.T["pool_idle_min_usd"]]
        if not fs:
            return
        gi = sum(f["pools"]["gpu_idle"] for f in fs); gi_all = sum(f["pools"]["idle"] for f in fs)
        pr = sum(f["pr_env_labels"] for f in fs)
        self.add("gpu_cap", action_type="gpu_cap", title=f"Cap provisioning of {self.CL['gpu_note']} on {self.names(fs)}",
                 cluster=self.names(fs), scope=f"{sum(f['pools']['gpu_n'] for f in fs)} GPU-attached node pool instances",
                 savings_monthly_usd=0, savings_basis="blocked_pending", confidence="medium", effort_tier="M", effort_rationale="M (catalog), no escalators - a provisioning limit plus a decision with the ML teams",
                 change_risk="medium", owner_role="platform", phase=2, lane="decision", _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the split: GPU-attached pools hold {usd(gi)}/mo of {self.names(fs)}'s {usd(gi_all)}/mo idle ({pct(gi, gi_all)}).",
                        "Establish which workloads actually need a GPU" + (", and which pull-request environments" if pr else "") + ".",
                        "Set a GPU limit on node provisioning" + (" and shorten the environment TTL where a GPU is not needed." if pr else "."),
                        "Verify: GPU-attached node pool idle falls and workloads that need one still start."],
                 verification="GPU-attached node pool idle $ falls; GPU workloads still start",
                 evidence=f"{pct(gi, gi_all)} of idle node cost on {self.names(fs)} is on {self.CL['gpu_note']}. Not claimed as a saving: it depends on a decision about what the environment needs.")

    # ---- other under-provisioned
    def r_underprov(self):
        fs = [f for f in self.F if f["res_other"]["n"] > 0 and f["res_other"]["literal"] >= 1]
        if not fs:
            return
        lit = sum(f["res_other"]["literal"] for f in fs); lik = sum(f["res_other"]["likely"] for f in fs); low = sum(f["res_other"]["low"] for f in fs)
        obs = sum(f["res_other"]["likely_o"] for f in fs)
        n = sum(f["res_other"]["n"] for f in fs)
        rows = sorted([dict(r, cluster=f["name"]) for f in fs for r in f["res_other_list"]], key=lambda r: -r["inc"])
        nods = sum(f["no_requests_ds_n"] for f in fs)
        t, r = effort("S", ["cross-team - the workloads sit with several teams"] if self.cross_team([f["res_other_owners"] for f in fs]) else [])
        self.add("underprov_other", action_type="rightsize_underprovisioned", title="Raise requests on the remaining under-provisioned workloads",
                 cluster=self.names(fs), scope=f"{n} workloads with high-severity under-provisioning" + (f"; {nods} DaemonSets have no requests set" if nods else ""),
                 savings_monthly_usd=lik, obs=obs, savings_basis="resiliency_investment", confidence="medium", effort_tier=t, effort_rationale=r,
                 change_risk="medium", owner_role="app-team", phase=2, depends_on=["oom_fix"] if self.has("oom_fix") else [], lane="config", range={"low": low, "likely": lik, "literal": lit},
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=(["Do the OOM action first; this is the same exercise on workloads that are not yet being killed."] if self.has("oom_fix") else []) +
                       ["Start with the largest: " + "; ".join(f"{short_name(x['name'])} ({x['cpu_cur']:.0f} to {x['cpu_rec']:.0f} cores)" for x in rows[:3]) + ".",
                        "Size from observed usage plus a deliberate margin, not from the printed recommendation.",
                        "This is spend, not saving. It belongs in the resiliency column and is never netted against savings.",
                        "Verify: the under-provisioning indicators clear without new throttling."],
                 verification="Under-provisioned indicators clear; no new throttling",
                 evidence=f"Range {usd(low)} low, {usd(lik)} likely, {usd(lit)} literal per month. The literal figure is an upper bound: recommended values include headroom for peaks, and batch steps rarely need it every run.")

    # ---- policy headroom
    def r_headroom(self):
        fs = [f for f in self.F if f["headroom_policy"]]
        if not fs:
            return
        pol = fs[0]["headroom_policy"][0]
        others = [f for f in self.F if not f["headroom_policy"]]
        t, r = effort("S", ["the policy choice may well be deliberate - this is a decision to make, not a correction to apply (inferred - cause not confirmed)"])
        self.add("headroom_policy", action_type="policy_headroom_tune", title=f"Review the {pol} policy on {self.names(fs)}", cluster=self.names(fs),
                 scope=f"policy: {pol} - {sum(f['policies'][pol] for f in fs):,} workloads", savings_monthly_usd=0, savings_basis="achievable_now", confidence="medium",
                 effort_tier=t, effort_rationale=r, change_risk="medium", owner_role="ps-admin", phase=1, lane="decision",
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the current setting: {self.names(fs)} " + ("is" if len(fs) == 1 else "are") + f" on {pol}" + (f"; the other {len(others)} cluster{'s' if len(others) != 1 else ''} run something else." if others else "."),
                        "Decide whether that is deliberate. If it was chosen for a reason (a large production cluster, strict latency needs), keep it.",
                        f"Understand the cost both ways: {pol} lowers the savings ceiling on over-provisioned workloads and inflates recommended requests on under-provisioned ones.",
                        "If you change it, change it before automation runs there, so automation applies the policy you actually want.",
                        "Verify: potential savings re-computes within 24h of the change."],
                 verification="Potential savings and cost-increase figures re-compute within 24h",
                 evidence=f"Potential savings on {self.names(fs)} is computed inside the {pol} policy, so it is not comparable with clusters on another policy.")

    # ---- local storage (non-managed)
    def r_local_storage(self):
        fs = [f for f in self.F if f["unevictable"]["codes"].get("local_node_storage", {}).get("nm", 0) > 0]
        if not fs:
            return
        n = sum(f["unevictable"]["codes"]["local_node_storage"]["nm"] for f in fs)
        self.add("local_storage", action_type="unevictable_localpv", title="Migrate local-storage pods off nodes the autoscaler needs to drain",
                 cluster=self.names(fs), scope=f"{n} pods outside platform namespaces", savings_monthly_usd=0, savings_basis="blocked_pending", confidence="high",
                 effort_tier="L", effort_rationale="L (catalog), no escalators - a storage migration, already the catalog floor", change_risk="critical", owner_role="platform", phase=3, lane="config",
                 _uids=sorted({f["uid"] for f in fs}),
                 steps=[f"Confirm the blockers: {n} pods outside platform namespaces report local node storage as the reason drain is blocked.",
                        "Separate scratch from durable. A pod using emptyDir for cache can usually be marked safe to evict; one holding state on a local volume cannot.",
                        "For the durable cases, plan a move to network storage. That is a design change, which is why this is Phase 3.",
                        "Verify: the local-storage blocker count falls on the affected pools."],
                 verification="Unevictable count with reason local node storage falls",
                 evidence=f"{n} pods, carried in the plan so it is visible rather than because it should be done soon. Platform pods with the same reason (DNS, metrics) are expected and not counted.")

    # ---- governance
    def r_governance(self):
        last = next((k for k in ("automation_w3", "automation_w2", "automation_proof") if self.has(k)), None)
        pr = sum(f["pr_env_labels"] for f in self.F)
        nns = sum(len(f["namespaces"]) for f in self.F)
        if last:
            self.add("default_policy", action_type="policy_default_new_ns", title="Apply a default optimization policy and automation to new namespaces" + (" and PR environments" if pr else ""),
                     cluster="tenant-wide", scope="all clusters", savings_monthly_usd=0, savings_basis="achievable_now", confidence="high", effort_tier="S", effort_rationale="S (catalog), no escalators",
                     change_risk="medium", owner_role="ps-admin", phase=3, depends_on=[last], lane="toggle", gated_on_automation=True,
                     steps=["Confirm the automation waves have run clean and the team is comfortable with the audit record.",
                            "Set a default optimization policy so new namespaces are covered on creation, and make automation the default for newly discovered eligible workloads, with platform-managed add-ons held out by rule.",
                            "Verify: a namespace or workload created after the change is covered with no manual step."],
                     verification="A newly created namespace or workload inherits the intended policy with no manual step",
                     evidence=(f"{pr:,} workloads are tied to pull-request environments within the window. " if pr else f"The estate spans {nns} namespaces. ") + "Without a default, every new one starts unoptimized and someone has to notice.")
        pool_keys = [k for k in ("pool_shrink_blocked", "pool_shrink_free", "autoprov_review") if self.has(k)]
        self.add("review_cadence", action_type="review_cadence", title="Put a named owner and a date on a quarterly InfraFit review", cluster="tenant-wide", scope="all clusters",
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high", effort_tier="XS", effort_rationale="XS (catalog), no escalators - a calendar entry and an owner",
                 change_risk="low", owner_role="platform", phase=3, lane="decision",
                 steps=["Name the owner. Configuration drift - stale annotations, oversized pool floors, unreviewed provisioning shapes - is what this plan found, and a standing review is what catches the next one.",
                        "Set the first date and put node idle % and unevictable pod count on the agenda as standing items.",
                        "Verify: the first review happens and produces a dated action list."],
                 verification="First review completed on its date with a dated action list",
                 evidence="These are configuration-drift findings rather than tuning problems. A review cadence is what catches the next one.")
        if pool_keys or self.has("pool_machine_recs"):
            deps = pool_keys + (["pool_machine_recs"] if self.has("pool_machine_recs") else [])
            self.add("commitments", action_type="commitment_realignment", title=f"Re-cut any {self.CL['commit']} once capacity settles", cluster="tenant-wide", scope="any commitments covering the clusters in scope",
                     savings_monthly_usd=0, savings_basis="achievable_now", confidence="medium", effort_tier="M", effort_rationale="M (catalog), no escalators", change_risk="medium", owner_role="finops", phase=3, depends_on=deps, lane="decision",
                     steps=["Wait for Phase 2 to settle. Re-cutting coverage against a capacity level that is still falling locks in the wrong number.",
                            "Re-measure steady-state compute after the pool and request changes have landed.",
                            f"Re-cut {self.CL['commit']} against the new baseline.",
                            "Verify: coverage ratio returns to target against the post-optimization run rate."],
                     verification="Commitment coverage ratio back to target against the new run rate",
                     evidence=f"Combined node spend is {usd(self.total_node_cost)}/mo with {usd(self.total_idle)}/mo idle. If consolidation reclaims a meaningful share, existing commitments will be over-cut against the new footprint.")
        d = date.fromisoformat(self.cfg["prepared"]) + timedelta(days=self.cfg["rerun_days"])
        self.rerun_date = d
        self.add("rerun", action_type="plan_rerun", title=f"Regenerate this plan on {d.day} {d.strftime('%B %Y')} and diff it", cluster="tenant-wide", scope="all clusters",
                 savings_monthly_usd=0, savings_basis="achievable_now", confidence="high", effort_tier="XS", effort_rationale="XS (catalog), no escalators", change_risk="low", owner_role="ps-csm", phase=3, lane="decision",
                 steps=[f"Re-run on {d.day} {d.strftime('%B %Y')}, {self.cfg['rerun_days']} days out." + (" By then every cluster has a full month or more of history, so the run-rate scaling in this plan drops away." if self.early else ""),
                        "Diff against this plan rather than reading the new one cold - what moved and what did not is a better conversation than a fresh list.",
                        "Verify: the re-run shows Phase 1 and Phase 2 actions closed and quantifies what they returned."],
                 verification="Re-run shows closed actions and their realized return",
                 evidence=f"This plan is a snapshot of ~{self.window:.0f} days of data." + (" Its figures are scaled up and will be replaced by measured ones." if self.early else ""))

    # ------------------------------------------------------------------ finish: graph, scoring, order
    def finish(self):
        A = self.A
        for a in A:
            uids = a.pop("_nk_uids", None) or []
            resource = a.pop("_nk_resource", None) or a["key"]
            cluster_part = "+".join(sorted(uids)) if uids else "tenant"
            a["natural_key"] = f"{cluster_part}:{resource}:{a['action_type']}"
        # A rule key is usually one action, but r_pool_actions now emits one action per node group under a
        # shared key (pool_shrink_blocked/pool_shrink_free/pool_machine_recs), so a dependency named by key
        # must expand to every action carrying that key, not just one of them.
        by_key_all = collections.defaultdict(list)
        for a in A:
            by_key_all[a["key"]].append(a)
        for a in A:
            a["depends_on"] = [x["id"] for k in a["depends_on"] for x in by_key_all.get(k, [])]
        by = {a["id"]: a for a in A}
        for a in A:
            for p in a["depends_on"]:
                if a["id"] not in by[p]["unblocks"]:
                    by[p]["unblocks"].append(a["id"])
        # stale opt-out also releases pool actions that sit on its pools even without an explicit depends_on
        def released(aid, seen=None):
            seen = seen if seen is not None else set()
            tot = 0
            for d in by[aid]["unblocks"]:
                if d in seen:
                    continue
                seen.add(d)
                if by[d]["savings_basis"] != "resiliency_investment":
                    tot += by[d]["savings_monthly_usd"] or 0
                tot += released(d, seen)
            return tot
        for a in A:
            rv = released(a["id"])
            own = a["savings_monthly_usd"] or 0
            a["released_value"] = rv
            # Promote on value released, but never demote a rule that declared
            # itself an unblocker. Some gates release the ability to conclude
            # anything rather than a sum of dollars - a pool whose autoscaler is
            # invisible has no attributable figure to carry, and attributing one
            # would inflate the very number it is meant to qualify.
            a["is_unblocker"] = bool(a.get("is_unblocker")) or (bool(rv) and rv >= 2 * own)
            a["roi_score"] = None if a["savings_basis"] == "resiliency_investment" else roi_score(own, a["confidence"], a["effort_tier"])
        for a in sorted(A, key=lambda x: x["phase"]):
            if a["is_unblocker"] and a["phase"] > 1:
                tgt = a["phase"] - 1
                if all(by[p]["phase"] <= tgt for p in a["depends_on"]):
                    a["phase"] = tgt
        A.sort(key=lambda a: (a["phase"], 0 if a["is_unblocker"] else 1, -(a["roi_score"] or 0)))

        def topo(rows):
            out, placed, pend = [], set(), list(rows)
            while pend:
                for i, a in enumerate(pend):
                    if all(p in placed or by[p]["phase"] != a["phase"] for p in a["depends_on"]):
                        out.append(pend.pop(i)); placed.add(a["id"]); break
                else:
                    raise SystemExit("dependency cycle: " + ",".join(x["id"] for x in pend))
            return out
        A[:] = [a for ph in (1, 2, 3) for a in topo([x for x in A if x["phase"] == ph])]
        for a in A:
            a.pop("_pools", None); a.pop("_rows", None)
            for p in a["depends_on"]:
                assert by[p]["phase"] <= a["phase"] and A.index(by[p]) < A.index(a)
        return self.summary()

    def summary(self):
        A, by = self.A, {a["id"]: a for a in self.A}
        tot = collections.Counter(); obs = collections.Counter()
        for a in A:
            tot[a["savings_basis"]] += a["savings_monthly_usd"] or 0
            obs[a["savings_basis"]] += a.get("obs", 0) or 0
        rng = collections.Counter()
        for a in A:
            if a.get("range"):
                for k, v in a["range"].items():
                    rng[k] += v

        def downstream(aid, out=None):
            out = out if out is not None else set()
            for d in by[aid]["unblocks"]:
                if d not in out:
                    out.add(d); downstream(d, out)
            return out
        gated_ids = {a["id"] for a in A if a["gated_on_automation"]}
        gated = sum(by[i]["savings_monthly_usd"] or 0 for i in gated_ids if by[i]["savings_basis"] == "achievable_now")
        ach = tot["achievable_now"]
        lanes = collections.Counter()
        for a in A:
            if a["savings_basis"] == "achievable_now":
                lanes[a["lane"]] += a["savings_monthly_usd"] or 0
        claimed_idle = 0.0
        for cn, pid in self.claimed_pools:
            claimed_idle += next(p["idle"] for p in self.by[cn]["pools"]["long_lived"] if p["id"] == pid)
        ap_idle = sum(f["pools"]["ap_idle"] for f in self.F)
        gpu_idle = sum(f["pools"]["gpu_idle"] for f in self.F)
        held = sum(f["lanes"]["managed"]["pot"] for f in self.F)
        confs = collections.Counter(a["confidence"] for a in A)
        first = [a for a in A if a["phase"] == 1]
        first_moves = sorted(first, key=lambda a: (0 if a["is_unblocker"] else 1, 0 if a["change_risk"] == "critical" else 1, -(a["released_value"] or a["savings_monthly_usd"] or 0)))[:3]
        adoption = self.adoption(A, tot, rng)
        return {
            "adoption": adoption, "fit_split": self.fit_split(A),
            "status": self.cfg.get("status"), "tenant": self.cfg["customer"], "generated": self.cfg["prepared"], "cloud": self.cloud, "early": self.early,
            "window_days": round(self.window, 1), "run_rate_factor": {f["name"]: round(f["factor"], 3) for f in self.F},
            "scope": {"clusters_analyzed": len(self.F), "cluster_names": [f["name"] for f in self.F]},
            "totals": {"achievable_now_monthly": round(ach), "blocked_pending_monthly": round(tot["blocked_pending"]),
                       "resiliency_investment_monthly": round(tot["resiliency_investment"]),
                       "resiliency_range": {k: round(v) for k, v in rng.items()}},
            "observed": {"achievable_now": round(obs["achievable_now"]), "blocked_pending": round(obs["blocked_pending"]), "resiliency_likely": round(obs["resiliency_investment"])},
            "automation_gated": {"usd": round(gated), "share": (gated / ach) if ach else 0},
            "achievable_by_lane": {k: round(v) for k, v in lanes.items()},
            "idle": {"total": round(self.total_idle), "node_cost": round(self.total_node_cost), "auto_provisioned": round(ap_idle), "claimed_long_lived": round(claimed_idle),
                     "unclaimed_other": round(self.total_idle - claimed_idle - ap_idle), "gpu": round(gpu_idle)},
            "pod": {"cost": round(self.pod_cost), "waste": round(self.pod_waste), "potential": round(self.pod_pot), "managed_held_out": round(held)},
            "confidence_counts": dict(confs), "first_moves": [a["id"] for a in first_moves],
            "rerun_date": getattr(self, "rerun_date", date.fromisoformat(self.cfg["prepared"]) + timedelta(days=self.cfg["rerun_days"])).isoformat(),
            "decisions": self.decisions(), "decision_detail": self.decision_detail, "evidence": self.evidence(), "confidence": self.confidence_summary(), "cluster_idle": self.cluster_idle(),
            "scale_context": self.scale_context(), "outlook": self.outlook(A, tot), "actions": A,
        }

    def fit_split(self, A):
        out = {}
        for basis in ("achievable_now", "blocked_pending", "resiliency_investment"):
            d = {"PodFit": 0.0, "InfraFit": 0.0}
            for x in A:
                if x["savings_basis"] == basis and x["fit"] in d:
                    d[x["fit"]] += x["savings_monthly_usd"] or 0
            tot = sum(d.values())
            out[basis] = {k: {"usd": round(v), "share": (v / tot if tot else 0)} for k, v in d.items()}
        out["node_idle"] = {"PodFit": {"usd": 0, "share": 0.0}, "InfraFit": {"usd": round(self.total_idle), "share": 1.0}}
        return out

    def adoption(self, A, tot, rng):
        """100% = every recommendation applied. Adjusted = configured adoption_factor applied to savings.
        Spend is not haircut: adopting less of a resiliency fix means less protection, not a smaller bill."""
        f = float(self.cfg["adoption_factor"])
        kinds = collections.defaultdict(float)
        for x in A:
            if x["savings_basis"] == "blocked_pending":
                kinds[x["value_kind"]] += x["savings_monthly_usd"] or 0
        ach, pend = tot["achievable_now"], tot["blocked_pending"]
        return {"factor": f,
                "achievable_now": {"full": round(ach), "adjusted": round(round(ach) * f), "kind": "computed"},
                "blocked_pending": {"full": round(pend), "adjusted": round(round(pend) * f),
                                    "estimate": {"full": round(kinds["estimate"]), "adjusted": round(kinds["estimate"] * f)},
                                    "ceiling": {"full": round(kinds["ceiling"]), "adjusted": round(kinds["ceiling"] * f), "note": "Idle ceiling: assumes idle falls to zero, which no pool does. Treat the adjusted figure as an upper bound; the realistic figure is lower."}},
                "resiliency_investment": {"low": round(rng.get("low", 0)), "likely": round(rng.get("likely", 0)), "literal": round(rng.get("literal", 0)), "note": "Spend is not reduced by the adoption factor. The likely case is already a conservative sizing; literal is full adoption of PerfectScale's recommendation."},
                "node_idle": {"current": round(self.total_idle), "note": "Current state, not a saving. No adoption factor applies."}}

    # ------------------------------------------------------------------ decisions (computed from what fired)
    def decisions(self):
        """Decisions to settle, each valued by the dollars it unblocks and ranked by that value."""
        by_key = {a["key"]: a for a in self.A}  # presence checks only - a key may now cover several actions (per node group)

        def val(keys, released=False):
            tot = 0.0
            for a in self.A:
                if a["key"] in keys and a["savings_basis"] != "resiliency_investment":
                    tot += (a["released_value"] if released and a["released_value"] else a["savings_monthly_usd"]) or 0
            return tot
        D = []

        def add(text, usd, kind="saving", note=""):
            D.append({"text": text, "usd": round(usd), "kind": kind, "note": note})
        if "automation_proof" in by_key:
            add("Is automation enabled for the clusters in scope, and who can switch it on? " + ("The audit log is empty everywhere. " if not any(f["audit"]["n"] for f in self.F) else "") + "If the Automation page offers 'Request Access', that is the gate.",
                val(["automation_proof", "automation_w2", "automation_w3"]))
        if "job_rightsize" in by_key or "oom_fix" in by_key:
            add("Who owns the job resource configuration, and who approves memory increases? The OOM fix is spend; the likely figure is the place to start and the literal recommendation is a ceiling.",
                val(["job_rightsize"]), "saving", "plus the resiliency spend, which is protection rather than saving")
        if "stale_optout" in by_key:
            add("Are the do-not-evict annotations on long-running controllers deliberate, or inherited from a chart default? Running batch jobs should keep theirs; controllers can usually be rescheduled.",
                val(["stale_optout"], released=True), "idle ceiling", "released once the pool actions can run; includes PerfectScale estimates and an idle ceiling, so an upper bound")
        if "pool_shrink_blocked" in by_key or "pool_shrink_free" in by_key:
            add("Are the minimum sizes on the long-lived node pools deliberate, and who owns changing them?", val(["pool_shrink_blocked", "pool_shrink_free"]), "idle ceiling", "upper bound; no pool reaches zero idle")
        if "autoprov_review" in by_key:
            ap = sum(f["pools"]["ap_idle"] for f in self.F)
            add("Who owns the autoscaler's provisioning limits and allowed machine shapes? The shapes it chooses for short-lived pools are the largest idle pool in the plan.", ap, "idle at stake", "not claimed as a saving")
        if "gpu_cap" in by_key:
            add("Does every environment that gets GPU capacity need it, and is the environment lifetime the right length?", sum(f["pools"]["gpu_idle"] for f in self.F), "idle at stake", "not claimed as a saving")
        if "headroom_policy" in by_key:
            add("Is the headroom-style optimization policy deliberate, or inherited? It lowers the savings ceiling and inflates recommended requests.", 0, "saving", "changes what is computed, not a new saving")
        if "stateful_manual" in by_key or "heldout_manual" in by_key:
            add("The workloads kept out of automation - is that permanent, or revisitable once the automation record is re-established?", val(["stateful_manual", "heldout_manual"]))
        if self.early:
            add(f"Is a {self.window:.0f}-day window representative? Batch and delivery cycles can swing load. If month-end or delivery weeks look very different, the run-rate figures here will move - which is the reason to re-run in {self.cfg['rerun_days']} days.", 0, "saving", "affects every figure")
            for c, s in self.trends.items():
                if s.get("variable"):
                    D[-1]["text"] += f" Daily cost on {c} varies by {s['cv'] * 100:.0f}% (coefficient of variation), so treat its run-rate with extra care."
        if self.cfg["intake"].get("change_freeze") is None:
            add("Is there a change freeze or approval lead time for production? None was recorded at intake; if one exists, production actions move up an effort tier.", 0, "saving", "affects timing of every production action")
        D.sort(key=lambda d: (0 if d["kind"] in ("saving", "idle ceiling") and d["usd"] else 1, -d["usd"]))
        for d in D:
            d["group"] = next((g for s, g in DECISION_GROUPS if d["text"].startswith(s)), "Access and constraints")
        self.decision_detail = D
        return [d["text"] for d in D]

    def evidence(self):
        """What the data establishes, what is inferred from it, and what is unknown. Every line is generated from facts."""
        I = {k: sum(f["pools"][k] for f in self.F) for k in ("idle", "cost", "ap_idle", "ap_n")}
        fs = [f for f in self.F if f["pools"]["ap_n"]]
        out = []

        def add(basis, claim, support="", check=""):
            out.append({"basis": basis, "claim": claim, "support": support, "check": check})
        add("measured", f"{usd(I['idle'])}/mo of node capacity is idle, {pct(I['idle'], I['cost'])} of {usd(I['cost'])}/mo node spend.", "PerfectScale node-pool data, scaled to a 30-day run-rate.", "InfraFit view, node pool totals.")
        if fs:
            tot = {k: sum(f["ap_totals"][k] for f in fs) for k in ("cpu_req", "cpu_used", "mem_req", "mem_used")}
            med = sorted(f["pools"]["ap_median_wall_min"] for f in fs)[len(fs) // 2]
            add("measured", f"{usd(I['ap_idle'])}/mo of it sits on {int(I['ap_n']):,} short-lived auto-provisioned pools; the median pool lives about {med:.0f} minutes.", "Pool running time divided by average node count.", "InfraFit view, filtered to auto-provisioned pools.")
            if tot["cpu_used"] and tot["mem_used"]:
                add("measured", f"On those pools, pods request on average {tot['cpu_req'] / tot['cpu_used']:.1f}x the CPU and {tot['mem_req'] / tot['mem_used']:.1f}x the memory they use (time-averaged).", "Requested versus used, averaged over each pool's life.", "InfraFit view, pool detail.")
            jr = {k: sum(f["job_req_vs_rec"][k] for f in fs) for k in ("cpu_cur", "cpu_rec", "mem_cur_gib", "mem_rec_gib")}
            if jr["cpu_cur"]:
                add("measured", f"PerfectScale's recommendations for job workloads (grouped and ungrouped), taken together, move CPU from {jr['cpu_cur']:,.0f} to {jr['cpu_rec']:,.0f} cores and memory from {jr['mem_cur_gib']:,.0f} to {jr['mem_rec_gib']:,.0f} GiB. The savings come from a few oversized groups; others need more.", "Sum of current versus recommended requests across job workloads.", "PodFit view, job workloads.")
            add("inferred", "The idle on these pools most likely reflects the gap between peak and average use: nodes are sized for job peaks and hold far less most of the time. Cause not confirmed.", "Requests far above average use, large memory-heavy machine shapes, and recommendations that protect peaks rather than lower requests.", "Compare each shape's memory-to-CPU ratio, pods per node, and node lifetime against job runtime.")
            add("inferred", "Lowering requests alone is unlikely to shrink these shapes. Cause not confirmed.", "PerfectScale's recommendations do not lower aggregate requests for these jobs.", "Apply recommendations to one job family and watch which machine shape is chosen next.")
        add("measured", f"{usd(self.pod_pot)}/mo is PerfectScale's total computed workload saving in scope; {usd(self.pod_waste)}/mo is workload waste.", "Workload list, scaled to a 30-day run-rate.", "PodFit view totals.")
        add("measured", "Automation has not yet run on these clusters." if not any(f["audit"]["n"] for f in self.F) else "Automation has run on part of these clusters.", "Automation audit log.", "Automation page, audit log.")
        add("unknown", "Autoscaler settings: autoscaling profile, auto-provisioning limits, allowed machine families.", "The API does not expose them.", "Ask the platform team, or read the cluster configuration.")
        add("unknown", "How many jobs share a node, and whether a node is created per job.", "Not in the data collected.", "Pod-to-node placement for a sample of job runs.")
        add("unknown", f"Whether a {self.window:.0f}-day window is representative of normal load.", "Window length.", f"Re-run on {(date.fromisoformat(self.cfg['prepared']) + timedelta(days=self.cfg['rerun_days'])).isoformat()}.")
        return out

    def confidence_summary(self):
        """Plain-language version of the evidence: what we know, what we think, what we need from the customer."""
        fs = [f for f in self.F if f["pools"]["ap_n"]]
        I_idle, I_cost = self.total_idle, self.total_node_cost
        ap = sum(f["pools"]["ap_idle"] for f in fs)
        know, think, need = [], [], []
        if fs:
            med = sorted(f["pools"]["ap_median_wall_min"] for f in fs)[len(fs) // 2]
            tot = {k: sum(f["ap_totals"][k] for f in fs) for k in ("mem_req", "mem_used")}
            take = (f"The {usd(I_idle)}/mo of idle node capacity is measured. We do not yet know why {usd(ap)} of it sits on short-lived pools, and this section says how we will find out.")
            know.append(f"{usd(I_idle)}/mo of node capacity is idle, {pct(I_idle, I_cost)} of node spend.")
            know.append(f"{usd(ap)}/mo of that is on pools that live about {med:.0f} minutes each.")
            if tot["mem_used"]:
                know.append(f"Pods on those pools ask for about {tot['mem_req'] / tot['mem_used']:.0f} times the memory they typically use.")
            think.append("These pools are sized for the largest moment of each job and sit mostly empty the rest of the time. Not confirmed.")
            think.append("Lowering requests alone is unlikely to shrink them, because PerfectScale's recommendations for these jobs do not lower requests overall. Not confirmed.")
            need.append("Your autoscaler settings: profile, auto-provisioning limits and allowed machine types.")
            need.append("Whether several jobs share a node, or each job gets its own.")
        else:
            take = f"The {usd(I_idle)}/mo of idle node capacity is measured from PerfectScale data."
            know.append(f"{usd(I_idle)}/mo of node capacity is idle, {pct(I_idle, I_cost)} of node spend.")
        know.append(f"{usd(self.pod_pot)}/mo is PerfectScale's computed workload saving in scope.")
        if not any(f["audit"]["n"] for f in self.F):
            know.append("Automation has not yet run on these clusters, so no savings have been realized.")
        need.append(f"A second look after more data: this plan rests on about {self.window:.0f} days, and we will re-run on {(date.fromisoformat(self.cfg['prepared']) + timedelta(days=self.cfg['rerun_days'])).isoformat()}.")
        grouping = None
        if fs:
            jr = {k: sum(f["job_req_vs_rec"][k] for f in fs) for k in ("cpu_cur", "cpu_rec", "mem_cur_gib", "mem_rec_gib")}
            raises = jr["cpu_rec"] > jr["cpu_cur"] or jr["mem_rec_gib"] > jr["mem_cur_gib"]
            grouping = ("Label-based grouping combines usage, requests and limits across all runs of a job, and recommendations come from that combined usage. "
                        "InfraFit sizes nodes from workload requests, so lower requests can mean tighter packing and fewer nodes, but not automatically: "
                        "the result also depends on how many jobs run at once, whether pods can be evicted, autoscaler settings, DaemonSets and scheduling constraints. "
                        "PerfectScale notes that short-lived, bursty jobs give less accurate node recommendations. "
                        + ("For these jobs the current recommendations raise requests overall, so grouping alone is not expected to shrink the pools. " if raises else "")
                        + "Automation on a grouped workload only reduces resources after enough data has been collected.")
        return {"takeaway": take, "know": know, "think": think, "need": need, "grouping": grouping}

    def cluster_idle(self):
        """Why each cluster's node idle is what it is, from the data (largest driver wins)."""
        rows = []
        for f in self.F:
            P = f["pools"]
            infl = f["shapes_inflated_idle_share"] if P["ap_n"] else 0
            parts = {"auto-provisioned pools on shapes where CPU requests are 5x or more of use": P["ap_idle"] * infl,
                     "auto-provisioned pools on other shapes": P["ap_idle"] * (1 - infl),
                     "long-lived node pools": P["ll_idle"]}
            driver = max(parts, key=parts.get)
            rows.append({"cluster": f["name"], "idle": round(P["idle"]), "idle_pct": P["idle_pct"], "node_cost": round(P["cost"]),
                         "auto_provisioned": round(P["ap_idle"]), "ap_inflated_share": infl, "long_lived": round(P["ll_idle"]),
                         "gpu": round(P["gpu_idle"]), "gpu_share": P["gpu_idle_share"], "driver": driver + (f"; GPU-attached pools hold {P['gpu_idle_share'] * 100:.0f}% of idle" if P["gpu_idle_share"] >= 0.5 else ""),
                         "driver_usd": round(parts[driver]), "driver_share": parts[driver] / P["idle"] if P["idle"] else 0})
        return rows

    def scale_context(self):
        """Puts the savings in proportion to the spend they sit beside."""
        a = sum(a["savings_monthly_usd"] or 0 for a in self.A if a["savings_basis"] == "achievable_now")
        return {"achievable_vs_node_cost": a / self.total_node_cost if self.total_node_cost else 0,
                "idle_vs_node_cost": self.total_idle / self.total_node_cost if self.total_node_cost else 0,
                "idle_per_achievable_dollar": self.total_idle / a if a else None}

    def outlook(self, A, tot):
        """Three planning states built only from figures already in the plan; no new assumptions."""
        f = float(self.cfg["adoption_factor"])
        est = sum(x["savings_monthly_usd"] or 0 for x in A if x["savings_basis"] == "blocked_pending" and x["value_kind"] == "estimate")
        ceil = sum(x["savings_monthly_usd"] or 0 for x in A if x["savings_basis"] == "blocked_pending" and x["value_kind"] == "ceiling")
        ach = tot["achievable_now"]
        return {"low": {"usd": round((ach + est) * f), "label": f"Computed and estimated savings only, at {f * 100:.0f}% adoption"},
                "planning": {"usd": round((ach + est + ceil) * f), "label": f"Everything, including the idle ceiling, at {f * 100:.0f}% adoption"},
                "maximum": {"usd": round(ach + est + ceil), "label": "Everything at 100% adoption, with every idle dollar reclaimed"},
                "note": "Built from the savings figures only; resiliency spend is separate. Achievable and Pending are combined here and only here: they overlap, so treat Planning and Maximum as upper bounds, and the idle-ceiling part is an upper bound in its own right."}
