#!/usr/bin/env python3
"""Acceptance gate: run the accountless local discovery scan against a real
controller and diff the result against the reference plant's ground truth
(apk_files/ha_config/core.config_entries).

Strictly read-only (FN=Read only). Usage:

    python3 tools/check_local_discovery.py [--host 127.0.0.1] [--port 8080]
        [--report apk_files/reports/discovery_acceptance.json]

Exit codes: 0 = scan completed AND the acceptance gate passed (no required
mismatches); 2 = gate failed (report shows why); other = crash.

Per reference ENTITY DEFINITION (404 of them; several share one read id) the
diff compares presence, platform, exact name, enum options/value map, unit,
heating-circuit grouping and live current-value presence. Reference read ids
covered by a higher-priority platform on the same read id (e.g. the
binary_sensor twin of a switch — today's bundle generator emits only the
switch) are reported separately as "covered_by_other_platform" and do not
fail the gate. Write-only ids are reported separately and are never counted
as discovered.

The diff runs PER LANGUAGE (--language de|en|both, default both) against one
shared read-only scan: entity NAMES are checked against the catalog's
language-aware choice (German prefers the reference-bundle name, English
the APK label) and enum OPTIONS against the state list read live from the
controller's member-4353 descriptor with each token's label resolved from
the shipped map for that language; heating-circuit names against the
controller's own circuit names. Where those deliberately differ from the
old reference-bundle labels, the differences are reported informationally
("renamed_from_reference", "enum_relabelled_from_reference",
"circuit_renamed_from_reference") and do not fail the gate — a discovered
value that matches neither is still a hard failure. The language-aware
target: with de those informational counts drop to near zero (German users
keep their labels); with en they stay high, which is correct.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "custom_components" / "ochsner_local_ots"

# Same priority the catalog builder / bundle generator dedupe uses: for a read
# id exposed by several reference entity definitions, discovery emits exactly
# one entity of the highest-priority platform.
PLATFORM_PRIORITY = ["switch", "select", "number", "text", "binary_sensor", "sensor"]

REF_PLATFORM_KEYS = (
    ("sensor", "sensors"),
    ("binary_sensor", "binary_sensors"),
    ("number", "numbers"),
    ("select", "selects"),
    ("text", "texts"),
    ("switch", "switches"),
)


def load_integration_modules() -> types.ModuleType:
    """Load the integration's HA-free modules without importing the package
    __init__ (which needs Home Assistant)."""

    pkg = sys.modules.get("ots_local_lib")
    if pkg is None:
        pkg = types.ModuleType("ots_local_lib")
        pkg.__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
        sys.modules["ots_local_lib"] = pkg

    for name in ("const", "catalog", "api", "discovery", "discovery_entities"):
        full = f"ots_local_lib.{name}"
        if full in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(full, PKG_DIR / f"{name}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full] = mod
        spec.loader.exec_module(mod)
    return pkg


def reference_controller() -> Dict[str, Any]:
    path = REPO_ROOT / "apk_files" / "ha_config" / "core.config_entries"
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = next(e for e in data["data"]["entries"] if e["domain"] == "ochsner_local_ots")
    return entry["data"]["controllers"][0]


def norm_map(opts: Any) -> Any:
    if not isinstance(opts, dict):
        return None
    return {str(k): str(v) for k, v in sorted(opts.items(), key=lambda kv: str(kv[0]))}


def unique_id_for(host: str, platform: str, ent: Dict[str, Any]) -> str:
    """The preserved unique-id rule: bundle uuid if present, else host scheme."""
    uuid = str(ent.get("uuid") or "").strip()
    if uuid:
        return uuid
    rid = str(ent.get("id") or ent.get("read_id") or "")
    return f"{host}:{platform}:{rid}".replace("=", "")


def scan_counts(catalog: Any, scan: Any) -> Dict[str, int]:
    """Scan summary. ``skipped_unreadable`` counts EVERY catalog point that
    was scanned but did not answer under "values" (readable -> entity,
    unreadable -> skipped), regardless of module presence."""

    readable = sum(1 for p in catalog.points if str(p["id"]) in scan.values)
    return {
        "swept_ids": scan.swept_ids,
        "readable_ids": len(scan.values),
        "enum_descriptors_read": len(scan.enum_labels),
        "skipped_unreadable": len(catalog.points) - readable,
    }


def bilingual_audit(catalog: Any, scan: Any, language: str) -> Dict[str, Any]:
    """Per-language audit of the bilingual catalog target: every point name
    and every live enum token must resolve in the requested language itself.

    Classes: names — native / translated / still_symbol (verbatim apk_symbol
    code identifiers, the ONLY permitted exceptions) / cross_language
    (resolved from the other language's evidence); enum tokens (from the
    live member-4353 descriptors) — labelled / raw_token (fell through to
    the raw token) / cross_language. The gate requires zero cross_language
    and zero raw_token.
    """
    cat_mod = sys.modules["ots_local_lib.catalog"]
    language = cat_mod.normalize_language(language)

    names = {"native": 0, "translated": 0, "still_symbol": 0}
    name_cross: List[Dict[str, Any]] = []
    for rec in catalog.points:
        name, source = cat_mod.resolve_point_name(rec, language)
        prov, _, lang_tag = source.rpartition(":")
        if lang_tag != language:
            name_cross.append({"id": rec["id"], "name": name, "source": source})
            continue
        if prov == "apk_symbol":
            names["still_symbol"] += 1
        elif prov == "translated":
            names["translated"] += 1
        else:
            names["native"] += 1

    enums = {"labelled": 0}
    enum_cross: List[Dict[str, Any]] = []
    enum_raw: List[Dict[str, Any]] = []
    for rid, tokens in (scan.enum_labels or {}).items():
        rec = catalog.points_by_id.get(str(rid))
        if rec is None:
            continue
        for token in tokens:
            token = token.strip()
            if not token or not cat_mod.normalize_enum_token(token):
                continue
            label, src = cat_mod.resolve_enum_label_source(
                rec, token, language, catalog.enum_token_labels
            )
            if src == language:
                enums["labelled"] += 1
            elif src == "token":
                enum_raw.append({"id": str(rid), "token": token})
            else:
                enum_cross.append({"id": str(rid), "token": token, "label": label, "from": src})

    return {
        "names": {**names, "cross_language": name_cross},
        "enum_tokens": {
            **enums,
            "raw_token": enum_raw,
            "cross_language": enum_cross,
        },
    }


def static_asset_audit(catalog: Any) -> Dict[str, Any]:
    """Audit of the shipped catalog asset alone — independent of what the
    live scan happens to reach, so unreadable or absent-module points are
    covered too.

    Gates: every point resolves natively in both languages; the verbatim-
    symbol exception is exactly the apk_symbol scope (a point whose ONLY
    name evidence is the APK code identifier); the shared token maps carry
    the same tokens in both languages; and no point records an enum label
    gap (the builder recomputes gaps against the descriptor token evidence,
    so a gap means a token would fall back to its raw spelling).
    """
    cat_mod = sys.modules["ots_local_lib.catalog"]

    name_cross: List[Dict[str, Any]] = []
    name_missing: List[str] = []
    symbol_scope_violations: List[Dict[str, Any]] = []
    symbol_ids: List[str] = []
    symbol_names: set = set()
    for rec in catalog.points:
        is_symbol = False
        for language in ("de", "en"):
            name, source = cat_mod.resolve_point_name(rec, language)
            prov, _, lang_tag = source.rpartition(":")
            if not str(name).strip():
                name_missing.append(rec["id"])
            elif lang_tag != language:
                name_cross.append({"id": rec["id"], "language": language, "source": source})
            if prov == "apk_symbol":
                is_symbol = True
                if rec.get("name_source") != "apk_symbol":
                    symbol_scope_violations.append({"id": rec["id"], "source": source})
        if is_symbol:
            symbol_ids.append(rec["id"])
            symbol_names.add(str(rec.get("name")))

    enum_gaps: List[Dict[str, Any]] = []
    point_provenance_mismatch: List[Dict[str, Any]] = []
    for rec in catalog.points:
        gaps = rec.get("enum_label_gaps") or {}
        for language, tokens in gaps.items():
            enum_gaps.append({"id": rec["id"], "language": language, "tokens": tokens})
        # Per-point provenance must cover exactly the point's own label keys.
        own_keys = set(rec.get("enum_labels_de") or {}) | set(rec.get("enum_labels_en") or {})
        prov_keys = set(rec.get("enum_label_sources") or {})
        if own_keys != prov_keys:
            point_provenance_mismatch.append(
                {"id": rec["id"], "tokens": sorted(own_keys.symmetric_difference(prov_keys))}
            )

    shared = catalog.enum_token_labels
    root_pair_mismatch = sorted(
        set(shared.get("en") or {}).symmetric_difference(shared.get("de") or {})
    )
    # Shared-map provenance must name every token on both sides.
    root_prov = cat_mod.load_catalog_raw().get("enum_token_label_sources") or {}
    root_provenance_mismatch = sorted(
        set(shared.get("en") or {}).symmetric_difference(root_prov.get("en") or {})
    ) + sorted(set(shared.get("de") or {}).symmetric_difference(root_prov.get("de") or {}))

    gate_failures = {
        "name_cross_language": len(name_cross),
        "name_missing": len(name_missing),
        "symbol_scope_violations": len(symbol_scope_violations),
        # The permitted exception is EXACTLY the specified apk_symbol scope:
        # 29 points carrying 25 unique technical code identifiers.
        "symbol_cardinality_deviation": 0 if (len(symbol_ids), len(symbol_names)) == (29, 25) else 1,
        "enum_label_gaps": len(enum_gaps),
        "root_token_pair_mismatch": len(root_pair_mismatch),
        "root_provenance_incomplete": len(root_provenance_mismatch),
        "point_provenance_mismatch": len(point_provenance_mismatch),
    }
    return {
        "symbol_points": len(symbol_ids),
        "symbol_names_unique": len(symbol_names),
        "symbol_expected": {"points": 29, "unique_names": 25},
        "gate_failures": gate_failures,
        "gate_passed": not any(gate_failures.values()),
        "detail": {
            "name_cross_language": name_cross,
            "name_missing": name_missing,
            "symbol_scope_violations": symbol_scope_violations,
            "enum_label_gaps": enum_gaps,
            "root_token_pair_mismatch": root_pair_mismatch,
            "root_provenance_incomplete": root_provenance_mismatch,
            "point_provenance_mismatch": point_provenance_mismatch,
            "symbol_ids": symbol_ids,
        },
    }


def build_expectations(catalog: Any, scan: Any, *, language: str = "en") -> Dict[str, Any]:
    """Per-id expectations derived from the catalog + live scan, per language.

    - names: the catalog's language-aware choice (catalog.resolve_point_name:
      German prefers the bundle name, English the APK label; identity read
      from the pump wins where applicable)
    - select options / value maps: the controller's member-4353 state list
      (index = value) with each token's label resolved from the shipped map
      for the language, falling back to the raw token; duplicate labels keep
      their first value in the label->value orientation. Without a live
      descriptor, catalog metadata.
    - circuit names: the controller's own circuit names, falling back to the
      language's "Heizkreis <n>"/"Heating circuit <n>" — exactly what
      build_entities() produces.
    """

    cat_mod = sys.modules["ots_local_lib.catalog"]
    language = cat_mod.normalize_language(language)
    hc_fallback = "Heizkreis {n}" if language == "de" else "Heating circuit {n}"
    hc_ordinal = {tag: i + 1 for i, tag in enumerate(catalog.hc_tags)}
    exp: Dict[str, Any] = {"name": {}, "select_options": {}, "value_map": {}, "circuit": {}}
    for rec in catalog.points:
        rid = str(rec["id"])
        name, _source = cat_mod.resolve_point_name(rec, language)
        exp["name"][rid] = str(name or rid)
        tokens = scan.enum_labels.get(rid) if scan is not None else None
        if tokens:
            tokens_by_value = {idx: t.strip() for idx, t in enumerate(tokens) if t.strip()}
            if tokens_by_value:
                # select options invert label->value: unique labels; the
                # value_map is display-only: duplicate labels allowed.
                labels_by_value = cat_mod.resolve_option_labels(
                    rec, tokens_by_value, language, catalog.enum_token_labels
                )
                exp["select_options"][rid] = {
                    labels_by_value[v]: v for v in sorted(labels_by_value)
                }
                exp["value_map"][rid] = {
                    str(v): cat_mod.resolve_enum_label(
                        rec, tokens_by_value[v], language, catalog.enum_token_labels
                    )
                    for v in sorted(tokens_by_value)
                }
        else:
            if isinstance(rec.get("options"), dict):
                exp["select_options"][rid] = rec["options"]
            if isinstance(rec.get("value_map"), dict):
                exp["value_map"][rid] = rec["value_map"]
        hc_tag = rec.get("hc_tag")
        if hc_tag is not None and int(hc_tag) in hc_ordinal:
            hc_tag = int(hc_tag)
            circuit_name = None
            if scan is not None:
                circuit_name = scan.circuit_names.get(hc_tag)
            exp["circuit"][rid] = circuit_name or hc_fallback.format(n=hc_ordinal[hc_tag])
    return exp


def diff_report(
    ref_ctrl: Dict[str, Any],
    entities: Dict[str, List[Dict[str, Any]]],
    scan_values: Dict[str, Any],
    *,
    host: str,
    expectations: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compare discovered entities against every reference entity definition.

    Pure function so the diff logic itself is unit-testable with intentional
    mismatches. Without ``expectations`` the reference entry itself is the
    expectation (legacy behavior, used by the unit tests); with the
    spec-v4 ``build_expectations`` output, names/enums/circuit names are
    checked against the catalog + live pump data, and deliberate deviations
    from the reference labels are reported informationally.
    """

    expectations = expectations or {}
    exp_names: Dict[str, Any] = expectations.get("name") or {}
    exp_select: Dict[str, Any] = expectations.get("select_options") or {}
    exp_vm: Dict[str, Any] = expectations.get("value_map") or {}
    exp_circuit: Dict[str, Any] = expectations.get("circuit") or {}

    # Reference view: every entity definition, plus per-read-id def groups.
    ref_defs: List[Dict[str, Any]] = []
    ref_defs_by_rid: Dict[str, List[Dict[str, Any]]] = {}
    ref_all_ids: set = set()
    for platform, key in REF_PLATFORM_KEYS:
        for ent in ref_ctrl.get(key, []) or []:
            if not isinstance(ent, dict):
                continue
            rid = str(ent.get("id") or ent.get("read_id") or "")
            for f in ("id", "read_id", "write_id"):
                if ent.get(f):
                    ref_all_ids.add(str(ent[f]))
            if not rid:
                continue
            d = {"platform": platform, "rid": rid, "ent": ent}
            ref_defs.append(d)
            ref_defs_by_rid.setdefault(rid, []).append(d)

    ref_primary_ids = set(ref_defs_by_rid)
    ref_write_only = sorted(ref_all_ids - ref_primary_ids)

    # Discovered view: one entity per read id by construction.
    disc_by_rid: Dict[str, Dict[str, Any]] = {}
    # HA unique_ids are scoped per platform domain, so collisions are checked
    # per platform (a binary_sensor and a switch may share an id).
    unique_ids: Dict[tuple, str] = {}
    unique_id_collisions: List[Dict[str, Any]] = []
    unique_id_rule_violations: List[Dict[str, Any]] = []
    for platform, key in REF_PLATFORM_KEYS:
        for ent in entities.get(key, []) or []:
            rid = str(ent.get("id") or ent.get("read_id") or "")
            disc_by_rid[rid] = {"platform": platform, "ent": ent}
            # The preserved rule: discovery entities must NOT carry a uuid, so
            # their unique_id is the documented host:platform:id shape.
            if ent.get("uuid"):
                unique_id_rule_violations.append({"id": rid, "platform": platform, "uuid": ent.get("uuid")})
            uid = (platform, unique_id_for(host, platform, ent))
            assert uid[1] == f"{host}:{platform}:{rid}".replace("=", "") or ent.get("uuid")
            if uid in unique_ids:
                unique_id_collisions.append({"unique_id": uid[1], "platform": platform, "ids": [unique_ids[uid], rid]})
            unique_ids[uid] = rid

    missing: List[Dict[str, Any]] = []
    platform_mismatch: List[Dict[str, Any]] = []
    covered_by_other_platform: List[Dict[str, Any]] = []
    name_mismatch: List[Dict[str, Any]] = []
    enum_mismatch: List[Dict[str, Any]] = []
    unit_mismatch: List[Dict[str, Any]] = []
    grouping_mismatch: List[Dict[str, Any]] = []
    value_missing: List[Dict[str, Any]] = []
    enum_value_unmapped: List[Dict[str, Any]] = []
    # Informational (spec-v4 deliberate deviations from the reference labels).
    renamed_from_reference: List[Dict[str, Any]] = []
    enum_relabelled_from_reference: List[Dict[str, Any]] = []
    circuit_renamed_from_reference: List[Dict[str, Any]] = []
    matched_defs = 0

    for d in ref_defs:
        rid = d["rid"]
        ref_ent = d["ent"]
        disc = disc_by_rid.get(rid)
        if disc is None:
            missing.append({"id": rid, "platform": d["platform"], "name": ref_ent.get("name")})
            continue

        expected_platform = min(
            (x["platform"] for x in ref_defs_by_rid[rid]),
            key=PLATFORM_PRIORITY.index,
        )
        if disc["platform"] != expected_platform:
            platform_mismatch.append(
                {"id": rid, "reference": d["platform"], "expected": expected_platform, "discovered": disc["platform"]}
            )
            continue

        if rid not in scan_values:
            value_missing.append({"id": rid, "name": ref_ent.get("name")})

        if d["platform"] != expected_platform:
            # This definition's read id is exposed through a higher-priority
            # platform (e.g. binary_sensor twin of a switch). Not a gate
            # failure — it is exactly what today's bundle generator produces.
            covered_by_other_platform.append(
                {"id": rid, "reference": d["platform"], "discovered": disc["platform"], "name": ref_ent.get("name")}
            )
            continue

        matched_defs += 1
        disc_ent = disc["ent"]

        # NAME: expected is the catalog's pump-first choice; the reference
        # name only decides when no expectation is supplied (unit tests).
        expected_name = str(exp_names.get(rid, ref_ent.get("name")) or "")
        if str(disc_ent.get("name") or "") != expected_name:
            name_mismatch.append(
                {"id": rid, "expected": expected_name, "reference": ref_ent.get("name"), "discovered": disc_ent.get("name")}
            )
        elif expected_name != str(ref_ent.get("name") or ""):
            renamed_from_reference.append({"id": rid, "reference": ref_ent.get("name"), "discovered": expected_name})

        # ENUM: expected is the pump's member-4353 state list (falling back
        # to catalog metadata) in the entity's own orientation.
        ref_enum = norm_map(ref_ent.get("options")) or norm_map(ref_ent.get("value_map"))
        disc_enum = norm_map(disc_ent.get("options")) or norm_map(disc_ent.get("value_map"))
        if expectations:
            if disc_ent.get("options") is not None:
                expected_enum = norm_map(exp_select.get(rid))
            elif disc_ent.get("value_map") is not None:
                expected_enum = norm_map(exp_vm.get(rid))
            else:
                expected_enum = None
            if expected_enum is None and ref_enum is not None:
                expected_enum = ref_enum
        else:
            expected_enum = ref_enum
        if expected_enum is not None and disc_enum != expected_enum:
            enum_mismatch.append({"id": rid, "expected": expected_enum, "reference": ref_enum, "discovered": disc_enum})
        elif expected_enum is not None:
            if ref_enum is not None and expected_enum != ref_enum:
                enum_relabelled_from_reference.append({"id": rid, "reference": ref_enum, "discovered": expected_enum})
            if rid in scan_values:
                value = scan_values.get(rid)
                keys = set(expected_enum) | set(expected_enum.values())
                try:
                    normalized = str(int(float(value)))
                except (TypeError, ValueError):
                    normalized = str(value)
                if normalized not in keys and str(value) not in keys:
                    enum_value_unmapped.append({"id": rid, "value": value, "name": ref_ent.get("name")})

        ref_unit = str(ref_ent.get("unit")) if ref_ent.get("unit") else None
        disc_unit = str(disc_ent.get("unit")) if disc_ent.get("unit") else None
        if ref_unit and disc_unit != ref_unit:
            unit_mismatch.append({"id": rid, "reference": ref_unit, "discovered": disc_unit})

        ref_grouped = bool(ref_ent.get("heating_circuit_uid"))
        disc_grouped = bool(disc_ent.get("heating_circuit_uid"))
        # Circuit-device identity: same membership AND the same circuit. The
        # uid encoding differs by design (bundle uuid vs "tag:<instance_tag>"),
        # so the circuit NAME carries the comparison. Expected is the pump's
        # own circuit name (spec v4 B1) when expectations are supplied.
        expected_circuit = exp_circuit.get(rid) if expectations else ref_ent.get("heating_circuit_name")
        if expected_circuit is None:
            expected_circuit = ref_ent.get("heating_circuit_name")
        if ref_grouped != disc_grouped or (
            ref_grouped
            and expected_circuit
            and str(disc_ent.get("heating_circuit_name") or "") != str(expected_circuit)
        ):
            grouping_mismatch.append(
                {
                    "id": rid,
                    "name": ref_ent.get("name"),
                    "reference_grouped": ref_grouped,
                    "discovered_grouped": disc_grouped,
                    "expected_circuit": expected_circuit,
                    "reference_circuit": ref_ent.get("heating_circuit_name"),
                    "discovered_circuit": disc_ent.get("heating_circuit_name"),
                }
            )
        elif (
            ref_grouped
            and ref_ent.get("heating_circuit_name")
            and str(expected_circuit or "") != str(ref_ent.get("heating_circuit_name"))
        ):
            circuit_renamed_from_reference.append(
                {"id": rid, "reference": ref_ent.get("heating_circuit_name"), "discovered": expected_circuit}
            )

    found_but_unknown = sorted(rid for rid in disc_by_rid if rid not in ref_primary_ids)

    gate_failures = {
        "missing": len(missing),
        "platform_mismatch": len(platform_mismatch),
        "name_mismatch": len(name_mismatch),
        "enum_mismatch": len(enum_mismatch),
        "unit_mismatch": len(unit_mismatch),
        "grouping_mismatch": len(grouping_mismatch),
        "value_missing": len(value_missing),
        "unique_id_collisions": len(unique_id_collisions),
        "unique_id_rule_violations": len(unique_id_rule_violations),
    }

    return {
        "reference": {
            "entity_definitions": len(ref_defs),
            "primary_ids": len(ref_primary_ids),
            "distinct_ids": len(ref_all_ids),
            "write_only_ids": ref_write_only,
        },
        "coverage": {
            "definitions_matched_exactly": matched_defs,
            "definitions_covered_by_other_platform": len(covered_by_other_platform),
            "primary_ids_discovered": len(ref_primary_ids) - len({m["id"] for m in missing}),
            "primary_ids_total": len(ref_primary_ids),
            "primary_coverage_percent": round(
                100.0 * (len(ref_primary_ids) - len({m["id"] for m in missing})) / max(1, len(ref_primary_ids)), 2
            ),
        },
        "gate_failures": gate_failures,
        "gate_passed": not any(gate_failures.values()),
        "diff": {
            "known_but_missed": missing,
            "platform_mismatch": platform_mismatch,
            "covered_by_other_platform": covered_by_other_platform,
            "name_mismatch": name_mismatch,
            "enum_mismatch": enum_mismatch,
            "unit_mismatch": unit_mismatch,
            "grouping_mismatch": grouping_mismatch,
            "value_missing": value_missing,
            "enum_value_unmapped": enum_value_unmapped,
            "renamed_from_reference": renamed_from_reference,
            "enum_relabelled_from_reference": enum_relabelled_from_reference,
            "circuit_renamed_from_reference": circuit_renamed_from_reference,
            "unique_id_collisions": unique_id_collisions,
            "unique_id_rule_violations": unique_id_rule_violations,
            "found_but_unknown": {"count": len(found_but_unknown), "ids": found_but_unknown},
        },
    }


