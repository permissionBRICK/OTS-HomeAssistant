from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import ClimatixGenericApi, ClimatixGenericConnection
from .bundle_generator import generate_entities_from_bundle
from .const import (
    CONF_BINARY_SENSORS,
    CONF_BUNDLE_STORAGE_KEY,
    CONF_CONFIG_ID,
    CONF_CONTROLLERS,
    CONF_HOST,
    CONF_LANGUAGE,
    CONF_NUMBERS,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SELECTS,
    CONF_SENSORS,
    CONF_SITE_ID,
    CONF_TEXTS,
    CONF_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .ots_client import decode_ots_bundle_content, ots_getconfig, ots_login


_LOGGER = logging.getLogger(__name__)

_BUNDLE_STORE_VERSION = 1
_BUNDLE_STORE_KEY = f"{DOMAIN}_bundles"


def merge_missing_entities(
    existing: List[Dict[str, Any]],
    discovered: List[Dict[str, Any]],
    *,
    kind: str,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return (merged_list, added_count) without removing anything."""

    def _key(d: Dict[str, Any]) -> tuple[str, str]:
        u = str(d.get("uuid") or "").strip()
        if u:
            return ("uuid", u)
        if kind in {"sensor", "binary_sensor"}:
            return ("id", str(d.get("id") or "").strip())
        return ("read_id", str(d.get("read_id") or d.get("id") or "").strip())

    seen = set()
    out: List[Dict[str, Any]] = []
    for x in existing:
        out.append(x)
        k = _key(x)
        if k[1]:
            seen.add(k)

    added = 0
    for x in discovered:
        k = _key(x)
        if not k[1]:
            continue
        if k in seen:
            continue
        out.append(x)
        seen.add(k)
        added += 1

    return out, added


async def async_rescan_from_bundle(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    controllers: List[Dict[str, Any]],
    bundles_by_key: Dict[str, Any],
) -> tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Probe controllers against provided bundles and merge missing entities."""

    session = async_get_clientsession(hass)

    added_by_platform: Dict[str, int] = {"sensors": 0, "binary_sensors": 0, "numbers": 0, "selects": 0, "texts": 0}
    updated_controllers: List[Dict[str, Any]] = []

    for ctrl in controllers:
        ctrl_d = dict(ctrl)
        bundle_key = ctrl_d.get(CONF_BUNDLE_STORAGE_KEY)
        bundle = bundles_by_key.get(bundle_key) if isinstance(bundle_key, str) and bundle_key else None
        if not isinstance(bundle, dict):
            updated_controllers.append(ctrl_d)
            continue

        host: str = str(ctrl_d.get(CONF_HOST) or "").strip()
        if not host:
            updated_controllers.append(ctrl_d)
            continue

        port: int = int(ctrl_d.get(CONF_PORT, DEFAULT_PORT))
        username: str = str(ctrl_d.get(CONF_USERNAME, DEFAULT_USERNAME))
        password: str = str(ctrl_d.get(CONF_PASSWORD, DEFAULT_PASSWORD))
        pin: str = str(ctrl_d.get(CONF_PIN, DEFAULT_PIN))
        language = ctrl_d.get(CONF_LANGUAGE)

        api = ClimatixGenericApi(
            session,
            ClimatixGenericConnection(
                host=host,
                port=port,
                username=username,
                password=password,
                pin=pin,
            ),
        )

        try:
            discovered = await generate_entities_from_bundle(bundle=bundle, api=api, language=language, probe=True)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Probe failed for %s:%s (%s): %s", host, port, bundle_key, err)
            updated_controllers.append(ctrl_d)
            continue

        for key, kind in (
            (CONF_SENSORS, "sensor"),
            (CONF_BINARY_SENSORS, "binary_sensor"),
            (CONF_NUMBERS, "number"),
            (CONF_SELECTS, "select"),
            (CONF_TEXTS, "text"),
        ):
            existing_list = list(ctrl_d.get(key, []) or [])
            new_list = list(discovered.get(key, []) or [])
            merged, added = merge_missing_entities(existing_list, new_list, kind=kind)
            ctrl_d[key] = merged

            if key == CONF_SENSORS:
                added_by_platform["sensors"] += added
            elif key == CONF_BINARY_SENSORS:
                added_by_platform["binary_sensors"] += added
            elif key == CONF_NUMBERS:
                added_by_platform["numbers"] += added
            elif key == CONF_SELECTS:
                added_by_platform["selects"] += added
            elif key == CONF_TEXTS:
                added_by_platform["texts"] += added

        updated_controllers.append(ctrl_d)

    return updated_controllers, added_by_platform


async def async_redownload_bundles_and_merge(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    ots_username: str,
    ots_password: str,
) -> tuple[bool, str, Dict[str, int]]:
    """Re-download OTS bundles, rescan, merge missing entities, and persist.

    Transactional rules:
    - If download fails for any controller -> no changes.
    - If scan discovers 0 entities total across all controllers -> no changes.
    - Never remove existing entities.

    Returns (ok, error_key, added_by_platform).
    """

    data = dict(entry.data)
    controllers_raw = data.get(CONF_CONTROLLERS)
    if not isinstance(controllers_raw, list) or not controllers_raw:
        return False, "no_controllers", {}

    controllers = [dict(c) for c in controllers_raw if isinstance(c, dict)]
    if not controllers:
        return False, "no_controllers", {}

    store = Store(hass, _BUNDLE_STORE_VERSION, _BUNDLE_STORE_KEY)
    stored = await store.async_load() or {}
    if not isinstance(stored, dict):
        stored = {}

    # Login first to establish session cookies.
    try:
        await ots_login(hass, username=ots_username, password=ots_password)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("OTS login failed during re-download: %s", err)
        return False, "auth", {}

    new_bundles: Dict[str, Any] = {}

    # Download bundles for all controllers first.
    for ctrl in controllers:
        bundle_key = ctrl.get(CONF_BUNDLE_STORAGE_KEY)
        cfg_id = ctrl.get(CONF_CONFIG_ID)
        site_id = ctrl.get(CONF_SITE_ID)
        if not isinstance(bundle_key, str) or not bundle_key:
            return False, "no_bundle_key", {}
        if not isinstance(cfg_id, str) or not cfg_id.strip() or not isinstance(site_id, str) or not site_id.strip():
            return False, "no_ots_ids", {}

        try:
            cfg = await ots_getconfig(hass, config_id=cfg_id, site_id=site_id, stamp=0)
            if not cfg.get("success"):
                return False, "download_failed", {}
            content = cfg.get("content")
            if not isinstance(content, str) or not content:
                return False, "download_failed", {}
            bundle = decode_ots_bundle_content(content)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("OTS bundle download failed for %s/%s: %s", cfg_id, site_id, err)
            return False, "download_failed", {}

        new_bundles[bundle_key] = bundle

    # Probe and ensure at least one entity is discovered across all controllers.
    updated_controllers, added_by_platform = await async_rescan_from_bundle(
        hass,
        entry,
        controllers=controllers,
        bundles_by_key=new_bundles,
    )

    # Ensure the *new bundle(s)* aren't unusable: they must generate at least one entity.
    # This is independent of whether the entities already existed.
    try:
        session = async_get_clientsession(hass)
        total_generated = 0
        for ctrl in controllers:
            bundle_key = str(ctrl.get(CONF_BUNDLE_STORAGE_KEY) or "")
            bundle = new_bundles.get(bundle_key)
            if not isinstance(bundle, dict):
                continue
            host: str = str(ctrl.get(CONF_HOST) or "").strip()
            if not host:
                continue
            port: int = int(ctrl.get(CONF_PORT, DEFAULT_PORT))
            username: str = str(ctrl.get(CONF_USERNAME, DEFAULT_USERNAME))
            password: str = str(ctrl.get(CONF_PASSWORD, DEFAULT_PASSWORD))
            pin: str = str(ctrl.get(CONF_PIN, DEFAULT_PIN))
            language = ctrl.get(CONF_LANGUAGE)

            api = ClimatixGenericApi(
                session,
                ClimatixGenericConnection(
                    host=host,
                    port=port,
                    username=username,
                    password=password,
                    pin=pin,
                ),
            )
            discovered = await generate_entities_from_bundle(bundle=bundle, api=api, language=language, probe=True)
            for k in ("sensors", "binary_sensors", "numbers", "selects", "texts"):
                total_generated += len(list(discovered.get(k, []) or []))
        if total_generated == 0:
            return False, "no_entities", {}
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Sanity probe for new bundle failed: %s", err)
        return False, "no_entities", {}

    # Persist bundles + updated controllers.
    stored_updated = dict(stored)
    for k, v in new_bundles.items():
        stored_updated[k] = v
    await store.async_save(stored_updated)

    hass.config_entries.async_update_entry(entry, data={CONF_CONTROLLERS: updated_controllers})

    return True, "", added_by_platform
