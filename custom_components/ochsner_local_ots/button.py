"""Buttons: "Back up settings" on every controller, and the DHW boost (only on
plants with DHW), the app's one-time boost charge."""

from __future__ import annotations

from typing import Any, Dict, List

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import plant as P
from . import settings_backup
from .const import DOMAIN
from .coordinator import ClimatixCoordinator
from .plant_entity import async_write_planned, main_device_info, plant_runtime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    entities: List[ButtonEntity] = []
    for ctrl in store.get("controllers") or []:
        entities.append(OchsnerSettingsBackupButton(ctrl))
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
        await async_write_planned(self._ctrl, P.plan_dhw_boost, P.dhw_mode_input_ids())


class OchsnerSettingsBackupButton(CoordinatorEntity[ClimatixCoordinator], ButtonEntity):
    """Back up the heat pump's settings now (reads only, no flash write)."""

    _attr_has_entity_name = True
    _attr_translation_key = "settings_backup"
    _attr_icon = "mdi:content-save-cog"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, ctrl: Dict[str, Any]) -> None:
        super().__init__(ctrl["coordinator"])
        self._ctrl = ctrl
        self._attr_unique_id = f"{ctrl['host']}:button:settings_backup"
        self._attr_device_info = main_device_info(ctrl)

    async def async_press(self) -> None:
        try:
            await settings_backup.async_create_backup(self.hass, self._ctrl, settings_backup.SOURCE_MANUAL)
        except HomeAssistantError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="backup_failed") from err
