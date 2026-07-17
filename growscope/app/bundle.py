"""Grow bundles: one zip holding the grow, its journal, recipe, photos, series
export, and current timelapses. Export yours, import someone else's, replay
your run against it."""
from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from . import db, history, influx, lapse
from .config import MEDIA_DIR

_LOG = logging.getLogger("bundle")

BUNDLE_VERSION = 1


async def export(grow: dict) -> bytes:
    from . import recipes as recipes_mod
    start, end = recipes_mod.grow_window(grow)
    start_ms = int(datetime(start.year, start.month, start.day,
                            tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime(end.year, end.month, end.day, 23, 59, 59,
                          tzinfo=timezone.utc).timestamp() * 1000)

    entities = db.get_setting("recorded_entities", [])
    series = await history.series(entities, start_ms, end_ms, "5m") if entities else {}

    meta = {
        "bundle_version": BUNDLE_VERSION,
        "exported": datetime.now().isoformat(timespec="seconds"),
        "grow": {k: grow.get(k) for k in
                 ("name", "room", "start_date", "flip_date", "chop_date", "status")},
        "journal": [{k: e[k] for k in ("ts", "kind", "title", "note", "photo")}
                    for e in db.journal(grow["id"])],
        "recipe": db.recipe(grow["recipe_id"]) if grow.get("recipe_id") else None,
        "photos": db.photos(grow["id"]),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("grow.json", json.dumps(meta, indent=2))
        if series:
            z.writestr("series.json", json.dumps(series))
        for photo in meta["photos"]:
            src = MEDIA_DIR / photo["path"]
            if src.exists():
                z.write(src, photo["path"])
        for item in lapse.list_timelapses():
            if item["name"].startswith(grow["slug"] + "_"):
                src = MEDIA_DIR / "timelapses" / item["name"]
                manifest = src.with_suffix(".json")
                z.write(src, f"timelapses/{item['name']}")
                if manifest.exists():
                    z.write(manifest, f"timelapses/{manifest.name}")
    _LOG.info("bundle exported for %s (%d bytes)", grow["name"], buf.tell())
    return buf.getvalue()


async def import_bundle(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        if "grow.json" not in names:
            return {"ok": False, "detail": "not a GrowScope bundle (no grow.json)"}
        meta = json.loads(z.read("grow.json"))
        g = meta["grow"]
        grow = db.add_grow(f"{g['name']} (imported)", g.get("room", ""),
                           g["start_date"], g.get("flip_date"))
        db.update_grow(grow["id"], {"chop_date": g.get("chop_date"),
                                    "status": g.get("status") or "archived"})
        if meta.get("recipe"):
            recipe = db.save_recipe(meta["recipe"]["name"] + " (imported)",
                                    meta["recipe"]["params"])
            db.update_grow(grow["id"], {"recipe_id": recipe["id"]})
        for entry in meta.get("journal", []):
            db.add_journal(grow["id"], entry["ts"], entry.get("kind", "note"),
                           entry.get("title", ""), entry.get("note", ""),
                           entry.get("photo", ""))
        grow = db.grow(grow["id"])
        copied = 0
        for name in names:
            if name.startswith("photos/") or name.startswith("timelapses/"):
                # Photos keep their bundle path but under the new grow's slug;
                # timelapses get the new slug prefix so they list under this grow.
                content = z.read(name)
                if name.startswith("photos/"):
                    parts = Path(name)
                    target_rel = f"photos/{grow['slug']}/{parts.name}"
                else:
                    old_name = Path(name).name
                    tail = old_name.split("_", 1)[-1]
                    target_rel = f"timelapses/{grow['slug']}_{tail}"
                target = MEDIA_DIR / target_rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                copied += 1
        for photo in meta.get("photos", []):
            db.add_photo(grow["id"], photo["ts"],
                         f"photos/{grow['slug']}/{Path(photo['path']).name}",
                         photo.get("source", "import"))
        if "series.json" in names:
            series = json.loads(z.read("series.json"))
            lines = []
            for entity, points in series.items():
                tag = influx._escape_tag(f"import_{grow['slug']}_{entity}")
                for ts_ms, value in points:
                    lines.append(f"growscope,entity_id={tag} value={value} {int(ts_ms / 1000)}")
            await influx.write_lines(lines)
        return {"ok": True, "grow_id": grow["id"], "files": copied,
                "detail": f"imported as '{grow['name']}'"}
