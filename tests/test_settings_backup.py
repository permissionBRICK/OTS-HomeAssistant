"""Settings backup and restore (issue #35): which points are settings, the
store, the comparison with a backup, the restore and its options flow, the
button, the sensor and the diagnostics export."""

from __future__ import annotations

import json
import logging

import pytest

pytest.importorskip("homeassistant")
pytest.importorskip("pytest_asyncio")

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from custom_components.ochsner_local_ots import config_flow as cf
from custom_components.ochsner_local_ots import diagnostics
from custom_components.ochsner_local_ots import plant as P
from custom_components.ochsner_local_ots import settings_backup as B
from custom_components.ochsner_local_ots.catalog import load_catalog
from custom_components.ochsner_local_ots.const import DOMAIN
from tests.test_dashboard_ha import (  # noqa: F401  (pytest fixtures)
    KEY,
    hass,
    settle_clock,
    setup_plant,
    teardown,
)
from tests.test_schedule import HOUSE_DAY, day_values, program_values, stored_points

INERTIA = "ACO+/iiJAAE="  # Gebäudeträgheit (number)
HEAT_LIMIT1 = P.circuit_role_ids(1)["hc1.heat_limit"]
OP1 = P.circuit_role_ids(1)["hc1.op_program"]
NAME1 = "BCNL7o58AAE="  # name of heating circuit 1 (text)
SEASON1 = P.circuit_role_ids(1)["hc1.season"]
COMFORT_ECO1 = P.circuit_role_ids(1)["hc1.comfort_eco"]
ROOM_OFFSET1 = P.circuit_role_ids(1)["hc1.room_offset"]
FLOW_OFFSET1 = "AyKvbyiJSTA="  # sensor offset of circuit 1's flow temperature
NOT_SETTINGS = {
    "AyKaCCiJAAE=": "system pressure (a measurement)",
    "BSLZ1IwCAAE=": "collective fault (a status)",
    "ASJtqVsWAAE=": "DHW emergency mode",
    "ByIDxCiJAAE=": "DHW 3-way valve (an output)",
    "ByIKwCiJSjA=": "mixing valve override (an output)",
    "CiM1EI58AAE=": "days to the end of the screed program (computed)",
}


# setup_plant replaces the automatic backup with a stub unless asked for it.
REAL_AUTO_BACKUP = B.async_auto_backup


def with_program(fake):
    for oid, value in program_values(1).items():
        fake.set(oid, value[0])


def store(hass):
    return B.loaded_store(hass)


def options_flow(hass, plant):
    flow = cf.ClimatixGenericOptionsFlowHandler(plant.entry)
    flow.hass = hass
    flow.handler = plant.entry.entry_id
    flow.flow_id = "options"
    return flow


BASE = {"scan_interval": 30, "polling_threshold": 20, "max_ids_per_read_request": 40}


# --- scope --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_are_user_settings_only(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        found = B.settings(plant.ctrl, load_catalog())
        for oid in (INERTIA, HEAT_LIMIT1, OP1, NAME1, COMFORT_ECO1, ROOM_OFFSET1, FLOW_OFFSET1):
            assert P.canonical_id(oid) in found, oid
        # Disabled as an entity (the thermostat writes it), still a setting.
        assert P.canonical_id(SEASON1) in found
        for oid, what in NOT_SETTINGS.items():
            assert P.canonical_id(oid) not in found, what
        # Temporary programs from an old bundle are no settings either.
        for name, oid in (("Party mode heating circuit 1", P.circuit_role_ids(1)["hc1.party_on"]),
                          ("Holiday mode heating circuit 1", "AiPkM458AAE=")):
            cfg = {"name": name, "read_id": oid, "write_id": oid, "options": {"Off": 0, "On": 1}}
            assert not B.is_setting(cfg, load_catalog()), name
        # The two curves' points share their names; labels tell them apart.
        catalog = load_catalog()
        base = {k: B.setting_name(found.get(P.canonical_id(o)), P.canonical_id(o), catalog, "de")
                for k, o in (("heat", P.circuit_role_ids(1)["hc1.heat_curve_base_outdoor"]),
                             ("cool", P.circuit_role_ids(1)["hc1.cool_curve_base_outdoor"]))}
        assert base == {"heat": "Fußboden, Fußpunkt Außentemperatur (Heizkurve)",
                        "cool": "Fußboden, Fußpunkt Außentemperatur (Kühlkurve)"}
        # Nothing disabled by default (service, tests, network, privacy) but the season.
        disabled = {
            P.canonical_id(c["read_id"])
            for key in ("numbers", "selects", "switches", "texts")
            for c in plant.ctrl[key]
            if c.get("enabled_default") is False
        }
        assert disabled & set(found) == {P.canonical_id(s) for s in (SEASON1, P.circuit_role_ids(2)["hc2.season"], P.circuit_role_ids(3)["hc3.season"])}
        assert 100 < len(found) < 160
    finally:
        await teardown(hass, plant)


