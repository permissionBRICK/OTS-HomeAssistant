"""Plant roles and capabilities, as the OCHSNER Smart app sees a plant.

The app (com.ochsner.app 1.2.0) has no per-model code: it adapts purely from
a handful of availability bitfields and reads/writes a fixed set of
datapoints. This module is the hand-maintained table of those datapoints,
grouped by ROLE KEY (``hp.outdoor``, ``hc1.badge``, ``dhw.program``, ...),
plus the app's rules on top of them:

- capabilities (which sections a plant has) from the availability points;
- circuit state mapping and the exact write sequences of the app's circuit
  and DHW actions (used by the climate / water_heater / button entities);
- supplement entities: writable points the app uses that the packaged
  discovery catalog does not expose as writable entities.

Sources: decompiled app (``p045d8/v.java`` circuit actions, ``p045d8/z.java``
DHW actions, ``p045d8/C.java`` pool actions, ``B7/x|A|D|G.java`` per-circuit
control screens, ``H7/*`` statistics DTOs, ``G2/m.java`` overview reads) and
read-only reads on a real AirHawk 518.

Datapoint ids are the controller's base64 object addresses (see catalog.py
for the codec). Most per-circuit ids are the HC1 id re-tagged with the
circuit's instance tag; the ones that are NOT (the app reads a different
point per circuit) are listed explicitly in ``_HC_EXPLICIT``.

No Home Assistant imports here: tests load this module HA-free.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from .catalog import (
    HC_INSTANCE_TAGS,
    canonical_oa,
    encode_oa,
    normalize_language,
    resolve_enum_label,
    retag as _retag,
    try_decode_oa,
)

# --- ids ---------------------------------------------------------------------


def make_id(object_type: int, tag: int, point_index: int, member: int) -> str:
    """Base64 OA of little-endian ``<HIH``: objectType, (tag<<16)|pointIndex, member."""
    return encode_oa(object_type, tag, point_index, member)


def retag(oid: str, tag: int) -> str:
    """The same datapoint on another module instance (e.g. another circuit)."""
    return _retag(oid, tag)


# Circuit instance tags, HC1..HC4.
HC_TAGS: Tuple[int, ...] = tuple(HC_INSTANCE_TAGS)
HC_COUNT = len(HC_TAGS)

# Per-circuit roles, given as the HC1 id; HC2..4 are the same point re-tagged.
HC_TEMPLATE: Dict[str, str] = {
    "name": "BCNL7o58AAE=",
    # member 290: the user's operating-program selection (app writes 0 on / 1 off)
    "op_program": "AiIQvY58IgE=",
    # member 256: resulting (present) program, read-only
    "op_program_present": "AiIQvY58AAE=",
    "comfort_eco": "AiPKUI58AAE=",
    "season": "AiMN7I58AAE=",
    "room_offset": "ACNZw458AAE=",
    "target_selector": "CyP/dY58AAE=",
    "badge": "CyPmL458AAE=",
    "status": "CyMogo58AAE=",
    "heat_limit_status": "CyO9/o58AAE=",
    "cool_limit_status": "CyNm0I58AAE=",
    "room_target_heat": "CiPcW458AAE=",
    "room_target_cool": "CiO36o58AAE=",
    "flow_target": "ACJz7Y58AAE=",
    "setpoint_normal_heat": "ASMhEo58AAE=",
    "setpoint_reduced_heat": "ASOkTo58AAE=",
    "setpoint_normal_cool": "ASNKo458AAE=",
    "setpoint_reduced_cool": "ASPP/458AAE=",
    "setpoint_frost_room": "ASOcQo58AAE=",
    "heat_curve_base_outdoor": "ACPl8458AAE=",
    "heat_curve_base_flow": "ACPUwI58AAE=",
    "heat_curve_norm_outdoor": "ACOGw458AAE=",
    "heat_curve_norm_flow": "ACO38I58AAE=",
    "heat_curve_max_flow": "ACM5mY58AAE=",
    "heat_limit": "ACM8vI58AAE=",
    "heat_curve_ref_room": "ACNx1I58AAE=",
    "cool_curve_base_outdoor": "ACN5nI58AAE=",
    "cool_curve_base_flow": "ACNIr458AAE=",
    "cool_curve_norm_outdoor": "ACMarI58AAE=",
    "cool_curve_norm_flow": "ACMrn458AAE=",
    "cool_limit": "ACPdII58AAE=",
    "cool_curve_ref_room": "ACNxpY58AAE=",
    "party_on": "AiMMLY58AAE=",
    "party_hours": "ACMqxI58AAE=",
    "party_temp": "ACOd7I58AAE=",
}

# Per-circuit roles whose point differs per circuit (not a re-tag). HC1..HC4;
# None = unknown/not present for that circuit. room_target_current, room
# temperature and humidity are what the app's circuit screens read
# (B7/x, A, D, G.java); dew point and IO points from the circuit detail
# screens (H7/C0693h0, C0709p0, C0724x0, F0.java); the manual setpoint from
# the packaged catalog (hc_tag membership).
_HC_EXPLICIT: Dict[str, Tuple[Optional[str], ...]] = {
    "availability": ("ESNtpAAAAAE=", "ESMOlAAAAAE=", "ESMvhAAAAAE=", "ESPI9AAAAAE="),
    "room_target_current": ("CiMeXI58AAE=", "CiPkJO1MAAE=", "CiOt/MxcAAE=", "CiMQ1SssAAE="),
    "room_temp": ("AyI4FSiJAAE=", "AyJN3SiJAAE=", "AyKemiiJAAE=", "AyKGXSiJAAE="),
    "humidity": ("AyJLCiiJAAE=", "AyKEuyiJAAE=", "AyLB1CiJAAE=", "AyI7yCiJAAE="),
    "dew_point": ("CiMeMY58AAE=", "CiP+/+1MAAE=", "CiNeusxcAAE=", "CiMfcissAAE="),
    "setpoint_manual": ("ASMXeI58AAE=", "ASPYye1MAAE=", "ASOdpsxcAAE=", None),
    "flow_actual": ("AyKvbyiJAAE=", "AyLrQiiJAAE=", "AyLIqSiJAAE=", "AyJjGCiJAAE="),
    "mixer": ("BiJEkiiJSjA=", "BiIAvyiJSjA=", "BiIjVCiJSjA=", "BiKI5SiJSjA="),
    "pump": ("ByIPECiJAAE=", "ByJ62CiJAAE=", "ByKpnyiJAAE=", "ByKxWCiJAAE="),
}

# Plant availability bitfields / gates.
AVAIL_ROLES: Dict[str, str] = {
    "avail.bms": "ESNHDAAAAAE=",
    "avail.dhw": "ESMSIwAAAAE=",
    "avail.buffer": "AiPN8gAAAAE=",
    "avail.buffer_aux": "ESNbHQAAAAE=",
    "avail.aux": "ESM/0gAAAAE=",
    "avail.pool": "AiM3dQAAAAE=",
    "avail.silent": "AiPMsAAAAAE=",
    "avail.emergency": "AiMR5AAAAAE=",
    "avail.ext_dhw": "AiP/JgAAAAE=",
    "avail.ext_buffer": "AiNnNAAAAAE=",
}

# Heat pump / plant roles (always present unless the point is absent).
HP_ROLES: Dict[str, str] = {
    "hp.outdoor": "AyKWuiiJAAE=",
    "hp.outdoor_avg": "ACMZwiiJAAE=",
    "hp.flow": "AyJZqiiJAAE=",
    "hp.return": "AyILsCiJAAE=",
    "hp.flow_rate": "AyIZciiJAAE=",
    "hp.buffer_top": "AyJn4iiJAAE=",
    "hp.status": "CyNmeMdZAAE=",
    "hp.system_status": "CyMOIMdZAAE=",
    "hp.program": "AiMu5MdZAAE=",
    "hp.aux_program": "AiNiMcdZAAE=",
    "hp.aux_status": "CyM4OMdZAAE=",
    "hp.aux_flow": "AyIkniiJAAE=",
    "hp.silent_program": "AiP5J8dZAAE=",
    "hp.heat_output": "CiO6JyiJAAE=",
    "hp.el_power": "CiPAhCiJAAE=",
    "hp.pressure": "AyKaCCiJAAE=",
    # Heat pump cycles today. The other readable "Schaltzyklen heute"
    # (AyNQYSiJAAE=) is the auxiliary heater's: the app reads it as
    # AdditionalHeatingStatistic.cyclesToday (H7/C0721w.java).
    "hp.cycles_today": "AyMePiiJAAE=",
    "hp.cycles_total": "AyNkcSiJAAE=",
    "hp.defrosts_today": "AyNkR4r8AAE=",
    "hp.defrosts_total": "AyOVHIr8AAE=",
    # Compressor operating hours (app: heat pump statistics, H7/L0.java).
    "hp.hours_total": "AyOvsSiJAAE=",
    "hp.energy_heat_total": "AyMXFSiJAAE=",
    "hp.energy_heat_year": "DCNBAiiJAAE=",
    "hp.energy_el_year": "DCP7vSiJAAE=",
    "hp.energy_heat_prev_year": "DCPOmiiJAAE=",
    "hp.energy_el_prev_year": "DCM2oyiJAAE=",
    "hp.jaz_year": "CiMUVyiJAAE=",
    "hp.jaz_prev_year": "CiM6siiJAAE=",
    "hp.sum_fault": "BSLZ1IwCAAE=",
    "hp.dhw_valve": "ByIDxCiJAAE=",
    "hp.capability": "ESO6g8dZAAE=",
}

DHW_ROLES: Dict[str, str] = {
    "dhw.temp": "AyKOLyiJAAE=",
    "dhw.target": "CiMzqnXZAAE=",
    "dhw.status": "CyMognXZAAE=",
    "dhw.program": "AiIQvXXZIgE=",
    "dhw.comfort_eco": "AiPKUHXZAAE=",
    "dhw.boost": "AiOzyHXZAAE=",
    "dhw.offset": "ACPOwnXZAAE=",
    "dhw.setpoint_comfort": "ASMv1XXZAAE=",
    "dhw.setpoint_eco": "ASOp1XXZAAE=",
    "dhw.setpoint_reduced": "ASOXCHXZAAE=",
    "dhw.setpoint_boost": "ASPuhXXZAAE=",
}

# Pool module (tag 56386), from the app's pool tile (B7/Q.java) and actions
# (p045d8/C.java: ToggleOff writes mode 1, ToggleOn writes mode 2).
POOL_ROLES: Dict[str, str] = {
    "pool.mode": "AiIQvULcIgE=",
    "pool.setpoint": "ASPmx0LcAAE=",
    "pool.status": "CyMogkLcAAE=",
}

# Default display units for roles whose entity carries none.
ROLE_UNITS: Dict[str, str] = {
    "hp.outdoor": "°C", "hp.outdoor_avg": "°C", "hp.flow": "°C", "hp.return": "°C",
    "hp.buffer_top": "°C", "hp.aux_flow": "°C", "hp.flow_rate": "m³/h",
    "hp.heat_output": "kW", "hp.el_power": "kW", "hp.pressure": "bar",
    "hp.hours_total": "h",
    "dhw.temp": "°C", "dhw.target": "°C", "dhw.offset": "K",
    "dhw.setpoint_comfort": "°C", "dhw.setpoint_eco": "°C",
    "dhw.setpoint_reduced": "°C", "dhw.setpoint_boost": "°C",
    "pool.setpoint": "°C",
}
_HC_UNITS: Dict[str, str] = {
    "room_offset": "K", "room_target_current": "°C", "room_target_heat": "°C",
    "room_target_cool": "°C", "flow_target": "°C", "dew_point": "°C",
    "room_temp": "°C", "humidity": "%", "flow_actual": "°C", "mixer": "%",
    "party_temp": "°C", "party_hours": "h",
}


def hc_key(n: int, key: str) -> str:
    return f"hc{int(n)}.{key}"


def circuit_role_ids(n: int) -> Dict[str, str]:
    """Role key -> id for heating circuit ``n`` (1-based)."""
    if not 1 <= int(n) <= HC_COUNT:
        raise ValueError(f"heating circuit {n} out of range")
    tag = HC_TAGS[int(n) - 1]
    out: Dict[str, str] = {}
    for key, hc1 in HC_TEMPLATE.items():
        out[hc_key(n, key)] = hc1 if n == 1 else retag(hc1, tag)
    for key, ids in _HC_EXPLICIT.items():
        oid = ids[int(n) - 1]
        if oid:
            out[hc_key(n, key)] = oid
    return out


def hc_availability_id(n: int) -> str:
    return _HC_EXPLICIT["availability"][int(n) - 1]  # type: ignore[return-value]


def role_unit(role: str) -> Optional[str]:
    if role in ROLE_UNITS:
        return ROLE_UNITS[role]
    if role.startswith("hc") and "." in role:
        key = role.split(".", 1)[1]
        if key in _HC_UNITS:
            return _HC_UNITS[key]
        if "curve" in key or key.startswith(("setpoint_", "heat_limit", "cool_limit")):
            if not key.endswith("_status"):
                return "°C"
    if role.startswith("hp.energy_"):
        return "kWh"
    return None


# --- values -------------------------------------------------------------------


def first_value(values: Mapping[str, Any], oid: Optional[str]) -> Any:
    """The coordinator's first element for an id (``[v, v]`` -> v), else None."""
    if not oid or not isinstance(values, Mapping):
        return None
    raw = values.get(oid)
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw


def as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, str) and not value.strip():
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return int(round(f))


def as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, str):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _bits(values: Mapping[str, Any], oid: str) -> int:
    """A bitfield value; missing / absent (state 5) counts as 0."""
    return as_int(first_value(values, oid)) or 0


# --- capabilities (B3) --------------------------------------------------------


def capability_ids() -> List[str]:
    """Ids the capability computation needs (always polled)."""
    ids = list(AVAIL_ROLES.values())
    ids += [hc_availability_id(n) for n in range(1, HC_COUNT + 1)]
    ids += [HP_ROLES["hp.el_power"], HP_ROLES["hp.heat_output"], HP_ROLES["hp.capability"]]
    return ids


def compute_capabilities(values: Mapping[str, Any]) -> Dict[str, Any]:
    """The app's section rules. Returns plant capabilities + per-circuit flags.

    ``{"bms", "dhw", "buffer", "buffer_temp", "aux", "aux_temp",
    "buffer_aux_heater", "pool", "silent", "emergency", "power", "cooling",
    "circuits": [{"index", "heat", "cool", "room_sensor", "humidity"}]}``
    """
    a = {k: _bits(values, oid) for k, oid in AVAIL_ROLES.items()}
    bms = a["avail.bms"] != 0
    circuits: List[Dict[str, Any]] = []
    if not bms:
        for n in range(1, HC_COUNT + 1):
            bits = _bits(values, hc_availability_id(n))
            if not bits & 1:
                continue
            circuits.append(
                {
                    "index": n,
                    "heat": bool(bits & 2),
                    "cool": bool(bits & 4),
                    "room_sensor": bool(bits & 32),
                    "humidity": bool(bits & 64),
                }
            )
    power = (
        first_value(values, HP_ROLES["hp.el_power"]) is not None
        and first_value(values, HP_ROLES["hp.heat_output"]) is not None
    )
    return {
        "bms": bms,
        "dhw": (not bms) and bool(a["avail.dhw"] & 256),
        "buffer": a["avail.buffer"] > 0,
        "buffer_temp": bool(a["avail.buffer_aux"] & 1024),
        "aux": bool(a["avail.aux"] & (16 | 32)),
        "aux_temp": bool(a["avail.aux"] & 2048),
        "buffer_aux_heater": bool(a["avail.buffer_aux"] & 4),
        "pool": (not bms) and a["avail.pool"] > 0,
        "silent": a["avail.silent"] == 1,
        "emergency": a["avail.emergency"] == 1,
        "power": power,
        "cooling": any(c["cool"] for c in circuits),
        "circuits": circuits,
    }


