# ---------------------------------------------------------------------------
# const.py – Shared constants for the Fritz!Box Mesh integration
#
# All string keys and default values live here so that a single change
# propagates everywhere: config_flow, coordinator, sensors, etc.
# ---------------------------------------------------------------------------

# The integration's unique identifier.  Must match the `domain` field in
# manifest.json and the folder name under custom_components/.
DOMAIN = "fritzmesh"

# ── Config-entry data keys ──────────────────────────────────────────────────
# These strings are used as keys when reading/writing the ConfigEntry `data`
# dict.  They also match the field names in the config-flow schema so that
# `user_input` can be passed directly to the entry without remapping.

CONF_HOST     = "host"          # Fritz!Box hostname or IP address
CONF_PORT     = "port"          # TR-064 service port (HTTP: 49000, HTTPS: 49443)
CONF_USERNAME = "username"      # Fritz!Box web-UI username (may be empty)
CONF_PASSWORD = "password"      # Fritz!Box web-UI password
CONF_USE_TLS  = "use_tls"       # Whether to use HTTPS for the TR-064 connection
CONF_POLL_INTERVAL = "poll_interval"  # How often (seconds) to refresh topology
CONF_DEBUG_MODE = "debug_mode"  # How to expose raw mesh JSON for troubleshooting
CONF_DEBUG_USE_JSON = "debug_use_json"  # Use local debug JSON file instead of TR-064
CONF_DEBUG_JSON_PATH = "debug_json_path"  # Path to debug mesh JSON file

# ── Defaults ────────────────────────────────────────────────────────────────
# Sensible defaults shown in the config-flow form.  192.168.178.1 is the
# factory default IP of every AVM Fritz!Box sold in German-speaking markets.
# Port 49000 is the standard TR-064 HTTP port; TLS would use 49443.

DEFAULT_HOST          = "192.168.178.1"
DEFAULT_PORT          = 49000   # Plain HTTP TR-064 port
DEFAULT_USE_TLS       = False   # Most home setups don't enable TLS on TR-064
DEFAULT_POLL_INTERVAL = 60      # Refresh every 60 seconds (1 minute)
DEFAULT_DEBUG_MODE    = "off"
DEFAULT_DEBUG_USE_JSON = False
DEFAULT_DEBUG_JSON_PATH = ""

DEBUG_MODE_OFF = "off"
DEBUG_MODE_LOG = "log"
DEBUG_MODE_FILE = "file"
DEBUG_MODE_LOG_AND_FILE = "log_and_file"
DEBUG_MODE_CHOICES = [
    DEBUG_MODE_OFF,
    DEBUG_MODE_LOG,
    DEBUG_MODE_FILE,
    DEBUG_MODE_LOG_AND_FILE,
]
