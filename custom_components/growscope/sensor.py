"""Per-grow sensors: day of cycle, flower day, stage. One HA device per grow."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import GrowScopeCoordinator
from .const import DOMAIN

SENSOR_KINDS = (
    ("day", "Day", "d"),
    ("flower_day", "Flower day", "d"),
    ("stage", "Stage", None),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: GrowScopeCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[int] = set()

    @callback
    def _sync_grows() -> None:
        new_entities: list[GrowSensor] = []
        for grow in coordinator.data or []:
            if grow["id"] in known:
                continue
            known.add(grow["id"])
            new_entities.extend(
                GrowSensor(coordinator, grow["id"], kind, label, unit)
                for kind, label, unit in SENSOR_KINDS
            )
        if new_entities:
            async_add_entities(new_entities)

    _sync_grows()
    entry.async_on_unload(coordinator.async_add_listener(_sync_grows))


class GrowSensor(CoordinatorEntity[GrowScopeCoordinator], SensorEntity):
    """One value from one grow."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GrowScopeCoordinator,
        grow_id: int,
        kind: str,
        label: str,
        unit: str | None,
    ) -> None:
        super().__init__(coordinator)
        self._grow_id = grow_id
        self._kind = kind
        self._attr_name = label
        self._attr_native_unit_of_measurement = unit
        self._attr_unique_id = f"growscope_{grow_id}_{kind}"

    def _grow(self) -> dict | None:
        for grow in self.coordinator.data or []:
            if grow["id"] == self._grow_id:
                return grow
        return None

    @property
    def device_info(self) -> DeviceInfo:
        grow = self._grow() or {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"grow_{self._grow_id}")},
            name=f"Grow: {grow.get('name', self._grow_id)}",
            manufacturer="GrowScope",
            model="Grow",
            configuration_url=self.coordinator.url,
        )

    @property
    def available(self) -> bool:
        return super().available and self._grow() is not None

    @property
    def native_value(self):
        grow = self._grow()
        return grow.get(self._kind) if grow else None
