"""Persist the platform database between scheduled cloud runs as versioned GitHub
Release assets (release tag `state-db`). No git history growth, keeps the last N
versions as backups, uses only the workflow's GITHUB_TOKEN.

  python tools/state_store.py restore   # download newest snapshot into $APWX_STATE_DIR
  python tools/state_store.py save      # upload current DB, prune old versions

Environment: GH_TOKEN (or GITHUB_TOKEN), GITHUB_REPOSITORY, APWX_STATE_DIR.
"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"
TAG = "state-db"
KEEP = 5


def _token():
    t = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not t:
        sys.exit("GH_TOKEN/GITHUB_TOKEN not set")
    return t


def _req(method, url, data=None, headers=None, raw=False, timeout=120):
    h = {"Accept": "application/vnd.github+json", "User-Agent": "ap-weather-state-store", "X-GitHub-Api-Version": "2022-11-28"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    req.add_unredirected_header("Authorization", f"Bearer {_token()}")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return body if raw else (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (500, 502, 503, 504) and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            raise


def _release(repo, create=False):
    rel = _req("GET", f"{API}/repos/{repo}/releases/tags/{TAG}")
    if rel or not create:
        return rel
    body = json.dumps({"tag_name": TAG, "name": "Platform state (automated)", "prerelease": True,
                       "body": "Automated SQLite snapshots of the AP Weather Intelligence platform state. Do not edit."}).encode()
    return _req("POST", f"{API}/repos/{repo}/releases", data=body, headers={"Content-Type": "application/json"})


def _assets(repo, rel):
    out = _req("GET", f"{API}/repos/{repo}/releases/{rel['id']}/assets?per_page=100") or []
    return sorted([a for a in out if a["name"].startswith("apwx-") and a["name"].endswith(".sqlite.gz")],
                  key=lambda a: a["name"], reverse=True)


def restore(repo, state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    rel = _release(repo)
    if not rel:
        print("no state release yet - starting with an empty database")
        return 0
    assets = _assets(repo, rel)
    for a in assets:
        try:
            data = _req("GET", f"{API}/repos/{repo}/releases/assets/{a['id']}", headers={"Accept": "application/octet-stream"}, raw=True)
            raw = gzip.decompress(data)
            tmp = state_dir / "apwx.sqlite.tmp"
            tmp.write_bytes(raw)
            con = sqlite3.connect(str(tmp))
            ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            con.close()
            if ok:
                shutil.move(str(tmp), str(state_dir / "apwx.sqlite"))
                print(f"restored {a['name']} ({len(raw) / 1e6:.1f} MB)")
                return 0
            print(f"integrity check failed for {a['name']}, trying an older snapshot")
        except Exception as e:  # noqa: BLE001
            print(f"could not restore {a['name']}: {e}")
    print("no usable snapshot found - starting with an empty database")
    return 0


def save(repo, state_dir):
    db = state_dir / "apwx.sqlite"
    if not db.exists():
        print("no database to save")
        return 0
    rel = _release(repo, create=True)
    name = f"apwx-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.sqlite.gz"
    data = gzip.compress(db.read_bytes(), compresslevel=6)
    _req("POST", f"{UPLOADS}/repos/{repo}/releases/{rel['id']}/assets?name={name}", data=data,
         headers={"Content-Type": "application/gzip"}, timeout=300)
    print(f"saved {name} ({len(data) / 1e6:.1f} MB compressed)")
    for a in _assets(repo, rel)[KEEP:]:
        _req("DELETE", f"{API}/repos/{repo}/releases/assets/{a['id']}")
        print(f"pruned {a['name']}")
    return 0


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("restore", "save"):
        sys.exit(__doc__)
    repo = os.environ.get("GITHUB_REPOSITORY") or sys.exit("GITHUB_REPOSITORY not set")
    state_dir = Path(os.environ.get("APWX_STATE_DIR", "state"))
    return restore(repo, state_dir) if sys.argv[1] == "restore" else save(repo, state_dir)


if __name__ == "__main__":
    sys.exit(main())