from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ClimatixGenericApi


class ClimatixCoordinator(DataUpdateCoordinator[Dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api: ClimatixGenericApi,
        ids: List[str],
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

    async def _async_update_data(self) -> Dict[str, Any]:
        try:
            return await self.api.read(self.ids)
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
