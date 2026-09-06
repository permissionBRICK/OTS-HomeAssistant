"""Home Assistant network discovery and persisted controller addresses."""

from __future__ import annotations

import asyncio
from dataclasses import replace

from homeassistant.components import dhcp, network
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ClimatixGenericConnection
from .const import (
    CONF_CONTROLLERS,
    CONF_DEVICE_MODEL,
    CONF_HOST,
    CONF_IDENTITY_KEY,
    CONF_MAC_ADDRESS,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_PORT,
    CONF_SERIAL_NUMBER,
    CONF_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .network_discovery import (
    ControllerIdentity,
    async_discover,
    async_probe,
    identity_key,
    scan_hosts,
    serial_key,
)


def connection_from_controller(controller: dict) -> ClimatixGenericConnection:
    return ClimatixGenericConnection(
        host=str(controller.get(CONF_HOST) or ""),
        port=int(controller.get(CONF_PORT, DEFAULT_PORT)),
        username=str(controller.get(CONF_USERNAME, DEFAULT_USERNAME)),
        password=str(controller.get(CONF_PASSWORD, DEFAULT_PASSWORD)),
        pin=str(controller.get(CONF_PIN, DEFAULT_PIN)),
    )


def controllers_from_entry(entry) -> list[dict]:
    controllers = entry.data.get(CONF_CONTROLLERS)
    if isinstance(controllers, list):
        return [dict(ctrl) for ctrl in controllers if isinstance(ctrl, dict)]
    return [dict(entry.data)] if entry.data.get(CONF_HOST) else []


async def async_find_controllers(
    hass, conn: ClimatixGenericConnection
) -> list[ControllerIdentity]:
    """Scan enabled LANs; serialize sweeps across configured controllers."""
    data = hass.data.setdefault(DOMAIN, {})
    lock = data.setdefault("network_scan_lock", asyncio.Lock())
    async with lock:
        adapters = await network.async_get_adapters(hass)
        hosts = scan_hosts(adapters, conn.host)
        session = async_get_clientsession(hass)

        async def probe(host):
            return await async_probe(session, replace(conn, host=host))

        return await async_discover(hosts, probe)


async def async_find_cached_controller(
    hass, conn: ClimatixGenericConnection, mac: str | None
) -> list[ControllerIdentity]:
    """Probe only the saved MAC's DHCP address, without enumerating the subnet.

    The MAC locates a candidate; the connection still verifies the saved serial
    before adopting it. No assumptions about vendor prefixes or hostnames apply.
    """
    get_cached = getattr(dhcp, "async_discovered_service_info", None)
    if not mac or get_cached is None:
        return []
    try:
        cached = get_cached(hass)
    except KeyError:  # DHCP may not have finished setting up yet.
        return []
    expected = dr.format_mac(mac)
    hosts = list(
        dict.fromkeys(
            info.ip
            for info in cached
            if dr.format_mac(info.macaddress) == expected and info.ip != conn.host
        )
    )
    session = async_get_clientsession(hass)

    async def probe(host):
        return await async_probe(session, replace(conn, host=host))

    return await async_discover(hosts, probe)


def async_store_controllers(
    hass, entry, controllers: list[dict], *, reload: bool
) -> None:
    data = {**entry.data, CONF_CONTROLLERS: controllers}
    if data == entry.data:
        return
    if not reload and entry.update_listeners:
        pending = hass.data.setdefault(DOMAIN, {}).setdefault("_address_updates", {})
        pending[entry.entry_id] = pending.get(entry.entry_id, 0) + 1
    hass.config_entries.async_update_entry(entry, data=data)


def async_update_address(
    hass, entry, key: str, found: ControllerIdentity, *, reload: bool
) -> bool:
    """Update only the serial-matched controller; never rewrite stored ids."""
    controllers = controllers_from_entry(entry)
    for ctrl in controllers:
        if identity_key(ctrl) != key or ctrl.get(CONF_SERIAL_NUMBER) != found.serial:
            continue
        ctrl[CONF_IDENTITY_KEY] = key
        ctrl[CONF_HOST] = found.host
        if found.mac:
            ctrl[CONF_MAC_ADDRESS] = found.mac
        async_store_controllers(hass, entry, controllers, reload=reload)
        registry = dr.async_get(hass)
        for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
            if any(
                domain == DOMAIN and (ident == key or ident.startswith(f"{key}:hc:"))
                for domain, ident in device.identifiers
            ):
                registry.async_update_device(
                    device.id,
                    configuration_url=f"http://{found.host}:{int(ctrl.get(CONF_PORT, DEFAULT_PORT))}",
                )
        return True
    return False


def async_register_controller(hass, entry, controller: dict, base_url: str) -> dr.DeviceEntry:
    key = identity_key(controller)
    identifiers = {(DOMAIN, key)}
    serial = controller.get(CONF_SERIAL_NUMBER)
    if serial:
        identifiers.add((DOMAIN, serial_key(serial)))
    mac = controller.get(CONF_MAC_ADDRESS)
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=identifiers,
        connections={(dr.CONNECTION_NETWORK_MAC, mac)} if mac else set(),
        manufacturer="Ochsner",
        model=controller.get(CONF_DEVICE_MODEL),
        serial_number=serial,
        configuration_url=base_url,
    )
