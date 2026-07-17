"""Series reads from InfluxDB (v1 InfluxQL, v2 Flux) and backfill from the HA recorder.

Charts ask for downsampled numeric series. Phase overlays ask for raw string
states. Backfill pulls whatever raw history HA still holds (about 10 days on a
stock recorder) so charts are not empty on day one.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timezone

import httpx

from . import db, ha, influx

_LOG = logging.getLogger("history")


def _iso_utc(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _v1_query(cfg: dict, q: str) -> dict:
    headers, params = influx._auth(cfg)
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.get(f"{cfg['url']}/query", headers=headers,
                                params={**params, "db": cfg["database"], "q": q, "epoch": "ms"})
    resp.raise_for_status()
    return resp.json()


async def _v2_query(cfg: dict, flux: str) -> list[dict]:
    headers, _ = influx._auth(cfg)
    headers["Content-Type"] = "application/vnd.flux"
    headers["Accept"] = "application/csv"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{cfg['url']}/api/v2/query",
                                 headers=headers, params={"org": cfg["org"]}, content=flux)
    resp.raise_for_status()
    rows = []
    for line in resp.text.splitlines():
        if not line or line.startswith("#"):
            continue
        rows.append(line)
    if not rows:
        return []
    parsed = list(csv.reader(io.StringIO("\n".join(rows))))
    header = parsed[0]
    out = []
    for values in parsed[1:]:
        if len(values) != len(header):
            continue
        out.append(dict(zip(header, values)))
    return out


def _is_v2(cfg: dict) -> bool:
    version = influx.status().get("version", "")
    return influx._is_v2(version, cfg)


async def series(entities: list[str], start_ms: int, end_ms: int,
                 interval: str = "5m") -> dict[str, list]:
    """Downsampled numeric series per entity: {entity_id: [[ts_ms, mean], ...]}."""
    cfg = influx.config()
    if not cfg["url"] or not entities:
        return {}
    out: dict[str, list] = {e: [] for e in entities}
    try:
        if _is_v2(cfg):
            ids = ", ".join(f'"{e}"' for e in entities)
            flux = (
                f'from(bucket: "{cfg["database"]}")'
                f' |> range(start: {_iso_utc(start_ms)}, stop: {_iso_utc(end_ms)})'
                f' |> filter(fn: (r) => r._measurement == "growscope" and r._field == "value")'
                f' |> filter(fn: (r) => contains(value: r.entity_id, set: [{ids}]))'
                f' |> aggregateWindow(every: {interval}, fn: mean, createEmpty: false)'
            )
            for row in await _v2_query(cfg, flux):
                entity = row.get("entity_id", "")
                if entity in out and row.get("_value"):
                    ts = int(datetime.fromisoformat(
                        row["_time"].replace("Z", "+00:00")).timestamp() * 1000)
                    out[entity].append([ts, round(float(row["_value"]), 3)])
        else:
            where = " OR ".join(f"entity_id='{e}'" for e in entities)
            q = (f'SELECT mean("value") FROM "growscope" WHERE ({where})'
                 f" AND time >= {start_ms}ms AND time <= {end_ms}ms"
                 f' GROUP BY time({interval}), "entity_id" fill(none)')
            data = await _v1_query(cfg, q)
            for result in data.get("results", []):
                for s in result.get("series", []):
                    entity = s.get("tags", {}).get("entity_id", "")
                    if entity in out:
                        out[entity] = [[v[0], round(v[1], 3)] for v in s.get("values", [])
                                       if v[1] is not None]
    except (httpx.HTTPError, ValueError, KeyError) as err:
        _LOG.warning("series query failed: %s", err)
    return out


async def states(entity: str, start_ms: int, end_ms: int) -> list[list]:
    """Raw string states for one entity (phases, valve states): [[ts_ms, state], ...]."""
    cfg = influx.config()
    if not cfg["url"]:
        return []
    try:
        if _is_v2(cfg):
            flux = (
                f'from(bucket: "{cfg["database"]}")'
                f' |> range(start: {_iso_utc(start_ms)}, stop: {_iso_utc(end_ms)})'
                f' |> filter(fn: (r) => r._measurement == "growscope" and r._field == "state")'
                f' |> filter(fn: (r) => r.entity_id == "{entity}")'
            )
            rows = await _v2_query(cfg, flux)
            return [[int(datetime.fromisoformat(r["_time"].replace("Z", "+00:00")).timestamp() * 1000),
                     r.get("_value", "")] for r in rows if r.get("_time")]
        q = (f'SELECT "state" FROM "growscope" WHERE entity_id=\'{entity}\''
             f" AND time >= {start_ms}ms AND time <= {end_ms}ms")
        data = await _v1_query(cfg, q)
        for result in data.get("results", []):
            for s in result.get("series", []):
                return [[v[0], v[1]] for v in s.get("values", []) if v[1] is not None]
        return []
    except (httpx.HTTPError, ValueError, KeyError) as err:
        _LOG.warning("states query failed: %s", err)
        return []


async def backfill(days: int = 10) -> dict:
    """Pull raw HA recorder history for all recorded entities and write it to Influx.
    Run it once after binding entities so charts start with whatever HA still holds."""
    entities = db.get_setting("recorded_entities", [])
    if not entities:
        return {"ok": False, "detail": "no recorded entities configured"}
    if not ha.configured():
        return {"ok": False, "detail": "HA API not configured"}
    start = datetime.now(tz=timezone.utc).timestamp() - days * 86400
    start_iso = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")
    written = 0
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.get(
                f"{ha.HA_API}/history/period/{start_iso}",
                headers=ha._HEADERS,
                params={"filter_entity_id": ",".join(entities),
                        "significant_changes_only": "false"})
        resp.raise_for_status()
        lines = []
        for entity_states in resp.json():
            entity = ""
            for s in entity_states:
                entity = s.get("entity_id") or entity
                state = s.get("state", "")
                when = s.get("last_changed") or s.get("last_updated")
                if not entity or not when or state in ("", "unknown", "unavailable"):
                    continue
                ts = int(datetime.fromisoformat(when.replace("Z", "+00:00")).timestamp())
                lines.append(
                    f"growscope,entity_id={influx._escape_tag(entity)} {influx._field(state)} {ts}")
        for i in range(0, len(lines), 5000):
            if await influx.write_lines(lines[i:i + 5000]):
                written += len(lines[i:i + 5000])
        return {"ok": True, "detail": f"backfilled {written} points over {days} days"}
    except (httpx.HTTPError, ValueError) as err:
        return {"ok": False, "detail": str(err)}
