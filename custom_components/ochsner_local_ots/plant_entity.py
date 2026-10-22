"""Shared helpers for the plant-level entities (climate, water_heater, button)."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


def plant_runtime(ctrl: Dict[str, Any]) -> Dict[str, Any]:
    """The per-controller plant state prepared at setup (empty if none)."""
    plant = ctrl.get("plant")
    return plant if isinstance(plant, dict) else {}


def main_device_info(ctrl: Dict[str, Any]) -> DeviceInfo:
    host = ctrl["host"]
    return DeviceInfo(
        identifiers={(DOMAIN, host)},
        name=str(ctrl.get("device_name") or f"Climatix ({host})"),
        manufacturer="Ochsner",
        model=str(ctrl.get("device_model") or "Climatix"),
        serial_number=ctrl.get("device_serial") or None,
        sw_version=ctrl.get("device_sw_version") or None,
        configuration_url=ctrl.get("base_url"),
    )


def circuit_device_info(ctrl: Dict[str, Any], uid: str, name: str) -> DeviceInfo:
    """Same shape as the circuit DeviceInfo of the generic platforms."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{ctrl['host']}:hc:{uid}")},
        via_device_id=ctrl["coordinator"].device_id,
        name=name or "Heating circuit",
        manufacturer="Ochsner",
        model=str(ctrl.get("device_model") or "Climatix"),
        configuration_url=ctrl.get("base_url"),
    )


Plan = Callable[[Mapping[str, Any]], List[Tuple[str, Any]]]


def coordinator_values(coordinator: Any) -> Dict[str, Any]:
    data = coordinator.data if isinstance(coordinator.data, dict) else {}
    values = data.get("values")
    return values if isinstance(values, dict) else {}


async def async_write_planned(
    ctrl: Dict[str, Any],
    plan: Plan,
    refresh_ids: Iterable[str] = (),
) -> List[Tuple[str, Any]]:
    """Decide and write (id, value) pairs under the controller's write lock.

    ``plan`` gets the current values once every earlier write and its
    re-read has finished, and returns only the values that differ from
    them, so overlapping commands neither repeat a write nor drop a change.
    The pairs go through the counted write path in order; afterwards the
    touched and dependent ids are re-read once (also after a failure).
    """
    api = ctrl["api"]
    coordinator = ctrl["coordinator"]
    async with coordinator.async_write_session() as session:
        try:
            writes = list(plan(coordinator_values(coordinator)))
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if not writes:
            return []
        session.refresh([oid for oid, _ in writes] + [i for i in refresh_ids if i])
        try:
            for oid, value in writes:
                await api.write(oid, value)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Writing to the heat pump failed: {type(err).__name__}") from err
    return writes


def number_bounds(ctrl: Dict[str, Any], read_id: str) -> Tuple[Optional[float], Optional[float]]:
    """(min, max) of the configured number entity for a read id, if any."""
    for cfg in ctrl.get("numbers", []) or []:
        if str(cfg.get("read_id")) == read_id:
            lo = cfg.get("min", cfg.get("bundle_min"))
            hi = cfg.get("max", cfg.get("bundle_max"))
            try:
                return (float(lo) if lo is not None else None, float(hi) if hi is not None else None)
            except (TypeError, ValueError):
                return (None, None)
    return (None, None)
