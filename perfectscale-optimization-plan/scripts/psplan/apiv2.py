"""Read-only client for PerfectScale's public API v2, for the data pscli (v1 only) cannot reach.

pscli 1.2.0 calls /public/v1 only, and its typed models drop fields the plan needs. The public v2
workloads endpoint (GET /public/v2/clusters/{uid}/workloads, published 2026-08) adds, per workload:

  scaling.hpaTargets   the HPA's trigger metrics and targets ({"cpu": {"utilization": 50},
                       "istio_requests_per_second": {"average": 20}}); empty = no HPA
  automation.status    PerfectScale's automation eligibility (NotEnabled / DisabledByUser /
                       NotSupported ...), the same values the PodFit browser pass reads
  maturity             whether the recommendation has enough data yet
  optimizationPolicy   with its source level (cluster / namespace / workload)

It has no HPA min/max replicas, so a pinned HPA (min == max) looks like any CPU/memory HPA here.

Credentials are the same service token pscli uses (Organization Settings > API Tokens, Read Only
role). They are read from, in order: PERFECTSCALE_CLIENT_ID / PERFECTSCALE_CLIENT_SECRET in the
environment; the config's `api_credentials_file`; ~/.perfectscale/<profile>.env. A credentials
file must be readable by its owner only. Neither the credentials nor the token are ever logged,
written, or put in an exception message. scripts/set_api_credentials.sh writes the file safely.
"""
import json
import os
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://api.app.perfectscale.io"


class ApiError(RuntimeError):
    """A failed API call. Messages carry the HTTP status and path only, never a credential."""


def _read_env_file(path):
    p = Path(os.path.expanduser(str(path)))
    if not p.exists():
        return None, f"{p} not found"
    # Owner-only is enforceable on macOS/Linux; on Windows the file inherits the user profile's ACL
    # (other users already excluded) and st_mode cannot express it, so the check is POSIX-only.
    if os.name == "posix" and p.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return None, f"{p} is readable by other users; run: chmod 600 {p}"
    vals = {}
    for line in p.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    cid = vals.get("PS_ID") or vals.get("PERFECTSCALE_CLIENT_ID")
    sec = vals.get("PS_SECRET") or vals.get("PERFECTSCALE_CLIENT_SECRET")
    if not cid or not sec:
        return None, f"{p} has no client ID and secret"
    return (cid, sec), None


def load_credentials(cfg):
    """(client_id, client_secret) or (None, reason). Never raises with a credential in the text."""
    cid, sec = os.environ.get("PERFECTSCALE_CLIENT_ID"), os.environ.get("PERFECTSCALE_CLIENT_SECRET")
    if cid and sec:
        return (cid, sec), None
    if cfg.get("api_credentials_file"):
        return _read_env_file(cfg["api_credentials_file"])
    default = Path.home() / ".perfectscale" / f"{cfg.get('profile') or 'default'}.env"
    if default.exists():
        return _read_env_file(default)
    return None, ("no API credentials for profile '" + str(cfg.get("profile") or "default") + "' "
                  "(run: new_tenant.py auth --profile " + str(cfg.get("profile") or "default") + ")")


class PublicApi:
    def __init__(self, creds, base=DEFAULT_BASE, limiter=None, opener=None, max_retries=5, backoff=10):
        self._cid, self._secret = creds
        self.base = base.rstrip("/")
        self.limiter = limiter
        self.opener = opener or urllib.request.urlopen
        self.max_retries, self.backoff = max_retries, backoff
        self._token = None
        self._token_until = 0.0

    def __repr__(self):  # never show credentials, even by accident in a traceback
        return f"PublicApi(base={self.base!r})"

    def _request(self, method, path, body=None, token=None):
        url = self.base + path
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        n = 0
        while True:
            if self.limiter:
                self.limiter.wait()
            try:
                with self.opener(req, timeout=180) as r:
                    return r.status, r.read()
            except urllib.error.HTTPError as e:
                status = e.code
                e.close()
            except urllib.error.URLError as e:
                raise ApiError(f"{method} {path}: network error ({type(e.reason).__name__})") from None
            n += 1
            if status == 429 and n <= self.max_retries:
                wait = min(self.backoff * (2 ** (n - 1)), 60)
                if self.limiter:
                    self.limiter.penalize(wait)
                else:
                    time.sleep(wait)
                continue
            return status, b""

    def token(self):
        if self._token and time.time() < self._token_until - 60:
            return self._token
        status, raw = self._request("POST", "/public/v1/auth/public_auth",
                                    {"client_id": self._cid, "client_secret": self._secret})
        if status != 200:
            raise ApiError(f"token exchange failed: HTTP {status}")
        body = json.loads(raw)
        body = body.get("data") or body             # the live API wraps the token in {"data": ...}
        tok = body.get("access_token")
        if not tok:
            raise ApiError("token exchange returned no access_token")
        self._token = tok
        self._token_until = time.time() + float(body.get("expires_in") or 3600)
        return tok

    def get(self, path, params=None):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        status, raw = self._request("GET", path + q, token=self.token())
        if status == 401:                            # token expired mid-run: refresh once
            self._token = None
            status, raw = self._request("GET", path + q, token=self.token())
        if status != 200:
            raise ApiError(f"GET {path}: HTTP {status}")
        return json.loads(raw)

    def workloads_v2(self, uid, period="P30D"):
        return self.get(f"/public/v2/clusters/{urllib.parse.quote(uid)}/workloads", {"period": period})
