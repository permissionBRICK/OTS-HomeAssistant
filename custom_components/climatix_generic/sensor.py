from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import extract_first_numeric_value
from .const import CONF_ID, CONF_NAME, CONF_UNIT, DOMAIN
from .coordinator import ClimatixCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: ClimatixCoordinator = store["coordinator"]
    host: str = store["host"]
    sensors = store.get("sensors", [])

    entities = [ClimatixGenericSensor(coordinator, host=host, cfg=s) for s in sensors]
    async_add_entities(entities)


class ClimatixGenericSensor(CoordinatorEntity[ClimatixCoordinator], SensorEntity):
    def __init__(self, coordinator: ClimatixCoordinator, *, host: str, cfg: Dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._host = host
        self._id = str(cfg[CONF_ID])
        self._attr_name = str(cfg[CONF_NAME])
        self._attr_native_unit_of_measurement = cfg.get(CONF_UNIT)
        self._attr_unique_id = f"{host}_sensor_oa_{self._id}".replace("=", "")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"Climatix ({self._host})",
            manufacturer="Siemens",
            model="Climatix",
        )

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        value = extract_first_numeric_value(data, self._id)
        return value
