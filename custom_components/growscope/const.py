"""Constants for the GrowScope integration."""

DOMAIN = "growscope"

# Well-known hostname of a locally-built add-on install. Store installs get a
# repo-hash prefix instead - the config flow lets the user override.
DEFAULT_URL = "http://local-growscope:8099"

CONF_URL = "url"
UPDATE_INTERVAL_SECONDS = 60
