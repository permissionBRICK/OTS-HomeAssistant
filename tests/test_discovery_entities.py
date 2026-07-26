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


def test_no_read_only_downgrade(catalog_mod, discovery_mod, entities_mod):
    """Spec v4 G: a readable point of a known writable type stays a writable
    entity — the live value's momentary shape never demotes it."""

    enc = catalog_mod.encode_oa
    num_id = enc(8960, 100, 1, 256)
    txt_id = enc(8964, 100, 2, 256)
    points = [
        # Number whose live value happens to be a string: STAYS a number.
        {"id": num_id, "platform": "number", "name": "N", "sources": ["reference"], "write_id": num_id},
        # Text whose live value happens to be numeric: STAYS a text.
        {"id": txt_id, "platform": "text", "name": "T", "sources": ["reference"], "write_id": txt_id},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {num_id: "text", txt_id: 3.5})
    out = entities_mod.build_entities(catalog=cat, scan=scan)

    assert [n["read_id"] for n in out["numbers"]] == [num_id]
    assert [t["read_id"] for t in out["texts"]] == [txt_id]
    assert out["sensors"] == []


def test_structurally_impossible_entities_fall_back(catalog_mod, discovery_mod, entities_mod):
    """Only a structurally impossible writable falls back to read-only: no
    write binding at all, a select without options, a switch without on/off."""

    enc = catalog_mod.encode_oa
    no_write = enc(8962, 100, 3, 256)
    sw_no_onoff = enc(8706, 100, 4, 256)
    points = [
        {"id": no_write, "platform": "select", "name": "S", "sources": ["reference"], "options": {"A": 0}},
        {"id": sw_no_onoff, "platform": "switch", "name": "B", "sources": ["reference"], "write_id": sw_no_onoff},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {no_write: 0.0, sw_no_onoff: 1.0})
    out = entities_mod.build_entities(catalog=cat, scan=scan)

    assert out["selects"] == [] and out["switches"] == []
    assert [s["id"] for s in out["sensors"]] == [no_write]
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


