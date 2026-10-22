"""Heat pump dashboard backend with real Home Assistant registries.

The integration is set up against an in-memory fake controller serving the
sanitized AirHawk 518 snapshot (tests/fixtures); nothing touches a network.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
pytest_asyncio = pytest.importorskip("pytest_asyncio")

from homeassistant.config_entries import ConfigEntries, ConfigEntry, ConfigEntryState, current_entry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import frame
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import slugify

from custom_components import ochsner_local_ots as integration
from custom_components.ochsner_local_ots import dashboard, plant_api
from custom_components.ochsner_local_ots import plant as P
from custom_components.ochsner_local_ots.catalog import load_catalog
from custom_components.ochsner_local_ots.const import (
    CONF_CONTROLLERS,
    CONF_DEVICE_MODEL,
    CONF_DISCOVERY_SOURCE,
    CONF_HOST,
    CONF_IDENTITY_KEY,
    CONF_PLANT_NAME,
    CONF_PORT,
    DISCOVERY_SOURCE_LOCAL,
    DOMAIN,
    SIGNAL_PLANTS_CHANGED,
)
from custom_components.ochsner_local_ots.discovery import async_scan
from custom_components.ochsner_local_ots.discovery_entities import build_entities

KEY = "192.0.2.10"
OP1, CE1, SEASON1, OFF1 = "AiIQvY58IgE=", "AiPKUI58AAE=", "AiMN7I58AAE=", "ACNZw458AAE="
SEASON2, OP2 = "AiMN7O1MAAE=", "AiIQve1MIgE="
PROG, CE, BOOST = "AiIQvXXZIgE=", "AiPKUHXZAAE=", "AiOzyHXZAAE="


@pytest_asyncio.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    instance.config_entries = ConfigEntries(instance, {})
    instance.config.language = "de"
    frame.async_setup(instance)
    dr.async_setup(instance)
    await dr.async_load(instance, load_empty=True)
    await er.async_load(instance, load_empty=True)
    await ir.async_load(instance, load_empty=True)
    yield instance
    await instance.async_stop(force=True)


def add_entry(hass, data, options=None, minor_version=2):
    entry = ConfigEntry(
        domain=DOMAIN,
        title="AIRHAWK518C11A",
        version=1,
        minor_version=minor_version,
        unique_id=f"{DOMAIN}:local:{KEY}",
        source="user",
        data=data,
        options=options if options is not None else {"sidebar_panel": True, "color_scheme": "ha"},
        discovery_keys={},
        subentries_data=None,
    )
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


def dhw_plant(fake):
    """Turn the snapshot into a plant with DHW (values from the app's DTOs)."""
    fake.set("ESMSIwAAAAE=", 256)
    for oid, v in {
        "AyKOLyiJAAE=": 48.5, "CiMzqnXZAAE=": 50.0, "CyMognXZAAE=": 0, PROG: 0, CE: 2, BOOST: 0,
        "ACPOwnXZAAE=": 0, "ASMv1XXZAAE=": 50.0, "ASOp1XXZAAE=": 45.0, "ASOXCHXZAAE=": 40.0,
        "ASPuhXXZAAE=": 60.0,
    }.items():
        fake.set(oid, v)


def cooling_hc2(fake):
    fake.set("ESMOlAAAAAE=", 27 | 4)


async def setup_plant(hass, monkeypatch, fake_controller, mutate=None, options=None):
    """Scan the fake controller like onboarding does, then run real setup."""
    fake = fake_controller()
    if mutate:
        mutate(fake)
    catalog = load_catalog()
    scan = await async_scan(fake, catalog, gap_sec=0)
    ents = build_entities(catalog=catalog, scan=scan, hc_fallback_template="Heizkreis {n}", language="de")
    controller = {
        CONF_HOST: KEY, CONF_PORT: 80, CONF_IDENTITY_KEY: KEY,
        CONF_PLANT_NAME: f"{scan.plant_model} ({KEY})", CONF_DEVICE_MODEL: scan.plant_model,
        CONF_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LOCAL,
        **ents,
    }
    entry = add_entry(hass, {CONF_CONTROLLERS: [controller]}, options=options)
    fake.base_url = f"http://{KEY}:80"
    fake.reads.clear()
    monkeypatch.setattr(integration, "ClimatixGenericApi", lambda *a, **kw: fake)
    monkeypatch.setattr(integration, "async_probe", AsyncMock(return_value=None))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: None)
    forward = AsyncMock()
    monkeypatch.setattr(hass.config_entries, "async_forward_entry_setups", forward)
    reload = Mock()  # section changes schedule a reload; never run one here
    monkeypatch.setattr(hass.config_entries, "async_schedule_reload", reload)
    token = current_entry.set(entry)
    entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
    try:
        assert await integration.async_setup_entry(hass, entry)
    finally:
        current_entry.reset(token)
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    runtime = hass.data[DOMAIN][entry.entry_id]
    entities = await create_entities(hass, entry, runtime["platforms"])
    return SimpleNamespace(
        fake=fake, entry=entry, runtime=runtime, ctrl=runtime["controllers"][0],
        entities=entities, forward=forward, reload=reload,
    )


async def create_entities(hass, entry, platforms):
    """Run the platforms and put their entities in the entity registry."""
    from importlib import import_module

    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    out = {}
    for platform in platforms:
        module = import_module(f"custom_components.ochsner_local_ots.{platform}")
        created = []
        token = current_entry.set(entry)
        try:
            await module.async_setup_entry(hass, entry, created.extend)
        finally:
            current_entry.reset(token)
        for ent in created:
            info = ent.device_info
            device = devices.async_get_or_create(config_entry_id=entry.entry_id, **info) if info else None
            reg = registry.async_get_or_create(
                platform, DOMAIN, ent.unique_id, config_entry=entry,
                suggested_object_id=slugify(ent.unique_id),
                device_id=device.id if device else None,
                disabled_by=(
                    er.RegistryEntryDisabler.INTEGRATION
                    if ent.entity_registry_enabled_default is False else None
                ),
            )
            ent.entity_id = reg.entity_id
            out[ent.unique_id] = ent
    return out


async def teardown(hass, plant):
    monkey_unload = AsyncMock(return_value=True)
    hass.config_entries.async_unload_platforms = monkey_unload
    await integration.async_unload_entry(hass, plant.entry)


