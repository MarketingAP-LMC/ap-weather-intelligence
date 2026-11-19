"""HTTP client: retries with exponential backoff, gzip, timeouts, per-source health.

Only the Python standard library is used so the platform runs anywhere
(GitHub Actions runner, a laptop, or any container) without extra installs.
"""
import gzip
import http.client
import json
import os
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .timeutil import iso, utcnow

DEFAULT_UA = "AP-AgWeather-Intelligence/1.0 (automated agricultural weather monitor)"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class FetchError(Exception):
    def __init__(self, message, status=None, url=None):
        super().__init__(message)
        self.status = status
        self.url = url


class SourceHealth:
    """Thread-safe counters of successes/failures per data source for one run."""

    def __init__(self):
        self._lock = threading.Lock()
        self.stats = {}

    def record(self, source, ok, latency_ms=None, error=None):
        now = iso(utcnow())
        with self._lock:
            s = self.stats.setdefault(
                source,
                {"ok": 0, "err": 0, "last_attempt": None, "last_success": None,
                 "last_error": None, "last_error_time": None, "latency_ms": None},
            )
            s["last_attempt"] = now
            if ok:
                s["ok"] += 1
                s["last_success"] = now
                s["latency_ms"] = round(latency_ms or 0.0, 1)
            else:
                s["err"] += 1
                s["last_error"] = (error or "error")[:300]
                s["last_error_time"] = now

    def snapshot(self):
        with self._lock:
            return json.loads(json.dumps(self.stats))

    def reset(self):
        with self._lock:
            self.stats = {}


HEALTH = SourceHealth()


def user_agent():
    return os.environ.get("NWS_USER_AGENT", "").strip() or DEFAULT_UA


def build_url(url, params=None):
    if not params:
        return url
    return url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)


def _retry_after(value):
    try:
        return min(60.0, float(value))
    except (TypeError, ValueError):
        return None


def fetch(url, params=None, json_body=None, accept=None, timeout=45, retries=3,
          source="other", headers=None, record=True):
    """GET (or POST with json_body) and return the response body as bytes."""
    full = build_url(url, params)
    h = {"User-Agent": user_agent(), "Accept-Encoding": "gzip"}
    if accept:
        h["Accept"] = accept
    if headers:
        h.update(headers)
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        h["Content-Type"] = "application/json"
    last_err, last_status = None, None
    for attempt in range(retries + 1):
        t0 = time.monotonic()
        wait = None
        try:
            req = urllib.request.Request(full, data=data, headers=h,
                                         method="POST" if data is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
                    body = gzip.decompress(body)
            if record:
                HEALTH.record(source, True, (time.monotonic() - t0) * 1000.0)
            return body
        except urllib.error.HTTPError as e:
            last_status = e.code
            last_err = f"HTTP {e.code} {e.reason}"
            if e.code not in RETRY_STATUS:
                if record:
                    HEALTH.record(source, False, error=last_err)
                raise FetchError(last_err, e.code, full) from None
            wait = _retry_after(e.headers.get("Retry-After") if e.headers else None)
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                http.client.HTTPException) as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < retries:
            delay = wait if wait is not None else min(20.0, 1.5 * (2 ** attempt)) + random.uniform(0, 1.0)
            time.sleep(delay)
    if record:
        HEALTH.record(source, False, error=last_err)
    raise FetchError(f"{last_err} after {retries + 1} attempts", last_status, full)


def fetch_json(url, **kwargs):
    return json.loads(fetch(url, **kwargs).decode("utf-8"))


def fetch_text(url, encoding="utf-8", **kwargs):
    return fetch(url, **kwargs).decode(encoding, errors="replace")