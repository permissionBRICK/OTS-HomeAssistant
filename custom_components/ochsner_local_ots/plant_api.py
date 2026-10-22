"""Websocket API for the heat pump card and panel.

``ochsner_local_ots/plants`` describes every loaded plant: capabilities,
circuits and, per role, the entity the frontend writes through (service
calls only) plus units, bounds, option strings and enum labels.
``ochsner_local_ots/subscribe`` pushes each plant's raw role values on every
coordinator update, so the frontend also sees roles whose entity is
disabled.

Both honour Home Assistant's entity read permissions: a user who may not
read every entity only gets roles whose entity they may read, and roles
without an entity only if they may read the section's anchor entity
(circuit -> its climate, DHW -> the water heater, heat pump / plant ->
the heat pump status sensor, else the flash write counter). Everything
else in their payload comes from what they may read too: a circuit only
with its climate, a capability only with the roles it is computed from,
the plant's name and model only with the plant anchor (else generic).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from . import plant as P
from .catalog import DiscoveryCatalog, load_catalog, normalize_language
from .const import DOMAIN, SIGNAL_PLANTS_CHANGED
from .dashboard import color_scheme
from .plant_entity import plant_runtime

_LOGGER = logging.getLogger(__name__)

API_VERSION = 1

# Entity platforms by config key, writable first (a writable control wins
# over a read-only sensor on the same datapoint).
_PLATFORMS: Tuple[Tuple[str, str, bool], ...] = (
    ("numbers", "number", True),
    ("selects", "select", True),
    ("switches", "switch", True),
    ("texts", "text", True),
    ("sensors", "sensor", False),
    ("binary_sensors", "binary_sensor", False),
)


def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_plants)
    websocket_api.async_register_command(hass, ws_subscribe)


def iter_plants(hass: HomeAssistant) -> Iterator[Tuple[Any, Dict[str, Any]]]:
    data = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = data.get(entry.entry_id)
        if not isinstance(runtime, dict):
            continue
        for ctrl in runtime.get("controllers") or []:
            if isinstance(ctrl, dict) and ctrl.get("coordinator") is not None:
                yield entry, ctrl


def plant_id(entry: Any, ctrl: Dict[str, Any]) -> str:
    return f"{entry.entry_id}:{ctrl['host']}"


def _values(ctrl: Dict[str, Any]) -> Dict[str, Any]:
    data = ctrl["coordinator"].data
    values = data.get("values") if isinstance(data, dict) else None
    return values if isinstance(values, dict) else {}


def entity_index(ctrl: Dict[str, Any]) -> Dict[str, List[Tuple[str, str, Dict[str, Any], bool]]]:
    """canonical read id -> [(domain, unique_id, cfg, writable)], writable first."""
    host = ctrl["host"]
    out: Dict[str, List[Tuple[str, str, Dict[str, Any], bool]]] = {}
    for key, domain, writable in _PLATFORMS:
        for cfg in ctrl.get(key) or []:
            if not isinstance(cfg, dict):
                continue
            rid = str((cfg.get("id") if not writable else None) or cfg.get("read_id") or cfg.get("id") or "")
            if not rid:
                continue
            uid = str(cfg.get("uuid") or "") or f"{host}:{domain}:{rid}".replace("=", "")
            out.setdefault(P.canonical_id(rid), []).append((domain, uid, cfg, writable))
    return out


def role_labels(
    role: str,
    cfg: Optional[Dict[str, Any]],
    catalog: Optional[DiscoveryCatalog],
    oid: str,
    language: str,
) -> Optional[Dict[str, str]]:
    labels = P.builtin_labels(role, language)
    if labels:
        return labels
    rec = catalog.points_by_id.get(P.canonical_id(oid)) if catalog else None
    tokens = cfg.get("enum_tokens") if isinstance(cfg, dict) else None
    enum_like = bool(
        tokens
        or (cfg or {}).get("value_map")
        or (cfg or {}).get("options")
        or (rec and (rec.get("value_map") or rec.get("options")))
    )
    if not enum_like:
        return None
    labels = P.catalog_labels(rec, language, catalog.enum_token_labels if catalog else None, tokens)
    if labels:
        return labels
    vm = (cfg or {}).get("value_map")
    if isinstance(vm, dict) and vm:
        return {str(k): str(v) for k, v in vm.items()}
    opts = (cfg or {}).get("options")
    if isinstance(opts, dict) and opts:
        return {str(P.as_int(v)): str(k) for k, v in opts.items() if P.as_int(v) is not None}
    return None


Candidate = Tuple[str, str, Dict[str, Any], bool]


def _role_entity(
    registry: er.EntityRegistry, candidates: List[Candidate]
) -> Tuple[Optional[Candidate], Optional[str]]:
    """The first candidate with a registered entity, and its entity id."""
    for cand in candidates:
        eid = registry.async_get_entity_id(cand[0], DOMAIN, cand[1])
        if eid:
            return cand, eid
    return None, None


def build_role(
    hass: HomeAssistant,
    role: str,
    oid: str,
    candidates: List[Candidate],
    catalog: Optional[DiscoveryCatalog],
    language: str,
) -> Dict[str, Any]:
    registry = er.async_get(hass)
    chosen, entity_id = _role_entity(registry, candidates)
    out: Dict[str, Any] = {"entity_id": entity_id}
    cfg = chosen[2] if chosen else (candidates[0][2] if candidates else None)
    state = hass.states.get(entity_id) if entity_id else None
    attrs = state.attributes if state is not None else {}
    if entity_id:
        reg = registry.async_get(entity_id)
        if reg is not None and reg.disabled_by is not None:
            out["enabled"] = False
    unit = (cfg or {}).get("unit") or attrs.get("unit_of_measurement") or P.role_unit(role)
    if unit:
        out["unit"] = unit
    if chosen and chosen[3]:
        out["writable"] = True
        domain = chosen[0]
        if domain == "select":
            opts = cfg.get("options") if isinstance(cfg, dict) else None
            if isinstance(opts, dict):
                out["options"] = {
                    str(P.as_int(v)): str(label)
                    for label, v in opts.items()
                    if P.as_int(v) is not None
                }
        elif domain == "number":
            for key, attr, fallback in (("min", "min", "min"), ("max", "max", "max"), ("step", "step", "step")):
                value = attrs.get(attr, (cfg or {}).get(fallback))
                try:
                    value = float(value) if value is not None else None
                except (TypeError, ValueError):
                    value = None
                # Numbers without a configured range use a ±1e9 text box.
                if value is not None and abs(value) < 1e8:
                    out[key] = value
    labels = role_labels(role, cfg, catalog, oid, language)
    if labels:
        out["labels"] = labels
    return out


def _entity_id(hass: HomeAssistant, domain: str, unique_id: str) -> Optional[str]:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)


# --- read permissions -----------------------------------------------------------

CanRead = Callable[[Optional[str]], bool]


def read_filter(user: Any) -> Optional[CanRead]:
    """None when ``user`` may read every entity, else an entity_id check.

    (Websocket connections always carry a user; None is an internal caller.)
    """
    if user is None:
        return None
    perms = user.permissions
    if perms.access_all_entities(POLICY_READ):
        return None
    return lambda entity_id: bool(entity_id) and perms.check_entity(entity_id, POLICY_READ)


def section_anchor(
    role: str,
    climates: Dict[int, Optional[str]],
    water_heater: Optional[str],
    plant_anchor: Optional[str],
) -> Optional[str]:
    """The entity whose read permission covers a role without an entity."""
    section = role.split(".", 1)[0]
    if section.startswith("hc") and section[2:].isdigit() and int(section[2:]) in climates:
        return climates[int(section[2:])]
    if section == "dhw":
        return water_heater
    # Heat pump, availability, pool and absent circuits' availability.
    return plant_anchor


def readable_roles(
    role_entities: Dict[str, Optional[str]],
    climates: Dict[int, Optional[str]],
    water_heater: Optional[str],
    flash_writes: Optional[str],
    can_read: CanRead,
) -> set:
    anchor = plant_anchor(role_entities, flash_writes)
    out = set()
    for role, eid in role_entities.items():
        target = eid or section_anchor(role, climates, water_heater, anchor)
        if can_read(target):
            out.add(role)
    return out


# Capability -> the roles it is computed from (B3); "cooling" follows the
# circuits the user sees, "dhw" also the water heater.
_CAPABILITY_SOURCES: Dict[str, Tuple[str, ...]] = {
    "bms": ("avail.bms",),
    "dhw": ("avail.dhw",),
    "buffer": ("avail.buffer",),
    "buffer_temp": ("avail.buffer_aux",),
    "buffer_aux_heater": ("avail.buffer_aux",),
    "aux": ("avail.aux",),
    "aux_temp": ("avail.aux",),
    "pool": ("avail.pool",),
    "silent": ("avail.silent",),
    "emergency": ("avail.emergency",),
    "power": ("hp.el_power", "hp.heat_output"),
}


def visible_capabilities(
    capabilities: Dict[str, bool],
    roles: set,
    circuits: List[Dict[str, Any]],
    water_heater_readable: bool,
) -> Dict[str, bool]:
    """The capabilities a restricted user may see; the others are omitted."""
    out: Dict[str, bool] = {}
    for key, value in capabilities.items():
        if key == "cooling":
            if circuits:
                out[key] = any(bool(c.get("cool")) for c in circuits)
        elif key == "dhw" and water_heater_readable:
            out[key] = value
        elif all(r in roles for r in _CAPABILITY_SOURCES.get(key, ("",))):
            out[key] = value
    return out


def plant_anchor(role_entities: Dict[str, Optional[str]], flash_writes: Optional[str]) -> Optional[str]:
    """The entity whose read permission covers the plant as a whole."""
    return role_entities.get("hp.status") or flash_writes


def filter_plant(plant: Dict[str, Any], can_read: CanRead, language: str) -> Optional[Dict[str, Any]]:
    """The ``plants`` entry as a restricted user may see it (None = nothing)."""
    climates = {c["index"]: c.get("climate") for c in plant["circuits"]}
    dhw = plant.get("dhw") or {}
    role_entities = {r: v.get("entity_id") for r, v in plant["roles"].items()}
    roles = readable_roles(role_entities, climates, dhw.get("water_heater"), plant.get("flash_writes"), can_read)

    def visible(eid: Optional[str]) -> Optional[str]:
        return eid if can_read(eid) else None

    # A circuit (name, flags, device) only with its climate entity, which
    # carries the same name and modes.
    circuits = [c for c in plant["circuits"] if visible(c.get("climate"))]
    new_dhw = None
    if plant.get("dhw") is not None:
        new_dhw = {k: visible(v) for k, v in dhw.items()}
        if not any(new_dhw.values()) and not any(r.startswith("dhw.") for r in roles):
            new_dhw = None
    flash = visible(plant.get("flash_writes"))
    if not roles and not circuits and new_dhw is None and flash is None:
        return None
    main = can_read(plant_anchor(role_entities, plant.get("flash_writes")))
    return {
        **plant,
        "device_id": plant["device_id"] if main else None,
        "title": plant["title"] if main else P.default_plant_title(language),
        "model": plant["model"] if main else "",
        "capabilities": visible_capabilities(
            plant["capabilities"], roles, circuits, bool(visible(dhw.get("water_heater"))),
        ),
        "circuits": circuits,
        "dhw": new_dhw,
        "roles": {r: v for r, v in plant["roles"].items() if r in roles},
        "flash_writes": flash,
    }


def event_view(
    hass: HomeAssistant, ctrl: Dict[str, Any], caps: Dict[str, Any], can_read: CanRead
) -> Tuple[set, Callable[[Dict[str, bool]], Dict[str, bool]]]:
    """For a restricted user's subscription push: the roles they may read
    and the capability filter (same rules as filter_plant)."""
    host = ctrl["host"]
    registry = er.async_get(hass)
    index = entity_index(ctrl)
    role_entities = {
        role: _role_entity(registry, index.get(P.canonical_id(oid), []))[1]
        for role, oid in P.role_ids(caps).items()
    }
    climates = {
        int(c["index"]): _entity_id(hass, "climate", f"{host}:climate:hc{int(c['index'])}")
        for c in caps["circuits"]
    }
    water_heater = _entity_id(hass, "water_heater", f"{host}:water_heater:dhw") if caps["dhw"] else None
    flash = _entity_id(hass, "sensor", f"{host}:flash_writes".replace("=", ""))
    roles = readable_roles(role_entities, climates, water_heater, flash, can_read)
    circuits = [c for c in caps["circuits"] if can_read(climates.get(int(c["index"])))]
    readable_heater = can_read(water_heater)
    return roles, lambda capabilities: visible_capabilities(capabilities, roles, circuits, readable_heater)


def build_plant(
    hass: HomeAssistant,
    entry: Any,
    ctrl: Dict[str, Any],
    catalog: Optional[DiscoveryCatalog],
    language: str,
) -> Dict[str, Any]:
    host = ctrl["host"]
    coordinator = ctrl["coordinator"]
    values = _values(ctrl)
    caps = P.compute_capabilities(values)
    plant = plant_runtime(ctrl)
    device_reg = dr.async_get(hass)

    title = str(ctrl.get("device_name") or host)
    device = device_reg.async_get(coordinator.device_id) if coordinator.device_id else None
    if device is not None:
        title = device.name_by_user or device.name or title

    circuit_devices = plant.get("circuit_devices") or {}
    circuits = []
    for c in caps["circuits"]:
        n = int(c["index"])
        uid, stored_name = circuit_devices.get(n, (f"tag:{P.HC_TAGS[n - 1]}", ""))
        hc_device = device_reg.async_get_device_by_identifier((DOMAIN, f"{host}:hc:{uid}"), entry.entry_id)
        name = (
            P.clean_text(P.first_value(values, P.circuit_role_ids(n)[P.hc_key(n, "name")]))
            or stored_name
            or P.default_circuit_name(n, language)
        )
        circuits.append(
            {
                "index": n,
                "name": name,
                "device_id": hc_device.id if hc_device else None,
                "heat": c["heat"],
                "cool": c["cool"],
                "room_sensor": c["room_sensor"],
                "humidity": c["humidity"],
                "climate": _entity_id(hass, "climate", f"{host}:climate:hc{n}"),
            }
        )

    dhw = None
    if caps["dhw"]:
        dhw = {
            "water_heater": _entity_id(hass, "water_heater", f"{host}:water_heater:dhw"),
            "boost_button": _entity_id(hass, "button", f"{host}:button:dhw_boost"),
        }

    index = entity_index(ctrl)
    roles = {
        role: build_role(hass, role, oid, index.get(P.canonical_id(oid), []), catalog, language)
        for role, oid in P.role_ids(caps).items()
    }

    return {
        "plant_id": plant_id(entry, ctrl),
        "entry_id": entry.entry_id,
        "host": host,
        "device_id": coordinator.device_id,
        "title": title,
        "model": str(ctrl.get("device_model") or "Climatix"),
        "available": bool(coordinator.last_update_success),
        "capabilities": P.public_capabilities(caps),
        "circuits": circuits,
        "dhw": dhw,
        "badges": list(P.BADGES),
        "roles": roles,
        "flash_writes": _entity_id(hass, "sensor", f"{host}:flash_writes".replace("=", "")),
    }


def build_event(
    entry: Any,
    ctrl: Dict[str, Any],
    view: Optional[Callable[[Dict[str, Any]], Tuple[set, Callable[[Dict[str, bool]], Dict[str, bool]]]]] = None,
) -> Optional[Dict[str, Any]]:
    """The subscription event.

    ``view(caps)`` returns the roles the user may read and their capability
    filter (None = everything); a plant they may read nothing of gives None.
    """
    values = _values(ctrl)
    caps = P.compute_capabilities(values)
    ids = P.role_ids(caps)
    capabilities = P.public_capabilities(caps)
    if view is not None:
        readable, visible_caps = view(caps)
        if not readable:
            return None
        ids = {r: oid for r, oid in ids.items() if r in readable}
        capabilities = visible_caps(capabilities)
    return {
        "plant_id": plant_id(entry, ctrl),
        "available": bool(ctrl["coordinator"].last_update_success),
        "updated": dt_util.utcnow().isoformat(timespec="seconds"),
        "capabilities": capabilities,
        "values": P.role_values(values, ids),
    }


async def async_build_plants(
    hass: HomeAssistant, language: Optional[str], can_read: Optional[CanRead] = None
) -> Dict[str, Any]:
    language = normalize_language(language or getattr(hass.config, "language", "") or "en")
    try:
        catalog = await hass.async_add_executor_job(load_catalog)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Catalog unavailable for labels: %s", err)
        catalog = None
    plants = [build_plant(hass, entry, ctrl, catalog, language) for entry, ctrl in iter_plants(hass)]
    if can_read is not None:
        plants = [p for p in (filter_plant(p, can_read, language) for p in plants) if p is not None]
    return {
        "version": API_VERSION,
        "settings": {"color_scheme": color_scheme(hass)},
        "plants": plants,
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/plants",
        vol.Optional("language"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_plants(hass: HomeAssistant, connection: Any, msg: Dict[str, Any]) -> None:
    can_read = read_filter(connection.user)
    connection.send_result(msg["id"], await async_build_plants(hass, msg.get("language"), can_read))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: Any, msg: Dict[str, Any]) -> None:
    msg_id = msg["id"]
    listeners: List[Callable[[], None]] = []
    can_read = read_filter(connection.user)

    @callback
    def push(entry: Any, ctrl: Dict[str, Any]) -> None:
        view = None
        if can_read is not None:
            view = lambda caps: event_view(hass, ctrl, caps, can_read)  # noqa: E731
        event = build_event(entry, ctrl, view)
        if event is not None:
            connection.send_message(websocket_api.event_message(msg_id, event))

    @callback
    def detach() -> None:
        while listeners:
            listeners.pop()()

    @callback
    def attach_and_push() -> None:
        detach()
        for entry, ctrl in list(iter_plants(hass)):
            listeners.append(
                ctrl["coordinator"].async_add_listener(
                    lambda entry=entry, ctrl=ctrl: push(entry, ctrl)
                )
            )
            push(entry, ctrl)

    unsub_signal = async_dispatcher_connect(hass, SIGNAL_PLANTS_CHANGED, attach_and_push)

    @callback
    def unsubscribe() -> None:
        unsub_signal()
        detach()

    connection.subscriptions[msg_id] = unsubscribe
    connection.send_result(msg_id)
    attach_and_push()
