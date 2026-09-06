"""Address recovery must never substitute a different heat pump."""

import asyncio
import sys
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def net(lib):
    return sys.modules["ots_local_lib.network_discovery"]


@pytest.fixture
def connection(lib):
    return sys.modules["ots_local_lib.connection"]


def test_identity_requires_values_for_both_ochsner_registers(net):
    ids = [net.PLANT_SERIAL_OA, net.PLANT_MODEL_OA]
    for payload in (
        {},
        {"states": dict.fromkeys(ids, 5)},
        {"values": {ids[0]: "123"}},
        {"values": {ids[0]: "unknown", ids[1]: "Model"}},
    ):
        assert net.parse_identity("192.168.1.2", payload) is None
    found = net.parse_identity(
        "192.168.1.2",
        {
            "values": {
                ids[0]: ["123", "123"],
                ids[1]: ["AIRHAWK", "AIRHAWK"],
                net.PLANT_MAC_OA: "00-A0-03-12-34-56",
            }
        },
    )
    assert found.serial == "123"
    assert found.mac == "00:a0:03:12:34:56"


def test_scans_selected_private_subnets_and_saved_nat_subnet(net):
    adapters = [
        {"enabled": True, "ipv4": [{"address": "192.168.1.1", "network_prefix": 30}]},
        {"enabled": False, "ipv4": [{"address": "10.1.1.1", "network_prefix": 24}]},
        {"enabled": True, "ipv4": [{"address": "8.8.8.1", "network_prefix": 24}]},
        {"enabled": True, "ipv4": [{"address": "10.0.0.1", "network_prefix": 8}]},
    ]
    assert net.scan_hosts(adapters) == ["192.168.1.2"]
    hosts = net.scan_hosts(adapters, "192.168.178.80")
    assert hosts[0] == "192.168.178.1"
    assert "192.168.178.80" in hosts
    assert hosts[-1] == "192.168.1.2"
    assert net.scan_hosts([], "example.com") == []
    assert net.scan_hosts([], "8.8.8.8") == []


def test_scan_deduplicates_and_limits_concurrency(net, monkeypatch):
    async def scenario():
        active = maximum = 0
        calls = []

        async def probe(host):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            calls.append(host)
            await asyncio.sleep(0)
            active -= 1
            return net.ControllerIdentity(host, host, "Model")

        monkeypatch.setattr(net, "MAX_SCAN_HOSTS", 20)
        found = await net.async_discover([str(i) for i in range(50)] * 2, probe)
        assert len(found) == len(calls) == len(set(calls)) == 20
        assert maximum <= net.SCAN_CONCURRENCY

    asyncio.run(scenario())


def test_scan_cancellation_stops_workers(net):
    async def scenario():
        active = 0
        started = asyncio.Event()

        async def probe(_):
            nonlocal active
            active += 1
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        task = asyncio.create_task(net.async_discover(map(str, range(100)), probe))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert active == 0

    asyncio.run(scenario())


class FakeApi:
    def __init__(self, host, devices, catalog_mod, calls):
        self.host, self.devices, self.catalog, self.calls = (
            host,
            devices,
            catalog_mod,
            calls,
        )

    async def read_raw(self, ids, **kwargs):
        self.calls.append((self.host, "identity"))
        serial = self.devices.get(self.host)
        if serial is None:
            raise TimeoutError
        return {
            "values": {
                self.catalog.PLANT_SERIAL_OA: [serial, serial],
                self.catalog.PLANT_MODEL_OA: "AIRHAWK",
            }
        }

    async def read(self, ids, **kwargs):
        self.calls.append((self.host, "read"))
        return {"values": {oid: 22.5 for oid in ids}}

    async def write(self, oid, value):
        self.calls.append((self.host, "write", oid, value))
        if self.devices.get("write_error"):
            raise TimeoutError
        return {"values": {oid: value}}


def make_connection(
    connection, net, catalog_mod, devices, results, recovery_state=None
):
    calls = []
    discover = AsyncMock(return_value=results)
    saved = AsyncMock()
    conn = connection.ClimatixGenericConnection(
        "192.168.1.2", 80, "user", "pass", "pin"
    )
    factory = lambda c: FakeApi(c.host, devices, catalog_mod, calls)
    api = connection.SerialVerifiedApi(
        conn, "123", factory, discover, saved, recovery_state
    )
    return api, calls, discover, saved


