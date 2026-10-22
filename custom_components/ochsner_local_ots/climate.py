"""One climate entity per present heating circuit (the app's circuit tile).

State and writes follow the OCHSNER Smart app exactly (see plant.py): on/off
is the operating-program selection (0/1), heat/cool/auto is the
summer/winter changeover (3/2/4) on cool-capable circuits, comfort/eco is
the comfort/eco program (0/1) and the target is moved with the ±3 K room
setpoint correction, like the app's −/+ buttons.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import plant as P
from .const import DOMAIN
from .coordinator import ClimatixCoordinator
from .plant_entity import async_write_planned, circuit_device_info, plant_runtime


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    entities: List[OchsnerCircuitClimate] = []
    for ctrl in store.get("controllers") or []:
        plant = plant_runtime(ctrl)
        for circuit in (plant.get("caps") or {}).get("circuits", []) or []:
            entities.append(OchsnerCircuitClimate(ctrl, circuit))
    async_add_entities(entities)


class OchsnerCircuitClimate(CoordinatorEntity[ClimatixCoordinator], ClimateEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "heating_circuit"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = P.OFFSET_STEP
    _attr_preset_modes = list(P.PRESET_VALUES)
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, ctrl: Dict[str, Any], circuit: Dict[str, Any]) -> None:
        super().__init__(ctrl["coordinator"])
        self._ctrl = ctrl
        self._plant = plant_runtime(ctrl)
        self._n = int(circuit["index"])
        self._heat = bool(circuit.get("heat"))
        self._cool = bool(circuit.get("cool"))
        self._room_sensor = bool(circuit.get("room_sensor"))
        self._humidity = bool(circuit.get("humidity"))
        prefix = f"hc{self._n}."
        self._ids: Dict[str, str] = {
            role[len(prefix):]: oid for role, oid in P.circuit_role_ids(self._n).items()
        }
        uid, name = (self._plant.get("circuit_devices") or {}).get(
            self._n, (f"tag:{P.HC_TAGS[self._n - 1]}", "")
        )
        self._attr_device_info = circuit_device_info(
            ctrl, uid, name or P.default_circuit_name(self._n, self._plant.get("language") or "en")
        )
        self._attr_unique_id = f"{ctrl['host']}:climate:hc{self._n}"
        self._attr_hvac_modes = [HVACMode(m) for m in P.hvac_modes(self._heat, self._cool)]
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    # -- helpers -----------------------------------------------------------

    @property
    def _values(self) -> Dict[str, Any]:
        data = self.coordinator.data or {}
        values = data.get("values") if isinstance(data, dict) else None
        return values if isinstance(values, dict) else {}

    def _get(self, key: str) -> Any:
        return P.first_value(self._values, self._ids.get(key))

    def _label(self, key: str, raw: Any) -> Optional[str]:
        v = P.as_int(raw)
        if v is None:
            return None
        labels = (self._plant.get("labels") or {}).get(f"hc{self._n}.{key}") or {}
        return labels.get(str(v), str(v))

    def _dependents(self) -> List[str]:
        return [self._ids[k] for k in P.CIRCUIT_DEPENDENTS if k in self._ids]

    async def _write(self, plan) -> None:
        """Run ``plan(values)`` on the values current under the write lock."""
        await async_write_planned(self._ctrl, plan, self._dependents())

    # -- state -------------------------------------------------------------

    @property
    def hvac_mode(self) -> Optional[HVACMode]:
        mode = P.hvac_mode(self._get("op_program"), self._get("season"), self._heat, self._cool)
        return HVACMode(mode) if mode else None

    @property
    def hvac_action(self) -> Optional[HVACAction]:
        action = P.hvac_action_from_badge(self._get("badge"))
        return HVACAction(action) if action else None

    @property
    def preset_mode(self) -> Optional[str]:
        return P.preset_mode(self._get("comfort_eco"))

    @property
    def target_temperature(self) -> Optional[float]:
        return P.circuit_target(self._values, self._ids)

    @property
    def current_temperature(self) -> Optional[float]:
        if not self._room_sensor:
            return None
        return P.as_float(self._get("room_temp"))

    @property
    def current_humidity(self) -> Optional[float]:
        if not self._humidity:
            return None
        return P.as_float(self._get("humidity"))

    def _bounds(self):
        return P.offset_bounds(self.target_temperature, P.as_float(self._get("room_offset")))

    @property
    def min_temp(self) -> float:
        b = self._bounds()
        return b[0] if b else super().min_temp

    @property
    def max_temp(self) -> float:
        b = self._bounds()
        return b[1] if b else super().max_temp

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return {
            "badge": P.badge_name(self._get("badge")),
            "operating_program": self._label("op_program", self._get("op_program")),
            "room_offset": P.as_float(self._get("room_offset")),
            "flow_temperature": P.as_float(self._get("flow_actual")),
            "flow_target": P.as_float(self._get("flow_target")),
            "circuit_status": self._label("status", self._get("status")),
        }

    # -- actions -----------------------------------------------------------

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = str(hvac_mode)
        if mode not in P.hvac_modes(self._heat, self._cool):
            raise HomeAssistantError(f"Unsupported mode {mode} for this heating circuit")
        await self._write(lambda values: P.plan_hvac_mode(mode, values, self._ids, self._cool))

    async def async_turn_on(self) -> None:
        await self._write(lambda values: P.plan_turn_on(values, self._ids))

    async def async_turn_off(self) -> None:
        await self._write(lambda values: P.plan_hvac_mode(P.HVAC_OFF, values, self._ids, self._cool))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        if preset_mode not in P.PRESET_VALUES:
            raise HomeAssistantError(f"Unsupported preset {preset_mode}")
        await self._write(lambda values: P.plan_preset(preset_mode, values, self._ids))

    async def async_set_temperature(self, **kwargs: Any) -> None:
        if (mode := kwargs.get("hvac_mode")) is not None:
            await self.async_set_hvac_mode(mode)
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is None:
            return
        requested = float(temperature)
        await self._write(lambda values: P.plan_temperature(requested, values, self._ids))
