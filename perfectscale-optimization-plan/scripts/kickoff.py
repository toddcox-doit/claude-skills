#!/usr/bin/env python3
"""Open a tenant's POV kickoff pages with a one-click "update the plan" button.

  Double-click "Open kickoff pages.command" in the tenant folder, or from any folder:
  python3 <tenant folder>/skill/perfectscale-optimization-plan/scripts/kickoff.py

Starts a small web server on this computer only (127.0.0.1), opens the current version's kickoff
page in the browser, and keeps running until Ctrl+C. Opened this way, every CRD set the page
generates is saved, append-only, under crds/<cluster>/ in the tenant folder (psplan/crdstore.py),
and the page's CRD history section lists, diffs, downloads and marks them, with the exact objects
to delete for a rollback. It also accepts `run_plan.py --pin-hpa` requests for namespaces the page
offers HPA automation for.

A kickoff page opened straight from disk cannot run anything - browsers forbid it - so this helper
is what makes the button work. It is deliberately narrow:
  - listens on 127.0.0.1 only, never on the network
  - serves only the reports folder, read-only
  - accepts requests only with this run's secret token, from its own origin and host name
  - writes only CRD sets under crds/<cluster>/, for the cluster of the page that sent them, with
    file names and contents of the shapes the page generates
  - runs only `run_plan.py --pin-hpa`, with valid Kubernetes namespace names the page offers -
    never an arbitrary command
  - runs one update at a time
"""
import argparse
import http.server
import json
import re
import secrets
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from psplan import crdstore, versioning  # noqa: E402

