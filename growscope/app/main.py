"""GrowScope engine. FastAPI app served over HA ingress (or plain HTTP standalone)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import api, capture, config, db, influx, lapse

_LOG = logging.getLogger("main")
_UI = Path(__file__).parent / "ui"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init()
    tasks = [asyncio.create_task(capture.loop()),
             asyncio.create_task(influx.recorder_loop()),
             asyncio.create_task(lapse.nightly_loop())]
    _LOG.info("engine up (mode: %s)", "add-on" if config.IS_ADDON else "standalone")
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="GrowScope", lifespan=lifespan)


@app.middleware("http")
async def ingress_guard(request: Request, call_next):
    # Add-on mode: only the Supervisor ingress gateway may talk to us. HA has
    # already authenticated the user by the time a request reaches the gateway.
    # Standalone mode: no gateway exists - the user fronts/never exposes it themselves.
    if config.IS_ADDON:
        client = request.client.host if request.client else ""
        if client not in (config.INGRESS_GATEWAY, "127.0.0.1"):
            return JSONResponse({"detail": "ingress only"}, status_code=403)
    return await call_next(request)


app.include_router(api.router)
config.ensure_dirs()  # must exist before the static mount is created
app.mount("/media", StaticFiles(directory=str(config.MEDIA_DIR)), name="media")


@app.get("/")
async def index():
    return FileResponse(_UI / "index.html")
