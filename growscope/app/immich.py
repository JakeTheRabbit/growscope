"""Immich album sync: pull a grow's album from your Immich server so phone
photos land on the timeline without cables. Manual sync now, scheduled later."""
from __future__ import annotations

import logging

import httpx

from . import db, photos

_LOG = logging.getLogger("immich")


def config() -> dict:
    return {"url": db.get_setting("immich_url", ""),
            "api_key": db.get_setting("immich_api_key", "")}


def save_config(url: str, api_key: str) -> None:
    db.set_setting("immich_url", url.rstrip("/"))
    if api_key != "***":
        db.set_setting("immich_api_key", api_key)


async def albums() -> list[dict]:
    cfg = config()
    if not cfg["url"] or not cfg["api_key"]:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{cfg['url']}/api/albums",
                                    headers={"x-api-key": cfg["api_key"]})
        resp.raise_for_status()
        return [{"id": a["id"], "name": a.get("albumName", a["id"]),
                 "count": a.get("assetCount", 0)} for a in resp.json()]
    except (httpx.HTTPError, ValueError, KeyError) as err:
        _LOG.warning("album list failed: %s", err)
        return []


async def sync(grow: dict, album_id: str) -> dict:
    cfg = config()
    if not cfg["url"] or not cfg["api_key"]:
        return {"ok": False, "detail": "Immich URL and API key not set"}
    headers = {"x-api-key": cfg["api_key"]}
    added = skipped = failed = 0
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(f"{cfg['url']}/api/albums/{album_id}", headers=headers)
            resp.raise_for_status()
            assets = resp.json().get("assets", [])
            for asset in assets:
                name = asset.get("originalFileName", f"{asset['id']}.jpg")
                if db.photo_exists(grow["id"], f"photos/{grow['slug']}/"
                                               f"{_expected_name(asset, name)}"):
                    skipped += 1
                    continue
                dl = await client.get(f"{cfg['url']}/api/assets/{asset['id']}/original",
                                      headers=headers)
                if dl.status_code != 200:
                    failed += 1
                    continue
                result = photos.save(grow, name, dl.content, source="immich")
                if result.get("duplicate"):
                    skipped += 1
                else:
                    added += 1
        return {"ok": True, "detail": f"added {added}, skipped {skipped}, failed {failed}"}
    except (httpx.HTTPError, ValueError, KeyError) as err:
        return {"ok": False, "detail": str(err)}


def _expected_name(asset: dict, name: str) -> str:
    # Best-effort dedupe pre-check; photos.save's EXIF-timestamped name is the
    # real gate, so a miss here just costs one download.
    return name
