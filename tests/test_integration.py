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
GROUP = "light.govee_h7094"
LIGHT_1 = "light.govee_h7094_light_1"
LIGHT_2 = "light.govee_h7094_light_2"
ALL = [0, 1, 2, 3]


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


async def call(hass, service, entity_id, **data):
    await hass.services.async_call(LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)


async def test_group_lists_members(hass, sent):
    await setup_entry(hass)
    state = hass.states.get(GROUP)
    assert state.state == STATE_OFF
    assert state.attributes[ATTR_ENTITY_ID] == [f"light.govee_h7094_light_{n}" for n in range(1, 5)]


async def test_group_off_is_one_frame(hass, sent):
    await setup_entry(hass)
    await call(hass, "turn_on", GROUP)
    await call(hass, "turn_off", GROUP)
    assert sent[-1] == [off_frame(ALL)]
    for n in range(1, 5):
        assert hass.states.get(f"light.govee_h7094_light_{n}").state == STATE_OFF


async def test_group_colour_applies_to_all(hass, sent):
    await setup_entry(hass)
    await call(hass, "turn_on", GROUP, **{ATTR_RGB_COLOR: (0, 255, 0), ATTR_BRIGHTNESS: 255})
    assert sent == [[color_frame(ALL, (0, 255, 0)), brightness_frame(ALL, 100)]]
    for n in range(1, 5):
        state = hass.states.get(f"light.govee_h7094_light_{n}")
        assert state.state == STATE_ON
        assert state.attributes[ATTR_RGB_COLOR] == (0, 255, 0)


async def test_group_on_restores_each_lights_own_colour(hass, sent):
    await setup_entry(hass)
    await call(hass, "turn_on", LIGHT_1, **{ATTR_RGB_COLOR: (255, 0, 0)})
    await call(hass, "turn_on", LIGHT_2, **{ATTR_RGB_COLOR: (0, 0, 255), ATTR_BRIGHTNESS: 128})
    await call(hass, "turn_off", GROUP)
    await call(hass, "turn_on", GROUP)
    # Lights 3 and 4 share the default white and 100%, so they share frames.
    assert sent[-1] == [
        color_frame([0], (255, 0, 0)),
        color_frame([1], (0, 0, 255)),
        color_frame([2, 3], (255, 255, 255)),
        brightness_frame([0, 2, 3], 100),
        brightness_frame([1], 50),
    ]
    assert hass.states.get(LIGHT_2).attributes[ATTR_RGB_COLOR] == (0, 0, 255)


async def test_group_follows_members(hass, sent):
    await setup_entry(hass)
    await call(hass, "turn_on", LIGHT_2, **{ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 100})
    state = hass.states.get(GROUP)
    assert state.state == STATE_ON  # any member on
    assert state.attributes[ATTR_RGB_COLOR] == (255, 0, 0)  # only lit members count
    assert state.attributes[ATTR_BRIGHTNESS] == 100
    await call(hass, "turn_on", LIGHT_1, **{ATTR_RGB_COLOR: (0, 0, 255), ATTR_BRIGHTNESS: 200})
    state = hass.states.get(GROUP)
    assert state.attributes[ATTR_RGB_COLOR] == (128, 0, 128)
    assert state.attributes[ATTR_BRIGHTNESS] == 150
    await call(hass, "turn_off", LIGHT_1)
    await call(hass, "turn_off", LIGHT_2)
    assert hass.states.get(GROUP).state == STATE_OFF
