"""Domestic hot water as a water_heater entity (only on plants with DHW).

Operation modes map to the app's DHW actions (p045d8/z.java): off = program
1; eco / performance (Komfort) = program 0 + comfort/eco 1 / 2 + boost 0;
high_demand = program 0 + boost 1 (a one-time charge the controller resets
itself). The target is the setpoint of the active mode.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.components.water_heater import (
    WaterHeaterEntity,
    WaterHeaterEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import plant as P
from .const import DOMAIN
from .coordinator import ClimatixCoordinator
from .plant_entity import async_write_planned, main_device_info, number_bounds, plant_runtime

# Setpoint bounds when the setpoint has no configured number entity
# (docs/agent-notes/dhw-setpoints.md: 5-75 °C, boost 40-75 °C).
_DEFAULT_BOUNDS = {
    "dhw.setpoint_comfort": (5.0, 75.0),
    "dhw.setpoint_eco": (5.0, 75.0),
    "dhw.setpoint_reduced": (5.0, 75.0),
    "dhw.setpoint_boost": (40.0, 75.0),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    entities: List[OchsnerDhwWaterHeater] = []
    for ctrl in store.get("controllers") or []:
        if (plant_runtime(ctrl).get("caps") or {}).get("dhw"):
            entities.append(OchsnerDhwWaterHeater(ctrl))
    async_add_entities(entities)


class OchsnerDhwWaterHeater(CoordinatorEntity[ClimatixCoordinator], WaterHeaterEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "dhw"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = 0.5
    _attr_operation_list = list(P.DHW_OPERATIONS)
    _attr_supported_features = (
        WaterHeaterEntityFeature.TARGET_TEMPERATURE
        | WaterHeaterEntityFeature.OPERATION_MODE
        | WaterHeaterEntityFeature.ON_OFF
    )

    def __init__(self, ctrl: Dict[str, Any]) -> None:
        super().__init__(ctrl["coordinator"])
        self._ctrl = ctrl
        self._attr_unique_id = f"{ctrl['host']}:water_heater:dhw"
        self._attr_device_info = main_device_info(ctrl)

    @property
    def _values(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        values = data.get("values") if isinstance(data, dict) else None
        return values if isinstance(values, dict) else {}

    def _get(self, role: str) -> Any:
        return P.first_value(self._values, P.DHW_ROLES[role])

    def _setpoint_role(self) -> str:
        return P.dhw_setpoint_role(self.current_operation)

    def _role_bounds(self, role: str) -> tuple[float, float]:
        lo, hi = number_bounds(self._ctrl, P.DHW_ROLES[role])
        dlo, dhi = _DEFAULT_BOUNDS[role]
        return (lo if lo is not None else dlo, hi if hi is not None else dhi)

    @property
    def current_operation(self) -> Optional[str]:
        return P.dhw_operation(self._values)

    @property
    def current_temperature(self) -> Optional[float]:
        return P.as_float(self._get("dhw.temp"))

    @property
    def target_temperature(self) -> Optional[float]:
        return P.as_float(self._get(self._setpoint_role()))

    @property
    def min_temp(self) -> float:
        return self._role_bounds(self._setpoint_role())[0]

    @property
    def max_temp(self) -> float:
        return self._role_bounds(self._setpoint_role())[1]

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            "offset": P.as_float(self._get("dhw.offset")),
            "effective_target": P.as_float(self._get("dhw.target")),
            "boost": P.as_int(self._get("dhw.boost")) == 1,
        }

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        if operation_mode not in P.DHW_OPERATIONS:
            raise HomeAssistantError(f"Unsupported operation {operation_mode}")
        await async_write_planned(
            self._ctrl, lambda values: P.plan_dhw_operation(operation_mode, values), P.dhw_mode_input_ids()
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        await async_write_planned(self._ctrl, P.plan_dhw_on, P.dhw_mode_input_ids())

    async def async_turn_off(self, **kwargs: Any) -> None:
        await async_write_planned(
            self._ctrl, lambda values: P.plan_dhw_operation(P.DHW_OFF, values), P.dhw_mode_input_ids()
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("operation_mode")) is not None:
            await self.async_set_operation_mode(mode)
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        requested = float(temperature)

        def plan(values):
            # The setpoint of the mode that is active once earlier writes are done.
            operation = P.dhw_operation(values)
            if operation is None:
                # Which setpoint is active is unknown: never guess one.
                raise P.UnknownValueError("dhw_operation_unknown", "DHW operation unknown")
            role = P.dhw_setpoint_role(operation)
            lo, hi = self._role_bounds(role)
            value = P.clamp(P.round_half(requested), lo, hi)
            oid = P.DHW_ROLES[role]
            current = P.as_float(P.first_value(values, oid))
            if current is not None and abs(current - value) < 1e-6:
                return []
            return [(oid, value)]

        await async_write_planned(self._ctrl, plan, P.dhw_setpoint_input_ids())
