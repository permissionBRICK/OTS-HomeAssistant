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


def test_packaged_catalog_is_bilingual(catalog_mod):
    """The bilingual acceptance target on the shipped asset: every point
    resolves natively in BOTH languages, no enum token is left to a raw
    fallback (explicit reviewed pairs cover even the undocumented numeric
    states), and the only verbatim-symbol exception is the apk_symbol scope
    (29 points, 25 unique code identifiers)."""

    cat = catalog_mod.load_catalog()
    sources = {"apk_label", "bundle", "apk_symbol", "translated"}
    symbol_points = []
    for rec in cat.points:
        for lang in ("de", "en"):
            name, source = catalog_mod.resolve_point_name(rec, lang)
            prov, _, lang_tag = source.rpartition(":")
            assert name.strip(), rec["id"]
            assert lang_tag == lang, (rec["id"], source)
            assert prov in sources, (rec["id"], source)
            if prov == "apk_symbol":
                # The single permitted verbatim exception: points whose only
                # name evidence is the APK code identifier.
                assert rec["name_source"] == "apk_symbol", rec["id"]
                symbol_points.append(rec["id"])
        # Zero raw fallbacks: the builder records any unlabelled descriptor
        # token as a gap, and the shipped asset must have none.
        assert not rec.get("enum_label_gaps"), rec["id"]
    assert len(set(symbol_points)) == 29
    assert len({cat.points_by_id[i]["name"] for i in symbol_points}) == 25

    # The shared maps cover the same tokens in both languages, and every
    # token's provenance is recorded per side.
    shared = cat.enum_token_labels
    assert set(shared["de"]) == set(shared["en"])
    prov_maps = catalog_mod.load_catalog_raw().get("enum_token_label_sources")
    assert set(prov_maps["en"]) == set(shared["en"])
    assert set(prov_maps["de"]) == set(shared["de"])

    # Reviewed ground truths: the bundle's raw-symbol "German" slots are
    # corrected per point, in both languages, and point evidence outranks
    # the generic shared token meaning.
    parity = cat.points_by_id["AiOFwiApAAE="]
    assert catalog_mod.resolve_enum_label(parity, "odd", "de", shared) == "Ungerade"
    assert catalog_mod.resolve_enum_label(parity, "none", "de", shared) == "Keine"
    relay = cat.points_by_id["AiP8SiiJAAE="]
    assert catalog_mod.resolve_enum_label(relay, "Auto", "en", shared) == "Inactive"
    assert catalog_mod.resolve_enum_label(relay, "Auto", "de", shared) == "Inaktiv"
    cloud = cat.points_by_id["MgABAAAAAQA="]
    assert catalog_mod.resolve_enum_label(cloud, "Disabled", "de", shared) == "Deaktiviert"
    assert catalog_mod.resolve_enum_label(cloud, "Disabled", "en", shared) == "Disabled"
    status_hk = cat.points_by_id["CyMogo58AAE="]
    assert catalog_mod.resolve_enum_label(status_hk, "21", "en", shared) == "Unknown state 21"
    assert catalog_mod.resolve_enum_label(status_hk, "21", "de", shared) == "Unbekannter Zustand 21"


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
    # Bilingual pair: each language resolves natively, provenance + language
    # in the source tag; a missing side falls back cross-language, visibly.
    pair = {
        "id": "z",
        "name": "Betriebswahl",
        "name_source": "bundle",
        "name_en": "Operating program",
        "name_en_source": "translated",
        "name_de": "Betriebswahl",
        "name_de_source": "bundle",
    }
    assert catalog_mod.resolve_point_name(pair, "en") == ("Operating program", "translated:en")
    assert catalog_mod.resolve_point_name(pair, "de") == ("Betriebswahl", "bundle:de")

    shared = {"en": {"auto": "Automatic"}, "de": {"auto": "Automatik"}}
    # Chain per language: own map, shared map, then the other language
    # (own, shared), then the raw token.
    assert catalog_mod.resolve_enum_label(rec, "Off", "de", shared) == "Aus"
    assert catalog_mod.resolve_enum_label(rec, "Auto", "de", shared) == "Automatik"
    assert catalog_mod.resolve_enum_label(rec, "Auto", "en", shared) == "Automatic"
    assert catalog_mod.resolve_enum_label_source(rec, "Auto", "de", shared) == ("Automatik", "de")
    # Off has only the point's German label: English falls back to it,
    # tagged as German so the audit can count the cross-language path.
    assert catalog_mod.resolve_enum_label_source(rec, "Off", "en", shared) == ("Aus", "de")
    assert catalog_mod.resolve_enum_label_source(rec, "Party", "en", shared) == ("Party", "token")
    assert catalog_mod.resolve_enum_label(rec, "-", "en", shared) == "-"
