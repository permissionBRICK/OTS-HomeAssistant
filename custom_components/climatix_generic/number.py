from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import ClimatixGenericApi, extract_first_numeric_value
from .const import (
    CONF_ID,
    CONF_MAX,
    CONF_MIN,
    CONF_NAME,
    CONF_READ_ID,
    CONF_STEP,
    CONF_UNIT,
    CONF_UUID,
    CONF_WRITE_ID,
    DOMAIN,
)
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
    api: ClimatixGenericApi = hass.data[DOMAIN]["api"]
    host: str = hass.data[DOMAIN]["host"]
    base_url: str = hass.data[DOMAIN].get("base_url", f"http://{host}")

    entities = [
        ClimatixGenericNumber(coordinator, api=api, host=host, base_url=base_url, cfg=n)
        for n in discovery_info.get("numbers", [])
    ]
    async_add_entities(entities)


class ClimatixGenericNumber(CoordinatorEntity[ClimatixCoordinator], NumberEntity):
    def __init__(
        self,
        coordinator: ClimatixCoordinator,
        *,
        api: ClimatixGenericApi,
        host: str,
        base_url: str,
        cfg: Dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._host = host
        self._base_url = base_url
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
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = (
            str(configured_uuid)
            if configured_uuid
            else f"{host}:number:{self._read_id}".replace("=", "")
        )

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
        return extract_first_numeric_value(data, self._read_id)

    async def async_set_native_value(self, value: float) -> None:
        desired = float(value)

        data = self.coordinator.data or {}
        current = extract_first_numeric_value(data, self._read_id)
        if current is not None:
            try:
                if abs(float(current) - desired) < 1e-6:
                    return
            except (TypeError, ValueError):
                pass

        await self._api.write(self._write_id, desired)
        await self.coordinator.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: ClimatixCoordinator = store["coordinator"]
    api: ClimatixGenericApi = store["api"]
    host: str = store["host"]
    base_url: str = store.get("base_url", f"http://{host}")
    numbers = store.get("numbers", [])

    entities = [
        ClimatixGenericNumber(coordinator, api=api, host=host, base_url=base_url, cfg=n)
        for n in numbers
    ]
    async_add_entities(entities)
