"""Catalog builder: determinism, provenance, ground-truth invariants.

These tests need the research inputs under apk_files/ (not shipped in git);
they are skipped when absent.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MODEL = REPO_ROOT / "apk_files" / "reports" / "catalog_model.json"
NAMES = REPO_ROOT / "apk_files" / "reports" / "catalog_names.json"
REFERENCE = REPO_ROOT / "apk_files" / "ha_config" / "core.config_entries"
LIVE = REPO_ROOT / "apk_files" / "live_ids.json"
STRINGS = REPO_ROOT / "apk_files" / "jadx_out" / "resources" / "res" / "values" / "strings.xml"

pytestmark = pytest.mark.skipif(
    not (MODEL.exists() and NAMES.exists() and REFERENCE.exists() and LIVE.exists() and STRINGS.exists()),
    reason="APK research inputs not present",
)


@pytest.fixture(scope="module")
def builder(lib):
    spec = importlib.util.spec_from_file_location(
        "build_discovery_catalog", REPO_ROOT / "tools" / "build_discovery_catalog.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_discovery_catalog"] = mod
    spec.loader.exec_module(mod)
    return mod


def build(builder):
    return builder.build_catalog(
        model_path=MODEL,
        names_path=NAMES,
        reference_path=REFERENCE,
        live_path=LIVE,
        strings_path=STRINGS,
    )


def test_builder_is_deterministic(builder):
    a = build(builder)
    b = build(builder)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_builder_counts_and_provenance(builder):
    cat = build(builder)
    stats = cat["stats"]
    ids = {p["id"] for p in cat["points"]}
    for fp in ("intEncoding=", "getExitAnim=", "surfaceTint=", "stopTimeout=", "onSecondary="):
        assert fp not in ids
    # v4 membership on the current inputs: 672 schedule points and 150
    # nameless points are excluded entirely; what remains is named.
    assert stats["excluded_schedule"] == 672
    assert stats["excluded_unnamed"] == 149
    assert stats["points_total"] == stats["enabled"] + stats["disabled"]
    assert 500 <= stats["points_total"] <= 700
    # Reference seed: all 387 primary/read ids survive membership (backward
    # compatibility depends on it), plus confirmed-circuit propagation.
    assert stats["reference_points"] >= 387
    # Writability only ever comes from reference/bundle evidence, never APK-only.
    for p in cat["points"]:
        if p.get("write_id"):
            assert "reference" in p["sources"] or "hc_template" in p["sources"]


def test_technical_names_disabled_but_included(builder):
    cat = build(builder)
    fn = builder.is_technical_name
    # The documented test: no whitespace AND (case transition OR 2+ capitals).
    for name in ("CprOprHrs1", "HPMEmgyModConf", "Th-EngySumAct", "DHCP"):
        assert fn(name), name
    for name in ("Betriebswahl Heizkreis", "Aussentemperatur", "Sollwert", "Anlagentyp"):
        assert not fn(name), name
    tech = [p for p in cat["points"] if p.get("technical")]
    assert tech, "expected technical-named points to be included"
    for p in tech:
        assert p["enabled_default"] is False, p["name"]


def test_single_circuit_points_are_not_propagated(builder):
    """"Sollwert Handbetrieb" uses a different point_index per circuit
    (30743/51672/42653): its addresses cannot be derived, so it must exist
    exactly where the reference proves it — never on a guessed 4th address."""

    cat = build(builder)
    hits = [p for p in cat["points"] if p["name"] == "Sollwert Handbetrieb"]
    assert len(hits) == 3
    for p in hits:
        assert "hc_template" not in p["sources"]


def test_confirmed_circuit_points_exist_on_all_four_tags(builder, catalog_mod):
    cat = build(builder)
    hc_tags = cat["hc_tags"]
    ids = {p["id"] for p in cat["points"]}
    propagated = [p for p in cat["points"] if "hc_template" in p["sources"]]
    assert propagated, "expected confirmed circuit triples to be completed"
    for p in propagated:
        oa = catalog_mod.decode_oa(p["id"])
        # The full quad must exist after propagation.
        for tag in hc_tags:
            assert catalog_mod.encode_oa(oa.object_type, tag, oa.point_index, oa.member_id) in ids


def test_naming_priority_and_source_recorded(builder):
    cat = build(builder)
    for p in cat["points"]:
        assert p.get("name_source") in {"apk_label", "bundle", "apk_symbol"}
    by_source = cat["stats"]["by_name_source"]
    # Pump-first spec v4: the APK label outranks the bundle name, so a large
    # share of reference-known points is APK-named now.
    assert by_source["apk_label"] > by_source["bundle"] > by_source["apk_symbol"]
    # Spot check: the reference-known "Kunde" point is APK-labelled "Customer".
    names = {p["name"] for p in cat["points"]}
    assert "Customer" in names and "Kunde" not in names


def test_ambiguous_apk_labels_still_beat_bundle_name(builder):
    """Every usable APK label tier precedes the old bundle name; several
    distinct labels resolve deterministically to the most descriptive one
    (longest, then lexicographic)."""

    cat = build(builder)
    p = next(p for p in cat["points"] if p["id"] == "ASMhEo58AAE=")
    # APK labels "Standard heating" / "Target room temperature standard
    # heating"; the bundle name "Raumsollwert Normal Heizen" must lose.
    assert p["name"] == "Target room temperature standard heating"
    assert p["name_source"] == "apk_label"


def test_placeholder_labels_are_not_names(builder):
    """Printf-template APK labels ("Name of heating circuit %1$s") are
    ignored; the point falls back to its bundle name."""

    cat = build(builder)
    assert not any("%" in p["name"] for p in cat["points"])
    hc_names = sorted(p["name"] for p in cat["points"] if p["platform"] == "text" and p["name"].startswith("Name Heizkreis"))
    assert hc_names == ["Name Heizkreis 1", "Name Heizkreis 2", "Name Heizkreis 3", "Name Heizkreis 4"]
    # The HC4 instance is APK-known and enriched from the confirmed circuit
    # template: it carries the donor's writable text shaping.
    p4 = next(p for p in cat["points"] if p["name"] == "Name Heizkreis 4")
    assert p4["platform"] == "text" and p4.get("write_id")
    assert "hc_template" in p4["sources"]


def test_service_and_one_shot_controls_writable_but_disabled(builder):
    """One-shot/service/commissioning controls (screed drying, program
    start, manual defrost, error acknowledge, comms parameters, cloud
    connection) stay writable entities but enabled_default=False."""

    cat = build(builder)
    expected = (
        "Program start",
        "Operating program, screed drying program",
        "Handabtauung",
        "Acknowledge error",
        "Unlock system",
        "Stop bit",
        "Baud rate",
        "Parity",
        "Heat pump address",
        "Data connection to cloud",
    )
    by_name: dict = {}
    for p in cat["points"]:
        by_name.setdefault(p["name"], []).append(p)
    for name in expected:
        assert name in by_name, f"expected service control {name} in catalog"
        for p in by_name[name]:
            assert p.get("write_id"), name
            assert p["platform"] in {"number", "select", "switch"}, name
            assert p.get("enabled_default") is False, name


def test_shipped_catalog_matches_builder_output(builder, catalog_mod):
    built = build(builder)
    shipped = json.loads(catalog_mod.catalog_path().read_text(encoding="utf-8"))
    assert shipped["catalog_version"] == built["catalog_version"], (
        "packaged discovery_catalog.json is stale; re-run tools/build_discovery_catalog.py"
    )


def test_destructive_points_created_writable_but_disabled(builder):
    """Owner decision: destructive/one-shot/service points are STILL writable
    entities, just disabled by default — never dropped, never read-only."""

    cat = build(builder)
    hits = [p for p in cat["points"] if "relaistest" in p["name"].lower() or p["name"] in ("Geräte-Reset", "Appliance reset")]
    assert hits
    for p in hits:
        if p.get("write_id"):
            assert p["enabled_default"] is False, p["name"]
            assert p["platform"] in {"number", "select", "switch"}, p["name"]


def test_privacy_points_are_disabled_diagnostics(builder):
    cat = build(builder)
    by_name = {}
    for p in cat["points"]:
        by_name.setdefault(p["name"], p)
    # v4 names are APK-first, so the privacy points carry their English labels.
    for name in (
        "Customer",
        "IP address",
        "Gateway",
        "MAC",
        "Service contact telephone number",
        "Service contact email",
        "Signature part 1",
        "Signature part 2",
        "Signature part 3",
    ):
        p = by_name.get(name)
        assert p is not None, f"expected privacy point {name} in catalog"
        assert p.get("diagnostic") is True
        assert p.get("enabled_default") is False


def test_shared_write_bindings_are_never_retagged(builder):
    """Regression (review round 3): for the confirmed read triples
    (8970,54840,256) and (8970,4149,256) all proven circuits write through
    the SAME binding ACPkn458AAE= — the derived HC4 instances must preserve
    that common binding, never an invented retag (ACPknyssAAE=)."""

    cat = build(builder)
    pts = {p["id"]: p for p in cat["points"]}
    for hc4_id in ("CiM41issAAE=", "CiM1ECssAAE="):
        p = pts[hc4_id]
        assert p.get("write_id") == "ACPkn458AAE=", p
        assert "hc_template" in p["sources"]
    assert not any(p.get("write_id") == "ACPknyssAAE=" for p in cat["points"])


def test_service_evidence_in_alternate_names_disables(builder):
    """Regression (review round 3): the APK-first display name "Program
    selection" must not mask the service evidence in its bundle name "Modus
    Austrocknungsprogramm" — all four instances (incl. the enriched HC4 one)
    stay writable but enabled_default=False. Ordinary screed-drying
    PARAMETERS ("Start temperature") stay enabled."""

    cat = build(builder)
    sel = [p for p in cat["points"] if p["name"] == "Program selection"]
    assert len(sel) == 4
    for p in sel:
        assert p.get("write_id"), p["id"]
        assert p["platform"] in {"select", "number", "switch"}, p["id"]
        assert p.get("enabled_default") is False, p["id"]
    start = [p for p in cat["points"] if p["name"] == "Start temperature"]
    assert start and all(p.get("enabled_default") is not False for p in start)


def test_language_aware_names_shipped(builder):
    """Every point ships the explicit bilingual pair with per-side
    provenance; each language keeps its native evidence verbatim."""

    cat = build(builder)
    pts = {p["id"]: p for p in cat["points"]}
    p = pts["AiIQvY58IgE="]  # Betriebswahl Heizkreis (ground-truth point)
    assert p["name"] == "Heating circuit operating program"
    assert p["name_source"] == "apk_label"
    assert p["name_en"] == "Heating circuit operating program"
    assert p["name_en_source"] == "apk_label"
    assert p["name_de"] == "Betriebswahl Heizkreis"
    assert p["name_de_source"] == "bundle"
    fn = builder.is_technical_name
    sources = {"apk_label", "bundle", "apk_symbol", "translated"}
    for q in cat["points"]:
        assert q.get("name_en") and q.get("name_de"), q["id"]
        assert q["name_en_source"] in sources and q["name_de_source"] in sources, q["id"]
        # Native PROSE evidence is never overwritten by a translation; a
        # technical machine name gets its reviewed pair on both sides.
        if q["name_source"] == "apk_label" and not fn(q["name"]):
            assert q["name_en"] == q["name"], q["id"]
        if q["name_source"] == "bundle" and not fn(q["name"]):
            assert q["name_de"] == q["name"], q["id"]
    assert cat["stats"]["name_de_points"] == cat["stats"]["points_total"]
    # The technical bundle symbols carry reviewed pairs now, e.g. the
    # DHW emergency-mode select and the compressor-hours year buckets.
    p = pts["AiN5rlsWAAE="]  # DHWEmgyMod
    assert p["name_en"] == "Operating program, DHW emergency mode"
    assert p["name_de"] == "Betriebswahl Notbetrieb Warmwasser"
    assert p["name_en_source"] == p["name_de_source"] == "translated"
    p = pts["AyOgSyiJAAE="]  # CprOprHrs1
    assert p["name_en"] == "Compressor operating hours, previous year"
    assert p["name_de"] == "Betriebsstunden Verdichter Vorjahr"
    # Verbatim both sides is reserved for apk_symbol evidence.
    for q in cat["points"]:
        if q["name_en_source"] == "apk_symbol" or q["name_de_source"] == "apk_symbol":
            assert q["name_source"] == "apk_symbol", q["id"]


def test_enum_de_index_join_ground_truth(builder):
    """The verified index join: Betriebswahl Heizkreis tokens map to the
    bundle's German labels by list index; pump-only states (Eco, Party,
    Holiday) are covered by the shared reviewed maps, and the per-point join
    still wins over them."""

    cat = build(builder)
    pts = {p["id"]: p for p in cat["points"]}
    p = pts["AiIQvY58IgE="]
    assert p["enum_labels_de"] == {
        "comfort": "Komfort",
        "off": "Aus",
        "red": "Reduziert",
        "norm": "Normalbetrieb",
        "heatman": "Handbetrieb Heizen",
        "coolman": "Handbetrieb Kühlen",
    }
    # Eco/Party/Holiday have no bundle evidence: the shared maps carry them
    # in both languages now, so the point has no German gaps left.
    assert "de" not in (p.get("enum_label_gaps") or {})
    de_shared = cat["enum_token_labels"]["de"]
    assert de_shared["party"] == "Partybetrieb"
    assert de_shared["eco"] == "Eco" and de_shared["holiday"] == "Urlaub"
    # Propagation carries the maps to derived circuit instances (same
    # template, same tokens) — every circuit's operating-mode point has them.
    variants = [q for q in cat["points"] if q.get("name_de") == "Betriebswahl Heizkreis"]
    assert len(variants) >= 3
    assert all(q.get("enum_labels_de") == p["enum_labels_de"] for q in variants)


def test_shared_token_label_maps(builder):
    """The shared EN map comes from the APK IFType suffixes (unambiguous
    ones). Ambiguous suffixes ("off" is 'Off' for some types, 'Operating
    program switched off' for others) are not forced and placeholder tokens
    ('-') never become keys. The shared DE map holds only reviewed
    translations of those shared EN labels — point-specific German bundle
    evidence still never travels between points (review round 1)."""

    cat = build(builder)
    assert set(cat["enum_token_labels"]) == {"en", "de"}
    de = cat["enum_token_labels"]["de"]
    assert all(k in cat["enum_token_labels"]["en"] for k in de)
    en = cat["enum_token_labels"]["en"]
    assert en["auto"] == "Automatic"
    assert en["timinoff"] == "Starting procedure"  # ti_min_off, faithful to the APK
    assert "off" not in en
    assert "" not in en
    assert cat["stats"]["en_suffixes_ambiguous"] > 0


def test_signed_numeric_tokens_never_collide(builder, catalog_mod):
    """Regression (review round 1): the timezone list carries -12..-1 and
    1..12; a sign-blind normalization collapsed them and shifted UTC labels
    between numeric values. All 25 indices must keep distinct keys and map
    to their exact reference labels."""

    norm = catalog_mod.normalize_enum_token
    assert norm("-12") != norm("12")
    assert norm("+3") != norm("3")
    assert norm("-") == ""

    cat = build(builder)
    p = next(p for p in cat["points"] if p["id"] == "AiNSjtVVAAE=")  # Zeitzone
    labels = p["enum_labels_de"]
    tokens = [str(n) for n in range(-12, 13)]  # descriptor order: -12..-1,0,1..12
    assert len(labels) == 25
    ref = {str(v): lab for lab, v in p["options"].items()}
    for idx, tok in enumerate(tokens):
        assert labels[norm(tok)] == ref[str(idx)], tok


def test_reference_select_de_stats(builder):
    """The reference-plant result the owner asked to see: of the distinct
    reference select records, how many map fully to German."""

    cat = build(builder)
    rs = cat["stats"]["reference_selects"]
    assert rs["total"] == 39
    assert rs["with_descriptor"] == 38
    assert rs["de_mapped"] == 37
    # 33 map fully from bundle evidence alone; the shared reviewed
    # translations close the remaining four (Eco/Party/Holiday etc.).
    assert rs["de_fully_mapped"] == 37
