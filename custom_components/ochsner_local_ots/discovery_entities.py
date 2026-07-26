"""Turn a local discovery scan into entity configs.

The output dictionaries are byte-compatible with what
bundle_generator.generate_entities_from_bundle() produces today, and the
classification rules are the same ones the bundle path uses: a non-readonly
write binding plus a known type makes a writable entity (number / select /
switch / text); destructive or weakly-named points are still created but
disabled by default in the entity registry.

UNIQUE-ID COMPATIBILITY (do not change): discovery entities carry NO "uuid"
key, so every platform falls back to the long-standing stable scheme

    unique_id = f"{host}:{platform}:{read_id}".replace("=", "")

(the same shape the options-flow override keys and uuid-less bundle entities
already use). Bundle-created entities keep their bundle UUID unique_ids —
nothing existing is ever renamed or rewritten; upgrades and rescans cannot
churn entities. The two schemes cannot collide (bundle UUIDs contain no ":"
separator triple), so merging discovery additions into a bundle entry can
never produce "_2" duplicates. A fresh local-scan entry for a plant that was
previously bundle-based is a NEW config entry and intentionally uses the
host-based scheme throughout.

No Home Assistant imports here so tools/ can reuse this module directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .catalog import DiscoveryCatalog, canonical_oa, try_decode_oa
from .discovery import DiscoveryScanResult

PLATFORMS = ("sensors", "binary_sensors", "numbers", "selects", "texts", "switches")

_KIND_TO_KEY = {
    "sensor": "sensors",
    "binary_sensor": "binary_sensors",
    "number": "numbers",
    "select": "selects",
    "text": "texts",
    "switch": "switches",
}


def _is_numeric(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _apply_common(cfg: Dict[str, Any], rec: Dict[str, Any]) -> Dict[str, Any]:
    if rec.get("enabled_default") is False:
        cfg["enabled_default"] = False
    if rec.get("diagnostic"):
        cfg["diagnostic"] = True
    return cfg


def build_entities(
    *,
    catalog: DiscoveryCatalog,
    scan: DiscoveryScanResult,
    hc_uid_by_tag: Optional[Dict[int, str]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Convert readable scan results into per-platform entity config lists."""

    hc_uid_by_tag = dict(hc_uid_by_tag or {})
    hc_ordinal = {tag: i + 1 for i, tag in enumerate(catalog.hc_tags)}

    out: Dict[str, List[Dict[str, Any]]] = {key: [] for key in PLATFORMS}

    for rec in catalog.points:
        oid = str(rec["id"])
        if oid not in scan.values:
            continue
        if rec.get("schedule") or rec.get("descriptor"):
            continue

        value = scan.values.get(oid)
        platform = str(rec.get("platform") or "sensor")
        write_id = rec.get("write_id")
        options = rec.get("options") if isinstance(rec.get("options"), dict) else None
        value_map = rec.get("value_map") if isinstance(rec.get("value_map"), dict) else None

        # Same demotion guards the bundle generator applies after probing:
        # a "writable" shape only stays writable when the live value fits it.
        if platform in {"number", "select", "text", "switch"} and not (isinstance(write_id, str) and write_id):
            platform = "binary_sensor" if platform == "switch" else "sensor"
        if platform == "number" and not _is_numeric(value):
            platform = "sensor"
        if platform == "text" and value is not None and not isinstance(value, str):
            platform = "sensor"
        if platform == "select" and not options:
            platform = "sensor"
        if platform == "switch" and ("on_value" not in rec or "off_value" not in rec):
            platform = "binary_sensor"

        cfg: Dict[str, Any] = {"name": str(rec.get("name") or oid)}

        # Circuit membership comes from the catalog's explicit hc_tag (the OA
        # tag alone cannot reveal it: pump/mixer/flow-temp points live on the
        # shared module tag but belong to a circuit).
        hc_tag = rec.get("hc_tag")
        if hc_tag is not None and int(hc_tag) in hc_ordinal:
            hc_tag = int(hc_tag)
            cfg["heating_circuit_uid"] = str(hc_uid_by_tag.get(hc_tag) or f"tag:{hc_tag}")
            cfg["heating_circuit_name"] = (
                scan.circuit_names.get(hc_tag) or f"Heizkreis {hc_ordinal[hc_tag]}"
            )

        if platform == "sensor":
            cfg["id"] = oid
            if rec.get("unit"):
                cfg["unit"] = str(rec["unit"])
            if value_map:
                cfg["value_map"] = dict(value_map)
        elif platform == "binary_sensor":
            cfg["id"] = oid
            if value_map:
                cfg["value_map"] = dict(value_map)
        elif platform == "number":
            cfg["read_id"] = oid
            cfg["write_id"] = str(write_id)
            if rec.get("unit"):
                cfg["unit"] = str(rec["unit"])
            for k in ("min", "max", "bundle_min", "bundle_max", "step"):
                if rec.get(k) is not None:
                    cfg[k] = rec[k]
        elif platform == "select":
            cfg["read_id"] = oid
            cfg["write_id"] = str(write_id)
            cfg["options"] = dict(options or {})
        elif platform == "text":
            cfg["read_id"] = oid
            cfg["write_id"] = str(write_id)
        elif platform == "switch":
            cfg["read_id"] = oid
            cfg["write_id"] = str(write_id)
            cfg["on_value"] = rec["on_value"]
            cfg["off_value"] = rec["off_value"]
            cfg["enabled_default"] = rec.get("enabled_default", True) is not False
        else:
            continue

        out[_KIND_TO_KEY[platform]].append(_apply_common(cfg, rec))

    # Same final dedupe as the bundle generator: a value that became a
    # writable control (or text) is not also exposed as a plain sensor.
    writable_read_ids = {
        str(c.get("read_id"))
        for key in ("numbers", "selects", "texts", "switches")
        for c in out[key]
    }
    out["sensors"] = [s for s in out["sensors"] if str(s.get("id")) not in writable_read_ids]

    return out


