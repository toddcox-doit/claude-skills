#!/usr/bin/env python3
"""Build a shareable bundle of this library, and refuse to ship a dirty one.

  python3 scripts/package.py --deny "Acme,Northwind" [--out ~/Desktop] [--skip-tests]

Stages the library into a temp folder, drops build junk, runs the test suite
against the staged copy, scans every file, and only then writes the zip.

The scan is the point. A bundle that leaves your machine must carry no customer
name, no absolute path, no credential and no tenant data. `--deny` takes the
customer names this machine has worked on; they are supplied at build time and
deliberately never stored in this script, because a list of your customers is
itself the thing you are trying not to ship.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent
SHIP = ["SKILL.md", "README.md", "LICENSE", "scripts", "templates", "references"]
DROP_DIRS = {"__pycache__", ".git", "dist", "node_modules", ".pytest_cache"}
DROP_FILES = {".DS_Store", ".env"}
TEXT = {".py", ".js", ".md", ".json", ".txt", ".yml", ".yaml", ".cfg", ".toml", ".sh"}
# Extensionless files that are nonetheless plain text and must be scanned, not
# rejected as "unexpected binary" (e.g. LICENSE).
TEXT_NAMES = {"LICENSE", "NOTICE", "AUTHORS"}

# Things that must never appear in a bundle, whatever the customer is called.
PATTERNS = [
    (r"/Users/[A-Za-z0-9._-]+", "absolute macOS path"),
    (r"/home/[A-Za-z0-9._-]+", "absolute Linux path"),
    (r"[A-Za-z]:\\\\Users\\\\", "absolute Windows path"),
    (r"\beyJ[A-Za-z0-9_-]{10,}", "JWT-shaped token"),
    # the key may be quoted, as it is in JSON: "client_secret": "…"
    (r"(?i)\b(client[_-]?secret|service[_-]?token|api[_-]?secret|password)"
     r"['\"]?\s*[:=]\s*['\"][^'\"]{8,}", "inline credential"),
    (r"(?i)\bBearer\s+[A-Za-z0-9._-]{20,}", "bearer token literal"),
    (r"(?i)\bsk-[A-Za-z0-9]{20,}", "API key"),
]
# Detector patterns in the library legitimately contain these shapes.
ALLOW = {
    "scripts/psplan/verify.py": ["absolute macOS path", "absolute Linux path"],
    "scripts/package.py": ["absolute macOS path", "absolute Linux path",
                           "absolute Windows path", "JWT-shaped token",
                           "inline credential", "bearer token literal", "API key"],
}


def stage(dst: Path):
    for item in SHIP:
        src = LIB / item
        if not src.exists():
            print(f"  note: {item} not present, skipping")
            continue
        if src.is_dir():
            shutil.copytree(src, dst / item,
                            ignore=shutil.ignore_patterns(*DROP_DIRS, *DROP_FILES))
        else:
            shutil.copy2(src, dst / item)
    for d in list(dst.rglob("*")):
        if d.is_dir() and d.name in DROP_DIRS:
            shutil.rmtree(d, ignore_errors=True)
        elif d.is_file() and d.name in DROP_FILES:
            d.unlink()


def scan(root: Path, deny):
    """Return a list of (relative path, what, sample). Empty means shippable."""
    hits = []
    deny_rx = [(re.compile(rf"\b{re.escape(d)}\b", re.I), f"customer name '{d}'")
               for d in deny if d]
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() not in TEXT and p.name not in TEXT_NAMES:
            hits.append((rel, "unexpected binary in bundle", p.suffix or "no suffix"))
            continue
        try:
            t = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            hits.append((rel, "undecodable file", ""))
            continue
        allowed = ALLOW.get(rel, [])
        for rx, what in PATTERNS:
            if what in allowed:
                continue
            m = re.search(rx, t)
            if m:
                hits.append((rel, what, m.group(0)[:60]))
        for rx, what in deny_rx:
            m = rx.search(t)
            if m:
                hits.append((rel, what, t[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")))
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--deny", default="", help="comma-separated customer names that must not appear")
    ap.add_argument("--out", default=str(LIB / "dist"), help="where to write the zip")
    ap.add_argument("--skip-tests", action="store_true")
    a = ap.parse_args()
    deny = [d.strip() for d in a.deny.split(",") if d.strip()]
    if not deny:
        print("warning: --deny is empty, so no customer-name check will run.\n"
              "         Pass every customer this machine has analysed.", file=sys.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / LIB.name
        staged.mkdir(parents=True)
        print(f"staging {LIB.name} ...")
        stage(staged)

        if not a.skip_tests:
            print("running tests against the staged copy ...")
            r = subprocess.run([sys.executable, "-W", "ignore", "-m", "unittest", "discover", "-s", "tests"],
                               cwd=staged / "scripts", capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout[-2000:] or r.stderr[-2000:], file=sys.stderr)
                raise SystemExit("tests failed; bundle not built")
            print("  " + (r.stderr.strip().splitlines() or ["ok"])[-1])

        print(f"scanning {sum(1 for _ in staged.rglob('*') if _.is_file())} files ...")
        hits = scan(staged, deny)
        if hits:
            print("\nREFUSING TO BUILD - the bundle is not clean:\n", file=sys.stderr)
            for rel, what, sample in hits:
                print(f"  {rel}\n      {what}: {sample}", file=sys.stderr)
            raise SystemExit(f"\n{len(hits)} problem(s); fix them and re-run")
        print("  clean: no customer names, paths, credentials or stray files")

        out = Path(a.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        zpath = out / f"{LIB.name}-{date.today().isoformat()}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(staged.rglob("*")):
                if p.is_file():
                    z.write(p, Path(LIB.name) / p.relative_to(staged))
        digest = hashlib.sha256(zpath.read_bytes()).hexdigest()[:16]
        files = sum(1 for p in staged.rglob("*") if p.is_file())

        manifest = {"bundle": zpath.name, "built": date.today().isoformat(),
                    "files": files, "bytes": zpath.stat().st_size, "sha256_16": digest,
                    "checked_against": deny}
        (out / "MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")

        print(f"\nwrote {zpath}")
        print(f"  {files} files, {zpath.stat().st_size / 1024:.0f} KB, sha256 {digest}")
        print(f"  manifest: {out / 'MANIFEST.json'}")
        print("\nShare the zip. The recipient unzips it and reads README.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