def test_schedule_helpers_match_the_card():
    # The house program: Normal 04:00-20:00 on a Normal default.
    assert B.normal_periods(HOUSE_DAY, 1) == [(240, 1200)]
    assert B.points_for([(240, 1200)], 1) == HOUSE_DAY
    assert B.points_for([(240, 1200)], 0) == [(240, 1), (1200, 0)]
    assert B.normal_periods([], 0) == [] and B.normal_periods([], 1) == [(0, 1440)]
    slots = [[0, 0], [240, 1], [1200, 0], [None, 0], [None, 1], [None, 0]]
    assert B.write_count(slots, HOUSE_DAY) == 0
    assert B.write_count(slots, [(0, 0), (360, 1), (1320, 0)]) == 2
    assert B.display_periods([(240, 1200)], "de") == "Normal 04:00–20:00"
    assert B.display_periods([], "en") == "Night all day"


# --- backing up ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_backup_reads_settings_and_time_programs(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=with_program)
    try:
        writes = plant.runtime["write_counts"].get(KEY, 0)
        backup, stored = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_MANUAL)
        assert stored and plant.fake.writes == []
        assert plant.runtime["write_counts"].get(KEY, 0) == writes  # reads only
        values = backup["values"]
        assert values[P.canonical_id(INERTIA)] == plant.fake.values[INERTIA][0]
        assert values[P.canonical_id(NAME1)] == "Fußboden"
        assert not set(values) & {P.canonical_id(o) for o in NOT_SETTINGS}
        assert list(backup["schedules"]) == ["1"]  # only circuit 1 has a scheduler here
        assert backup["schedules"]["1"]["default"] == 1
        assert backup["schedules"]["1"]["days"][0][:3] == [[0, 0], [240, 1], [1200, 0]]
        assert backup["controller"] == {"model": plant.ctrl["device_model"], "sw_version": None, "fingerprint": None}
        assert B.validate_backup(backup)
        assert store(hass).latest(KEY) is backup
        json.dumps(backup)  # storable as is
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_automatic_backups_only_when_something_changed(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        first, stored = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_AUTOMATIC)
        assert stored
        again, stored = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_AUTOMATIC)
        assert not stored and again is first
        plant.fake.set(INERTIA, 12)
        changed, stored = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_AUTOMATIC)
        assert stored and changed["values"][P.canonical_id(INERTIA)] == 12
        # A manual backup is always kept.
        _manual, stored = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_MANUAL)
        assert stored
        assert [b["source"] for b in store(hass).backups(KEY)] == ["manual", "automatic", "automatic"]
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_store_keeps_ten_of_each_and_survives_a_reload(hass):
    s = await B.async_get_store(hass)

    def backup(i, source):
        value = i if source == "manual" else 100 + i  # never equal to the newest one
        return {"id": f"{source}{i}", "created": f"2026-11-{i + 1:02d}T10:00:00+00:00", "source": source,
                "values": {INERTIA: value}, "schedules": {}}

    for i in range(12):
        await s.async_add("k", backup(i, "manual"))
        await s.async_add("k", backup(i, "automatic"))
    kept = s.backups("k")
    assert [b["id"] for b in kept if b["source"] == "manual"] == [f"manual{i}" for i in range(11, 1, -1)]
    assert [b["id"] for b in kept if b["source"] == "automatic"] == [f"automatic{i}" for i in range(11, 1, -1)]
    assert s.get("k", "manual11") is kept[1]

    with open(hass.config.path(".storage", B.STORAGE_KEY), encoding="utf-8") as fh:
        saved = json.load(fh)["data"]
    assert [b["id"] for b in saved["controllers"]["k"]] == [b["id"] for b in kept]

    # Loading keeps only valid backups.
    fresh = B.BackupStore(hass)

    async def load():
        junk = {"id": "x", "created": "never", "source": "manual", "values": {INERTIA: 1}}
        return {"controllers": {"k": [junk, *saved["controllers"]["k"]], "bad": "nope"}}

    fresh._store.async_load = load
    await fresh.async_load()
    assert [b["id"] for b in fresh.backups("k")] == [b["id"] for b in kept] and fresh.backups("bad") == []


