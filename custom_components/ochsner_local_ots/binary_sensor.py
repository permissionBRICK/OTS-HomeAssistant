from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import extract_first_value
from .const import (
    CONF_HEATING_CIRCUIT_NAME,
    CONF_HEATING_CIRCUIT_UID,
    CONF_ID,
    CONF_NAME,
    CONF_UUID,
    CONF_VALUE_MAP,
    DOMAIN,
)
from .coordinator import ClimatixCoordinator


def _to_bool(raw: Any) -> Optional[bool]:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        # Treat 0 as False; any other number as True.
        return bool(int(raw))
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in {"true", "on", "yes", "1"}:
            return True
        if s in {"false", "off", "no", "0"}:
            return False
    return None


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
        ClimatixGenericBinarySensor(coordinator, host=host, base_url=base_url, cfg=s)
        for s in discovery_info.get("binary_sensors", [])
    ]
    async_add_entities(entities)


class ClimatixGenericBinarySensor(CoordinatorEntity[ClimatixCoordinator], BinarySensorEntity):
    def __init__(self, coordinator: ClimatixCoordinator, *, host: str, base_url: str, cfg: Dict[str, Any]) -> None:
        super().__init__(coordinator)
        self._host = host
        self._base_url = base_url
        self._parent_device_name = str(cfg.get("device_name") or f"Climatix ({host})")
        self._device_model = str(cfg.get("device_model") or "Climatix")
        self._hc_uid = str(cfg.get(CONF_HEATING_CIRCUIT_UID) or "").strip()
        self._hc_name = str(cfg.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
        self._id = str(cfg[CONF_ID])
        self._attr_name = str(cfg[CONF_NAME])
        self._value_map: Dict[str, str] = {str(k): str(v) for k, v in (cfg.get(CONF_VALUE_MAP) or {}).items()}
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = (
            str(configured_uuid) if configured_uuid else f"{host}:binary_sensor:{self._id}".replace("=", "")
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
    def is_on(self) -> Optional[bool]:
        data = self.coordinator.data or {}
        raw = extract_first_value(data, self._id)

        # If a value_map is provided, allow mapping textual states into bool.
        if raw is not None and self._value_map:
            mapped = self._value_map.get(str(raw))
            if mapped is not None:
                b = _to_bool(mapped)
                if b is not None:
                    return b

        return _to_bool(raw)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    controllers = store.get("controllers") or []
    entities = []
    for ctrl in controllers:
        coordinator: ClimatixCoordinator = ctrl["coordinator"]
        host: str = ctrl["host"]
        base_url: str = ctrl.get("base_url", f"http://{host}")
        device_name: str = ctrl.get("device_name", f"Climatix ({host})")
        device_model: str = ctrl.get("device_model", "Climatix")
        binary_sensors = ctrl.get("binary_sensors", [])
        for s in binary_sensors:
            entities.append(ClimatixGenericBinarySensor(coordinator, host=host, base_url=base_url, cfg=dict(s, device_name=device_name, device_model=device_model)))
    async_add_entities(entities)
