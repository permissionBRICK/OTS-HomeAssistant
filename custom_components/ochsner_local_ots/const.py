DOMAIN = "ochsner_local_ots"

DEFAULT_PORT = 80
DEFAULT_USERNAME = "JSON"
DEFAULT_PASSWORD = "SBTAdmin!"
DEFAULT_PIN = "7659"
DEFAULT_SCAN_INTERVAL_SEC = 30

# Gated polling: poll again when a per-OA counter reaches this threshold.
# Used by Automatic and Slow modes.
DEFAULT_POLLING_THRESHOLD = 20

# Delay (seconds) before reloading the integration after saving options/overrides.
# This helps Home Assistant flush .storage updates before any restart/shutdown.
DELAY_RELOAD_SEC = 3

# Sensors whose name contains any of these keywords are added disabled by default.
# This only affects the first time the entity is created in HA (entity registry).
DISABLE_BY_DEFAULT_SENSOR_KEYWORDS = [
	"Engy",
	"ZH",
	"JAZ_",
	"schreitung",
	"CprOpr",
	"Rt-Sp",
]

CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PIN = "pin"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_POLLING_THRESHOLD = "polling_threshold"
CONF_SENSORS = "sensors"
CONF_BINARY_SENSORS = "binary_sensors"
CONF_NUMBERS = "numbers"
CONF_SELECTS = "selects"
CONF_TEXTS = "texts"

# Multi-controller support (UI flow can add multiple plants/heatpumps under one config entry)
CONF_CONTROLLERS = "controllers"
CONF_PLANT_KEY = "plant_key"
CONF_PLANT_NAME = "plant_name"
CONF_CONFIG_ID = "config_id"
CONF_SITE_ID = "site_id"
CONF_BUNDLE_STORAGE_KEY = "bundle_storage_key"

# Options
CONF_RESCAN_ON_START = "rescan_on_start"
CONF_RESCAN_NOW = "rescan_now"
CONF_REDOWNLOAD_BUNDLE = "redownload_bundle"

# Localization (used during onboarding entity generation)
CONF_LANGUAGE = "language"

# Per-controller device metadata
CONF_DEVICE_MODEL = "device_model"

CONF_NAME = "name"
CONF_UUID = "uuid"
CONF_ID = "id"  # genericJsonId (OA)
CONF_READ_ID = "read_id"
CONF_WRITE_ID = "write_id"
CONF_OPTIONS = "options"
CONF_UNIT = "unit"

CONF_VALUE_MAP = "value_map"

# Per-entity UI overrides (stored in config_entry.options)
# Keyed by entity unique_id string.
CONF_ENTITY_OVERRIDES = "entity_overrides"
CONF_DEVICE_CLASS = "device_class"
CONF_STATE_CLASS = "state_class"

# Per-entity polling behavior override (stored in entity_overrides)
CONF_POLLING_MODE = "polling_mode"
POLLING_MODE_AUTOMATIC = "automatic"
POLLING_MODE_FAST = "fast"
POLLING_MODE_SLOW = "slow"

CONF_MIN = "min"
CONF_MAX = "max"
CONF_STEP = "step"

# Bundle-provided bounds for writable numbers (used to constrain UI overrides)
CONF_BUNDLE_MIN = "bundle_min"
CONF_BUNDLE_MAX = "bundle_max"

# Optional grouping metadata (used by UI onboarding auto-generator)
CONF_HEATING_CIRCUIT_UID = "heating_circuit_uid"
CONF_HEATING_CIRCUIT_NAME = "heating_circuit_name"
