"""Accountless local discovery scan for Ochsner/Climatix controllers.

Two-phase, strictly read-only scan against the packaged discovery catalog:

Phase A (module probe): read a handful of representative points per known
instance tag. A module (instance tag) is present iff at least one of its
points answers under "values".

Phase B (sweep): read every non-schedule catalog point of the present tags.

Presence rule (empirically established on real hardware): an id returned
under "values" exists and is readable -> candidate for an entity. An id that
only appears under "states" as a bare scalar is absent-or-permission-denied
-> no entity. Schedule members (514..525) and enum descriptors (member 4353)
are never swept.

This module performs NO writes and is free of Home Assistant imports so the
acceptance tooling under tools/ can drive it against a controller directly.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .catalog import DiscoveryCatalog, hc_name_oa

_LOGGER = logging.getLogger(__name__)

# Pause between discovery read batches so a full sweep stays gentle on the
# controller (a batch of 40 ids answers in ~0.2 s on real hardware).
DEFAULT_INTER_REQUEST_GAP_SEC = 0.1
DEFAULT_PROBE_POINTS_PER_TAG = 8


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
    probed_ids: int = 0
    swept_ids: int = 0

    @property
    def found_ids(self) -> List[str]:
        return list(self.values)


def _first_value(raw: Any) -> Any:
    if isinstance(raw, list):
        return raw[0] if raw else None
    return raw


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
    probe_points_per_tag: int = DEFAULT_PROBE_POINTS_PER_TAG,
    progress: Optional[Callable[[int, int], None]] = None,
) -> DiscoveryScanResult:
    """Run the two-phase read-only catalog scan.

    ``api`` only needs an async ``read_raw(ids)`` method (ClimatixGenericApi).
    """

    result = DiscoveryScanResult(catalog_version=catalog.catalog_version)

    # Phase A: which modules (instance tags) exist on this plant?
    probe_by_tag = catalog.probe_ids_by_tag(per_tag=probe_points_per_tag)
    probe_ids: List[str] = [oid for ids in probe_by_tag.values() for oid in ids]
    await _read_batched(
        api,
        probe_ids,
        batch_size=batch_size,
        gap_sec=gap_sec,
        values_out=result.values,
        states_out=result.states,
    )
    result.probed_ids = len(probe_ids)

    present = {
        tag
        for tag, ids in probe_by_tag.items()
        if any(oid in result.values for oid in ids)
    }
    result.present_tags = sorted(present)
    _LOGGER.debug(
        "Discovery phase A: %d/%d instance tags present (%s)",
        len(present),
        len(probe_by_tag),
        result.present_tags,
    )

    # Phase B: sweep every non-schedule catalog point of the present modules.
    sweep_ids = [oid for oid in catalog.scan_ids_for_tags(present) if oid not in result.values]
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

    # Circuit names: the controller holds the owner's own name per circuit
    # (e.g. "Fussboden"); use it to label the per-circuit devices.
    hc_present = [t for t in catalog.hc_tags if t in present]
    if hc_present:
        name_ids = {hc_name_oa(t): t for t in hc_present}
        name_values: Dict[str, Any] = {}
        await _read_batched(
            api,
            list(name_ids),
            batch_size=batch_size,
            gap_sec=gap_sec,
            values_out=name_values,
            states_out=result.states,
        )
        for oid, tag in name_ids.items():
            if oid not in name_values:
                continue
            v = name_values.get(oid)
            if isinstance(v, str) and v.strip() and not v.strip().startswith("#"):
                result.circuit_names[tag] = v.strip()
            # Circuit-name points are regular catalog points too; make the
            # value available for entity generation as well.
            result.values[oid] = v

    _LOGGER.debug(
        "Discovery scan done: %d readable ids (probed=%d swept=%d), circuits=%s",
        len(result.values),
        result.probed_ids,
        result.swept_ids,
        result.circuit_names,
    )
    return result