@pytest.mark.asyncio
async def test_auto_backup_after_setup(hass, monkeypatch, fake_controller):
    monkeypatch.setattr(B, "AUTO_BACKUP_DELAY_SEC", 0)
    plant = await setup_plant(hass, monkeypatch, fake_controller, auto_backup=True)
    try:
        await hass.async_block_till_done(wait_background_tasks=True)
        (backup,) = store(hass).backups(KEY)
        assert backup["source"] == "automatic"
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_auto_backup_skips_an_unreachable_heat_pump(hass, monkeypatch, fake_controller, caplog):
    caplog.set_level(logging.INFO, logger=B.__name__)
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        monkeypatch.setattr(B, "AUTO_BACKUP_DELAY_SEC", 0)

        async def fail(*a, **kw):
            raise OSError("down")

        monkeypatch.setattr(plant.fake, "read", fail)
        await REAL_AUTO_BACKUP(hass, plant.ctrl)  # never raises
        assert store(hass).backups(KEY) == []
        assert "automatic settings backup was skipped" in caplog.text
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_button_and_sensor(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        button = plant.entities[f"{KEY}:button:settings_backup"]
        sensor = plant.entities[f"{KEY}:last_settings_backup".replace("=", "")]
        assert button.entity_category == "config" and sensor.entity_category == "diagnostic"
        assert button.device_info["identifiers"] == sensor.device_info["identifiers"] == {(DOMAIN, KEY)}
        button.hass = sensor.hass = hass
        assert sensor.native_value is None and sensor.extra_state_attributes == {"backups": 0}
        await button.async_press()
        latest = store(hass).latest(KEY)
        assert latest["source"] == "manual"
        assert sensor.native_value == B.created_at(latest)
        attrs = sensor.extra_state_attributes
        assert attrs["backups"] == 1 and attrs["settings"] == len(latest["values"]) and attrs["source"] == "manual"

        async def fail(*a, **kw):
            raise OSError("down")

        monkeypatch.setattr(plant.fake, "read", fail)
        with pytest.raises(HomeAssistantError) as err:
            await button.async_press()
        assert err.value.translation_key == "backup_failed"
    finally:
        await teardown(hass, plant)


# --- restoring ----------------------------------------------------------------


async def changed_plant(hass, monkeypatch, fake_controller):
    """A plant, its backup, then four settings and Monday's program changed."""
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=with_program)
    backup, _ = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_MANUAL)
    fake = plant.fake
    fake.set(INERTIA, 25)
    fake.set(HEAT_LIMIT1, 15.5)
    fake.set(OP1, 1)
    fake.set(NAME1, "Keller")
    for oid, value in day_values(1, 0, [(0, 0), (360, 1), (1320, 0)]).items():
        fake.set(oid, value[0])
    fake.writes.clear()
    return plant, backup


