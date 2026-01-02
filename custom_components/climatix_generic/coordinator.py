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
            name="climatix_generic",
            update_interval=update_interval,
        )
        self.api = api
        self.ids = ids

    async def _async_update_data(self) -> Dict[str, Any]:
        try:
            return await self.api.read(self.ids)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
