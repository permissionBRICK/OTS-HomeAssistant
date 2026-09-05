"""Read-only controller identification and bounded IPv4 subnet discovery.

No Home Assistant imports: the scanner can also be exercised against hardware.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from itertools import islice

from aiohttp import ClientSession

from .api import ClimatixGenericApi, ClimatixGenericConnection, extract_first_value
from .catalog import PLANT_MODEL_OA, PLANT_SERIAL_OA
from .const import CONF_HOST, CONF_IDENTITY_KEY

PLANT_MAC_OA = "IgABAAAAAAA="
PROBE_TIMEOUT = 2.0
SCAN_CONCURRENCY = 8
MAX_SCAN_HOSTS = 4096


@dataclass(frozen=True)
class ControllerIdentity:
    host: str
    serial: str
    model: str
    mac: str | None = None


def serial_key(serial: str) -> str:
    return f"serial:{serial}"


def identity_key(controller: dict) -> str:
    """Legacy ids retain their original address as an immutable namespace."""
    return str(controller.get(CONF_IDENTITY_KEY) or controller.get(CONF_HOST) or "")


def _identity_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or len(value) > 128
        or value.lower() in {"0", "unknown", "none", "null", "n/a", "-"}
    ):
        return None
    if not any(c.isalnum() for c in value) or any(ord(c) < 32 for c in value):
        return None
    return value


def parse_identity(host: str, payload: dict) -> ControllerIdentity | None:
    # These are Ochsner application-specific addresses, not generic web-server
    # banners. A bare status/error code never proves device identity.
    serial = _identity_text(extract_first_value(payload, PLANT_SERIAL_OA))
    model = _identity_text(extract_first_value(payload, PLANT_MODEL_OA))
    if not serial or not model:
        return None
    raw_mac = extract_first_value(payload, PLANT_MAC_OA)
    mac = None
    if isinstance(raw_mac, str):
        compact = raw_mac.replace("-", "").replace(":", "").lower()
        if (
            len(compact) == 12
            and all(c in "0123456789abcdef" for c in compact)
            and compact != "0" * 12
        ):
            mac = ":".join(compact[i : i + 2] for i in range(0, 12, 2))
    return ControllerIdentity(host, serial, model, mac)


async def async_probe(
    session: ClientSession, conn: ClimatixGenericConnection
) -> ControllerIdentity | None:
    """One small identity read; no catalog sweep and no writes."""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            api = ClimatixGenericApi(session, replace(conn, timeout_sec=PROBE_TIMEOUT))
            payload = await api.read_raw(
                [PLANT_SERIAL_OA, PLANT_MODEL_OA, PLANT_MAC_OA]
            )
            return parse_identity(conn.host, payload)
    except (TimeoutError, OSError, ValueError, RuntimeError):
        return None
    except Exception:  # noqa: BLE001 -- ignore non-controller HTTP/auth/protocol responses without logging PIN-bearing URLs
        return None


def scan_hosts(adapters: Iterable[dict], last_host: str | None = None) -> list[str]:
    """Enabled private IPv4 subnets, bounded even on a very large LAN.

    An off-interface saved private address (e.g. HA behind container NAT) gets
    a /24 fallback. Prefer the saved address's subnet when the budget is tight.
    """
    networks: list[IPv4Network] = []
    local_addresses: set[IPv4Address] = set()
    for adapter in adapters:
        if not adapter.get("enabled"):
            continue
        for info in adapter.get("ipv4", []):
            try:
                address = IPv4Address(info["address"])
                net = ip_network(f"{address}/{info['network_prefix']}", strict=False)
            except (ValueError, KeyError):
                continue
            local_addresses.add(address)
            if _private_lan(address) and net.prefixlen >= 20:
                networks.append(net)
    try:
        previous = ip_address(last_host or "")
    except ValueError:
        previous = None
    if isinstance(previous, IPv4Address) and _private_lan(previous):
        if not any(previous in net for net in networks):
            networks.insert(0, ip_network(f"{previous}/24", strict=False))
        networks.sort(key=lambda net: previous not in net)
    hosts: dict[str, None] = {}
    for net in networks:
        for host in net.hosts():
            if host in local_addresses:
                continue
            hosts[str(host)] = None
            if len(hosts) >= MAX_SCAN_HOSTS:
                return list(hosts)
    return list(hosts)


def _private_lan(address: IPv4Address) -> bool:
    return any(address in net for net in _PRIVATE_NETWORKS)


_PRIVATE_NETWORKS = tuple(
    ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


async def async_discover(
    hosts: Iterable[str],
    probe: Callable[[str], Awaitable[ControllerIdentity | None]],
) -> list[ControllerIdentity]:
    """Fixed worker pool, bounded candidates, cancellation propagates cleanly."""
    candidates = iter(dict.fromkeys(islice(hosts, MAX_SCAN_HOSTS)))
    results: list[ControllerIdentity] = []
    count = 0

    async def worker() -> None:
        nonlocal count
        while count < MAX_SCAN_HOSTS:
            host = next(candidates, None)
            if host is None:
                return
            count += 1
            found = await probe(host)
            if found is not None:
                results.append(found)

    async with asyncio.TaskGroup() as group:
        for _ in range(SCAN_CONCURRENCY):
            group.create_task(worker())
    return sorted(results, key=lambda item: item.host)
