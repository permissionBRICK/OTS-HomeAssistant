"""Heating-circuit time programs: encoding, write planning and the websocket API."""

from __future__ import annotations

import random

import pytest

from custom_components.ochsner_local_ots import schedule as S

# The house AirHawk's program (2026-10-25, every day): 00:00 Eco, 04:00 Normal, 20:00 Eco.
HOUSE_DAY = [(0, 0), (240, 1), (1200, 0)]


def day_values(n, day, points, unused_values=(0, 1, 0, 0, 1, 0)):
    """Controller values (``[v, v]``) of one day holding ``points``."""
    times, vals = S.day_ids(n, day)
    out = {}
    for i in range(S.SLOTS):
        if i < len(points):
            t, v = S.encode_time(points[i][0]), points[i][1]
        else:
            t, v = S.UNUSED, unused_values[i]
        out[times[i]] = [t, t]
        out[vals[i]] = [v, v]
    return out


def program_values(n, week=None, default=1, present=1):
    present_id, default_id = S.master_ids(n)
    out = {present_id: [present, present], default_id: [default, default]}
    for day in range(7):
        out.update(day_values(n, day, (week or {}).get(day, HOUSE_DAY)))
    return out


def apply(values, writes):
    out = dict(values)
    for oid, value in writes:
        out[oid] = [value, value]
    return out


def stored_points(values, n, day):
    return [(m, v) for m, v in S.parse_day(values, n, day) if m is not None]


# --- encoding / ids -------------------------------------------------------------


def test_time_encoding_matches_the_controller():
    assert S.encode_time(240) == 4  # 04:00, as read from the house
    assert S.encode_time(20 * 60 + 15) == 3860  # 20:15, read back after a live write
    assert S.decode_time(3860) == 1215
    assert S.decode_time(S.UNUSED) is None and S.decode_time(-1) is None
    assert S.decode_time(24) is None  # hour 24 is no time of day
    assert S.decode_time(0) == 0
    with pytest.raises(ValueError):
        S.encode_time(1440)


def test_ids_are_the_apps():
    times, vals = S.day_ids(1, 0)
    assert times[0] == "DSJWZo58AgI=" and times[1] == "DSJWZo58AwI=" and vals[0] == "DSJWZo58CAI="
    assert vals[5] == "DSJWZo58DQI="
    assert S.day_ids(1, 1)[0][0] == "DSKw8o58AgI="  # Tuesday
    assert S.day_ids(1, 5)[0][0] == "DSLzbo58AgI="  # Saturday
    assert S.day_ids(1, 6)[0][0] == "DSKFvo58AgI="  # Sunday
    assert S.day_ids(2, 0)[0][0] == "DSJWZu1MAgI="
    assert S.master_ids(1) == ("CyItGY58AAE=", "CyItGY58PgE=")
    assert len(S.circuit_ids(3)) == 2 + 7 * 12


def test_parse_circuit():
    values = program_values(1)
    p = S.parse_circuit(values, 1)
    assert p["supported"] and p["complete"] and p["default"] == 1 and p["present"] == 1
    assert p["days"][0] == [[0, 0], [240, 1], [1200, 0], [None, 0], [None, 1], [None, 0]]
    assert S.parse_circuit({}, 2) == {"index": 2, "supported": False}
    del values[S.day_ids(1, 3)[1][2]]
    p = S.parse_circuit(values, 1)
    assert p["days"][3] is None and not p["complete"]


def test_validate_points():
    assert S.validate_points([[0, 0], [360, 1]]) == [(0, 0), (360, 1)]
    for bad in ([[0, 2]], [[1440, 1]], [[60, 1], [60, 0]], [[60, 1], [30, 0]], [[0, 0]] * 7, [[0]], "x", [[True, 1]]):
        with pytest.raises(ValueError):
            S.validate_points(bad)


# --- planning --------------------------------------------------------------------