def public_capabilities(caps: Mapping[str, Any]) -> Dict[str, bool]:
    """The websocket ``capabilities`` object (no circuit list)."""
    return {k: bool(v) for k, v in caps.items() if k != "circuits"}


def presence_signature(caps: Mapping[str, Any]) -> Tuple[Any, ...]:
    """Changes when the role/entity layout would change (circuits, DHW, pool)."""
    return (
        tuple((c["index"], c["heat"], c["cool"], c["room_sensor"], c["humidity"]) for c in caps.get("circuits", [])),
        bool(caps.get("dhw")),
        bool(caps.get("pool")),
    )


def role_ids(caps: Mapping[str, Any]) -> Dict[str, str]:
    """Role key -> id for every role of a present section.

    Heat pump and availability roles are always included (circuit
    availability for all four circuits, so a newly enabled circuit is seen).
    """
    out: Dict[str, str] = {}
    out.update(HP_ROLES)
    out.update(AVAIL_ROLES)
    for n in range(1, HC_COUNT + 1):
        out[hc_key(n, "availability")] = hc_availability_id(n)
    for c in caps.get("circuits", []) or []:
        out.update(circuit_role_ids(int(c["index"])))
    if caps.get("dhw"):
        out.update(DHW_ROLES)
    if caps.get("pool"):
        out.update(POOL_ROLES)
    return out