# --- setup / polling ----------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_polls_present_roles_and_adds_entities(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        platforms = plant.runtime["platforms"]
        assert "climate" in platforms
        assert "water_heater" not in platforms and "button" not in platforms
        coordinator = plant.ctrl["coordinator"]
        ids = set(coordinator.ids)
        for role, oid in P.role_ids(P.compute_capabilities(plant.fake.values)).items():
            assert oid in ids, role
        assert "CyPmLyssAAE=" not in ids  # HC4 absent: its roles are not polled
        # (The DHW emergency status has a catalog sensor of its own, polled anyway.)
        assert not any(
            oid in ids for role, oid in P.DHW_ROLES.items()
            if oid not in P.capability_ids() and role != "dhw.emergency_status"
        )
        caps = plant.ctrl["plant"]["caps"]
        assert [c["index"] for c in caps["circuits"]] == [1, 2, 3]

        # Climate per present circuit, attached to the existing circuit device.
        climate = plant.entities[f"{KEY}:climate:hc1"]
        assert set(u for u in plant.entities if ":climate:" in u) == {
            f"{KEY}:climate:hc{n}" for n in (1, 2, 3)
        }
        assert climate.device_info["identifiers"] == {(DOMAIN, f"{KEY}:hc:tag:31886")}
        sensor_on_hc1 = next(
            e for u, e in plant.entities.items()
            if u == f"{KEY}:number:ACO38I58AAE"
        )
        assert sensor_on_hc1.device_info["identifiers"] == climate.device_info["identifiers"]
        assert climate.device_info["name"] == "Fußboden"

        # Supplements: runtime only, standard unique_ids, never persisted.
        assert f"{KEY}:select:AiPKUI58AAE" in plant.entities
        assert f"{KEY}:number:ACNZw458AAE" in plant.entities
        stored = plant.entry.data[CONF_CONTROLLERS][0]
        assert not any(s.get("read_id") == CE1 for s in stored["selects"])
        sup = plant.entities[f"{KEY}:select:AiPKUI58AAE"]
        assert sup.options == ["Komfort", "Eco"] and sup.current_option == "Komfort"
        assert sup.device_info["identifiers"] == {(DOMAIN, f"{KEY}:hc:tag:31886")}
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_existing_unique_ids_unchanged(hass, monkeypatch, fake_controller):
    """The new feature only adds: every stored entity keeps its unique_id."""
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        stored = plant.entry.data[CONF_CONTROLLERS][0]
        expected = set()
        for key, domain in (("sensors", "sensor"), ("numbers", "number"), ("selects", "select"),
                            ("switches", "switch"), ("texts", "text"), ("binary_sensors", "binary_sensor")):
            for cfg in stored.get(key, []):
                rid = cfg.get("id") if domain in ("sensor", "binary_sensor") else cfg["read_id"]
                expected.add(f"{KEY}:{domain}:{rid}".replace("=", ""))
        assert expected <= set(plant.entities)
        new = set(plant.entities) - expected
        assert {u for u in new if ":climate:" not in u and u.split(":")[1] in ("number", "select")} == {
            f"{KEY}:select:AiPKUI58AAE", f"{KEY}:select:AiPKUO1MAAE", f"{KEY}:select:AiPKUMxcAAE",
            f"{KEY}:number:ACNZw458AAE", f"{KEY}:number:ACNZw+1MAAE", f"{KEY}:number:ACNZw8xcAAE",
        }
    finally:
        await teardown(hass, plant)


