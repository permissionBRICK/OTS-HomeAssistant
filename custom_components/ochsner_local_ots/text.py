from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.config_entries import ConfigEntry

from .api import ClimatixGenericApi, extract_first_value
from .const import (
    CONF_HEATING_CIRCUIT_NAME,
    CONF_HEATING_CIRCUIT_UID,
    CONF_ID,
    CONF_NAME,
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
        ClimatixGenericText(coordinator, api=api, host=host, base_url=base_url, cfg=t)
        for t in discovery_info.get("texts", [])
    ]
    async_add_entities(entities)


class ClimatixGenericText(CoordinatorEntity[ClimatixCoordinator], TextEntity):
    _attr_mode = TextMode.TEXT
    _attr_entity_category = EntityCategory.CONFIG

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
            raise ValueError("Text config missing read_id (or id)")
        if not self._write_id or self._write_id == "None":
            raise ValueError("Text config missing write_id (or id)")

        self._attr_name = str(cfg[CONF_NAME])
        configured_uuid = cfg.get(CONF_UUID)
        self._attr_unique_id = (
            str(configured_uuid)
            if configured_uuid
            else f"{host}:text:{self._read_id}".replace("=", "")
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
    def native_value(self) -> Optional[str]:
        data = self.coordinator.data or {}
        raw = extract_first_value(data, self._read_id)
        if raw is None:
            return None
        # Avoid showing dict/list in the UI; keep it readable.
        if isinstance(raw, (dict, list)):
            return str(raw)
        return str(raw)

    async def async_set_value(self, value: str) -> None:
        desired = "" if value is None else str(value)

        data = self.coordinator.data or {}
        current = extract_first_value(data, self._read_id)
        if current is not None and str(current) == desired:
            return

        await self._api.write(self._write_id, desired)
        try:
            await self.coordinator.async_refresh_ids([self._read_id])
        except Exception:
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
        texts = ctrl.get("texts", [])
        for t in texts:
            entities.append(
                ClimatixGenericText(
                    coordinator,
                    api=api,
                    host=host,
                    base_url=base_url,
                    cfg=dict(t, device_name=device_name, device_model=device_model),
                )
            )
    async_add_entities(entities)
