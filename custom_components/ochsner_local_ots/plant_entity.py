"""Shared helpers for the plant-level entities (climate, water_heater, button)."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo

from .api import clean_number
from .const import DOMAIN
from .plant import UnknownValueError


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


async def async_write_planned(
    ctrl: Dict[str, Any],
    plan: Plan,
    reads: Iterable[str],
    refresh_ids: Iterable[str] = (),
) -> List[Tuple[str, Any]]:
    """Decide and write (id, value) pairs under the controller's write lock.

    The ids in ``reads`` (everything the plan decides from) are read once
    after every earlier write and its re-read has finished; ``plan`` gets
    those fresh values (an id the read did not return is unknown, never its
    cached value) and returns only the values that differ from them, so
    neither overlapping commands nor changes made outside HA make it repeat
    a write or drop a change. The pairs go through the counted write path
    in order; afterwards the touched and dependent ids are re-read once
    (also after a failure).
    """
    api = ctrl["api"]
    coordinator = ctrl["coordinator"]
    async with coordinator.async_write_session() as session:
        values = await coordinator.async_fresh_values(reads)
        try:
            writes = list(plan(values))
        except UnknownValueError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key=err.translation_key) from err
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if not writes:
            return []
        session.refresh([oid for oid, _ in writes] + [i for i in refresh_ids if i])
        try:
            for oid, value in writes:
                if isinstance(value, float):
                    value = clean_number(value)
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
