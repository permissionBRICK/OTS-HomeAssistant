"""Ochsner/Climatix object-address (OA) codec and discovery catalog loader.

An OA is the canonical Base64 encoding of exactly eight bytes:

    byte 0..1  object_type   unsigned 16-bit, little-endian
    byte 2..5  object_id     unsigned 32-bit, little-endian
    byte 6..7  member_id     unsigned 16-bit, little-endian

    object_id = (instance_tag << 16) | point_index

This module is intentionally free of Home Assistant imports so the
acceptance/check tooling under tools/ can reuse it outside HA.
"""

from __future__ import annotations

import base64
import json
import struct
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional


# Heating-circuit module: one template instantiated per circuit. The four
# known circuit instance tags, in circuit order (HC1..HC4).
HC_INSTANCE_TAGS: tuple[int, ...] = (31886, 19693, 23756, 11307)

# The circuit's display name as configured by the owner on the controller
# (live-verified: 31886->"Fussboden", 19693->"Radiatoren", 23756->"Wintergarten").
HC_NAME_OBJECT_TYPE = 8964
HC_NAME_POINT_INDEX = 61003
HC_NAME_MEMBER_ID = 256

# Schedule slot members (514..525) are excluded from the default scan.
SCHEDULE_MEMBER_IDS = frozenset(range(514, 526))

CATALOG_FILENAME = "discovery_catalog.json"


class OA(NamedTuple):
    object_type: int
    instance_tag: int
    point_index: int
    member_id: int


def _u16(name: str, value: Any) -> int:
    value = int(value)
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"{name} must be in 0..65535")
    return value


def encode_oa(object_type: int, instance_tag: int, point_index: int, member_id: int) -> str:
    object_type = _u16("object_type", object_type)
    instance_tag = _u16("instance_tag", instance_tag)
    point_index = _u16("point_index", point_index)
    member_id = _u16("member_id", member_id)
    object_id = (instance_tag << 16) | point_index
    raw = struct.pack("<HIH", object_type, object_id, member_id)
    return base64.b64encode(raw).decode("ascii")


def decode_oa(encoded: str) -> OA:
    # OAs occur both padded ("BCNL7o58AAE=") and unpadded ("BCNL7o58AAE",
    # e.g. in options override keys); accept either canonical spelling but
    # reject anything with wrong length or non-zero padding bits.
    if not isinstance(encoded, str):
        raise ValueError("an OA must be a string")
    padded = encoded + "=" * (-len(encoded) % 4) if not encoded.endswith("=") else encoded
    raw = base64.b64decode(padded, validate=True)
    if len(raw) != 8:
        raise ValueError("an OA must decode to exactly eight bytes")
    canonical = base64.b64encode(raw).decode("ascii")
    if encoded not in (canonical, canonical.rstrip("=")):
        raise ValueError("non-canonical Base64 OA")
    object_type, object_id, member_id = struct.unpack("<HIH", raw)
    return OA(
        object_type=object_type,
        instance_tag=object_id >> 16,
        point_index=object_id & 0xFFFF,
        member_id=member_id,
    )


def try_decode_oa(encoded: Any) -> Optional[OA]:
    if not isinstance(encoded, str) or not encoded:
        return None
    try:
        return decode_oa(encoded)
    except Exception:  # noqa: BLE001
        return None


def canonical_oa(encoded: str) -> str:
    """Return the canonical (padded) spelling of an OA.

    Catalog, overlay and dedupe keys are always canonicalized so a padded and
    an unpadded spelling of the same address collapse to one point.
    """
    return encode_oa(*decode_oa(encoded))


def derive_member(encoded: str, member_id: int) -> str:
    oa = decode_oa(encoded)
    return encode_oa(oa.object_type, oa.instance_tag, oa.point_index, member_id)


def retag(encoded: str, instance_tag: int) -> str:
    """Re-encode an OA with a different instance tag (template instantiation)."""
    oa = decode_oa(encoded)
    return encode_oa(oa.object_type, instance_tag, oa.point_index, oa.member_id)