# --- climate ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_climate_state_heat_only(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        assert [str(m) for m in c.hvac_modes] == ["off", "heat"]
        assert c.hvac_mode == "heat"
        assert c.preset_mode == "comfort" and c.preset_modes == ["comfort", "eco"]
        assert c.hvac_action == "heating"  # badge 3 auto_comfort_red
        assert c.target_temperature == 21.5  # selector 2 -> room target heating
        assert (c.min_temp, c.max_temp) == (18.5, 24.5)
        assert c.target_temperature_step == 0.5
        assert c.current_temperature is None  # no room sensor bit
        attrs = c.extra_state_attributes
        assert attrs["badge"] == "auto_comfort_red"
        assert attrs["operating_program"] == "Komfort"
        assert attrs["room_offset"] == 0.0
        assert attrs["flow_temperature"] == pytest.approx(25.9304)
        assert attrs["flow_target"] == pytest.approx(26.7993)
        assert isinstance(attrs["circuit_status"], str) and attrs["circuit_status"] != "2"
        c3 = plant.entities[f"{KEY}:climate:hc3"]
        assert c3.target_temperature == 10.0  # Wintergarten: frost setpoint

        # HA's own state rendering works with these values.
        c.hass = hass
        assert c.state == "heat"
        caps = c.capability_attributes
        assert caps["min_temp"] == 18.5 and caps["max_temp"] == 24.5
        assert caps["preset_modes"] == ["comfort", "eco"]
        state = c.state_attributes
        assert state["temperature"] == 21.5 and state["hvac_action"] == "heating"
        assert state["preset_mode"] == "comfort"
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_climate_write_sequences(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    fake = plant.fake
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        fake.writes.clear()
        await c.async_set_preset_mode("eco")  # app SetEco (op already 0)
        assert fake.writes == [(CE1, 1)]
        assert c.preset_mode == "eco"
        # Touched and dependent ids are re-read once.
        assert {CE1, OP1, "CyPmL458AAE=", "CiPcW458AAE="} <= set(fake.reads[-1])

        fake.writes.clear()
        await c.async_set_preset_mode("eco")
        assert fake.writes == []  # unchanged -> no flash write

        await c.async_turn_off()  # app ToggleOff
        assert fake.writes == [(OP1, 1)] and c.hvac_mode == "off"
        fake.writes.clear()
        await c.async_set_preset_mode("comfort")  # selecting a preset turns it on
        assert fake.writes == [(OP1, 0), (CE1, 0)]
        fake.writes.clear()
        await c.async_set_hvac_mode("off")
        await c.async_set_hvac_mode("heat")  # heat-only: season untouched
        assert fake.writes == [(OP1, 1), (OP1, 0)]
        fake.writes.clear()
        await c.async_turn_on()
        assert fake.writes == []

        # −/+ semantics: the room setpoint correction, ±3 K in 0.5 steps.
        await c.async_set_temperature(temperature=22.5)
        assert fake.writes == [(OFF1, 1.0)]
        assert c.target_temperature == 21.5  # the fake does not recompute targets
        fake.set("CiPcW458AAE=", 22.5)  # the controller applies the correction
        await plant.ctrl["coordinator"].async_refresh_ids(["CiPcW458AAE="])
        assert (c.min_temp, c.max_temp) == (18.5, 24.5)  # base stays 21.5
        fake.writes.clear()
        await c.async_set_temperature(temperature=22.6)  # rounds to the current step
        assert fake.writes == []

        # Every write went through the counted write path (flash counter).
        assert plant.runtime["write_counts"][KEY] == 7
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_climate_cooling_circuit(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=cooling_hc2)
    fake = plant.fake
    try:
        c = plant.entities[f"{KEY}:climate:hc2"]
        assert [str(m) for m in c.hvac_modes] == ["off", "heat", "cool", "heat_cool"]
        assert c.hvac_mode == "heat_cool"  # season 4
        fake.writes.clear()
        await c.async_set_hvac_mode("cool")  # app SetSummer
        assert fake.writes == [(SEASON2, 2)] and c.hvac_mode == "cool"
        await c.async_set_hvac_mode("heat")  # app SetWinter
        await c.async_turn_off()
        fake.writes.clear()
        await c.async_set_hvac_mode("heat_cool")  # app SetAuto from off
        assert fake.writes == [(OP2, 0), (SEASON2, 4)]
        assert plant.ctrl["plant"]["caps"]["cooling"] is True
    finally:
        await teardown(hass, plant)


# --- water heater / button --------------------------------------------------


@pytest.mark.asyncio
async def test_dhw_water_heater_and_boost(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=dhw_plant)
    fake = plant.fake
    try:
        assert {"water_heater", "button"} <= set(plant.runtime["platforms"])
        wh = plant.entities[f"{KEY}:water_heater:dhw"]
        button = plant.entities[f"{KEY}:button:dhw_boost"]
        assert wh.device_info["identifiers"] == {(DOMAIN, KEY)}
        assert wh.operation_list == ["off", "eco", "performance", "high_demand"]
        assert wh.current_operation == "performance"
        assert wh.current_temperature == 48.5
        assert wh.target_temperature == 50.0  # comfort setpoint
        assert (wh.min_temp, wh.max_temp) == (5.0, 75.0)
        assert wh.extra_state_attributes == {"offset": 0.0, "effective_target": 50.0, "boost": False}

        fake.writes.clear()
        await wh.async_set_operation_mode("eco")
        assert fake.writes == [(CE, 1)]
        assert wh.current_operation == "eco" and wh.target_temperature == 45.0
        fake.writes.clear()
        await wh.async_set_temperature(temperature=46.3)
        assert fake.writes == [("ASOp1XXZAAE=", 46.5)]  # the eco setpoint, 0.5 steps
        fake.writes.clear()
        await wh.async_set_operation_mode("high_demand")
        assert fake.writes == [(BOOST, 1)] and wh.current_operation == "high_demand"
        assert (wh.min_temp, wh.max_temp) == (40.0, 75.0)
        fake.writes.clear()
        await wh.async_set_temperature(temperature=30)
        assert fake.writes == [("ASPuhXXZAAE=", 40.0)]  # boost bounds
        fake.writes.clear()
        await wh.async_set_operation_mode("performance")
        assert fake.writes == [(CE, 2), (BOOST, 0)]
        fake.writes.clear()
        await wh.async_turn_off()
        assert fake.writes == [(PROG, 1)] and wh.current_operation == "off"
        fake.writes.clear()
        await button.async_press()  # program on, then boost
        assert fake.writes == [(PROG, 0), (BOOST, 1)]
        fake.writes.clear()
        await button.async_press()
        assert fake.writes == []  # already boosting

        wh.hass = hass
        assert wh.state == "high_demand"
        assert wh.capability_attributes["target_temp_step"] == 0.5
        assert wh.capability_attributes["operation_list"] == ["off", "eco", "performance", "high_demand"]
        assert wh.state_attributes["temperature"] == 40.0  # boost setpoint after the clamp

        # DHW supplements exist next to the catalog's read-only sensors.
        assert f"{KEY}:number:ACPOwnXZAAE" in plant.entities
        assert f"{KEY}:select:AiPKUHXZAAE" in plant.entities
        assert f"{KEY}:sensor:ACPOwnXZAAE" in plant.entities
    finally:
        await teardown(hass, plant)


# --- websocket ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ws_plants_payload_against_fixture(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        result = await plant_api.async_build_plants(hass, "de")
        json.dumps(result)  # JSON-serializable
        assert result["version"] == 1
        assert result["settings"] == {"color_scheme": "ha"}
        (p,) = result["plants"]
        assert p["plant_id"] == f"{plant.entry.entry_id}:{KEY}"
        assert p["entry_id"] == plant.entry.entry_id and p["host"] == KEY
        assert p["device_id"] == plant.ctrl["coordinator"].device_id
        assert p["model"] == "AIRHAWK518C11A"
        assert p["available"] is True
        assert p["capabilities"] == {
            "bms": False, "dhw": False, "buffer": True, "buffer_temp": True, "aux": True,
            "aux_temp": True, "buffer_aux_heater": False, "pool": False, "silent": True,
            "emergency": True, "power": True, "cooling": False,
        }
        assert [(c["index"], c["name"], c["heat"], c["cool"]) for c in p["circuits"]] == [
            (1, "Fußboden", True, False), (2, "Radiatoren", True, False), (3, "Wintergarten", True, False),
        ]
        reg = er.async_get(hass)
        c1 = p["circuits"][0]
        assert c1["climate"] == reg.async_get_entity_id("climate", DOMAIN, f"{KEY}:climate:hc1")
        assert c1["device_id"] == dr.async_get(hass).async_get_device_by_identifier(
            (DOMAIN, f"{KEY}:hc:tag:31886"), plant.entry.entry_id
        ).id
        assert c1["room_sensor"] is False and c1["humidity"] is False
        assert p["dhw"] is None
        assert p["badges"][0] == "off_grey" and p["badges"][-1] == "vacation_blue" and len(p["badges"]) == 23
        assert p["flash_writes"] == reg.async_get_entity_id("sensor", DOMAIN, f"{KEY}:flash_writes")

        roles = p["roles"]
        assert roles["hp.outdoor"] == {
            "entity_id": reg.async_get_entity_id("sensor", DOMAIN, f"{KEY}:sensor:AyKWuiiJAAE"),
            "unit": "°C",
        }
        assert roles["hp.status"]["labels"]["1"] == "Heizen"
        assert roles["hp.status"]["labels"]["0"] == "Aus"
        hp_program = roles["hp.program"]
        assert hp_program["writable"] is True
        assert hp_program["options"] == {
            "0": "Automatik", "1": "Handbetrieb Heizen", "2": "Handbetrieb Kühlen", "3": "Abgeschaltet",
        }
        # The option strings are exactly what the select entity accepts.
        select = plant.entities[f"{KEY}:select:AiMu5MdZAAE"]
        assert set(hp_program["options"].values()) == set(select.options)
        flow = roles["hc1.heat_curve_norm_flow"]
        assert flow["writable"] is True and flow["unit"] == "°C"
        assert (flow["min"], flow["max"], flow["step"]) == (20.0, 90.0, 0.1)
        assert roles["hc1.badge"] == {"entity_id": None}
        assert roles["hc1.comfort_eco"]["options"] == {"0": "Komfort", "1": "Eco"}
        assert roles["hc1.room_offset"]["writable"] is True
        assert (roles["hc1.room_offset"]["min"], roles["hc1.room_offset"]["max"]) == (-3.0, 3.0)
        # Registry-disabled entity: still listed, flagged.
        assert roles["hc1.season"]["enabled"] is False
        assert roles["hc1.season"]["writable"] is True
        assert "enabled" not in roles["hp.program"]
        # Only roles of present sections.
        assert not any(r.startswith(("dhw.", "pool.", "hc4.")) and r != "hc4.availability" for r in roles)
        assert "hc3.heat_curve_base_flow" in roles

        # Emergency mode status: catalog enum labels, bilingual.
        assert roles["hp.emergency_status"]["labels"] == {
            "0": "Passiv", "1": "Reduziert, nicht bestätigt", "2": "Reduziert, bestätigt",
            "3": "Standard, bestätigt", "4": "Anlagenstörung",
        }
        assert "dhw.emergency_status" not in roles  # no DHW on this plant

        en = (await plant_api.async_build_plants(hass, "en"))["plants"][0]["roles"]
        assert en["hp.emergency_status"]["labels"] == {
            "0": "Passive", "1": "Reduced, unconfirmed", "2": "Reduced, confirmed",
            "3": "Standard, confirmed", "4": "Plant fault",
        }
        assert en["hp.status"]["labels"]["1"] != "Heizen"
        assert en["hc1.op_program"]["labels"]["1"] == "Off"
        assert en["hp.program"]["options"] == hp_program["options"]  # entity strings, not translated
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_ws_plants_dhw_and_handler(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=dhw_plant)
    try:
        conn = FakeConnection()
        plant_api.ws_plants(hass, conn, {"id": 7, "type": f"{DOMAIN}/plants"})
        await hass.async_block_till_done(wait_background_tasks=True)
        ((msg_id, result),) = conn.results
        assert msg_id == 7
        (p,) = result["plants"]
        reg = er.async_get(hass)
        assert p["capabilities"]["dhw"] is True
        assert p["dhw"] == {
            "water_heater": reg.async_get_entity_id("water_heater", DOMAIN, f"{KEY}:water_heater:dhw"),
            "boost_button": reg.async_get_entity_id("button", DOMAIN, f"{KEY}:button:dhw_boost"),
        }
        assert p["roles"]["dhw.comfort_eco"]["options"] == {"1": "Eco", "2": "Komfort"}
        assert p["roles"]["dhw.setpoint_boost"]["min"] == 40.0
        assert p["roles"]["dhw.offset"]["step"] == 3.0
        assert p["roles"]["dhw.emergency_status"]["labels"]["4"] == "Anlagenstörung"
        conn = FakeConnection()
        plant_api.ws_subscribe(hass, conn, {"id": 8, "type": f"{DOMAIN}/subscribe"})
        values = conn.messages[0]["event"]["values"]
        assert values["dhw.emergency_status"] == 0 and values["hp.emergency_status"] == 0
        conn.subscriptions[8]()
    finally:
        await teardown(hass, plant)


class FakeConnection:
    def __init__(self, user=None):
        self.user = user
        self.results = []
        self.messages = []
        self.subscriptions = {}

    def send_result(self, msg_id, result=None):
        self.results.append((msg_id, result))

    def send_message(self, msg):
        self.messages.append(msg)


@pytest.mark.asyncio
async def test_ws_subscribe_pushes_on_updates(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        conn = FakeConnection()
        plant_api.ws_subscribe(hass, conn, {"id": 3, "type": f"{DOMAIN}/subscribe"})
        assert conn.results == [(3, None)]
        (first,) = conn.messages
        assert first["id"] == 3 and first["type"] == "event"
        event = first["event"]
        assert event["plant_id"] == f"{plant.entry.entry_id}:{KEY}"
        assert event["available"] is True and event["updated"]
        assert event["capabilities"]["buffer"] is True
        values = event["values"]
        assert values["hp.outdoor"] == pytest.approx(12.1851)
        assert values["hp.status"] == 1 and values["hc1.badge"] == 3 and values["hc1.season"] == 4
        assert values["hc1.name"] == "Fußboden" and values["avail.dhw"] == 0
        assert values["hp.emergency_status"] == 0 and "dhw.emergency_status" not in values
        assert "hc1.room_temp" not in values
        json.dumps(first)

        coordinator = plant.ctrl["coordinator"]
        data = dict(coordinator.data)
        data["values"] = dict(data["values"], **{"AyKWuiiJAAE=": [3.5, 3.5], "ESMSIwAAAAE=": [256, 256]})
        coordinator.async_set_updated_data(data)
        last = conn.messages[-1]["event"]
        assert len(conn.messages) == 2
        assert last["values"]["hp.outdoor"] == 3.5
        assert last["capabilities"]["dhw"] is True  # recomputed per update

        async_dispatcher_send(hass, SIGNAL_PLANTS_CHANGED)
        await hass.async_block_till_done()
        assert len(conn.messages) == 3

        conn.subscriptions[3]()  # unsubscribe
        coordinator.async_set_updated_data(data)
        async_dispatcher_send(hass, SIGNAL_PLANTS_CHANGED)
        await hass.async_block_till_done()
        assert len(conn.messages) == 3
        # Not fresh readings of the section points: no reload.
        plant.reload.assert_not_called()
    finally:
        await teardown(hass, plant)


# --- frontend + panel ---------------------------------------------------------


@pytest.fixture
def frontend_env(hass, monkeypatch, tmp_path):
    from homeassistant.components import frontend

    folder = tmp_path / "frontend"
    (folder / "lib").mkdir(parents=True)
    (folder / "ochsner-heatpump.js").write_text("// placeholder\n")
    (folder / "lib" / "a.js").write_text("export const a = 1;\n")
    monkeypatch.setattr(dashboard, "FRONTEND_DIR", folder)
    http = SimpleNamespace(async_register_static_paths=AsyncMock())
    monkeypatch.setattr(hass, "http", http, raising=False)
    extra = []
    monkeypatch.setattr(frontend, "add_extra_js_url", lambda _h, url, es5=False: extra.append(url))
    return SimpleNamespace(folder=folder, http=http, extra=extra)


@pytest.mark.asyncio
async def test_frontend_served_versioned_once(hass, frontend_env):
    assert await integration.async_setup(hass, {})
    assert await integration.async_setup(hass, {})  # idempotent
    (call,) = frontend_env.http.async_register_static_paths.await_args_list
    (cfg,) = call.args[0]
    version = dashboard.frontend_version(frontend_env.folder)
    assert version.startswith("2.2.0-")
    assert cfg.url_path == f"/{DOMAIN}/frontend/{version}"
    assert cfg.path == str(frontend_env.folder) and cfg.cache_headers is True
    assert frontend_env.extra == [f"/{DOMAIN}/frontend/{version}/ochsner-heatpump.js"]
    # Any file change (incl. relative imports) changes the URL.
    (frontend_env.folder / "lib" / "a.js").write_text("export const a = 2;\n")
    assert dashboard.frontend_version(frontend_env.folder) != version
    # Websocket commands are registered at component level.
    from homeassistant.components import websocket_api

    handlers = hass.data[websocket_api.DOMAIN]
    assert f"{DOMAIN}/plants" in handlers and f"{DOMAIN}/subscribe" in handlers


def _panels(hass):
    from homeassistant.components.frontend import DATA_PANELS

    return hass.data.get(DATA_PANELS, {})


def loaded(hass, options):
    entry = add_entry(hass, {}, options=options)
    entry._async_set_state(hass, ConfigEntryState.LOADED, None)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"controllers": [], "platforms": ["sensor"]}
    return entry


@pytest.mark.asyncio
async def test_panel_register_remove_on_options_and_unload(hass, frontend_env, monkeypatch):
    await integration.async_setup(hass, {})
    entry = loaded(hass, {"sidebar_panel": True, "color_scheme": "ha"})
    await dashboard.async_sync_panel(hass)
    await dashboard.async_sync_panel(hass)  # no double registration error
    panel = _panels(hass)["ochsner-heatpump"]
    assert panel.sidebar_title == "Wärmepumpe" and panel.sidebar_icon == "mdi:heat-pump"
    assert panel.require_admin is False
    custom = panel.config["_panel_custom"]
    assert custom["name"] == "ochsner-heatpump-panel"
    assert custom["module_url"] == frontend_env.extra[0]

    # Options change (the options flow reloads the entry): switched off.
    hass.config_entries.async_update_entry(entry, options={"sidebar_panel": False, "color_scheme": "ha"})
    await dashboard.async_sync_panel(hass)
    assert "ochsner-heatpump" not in _panels(hass)

    # A second loaded entry that wants the panel keeps it registered ...
    other = loaded(hass, {"sidebar_panel": True, "color_scheme": "ochsner"})
    await dashboard.async_sync_panel(hass)
    assert "ochsner-heatpump" in _panels(hass)
    # ... until it is unloaded.
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))
    assert await integration.async_unload_entry(hass, other)
    assert "ochsner-heatpump" not in _panels(hass)
    await dashboard.async_sync_panel(hass)  # removing twice is harmless