def test_pump_enum_labels_override_catalog_options(catalog_mod, discovery_mod, entities_mod):
    """Spec v4 C: the state list read from the controller's own descriptor
    wins over packaged metadata; index in the list = numeric value."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 40, 290)
    vm_id = enc(8971, 100, 41, 256)
    points = [
        {"id": sel_id, "platform": "select", "name": "Mode", "sources": ["reference"], "write_id": sel_id, "options": {"Komfort": 0, "Aus": 1}},
        {"id": vm_id, "platform": "sensor", "name": "Status", "sources": ["reference"], "value_map": {"0": "Standby", "1": "Heizbetrieb"}},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {sel_id: 0, vm_id: 2})
    scan.enum_labels[sel_id] = ["Comfort", "Off", "Red", "Norm"]
    scan.enum_labels[vm_id] = ["Off", "Htg", "Stby", "Dhw"]

    out = entities_mod.build_entities(catalog=cat, scan=scan)
    assert out["selects"][0]["options"] == {"Comfort": 0, "Off": 1, "Red": 2, "Norm": 3}
    assert out["sensors"][0]["value_map"] == {"0": "Off", "1": "Htg", "2": "Stby", "3": "Dhw"}


def test_pump_enum_duplicate_labels_keep_every_value(catalog_mod, discovery_mod, entities_mod):
    """The descriptor is authoritative for WHICH options exist: a duplicate
    label must never drop a value. Identical raw tokens get a deterministic
    " (value)" disambiguation (review round 3)."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 40, 290)
    vm_id = enc(8971, 100, 41, 256)
    points = [
        {"id": sel_id, "platform": "select", "name": "Mode", "sources": ["reference"], "write_id": sel_id, "options": {"A": 0}},
        {"id": vm_id, "platform": "sensor", "name": "Status", "sources": ["reference"], "value_map": {"0": "x"}},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    scan = make_scan(discovery_mod, {sel_id: 0, vm_id: 0})
    scan.enum_labels[sel_id] = ["Standby", "Heat", "Standby"]
    scan.enum_labels[vm_id] = ["Standby", "Heat", "Standby"]

    out = entities_mod.build_entities(catalog=cat, scan=scan)
    assert out["selects"][0]["options"] == {"Standby": 0, "Heat": 1, "Standby (2)": 2}
    # value->label is display-only: duplicates allowed, no disambiguation.
    assert out["sensors"][0]["value_map"] == {"0": "Standby", "1": "Heat", "2": "Standby"}


def test_missing_pump_enum_falls_back_to_catalog(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 40, 290)
    points = [
        {"id": sel_id, "platform": "select", "name": "Mode", "sources": ["reference"], "write_id": sel_id, "options": {"Komfort": 0, "Aus": 1}},
    ]
    cat = make_catalog(catalog_mod, points, [100])
    out = entities_mod.build_entities(catalog=cat, scan=make_scan(discovery_mod, {sel_id: 0}))
    assert out["selects"][0]["options"] == {"Komfort": 0, "Aus": 1}


def _overlay_base(catalog_mod, points, tags):
    return {
        "schema_version": 1,
        "catalog_version": "test",
        "hc_tags": [31886, 19693, 23756, 11307],
        "points": points,
        "tags": [{"tag": t} for t in tags],
    }


def test_overlay_cannot_rename_or_reenable_packaged_points(catalog_mod, entities_mod):
    """A stored bundle enriches metadata but can never undo v4 naming or the
    disable/diagnostic flags of packaged points."""

    enc = catalog_mod.encode_oa
    tech_id = enc(8960, 100, 1, 256)
    apk_id = enc(8960, 100, 2, 256)
    base = _overlay_base(
        catalog_mod,
        [
            {"id": tech_id, "platform": "sensor", "name": "CprOprHrs1", "name_source": "bundle", "sources": ["reference"], "technical": True, "diagnostic": True, "enabled_default": False},
            {"id": apk_id, "platform": "sensor", "name": "Outdoor temperature", "name_source": "apk_label", "sources": ["apk"]},
        ],
        [100],
    )
    overlay = [
        {"id": tech_id, "platform": "sensor", "name": "Betriebsstunden Verdichter", "name_source": "bundle", "sources": ["bundle_import"], "unit": "h"},
        {"id": apk_id, "platform": "sensor", "name": "Außentemperatur", "name_source": "bundle", "sources": ["bundle_import"]},
    ]
    cat = entities_mod.catalog_with_overlay(base, overlay)

    tech = cat.points_by_id[tech_id]
    # Flags survive; the bundle's metadata (unit) still enriches.
    assert tech["enabled_default"] is False and tech["diagnostic"] is True
    assert tech["name"] == "CprOprHrs1" and tech["unit"] == "h"
    # The packaged APK-first name survives the bundle name.
    assert cat.points_by_id[apk_id]["name"] == "Outdoor temperature"
    assert cat.points_by_id[apk_id]["name_source"] == "apk_label"


def test_overlay_write_binding_triggers_service_rule(catalog_mod, entities_mod):
    """A write binding contributed by a bundle makes the service/one-shot
    disable rule applicable to the merged point."""

    enc = catalog_mod.encode_oa
    oid = enc(8706, 100, 3, 256)
    base = _overlay_base(
        catalog_mod,
        [{"id": oid, "platform": "sensor", "name": "Handabtauung", "name_source": "apk_label", "sources": ["apk"]}],
        [100],
    )
    overlay = [{"id": oid, "platform": "switch", "name": "Handabtauung", "name_source": "bundle", "sources": ["bundle_import"], "write_id": oid, "on_value": 1, "off_value": 0}]
    cat = entities_mod.catalog_with_overlay(base, overlay)
    rec = cat.points_by_id[oid]
    assert rec["platform"] == "switch" and rec["write_id"] == oid
    assert rec["enabled_default"] is False


def test_overlay_new_points_get_v4_flags(catalog_mod, entities_mod):
    enc = catalog_mod.encode_oa
    known = enc(8960, 100, 1, 256)
    tech_new = enc(8960, 200, 1, 256)
    priv_new = enc(8964, 200, 2, 256)
    base = _overlay_base(
        catalog_mod,
        [{"id": known, "platform": "sensor", "name": "Known", "name_source": "apk_label", "sources": ["apk"]}],
        [100],
    )
    overlay = [
        {"id": tech_new, "platform": "sensor", "name": "Th-EngySumAct", "name_source": "bundle", "sources": ["bundle_import"]},
        {"id": priv_new, "platform": "text", "name": "Kunde", "name_source": "bundle", "sources": ["bundle_import"], "write_id": priv_new},
    ]
    cat = entities_mod.catalog_with_overlay(base, overlay)
    tech = cat.points_by_id[tech_new]
    assert tech["technical"] is True and tech["enabled_default"] is False and tech["diagnostic"] is True
    priv = cat.points_by_id[priv_new]
    assert priv["enabled_default"] is False and priv["diagnostic"] is True


def test_overlay_cannot_reintroduce_schedules_or_descriptors(catalog_mod, entities_mod):
    """Spec v4 A3 excludes schedules from the integration entirely: neither
    the schedule object type nor schedule members nor enum descriptors can
    re-enter through a stored bundle overlay."""

    enc = catalog_mod.encode_oa
    known = enc(8960, 100, 1, 256)
    sched_ot = enc(8717, 100, 5, 256)
    sched_mid = enc(8960, 100, 6, 514)
    desc = enc(8960, 100, 7, 4353)
    base = _overlay_base(
        catalog_mod,
        [{"id": known, "platform": "sensor", "name": "Known", "name_source": "apk_label", "sources": ["apk"]}],
        [100],
    )
    overlay = [
        {"id": sched_ot, "platform": "sensor", "name": "Zeitprogramm", "name_source": "bundle", "sources": ["bundle_import"]},
        {"id": sched_mid, "platform": "sensor", "name": "Schaltzeit 1", "name_source": "bundle", "sources": ["bundle_import"]},
        {"id": desc, "platform": "sensor", "name": "States", "name_source": "bundle", "sources": ["bundle_import"]},
    ]
    cat = entities_mod.catalog_with_overlay(base, overlay)
    for oid in (sched_ot, sched_mid, desc):
        assert oid not in cat.points_by_id
    sweep = cat.scan_ids_for_tags({100})
    assert sweep == [known]

    # The bundle-entity extraction path filters them as well.
    ents = {
        "sensors": [
            {"id": sched_ot, "name": "Zeitprogramm"},
            {"id": sched_mid, "name": "Schaltzeit 1"},
            {"id": desc, "name": "States"},
            {"id": known, "name": "Known"},
        ]
    }
    out = entities_mod.overlay_points_from_bundle_entities(ents)
    assert [r["id"] for r in out] == [known]


# --- language-aware naming ---------------------------------------------------


def make_lang_catalog(catalog_mod, points, tags, en_map=None):
    return catalog_mod.DiscoveryCatalog(
        {
            "schema_version": 1,
            "catalog_version": "test",
            "hc_tags": [31886, 19693, 23756, 11307],
            "enum_token_labels": {"en": en_map or {}},
            "points": points,
            "tags": [{"tag": t} for t in tags],
        }
    )


def _mode_point(catalog_mod, sel_id):
    return {
        "id": sel_id,
        "platform": "select",
        "name": "Heating circuit operating program",
        "name_source": "apk_label",
        "name_en": "Heating circuit operating program",
        "name_en_source": "apk_label",
        "name_de": "Betriebswahl Heizkreis",
        "name_de_source": "bundle",
        "sources": ["reference"],
        "write_id": sel_id,
        "options": {"Komfort": 0, "Aus": 1},
        "enum_labels_de": {"comfort": "Komfort", "off": "Aus", "auto": "Automatik"},
        "enum_labels_en": {"comfort": "Comfort", "off": "Off"},
    }


def test_language_aware_names_and_labels(catalog_mod, discovery_mod, entities_mod):
    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 3, 290)
    cat = make_lang_catalog(
        catalog_mod, [_mode_point(catalog_mod, sel_id)], [100], en_map={"auto": "Automatic"}
    )
    scan = make_scan(discovery_mod, {sel_id: 0})
    scan.enum_labels[sel_id] = ["Comfort", "Off", "Auto", "Party"]

    out_de = entities_mod.build_entities(catalog=cat, scan=scan, language="de")
    sel = out_de["selects"][0]
    assert sel["name"] == "Betriebswahl Heizkreis"
    assert sel["name_source"] == "bundle:de"
    # German: the point's own index-joined map; unmapped tokens stay raw.
    assert sel["options"] == {"Komfort": 0, "Aus": 1, "Automatik": 2, "Party": 3}
    # Raw descriptor tokens ship with the entity for offline re-resolution.
    assert sel["enum_tokens"] == ["Comfort", "Off", "Auto", "Party"]

    out_en = entities_mod.build_entities(catalog=cat, scan=scan, language="en")
    sel = out_en["selects"][0]
    assert sel["name"] == "Heating circuit operating program"
    assert sel["name_source"] == "apk_label:en"
    # English: shared APK-resource map only; unmapped tokens stay raw.
    assert sel["options"] == {"Comfort": 0, "Off": 1, "Automatic": 2, "Party": 3}


def test_language_never_changes_identity(catalog_mod, discovery_mod, entities_mod):
    """de/en output differs only in display strings: ids, write ids,
    platforms and numeric option values are byte-identical."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 3, 290)
    cat = make_lang_catalog(catalog_mod, [_mode_point(catalog_mod, sel_id)], [100])
    scan = make_scan(discovery_mod, {sel_id: 0})
    scan.enum_labels[sel_id] = ["Comfort", "Off"]

    out_de = entities_mod.build_entities(catalog=cat, scan=scan, language="de")
    out_en = entities_mod.build_entities(catalog=cat, scan=scan, language="en")
    for key in out_de:
        assert len(out_de[key]) == len(out_en[key])
        for a, b in zip(out_de[key], out_en[key]):
            assert a.get("id") == b.get("id")
            assert a.get("read_id") == b.get("read_id")
            assert a.get("write_id") == b.get("write_id")
            if a.get("options"):
                assert sorted(a["options"].values()) == sorted(b["options"].values())


def test_relocalize_entities_offline(catalog_mod, entities_mod):
    """A language change re-resolves stored discovery entities offline:
    names and enum label spellings change, identity does not; bundle (uuid)
    and non-catalog entities are untouched; idempotent."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 3, 290)
    cat = make_lang_catalog(
        catalog_mod, [_mode_point(catalog_mod, sel_id)], [100], en_map={"auto": "Automatic"}
    )
    stored = {
        "selects": [
            {
                "name": "Heating circuit operating program",
                "read_id": sel_id,
                "write_id": sel_id,
                "options": {"Comfort": 0, "Off": 1, "Automatic": 2},
                "enum_tokens": ["Comfort", "Off", "Auto"],
                "heating_circuit_uid": "tag:31886",
                "heating_circuit_name": "Heating circuit 1",
            },
            {"name": "Bundle select", "uuid": "abc-123", "read_id": sel_id, "write_id": sel_id, "options": {"X": 0}},
            {"name": "Foreign", "read_id": enc(8706, 999, 1, 256), "write_id": enc(8706, 999, 1, 256), "options": {"Y": 0}},
        ],
        "sensors": [],
        "binary_sensors": [],
        "numbers": [],
        "texts": [],
        "switches": [],
    }
    out, changed = entities_mod.relocalize_entities(stored, cat, "de")
    assert changed
    ent = out["selects"][0]
    assert ent["name"] == "Betriebswahl Heizkreis"
    assert ent["options"] == {"Komfort": 0, "Aus": 1, "Automatik": 2}
    assert ent["read_id"] == sel_id and ent["write_id"] == sel_id
    assert ent["enum_tokens"] == ["Comfort", "Off", "Auto"]
    # The integration-provided circuit fallback follows the language; an
    # owner-authored circuit name would not match and never changes.
    assert ent["heating_circuit_name"] == "Heizkreis 1"
    # A bundle-uuid entity on a catalog point is renamed (friendly renames
    # are allowed) but keeps identity and its bundle enum labels untouched.
    bundle_ent = out["selects"][1]
    assert bundle_ent["name"] == "Betriebswahl Heizkreis"
    assert bundle_ent["uuid"] == "abc-123"
    assert bundle_ent["options"] == {"X": 0}
    # A non-catalog entity is untouched entirely.
    assert out["selects"][2] == stored["selects"][2]

    # Idempotent; and switching back restores the English labels.
    again, changed2 = entities_mod.relocalize_entities(out, cat, "de")
    assert not changed2 and again == out
    back, _ = entities_mod.relocalize_entities(out, cat, "en")
    assert back["selects"][0]["options"] == {"Comfort": 0, "Off": 1, "Automatic": 2}
    assert back["selects"][0]["name"] == "Heating circuit operating program"
    assert back["selects"][0]["heating_circuit_name"] == "Heating circuit 1"