@pytest.mark.asyncio
async def test_plan_lists_the_differences(hass, monkeypatch, fake_controller):
    plant, backup = await changed_plant(hass, monkeypatch, fake_controller)
    try:
        plan = await B.async_plan_restore(hass, plant.ctrl, backup)
        by_key = {c.key: c for c in plan.changes}
        assert set(by_key) == {
            f"v:{P.canonical_id(o)}" for o in (INERTIA, HEAT_LIMIT1, OP1, NAME1)
        } | {"s:1:0"}
        assert by_key[f"v:{P.canonical_id(NAME1)}"].label.endswith(": “Keller” → “Fußboden”")
        assert by_key[f"v:{P.canonical_id(HEAT_LIMIT1)}"].label == "Fußboden, Heizgrenze: 15.5 °C → 16.7 °C"
        assert by_key["s:1:0"].label == "Fußboden, Zeitprogramm Montag: Normal 06:00–22:00 → Normal 04:00–20:00"
        assert by_key["s:1:0"].writes == 2
        assert plan.writes == 6 and plan.skipped == []
        assert plant.fake.writes == []
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_restore_writes_only_the_selected_differences(hass, monkeypatch, fake_controller):
    plant, backup = await changed_plant(hass, monkeypatch, fake_controller)
    fake = plant.fake
    try:
        before = plant.runtime["write_counts"].get(KEY, 0)
        keys = [f"v:{P.canonical_id(INERTIA)}", f"v:{P.canonical_id(NAME1)}", "s:1:0"]
        result = await B.async_restore(hass, plant.ctrl, backup, keys)
        assert sorted(fake.writes[:2]) == sorted([(INERTIA, 10.0), (NAME1, "Fußboden")])
        assert fake.values[INERTIA][0] == 10 and fake.values[NAME1][0] == "Fußboden"
        assert stored_points(fake.values, 1, 0) == HOUSE_DAY
        assert fake.values[HEAT_LIMIT1][0] == 15.5 and fake.values[OP1][0] == 1  # not selected
        assert len(result.restored) == 3 and result.failed == []
        assert result.writes == len(fake.writes) == 4
        assert plant.runtime["write_counts"][KEY] == before + 4
        # The settings from before the restore were backed up first.
        newest = store(hass).latest(KEY)
        assert newest["source"] == "before_restore" and newest["values"][P.canonical_id(INERTIA)] == 25
        # Restoring again writes nothing for what already matches.
        fake.writes.clear()
        again = await B.async_restore(hass, plant.ctrl, backup, keys)
        assert fake.writes == [] and again.restored == [] and again.writes == 0
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_restore_retries_a_rejected_write_and_reports_failures(hass, monkeypatch, fake_controller):
    plant, backup = await changed_plant(hass, monkeypatch, fake_controller)
    fake = plant.fake
    original = type(fake).write
    attempts = {}

    async def write(oid, value):
        attempts[oid] = attempts.get(oid, 0) + 1
        if oid == INERTIA and attempts[oid] == 1:
            raise RuntimeError("Controller error during write")  # allowed once the limit is in
        if oid == OP1:
            raise RuntimeError("Controller error during write")  # always rejected
        if oid == NAME1:
            return {"values": {}}  # accepted, but the controller keeps its value
        return await original(fake, oid, value)

    monkeypatch.setattr(fake, "write", write)
    try:
        keys = [f"v:{P.canonical_id(o)}" for o in (INERTIA, HEAT_LIMIT1, OP1, NAME1)]
        result = await B.async_restore(hass, plant.ctrl, backup, keys)
        # Retried while other writes still succeed: OP1 three times.
        assert attempts[INERTIA] == 2 and attempts[OP1] == 3
        assert fake.values[INERTIA][0] == 10 and fake.values[HEAT_LIMIT1][0] == 16.7
        assert len(result.restored) == 2
        failed = sorted(result.failed)
        assert len(failed) == 2
        assert any("Keller" in f and "behalten" in f for f in failed)
        assert any("abgelehnt" in f for f in failed)
        assert result.writes == 3  # inertia (2nd try), heat limit, name
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_plan_skips_settings_this_plant_lacks(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        backup, _ = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_MANUAL)
        foreign = dict(backup, values={
            **backup["values"],
            P.canonical_id("ASMv1XXZAAE="): 52.0,  # a DHW setpoint: no DHW here
            P.canonical_id("AyKaCCiJAAE="): 1.8,  # system pressure: an entity, but no setting
        })
        plant.fake.drop(INERTIA)  # unreadable right now
        plan = await B.async_plan_restore(hass, plant.ctrl, foreign)
        assert plan.changes == []
        assert len(plan.skipped) == 3 and "Gebäudeträgheit" in plan.skipped
        assert not [n for n in plan.skipped if n.endswith("=")]  # names, not ids
    finally:
        await teardown(hass, plant)


# --- export / import / diagnostics -------------------------------------------


@pytest.mark.asyncio
async def test_diagnostics_export_round_trips_without_secrets(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=with_program)
    try:
        plant.ctrl["device_serial"] = "SER-4711"
        await B.async_create_backup(hass, plant.ctrl, B.SOURCE_MANUAL)
        entry_diag = await diagnostics.async_get_config_entry_diagnostics(hass, plant.entry)
        dumped = json.dumps(entry_diag)
        for secret in ("SBTAdmin", "7659", KEY, "SER-4711"):
            assert secret not in dumped
        (ctrl_diag,) = entry_diag["controllers"]
        export = ctrl_diag["settings_backup"]
        assert export["controller"]["fingerprint"] == B.fingerprint("SER-4711")
        assert export["names"][P.canonical_id(INERTIA)] == "Gebäudeträgheit"
        assert ctrl_diag["plant"]["circuits"][0]["index"] == 1

        by_ident = {
            ident: device
            for device in dr.async_entries_for_config_entry(dr.async_get(hass), plant.entry.entry_id)
            for _domain, ident in device.identifiers
        }
        main, circuit = by_ident[KEY], by_ident[f"{KEY}:hc:tag:31886"]
        for device in (main, circuit):
            assert (await diagnostics.async_get_device_diagnostics(hass, plant.entry, device))["settings_backup"] == export

        # The downloaded file (HA wraps it under "data") imports again.
        download = json.loads(json.dumps({"home_assistant": {"version": "x"}, "data": entry_diag}))
        (imported,) = B.parse_import(download)
        assert imported["backups"][0]["values"] == store(hass).latest(KEY)["values"]
        with pytest.raises(ValueError):
            B.parse_import({"data": {"controllers": []}})
        broken = json.loads(json.dumps(export))
        broken["backups"][0]["schedules"]["1"]["days"][0][0] = [9999, 1]
        with pytest.raises(ValueError):
            B.parse_import(broken)
    finally:
        await teardown(hass, plant)


