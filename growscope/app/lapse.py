"""Timelapse assembly. Each day of frames becomes one small segment encode, the
current timelapse is a concat of segments - so rebuilding after new frames is
seconds of work, never a full re-encode.

Pacing is day-normalized: every day occupies SECONDS_PER_DAY seconds of video,
so day N sits at the same timestamp in every grow's timelapse. That is what
makes side-by-side replay possible later.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from . import db
from .config import FRAMES_DIR, LAPSES_DIR, SECONDS_PER_DAY, SEGMENTS_DIR

_LOG = logging.getLogger("lapse")

status: dict = {"building": False, "last_build": "", "last_result": ""}

# Short GOP so scrubbing lands on keyframes often - matters for replay later.
_ENCODE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
           "-maxrate", "3M", "-bufsize", "6M", "-g", "15", "-pix_fmt", "yuv420p",
           "-vf", "scale=960:540:force_original_aspect_ratio=decrease,"
                  "pad=960:540:(ow-iw)/2:(oh-ih)/2"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def _build_segment(day_dir: Path, seg_path: Path) -> bool:
    frames = sorted(day_dir.glob("*.jpg"))
    if not frames:
        return False
    newest = max(f.stat().st_mtime for f in frames)
    if seg_path.exists() and seg_path.stat().st_mtime >= newest:
        return True  # up to date
    seg_path.parent.mkdir(parents=True, exist_ok=True)
    framerate = max(len(frames) / SECONDS_PER_DAY, 1.0)
    proc = _run(["ffmpeg", "-y", "-framerate", f"{framerate:.4f}",
                 "-pattern_type", "glob", "-i", str(day_dir / "*.jpg"),
                 *_ENCODE, str(seg_path)])
    if proc.returncode != 0:
        _LOG.error("segment %s failed: %s", seg_path.name, proc.stderr[-400:])
        seg_path.unlink(missing_ok=True)
        return False
    return True


def _concat(segments: list[Path], out_path: Path) -> bool:
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("".join(f"file '{s.as_posix()}'\n" for s in segments))
    proc = _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), "-c", "copy", str(out_path)])
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        _LOG.error("concat %s failed: %s", out_path.name, proc.stderr[-400:])
        return False
    return True


def build_grow(grow: dict) -> list[str]:
    """Build/refresh every camera's current timelapse for one grow. Blocking - run in a thread."""
    built = []
    grow_frames = FRAMES_DIR / grow["slug"]
    if not grow_frames.exists():
        return built
    for cam_dir in sorted(p for p in grow_frames.iterdir() if p.is_dir()):
        seg_root = SEGMENTS_DIR / grow["slug"] / cam_dir.name
        segments = []
        for day_dir in sorted(p for p in cam_dir.iterdir() if p.is_dir()):
            seg_path = seg_root / f"{day_dir.name}.mp4"
            if _build_segment(day_dir, seg_path):
                segments.append(seg_path)
        if not segments:
            continue
        out_path = LAPSES_DIR / f"{grow['slug']}_{cam_dir.name}_current.mp4"
        if _concat(segments, out_path):
            built.append(out_path.name)
    return built


async def build_grow_async(grow: dict) -> list[str]:
    status["building"] = True
    try:
        built = await asyncio.to_thread(build_grow, grow)
        status["last_build"] = datetime.now().isoformat(timespec="seconds")
        status["last_result"] = ", ".join(built) if built else "nothing to build"
        return built
    finally:
        status["building"] = False


def list_timelapses() -> list[dict]:
    out = []
    for f in sorted(LAPSES_DIR.glob("*.mp4")):
        stat = f.stat()
        out.append({"name": f.name, "size_mb": round(stat.st_size / 1e6, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")})
    return out


async def nightly_loop() -> None:
    """Rebuild all active grows shortly after midnight - yesterday's segment gets
    finalized and the current lapse is fresh every morning."""
    while True:
        now = datetime.now()
        target = now.replace(hour=0, minute=15, second=0, microsecond=0)
        if target <= now:
            target = target.replace(day=now.day)
            wait = (target.timestamp() + 86400) - now.timestamp()
        else:
            wait = (target - now).total_seconds()
        await asyncio.sleep(wait)
        try:
            for grow in db.grows(include_archived=False):
                await build_grow_async(grow)
        except Exception:
            _LOG.exception("nightly build failed")