@pytest.mark.asyncio
async def test_panel_title_english_and_reload_cycle(hass, frontend_env, monkeypatch):
    hass.config.language = "en"
    await integration.async_setup(hass, {})
    entry = loaded(hass, {})  # missing option = default on
    await dashboard.async_sync_panel(hass)
    assert _panels(hass)["ochsner-heatpump"].sidebar_title == "Heat pump"
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True))
    assert await integration.async_unload_entry(hass, entry)
    assert "ochsner-heatpump" not in _panels(hass)
    # Reload: loaded again -> registered again without errors.
    entry = loaded(hass, {"sidebar_panel": True})
    await dashboard.async_sync_panel(hass)
    assert "ochsner-heatpump" in _panels(hass)


@pytest.mark.asyncio
async def test_setup_entry_registers_panel_and_unload_removes_it(hass, monkeypatch, fake_controller, frontend_env):
    await integration.async_setup(hass, {})
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    assert "ochsner-heatpump" in _panels(hass)
    await teardown(hass, plant)
    assert "ochsner-heatpump" not in _panels(hass)
    plant = await setup_plant(hass, monkeypatch, fake_controller, options={"sidebar_panel": False, "color_scheme": "ochsner"})
    try:
        assert "ochsner-heatpump" not in _panels(hass)
        assert (await plant_api.async_build_plants(hass, None))["settings"] == {"color_scheme": "ochsner"}
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_unload_only_unloads_loaded_platforms(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    unload = AsyncMock(return_value=True)
    monkeypatch.setattr(hass.config_entries, "async_unload_platforms", unload)
    assert await integration.async_unload_entry(hass, plant.entry)
    platforms = unload.await_args.args[1]
    assert "climate" in platforms and "water_heater" not in platforms and "button" not in platforms
    assert platforms == plant.forward.await_args.args[1]


# --- migration ----------------------------------------------------------------


def _notifications(hass):
    from homeassistant.components import persistent_notification

    return persistent_notification._async_get_or_create_notifications(hass)


@pytest.mark.asyncio
async def test_migration_enables_panel_and_notifies_once(hass):
    keep = {"entity_overrides": {"x": {"unit": "K"}}, "scan_interval": 15}
    a = add_entry(hass, {CONF_CONTROLLERS: []}, options=dict(keep), minor_version=1)
    b = add_entry(hass, {CONF_CONTROLLERS: []}, options={"sidebar_panel": False}, minor_version=1)
    assert await integration.async_migrate_entry(hass, a)
    assert a.minor_version == 2 and a.version == 1
    assert a.options == {**keep, "sidebar_panel": True, "color_scheme": "ha"}
    assert await integration.async_migrate_entry(hass, b)
    assert b.options["sidebar_panel"] is False  # an explicit choice is kept
    notes = _notifications(hass)
    assert list(notes) == [dashboard.NOTIFICATION_ID]  # one notification for all entries
    note = notes[dashboard.NOTIFICATION_ID]
    assert "Seitenleiste" in note["message"] and "Konfigurieren" in note["message"]

    # Later restarts: already migrated -> no repeat.
    notes.clear()
    assert await integration.async_migrate_entry(hass, a)
    assert _notifications(hass) == {}


@pytest.mark.asyncio
async def test_migration_english_and_future_version(hass):
    hass.config.language = "en"
    a = add_entry(hass, {CONF_CONTROLLERS: []}, options={}, minor_version=1)
    assert await integration.async_migrate_entry(hass, a)
    msg = _notifications(hass)[dashboard.NOTIFICATION_ID]["message"]
    assert "sidebar" in msg and "Configure" in msg and "card" in msg
    future = ConfigEntry(
        domain=DOMAIN, title="x", version=2, minor_version=1, unique_id="f", source="user",
        data={}, options={}, discovery_keys={}, subentries_data=None,
    )
    assert not await integration.async_migrate_entry(hass, future)


@pytest.mark.asyncio
async def test_write_failure_raises_and_rereads(hass, monkeypatch, fake_controller):
    from homeassistant.exceptions import HomeAssistantError

    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        plant.fake.write = AsyncMock(side_effect=RuntimeError("Controller error during write"))
        plant.fake.reads.clear()
        with pytest.raises(HomeAssistantError):
            await c.async_set_preset_mode("eco")
        assert CE1 in plant.fake.reads[-1]  # state re-read after the failed write
        assert plant.runtime["write_counts"].get(KEY, 0) == 0  # nothing counted
        assert c.preset_mode == "comfort"
    finally:
        await teardown(hass, plant)


# --- overlapping commands -----------------------------------------------------


def yielding_controller(fake_controller):
    """A fake whose reads and writes suspend, so overlapping commands interleave."""

    class Yielding(fake_controller):
        async def read(self, ids, *, on_http_request=None):
            await asyncio.sleep(0)
            return await super().read(ids, on_http_request=on_http_request)

        async def write(self, oid, value):
            await asyncio.sleep(0)
            out = await super().write(oid, value)
            await asyncio.sleep(0)
            return out

    return Yielding


@pytest.mark.asyncio
async def test_overlapping_commands_write_each_change_once(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, yielding_controller(fake_controller))
    fake = plant.fake
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        sup_select = plant.entities[f"{KEY}:select:AiPKUI58AAE"]
        sup_offset = plant.entities[f"{KEY}:number:ACNZw458AAE"]
        assert c.preset_mode == "comfort"

        # Two overlapping Eco requests from Comfort: one flash write.
        fake.writes.clear()
        await asyncio.gather(c.async_set_preset_mode("eco"), c.async_set_preset_mode("eco"))
        assert fake.writes == [(CE1, 1)]

        # Comfort right after Eco: the later command is not dropped as "unchanged".
        fake.writes.clear()
        await asyncio.gather(c.async_set_preset_mode("comfort"), c.async_set_preset_mode("eco"))
        await asyncio.gather(c.async_set_preset_mode("comfort"))
        assert fake.writes == [(CE1, 0), (CE1, 1), (CE1, 0)]
        assert c.preset_mode == "comfort"

        # Supplement entities share the same serialization.
        fake.writes.clear()
        await asyncio.gather(sup_select.async_select_option("Eco"), c.async_set_preset_mode("eco"))
        assert fake.writes == [(CE1, 1)]
        fake.writes.clear()
        await asyncio.gather(c.async_set_preset_mode("comfort"), sup_select.async_select_option("Eco"))
        assert fake.writes == [(CE1, 0), (CE1, 1)]
        assert sup_select.current_option == "Eco"

        fake.writes.clear()
        await asyncio.gather(c.async_set_temperature(temperature=22.5), sup_offset.async_set_native_value(1.0))
        assert fake.writes == [(OFF1, 1.0)]

        # Off then on, overlapping: both happen, in order.
        fake.writes.clear()
        await asyncio.gather(c.async_turn_off(), c.async_turn_on(), c.async_turn_on())
        assert fake.writes == [(OP1, 1), (OP1, 0)]
        assert c.hvac_mode == "heat"
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_overlapping_dhw_commands(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, yielding_controller(fake_controller), mutate=dhw_plant)
    fake = plant.fake
    try:
        wh = plant.entities[f"{KEY}:water_heater:dhw"]
        button = plant.entities[f"{KEY}:button:dhw_boost"]
        fake.writes.clear()
        await asyncio.gather(button.async_press(), button.async_press(), wh.async_set_operation_mode("high_demand"))
        assert fake.writes == [(BOOST, 1)]
        fake.writes.clear()
        # The setpoint follows the mode that is active when the write runs.
        await asyncio.gather(wh.async_set_operation_mode("eco"), wh.async_set_temperature(temperature=44))
        assert fake.writes == [(CE, 1), (BOOST, 0), ("ASOp1XXZAAE=", 44.0)]
    finally:
        await teardown(hass, plant)


# --- polls overlapping commands ------------------------------------------------


def held_poll_controller(fake_controller):
    """A fake whose next read answers with the values of the moment it was
    sent but returns only once released (a slow poll in flight)."""

    class Held(fake_controller):
        hold: "asyncio.Event | None" = None
        held = None

        async def read(self, ids, *, on_http_request=None):
            out = await super().read(ids, on_http_request=on_http_request)
            gate, self.hold = self.hold, None
            if gate is not None:
                self.held = list(ids)
                await gate.wait()
            return out

    return Held


async def _start_held_poll(hass, coordinator, fake):
    fake.hold = asyncio.Event()
    gate = fake.hold
    task = hass.async_create_task(coordinator.async_refresh())
    for _ in range(20):
        await asyncio.sleep(0)
        if fake.held is not None:
            break
    assert fake.held is not None
    return gate, task


@pytest.mark.asyncio
@pytest.mark.parametrize("polled", [True, False])
async def test_poll_in_flight_never_undoes_a_command(hass, monkeypatch, fake_controller, polled):
    plant = await setup_plant(hass, monkeypatch, held_poll_controller(fake_controller))
    fake, coordinator = plant.fake, plant.ctrl["coordinator"]
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        assert c.preset_mode == "comfort"
        # polled: the slow poll read comfort/eco (Comfort) before the command;
        # not polled: it skipped the id and merges onto its old snapshot.
        state = coordinator._poll_state[CE1]
        state.counter = 0 if polled else 10
        gate, task = await _start_held_poll(hass, coordinator, fake)
        assert (CE1 in fake.held) is polled

        fake.writes.clear()
        await c.async_set_preset_mode("eco")
        assert fake.writes == [(CE1, 1)] and c.preset_mode == "eco"

        gate.set()
        await task
        assert coordinator.last_update_success
        assert c.preset_mode == "eco"  # the older poll does not restore Comfort
        await c.async_set_preset_mode("eco")
        assert fake.writes == [(CE1, 1)]  # ... so Eco is not written again
        # The poll's other values are published.
        assert coordinator.data["values"][OP1] == fake.values[OP1]
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_reads_publish_newest_first_value(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, held_poll_controller(fake_controller))
    fake, coordinator = plant.fake, plant.ctrl["coordinator"]
    try:
        coordinator._poll_state[OFF1].counter = 0
        gate, task = await _start_held_poll(hass, coordinator, fake)
        # A targeted read started after the poll wins over it ...
        fake.set(OFF1, 1.5)
        await coordinator.async_refresh_ids([OFF1])
        gate.set()
        await task
        assert coordinator.data["values"][OFF1] == [1.5, 1.5]
        # ... and a poll started after a read publishes normally.
        fake.set(OFF1, 2.0)
        coordinator._poll_state[OFF1].counter = 0
        await coordinator.async_refresh()
        assert coordinator.data["values"][OFF1] == [2.0, 2.0]
    finally:
        await teardown(hass, plant)


# --- unknown room setpoint correction -----------------------------------------


def _forget(coordinator, *oids):
    data = dict(coordinator.data)
    data["values"] = {k: v for k, v in data["values"].items() if k not in oids}
    coordinator.async_set_updated_data(data)


@pytest.mark.asyncio
async def test_unknown_offset_is_reread_once_then_refused(hass, monkeypatch, fake_controller):
    from homeassistant.exceptions import HomeAssistantError

    plant = await setup_plant(hass, monkeypatch, fake_controller)
    fake, coordinator = plant.fake, plant.ctrl["coordinator"]
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        # The real correction is +2 K (target 23.5) but HA does not know it yet.
        fake.set(OFF1, 2.0)
        fake.set("CiPcW458AAE=", 23.5)
        await coordinator.async_refresh_ids(["CiPcW458AAE="])
        _forget(coordinator, OFF1)
        assert c.target_temperature == 23.5 and c.extra_state_attributes["room_offset"] is None
        assert (c.min_temp, c.max_temp) == (7, 35)  # HA defaults: base unknown
        fake.writes.clear()
        fake.reads.clear()
        await c.async_set_temperature(temperature=23.5)
        assert fake.reads[0] == [OFF1]  # one targeted read, then planned with +2 K
        assert fake.writes == []  # unchanged; never "offset 0"
        _forget(coordinator, OFF1)
        await c.async_set_temperature(temperature=22.5)
        assert fake.writes == [(OFF1, 1.0)]

        # Still unknown after the re-read: translated error, nothing written.
        fake.drop(OFF1)
        _forget(coordinator, OFF1)
        fake.writes.clear()
        fake.reads.clear()
        with pytest.raises(HomeAssistantError) as err:
            await c.async_set_temperature(temperature=23.5)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "room_offset_unknown"
        assert fake.reads == [[OFF1]] and fake.writes == []
        assert plant.runtime["write_counts"][KEY] == 1
    finally:
        await teardown(hass, plant)


# --- dependent values after writes ---------------------------------------------


SP_NORMAL1, SP_REDUCED1, TARGET1, BADGE1 = "ASMhEo58AAE=", "ASOkTo58AAE=", "CiPcW458AAE=", "CyPmL458AAE="
CURRENT_TARGET1 = "CiMeXI58AAE="
DHW_COMFORT_SP, DHW_OFFSET, DHW_TARGET = "ASMv1XXZAAE=", "ACPOwnXZAAE=", "CiMzqnXZAAE="


def recomputing_controller(fake_controller):
    """A fake that recomputes dependents like the controller does: the
    circuit target from comfort/eco, its setpoints and the correction (and
    the badge), the DHW target from the comfort setpoint and correction."""

    class Recomputing(fake_controller):
        def _first(self, oid):
            v = self.values.get(oid)
            return v[0] if isinstance(v, list) else v

        async def write(self, oid, value):
            out = await super().write(oid, value)
            eco = self._first(CE1) == 1
            base = self._first(SP_REDUCED1 if eco else SP_NORMAL1)
            self.set(TARGET1, float(base) + float(self._first(OFF1) or 0))
            self.set(BADGE1, 6 if eco else 3)
            if DHW_TARGET in self.values:
                self.set(DHW_TARGET, float(self._first(DHW_COMFORT_SP)) + float(self._first(DHW_OFFSET) or 0))
            return out

    return Recomputing


@pytest.mark.asyncio
async def test_any_write_rereads_the_sections_dependents(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, recomputing_controller(fake_controller))
    fake = plant.fake
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        offset = plant.entities[f"{KEY}:number:ACNZw458AAE"]  # supplement
        comfort_eco = plant.entities[f"{KEY}:select:AiPKUI58AAE"]  # supplement
        setpoint = plant.entities[f"{KEY}:number:ASMhEo58AAE"]  # catalog number
        assert c.target_temperature == 21.5

        # Supplement correction +1 K: the climate target follows at once ...
        fake.reads.clear()
        await offset.async_set_native_value(1.0)
        assert len(fake.reads) == 1  # one read for the write and its dependents
        assert {OFF1, TARGET1, BADGE1, "ACJz7Y58AAE=", "CyP/dY58AAE="} <= set(fake.reads[0])
        assert c.target_temperature == 22.5
        # ... so asking for the shown target does not overshoot to +2 K.
        fake.writes.clear()
        await c.async_set_temperature(temperature=22.5)
        assert fake.writes == []
        await c.async_set_temperature(temperature=23.0)
        assert fake.writes == [(OFF1, 1.5)] and c.target_temperature == 23.0

        # Existing catalog number (normal setpoint) -> target re-read too.
        await setpoint.async_set_native_value(22.0)
        assert c.target_temperature == 23.5 and (c.min_temp, c.max_temp) == (19.0, 25.0)

        # Comfort/eco select -> target and badge follow.
        await comfort_eco.async_select_option("Eco")
        assert c.target_temperature == 20.5 and c.preset_mode == "eco"
        assert c.extra_state_attributes["badge"] == "auto_eco_red"

        # Climate preset write: one read covers the write and the dependents.
        fake.reads.clear()
        await c.async_set_preset_mode("comfort")
        assert len(fake.reads) == 1 and c.target_temperature == 23.5

        # A point outside every plant section only re-reads itself.
        other = next(
            e for u, e in plant.entities.items()
            if u.startswith(f"{KEY}:number:") and P.write_section(e._read_id) is None
        )
        fake.reads.clear()
        await other.async_set_native_value(float(other.native_value) + 1)
        assert fake.reads == [[other._read_id]]
    finally:
        await teardown(hass, plant)


def lossy_controller(fake_controller):
    """A recomputing fake whose next write is applied but whose response is
    lost (timeout), and whose next ``fail_reads`` reads fail."""

    class Lossy(recomputing_controller(fake_controller)):
        lose_response = False
        fail_reads = 0

        async def read(self, ids, *, on_http_request=None):
            if self.fail_reads:
                self.fail_reads -= 1
                self.reads.append(list(ids))
                raise asyncio.TimeoutError()
            return await super().read(ids, on_http_request=on_http_request)

        async def write(self, oid, value):
            out = await super().write(oid, value)
            if self.lose_response:
                self.lose_response = False
                raise asyncio.TimeoutError()
            return out

    return Lossy


@pytest.mark.asyncio
async def test_lost_write_response_still_rereads_the_dependents(hass, monkeypatch, fake_controller):
    from homeassistant.exceptions import HomeAssistantError

    plant = await setup_plant(hass, monkeypatch, lossy_controller(fake_controller))
    fake, coordinator = plant.fake, plant.ctrl["coordinator"]
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        assert c.target_temperature == 21.5 and c.extra_state_attributes["room_offset"] == 0

        # 22.5 °C: the controller applies +1 K, the response times out.
        fake.lose_response = True
        fake.writes.clear()
        fake.reads.clear()
        with pytest.raises(HomeAssistantError):
            await c.async_set_temperature(temperature=22.5)
        assert fake.writes == [(OFF1, 1.0)] and fake.values[TARGET1] == [22.5, 22.5]
        assert {OFF1, TARGET1, BADGE1, "CyP/dY58AAE="} <= set(fake.reads[-1])
        assert c.target_temperature == 22.5
        # Retrying 22.5 °C writes nothing (it used to write +2 K -> 23.5 °C).
        await c.async_set_temperature(temperature=22.5)
        assert fake.writes == [(OFF1, 1.0)] and c.target_temperature == 22.5

        # Lost response and the re-read fails too: target and correction
        # become unknown instead of keeping their stale values ...
        fake.lose_response, fake.fail_reads = True, 2  # re-read + fallback poll
        with pytest.raises(HomeAssistantError):
            await c.async_set_temperature(temperature=23.0)
        assert fake.values[OFF1] == [1.5, 1.5] and fake.values[TARGET1] == [23.0, 23.0]
        assert fake.fail_reads == 0 and not coordinator.last_update_success
        values = coordinator.data["values"]
        assert OFF1 not in values and TARGET1 not in values
        assert c.target_temperature is None
        # ... so the next relative change reads them first: 23.0 °C is reached.
        fake.writes.clear()
        fake.reads.clear()
        await c.async_set_temperature(temperature=23.0)
        assert OFF1 in fake.reads[0] and TARGET1 in fake.reads[0]
        assert fake.writes == [] and c.target_temperature == 23.0

        # Still unreadable: refused with the translated error, nothing written.
        fake.lose_response, fake.fail_reads = True, 2
        with pytest.raises(HomeAssistantError):
            await c.async_set_temperature(temperature=22.0)
        fake.writes.clear()
        fake.fail_reads = 1  # the read before planning
        with pytest.raises(HomeAssistantError) as err:
            await c.async_set_temperature(temperature=22.0)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key in {"room_offset_unknown", "room_target_unknown"}
        assert fake.writes == []
        # The invalidated ids are polled on the next cycle whatever their mode.
        coordinator._poll_state[TARGET1].counter = 10
        await coordinator.async_refresh()
        assert coordinator.data["values"][TARGET1] == [22.0, 22.0]
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_reread_omitting_a_dependent_marks_it_unknown(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, recomputing_controller(fake_controller))
    fake, coordinator = plant.fake, plant.ctrl["coordinator"]
    try:
        c = plant.entities[f"{KEY}:climate:hc1"]
        real_read = fake.read

        async def omit_targets(ids, *, on_http_request=None):
            out = await real_read(ids, on_http_request=on_http_request)
            out["values"].pop(TARGET1, None)
            out["values"].pop(CURRENT_TARGET1, None)
            return out

        fake.read = omit_targets
        await c.async_set_temperature(temperature=22.5)
        assert fake.writes[-1] == (OFF1, 1.0)
        values = coordinator.data["values"]
        assert TARGET1 not in values and CURRENT_TARGET1 not in values and OFF1 in values
        assert c.target_temperature is None
        fake.read = real_read
        fake.writes.clear()
        await c.async_set_temperature(temperature=22.5)
        assert fake.writes == [] and c.target_temperature == 22.5
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_dhw_writes_reread_the_dhw_target(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, recomputing_controller(fake_controller), mutate=dhw_plant)
    try:
        wh = plant.entities[f"{KEY}:water_heater:dhw"]
        dhw_offset = plant.entities[f"{KEY}:number:ACPOwnXZAAE"]
        assert wh.extra_state_attributes["effective_target"] == 50.0
        await dhw_offset.async_set_native_value(3.0)
        assert wh.extra_state_attributes["effective_target"] == 53.0
        await wh.async_set_temperature(temperature=52)
        assert wh.extra_state_attributes["effective_target"] == 55.0
    finally:
        await teardown(hass, plant)


# --- read permissions -----------------------------------------------------------


def policy_user(hass, policy):
    from homeassistant.auth.permissions import PolicyPermissions
    from homeassistant.auth.permissions.models import PermissionLookup

    lookup = PermissionLookup(er.async_get(hass), dr.async_get(hass))
    return SimpleNamespace(permissions=PolicyPermissions(policy, lookup))


async def ws_plants_as(hass, user):
    conn = FakeConnection(user)
    plant_api.ws_plants(hass, conn, {"id": 1, "type": f"{DOMAIN}/plants", "language": "de"})
    await hass.async_block_till_done(wait_background_tasks=True)
    return conn.results[0][1]["plants"]


@pytest.mark.asyncio
async def test_ws_honours_entity_read_permissions(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        reg = er.async_get(hass)
        climate1 = reg.async_get_entity_id("climate", DOMAIN, f"{KEY}:climate:hc1")
        outdoor = reg.async_get_entity_id("sensor", DOMAIN, f"{KEY}:sensor:AyKWuiiJAAE")
        status = reg.async_get_entity_id("sensor", DOMAIN, f"{KEY}:sensor:CyNmeMdZAAE")
        (full,) = (await plant_api.async_build_plants(hass, "de"))["plants"]

        # A non-admin user who may read all entities sees everything.
        assert await ws_plants_as(hass, policy_user(hass, {"entities": True})) == [full]

        # Restricted to the HC1 climate and the outdoor sensor.
        user = policy_user(hass, {"entities": {"entity_ids": {climate1: True, outdoor: True}}})
        (p,) = await ws_plants_as(hass, user)
        roles = p["roles"]
        assert roles["hp.outdoor"]["entity_id"] == outdoor
        assert "hp.status" not in roles and "hp.program" not in roles
        # Raw HC1 roles without an entity come with the readable climate ...
        assert roles["hc1.badge"] == {"entity_id": None} and "hc1.target_selector" in roles
        # ... roles with an entity the user may not read do not.
        assert "hc1.heat_curve_norm_flow" not in roles and "hc1.room_offset" not in roles
        assert not any(r.startswith(("hc2.", "hc3.", "avail.")) for r in roles)
        assert "hc4.availability" not in roles  # plant anchor (status sensor) not readable
        assert [(c["index"], c["climate"], c["name"]) for c in p["circuits"]] == [(1, climate1, "Fußboden")]
        assert p["circuits"][0] == full["circuits"][0]
        assert p["flash_writes"] is None
        # Plant metadata needs the plant anchor (status sensor): generic here.
        assert (p["title"], p["model"], p["device_id"]) == ("Wärmepumpe", "", None)
        assert full["title"] != "Wärmepumpe" and full["model"] == "AIRHAWK518C11A"
        # Only capabilities computed from what the user sees: the circuit.
        assert p["capabilities"] == {"cooling": False}

        conn = FakeConnection(user)
        plant_api.ws_subscribe(hass, conn, {"id": 5, "type": f"{DOMAIN}/subscribe"})
        values = conn.messages[0]["event"]["values"]
        assert set(values) <= set(roles)
        assert values["hc1.badge"] == 3 and values["hp.outdoor"] == pytest.approx(12.1851)
        assert "hp.status" not in values and "hc2.badge" not in values and "avail.dhw" not in values
        assert conn.messages[0]["event"]["capabilities"] == {"cooling": False}
        coordinator = plant.ctrl["coordinator"]
        data = dict(coordinator.data)
        data["values"] = dict(data["values"], **{"CyNmeMdZAAE=": [3, 3], "CyPmL458AAE=": [5, 5]})
        coordinator.async_set_updated_data(data)  # every push is filtered
        pushed = conn.messages[-1]["event"]["values"]
        assert len(conn.messages) == 2 and pushed["hc1.badge"] == 5 and "hp.status" not in pushed
        assert conn.messages[-1]["event"]["capabilities"] == {"cooling": False}
        conn.subscriptions[5]()

        # Only HC1's flow target sensor: the value, nothing about the circuit
        # (name, flags) or the plant.
        flow_target = full["roles"]["hc1.flow_target"]["entity_id"]
        assert flow_target and full["roles"]["hc1.name"]["entity_id"]
        user = policy_user(hass, {"entities": {"entity_ids": {flow_target: True}}})
        (p,) = await ws_plants_as(hass, user)
        assert set(p["roles"]) == {"hc1.flow_target"} and p["circuits"] == []
        assert p["capabilities"] == {} and p["dhw"] is None
        assert (p["title"], p["model"], p["device_id"]) == ("Wärmepumpe", "", None)
        assert "Fußboden" not in json.dumps(p, ensure_ascii=False)
        assert "AIRHAWK" not in json.dumps(p)
        conn = FakeConnection(user)
        plant_api.ws_subscribe(hass, conn, {"id": 7, "type": f"{DOMAIN}/subscribe"})
        event = conn.messages[0]["event"]
        assert set(event["values"]) == {"hc1.flow_target"} and event["capabilities"] == {}
        conn.subscriptions[7]()

        # English generic names.
        (p,) = (await plant_api.async_build_plants(hass, "en", lambda e: bool(e) and e == flow_target))["plants"]
        assert p["title"] == "Heat pump"

        # The status sensor anchors the plant-level roles without an entity.
        (p,) = await ws_plants_as(hass, policy_user(hass, {"entities": {"entity_ids": {status: True}}}))
        anchored = {
            r for r, v in full["roles"].items()
            if v["entity_id"] is None and r.startswith(("hp.", "avail."))
        }
        assert "avail.bms" in anchored
        assert set(p["roles"]) == {"hp.status"} | anchored and p["circuits"] == []
        assert (p["title"], p["model"], p["device_id"]) == (full["title"], full["model"], full["device_id"])
        # Capabilities from availability points without an entity (anchored
        # to the status sensor); not those with their own sensor (DHW, aux,
        # buffer aux, pool), power (power sensors) or cooling (no circuit).
        assert p["capabilities"] == {
            k: full["capabilities"][k] for k in ("bms", "buffer", "silent", "emergency")
        }

        # Nothing of the plant readable: no plant, no events.
        nobody = policy_user(hass, {"entities": {"entity_ids": {"light.kitchen": True}}})
        assert await ws_plants_as(hass, nobody) == []
        conn = FakeConnection(nobody)
        plant_api.ws_subscribe(hass, conn, {"id": 6, "type": f"{DOMAIN}/subscribe"})
        coordinator.async_set_updated_data(data)
        assert conn.results == [(6, None)] and conn.messages == []
        conn.subscriptions[6]()
    finally:
        await teardown(hass, plant)


def test_raw_roles_follow_their_section_anchor():
    roles = {
        "hp.status": "sensor.status", "hp.cycles_total": None, "avail.pool": None,
        "hc1.badge": None, "hc1.season": "select.season1", "hc2.badge": None,
        "hc4.availability": None, "dhw.boost": None, "dhw.temp": "sensor.dhw_temp",
    }
    climates = {1: "climate.hc1", 2: None}

    def readable(*allowed, flash=None, water_heater="water_heater.dhw"):
        return plant_api.readable_roles(roles, climates, water_heater, flash, lambda e: bool(e) and e in allowed)

    assert readable("climate.hc1") == {"hc1.badge"}
    assert readable("select.season1") == {"hc1.season"}
    assert readable("sensor.status") == {"hp.status", "hp.cycles_total", "avail.pool", "hc4.availability"}
    assert readable("water_heater.dhw") == {"dhw.boost"}
    assert readable("water_heater.dhw", water_heater=None) == set()
    # Without a status entity, the flash write counter anchors the plant.
    roles["hp.status"] = None
    assert readable("sensor.flash", flash="sensor.flash") == {
        "hp.status", "hp.cycles_total", "avail.pool", "hc4.availability",
    }
    assert readable("sensor.flash") == set()


# --- sections changing at runtime ---------------------------------------------------


@pytest.mark.asyncio
async def test_section_change_reloads_entry_once(hass, monkeypatch, fake_controller, caplog):
    import logging

    from homeassistant.core import CoreState

    plant = await setup_plant(hass, monkeypatch, fake_controller)
    fake, coordinator = plant.fake, plant.ctrl["coordinator"]
    reload = plant.reload
    HC4_AVAIL, DHW_AVAIL, OUTDOOR = "ESPI9AAAAAE=", "ESMSIwAAAAE=", "AyKWuiiJAAE="
    sections = P.section_ids()
    hass.set_state(CoreState.running)

    async def fresh(**changes):
        """An update that re-reads every section point."""
        for k, v in changes.items():
            fake.set(k, v)
        await coordinator.async_refresh_ids(sections)

    def failed():
        coordinator.last_update_success = False  # what listeners see after a failed poll
        coordinator.async_update_listeners()
        coordinator.last_update_success = True

    try:
        # Values that do not change the sections never reload.
        await fresh(**{OUTDOOR: 3.0})
        await fresh(**{OUTDOOR: 4.0})
        assert not reload.called

        # A flapping section is ignored: the new set must hold for two
        # fresh readings in a row.
        for _ in range(3):
            await fresh(**{HC4_AVAIL: 27})
            await fresh(**{HC4_AVAIL: 0})
        await fresh(**{HC4_AVAIL: 27, DHW_AVAIL: 256})
        await fresh(**{HC4_AVAIL: 27, DHW_AVAIL: 0})
        await fresh(**{HC4_AVAIL: 0})
        assert not reload.called

        # Updates that did not re-read the section points do not count, however
        # often the changed value is republished (targeted reads, forgotten
        # values) ...
        fake.set(HC4_AVAIL, 27)
        await coordinator.async_refresh_ids([HC4_AVAIL])  # one section id only
        for _ in range(3):
            await coordinator.async_refresh_ids([OUTDOOR])
            coordinator.async_invalidate([OUTDOOR])
        assert not reload.called
        # ... they make the next poll read every section point (whatever their
        # polling mode); its reading is the first observation, the next the second.
        for oid in sections:
            coordinator._poll_state[oid].counter = 10
        fake.reads.clear()
        await coordinator.async_refresh()
        assert set(sections) <= set(fake.reads[-1]) and not reload.called
        await coordinator.async_refresh()
        assert set(sections) <= set(fake.reads[-1])
        reload.assert_called_once_with(plant.entry.entry_id)
        reload.reset_mock()
        hass.data[DOMAIN]["_section_reloads"].clear()

        # A failed update in between starts the confirmation over.
        await fresh(**{HC4_AVAIL: 27})
        failed()
        await fresh(**{HC4_AVAIL: 27})
        assert not reload.called

        # Not while Home Assistant starts or stops, nor while the entry is not loaded.
        for core_state in (CoreState.starting, CoreState.stopping):
            hass.set_state(core_state)
            await fresh(**{HC4_AVAIL: 27})
            assert not reload.called
        hass.set_state(CoreState.running)
        plant.entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
        await fresh(**{HC4_AVAIL: 27})
        assert not reload.called
        plant.entry._async_set_state(hass, ConfigEntryState.LOADED, None)

        # HC4 enabled and stable: one reload, logged.
        caplog.set_level(logging.INFO, logger="custom_components.ochsner_local_ots")
        await fresh(**{HC4_AVAIL: 27})
        reload.assert_called_once_with(plant.entry.entry_id)
        assert "sections of" in caplog.text and "[1, 2, 3] -> [1, 2, 3, 4]" in caplog.text

        # Never loops: at most once per 10 minutes per entry.
        for _ in range(5):
            await fresh(**{HC4_AVAIL: 27})
        assert reload.call_count == 1
        reloads = hass.data[DOMAIN]["_section_reloads"]
        reloads[plant.entry.entry_id] -= integration.SECTION_RELOAD_MIN_INTERVAL_SEC + 1
        await fresh(**{HC4_AVAIL: 27})
        assert reload.call_count == 2

        # Back to the loaded sections: nothing to do.
        await fresh(**{HC4_AVAIL: 0})
        await fresh(**{HC4_AVAIL: 0})
        assert reload.call_count == 2
    finally:
        await teardown(hass, plant)
    # Unloading removes the watcher.
    reloads[plant.entry.entry_id] = 0.0
    await fresh(**{DHW_AVAIL: 256})
    await fresh(**{DHW_AVAIL: 256})
    assert reload.call_count == 2


def test_section_signature():
    caps = P.compute_capabilities({"ESNtpAAAAAE=": [27, 27]})
    sig = P.section_signature(caps)
    assert sig == (False, False, False, False, ((1, True, False, False, False),))
    assert P.section_signature(P.compute_capabilities({"ESNtpAAAAAE=": [27 | 4, 0]})) != sig
    assert P.section_signature(P.compute_capabilities({"ESNtpAAAAAE=": [27, 0], "AiPN8gAAAAE=": [1, 1]})) != sig
    # Not section changes: power readings, silent mode.
    assert P.section_signature(P.compute_capabilities({"ESNtpAAAAAE=": [27, 0], "AiPMsAAAAAE=": [1, 1]})) == sig
