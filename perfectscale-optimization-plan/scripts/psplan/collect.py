"""Read-only collector (pscli public API). Replaces the shell script.

Lessons baked in:
  * The API rate-limits (HTTP 429). One global limiter + shared penalty window across all workers; exponential backoff.
  * Resumable and incremental: a manifest records what was pulled and when; fresh files are skipped (max_age_hours).
  * No redundant pulls: `workloads list -V all` is a superset of capacity/policy/risk/usage; group-by rollups are NOT
    pulled (they do not reconcile to the workload list); risky/muted come from the workload rows.
  * Unevictable pods: one cluster-wide pull first; if that fails, fall back to the top-N node groups by idle cost
    (auto-provisioned clusters have thousands of short-lived pools).
  * Clusters are addressed by UID (names can be ambiguous).
"""
import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .util import exists, jl, jload


class Limiter:
    def __init__(self, min_interval=0.4):
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.next_ok = 0.0
        self.penalty_until = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(self.next_ok, self.penalty_until, now)
            self.next_ok = t + self.min_interval
        if t > now:
            time.sleep(t - now)

    def penalize(self, seconds):
        with self.lock:
            self.penalty_until = max(self.penalty_until, time.time() + seconds)


class Collector:
    def __init__(self, cfg, log=print, refresh=False, only=None, max_age_hours=24, top_groups=200, compress=False,
                 min_interval=0.4, max_retries=7, backoff=15):
        self.cfg, self.log = cfg, log
        self.refresh, self.only = refresh, set(only or [])
        self.max_age = max_age_hours * 3600
        self.top_groups, self.compress = top_groups, compress
        self.limiter = Limiter(min_interval)
        self.max_retries, self.backoff = max_retries, backoff
        self.pscli = cfg.get("pscli") or shutil.which("pscli")
        if not self.pscli:
            raise SystemExit("pscli not found on PATH (set 'pscli' in the config or install it)")
        self.env = dict(os.environ)
        if cfg.get("profile"):
            self.env["PERFECTSCALE_PROFILE"] = cfg["profile"]
        self.failures = []
        self._print = threading.Lock()

    # ------------------------------------------------------------------ plumbing
    def say(self, msg):
        with self._print:
            self.log(msg)

    def _run(self, args, out_path):
        """Run pscli, streaming stdout to out_path. Retries on 429. Returns (ok, stderr_text)."""
        tmp = Path(str(out_path) + ".tmp")
        n = 0
        while True:
            self.limiter.wait()
            with tmp.open("w") as fh:
                p = subprocess.run([self.pscli] + args, stdout=fh, stderr=subprocess.PIPE, env=self.env, text=True)
            if p.returncode == 0:
                tmp.replace(out_path)
                return True, ""
            n += 1
            if ("RATE_LIMITED" in p.stderr or "429" in p.stderr) and n <= self.max_retries:
                wait = self.backoff * n
                self.say(f"    429 rate-limited; all workers pause {wait}s (retry {n}/{self.max_retries})")
                self.limiter.penalize(wait)
                continue
            tmp.replace(str(out_path) + ".partial")
            return False, p.stderr.strip()[:300]

    def _fresh(self, path, manifest, key):
        if self.refresh or not exists(path):
            return False
        m = manifest.get(key)
        return bool(m and m.get("ok") and (time.time() - m.get("ts", 0)) < self.max_age)

    def _step(self, d, manifest, key, fname, args, label):
        path = d / fname
        if self._fresh(path, manifest, key):
            self.say(f"  [skip] {label}")
            return True
        t0 = time.time()
        for stale in (Path(str(path) + ".partial"), Path(str(path) + ".err")):
            stale.unlink(missing_ok=True)
        ok, err = self._run(args, path)
        manifest[key] = {"ok": ok, "ts": time.time(), "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "cmd": " ".join(["pscli"] + args), "err": err}
        if ok:
            nlines = sum(1 for _ in path.open()) if fname.endswith(".jsonl") else 1
            self.say(f"  [ok]   {label}  ({nlines:,} lines, {time.time() - t0:.0f}s)")
        else:
            self.failures.append(f"{d.name}/{fname}: {err}")
            self.say(f"  [FAIL] {label}: {err[:140]}")
        return ok

    # ------------------------------------------------------------------ per cluster
    def collect_cluster(self, cluster):
        name, uid = cluster["name"], cluster["uid"]
        d = self.cfg.data(name)
        d.mkdir(parents=True, exist_ok=True)
        mpath = d / "collect-manifest.json"
        manifest = jload(mpath, {}) or {}
        self.say(f"=== {name} ({uid}) ===")
        S = lambda key, fname, args, label: self._step(d, manifest, key, fname, args, f"{name}: {label}")  # noqa: E731
        S("cluster", "cluster.json", ["-o", "json", "clusters", "get", "-c", uid], "cluster record")
        S("summary", "summary.json", ["-o", "json", "workloads", "summary", "-c", uid, "-w", "30d"], "summary")
        S("workloads", "workloads-all.jsonl", ["-o", "jsonl", "workloads", "list", "-c", uid, "-V", "all"], "workloads (-V all)")
        S("audit", "audit.jsonl", ["-o", "jsonl", "automation", "audit-logs", "-c", uid, "--all", "--since", "720h"], "automation audit log (30d)")
        S("nodegroups", "nodegroups.jsonl", ["-o", "jsonl", "nodegroups", "list", "-c", uid, "--all"], "node groups")
        self._unevictable(d, manifest, name, uid)
        with mpath.open("w") as fh:
            json.dump(manifest, fh, indent=1)
        if self.compress:
            self._gzip(d)
        return name

    def _unevictable(self, d, manifest, name, uid):
        full = d / "unevictable-full.jsonl"
        top = d / "unevictable-topgroups.jsonl"
        if not self.refresh and ((exists(full) and manifest.get("unevictable", {}).get("ok")) or (exists(top) and manifest.get("unevictable_top", {}).get("ok"))):
            self.say(f"  [skip] {name}: unevictable (already collected)")
            return
        self.say(f"  {name}: unevictable pods, cluster-wide pull ...")
        ok, err = self._run(["-o", "jsonl", "unevictable", "list", "-c", uid, "--all"], full)
        manifest["unevictable"] = {"ok": ok, "ts": time.time(), "err": err}
        if ok:
            self.say(f"  [ok]   {name}: unevictable cluster-wide ({sum(1 for _ in full.open()):,} lines)")
            return
        self.say(f"  [warn] {name}: cluster-wide failed ({err[:100]}); falling back to top-{self.top_groups} node groups by idle cost")
        groups = sorted(jl(d / "nodegroups.jsonl"), key=lambda g: -((g.get("cost") or {}).get("idle_total") or 0))[: self.top_groups]
        tmp = Path(str(top) + ".tmp")
        errs = 0
        with tmp.open("w") as out:
            for i, g in enumerate(groups, 1):
                gid = g.get("id") or g.get("name")
                one = d / ".ug.out"
                gok, gerr = self._run(["-o", "jsonl", "unevictable", "list", "-c", uid, "-g", gid, "--all"], one)
                if gok:
                    out.write(one.read_text())
                else:
                    errs += 1
                if i % 25 == 0:
                    self.say(f"    ... {i}/{len(groups)}")
        (d / ".ug.out").unlink(missing_ok=True)
        tmp.replace(top)
        manifest["unevictable_top"] = {"ok": True, "ts": time.time(), "failed_groups": errs, "groups": len(groups)}
        self.say(f"  [ok]   {name}: unevictable top-{self.top_groups} ({sum(1 for _ in top.open()):,} lines, {errs} group failures) - PARTIAL")

    def _gzip(self, d):
        import gzip
        for f in ("workloads-all.jsonl", "nodegroups.jsonl", "unevictable-full.jsonl"):
            p = d / f
            if p.exists() and p.stat().st_size > 1_000_000:
                with p.open("rb") as src, gzip.open(str(p) + ".gz", "wb") as dst:
                    shutil.copyfileobj(src, dst)
                p.unlink()
                self.say(f"  [gz]   {d.name}/{f}")

    # ------------------------------------------------------------------ entry
    def run(self):
        ok = subprocess.run([self.pscli, "auth", "status"], env=self.env, capture_output=True).returncode == 0
        if not ok:
            raise SystemExit(f"pscli is not authenticated (profile={self.cfg.get('profile') or 'default'}). Run: pscli auth status")
        clusters = [c for c in self.cfg["clusters"] if not self.only or c["name"] in self.only]
        workers = max(1, min(len(clusters), 3))
        with ThreadPoolExecutor(max_workers=workers) as ex:
            list(ex.map(self.collect_cluster, clusters))
        self.say("\nCOLLECTION COMPLETE")
        if self.failures:
            self.say("Steps that failed (re-run to retry only these):")
            for f in self.failures:
                self.say("  - " + f)
        return not self.failures
