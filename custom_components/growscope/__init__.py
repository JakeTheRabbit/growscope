"""GrowScope integration: surfaces engine grows as HA devices and sensors."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_URL, DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]

type GrowScopeConfigEntry = ConfigEntry


class GrowScopeCoordinator(DataUpdateCoordinator[list[dict]]):
    """Polls the engine's grow registry."""

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.url = url

    async def _async_update_data(self) -> list[dict]:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(15):
                resp = await session.get(f"{self.url}/api/grows")
                if resp.status != 200:
                    raise UpdateFailed(f"engine returned {resp.status}")
                return await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"engine unreachable: {err}") from err


async def async_setup_entry(hass: HomeAssistant, entry: GrowScopeConfigEntry) -> bool:
    coordinator = GrowScopeCoordinator(hass, entry.data[CONF_URL].rstrip("/"))
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    stages: dict[int, str] = {g["id"]: g.get("stage", "") for g in coordinator.data or []}

    def _watch_stages() -> None:
        for grow in coordinator.data or []:
            previous = stages.get(grow["id"])
            current = grow.get("stage", "")
            stages[grow["id"]] = current
            if previous is not None and previous != current:
                hass.bus.async_fire(f"{DOMAIN}_stage_changed", {
                    "grow_id": grow["id"], "grow": grow["name"],
                    "from": previous, "to": current,
                    "day": grow.get("day"), "flower_day": grow.get("flower_day"),
                })

    entry.async_on_unload(coordinator.async_add_listener(_watch_stages))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _register_services(hass)
    return True


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "flip"):
        return

    def _coordinator() -> GrowScopeCoordinator | None:
        for coordinator in hass.data.get(DOMAIN, {}).values():
            return coordinator
        return None

    async def _engine(method: str, path: str, payload: dict | None = None) -> None:
        coordinator = _coordinator()
        if coordinator is None:
            raise HomeAssistantError("GrowScope is not set up")
        session = async_get_clientsession(hass)
        try:
            async with asyncio.timeout(120):
                resp = await session.request(method, f"{coordinator.url}{path}", json=payload)
                if resp.status >= 400:
                    raise HomeAssistantError(f"engine returned {resp.status} for {path}")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"engine unreachable: {err}") from err
        await coordinator.async_request_refresh()

    def _today() -> str:
        return dt_util.now().date().isoformat()

    async def flip(call: ServiceCall) -> None:
        await _engine("PATCH", f"/api/grows/{int(call.data['grow_id'])}",
                      {"flip_date": call.data.get("date") or _today()})

    async def chop(call: ServiceCall) -> None:
        await _engine("PATCH", f"/api/grows/{int(call.data['grow_id'])}",
                      {"chop_date": call.data.get("date") or _today(), "status": "archived"})

    async def log_event(call: ServiceCall) -> None:
        await _engine("POST", "/api/journal", {
            "grow_id": int(call.data["grow_id"]),
            "ts": dt_util.now().isoformat(timespec="seconds"),
            "kind": call.data.get("kind", "note"),
            "title": call.data["title"],
            "note": call.data.get("note", ""),
        })

    async def capture_now(call: ServiceCall) -> None:
        await _engine("POST", f"/api/cameras/{int(call.data['camera_id'])}/capture")

    async def build_timelapse(call: ServiceCall) -> None:
        await _engine("POST", f"/api/lapse/build/{int(call.data['grow_id'])}")

    for name, handler in (("flip", flip), ("chop", chop), ("log_event", log_event),
                          ("capture_now", capture_now), ("build_timelapse", build_timelapse)):
        hass.services.async_register(DOMAIN, name, handler)


async def async_unload_entry(hass: HomeAssistant, entry: GrowScopeConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