def role_values(values: Mapping[str, Any], ids_by_role: Mapping[str, str]) -> Dict[str, Any]:
    """Role key -> first value; absent roles are omitted, text is cleaned."""
    out: Dict[str, Any] = {}
    for role, oid in ids_by_role.items():
        v = first_value(values, oid)
        if v is None:
            continue
        if isinstance(v, str):
            v = clean_text(v)
            if v is None:
                continue
        out[role] = v
    return out


def clean_text(value: Any) -> Optional[str]:
    """Usable controller text: mojibake repaired, not a ``#`` placeholder."""
    if not isinstance(value, str):
        return None
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        repaired = value
    repaired = repaired.strip()
    if not repaired or repaired.startswith("#"):
        return None
    return repaired


# --- badge (B3) -------------------------------------------------------------

BADGES: Tuple[str, ...] = (
    "off_grey",
    "fault_orange",
    "auto_comfort_grey",
    "auto_comfort_red",
    "auto_comfort_blue",
    "auto_eco_grey",
    "auto_eco_red",
    "auto_eco_blue",
    "heat_comfort_grey",
    "heat_comfort_red",
    "heat_eco_grey",
    "heat_eco_red",
    "cool_comfort_grey",
    "cool_comfort_blue",
    "cool_eco_grey",
    "cool_eco_blue",
    "off_frost_red",
    "vacation_grey",
    "vacation_red",
    "party_grey",
    "party_red",
    "party_blue",
    "vacation_blue",
)


def badge_name(value: Any) -> str:
    v = as_int(value)
    if v is None or not 0 <= v < len(BADGES):
        return "unknown"
    return BADGES[v]


def hvac_action_from_badge(value: Any) -> Optional[str]:
    """HA hvac_action string for a badge value (None = unknown)."""
    name = badge_name(value)
    if name == "unknown":
        return None
    if name == "off_grey":
        return "off"
    if name == "fault_orange":
        return "idle"
    if name.endswith("_red"):
        return "heating"
    if name.endswith("_blue"):
        return "cooling"
    return "idle"


# --- enum labels ------------------------------------------------------------

