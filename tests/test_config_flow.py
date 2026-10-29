"""Config flow tests: discovery, manual entry, errors, reconfigure."""
from unittest.mock import AsyncMock, patch

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee_segments.const import CONF_DEVICE_ID, CONF_SKU, DOMAIN

from .conftest import DEVICE_ID, HOST, SCAN

ENTRY_DATA = {CONF_HOST: HOST, CONF_SKU: "H7094", CONF_DEVICE_ID: DEVICE_ID}


async def start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})


async def test_discovered_device(hass, fake_listener):
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": DEVICE_ID})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Govee H7094"
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == DEVICE_ID


async def test_nothing_discovered_goes_to_manual(hass, fake_listener):
    fake_listener.devices = {}
    result = await start(hass)
    assert result["step_id"] == "manual"


async def test_manual_entry(hass, fake_listener):
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"device": "manual"})
    assert result["step_id"] == "manual"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: f" {HOST} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == ENTRY_DATA


async def test_manual_errors(hass, fake_listener):
    fake_listener.devices = {}
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "not-an-ip"})
    assert result["errors"] == {CONF_HOST: "invalid_host"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: HOST})
    assert result["errors"] == {"base": "cannot_connect"}
    fake_listener.devices = {HOST: {**SCAN, "sku": "H6199"}}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: HOST})
    assert result["errors"] == {"base": "unsupported"}


async def test_already_configured_is_hidden_then_aborts(hass, fake_listener):
    MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, data=ENTRY_DATA).add_to_hass(hass)
    result = await start(hass)
    assert result["step_id"] == "manual"  # the only device found is already set up
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: HOST})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_port_in_use(hass, fake_listener):
    with patch(
        "custom_components.govee_segments.config_flow.async_get_listener",
        AsyncMock(side_effect=OSError(98, "Address in use")),
    ):
        result = await start(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "port_in_use"


async def test_reconfigure(hass, fake_listener):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DEVICE_ID, version=2, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    fake_listener.devices = {"192.0.2.99": SCAN, "192.0.2.50": {**SCAN, "device": "OTHER"}}
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "192.0.2.50"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HOST: "192.0.2.99"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.0.2.99"
