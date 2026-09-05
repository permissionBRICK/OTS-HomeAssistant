"""Exercise HA's real forwarding wrapper, which turns platform errors into False."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_asyncio")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant import loader
from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components import ochsner_local_ots as integration
from custom_components.ochsner_local_ots.const import DOMAIN


@pytest.mark.asyncio
@pytest.mark.parametrize("loaded", [["sensor"], ["sensor", "number"]])
@pytest.mark.parametrize("tracked", [True, False])
async def test_unload_only_forwards_platforms_loaded_for_this_entry(
    tmp_path, monkeypatch, loaded, tracked
):
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    entry = ConfigEntry(
        domain=DOMAIN,
        title="Pump",
        source="user",
        version=1,
        minor_version=1,
        unique_id="pump",
        data={},
        options={},
        discovery_keys={},
        subentries_data=None,
        state=ConfigEntryState.LOADED,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    session = SimpleNamespace(close=AsyncMock())
    runtime = {
        "controllers": [
            {"session": session, "numbers": [{}] if "number" in loaded else []}
        ]
    }
    if tracked:
        runtime["platforms"] = loaded
    hass.data[DOMAIN] = {entry.entry_id: runtime}
    # Other integrations can load platforms which this pump does not use.
    hass.config.components.update(
        {"sensor", "binary_sensor", "number", "select", "text", "switch"}
    )
    calls = []

    def get_integration(_hass, platform):
        async def unload(_hass, _entry):
            calls.append(platform)
            if platform not in loaded:
                raise ValueError("Config entry was never loaded!")
            return True

        return SimpleNamespace(
            domain=platform,
            async_get_component=AsyncMock(
                return_value=SimpleNamespace(async_unload_entry=unload)
            ),
        )

    monkeypatch.setattr(loader, "async_get_loaded_integration", get_integration)
    try:
        assert await integration.async_unload_entry(hass, entry)
        assert sorted(calls) == sorted(loaded)
        session.close.assert_awaited_once()
        assert entry.entry_id not in hass.data[DOMAIN]
    finally:
        await hass.async_stop(force=True)
