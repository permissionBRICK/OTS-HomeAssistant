from __future__ import annotations

from typing import Any, Dict

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ClimatixGenericApi, extract_first_numeric_value
from .const import CONF_ID, CONF_MAX, CONF_MIN, CONF_NAME, CONF_READ_ID, CONF_STEP, CONF_UNIT, CONF_WRITE_ID, DOMAIN
from .coordinator import ClimatixCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: ClimatixCoordinator = store["coordinator"]
    api: ClimatixGenericApi = store["api"]
    host: str = store["host"]
    numbers = store.get("numbers", [])

    entities = [ClimatixGenericNumber(coordinator, api=api, host=host, cfg=n) for n in numbers]
    async_add_entities(entities)


class ClimatixGenericNumber(CoordinatorEntity[ClimatixCoordinator], NumberEntity):
    def __init__(
        self,
        coordinator: ClimatixCoordinator,
        *,
        api: ClimatixGenericApi,
        host: str,
        cfg: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._host = host
        base_id = cfg.get(CONF_ID)
        self._read_id = str(cfg.get(CONF_READ_ID) or base_id)
        self._write_id = str(cfg.get(CONF_WRITE_ID) or base_id or self._read_id)
        if not self._read_id or self._read_id == "None":
            raise ValueError("Number config missing read_id (or id)")
        if not self._write_id or self._write_id == "None":
            raise ValueError("Number config missing write_id (or id)")
        self._attr_name = str(cfg[CONF_NAME])
        self._attr_native_unit_of_measurement = cfg.get(CONF_UNIT)
        self._attr_native_min_value = float(cfg.get(CONF_MIN, 0))
        self._attr_native_max_value = float(cfg.get(CONF_MAX, 100))
        self._attr_native_step = float(cfg.get(CONF_STEP, 0.5))
        self._attr_unique_id = f"{host}_number_oa_{self._read_id}".replace("=", "")

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
        return extract_first_numeric_value(data, self._read_id)

    async def async_set_native_value(self, value: float) -> None:
        await self._api.write(self._write_id, float(value))
        await self.coordinator.async_request_refresh()
