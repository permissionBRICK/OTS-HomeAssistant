from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import extract_first_numeric_value
from .const import CONF_ID, CONF_NAME, CONF_UNIT, CONF_UUID, DOMAIN
from .coordinator import ClimatixCoordinator


async def async_setup_platform(
    hass: HomeAssistant,
    config: Dict[str, Any],
    async_add_entities,
    discovery_info: Optional[Dict[str, Any]] = None,
) -> None:
    if not discovery_info:
        return

    coordinator: ClimatixCoordinator = hass.data[DOMAIN]["coordinator"]
    host: str = hass.data[DOMAIN]["host"]
    base_url: str = hass.data[DOMAIN].get("base_url", f"http://{host}")

    entities = [
        ClimatixGenericSensor(coordinator, host=host, base_url=base_url, cfg=s)
        for s in discovery_info.get("sensors", [])
    ]
    async_add_entities(entities)


class ClimatixGenericSensor(CoordinatorEntity[ClimatixCoordinator], SensorEntity):
    def __init__(self, coordinator: ClimatixCoordinator, *, host: str, base_url: str, cfg: Dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._host = host
        self._base_url = base_url
        self._id = str(cfg[CONF_ID])
        self._attr_name = str(cfg[CONF_NAME])
        self._attr_native_unit_of_measurement = cfg.get(CONF_UNIT)
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = str(configured_uuid) if configured_uuid else f"{host}:sensor:{self._id}".replace("=", "")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"Climatix ({self._host})",
            manufacturer="Siemens",
            model="Climatix",
            configuration_url=self._base_url,
        )

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        value = extract_first_numeric_value(data, self._id)
        return value


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: ClimatixCoordinator = store["coordinator"]
    host: str = store["host"]
    base_url: str = store.get("base_url", f"http://{host}")
    sensors = store.get("sensors", [])

    entities = [
        ClimatixGenericSensor(coordinator, host=host, base_url=base_url, cfg=s)
        for s in sensors
    ]
    async_add_entities(entities)
