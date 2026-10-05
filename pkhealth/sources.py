"""Download the raw sources and record where each file came from.

Every file lands in data/raw/ unchanged. data/raw/manifest.json keeps the URL,
download time, size and SHA-256 of each one, so any number in the report can
be traced back to the exact bytes it was computed from.
"""

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

USER_AGENT = "pk-health-facility-graph/0.1 (research project)"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, dest, attempts=4):
    for i in range(attempts):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=180)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
            return
        except requests.RequestException as exc:
            if i == attempts - 1:
                raise
            wait = 5 * 2**i
            print(f"  {exc.__class__.__name__} for {url}, retrying in {wait}s")
            time.sleep(wait)


def download_all(sources, raw_dir, refresh=False):
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = raw_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    for key, src in sources.items():
        dest = raw_dir / src["file"]
        if dest.exists() and not refresh:
            continue
        print(f"downloading {key}")
        fetch(src["url"], dest)
        manifest[key] = {
            "url": src["url"],
            "file": src["file"],
            "licence": src.get("licence", ""),
            "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bytes": dest.stat().st_size,
            "sha256": _sha256(dest),
        }
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest
