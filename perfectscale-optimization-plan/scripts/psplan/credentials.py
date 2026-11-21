"""One credential entry per tenant, for both data paths: pscli (public API v1) and the collector's
public API v2 step (HPA triggers, automation status, maturity).

A PerfectScale Read Only service token is a client ID + secret. This module asks for it once,
checks it against the API before keeping anything, then:

  * saves it to ~/.perfectscale/<profile>.env for the v2 step (owner-only on macOS/Linux; on
    Windows the file inherits the user profile's ACL, which already excludes other users), and
  * installs it into pscli's own profile with `pscli auth login`.

pscli reads the client ID from stdin but insists on a terminal for the secret, so on macOS/Linux
the login runs through a pseudo-terminal and the secret is typed into pscli's own prompt - never put
on a command line, where `ps` would show it to other users. Windows has no pseudo-terminal; there
the secret goes in pscli's -k flag, and Windows does not show another user's command line to a
non-administrator.

Nothing here prints, logs or returns the secret. Prompts happen only when a person is at a terminal
(can_prompt()); unattended runs never block waiting for input.
"""
import getpass
import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .apiv2 import DEFAULT_BASE, ApiError, PublicApi

POSIX = os.name == "posix"


def env_path(profile):
    return Path.home() / ".perfectscale" / f"{profile or 'default'}.env"


def can_prompt():
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def save_env(profile, creds):
    """Write the credentials file, creating it owner-only from the first byte (no window in which
    a default-permission file holds the secret)."""
    p = env_path(profile)
    p.parent.mkdir(parents=True, exist_ok=True)
    if POSIX:
        os.chmod(p.parent, 0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"PS_ID={creds[0]}\nPS_SECRET={creds[1]}\n")
    if POSIX:
        os.chmod(p, 0o600)
    return p


def check_api(creds, base=None):
    """(ok, message). Exchanges the credentials for a token - read-only, and the cheapest proof
    they are right before anything is saved."""
    try:
        PublicApi(creds, base=base or DEFAULT_BASE).token()
        return True, "API credentials work"
    except ApiError as e:
        return False, str(e)


def pscli_status(pscli, profile):
    """(ok, detail) for a pscli profile, from `pscli auth status`."""
    if not pscli:
        return False, "pscli not found on PATH"
    env = dict(os.environ, PERFECTSCALE_PROFILE=profile or "default")
    r = subprocess.run([pscli, "-o", "json", "auth", "status"], env=env, capture_output=True, text=True)
    if r.returncode != 0:
        return False, f"pscli profile '{profile}' is not signed in"
    return True, f"pscli profile '{profile}' is signed in"


def _read_until(fd, needle, timeout):
    buf, end = b"", time.time() + timeout
    while needle not in buf.lower() and time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
    return needle in buf.lower()


def pscli_login(pscli, profile, creds, timeout=60):
    """(ok, message). Installs the token into pscli's profile without exposing the secret."""
    if not pscli:
        return False, "pscli not found on PATH"
    cid, secret = creds
    env = dict(os.environ, PERFECTSCALE_PROFILE=profile or "default")
    if not POSIX:
        r = subprocess.run([pscli, "auth", "login", "-i", cid, "-k", secret], env=env, capture_output=True, text=True)
        ok = r.returncode == 0
    else:
        import pty
        master, slave = pty.openpty()
        p = subprocess.Popen([pscli, "auth", "login"], env=env, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        os.close(slave)
        try:
            if _read_until(master, b"client id", 15):
                os.write(master, cid.encode() + b"\n")
            if _read_until(master, b"secret", 15):
                os.write(master, secret.encode() + b"\n")
            ok = p.wait(timeout=timeout) == 0
        except subprocess.TimeoutExpired:
            p.kill()
            ok = False
        finally:
            os.close(master)
    del secret
    if not ok:
        return False, "pscli auth login did not succeed (check the token in PerfectScale and retry)"
    return pscli_status(pscli, profile)


def prompt_creds(profile, ask=input, ask_secret=getpass.getpass, out=print):
    out(f"PerfectScale API token for profile '{profile}' (Read Only role; app.perfectscale.io > avatar >")
    out("Organization Settings > API Tokens > Generate Token). The secret is not shown as you type.")
    cid = ask("  Client ID: ").strip()
    secret = ask_secret("  Client secret: ").strip()
    if not cid or not secret:
        return None
    return cid, secret


def setup(profile, pscli=None, base=None, login_pscli=True, out=print, ask=input, ask_secret=getpass.getpass, attempts=3):
    """Ask once, verify against the API, save for the v2 step and install into pscli.
    Returns the credentials, or None if the person gave up or the token never verified."""
    pscli = pscli or shutil.which("pscli")
    for n in range(1, attempts + 1):
        creds = prompt_creds(profile, ask=ask, ask_secret=ask_secret, out=out)
        if not creds:
            out("  Both values are needed.")
            continue
        ok, msg = check_api(creds, base)
        if not ok:
            out(f"  That token did not work: {msg}." + (" Try again." if n < attempts else ""))
            continue
        path = save_env(profile, creds)
        out(f"  Verified, and saved for the API v2 step: {path}")
        if login_pscli:
            ok, msg = pscli_login(pscli, profile, creds)
            out(f"  {msg}" if ok else f"  pscli login failed: {msg}")
        return creds
    return None


def offer(profile, reason, pscli=None, base=None, login_pscli=True, out=print, ask=input, ask_secret=getpass.getpass):
    """In an interactive run, offer the one-time setup; returns credentials or None. Never asks
    when nobody is at a terminal."""
    if not can_prompt():
        return None
    out(f"{reason}")
    ans = ask(f"Store the PerfectScale API token for profile '{profile}' now? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        return None
    return setup(profile, pscli=pscli, base=base, login_pscli=login_pscli, out=out, ask=ask, ask_secret=ask_secret)
