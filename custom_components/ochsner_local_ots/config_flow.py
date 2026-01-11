from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from aiohttp import BasicAuth

from .const import (
    CONF_BINARY_SENSORS,
    CONF_BUNDLE_STORAGE_KEY,
    CONF_CONFIG_ID,
    CONF_CONTROLLERS,
    CONF_DEVICE_MODEL,
    CONF_LANGUAGE,
    CONF_NUMBERS,
    CONF_PIN,
    CONF_PLANT_KEY,
    CONF_PLANT_NAME,
    CONF_SITE_ID,
    CONF_SCAN_INTERVAL,
    CONF_SELECTS,
    CONF_SENSORS,
    CONF_TEXTS,
    CONF_RESCAN_NOW,
    CONF_RESCAN_ON_START,
    CONF_REDOWNLOAD_BUNDLE,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_USERNAME,
    DOMAIN,
)

from .bundle_refresh import async_redownload_bundles_and_merge

from .bundle_generator import generate_entities_from_bundle
from .ots_client import decode_ots_bundle_content, ots_getconfig, ots_login

_LOGGER = logging.getLogger(__name__)


CONF_OTS_USER = "ots_user"
CONF_OTS_PASS = "ots_pass"
CONF_PLANTS = "plants"
CONF_LOCAL_IP = "local_ip"


class ClimatixGenericConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return ClimatixGenericOptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        self._ots_user: Optional[str] = None
        self._ots_pass: Optional[str] = None
        self._language: str = "AUTO"
        self._plants: List[Dict[str, Any]] = []
        self._selected_keys: List[str] = []
        self._host_by_key: Dict[str, str] = {}
        # User-provided label used as device name (and config entry title) in HA.
        self._name_by_key: Dict[str, str] = {}
        self._host_index: int = 0

    def _default_language(self) -> str:
        # Prefer HA UI language if known; fall back to AUTO.
        try:
            lang = str(getattr(self.hass.config, "language", "") or "").strip().lower()
        except Exception:  # noqa: BLE001
            lang = ""
        if not lang:
            return "AUTO"
        # Map common HA language tags to bundle translation codes.
        if lang.startswith("de"):
            return "DE"
        if lang.startswith("en"):
            return "EN"
        if lang.startswith("fr"):
            return "FR"
        if lang.startswith("it"):
            return "IT"
        if lang.startswith("es"):
            return "ES"
        if lang.startswith("nl"):
            return "NL"
        if lang.startswith("pl"):
            return "PL"
        if lang.startswith("cs"):
            return "CS"
        return "AUTO"

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if user_input is not None:
            self._ots_user = str(user_input.get(CONF_OTS_USER) or "").strip()
            self._ots_pass = str(user_input.get(CONF_OTS_PASS) or "")
            self._language = str(user_input.get(CONF_LANGUAGE) or "AUTO").strip().upper() or "AUTO"
            try:
                plant_infos = await ots_login(self.hass, username=self._ots_user, password=self._ots_pass)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("OTS login failed: %s", err)
                errors["base"] = "auth"
            else:
                # Normalize plants into lightweight dicts for later steps.
                self._plants = [
                    {
                        CONF_CONFIG_ID: p.config_id,
                        CONF_SITE_ID: p.site_id,
                        CONF_PLANT_NAME: p.name,
                        CONF_PLANT_KEY: f"{p.config_id}:{p.site_id}",
                    }
                    for p in plant_infos
                ]
                return await self.async_step_select_plants()

        schema = vol.Schema(
            {
                vol.Required(CONF_OTS_USER): selector.TextSelector(),
                vol.Required(CONF_OTS_PASS): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_LANGUAGE, default=self._default_language()): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value="AUTO", label="Auto"),
                            selector.SelectOptionDict(value="DE", label="Deutsch"),
                            selector.SelectOptionDict(value="EN", label="English"),
                            selector.SelectOptionDict(value="FR", label="Français"),
                            selector.SelectOptionDict(value="IT", label="Italiano"),
                            selector.SelectOptionDict(value="ES", label="Español"),
                            selector.SelectOptionDict(value="NL", label="Nederlands"),
                            selector.SelectOptionDict(value="PL", label="Polski"),
                            selector.SelectOptionDict(value="CS", label="Čeština"),
                        ],
                        multiple=False,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"note": "Credentials are not stored in Home Assistant."},
        )

    async def async_step_select_plants(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if not self._plants:
            return await self.async_step_user()

        options_map = {str(p[CONF_PLANT_KEY]): str(p[CONF_PLANT_NAME]) for p in self._plants}
        options = [selector.SelectOptionDict(value=k, label=v) for k, v in options_map.items()]
        default_sel = options[0]["value"] if options else ""

        if user_input is not None:
            selected = user_input.get(CONF_PLANTS)
            if isinstance(selected, str) and selected in options_map:
                self._selected_keys = [selected]

            if not self._selected_keys:
                errors["base"] = "no_selection"
            else:
                self._host_by_key = {}
                self._host_index = 0
                return await self.async_step_hosts()

        schema = vol.Schema(
            {
                vol.Required(CONF_PLANTS, default=default_sel): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=False,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="select_plants", data_schema=schema, errors=errors)

    async def async_step_hosts(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}
        if not self._selected_keys:
            return await self.async_step_select_plants()

        current_key = self._selected_keys[self._host_index]
        plant = next((p for p in self._plants if p.get(CONF_PLANT_KEY) == current_key), None)
        plant_name = str((plant or {}).get(CONF_PLANT_NAME) or current_key)

        default_name = self._name_by_key.get(current_key) or plant_name

        if user_input is not None:
            host = str(user_input.get(CONF_LOCAL_IP) or "").strip()
            hp_name = str(user_input.get(CONF_NAME) or "").strip() or plant_name
            if not host:
                errors["base"] = "invalid_host"
            else:
                ok = await self._async_check_local_supported(host)
                if not ok:
                    errors["base"] = "not_supported"
                else:
                    self._host_by_key[current_key] = host
                    self._name_by_key[current_key] = hp_name
                    self._host_index += 1
                    if self._host_index < len(self._selected_keys):
                        return await self.async_step_hosts()
                    return await self.async_step_finish()

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_IP): str,
                vol.Required(CONF_NAME, default=default_name): str,
            }
        )
        return self.async_show_form(
            step_id="hosts",
            data_schema=schema,
            errors=errors,
            description_placeholders={"plant": plant_name},
        )

    async def _async_check_local_supported(self, host: str) -> bool:
        session = async_get_clientsession(self.hass)
        auth = BasicAuth(DEFAULT_USERNAME, DEFAULT_PASSWORD)
        urls = [
            f"http://{host}:{DEFAULT_PORT}/jsongen.html",
            f"http://{host}:{DEFAULT_PORT}/JSONgen.html",
        ]
        for url in urls:
            try:
                async with session.get(url, auth=auth, timeout=10) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json(content_type=None)
                    if isinstance(data, dict) and data.get("Error") == 7:
                        return True
            except Exception:  # noqa: BLE001
                continue
        return False

    async def async_step_finish(self, user_input: Optional[Dict[str, Any]] = None):
        # No form; perform imports and create the config entry.
        if not self._ots_user or self._ots_pass is None:
            return await self.async_step_user()

        controllers: List[Dict[str, Any]] = []
        store = Store(self.hass, 1, f"{DOMAIN}_bundles")
        stored = await store.async_load() or {}
        if not isinstance(stored, dict):
            stored = {}

        for key in self._selected_keys:
            plant = next((p for p in self._plants if p.get(CONF_PLANT_KEY) == key), None)
            if not plant:
                continue
            host = self._host_by_key.get(key)
            if not host:
                continue

            # Use the user-provided label as the HA device name / config entry title.
            # Use the OTS plant name as the HA device model (this matches the previous onboarding default).
            user_name = str(self._name_by_key.get(key) or "").strip()
            ots_plant_name = str(plant.get(CONF_PLANT_NAME) or "").strip() or f"Climatix ({host})"
            plant_name = user_name or ots_plant_name

            cfg = await ots_getconfig(self.hass, config_id=str(plant[CONF_CONFIG_ID]), site_id=str(plant[CONF_SITE_ID]), stamp=0)
            if not cfg.get("success"):
                raise RuntimeError(f"GetConfig failed for {key}: {cfg.get('message')}")
            content = cfg.get("content")
            if not isinstance(content, str) or not content:
                raise RuntimeError(f"GetConfig returned no content for {key}")

            bundle = decode_ots_bundle_content(content)

            # Persist bundle locally (but do not persist OTS credentials).
            bundle_storage_key = f"{DOMAIN}:{key}".replace(" ", "_")
            stored[bundle_storage_key] = bundle
            await store.async_save(stored)

            # Generate entities using the same heuristics as the CLI (with probing).
            session = async_get_clientsession(self.hass)
            from .api import ClimatixGenericApi, ClimatixGenericConnection

            api = ClimatixGenericApi(
                session,
                ClimatixGenericConnection(
                    host=host,
                    port=DEFAULT_PORT,
                    username=DEFAULT_USERNAME,
                    password=DEFAULT_PASSWORD,
                    pin=DEFAULT_PIN,
                ),
            )

            ents = await generate_entities_from_bundle(bundle=bundle, api=api, language=self._language, probe=True)
            # Some OTS plant names are like "AIRHAWK518C11A - 523203292" (model + serial).
            # Store only the model part as the HA device model.
            device_model = ots_plant_name
            if " - " in device_model:
                device_model = device_model.split(" - ", 1)[0].strip() or ots_plant_name
            _LOGGER.debug(
                "Discovered entities for %s (%s): sensors=%d binary_sensors=%d numbers=%d selects=%d texts=%d",
                plant_name,
                host,
                len(ents.get("sensors", [])),
                len(ents.get("binary_sensors", [])),
                len(ents.get("numbers", [])),
                len(ents.get("selects", [])),
                len(ents.get("texts", [])),
            )

            if not any(len(ents.get(k, [])) for k in ("sensors", "binary_sensors", "numbers", "selects", "texts")):
                raise RuntimeError(
                    f"Probing succeeded but discovered 0 usable values for {plant_name} ({host}). "
                    "This usually means the controller rejected reads (auth/PIN/endpoint) or none of the bundle IDs exist on the device."
                )

            controllers.append(
                {
                    CONF_PLANT_KEY: key,
                    CONF_PLANT_NAME: plant_name,
                    CONF_CONFIG_ID: str(plant.get(CONF_CONFIG_ID) or ""),
                    CONF_SITE_ID: str(plant.get(CONF_SITE_ID) or ""),
                    CONF_LANGUAGE: self._language,
                    CONF_DEVICE_MODEL: device_model,
                    CONF_HOST: host,
                    CONF_PORT: DEFAULT_PORT,
                    CONF_USERNAME: DEFAULT_USERNAME,
                    CONF_PASSWORD: DEFAULT_PASSWORD,
                    CONF_PIN: DEFAULT_PIN,
                    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_SEC,
                    CONF_SENSORS: ents.get("sensors", []),
                    CONF_BINARY_SENSORS: ents.get("binary_sensors", []),
                    CONF_NUMBERS: ents.get("numbers", []),
                    CONF_SELECTS: ents.get("selects", []),
                    CONF_TEXTS: ents.get("texts", []),
                    CONF_BUNDLE_STORAGE_KEY: bundle_storage_key,
                }
            )

        # Clear credentials from memory as soon as possible.
        self._ots_user = None
        self._ots_pass = None

        # Unique per plant (one controller per entry).
        uniq = str(controllers[0].get(CONF_PLANT_KEY) or "") if controllers else ""
        if uniq:
            await self.async_set_unique_id(f"{DOMAIN}:{uniq}"[:255])
            self._abort_if_unique_id_configured()

        title = str(controllers[0].get(CONF_PLANT_NAME) or "").strip() if controllers else ""
        if not title:
            title = "Climatix (Ochsner)"

        return self.async_create_entry(
            title=title,
            data={CONF_CONTROLLERS: controllers},
        )

    async def async_step_import(self, user_input: Dict[str, Any]):
        # Import from YAML so the integration becomes a config entry.
        host = user_input[CONF_HOST]
        await self.async_set_unique_id(f"{DOMAIN}:{host}")

        # Store entire YAML block (including entity configs) in the config entry.
        data = {
            CONF_HOST: user_input.get(CONF_HOST),
            CONF_PORT: user_input.get(CONF_PORT, DEFAULT_PORT),
            CONF_USERNAME: user_input.get(CONF_USERNAME, DEFAULT_USERNAME),
            CONF_PASSWORD: user_input.get(CONF_PASSWORD, DEFAULT_PASSWORD),
            CONF_PIN: user_input.get(CONF_PIN, DEFAULT_PIN),
            CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC),
            CONF_SENSORS: list(user_input.get(CONF_SENSORS, []) or []),
            CONF_BINARY_SENSORS: list(user_input.get(CONF_BINARY_SENSORS, []) or []),
            CONF_NUMBERS: list(user_input.get(CONF_NUMBERS, []) or []),
            CONF_SELECTS: list(user_input.get(CONF_SELECTS, []) or []),
            CONF_TEXTS: list(user_input.get(CONF_TEXTS, []) or []),
        }

        # If an entry already exists for this host, keep YAML as source-of-truth:
        # update the entry data and reload so changes in configuration.yaml apply.
        existing = None
        for entry in self._async_current_entries():
            if entry.unique_id == self.unique_id:
                existing = entry
                break
        if existing is not None:
            if dict(existing.data) != data:
                self.hass.config_entries.async_update_entry(existing, data=data)
                return self.async_abort(reason="updated")
            return self.async_abort(reason="already_configured")

        return self.async_create_entry(title=f"Climatix ({host})", data=data)


class ClimatixGenericOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # Home Assistant's OptionsFlow exposes config_entry as a read-only property.
        # Newer HA requires passing it to the base initializer.
        try:
            super().__init__(config_entry)
        except TypeError:
            # Backwards compatibility with older HA versions.
            super().__init__()
            self._config_entry = config_entry

        self._pending_options: Dict[str, Any] = {}

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        current = int(self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC))
        rescan_on_start = bool(self.config_entry.options.get(CONF_RESCAN_ON_START, False))
        rescan_now_default = False
        redownload_default = False

        if user_input is not None:
            try:
                interval = int(user_input.get(CONF_SCAN_INTERVAL, current))
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_scan_interval"
            else:
                if interval < 1:
                    errors["base"] = "invalid_scan_interval"
                else:
                    out = {
                        CONF_SCAN_INTERVAL: interval,
                        CONF_RESCAN_ON_START: bool(user_input.get(CONF_RESCAN_ON_START, rescan_on_start)),
                    }

                    # One-shot flag: if enabled, setup will rescan then clear it.
                    if bool(user_input.get(CONF_RESCAN_NOW, False)):
                        out[CONF_RESCAN_NOW] = True

                    if bool(user_input.get(CONF_REDOWNLOAD_BUNDLE, False)):
                        self._pending_options = out
                        return await self.async_step_redownload()

                    return self.async_create_entry(title="", data=out)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=3600,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
                vol.Optional(CONF_RESCAN_ON_START, default=rescan_on_start): selector.BooleanSelector(),
                vol.Optional(CONF_RESCAN_NOW, default=rescan_now_default): selector.BooleanSelector(),
                vol.Optional(CONF_REDOWNLOAD_BUNDLE, default=redownload_default): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_redownload(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        if user_input is not None:
            ots_user = str(user_input.get(CONF_OTS_USER) or "").strip()
            ots_pass = str(user_input.get(CONF_OTS_PASS) or "")
            if not ots_user or not ots_pass:
                errors["base"] = "auth"
            else:
                ok, err_key, _added = await async_redownload_bundles_and_merge(
                    self.hass,
                    self.config_entry,
                    ots_username=ots_user,
                    ots_password=ots_pass,
                )
                if not ok:
                    errors["base"] = err_key or "download_failed"
                else:
                    # We already updated entry.data; trigger exactly one reload.
                    self.hass.data.setdefault(DOMAIN, {}).setdefault("_skip_reload_once", set()).add(self.config_entry.entry_id)
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    return self.async_create_entry(title="", data=self._pending_options)

        schema = vol.Schema(
            {
                vol.Required(CONF_OTS_USER): selector.TextSelector(),
                vol.Required(CONF_OTS_PASS): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="redownload",
            data_schema=schema,
            errors=errors,
            description_placeholders={"note": "Credentials are not stored in Home Assistant."},
        )
