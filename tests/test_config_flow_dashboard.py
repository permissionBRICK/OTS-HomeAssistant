"""Config flow: the dashboard question ends every interactive creation path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from homeassistant.config_entries import ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import frame

from custom_components.ochsner_local_ots import config_flow as cf
from custom_components.ochsner_local_ots.const import CONF_CONTROLLERS, CONF_HOST, CONF_PORT, DOMAIN
from custom_components.ochsner_local_ots.network_discovery import ControllerIdentity

FOUND = ControllerIdentity("192.0.2.20", "SER1", "AIRHAWK518C11A", "00:a0:03:11:22:33")


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    frame.async_setup(instance)
    yield instance
    await instance.async_stop(force=True)


def make_flow(hass, source="user"):
    flow = cf.ClimatixGenericConfigFlow()
    flow.hass = hass
    flow.context = {"source": source}
    flow.flow_id = f"flow-{source}"
    flow.handler = DOMAIN
    return flow


def fake_scan(flow, serial="SER1"):
    async def scan(**kwargs):
        flow._plant_serial = serial
        flow._plant_model = "AIRHAWK518C11A"
        flow._conn = {CONF_HOST: kwargs["host"], CONF_PORT: kwargs["port"]}
        flow._scanned_entities = {"sensors": [{"id": "AyKWuiiJAAE=", "name": "Außentemperatur"}]}
        return None

    return scan


def assert_dashboard_form(result):
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "dashboard"
    (key,) = result["data_schema"].schema
    assert str(key) == "sidebar_panel" and key.default() is True


async def finish(flow, result, sidebar_panel):
    assert_dashboard_form(result)
    result = await flow.async_step_dashboard({"sidebar_panel": sidebar_panel})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"] == {"sidebar_panel": sidebar_panel, "color_scheme": "ha"}
    assert result["data"][CONF_CONTROLLERS][0]["serial_number"] == "SER1"
    return result


def test_minor_version_bumped():
    assert cf.ClimatixGenericConfigFlow.VERSION == 1
    assert cf.ClimatixGenericConfigFlow.MINOR_VERSION == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("sidebar_panel", [True, False])
async def test_manual_path(hass, monkeypatch, sidebar_panel):
    flow = make_flow(hass)
    monkeypatch.setattr(flow, "_async_validate_and_scan", fake_scan(flow))
    result = await flow.async_step_manual({"local_ip": "192.0.2.20"})
    await finish(flow, result, sidebar_panel)


@pytest.mark.asyncio
async def test_user_step_with_address(hass, monkeypatch):
    flow = make_flow(hass)
    monkeypatch.setattr(flow, "_async_validate_and_scan", fake_scan(flow))
    result = await flow.async_step_user({"local_ip": "192.0.2.20"})
    await finish(flow, result, True)


@pytest.mark.asyncio
async def test_advanced_path(hass, monkeypatch):
    flow = make_flow(hass)
    monkeypatch.setattr(flow, "_async_validate_and_scan", fake_scan(flow))
    result = await flow.async_step_advanced(
        {"local_ip": "192.0.2.20", "port": 8080, "username": "u", "password": "p", "pin": "1"}
    )
    await finish(flow, result, False)


@pytest.mark.asyncio
async def test_network_scan_and_dhcp_paths(hass, monkeypatch):
    monkeypatch.setattr(cf, "async_get_clientsession", lambda _: None)
    monkeypatch.setattr(cf, "async_probe", AsyncMock(return_value=FOUND))

    async def find(*_):
        await asyncio.sleep(0)
        return [FOUND]

    monkeypatch.setattr(cf, "async_find_controllers", find)
    flow = make_flow(hass)
    monkeypatch.setattr(flow, "_async_validate_and_scan", fake_scan(flow))
    result = await flow.async_step_scan()
    await result["progress_task"]
    await flow.async_step_scan()
    result = await flow.async_step_select_device({CONF_HOST: FOUND.host})
    assert result["step_id"] == "discovery_confirm"
    result = await flow.async_step_discovery_confirm({})
    created = await finish(flow, result, True)
    assert created["data"][CONF_CONTROLLERS][0]["mac_address"] == FOUND.mac

    flow = make_flow(hass, "dhcp")
    monkeypatch.setattr(flow, "_async_validate_and_scan", fake_scan(flow))
    info = SimpleNamespace(ip=FOUND.host, macaddress="00a003112233", hostname="POL688")
    result = await flow.async_step_dhcp(info)
    assert result["step_id"] == "discovery_confirm"
    result = await flow.async_step_discovery_confirm({})
    await finish(flow, result, False)


@pytest.mark.asyncio
async def test_dashboard_step_redisplays_and_guards(hass, monkeypatch):
    flow = make_flow(hass)
    assert (await flow.async_step_dashboard({"sidebar_panel": True}))["type"] == FlowResultType.ABORT
    monkeypatch.setattr(flow, "_async_validate_and_scan", fake_scan(flow))
    await flow.async_step_manual({"local_ip": "192.0.2.20"})
    assert_dashboard_form(await flow.async_step_dashboard(None))


@pytest.mark.asyncio
async def test_yaml_import_asks_nothing(hass):
    flow = make_flow(hass, "import")
    result = await flow.async_step_import({CONF_HOST: "192.0.2.30"})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["options"] == {"sidebar_panel": True, "color_scheme": "ha"}


def make_options_flow(options):
    entry = SimpleNamespace(options=dict(options), data={})
    flow = cf.ClimatixGenericOptionsFlowHandler(entry)
    flow.handler = "entry"
    flow.hass = SimpleNamespace(config_entries=SimpleNamespace(async_get_known_entry=lambda _: entry))
    return flow


BASE = {"scan_interval": 30, "polling_threshold": 20, "max_ids_per_read_request": 40}


def _defaults(result):
    return {str(k): k.default() for k in result["data_schema"].schema if callable(getattr(k, "default", None))}


@pytest.mark.asyncio
async def test_options_flow_dashboard_settings():
    flow = make_options_flow({"sidebar_panel": False, "color_scheme": "ochsner", "entity_overrides": {"k": {}}})
    form = await flow.async_step_init(None)
    defaults = _defaults(form)
    assert defaults["sidebar_panel"] is False and defaults["color_scheme"] == "ochsner"
    keys = [str(k) for k in form["data_schema"].schema]
    assert keys.index("sidebar_panel") == keys.index("language") + 1

    res = await flow.async_step_init({**BASE, "sidebar_panel": True, "color_scheme": "ha"})
    assert res["data"]["sidebar_panel"] is True and res["data"]["color_scheme"] == "ha"
    assert res["data"]["entity_overrides"] == {"k": {}}

    # Not submitted -> unchanged; invalid scheme -> current value.
    flow = make_options_flow({"sidebar_panel": False, "color_scheme": "ochsner"})
    res = await flow.async_step_init({**BASE, "color_scheme": "neon"})
    assert res["data"]["sidebar_panel"] is False and res["data"]["color_scheme"] == "ochsner"

    # Pre-migration entry without options: defaults (panel on, HA colours).
    flow = make_options_flow({})
    defaults = _defaults(await flow.async_step_init(None))
    assert defaults["sidebar_panel"] is True and defaults["color_scheme"] == "ha"
