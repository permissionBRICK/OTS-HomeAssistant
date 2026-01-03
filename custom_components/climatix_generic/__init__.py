from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

import voluptuous as vol

from homeassistant.const import CONF_HOST, CONF_PORT, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, SOURCE_IMPORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ClimatixGenericApi, ClimatixGenericConnection
from .const import (
    CONF_ID,
    CONF_OPTIONS,
    CONF_READ_ID,
    CONF_SELECTS,
    CONF_UUID,
    CONF_WRITE_ID,
    CONF_NAME,
    CONF_NUMBERS,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_SENSORS,
    CONF_UNIT,
    CONF_MIN,
    CONF_MAX,
    CONF_STEP,
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
        vol.Optional(CONF_MIN, default=0): vol.Coerce(float),
        vol.Optional(CONF_MAX, default=100): vol.Coerce(float),
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
        "numbers": normalized_numbers,
        "selects": normalized_selects,
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = dict(entry.data)

    host: str = data[CONF_HOST]
    port: int = int(data.get(CONF_PORT, DEFAULT_PORT))
    username: str = str(data.get(CONF_USERNAME, DEFAULT_USERNAME))
    password: str = str(data.get(CONF_PASSWORD, DEFAULT_PASSWORD))
    pin: str = str(data.get(CONF_PIN, DEFAULT_PIN))
    scan_interval_sec: int = int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC))

    ents = _normalize_entities(data)
    sensors = ents["sensors"]
    numbers = ents["numbers"]
    selects = ents["selects"]

    ids: List[str] = []
    for s in sensors:
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

    session = async_get_clientsession(hass)
    api = ClimatixGenericApi(
        session,
        ClimatixGenericConnection(
            host=host,
            port=port,
            username=username,
            password=password,
            pin=pin,
        ),
    )

    coordinator = ClimatixCoordinator(
        hass,
        api=api,
        ids=ids,
        update_interval=timedelta(seconds=scan_interval_sec),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
        "host": host,
        "port": port,
        "base_url": f"http://{host}:{port}" if int(port) != 80 else f"http://{host}",
        "sensors": sensors,
        "numbers": numbers,
        "selects": selects,
    }

    setups = []
    if sensors:
        setups.append("sensor")
    if numbers:
        setups.append("number")
    if selects:
        setups.append("select")
    if setups:
        await hass.config_entries.async_forward_entry_setups(entry, setups)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, ["sensor", "number", "select"])
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok
