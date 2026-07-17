"""REST API consumed by the ingress UI and the GrowScope custom integration."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import capture, db, ha, influx, lapse

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


class CameraIn(BaseModel):
    grow_id: int
    entity_id: str
    interval_min: int = 10
    lights_entity: str = ""
    window_start: str = ""
    window_end: str = ""


class CameraPatch(BaseModel):
    entity_id: str | None = None
    interval_min: int | None = None
    lights_entity: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    enabled: int | None = None


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
    return {"ok": True, "app": "growscope", "version": "0.1.0"}


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
    return db.add_camera(body.grow_id, body.entity_id, body.interval_min,
                         body.lights_entity, body.window_start, body.window_end)


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
