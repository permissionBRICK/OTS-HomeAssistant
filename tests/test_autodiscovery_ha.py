"""Discovery and address persistence using real Home Assistant registries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from homeassistant.config_entries import (
    ConfigEntries,
    ConfigEntry,
    ConfigEntryState,
    current_entry,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import frame

from custom_components.ochsner_local_ots import _async_update_listener
from custom_components.ochsner_local_ots import autodiscovery as ad
from custom_components.ochsner_local_ots import config_flow as cf
from custom_components.ochsner_local_ots.const import (
    CONF_CONTROLLERS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_OVERRIDES,
    CONF_HOST,
    CONF_ID,
    CONF_IDENTITY_KEY,
    CONF_MAC_ADDRESS,
    CONF_NAME,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SENSORS,
    CONF_SERIAL_NUMBER,
    DOMAIN,
)
from custom_components.ochsner_local_ots.network_discovery import (
    ControllerIdentity,
    identity_key,
)


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    frame.async_setup(instance)
    dr.async_setup(instance)
    await dr.async_load(instance, load_empty=True)
    yield instance
    await instance.async_stop(force=True)


def add_entry(hass, controllers, options=None):
    entry = ConfigEntry(
        domain=DOMAIN,
        title="Existing pump",
        version=1,
        minor_version=1,
        unique_id="old-host-id",
        source="user",
        data={CONF_CONTROLLERS: controllers},
        options=options or {},
        discovery_keys={},
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


def flow_for(hass):
    flow = cf.ClimatixGenericConfigFlow()
    flow.hass = hass
    flow.context = {"source": "dhcp"}
    flow.flow_id = "test-discovery"
    return flow


@pytest.mark.asyncio
async def test_dhcp_requires_api_identity_and_one_click_confirmation(hass, monkeypatch):
    flow = flow_for(hass)
    found = ControllerIdentity("192.168.1.2", "123", "AIRHAWK", "00:a0:03:11:22:33")
    probe = AsyncMock(return_value=None)
    monkeypatch.setattr(cf, "async_probe", probe)
    monkeypatch.setattr(cf, "async_get_clientsession", lambda _: None)
    info = SimpleNamespace(
        ip=found.host, macaddress="00a003112233", hostname="POL688-112233"
    )
    assert (await flow.async_step_dhcp(info))["reason"] == "not_ochsner"
    probe.return_value = found
    result = await flow.async_step_dhcp(info)
    assert result["step_id"] == "discovery_confirm"
    assert result["description_placeholders"]["serial"] == "123"
    assert len(result["data_schema"].schema) == 0
    assert flow.context["confirm_only"] is True

    async def scan(**kwargs):
        flow._plant_serial = found.serial
        flow._plant_model = found.model
        flow._conn = {CONF_HOST: found.host, CONF_PORT: 80}
        flow._scanned_entities = {
            "sensors": [{CONF_ID: "temperature", CONF_NAME: "Temperature"}]
        }

    monkeypatch.setattr(flow, "_async_validate_and_scan", scan)
    result = await flow.async_step_discovery_confirm({})
    assert result["type"] == "create_entry"
    controller = result["data"][CONF_CONTROLLERS][0]
    assert controller[CONF_IDENTITY_KEY] == "serial:123"
    assert controller[CONF_MAC_ADDRESS] == found.mac
    assert result["title"] == "AIRHAWK (123)"
    assert flow.unique_id == f"{DOMAIN}:serial:123"


@pytest.mark.asyncio
async def test_discovered_ip_is_reverified_before_adoption(hass, monkeypatch):
    flow = flow_for(hass)
    flow._discovered = ControllerIdentity("192.168.1.2", "123", "AIRHAWK")
    monkeypatch.setattr(cf, "async_get_clientsession", lambda _: None)
    monkeypatch.setattr(
        cf,
        "async_probe",
        AsyncMock(return_value=ControllerIdentity("192.168.1.2", "OTHER", "AIRHAWK")),
    )
    scan = AsyncMock()
    monkeypatch.setattr(flow, "_async_validate_and_scan", scan)
    result = await flow.async_step_discovery_confirm({})
    assert result["errors"] == {"base": "identity_changed"}
    scan.assert_not_awaited()


@pytest.mark.asyncio
async def test_rediscovery_updates_saved_ip_preserves_legacy_ids_and_options(
    hass, monkeypatch
):
    controller = {
        CONF_HOST: "192.168.1.2",
        CONF_SERIAL_NUMBER: "123",
        CONF_DEVICE_MODEL: "AIRHAWK",
    }
    options = {
        CONF_ENTITY_OVERRIDES: {
            "192.168.1.2:sensor:temperature": {"polling_mode": "fast"}
        }
    }
    entry = add_entry(hass, [controller], options)
    reload = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload)
    entry.add_update_listener(_async_update_listener)
    ad.async_register_controller(hass, entry, controller, "http://192.168.1.2")
    registry = dr.async_get(hass)
    before = dr.async_entries_for_config_entry(registry, entry.entry_id)[0]
    found = ControllerIdentity("192.168.1.3", "123", "AIRHAWK", "00:a0:03:11:22:33")
    result = await flow_for(hass)._async_offer_discovery(found)
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    reload.assert_awaited_once_with(entry.entry_id)
    saved = entry.data[CONF_CONTROLLERS][0]
    assert saved[CONF_HOST] == found.host
    assert identity_key(saved) == "192.168.1.2"
    assert entry.options == options
    ad.async_register_controller(hass, entry, saved, "http://192.168.1.3")
    devices = dr.async_entries_for_config_entry(registry, entry.entry_id)
    assert len(devices) == 1
    assert devices[0].id == before.id
    assert devices[0].identifiers == {(DOMAIN, "192.168.1.2"), (DOMAIN, "serial:123")}
    assert devices[0].configuration_url == "http://192.168.1.3"


@pytest.mark.asyncio
async def test_recovery_updates_do_not_reload_or_swallow_next_options_change(
    hass, monkeypatch
):
    entry = add_entry(hass, [{CONF_HOST: "192.168.1.2", CONF_SERIAL_NUMBER: "123"}])
    reload = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload)
    entry.add_update_listener(_async_update_listener)
    # Two updates can be queued before the update listeners get CPU time.
    for host in ("192.168.1.3", "192.168.1.4"):
        assert ad.async_update_address(
            hass,
            entry,
            "192.168.1.2",
            ControllerIdentity(host, "123", "AIRHAWK"),
            reload=False,
        )
    await hass.async_block_till_done()
    reload.assert_not_awaited()
    hass.config_entries.async_update_entry(entry, options={CONF_SCAN_INTERVAL: 45})
    await hass.async_block_till_done()
    reload.assert_awaited_once_with(entry.entry_id)
    assert not ad.async_update_address(
        hass,
        entry,
        "192.168.1.2",
        ControllerIdentity("192.168.1.5", "OTHER", "AIRHAWK"),
        reload=False,
    )
    assert entry.data[CONF_CONTROLLERS][0][CONF_HOST] == "192.168.1.4"


@pytest.mark.asyncio
async def test_manual_scan_ignores_hostname_and_vendor_prefix(hass, monkeypatch):
    monkeypatch.setattr(
        ad.network,
        "async_get_adapters",
        AsyncMock(
            return_value=[
                {
                    "enabled": True,
                    "ipv4": [{"address": "192.168.1.1", "network_prefix": 30}],
                }
            ]
        ),
    )
    monkeypatch.setattr(ad, "async_get_clientsession", lambda _: None)
    monkeypatch.setattr(
        ad.dhcp, "async_discovered_service_info", lambda _: [], raising=False
    )
    found = ControllerIdentity("192.168.1.2", "123", "AIRHAWK", "aa:bb:cc:11:22:33")
    probe = AsyncMock(return_value=found)
    monkeypatch.setattr(ad, "async_probe", probe)
    assert await ad.async_find_controllers(hass, ad.connection_from_controller({})) == [
        found
    ]
    assert probe.await_args.args[1].host == found.host


@pytest.mark.asyncio
@pytest.mark.parametrize("quick_hit", [False, True])
async def test_setup_poll_recovery_and_reload_keep_entities(
    hass, monkeypatch, unused_tcp_port, quick_hit
):
    """Real HTTP API + real coordinator + entity factories across an IP change."""
    from aiohttp import ClientSession, web

    from custom_components import ochsner_local_ots as integration
    from custom_components.ochsner_local_ots import sensor
    from custom_components.ochsner_local_ots.catalog import (
        PLANT_MODEL_OA,
        PLANT_SERIAL_OA,
    )
    from custom_components.ochsner_local_ots.network_discovery import PLANT_MAC_OA

    devices = {"127.0.0.1": "123", "127.0.0.2": "123"}
    requests = []

    async def controller(request):
        host = request.host.split(":")[0]
        assert request.query["FN"] == "Read"
        requests.append((host, request.query.getall("OA")))
        values = {
            PLANT_SERIAL_OA: devices[host],
            PLANT_MODEL_OA: "AIRHAWK",
            PLANT_MAC_OA: "00-A0-03-11-22-33",
            "temperature": 22.5,
        }
        return web.json_response(
            {
                "values": {
                    key: values[key]
                    for key in request.query.getall("OA")
                    if key in values
                }
            }
        )

    app = web.Application()
    app.router.add_get("/{path:.*}", controller)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", unused_tcp_port).start()
    entry = add_entry(
        hass,
        [
            {
                CONF_HOST: "127.0.0.1",
                CONF_PORT: unused_tcp_port,
                CONF_SENSORS: [{CONF_ID: "temperature", CONF_NAME: "Temperature"}],
            }
        ],
    )
    reload = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_reload", reload)
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", AsyncMock())
    monkeypatch.setattr(
        hass.config_entries, "async_forward_entry_unload", AsyncMock(return_value=True)
    )
    found = ControllerIdentity("127.0.0.2", "123", "AIRHAWK", "00:a0:03:11:22:33")
    find = AsyncMock(return_value=[found])
    monkeypatch.setattr(integration, "async_find_controllers", find)
    session = ClientSession()
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: session)
    monkeypatch.setattr(ad, "async_get_clientsession", lambda _: session)
    monkeypatch.setattr(
        ad.dhcp,
        "async_discovered_service_info",
        lambda _: (
            [SimpleNamespace(ip=found.host, macaddress="00A003112233")]
            if quick_hit
            else []
        ),
    )
    context_token = current_entry.set(entry)
    entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
    try:
        assert await integration.async_setup_entry(hass, entry)
        entry._async_set_state(hass, ConfigEntryState.LOADED, None)
        await hass.async_block_till_done()
        reload.assert_not_awaited()
        find.assert_not_awaited()
        saved = entry.data[CONF_CONTROLLERS][0]
        assert saved[CONF_SERIAL_NUMBER] == "123"
        assert saved[CONF_IDENTITY_KEY] == "127.0.0.1"
        runtime = hass.data[DOMAIN][entry.entry_id]["controllers"][0]
        entities = []
        await sensor.async_setup_entry(hass, entry, entities.extend)
        ids = [entity.unique_id for entity in entities]
        assert ids[0] == "127.0.0.1:sensor:temperature"
        assert entities[0].native_value == 22.5
        device_id = dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )[0].id

        assert runtime["coordinator"].device_id == device_id

        # Another controller acquires the previous lease while HA is running.
        devices["127.0.0.1"] = "OTHER"
        requests.clear()
        await runtime["coordinator"].async_refresh()
        await hass.async_block_till_done()
        assert runtime["coordinator"].last_update_success
        assert entry.data[CONF_CONTROLLERS][0][CONF_HOST] == found.host
        assert not any(
            host == "127.0.0.1" and "temperature" in oas for host, oas in requests
        )
        assert runtime["api"].base_url == f"http://127.0.0.2:{unused_tcp_port}"
        reload.assert_not_awaited()
        assert (
            dr.async_get(hass).async_get(device_id).configuration_url
            == runtime["api"].base_url
        )

        assert await integration.async_unload_entry(hass, entry)
        await entry._async_process_on_unload(hass)
        entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
        assert await integration.async_setup_entry(hass, entry)
        after = []
        await sensor.async_setup_entry(hass, entry, after.extend)
        assert [entity.unique_id for entity in after] == ids
        assert hass.data[DOMAIN][entry.entry_id]["controllers"][0]["coordinator"].device_id == device_id
        assert (
            len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id))
            == 1
        )
        assert find.await_count == (0 if quick_hit else 1)
    finally:
        current_entry.reset(context_token)
        await integration.async_unload_entry(hass, entry)
        await session.close()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_network_scan_progress_then_selection(hass, monkeypatch):
    flow = flow_for(hass)
    found = ControllerIdentity("192.168.1.2", "123", "AIRHAWK")

    async def find(*_):
        await asyncio.sleep(0)
        return [found]

    monkeypatch.setattr(cf, "async_find_controllers", find)
    result = await flow.async_step_scan()
    assert result["type"] == FlowResultType.SHOW_PROGRESS
    await result["progress_task"]
    assert (await flow.async_step_scan())["type"] == FlowResultType.SHOW_PROGRESS_DONE
    assert (await flow.async_step_select_device())["step_id"] == "select_device"
    result = await flow.async_step_select_device({CONF_HOST: found.host})
    assert result["step_id"] == "discovery_confirm"


@pytest.mark.asyncio
async def test_empty_network_scan_explains_manual_fallback(hass, monkeypatch):
    flow = flow_for(hass)
    monkeypatch.setattr(cf, "async_find_controllers", AsyncMock(return_value=[]))
    result = await flow.async_step_scan()
    if result["type"] == FlowResultType.SHOW_PROGRESS:
        await result["progress_task"]
        await flow.async_step_scan()
    assert (await flow.async_step_select_device())["reason"] == "no_devices_found"


@pytest.mark.asyncio
async def test_user_scan_can_test_existing_pump_without_recreating_entry(
    hass, monkeypatch
):
    """The documented non-destructive discovery test includes configured pumps."""
    found = ControllerIdentity("192.168.1.2", "123", "AIRHAWK", "00:a0:03:11:22:33")
    controller = {
        CONF_HOST: found.host,
        CONF_IDENTITY_KEY: found.host,
        CONF_SERIAL_NUMBER: found.serial,
        CONF_DEVICE_MODEL: found.model,
        CONF_MAC_ADDRESS: found.mac,
        CONF_SENSORS: [{CONF_ID: "temperature", CONF_NAME: "Temperature"}],
    }
    entry = add_entry(hass, [controller], {CONF_SCAN_INTERVAL: 45})
    original_data, original_options = dict(entry.data), dict(entry.options)
    reload = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_reload", reload)
    entry.add_update_listener(_async_update_listener)
    ad.async_register_controller(hass, entry, controller, "http://192.168.1.2:80")
    device_id = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[
        0
    ].id
    monkeypatch.setattr(cf, "async_find_controllers", AsyncMock(return_value=[found]))
    flow = flow_for(hass)
    flow.context = {"source": "user"}
    result = await flow.async_step_scan()
    if result["type"] == FlowResultType.SHOW_PROGRESS:
        await result["progress_task"]
        await flow.async_step_scan()
    assert (await flow.async_step_select_device())["step_id"] == "select_device"
    result = await flow.async_step_select_device({CONF_HOST: found.host})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.data == original_data
    assert entry.options == original_options
    assert [
        device.id
        for device in dr.async_entries_for_config_entry(
            dr.async_get(hass), entry.entry_id
        )
    ] == [device_id]
    reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_quick_lookup_only_probes_saved_mac_without_subnet_scan(
    hass, monkeypatch
):
    found = ControllerIdentity("192.168.1.3", "123", "AIRHAWK", "aa:bb:cc:11:22:33")
    monkeypatch.setattr(
        ad.dhcp,
        "async_discovered_service_info",
        lambda _: [
            SimpleNamespace(ip="192.168.1.4", macaddress="00a003112233"),
            SimpleNamespace(ip=found.host, macaddress="AABBCC112233"),
            SimpleNamespace(ip=found.host, macaddress="aa-bb-cc-11-22-33"),
            SimpleNamespace(ip="192.168.1.2", macaddress="AABBCC112233"),
        ],
    )
    adapters = AsyncMock()
    monkeypatch.setattr(ad.network, "async_get_adapters", adapters)
    monkeypatch.setattr(ad, "async_get_clientsession", lambda _: None)
    probe = AsyncMock(return_value=found)
    monkeypatch.setattr(ad, "async_probe", probe)
    conn = ad.connection_from_controller({CONF_HOST: "192.168.1.2"})
    assert await ad.async_find_cached_controller(hass, conn, found.mac) == [found]
    probe.assert_awaited_once()
    assert probe.await_args.args[1].host == found.host
    adapters.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_state", ["no_mac", "empty", "not_ready"])
async def test_missing_quick_candidates_do_not_start_a_scan(
    hass, monkeypatch, cache_state
):
    def cached(_):
        if cache_state == "not_ready":
            raise KeyError("dhcp")
        return []

    monkeypatch.setattr(ad.dhcp, "async_discovered_service_info", cached)
    adapters, probe = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ad.network, "async_get_adapters", adapters)
    monkeypatch.setattr(ad, "async_probe", probe)
    monkeypatch.setattr(ad, "async_get_clientsession", lambda _: None)
    assert (
        await ad.async_find_cached_controller(
            hass,
            ad.connection_from_controller({}),
            None if cache_state == "no_mac" else "aa:bb:cc:11:22:33",
        )
        == []
    )
    adapters.assert_not_awaited()
    probe.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", ["sensor", "binary_sensor", "number", "select", "text", "switch"])
async def test_heating_circuit_registry_links_survive_reload(hass, platform, caplog):
    """Every platform reuses the correct parent and existing circuit device."""
    from datetime import timedelta
    from importlib import import_module

    from custom_components.ochsner_local_ots.coordinator import ClimatixCoordinator

    module = import_module(f"custom_components.ochsner_local_ots.{platform}")
    controllers = [
        {CONF_HOST: "192.168.1.2", CONF_IDENTITY_KEY: "old-host", CONF_SERIAL_NUMBER: "123"},
        {CONF_HOST: "192.168.1.3", CONF_IDENTITY_KEY: "serial:456", CONF_SERIAL_NUMBER: "456"},
    ]
    entry = add_entry(hass, controllers)
    registry = dr.async_get(hass)
    original_ids = []
    token = current_entry.set(entry)
    try:
        for _ in range(2):
            runtime = []
            parent_ids = []
            for ctrl in controllers:
                key = identity_key(ctrl)
                url = f"http://{ctrl[CONF_HOST]}"
                parent = ad.async_register_controller(hass, entry, ctrl, url)
                parent_ids.append(parent.id)
                coordinator = ClimatixCoordinator(
                    hass, api=None, ids=[], update_interval=timedelta(seconds=30)
                )
                coordinator.device_id = parent.id
                cfg = {
                    "id": "value", "read_id": "value", "write_id": "value",
                    "name": "Circuit value", "heating_circuit_uid": "hc1",
                    "heating_circuit_name": "Heating circuit 1",
                    "options": {"Off": 0, "On": 1}, "on_value": 1, "off_value": 0,
                }
                runtime.append({
                    "host": key, "base_url": url, "api": None,
                    "coordinator": coordinator,
                    ("switches" if platform == "switch" else f"{platform}s"): [cfg],
                })
            hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"controllers": runtime}
            entities = []
            await module.async_setup_entry(hass, entry, entities.extend)
            circuit_ids = []
            for entity in entities:
                info = entity.device_info
                if not any(":hc:" in ident for _, ident in info["identifiers"]):
                    assert "via_device" not in info
                    assert "via_device_id" not in info
                    continue
                assert "via_device" not in info
                index = len(circuit_ids)
                assert info["via_device_id"] == parent_ids[index]
                assert info["identifiers"] == {(DOMAIN, f"{identity_key(controllers[index])}:hc:hc1")}
                device = registry.async_get_or_create(config_entry_id=entry.entry_id, **info)
                assert device.via_device_id == parent_ids[index]
                circuit_ids.append(device.id)
            assert len(circuit_ids) == 2
            if original_ids:
                assert circuit_ids == original_ids
            original_ids = circuit_ids
        assert len(dr.async_entries_for_config_entry(registry, entry.entry_id)) == 4
        assert "deprecated `via_device`" not in caplog.text
    finally:
        current_entry.reset(token)
