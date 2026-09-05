from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.helpers import entity_registry as er, selector

from .const import (
    CONF_BINARY_SENSORS,
    CONF_BUNDLE_STORAGE_KEY,
    CONF_CATALOG_VERSION,
    CONF_CONTROLLERS,
    DISCOVERY_SOURCE_LOCAL,
    CONF_BUNDLE_MAX,
    CONF_BUNDLE_MIN,
    CONF_HEATING_CIRCUIT_NAME,
    CONF_DEVICE_CLASS,
    CONF_DEVICE_MODEL,
    CONF_DISCOVERY_SOURCE,
    CONF_ENTITY_OVERRIDES,
    CONF_ID,
    CONF_IDENTITY_KEY,
    CONF_MAC_ADDRESS,
    CONF_LANGUAGE,
    CONF_LOCAL_SCAN_NOW,
    CONF_MAX,
    CONF_MIN,
    CONF_NUMBERS,
    CONF_PIN,
    CONF_PLANT_NAME,
    CONF_SCAN_INTERVAL,
    CONF_POLLING_THRESHOLD,
    CONF_MAX_IDS_PER_READ_REQUEST,
    CONF_SELECTS,
    CONF_SERIAL_NUMBER,
    CONF_SW_VERSION,
    CONF_SWITCHES,
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
    DEFAULT_MAX_IDS_PER_READ_REQUEST,
    DEFAULT_USERNAME,
    DELAY_RELOAD_SEC,
    DOMAIN,
)

from .bundle_refresh import async_redownload_bundles_and_merge
from .catalog import explicit_language
from .autodiscovery import (
    async_find_controllers, async_update_address, connection_from_controller,
    controllers_from_entry,
)
from .network_discovery import ControllerIdentity, async_probe, identity_key, serial_key
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)


# Only used by the options-flow bundle re-download (legacy path).
CONF_OTS_USER = "ots_user"
CONF_OTS_PASS = "ots_pass"
CONF_LOCAL_IP = "local_ip"

# Options-flow language choices: follow Home Assistant, Deutsch, English.
LANGUAGE_AUTO = "auto"
LANGUAGE_OPTIONS = (LANGUAGE_AUTO, "de", "en")


class ClimatixGenericConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return ClimatixGenericOptionsFlowHandler(config_entry)

    def __init__(self) -> None:
        # Onboarding is IP-only: default Climatix credentials/PIN are used
        # automatically; the advanced step only exists for non-default plants.
        self._offer_advanced: bool = False
        self._last_host: str = ""
        self._scanned_entities: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._scan_summary: Dict[str, Any] = {}
        self._catalog_version: str = ""
        self._conn: Dict[str, Any] = {}
        # Plant identity read from the controller during the scan (pump-first
        # naming): model type names the device, serial identifies it.
        self._plant_model: Optional[str] = None
        self._plant_serial: Optional[str] = None
        self._plant_sw_version: Optional[str] = None
        self._discovered: ControllerIdentity | None = None
        self._network_task: asyncio.Task | None = None
        self._network_results: list[ControllerIdentity] = []

    def _host_already_configured(self, host: str) -> bool:
        host = host.strip()
        for entry in self._async_current_entries():
            data = entry.data or {}
            hosts = set()
            if isinstance(data.get(CONF_CONTROLLERS), list):
                for c in data[CONF_CONTROLLERS]:
                    if isinstance(c, dict) and c.get(CONF_HOST):
                        hosts.add(str(c[CONF_HOST]).strip())
            if data.get(CONF_HOST):
                hosts.add(str(data[CONF_HOST]).strip())
            if host in hosts:
                return True
        return False

    async def _async_validate_and_scan(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        pin: str,
    ) -> Optional[str]:
        """Run the read-only local catalog scan; return an error key or None."""

        from aiohttp import ClientResponseError

        from .local_scan import async_scan_controller

        try:
            entities, scan, catalog = await async_scan_controller(
                self.hass,
                host=host,
                port=port,
                username=username,
                password=password,
                pin=pin,
            )
        except ClientResponseError as err:
            # Never log the exception itself: aiohttp errors embed the full
            # request URL, which includes the PIN query parameter.
            _LOGGER.debug("Local scan HTTP error for %s: status=%s", host, err.status)
            return "auth_failed" if err.status in (401, 403) else "cannot_connect"
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Local scan failed for %s: %s", host, type(err).__name__)
            return "cannot_connect"

        if not scan.values:
            # Endpoint answered but no catalog point was readable: usually a
            # non-default PIN or credentials -> point the user at the advanced
            # settings.
            return "probe_failed"

        if self._discovered and scan.plant_serial != self._discovered.serial:
            return "identity_changed"

        self._scanned_entities = entities
        self._catalog_version = catalog.catalog_version
        self._plant_model = scan.plant_model
        self._plant_serial = scan.plant_serial
        self._plant_sw_version = scan.plant_sw_version
        self._conn = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_USERNAME: username,
            CONF_PASSWORD: password,
            CONF_PIN: pin,
        }
        self._scan_summary = {
            key: len(entities.get(key, [])) for key in ("sensors", "binary_sensors", "numbers", "selects", "texts", "switches")
        }
        _LOGGER.info(
            "Local discovery for %s found %d readable datapoints (%s), circuits=%s",
            host,
            len(scan.values),
            self._scan_summary,
            scan.circuit_names,
        )
        return None

    def _create_local_entry(self):
        host = str(self._conn.get(CONF_HOST) or "")
        # Pump-first naming: the plant/model type read from the controller
        # ("Anlagentyp", e.g. "AIRHAWK518C11A") names the HA device.
        label = self._plant_serial or host
        title = f"{self._plant_model or 'Ochsner'} ({label})"
        entities = self._scanned_entities or {}
        controller: Dict[str, Any] = {
            CONF_IDENTITY_KEY: serial_key(self._plant_serial) if self._plant_serial else host,
            CONF_PLANT_NAME: title,
            CONF_DEVICE_MODEL: self._plant_model or "Climatix",
            CONF_SERIAL_NUMBER: self._plant_serial,
            CONF_SW_VERSION: self._plant_sw_version,
            CONF_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LOCAL,
            CONF_CATALOG_VERSION: self._catalog_version,
            **self._conn,
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_SEC,
            CONF_SENSORS: entities.get("sensors", []),
            CONF_BINARY_SENSORS: entities.get("binary_sensors", []),
            CONF_NUMBERS: entities.get("numbers", []),
            CONF_SELECTS: entities.get("selects", []),
            CONF_TEXTS: entities.get("texts", []),
            CONF_SWITCHES: entities.get("switches", []),
        }
        if self._discovered and self._discovered.mac:
            controller[CONF_MAC_ADDRESS] = self._discovered.mac
        return self.async_create_entry(title=title, data={CONF_CONTROLLERS: [controller]})

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None):
        if user_input is not None:
            return await self.async_step_manual(user_input)
        return self.async_show_menu(step_id="user", menu_options=["scan", "manual"])

    async def async_step_scan(self, user_input=None):
        if self._network_task is None:
            self._network_task = self.hass.async_create_task(
                async_find_controllers(self.hass, connection_from_controller({})),
                "Ochsner network discovery",
            )
        if not self._network_task.done():
            return self.async_show_progress(
                step_id="scan", progress_action="scan_network", progress_task=self._network_task
            )
        try:
            self._network_results = self._network_task.result()
        except Exception:
            self._network_results = []
        self._network_task = None
        return self.async_show_progress_done(next_step_id="select_device")

    async def async_step_select_device(self, user_input=None):
        if not self._network_results:
            return self.async_abort(reason="no_devices_found")
        if user_input is not None:
            found = next((item for item in self._network_results if item.host == user_input.get(CONF_HOST)), None)
            if found:
                return await self._async_offer_discovery(found)
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required(CONF_HOST): vol.In({
                item.host: f"{item.model} ({item.host}, {item.serial})" for item in self._network_results
            })}),
        )

    async def async_step_dhcp(self, discovery_info):
        # Hostname/OUI are only hints. Read the Ochsner application identity.
        conn = connection_from_controller({CONF_HOST: discovery_info.ip})
        for entry in self._async_current_entries():
            for ctrl in controllers_from_entry(entry):
                mac = str(ctrl.get(CONF_MAC_ADDRESS) or "").replace(":", "").lower()
                if ctrl.get(CONF_HOST) == discovery_info.ip or (mac and mac == discovery_info.macaddress.lower()):
                    conn = connection_from_controller({**ctrl, CONF_HOST: discovery_info.ip})
        found = await async_probe(async_get_clientsession(self.hass), conn)
        if found is None:
            return self.async_abort(reason="not_ochsner")
        return await self._async_offer_discovery(found)

    async def _async_offer_discovery(self, found):
        self._discovered = found
        for entry in self._async_current_entries():
            for ctrl in controllers_from_entry(entry):
                if ctrl.get(CONF_SERIAL_NUMBER) == found.serial:
                    async_update_address(self.hass, entry, identity_key(ctrl), found, reload=True)
                    return self.async_abort(reason="already_configured")
                if ctrl.get(CONF_HOST) == found.host:
                    return self.async_abort(reason="already_configured")
        await self.async_set_unique_id(f"{DOMAIN}:{serial_key(found.serial)}")
        self._abort_if_unique_id_configured()
        self.context["title_placeholders"] = {"name": f"{found.model} ({found.serial})"}
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(self, user_input=None):
        if self._discovered is None:
            return self.async_abort(reason="not_ochsner")
        errors = {}
        if user_input is not None:
            conn = connection_from_controller({CONF_HOST: self._discovered.host})
            found = await async_probe(async_get_clientsession(self.hass), conn)
            if found is None:
                errors["base"] = "cannot_connect"
            elif found.serial != self._discovered.serial:
                errors["base"] = "identity_changed"
            else:
                error = await self._async_validate_and_scan(
                    host=conn.host, port=conn.port, username=conn.username,
                    password=conn.password, pin=conn.pin,
                )
                if error is None:
                    return await self._async_finish_local_entry()
                errors["base"] = error
        self._set_confirm_only()
        return self.async_show_form(
            step_id="discovery_confirm", data_schema=vol.Schema({}), errors=errors,
            description_placeholders={"model": self._discovered.model,
                                      "host": self._discovered.host,
                                      "serial": self._discovered.serial},
        )

    async def _async_finish_local_entry(self):
        if self._plant_serial:
            for entry in self._async_current_entries():
                for ctrl in controllers_from_entry(entry):
                    if ctrl.get(CONF_SERIAL_NUMBER) == self._plant_serial:
                        found = ControllerIdentity(
                            self._conn[CONF_HOST], self._plant_serial,
                            self._plant_model or "Climatix",
                            self._discovered.mac if self._discovered else None,
                        )
                        async_update_address(self.hass, entry, identity_key(ctrl), found, reload=True)
                        return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(f"{DOMAIN}:{serial_key(self._plant_serial)}")
            self._abort_if_unique_id_configured()
        return self._create_local_entry()

    async def async_step_manual(self, user_input: Optional[Dict[str, Any]] = None):
        """IP-only onboarding: local catalog scan, no cloud account, no bundle."""

        errors: Dict[str, str] = {}
        if user_input is not None:
            if bool(user_input.get("advanced")):
                self._last_host = str(user_input.get(CONF_LOCAL_IP) or "").strip()
                return await self.async_step_advanced()

            host = str(user_input.get(CONF_LOCAL_IP) or "").strip()
            self._last_host = host
            if not host:
                errors["base"] = "invalid_host"
            elif self._host_already_configured(host):
                return self.async_abort(reason="already_configured")
            else:
                await self.async_set_unique_id(f"{DOMAIN}:local:{host}"[:255])
                self._abort_if_unique_id_configured()
                error = await self._async_validate_and_scan(
                    host=host,
                    port=DEFAULT_PORT,
                    username=DEFAULT_USERNAME,
                    password=DEFAULT_PASSWORD,
                    pin=DEFAULT_PIN,
                )
                if error is None:
                    return await self._async_finish_local_entry()
                errors["base"] = error
                # Offer the advanced (credentials/PIN) settings after a failure.
                self._offer_advanced = True

        schema_dict: Dict[Any, Any] = {
            vol.Required(CONF_LOCAL_IP, default=self._last_host): str,
        }
        if self._offer_advanced:
            schema_dict[vol.Optional("advanced", default=False)] = selector.BooleanSelector()

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_advanced(self, user_input: Optional[Dict[str, Any]] = None):
        """Optional overrides for the rare plant without default credentials."""

        errors: Dict[str, str] = {}
        if user_input is not None:
            host = str(user_input.get(CONF_LOCAL_IP) or "").strip()
            self._last_host = host
            if not host:
                errors["base"] = "invalid_host"
            elif self._host_already_configured(host):
                return self.async_abort(reason="already_configured")
            else:
                await self.async_set_unique_id(f"{DOMAIN}:local:{host}"[:255])
                self._abort_if_unique_id_configured()
                error = await self._async_validate_and_scan(
                    host=host,
                    port=int(user_input.get(CONF_PORT) or DEFAULT_PORT),
                    username=str(user_input.get(CONF_USERNAME) or DEFAULT_USERNAME),
                    password=str(user_input.get(CONF_PASSWORD) or DEFAULT_PASSWORD),
                    pin=str(user_input.get(CONF_PIN) or DEFAULT_PIN),
                )
                if error is None:
                    return await self._async_finish_local_entry()
                errors["base"] = error

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCAL_IP, default=self._last_host): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
                vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
                vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                vol.Optional(CONF_PIN, default=DEFAULT_PIN): str,
            }
        )
        return self.async_show_form(step_id="advanced", data_schema=schema, errors=errors)

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
            CONF_SWITCHES: list(user_input.get(CONF_SWITCHES, []) or []),
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
        # The displayed language must MATCH the effective behavior: without
        # a stored option the legacy per-controller CONF_LANGUAGE applies,
        # so that is what the form shows (never "follow HA" while the
        # runtime would resolve the controller language).
        language_cur = str(existing_options.get(CONF_LANGUAGE) or "")
        if language_cur not in LANGUAGE_OPTIONS:
            language_cur = self._effective_language_default()
        current = int(existing_options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC))
        poll_threshold_cur = int(existing_options.get(CONF_POLLING_THRESHOLD, DEFAULT_POLLING_THRESHOLD))
        max_ids_cur = int(existing_options.get(CONF_MAX_IDS_PER_READ_REQUEST, DEFAULT_MAX_IDS_PER_READ_REQUEST))
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
                        try:
                            max_ids = int(user_input.get(CONF_MAX_IDS_PER_READ_REQUEST, max_ids_cur))
                        except Exception:  # noqa: BLE001
                            errors["base"] = "invalid_max_ids"
                            max_ids = max_ids_cur

                        if not errors and (max_ids < 1 or max_ids > 200):
                            errors["base"] = "invalid_max_ids"

                        if errors:
                            pass
                        else:
                            out = dict(existing_options)
                            out[CONF_SCAN_INTERVAL] = interval
                            out[CONF_POLLING_THRESHOLD] = poll_threshold
                            out[CONF_MAX_IDS_PER_READ_REQUEST] = max_ids
                            out[CONF_RESCAN_ON_START] = bool(user_input.get(CONF_RESCAN_ON_START, rescan_on_start))

                            # Language of entity names / enum labels: every
                            # SUBMITTED value is stored explicitly ("auto"
                            # included, so it really follows HA) — what the
                            # form showed is what the runtime does. Only a
                            # never-submitted form keeps no key (legacy
                            # per-controller CONF_LANGUAGE fallback, which
                            # is also the displayed default then).
                            language_sel = str(user_input.get(CONF_LANGUAGE) or language_cur)
                            if language_sel not in LANGUAGE_OPTIONS:
                                language_sel = language_cur
                            out[CONF_LANGUAGE] = language_sel

                            # Ensure entity overrides always survive option updates.
                            if CONF_ENTITY_OVERRIDES not in out:
                                out[CONF_ENTITY_OVERRIDES] = {}

                            # One-shot flag: if enabled, setup will rescan then clear it.
                            if bool(user_input.get(CONF_RESCAN_NOW, False)):
                                out[CONF_RESCAN_NOW] = True

                            # One-shot flag: run the accountless local catalog
                            # scan on next setup and merge missing entities.
                            if bool(user_input.get(CONF_LOCAL_SCAN_NOW, False)):
                                out[CONF_LOCAL_SCAN_NOW] = True

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
                    CONF_LANGUAGE,
                    default=language_cur,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(LANGUAGE_OPTIONS),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        translation_key="language",
                    )
                ),
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
                vol.Optional(
                    CONF_MAX_IDS_PER_READ_REQUEST,
                    default=max_ids_cur,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=200,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional(CONF_RESCAN_ON_START, default=rescan_on_start): selector.BooleanSelector(),
                vol.Optional(CONF_RESCAN_NOW, default=rescan_now_default): selector.BooleanSelector(),
                vol.Optional(CONF_LOCAL_SCAN_NOW, default=False): selector.BooleanSelector(),
            }
        )
        # The bundle re-download only makes sense for entries created from a
        # cloud bundle; local-scan entries never had one.
        if self._has_bundle_controllers():
            schema = schema.extend({vol.Optional(CONF_REDOWNLOAD_BUNDLE, default=redownload_default): selector.BooleanSelector()})
        schema = schema.extend({vol.Optional("configure_entities", default=edit_entities_default): selector.BooleanSelector()})

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _effective_language_default(self) -> str:
        """What the runtime resolves WITHOUT a stored option: the first
        controller's explicit CONF_LANGUAGE (legacy bundle entries store
        e.g. "DE"), else follow-HA."""
        controllers = self.config_entry.data.get(CONF_CONTROLLERS)
        if isinstance(controllers, list):
            for ctrl in controllers:
                if isinstance(ctrl, dict):
                    lang = explicit_language(ctrl.get(CONF_LANGUAGE))
                    if lang:
                        return lang
        return LANGUAGE_AUTO

    def _has_bundle_controllers(self) -> bool:
        raw = self.config_entry.data.get(CONF_CONTROLLERS)
        if not isinstance(raw, list):
            # Legacy single-controller (YAML import) entries have no bundles.
            return False
        return any(isinstance(c, dict) and c.get(CONF_BUNDLE_STORAGE_KEY) for c in raw)

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
            host = identity_key(ctrl)
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
            host = identity_key(ctrl)
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
            host = identity_key(ctrl)
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
            host = identity_key(ctrl)
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
            host = identity_key(ctrl)
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
