from __future__ import annotations

from typing import Any, Dict, Optional

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ClimatixGenericApi, extract_first_value
from .const import CONF_NAME, CONF_OPTIONS, CONF_READ_ID, CONF_WRITE_ID, DOMAIN
from .coordinator import ClimatixCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator: ClimatixCoordinator = store["coordinator"]
    api: ClimatixGenericApi = store["api"]
    host: str = store["host"]
    selects = store.get("selects", [])

    entities = [ClimatixGenericSelect(coordinator, api=api, host=host, cfg=s) for s in selects]
    async_add_entities(entities)


class ClimatixGenericSelect(CoordinatorEntity[ClimatixCoordinator], SelectEntity):
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

        self._attr_name = str(cfg[CONF_NAME])

        self._read_id = str(cfg.get(CONF_READ_ID) or "")
        self._write_id = str(cfg.get(CONF_WRITE_ID) or self._read_id)
        if not self._read_id:
            raise ValueError("Select config missing read_id")
        if not self._write_id:
            raise ValueError("Select config missing write_id")

        options = cfg.get(CONF_OPTIONS)
        if not isinstance(options, dict) or not options:
            raise ValueError("Select config must include non-empty options mapping")

        # options: displayed text -> written value
        self._label_to_value: Dict[str, Any] = {str(k): v for k, v in options.items()}
        self._value_to_label: Dict[str, str] = {str(v): str(k) for k, v in self._label_to_value.items()}

        self._attr_options = list(self._label_to_value.keys())
        self._attr_unique_id = f"{host}_select_oa_{self._read_id}".replace("=", "")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=f"Climatix ({self._host})",
            manufacturer="Siemens",
            model="Climatix",
        )

    def _match_label_for_value(self, raw: Any) -> Optional[str]:
        if raw is None:
            return None

        # Try numeric comparison first (handles controller returning 1.0 while config uses 1).
        try:
            raw_f = float(raw)
            for label, v in self._label_to_value.items():
                try:
                    v_f = float(v)
                except (TypeError, ValueError):
                    continue
                if abs(raw_f - v_f) < 1e-6:
                    return label
        except (TypeError, ValueError):
            pass

        # Fallback: string match
        return self._value_to_label.get(str(raw))

    @property
    def current_option(self) -> Optional[str]:
        data = self.coordinator.data or {}
        raw = extract_first_value(data, self._read_id)
        return self._match_label_for_value(raw)

    async def async_select_option(self, option: str) -> None:
        if option not in self._label_to_value:
            raise ValueError(f"Unknown option: {option}")
        value = self._label_to_value[option]
        await self._api.write(self._write_id, value)
        await self.coordinator.async_request_refresh()