# Bilingual labels for roles the integration knows better than the catalog
# (supplements, app-only points, members the catalog lacks). raw -> (de, en).
_LABELS: Dict[str, Dict[int, Tuple[str, str]]] = {
    "op_program": {
        0: ("Komfort", "Comfort"),
        1: ("Aus", "Off"),
        2: ("Reduziert", "Reduced"),
        3: ("Normalbetrieb", "Standard mode"),
        4: ("Handbetrieb Heizen", "Manual heating"),
        5: ("Handbetrieb Kühlen", "Manual cooling"),
        6: ("Eco", "Eco"),
        7: ("Party", "Party"),
        8: ("Urlaub", "Holiday"),
    },
    "comfort_eco": {0: ("Komfort", "Comfort"), 1: ("Eco", "Eco")},
    "season": {
        0: ("Automatik", "Automatic"),
        1: ("Nach Datum", "By date"),
        2: ("Sommer (nur Kühlen)", "Summer (cooling only)"),
        3: ("Winter (nur Heizen)", "Winter (heating only)"),
        4: ("Automatik (Heizen/Kühlen)", "Automatic (heating/cooling)"),
    },
    "target_selector": {
        0: ("Raum Heizen", "Room heating"),
        1: ("Raum Kühlen", "Room cooling"),
        2: ("Sollwert Heizen", "Setpoint heating"),
        3: ("Sollwert Kühlen", "Setpoint cooling"),
    },
    "dhw.program": {0: ("Ein", "On"), 1: ("Aus", "Off")},
    "dhw.comfort_eco": {1: ("Eco", "Eco"), 2: ("Komfort", "Comfort")},
    "dhw.boost": {0: ("Aus", "Off"), 1: ("Boost", "Boost")},
    "hp.program": {
        0: ("Automatik", "Automatic"),
        1: ("Handbetrieb Heizen", "Manual heating"),
        2: ("Handbetrieb Kühlen", "Manual cooling"),
        3: ("Abgeschaltet", "Switched off"),
    },
    "hp.sum_fault": {
        0: ("Null", "None"),
        1: ("Störungsfrei", "OK"),
        2: ("Sperre aus", "Lock off"),
        3: ("Sperre ein", "Lock on"),
        4: ("Störung aus", "Fault off"),
        5: ("Störung ein", "Fault on"),
        6: ("Alarm aus", "Alarm off"),
        7: ("Alarm ein", "Alarm on"),
        8: ("Info aus", "Info off"),
        9: ("Info ein", "Info on"),
    },
    "pool.mode": {
        0: ("Automatik", "Automatic"),
        1: ("Aus", "Off"),
        2: ("Ein", "On"),
        3: ("Urlaub", "Holiday"),
    },
}


def _label_table_key(role: str) -> str:
    if role.startswith("hc") and "." in role:
        key = role.split(".", 1)[1]
        if key == "op_program_present":
            return "op_program"
        return key
    return role


def builtin_labels(role: str, language: str) -> Optional[Dict[str, str]]:
    """raw value (str) -> label for roles with a hand-maintained table."""
    table = _LABELS.get(_label_table_key(role))
    if table is None:
        return None
    idx = 0 if normalize_language(language) == "de" else 1
    return {str(v): labels[idx] for v, labels in sorted(table.items())}


def catalog_labels(
    rec: Optional[Mapping[str, Any]],
    language: str,
    shared_labels: Optional[Mapping[str, Mapping[str, str]]] = None,
    tokens: Optional[List[str]] = None,
) -> Optional[Dict[str, str]]:
    """raw value (str) -> label from a catalog point record.

    Uses the pump's descriptor tokens when known (stored per discovery
    entity), else numeric-keyed catalog labels, else the record's own
    value_map/options (German), so a label is never empty.
    """
    if not rec:
        return None
    lang = normalize_language(language)
    if tokens:
        out = {}
        for idx, tok in enumerate(tokens):
            tok = str(tok).strip()
            if tok:
                out[str(idx)] = resolve_enum_label(dict(rec), tok, lang, dict(shared_labels or {}))
        if out:
            return out
    other = "en" if lang == "de" else "de"
    numeric: Dict[str, str] = {}
    for cand in (other, lang):  # requested language wins
        m = rec.get(f"enum_labels_{cand}")
        if isinstance(m, Mapping):
            for k, v in m.items():
                if as_int(k) is not None and str(as_int(k)) == str(k) and v:
                    numeric[str(k)] = str(v)
    if numeric:
        return dict(sorted(numeric.items(), key=lambda kv: int(kv[0])))
    vm = rec.get("value_map")
    if isinstance(vm, Mapping) and vm:
        return {str(k): str(v) for k, v in vm.items()}
    opts = rec.get("options")
    if isinstance(opts, Mapping) and opts:
        out = {}
        for label, v in opts.items():
            iv = as_int(v)
            if iv is not None:
                out.setdefault(str(iv), str(label))
        return dict(sorted(out.items(), key=lambda kv: int(kv[0]))) or None
    return None


# --- circuit (climate) logic (B4) --------------------------------------------

HVAC_OFF = "off"
HVAC_HEAT = "heat"
HVAC_COOL = "cool"
HVAC_HEAT_COOL = "heat_cool"

