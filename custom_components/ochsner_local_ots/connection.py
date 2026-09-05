"""Serial-verified connection recovery without changing entity identity."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from .api import ClimatixGenericApi, ClimatixGenericConnection
from .catalog import PLANT_MODEL_OA, PLANT_SERIAL_OA
from .network_discovery import ControllerIdentity, parse_identity

RECOVERY_INTERVAL = 300.0


class ControllerUnavailable(RuntimeError):
    """The saved controller cannot be identified at any candidate address."""


class SerialVerifiedApi:
    """Verify before reads/writes, recover reads, never replay a failed write.

    A lock prevents a reconnect from changing a concurrent write's destination.
    Recovery candidates must have the expected serial, and are rechecked before
    committing the address. Failed scans are rate-limited across polling cycles.
    """

    def __init__(
        self,
        conn: ClimatixGenericConnection,
        serial: str,
        factory: Callable[[ClimatixGenericConnection], ClimatixGenericApi],
        discover: Callable[
            [ClimatixGenericConnection], Awaitable[list[ControllerIdentity]]
        ],
        on_address: Callable[[ControllerIdentity], Awaitable[None]],
        recovery_state: dict | None = None,
    ) -> None:
        self.conn = conn
        self.serial = serial
        self._factory = factory
        self._api = factory(conn)
        self._discover = discover
        self._on_address = on_address
        self._recovery_state = recovery_state if recovery_state is not None else {}
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return self.conn.base_url

    async def _verify(self, api, host, on_http_request=None) -> ControllerIdentity:
        payload = await api.read_raw(
            [PLANT_SERIAL_OA, PLANT_MODEL_OA], on_http_request=on_http_request
        )
        found = parse_identity(host, payload)
        if found is None or found.serial != self.serial:
            raise ControllerUnavailable("Controller serial number does not match")
        return found

    async def _recover(self) -> None:
        if time.monotonic() < self._recovery_state.get("next_scan", 0.0):
            raise ControllerUnavailable(
                "Controller unavailable; waiting before another subnet scan"
            )
        self._recovery_state["next_scan"] = time.monotonic() + RECOVERY_INTERVAL
        try:
            candidates = await self._discover(self.conn)
        finally:
            # Leave a quiet interval even when a large subnet scan takes longer
            # than RECOVERY_INTERVAL, or setup is cancelled partway through.
            self._recovery_state["next_scan"] = time.monotonic() + RECOVERY_INTERVAL
        matches = [item for item in candidates if item.serial == self.serial]
        by_host = {item.host: item for item in matches}
        if len(by_host) != 1:
            raise ControllerUnavailable(
                "No unambiguous controller with the saved serial number found"
            )
        found = next(iter(by_host.values()))
        conn = replace(self.conn, host=found.host)
        api = self._factory(conn)
        await self._verify(api, conn.host)
        await self._on_address(found)
        self.conn, self._api = conn, api

    async def _ensure_identity(self, on_http_request=None) -> None:
        try:
            await self._verify(self._api, self.conn.host, on_http_request)
        except Exception:  # noqa: BLE001 -- protocol/auth/transport failures all need verification elsewhere
            await self._recover()

    async def _read(self, method, ids, on_http_request=None):
        async with self._lock:
            await self._ensure_identity(on_http_request)
            try:
                return await getattr(self._api, method)(
                    ids, on_http_request=on_http_request
                )
            except Exception:  # noqa: BLE001 -- retry reads only, after verifying the recovered identity
                await self._recover()
                return await getattr(self._api, method)(
                    ids, on_http_request=on_http_request
                )

    async def read(self, ids, *, on_http_request=None):
        return await self._read("read", list(ids), on_http_request)

    async def read_raw(self, ids, *, on_http_request=None):
        return await self._read("read_raw", list(ids), on_http_request)

    async def write(self, generic_id, value):
        async with self._lock:
            await self._ensure_identity()
            return await self._api.write(generic_id, value)
