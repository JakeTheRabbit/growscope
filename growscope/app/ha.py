"""Home Assistant API client. Works through the Supervisor proxy in add-on mode
or directly against HA with a long-lived token in standalone mode."""
from __future__ import annotations

import logging

import httpx

from .config import HA_API, HA_TOKEN

_LOG = logging.getLogger("ha")
_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}"}


def configured() -> bool:
    return bool(HA_API and HA_TOKEN)


async def get_state(entity_id: str) -> dict | None:
    if not configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{HA_API}/states/{entity_id}", headers=_HEADERS)
        return resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as err:
        _LOG.warning("state fetch failed for %s: %s", entity_id, err)
        return None


async def list_entities(domains: list[str] | None = None) -> list[dict]:
    """Entity ids + names for the UI dropdowns. Trimmed hard - never returns full state."""
    if not configured():
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{HA_API}/states", headers=_HEADERS)
        resp.raise_for_status()
        out = []
        for s in resp.json():
            eid = s.get("entity_id", "")
            if domains and eid.split(".", 1)[0] not in domains:
                continue
            out.append({"entity_id": eid,
                        "name": s.get("attributes", {}).get("friendly_name", eid)})
        return sorted(out, key=lambda e: e["entity_id"])
    except (httpx.HTTPError, ValueError) as err:
        _LOG.warning("entity list failed: %s", err)
        return []


async def camera_jpeg(entity_id: str) -> bytes | None:
    if not configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{HA_API}/camera_proxy/{entity_id}", headers=_HEADERS)
        if resp.status_code == 200 and resp.content:
            return resp.content
        _LOG.warning("camera_proxy %s returned %s", entity_id, resp.status_code)
        return None
    except httpx.HTTPError as err:
        _LOG.warning("snapshot failed for %s: %s", entity_id, err)
        return None


async def ping() -> bool:
    if not configured():
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{HA_API}/", headers=_HEADERS)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False
