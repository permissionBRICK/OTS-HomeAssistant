"""The acceptance diff logic must surface every mismatch class instead of
collapsing them away, and the preserved unique-id rule must be collision-free
for merged (bundle + discovery) entity sets."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE = REPO_ROOT / "apk_files" / "ha_config" / "core.config_entries"


@pytest.fixture(scope="module")
def checker(lib):
    spec = importlib.util.spec_from_file_location(
        "check_local_discovery", REPO_ROOT / "tools" / "check_local_discovery.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_local_discovery"] = mod
    spec.loader.exec_module(mod)
    return mod


REF_CTRL = {
    "sensors": [
        {"id": "S1", "name": "Temp A", "uuid": "uuid-s1", "unit": "°C"},
        {"id": "S2", "name": "Enum B", "uuid": "uuid-s2", "value_map": {"0": "Off", "1": "On"}},
        # Sensor twin of the switch below (shares the read id).
        {"id": "SW1", "name": "Boost", "uuid": "uuid-s3"},
    ],
    "binary_sensors": [],
    "numbers": [{"read_id": "N1", "write_id": "N1w", "name": "Setpoint", "uuid": "uuid-n1", "unit": "K"}],
    "selects": [],
    "texts": [],
    "switches": [{"read_id": "SW1", "write_id": "SW1", "name": "Boost", "uuid": "uuid-sw1", "on_value": 1, "off_value": 0}],
}


def perfect_discovery():
    return {
        "sensors": [
            {"id": "S1", "name": "Temp A", "unit": "°C"},
            {"id": "S2", "name": "Enum B", "value_map": {"0": "Off", "1": "On"}},
        ],
        "binary_sensors": [],
        "numbers": [{"read_id": "N1", "write_id": "N1w", "name": "Setpoint", "unit": "K"}],
        "selects": [],
        "texts": [],
        "switches": [{"read_id": "SW1", "write_id": "SW1", "name": "Boost", "on_value": 1, "off_value": 0}],
    }


VALUES = {"S1": 21.0, "S2": 1.0, "N1": 3.0, "SW1": 0.0}


def test_perfect_match_passes_gate(checker):
    report = checker.diff_report(REF_CTRL, perfect_discovery(), VALUES, host="h")
    assert report["gate_passed"], report["gate_failures"]
    # The sensor twin of the switch is covered, not hidden and not a failure.
    assert report["coverage"]["definitions_covered_by_other_platform"] == 1
    assert report["diff"]["covered_by_other_platform"][0]["id"] == "SW1"
    # Write-only ids are reported separately, never as discovered.
    assert report["reference"]["write_only_ids"] == ["N1w"]


def test_each_mismatch_class_is_detected(checker):
    # Missing id.
    ents = perfect_discovery()
    ents["sensors"] = [e for e in ents["sensors"] if e["id"] != "S1"]
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["missing"] == 1

    # Name mismatch.
    ents = perfect_discovery()
    ents["sensors"][0]["name"] = "Renamed"
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["name_mismatch"] == 1

    # Enum mismatch.
    ents = perfect_discovery()
    ents["sensors"][1]["value_map"] = {"0": "Aus", "1": "On"}
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["enum_mismatch"] == 1

    # Unit mismatch.
    ents = perfect_discovery()
    del ents["numbers"][0]["unit"]
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["unit_mismatch"] == 1

    # Platform mismatch: switch demoted to a plain sensor must NOT be
    # collapsed into a "covered" pair.
    ents = perfect_discovery()
    ents["switches"] = []
    ents["sensors"].append({"id": "SW1", "name": "Boost"})
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["platform_mismatch"] >= 1

    # Grouping mismatch.
    ents = perfect_discovery()
    ents["sensors"][0]["heating_circuit_uid"] = "tag:31886"
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["grouping_mismatch"] == 1

    # Current value missing for a discovered id.
    r = checker.diff_report(REF_CTRL, perfect_discovery(), {k: v for k, v in VALUES.items() if k != "S1"}, host="h")
    assert not r["gate_passed"] and r["gate_failures"]["value_missing"] == 1


def test_uuid_on_discovery_entity_is_a_gate_violation(checker):
    # Discovery entities must never carry a uuid: that would silently switch
    # their identity scheme.
    ents = perfect_discovery()
    ents["sensors"][0]["uuid"] = "sneaky-uuid"
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert not r["gate_passed"]
    assert r["gate_failures"]["unique_id_rule_violations"] == 1


def test_grouping_gate_compares_circuit_names(checker):
    ref = {k: [dict(e) for e in v] for k, v in REF_CTRL.items()}
    ref["sensors"][0]["heating_circuit_uid"] = "uuid-hc1"
    ref["sensors"][0]["heating_circuit_name"] = "Fußboden"
    ents = perfect_discovery()
    ents["sensors"][0]["heating_circuit_uid"] = "tag:31886"
    ents["sensors"][0]["heating_circuit_name"] = "Fußboden"
    r = checker.diff_report(ref, ents, VALUES, host="h")
    assert r["gate_failures"]["grouping_mismatch"] == 0

    ents["sensors"][0]["heating_circuit_name"] = "Radiatoren"
    r = checker.diff_report(ref, ents, VALUES, host="h")
    assert r["gate_failures"]["grouping_mismatch"] == 1


def test_unique_id_rule_and_collisions(checker):
    # Bundle entity keeps its uuid; discovery entity uses the host scheme.
    assert checker.unique_id_for("h", "sensor", {"id": "AB==", "uuid": "u-1"}) == "u-1"
    assert checker.unique_id_for("h", "sensor", {"id": "AB=="}) == "h:sensor:AB"

    # Two discovered entities on the same platform+id collide (must be flagged).
    ents = perfect_discovery()
    ents["sensors"].append({"id": "S1", "name": "Twin"})
    r = checker.diff_report(REF_CTRL, ents, VALUES, host="h")
    assert r["gate_failures"]["unique_id_collisions"] == 1


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference plant data not present")
def test_reference_merge_has_no_identity_collisions(checker, catalog_mod, discovery_mod, entities_mod):
    """Merging discovery output into the real reference entry must keep every
    existing unique_id untouched and introduce no collisions (no "_2" churn)."""

    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    entry = next(e for e in data["data"]["entries"] if e["domain"] == "ochsner_local_ots")
    ctrl = entry["data"]["controllers"][0]
    host = str(ctrl["host"])

    # Simulate a full scan: every catalog point readable.
    cat = catalog_mod.load_catalog()
    scan = discovery_mod.DiscoveryScanResult(catalog_version=cat.catalog_version)
    for p in cat.points:
        scan.values[p["id"]] = 1.0
    discovered = entities_mod.build_entities(catalog=cat, scan=scan)

    merged, _added = entities_mod.merge_discovered_entities(ctrl, discovered)

    # HA unique_ids are scoped per platform domain: check per platform.
    seen: dict[tuple, str] = {}
    platform_by_key = {
        "sensors": "sensor",
        "binary_sensors": "binary_sensor",
        "numbers": "number",
        "selects": "select",
        "texts": "text",
        "switches": "switch",
    }
    before_ids = set()
    for key, platform in platform_by_key.items():
        for ent in ctrl.get(key, []) or []:
            before_ids.add((platform, checker.unique_id_for(host, platform, ent)))
    for key, platform in platform_by_key.items():
        for ent in merged.get(key, []) or []:
            uid = (platform, checker.unique_id_for(host, platform, ent))
            assert uid not in seen, f"unique_id collision: {uid}"
            seen[uid] = key
    # Every pre-existing unique_id survived unchanged.
    assert before_ids <= set(seen)

    # Explicitly: the legacy "twin" definitions (read ids exposed through a
    # second platform, e.g. the binary_sensor twin of a switch — the current
    # bundle generator no longer produces these, and a fresh discovery entry
    # consolidates them) SURVIVE an upgrade/merge verbatim with their UUIDs.
    rid_platforms: dict[str, set] = {}
    for key, platform in platform_by_key.items():
        for ent in ctrl.get(key, []) or []:
            rid = str(ent.get("id") or ent.get("read_id") or "")
            rid_platforms.setdefault(rid, set()).add(platform)
    twin_rids = {rid for rid, ps in rid_platforms.items() if len(ps) > 1}
    assert twin_rids, "reference entry should contain legacy twin definitions"
    for key, platform in platform_by_key.items():
        originals = [e for e in ctrl.get(key, []) or [] if str(e.get("id") or e.get("read_id") or "") in twin_rids]
        merged_list = merged.get(key, []) or []
        for orig in originals:
            assert orig in merged_list, f"legacy twin lost: {orig.get('name')} ({key})"


def test_scan_counts_skipped_unreadable_covers_absent_modules(checker, catalog_mod, discovery_mod):
    """skipped_unreadable = every catalog point scanned but not readable,
    including points of fully absent modules."""

    enc = catalog_mod.encode_oa
    a, b, c = enc(8960, 100, 1, 256), enc(8960, 100, 2, 256), enc(8960, 200, 1, 256)
    cat = catalog_mod.DiscoveryCatalog(
        {
            "schema_version": 1,
            "catalog_version": "t",
            "hc_tags": [],
            "points": [
                {"id": a, "platform": "sensor", "name": "A", "sources": ["apk"]},
                {"id": b, "platform": "sensor", "name": "B", "sources": ["apk"]},
                # Point on a fully absent module tag: must still be counted.
                {"id": c, "platform": "sensor", "name": "C", "sources": ["apk"]},
            ],
            "tags": [{"tag": 100}, {"tag": 200}],
        }
    )
    scan = discovery_mod.DiscoveryScanResult(catalog_version="t")
    scan.values[a] = 1.0
    scan.swept_ids = 3
    counts = checker.scan_counts(cat, scan)
    assert counts["skipped_unreadable"] == 2
    assert counts["readable_ids"] == 1
    assert counts["swept_ids"] == 3
