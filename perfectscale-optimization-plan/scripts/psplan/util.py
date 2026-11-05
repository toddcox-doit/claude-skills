"""Small shared helpers. No tenant knowledge."""
import gzip
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path


def _open(p):
    """Open text file, transparently preferring <name>.gz if only the compressed copy exists."""
    p = Path(p)
    gz = p.with_name(p.name + ".gz")
    if not p.exists() and gz.exists():
        return gzip.open(gz, "rt")
    return p.open()


def exists(path):
    p = Path(path)
    return p.exists() or p.with_name(p.name + ".gz").exists()


def jl(path):
    """Read a JSONL file leniently (skips blank/bad lines). Returns [] if missing."""
    if not exists(path):
        return []
    out = []
    with _open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def jload(path, default=None):
    if not exists(path):
        return default
    p = Path(path)
    if p.exists() and p.stat().st_size == 0:
        return default
    try:
        with _open(path) as fh:
            return json.load(fh)
    except ValueError:
        return default


def parse_ts(s):
    s = (s or "")[:19]
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def usd(v):
    return f"${v:,.0f}"


def usd_signed(v):
    return f"${v:,.0f}" if v >= 0 else f"$({abs(v):,.0f})"


def pct(a, b, lt1=True):
    if not b:
        return "0%"
    p = 100.0 * a / b
    if lt1 and 0 < p < 0.5:
        return "<1%"
    return f"{p:.0f}%"


def roi_score(savings, confidence, effort_tier):
    W = {"XS": 1, "S": 2, "M": 4, "L": 8}
    C = {"high": 1.0, "medium": 0.75, "low": 0.5}
    if not savings:
        return 0
    return int(math.floor(savings * C[confidence] / W[effort_tier] + 0.5))  # half-up


def nice_date(d):
    """'2026-10-05' or datetime -> '5 October'"""
    if isinstance(d, str):
        d = datetime.fromisoformat(d[:10])
    return f"{d.day} {d.strftime('%B')}"


def long_date(d):
    if isinstance(d, str):
        d = datetime.fromisoformat(d[:10])
    return f"{d.day} {d.strftime('%B %Y')}"


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def human_list(items, conj="and"):
    items = list(items)
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]