async def run(
    host: str, port: int, username: str, password: str, pin: str, languages: List[str]
) -> Dict[str, Any]:
    load_integration_modules()
    import aiohttp

    from ots_local_lib import api as api_mod  # type: ignore
    from ots_local_lib import catalog as cat_mod  # type: ignore
    from ots_local_lib import discovery as disc_mod  # type: ignore
    from ots_local_lib import discovery_entities as de_mod  # type: ignore

    catalog = cat_mod.load_catalog()
    ref = reference_controller()

    # ONE read-only scan; the language pass is offline label resolution.
    async with aiohttp.ClientSession() as session:
        api = api_mod.ClimatixGenericApi(
            session,
            api_mod.ClimatixGenericConnection(
                host=host, port=port, username=username, password=password, pin=pin
            ),
        )
        scan = await disc_mod.async_scan(api, catalog)

    report: Dict[str, Any] = {
        "host": f"{host}:{port}",
        "catalog_version": catalog.catalog_version,
        "present_tags": scan.present_tags,
        "circuit_names": {str(k): v for k, v in scan.circuit_names.items()},
        "plant": {
            "model": scan.plant_model,
            "serial": scan.plant_serial,
            "sw_version": scan.plant_sw_version,
        },
        "catalog": {
            "points_total": len(catalog.points),
            "enabled": sum(1 for p in catalog.points if p.get("enabled_default") is not False),
            "disabled": sum(1 for p in catalog.points if p.get("enabled_default") is False),
            "disabled_technical": sum(1 for p in catalog.points if p.get("technical")),
            "writable": sum(1 for p in catalog.points if p.get("write_id")),
        },
        "scan": scan_counts(catalog, scan),
        "languages": {},
    }

    for language in languages:
        hc_fallback = "Heizkreis {n}" if language == "de" else "Heating circuit {n}"
        entities = de_mod.build_entities(
            catalog=catalog, scan=scan, language=language, hc_fallback_template=hc_fallback
        )
        expectations = build_expectations(catalog, scan, language=language)
        lang_report = diff_report(ref, entities, scan.values, host=host, expectations=expectations)
        lang_report["entities"] = {key: len(entities.get(key, [])) for key in de_mod.PLATFORMS}

        # Independent gate: localization must never drop or shift a
        # pump-advertised option. For every discovered select with a live
        # descriptor, the option VALUES must equal exactly the nonempty
        # descriptor indices (labels are display-only and may collide into
        # deterministic disambiguations; values may not).
        incomplete: List[Dict[str, Any]] = []
        for ent in entities.get("selects", []) or []:
            rid = str(ent.get("read_id") or ent.get("id") or "")
            tokens = scan.enum_labels.get(rid)
            if not tokens:
                continue
            expected_values = {idx for idx, t in enumerate(tokens) if t.strip()}
            got_values = {int(v) for v in (ent.get("options") or {}).values()}
            if got_values != expected_values:
                incomplete.append(
                    {"id": rid, "expected_values": sorted(expected_values), "got_values": sorted(got_values)}
                )
        lang_report["gate_failures"]["select_options_incomplete"] = len(incomplete)
        lang_report["diff"]["select_options_incomplete"] = incomplete

        # Bilingual audit gate: names and live enum tokens must resolve in
        # the requested language itself; raw tokens are tolerated only for
        # the undocumented numeric states (identical in both languages).
        audit = bilingual_audit(catalog, scan, language)
        lang_report["bilingual_audit"] = audit
        lang_report["gate_failures"]["name_cross_language"] = len(audit["names"]["cross_language"])
        lang_report["gate_failures"]["enum_cross_language"] = len(audit["enum_tokens"]["cross_language"])
        lang_report["gate_failures"]["enum_raw_token"] = len(audit["enum_tokens"]["raw_token"])
        lang_report["gate_passed"] = not any(lang_report["gate_failures"].values())

        report["languages"][language] = lang_report

    # The static shipped-asset audit is language-independent and covers every
    # catalog point, including ones this plant cannot read.
    report["static_audit"] = static_asset_audit(catalog)
    report["gate_passed"] = report["static_audit"]["gate_passed"] and all(
        r["gate_passed"] for r in report["languages"].values()
    )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--username", default="JSON")
    ap.add_argument("--password", default="SBTAdmin!")
    ap.add_argument("--pin", default="7659")
    ap.add_argument(
        "--language",
        choices=("de", "en", "both"),
        default="both",
        help="run the diff in one language or (default) both",
    )
    ap.add_argument("--report", type=Path, default=None, help="write full JSON report here")
    args = ap.parse_args()

    languages = ["de", "en"] if args.language == "both" else [args.language]
    report = asyncio.run(run(args.host, args.port, args.username, args.password, args.pin, languages))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Full report written to {args.report}")

    summary_keys = ("host", "catalog_version", "present_tags", "circuit_names", "plant", "catalog", "scan")
    print(json.dumps({k: report[k] for k in summary_keys}, ensure_ascii=False, indent=1))
    sa = report["static_audit"]
    print(
        f"static_audit: gate_passed={sa['gate_passed']} symbol_points={sa['symbol_points']} "
        f"symbol_names_unique={sa['symbol_names_unique']} failures="
        + json.dumps({k: v for k, v in sa["gate_failures"].items() if v})
    )
    for language, lr in report["languages"].items():
        print(f"--- language={language}")
        print(
            json.dumps(
                {k: lr[k] for k in ("reference", "coverage", "entities", "gate_failures", "gate_passed")},
                ensure_ascii=False,
                indent=1,
            )
        )
        d = lr["diff"]
        print(
            f"diff[{language}]: "
            + " ".join(
                f"{k}={len(v['ids']) if isinstance(v, dict) and 'ids' in v else len(v)}"
                for k, v in d.items()
            )
        )
        ba = lr["bilingual_audit"]
        n, e = ba["names"], ba["enum_tokens"]
        print(
            f"bilingual[{language}]: names native={n['native']} translated={n['translated']} "
            f"symbol={n['still_symbol']} cross={len(n['cross_language'])} | enum tokens "
            f"labelled={e['labelled']} raw={len(e['raw_token'])} cross={len(e['cross_language'])}"
        )
    return 0 if report["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
