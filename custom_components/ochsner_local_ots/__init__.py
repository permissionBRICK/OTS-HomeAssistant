from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ClimatixGenericApi, ClimatixGenericApiWriteHook, ClimatixGenericConnection
from .const import (
    CONF_ID,
    CONF_OPTIONS,
    CONF_READ_ID,
    CONF_SELECTS,
    CONF_UUID,
    CONF_WRITE_ID,
    CONF_NAME,
    CONF_BINARY_SENSORS,
    CONF_NUMBERS,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_SENSORS,
    CONF_UNIT,
    CONF_MIN,
    CONF_MAX,
    CONF_STEP,
    CONF_VALUE_MAP,
    CONF_CONTROLLERS,
    CONF_DEVICE_MODEL,
    CONF_PLANT_NAME,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .coordinator import ClimatixCoordinator


_SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Optional(CONF_UUID): str,
        vol.Required(CONF_ID): str,
        vol.Optional(CONF_UNIT): str,
        # Optional mapping for enum-ish sensors: raw_value -> display label
        vol.Optional(CONF_VALUE_MAP): {str: str},
    }
)

_BINARY_SENSOR_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Optional(CONF_UUID): str,
        vol.Required(CONF_ID): str,
        vol.Optional(CONF_VALUE_MAP): {str: str},
    }
)

_NUMBER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Optional(CONF_UUID): str,
        # Backwards compatible: either provide `id`, or provide `read_id` and/or `write_id`.
        vol.Optional(CONF_ID): str,
        vol.Optional(CONF_READ_ID): str,
        vol.Optional(CONF_WRITE_ID): str,
        vol.Optional(CONF_UNIT): str,
        # Only show sliders when min/max are explicitly provided.
        vol.Optional(CONF_MIN): vol.Coerce(float),
        vol.Optional(CONF_MAX): vol.Coerce(float),
        vol.Optional(CONF_STEP, default=0.5): vol.Coerce(float),
    }
)

_SELECT_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Optional(CONF_UUID): str,
        vol.Optional(CONF_ID): str,
        vol.Optional(CONF_READ_ID): str,
        vol.Optional(CONF_WRITE_ID): str,
        vol.Required(CONF_OPTIONS): {str: vol.Any(str, int, float)},
    }
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                vol.Optional(CONF_PIN, default=DEFAULT_PIN): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_SEC): vol.Coerce(int),
                vol.Optional(CONF_SENSORS, default=[]): [_SENSOR_SCHEMA],
                vol.Optional(CONF_BINARY_SENSORS, default=[]): [_BINARY_SENSOR_SCHEMA],
                vol.Optional(CONF_NUMBERS, default=[]): [_NUMBER_SCHEMA],
                vol.Optional(CONF_SELECTS, default=[]): [_SELECT_SCHEMA],
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: Dict[str, Any]) -> bool:
    cfg = config.get(DOMAIN)
    if not cfg:
        return True

    hass.data.setdefault(DOMAIN, {})
    if hass.data[DOMAIN].get("_import_started"):
        return True
    hass.data[DOMAIN]["_import_started"] = True

    # Ensure a config entry exists (and stays synced) so HA can register a Device.
    # The config flow's import step updates the existing entry when YAML changes.
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=dict(cfg),
        )
    )
    return True


