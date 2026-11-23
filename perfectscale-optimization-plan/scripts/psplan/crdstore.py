"""Version history of the CRD sets generated on the POV kickoff page.

Every set the page generates (workload CRDs or namespace CRDs) is kept, append-only, in the tenant
folder: crds/<cluster>/<YYYYMMDD-HHMMSS>-<kind>/ holding the files exactly as generated plus a
manifest.json. Nothing is ever pruned - a set is a few kilobytes, and the history is the record of
what each customer was given - but the page shows the last three of each kind first.

The manifest carries, besides when and from which plan version:
  - a sha256 per file, so an identical re-generation is recognised and not stored twice
  - the Kubernetes objects the set creates (kind, namespace, name), so a rollback can list exactly
    what to delete: `kubectl apply` of an older set never removes a config added since
  - what changed against the previous set of the same kind (files added, removed, changed)
  - whether the set was handed to the customer (set from the page)

Only files this page generates are accepted: names of the three shapes the generators write, each
a PerfectScale Automation CR. The ClusterAutomationConfig is shared by every set, so a rollback
never deletes it; PerfectScale's own switches (stopAllAutomation / cleanupAllAutomation in it) are
how to pause or revert the resource changes automation already made.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

SLUG_RX = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
ID_RX = re.compile(r"^\d{8}-\d{6}-(wl|ns)(-\d+)?$")
NAME_RX = re.compile(r"^(?:[a-z0-9][a-z0-9-]{0,120}-cluster-automation-config\.yaml"
                     r"|(?:namespaces|workloads)/[a-z0-9][a-z0-9._-]{0,250}\.yaml)$")
KINDS = {"wl": "workload CRDs", "ns": "namespace CRDs"}
API = "apiVersion: perfectscale.io/v1"
MAX_FILES, MAX_FILE, MAX_META = 5000, 256 * 1024, 16 * 1024
SHARED = {"ClusterAutomationConfig"}          # one per cluster, shared by every set: never in a delete list


def objects(text):
    """[kind, namespace, name] for each document of a generated file (they all follow one layout:
    top-level kind, metadata.name / metadata.namespace indented two spaces)."""
    out = []
    for doc in re.split(r"(?m)^---\s*$", text):
        k = re.search(r"(?m)^kind:\s*(\S+)", doc)
        if not k or "metadata:" not in doc:
            continue
        meta = doc.split("metadata:", 1)[1]
        n = re.search(r'(?m)^  name:\s*"?([^"\s]+)"?', meta)
        ns = re.search(r'(?m)^  namespace:\s*"?([^"\s]+)"?', meta)
        out.append([k.group(1), ns.group(1) if ns else "", n.group(1) if n else ""])
    return out


def validate(kind, files, meta=None):
    """Raise ValueError unless this is a set the kickoff page could have generated."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise ValueError("a set needs between 1 and %d files" % MAX_FILES)
    seen = set()
    for f in files:
        name, text = (f or {}).get("name"), (f or {}).get("text")
        if not isinstance(name, str) or not NAME_RX.match(name) or ".." in name:
            raise ValueError(f"not a file name this page generates: {name!r}")
        if name in seen:
            raise ValueError(f"duplicate file {name}")
        seen.add(name)
        if not isinstance(text, str) or len(text) > MAX_FILE or API not in text or not objects(text):
            raise ValueError(f"{name} is not a PerfectScale Automation CR this page generates")
    if meta is not None and len(json.dumps(meta)) > MAX_META:
        raise ValueError("metadata too large")


def _dir(root, slug):
    if not SLUG_RX.match(slug or ""):
        raise ValueError(f"bad cluster name {slug!r}")
    return Path(root) / "crds" / slug


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _read(d):
    return json.loads((d / "manifest.json").read_text())


