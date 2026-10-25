"""Heating-circuit time programs (Climatix schedules), read and written on demand.

Each circuit has a scheduler (object type 8715, point 6445 on the circuit's
instance tag): member 256 is its present output, 318 its default value. The
week lives in seven day objects (type 8717, one point per weekday), each with
six switch points: members 514..519 are the times, 520..525 the value that
applies from that time on (0 = Eco, the reduced setpoint; 1 = Normal). A time
is ``hour | minute << 8`` (little-endian [h, m, 0, 0]); 0xFFFFFFFF = unused.
Before a day's first switch point the default value applies.

The points are not polled: ``ochsner_local_ots/schedules`` reads them (cached
briefly) and ``ochsner_local_ots/schedule/set`` writes the switch points of
whole days, sending only the members that differ from a fresh read.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import plant as P
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MASTER_OBJECT_TYPE = 8715
MASTER_POINT = 6445
MEMBER_PRESENT = 256
MEMBER_DEFAULT = 318

DAY_OBJECT_TYPE = 8717
# Monday .. Sunday
DAY_POINTS: Tuple[int, ...] = (26198, 62128, 42023, 55015, 53753, 28403, 48773)
SLOTS = 6
TIME_MEMBER = 514
VALUE_MEMBER = 520
UNUSED = 0xFFFFFFFF

ECO = 0
NORMAL = 1
VALUES = (ECO, NORMAL)

CACHE_SECONDS = 60.0

Write = Tuple[str, int]


# --- encoding ------------------------------------------------------------------


def encode_time(minute: int) -> int:
    hour, mins = divmod(int(minute), 60)
    if not 0 <= hour <= 23:
        raise ValueError(f"time {minute} out of range")
    return hour | (mins << 8)


def decode_time(raw: Any) -> Optional[int]:
    """Minute of the day, None for an unused switch point (or a value that
    is not a time of day)."""
    value = P.as_int(raw)
    if value is None or value in (UNUSED, -1):
        return None
    hour, mins = value & 0xFF, (value >> 8) & 0xFF
    if hour > 23 or mins > 59:
        return None
    return hour * 60 + mins


# --- ids -------------------------------------------------------------------------


def _tag(n: int) -> int:
    if not 1 <= int(n) <= P.HC_COUNT:
        raise ValueError(f"heating circuit {n} out of range")
    return P.HC_TAGS[int(n) - 1]


def master_ids(n: int) -> Tuple[str, str]:
    """(present, default) of circuit ``n``'s scheduler."""
    tag = _tag(n)
    return (
        P.make_id(MASTER_OBJECT_TYPE, tag, MASTER_POINT, MEMBER_PRESENT),
        P.make_id(MASTER_OBJECT_TYPE, tag, MASTER_POINT, MEMBER_DEFAULT),
    )


def day_ids(n: int, day: int) -> Tuple[List[str], List[str]]:
    """(time ids, value ids) of the six switch points of ``day`` (0 = Monday)."""
    tag = _tag(n)
    point = DAY_POINTS[int(day)]
    times = [P.make_id(DAY_OBJECT_TYPE, tag, point, TIME_MEMBER + i) for i in range(SLOTS)]
    values = [P.make_id(DAY_OBJECT_TYPE, tag, point, VALUE_MEMBER + i) for i in range(SLOTS)]
    return times, values


def circuit_ids(n: int, days: Sequence[int] = range(7)) -> List[str]:
    out = list(master_ids(n))
    for day in days:
        times, values = day_ids(n, day)
        out += times + values
    return out


# --- parsing ---------------------------------------------------------------------


def parse_day(values: Mapping[str, Any], n: int, day: int) -> Optional[List[List[Optional[int]]]]:
    """The six slots of a day as [minute or None, value], or None when the
    read did not answer all of them."""
    times, vals = day_ids(n, day)
    slots: List[List[Optional[int]]] = []
    for tid, vid in zip(times, vals):
        raw_t = P.first_value(values, tid)
        raw_v = P.as_int(P.first_value(values, vid))
        if P.as_int(raw_t) is None or raw_v is None:
            return None
        slots.append([decode_time(raw_t), raw_v])
    return slots


