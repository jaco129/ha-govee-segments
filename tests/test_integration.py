"""Home Assistant tests: config flow, entities, and the frames each service call sends.

Needs pytest-homeassistant-custom-component. Run with: pytest tests
"""
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.light import ATTR_BRIGHTNESS, ATTR_RGB_COLOR, DOMAIN as LIGHT_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_OFF, STATE_ON
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee_segments.const import CONF_SKU, DOMAIN
from custom_components.govee_segments.protocol import brightness_frame, color_frame, off_frame

HOST = "192.0.2.10"
LIGHT_2 = "light.govee_h7094_light_2"


@pytest.fixture
def sent():
    """Replace the UDP sender; collect the frame lists it would have sent."""
    frames: list[list[bytes]] = []
    with (
        patch("custom_components.govee_segments.GoveeSender.async_start"),
        patch("custom_components.govee_segments.GoveeSender.send_frames", side_effect=frames.append),
    ):
        yield frames


async def setup_entry(hass) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN, title="Govee H7094", unique_id=HOST, data={CONF_HOST: HOST, CONF_SKU: "H7094"}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_creates_entry(hass, sent):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: f" {HOST} ", CONF_SKU: "H7094"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Govee H7094"
    assert result["data"] == {CONF_HOST: HOST, CONF_SKU: "H7094"}


async def test_config_flow_rejects_bad_host(hass, sent):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "not-an-ip", CONF_SKU: "H7094"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_HOST: "invalid_host"}


async def test_config_flow_aborts_duplicate(hass, sent):
    await setup_entry(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_SKU: "H7094"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_four_lights_created(hass, sent):
    await setup_entry(hass)
    for n in range(1, 5):
        state = hass.states.get(f"light.govee_h7094_light_{n}")
        assert state is not None
        assert state.state == STATE_OFF
        assert state.attributes["assumed_state"] is True
    assert sent == []  # setting up must not touch the lights


async def test_turn_on_sends_colour_then_brightness(hass, sent):
    await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN,
        "turn_on",
        {ATTR_ENTITY_ID: LIGHT_2, ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 128},
        blocking=True,
    )
    assert sent == [[color_frame([1], (255, 0, 0)), brightness_frame([1], 50)]]
    state = hass.states.get(LIGHT_2)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (255, 0, 0)
    assert state.attributes[ATTR_BRIGHTNESS] == 128


async def test_turn_off_then_on_restores_colour(hass, sent):
    await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT_2, ATTR_RGB_COLOR: (0, 0, 255)}, blocking=True
    )
    await hass.services.async_call(LIGHT_DOMAIN, "turn_off", {ATTR_ENTITY_ID: LIGHT_2}, blocking=True)
    assert sent[-1] == [off_frame([1])]
    assert hass.states.get(LIGHT_2).state == STATE_OFF
    await hass.services.async_call(LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT_2}, blocking=True)
    assert sent[-1] == [color_frame([1], (0, 0, 255)), brightness_frame([1], 100)]


async def test_lowest_brightness_stays_on(hass, sent):
    await setup_entry(hass)
    await hass.services.async_call(
        LIGHT_DOMAIN, "turn_on", {ATTR_ENTITY_ID: LIGHT_2, ATTR_BRIGHTNESS: 1}, blocking=True
    )
    assert sent[-1][1] == brightness_frame([1], 1)


async def test_unload(hass, sent):
    entry = await setup_entry(hass)
    with patch("custom_components.govee_segments.GoveeSender.close") as close:
        assert await hass.config_entries.async_unload(entry.entry_id)
    close.assert_called_once()
