"""Constants for Govee LAN Segments."""

DOMAIN = "govee_segments"

CONF_SKU = "sku"
CONF_DEVICE_ID = "device_id"

# Holds UDP 4002 too; both can't run on one host.
CONFLICTING_DOMAIN = "govee_light_local"
ISSUE_PORT_CONFLICT = "port_conflict"