# --- options flow -------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_flow_restores_a_stored_backup(hass, monkeypatch, fake_controller):
    plant, backup = await changed_plant(hass, monkeypatch, fake_controller)
    try:
        flow = options_flow(hass, plant)
        form = await flow.async_step_init(None)
        assert "restore_settings" in [str(k) for k in form["data_schema"].schema]
        form = await flow.async_step_init({**BASE, "restore_settings": True})
        assert form["step_id"] == "restore_select"
        (choice, upload) = form["data_schema"].schema
        assert str(choice) == "backup" and choice.default() == f"0:{backup['id']}" and str(upload) == "file"

        form = await flow.async_step_restore_select({"backup": f"0:{backup['id']}"})
        assert form["step_id"] == "restore_confirm"
        assert form["description_placeholders"]["count"] == "5"
        (changes,) = form["data_schema"].schema
        assert len(changes.default()) == 5
        form = await flow.async_step_restore_confirm({"changes": []})
        assert form["errors"] == {"base": "no_changes_selected"}

        progress = await flow.async_step_restore_confirm({"changes": [f"v:{P.canonical_id(INERTIA)}"]})
        assert progress["type"] == FlowResultType.SHOW_PROGRESS and progress["progress_action"] == "restore"
        await flow._restore_task
        done = await flow.async_step_restore_run()
        assert done["type"] == FlowResultType.SHOW_PROGRESS_DONE and done["step_id"] == "restore_done"
        form = await flow.async_step_restore_done()
        assert form["description_placeholders"] == {"restored": "1", "writes": "1", "failed": ""}
        assert plant.fake.writes == [(INERTIA, 10.0)]
        entry = await flow.async_step_restore_done({})
        assert entry["type"] == FlowResultType.CREATE_ENTRY and entry["data"]["scan_interval"] == 30
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_options_flow_nothing_to_restore(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller)
    try:
        backup, _ = await B.async_create_backup(hass, plant.ctrl, B.SOURCE_MANUAL)
        flow = options_flow(hass, plant)
        await flow.async_step_init({**BASE, "restore_settings": True})
        form = await flow.async_step_restore_select({"backup": f"0:{backup['id']}"})
        assert form["step_id"] == "restore_nothing"
        assert (await flow.async_step_restore_nothing({}))["type"] == FlowResultType.CREATE_ENTRY
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_options_flow_restores_an_uploaded_file(hass, monkeypatch, fake_controller):
    plant, _backup = await changed_plant(hass, monkeypatch, fake_controller)
    try:
        await B.async_create_backup(hass, plant.ctrl, B.SOURCE_AUTOMATIC)  # the changed state
        catalog = load_catalog()
        export = B.export(plant.ctrl, store(hass).backups(KEY), catalog)
        upload = {"data": {"controllers": [{"settings_backup": export}]}}
        monkeypatch.setattr(cf, "_read_uploaded_json", lambda hass, file_id: json.loads(json.dumps(upload)))

        # No serial here: the file cannot prove it is this controller's.
        flow = options_flow(hass, plant)
        await flow.async_step_init({**BASE, "restore_settings": True})
        form = await flow.async_step_restore_select({"file": "upload-1"})
        assert form["errors"] == {"base": "other_controller"}
        assert "other_controller" in [str(k) for k in form["data_schema"].schema]
        form = await flow.async_step_restore_select({"file": "upload-1", "other_controller": True})
        assert form["step_id"] == "restore_file"  # the file holds two backups
        form = await flow.async_step_restore_file({"backup": "1"})  # the older one
        assert form["step_id"] == "restore_confirm" and form["description_placeholders"]["count"] == "5"

        # With a serial the fingerprint matches.
        plant.ctrl["device_serial"] = "SER-4711"
        export["controller"]["fingerprint"] = B.fingerprint("SER-4711")
        upload["data"]["controllers"][0]["settings_backup"] = export
        flow = options_flow(hass, plant)
        await flow.async_step_init({**BASE, "restore_settings": True})
        assert (await flow.async_step_restore_select({"file": "upload-2"}))["step_id"] == "restore_file"

        # Another model is refused; a file without a backup too.
        export["controller"] = {"model": "AIRFALCON212", "fingerprint": "0" * 16}
        upload["data"]["controllers"][0]["settings_backup"] = export
        flow = options_flow(hass, plant)
        await flow.async_step_init({**BASE, "restore_settings": True})
        form = await flow.async_step_restore_select({"file": "upload-3", "other_controller": True})
        assert form["errors"] == {"base": "other_model"}

        def not_json(hass, file_id):
            raise ValueError("not a JSON file")

        monkeypatch.setattr(cf, "_read_uploaded_json", not_json)
        form = await flow.async_step_restore_select({"file": "upload-4"})
        assert form["errors"] == {"file": "invalid_file"}
    finally:
        await teardown(hass, plant)
