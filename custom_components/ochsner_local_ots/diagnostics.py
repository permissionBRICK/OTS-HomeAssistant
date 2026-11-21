"""Diagnostics download: the plant's setup and its settings backups.

The download doubles as the export of the settings backups: the options
flow's restore accepts it as a file. Credentials, addresses and the serial
number are left out (the backups carry a fingerprint of the serial instead).
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import settings_backup
from .catalog import load_catalog
from .const import CONF_DISCOVERY_SOURCE, CONF_SCAN_INTERVAL, DOMAIN

_PLATFORMS = ("sensors", "binary_sensors", "numbers", "selects", "texts", "switches")
_OPTIONS = ("language", "sidebar_panel", "color_scheme", CONF_SCAN_INTERVAL, "polling_threshold", "max_ids_per_read_request")


def _controllers(hass: HomeAssistant, entry: ConfigEntry) -> List[Dict[str, Any]]:
    runtime = (hass.data.get(DOMAIN) or {}).get(entry.entry_id)
    return list(runtime.get("controllers") or []) if isinstance(runtime, dict) else []


def _stored(entry: ConfigEntry, ctrl: Mapping[str, Any]) -> Mapping[str, Any]:
    for stored in entry.data.get("controllers") or []:
        if isinstance(stored, Mapping) and stored.get("identity_key") == ctrl.get("host"):
            return stored
    return {}


async def _async_controller(hass: HomeAssistant, entry: ConfigEntry, ctrl: Mapping[str, Any]) -> Dict[str, Any]:
    catalog = await hass.async_add_executor_job(load_catalog)
    store = await settings_backup.async_get_store(hass)
    plant = ctrl.get("plant") if isinstance(ctrl.get("plant"), Mapping) else {}
    caps = plant.get("caps") or {}
    runtime = (hass.data.get(DOMAIN) or {}).get(entry.entry_id) or {}
    coordinator = ctrl.get("coordinator")
    return {
        "controller": settings_backup.controller_info(ctrl),
        "discovery_source": _stored(entry, ctrl).get(CONF_DISCOVERY_SOURCE),
        "available": bool(coordinator and coordinator.last_update_success),
        "plant": {
            "circuits": [
                {k: c.get(k) for k in ("index", "heat", "cool", "room_sensor", "humidity")}
                for c in caps.get("circuits") or []
            ],
            **{k: v for k, v in caps.items() if k != "circuits" and isinstance(v, (bool, int, str))},
            "language": plant.get("language"),
        },
        "entities": {key: len(ctrl.get(key) or []) for key in _PLATFORMS},
        "flash_writes": (runtime.get("write_counts") or {}).get(ctrl.get("host")),
        "settings_backup": settings_backup.export(ctrl, store.backups(str(ctrl["host"])), catalog),
    }


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> Dict[str, Any]:
    return {
        "options": {k: entry.options.get(k) for k in _OPTIONS if k in entry.options},
        "entity_overrides": len(entry.options.get("entity_overrides") or {}),
        "controllers": [await _async_controller(hass, entry, ctrl) for ctrl in _controllers(hass, entry)],
    }


def _controller_of(hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry) -> Optional[Dict[str, Any]]:
    """The controller of a main device or of one of its circuit devices."""
    for ctrl in _controllers(hass, entry):
        host = str(ctrl["host"])
        for domain, ident in device.identifiers:
            if domain == DOMAIN and (ident == host or str(ident).startswith(f"{host}:hc:")):
                return ctrl
    return None


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: dr.DeviceEntry
) -> Dict[str, Any]:
    ctrl = _controller_of(hass, entry, device)
    if ctrl is None:
        return {"controller": None}
    return await _async_controller(hass, entry, ctrl)