def history(root, slug):
    """Every set for the cluster, newest first, without file contents."""
    base = _dir(root, slug)
    if not base.is_dir():
        return []
    sets = [_read(d) for d in base.iterdir() if d.is_dir() and ID_RX.match(d.name) and (d / "manifest.json").is_file()]
    sets.sort(key=lambda m: m["id"], reverse=True)
    return [{k: v for k, v in m.items() if k != "objects"} | {"n_objects": len(m.get("objects") or [])} for m in sets]


def get(root, slug, sid):
    if not ID_RX.match(sid or ""):
        raise ValueError(f"bad set id {sid!r}")
    d = _dir(root, slug) / sid
    if not (d / "manifest.json").is_file():
        raise ValueError(f"no set {sid}")
    m = _read(d)
    m["texts"] = {f["name"]: (d / f["name"]).read_text() for f in m["files"]}
    return m


def save(root, slug, kind, files, meta=None, now=None):
    """Store a generated set. Returns {"id", "unchanged"}; an identical set to the latest of the same
    kind is not stored again."""
    validate(kind, files, meta)
    base = _dir(root, slug)
    now = now or datetime.now(timezone.utc)
    hashes = {f["name"]: _sha(f["text"]) for f in files}
    prev = next((m for m in history(root, slug) if m["kind"] == kind), None)
    if prev and {f["name"]: f["sha256"] for f in prev["files"]} == hashes:
        return {"id": prev["id"], "unchanged": True}
    sid = now.strftime("%Y%m%d-%H%M%S") + "-" + kind
    i = 2
    while (base / sid).exists():
        sid = now.strftime("%Y%m%d-%H%M%S") + f"-{kind}-{i}"
        i += 1
    d = base / sid
    for f in files:
        p = (d / f["name"]).resolve()
        if not p.is_relative_to(d.resolve()):                 # belt and braces: NAME_RX already forbids it
            raise ValueError(f"bad file name {f['name']!r}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f["text"])
    objs = sorted({tuple(o) for f in files for o in objects(f["text"])})
    pm = get(root, slug, prev["id"]) if prev else None
    ph = {f["name"]: f["sha256"] for f in pm["files"]} if pm else {}
    pobjs = {tuple(o) for o in (pm or {}).get("objects", [])}
    m = {"id": sid, "kind": kind, "cluster": slug, "created": now.isoformat(timespec="seconds"),
         "files": [{"name": f["name"], "sha256": hashes[f["name"]], "docs": len(objects(f["text"]))} for f in files],
         "objects": [list(o) for o in objs], "previous": prev["id"] if prev else None,
         "changes": {"added": sorted(set(hashes) - set(ph)), "removed": sorted(set(ph) - set(hashes)),
                     "changed": sorted(n for n in hashes if n in ph and ph[n] != hashes[n])} if pm else None,
         "removed_objects": [list(o) for o in sorted(pobjs - set(objs)) if o[0] not in SHARED],
         "handed": False, "handed_at": None, "meta": meta or {}}
    (d / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    return {"id": sid, "unchanged": False}


def mark(root, slug, sid, handed, now=None):
    """Record (or clear) that a set was handed to the customer."""
    if not ID_RX.match(sid or ""):
        raise ValueError(f"bad set id {sid!r}")
    d = _dir(root, slug) / sid
    if not (d / "manifest.json").is_file():
        raise ValueError(f"no set {sid}")
    m = _read(d)
    m["handed"] = bool(handed)
    m["handed_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds") if handed else None
    (d / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    return {"id": sid, "handed": m["handed"], "handed_at": m["handed_at"]}


def rollback_deletes(target_objects, current_objects):
    """kubectl delete commands for the objects the current set has and the target does not. Applying
    the target's files does not remove them; deleting them returns their workloads to the cluster's
    defaults (automation Disabled in the generated ClusterAutomationConfig)."""
    keep = {tuple(o) for o in target_objects}
    gone = sorted({tuple(o) for o in current_objects} - keep)
    return [f"kubectl delete {k.lower()} {n} -n {ns}" if ns else f"kubectl delete {k.lower()} {n}"
            for k, ns, n in gone if k not in SHARED]
