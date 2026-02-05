from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import entity_registry as er, selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from aiohttp import BasicAuth

from .const import (
    CONF_BINARY_SENSORS,
    CONF_BUNDLE_STORAGE_KEY,
    CONF_CONFIG_ID,
    CONF_CONTROLLERS,
    CONF_BUNDLE_MAX,
    CONF_BUNDLE_MIN,
    CONF_HEATING_CIRCUIT_NAME,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_ENTITY_OVERRIDES,
    CONF_ID,
    CONF_LANGUAGE,
    CONF_MAX,
    CONF_MIN,
    CONF_NUMBERS,
    CONF_PIN,
    CONF_PLANT_KEY,
    CONF_PLANT_NAME,
    CONF_SITE_ID,
    CONF_SCAN_INTERVAL,
    CONF_POLLING_THRESHOLD,
    CONF_SELECTS,
    CONF_SENSORS,
    CONF_STEP,
    CONF_STATE_CLASS,
    CONF_TEXTS,
    CONF_UNIT,
    CONF_UUID,
    CONF_READ_ID,
    CONF_RESCAN_NOW,
    CONF_RESCAN_ON_START,
    CONF_REDOWNLOAD_BUNDLE,
    CONF_POLLING_MODE,
    POLLING_MODE_AUTOMATIC,
    POLLING_MODE_FAST,
    POLLING_MODE_SLOW,
    DEFAULT_PASSWORD,
    DEFAULT_PIN,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_POLLING_THRESHOLD,
    DEFAULT_USERNAME,
    DELAY_RELOAD_SEC,
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

    @staticmethod
    def _clone_options(options: Dict[str, Any]) -> Dict[str, Any]:
        """Return an options dict safe to mutate.

        Home Assistant's config entry options are nested dicts. A shallow copy will
        keep references to nested dicts (like entity_overrides). Mutating those in
        place can change entry.options before async_update_entry(), causing HA to
        consider the update a no-op and skip persisting to .storage.
        """

        out = dict(options or {})
        ov = out.get(CONF_ENTITY_OVERRIDES)
        if isinstance(ov, dict):
            out[CONF_ENTITY_OVERRIDES] = {k: dict(v) if isinstance(v, dict) else v for k, v in ov.items()}
        else:
            out[CONF_ENTITY_OVERRIDES] = {}
        return out

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        existing_options = dict(self.config_entry.options or {})
        current = int(existing_options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC))
        poll_threshold_cur = int(existing_options.get(CONF_POLLING_THRESHOLD, DEFAULT_POLLING_THRESHOLD))
        rescan_on_start = bool(existing_options.get(CONF_RESCAN_ON_START, False))
        rescan_now_default = False
        redownload_default = False
        edit_entities_default = False

        if user_input is not None:
            try:
                interval = int(user_input.get(CONF_SCAN_INTERVAL, current))
            except Exception:  # noqa: BLE001
                errors["base"] = "invalid_scan_interval"
            else:
                if interval < 1:
                    errors["base"] = "invalid_scan_interval"
                else:
                    try:
                        poll_threshold = int(user_input.get(CONF_POLLING_THRESHOLD, poll_threshold_cur))
                    except Exception:  # noqa: BLE001
                        errors["base"] = "invalid_polling_threshold"
                        poll_threshold = poll_threshold_cur

                    if not errors and (poll_threshold < 10 or poll_threshold > 120):
                        errors["base"] = "invalid_polling_threshold"

                    if errors:
                        # Fall through to re-render form.
                        pass
                    else:
                        out = dict(existing_options)
                        out[CONF_SCAN_INTERVAL] = interval
                        out[CONF_POLLING_THRESHOLD] = poll_threshold
                        out[CONF_RESCAN_ON_START] = bool(user_input.get(CONF_RESCAN_ON_START, rescan_on_start))

                        # Ensure entity overrides always survive option updates.
                        if CONF_ENTITY_OVERRIDES not in out:
                            out[CONF_ENTITY_OVERRIDES] = {}

                        # One-shot flag: if enabled, setup will rescan then clear it.
                        if bool(user_input.get(CONF_RESCAN_NOW, False)):
                            out[CONF_RESCAN_NOW] = True

                        if bool(user_input.get(CONF_REDOWNLOAD_BUNDLE, False)):
                            self._pending_options = out
                            return await self.async_step_redownload()

                        if bool(user_input.get("configure_entities", False)):
                            self._pending_options = out
                            return await self.async_step_entity_override_select()

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
                vol.Optional(
                    CONF_POLLING_THRESHOLD,
                    default=poll_threshold_cur,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=120,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_RESCAN_ON_START, default=rescan_on_start): selector.BooleanSelector(),
                vol.Optional(CONF_RESCAN_NOW, default=rescan_now_default): selector.BooleanSelector(),
                vol.Optional(CONF_REDOWNLOAD_BUNDLE, default=redownload_default): selector.BooleanSelector(),
                vol.Optional("configure_entities", default=edit_entities_default): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _iter_controllers(self) -> List[Dict[str, Any]]:
        raw = self.config_entry.data.get(CONF_CONTROLLERS)
        if isinstance(raw, list) and raw:
            return [c for c in raw if isinstance(c, dict)]

        # Backwards compatible single-controller entry.
        host = str(self.config_entry.data.get(CONF_HOST) or "").strip()
        if not host:
            return []
        return [
            {
                CONF_HOST: host,
                CONF_SENSORS: list(self.config_entry.data.get(CONF_SENSORS, []) or []),
                CONF_BINARY_SENSORS: list(self.config_entry.data.get(CONF_BINARY_SENSORS, []) or []),
                CONF_NUMBERS: list(self.config_entry.data.get(CONF_NUMBERS, []) or []),
                CONF_SELECTS: list(self.config_entry.data.get(CONF_SELECTS, []) or []),
            }
        ]

    def _build_entity_choices(self) -> List[selector.SelectOptionDict]:
        opts: List[selector.SelectOptionDict] = []

        def _shorten(s: Optional[str], max_len: int = 40, tail_len: int = 10) -> Optional[str]:
            if not s:
                return s
            s2 = str(s)
            if len(s2) <= max_len:
                return s2
            if max_len <= 3:
                return s2[:max_len]

            # Center ellipsis, keep the last `tail_len` characters.
            # Example (max_len=40, tail_len=10): prefix(27) + '...' + last10
            tl = int(tail_len)
            if tl < 0:
                tl = 0
            if tl > max_len - 3:
                tl = max_len - 3

            prefix_len = max_len - 3 - tl
            if prefix_len < 1:
                prefix_len = 1
                tl = max_len - 3 - prefix_len
                if tl < 0:
                    tl = 0

            if tl:
                return f"{s2[:prefix_len]}...{s2[-tl:]}"
            return f"{s2[:prefix_len]}..."

        # Map unique_id -> entity_id for this config entry (to disambiguate duplicates).
        unique_to_entity_id: Dict[str, str] = {}
        try:
            ent_reg = er.async_get(self.hass)
            for ent in er.async_entries_for_config_entry(ent_reg, self.config_entry.entry_id):
                if getattr(ent, "unique_id", None) and getattr(ent, "entity_id", None):
                    unique_to_entity_id[str(ent.unique_id)] = str(ent.entity_id)
        except Exception:
            unique_to_entity_id = {}

        controllers = self._iter_controllers()
        for ctrl in controllers:
            host = str(ctrl.get(CONF_HOST) or "").strip()
            plant_name = str(ctrl.get(CONF_PLANT_NAME) or host or "controller")

            for s in list(ctrl.get(CONF_SENSORS, []) or []):
                if not isinstance(s, dict):
                    continue
                ent_id = str(s.get(CONF_ID) or "").strip()
                if not ent_id:
                    continue
                uuid = str(s.get(CONF_UUID) or "").strip()
                override_key = f"{host}:sensor:{ent_id}".replace("=", "")
                name = _shorten(str(s.get(CONF_NAME) or ent_id))
                hc_name = str(s.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
                hc_part = f" · {hc_name}" if hc_name else ""
                # Entity registry keys by the entity's unique_id (which may be UUID-based
                # or host+id-based depending on config/bundle).
                ha_entity_id = unique_to_entity_id.get(uuid) if uuid else None
                if not ha_entity_id:
                    ha_entity_id = unique_to_entity_id.get(override_key)
                ha_entity_id = _shorten(ha_entity_id)
                eid_part = f" · {ha_entity_id}" if ha_entity_id else ""
                opts.append(selector.SelectOptionDict(value=override_key, label=f"sensor · {plant_name}{hc_part} · {name} ({eid_part} - {ent_id})"))

            for bs in list(ctrl.get(CONF_BINARY_SENSORS, []) or []):
                if not isinstance(bs, dict):
                    continue
                ent_id = str(bs.get(CONF_ID) or "").strip()
                if not ent_id:
                    continue
                uuid = str(bs.get(CONF_UUID) or "").strip()
                override_key = f"{host}:binary_sensor:{ent_id}".replace("=", "")
                name = _shorten(str(bs.get(CONF_NAME) or ent_id))
                hc_name = str(bs.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
                hc_part = f" · {hc_name}" if hc_name else ""
                ha_entity_id = unique_to_entity_id.get(uuid) if uuid else None
                if not ha_entity_id:
                    ha_entity_id = unique_to_entity_id.get(override_key)
                ha_entity_id = _shorten(ha_entity_id)
                eid_part = f" · {ha_entity_id}" if ha_entity_id else ""
                opts.append(
                    selector.SelectOptionDict(
                        value=override_key,
                        label=f"binary_sensor · {plant_name}{hc_part} · {name} ({eid_part} - {ent_id})",
                    )
                )

            for n in list(ctrl.get(CONF_NUMBERS, []) or []):
                if not isinstance(n, dict):
                    continue
                read_id = str(n.get(CONF_READ_ID) or n.get(CONF_ID) or "").strip()
                if not read_id:
                    continue
                uuid = str(n.get(CONF_UUID) or "").strip()
                override_key = f"{host}:number:{read_id}".replace("=", "")
                name = _shorten(str(n.get(CONF_NAME) or read_id))
                hc_name = str(n.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
                hc_part = f" · {hc_name}" if hc_name else ""
                ha_entity_id = unique_to_entity_id.get(uuid) if uuid else None
                if not ha_entity_id:
                    ha_entity_id = unique_to_entity_id.get(override_key)
                ha_entity_id = _shorten(ha_entity_id)
                eid_part = f" · {ha_entity_id}" if ha_entity_id else ""
                opts.append(selector.SelectOptionDict(value=override_key, label=f"number · {plant_name}{hc_part} · {name} ({eid_part} - {read_id})"))

            for sel in list(ctrl.get(CONF_SELECTS, []) or []):
                if not isinstance(sel, dict):
                    continue
                read_id = str(sel.get(CONF_READ_ID) or sel.get(CONF_ID) or "").strip()
                if not read_id:
                    continue
                uuid = str(sel.get(CONF_UUID) or "").strip()
                override_key = f"{host}:select:{read_id}".replace("=", "")
                name = _shorten(str(sel.get(CONF_NAME) or read_id))
                hc_name = str(sel.get(CONF_HEATING_CIRCUIT_NAME) or "").strip()
                hc_part = f" · {hc_name}" if hc_name else ""
                ha_entity_id = unique_to_entity_id.get(uuid) if uuid else None
                if not ha_entity_id:
                    ha_entity_id = unique_to_entity_id.get(override_key)
                ha_entity_id = _shorten(ha_entity_id)
                eid_part = f" · {ha_entity_id}" if ha_entity_id else ""
                opts.append(
                    selector.SelectOptionDict(
                        value=override_key,
                        label=f"select · {plant_name}{hc_part} · {name} ({eid_part} - {read_id})",
                    )
                )

        # Stable ordering in UI.
        opts.sort(key=lambda o: str(o.get("label") or ""))
        return opts

    def _find_number_cfg_by_unique_id(self, entity_unique_id: str) -> Optional[Dict[str, Any]]:
        controllers = self._iter_controllers()
        for ctrl in controllers:
            host = str(ctrl.get(CONF_HOST) or "").strip()
            for n in list(ctrl.get(CONF_NUMBERS, []) or []):
                if not isinstance(n, dict):
                    continue
                read_id = str(n.get(CONF_READ_ID) or n.get(CONF_ID) or "").strip()
                if not read_id:
                    continue
                override_key = f"{host}:number:{read_id}".replace("=", "")
                if override_key == entity_unique_id:
                    return n
        return None

    def _find_select_cfg_by_unique_id(self, entity_unique_id: str) -> Optional[Dict[str, Any]]:
        controllers = self._iter_controllers()
        for ctrl in controllers:
            host = str(ctrl.get(CONF_HOST) or "").strip()
            for sel in list(ctrl.get(CONF_SELECTS, []) or []):
                if not isinstance(sel, dict):
                    continue
                read_id = str(sel.get(CONF_READ_ID) or sel.get(CONF_ID) or "").strip()
                if not read_id:
                    continue
                override_key = f"{host}:select:{read_id}".replace("=", "")
                if override_key == entity_unique_id:
                    return sel
        return None

    def _find_sensor_cfg_by_unique_id(self, entity_unique_id: str) -> Optional[Dict[str, Any]]:
        controllers = self._iter_controllers()
        for ctrl in controllers:
            host = str(ctrl.get(CONF_HOST) or "").strip()
            for s in list(ctrl.get(CONF_SENSORS, []) or []):
                if not isinstance(s, dict):
                    continue
                ent_id = str(s.get(CONF_ID) or "").strip()
                if not ent_id:
                    continue
                override_key = f"{host}:sensor:{ent_id}".replace("=", "")
                if override_key == entity_unique_id:
                    return s
        return None

    def _find_binary_sensor_cfg_by_unique_id(self, entity_unique_id: str) -> Optional[Dict[str, Any]]:
        controllers = self._iter_controllers()
        for ctrl in controllers:
            host = str(ctrl.get(CONF_HOST) or "").strip()
            for bs in list(ctrl.get(CONF_BINARY_SENSORS, []) or []):
                if not isinstance(bs, dict):
                    continue
                ent_id = str(bs.get(CONF_ID) or "").strip()
                if not ent_id:
                    continue
                override_key = f"{host}:binary_sensor:{ent_id}".replace("=", "")
                if override_key == entity_unique_id:
                    return bs
        return None

    async def async_step_entity_override_select(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        choices = self._build_entity_choices()
        if not choices:
            # Nothing to configure; just finish.
            return self.async_create_entry(title="", data=self._pending_options or dict(self.config_entry.options or {}))

        if user_input is not None:
            entity_key = str(user_input.get("entity") or "").strip()
            if not entity_key:
                errors["base"] = "no_selection"
            else:
                base = self._pending_options or dict(self.config_entry.options or {})
                self._pending_options = self._clone_options(base)
                self._pending_options["_editing_entity"] = entity_key
                return await self.async_step_entity_override_edit()

        schema = vol.Schema(
            {
                vol.Required("entity"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=choices,
                        multiple=False,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="entity_override_select", data_schema=schema, errors=errors)

    async def async_step_entity_override_edit(self, user_input: Optional[Dict[str, Any]] = None):
        errors: Dict[str, str] = {}

        pending = self._clone_options(self._pending_options or {})
        entity_key = str(pending.get("_editing_entity") or "").strip()
        if not entity_key:
            return await self.async_step_init()

        overrides = pending.get(CONF_ENTITY_OVERRIDES)
        if not isinstance(overrides, dict):
            overrides = {}
        else:
            # Make sure we're never mutating a nested dict that still aliases entry.options.
            overrides = dict(overrides)

        # Overrides may be stored under either the stable override key (host+kind+id)
        # or (legacy) under the entity unique_id (UUID-based). Prefer stable.
        current_ov: Dict[str, Any] = {}
        legacy_keys = [entity_key]

        sensor_cfg = self._find_sensor_cfg_by_unique_id(entity_key)
        is_sensor = isinstance(sensor_cfg, dict)
        if is_sensor and isinstance(sensor_cfg, dict):
            uuid = str(sensor_cfg.get(CONF_UUID) or "").strip()
            if uuid and uuid not in legacy_keys:
                legacy_keys.append(uuid)

        binary_sensor_cfg = self._find_binary_sensor_cfg_by_unique_id(entity_key)
        is_binary_sensor = isinstance(binary_sensor_cfg, dict)
        if is_binary_sensor and isinstance(binary_sensor_cfg, dict):
            uuid = str(binary_sensor_cfg.get(CONF_UUID) or "").strip()
            if uuid and uuid not in legacy_keys:
                legacy_keys.append(uuid)

        number_cfg = self._find_number_cfg_by_unique_id(entity_key)
        is_number = isinstance(number_cfg, dict)
        bundle_min = None
        bundle_max = None
        cfg_min = None
        cfg_max = None
        cfg_step = None
        if is_number:
            bundle_min = number_cfg.get(CONF_BUNDLE_MIN)
            bundle_max = number_cfg.get(CONF_BUNDLE_MAX)
            # Backwards compatible: treat existing min/max as bundle bounds.
            if bundle_min is None:
                bundle_min = number_cfg.get(CONF_MIN)
            if bundle_max is None:
                bundle_max = number_cfg.get(CONF_MAX)
            cfg_min = number_cfg.get(CONF_MIN)
            cfg_max = number_cfg.get(CONF_MAX)
            cfg_step = number_cfg.get(CONF_STEP)

        select_cfg = self._find_select_cfg_by_unique_id(entity_key)
        is_select = isinstance(select_cfg, dict)
        if is_select and isinstance(select_cfg, dict):
            uuid = str(select_cfg.get(CONF_UUID) or "").strip()
            if uuid and uuid not in legacy_keys:
                legacy_keys.append(uuid)

        # Resolve current override (stable key first, then legacy UUID-based).
        for k in legacy_keys:
            v = overrides.get(k)
            if isinstance(v, dict):
                current_ov = v
                break

        cur_unit = current_ov.get(CONF_UNIT)
        cur_dc = current_ov.get(CONF_DEVICE_CLASS)
        cur_min = current_ov.get(CONF_MIN, cfg_min)
        cur_max = current_ov.get(CONF_MAX, cfg_max)
        cur_step = current_ov.get(CONF_STEP, cfg_step)

        cur_sc = current_ov.get(CONF_STATE_CLASS) if is_sensor else None
        cur_polling = str(current_ov.get(CONF_POLLING_MODE) or POLLING_MODE_AUTOMATIC)
        if cur_polling not in {POLLING_MODE_AUTOMATIC, POLLING_MODE_FAST, POLLING_MODE_SLOW}:
            cur_polling = POLLING_MODE_AUTOMATIC

        if user_input is not None:
            clear = bool(user_input.get("clear", False))
            unit = str(user_input.get(CONF_UNIT) or "").strip()
            dc = str(user_input.get(CONF_DEVICE_CLASS) or "").strip()
            sc = str(user_input.get(CONF_STATE_CLASS) or "").strip() if is_sensor else ""

            polling = str(user_input.get(CONF_POLLING_MODE) or POLLING_MODE_AUTOMATIC)
            if polling not in {POLLING_MODE_AUTOMATIC, POLLING_MODE_FAST, POLLING_MODE_SLOW}:
                polling = POLLING_MODE_AUTOMATIC

            min_v = user_input.get(CONF_MIN) if is_number else None
            max_v = user_input.get(CONF_MAX) if is_number else None
            step_v = user_input.get(CONF_STEP) if is_number else None

            # Validate number ranges (only if both provided).
            if is_number and not clear:
                try:
                    if min_v is not None and max_v is not None:
                        if float(max_v) <= float(min_v):
                            errors["base"] = "invalid_range"
                except Exception:
                    errors["base"] = "invalid_range"

            if errors:
                # Fall through to re-render form.
                pass

            if not errors:
                # Build override dict; keep only non-empty values.
                out_ov: Dict[str, Any] = {}
                if polling and polling != POLLING_MODE_AUTOMATIC:
                    out_ov[CONF_POLLING_MODE] = polling
                if unit:
                    out_ov[CONF_UNIT] = unit
                if dc:
                    out_ov[CONF_DEVICE_CLASS] = dc
                if is_sensor and sc:
                    out_ov[CONF_STATE_CLASS] = sc
                if is_number:
                    if min_v not in (None, ""):
                        try:
                            min_f = float(min_v)
                            if cfg_min is None or float(cfg_min) != min_f:
                                out_ov[CONF_MIN] = min_f
                        except Exception:
                            pass
                    if max_v not in (None, ""):
                        try:
                            max_f = float(max_v)
                            if cfg_max is None or float(cfg_max) != max_f:
                                out_ov[CONF_MAX] = max_f
                        except Exception:
                            pass
                    if step_v not in (None, ""):
                        try:
                            step_f = float(step_v)
                            if cfg_step is None or float(cfg_step) != step_f:
                                out_ov[CONF_STEP] = step_f
                        except Exception:
                            pass

                if clear or not out_ov:
                    for k in legacy_keys:
                        overrides.pop(k, None)
                else:
                    overrides[entity_key] = out_ov
                    # Clean up any legacy UUID-keyed override to avoid duplicates/confusion.
                    for k in legacy_keys:
                        if k != entity_key:
                            overrides.pop(k, None)

                try:
                    _LOGGER.debug(
                        "Saving entity override: entry_id=%s entity_key=%s is_sensor=%s is_number=%s legacy_keys=%s out_ov=%s overrides_count=%d",
                        self.config_entry.entry_id,
                        entity_key,
                        is_sensor,
                        is_number,
                        legacy_keys,
                        out_ov,
                        len(overrides),
                    )
                except Exception:
                    pass

                # Persist immediately so entity changes apply right away.
                pending[CONF_ENTITY_OVERRIDES] = overrides
                pending.pop("_editing_entity", None)
                self._pending_options = pending

                edit_another = bool(user_input.get("edit_another", True))

                # If the user keeps editing, persist options but skip reload to avoid churn.
                # If this is the final save, request a delayed reload.
                try:
                    if edit_another:
                        self.hass.data.setdefault(DOMAIN, {}).setdefault("_skip_reload_once", set()).add(self.config_entry.entry_id)
                    else:
                        self.hass.data.setdefault(DOMAIN, {}).setdefault("_delay_reload_once", set()).add(self.config_entry.entry_id)
                except Exception:
                    pass

                try:
                    self.hass.config_entries.async_update_entry(self.config_entry, options=dict(pending))
                except Exception as err:  # noqa: BLE001
                    _LOGGER.warning("Failed to persist options (entity overrides) for entry %s: %s", self.config_entry.entry_id, err)

                if edit_another:
                    return await self.async_step_entity_override_select()

                # Close the options flow (options already saved above).
                return self.async_create_entry(title="", data=pending)

        device_class_options = [
            selector.SelectOptionDict(value="", label="(no device class)"),
            selector.SelectOptionDict(value="temperature", label="Temperature"),
            selector.SelectOptionDict(value="humidity", label="Humidity"),
            selector.SelectOptionDict(value="pressure", label="Pressure"),
            selector.SelectOptionDict(value="power", label="Power"),
            selector.SelectOptionDict(value="energy", label="Energy"),
        ]

        state_class_options = [
            selector.SelectOptionDict(value="", label="(no state class)"),
            selector.SelectOptionDict(value="measurement", label="Measurement"),
            selector.SelectOptionDict(value="total", label="Total"),
            selector.SelectOptionDict(value="total_increasing", label="Total increasing"),
        ]

        polling_options = [
            selector.SelectOptionDict(value=POLLING_MODE_AUTOMATIC, label="Automatic"),
            selector.SelectOptionDict(value=POLLING_MODE_FAST, label="Fast"),
            selector.SelectOptionDict(value=POLLING_MODE_SLOW, label="Slow"),
        ]

        schema_dict: Dict[Any, Any] = {
            vol.Optional("clear", default=False): selector.BooleanSelector(),
            vol.Optional(CONF_POLLING_MODE, default=str(cur_polling)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=polling_options,
                    multiple=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
        }

        # Only show unit/device_class overrides where they make sense today.
        if is_sensor or is_number:
            schema_dict[vol.Optional(CONF_DEVICE_CLASS, default=str(cur_dc or ""))] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=device_class_options,
                    multiple=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
            schema_dict[vol.Optional(CONF_UNIT, default=str(cur_unit or ""))] = selector.TextSelector()

        if is_sensor:
            schema_dict[vol.Optional(CONF_STATE_CLASS, default=str(cur_sc or ""))] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=state_class_options,
                    multiple=False,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        if is_number:
            # Only allow min/max editing if we have sensible bundle bounds.
            try:
                bmn = float(bundle_min) if bundle_min is not None else None
                bmx = float(bundle_max) if bundle_max is not None else None
            except Exception:
                bmn = bmx = None

            if bmn is not None and bmx is not None and bmx > bmn:
                schema_dict[vol.Optional(CONF_MIN, default=bmn if cur_min is None else float(cur_min))] = selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=bmn,
                        max=bmx,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
                schema_dict[vol.Optional(CONF_MAX, default=bmx if cur_max is None else float(cur_max))] = selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=bmn,
                        max=bmx,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )

            # Step can always be overridden; keep it positive.
            default_step = 1.0
            if cur_step not in (None, ""):
                try:
                    default_step = float(cur_step)
                except Exception:
                    default_step = 1.0
            schema_dict[vol.Optional(CONF_STEP, default=default_step)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.000001,
                    max=1_000_000,
                    step=0.1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        schema_dict[vol.Optional("edit_another", default=True)] = selector.BooleanSelector()
        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="entity_override_edit", data_schema=schema, errors=errors)

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
