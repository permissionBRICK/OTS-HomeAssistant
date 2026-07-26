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


# --- language-aware naming (spec: language-aware naming, empirically
# established: the controller cannot localize — jsongen ignores LNG, so the
# pump's enum strings are symbolic keys and localization lives here) --------

LANGUAGE_DE = "de"
LANGUAGE_EN = "en"

_NORM_STRIP = re.compile(r"[\s_\-]+")


def normalize_enum_token(token: str) -> str:
    """Canonical join/lookup key for a pump enum token.

    The pump's member-4353 tokens ("TiMinOff") and the APK's string-resource
    suffixes ("ti_min_off") spell the same state differently; lowercasing and
    dropping whitespace/underscores/hyphens makes the two spellings equal. A
    LEADING sign is part of the value, not spelling: signed numeric tokens
    ("-12" vs "12" in the timezone list) must never collapse to one key. A
    token that normalizes to nothing (placeholder "-") has no key.
    """
    s = str(token or "").strip()
    sign = s[:1] if s[:1] in "+-" else ""
    key = _NORM_STRIP.sub("", s[len(sign):]).lower()
    return sign + key if key else ""


def normalize_language(value: Any) -> str:
    """Collapse any language spelling ("DE", "de-AT", "German") to de/en.

    Only German and English exist as label sources; everything else is
    English (the documented default)."""
    v = str(value or "").strip().lower()
    if v.startswith("de") or v == "german":
        return LANGUAGE_DE
    return LANGUAGE_EN


def explicit_language(value: Any) -> Optional[str]:
    """de/en when the value is an explicit language choice, else None.

    Empty and "auto" (the options-flow "follow Home Assistant" choice) are
    not explicit: the caller falls through to the next source in the
    documented order (options -> per-controller setting -> HA language)."""
    v = str(value or "").strip().lower()
    if not v or v == "auto":
        return None
    return normalize_language(v)


def resolve_point_name(rec: Dict[str, Any], language: str) -> tuple[str, str]:
    """The display name for a catalog point in the user's language.

    Returns (name, source) where source is e.g. "bundle:de" for
    debuggability. The packaged record carries the English-priority choice in
    ``name``/``name_source`` (APK label > bundle > symbol) and, when the
    German bundle name differs, that name in ``name_de``/``name_de_source``;
    German preference is simply the other priority order. Identity read from
    the pump (circuit names, plant model) is applied by the caller on top and
    always wins.
    """
    name = str(rec.get("name") or rec.get("id") or "")
    source = str(rec.get("name_source") or "")
    if normalize_language(language) == LANGUAGE_DE:
        name_de = rec.get("name_de")
        if isinstance(name_de, str) and name_de:
            return name_de, f"{rec.get('name_de_source') or 'bundle'}:de"
    return name, f"{source}:en"


def resolve_enum_label(
    rec: Dict[str, Any],
    token: str,
    language: str,
    shared_labels: Optional[Dict[str, Dict[str, str]]] = None,
) -> str:
    """Localized display label for one pump enum token, falling back to the
    raw token (an option is never empty).

    German uses ONLY the point's own index-joined map (enum_labels_de): the
    bundle evidence is point-specific, so a label from another point's join
    is never applied — an unmapped token stays a raw token. English uses the
    shared APK-resource map (catalog root ``enum_token_labels``), which is
    justified per token by the <Type>IFType_<state> resources themselves.
    """
    token = str(token).strip()
    key = normalize_enum_token(token)
    if not key:
        # Placeholder tokens like "-" normalize to nothing: no lookup.
        return token
    lang = normalize_language(language)
    if lang == LANGUAGE_DE:
        own = rec.get("enum_labels_de")
        if isinstance(own, dict):
            label = own.get(key)
            if isinstance(label, str) and label:
                return label
        return token
    if shared_labels:
        shared = shared_labels.get(lang)
        if isinstance(shared, dict):
            label = shared.get(key)
            if isinstance(label, str) and label:
                return label
    return token


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


def resolve_option_labels(
    rec: Dict[str, Any],
    tokens_by_value: Dict[int, str],
    language: str,
    shared_labels: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict[int, str]:
    """value -> UNIQUE display label for a pump enum, indices preserved.

    The pump descriptor is authoritative for WHICH options exist and their
    numeric values: localization must never drop or shift one. When two
    values' localized labels collide, the colliding entries fall back to
    their distinct raw tokens; identical raw tokens (or a remaining cross-
    collision) get a deterministic " (value)" suffix — ugly beats silently
    unselectable.
    """
    localized = {
        v: resolve_enum_label(rec, tok, language, shared_labels)
        for v, tok in tokens_by_value.items()
    }
    counts: Dict[str, int] = {}
    for lab in localized.values():
        counts[lab] = counts.get(lab, 0) + 1

    out: Dict[int, str] = {}
    used: set = set()
    for v in sorted(localized):
        lab = localized[v]
        token = str(tokens_by_value[v]).strip()
        candidates = [lab] if counts[lab] == 1 else [token, lab]
        chosen = next((c for c in candidates if c and c not in used), None)
        if chosen is None:
            base = next((c for c in candidates if c), str(v))
            chosen = f"{base} ({v})"
            while chosen in used:
                chosen += "*"
        used.add(chosen)
        out[v] = chosen
    return out


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

        # Shared token->label maps (normalized-token keys) for languages
        # without per-point evidence; per-point German maps live on the
        # point records (enum_labels_de).
        etl = raw.get("enum_token_labels")
        self.enum_token_labels: Dict[str, Dict[str, str]] = {}
        if isinstance(etl, dict):
            for lang, m in etl.items():
                if isinstance(m, dict):
                    self.enum_token_labels[str(lang)] = {str(k): str(v) for k, v in m.items()}

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
