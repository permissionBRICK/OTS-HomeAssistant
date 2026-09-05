"""Smart app DHW settings must work without a reference plant with DHW."""

import importlib.util
from pathlib import Path

import pytest


# Independently transcribed from Smart app v1.2.0/1785 (H7.Q0 and c7.z).
SETPOINTS = {
    "ASMv1XXZAAE=": (5.0, 75.0),
    "ASOp1XXZAAE=": (5.0, 75.0),
    "ASOXCHXZAAE=": (5.0, 75.0),
    "ASPuhXXZAAE=": (40.0, 75.0),
}


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[1] / "tools/build_discovery_catalog.py"
    spec = importlib.util.spec_from_file_location("dhw_catalog_builder", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_packaged_bindings_match_app_and_builder(catalog_mod, builder):
    catalog = catalog_mod.load_catalog()
    for oid, (minimum, maximum) in SETPOINTS.items():
        expected = {
            "platform": "number", "write_id": oid, "write_source": "apk_settings",
            "unit": "°C", "min": minimum, "max": maximum, "step": 0.5,
        }
        point = catalog.points_by_id[oid]
        rebuilt = {"id": oid, "platform": "sensor"}
        builder.apply_apk_number_binding(rebuilt)
        for key, value in expected.items():
            assert point[key] == value
            assert rebuilt[key] == value
    # Similar-looking measurements are not automatically made writable.
    for oid in ("CiOhSHXZAAE=", "CiPvuXXZAAE=", "CiMzqnXZAAE="):
        assert catalog.points_by_id[oid]["platform"] == "sensor"
        point = {"id": oid, "platform": "sensor"}
        builder.apply_apk_number_binding(point)
        assert point == {"id": oid, "platform": "sensor"}


def test_dhw_discovery_and_existing_sensor_rescan(catalog_mod, discovery_mod, entities_mod):
    catalog = catalog_mod.load_catalog()
    scan = discovery_mod.DiscoveryScanResult(
        catalog_version=catalog.catalog_version,
        values={oid: 50.0 for oid in SETPOINTS},
    )
    discovered = entities_mod.build_entities(catalog=catalog, scan=scan)
    assert {e["read_id"] for e in discovered["numbers"]} == set(SETPOINTS)
    assert discovered["sensors"] == []
    for entity in discovered["numbers"]:
        assert entity["write_id"] == entity["read_id"]
        assert entity["unit"] == "°C"
        assert entity["step"] == 0.5

    old_sensors = [{"id": oid, "name": "Existing dashboard sensor"} for oid in SETPOINTS]
    existing = {"sensors": old_sensors}
    merged, added = entities_mod.merge_discovered_entities(existing, discovered)
    assert added["numbers"] == 4
    assert merged["sensors"] == old_sensors
    assert existing == {"sensors": old_sensors}
    again, added_again = entities_mod.merge_discovered_entities(merged, discovered)
    assert again == merged
    assert not any(added_again.values())


def test_controller_without_dhw_gets_no_dhw_controls(catalog_mod, discovery_mod, entities_mod):
    catalog = catalog_mod.load_catalog()
    hc_id = "ASMhEo58AAE="
    scan = discovery_mod.DiscoveryScanResult(
        catalog_version=catalog.catalog_version,
        values={hc_id: 22.5},
        states={oid: 5 for oid in SETPOINTS},
    )
    discovered = entities_mod.build_entities(catalog=catalog, scan=scan)
    assert [e["read_id"] for e in discovered["numbers"]] == [hc_id]
    assert discovered["sensors"] == []
