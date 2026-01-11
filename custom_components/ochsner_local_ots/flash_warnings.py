from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN


_LOGGER = logging.getLogger(__name__)

_STORE_VERSION = 1
_STORE_KEY = f"{DOMAIN}_flash_warnings"


_THRESHOLDS = (
    (2_000, "2k"),
    (10_000, "10k"),
    (50_000, "50k"),
)


def _notif_id(host: str, key: str) -> str:
    safe_host = (host or "unknown").replace(" ", "_").replace(":", "_")
    return f"{DOMAIN}.flash_writes.{safe_host}.{key}"[:255]


def _message(*, count: int) -> str:
    return (
        f"The heat pump controller has performed {count} flash writes since the integration was added. "
        "Siemens Climatix controllers are commonly rated for about 100,000 flash write cycles. "
        "Excessive writes (e.g., frequent automations writing setpoints/settings) can wear out flash early. "
        "Consider reducing write frequency, adding hysteresis/debouncing, or limiting writes to necessary changes."
    )


def _data_key() -> str:
    return "_flash_warning_state"


def _unsub_key() -> str:
    return "_flash_warning_unsub"


async def _async_load_state(hass: HomeAssistant) -> Dict[str, Any]:
    existing = (hass.data.setdefault(DOMAIN, {}) or {}).get(_data_key())
    if isinstance(existing, dict) and "dismissed" in existing:
        return existing

    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    raw = await store.async_load() or {}

    # Migrate legacy format (nested dict keyed by entry_id/host/threshold flag) to new:
    # {"dismissed": {notification_id: true}}
    if isinstance(raw, dict) and "dismissed" in raw and isinstance(raw.get("dismissed"), dict):
        state: Dict[str, Any] = {"dismissed": dict(raw.get("dismissed") or {})}
    else:
        state = {"dismissed": {}}

    hass.data.setdefault(DOMAIN, {})[_data_key()] = state
    return state


async def _async_save_state(hass: HomeAssistant, state: Dict[str, Any]) -> None:
    store = Store(hass, _STORE_VERSION, _STORE_KEY)
    await store.async_save(state)


async def async_setup_flash_warning_listener(hass: HomeAssistant) -> None:
    """Listen for persistent_notification dismiss calls to persist user dismissal."""

    domain_state = hass.data.setdefault(DOMAIN, {})
    if domain_state.get(_unsub_key()) is not None:
        return

    async def _on_call_service(event) -> None:
        try:
            data = event.data or {}
            if data.get("domain") != "persistent_notification":
                return
            if data.get("service") != "dismiss":
                return
            svc_data = data.get("service_data") or {}
            if not isinstance(svc_data, dict):
                return
            nid = svc_data.get("notification_id")
            if not isinstance(nid, str) or not nid:
                return
            # Only handle our notifications.
            if not nid.startswith(f"{DOMAIN}.flash_writes."):
                return
        except Exception:
            return

        try:
            state = await _async_load_state(hass)
            dismissed = state.setdefault("dismissed", {})
            if not isinstance(dismissed, dict):
                state["dismissed"] = {}
                dismissed = state["dismissed"]
            if dismissed.get(nid) is True:
                return
            dismissed[nid] = True
            await _async_save_state(hass, state)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to persist notification dismissal: %s", err)

    domain_state[_unsub_key()] = hass.bus.async_listen("call_service", _on_call_service)


async def async_maybe_create_flash_wear_notifications(
    hass: HomeAssistant,
    *,
    entry_id: str,
    host: str,
    count: int,
) -> None:
    """Create persistent warnings at thresholds until dismissed.

    Home Assistant's persistent notifications may not survive restarts in some setups.
    This re-creates them on startup/writes as long as they haven't been dismissed.
    Dismissal is detected by listening for `persistent_notification.dismiss`.
    """

    try:
        n = int(count)
    except Exception:
        return

    await async_setup_flash_warning_listener(hass)

    state = await _async_load_state(hass)
    dismissed = state.get("dismissed")
    if not isinstance(dismissed, dict):
        dismissed = {}
        state["dismissed"] = dismissed

    host_key = str(host or "")

    for threshold, key in _THRESHOLDS:
        if n < threshold:
            continue

        nid = _notif_id(host_key, key)
        if dismissed.get(nid) is True:
            continue

        try:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "Ochsner heat pump flash wear warning",
                    "message": _message(count=n),
                    "notification_id": nid,
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Failed to create persistent notification: %s", err)
            continue

