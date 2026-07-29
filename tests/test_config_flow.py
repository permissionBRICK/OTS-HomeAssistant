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


def test_resolve_language_order_and_explicit_auto():
    """Documented order: explicit option > per-controller CONF_LANGUAGE >
    HA language > en. Regression (review round 3): an explicit "auto"
    (follow Home Assistant) skips the controller's stored language — a
    legacy bundle controller keeps e.g. "DE" there."""

    from types import SimpleNamespace

    from custom_components.ochsner_local_ots.const import CONF_LANGUAGE
    from custom_components.ochsner_local_ots.local_scan import resolve_language

    hass_en = SimpleNamespace(config=SimpleNamespace(language="en"))
    hass_de = SimpleNamespace(config=SimpleNamespace(language="de-AT"))
    legacy_ctrl = {CONF_LANGUAGE: "DE"}

    # Explicit option always wins.
    assert resolve_language(hass_en, option_value="de", controller=legacy_ctrl) == "de"
    assert resolve_language(hass_de, option_value="en", controller=legacy_ctrl) == "en"
    # Absent option: the controller's stored language applies.
    assert resolve_language(hass_en, option_value=None, controller=legacy_ctrl) == "de"
    # Explicit "auto": follow HA, NOT the controller's stored language.
    assert resolve_language(hass_en, option_value="auto", controller=legacy_ctrl) == "en"
    assert resolve_language(hass_de, option_value="auto", controller=legacy_ctrl) == "de"
    # Nothing anywhere: HA language, default en.
    assert resolve_language(hass_de) == "de"
    assert resolve_language(SimpleNamespace(config=SimpleNamespace(language=None))) == "en"


def make_options_flow(options=None, data=None):
    from types import SimpleNamespace

    entry = SimpleNamespace(options=dict(options or {}), data=dict(data or {}))
    flow = cf.ClimatixGenericOptionsFlowHandler(entry)
    flow.hass = None
    return flow


def test_options_flow_language_setting():
    """The displayed language always MATCHES the effective behavior (review
    round 5): without a stored option the form shows the legacy controller
    language (or auto); every SUBMITTED value is stored explicitly — "auto"
    included, so a saved "follow HA" really follows HA."""

    from custom_components.ochsner_local_ots.const import (
        CONF_CONTROLLERS,
        CONF_LANGUAGE as LANG,
    )

    base = {"scan_interval": 30, "polling_threshold": 20, "max_ids_per_read_request": 40}

    def language_default(flow):
        res = run(flow.async_step_init(None))
        for key in res["data_schema"].schema:
            if str(key) == LANG:
                return key.default()
        raise AssertionError("language field missing")

    # Fresh entry: shows auto; submitting it stores explicit auto.
    flow = make_options_flow()
    assert language_default(flow) == "auto"
    res = run(flow.async_step_init({**base, LANG: "auto"}))
    assert res["type"] == "create_entry"
    assert res["data"][LANG] == "auto"

    # Legacy bundle controller with stored "DE": the form shows "de" (what
    # the runtime resolves), NOT "follow HA".
    legacy_data = {CONF_CONTROLLERS: [{LANG: "DE"}]}
    flow = make_options_flow(data=legacy_data)
    assert language_default(flow) == "de"
    # Submitting the displayed value keeps the same effective behavior.
    res = run(flow.async_step_init({**base, LANG: "de"}))
    assert res["data"][LANG] == "de"
    # Explicitly choosing follow-HA on the legacy entry stores "auto",
    # which bypasses the controller language at runtime.
    flow = make_options_flow(data=legacy_data)
    res = run(flow.async_step_init({**base, LANG: "auto"}))
    assert res["data"][LANG] == "auto"

    # Merely opening the form (no submit) changes nothing.
    flow = make_options_flow(data=legacy_data)
    res = run(flow.async_step_init(None))
    assert res["type"] == "form"

    # An invalid value falls back to the displayed default.
    flow = make_options_flow(options={LANG: "de"})
    res = run(flow.async_step_init({**base, LANG: "fr"}))
    assert res["data"][LANG] == "de"


def test_onboarding_user_step_has_no_language_field():
    flow = make_flow()
    res = run(flow.async_step_user(None))
    schema_keys = [str(k) for k in res["data_schema"].schema]
    assert schema_keys == ["local_ip"]
