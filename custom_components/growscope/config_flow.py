"""Config flow: point the integration at a running GrowScope engine."""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_URL, DEFAULT_URL, DOMAIN


class GrowScopeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].rstrip("/")
            if await self._engine_ok(url):
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="GrowScope", data={CONF_URL: url})
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=DEFAULT_URL): str}),
            errors=errors,
        )

    async def _engine_ok(self, url: str) -> bool:
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(10):
                resp = await session.get(f"{url}/api/health")
            return resp.status == 200
        except (aiohttp.ClientError, TimeoutError):
            return False
