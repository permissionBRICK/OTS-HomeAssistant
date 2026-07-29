"""Home Assistant glue for the accountless local discovery scan.

Used by the config flow (IP-only onboarding) and by __init__ (local rescan
and the bundle-entry migration merge). All controller traffic is read-only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import ClimatixGenericApi, ClimatixGenericConnection
from .bundle_generator import generate_entities_from_bundle
from .catalog import (
    DiscoveryCatalog,
    explicit_language,
    load_catalog,
    load_catalog_raw,
    normalize_language,
)
from .const import (
    CONF_BINARY_SENSORS,
    CONF_BUNDLE_STORAGE_KEY,
    CONF_DEVICE_MODEL,
    CONF_DISCOVERY_SOURCE,
    CONF_HOST,
    CONF_LANGUAGE,
    CONF_NUMBERS,
    CONF_PASSWORD,
    CONF_PIN,
    CONF_PLANT_NAME,
    CONF_PORT,
    CONF_SELECTS,
    CONF_SENSORS,
    CONF_SERIAL_NUMBER,
    CONF_SW_VERSION,
    CONF_SWITCHES,
    CONF_TEXTS,
    CONF_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DISCOVERY_SOURCE_LOCAL,
    DOMAIN,
)
from .discovery import DiscoveryScanResult, async_scan
from .discovery_entities import (
    PLATFORMS,
    build_entities,
    catalog_with_overlay,
    hc_uid_map_from_existing_entities,
    merge_discovered_entities,
    overlay_points_from_bundle_entities,
    relocalize_entities,
)

_LOGGER = logging.getLogger(__name__)

_BUNDLE_STORE_VERSION = 1
_BUNDLE_STORE_KEY = f"{DOMAIN}_bundles"

_PLATFORM_CONF_KEYS = (
    ("sensors", CONF_SENSORS),
    ("binary_sensors", CONF_BINARY_SENSORS),
    ("numbers", CONF_NUMBERS),
    ("selects", CONF_SELECTS),
    ("texts", CONF_TEXTS),
    ("switches", CONF_SWITCHES),
)


def resolve_language(
    hass: HomeAssistant,
    *,
    option_value: Any = None,
    controller: Optional[Dict[str, Any]] = None,
) -> str:
    """The language names/labels resolve in, per the documented order:
    an explicit options-flow choice, then the per-controller CONF_LANGUAGE
    (set by the bundle path), then the Home Assistant language, default
    English. Returns "de" or "en".

    An explicit "auto" option is the user's "follow Home Assistant" choice:
    it skips the controller's stored language (a legacy bundle controller
    keeps e.g. "DE" there) and resolves from the HA language directly. Only
    an ABSENT option falls through to the controller language."""
    lang = explicit_language(option_value)
    if lang:
        return lang
    if str(option_value or "").strip().lower() != "auto":
        lang = explicit_language((controller or {}).get(CONF_LANGUAGE))
        if lang:
            return lang
    return normalize_language(getattr(hass.config, "language", "") or "en")


async def async_load_runtime_catalog(hass: HomeAssistant) -> DiscoveryCatalog:
    """Packaged catalog, enriched with any locally stored cloud bundles.

    Bundle enrichment is optional and additive: it supplies exact names, enum
    options, units and write bindings for addresses the packaged catalog does
    not know. It never enters onboarding (which stays IP-only).
    """

    # The packaged catalog is a ~350 KiB JSON file: parse it off the event loop.
    raw = await hass.async_add_executor_job(load_catalog_raw)

    try:
        store = Store(hass, _BUNDLE_STORE_VERSION, _BUNDLE_STORE_KEY)
        stored = await store.async_load() or {}
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Could not load stored bundles for catalog enrichment: %s", err)
        stored = {}

    overlay: List[Dict[str, Any]] = []
    if isinstance(stored, dict):
        for key, bundle in stored.items():
            if not isinstance(bundle, dict):
                continue
            try:
                # probe=False: pure offline metadata extraction, no reads.
                ents = await generate_entities_from_bundle(bundle=bundle, api=None, probe=False)
                overlay.extend(overlay_points_from_bundle_entities(ents))
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Bundle %s could not enrich the catalog: %s", key, err)

    if not overlay:
        return await hass.async_add_executor_job(load_catalog)
    catalog = await hass.async_add_executor_job(catalog_with_overlay, raw, overlay)
    _LOGGER.debug("Runtime catalog enriched with %d bundle-derived points", len(overlay))
    return catalog


async def async_scan_controller(
    hass: HomeAssistant,
    *,
    host: str,
    port: int = DEFAULT_PORT,
    username: str = DEFAULT_USERNAME,
    password: str = DEFAULT_PASSWORD,
    pin: str = DEFAULT_PIN,
    catalog: Optional[DiscoveryCatalog] = None,
    hc_uid_by_tag: Optional[Dict[int, str]] = None,
    language: Optional[str] = None,
) -> Tuple[Dict[str, List[Dict[str, Any]]], DiscoveryScanResult, DiscoveryCatalog]:
    """Run the read-only catalog scan and return (entities, scan, catalog).

    ``language`` (de/en) picks display names and enum labels; None resolves
    from the Home Assistant language (IP-only onboarding has no setting yet).
    """

    if catalog is None:
        catalog = await async_load_runtime_catalog(hass)

    session = async_get_clientsession(hass)
    api = ClimatixGenericApi(
        session,
        ClimatixGenericConnection(
            host=host, port=port, username=username, password=password, pin=pin
        ),
    )
    scan = await async_scan(api, catalog)
    if language is None:
        language = resolve_language(hass)
    language = normalize_language(language)
    # Localized fallback name for a circuit whose owner-configured name could
    # not be read (spec v4 F: DE + EN for integration-provided naming).
    hc_fallback = "Heizkreis {n}" if language == "de" else "Heating circuit {n}"
    entities = build_entities(
        catalog=catalog,
        scan=scan,
        hc_uid_by_tag=hc_uid_by_tag,
        hc_fallback_template=hc_fallback,
        language=language,
    )
    return entities, scan, catalog


async def async_relocalize_controllers(
    hass: HomeAssistant,
    *,
    controllers: List[Dict[str, Any]],
    option_language: Any = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Re-resolve discovery entity names/enum labels for the current language.

    Offline (no controller traffic): runs at every setup so a language
    change applies on reload without a rescan. Only controllers with
    discovery provenance are touched (local-scan entries, and bundle
    entries whose merged discovery additions carry no uuid); a YAML-style
    controller without provenance is left alone. Identity never changes.
    """

    catalog = await async_load_runtime_catalog(hass)

    changed_any = False
    out: List[Dict[str, Any]] = []
    for ctrl in controllers:
        ctrl_d = dict(ctrl)
        eligible = (
            ctrl_d.get(CONF_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LOCAL
            or bool(ctrl_d.get(CONF_BUNDLE_STORAGE_KEY))
        )
        if not eligible:
            out.append(ctrl_d)
            continue
        language = resolve_language(hass, option_value=option_language, controller=ctrl_d)
        ents = {key: list(ctrl_d.get(conf_key) or []) for key, conf_key in _PLATFORM_CONF_KEYS}
        new_ents, changed = relocalize_entities(ents, catalog, language)
        if changed:
            for key, conf_key in _PLATFORM_CONF_KEYS:
                ctrl_d[conf_key] = new_ents[key]
            changed_any = True
        out.append(ctrl_d)
    return out, changed_any


async def async_local_scan_merge(
    hass: HomeAssistant,
    *,
    controllers: List[Dict[str, Any]],
    only_local_entries: bool,
    option_language: Any = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Rescan controllers against the local catalog and merge missing entities.

    ``only_local_entries`` limits the pass to controllers created by the local
    discovery flow; with False it also enriches bundle-based controllers (the
    migration path for existing users — additions only, no churn).
    """

    catalog = await async_load_runtime_catalog(hass)

    added_total: Dict[str, int] = {key: 0 for key in PLATFORMS}
    updated: List[Dict[str, Any]] = []

    for ctrl in controllers:
        ctrl_d = dict(ctrl)
        host = str(ctrl_d.get(CONF_HOST) or "").strip()
        is_local = ctrl_d.get(CONF_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LOCAL
        if not host or (only_local_entries and not is_local):
            updated.append(ctrl_d)
            continue

        # Attach added circuit entities to the circuit devices the entry
        # already uses (bundle UUID-based or tag-based), never new ones.
        hc_uid_by_tag = hc_uid_map_from_existing_entities(
            {key: list(ctrl_d.get(conf_key) or []) for key, conf_key in _PLATFORM_CONF_KEYS},
            catalog.hc_tags,
        )

        try:
            discovered, scan, _cat = await async_scan_controller(
                hass,
                host=host,
                port=int(ctrl_d.get(CONF_PORT, DEFAULT_PORT)),
                username=str(ctrl_d.get(CONF_USERNAME, DEFAULT_USERNAME)),
                password=str(ctrl_d.get(CONF_PASSWORD, DEFAULT_PASSWORD)),
                pin=str(ctrl_d.get(CONF_PIN, DEFAULT_PIN)),
                catalog=catalog,
                hc_uid_by_tag=hc_uid_by_tag,
                language=resolve_language(hass, option_value=option_language, controller=ctrl_d),
            )
        except Exception as err:  # noqa: BLE001
            # Log only the exception type/status: aiohttp errors can embed the
            # full request URL including the PIN query parameter.
            _LOGGER.warning(
                "Local catalog scan failed for %s: %s%s",
                host,
                type(err).__name__,
                f" (status={getattr(err, 'status', '')})" if getattr(err, "status", None) else "",
            )
            updated.append(ctrl_d)
            continue

        merged, added = merge_discovered_entities(ctrl_d, discovered)
        # Pump-first device naming: refresh the identity read from the
        # controller (model type, serial number, software version). The plant
        # name is only set while it still is a generic default — a
        # user-chosen name is never overwritten.
        if scan.plant_model:
            merged[CONF_DEVICE_MODEL] = scan.plant_model
            current_name = str(merged.get(CONF_PLANT_NAME) or "")
            if current_name in ("", f"Ochsner ({host})", f"Climatix ({host})"):
                merged[CONF_PLANT_NAME] = f"{scan.plant_model} ({host})"
        if scan.plant_serial:
            merged[CONF_SERIAL_NUMBER] = scan.plant_serial
        if scan.plant_sw_version:
            merged[CONF_SW_VERSION] = scan.plant_sw_version
        for k, v in added.items():
            added_total[k] += v
        updated.append(merged)

    return updated, added_total