# App: SetWinter = heat only (3), SetSummer = cool only (2), SetAuto (4).
SEASON_FOR_MODE = {HVAC_HEAT: 3, HVAC_COOL: 2, HVAC_HEAT_COOL: 4}
PRESET_VALUES = {"comfort": 0, "eco": 1}

OFFSET_LIMIT = 3.0
OFFSET_STEP = 0.5


def hvac_modes(heat: bool, cool: bool) -> List[str]:
    modes = [HVAC_OFF]
    if heat or not cool:
        modes.append(HVAC_HEAT)
    if cool:
        modes.append(HVAC_COOL)
    if heat and cool:
        modes.append(HVAC_HEAT_COOL)
    return modes


def hvac_mode(op_program: Any, season: Any, heat: bool, cool: bool) -> Optional[str]:
    op = as_int(op_program)
    if op is None:
        return None
    if op == 1:
        return HVAC_OFF
    modes = hvac_modes(heat, cool)
    if op == 4:
        mode = HVAC_HEAT
    elif op == 5:
        mode = HVAC_COOL
    elif cool:
        s = as_int(season)
        mode = {3: HVAC_HEAT, 2: HVAC_COOL}.get(s, HVAC_HEAT_COOL) if s is not None else HVAC_HEAT_COOL
    else:
        mode = HVAC_HEAT
    if mode not in modes:
        mode = next(m for m in modes if m != HVAC_OFF)
    return mode


def preset_mode(comfort_eco: Any) -> Optional[str]:
    v = as_int(comfort_eco)
    for name, raw in PRESET_VALUES.items():
        if v == raw:
            return name
    return None


Write = Tuple[str, Any]


def _differs(current: Any, desired: Any) -> bool:
    """Write unless the current reading equals the desired value (unknown -> write)."""
    if current is None:
        return True
    try:
        return abs(float(current) - float(desired)) > 1e-6
    except (TypeError, ValueError):
        return str(current) != str(desired)


def plan_hvac_mode(mode: str, values: Mapping[str, Any], ids: Mapping[str, str], cool: bool) -> List[Write]:
    """Writes for climate.set_hvac_mode (app ToggleOff / ToggleOn / SetWinter/Summer/Auto)."""
    op_id = ids["op_program"]
    if mode == HVAC_OFF:
        return [(op_id, 1)] if _differs(first_value(values, op_id), 1) else []
    if mode not in SEASON_FOR_MODE:
        raise ValueError(f"unsupported hvac mode {mode}")
    out: List[Write] = []
    if _differs(first_value(values, op_id), 0):
        out.append((op_id, 0))
    season_id = ids.get("season")
    if cool and season_id:
        season = SEASON_FOR_MODE[mode]
        if _differs(first_value(values, season_id), season):
            out.append((season_id, season))
    return out


def plan_turn_on(values: Mapping[str, Any], ids: Mapping[str, str]) -> List[Write]:
    op_id = ids["op_program"]
    return [(op_id, 0)] if _differs(first_value(values, op_id), 0) else []


def plan_preset(preset: str, values: Mapping[str, Any], ids: Mapping[str, str]) -> List[Write]:
    """Writes for climate.set_preset_mode (app SetComfort / SetEco)."""
    if preset not in PRESET_VALUES:
        raise ValueError(f"unsupported preset {preset}")
    out: List[Write] = []
    op_id = ids["op_program"]
    if _differs(first_value(values, op_id), 0):
        out.append((op_id, 0))
    ce_id = ids["comfort_eco"]
    if _differs(first_value(values, ce_id), PRESET_VALUES[preset]):
        out.append((ce_id, PRESET_VALUES[preset]))
    return out


def round_half(value: float) -> float:
    """Round to the nearest 0.5 (halves away from the lower step)."""
    return math.floor(float(value) * 2.0 + 0.5) / 2.0


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def circuit_target(values: Mapping[str, Any], ids: Mapping[str, str]) -> Optional[float]:
    """The circuit's displayed room target (B4)."""
    sel = as_int(first_value(values, ids.get("target_selector")))
    if sel in (0, 2):
        t = as_float(first_value(values, ids.get("room_target_heat")))
    elif sel in (1, 3):
        t = as_float(first_value(values, ids.get("room_target_cool")))
    else:
        t = None
    if t is None:
        t = as_float(first_value(values, ids.get("room_target_current")))
    return t


def offset_bounds(target: Optional[float], offset: Optional[float]) -> Optional[Tuple[float, float]]:
    """(min_temp, max_temp) a target can be moved to via the ±3 K offset."""
    if target is None:
        return None
    base = target - (offset or 0.0)
    return (base - OFFSET_LIMIT, base + OFFSET_LIMIT)


def new_offset(requested: float, target: float, offset: Optional[float]) -> float:
    """Offset that moves the current target to ``requested`` (app −/+ math)."""
    cur = offset or 0.0
    out = clamp(round_half(cur + float(requested) - float(target)), -OFFSET_LIMIT, OFFSET_LIMIT)
    return 0.0 if out == 0 else out