def test_recovery_rejects_old_ip_reused_by_other_pump(connection, net, catalog_mod):
    async def scenario():
        found = net.ControllerIdentity("192.168.1.3", "123", "AIRHAWK")
        api, calls, discover, saved = make_connection(
            connection,
            net,
            catalog_mod,
            {"192.168.1.2": "OTHER", "192.168.1.3": "123"},
            [found],
        )
        assert await api.read(["temperature"]) == {"values": {"temperature": 22.5}}
        assert api.conn.host == found.host
        saved.assert_awaited_once_with(found)
        assert ("192.168.1.2", "read") not in calls
        await api.write("setpoint", 21)
        assert calls[-2:] == [
            (found.host, "identity"),
            (found.host, "write", "setpoint", 21),
        ]
        discover.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "scenario", ["missing", "other_serial", "ambiguous", "changed_after_scan"]
)
def test_unverified_recovery_never_writes(connection, net, catalog_mod, scenario):
    async def run():
        devices = {"192.168.1.3": "123", "192.168.1.4": "123"}
        found = net.ControllerIdentity("192.168.1.3", "123", "AIRHAWK")
        results = [found]
        if scenario == "missing":
            results = []
        elif scenario == "other_serial":
            results = [net.ControllerIdentity(found.host, "OTHER", found.model)]
        elif scenario == "ambiguous":
            results.append(net.ControllerIdentity("192.168.1.4", "123", found.model))
        else:
            devices[found.host] = "OTHER"
        api, calls, _discover, saved = make_connection(
            connection, net, catalog_mod, devices, results
        )
        with pytest.raises(connection.ControllerUnavailable):
            await api.write("setpoint", 21)
        assert not any(call[1] == "write" for call in calls)
        saved.assert_not_awaited()

    asyncio.run(run())


def test_failed_scans_throttled_across_setup_retries(connection, net, catalog_mod):
    async def scenario():
        state = {}
        api, _, discover, _ = make_connection(
            connection, net, catalog_mod, {}, [], state
        )
        for _ in range(2):
            with pytest.raises(connection.ControllerUnavailable):
                await api.read(["x"])
        discover.assert_awaited_once()
        recreated, _, rediscover, _ = make_connection(
            connection, net, catalog_mod, {}, [], state
        )
        with pytest.raises(connection.ControllerUnavailable):
            await recreated.read(["x"])
        rediscover.assert_not_awaited()

    asyncio.run(scenario())


def test_failed_write_is_not_replayed(connection, net, catalog_mod):
    async def scenario():
        api, calls, discover, _ = make_connection(
            connection,
            net,
            catalog_mod,
            {"192.168.1.2": "123", "write_error": True},
            [],
        )
        with pytest.raises(TimeoutError):
            await api.write("x", 1)
        assert len([call for call in calls if call[1] == "write"]) == 1
        discover.assert_not_awaited()

    asyncio.run(scenario())


def test_quick_recovery_works_during_full_scan_cooldown(connection, net, catalog_mod):
    async def scenario():
        found = net.ControllerIdentity("192.168.1.3", "123", "AIRHAWK")
        api, _, full_scan, saved = make_connection(
            connection,
            net,
            catalog_mod,
            {found.host: "123"},
            [],
            {"next_scan": float("inf")},
        )
        quick = AsyncMock(return_value=[found])
        api._quick_discover = quick
        assert await api.read(["x"]) == {"values": {"x": 22.5}}
        quick.assert_awaited_once()
        full_scan.assert_not_awaited()
        saved.assert_awaited_once_with(found)

    asyncio.run(scenario())


@pytest.mark.parametrize("quick_result", ["missing", "wrong_serial", "stale"])
def test_full_scan_only_after_quick_lookup_fails(
    connection, net, catalog_mod, quick_result
):
    async def scenario():
        found = net.ControllerIdentity("192.168.1.3", "123", "AIRHAWK")
        api, _, full_scan, saved = make_connection(
            connection, net, catalog_mod, {found.host: "123"}, [found]
        )
        order = []

        async def quick(_):
            order.append("quick")
            if quick_result == "missing":
                return []
            return [
                net.ControllerIdentity(
                    "192.168.1.4",
                    "OTHER" if quick_result == "wrong_serial" else "123",
                    "AIRHAWK",
                )
            ]

        async def full(_):
            order.append("full")
            return [found]

        api._quick_discover = quick
        full_scan.side_effect = full
        await api.read(["x"])
        assert order == ["quick", "full"]
        saved.assert_awaited_once_with(found)

    asyncio.run(scenario())


def test_healthy_controller_and_datapoint_error_do_not_discover(
    connection, net, catalog_mod
):
    async def scenario():
        api, _, full_scan, saved = make_connection(
            connection, net, catalog_mod, {"192.168.1.2": "123"}, []
        )
        quick = AsyncMock()
        api._quick_discover = quick
        await api.read(["x"])
        api._api.read = AsyncMock(side_effect=ValueError("Invalid datapoint"))
        with pytest.raises(ValueError, match="Invalid datapoint"):
            await api.read(["bad_id"])
        quick.assert_not_awaited()
        full_scan.assert_not_awaited()
        saved.assert_not_awaited()

    asyncio.run(scenario())
