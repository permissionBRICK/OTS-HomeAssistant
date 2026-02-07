from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import timedelta
import random
import time
from typing import Any, Dict, List

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
        self.api = api
        self.ids = ids
        self._id_modes: Dict[str, str] = {
            str(k): str(v) for k, v in (id_modes or {}).items() if k
        }
        self._poll_state: Dict[str, _PollState] = {}

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

    def _get_mode(self, oid: str) -> str:
        mode = str(
            self._id_modes.get(str(oid), POLLING_MODE_AUTOMATIC)
            or POLLING_MODE_AUTOMATIC
        )
        if mode not in {POLLING_MODE_AUTOMATIC, POLLING_MODE_FAST, POLLING_MODE_SLOW}:
            return POLLING_MODE_AUTOMATIC
        return mode

    def _should_poll(self, oid: str) -> bool:
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
            current_values = current.get("values") if isinstance(current, dict) else None
            if not isinstance(current_values, dict):
                current_values = {}

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

            self._note_read_values(len(ids_to_poll))
            payload = await self.api.read(
                ids_to_poll, on_http_request=self._note_http_request
            )
            new_values = payload.get("values") if isinstance(payload, dict) else None
            if not isinstance(new_values, dict):
                new_values = {}

            # Merge polled subset into existing values.
            merged_values: Dict[str, Any] = dict(current_values)
            for k, v in new_values.items():
                merged_values[str(k)] = v

            out = dict(current)
            out["values"] = merged_values

            # Update counters: increment on skipped cycles; on polled cycles compare old/new.
            for oid_s in skipped:
                state = self._poll_state.setdefault(oid_s, _PollState())
                state.counter = int(state.counter) + 1

            for oid_s in ids_to_poll:
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

    async def async_refresh_ids(self, ids: List[str]) -> None:
        """Re-read a subset of OA IDs and merge into coordinator data.

        Used after writes to immediately reflect the updated value without
        polling every configured ID.
        """

        ids = [str(x) for x in ids if x]
        if not ids:
            return

        self._note_read_values(len(ids))
        payload = await self.api.read(ids, on_http_request=self._note_http_request)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            return

        current = self.data if isinstance(self.data, dict) else {}
        current_values = current.get("values") if isinstance(current, dict) else None
        merged_values: Dict[str, Any] = (
            dict(current_values) if isinstance(current_values, dict) else {}
        )
        for k, v in values.items():
            merged_values[str(k)] = v

        new_data = dict(current)
        new_data["values"] = merged_values
        self.async_set_updated_data(new_data)

        # Treat targeted refreshes as real polls for counter tracking.
        try:
            for oid in ids:
                oid_s = str(oid)
                if not oid_s:
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