def plan_temperature(requested: float, values: Mapping[str, Any], ids: Mapping[str, str]) -> List[Write]:
    target = circuit_target(values, ids)
    if target is None:
        raise ValueError("circuit target temperature unknown")
    off_id = ids["room_offset"]
    cur = as_float(first_value(values, off_id))
    new = new_offset(requested, target, cur)
    return [(off_id, new)] if _differs(cur, new) else []


# Dependent reads after circuit writes (the controller recomputes these).
CIRCUIT_DEPENDENTS = (
    "op_program", "op_program_present", "comfort_eco", "season", "room_offset",
    "badge", "status", "target_selector", "room_target_current",
    "room_target_heat", "room_target_cool", "flow_target",
)


# --- DHW (water_heater) logic (B5) --------------------------------------------

DHW_OFF = "off"
DHW_ECO = "eco"
DHW_PERFORMANCE = "performance"  # Komfort
DHW_HIGH_DEMAND = "high_demand"  # Boost
DHW_OPERATIONS = [DHW_OFF, DHW_ECO, DHW_PERFORMANCE, DHW_HIGH_DEMAND]

DHW_COMFORT_VALUE = 2
DHW_ECO_VALUE = 1
DHW_OFFSET_STEP = 3.0
DHW_OFFSET_LIMIT = 9.0


def dhw_operation(values: Mapping[str, Any]) -> Optional[str]:
    program = as_int(first_value(values, DHW_ROLES["dhw.program"]))
    if program is None:
        return None
    if program == 1:
        return DHW_OFF
    if as_int(first_value(values, DHW_ROLES["dhw.boost"])) == 1:
        return DHW_HIGH_DEMAND
    if as_int(first_value(values, DHW_ROLES["dhw.comfort_eco"])) == DHW_ECO_VALUE:
        return DHW_ECO
    return DHW_PERFORMANCE


def _dhw_sequence(desired: List[Write], values: Mapping[str, Any]) -> List[Write]:
    return [(oid, v) for oid, v in desired if _differs(first_value(values, oid), v)]


def plan_dhw_operation(operation: str, values: Mapping[str, Any]) -> List[Write]:
    """Writes for water_heater.set_operation_mode (app DHW actions, z.java)."""
    program = DHW_ROLES["dhw.program"]
    ce = DHW_ROLES["dhw.comfort_eco"]
    boost = DHW_ROLES["dhw.boost"]
    if operation == DHW_OFF:  # ToggleOff
        desired = [(program, 1)]
    elif operation == DHW_ECO:  # SetEco
        desired = [(program, 0), (ce, DHW_ECO_VALUE), (boost, 0)]
    elif operation == DHW_PERFORMANCE:  # SetComfort
        desired = [(program, 0), (ce, DHW_COMFORT_VALUE), (boost, 0)]
    elif operation == DHW_HIGH_DEMAND:  # Boost
        desired = [(program, 0), (boost, 1)]
    else:
        raise ValueError(f"unsupported operation {operation}")
    return _dhw_sequence(desired, values)


def plan_dhw_on(values: Mapping[str, Any]) -> List[Write]:
    return _dhw_sequence([(DHW_ROLES["dhw.program"], 0)], values)


def plan_dhw_boost(values: Mapping[str, Any]) -> List[Write]:
    """Boost button: program on if needed, boost <- 1 (one-time charge)."""
    return _dhw_sequence([(DHW_ROLES["dhw.program"], 0), (DHW_ROLES["dhw.boost"], 1)], values)


def dhw_setpoint_role(operation: Optional[str]) -> str:
    if operation == DHW_ECO:
        return "dhw.setpoint_eco"
    if operation == DHW_HIGH_DEMAND:
        return "dhw.setpoint_boost"
    return "dhw.setpoint_comfort"


DHW_DEPENDENTS = (
    "dhw.program", "dhw.comfort_eco", "dhw.boost", "dhw.offset", "dhw.target", "dhw.status",
)


# --- supplement entities (B2) ---------------------------------------------------

# Writable points the app uses that the packaged catalog does not expose as a
# writable entity. Generated at runtime (never persisted) and only when the
# point answers.
_SUPPLEMENT_NAMES = {
    "comfort_eco": ("Komfort/Eco", "Comfort/Eco"),
    "room_offset": ("Raumsollwert-Korrektur", "Room setpoint correction"),
    "dhw.offset": ("Warmwasser-Korrektur", "DHW correction"),
    "dhw.comfort_eco": ("Warmwasser Komfort/Eco", "DHW comfort/eco"),
}


def _sup_name(key: str, language: str) -> str:
    de, en = _SUPPLEMENT_NAMES[key]
    return de if normalize_language(language) == "de" else en


def _sup_options(table: str, language: str) -> Dict[str, int]:
    labels = builtin_labels(table, language) or {}
    return {label: int(raw) for raw, label in labels.items()}


def canonical_id(oid: Any) -> str:
    """Padding-independent id key (padded and unpadded spellings match)."""
    s = str(oid or "").strip()
    return canonical_oa(s) if try_decode_oa(s) is not None else s


WRITABLE_PLATFORM_KEYS = ("numbers", "selects", "texts", "switches")