def parse_circuit(values: Mapping[str, Any], n: int) -> Dict[str, Any]:
    """Circuit ``n``'s time program from a read of ``circuit_ids(n)``.

    ``supported`` is False when the controller has no scheduler for the
    circuit; ``complete`` is False when a day could not be read (then it is
    None and the program must not be edited).
    """
    present_id, default_id = master_ids(n)
    present = P.as_int(P.first_value(values, present_id))
    default = P.as_int(P.first_value(values, default_id))
    if present is None and default is None:
        return {"index": n, "supported": False}
    days = [parse_day(values, n, d) for d in range(7)]
    return {
        "index": n,
        "supported": True,
        "complete": default in VALUES and all(d is not None for d in days),
        "present": present,
        "default": default,
        "days": days,
    }


# --- planning --------------------------------------------------------------------


def validate_points(points: Any) -> List[Tuple[int, int]]:
    """A day's switch points: at most six [minute, value] pairs, strictly
    ascending times within the day, values 0 (Eco) or 1 (Normal)."""
    if not isinstance(points, (list, tuple)) or len(points) > SLOTS:
        raise ValueError(f"a day has at most {SLOTS} switch points")
    out: List[Tuple[int, int]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("a switch point is [minute, value]")
        minute, value = point
        if isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute < 1440:
            raise ValueError("switch point time out of range")
        if isinstance(value, bool) or value not in VALUES:
            raise ValueError("switch point value must be 0 or 1")
        if out and minute <= out[-1][0]:
            raise ValueError("switch point times must be ascending")
        out.append((minute, value))
    return out


def plan_day(values: Mapping[str, Any], n: int, day: int, points: Sequence[Tuple[int, int]]) -> List[Write]:
    """Writes that turn ``day``'s stored switch points into ``points``.

    Only members that differ are written; unused slots keep their value.
    The order keeps the stored times ascending after every single write:
    slots whose time moves earlier first (ascending), then those that move
    later or become unused (descending). A slot that was unused gets its
    value before its time, so it never applies a stale value.
    """
    times, vals = day_ids(n, day)
    current = parse_day(values, n, day)
    if current is None:
        raise ValueError("the stored time program could not be read")
    new_t = [encode_time(m) for m, _ in points] + [UNUSED] * (SLOTS - len(points))
    new_v: List[Optional[int]] = [v for _, v in points] + [None] * (SLOTS - len(points))
    old_t = [P.as_int(P.first_value(values, tid)) for tid in times]

    def key(raw: Optional[int]) -> float:
        minute = decode_time(raw)
        return float("inf") if minute is None else float(minute)

    earlier = [i for i in range(SLOTS) if key(new_t[i]) <= key(old_t[i])]
    later = [i for i in reversed(range(SLOTS)) if key(new_t[i]) > key(old_t[i])]
    writes: List[Write] = []
    for i in earlier + later:
        time_w = [(times[i], new_t[i])] if new_t[i] != old_t[i] else []
        value_w = [(vals[i], new_v[i])] if new_v[i] is not None and new_v[i] != current[i][1] else []
        writes += value_w + time_w if current[i][0] is None else time_w + value_w
    return writes


# --- runtime ---------------------------------------------------------------------


async def async_read_circuits(ctrl: Dict[str, Any], circuits: Sequence[int], *, force: bool = False) -> Dict[int, Dict[str, Any]]:
    """Time programs of ``circuits``, from a cache younger than CACHE_SECONDS
    unless ``force``. A circuit whose read failed is left out."""
    cache: Dict[int, Tuple[float, Dict[str, Any]]] = ctrl.setdefault("schedule_cache", {})
    now = time.monotonic()
    out: Dict[int, Dict[str, Any]] = {}
    for n in circuits:
        hit = cache.get(n)
        if hit and not force and now - hit[0] < CACHE_SECONDS:
            out[n] = hit[1]
            continue
        try:
            payload = await ctrl["api"].read(circuit_ids(n))
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Reading the time program of circuit %s failed: %s", n, type(err).__name__)
            continue
        parsed = parse_circuit(payload.get("values") or {}, n)
        cache[n] = (time.monotonic(), parsed)
        out[n] = parsed
    return out


async def async_write_days(ctrl: Dict[str, Any], n: int, days: Mapping[int, Sequence[Tuple[int, int]]]) -> Dict[str, Any]:
    """Store the switch points of ``days`` and return circuit ``n``'s
    program as read back afterwards (planned from a fresh read under the
    controller's write lock, counted like every write)."""
    api = ctrl["api"]
    coordinator = ctrl["coordinator"]
    ids = circuit_ids(n, sorted(days))
    async with coordinator.async_write_session():
        try:
            fresh = (await api.read(ids)).get("values") or {}
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Reading the time program failed: {type(err).__name__}") from err
        try:
            writes = [w for day in sorted(days) for w in plan_day(fresh, n, day, days[day])]
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        try:
            for oid, value in writes:
                await api.write(oid, value)
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"Writing to the heat pump failed: {type(err).__name__}") from err
        finally:
            ctrl.setdefault("schedule_cache", {}).pop(n, None)
    programs = await async_read_circuits(ctrl, [n], force=True)
    if n not in programs:
        raise HomeAssistantError("Reading the time program back failed")
    return {**programs[n], "writes": len(writes)}


