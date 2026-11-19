"""Publish this project to a GitHub repository without git installed.

Uses the GitHub REST API (Git Data API) with a fine-grained personal access token
that YOU create and store in an environment variable - the script never prints it.

  1. Create an empty repository on github.com (e.g. american-prime/ap-weather-intelligence).
  2. Create a fine-grained token limited to that repository with permissions:
       Contents: Read and write, Workflows: Read and write, Metadata: Read.
  3. Run:  python tools/publish_to_github.py --repo OWNER/REPO
     and paste the token when asked (shown as *, kept in memory only). A GITHUB_TOKEN
     environment variable is used instead if one is set.
Re-running uploads a new commit with the current files (only changed files create new blobs).
"""
import argparse
import base64
import fnmatch
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"
EXCLUDE = ["state/*", "outbox/*", "reference/cache/*", "*/__pycache__/*", "__pycache__/*", "*.pyc", ".claude/*",
           "samples/*/simulation.sqlite", "*.sqlite-journal", "github_token.txt"]
TOKEN_FILE = ROOT / "github_token.txt"


_TOKEN = None


def _masked_input(prompt):
    """Read a secret from the terminal, echoing '*' per character (Windows), so a paste is visible."""
    if sys.platform != "win32":
        import getpass
        return getpass.getpass(prompt)
    import msvcrt
    print(prompt, end="", flush=True)
    chars = []
    while True:
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print()
            break
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()
            continue
        if ch in ("\b", "\x7f"):
            if chars:
                chars.pop()
                print("\b \b", end="", flush=True)
            continue
        if ch < " ":
            continue
        chars.append(ch)
        print("*", end="", flush=True)
    text = "".join(chars).replace("[200~", "").replace("[201~", "").strip()
    print(f"({len(text)} characters received)")
    return text


def token():
    """GITHUB_TOKEN from the environment / Windows user variables, otherwise ask once (kept in memory only)."""
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not t and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                t = winreg.QueryValueEx(k, "GITHUB_TOKEN")[0]
        except OSError:
            t = None
    if not t and TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8-sig", errors="ignore").strip()
        TOKEN_FILE.unlink()
        print("Read the token from github_token.txt and deleted the file.")
    if not t and sys.stdin and sys.stdin.isatty():
        t = _masked_input("Paste the GitHub token and press Enter (it shows as *): ")
    if not t:
        sys.exit("No GitHub token provided.")
    t = t.strip()
    if not t.startswith(("github_pat_", "ghp_")):
        sys.exit("That does not look like a GitHub token (fine-grained tokens start with github_pat_). Nothing was uploaded.")
    _TOKEN = t
    return t


def call(method, path, body=None, ok404=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "User-Agent": "ap-weather-publisher", "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json"})
    req.add_unredirected_header("Authorization", f"Bearer {token()}")
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404:
                return None
            if e.code in (409,) and ok404:
                return None
            if e.code in (500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            sys.exit(f"GitHub API {method} {path} failed: HTTP {e.code} {e.read()[:300]!r}")


def files():
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(ROOT).as_posix()
        if any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDE):
            continue
        out.append((rel, p))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="OWNER/REPO")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="Publish AP Weather Intelligence platform")
    args = ap.parse_args()
    repo = args.repo
    info = call("GET", f"/repos/{repo}", ok404=True)
    if info is None:
        sys.exit(f"Repository {repo} not found or token lacks access. Create it on github.com first.")
    ref = call("GET", f"/repos/{repo}/git/ref/heads/{args.branch}", ok404=True)
    if ref is None:
        # empty repository: create an initial commit through the contents API
        call("PUT", f"/repos/{repo}/contents/.github/keepalive.txt", {
            "message": "Initialize repository", "branch": args.branch,
            "content": base64.b64encode(b"init\n").decode()})
        ref = call("GET", f"/repos/{repo}/git/ref/heads/{args.branch}")
    parent = ref["object"]["sha"]
    base_tree = call("GET", f"/repos/{repo}/git/commits/{parent}")["tree"]["sha"]
    entries = []
    fl = files()
    for i, (rel, p) in enumerate(fl, 1):
        blob = call("POST", f"/repos/{repo}/git/blobs", {"content": base64.b64encode(p.read_bytes()).decode(), "encoding": "base64"})
        entries.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if i % 25 == 0:
            print(f"  uploaded {i}/{len(fl)} files")
    tree = call("POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": entries})
    commit = call("POST", f"/repos/{repo}/git/commits", {"message": args.message, "tree": tree["sha"], "parents": [parent]})
    call("PATCH", f"/repos/{repo}/git/refs/heads/{args.branch}", {"sha": commit["sha"], "force": False})
    print(f"Published {len(fl)} files to https://github.com/{repo} (commit {commit['sha'][:7]}).")
    print("Next: add the secrets listed in docs/SETUP.md, set Pages source to 'GitHub Actions', then run the workflow.")


if __name__ == "__main__":
    main()