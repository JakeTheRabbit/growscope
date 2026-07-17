"""Frame capture scheduler. Every 30s: for each enabled camera on an active grow,
capture if due and the lights gate passes. Frames land in
/media/growscope/frames/<grow>/<camera>/<date>/<time>.jpg"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime

from . import db, ha
from .config import FRAMES_DIR

_LOG = logging.getLogger("capture")

status: dict = {"last_tick": "", "captures": 0, "errors": 0, "last_error": ""}


def _parse_hhmm(value: str) -> dtime | None:
    try:
        hours, minutes = value.split(":")
        return dtime(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return None


async def lights_on(cam: dict) -> bool:
    """Lights gate: a lights entity beats a fixed window beats always-on."""
    if cam.get("lights_entity"):
        state = await ha.get_state(cam["lights_entity"])
        return bool(state and state.get("state") == "on")
    start = _parse_hhmm(cam.get("window_start", ""))
    end = _parse_hhmm(cam.get("window_end", ""))
    if start and end:
        now = datetime.now().time()
        if start <= end:
            return start <= now <= end
        return now >= start or now <= end  # overnight window, e.g. 22:00-10:00
    return True


def _due(cam: dict) -> bool:
    last = cam.get("last_capture_ts") or ""
    if not last:
        return True
    try:
        elapsed = (datetime.now() - datetime.fromisoformat(last)).total_seconds()
    except ValueError:
        return True
    return elapsed >= cam["interval_min"] * 60


async def capture_one(cam: dict, grow: dict) -> bool:
    jpeg = await ha.camera_jpeg(cam["entity_id"])
    if not jpeg:
        status["errors"] += 1
        status["last_error"] = f"{cam['entity_id']}: snapshot failed"
        return False
    cam_slug = cam["entity_id"].split(".", 1)[-1]
    now = datetime.now()
    day_dir = FRAMES_DIR / grow["slug"] / cam_slug / now.strftime("%Y-%m-%d")
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{now.strftime('%H%M%S')}.jpg").write_bytes(jpeg)
    db.mark_captured(cam["id"])
    status["captures"] += 1
    _LOG.info("captured %s for %s (%d bytes)", cam["entity_id"], grow["name"], len(jpeg))
    return True


async def tick() -> None:
    grows = {g["id"]: g for g in db.grows() if g["status"] == "active"}
    for cam in db.cameras():
        grow = grows.get(cam["grow_id"])
        if not grow or not cam["enabled"] or not _due(cam):
            continue
        if not await lights_on(cam):
            continue
        await capture_one(cam, grow)


async def loop() -> None:
    if not ha.configured():
        _LOG.warning("HA API not configured - capture scheduler idle. "
                     "Standalone mode needs GROWSCOPE_HA_URL and GROWSCOPE_HA_TOKEN.")
    while True:
        try:
            if ha.configured():
                await tick()
                status["last_tick"] = datetime.now().isoformat(timespec="seconds")
        except Exception:  # never let the loop die
            _LOG.exception("capture tick failed")
        await asyncio.sleep(30)
