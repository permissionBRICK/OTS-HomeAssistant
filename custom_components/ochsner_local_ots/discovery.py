"""Accountless local discovery scan for Ochsner/Climatix controllers.

Strictly read-only scan against the packaged discovery catalog (spec v4 D):
EVERY point the catalog knows is read; a module (instance tag) is present
iff at least one of its points answered. Readable enum-shaped points then
get their state descriptor (member 4353) read, plus the plant identity
(model/serial/software version) and the owner-configured circuit names.

Presence rule (empirically established on real hardware): an id returned
under "values" exists and is readable -> candidate for an entity. An id that
only appears under "states" as a bare scalar is absent-or-permission-denied
-> no entity, skipped.

This module performs NO writes and is free of Home Assistant imports so the
acceptance tooling under tools/ can drive it against a controller directly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .catalog import (
    PLANT_MODEL_OA,
    PLANT_SERIAL_OA,
    PLANT_SW_VERSION_OA,
    DiscoveryCatalog,
    descriptor_oa,
    hc_name_oa,
)

_LOGGER = logging.getLogger(__name__)

# Pause between discovery read batches so a full sweep stays gentle on the
# controller (a batch of 40 ids answers in ~0.2 s on real hardware).
DEFAULT_INTER_REQUEST_GAP_SEC = 0.1


@dataclass
class DiscoveryScanResult:
    catalog_version: str
    present_tags: List[int] = field(default_factory=list)
    # id -> first (unwrapped) value for every id that answered under "values".
    values: Dict[str, Any] = field(default_factory=dict)
    # id -> raw "states" payload (kept for diagnostics; NOT presence proof).
    states: Dict[str, Any] = field(default_factory=dict)
    # hc instance tag -> owner-configured circuit name read from the controller.
    circuit_names: Dict[int, str] = field(default_factory=dict)
    # read id -> star-list state labels from the controller's enum descriptor
    # (member 4353); index in the list = numeric value.
    enum_labels: Dict[str, List[str]] = field(default_factory=dict)
    # Plant identity read from the controller: model ("Anlagentyp"), serial
    # number and software version, when readable.
    plant_model: Optional[str] = None
    plant_serial: Optional[str] = None
    plant_sw_version: Optional[str] = None
    swept_ids: int = 0

    @property
    def found_ids(self) -> List[str]:
        return list(self.values)


def _first_value(raw: Any) -> Any:
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw


def _repair_mojibake(text: str) -> str:
    """Undo UTF-8-read-as-latin-1 double encoding ("Fußboden" -> "FuÃ\x9fboden").

    The controller mixes encodings in one response: text points are UTF-8
    while units carry raw latin-1 bytes (°), so the API's whole-response
    latin-1 fallback can mangle the UTF-8 strings. The round-trip repair is
    safe: a genuinely latin-1 string re-encodes to bytes that are not valid
    UTF-8 and is returned unchanged.
    """
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired


def _clean_text(raw: Any) -> Optional[str]:
    """A usable text value: non-empty and not a controller placeholder (#...)."""
    v = _first_value(raw)
    if isinstance(v, str):
        v = _repair_mojibake(v).strip()
        if v and not v.startswith("#"):
            return v
    return None


def parse_enum_descriptor(raw: Any) -> Optional[List[str]]:
    """Parse a member-4353 descriptor read into the state-label list.

    The controller answers with a star-separated list, e.g.
    "Comfort*Off*Red*Norm*HeatMan*CoolMan*Eco*Party*Holiday"; the index in
    the list IS the numeric value of the point.
    """
    v = _first_value(raw)
    if isinstance(v, str) and "*" in v:
        return v.split("*")
    return None


async def _read_batched(
    api: Any,
    ids: List[str],
    *,
    batch_size: int,
    gap_sec: float,
    values_out: Dict[str, Any],
    states_out: Dict[str, Any],
    progress: Optional[Callable[[int, int], None]] = None,
    total: Optional[int] = None,
    done_offset: int = 0,
) -> None:
    total = total if total is not None else len(ids)
    for i in range(0, len(ids), batch_size):
        chunk = ids[i : i + batch_size]
        try:
            payload = await api.read_raw(chunk)
        except Exception:  # noqa: BLE001
            # One bounded retry per failed chunk (still one in-flight request).
            await asyncio.sleep(max(gap_sec, 0.5))
            payload = await api.read_raw(chunk)
        values = payload.get("values") if isinstance(payload, dict) else None
        states = payload.get("states") if isinstance(payload, dict) else None
        if isinstance(values, dict):
            # Presence is MAP MEMBERSHIP under "values", not value content: an
            # id the controller lists there exists even if its current value
            # unwraps to None/empty.
            for k, v in values.items():
                values_out[str(k)] = _first_value(v)
        if isinstance(states, dict):
            for k, v in states.items():
                states_out[str(k)] = v
        if progress is not None:
            progress(done_offset + min(i + batch_size, len(ids)), total)
        if gap_sec > 0 and i + batch_size < len(ids):
            await asyncio.sleep(gap_sec)


async def async_scan(
    api: Any,
    catalog: DiscoveryCatalog,
    *,
    batch_size: int = 40,
    gap_sec: float = DEFAULT_INTER_REQUEST_GAP_SEC,
    progress: Optional[Callable[[int, int], None]] = None,
) -> DiscoveryScanResult:
    """Run the read-only catalog scan.

    ``api`` only needs an async ``read_raw(ids)`` method (ClimatixGenericApi).
    """

    result = DiscoveryScanResult(catalog_version=catalog.catalog_version)

    # Full sweep: every point the catalog knows (spec v4 D). Module presence
    # falls out of the result: an instance tag is present iff at least one of
    # its points answered under "values".
    all_tags = {int(t["tag"]) for t in catalog.tags if t.get("tag") is not None}
    sweep_ids = catalog.scan_ids_for_tags(all_tags)
    await _read_batched(
        api,
        sweep_ids,
        batch_size=batch_size,
        gap_sec=gap_sec,
        values_out=result.values,
        states_out=result.states,
        progress=progress,
        total=len(sweep_ids),
    )
    result.swept_ids = len(sweep_ids)

    tag_by_id = {str(rec["id"]): int(rec["tag"]) for rec in catalog.points}
    present = {tag_by_id[oid] for oid in result.values if oid in tag_by_id}
    result.present_tags = sorted(present)
    _LOGGER.debug(
        "Discovery sweep: %d/%d ids readable, %d/%d instance tags present (%s)",
        len(result.values),
        len(sweep_ids),
        len(present),
        len(all_tags),
        result.present_tags,
    )

    # Phase C: enum state descriptors. For every READABLE enum-shaped point
    # (select platform or known options/value_map), read member 4353 of the
    # same object address; the controller's state list takes priority over
    # packaged metadata when building entity options (spec v4 C).
    desc_to_point: Dict[str, str] = {}
    for rec in catalog.points:
        oid = str(rec["id"])
        if oid not in result.values:
            continue
        if rec.get("platform") == "select" or rec.get("options") or rec.get("value_map"):
            desc_to_point[descriptor_oa(oid)] = oid
    if desc_to_point:
        desc_values: Dict[str, Any] = {}
        await _read_batched(
            api,
            list(desc_to_point),
            batch_size=batch_size,
            gap_sec=gap_sec,
            values_out=desc_values,
            states_out=result.states,
        )
        for did, oid in desc_to_point.items():
            labels = parse_enum_descriptor(desc_values.get(did))
            if labels:
                result.enum_labels[oid] = labels

    # Plant identity: model type ("Anlagentyp") names the HA device, serial
    # number identifies it, software version becomes sw_version (spec v4 B1).
    ident_attr = {
        PLANT_MODEL_OA: "plant_model",
        PLANT_SERIAL_OA: "plant_serial",
        PLANT_SW_VERSION_OA: "plant_sw_version",
    }
    ident_values: Dict[str, Any] = {}
    await _read_batched(
        api,
        list(ident_attr),
        batch_size=batch_size,
        gap_sec=gap_sec,
        values_out=ident_values,
        states_out=result.states,
    )
    for oid, attr in ident_attr.items():
        setattr(result, attr, _clean_text(ident_values.get(oid)))

    # Circuit names: the controller holds the owner's own name per circuit
    # (e.g. "Fußboden"); use it to label the per-circuit devices. The name
    # points are usually catalog points already covered by the sweep; read
    # any remaining ones for the circuits that are present.
    hc_present = [t for t in catalog.hc_tags if t in present]
    if hc_present:
        name_ids = {hc_name_oa(t): t for t in hc_present}
        to_read = [oid for oid in name_ids if oid not in result.values]
        if to_read:
            name_values: Dict[str, Any] = {}
            await _read_batched(
                api,
                to_read,
                batch_size=batch_size,
                gap_sec=gap_sec,
                values_out=name_values,
                states_out=result.states,
            )
            # Circuit-name points are regular catalog points too; make the
            # values available for entity generation as well.
            result.values.update(name_values)
        for oid, tag in name_ids.items():
            name = _clean_text(result.values.get(oid)) if oid in result.values else None
            if name:
                result.circuit_names[tag] = name

    _LOGGER.debug(
        "Discovery scan done: %d readable ids (swept=%d), enums=%d, "
        "circuits=%s, model=%s sw=%s",
        len(result.values),
        result.swept_ids,
        len(result.enum_labels),
        result.circuit_names,
        result.plant_model,
        result.plant_sw_version,
    )
    return result
