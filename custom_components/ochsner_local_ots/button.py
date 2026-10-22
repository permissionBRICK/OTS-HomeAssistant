"""DHW boost button (only on plants with DHW): the app's one-time boost charge."""

from __future__ import annotations

from typing import Any, Dict, List

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import plant as P
from .const import DOMAIN
from .coordinator import ClimatixCoordinator
from .plant_entity import async_write_sequence, main_device_info, plant_runtime
from .water_heater import dhw_dependents


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    entities: List[OchsnerDhwBoostButton] = []
    for ctrl in store.get("controllers") or []:
        if (plant_runtime(ctrl).get("caps") or {}).get("dhw"):
            entities.append(OchsnerDhwBoostButton(ctrl))
    async_add_entities(entities)


class OchsnerDhwBoostButton(CoordinatorEntity[ClimatixCoordinator], ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "dhw_boost"
    _attr_icon = "mdi:water-boiler-alert"

    def __init__(self, ctrl: Dict[str, Any]) -> None:
        super().__init__(ctrl["coordinator"])
        self._ctrl = ctrl
        self._attr_unique_id = f"{ctrl['host']}:button:dhw_boost"
        self._attr_device_info = main_device_info(ctrl)

    async def async_press(self) -> None:
        data = self.coordinator.data or {}
        values: Dict[str, Any] = data.get("values") if isinstance(data, dict) else {}
        await async_write_sequence(self._ctrl, P.plan_dhw_boost(values or {}), dhw_dependents())
