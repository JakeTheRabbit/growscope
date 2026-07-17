"""Frame capture scheduler. Every 30s: for each enabled camera on an active grow,
capture if due and the lights gate passes. Frames land in
/media/growscope/frames/<grow>/<camera>/<date>/<time>.jpg"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time as dtime
from pathlib import Path

from . import db, ha
from .config import FRAMES_DIR, MEDIA_DIR

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


async def _fetch_url(url: str) -> bytes | None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
        if resp.status_code == 200 and resp.content:
            return resp.content
        _LOG.warning("source url %s returned %s", url, resp.status_code)
    except httpx.HTTPError as err:
        _LOG.warning("source url fetch failed: %s", err)
    return None


def _ingest_watch_dir(cam: dict, grow: dict) -> int:
    """Move new images from a watch folder into the frame store, dated by file mtime.
    Covers SMB inboxes and anything that drops stills on disk."""
    watch = Path(cam["watch_dir"])
    if not watch.is_absolute():
        watch = MEDIA_DIR / cam["watch_dir"]  # "inbox/f2" means /media/growscope/inbox/f2
    if not watch.is_dir():
        return 0
    moved = 0
    cam_slug = (cam["entity_id"] or "folder").split(".", 1)[-1] or "folder"
    for f in sorted(watch.iterdir()):
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png") or not f.is_file():
            continue
        taken = datetime.fromtimestamp(f.stat().st_mtime)
        day_dir = FRAMES_DIR / grow["slug"] / cam_slug / taken.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        target = day_dir / f"{taken.strftime('%H%M%S')}_{f.name}"
        try:
            f.replace(target)
            moved += 1
        except OSError as err:
            _LOG.warning("watch ingest failed for %s: %s", f, err)
    if moved:
        db.mark_captured(cam["id"])
        status["captures"] += moved
        _LOG.info("ingested %d frames from %s for %s", moved, watch, grow["name"])
    return moved


async def capture_one(cam: dict, grow: dict) -> bool:
    if cam.get("watch_dir"):
        return _ingest_watch_dir(cam, grow) >= 0
    if cam.get("source_url"):
        jpeg = await _fetch_url(cam["source_url"])
    else:
        jpeg = await ha.camera_jpeg(cam["entity_id"])
    if not jpeg:
        status["errors"] += 1
        status["last_error"] = f"{cam['entity_id'] or cam.get('source_url')}: snapshot failed"
        return False
    cam_slug = (cam["entity_id"] or "url").split(".", 1)[-1] or "url"
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
        _LOG.warning("HA API not configured - entity cameras and lights gates are off. "
                     "URL and watch-folder sources still run. Standalone mode needs "
                     "GROWSCOPE_HA_URL and GROWSCOPE_HA_TOKEN for the rest.")
    while True:
        try:
            await tick()
            status["last_tick"] = datetime.now().isoformat(timespec="seconds")
        except Exception:  # never let the loop die
            _LOG.exception("capture tick failed")
        await asyncio.sleep(30)