def test_plan_writes_only_changed_members():
    values = program_values(1)
    assert S.plan_day(values, 1, 0, HOUSE_DAY) == []
    # Night from 22:00 instead of 20:00: one time.
    times, vals = S.day_ids(1, 0)
    assert S.plan_day(values, 1, 0, [(0, 0), (240, 1), (1320, 0)]) == [(times[2], S.encode_time(1320))]
    # A second Normal period: the new slot's value before its time, nothing else.
    writes = S.plan_day(values, 1, 0, [(0, 0), (240, 1), (480, 0), (960, 1), (1200, 0)])
    assert writes == [
        (times[2], S.encode_time(480)),
        (vals[3], 1), (times[3], S.encode_time(960)),
        (vals[4], 0), (times[4], S.encode_time(1200)),
    ]
    assert stored_points(apply(values, writes), 1, 0) == [(0, 0), (240, 1), (480, 0), (960, 1), (1200, 0)]
    # Normal all day: every point unused, values untouched.
    writes = S.plan_day(values, 1, 0, [])
    assert writes == [(times[2], S.UNUSED), (times[1], S.UNUSED), (times[0], S.UNUSED)]


def test_plan_keeps_times_ascending_after_every_write():
    rng = random.Random(7)

    def random_day():
        k = rng.randint(0, 6)
        mins = sorted(rng.sample(range(0, 1440, 15), k))
        return [(m, rng.randint(0, 1)) for m in mins]

    for _ in range(400):
        old, new = random_day(), random_day()
        values = day_values(1, 0, old)
        times, _ = S.day_ids(1, 0)
        writes = S.plan_day(values, 1, 0, new)
        state = dict(values)
        for w in writes:
            state = apply(state, [w])
            seen = [S.decode_time(state[t][0]) for t in times]
            used = [m for m in seen if m is not None]
            assert used == sorted(used) and len(set(used)) == len(used), (old, new, writes)
        assert stored_points(state, 1, 0) == new
        assert len(writes) == len(set(writes))


def test_plan_refuses_an_unreadable_day():
    values = program_values(1)
    del values[S.day_ids(1, 0)[0][4]]
    with pytest.raises(ValueError):
        S.plan_day(values, 1, 0, HOUSE_DAY)


# --- websocket (real Home Assistant) ----------------------------------------------

pytest.importorskip("homeassistant")

from homeassistant.helpers import entity_registry as er  # noqa: E402

from custom_components.ochsner_local_ots.const import DOMAIN  # noqa: E402
from tests.test_dashboard_ha import (  # noqa: E402,F401  (pytest fixtures)
    KEY,
    FakeConnection,
    hass,
    policy_user,
    settle_clock,
    setup_plant,
    teardown,
)


class Conn(FakeConnection):
    def __init__(self, user=None):
        super().__init__(user)
        self.errors = []

    def send_error(self, msg_id, code, message):
        self.errors.append((msg_id, code, message))

    def async_handle_exception(self, msg, err):
        raise err


def with_program(fake):
    for oid, value in program_values(1).items():
        fake.set(oid, value[0])


async def call(hass, handler, msg, user=None):
    conn = Conn(user)
    handler(hass, conn, {"id": 1, **msg})
    await hass.async_block_till_done(wait_background_tasks=True)
    return conn


def plant_id(plant):
    return f"{plant.entry.entry_id}:{KEY}"


