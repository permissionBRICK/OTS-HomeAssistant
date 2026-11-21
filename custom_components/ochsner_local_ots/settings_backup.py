"""Backups of the heat pump's user settings, and restoring them.

A backup is a fresh read of the settings a user can change (parameters such
as the heating curves and building inertia, setpoints, operating programs,
circuit names, sensor offsets) and of the heating circuits' time programs.
It only reads the controller, so it costs no flash writes.

Backups live in Home Assistant's storage (``.storage/ochsner_local_ots.
settings_backups``), so Home Assistant's own backups contain them. Each time
the integration loads it takes one automatically, once the startup polls
are done, and keeps it only when something changed since the last backup;
the "Back up settings" button takes one on demand. The diagnostics download
exports them as a file the restore accepts again.

A restore compares a backup with a fresh read and writes only the settings
that differ, through the counted write path. The options flow shows the
differences first, and the current settings are backed up before anything
is written, so a restore can be undone.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import plant as P
from . import schedule as S
from .api import clean_number
from .catalog import DiscoveryCatalog, apply_v4_point_flags, load_catalog, resolve_point_name, try_decode_oa
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}.settings_backups"
STORAGE_VERSION = 1
EXPORT_FORMAT = f"{DOMAIN}.settings_backup"
EXPORT_FORMAT_VERSION = 1

SOURCE_AUTOMATIC = "automatic"
SOURCE_MANUAL = "manual"
SOURCE_BEFORE_RESTORE = "before_restore"
SOURCE_FILE = "file"  # an imported backup (never stored)
SOURCES = (SOURCE_AUTOMATIC, SOURCE_MANUAL, SOURCE_BEFORE_RESTORE)

# Kept per controller: the newest automatic backups (taken when the
# integration loads and before a restore) and, separately, the newest ones
# taken with the button, so a series of restarts never pushes those out.
KEEP_AUTOMATIC = 10
KEEP_MANUAL = 10

# The automatic backup waits until the startup polls are done.
AUTO_BACKUP_DELAY_SEC = 60

SIGNAL_BACKUPS_CHANGED = f"{DOMAIN}_settings_backups_changed"

_DATA_STORE = "_settings_backups"
_DATA_STORE_LOCK = "_settings_backups_lock"


class BackupError(HomeAssistantError):
    """Reading or restoring the settings failed (message for the log/UI)."""


# --- which datapoints are settings ------------------------------------------------

# (object type & 0xFF, member) of the points that hold a user setting:
# 0 parameters, 1 setpoints, 2 operating programs (member 290 is a circuit's
# program selection), 4 texts (the circuit names), and member 12361 of an
# input, its sensor offset. Inputs themselves (measurements), outputs,
# status values and computed values (days left of the screed program, ...)
# hold no setting, even where the controller lets them be written.
_SETTING_KINDS = frozenset({(0, 256), (1, 256), (2, 256), (2, 290), (3, 12361), (4, 256)})

# Writable points of a setting kind that are no user setting: the emergency
# mode points report or confirm a fault state.
_NOT_A_SETTING = re.compile(r"(?i)emergency|notbetrieb")

_PLATFORM_KEYS = (("numbers", "number"), ("selects", "select"), ("switches", "switch"), ("texts", "text"))


def _season_ids() -> frozenset:
    # The circuits' summer/winter changeover is disabled as an entity (the
    # thermostat's heat/cool/auto modes write it) but is a user setting.
    return frozenset(
        P.canonical_id(P.circuit_role_ids(n)[P.hc_key(n, "season")]) for n in range(1, P.HC_COUNT + 1)
    )


_ALWAYS_SETTINGS = _season_ids()


@dataclass(frozen=True)
class Setting:
    """A writable datapoint the backup covers, as the controller config has it."""

    key: str  # canonical read id
    read_id: str
    write_id: str
    platform: str
    cfg: Mapping[str, Any]


def is_setting(cfg: Mapping[str, Any], catalog: DiscoveryCatalog) -> bool:
    """Whether a writable entity config holds a user setting.

    The catalog's classification decides (by read id, so it does not depend
    on the language of the names): service, test, network, privacy and
    technical points are disabled by default there and never part of a
    backup. Supplements (writable points the app uses that the catalog
    lacks) are settings; an entity from an old cloud bundle that the catalog
    does not know is judged by the catalog's rules on its name.
    """
    rid = P.canonical_id(cfg.get("read_id"))
    if not rid or not (cfg.get("write_id") or cfg.get("read_id")):
        return False
    oa = try_decode_oa(rid)
    if oa is None or (oa.object_type & 0xFF, oa.member_id) not in _SETTING_KINDS:
        return False
    if rid in _ALWAYS_SETTINGS:
        return True
    names = [str(cfg.get("name") or "")]
    rec = catalog.points_by_id.get(rid)
    if rec is not None:
        if rec.get("enabled_default") is False:
            return False
        names += [str(rec.get(k) or "") for k in ("name", "name_en", "name_de")]
    elif not cfg.get("supplement"):
        flags = apply_v4_point_flags({"name": names[0], "write_id": str(cfg.get("write_id") or rid)})
        if flags.get("enabled_default") is False:
            return False
    if cfg.get("enabled_default") is False:
        return False
    return not any(_NOT_A_SETTING.search(n) for n in names if n)


def settings(ctrl: Mapping[str, Any], catalog: DiscoveryCatalog) -> Dict[str, Setting]:
    """The controller's settings by canonical read id (first entity wins)."""
    out: Dict[str, Setting] = {}
    for key, platform in _PLATFORM_KEYS:
        for cfg in ctrl.get(key) or []:
            if not isinstance(cfg, Mapping) or not is_setting(cfg, catalog):
                continue
            rid = str(cfg.get("read_id"))
            k = P.canonical_id(rid)
            if k not in out:
                out[k] = Setting(k, rid, str(cfg.get("write_id") or rid), platform, cfg)
    return out