def _normalize_entities(data: Dict[str, Any]) -> Dict[str, Any]:
    sensors: List[Dict[str, Any]] = list(data.get(CONF_SENSORS, []) or [])
    binary_sensors: List[Dict[str, Any]] = list(data.get(CONF_BINARY_SENSORS, []) or [])
    numbers: List[Dict[str, Any]] = list(data.get(CONF_NUMBERS, []) or [])
    selects: List[Dict[str, Any]] = list(data.get(CONF_SELECTS, []) or [])

    # Normalize numbers to have explicit read_id/write_id.
    normalized_numbers: List[Dict[str, Any]] = []
    for n in numbers:
        base_id = n.get(CONF_ID)
        read_id = n.get(CONF_READ_ID) or base_id
        write_id = n.get(CONF_WRITE_ID) or base_id or read_id
        if not read_id:
            raise vol.Invalid("Each number must define 'id' or 'read_id'")
        if not write_id:
            raise vol.Invalid("Each number must define 'id' or 'write_id'")
        nn = dict(n)
        nn[CONF_READ_ID] = str(read_id)
        nn[CONF_WRITE_ID] = str(write_id)
        normalized_numbers.append(nn)

    # Normalize selects to have explicit read_id/write_id.
    normalized_selects: List[Dict[str, Any]] = []
    for s in selects:
        base_id = s.get(CONF_ID)
        read_id = s.get(CONF_READ_ID) or base_id
        write_id = s.get(CONF_WRITE_ID) or base_id or read_id
        if not read_id:
            raise vol.Invalid("Each select must define 'id' or 'read_id'")
        if not write_id:
            raise vol.Invalid("Each select must define 'id' or 'write_id'")
        ss = dict(s)
        ss[CONF_READ_ID] = str(read_id)
        ss[CONF_WRITE_ID] = str(write_id)
        normalized_selects.append(ss)

    return {
        "sensors": sensors,
        "binary_sensors": binary_sensors,
        "numbers": normalized_numbers,
        "selects": normalized_selects,
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = dict(entry.data)

    # Reload the entry when options change (e.g., polling interval).
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    hass.data.setdefault(DOMAIN, {})

    session = async_get_clientsession(hass)

    controllers_raw = data.get(CONF_CONTROLLERS)
    controllers: List[Dict[str, Any]]
    if isinstance(controllers_raw, list) and controllers_raw:
        controllers = [dict(c) for c in controllers_raw if isinstance(c, dict)]
    else:
        # Backwards compatible single-controller entry (YAML import / previous UI).
        controllers = [
            {
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: int(data.get(CONF_PORT, DEFAULT_PORT)),
                CONF_USERNAME: str(data.get(CONF_USERNAME, DEFAULT_USERNAME)),
                CONF_PASSWORD: str(data.get(CONF_PASSWORD, DEFAULT_PASSWORD)),
                CONF_PIN: str(data.get(CONF_PIN, DEFAULT_PIN)),
                CONF_SCAN_INTERVAL: int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC)),
                CONF_SENSORS: list(data.get(CONF_SENSORS, []) or []),
                CONF_BINARY_SENSORS: list(data.get(CONF_BINARY_SENSORS, []) or []),
                CONF_NUMBERS: list(data.get(CONF_NUMBERS, []) or []),
                CONF_SELECTS: list(data.get(CONF_SELECTS, []) or []),
            }
        ]

    runtime_controllers: List[Dict[str, Any]] = []
    write_counts: Dict[str, int] = {}
    write_count_entities: Dict[str, Any] = {}
    any_sensors = any_binary_sensors = any_numbers = any_selects = False

    for ctrl in controllers:
        host: str = str(ctrl.get(CONF_HOST) or "")
        if not host:
            continue
        port: int = int(ctrl.get(CONF_PORT, DEFAULT_PORT))
        username: str = str(ctrl.get(CONF_USERNAME, DEFAULT_USERNAME))
        password: str = str(ctrl.get(CONF_PASSWORD, DEFAULT_PASSWORD))
        pin: str = str(ctrl.get(CONF_PIN, DEFAULT_PIN))
        override_scan_interval = entry.options.get(CONF_SCAN_INTERVAL)
        if override_scan_interval is not None:
            scan_interval_sec = int(override_scan_interval)
        else:
            scan_interval_sec = int(ctrl.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC))

        ents = _normalize_entities(ctrl)
        sensors = ents["sensors"]
        binary_sensors = ents["binary_sensors"]
        numbers = ents["numbers"]
        selects = ents["selects"]

        ids: List[str] = []
        for s in sensors:
            ent_id = s.get(CONF_ID)
            if ent_id and str(ent_id) not in ids:
                ids.append(str(ent_id))
        for s in binary_sensors:
            ent_id = s.get(CONF_ID)
            if ent_id and str(ent_id) not in ids:
                ids.append(str(ent_id))
        for n in numbers:
            ent_id = n.get(CONF_READ_ID)
            if ent_id and str(ent_id) not in ids:
                ids.append(str(ent_id))
        for s in selects:
            ent_id = s.get(CONF_READ_ID)
            if ent_id and str(ent_id) not in ids:
                ids.append(str(ent_id))

        inner_api = ClimatixGenericApi(
            session,
            ClimatixGenericConnection(
                host=host,
                port=port,
                username=username,
                password=password,
                pin=pin,
            ),
        )

        async def _on_write(host_key: str = host) -> None:
            write_counts[host_key] = int(write_counts.get(host_key, 0)) + 1
            ent = write_count_entities.get(host_key)
            if ent is not None:
                try:
                    ent.async_write_ha_state()
                except Exception:
                    # Never break writes due to counter UI.
                    pass

        api: Any = ClimatixGenericApiWriteHook(inner_api, on_write=_on_write)

        coordinator = ClimatixCoordinator(
            hass,
            api=api,
            ids=ids,
            update_interval=timedelta(seconds=scan_interval_sec),
        )
        await coordinator.async_config_entry_first_refresh()

        base_url = f"http://{host}:{port}" if int(port) != 80 else f"http://{host}"
        runtime_controllers.append(
            {
                "api": api,
                "coordinator": coordinator,
                "host": host,
                "port": port,
                "base_url": base_url,
                "device_name": str(ctrl.get(CONF_PLANT_NAME) or f"Climatix ({host})"),
                "device_model": str(ctrl.get(CONF_DEVICE_MODEL) or "Climatix"),
                "sensors": sensors,
                "binary_sensors": binary_sensors,
                "numbers": numbers,
                "selects": selects,
            }
        )

        any_sensors = any_sensors or bool(sensors)
        any_binary_sensors = any_binary_sensors or bool(binary_sensors)
        any_numbers = any_numbers or bool(numbers)
        any_selects = any_selects or bool(selects)

    hass.data[DOMAIN][entry.entry_id] = {
        "controllers": runtime_controllers,
        "write_counts": write_counts,
        "write_count_entities": write_count_entities,
    }

    setups = []
    # Always set up sensor platform (write-counter sensor is always present).
    if runtime_controllers:
        setups.append("sensor")
    if any_binary_sensors:
        setups.append("binary_sensor")
    if any_numbers:
        setups.append("number")
    if any_selects:
        setups.append("select")
    if setups:
        await hass.config_entries.async_forward_entry_setups(entry, setups)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "binary_sensor", "number", "select"])
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
