"""Move a tenant config to its next version, optionally saving HPA confirmations first.

Every re-render needs a new version_tag and out_dir (outputs for a tag are never replaced) and a
previous_plan pointing at the plan it replaces, so the change log is written. Doing that by hand in
JSON is the error-prone part of a re-run; `run_plan.py --next-version` and `--pin-hpa` do it here.

The config file is rewritten with only these keys changed. The file as it was is kept beside it as
config/<stem>.<old tag>.json, so every earlier version stays reproducible.
"""
import json
import re
from pathlib import Path

# Kubernetes namespace names are DNS labels (RFC 1123): this is the whole accepted alphabet, so a
# namespace can never smuggle regex syntax or a path into the config.
NS_RX = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def next_version(tag):
    """v5.3 -> v5.4, v5 -> v6, v2.1 -> v2.2; a tag with no trailing number gains '.1'."""
    m = re.match(r"^(.*?)(\d+)$", tag or "")
    return f"{m.group(1)}{int(m.group(2)) + 1}" if m else f"{tag or 'v'}.1"


def find_config(script_file, explicit=None):
    """The tenant config a command should use, so it works from any folder.

    Each tenant folder holds its own copy of the library at <tenant>/skill/<skill>/scripts/, so a
    script knows which tenant it belongs to from where it lives. With no --config, the one
    config/<name>.tenant.json in that tenant folder is used (version backups, <name>.tenant.<tag>.json,
    never match). A relative --config is tried from the current folder, then from the tenant folder."""
    script = Path(script_file).resolve()
    in_tenant = len(script.parents) > 3 and script.parents[2].name == "skill"
    root = script.parents[3] if in_tenant else None
    if explicit:
        p = Path(explicit).expanduser()
        for cand in ([p] if p.is_absolute() else [Path.cwd() / p] + ([root / p] if root else [])):
            if cand.is_file():
                return cand.resolve()
        raise SystemExit(f"no config at {explicit}")
    if root is None:
        raise SystemExit("This is the installed copy of the skill, not a tenant folder's copy, so it cannot tell which "
                         "customer you mean. Run the copy inside the tenant folder (skill/<skill>/scripts/), double-click "
                         "'Open kickoff pages.command' there, or pass --config <tenant folder>/config/<name>.tenant.json.")
    hits = sorted((root / "config").glob("*.tenant.json"))
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise SystemExit(f"no config/<name>.tenant.json in {root} yet - write one with new_tenant.py config")
    raise SystemExit(f"more than one config in {root / 'config'}; pass --config with one of: "
                     + ", ".join(f"config/{h.name}" for h in hits))


def tenant_root(config_path):
    p = Path(config_path).resolve()
    return p.parent.parent if p.parent.name == "config" else p.parent


def _tag_out(raw):
    """The version tag and output folder in effect, falling back to the library defaults like Config does."""
    from .config import DEFAULTS
    return raw.get("version_tag") or DEFAULTS["version_tag"], raw.get("out_dir") or DEFAULTS["out_dir"]


def _rel(p, root):
    return p.relative_to(root).as_posix() if p.is_relative_to(root) else str(p)


def current_plan_json(config_path, raw=None):
    """The plan JSON the config's current version produced, relative to the tenant root, or None."""
    raw = raw if raw is not None else json.loads(Path(config_path).read_text())
    root = tenant_root(config_path)
    tag, out = _tag_out(raw)
    hits = sorted((root / out).glob(f"**/*-optimization-plan-*-{tag}.json"))
    return _rel(hits[0], root) if hits else None


def validate_namespaces(names):
    bad = [n for n in names if not isinstance(n, str) or not NS_RX.match(n)]
    if bad:
        raise ValueError(f"not a Kubernetes namespace name: {', '.join(map(repr, bad))}")
    return list(dict.fromkeys(names))          # de-duplicated, order kept


def bump(config_path, pin=()):
    """Save `pin` (namespace names) into hpa_pinned_namespaces, then move the config to the next free
    version. Returns what changed. Raises ValueError before touching anything if a name is invalid."""
    path = Path(config_path).resolve()
    names = validate_namespaces(list(pin))
    raw = json.loads(path.read_text())
    root = tenant_root(path)
    old_tag, old_out = _tag_out(raw)
    prev = current_plan_json(path, raw)

    pats = list(raw.get("hpa_pinned_namespaces") or [])
    added = []
    for n in names:
        p = f"^{n}$"
        if p not in pats:
            pats.append(p); added.append(n)

    # Next tag whose output folder does not exist yet: never write into an existing version.
    new_tag = next_version(old_tag)
    out_parent = Path(old_out).parent
    while (root / out_parent / new_tag).exists():
        new_tag = next_version(new_tag)
    backup = path.with_name(f"{path.stem.removesuffix('.tenant')}.tenant.{old_tag}.json")
    if not backup.exists():
        backup.write_text(path.read_text())

    if names:
        raw["hpa_pinned_namespaces"] = pats
    raw.update(version_tag=new_tag, out_dir=str(out_parent / new_tag))
    if prev:
        raw["previous_plan"] = prev
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, indent=2) + "\n")
    tmp.replace(path)                          # atomic: a crash leaves the old file, never half a file
    return {"version": new_tag, "previous_version": old_tag, "out_dir": raw["out_dir"], "previous_plan": prev,
            "pinned_added": added, "pinned_all": pats if names else list(raw.get("hpa_pinned_namespaces") or []),
            "backup": _rel(backup, root)}
