"""The frontend's contract fixture must be exactly what the backend sends.

tests/frontend/fixtures/backend-payload.json is the real
``ochsner_local_ots/plants`` payload (DE + EN labels) and the first
subscription event for two plants: the reference AirHawk 518 and a variant with
hot water, a cooling circuit and a pool. The JS contract tests run the frontend
against it, so a backend change that alters the payload fails here until the
fixture is regenerated:

    OTS_UPDATE_FRONTEND_FIXTURE=1 pytest tests/test_frontend_contract.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from custom_components.ochsner_local_ots import plant_api
from tests.test_dashboard_ha import (  # noqa: F401  (pytest fixtures)
    FakeConnection,
    cooling_hc2,
    dhw_plant,
    hass,
    setup_plant,
    teardown,
)

FIXTURE = Path(__file__).parent / "frontend" / "fixtures" / "backend-payload.json"
UPDATE = bool(os.environ.get("OTS_UPDATE_FRONTEND_FIXTURE"))


def dhw_cool_pool(fake) -> None:
    dhw_plant(fake)
    cooling_hc2(fake)
    fake.set("AiM3dQAAAAE=", 1)  # avail.pool
    for oid, value in {"AiIQvULcIgE=": 0, "ASPmx0LcAAE=": 28.0, "CyMogkLcAAE=": 1}.items():
        fake.set(oid, value)


VARIANTS = {"house": None, "dhw_cool_pool": dhw_cool_pool}


def _normalize(data):
    """Replace the run-specific entry and device ids with stable placeholders."""
    names: dict = {}

    def name(value, kind):
        if value is None:
            return None
        return names.setdefault(value, f"{kind}-{sum(1 for v in names.values() if v.startswith(kind)) + 1}")

    def walk(obj):
        if isinstance(obj, dict):
            out = {}
            for key, value in obj.items():
                if key == "entry_id":
                    out[key] = name(value, "entry")
                elif key == "device_id":
                    out[key] = name(value, "device")
                elif key == "plant_id" and isinstance(value, str) and ":" in value:
                    entry, _, host = value.partition(":")
                    out[key] = f"{name(entry, 'entry')}:{host}"
                else:
                    out[key] = walk(value)
            return out
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return json.loads(json.dumps(walk(data), ensure_ascii=False, sort_keys=True))


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", list(VARIANTS))
async def test_frontend_fixture_matches_backend(hass, monkeypatch, fake_controller, variant):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=VARIANTS[variant])
    try:
        out = {"plants": await plant_api.async_build_plants(hass, "de")}
        en = (await plant_api.async_build_plants(hass, "en"))["plants"][0]["roles"]
        out["labels_en"] = {r: v["labels"] for r, v in en.items() if "labels" in v}
        conn = FakeConnection()
        plant_api.ws_subscribe(hass, conn, {"id": 1, "type": "ochsner_local_ots/subscribe"})
        out["event"] = conn.messages[0]["event"]
        out["event"]["updated"] = "2026-10-22T10:00:00+00:00"
        conn.subscriptions[1]()
    finally:
        await teardown(hass, plant)

    stored = json.loads(FIXTURE.read_text(encoding="utf-8")) if FIXTURE.exists() else {}
    out = _normalize(out)
    if UPDATE:
        stored[variant] = out
        with FIXTURE.open("w", encoding="utf-8") as fh:
            json.dump(stored, fh, ensure_ascii=False, indent=0, sort_keys=True)
            fh.write("\n")
        return
    assert out == stored.get(variant), (
        "backend payload changed; regenerate with OTS_UPDATE_FRONTEND_FIXTURE=1"
    )
