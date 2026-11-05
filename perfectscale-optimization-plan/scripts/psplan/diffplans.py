"""Change log between two plan JSON files. Actions are matched on action_type (then order), falling back to title."""
import json
import sys
from collections import defaultdict
from pathlib import Path


def _load(p):
    with open(p) as fh:
        return json.load(fh)


def _val(a):
    return a.get("savings_monthly_usd", 0) or 0


def _t(a):
    return a.get("action_type") or a.get("title", "")


def pair(old, new):
    ob, nb = defaultdict(list), defaultdict(list)
    for a in old:
        ob[_t(a)].append(a)
    for a in new:
        nb[_t(a)].append(a)
    pairs, added, removed = [], [], []
    for k in list(dict.fromkeys(list(ob) + list(nb))):
        o, n = sorted(ob.get(k, []), key=lambda a: -_val(a)), sorted(nb.get(k, []), key=lambda a: -_val(a))
        for x, y in zip(o, n):
            pairs.append((x, y))
        removed += o[len(n):]
        added += n[len(o):]
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
