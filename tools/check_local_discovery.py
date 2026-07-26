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
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

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


def diff_report(
    ref_ctrl: Dict[str, Any],
    entities: Dict[str, List[Dict[str, Any]]],
    scan_values: Dict[str, Any],
    *,
    host: str,
) -> Dict[str, Any]:
    """Compare discovered entities against every reference entity definition.

    Pure function so the diff logic itself is unit-testable with intentional
    mismatches.
    """

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

        if str(disc_ent.get("name") or "") != str(ref_ent.get("name") or ""):
            name_mismatch.append({"id": rid, "reference": ref_ent.get("name"), "discovered": disc_ent.get("name")})

        ref_enum = norm_map(ref_ent.get("options")) or norm_map(ref_ent.get("value_map"))
        disc_enum = norm_map(disc_ent.get("options")) or norm_map(disc_ent.get("value_map"))
        if ref_enum is not None and disc_enum != ref_enum:
            enum_mismatch.append({"id": rid, "reference": ref_enum, "discovered": disc_enum})
        elif ref_enum is not None and rid in scan_values:
            value = scan_values.get(rid)
            keys = set(ref_enum) | set(ref_enum.values())
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
        # Circuit-device identity: same membership AND the same circuit (the
        # circuit NAME is comparable across paths — the uid encoding differs
        # by design: bundle uuid vs "tag:<instance_tag>").
        if ref_grouped != disc_grouped or (
            ref_grouped
            and ref_ent.get("heating_circuit_name")
            and str(disc_ent.get("heating_circuit_name") or "") != str(ref_ent.get("heating_circuit_name"))
        ):
            grouping_mismatch.append(
                {
                    "id": rid,
                    "name": ref_ent.get("name"),
                    "reference_grouped": ref_grouped,
                    "discovered_grouped": disc_grouped,
                    "reference_circuit": ref_ent.get("heating_circuit_name"),
                    "discovered_circuit": disc_ent.get("heating_circuit_name"),
                }
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
            "unique_id_collisions": unique_id_collisions,
            "unique_id_rule_violations": unique_id_rule_violations,
            "found_but_unknown": {"count": len(found_but_unknown), "ids": found_but_unknown},
        },
    }


async def run(host: str, port: int, username: str, password: str, pin: str) -> Dict[str, Any]:
    load_integration_modules()
    import aiohttp

    from ots_local_lib import api as api_mod  # type: ignore
    from ots_local_lib import catalog as cat_mod  # type: ignore
    from ots_local_lib import discovery as disc_mod  # type: ignore
    from ots_local_lib import discovery_entities as de_mod  # type: ignore

    catalog = cat_mod.load_catalog()
    ref = reference_controller()

    async with aiohttp.ClientSession() as session:
        api = api_mod.ClimatixGenericApi(
            session,
            api_mod.ClimatixGenericConnection(
                host=host, port=port, username=username, password=password, pin=pin
            ),
        )
        scan = await disc_mod.async_scan(api, catalog)

    entities = de_mod.build_entities(catalog=catalog, scan=scan)
    report = diff_report(ref, entities, scan.values, host=host)

    report["host"] = f"{host}:{port}"
    report["catalog_version"] = catalog.catalog_version
    report["present_tags"] = scan.present_tags
    report["circuit_names"] = {str(k): v for k, v in scan.circuit_names.items()}
    report["scan"] = {
        "probed_ids": scan.probed_ids,
        "swept_ids": scan.swept_ids,
        "readable_ids": len(scan.values),
    }
    report["entities"] = {key: len(entities.get(key, [])) for key in de_mod.PLATFORMS}
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--username", default="JSON")
    ap.add_argument("--password", default="SBTAdmin!")
    ap.add_argument("--pin", default="7659")
    ap.add_argument("--report", type=Path, default=None, help="write full JSON report here")
    args = ap.parse_args()

    report = asyncio.run(run(args.host, args.port, args.username, args.password, args.pin))

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Full report written to {args.report}")

    summary_keys = (
        "host",
        "catalog_version",
        "present_tags",
        "circuit_names",
        "scan",
        "reference",
        "coverage",
        "entities",
        "gate_failures",
        "gate_passed",
    )
    print(json.dumps({k: report[k] for k in summary_keys}, ensure_ascii=False, indent=1))
    d = report["diff"]
    print(
        "diff: "
        + " ".join(
            f"{k}={len(v['ids']) if isinstance(v, dict) and 'ids' in v else len(v)}"
            for k, v in d.items()
        )
    )
    return 0 if report["gate_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
