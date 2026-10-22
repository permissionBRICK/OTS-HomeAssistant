from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import ClimatixGenericApi, extract_first_numeric_value
from .const import (
    CONF_DIAGNOSTIC,
    CONF_ENABLED_DEFAULT,
    CONF_HEATING_CIRCUIT_NAME,
    CONF_HEATING_CIRCUIT_UID,
    CONF_BUNDLE_MAX,
    CONF_BUNDLE_MIN,
    CONF_DEVICE_CLASS,
    CONF_ENTITY_OVERRIDES,
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


_LOGGER = logging.getLogger(__name__)


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


# Numbers without a configured range use a text box with these bounds.
UNBOUNDED = 1_000_000_000.0


def number_settings(
    cfg: Dict[str, Any],
    *,
    host: str,
    entity_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Native unit, device class, range, mode and step of a number config,
    with the options-flow overrides applied (all in the native unit)."""
    read_id = str(cfg.get(CONF_READ_ID) or cfg.get(CONF_ID))
    name = str(cfg.get(CONF_NAME) or "")
    hc_uid = str(cfg.get(CONF_HEATING_CIRCUIT_UID) or "").strip()
    configured_uuid = cfg.get(CONF_UUID)
    unique_id = str(configured_uuid) if configured_uuid else f"{host}:number:{read_id}".replace("=", "")
    unit = cfg.get(CONF_UNIT)
    device_class = None

    # Stable override key: do not depend on whether a bundle provides a UUID.
    override_key = f"{host}:number:{read_id}".replace("=", "")

    # Apply UI overrides (options flow). These are keyed by unique_id.
    bundle_min = cfg.get(CONF_BUNDLE_MIN)
    bundle_max = cfg.get(CONF_BUNDLE_MAX)
    if bundle_min is None and cfg.get(CONF_MIN) is not None:
        bundle_min = cfg.get(CONF_MIN)
    if bundle_max is None and cfg.get(CONF_MAX) is not None:
        bundle_max = cfg.get(CONF_MAX)

    min_v = cfg.get(CONF_MIN)
    max_v = cfg.get(CONF_MAX)
    step_v = cfg.get(CONF_STEP)

    if isinstance(entity_overrides, dict):
        # Newer versions store overrides by stable override_key; older ones used unique_id.
        ov = entity_overrides.get(override_key)
        if not isinstance(ov, dict):
            ov = entity_overrides.get(unique_id)
        if isinstance(ov, dict):
            _LOGGER.debug(
                "Applying overrides to number name=%s read_id=%s hc_uid=%s unique_id=%s override_key=%s override_keys=%s",
                name,
                read_id,
                hc_uid,
                unique_id,
                override_key,
                sorted([str(k) for k in ov.keys()]),
            )
            if CONF_UNIT in ov:
                ov_unit = ov.get(CONF_UNIT)
                unit = str(ov_unit) if ov_unit not in (None, "") else None

            dc = ov.get(CONF_DEVICE_CLASS)
            # Supplement corrections are temperature differences (K): never
            # an absolute temperature Home Assistant would convert.
            if dc not in (None, "") and not (cfg.get("supplement") and str(dc) == "temperature"):
                # NumberDeviceClass is not available in all HA versions.
                try:
                    from homeassistant.components.number import NumberDeviceClass  # type: ignore

                    device_class = NumberDeviceClass(str(dc))
                except Exception:
                    pass

            # Number range overrides (clamped to bundle bounds when known).
            if CONF_MIN in ov and ov.get(CONF_MIN) not in (None, ""):
                try:
                    min_v = float(ov.get(CONF_MIN))
                except Exception:
                    pass
            if CONF_MAX in ov and ov.get(CONF_MAX) not in (None, ""):
                try:
                    max_v = float(ov.get(CONF_MAX))
                except Exception:
                    pass
            if CONF_STEP in ov and ov.get(CONF_STEP) not in (None, ""):
                try:
                    step_v = float(ov.get(CONF_STEP))
                except Exception:
                    pass

    # Clamp to bundle-provided bounds if available.
    try:
        bmn = float(bundle_min) if bundle_min is not None else None
        bmx = float(bundle_max) if bundle_max is not None else None
    except Exception:
        bmn = bmx = None

    if bmn is not None and min_v is not None:
        try:
            min_v = max(float(min_v), bmn)
        except Exception:
            pass
    if bmx is not None and max_v is not None:
        try:
            max_v = min(float(max_v), bmx)
        except Exception:
            pass

    # Apply range/mode.
    out_min, out_max, mode = -UNBOUNDED, UNBOUNDED, NumberMode.BOX
    if min_v is not None and max_v is not None:
        try:
            mn_f = float(min_v)
            mx_f = float(max_v)
            if mx_f > mn_f:
                out_min, out_max, mode = mn_f, mx_f, NumberMode.SLIDER
        except Exception:
            pass
    # (No or no valid configured range -> a text box.)

    step = None
    if step_v is not None:
        try:
            step_f = float(step_v)
            if step_f > 0:
                step = step_f
        except Exception:
            pass

    return {
        "unit": unit,
        "device_class": device_class,
        "min": out_min,
        "max": out_max,
        "mode": mode,
        "step": step,
    }


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
        entity_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(coordinator)
        self._api = api
        self._host = host
        self._base_url = base_url
        self._parent_device_name = str(cfg.get("device_name") or f"Climatix ({host})")
        self._device_model = str(cfg.get("device_model") or "Climatix")
        self._device_serial = cfg.get("device_serial") or None
        self._device_sw_version = cfg.get("device_sw_version") or None
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
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = (
            str(configured_uuid)
            if configured_uuid
            else f"{host}:number:{self._read_id}".replace("=", "")
        )

        # Local discovery flags (bundle entities never set these).
        if cfg.get(CONF_ENABLED_DEFAULT) is False:
            self._attr_entity_registry_enabled_default = False
        if cfg.get(CONF_DIAGNOSTIC):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        settings = number_settings(cfg, host=host, entity_overrides=entity_overrides)
        self._attr_native_unit_of_measurement = settings["unit"]
        if settings["device_class"] is not None:
            self._attr_device_class = settings["device_class"]
        self._attr_native_min_value = settings["min"]
        self._attr_native_max_value = settings["max"]
        self._attr_mode = settings["mode"]
        if settings["step"] is not None:
            self._attr_native_step = settings["step"]

        if _is_temperature_unit(self._attr_native_unit_of_measurement):
            self._attr_icon = "mdi:thermometer"

    @property
    def device_info(self) -> DeviceInfo:
        if self._hc_uid:
            return DeviceInfo(
                identifiers={(DOMAIN, f"{self._host}:hc:{self._hc_uid}")},
                via_device_id=self.coordinator.device_id,
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
            serial_number=self._device_serial,
            sw_version=self._device_sw_version,
            configuration_url=self._base_url,
        )

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        return extract_first_numeric_value(data, self._read_id)

    async def async_set_native_value(self, value: float) -> None:
        desired = float(value)

        async with self.coordinator.async_write_session() as session:
            data = self.coordinator.data or {}
            current = extract_first_numeric_value(data, self._read_id)
            if current is not None:
                try:
                    if abs(float(current) - desired) < 1e-6:
                        return
                except (TypeError, ValueError):
                    pass

            session.refresh([self._read_id])
            await self._api.write(self._write_id, desired)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    controllers = store.get("controllers") or []
    entity_overrides = (entry.options or {}).get(CONF_ENTITY_OVERRIDES)
    entities = []
    for ctrl in controllers:
        coordinator: ClimatixCoordinator = ctrl["coordinator"]
        api: ClimatixGenericApi = ctrl["api"]
        host: str = ctrl["host"]
        base_url: str = ctrl.get("base_url", f"http://{host}")
        device_name: str = ctrl.get("device_name", f"Climatix ({host})")
        device_model: str = ctrl.get("device_model", "Climatix")
        device_serial = ctrl.get("device_serial")
        device_sw_version = ctrl.get("device_sw_version")
        numbers = ctrl.get("numbers", [])
        for n in numbers:
            entities.append(
                ClimatixGenericNumber(
                    coordinator,
                    api=api,
                    host=host,
                    base_url=base_url,
                    cfg=dict(n, device_name=device_name, device_model=device_model, device_serial=device_serial, device_sw_version=device_sw_version),
                    entity_overrides=entity_overrides,
                )
            )
    async_add_entities(entities)
