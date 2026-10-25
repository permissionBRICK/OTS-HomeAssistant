"""Energy counters are classified for the Energy dashboard; ids stay unchanged."""

from __future__ import annotations

import pytest

pytest.importorskip("homeassistant")

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass  # noqa: E402

from custom_components.ochsner_local_ots.energy import GROWING, energy_classes  # noqa: E402
from custom_components.ochsner_local_ots.catalog import load_catalog  # noqa: E402
from custom_components.ochsner_local_ots.const import CONF_ENTITY_OVERRIDES  # noqa: E402
from tests.test_dashboard_ha import KEY, hass, setup_plant, teardown  # noqa: E402,F401  (pytest fixtures)


def test_growing_counters_are_energy_points_of_the_catalog():
    points = {p["id"]: p for p in load_catalog().points_by_id.values()}
    for oid in GROWING:
        assert oid in points, oid
        assert points[oid].get("unit") == "kWh", oid
        name = points[oid].get("name_en", "")
        assert "previous year" not in name and "ago" not in name, name


def test_classes():
    assert energy_classes("DCP7vSiJAAE=", "kWh") == (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)
    assert energy_classes("DCP7vSiJAAE", "kWh") == (SensorDeviceClass.ENERGY, SensorStateClass.TOTAL_INCREASING)
    assert energy_classes("DCPOmiiJAAE=", "kWh") == (SensorDeviceClass.ENERGY, None)  # previous year
    assert energy_classes("AyKWuiiJAAE=", "°C") is None


@pytest.mark.asyncio
async def test_energy_sensors_for_the_energy_dashboard(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        total = plant.entities[f"{KEY}:sensor:DCP7vSiJAAE"]
        assert total.device_class == SensorDeviceClass.ENERGY
        assert total.state_class == SensorStateClass.TOTAL_INCREASING
        assert total.native_unit_of_measurement == "kWh"
        assert total.native_value == 2322 and isinstance(total.native_value, int)
        thermal = plant.entities[f"{KEY}:sensor:DCNBAiiJAAE"]
        assert thermal.state_class == SensorStateClass.TOTAL_INCREASING
        previous = plant.entities[f"{KEY}:sensor:DCPOmiiJAAE"]
        assert previous.device_class == SensorDeviceClass.ENERGY and previous.state_class is None
        outdoor = plant.entities[f"{KEY}:sensor:AyKWuiiJAAE"]
        assert outdoor.device_class != SensorDeviceClass.ENERGY
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_user_override_still_wins(hass, monkeypatch, fake_controller):
    overrides = {CONF_ENTITY_OVERRIDES: {f"{KEY}:sensor:DCP7vSiJAAE": {"state_class": "total"}}}
    plant = await setup_plant(hass, monkeypatch, fake_controller, options=overrides)
    try:
        assert plant.entities[f"{KEY}:sensor:DCP7vSiJAAE"].state_class == SensorStateClass.TOTAL
    finally:
        await teardown(hass, plant)
