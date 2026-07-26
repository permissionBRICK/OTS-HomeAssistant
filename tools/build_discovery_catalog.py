#!/usr/bin/env python3
"""Build the packaged local-discovery catalog for ochsner_local_ots.

Inputs (research artifacts, not shipped):
  apk_files/reports/catalog_model.json   validated APK OA catalog + instance tags
  apk_files/reports/catalog_names.json   APK-derived human labels per OA
  apk_files/ha_config/core.config_entries  reference-plant seed (names, units,
                                           enum options, write ids, bounds)

Output (shipped with the integration):
  custom_components/ochsner_local_ots/data/discovery_catalog.json

The output is deterministic: same inputs -> byte-identical file (the version
field is a content hash, not a timestamp).

Catalog point record keys:
  id            read OA (canonical Base64)
  platform      sensor|binary_sensor|number|select|text|switch
  name          display name
  name_confidence  reference|apk_label|apk_symbol|address
  sources       subset of [apk, reference, hc_template]
  write_id, unit, options, value_map, min/max/bundle_min/bundle_max/step,
  on_value/off_value, enabled_default, diagnostic, hc_group, schedule,
  descriptor    (member 4353 metadata point; never an entity)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = REPO_ROOT / "custom_components" / "ochsner_local_ots"
sys.path.insert(0, str(PKG_DIR))

import catalog as oa_catalog  # noqa: E402  (loaded as a plain module, no HA needed)

HC_TAGS = list(oa_catalog.HC_INSTANCE_TAGS)

# Entities whose value is the owner's personal data or the network config.
# They are still created (feature parity with the bundle path), but only as
# disabled-by-default diagnostic entities.
PRIVACY_PATTERNS = re.compile(
    r"(?i)\b(kunde|kundenname|customer|owner|betreiber|besitzer|telefon|phone|"
    r"e-?mail|mac|ip-?adresse|ip address|subnetz\w*|subnet\w*|gateway|dns)\b"
)

# Destructive / one-shot / service-and-commissioning style writable points.
# Per the project owner's decision these are STILL CREATED as writable
# entities, but entity_registry_enabled_default=False so nothing fires by
# accident (the same approach as DISABLE_BY_DEFAULT_SWITCH_KEYWORDS).
DESTRUCTIVE_PATTERNS = re.compile(
    r"(?i)(reset|neustart|restart|reboot|werkseinstellung|factory|format|"
    r"relais[ -]?test|relay[ -]?test|inbetriebnahme|commissioning)"
)

# Circuit ordinal fragments that must be rewritten when a heating-circuit
# point is propagated from one circuit instance to another.
_HC_ORDINAL_RES = (
    re.compile(r"(?i)(heizkreis\s*)(\d+)"),
    re.compile(r"(HC)(\d+)"),
    re.compile(r"(HK)(\d+)"),
)

PLATFORM_PRIORITY = ["switch", "select", "number", "text", "binary_sensor", "sensor"]


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _reference_controller(config_entries: Any) -> Dict[str, Any]:
    entries = config_entries["data"]["entries"]
    ours = [e for e in entries if e.get("domain") == "ochsner_local_ots"]
    if not ours:
        raise SystemExit("reference config_entries has no ochsner_local_ots entry")
    controllers = ours[0]["data"]["controllers"]
    return controllers[0]


def _apk_label(names_rec: Optional[Dict[str, Any]], model_rec: Dict[str, Any]) -> Tuple[str, str]:
    """Return (name, confidence) from APK evidence."""

    labels: List[str] = []
    if isinstance(names_rec, dict):
        for n in names_rec.get("apk_names") or []:
            lab = n.get("label_en")
            if isinstance(lab, str) and lab.strip():
                labels.append(lab.strip())
    for rl in model_rec.get("resource_labels") or []:
        lab = rl.get("label")
        if isinstance(lab, str) and lab.strip():
            labels.append(lab.strip())

    distinct = sorted(set(labels))
    if len(distinct) == 1:
        return distinct[0], "apk_label"

    symbols: List[str] = []
    if isinstance(names_rec, dict):
        symbols += [s for s in names_rec.get("symbols") or [] if isinstance(s, str) and s.strip()]
    symbols += [s for s in model_rec.get("names") or [] if isinstance(s, str) and s.strip()]
    if symbols:
        return sorted(set(symbols))[0], "apk_symbol"

    oa = oa_catalog.decode_oa(model_rec["id"])
    return (
        f"OT{oa.object_type} T{oa.instance_tag} P{oa.point_index} M{oa.member_id}",
        "address",
    )


def _rewrite_hc_ordinal(name: str, target_ordinal: int) -> str:
    out = name
    for rx in _HC_ORDINAL_RES:
        out = rx.sub(lambda m: f"{m.group(1)}{target_ordinal}", out)
    return out


def _hc_uid_to_tag(ctrl: Dict[str, Any]) -> Dict[str, int]:
    """Map the reference bundle's heating-circuit uids to circuit instance tags.

    A circuit's own points (e.g. its name text) are addressed on the circuit
    instance tag, which identifies the circuit; shared-tag points (pump,
    mixer, flow temperature on tag 35112) inherit the circuit via this map.
    """

    out: Dict[str, int] = {}
    for key in ("sensors", "binary_sensors", "numbers", "selects", "texts", "switches"):
        for ent in ctrl.get(key, []) or []:
            if not isinstance(ent, dict):
                continue
            uid = str(ent.get("heating_circuit_uid") or "").strip()
            if not uid or uid in out:
                continue
            oa = oa_catalog.try_decode_oa(ent.get("read_id") or ent.get("id"))
            if oa is not None and oa.instance_tag in HC_TAGS:
                out[uid] = oa.instance_tag
    return out


def _reference_records(ctrl: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """One catalog record per read OA from the reference plant entity lists.

    When a read id backs several entity kinds (the reference entry predates
    the generator's dedupe), the writable/most specific platform wins — the
    same outcome today's bundle generator produces.
    """

    by_id: Dict[str, Dict[str, Any]] = {}
    hc_uid_to_tag = _hc_uid_to_tag(ctrl)

    def visit(kind: str, ent: Dict[str, Any]) -> None:
        read_id = str(ent.get("id") or ent.get("read_id") or "").strip()
        if not read_id or oa_catalog.try_decode_oa(read_id) is None:
            return

        rec: Dict[str, Any] = {
            "id": read_id,
            "platform": kind,
            "name": str(ent.get("name") or read_id),
            "name_confidence": "reference",
            "sources": ["reference"],
        }
        write_id = ent.get("write_id")
        if isinstance(write_id, str) and write_id and kind in {"number", "select", "text", "switch"}:
            rec["write_id"] = write_id
        for k in ("unit", "options", "value_map", "min", "max", "bundle_min", "bundle_max", "step", "on_value", "off_value"):
            if ent.get(k) is not None:
                rec[k] = ent[k]
        if ent.get("enabled_default") is False:
            rec["enabled_default"] = False

        # Circuit membership: from the bundle's circuit uid (works for
        # shared-tag points like pump/mixer/flow temperature whose OA cannot
        # reveal the circuit), falling back to the read OA's own circuit tag.
        hc_uid = str(ent.get("heating_circuit_uid") or "").strip()
        if hc_uid:
            hc_tag = hc_uid_to_tag.get(hc_uid)
            if hc_tag is None:
                oa = oa_catalog.try_decode_oa(read_id)
                if oa is not None and oa.instance_tag in HC_TAGS:
                    hc_tag = oa.instance_tag
            if hc_tag is not None:
                rec["hc_tag"] = hc_tag

        if PRIVACY_PATTERNS.search(rec["name"]):
            rec["diagnostic"] = True
            rec["enabled_default"] = False
        if rec.get("write_id") and DESTRUCTIVE_PATTERNS.search(rec["name"]):
            rec["enabled_default"] = False

        existing = by_id.get(read_id)
        if existing is None or PLATFORM_PRIORITY.index(kind) < PLATFORM_PRIORITY.index(existing["platform"]):
            if existing is not None:
                # Keep any metadata the lower-priority record contributed
                # (e.g. a sensor's unit alongside a number).
                for k, v in existing.items():
                    rec.setdefault(k, v)
                rec["platform"] = kind
                rec["id"] = read_id
            by_id[read_id] = rec

    for kind, key in (
        ("sensor", "sensors"),
        ("binary_sensor", "binary_sensors"),
        ("number", "numbers"),
        ("select", "selects"),
        ("text", "texts"),
        ("switch", "switches"),
    ):
        for ent in ctrl.get(key, []) or []:
            if isinstance(ent, dict):
                visit(kind, ent)

    return by_id


def _propagate_hc_templates(points: Dict[str, Dict[str, Any]]) -> int:
    """Instantiate reference heating-circuit metadata on the other HC tags.

    The circuit module is one template instantiated per circuit (proven:
    146/148 shared triples across the four circuit tags), so a point known on
    HC1 exists at the same (object_type, point_index, member_id) on any other
    fitted circuit. Only fills gaps; never overwrites reference records.
    """

    added = 0
    hc_ordinal = {tag: i + 1 for i, tag in enumerate(HC_TAGS)}

    ref_hc = [
        rec
        for rec in points.values()
        if "reference" in rec["sources"] and oa_catalog.decode_oa(rec["id"]).instance_tag in hc_ordinal
    ]

    for rec in ref_hc:
        src_oa = oa_catalog.decode_oa(rec["id"])
        src_tag = src_oa.instance_tag
        write_id = rec.get("write_id")
        write_oa = oa_catalog.try_decode_oa(write_id) if write_id else None

        for target_tag in HC_TAGS:
            if target_tag == src_tag:
                continue
            target_id = oa_catalog.retag(rec["id"], target_tag)
            target = points.get(target_id)
            if target is not None and "reference" in target.get("sources", []):
                continue

            new = {k: v for k, v in rec.items() if k not in {"id", "write_id", "sources", "name", "name_confidence", "hc_tag"}}
            new["id"] = target_id
            new["name"] = _rewrite_hc_ordinal(rec["name"], hc_ordinal[target_tag])
            new["name_confidence"] = "reference"
            new["sources"] = sorted(set((target.get("sources") if target else []) or []) | {"hc_template"})
            if rec.get("hc_tag") is not None:
                new["hc_tag"] = target_tag
            if write_oa is not None:
                if write_oa.instance_tag == src_tag:
                    new["write_id"] = oa_catalog.retag(write_id, target_tag)
                elif write_id == rec["id"]:
                    new["write_id"] = target_id
                else:
                    # Cross-module write binding: cannot be retagged safely,
                    # so the propagated point falls back to read-only.
                    new["platform"] = "sensor"

            if target is not None:
                # Reference-grade metadata replaces the weak-name APK
                # defaults; the source record's own flags (if any) are in new.
                target.pop("diagnostic", None)
                target.pop("enabled_default", None)
                target.update(new)
            else:
                points[target_id] = new
                added += 1
    return added


def _expand_apk_hc_union(points: Dict[str, Dict[str, Any]], apk_records: List[Dict[str, Any]]) -> int:
    """Add the APK circuit-template union to every circuit tag (24 synthetics)."""

    triples_by_tag: Dict[int, set] = {t: set() for t in HC_TAGS}
    rec_by_triple: Dict[Tuple[int, int, int], Dict[str, Any]] = {}
    for r in apk_records:
        tag = int(r["instance_tag"])
        if tag not in triples_by_tag:
            continue
        triple = (int(r["object_type"]), int(r["point_index"]), int(r["member_id"]))
        triples_by_tag[tag].add(triple)
        rec_by_triple.setdefault(triple, r)

    union = set().union(*triples_by_tag.values()) if triples_by_tag else set()
    added = 0
    for tag in HC_TAGS:
        for triple in union - triples_by_tag[tag]:
            ot, pi, mid = triple
            new_id = oa_catalog.encode_oa(ot, tag, pi, mid)
            if new_id in points:
                continue
            src = rec_by_triple[triple]
            name, confidence = _apk_label(None, src)
            points[new_id] = {
                "id": new_id,
                "platform": "sensor",
                "name": name,
                "name_confidence": confidence,
                "sources": ["hc_template"],
                "diagnostic": True,
                "enabled_default": False,
                "schedule": oa_catalog.is_schedule_member(mid) or None,
                "descriptor": (mid == 4353) or None,
            }
            points[new_id] = {k: v for k, v in points[new_id].items() if v is not None}
            added += 1
    return added


def build_catalog(
    *,
    model_path: Path,
    names_path: Path,
    reference_path: Path,
) -> Dict[str, Any]:
    model = _load_json(model_path)
    names = _load_json(names_path)
    ctrl = _reference_controller(_load_json(reference_path))

    apk_records = [r for r in model["catalog"] if r.get("classification") == "ochsner_object_address"]
    names_by_id = names.get("apk_id_catalog") or {}

    points: Dict[str, Dict[str, Any]] = {}

    # 1) APK-derived candidates (read-only; nothing in the APK proves
    #    writability, so writable shaping comes from reference/bundle data).
    for r in apk_records:
        oa_id = str(r["id"])
        # Trust our codec, not the report: re-encode and require equality.
        oa = oa_catalog.decode_oa(oa_id)
        assert oa_catalog.encode_oa(*oa) == oa_id

        name, confidence = _apk_label(names_by_id.get(oa_id), r)
        rec: Dict[str, Any] = {
            "id": oa_id,
            "platform": "sensor",
            "name": name,
            "name_confidence": confidence,
            "sources": ["apk"],
        }
        if oa_catalog.is_schedule_member(oa.member_id):
            rec["schedule"] = True
        if oa.member_id == 4353:
            rec["descriptor"] = True
        # Unknown/weakly named points become disabled diagnostic sensors;
        # unambiguous APK labels are good enough for a normal named sensor.
        if confidence != "apk_label":
            rec["diagnostic"] = True
            rec["enabled_default"] = False
        points[oa_id] = rec

    # 2) Reference-plant seed overrides: real names, units, enum options and
    #    the exact writable shaping the bundle path uses today.
    for read_id, ref in _reference_records(ctrl).items():
        existing = points.get(read_id)
        if existing is not None:
            merged = dict(existing)
            # The reference name/shape wins; the weak-name diagnostic/disabled
            # defaults from the APK pass no longer apply to a well-known point.
            merged.pop("diagnostic", None)
            merged.pop("enabled_default", None)
            merged.update(ref)
            merged["sources"] = sorted(set(existing["sources"]) | {"reference"})
            points[read_id] = merged
        else:
            points[read_id] = ref

    # 3) Heating-circuit template instantiation.
    propagated = _propagate_hc_templates(points)
    expanded = _expand_apk_hc_union(points, apk_records)

    # 4) Instance tag table (drives the phase-A module probe).
    module_by_tag: Dict[int, str] = {}
    for t in model.get("instance_tags") or []:
        if t.get("classification") == "ochsner_instance_tag":
            module_by_tag[int(t["instance_tag"])] = str(t.get("module") or "")
    all_tags = sorted({oa_catalog.decode_oa(p["id"]).instance_tag for p in points.values()})
    tags = [
        {"tag": tag, "module": module_by_tag.get(tag, "reference_only")}
        for tag in all_tags
    ]

    point_list = [points[k] for k in sorted(points)]
    body = {
        "hc_tags": HC_TAGS,
        "points": point_list,
        "tags": tags,
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]

    return {
        "schema_version": 1,
        "catalog_version": digest,
        "app_source": "com.ochsner.app v1.2.0/1785 + reference plant seed",
        **body,
        "stats": {
            "points_total": len(point_list),
            "apk_points": sum(1 for p in point_list if "apk" in p["sources"]),
            "reference_points": sum(1 for p in point_list if "reference" in p["sources"]),
            "hc_template_points": sum(1 for p in point_list if "hc_template" in p["sources"]),
            "hc_template_propagated": propagated,
            "hc_template_expanded": expanded,
            "schedule_points": sum(1 for p in point_list if p.get("schedule")),
            "writable_points": sum(1 for p in point_list if p.get("write_id")),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path, default=REPO_ROOT / "apk_files/reports/catalog_model.json")
    ap.add_argument("--names", type=Path, default=REPO_ROOT / "apk_files/reports/catalog_names.json")
    ap.add_argument("--reference", type=Path, default=REPO_ROOT / "apk_files/ha_config/core.config_entries")
    ap.add_argument("--out", type=Path, default=PKG_DIR / "data" / "discovery_catalog.json")
    args = ap.parse_args()

    catalog = build_catalog(model_path=args.model, names_path=args.names, reference_path=args.reference)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        json.dump(catalog, fh, ensure_ascii=False, sort_keys=True, indent=1)
        fh.write("\n")

    print(f"Wrote {args.out} (catalog_version={catalog['catalog_version']})")
    print(json.dumps(catalog["stats"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