TYPES = {".html": "text/html; charset=utf-8", ".md": "text/markdown; charset=utf-8", ".json": "application/json",
         ".csv": "text/csv; charset=utf-8", ".yaml": "text/yaml; charset=utf-8",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
KICKOFF = "-pov-automation-kickoff-"
MAX_BODY = 64 * 1024
MAX_SAVE = 32 * 1024 * 1024        # a generated CRD set: thousands of small YAML files at most


def page_prefix(name):
    """'acme-prod-a-pov-automation-kickoff-2026-11-20-v2.html' -> 'acme-prod-a': the same cluster
    across versions, so the helper can open the new version of the page you were on."""
    return name.split(KICKOFF, 1)[0]


class Helper:
    def __init__(self, config_path, port=0, run_plan=None):
        self.config = Path(config_path).resolve()
        if not self.config.is_file():
            raise SystemExit(f"kickoff: no config at {config_path}")
        self.root = versioning.tenant_root(self.config)
        self.run_plan = run_plan or [sys.executable, str(HERE / "run_plan.py")]
        self.token = secrets.token_urlsafe(24)
        self.jobs, self.lock, self.busy = {}, threading.Lock(), False
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self.port = self.httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    # ---- what is on disk
    def raw(self):
        return json.loads(self.config.read_text())

    def reports(self):
        """The folder that holds every version's outputs (out_dir's parent, e.g. reports/)."""
        return (self.root / self.raw()["out_dir"]).resolve().parent

    def pages(self):
        return sorted((self.root / self.raw()["out_dir"]).glob(f"*{KICKOFF}*.html"))

    def url_for(self, p):
        return "/" + p.resolve().relative_to(self.reports()).as_posix()

    def file_for(self, url_path):
        """Map a URL path to a file under reports/, or None. Never escapes the folder."""
        base = self.reports()
        p = (base / unquote(url_path).lstrip("/")).resolve()
        return p if p.is_file() and p.is_relative_to(base) else None

    def page_data(self, page):
        m = re.search(r"<script type=application/json id=povdata>(.*?)</script>", page.read_text(encoding="utf-8"), re.S)
        return json.loads(m.group(1).replace("<\\/", "</")) if m else {}

    def allowed_ns(self, page):
        """Namespaces the page offers HPA automation for, read from the page's own data."""
        return {n["ns"] for n in self.page_data(page).get("namespaces", []) if n.get("hpa_n")}

    def cluster_of(self, page_path):
        """The cluster a kickoff page belongs to (its data's slug), so CRD history is always filed
        under the page that generated it, never under a name a request makes up."""
        page = self.file_for(str(page_path or ""))
        if page is None or KICKOFF not in page.name:
            raise ValueError("need the kickoff page the request came from")
        slug = self.page_data(page).get("slug") or ""
        if not crdstore.SLUG_RX.match(slug):
            raise ValueError("that page carries no cluster")
        return slug

    # ---- the one job
    def start(self, namespaces, page):
        with self.lock:
            if self.busy:
                return None
            self.busy = True
            jid = secrets.token_hex(6)
            self.jobs[jid] = {"state": "running", "step": "starting", "page": page.name}
        threading.Thread(target=self._run, args=(jid, namespaces, page), daemon=True).start()
        return jid

    def _run(self, jid, namespaces, page):
        job = self.jobs[jid]
        lines = []
        try:
            print(f"kickoff: HPA confirmed pinned: {', '.join(namespaces)} - updating the plan", flush=True)
            proc = subprocess.Popen(self.run_plan + ["--config", str(self.config), "--pin-hpa", *namespaces],
                                    cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                line = line.rstrip()
                lines.append(line)
                if line.startswith(("version ", "verify:")):
                    job["step"] = line
                elif line.startswith("wrote "):
                    job["step"] = "writing " + Path(line[6:]).name
            rc = proc.wait()
            new = [p for p in self.pages() if page_prefix(p.name) == page_prefix(page.name)] or self.pages()
            ver = self.raw().get("version_tag")
            if rc == 0 and new:
                job.update(state="done", version=ver, url=self.url_for(new[0]))
                print(f"kickoff: {ver} is ready - {self.url_for(new[0])}", flush=True)
            else:
                fails = [x.strip() for x in lines if "FAIL" in x or "error" in x.lower()][-5:]
                job.update(state="failed", version=ver,
                           error=("verify failed: " + "; ".join(fails)) if fails else "run_plan.py exited with code %d" % rc,
                           url=self.url_for(new[0]) if new else None)
                print("kickoff: update failed\n  " + "\n  ".join(lines[-15:]), flush=True)
        except Exception as e:  # report, never take the server down
            job.update(state="failed", error=str(e))
        finally:
            with self.lock:
                self.busy = False

    # ---- HTTP
    def _handler(self):
        helper = self

        class H(http.server.BaseHTTPRequestHandler):
            server_version = "psplan-kickoff"

            def log_message(self, *a):                    # quiet: the terminal shows job progress instead
                pass

            def send(self, code, body, ctype="application/json", extra=None):
                data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json" else body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def host_ok(self):
                # Rejects DNS rebinding: another site's name pointed at 127.0.0.1 arrives with its own Host.
                if self.headers.get("Host") not in helper.hosts:
                    self.send(403, {"error": "unexpected host"})
                    return False
                return True

            def api_ok(self):
                if not secrets.compare_digest(self.headers.get("X-PS-Token") or "", helper.token):
                    self.send(403, {"error": "missing or wrong token - reload the page from kickoff.py"})
                    return False
                o = self.headers.get("Origin")
                if o is not None and o not in helper.origins:
                    self.send(403, {"error": "unexpected origin"})
                    return False
                return True

            def do_GET(self):
                if not self.host_ok():
                    return
                path = urlparse(self.path).path
                if path == "/":
                    pages = helper.pages()
                    if not pages:
                        return self.send(404, "No kickoff pages for the current version yet. Run run_plan.py first.", "text/plain; charset=utf-8")
                    return self.send(302, b"", "text/plain", {"Location": helper.url_for(pages[0])})
                m = re.fullmatch(r"/api/job/([0-9a-f]{12})", path)
                if m:
                    if not self.api_ok():
                        return
                    job = helper.jobs.get(m.group(1))
                    return self.send(200, job) if job else self.send(404, {"error": "no such job"})
                if path in ("/api/crd-history", "/api/crd-set", "/api/crd-rollback"):
                    if not self.api_ok():
                        return
                    from urllib.parse import parse_qs
                    q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                    try:
                        slug = helper.cluster_of(q.get("page"))
                        if path == "/api/crd-history":
                            return self.send(200, {"cluster": slug, "sets": crdstore.history(helper.root, slug)})
                        if path == "/api/crd-set":
                            return self.send(200, crdstore.get(helper.root, slug, q.get("id")))
                        target = crdstore.get(helper.root, slug, q.get("id"))
                        cur = crdstore.get(helper.root, slug, q.get("from")) if q.get("from") else None
                        return self.send(200, {"id": target["id"], "from": cur["id"] if cur else None,
                                               "delete": crdstore.rollback_deletes(target["objects"], cur["objects"]) if cur else []})
                    except ValueError as e:
                        return self.send(400, {"error": str(e)})
                f = helper.file_for(path)
                if f is None:
                    return self.send(404, "Not found", "text/plain; charset=utf-8")
                body = f.read_bytes()
                if f.suffix == ".html" and KICKOFF in f.name:
                    inject = ("<script>window.PS_HELPER=" + json.dumps({"token": helper.token, "api": "/api/"}) + ";</script></head>").encode()
                    body = body.replace(b"</head>", inject, 1)
                self.send(200, body, TYPES.get(f.suffix, "application/octet-stream"))

            def do_POST(self):
                if not self.host_ok():
                    return
                path = urlparse(self.path).path
                if path not in ("/api/pin-hpa", "/api/crd-save", "/api/crd-mark"):
                    return self.send(404, {"error": "not found"})
                if not self.api_ok():
                    return
                n = int(self.headers.get("Content-Length") or 0)
                if n <= 0 or n > (MAX_SAVE if path == "/api/crd-save" else MAX_BODY):
                    return self.send(400, {"error": "bad request body"})
                if path in ("/api/crd-save", "/api/crd-mark"):
                    try:
                        req = json.loads(self.rfile.read(n))
                        slug = helper.cluster_of(req.get("page"))
                        meta = dict(req.get("meta") or {}, plan_version=helper.raw().get("version_tag"),
                                    page=Path(urlparse(str(req.get("page"))).path).name)
                        with helper.lock:            # one writer at a time: ids and "unchanged" stay exact
                            if path == "/api/crd-save":
                                r = crdstore.save(helper.root, slug, req.get("kind"), req.get("files"), meta)
                                if not r["unchanged"]:
                                    print(f"kickoff: saved CRD set {slug}/{r['id']}", flush=True)
                            else:
                                r = crdstore.mark(helper.root, slug, req.get("id"), req.get("handed"))
                        return self.send(200, r)
                    except (ValueError, AttributeError, TypeError) as e:
                        return self.send(400, {"error": str(e)})
                try:
                    req = json.loads(self.rfile.read(n))
                    names = versioning.validate_namespaces(list(req.get("namespaces") or []))
                except (ValueError, AttributeError, TypeError) as e:
                    return self.send(400, {"error": str(e)})
                page = helper.file_for(str(req.get("page") or ""))
                if not names or page is None or KICKOFF not in page.name:
                    return self.send(400, {"error": "need at least one namespace and the kickoff page it came from"})
                extra = sorted(set(names) - helper.allowed_ns(page))
                if extra:
                    return self.send(400, {"error": f"not offered for HPA confirmation on that page: {', '.join(extra)}"})
                jid = helper.start(names, page)
                if jid is None:
                    return self.send(409, {"error": "an update is already running"})
                self.send(202, {"job": jid})

            def do_PUT(self):
                self.send(405, {"error": "method not allowed"})
            do_DELETE = do_PATCH = do_PUT

        return H

    def serve(self, open_browser=True):
        url = f"http://127.0.0.1:{self.port}/"
        print(f"kickoff: {self.raw().get('customer')} kickoff pages at {url}  (this computer only; Ctrl+C to stop)", flush=True)
        if open_browser:
            threading.Timer(0.3, webbrowser.open, args=(url,)).start()
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nkickoff: stopped", flush=True)
        finally:
            self.httpd.server_close()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="the tenant config (default: the one config in the tenant folder this copy lives in)")
    ap.add_argument("--port", type=int, default=0, help="port on 127.0.0.1 (default: any free port)")
    ap.add_argument("--no-browser", action="store_true", help="print the address instead of opening a browser")
    a = ap.parse_args()
    Helper(versioning.find_config(__file__, a.config), port=a.port).serve(open_browser=not a.no_browser)


if __name__ == "__main__":
    main()