# --- values ------------------------------------------------------------------------


def values_equal(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, str) or isinstance(b, str):
        return str(a) == str(b)
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def coerce(setting: Setting, value: Any) -> Any:
    """A backed-up value as it is written to ``setting`` (None: unusable)."""
    if value is None or isinstance(value, (dict, list)):
        return None
    if setting.platform == "text":
        return str(value)
    if isinstance(value, str):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if setting.platform == "number":
        return clean_number(number, setting.cfg.get("step"))
    return int(round(number))


async def _async_read(ctrl: Mapping[str, Any], ids: Sequence[str]) -> Dict[str, Any]:
    """Fresh values by canonical id; ids the controller did not answer are left out."""
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return {}
    payload = await ctrl["api"].read(ids)
    raw = payload.get("values") if isinstance(payload, dict) else None
    out: Dict[str, Any] = {}
    if isinstance(raw, dict):
        for oid in ids:
            value = P.first_value(raw, oid)
            if value is not None and not isinstance(value, (dict, list)):
                out[P.canonical_id(oid)] = value
    return out


def _schedule_of(program: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not program.get("supported") or not program.get("complete"):
        return None
    return {"default": int(program["default"]), "days": [list(map(list, d)) for d in program["days"]]}


async def async_read_settings(
    hass: HomeAssistant, ctrl: Mapping[str, Any]
) -> Tuple[Dict[str, Setting], Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """(settings, their current values, time programs by circuit) from one
    fresh read, taken between two commands (under the write lock)."""
    catalog = await hass.async_add_executor_job(load_catalog)
    found = settings(ctrl, catalog)
    try:
        async with ctrl["coordinator"].async_write_session():
            values = await _async_read(ctrl, [s.read_id for s in found.values()])
            programs = await S.async_read_circuits(ctrl, S._present_circuits(ctrl), force=True)
    except HomeAssistantError:
        raise
    except Exception as err:  # noqa: BLE001
        raise BackupError(f"Reading the heat pump settings failed: {type(err).__name__}") from err
    schedules = {str(n): sch for n, p in sorted(programs.items()) if (sch := _schedule_of(p)) is not None}
    return found, values, schedules


# --- time programs ------------------------------------------------------------------

DAY_MINUTES = 1440


def day_points(slots: Any) -> List[Tuple[int, int]]:
    """Used switch points of a day's six slots, sorted by time (schedule.js)."""
    out = []
    for slot in slots or []:
        if isinstance(slot, (list, tuple)) and len(slot) == 2 and isinstance(slot[0], int):
            if 0 <= slot[0] < DAY_MINUTES:
                out.append((slot[0], S.NORMAL if slot[1] == S.NORMAL else S.ECO))
    return sorted(out)


def normal_periods(points: Sequence[Tuple[int, int]], default: int) -> List[Tuple[int, int]]:
    """The Normal periods [start, end) the points give on the default's background."""
    segments: List[List[int]] = []
    current, at = (S.NORMAL if default == S.NORMAL else S.ECO), 0
    for minute, value in points:
        if minute > at:
            segments.append([at, minute, current])
            at = minute
        current = value
    segments.append([at, DAY_MINUTES, current])
    merged: List[List[int]] = []
    for seg in segments:
        if merged and merged[-1][2] == seg[2]:
            merged[-1][1] = seg[1]
        else:
            merged.append(list(seg))
    return [(s, e) for s, e, v in merged if v == S.NORMAL]


def points_for(periods: Sequence[Tuple[int, int]], default: int) -> List[Tuple[int, int]]:
    """The fewest switch points giving ``periods`` with ``default`` (schedule.js)."""
    segs: List[Tuple[int, int]] = []
    at = 0
    for start, end in periods:
        if start > at:
            segs.append((at, S.ECO))
        segs.append((start, S.NORMAL))
        at = end
    if at < DAY_MINUTES:
        segs.append((at, S.ECO))
    out: List[Tuple[int, int]] = []
    current = S.NORMAL if default == S.NORMAL else S.ECO
    for minute, value in segs:
        if value != current:
            out.append((minute, value))
        current = value
    return out


def write_count(slots: Any, points: Sequence[Tuple[int, int]]) -> int:
    """Members a write of ``points`` changes in a day's stored slots (schedule.js)."""
    n = 0
    for i in range(S.SLOTS):
        cur = slots[i] if isinstance(slots, list) and i < len(slots) else [None, None]
        point = points[i] if i < len(points) else None
        if (point[0] if point else None) != cur[0]:
            n += 1
        if point and point[1] != cur[1]:
            n += 1
    return n


# --- labels -------------------------------------------------------------------------

_WORDS = {
    "en": {
        "automatic": "automatic", "manual": "manual", "before_restore": "before a restore", "file": "file",
        "on": "on", "off": "off", "time_program": "time program", "normal": "Normal", "night": "Night",
        "night_all_day": "Night all day", "normal_all_day": "Normal all day",
        "days": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
        "rejected": "the heat pump rejected the value", "kept": "the heat pump kept {value}",
        "not_restored": "not restored", "time_program_of": "Time program of circuit {n}",
        "too_many_points": "needs more than six switch points",
        "skipped": "Left as they are (this plant lacks them, or they cannot be read right now): {names}.",
        "failed": "Not restored:",
    },
    "de": {
        "automatic": "automatisch", "manual": "manuell", "before_restore": "vor einer Wiederherstellung",
        "file": "Datei", "on": "ein", "off": "aus", "time_program": "Zeitprogramm", "normal": "Normal",
        "night": "Nacht", "night_all_day": "ganztägig Nacht", "normal_all_day": "ganztägig Normal",
        "days": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
        "rejected": "die Wärmepumpe hat den Wert abgelehnt", "kept": "die Wärmepumpe hat {value} behalten",
        "not_restored": "nicht wiederhergestellt", "time_program_of": "Zeitprogramm von Heizkreis {n}",
        "too_many_points": "braucht mehr als sechs Schaltpunkte",
        "skipped": "Bleiben unverändert (gibt es hier nicht, oder sie sind gerade nicht lesbar): {names}.",
        "failed": "Nicht wiederhergestellt:",
    },
}


def _words(language: str) -> Dict[str, Any]:
    return _WORDS["de" if P.normalize_language(language) == "de" else "en"]


def language_of(ctrl: Mapping[str, Any], fallback: str = "en") -> str:
    plant = ctrl.get("plant") if isinstance(ctrl.get("plant"), Mapping) else {}
    return P.normalize_language(plant.get("language") or fallback)


def _number(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f.is_integer():
        return str(int(f))
    return f"{round(f, 4):g}"


def display_value(setting: Optional[Setting], value: Any, language: str) -> str:
    if value is None:
        return "–"
    words = _words(language)
    platform = setting.platform if setting else ""
    cfg = setting.cfg if setting else {}
    if platform == "select":
        for label, raw in (cfg.get("options") or {}).items():
            if values_equal(raw, value):
                return str(label)
        return _number(value)
    if platform == "switch":
        if values_equal(value, cfg.get("on_value", 1)):
            return words["on"]
        if values_equal(value, cfg.get("off_value", 0)):
            return words["off"]
        return _number(value)
    if platform == "text":
        return f"“{value}”"
    unit = cfg.get("unit")
    return f"{_number(value)} {unit}" if unit else _number(value)


def setting_name(setting: Optional[Setting], key: str, catalog: DiscoveryCatalog, language: str) -> str:
    """'Circuit, name' of a setting (the catalog name when the plant lacks it)."""
    if setting is not None:
        name = str(setting.cfg.get("name") or key)
        circuit = str(setting.cfg.get("heating_circuit_name") or "").strip()
        return f"{circuit}, {name}" if circuit else name
    rec = catalog.points_by_id.get(key)
    if rec is not None:
        return resolve_point_name(rec, language)[0]
    return key


def circuit_name(ctrl: Mapping[str, Any], n: int, language: str) -> str:
    plant = ctrl.get("plant") if isinstance(ctrl.get("plant"), Mapping) else {}
    _uid, name = (plant.get("circuit_devices") or {}).get(n, ("", ""))
    return name or P.default_circuit_name(n, language)


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def display_periods(periods: Sequence[Tuple[int, int]], language: str) -> str:
    words = _words(language)
    if not periods:
        return words["night_all_day"]
    if list(periods) == [(0, DAY_MINUTES)]:
        return words["normal_all_day"]
    return f"{words['normal']} " + ", ".join(f"{_clock(s)}–{_clock(e)}" for s, e in periods)


def skipped_sentence(names: Sequence[str], language: str) -> str:
    return _words(language)["skipped"].format(names=", ".join(names))


def failed_text(failed: Sequence[str], language: str) -> str:
    """A Markdown list of what was not restored (empty when everything was)."""
    if not failed:
        return ""
    return _words(language)["failed"] + "\n" + "\n".join(f"- {line}" for line in failed)


def source_label(source: str, language: str) -> str:
    return _words(language).get(source, source)


def created_at(backup: Mapping[str, Any]) -> Optional[datetime]:
    parsed = dt_util.parse_datetime(str(backup.get("created") or ""))
    return dt_util.as_utc(parsed) if parsed else None


def backup_label(backup: Mapping[str, Any], language: str) -> str:
    """'21.11.2026 14:03 · automatic' in the local time zone."""
    when = created_at(backup)
    fmt = "%d.%m.%Y %H:%M" if P.normalize_language(language) == "de" else "%Y-%m-%d %H:%M"
    stamp = dt_util.as_local(when).strftime(fmt) if when else "?"
    return f"{stamp} · {source_label(str(backup.get('source') or ''), language)}"


# --- storage ------------------------------------------------------------------------


def fingerprint(serial: Any) -> Optional[str]:
    """Identifies the controller in a backup without its serial number."""
    serial = str(serial or "").strip()
    if not serial:
        return None
    return hashlib.sha256(f"{DOMAIN}:{serial}".encode()).hexdigest()[:16]


def controller_info(ctrl: Mapping[str, Any]) -> Dict[str, Any]:
    # No name: a plant's default name contains the controller's address.
    return {
        "model": ctrl.get("device_model"),
        "sw_version": ctrl.get("device_sw_version"),
        "fingerprint": fingerprint(ctrl.get("device_serial")),
    }


def _same_content(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    av, bv = a.get("values") or {}, b.get("values") or {}
    if set(av) != set(bv) or any(not values_equal(av[k], bv[k]) for k in av):
        return False
    return (a.get("schedules") or {}) == (b.get("schedules") or {})


class BackupStore:
    """The stored backups of every controller, newest first."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._controllers: Dict[str, List[Dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load()
        controllers = data.get("controllers") if isinstance(data, dict) else None
        if isinstance(controllers, dict):
            self._controllers = {
                str(k): [b for b in v if isinstance(b, dict) and validate_backup(b)]
                for k, v in controllers.items()
                if isinstance(v, list)
            }

    def backups(self, key: str) -> List[Dict[str, Any]]:
        return list(self._controllers.get(key) or [])

    def latest(self, key: str) -> Optional[Dict[str, Any]]:
        backups = self._controllers.get(key) or []
        return backups[0] if backups else None

    def get(self, key: str, backup_id: str) -> Optional[Dict[str, Any]]:
        return next((b for b in self._controllers.get(key) or [] if b.get("id") == backup_id), None)

    async def async_add(self, key: str, backup: Dict[str, Any]) -> bool:
        """Store ``backup`` (newest); an automatic one only when it differs
        from the newest stored backup. Returns whether it was stored."""
        async with self._lock:
            backups = list(self._controllers.get(key) or [])
            if backup.get("source") != SOURCE_MANUAL and backups and _same_content(backups[0], backup):
                return False
            backups.insert(0, backup)
            manual = [b for b in backups if b.get("source") == SOURCE_MANUAL][:KEEP_MANUAL]
            automatic = [b for b in backups if b.get("source") != SOURCE_MANUAL][:KEEP_AUTOMATIC]
            keep = {id(b) for b in manual + automatic}
            self._controllers[key] = [b for b in backups if id(b) in keep]
            await self._store.async_save({"controllers": self._controllers})
            return True


async def async_get_store(hass: HomeAssistant) -> BackupStore:
    data = hass.data.setdefault(DOMAIN, {})
    lock = data.setdefault(_DATA_STORE_LOCK, asyncio.Lock())
    async with lock:
        store = data.get(_DATA_STORE)
        if store is None:
            store = BackupStore(hass)
            await store.async_load()
            data[_DATA_STORE] = store
    return store


def loaded_store(hass: HomeAssistant) -> Optional[BackupStore]:
    """The store if it was loaded already (for entity states, no I/O)."""
    return (hass.data.get(DOMAIN) or {}).get(_DATA_STORE)


# --- backing up ---------------------------------------------------------------------


@lru_cache(maxsize=1)
def _integration_version() -> str:
    """This integration's version (recorded in each backup)."""
    manifest = Path(__file__).with_name("manifest.json")
    return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "")



async def async_create_backup(hass: HomeAssistant, ctrl: Mapping[str, Any], source: str) -> Tuple[Dict[str, Any], bool]:
    """Back up the controller's settings now. Returns (backup, stored): an
    automatic backup identical to the newest one is not stored again, and
    the newest one is returned instead."""
    _found, values, schedules = await async_read_settings(hass, ctrl)
    if not values:
        raise BackupError("The heat pump answered none of its settings")
    version = await hass.async_add_executor_job(_integration_version)
    backup = {
        "id": uuid.uuid4().hex,
        "created": dt_util.utcnow().isoformat(timespec="seconds"),
        "source": source,
        "version": version,
        "controller": controller_info(ctrl),
        "values": values,
        "schedules": schedules,
    }
    store = await async_get_store(hass)
    key = str(ctrl["host"])
    stored = await store.async_add(key, backup)
    if stored:
        async_dispatcher_send(hass, SIGNAL_BACKUPS_CHANGED, key)
        _LOGGER.debug("Stored a settings backup (%s): %d settings, %d time programs", source, len(values), len(schedules))
        return backup, True
    return store.latest(key) or backup, False


async def async_auto_backup(hass: HomeAssistant, ctrl: Mapping[str, Any]) -> None:
    """The automatic backup when the integration loads (never raises)."""
    await asyncio.sleep(AUTO_BACKUP_DELAY_SEC)
    try:
        await async_create_backup(hass, ctrl, SOURCE_AUTOMATIC)
    except Exception as err:  # noqa: BLE001
        # The heat pump may just be unreachable; the next start tries again.
        _LOGGER.info("The automatic settings backup was skipped: %s", err)


# --- restoring ----------------------------------------------------------------------


@dataclass
class Change:
    """One difference between a backup and the controller, restorable on its own."""

    key: str
    label: str
    writes: int
    setting: Optional[Setting] = None
    value: Any = None
    circuit: int = 0
    day: int = 0
    points: Tuple[Tuple[int, int], ...] = ()
    periods: Tuple[Tuple[int, int], ...] = ()


@dataclass
class Plan:
    changes: List[Change] = field(default_factory=list)
    # Backed-up settings this controller cannot take back (labels).
    skipped: List[str] = field(default_factory=list)

    @property
    def writes(self) -> int:
        return sum(c.writes for c in self.changes)


@dataclass
class RestoreResult:
    restored: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    writes: int = 0


def plan_restore(
    ctrl: Mapping[str, Any],
    backup: Mapping[str, Any],
    found: Mapping[str, Setting],
    values: Mapping[str, Any],
    schedules: Mapping[str, Any],
    catalog: DiscoveryCatalog,
    language: str,
) -> Plan:
    """The differences between ``backup`` and the current ``values`` /
    ``schedules``. A setting the plant no longer has, or whose current value
    could not be read, is skipped: a write is never planned blind."""
    plan = Plan()
    words = _words(language)
    for raw_key, target in (backup.get("values") or {}).items():
        key = P.canonical_id(raw_key)
        setting = found.get(key)
        name = setting_name(setting, key, catalog, language)
        if setting is None or key not in values:
            plan.skipped.append(name)
            continue
        value = coerce(setting, target)
        if value is None:
            plan.skipped.append(name)
            continue
        current = values[key]
        if values_equal(current, value):
            continue
        label = f"{name}: {display_value(setting, current, language)} → {display_value(setting, value, language)}"
        plan.changes.append(Change(key=f"v:{key}", label=label, writes=1, setting=setting, value=value))

    for n_key, target in sorted((backup.get("schedules") or {}).items()):
        n = int(n_key)
        current = schedules.get(str(n))
        title = f"{circuit_name(ctrl, n, language)}, {words['time_program']}"
        if current is None:
            plan.skipped.append(title)
            continue
        for day in range(7):
            want = normal_periods(day_points(target["days"][day]), target["default"])
            have = normal_periods(day_points(current["days"][day]), current["default"])
            if want == have:
                continue
            day_title = f"{title} {words['days'][day]}"
            points = points_for(want, current["default"])
            if len(points) > S.SLOTS:
                plan.skipped.append(f"{day_title} ({words['too_many_points']})")
                continue
            plan.changes.append(
                Change(
                    key=f"s:{n}:{day}",
                    label=f"{day_title}: {display_periods(have, language)} → {display_periods(want, language)}",
                    writes=write_count(current["days"][day], points),
                    circuit=n,
                    day=day,
                    points=tuple(points),
                    periods=tuple(want),
                )
            )
    return plan


async def async_plan_restore(hass: HomeAssistant, ctrl: Mapping[str, Any], backup: Mapping[str, Any]) -> Plan:
    found, values, schedules = await async_read_settings(hass, ctrl)
    catalog = await hass.async_add_executor_job(load_catalog)
    return plan_restore(ctrl, backup, found, values, schedules, catalog, language_of(ctrl))


async def async_restore(
    hass: HomeAssistant, ctrl: Mapping[str, Any], backup: Mapping[str, Any], keys: Iterable[str]
) -> RestoreResult:
    """Write the selected differences (``Change.key``) back to the controller.

    The current settings are backed up first. Then the differences are
    planned again from a fresh read, so only what still differs is written.
    Writes the controller rejects are tried again after the others while
    some of them still succeed (a limit written later may allow them), and
    every written value is read back.
    """
    wanted = set(keys)
    await async_create_backup(hass, ctrl, SOURCE_BEFORE_RESTORE)
    plan = await async_plan_restore(hass, ctrl, backup)
    selected = [c for c in plan.changes if c.key in wanted]
    language = language_of(ctrl)
    words = _words(language)
    result = RestoreResult()
    api = ctrl["api"]
    coordinator = ctrl["coordinator"]

    values = [c for c in selected if c.setting is not None]
    if values:
        async with coordinator.async_write_session() as session:
            session.refresh([c.setting.read_id for c in values])
            pending, written = values, []
            while pending:
                rejected = []
                for change in pending:
                    try:
                        await api.write(change.setting.write_id, change.value)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.debug("Restoring %s failed: %s", change.setting.write_id, type(err).__name__)
                        rejected.append(change)
                    else:
                        result.writes += 1
                        written.append(change)
                if len(rejected) == len(pending):
                    result.failed += [f"{c.label} ({words['rejected']})" for c in rejected]
                    break
                pending = rejected
            try:
                check = await _async_read(ctrl, [c.setting.read_id for c in written])
            except Exception as err:  # noqa: BLE001
                raise BackupError(f"Reading the restored settings back failed: {type(err).__name__}") from err
            for change in written:
                now = check.get(change.setting.key)
                if values_equal(now, change.value):
                    result.restored.append(change.label)
                else:
                    kept = display_value(change.setting, now, language)
                    result.failed.append(f"{change.label} ({words['kept'].format(value=kept)})")

    by_circuit: Dict[int, List[Change]] = {}
    for change in selected:
        if change.setting is None:
            by_circuit.setdefault(change.circuit, []).append(change)
    for n, changes in sorted(by_circuit.items()):
        try:
            program = await S.async_write_days(ctrl, n, {c.day: list(c.points) for c in changes})
        except HomeAssistantError as err:
            _LOGGER.debug("Restoring the time program of circuit %s failed: %s", n, err)
            result.failed += [f"{c.label} ({words['rejected']})" for c in changes]
            continue
        result.writes += int(program.get("writes") or 0)
        for change in changes:
            stored = program.get("days") or []
            now = (
                normal_periods(day_points(stored[change.day]), program.get("default"))
                if change.day < len(stored) and stored[change.day] is not None
                else None
            )
            if now == list(change.periods):
                result.restored.append(change.label)
            else:
                result.failed.append(f"{change.label} ({words['not_restored']})")
    return result


# --- export / import ----------------------------------------------------------------


def export(ctrl: Mapping[str, Any], backups: Sequence[Mapping[str, Any]], catalog: DiscoveryCatalog) -> Dict[str, Any]:
    """The controller's backups as a file (the diagnostics download), with
    readable names for the settings."""
    language = language_of(ctrl)
    found = settings(ctrl, catalog)
    names: Dict[str, str] = {}
    for backup in backups:
        for key in backup.get("values") or {}:
            if key not in names:
                names[key] = setting_name(found.get(key), key, catalog, language)
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "controller": controller_info(ctrl),
        "names": names,
        "backups": [dict(b) for b in backups],
    }


def _valid_schedule(sch: Any) -> bool:
    if not isinstance(sch, dict) or sch.get("default") not in S.VALUES:
        return False
    days = sch.get("days")
    if not isinstance(days, list) or len(days) != 7:
        return False
    for day in days:
        if not isinstance(day, list) or len(day) != S.SLOTS:
            return False
        for slot in day:
            if not isinstance(slot, list) or len(slot) != 2:
                return False
            minute, value = slot
            if minute is not None and (isinstance(minute, bool) or not isinstance(minute, int) or not 0 <= minute < DAY_MINUTES):
                return False
            if isinstance(value, bool) or not isinstance(value, int):
                return False
    return True


def validate_backup(backup: Any) -> bool:
    if not isinstance(backup, dict) or not isinstance(backup.get("id"), str) or not created_at(backup):
        return False
    values = backup.get("values")
    if not isinstance(values, dict) or not values:
        return False
    for key, value in values.items():
        if try_decode_oa(key) is None or isinstance(value, (dict, list)):
            return False
    schedules = backup.get("schedules") or {}
    if not isinstance(schedules, dict):
        return False
    for n_key, sch in schedules.items():
        if not str(n_key).isdigit() or not 1 <= int(n_key) <= P.HC_COUNT or not _valid_schedule(sch):
            return False
    return True


def _exports(data: Any, depth: int = 0) -> Iterator[Dict[str, Any]]:
    """Every export in ``data``: a file from this module, or a diagnostics
    download (which nests them under "data", per controller)."""
    if depth > 4:
        return
    if isinstance(data, dict):
        if data.get("format") == EXPORT_FORMAT:
            yield data
            return
        for value in data.values():
            yield from _exports(value, depth + 1)
    elif isinstance(data, list):
        for value in data:
            yield from _exports(value, depth + 1)


def parse_import(data: Any) -> List[Dict[str, Any]]:
    """The valid exports in an uploaded file (ValueError when there is none).
    Each keeps only its valid backups, newest first."""
    out = []
    for item in _exports(data):
        if item.get("format_version") != EXPORT_FORMAT_VERSION:
            continue
        backups = [b for b in item.get("backups") or [] if validate_backup(b)]
        if not backups:
            continue
        backups.sort(key=lambda b: created_at(b), reverse=True)
        controller = item.get("controller") if isinstance(item.get("controller"), dict) else {}
        out.append({"controller": controller, "backups": backups})
    if not out:
        raise ValueError("no settings backup in the file")
    return out
