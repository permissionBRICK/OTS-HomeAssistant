from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.components.sensor import SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import extract_first_numeric_value, extract_first_value
from .const import (
    CONF_HEATING_CIRCUIT_NAME,
    CONF_HEATING_CIRCUIT_UID,
    CONF_ID,
    CONF_NAME,
    CONF_UNIT,
    CONF_UUID,
    CONF_VALUE_MAP,
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
    # Avoid false positives like "m³".
    if "m³" in ul or "m3" in ul:
        return False
    if ul in {"c", "°c", "k", "kelvin"}:
        return True
    if "c" in ul or "kelvin" in ul:
        return True
    return False


def _round_sensor_value(v: Any) -> Any:
    """Round floats to 1 decimal only if they have >1 meaningful decimals."""
    try:
        f = float(v)
    except Exception:
        return v

    # Leave integers (and near-integers) unchanged.
    if abs(f - round(f)) < 1e-12:
        return int(round(f))

    r1 = round(f, 1)
    # Only round if the value actually changes (i.e., there are extra decimals).
    if abs(f - r1) > 1e-12:
        # Avoid returning -0.0
        if abs(r1) < 1e-12:
            return 0.0
        return r1
    return f


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
        self._parent_device_name = str(cfg.get("device_name") or f"Climatix ({host})")
        self._device_model = str(cfg.get("device_model") or "Climatix")
        self._hc_uid = str(cfg.get(CONF_HEATING_CIRCUIT_UID) or "").strip()
        self._hc_name = str(cfg.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
        self._id = str(cfg[CONF_ID])
        self._attr_name = str(cfg[CONF_NAME])
        self._attr_native_unit_of_measurement = cfg.get(CONF_UNIT)
        if _is_temperature_unit(self._attr_native_unit_of_measurement):
            self._attr_icon = "mdi:thermometer"
        self._value_map: Dict[str, str] = {str(k): str(v) for k, v in (cfg.get(CONF_VALUE_MAP) or {}).items()}
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = str(configured_uuid) if configured_uuid else f"{host}:sensor:{self._id}".replace("=", "")

    def _map_value(self, raw: Any) -> Any:
        if raw is None or not self._value_map:
            return raw

        # Prefer numeric comparison if possible (controller may return 1.0 while map uses "1").
        try:
            raw_f = float(raw)
            for k, v in self._value_map.items():
                try:
                    if abs(float(k) - raw_f) < 1e-6:
                        return v
                except (TypeError, ValueError):
                    continue
        except (TypeError, ValueError):
            pass

        return self._value_map.get(str(raw), raw)

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
        raw = extract_first_value(data, self._id)
        mapped = self._map_value(raw)
        if mapped is raw:
            # Keep legacy numeric behavior (so graphs/stats work for normal sensors).
            numeric = extract_first_numeric_value(data, self._id)
            if numeric is not None:
                return _round_sensor_value(numeric)
        return mapped


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
        sensors = ctrl.get("sensors", [])
        for s in sensors:
            entities.append(ClimatixGenericSensor(coordinator, host=host, base_url=base_url, cfg=dict(s, device_name=device_name, device_model=device_model)))

        # Always add a write-counter sensor per controller.
        entities.append(ClimatixGenericWriteCounterSensor(entry_id=entry.entry_id, host=host, base_url=base_url, device_name=device_name, device_model=device_model))
    async_add_entities(entities)


class ClimatixGenericWriteCounterSensor(SensorEntity, RestoreEntity):
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, *, entry_id: str, host: str, base_url: str, device_name: str, device_model: str) -> None:
        self._entry_id = entry_id
        self._host = host
        self._base_url = base_url
        self._device_name = device_name
        self._device_model = device_model
        self._attr_name = "Flash writes"
        self._attr_unique_id = f"{host}:flash_writes".replace("=", "")
        self._count: int = 0

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=self._device_name,
            manufacturer="Ochsner",
            model=self._device_model,
            configuration_url=self._base_url,
        )

    @property
    def native_unit_of_measurement(self) -> str:
        return "writes"

    @property
    def native_value(self) -> int:
        # Source of truth is hass.data so the write hook can update without holding entity refs.
        store = (self.hass.data.get(DOMAIN, {}) or {}).get(self._entry_id, {})
        counts = store.get("write_counts") or {}
        try:
            return int(counts.get(self._host, self._count))
        except Exception:
            return self._count

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        # Restore last known count.
        restored_value: int | None = None
        restored = await self.async_get_last_state()
        if restored is not None and restored.state not in (None, "unknown", "unavailable"):
            try:
                restored_value = int(float(restored.state))
                self._count = restored_value
            except Exception:
                self._count = 0

        store = self.hass.data.setdefault(DOMAIN, {}).setdefault(self._entry_id, {})
        counts = store.setdefault("write_counts", {})

        # IMPORTANT: During startup, the integration may have already initialized the
        # in-memory counter dict with 0. Prefer the restored value (if any) so the
        # counter persists across restarts.
        if restored_value is not None:
            counts[self._host] = restored_value
        elif self._host not in counts:
            counts[self._host] = self._count
        else:
            try:
                self._count = int(float(counts[self._host]))
            except Exception:
                counts[self._host] = self._count

        # Register so the write hook can call async_write_ha_state.
        store.setdefault("write_count_entities", {})[self._host] = self

        # Push restored state immediately.
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        store = (self.hass.data.get(DOMAIN, {}) or {}).get(self._entry_id, {})
        ents = store.get("write_count_entities")
        if isinstance(ents, dict) and ents.get(self._host) is self:
            ents.pop(self._host, None)
        await super().async_will_remove_from_hass()
