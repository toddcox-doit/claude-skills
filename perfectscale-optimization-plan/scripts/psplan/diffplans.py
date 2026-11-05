"""Change log between two plan JSON files. Actions are matched by their stable `natural_key`
(cluster(s) + resource/rule-key + action_type - see actions.py Plan.finish()), not by action_type and
a descending-$ rank zip: two same-type actions on different clusters (or different node groups) can
swap relative dollar rank between runs, and rank-based pairing would wrongly report that as a changed
action instead of recognizing each one by its own stable identity."""
import json
import sys
from pathlib import Path


def _load(p):
    with open(p) as fh:
        return json.load(fh)


def _val(a):
    return a.get("savings_monthly_usd", 0) or 0


def _nk(a):
    return a.get("natural_key")


def pair(old, new):
    """Match old vs. new actions by natural_key. A key present in both is "the same action, possibly
    changed"; present only in old is removed; only in new is added. Actions without a natural_key at
    all (e.g. a plan rendered before this field existed) can never be matched and are reported as a
    straight removal/addition rather than silently dropped."""
    ob = {_nk(a): a for a in old if _nk(a)}
    nb = {_nk(a): a for a in new if _nk(a)}
    pairs = [(ob[k], nb[k]) for k in ob if k in nb]
    removed = [ob[k] for k in ob if k not in nb] + [a for a in old if not _nk(a)]
    added = [nb[k] for k in nb if k not in ob] + [a for a in new if not _nk(a)]
    return pairs, added, removed


def diff(old_path, new_path):
    od, nd = _load(old_path), _load(new_path)
    ot, nt = od.get("totals", {}), nd.get("totals", {})
    L = ["# Plan change log", "", f"Compared {Path(old_path).name} with {Path(new_path).name}.", "", "## Headline movement", "", "| Measure | Before | After |", "|---|---|---|"]
    for k in sorted(set(ot) | set(nt)):
        a, b = ot.get(k), nt.get(k)
        if isinstance(a, (int, float)) or isinstance(b, (int, float)):
            L.append(f"| {k} | {a if a is not None else '-'} | {b if b is not None else '-'} |")
    L += ["", f"Actions: {len(od.get('actions', []))} before, {len(nd.get('actions', []))} after.", ""]
    pairs, added, removed = pair(od.get("actions", []), nd.get("actions", []))
    L += ["## Changed actions", "", "| Before | After | Value before | Value after | Phase before | Phase after | Effort before | Effort after |", "|---|---|---|---|---|---|---|---|"]
    n_same = 0
    for o, n in pairs:
        ch = (abs(_val(o) - _val(n)) > 1, o.get("phase") != n.get("phase"), o.get("effort_tier") != n.get("effort_tier"), o.get("savings_basis") != n.get("savings_basis"), o.get("title") != n.get("title"))
        if not any(ch[:4]) and not ch[4]:
            n_same += 1
            continue
        L.append(f"| {o['id']} {o['title']} | {n['id']} {n['title']} | {_val(o):,.0f} | {_val(n):,.0f} | {o.get('phase')} | {n.get('phase')} | {o.get('effort_tier')} | {n.get('effort_tier')} |")
    L += ["", f"{n_same} actions unchanged in value, phase, effort and title.", "", "## New actions", ""]
    L += [f"- {a['id']} {a['title']} ({_val(a):,.0f}/mo)" for a in added] or ["- none"]
    L += ["", "## Dropped actions", ""] + ([f"- {a['id']} {a['title']} ({_val(a):,.0f}/mo)" for a in removed] or ["- none"])
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    print(diff(sys.argv[1], sys.argv[2]))