def test_relocalize_without_stored_tokens(catalog_mod, entities_mod):
    """Entities created before the language feature shipped raw pump tokens
    as labels and carry no enum_tokens: the labels double as tokens, so a
    German re-resolution still works; a metadata-fallback entity whose
    options were never tokens resolves to no known token and stays put."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 3, 290)
    cat = make_lang_catalog(catalog_mod, [_mode_point(catalog_mod, sel_id)], [100])
    stored = {
        "selects": [
            {"name": "Old", "read_id": sel_id, "write_id": sel_id, "options": {"Comfort": 0, "Off": 1}},
        ],
        "sensors": [
            {"name": "Old vm", "id": sel_id, "value_map": {"0": "Comfort", "1": "Off"}},
        ],
        "binary_sensors": [],
        "numbers": [],
        "texts": [],
        "switches": [],
    }
    out, changed = entities_mod.relocalize_entities(stored, cat, "de")
    assert changed
    assert out["selects"][0]["options"] == {"Komfort": 0, "Aus": 1}
    assert out["sensors"][0]["value_map"] == {"0": "Komfort", "1": "Aus"}

    # German-metadata options (no descriptor at scan time): labels are not
    # tokens, no known-token match, values and labels survive unchanged.
    stored_meta = {
        "selects": [
            {"name": "Meta", "read_id": sel_id, "write_id": sel_id, "options": {"Komfort": 0, "Aus": 1}},
        ],
    }
    out2, _ = entities_mod.relocalize_entities(stored_meta, cat, "en")
    assert out2["selects"][0]["options"] == {"Komfort": 0, "Aus": 1}


def test_localization_collision_falls_back_to_tokens(catalog_mod, discovery_mod, entities_mod):
    """Two DISTINCT tokens whose localized labels collide fall back to their
    raw tokens; every descriptor value stays selectable (review round 3)."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 3, 290)
    point = {
        "id": sel_id,
        "platform": "select",
        "name": "Mode",
        "name_source": "bundle",
        "sources": ["reference"],
        "write_id": sel_id,
        "options": {"A": 0},
        # Both tokens map to the same German label: a real bundle can do
        # this (two controller states shown as one label in the app).
        "enum_labels_de": {"stby1": "Standby", "stby2": "Standby"},
    }
    cat = make_lang_catalog(catalog_mod, [point], [100])
    scan = make_scan(discovery_mod, {sel_id: 0})
    scan.enum_labels[sel_id] = ["Stby1", "Stby2", "Heat"]

    out = entities_mod.build_entities(catalog=cat, scan=scan, language="de")
    opts = out["selects"][0]["options"]
    # No value dropped, colliding labels resolved to the distinct raw tokens.
    assert opts == {"Stby1": 0, "Stby2": 1, "Heat": 2}

    # Relocalization preserves the full value set too (round trip).
    stored = {"selects": [dict(out["selects"][0], read_id=sel_id)]}
    rel, _ = entities_mod.relocalize_entities(stored, cat, "de")
    assert sorted(rel["selects"][0]["options"].values()) == [0, 1, 2]


