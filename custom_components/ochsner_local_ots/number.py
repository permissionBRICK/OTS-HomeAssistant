from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import ClimatixGenericApi, extract_first_numeric_value
from .const import (
    CONF_HEATING_CIRCUIT_NAME,
    CONF_HEATING_CIRCUIT_UID,
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


def _is_temperature_unit(unit: Any) -> bool:
    if not unit:
        return False
    u = str(unit).strip()
    if not u:
        return False
    ul = u.lower()
    if "°" in u:
        return True
    if "m³" in ul or "m3" in ul:
        return False
    if ul in {"c", "°c", "k", "kelvin"}:
        return True
    if "c" in ul or "kelvin" in ul:
        return True
    return False


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
        self._parent_device_name = str(cfg.get("device_name") or f"Climatix ({host})")
        self._device_model = str(cfg.get("device_model") or "Climatix")
        self._hc_uid = str(cfg.get(CONF_HEATING_CIRCUIT_UID) or "").strip()
        self._hc_name = str(cfg.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
        base_id = cfg.get(CONF_ID)
        self._read_id = str(cfg.get(CONF_READ_ID) or base_id)
        self._write_id = str(cfg.get(CONF_WRITE_ID) or base_id or self._read_id)
        if not self._read_id or self._read_id == "None":
            raise ValueError("Number config missing read_id (or id)")
        if not self._write_id or self._write_id == "None":
            raise ValueError("Number config missing write_id (or id)")
        self._attr_name = str(cfg[CONF_NAME])
        self._attr_native_unit_of_measurement = cfg.get(CONF_UNIT)
        if _is_temperature_unit(self._attr_native_unit_of_measurement):
            self._attr_icon = "mdi:thermometer"

        min_v = cfg.get(CONF_MIN)
        max_v = cfg.get(CONF_MAX)
        if min_v is not None and max_v is not None:
            self._attr_native_min_value = float(min_v)
            self._attr_native_max_value = float(max_v)
            self._attr_mode = NumberMode.SLIDER
        else:
            # No configured range -> use a text box.
            # HA's number service validates values with:
            #   value < entity.min_value or value > entity.max_value
            # so these must never be None.
            self._attr_native_min_value = -1_000_000_000.0
            self._attr_native_max_value = 1_000_000_000.0
            self._attr_mode = NumberMode.BOX

        step_v = cfg.get(CONF_STEP)
        if step_v is not None:
            self._attr_native_step = float(step_v)
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = (
            str(configured_uuid)
            if configured_uuid
            else f"{host}:number:{self._read_id}".replace("=", "")
        )

    @property
    def device_info(self) -> DeviceInfo:
        if self._hc_uid:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self._host}:hc:{self._hc_uid}")},
                via_device=(DOMAIN, self._host),
                name=self._hc_name or "Heating circuit",
                manufacturer="Ochsner",
                model=self._device_model,
                configuration_url=self._base_url,
            )
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=self._parent_device_name,
            manufacturer="Ochsner",
            model=self._device_model,
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
        try:
            await self.coordinator.async_refresh_ids([self._read_id])
        except Exception:
            # Fallback: refresh everything if the targeted read fails.
            await self.coordinator.async_request_refresh()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    controllers = store.get("controllers") or []
    entities = []
    for ctrl in controllers:
        coordinator: ClimatixCoordinator = ctrl["coordinator"]
        api: ClimatixGenericApi = ctrl["api"]
        host: str = ctrl["host"]
        base_url: str = ctrl.get("base_url", f"http://{host}")
        device_name: str = ctrl.get("device_name", f"Climatix ({host})")
        device_model: str = ctrl.get("device_model", "Climatix")
        numbers = ctrl.get("numbers", [])
        for n in numbers:
            entities.append(ClimatixGenericNumber(coordinator, api=api, host=host, base_url=base_url, cfg=dict(n, device_name=device_name, device_model=device_model)))
    async_add_entities(entities)
