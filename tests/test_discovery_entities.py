"""Entity classification parity + unique-id compatibility + merge rules."""

from __future__ import annotations


def make_catalog(catalog_mod, points, tags):
    return catalog_mod.DiscoveryCatalog(
        {
            "schema_version": 1,
            "catalog_version": "test",
            "hc_tags": [31886, 19693, 23756, 11307],
            "points": points,
            "tags": [{"tag": t} for t in tags],
        }
    )


def make_scan(discovery_mod, values, circuit_names=None):
    scan = discovery_mod.DiscoveryScanResult(catalog_version="test")
    scan.values.update(values)
    scan.circuit_names.update(circuit_names or {})
    return scan


def test_writable_shapes_stay_writable(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    num_id = enc(8960, 100, 1, 256)
    sel_id = enc(8962, 100, 2, 256)
    sw_id = enc(8706, 100, 3, 256)
    txt_id = enc(8964, 100, 4, 256)
    points = [
        {"id": num_id, "platform": "number", "name": "Setpoint", "sources": ["reference"], "write_id": num_id, "min": 10.0, "max": 30.0, "bundle_min": 10.0, "bundle_max": 30.0, "step": 0.5, "unit": "°C"},
        {"id": sel_id, "platform": "select", "name": "Mode", "sources": ["reference"], "write_id": sel_id, "options": {"Aus": 1, "Komfort": 0}},
        {"id": sw_id, "platform": "switch", "name": "Boost", "sources": ["reference"], "write_id": sw_id, "on_value": 1, "off_value": 0},
        {"id": txt_id, "platform": "text", "name": "Label", "sources": ["reference"], "write_id": txt_id},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {num_id: 21.5, sel_id: 1.0, sw_id: 0.0, txt_id: "abc"})
    out = entities_mod.build_entities(catalog=cat, scan=scan)

    assert [n["read_id"] for n in out["numbers"]] == [num_id]
    assert out["numbers"][0]["write_id"] == num_id
    assert out["numbers"][0]["min"] == 10.0 and out["numbers"][0]["max"] == 30.0
    assert out["selects"][0]["options"] == {"Aus": 1, "Komfort": 0}
    assert out["switches"][0]["on_value"] == 1 and out["switches"][0]["off_value"] == 0
    assert out["texts"][0]["read_id"] == txt_id
    # A writable point must not additionally appear as a sensor.
    assert out["sensors"] == []


def test_unique_id_compatibility_no_uuid(catalog_mod, discovery_mod, entities_mod):
    # Discovery entities must NOT carry a uuid: the platforms then fall back to
    # the long-standing f"{host}:{platform}:{read_id}".replace("=", "") scheme.
    enc = catalog_mod.encode_oa
    oid = enc(8960, 100, 1, 256)
    cat = make_catalog(catalog_mod, [{"id": oid, "platform": "sensor", "name": "T", "sources": ["apk"]}], [100])
    out = entities_mod.build_entities(catalog=cat, scan=make_scan(discovery_mod, {oid: 1.0}))
    for lists in out.values():
        for ent in lists:
            assert "uuid" not in ent


def test_demotion_guards(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    num_id = enc(8960, 100, 1, 256)
    txt_id = enc(8964, 100, 2, 256)
    no_write = enc(8962, 100, 3, 256)
    sw_no_onoff = enc(8706, 100, 4, 256)
    points = [
        # Number whose live value is a string -> plain sensor.
        {"id": num_id, "platform": "number", "name": "N", "sources": ["reference"], "write_id": num_id},
        # Text whose live value is numeric -> plain sensor.
        {"id": txt_id, "platform": "text", "name": "T", "sources": ["reference"], "write_id": txt_id},
        # Select without a write id -> sensor.
        {"id": no_write, "platform": "select", "name": "S", "sources": ["reference"], "options": {"A": 0}},
        # Switch without on/off values -> binary sensor.
        {"id": sw_no_onoff, "platform": "switch", "name": "B", "sources": ["reference"], "write_id": sw_no_onoff},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {num_id: "text", txt_id: 3.5, no_write: 0.0, sw_no_onoff: 1.0})
    out = entities_mod.build_entities(catalog=cat, scan=scan)

    assert out["numbers"] == [] and out["texts"] == [] and out["selects"] == [] and out["switches"] == []
    assert {s["id"] for s in out["sensors"]} == {num_id, txt_id, no_write}
    assert [b["id"] for b in out["binary_sensors"]] == [sw_no_onoff]


def test_disabled_by_default_and_diagnostic_flags(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    weak = enc(8960, 100, 1, 256)
    priv = enc(8964, 100, 2, 256)
    reset_sw = enc(8706, 100, 3, 256)
    points = [
        {"id": weak, "platform": "sensor", "name": "n1_SomeSymbol", "sources": ["apk"], "diagnostic": True, "enabled_default": False},
        {"id": priv, "platform": "text", "name": "Kunde", "sources": ["reference"], "write_id": priv, "diagnostic": True, "enabled_default": False},
        # Destructive one-shot: still created and writable, but disabled.
        {"id": reset_sw, "platform": "switch", "name": "Geräte-Reset", "sources": ["reference"], "write_id": reset_sw, "on_value": 1, "off_value": 0, "enabled_default": False},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {weak: 5.0, priv: "Max Mustermann", reset_sw: 0.0})
    out = entities_mod.build_entities(catalog=cat, scan=scan)

    s = out["sensors"][0]
    assert s["enabled_default"] is False and s["diagnostic"] is True
    t = out["texts"][0]
    assert t["enabled_default"] is False and t["diagnostic"] is True
    sw = out["switches"][0]
    assert sw["enabled_default"] is False
    # The destructive point is still a real switch (created, writable, disabled).
    assert sw["write_id"] == reset_sw


def test_circuit_grouping_uses_controller_names(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    hc1 = enc(8960, 31886, 1, 256)
    hc2 = enc(8960, 19693, 1, 256)
    points = [
        {"id": hc1, "platform": "sensor", "name": "Raumtemperatur", "sources": ["reference"], "hc_tag": 31886},
        {"id": hc2, "platform": "sensor", "name": "Raumtemperatur", "sources": ["reference"], "hc_tag": 19693},
    ]
    cat = make_catalog(catalog_mod, points, [31886, 19693])
    scan = make_scan(discovery_mod, {hc1: 21.0, hc2: 22.0}, circuit_names={31886: "Fussboden"})
    out = entities_mod.build_entities(catalog=cat, scan=scan)

    by_id = {s["id"]: s for s in out["sensors"]}
    assert by_id[hc1]["heating_circuit_uid"] == "tag:31886"
    assert by_id[hc1]["heating_circuit_name"] == "Fussboden"
    # No controller-provided name -> ordinal fallback.
    assert by_id[hc2]["heating_circuit_name"] == "Heizkreis 2"

    # Merging into an existing bundle entry keeps the bundle's circuit device.
    uid_map = entities_mod.hc_uid_map_from_existing_entities(
        {"sensors": [{"id": hc1, "heating_circuit_uid": "bundle-uuid-1"}]},
        cat.hc_tags,
    )
    assert uid_map == {31886: "bundle-uuid-1"}
    out2 = entities_mod.build_entities(catalog=cat, scan=scan, hc_uid_by_tag=uid_map)
    assert {s["id"]: s for s in out2["sensors"]}[hc1]["heating_circuit_uid"] == "bundle-uuid-1"


def test_merge_is_additive_and_never_duplicates(catalog_mod, entities_mod):
    enc = catalog_mod.encode_oa
    known = enc(8960, 100, 1, 256)
    new = enc(8960, 100, 2, 256)
    ctrl = {
        "host": "1.2.3.4",
        "sensors": [{"name": "Known", "uuid": "bundle-uuid", "id": known}],
        "numbers": [],
    }
    discovered = {
        "sensors": [
            {"name": "Known (rediscovered)", "id": known},
            {"name": "New", "id": new},
        ],
        "numbers": [{"name": "Known as number", "read_id": known, "write_id": known}],
    }
    merged, added = entities_mod.merge_discovered_entities(ctrl, discovered)

    # The already-exposed sensor is not duplicated; the new one is added.
    assert [s["name"] for s in merged["sensors"]] == ["Known", "New"]
    # A writable control may still be added next to an existing read-only sensor.
    assert [n["name"] for n in merged["numbers"]] == ["Known as number"]
    assert added["sensors"] == 1 and added["numbers"] == 1
    # Existing entries are untouched (same objects, same uuid -> same unique_id).
    assert merged["sensors"][0]["uuid"] == "bundle-uuid"


def test_bundle_overlay_can_add_a_fifth_circuit(catalog_mod, discovery_mod, entities_mod):
    """A donated bundle proving more circuits than the packaged catalog knows
    extends grouping and the controller-name read to those circuits too."""

    enc = catalog_mod.encode_oa
    hc1 = enc(8960, 31886, 1, 256)
    base_raw = {
        "schema_version": 1,
        "catalog_version": "test",
        "hc_tags": [31886, 19693, 23756, 11307],
        "points": [{"id": hc1, "platform": "sensor", "name": "Raumtemperatur", "sources": ["reference"], "hc_tag": 31886}],
        "tags": [{"tag": 31886}],
    }
    fifth_tag = 41000
    hc5 = enc(8960, fifth_tag, 1, 256)
    overlay = entities_mod.overlay_points_from_bundle_entities(
        {"sensors": [{"id": hc5, "name": "Raumtemperatur", "heating_circuit_uid": "bundle-hc5"}]}
    )
    cat = entities_mod.catalog_with_overlay(base_raw, overlay)
    assert fifth_tag in cat.hc_tags
    assert fifth_tag in {t["tag"] for t in cat.tags}

    scan = discovery_mod.DiscoveryScanResult(catalog_version="test")
    scan.values[hc5] = 21.0
    scan.circuit_names[fifth_tag] = "Gästehaus"
    out = entities_mod.build_entities(catalog=cat, scan=scan)
    ent = out["sensors"][0]
    assert ent["heating_circuit_uid"] == f"tag:{fifth_tag}"
    assert ent["heating_circuit_name"] == "Gästehaus"


def test_single_circuit_plant_only_groups_present_circuit(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    hc1 = enc(8960, 31886, 1, 256)
    hc2 = enc(8960, 19693, 1, 256)
    points = [
        {"id": hc1, "platform": "sensor", "name": "Raumtemperatur", "sources": ["reference"], "hc_tag": 31886},
        {"id": hc2, "platform": "sensor", "name": "Raumtemperatur", "sources": ["reference"], "hc_tag": 19693},
    ]
    cat = make_catalog(catalog_mod, points, [31886, 19693])
    # Only circuit 1 answered the scan.
    out = entities_mod.build_entities(catalog=cat, scan=make_scan(discovery_mod, {hc1: 20.0}))
    assert len(out["sensors"]) == 1
    assert out["sensors"][0]["id"] == hc1


def test_merge_dedupe_is_padding_independent(catalog_mod, entities_mod):
    # An existing entity stored with an unpadded id must block re-adding the
    # padded spelling of the same OA (and vice versa).
    ctrl = {"sensors": [{"name": "Old", "uuid": "u", "id": "BCNL7o58AAE"}]}
    discovered = {"sensors": [{"name": "New spelling", "id": "BCNL7o58AAE="}]}
    merged, added = entities_mod.merge_discovered_entities(ctrl, discovered)
    assert added["sensors"] == 0
    assert [s["name"] for s in merged["sensors"]] == ["Old"]


def test_overlay_ids_are_canonicalized(catalog_mod, entities_mod):
    overlay = entities_mod.overlay_points_from_bundle_entities(
        {"texts": [{"name": "T", "read_id": "BCNL7o58AAE", "write_id": "BCNL7o58AAE"}]}
    )
    assert overlay[0]["id"] == "BCNL7o58AAE="
    assert overlay[0]["write_id"] == "BCNL7o58AAE="


def test_schedule_and_descriptor_points_never_become_entities(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    sched = enc(8960, 100, 1, 514)
    desc = enc(8960, 100, 2, 4353)
    points = [
        {"id": sched, "platform": "sensor", "name": "Sched", "sources": ["apk"], "schedule": True},
        {"id": desc, "platform": "sensor", "name": "Desc", "sources": ["apk"], "descriptor": True},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {sched: 1.0, desc: 2.0})
    out = entities_mod.build_entities(catalog=cat, scan=scan)
    assert all(not lst for lst in out.values())