# --- websocket ---------------------------------------------------------------------


def _find(hass: HomeAssistant, plant_id: str) -> Tuple[Any, Optional[Dict[str, Any]]]:
    from .plant_api import iter_plants, plant_id as make_plant_id

    for entry, ctrl in iter_plants(hass):
        if make_plant_id(entry, ctrl) == plant_id:
            return entry, ctrl
    return None, None


def _climate(hass: HomeAssistant, ctrl: Dict[str, Any], n: int) -> Optional[str]:
    from .plant_api import _entity_id

    return _entity_id(hass, "climate", f"{ctrl['host']}:climate:hc{int(n)}")


def _present_circuits(ctrl: Dict[str, Any]) -> List[int]:
    from .plant_api import _values

    caps = P.compute_capabilities(_values(ctrl))
    return [] if caps.get("bms") else [int(c["index"]) for c in caps["circuits"]]


def _may(user: Any, entity_id: Optional[str], policy: str) -> bool:
    if user is None:
        return True
    if user.permissions.access_all_entities(policy):
        return True
    return bool(entity_id) and user.permissions.check_entity(entity_id, policy)


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_schedules)
    websocket_api.async_register_command(hass, ws_schedule_set)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedules",
        vol.Required("plant_id"): str,
        vol.Optional("force", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_schedules(hass: HomeAssistant, connection: Any, msg: Dict[str, Any]) -> None:
    """Every present circuit's time program the user may read (its climate)."""
    _entry, ctrl = _find(hass, msg["plant_id"])
    if ctrl is None:
        connection.send_error(msg["id"], "not_found", "Unknown plant")
        return
    user = connection.user
    circuits = [n for n in _present_circuits(ctrl) if _may(user, _climate(hass, ctrl, n), POLICY_READ)]
    programs = await async_read_circuits(ctrl, circuits, force=bool(msg.get("force")))
    out = []
    for n in circuits:
        if n not in programs:
            continue
        climate = _climate(hass, ctrl, n)
        out.append({**programs[n], "writable": bool(climate) and _may(user, climate, POLICY_CONTROL)})
    connection.send_result(msg["id"], {"plant_id": msg["plant_id"], "circuits": out})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/schedule/set",
        vol.Required("plant_id"): str,
        vol.Required("circuit"): vol.All(int, vol.Range(min=1, max=P.HC_COUNT)),
        vol.Required("days"): {vol.Coerce(int): list},
    }
)
@websocket_api.async_response
async def ws_schedule_set(hass: HomeAssistant, connection: Any, msg: Dict[str, Any]) -> None:
    """Store whole days of a circuit's time program (needs control of its climate)."""
    _entry, ctrl = _find(hass, msg["plant_id"])
    n = msg["circuit"]
    if ctrl is None or n not in _present_circuits(ctrl):
        connection.send_error(msg["id"], "not_found", "Unknown plant or circuit")
        return
    climate = _climate(hass, ctrl, n)
    if not climate or not _may(connection.user, climate, POLICY_CONTROL):
        connection.send_error(msg["id"], "unauthorized", "Not allowed to control this circuit")
        return
    try:
        days = {}
        for key, points in msg["days"].items():
            day = int(key)
            if not 0 <= day <= 6:
                raise ValueError("day must be 0 (Monday) .. 6 (Sunday)")
            days[day] = validate_points(points)
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    if not days:
        connection.send_error(msg["id"], "invalid_format", "No day given")
        return
    try:
        program = await async_write_days(ctrl, n, days)
    except HomeAssistantError as err:
        connection.send_error(msg["id"], "write_failed", str(err))
        return
    connection.send_result(msg["id"], {**program, "writable": True})
