from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import random
from typing import Any, Dict, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ClimatixGenericApi
from .api import extract_first_value
from .const import (
    CONF_POLLING_MODE,
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

    def _get_mode(self, oid: str) -> str:
        mode = str(self._id_modes.get(str(oid), POLLING_MODE_AUTOMATIC) or POLLING_MODE_AUTOMATIC)
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
                payload = await self.api.read(self.ids)
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

            payload = await self.api.read(ids_to_poll)
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
                    # Automatic: poll again at 20.
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

        payload = await self.api.read(ids)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            return

        current = self.data if isinstance(self.data, dict) else {}
        current_values = current.get("values") if isinstance(current, dict) else None
        merged_values: Dict[str, Any] = dict(current_values) if isinstance(current_values, dict) else {}
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