def merge_discovered_entities(
    ctrl: Dict[str, Any],
    discovered: Dict[str, List[Dict[str, Any]]],
) -> tuple[Dict[str, Any], Dict[str, int]]:
    """Merge discovered entities into an existing controller dict.

    Additions only — nothing is removed or rewritten, so existing entities
    keep their unique_ids and devices (backward compatibility guarantee).
    Deduplication is by read OA: a read-only candidate is skipped when ANY
    existing entity already exposes that id; a writable candidate is only
    skipped when a writable entity already uses it (so a writable control can
    still be added next to a pre-existing read-only sensor, like the bundle
    rescan does). The per-platform config keys are the same strings as the
    PLATFORMS names ("sensors", "numbers", ...).
    """

    def _read_id(ent: Dict[str, Any]) -> str:
        rid = str(ent.get("id") or ent.get("read_id") or "").strip()
        # Padding-independent dedupe: padded and unpadded spellings of the
        # same OA are the same datapoint.
        oa = try_decode_oa(rid)
        return canonical_oa(rid) if oa is not None else rid

    writable_keys = {"numbers", "selects", "texts", "switches"}

    existing_all: set = set()
    existing_writable: set = set()
    existing_by_key: Dict[str, set] = {}
    for key in PLATFORMS:
        ids = {_read_id(e) for e in (ctrl.get(key) or []) if isinstance(e, dict)}
        ids.discard("")
        existing_by_key[key] = ids
        existing_all |= ids
        if key in writable_keys:
            existing_writable |= ids

    out = dict(ctrl)
    added_by_platform: Dict[str, int] = {key: 0 for key in PLATFORMS}
    for key in PLATFORMS:
        merged = list(ctrl.get(key) or [])
        for ent in discovered.get(key, []) or []:
            rid = _read_id(ent)
            if not rid:
                continue
            if key in {"sensors", "binary_sensors"}:
                if rid in existing_all:
                    continue
            else:
                if rid in existing_writable or rid in existing_by_key[key]:
                    continue
            merged.append(ent)
            existing_by_key[key].add(rid)
            existing_all.add(rid)
            if key in writable_keys:
                existing_writable.add(rid)
            added_by_platform[key] += 1
        out[key] = merged

    return out, added_by_platform


def hc_uid_map_from_existing_entities(entities_by_platform: Dict[str, List[Dict[str, Any]]], hc_tags: List[int]) -> Dict[int, str]:
    """Map circuit instance tags to already-used heating_circuit_uid values.

    Used when merging discovery results into an existing bundle-based entry so
    added circuit entities join the user's existing circuit devices instead of
    creating parallel ones. The mapping decodes each grouped entity's read OA:
    its instance tag identifies the circuit.
    """

    out: Dict[int, str] = {}
    for ents in entities_by_platform.values():
        for ent in ents or []:
            if not isinstance(ent, dict):
                continue
            uid = str(ent.get("heating_circuit_uid") or "").strip()
            if not uid:
                continue
            oa = try_decode_oa(ent.get("read_id") or ent.get("id"))
            if oa is None:
                continue
            if oa.instance_tag in hc_tags and oa.instance_tag not in out:
                out[oa.instance_tag] = uid
    return out


