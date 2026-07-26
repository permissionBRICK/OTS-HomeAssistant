"""OA codec + packaged catalog invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE = REPO_ROOT / "apk_files" / "ha_config" / "core.config_entries"


# Live-verified vectors from the reference plant.
VECTORS = [
    # (encoded, object_type, instance_tag, point_index, member_id)
    ("ACJz7Y58AAE=", 8704, 31886, 60787, 256),  # HC1 target flow temperature
    ("BCNL7o58AAE=", 8964, 31886, 61003, 256),  # HC1 circuit name ("Name Heizkreis 1")
    ("BCNL7u1MAAE=", 8964, 19693, 61003, 256),  # HC2 circuit name
    ("CyP7TIwCAAE=", 8971, 652, 19707, 256),   # "#Kuehlen erlaubt" (states-only point)
]


def test_encode_decode_vectors(catalog_mod):
    for encoded, ot, tag, pi, mid in VECTORS:
        assert catalog_mod.encode_oa(ot, tag, pi, mid) == encoded
        oa = catalog_mod.decode_oa(encoded)
        assert (oa.object_type, oa.instance_tag, oa.point_index, oa.member_id) == (ot, tag, pi, mid)


def test_decode_accepts_padded_and_unpadded(catalog_mod):
    # OAs occur both padded and unpadded (e.g. options override keys strip '=').
    for encoded, ot, tag, pi, mid in VECTORS:
        unpadded = encoded.rstrip("=")
        assert unpadded != encoded
        oa = catalog_mod.decode_oa(unpadded)
        assert (oa.object_type, oa.instance_tag, oa.point_index, oa.member_id) == (ot, tag, pi, mid)
    # But not arbitrary re-paddings or garbage.
    import pytest as _pytest

    with _pytest.raises(ValueError):
        catalog_mod.decode_oa("BCNL7o58AAE==")


def test_hc_name_oa_matches_reference_text(catalog_mod):
    # The owner-configured circuit name point, proven on real hardware.
    assert catalog_mod.hc_name_oa(31886) == "BCNL7o58AAE="
    assert catalog_mod.hc_name_oa(23756) == "BCNL7sxcAAE="


def test_encode_rejects_out_of_range(catalog_mod):
    with pytest.raises(ValueError):
        catalog_mod.encode_oa(0x10000, 0, 0, 0)
    with pytest.raises(ValueError):
        catalog_mod.encode_oa(0, -1, 0, 0)


def test_decode_rejects_wrong_length_and_noncanonical(catalog_mod):
    with pytest.raises(ValueError):
        catalog_mod.decode_oa("AAAA")  # 3 bytes
    # 'getExitAnim=' is one of the APK false positives: 8 bytes long but with
    # non-zero padding bits, so it is not a canonical OA spelling.
    with pytest.raises(ValueError):
        catalog_mod.decode_oa("getExitAnim=")
    assert catalog_mod.try_decode_oa("getExitAnim=") is None


def test_derive_member_and_retag(catalog_mod):
    assert catalog_mod.derive_member("ACJz7Y58AAE=", 4353) == catalog_mod.encode_oa(8704, 31886, 60787, 4353)
    assert catalog_mod.retag("ACJz7Y58AAE=", 19693) == "ACJz7e1MAAE="


def test_catalog_canonicalizes_padded_and_unpadded_duplicates(catalog_mod):
    # The same OA spelled padded and unpadded must collapse to ONE point.
    raw = {
        "schema_version": 1,
        "catalog_version": "test",
        "hc_tags": [],
        "points": [
            {"id": "BCNL7o58AAE=", "platform": "sensor", "name": "A", "sources": ["apk"]},
            {"id": "BCNL7o58AAE", "platform": "text", "name": "B", "sources": ["reference"], "write_id": "BCNL7o58AAE"},
        ],
        "tags": [{"tag": 31886}],
    }
    cat = catalog_mod.DiscoveryCatalog(raw)
    assert len(cat.points) == 1
    rec = cat.points_by_id["BCNL7o58AAE="]
    assert rec["name"] == "B"
    assert rec["write_id"] == "BCNL7o58AAE="  # canonicalized too
    assert catalog_mod.canonical_oa("BCNL7o58AAE") == "BCNL7o58AAE="


def test_schedule_members(catalog_mod):
    for mid in range(514, 526):
        assert catalog_mod.is_schedule_member(mid)
    assert not catalog_mod.is_schedule_member(256)
    assert not catalog_mod.is_schedule_member(526)


def test_packaged_catalog_loads_and_is_canonical(catalog_mod):
    cat = catalog_mod.load_catalog()
    assert cat.catalog_version
    assert list(cat.hc_tags) == [31886, 19693, 23756, 11307]
    # v4 membership: only named, non-schedule, non-descriptor points remain.
    assert 500 <= len(cat.points) <= 700
    declared_tags = {t["tag"] for t in cat.tags}
    for rec in cat.points:
        oa = catalog_mod.decode_oa(rec["id"])  # raises on non-canonical
        assert catalog_mod.encode_oa(*oa) == rec["id"]
        assert oa.instance_tag in declared_tags
        # Schedules (object_type 8717 / members 514..525) and enum
        # descriptors (member 4353) are excluded from the catalog entirely.
        assert oa.object_type != 8717
        assert not catalog_mod.is_schedule_member(oa.member_id)
        assert oa.member_id != catalog_mod.DESCRIPTOR_MEMBER_ID
        # Every point has a name and its recorded source.
        assert str(rec.get("name") or "").strip()
        assert rec.get("name_source") in {"apk_label", "bundle", "apk_symbol"}
    # The five APK false positives must not be in the catalog.
    for fp in ("intEncoding=", "getExitAnim=", "surfaceTint=", "stopTimeout=", "onSecondary="):
        assert fp not in cat.points_by_id


def test_packaged_catalog_sweep_shape(catalog_mod):
    cat = catalog_mod.load_catalog()
    sweep = cat.scan_ids_for_tags({t["tag"] for t in cat.tags})
    # The full sweep covers every catalog point exactly once (spec v4 D).
    assert len(sweep) == len(set(sweep)) == len(cat.points)


@pytest.mark.skipif(not REFERENCE.exists(), reason="reference plant data not present")
def test_packaged_catalog_covers_reference_plant(catalog_mod):
    cat = catalog_mod.load_catalog()
    data = json.loads(REFERENCE.read_text(encoding="utf-8"))
    entry = next(e for e in data["data"]["entries"] if e["domain"] == "ochsner_local_ots")
    ctrl = entry["data"]["controllers"][0]

    ref_ids = set()
    for key in ("sensors", "binary_sensors", "numbers", "selects", "texts", "switches"):
        for ent in ctrl.get(key, []) or []:
            for f in ("id", "read_id", "write_id"):
                if ent.get(f):
                    ref_ids.add(str(ent[f]))
    assert len(ref_ids) == 392

    catalog_ids = set(cat.points_by_id)
    catalog_write_ids = {p.get("write_id") for p in cat.points if p.get("write_id")}
    missing = ref_ids - catalog_ids - catalog_write_ids
    assert not missing, f"reference ids missing from catalog: {sorted(missing)}"


def test_language_helpers(catalog_mod):
    n = catalog_mod.normalize_language
    assert n("DE") == n("de-AT") == n("German") == "de"
    assert n("en") == n("") == n(None) == n("fr") == "en"
    e = catalog_mod.explicit_language
    assert e("de") == "de" and e("EN") == "en"
    assert e("") is None and e(None) is None and e("auto") is None

    t = catalog_mod.normalize_enum_token
    assert t("TiMinOff") == t("ti_min_off") == "timinoff"
    assert t("-12") == "-12" and t("12") == "12" and t("-12") != t("12")
    assert t("-") == "" and t(" ") == ""


def test_resolve_point_name_and_enum_label(catalog_mod):
    rec = {
        "id": "x",
        "name": "Program",
        "name_source": "apk_label",
        "name_de": "Programm",
        "name_de_source": "bundle",
        "enum_labels_de": {"off": "Aus"},
    }
    assert catalog_mod.resolve_point_name(rec, "de") == ("Programm", "bundle:de")
    assert catalog_mod.resolve_point_name(rec, "en") == ("Program", "apk_label:en")
    assert catalog_mod.resolve_point_name({"id": "y", "name": "N", "name_source": "bundle"}, "de") == ("N", "bundle:en")

    shared = {"en": {"auto": "Automatic"}}
    # German: point-specific map only, then the raw token — never the shared map.
    assert catalog_mod.resolve_enum_label(rec, "Off", "de", shared) == "Aus"
    assert catalog_mod.resolve_enum_label(rec, "Auto", "de", shared) == "Auto"
    assert catalog_mod.resolve_enum_label(rec, "Auto", "en", shared) == "Automatic"
    assert catalog_mod.resolve_enum_label(rec, "Off", "en", shared) == "Off"
    assert catalog_mod.resolve_enum_label(rec, "-", "en", shared) == "-"
