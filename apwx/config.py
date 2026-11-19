"""Configuration loading.

Non-secret configuration lives in config/*.json (versioned with the code).
Secrets (email credentials, recipient address) are read from environment
variables at the moment they are needed and are never written to disk.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"

LEVELS = ["NONE", "INFORMATIONAL", "MODERATE", "HIGH", "CRITICAL"]
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}


def level_rank(level):
    return LEVEL_RANK.get(level or "NONE", 0)


def max_level(*levels):
    best = "NONE"
    for lv in levels:
        if level_rank(lv) > level_rank(best):
            best = lv
    return best


def shift_level(level, steps):
    idx = max(0, min(len(LEVELS) - 1, level_rank(level) + steps))
    return LEVELS[idx]


def env(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def env_flag(name, default=False):
    value = env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


class Config:
    """All non-secret configuration plus resolved runtime paths."""

    def __init__(self, config_dir=None):
        self.dir = Path(config_dir) if config_dir else CONFIG_DIR
        self.settings = load_json(self.dir / "settings.json")
        states_doc = load_json(self.dir / "states.json")
        self.all_states = {s["abbr"]: s for s in states_doc["states"]}
        self.states = {k: v for k, v in self.all_states.items() if v.get("monitored")}
        self.crops = load_json(self.dir / "crops.json")
        self.sources = load_json(self.dir / "sources.json")
        counties_path = self.dir / "counties.json"
        self.counties_doc = load_json(counties_path) if counties_path.exists() else {"counties": []}
        self.counties = {c["fips"]: c for c in self.counties_doc.get("counties", [])}
        self.state_fips = {v["fips"]: k for k, v in self.states.items()}

    # ----- lookups -------------------------------------------------------
    @property
    def monitored_states(self):
        return list(self.states.keys())

    def counties_in_state(self, abbr):
        return [c for c in self.counties.values() if c["state"] == abbr]

    def crop_ids(self):
        return [k for k in self.crops["crops"].keys()]

    def crop(self, crop_id):
        return self.crops["crops"][crop_id]

    def source_ref(self, key):
        return self.sources.get("references", {}).get(key)

    # ----- runtime paths -------------------------------------------------
    @property
    def state_dir(self):
        return Path(env("APWX_STATE_DIR", str(ROOT / "state")))

    @property
    def db_path(self):
        return Path(env("APWX_DB", str(self.state_dir / "apwx.sqlite")))

    @property
    def site_dir(self):
        return Path(env("APWX_SITE_DIR", str(self.state_dir / "docs")))

    @property
    def outbox_dir(self):
        return Path(env("APWX_OUTBOX", str(ROOT / "outbox")))

    @property
    def dashboard_url(self):
        return env("DASHBOARD_URL", self.settings.get("dashboard_url", "")) or ""

    def get(self, *path, default=None):
        node = self.settings
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node