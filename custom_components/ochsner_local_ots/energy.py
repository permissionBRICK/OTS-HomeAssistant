"""Energy counters of the controller, classified for Home Assistant's Energy dashboard.

The controller counts thermal and electrical energy (kWh, whole numbers)
per purpose in yearly buckets (current year, previous year, 2 and 3 years
ago) plus lifetime totals for heating, cooling and defrosting. The current
year and lifetime counters only grow (the yearly ones restart on 1 January,
which ``total_increasing`` treats as a new meter cycle), so they get long-term
statistics and can be added to the Energy dashboard. The buckets of past
years are fixed values: device class energy, no statistics.

House AirHawk 518, 2026-10-25: total electrical this year 2346 kWh =
heating 2235 + defrosting 23 + auxiliary heater 81 (+ DHW and cooling 0).
"""

from __future__ import annotations

from typing import Optional, Tuple

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .plant import canonical_id

ENERGY_UNITS = frozenset({"Wh", "kWh", "MWh"})

# Counters that only grow: current year (restart on 1 January) and lifetime.
GROWING: frozenset = frozenset(
    canonical_id(i)
    for i in (
        # totals, current year
        "DCP7vSiJAAE=",  # total electrical energy consumption
        "DCNBAiiJAAE=",  # total thermal energy
        # electrical, current year
        "AyOFhCiJAAE=",  # heating
        "AyPJHSiJAAE=",  # DHW
        "AyOamiiJAAE=",  # cooling
        "AyPeUSiJAAE=",  # defrosting
        # thermal, current year
        "AyOvVSiJAAE=",  # heating
        "AyP3viiJAAE=",  # DHW
        "AyOwSyiJAAE=",  # cooling
        "AyPg8iiJAAE=",  # defrosting
        # auxiliary heater, current year
        "AyOEOiiJAAE=",  # heating
        "AyMxLSiJAAE=",  # DHW
        "AyNGByiJAAE=",  # defrosting
        # lifetime
        "AyMXFSiJAAE=",  # heating energy
        "AyPotiiJAAE=",  # heating energy (second counter)
        "AyPngiiJAAE=",  # electrical energy for heating
        "AyPo+SiJAAE=",  # cooling energy
        "AyMYbiiJAAE=",  # electrical energy for cooling
        "AyMUuSiJAAE=",  # defrosting energy
        "AyN02SiJAAE=",  # defrosting energy (second counter)
        "AyMXtyiJAAE=",  # electrical energy for defrosting
    )
)


def energy_classes(oid: str, unit: Optional[str]) -> Optional[Tuple[SensorDeviceClass, Optional[SensorStateClass]]]:
    """(device class, state class) of an energy sensor, None for other sensors."""
    if unit not in ENERGY_UNITS:
        return None
    state = SensorStateClass.TOTAL_INCREASING if canonical_id(oid) in GROWING else None
    return SensorDeviceClass.ENERGY, state