def hc_name_oa(instance_tag: int) -> str:
    """OA of the owner-configured circuit name for a heating-circuit tag."""
    return encode_oa(HC_NAME_OBJECT_TYPE, instance_tag, HC_NAME_POINT_INDEX, HC_NAME_MEMBER_ID)


def is_schedule_member(member_id: int) -> bool:
    return int(member_id) in SCHEDULE_MEMBER_IDS


class DiscoveryCatalog:
    """Parsed, validated view over the packaged discovery catalog asset."""

    def __init__(self, raw: Dict[str, Any]) -> None:
        if not isinstance(raw, dict):
            raise ValueError("catalog root must be an object")
        if int(raw.get("schema_version", 0)) != 1:
            raise ValueError("unsupported catalog schema_version")

        self.catalog_version: str = str(raw.get("catalog_version") or "")
        self.hc_tags: List[int] = [int(t) for t in raw.get("hc_tags", []) or []]

        points = raw.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError("catalog has no points")

        self.points: List[Dict[str, Any]] = []
        self.points_by_id: Dict[str, Dict[str, Any]] = {}
        for p in points:
            if not isinstance(p, dict):
                continue
            oa = try_decode_oa(p.get("id"))
            if oa is None:
                raise ValueError(f"catalog point has invalid OA: {p.get('id')!r}")
            rec = dict(p)
            # Canonicalize ids at ingestion: a padded and an unpadded spelling
            # of the same address must collapse to one point.
            rec["id"] = encode_oa(*oa)
            if rec.get("write_id"):
                rec["write_id"] = canonical_oa(str(rec["write_id"]))
            rec["tag"] = oa.instance_tag
            rec["member_id"] = oa.member_id
            existing = self.points_by_id.get(rec["id"])
            if existing is not None:
                existing.update({k: v for k, v in rec.items()})
                continue
            self.points.append(rec)
            self.points_by_id[str(rec["id"])] = rec

        tags_raw = raw.get("tags")
        self.tags: List[Dict[str, Any]] = [dict(t) for t in tags_raw if isinstance(t, dict)] if isinstance(tags_raw, list) else []
        known_tags = {int(t.get("tag")) for t in self.tags if t.get("tag") is not None}
        for rec in self.points:
            if int(rec["tag"]) not in known_tags:
                raise ValueError(f"catalog point {rec['id']} uses undeclared tag {rec['tag']}")

    # -- scan helpers -----------------------------------------------------

    def probe_ids_by_tag(self, *, per_tag: int = 8) -> Dict[int, List[str]]:
        """Representative points per instance tag for the phase-A module probe.

        Prefer reference-plant points (proven to exist on real hardware at
        least once); deterministic order so scans are reproducible.
        """

        by_tag: Dict[int, List[Dict[str, Any]]] = {}
        for rec in self.points:
            if rec.get("schedule"):
                continue
            by_tag.setdefault(int(rec["tag"]), []).append(rec)

        out: Dict[int, List[str]] = {}
        for tag, recs in by_tag.items():
            recs_sorted = sorted(
                recs,
                key=lambda r: (0 if "reference" in (r.get("sources") or []) else 1, str(r["id"])),
            )
            out[tag] = [str(r["id"]) for r in recs_sorted[: max(1, int(per_tag))]]
        return out

    def scan_ids_for_tags(self, tags: set[int]) -> List[str]:
        """All non-schedule read ids belonging to the given (present) tags."""
        out: List[str] = []
        seen: set[str] = set()
        for rec in self.points:
            if rec.get("schedule"):
                continue
            if int(rec["tag"]) not in tags:
                continue
            oid = str(rec["id"])
            if oid not in seen:
                seen.add(oid)
                out.append(oid)
        return out


def catalog_path() -> Path:
    return Path(__file__).resolve().parent / "data" / CATALOG_FILENAME


@lru_cache(maxsize=1)
def load_catalog_raw() -> Dict[str, Any]:
    """Load the packaged catalog JSON once per process."""
    with catalog_path().open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def load_catalog() -> DiscoveryCatalog:
    """Load and validate the packaged catalog once per process."""
    return DiscoveryCatalog(load_catalog_raw())
