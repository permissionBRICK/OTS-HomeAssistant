from __future__ import annotations

import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
import random
import time
from typing import Any, AsyncIterator, Dict, Iterable, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ClimatixGenericApi, extract_first_value
from .const import (
    POLLING_MODE_AUTOMATIC,
    POLLING_MODE_FAST,
    POLLING_MODE_SLOW,
)


def _values_equal(a: Any, b: Any) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    # Prefer numeric comparison; controllers often return 1.0 for integral values.
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


@dataclass
class _PollState:
    counter: int = 0


class WriteSession:
    """Ids to re-read once when a serialized write session ends."""

    def __init__(self) -> None:
        self.refresh_ids: Dict[str, None] = {}

    def refresh(self, ids: Iterable[str]) -> None:
        for oid in ids:
            if oid:
                self.refresh_ids[str(oid)] = None


class ClimatixCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: ClimatixGenericApi,
        ids: List[str],
        id_modes: Dict[str, str] | None = None,
        poll_threshold: int = 20,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name="ochsner_local_ots",
            update_interval=update_interval,
        )
        # Set during setup before platforms create heating-circuit devices.
        self.device_id: str | None = None
        self.api = api
        self.ids = ids
        self._id_modes: Dict[str, str] = {
            str(k): str(v) for k, v in (id_modes or {}).items() if k
        }
        self._poll_state: Dict[str, _PollState] = {}
        # One write at a time per controller (see async_write_session).
        self.write_lock = asyncio.Lock()
        self._write_session: WriteSession | None = None
        # Freshness per id: every read takes a ticket when it starts, every
        # write attempt bumps the ids it makes stale. A read publishes an id
        # only if nothing newer (a write, or a read that started later) has
        # touched it since its ticket, so a poll that was in flight during a
        # command never restores a pre-command value.
        self._seq: int = 0
        self._id_seq: Dict[str, int] = {}
        # Ids invalidated after a write: polled on the next cycle whatever
        # their polling mode.
        self._force_poll: set[str] = set()

        try:
            pt = int(poll_threshold)
        except Exception:
            pt = 20
        if pt < 10:
            pt = 10
        if pt > 120:
            pt = 120
        self._poll_threshold: int = pt

        # Diagnostics: actual HTTP requests (GET attempts) and requested OA values.
        # (Counts since integration start / entry setup.)
        self._read_http_requests_total: int = 0
        self._read_values_total: int = 0
        # Rolling 5-minute average based on cumulative samples.
        # Stores (monotonic_ts, http_requests_total, values_total)
        self._read_samples: deque[tuple[float, int, int]] = deque()

    def _append_read_sample(self) -> None:
        now = time.monotonic()
        self._read_samples.append(
            (now, int(self._read_http_requests_total), int(self._read_values_total))
        )

        # Prune samples older than ~6 minutes to keep memory bounded.
        cutoff = now - 360.0
        while self._read_samples and self._read_samples[0][0] < cutoff:
            self._read_samples.popleft()

    def _note_http_request(self) -> None:
        self._read_http_requests_total += 1
        self._append_read_sample()

    def _note_read_values(self, requested_values: int) -> None:
        try:
            self._read_values_total += int(requested_values)
        except Exception:
            self._read_values_total += 0
        self._append_read_sample()

    @property
    def read_requests_total(self) -> int:
        return int(self._read_http_requests_total)

    @property
    def read_values_total(self) -> int:
        return int(self._read_values_total)

    def _rate_per_min_over_last(self, seconds: float) -> tuple[float, float]:
        """Return (http_requests_per_min, values_per_min) over the last `seconds` window."""

        if not self._read_samples:
            return (0.0, 0.0)

        now = time.monotonic()
        window_start = now - float(seconds)

        # Find oldest sample within window.
        oldest = None
        for s in self._read_samples:
            if s[0] >= window_start:
                oldest = s
                break

        latest = self._read_samples[-1]
        if oldest is None:
            oldest = self._read_samples[0]

        dt = float(latest[0] - oldest[0])
        if dt <= 1e-6:
            return (0.0, 0.0)

        req_delta = float(latest[1] - oldest[1])
        val_delta = float(latest[2] - oldest[2])
        minutes = dt / 60.0
        return (req_delta / minutes, val_delta / minutes)

    @property
    def read_requests_per_min_5m(self) -> float:
        return float(self._rate_per_min_over_last(300.0)[0])

    @property
    def read_values_per_min_5m(self) -> float:
        return float(self._rate_per_min_over_last(300.0)[1])

    def _ticket(self) -> int:
        self._seq += 1
        return self._seq

    def note_write(self, ids: Iterable[str]) -> None:
        """Mark ids a write (attempt) may change, plus the running write
        session's re-read ids: reads that started earlier must not publish
        them. Called before and after each write attempt."""
        seq = self._ticket()
        session_ids = self._write_session.refresh_ids if self._write_session else {}
        for oid in [*ids, *session_ids]:
            if oid:
                self._id_seq[str(oid)] = seq

    def _publishable(self, oid: str, ticket: int) -> bool:
        return self._id_seq.get(oid, 0) < ticket

    def _merge_read(self, new_values: Dict[str, Any], ticket: int) -> tuple[Dict[str, Any], Dict[str, Any], set]:
        """Merge a read onto the CURRENT data (not a snapshot from before the
        read). Returns (current values, merged values, ids published)."""
        current = self.data if isinstance(self.data, dict) else {}
        current_values = current.get("values") if isinstance(current, dict) else None
        if not isinstance(current_values, dict):
            current_values = {}
        merged: Dict[str, Any] = dict(current_values)
        published = set()
        for k, v in new_values.items():
            k = str(k)
            if not self._publishable(k, ticket):
                continue
            merged[k] = v
            self._id_seq[k] = ticket
            published.add(k)
        return current_values, merged, published

    def _get_mode(self, oid: str) -> str:
        mode = str(
            self._id_modes.get(str(oid), POLLING_MODE_AUTOMATIC)
            or POLLING_MODE_AUTOMATIC
        )
        if mode not in {POLLING_MODE_AUTOMATIC, POLLING_MODE_FAST, POLLING_MODE_SLOW}:
            return POLLING_MODE_AUTOMATIC
        return mode

    def _should_poll(self, oid: str) -> bool:
        if str(oid) in self._force_poll:
            return True
        state = self._poll_state.setdefault(str(oid), _PollState())
        mode = self._get_mode(str(oid))

        if mode == POLLING_MODE_FAST:
            return True

        if mode == POLLING_MODE_SLOW:
            return int(state.counter) >= self._poll_threshold

        # Automatic:
        # - start at 0
        # - poll while counter <= 5
        # - once counter > 5, stop polling but keep incrementing each cycle
        # - when counter reaches threshold, poll again
        if int(state.counter) <= 5:
            return True
        return int(state.counter) >= self._poll_threshold

    async def _async_update_data(self) -> Dict[str, Any]:
        try:
            # First refresh: poll everything to seed values.
            if not isinstance(self.data, dict) or "values" not in self.data:
                if self.ids:
                    self._note_read_values(len(self.ids))
                payload = await self.api.read(
                    self.ids, on_http_request=self._note_http_request
                )
                for oid in self.ids:
                    self._poll_state.setdefault(str(oid), _PollState(counter=0))
                return payload

            current = self.data if isinstance(self.data, dict) else {}

            ids_to_poll: List[str] = []
            skipped: List[str] = []
            for oid in self.ids:
                oid_s = str(oid)
                if self._should_poll(oid_s):
                    ids_to_poll.append(oid_s)
                else:
                    skipped.append(oid_s)

            # If nothing is due for polling, just advance counters and return existing data.
            if not ids_to_poll:
                for oid_s in skipped:
                    state = self._poll_state.setdefault(oid_s, _PollState())
                    state.counter = int(state.counter) + 1
                return current

            ticket = self._ticket()
            self._note_read_values(len(ids_to_poll))
            payload = await self.api.read(
                ids_to_poll, on_http_request=self._note_http_request
            )
            new_values = payload.get("values") if isinstance(payload, dict) else None
            if not isinstance(new_values, dict):
                new_values = {}
            self._force_poll.difference_update(ids_to_poll)

            # Merge the polled subset into the data as it is NOW: commands may
            # have written / re-read values while this read was in flight;
            # their values win (no await between here and publication).
            current_values, merged_values, published = self._merge_read(new_values, ticket)
            stale = {oid for oid in ids_to_poll if oid in new_values and oid not in published}

            out = dict(self.data) if isinstance(self.data, dict) else {}
            out["values"] = merged_values

            # Update counters: increment on skipped cycles; on polled cycles compare old/new.
            for oid_s in skipped:
                state = self._poll_state.setdefault(oid_s, _PollState())
                state.counter = int(state.counter) + 1

            for oid_s in ids_to_poll:
                if oid_s in stale:
                    # A command re-read it meanwhile (and did the bookkeeping).
                    continue
                state = self._poll_state.setdefault(oid_s, _PollState())
                mode = self._get_mode(oid_s)

                if mode == POLLING_MODE_SLOW:
                    # Slow: value-agnostic. Poll only when counter reaches threshold.
                    # After polling, reset to a small random (0..3) to spread load.
                    if int(state.counter) >= self._poll_threshold:
                        state.counter = random.randint(0, 3)
                    else:
                        # If someone forces a poll earlier (shouldn't happen here), keep same semantics.
                        state.counter = int(state.counter) + 1
                    continue

                old_raw = extract_first_value({"values": current_values}, oid_s)
                new_raw = extract_first_value({"values": merged_values}, oid_s)

                if mode == POLLING_MODE_AUTOMATIC and int(state.counter) >= self._poll_threshold:
                    # Automatic: poll again at threshold.
                    # If still unchanged, spread the next polls (7..9). If it changed, reset to 0.
                    if _values_equal(old_raw, new_raw):
                        state.counter = random.randint(7, 9)
                    else:
                        state.counter = 0
                    continue

                # Fast + normal automatic polling path: update counter based on change.
                if _values_equal(old_raw, new_raw):
                    state.counter = int(state.counter) + 1
                else:
                    state.counter = 0

            return out
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

    async def async_refresh_ids(self, ids: List[str]) -> set[str]:
        """Re-read a subset of OA IDs and merge into coordinator data.

        Used after writes to immediately reflect the updated value without
        polling every configured ID. Ids a write touched after this read
        started (or a later read already published) keep their newer value.
        Returns the ids the controller answered.
        """

        ids = [str(x) for x in ids if x]
        if not ids:
            return set()

        ticket = self._ticket()
        self._note_read_values(len(ids))
        payload = await self.api.read(ids, on_http_request=self._note_http_request)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            return set()
        self._force_poll.difference_update(str(k) for k in values)

        current_values, merged_values, published = self._merge_read(values, ticket)
        current = self.data if isinstance(self.data, dict) else {}
        new_data = dict(current)
        new_data["values"] = merged_values
        self.async_set_updated_data(new_data)

        # Treat targeted refreshes as real polls for counter tracking.
        try:
            for oid in ids:
                oid_s = str(oid)
                if not oid_s or oid_s not in published:
                    continue
                state = self._poll_state.setdefault(oid_s, _PollState())
                mode = self._get_mode(oid_s)

                old_raw = extract_first_value({"values": current_values}, oid_s)
                new_raw = extract_first_value({"values": merged_values}, oid_s)

                if mode == POLLING_MODE_SLOW:
                    # After a forced refresh, restart the slow cycle.
                    state.counter = random.randint(0, 3)
                    continue

                if _values_equal(old_raw, new_raw):
                    state.counter = int(state.counter) + 1
                else:
                    state.counter = 0
        except Exception:
            # Never break state updates due to counter bookkeeping.
            pass
        return {str(k) for k in values}

    @asynccontextmanager
    async def async_write_session(self) -> AsyncIterator[WriteSession]:
        """Serialize read -> decide -> write -> re-read cycles per controller.

        Callers read the current values and decide what to write only inside
        the session, so an overlapping command sees the result (writes and
        re-reads) of the one before it. Ids marked with ``session.refresh``
        are re-read once when the session ends, also after a failed write.
        """
        async with self.write_lock:
            session = WriteSession()
            self._write_session = session
            try:
                yield session
            finally:
                self._write_session = None
                if session.refresh_ids:
                    await self._async_refresh_after_write(list(session.refresh_ids))

    def queue_refresh_after_write(self, ids: Iterable[str]) -> None:
        """Re-read ids when the running write session ends (no session: no-op;
        the caller re-reads with async_refresh_after_write)."""
        if self._write_session is not None:
            self._write_session.refresh(ids)

    def async_invalidate(self, ids: Iterable[str]) -> None:
        """Forget values that may be wrong (unknown until read again).

        After a write whose re-read failed or left ids out: the controller
        may have changed them, and a stale target or correction would make
        the next relative change wrong. Polled on the next cycle.
        """
        ids = {str(i) for i in ids if i}
        if not ids:
            return
        polled = set(self.ids)
        self._force_poll.update(i for i in ids if i in polled)
        data = self.data if isinstance(self.data, dict) else {}
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        if not ids & set(values):
            return
        new_data = dict(data)
        new_data["values"] = {k: v for k, v in values.items() if k not in ids}
        self.async_set_updated_data(new_data)

    async def async_refresh_after_write(self, ids: Iterable[str]) -> None:
        """Re-read ids a write made stale: at the end of the running write
        session (one read for the whole command), else right away."""
        ids = [str(i) for i in ids if i]
        if not ids:
            return
        if self._write_session is not None:
            self._write_session.refresh(ids)
            return
        await self._async_refresh_after_write(ids)

    async def _async_refresh_after_write(self, ids: List[str]) -> None:
        try:
            answered = await self.async_refresh_ids(ids)
        except Exception:
            # Unknown beats stale; then poll what is due (incl. these ids).
            self.async_invalidate(ids)
            await self.async_request_refresh()
            return
        self.async_invalidate([oid for oid in ids if oid not in answered])