@pytest.mark.asyncio
async def test_ws_schedules_reads_each_circuit_and_caches(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=with_program)
    try:
        msg = {"type": f"{DOMAIN}/schedules", "plant_id": plant_id(plant)}
        conn = await call(hass, S.ws_schedules, msg)
        result = conn.results[0][1]
        by_index = {c["index"]: c for c in result["circuits"]}
        assert sorted(by_index) == [1, 2, 3]
        hc1 = by_index[1]
        assert hc1["supported"] and hc1["complete"] and hc1["writable"] and hc1["default"] == 1
        assert hc1["days"][6][:3] == [[0, 0], [240, 1], [1200, 0]]
        assert by_index[2] == {"index": 2, "supported": False, "writable": True}
        reads = len(plant.fake.reads)
        await call(hass, S.ws_schedules, msg)  # cached
        assert len(plant.fake.reads) == reads
        await call(hass, S.ws_schedules, {**msg, "force": True})
        assert len(plant.fake.reads) > reads
        conn = await call(hass, S.ws_schedules, {**msg, "plant_id": "nope"})
        assert conn.errors[0][1] == "not_found"
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_ws_schedule_set_writes_changed_members_and_reads_back(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=with_program)
    try:
        fake = plant.fake
        fake.writes.clear()
        msg = {
            "type": f"{DOMAIN}/schedule/set", "plant_id": plant_id(plant), "circuit": 1,
            # Saturday and Sunday: Normal 06:30-23:00.
            "days": {"5": [[0, 0], [390, 1], [1380, 0]], "6": [[0, 0], [390, 1], [1380, 0]]},
        }
        conn = await call(hass, S.ws_schedule_set, msg)
        assert not conn.errors
        result = conn.results[0][1]
        sat, sun = S.day_ids(1, 5)[0], S.day_ids(1, 6)[0]
        # Both points move later: the later one first, so times stay ascending.
        assert fake.writes == [
            (sat[2], S.encode_time(1380)), (sat[1], S.encode_time(390)),
            (sun[2], S.encode_time(1380)), (sun[1], S.encode_time(390)),
        ]
        assert result["writes"] == 4 and result["days"][5][:3] == [[0, 0], [390, 1], [1380, 0]]
        assert result["days"][0][:3] == [[0, 0], [240, 1], [1200, 0]]
        # Counted like every controller write (flash counter).
        assert plant.runtime["write_counts"][KEY] == 4
        # The same request again changes nothing.
        fake.writes.clear()
        conn = await call(hass, S.ws_schedule_set, msg)
        assert fake.writes == [] and conn.results[0][1]["writes"] == 0
        # Invalid input never reaches the controller.
        conn = await call(hass, S.ws_schedule_set, {**msg, "days": {"0": [[600, 1], [300, 0]]}})
        assert conn.errors[0][1] == "invalid_format" and fake.writes == []
        conn = await call(hass, S.ws_schedule_set, {**msg, "days": {"7": []}})
        assert conn.errors[0][1] == "invalid_format"
        conn = await call(hass, S.ws_schedule_set, {**msg, "circuit": 4})
        assert conn.errors[0][1] == "not_found"
    finally:
        await teardown(hass, plant)


@pytest.mark.asyncio
async def test_ws_schedules_honour_entity_permissions(hass, monkeypatch, fake_controller):
    plant = await setup_plant(hass, monkeypatch, fake_controller, mutate=with_program)
    try:
        reg = er.async_get(hass)
        climate1 = reg.async_get_entity_id("climate", DOMAIN, f"{KEY}:climate:hc1")
        read_only = policy_user(hass, {"entities": {"entity_ids": {climate1: {"read": True}}}})
        conn = await call(hass, S.ws_schedules, {"type": f"{DOMAIN}/schedules", "plant_id": plant_id(plant)}, read_only)
        circuits = conn.results[0][1]["circuits"]
        assert [c["index"] for c in circuits] == [1] and circuits[0]["writable"] is False
        plant.fake.writes.clear()
        msg = {"type": f"{DOMAIN}/schedule/set", "plant_id": plant_id(plant), "circuit": 1, "days": {"0": []}}
        conn = await call(hass, S.ws_schedule_set, msg, read_only)
        assert conn.errors[0][1] == "unauthorized" and plant.fake.writes == []
        control = policy_user(hass, {"entities": {"entity_ids": {climate1: True}}})
        conn = await call(hass, S.ws_schedule_set, msg, control)
        assert not conn.errors and len(plant.fake.writes) == 3
    finally:
        await teardown(hass, plant)
