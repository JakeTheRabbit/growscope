"""Engine configuration: add-on options, paths, HA API access.

Runs in two modes:
- Supervised (HA add-on): SUPERVISOR_TOKEN is set, HA API via the Supervisor proxy.
- Standalone Docker (HA Core users): GROWSCOPE_HA_URL + GROWSCOPE_HA_TOKEN point
  at Home Assistant directly with a long-lived access token.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
IS_ADDON = bool(SUPERVISOR_TOKEN)

if IS_ADDON:
    HA_API = "http://supervisor/core/api"
    HA_TOKEN = SUPERVISOR_TOKEN
else:
    HA_API = os.environ.get("GROWSCOPE_HA_URL", "").rstrip("/")
    if HA_API and not HA_API.endswith("/api"):
        HA_API += "/api"
    HA_TOKEN = os.environ.get("GROWSCOPE_HA_TOKEN", "")

DATA_DIR = Path(os.environ.get("GROWSCOPE_DATA", "/data" if IS_ADDON else "./data"))
MEDIA_DIR = Path(os.environ.get("GROWSCOPE_MEDIA", "/media/growscope" if IS_ADDON else "./media/growscope"))
FRAMES_DIR = MEDIA_DIR / "frames"
SEGMENTS_DIR = MEDIA_DIR / "segments"
LAPSES_DIR = MEDIA_DIR / "timelapses"

_OPTIONS_FILE = DATA_DIR / "options.json"


def _options() -> dict:
    try:
        return json.loads(_OPTIONS_FILE.read_text())
    except (OSError, ValueError):
        return {}


OPTIONS = _options()
LOG_LEVEL = str(OPTIONS.get("log_level", "info")).upper()
SECONDS_PER_DAY = float(OPTIONS.get("seconds_per_day", 2.0))

# Ingress requests arrive only from the Supervisor gateway. Anything else is
# rejected in add-on mode so the API is never reachable off-box unauthenticated.
INGRESS_GATEWAY = "172.30.32.2"

logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


def ensure_dirs() -> None:
    for d in (DATA_DIR, FRAMES_DIR, SEGMENTS_DIR, LAPSES_DIR):
        d.mkdir(parents=True, exist_ok=True)
