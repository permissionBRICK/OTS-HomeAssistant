from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import ClimatixGenericApi, extract_first_value
from .const import (
    CONF_ENABLED_DEFAULT,
    CONF_HEATING_CIRCUIT_NAME,
    CONF_HEATING_CIRCUIT_UID,
    CONF_NAME,
    CONF_OFF_VALUE,
    CONF_ON_VALUE,
    CONF_READ_ID,
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
        ClimatixGenericSwitch(coordinator, api=api, host=host, base_url=base_url, cfg=s)
        for s in discovery_info.get("switches", [])
    ]
    async_add_entities(entities)


class ClimatixGenericSwitch(CoordinatorEntity[ClimatixCoordinator], SwitchEntity):
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

        self._attr_name = str(cfg[CONF_NAME])

        self._read_id = str(cfg.get(CONF_READ_ID) or "")
        self._write_id = str(cfg.get(CONF_WRITE_ID) or self._read_id)
        if not self._read_id:
            raise ValueError("Switch config missing read_id")
        if not self._write_id:
            raise ValueError("Switch config missing write_id")

        # Values written to turn the datapoint on / off. Kept as provided by the
        # bundle generator (index or state code) so they match the controller.
        if CONF_ON_VALUE not in cfg or CONF_OFF_VALUE not in cfg:
            raise ValueError("Switch config must include on_value and off_value")
        self._on_value: Any = cfg[CONF_ON_VALUE]
        self._off_value: Any = cfg[CONF_OFF_VALUE]

        enabled_default = cfg.get(CONF_ENABLED_DEFAULT)
        if enabled_default is False:
            self._attr_entity_registry_enabled_default = False

        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = (
            str(configured_uuid)
            if configured_uuid
            else f"{host}:switch:{self._read_id}".replace("=", "")
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
        raw = extract_first_value(data, self._read_id)
        if raw is None:
            return None
        if _values_equal(raw, self._on_value):
            return True
        if _values_equal(raw, self._off_value):
            return False
        # Fallback: treat any non-zero numeric read as "on" (handles controllers
        # that report the ordinal while on/off values are state codes).
        try:
            return abs(float(raw)) > 1e-6
        except (TypeError, ValueError):
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write_value(self._on_value)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write_value(self._off_value)

    async def _write_value(self, value: Any) -> None:
        data = self.coordinator.data or {}
        current_raw = extract_first_value(data, self._read_id)
        # Skip the write when the datapoint already holds the target value, to
        # avoid unnecessary controller writes.
        if _values_equal(current_raw, value):
            return

        await self._api.write(self._write_id, value)
        try:
            await self.coordinator.async_refresh_ids([self._read_id])
        except Exception:
            # Fallback: refresh everything if the targeted read fails.
            await self.coordinator.async_request_refresh()


def _values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    # Prefer numeric comparison (controller may return floats for integral values).
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


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
        switches = ctrl.get("switches", [])
        for s in switches:
            entities.append(
                ClimatixGenericSwitch(
                    coordinator,
                    api=api,
                    host=host,
                    base_url=base_url,
                    cfg=dict(s, device_name=device_name, device_model=device_model),
                )
            )
    async_add_entities(entities)