def test_relocalize_bundle_uuid_entities_rename_only(catalog_mod, entities_mod):
    """Bundle (uuid) entities whose read id is a catalog point get their
    NAME re-resolved per language (friendly renames are allowed); uuid,
    ids, platform shaping and enum labels (no provable tokens) stay
    byte-identical, and no duplicate entity appears (review round 3)."""

    enc = catalog_mod.encode_oa
    sel_id = enc(8706, 100, 3, 290)
    cat = make_lang_catalog(catalog_mod, [_mode_point(catalog_mod, sel_id)], [100])
    bundle_ent = {
        "name": "Betriebswahl Heizkreis",
        "uuid": "bundle-uuid-1",
        "read_id": sel_id,
        "write_id": sel_id,
        # Bundle-authored German labels: NOT pump tokens, never re-derived.
        "options": {"Komfort": 0, "Aus": 1},
        "heating_circuit_uid": "hc-uuid",
        "heating_circuit_name": "Fussboden",
    }
    stored = {"selects": [dict(bundle_ent)]}

    out_en, changed = entities_mod.relocalize_entities(stored, cat, "en")
    assert changed
    ent = out_en["selects"][0]
    assert ent["name"] == "Heating circuit operating program"
    assert ent["uuid"] == "bundle-uuid-1"
    assert ent["read_id"] == sel_id and ent["write_id"] == sel_id
    # Conservative: bundle labels are not tokens — untouched in any language.
    assert ent["options"] == {"Komfort": 0, "Aus": 1}
    # Owner-authored circuit name never matches the fallback pattern.
    assert ent["heating_circuit_name"] == "Fussboden"
    assert len(out_en["selects"]) == 1

    # DE -> EN -> DE round trip restores the German name exactly.
    out_de, _ = entities_mod.relocalize_entities(out_en, cat, "de")
    ent_de = out_de["selects"][0]
    assert ent_de["name"] == "Betriebswahl Heizkreis"
    for k in ("uuid", "read_id", "write_id", "options", "heating_circuit_uid", "heating_circuit_name"):
        assert ent_de[k] == bundle_ent[k], k
