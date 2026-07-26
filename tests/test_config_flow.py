"""IP-only onboarding: the user step asks for the IP address and nothing
else; advanced credential overrides only appear after a failure; no cloud or
bundle step is reachable during onboarding.

These tests import the real config flow and therefore need Home Assistant
(they run in the project venv); they are skipped when HA is unavailable.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ha = pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.ochsner_local_ots import config_flow as cf  # noqa: E402
from custom_components.ochsner_local_ots.const import (  # noqa: E402
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
)


def make_flow():
    flow = cf.ClimatixGenericConfigFlow()
    flow.hass = None
    flow.context = {}
    flow.flow_id = "test-flow"
    return flow


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def schema_keys(result):
    return [str(k.schema) for k in result["data_schema"].schema]


def test_user_step_asks_only_for_ip():
    flow = make_flow()
    result = run(flow.async_step_user(None))
    assert result["type"] == "form" and result["step_id"] == "user"
    assert schema_keys(result) == ["local_ip"]


def test_user_step_offers_advanced_only_after_failure(monkeypatch):
    flow = make_flow()
    monkeypatch.setattr(flow, "_host_already_configured", lambda host: False)

    async def no_unique(*a, **kw):
        return None

    monkeypatch.setattr(flow, "async_set_unique_id", no_unique)
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", lambda: None)

    attempted = {}

    async def failing_scan(**kwargs):
        attempted.update(kwargs)
        return "cannot_connect"

    monkeypatch.setattr(flow, "_async_validate_and_scan", failing_scan)

    result = run(flow.async_step_user({"local_ip": "192.0.2.10"}))
    assert result["type"] == "form" and result["errors"]["base"] == "cannot_connect"
    # The failed attempt used the built-in defaults automatically.
    assert attempted == {
        "host": "192.0.2.10",
        "port": DEFAULT_PORT,
        "username": DEFAULT_USERNAME,
        "password": DEFAULT_PASSWORD,
        "pin": DEFAULT_PIN,
    }
    # Only now is the advanced toggle offered.
    assert schema_keys(result) == ["local_ip", "advanced"]


def test_advanced_step_defaults_are_the_climatix_defaults():
    flow = make_flow()
    result = run(flow.async_step_advanced(None))
    assert result["step_id"] == "advanced"
    schema = result["data_schema"].schema
    defaults = {str(k.schema): k.default() for k in schema if k.default is not None}
    assert defaults["port"] == DEFAULT_PORT
    assert defaults["username"] == DEFAULT_USERNAME
    assert defaults["password"] == DEFAULT_PASSWORD
    assert defaults["pin"] == DEFAULT_PIN


def test_no_cloud_or_bundle_steps_in_onboarding():
    flow = cf.ClimatixGenericConfigFlow
    for legacy_step in ("async_step_select_plants", "async_step_hosts", "async_step_finish"):
        assert not hasattr(flow, legacy_step)
