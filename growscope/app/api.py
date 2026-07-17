"""REST API consumed by the ingress UI and the GrowScope custom integration."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Response, UploadFile
from pydantic import BaseModel

from . import bundle, capture, db, ha, history, immich, influx, journal, lapse, photos, recipes

router = APIRouter(prefix="/api")


class GrowIn(BaseModel):
    name: str
    room: str = ""
    start_date: str
    flip_date: str | None = None


class GrowPatch(BaseModel):
    name: str | None = None
    room: str | None = None
    start_date: str | None = None
    flip_date: str | None = None
    chop_date: str | None = None
    status: str | None = None
    recipe_id: int | None = None


class CameraIn(BaseModel):
    grow_id: int
    entity_id: str = ""
    interval_min: int = 10
    lights_entity: str = ""
    window_start: str = ""
    window_end: str = ""
    source_url: str = ""
    watch_dir: str = ""


class CameraPatch(BaseModel):
    entity_id: str | None = None
    interval_min: int | None = None
    lights_entity: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    enabled: int | None = None
    source_url: str | None = None
    watch_dir: str | None = None


class JournalIn(BaseModel):
    grow_id: int
    ts: str
    kind: str = "note"
    title: str
    note: str = ""


class RecipeIn(BaseModel):
    name: str
    params: dict
    id: int | None = None


class WatchesIn(BaseModel):
    watches: dict[str, int]


class ImmichConfigIn(BaseModel):
    url: str
    api_key: str


class InfluxConfig(BaseModel):
    url: str = ""
    database: str = influx.DEFAULT_DB
    username: str = ""
    password: str = ""
    org: str = ""
    token: str = ""


class EntityList(BaseModel):
    entities: list[str]


@router.get("/health")
async def health():
    return {"ok": True, "app": "growscope", "version": "0.2.0"}


@router.get("/status")
async def status():
    return {
        "ha_connected": await ha.ping(),
        "capture": capture.status,
        "lapse": lapse.status,
        "influx": influx.status(),
        "grows_active": len(db.grows(include_archived=False)),
        "recorded_entities": len(db.get_setting("recorded_entities", [])),
    }


@router.get("/grows")
async def get_grows():
    return db.grows()


@router.post("/grows")
async def post_grow(body: GrowIn):
    return db.add_grow(body.name, body.room, body.start_date, body.flip_date)


@router.patch("/grows/{grow_id}")
async def patch_grow(grow_id: int, body: GrowPatch):
    grow = db.update_grow(grow_id, body.model_dump(exclude_none=True))
    if not grow:
        raise HTTPException(404)
    return grow


@router.delete("/grows/{grow_id}")
async def remove_grow(grow_id: int):
    db.delete_grow(grow_id)
    return {"ok": True}


@router.get("/cameras")
async def get_cameras():
    return db.cameras()


@router.post("/cameras")
async def post_camera(body: CameraIn):
    if not db.grow(body.grow_id):
        raise HTTPException(400, "no such grow")
    if not (body.entity_id or body.source_url or body.watch_dir):
        raise HTTPException(400, "need a camera entity, a source URL, or a watch folder")
    return db.add_camera(body.grow_id, body.entity_id, body.interval_min,
                         body.lights_entity, body.window_start, body.window_end,
                         body.source_url, body.watch_dir)


@router.patch("/cameras/{camera_id}")
async def patch_camera(camera_id: int, body: CameraPatch):
    cam = db.update_camera(camera_id, body.model_dump(exclude_none=True))
    if not cam:
        raise HTTPException(404)
    return cam


@router.delete("/cameras/{camera_id}")
async def remove_camera(camera_id: int):
    db.delete_camera(camera_id)
    return {"ok": True}


@router.post("/cameras/{camera_id}/capture")
async def capture_now(camera_id: int):
    cam = db.camera(camera_id)
    if not cam:
        raise HTTPException(404)
    grow = db.grow(cam["grow_id"])
    if not grow:
        raise HTTPException(400, "camera has no grow")
    ok = await capture.capture_one(cam, grow)
    return {"ok": ok, "detail": "" if ok else capture.status.get("last_error", "failed")}


@router.get("/ha/entities")
async def entities(domains: str = ""):
    wanted = [d for d in domains.split(",") if d] or None
    return await ha.list_entities(wanted)


@router.get("/influx/config")
async def influx_get():
    cfg = influx.config()
    cfg["password"] = "***" if cfg["password"] else ""
    cfg["token"] = "***" if cfg["token"] else ""
    return cfg


@router.post("/influx/config")
async def influx_set(body: InfluxConfig):
    cfg = body.model_dump()
    # A masked secret coming back means "keep what you have"
    for secret in ("password", "token"):
        if cfg.get(secret) == "***":
            cfg.pop(secret)
    influx.save_config(cfg)
    return await influx.test()


@router.post("/influx/test")
async def influx_test():
    return await influx.test()


@router.post("/influx/provision")
async def influx_provision():
    return await influx.provision()


@router.get("/record/entities")
async def record_get():
    return {"entities": db.get_setting("recorded_entities", [])}


@router.post("/record/entities")
async def record_set(body: EntityList):
    cleaned = sorted({e.strip() for e in body.entities if e.strip()})
    db.set_setting("recorded_entities", cleaned)
    return {"entities": cleaned}


@router.get("/timelapses")
async def timelapses():
    return lapse.list_timelapses()


@router.post("/lapse/build/{grow_id}")
async def build(grow_id: int):
    grow = db.grow(grow_id)
    if not grow:
        raise HTTPException(404)
    built = await lapse.build_grow_async(grow)
    return {"ok": True, "built": built, "detail": lapse.status["last_result"]}


# --- history / charts ---

@router.get("/history/series")
async def history_series(entities: str, start: int, end: int, interval: str = "5m"):
    wanted = [e for e in entities.split(",") if e]
    if not wanted:
        raise HTTPException(400, "no entities")
    if interval not in ("1m", "5m", "15m", "1h", "6h", "1d"):
        raise HTTPException(400, "bad interval")
    return await history.series(wanted, start, end, interval)


@router.get("/history/states")
async def history_states(entity: str, start: int, end: int):
    return await history.states(entity, start, end)


@router.post("/record/backfill")
async def record_backfill(days: int = 10):
    return await history.backfill(min(max(days, 1), 30))


# --- journal ---

@router.get("/journal/{grow_id}")
async def journal_list(grow_id: int):
    return db.journal(grow_id)


@router.post("/journal")
async def journal_add(body: JournalIn):
    if not db.grow(body.grow_id):
        raise HTTPException(400, "no such grow")
    return db.add_journal(body.grow_id, body.ts, body.kind, body.title, body.note)


@router.delete("/journal/{entry_id}")
async def journal_delete(entry_id: int):
    db.delete_journal(entry_id)
    return {"ok": True}


@router.get("/journal-watches")
async def watches_get():
    return {"watches": journal.watches()}


@router.post("/journal-watches")
async def watches_set(body: WatchesIn):
    journal.set_watches(body.watches)
    return {"watches": journal.watches()}


# --- photos ---

@router.get("/photos/{grow_id}")
async def photos_list(grow_id: int):
    return db.photos(grow_id)


@router.post("/photos/{grow_id}")
async def photos_upload(grow_id: int, files: list[UploadFile]):
    grow = db.grow(grow_id)
    if not grow:
        raise HTTPException(404)
    results = []
    for f in files:
        data = await f.read()
        if not data:
            continue
        results.append(photos.save(grow, f.filename or "photo.jpg", data))
    return {"saved": [r for r in results if not r.get("duplicate")],
            "duplicates": len([r for r in results if r.get("duplicate")])}


@router.delete("/photos/item/{photo_id}")
async def photos_delete(photo_id: int):
    if not photos.remove(photo_id):
        raise HTTPException(404)
    return {"ok": True}


# --- recipes ---

@router.get("/recipes")
async def recipes_list():
    return db.recipes()


@router.post("/recipes")
async def recipes_save(body: RecipeIn):
    return db.save_recipe(body.name, body.params, body.id)


@router.delete("/recipes/{recipe_id}")
async def recipes_delete(recipe_id: int):
    db.delete_recipe(recipe_id)
    return {"ok": True}


@router.get("/recipes/targets/{grow_id}")
async def recipe_targets(grow_id: int):
    grow = db.grow(grow_id)
    if not grow:
        raise HTTPException(404)
    start, end = recipes.grow_window(grow)
    return recipes.evaluate(grow, start, end)


# --- bundles ---

@router.get("/grows/{grow_id}/bundle")
async def bundle_export(grow_id: int):
    grow = db.grow(grow_id)
    if not grow:
        raise HTTPException(404)
    data = await bundle.export(grow)
    return Response(content=data, media_type="application/zip", headers={
        "Content-Disposition":
            f'attachment; filename="{grow["slug"]}_{date.today().isoformat()}.growscope.zip"'})


@router.post("/bundles/import")
async def bundle_import(file: UploadFile):
    data = await file.read()
    if not data:
        raise HTTPException(400, "empty upload")
    return await bundle.import_bundle(data)


# --- immich ---

@router.get("/immich/config")
async def immich_get():
    cfg = immich.config()
    cfg["api_key"] = "***" if cfg["api_key"] else ""
    return cfg


@router.post("/immich/config")
async def immich_set(body: ImmichConfigIn):
    immich.save_config(body.url, body.api_key)
    return {"ok": True, "albums": await immich.albums()}


@router.get("/immich/albums")
async def immich_albums():
    return await immich.albums()


@router.post("/immich/sync/{grow_id}")
async def immich_sync(grow_id: int, album_id: str):
    grow = db.grow(grow_id)
    if not grow:
        raise HTTPException(404)
    return await immich.sync(grow, album_id)
