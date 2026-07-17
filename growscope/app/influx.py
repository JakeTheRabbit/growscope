"""InfluxDB client - v1 and v2 treated as equals, autodetected.

GrowScope never ships a database. This connects to the InfluxDB app from the
HA add-on store (v1.x today) or any container/external instance (v1/v2, and
v3 through its v1/v2 compat APIs). On provision it creates its own database
or bucket and touches nothing else.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from . import db, ha

_LOG = logging.getLogger("influx")

DEFAULT_DB = "growscope"

_status: dict = {"ok": False, "detail": "not configured", "version": "", "last_write": ""}


def config() -> dict:
    return {
        "url": db.get_setting("influx_url", ""),
        "database": db.get_setting("influx_database", DEFAULT_DB),
        "username": db.get_setting("influx_username", ""),
        "password": db.get_setting("influx_password", ""),
        "org": db.get_setting("influx_org", ""),
        "token": db.get_setting("influx_token", ""),
    }


def save_config(cfg: dict) -> None:
    for key in ("url", "database", "username", "password", "org", "token"):
        if key in cfg:
            value = (cfg.get(key) or "").strip()
            if key == "url":
                value = value.rstrip("/")
            db.set_setting(f"influx_{key}", value)


def status() -> dict:
    return dict(_status)


def _auth(cfg: dict) -> tuple[dict, dict]:
    """Returns (headers, params) for the configured auth style."""
    headers, params = {}, {}
    if cfg["token"]:
        headers["Authorization"] = f"Token {cfg['token']}"
    if cfg["username"]:
        params["u"] = cfg["username"]
        params["p"] = cfg["password"]
    return headers, params


async def detect_version(cfg: dict) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{cfg['url']}/ping")
    ver = resp.headers.get("X-Influxdb-Version", "")
    if not ver and resp.status_code >= 400:
        raise RuntimeError(f"ping returned {resp.status_code}")
    return ver


def _is_v2(version: str, cfg: dict) -> bool:
    if version.startswith("1"):
        return False
    if version.startswith("2") or version.startswith("3"):
        return True
    # Unknown version string - go by auth style. Token + org smells like v2.
    return bool(cfg["token"] and cfg["org"])


async def test(cfg: dict | None = None) -> dict:
    cfg = cfg or config()
    if not cfg["url"]:
        return {"ok": False, "detail": "no URL set"}
    try:
        version = await detect_version(cfg)
        headers, params = _auth(cfg)
        async with httpx.AsyncClient(timeout=10) as client:
            if _is_v2(version, cfg):
                resp = await client.get(f"{cfg['url']}/api/v2/buckets",
                                        headers=headers, params={"limit": 1})
            else:
                resp = await client.get(f"{cfg['url']}/query", headers=headers,
                                        params={**params, "q": "SHOW DATABASES"})
        ok = resp.status_code in (200, 204)
        detail = "connected" if ok else f"auth/query failed ({resp.status_code})"
        _status.update({"ok": ok, "detail": detail, "version": version or "unknown"})
        return {"ok": ok, "detail": detail, "version": version or "unknown"}
    except (httpx.HTTPError, RuntimeError) as err:
        _status.update({"ok": False, "detail": str(err), "version": ""})
        return {"ok": False, "detail": str(err)}


async def provision(cfg: dict | None = None) -> dict:
    """Create our database (v1) or bucket (v2). Idempotent, touches nothing else."""
    cfg = cfg or config()
    check = await test(cfg)
    if not check["ok"]:
        return check
    headers, params = _auth(cfg)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if _is_v2(check.get("version", ""), cfg):
                resp = await client.get(f"{cfg['url']}/api/v2/orgs",
                                        headers=headers, params={"org": cfg["org"]})
                orgs = resp.json().get("orgs", [])
                if not orgs:
                    return {"ok": False, "detail": f"org '{cfg['org']}' not found"}
                org_id = orgs[0]["id"]
                resp = await client.post(f"{cfg['url']}/api/v2/buckets", headers=headers,
                                         json={"orgID": org_id, "name": cfg["database"],
                                               "retentionRules": []})
                if resp.status_code not in (201, 422):  # 422 = already exists
                    return {"ok": False, "detail": f"bucket create failed ({resp.status_code})"}
            else:
                resp = await client.post(f"{cfg['url']}/query", headers=headers,
                                         params={**params, "q": f'CREATE DATABASE "{cfg["database"]}"'})
                if resp.status_code != 200:
                    return {"ok": False, "detail": f"CREATE DATABASE failed ({resp.status_code})"}
        return {"ok": True, "detail": f"provisioned '{cfg['database']}'"}
    except (httpx.HTTPError, ValueError) as err:
        return {"ok": False, "detail": str(err)}


def _escape_tag(value: str) -> str:
    return value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")


def _field(state: str) -> str:
    try:
        return f"value={float(state)}"
    except ValueError:
        escaped = state.replace("\\", "\\\\").replace('"', '\\"')
        return f'state="{escaped}"'


async def write_states(states: list[dict]) -> bool:
    cfg = config()
    if not cfg["url"]:
        return False
    ts = int(time.time())
    lines = []
    for s in states:
        state = s.get("state", "")
        if state in ("", "unknown", "unavailable"):
            continue
        lines.append(f"growscope,entity_id={_escape_tag(s['entity_id'])} {_field(state)} {ts}")
    if not lines:
        return True
    headers, params = _auth(cfg)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if _is_v2(_status.get("version", ""), cfg):
                resp = await client.post(
                    f"{cfg['url']}/api/v2/write", headers=headers,
                    params={"org": cfg["org"], "bucket": cfg["database"], "precision": "s"},
                    content="\n".join(lines))
            else:
                resp = await client.post(
                    f"{cfg['url']}/write", headers=headers,
                    params={**params, "db": cfg["database"], "precision": "s"},
                    content="\n".join(lines))
        ok = resp.status_code in (200, 204)
        if ok:
            _status["last_write"] = time.strftime("%Y-%m-%d %H:%M:%S")
        else:
            _LOG.warning("write failed: %s %s", resp.status_code, resp.text[:200])
        return ok
    except httpx.HTTPError as err:
        _LOG.warning("write failed: %s", err)
        return False


async def recorder_loop() -> None:
    """Poll bound entities every 60s and record them. Independent of frame capture -
    if Influx is down this pauses and resumes, frames keep going regardless."""
    await test()
    while True:
        try:
            entities = db.get_setting("recorded_entities", [])
            if entities and _status.get("ok"):
                states = []
                for entity_id in entities:
                    s = await ha.get_state(entity_id)
                    if s:
                        states.append({"entity_id": entity_id, "state": s.get("state", "")})
                await write_states(states)
            elif entities:
                await test()
        except Exception:  # never let the loop die
            _LOG.exception("recorder tick failed")
        await asyncio.sleep(60)