def writable_read_ids(entities_by_platform: Mapping[str, Iterable[Mapping[str, Any]]]) -> set:
    out = set()
    for key in WRITABLE_PLATFORM_KEYS:
        for ent in entities_by_platform.get(key, []) or []:
            if isinstance(ent, Mapping):
                rid = canonical_id(ent.get("read_id") or ent.get("id"))
                if rid:
                    out.add(rid)
    return out


def supplement_entities(
    *,
    values: Mapping[str, Any],
    caps: Mapping[str, Any],
    existing: Mapping[str, Iterable[Mapping[str, Any]]],
    circuit_devices: Mapping[int, Tuple[str, str]],
    language: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Runtime entity configs for supplement datapoints.

    ``circuit_devices`` maps circuit index -> (heating_circuit_uid, name) so
    supplements join the circuit device the circuit's other entities use.
    Deduplicated by read id against the stored WRITABLE entities (a read-only
    catalog sensor on the same id does not block the writable control,
    matching the discovery merge rule).
    """
    taken = writable_read_ids(existing)
    out: Dict[str, List[Dict[str, Any]]] = {"numbers": [], "selects": []}

    def answers(oid: str) -> bool:
        return first_value(values, oid) is not None

    for c in caps.get("circuits", []) or []:
        n = int(c["index"])
        ids = circuit_role_ids(n)
        uid, hc_name = circuit_devices.get(n, (f"tag:{HC_TAGS[n - 1]}", ""))
        group = {"heating_circuit_uid": uid, "heating_circuit_name": hc_name or default_circuit_name(n, language)}
        oid = ids[hc_key(n, "comfort_eco")]
        if answers(oid) and canonical_id(oid) not in taken:
            out["selects"].append(
                {
                    "name": _sup_name("comfort_eco", language),
                    "read_id": oid,
                    "write_id": oid,
                    "options": _sup_options("comfort_eco", language),
                    "supplement": hc_key(n, "comfort_eco"),
                    **group,
                }
            )
            taken.add(canonical_id(oid))
        oid = ids[hc_key(n, "room_offset")]
        if answers(oid) and canonical_id(oid) not in taken:
            out["numbers"].append(
                {
                    "name": _sup_name("room_offset", language),
                    "read_id": oid,
                    "write_id": oid,
                    "unit": "K",
                    "min": -OFFSET_LIMIT,
                    "max": OFFSET_LIMIT,
                    "bundle_min": -OFFSET_LIMIT,
                    "bundle_max": OFFSET_LIMIT,
                    "step": OFFSET_STEP,
                    "supplement": hc_key(n, "room_offset"),
                    **group,
                }
            )
            taken.add(canonical_id(oid))

    if caps.get("dhw"):
        oid = DHW_ROLES["dhw.offset"]
        if answers(oid) and canonical_id(oid) not in taken:
            out["numbers"].append(
                {
                    "name": _sup_name("dhw.offset", language),
                    "read_id": oid,
                    "write_id": oid,
                    "unit": "K",
                    "min": -DHW_OFFSET_LIMIT,
                    "max": DHW_OFFSET_LIMIT,
                    "bundle_min": -DHW_OFFSET_LIMIT,
                    "bundle_max": DHW_OFFSET_LIMIT,
                    "step": DHW_OFFSET_STEP,
                    "supplement": "dhw.offset",
                }
            )
            taken.add(canonical_id(oid))
        oid = DHW_ROLES["dhw.comfort_eco"]
        if answers(oid) and canonical_id(oid) not in taken:
            out["selects"].append(
                {
                    "name": _sup_name("dhw.comfort_eco", language),
                    "read_id": oid,
                    "write_id": oid,
                    "options": _sup_options("dhw.comfort_eco", language),
                    "supplement": "dhw.comfort_eco",
                }
            )
            taken.add(canonical_id(oid))
    return out


def default_circuit_name(n: int, language: str) -> str:
    return (f"Heizkreis {n}" if normalize_language(language) == "de" else f"Heating circuit {n}")


def circuit_device_map(
    entities_by_platform: Mapping[str, Iterable[Mapping[str, Any]]],
) -> Dict[int, Tuple[str, str]]:
    """circuit index -> (heating_circuit_uid, heating_circuit_name) in use.

    A circuit's own points live on its instance tag; the first stored entity
    on that tag with a grouping uid names the device the circuit already has
    (bundle UUID or discovery ``tag:<n>``).
    """
    out: Dict[int, Tuple[str, str]] = {}
    for ents in entities_by_platform.values():
        for ent in ents or []:
            if not isinstance(ent, Mapping):
                continue
            uid = str(ent.get("heating_circuit_uid") or "").strip()
            if not uid:
                continue
            oa = try_decode_oa(ent.get("read_id") or ent.get("id"))
            if oa is None or oa.instance_tag not in HC_TAGS:
                continue
            n = HC_TAGS.index(oa.instance_tag) + 1
            if n not in out:
                out[n] = (uid, str(ent.get("heating_circuit_name") or "").strip())
    return out