def _hc_tag_by_uid(ents: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
    """Infer each bundle heating-circuit uid's own instance tag.

    A circuit groups points on its own instance tag plus points on module
    tags shared with the other circuits; the tag unique to one circuit uid is
    that circuit's instance tag.
    """

    tags_by_uid: Dict[str, set] = {}
    for lists in ents.values():
        for ent in lists or []:
            if not isinstance(ent, dict):
                continue
            uid = str(ent.get("heating_circuit_uid") or "").strip()
            if not uid:
                continue
            oa = try_decode_oa(ent.get("read_id") or ent.get("id"))
            if oa is not None:
                tags_by_uid.setdefault(uid, set()).add(oa.instance_tag)

    uid_count_by_tag: Dict[int, int] = {}
    for tags in tags_by_uid.values():
        for t in tags:
            uid_count_by_tag[t] = uid_count_by_tag.get(t, 0) + 1

    out: Dict[str, int] = {}
    for uid, tags in tags_by_uid.items():
        own = [t for t in tags if uid_count_by_tag[t] == 1]
        if len(own) == 1:
            out[uid] = own[0]
    return out


def overlay_points_from_bundle_entities(
    ents: Dict[str, List[Dict[str, Any]]],
) -> List[Dict[str, Any]]:
    """Convert bundle-generated entity configs into catalog point overlays.

    This is the optional enrichment path: a cloud bundle (from a legacy entry
    or a donated file) contributes exact names, enum options, units, bounds
    and write bindings for addresses the APK catalog does not know. The
    overlay never enters onboarding — it only enriches the local catalog scan.
    """

    hc_tag_by_uid = _hc_tag_by_uid(ents)

    out: List[Dict[str, Any]] = []
    for kind, key in (
        ("sensor", "sensors"),
        ("binary_sensor", "binary_sensors"),
        ("number", "numbers"),
        ("select", "selects"),
        ("text", "texts"),
        ("switch", "switches"),
    ):
        for ent in ents.get(key, []) or []:
            if not isinstance(ent, dict):
                continue
            read_id = str(ent.get("id") or ent.get("read_id") or "").strip()
            if not read_id or try_decode_oa(read_id) is None:
                continue
            read_id = canonical_oa(read_id)
            rec: Dict[str, Any] = {
                "id": read_id,
                "platform": kind,
                "name": str(ent.get("name") or read_id),
                "name_confidence": "reference",
                "sources": ["bundle_import"],
            }
            write_id = ent.get("write_id")
            if isinstance(write_id, str) and write_id and kind in {"number", "select", "text", "switch"}:
                rec["write_id"] = canonical_oa(write_id) if try_decode_oa(write_id) else write_id
            for k in ("unit", "options", "value_map", "min", "max", "bundle_min", "bundle_max", "step", "on_value", "off_value"):
                if ent.get(k) is not None:
                    rec[k] = ent[k]
            if ent.get("enabled_default") is False:
                rec["enabled_default"] = False
            hc_uid = str(ent.get("heating_circuit_uid") or "").strip()
            if hc_uid and hc_uid in hc_tag_by_uid:
                rec["hc_tag"] = hc_tag_by_uid[hc_uid]
            out.append(rec)
    return out


def catalog_with_overlay(base_raw: Dict[str, Any], overlay: List[Dict[str, Any]]) -> DiscoveryCatalog:
    """Return a runtime catalog with bundle-derived overlay points merged in.

    Overlay metadata wins over packaged metadata (a real bundle is the more
    authoritative source for this plant); unknown addresses are added along
    with their instance tags so the sweep covers them.
    """

    raw = dict(base_raw)
    points_by_id: Dict[str, Dict[str, Any]] = {}
    for p in raw.get("points", []) or []:
        if isinstance(p, dict) and p.get("id"):
            points_by_id[str(p["id"])] = dict(p)

    for ov in overlay:
        oid = str(ov.get("id") or "")
        oa = try_decode_oa(oid)
        if oa is None:
            continue
        oid = canonical_oa(oid)
        ov = dict(ov, id=oid)
        existing = points_by_id.get(oid)
        if existing is not None:
            merged = dict(existing)
            merged.pop("diagnostic", None)
            merged.pop("enabled_default", None)
            merged.update(ov)
            merged["sources"] = sorted(set(existing.get("sources") or []) | set(ov.get("sources") or []))
            points_by_id[oid] = merged
        else:
            points_by_id[oid] = dict(ov)

    known_tags = {int(t.get("tag")) for t in raw.get("tags", []) or [] if isinstance(t, dict)}
    new_tags = list(raw.get("tags", []) or [])
    hc_tags = [int(t) for t in raw.get("hc_tags", []) or []]
    for p in points_by_id.values():
        oa = try_decode_oa(p["id"])
        if oa is None:
            continue
        if oa.instance_tag not in known_tags:
            known_tags.add(oa.instance_tag)
            new_tags.append({"tag": oa.instance_tag, "module": "bundle_import"})
        # A donated bundle can prove more heating circuits than the packaged
        # catalog knows (e.g. a 5th circuit): any circuit-tagged point with an
        # unknown circuit tag extends the circuit list so grouping and the
        # controller-name read cover it too.
        p_hc_tag = p.get("hc_tag")
        if p_hc_tag is not None and int(p_hc_tag) not in hc_tags:
            hc_tags.append(int(p_hc_tag))

    raw["points"] = [points_by_id[k] for k in sorted(points_by_id)]
    raw["tags"] = new_tags
    raw["hc_tags"] = hc_tags
    return DiscoveryCatalog(raw)
