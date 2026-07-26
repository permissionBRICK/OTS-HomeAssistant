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
import re
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

# Schedules/time programs are excluded from the integration entirely
# (spec v4 A3): the schedule object type and the schedule slot members.
SCHEDULE_OBJECT_TYPE = 8717
SCHEDULE_MEMBER_IDS = frozenset(range(514, 526))

# Enum state descriptor: member 4353 on an enum point's own object address
# answers with the star-separated state list (e.g. "Comfort*Off*Red*Norm*...");
# the index in that list IS the numeric value. Read live at scan time.
DESCRIPTOR_MEMBER_ID = 4353

# Plant identity, read live from the controller at scan time (spec v4 B1):
# the plant/model type names the HA device, the serial number identifies it
# (never a visible entity), the software version becomes sw_version.
PLANT_MODEL_OA = "BCP0c9VVAAE="  # "Anlagentyp" -> e.g. "AIRHAWK518C11A"
PLANT_SERIAL_OA = "BCOOVNVVAAE="  # "Seriennummer"
PLANT_SW_VERSION_OA = "IAABAAAAAAA="  # "Software Version" -> e.g. "v3.3.20"

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


def descriptor_oa(encoded: str) -> str:
    """OA of the enum state descriptor for a point's object address."""
    return derive_member(encoded, DESCRIPTOR_MEMBER_ID)


def is_schedule_member(member_id: int) -> bool:
    return int(member_id) in SCHEDULE_MEMBER_IDS


def is_excluded_point(oa: OA) -> bool:
    """Schedule/time-program and enum-descriptor addresses never become
    catalog points (spec v4 A3) — neither in the packaged asset nor via the
    runtime bundle-overlay path. Descriptors are read live at scan time."""
    return (
        oa.object_type == SCHEDULE_OBJECT_TYPE
        or is_schedule_member(oa.member_id)
        or oa.member_id == DESCRIPTOR_MEMBER_ID
    )


# --- v4 point classification -------------------------------------------------
# Single source of truth, shared by the catalog builder
# (tools/build_discovery_catalog.py) and the runtime bundle-overlay path, so
# a stored cloud bundle can never undo the packaged rules.

# Entities whose value is the owner's personal data, plant identity or the
# network config (spec v4 E: Kunde, Firma, Telefonnummer, E-Mailadresse,
# IP-Adresse, Subnetzmaske, Gateway, DNS, MAC, Signature part 1-3; plus the
# serial number, which identifies the device but must not be a visible
# entity). They are still created, but only as disabled diagnostic entities.
PRIVACY_NAME_PATTERNS = re.compile(
    r"(?i)\b(kunde|kundenname|customer|owner|betreiber|besitzer|telefon\w*|(?:tele)?phone|"
    r"e-?mail|mac|ip-?adresse|ip address|subnetz\w*|subnet\w*|gateway|dns|"
    r"firma|company|signature?\w*|signatur\w*|seriennummer|serial number)\b"
)

# Destructive / one-shot / service-and-commissioning style writable points:
# resets and relay tests, screed-drying program controls, manual defrost,
# error acknowledge / system unlock, and communication-parameter settings
# (baud rate, parity, stop bit, bus address, cloud connection). Per the
# project owner's decision these are STILL CREATED as writable entities, but
# entity_registry_enabled_default=False so nothing fires by accident.
SERVICE_NAME_PATTERNS = re.compile(
    r"(?i)(reset|neustart|restart|reboot|werkseinstellung|factory|format|"
    r"relais[ -]?test|relay[ -]?test|inbetriebnahme|commissioning|"
    r"program\s*start|programm\s*start|screed|estrich|austrocknung|"
    r"abtauung|defrost|acknowledge|quittier|unlock|entriegel|"
    r"stop\s*bit|baud|parit(?:y|ät)|cloud|\baddress\b|\badresse\b)"
)

# Technical-name test (owner-approved, spec v4 A4): a name with no
# whitespace that has a lowercase->uppercase transition or a run of 2+
# consecutive capitals (e.g. "CprOprHrs1", "HPMEmgyModConf", "Th-EngySumAct",
# "DHCP") is a machine symbol, not a user-facing label.
_TECH_TRANSITION = re.compile(r"[a-z][A-Z]")
_TECH_CAP_RUN = re.compile(r"[A-Z]{2}")


def is_technical_name(name: str) -> bool:
    """True when a name is a machine symbol rather than a user-facing label."""
    if not name or any(ch.isspace() for ch in name):
        return False
    return bool(_TECH_TRANSITION.search(name) or _TECH_CAP_RUN.search(name))


def apply_v4_point_flags(rec: Dict[str, Any], extra_names: Optional[List[str]] = None) -> Dict[str, Any]:
    """Apply the v4 disable/diagnostic rules to a catalog point record.

    The privacy and service/one-shot rules are matched against ALL known
    name evidence (``extra_names``: alternate labels, the bundle name, APK
    symbols), not just the chosen display name — an APK-first display name
    like "Program selection" must not mask the service evidence in its
    bundle name "Modus Austrocknungsprogramm". The technical-name test only
    looks at the chosen display name (almost every point has a technical
    symbol as an alternate). Flags are only ever strengthened (a point can
    be disabled, never re-enabled), so re-running this is safe.
    """
    name = str(rec.get("name") or "")
    evidence = [name] + [str(n) for n in (extra_names or []) if n]
    if is_technical_name(name):
        rec["technical"] = True
        rec["diagnostic"] = True
        rec["enabled_default"] = False
    if any(PRIVACY_NAME_PATTERNS.search(n) for n in evidence):
        rec["diagnostic"] = True
        rec["enabled_default"] = False
    if rec.get("write_id") and any(SERVICE_NAME_PATTERNS.search(n) for n in evidence):
        rec["enabled_default"] = False
    return rec


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

    def scan_ids_for_tags(self, tags: set[int]) -> List[str]:
        """All non-schedule read ids belonging to the given tags.

        The packaged catalog contains no schedule points (v4 membership); the
        guard only matters for overlay points from stored bundles.
        """
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
