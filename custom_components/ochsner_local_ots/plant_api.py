"""Websocket API for the heat pump card and panel.

``ochsner_local_ots/plants`` describes every loaded plant: capabilities,
circuits and, per role, the entity the frontend writes through (service
calls only) plus units, bounds, option strings and enum labels.
``ochsner_local_ots/subscribe`` pushes each plant's raw role values on every
coordinator update, so the frontend also sees roles whose entity is
disabled.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import voluptuous as vol

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


def build_role(
    hass: HomeAssistant,
    role: str,
    oid: str,
    candidates: List[Tuple[str, str, Dict[str, Any], bool]],
    catalog: Optional[DiscoveryCatalog],
    language: str,
) -> Dict[str, Any]:
    registry = er.async_get(hass)
    chosen: Optional[Tuple[str, str, Dict[str, Any], bool]] = None
    entity_id: Optional[str] = None
    for cand in candidates:
        eid = registry.async_get_entity_id(cand[0], DOMAIN, cand[1])
        if eid:
            chosen, entity_id = cand, eid
            break
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


def build_event(entry: Any, ctrl: Dict[str, Any]) -> Dict[str, Any]:
    values = _values(ctrl)
    caps = P.compute_capabilities(values)
    return {
        "plant_id": plant_id(entry, ctrl),
        "available": bool(ctrl["coordinator"].last_update_success),
        "updated": dt_util.utcnow().isoformat(timespec="seconds"),
        "capabilities": P.public_capabilities(caps),
        "values": P.role_values(values, P.role_ids(caps)),
    }


async def async_build_plants(hass: HomeAssistant, language: Optional[str]) -> Dict[str, Any]:
    language = normalize_language(language or getattr(hass.config, "language", "") or "en")
    try:
        catalog = await hass.async_add_executor_job(load_catalog)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Catalog unavailable for labels: %s", err)
        catalog = None
    plants = [build_plant(hass, entry, ctrl, catalog, language) for entry, ctrl in iter_plants(hass)]
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
    connection.send_result(msg["id"], await async_build_plants(hass, msg.get("language")))


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: Any, msg: Dict[str, Any]) -> None:
    msg_id = msg["id"]
    listeners: List[Callable[[], None]] = []

    @callback
    def push(entry: Any, ctrl: Dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg_id, build_event(entry, ctrl)))

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
