"""Two-phase scan behavior against a scripted fake controller."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List


def make_catalog(catalog_mod):
    """Small synthetic catalog: one 'shared' tag, two circuit tags, one absent tag."""

    enc = catalog_mod.encode_oa
    points = [
        {"id": enc(8960, 100, 10, 256), "platform": "sensor", "name": "Shared A", "name_confidence": "reference", "sources": ["reference"]},
        {"id": enc(8960, 100, 11, 256), "platform": "sensor", "name": "Shared B", "name_confidence": "apk_label", "sources": ["apk"]},
        {"id": enc(8960, 100, 12, 514), "platform": "sensor", "name": "Sched", "name_confidence": "apk_label", "sources": ["apk"], "schedule": True},
        {"id": enc(8960, 31886, 20, 256), "platform": "number", "name": "HC Setpoint", "name_confidence": "reference", "sources": ["reference"], "write_id": enc(8960, 31886, 20, 256), "min": 10, "max": 30, "hc_tag": 31886},
        {"id": enc(8960, 19693, 20, 256), "platform": "number", "name": "HC Setpoint", "name_confidence": "reference", "sources": ["reference"], "write_id": enc(8960, 19693, 20, 256), "min": 10, "max": 30, "hc_tag": 19693},
        {"id": enc(8960, 200, 30, 256), "platform": "sensor", "name": "Absent module point", "name_confidence": "apk_symbol", "sources": ["apk"], "diagnostic": True, "enabled_default": False},
    ]
    raw = {
        "schema_version": 1,
        "catalog_version": "test",
        "hc_tags": [31886, 19693, 23756, 11307],
        "points": points,
        "tags": [{"tag": 100}, {"tag": 31886}, {"tag": 19693}, {"tag": 200}],
    }
    return catalog_mod.DiscoveryCatalog(raw)


class FakeApi:
    """Read-only fake controller. Intentionally has NO write method: any write
    attempt during discovery would crash the test."""

    def __init__(self, catalog_mod, readable: Dict[str, Any], max_batch: int = 40):
        self._readable = readable
        self._max_batch = max_batch
        self.requests: List[List[str]] = []

    async def read_raw(self, ids, **kwargs):
        ids = list(ids)
        assert len(ids) <= self._max_batch, "batch larger than the configured cap"
        self.requests.append(ids)
        values = {}
        states = {}
        for oid in ids:
            if oid in self._readable:
                values[oid] = [self._readable[oid], self._readable[oid]]
            else:
                # Absent-or-permission-denied: bare scalar 5 under "states".
                states[oid] = 5
        return {"values": values, "states": states}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_presence_rule_and_tag_gating(catalog_mod, discovery_mod):
    cat = make_catalog(catalog_mod)
    enc = catalog_mod.encode_oa
    readable = {
        enc(8960, 100, 10, 256): 21.5,
        enc(8960, 100, 11, 256): 1.0,
        enc(8960, 31886, 20, 256): 22.0,
        catalog_mod.hc_name_oa(31886): "Fussboden",
    }
    api = FakeApi(catalog_mod, readable)
    scan = run(discovery_mod.async_scan(api, cat, gap_sec=0))

    # Tags 100 and 31886 answered; 19693 and 200 did not.
    assert scan.present_tags == [100, 31886]

    requested = {oid for req in api.requests for oid in req}
    # Absent module's points are only touched by the phase-A probe, never swept
    # beyond it; schedule points are never requested at all.
    assert enc(8960, 100, 12, 514) not in requested
    # The circuit name of the absent circuit (19693) is not requested.
    assert catalog_mod.hc_name_oa(19693) not in requested

    # Presence rule: readable ids carry their first value; states-only ids don't exist.
    assert scan.values[enc(8960, 100, 10, 256)] == 21.5
    assert enc(8960, 200, 30, 256) not in scan.values
    assert scan.circuit_names == {31886: "Fussboden"}


def test_scan_is_read_only(catalog_mod, discovery_mod):
    cat = make_catalog(catalog_mod)
    api = FakeApi(catalog_mod, {})
    assert not hasattr(api, "write")
    scan = run(discovery_mod.async_scan(api, cat, gap_sec=0))
    assert scan.values == {}
    assert scan.present_tags == []


def test_batch_size_respected(catalog_mod, discovery_mod):
    cat = make_catalog(catalog_mod)
    enc = catalog_mod.encode_oa
    api = FakeApi(catalog_mod, {enc(8960, 100, 10, 256): 1.0}, max_batch=2)
    run(discovery_mod.async_scan(api, cat, batch_size=2, gap_sec=0))
    assert all(len(req) <= 2 for req in api.requests)


def test_presence_is_map_membership_not_value_content(catalog_mod, discovery_mod):
    """An id listed under "values" exists even when its value is 5 or None;
    an id only under "states" (bare 5) does not exist."""

    cat = make_catalog(catalog_mod)
    enc = catalog_mod.encode_oa
    with_five = enc(8960, 100, 10, 256)
    with_none = enc(8960, 100, 11, 256)
    states_only = enc(8960, 200, 30, 256)

    class Api(FakeApi):
        async def read_raw(self, ids, **kwargs):
            ids = list(ids)
            self.requests.append(ids)
            values = {}
            states = {}
            for oid in ids:
                if oid == with_five:
                    values[oid] = [5, 5]  # legitimate numeric value 5
                elif oid == with_none:
                    values[oid] = [None, None]
                else:
                    states[oid] = 5  # absent-or-permission-denied sentinel
            return {"values": values, "states": states}

    scan = run(discovery_mod.async_scan(Api(catalog_mod, {}), cat, gap_sec=0))
    assert scan.values[with_five] == 5
    assert with_none in scan.values and scan.values[with_none] is None
    assert states_only not in scan.values
    assert scan.present_tags == [100]


def test_failed_chunk_is_retried_once(catalog_mod, discovery_mod):
    cat = make_catalog(catalog_mod)
    enc = catalog_mod.encode_oa
    readable = {enc(8960, 100, 10, 256): 1.0}

    class FlakyApi(FakeApi):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.failed_once = False

        async def read_raw(self, ids, **kwargs):
            if not self.failed_once:
                self.failed_once = True
                self.requests.append(list(ids))
                raise RuntimeError("transient")
            return await super().read_raw(ids, **kwargs)

    api = FlakyApi(catalog_mod, readable)
    scan = run(discovery_mod.async_scan(api, cat, gap_sec=0))
    assert scan.values[enc(8960, 100, 10, 256)] == 1.0
    # The failed chunk was retried exactly once (first two requests identical).
    assert api.requests[0] == api.requests[1]

    class BrokenApi(FakeApi):
        async def read_raw(self, ids, **kwargs):
            raise RuntimeError("down")

    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        run(discovery_mod.async_scan(BrokenApi(catalog_mod, {}), cat, gap_sec=0))
