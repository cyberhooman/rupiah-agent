"""Fetch with a disk cache, retries and a stale-cache fallback."""
import json
import re
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
UA = {"User-Agent": "Mozilla/5.0 (rupiah-agent; open-source research project)"}


def fetch(url: str, ttl_hours: float = 12, as_json: bool = True):
    """Return (payload, stale). stale=True means the live fetch failed and an old copy was used."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", url)[-150:] + (".json" if as_json else ".html"))
    load = (lambda: json.loads(f.read_text("utf-8"))) if as_json else (lambda: f.read_text("utf-8"))
    if f.exists() and time.time() - f.stat().st_mtime < ttl_hours * 3600:
        return load(), False
    err = None
    for wait in (0, 2, 6):
        time.sleep(wait)
        try:
            r = httpx.get(url, headers=UA, timeout=30, follow_redirects=True)
            r.raise_for_status()
            payload = r.json() if as_json else r.text
            f.write_text(r.text, "utf-8")
            return payload, False
        except (httpx.HTTPError, ValueError) as e:
            err = e
    if f.exists():
        return load(), True
    raise RuntimeError(f"could not fetch {url}: {err}")
