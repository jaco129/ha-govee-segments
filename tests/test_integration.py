"""Home Assistant tests: setup, migration, lights, reconciling, diagnostics.

Needs pytest-homeassistant-custom-component. Run with: pytest tests
"""
from unittest.mock import AsyncMock, patch

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_MODE,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_EFFECT_LIST,
    ATTR_RGB_COLOR,
    DOMAIN as LIGHT_DOMAIN,
    ColorMode,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_HOST, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr, entity_registry as er, issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee_segments.const import CONF_DEVICE_ID, CONF_SKU, DOMAIN
from custom_components.govee_segments.diagnostics import async_get_config_entry_diagnostics
from custom_components.govee_segments.light import load_scenes
from custom_components.govee_segments.protocol import (
    brightness_frame,
    color_frame,
    off_frame,
    scene_frames,
)

from .conftest import DEVICE_ID, HOST, STATUS_ON

GROUP = "light.govee_h7094"
LIGHT_1 = "light.govee_h7094_light_1"
LIGHT_2 = "light.govee_h7094_light_2"
ALL = [0, 1, 2, 3]
TURN_ON = ("turn", {"value": 1})
TURN_OFF = ("turn", {"value": 0})


def make_entry(**overrides) -> MockConfigEntry:
    kwargs = dict(
        domain=DOMAIN,
        title="Govee H7094",
        version=2,
        unique_id=DEVICE_ID,
        data={CONF_HOST: HOST, CONF_SKU: "H7094", CONF_DEVICE_ID: DEVICE_ID},
    )
    kwargs.update(overrides)
    return MockConfigEntry(**kwargs)


async def setup(hass, entry=None) -> MockConfigEntry:
    entry = entry or make_entry()
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def call(hass, service, entity_id, **data):
    await hass.services.async_call(LIGHT_DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)


async def poll(hass, entry):
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
def no_quiet_window():
    with patch("custom_components.govee_segments.light.QUIET_SECONDS", 0):
        yield


# --- setup -------------------------------------------------------------------


async def test_setup_creates_device_and_lights(hass, fake_listener):
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.identifiers == {(DOMAIN, DEVICE_ID)}
    assert device.sw_version == "1.06.24"
    assert device.hw_version == "1.04.01"
    # devStatus says the fixture is on, and with no saved state each light is assumed lit.
    assert hass.states.get(GROUP).state == STATE_ON
    for n in range(1, 5):
        assert hass.states.get(f"light.govee_h7094_light_{n}").state == STATE_ON
    for entity_id in (GROUP, LIGHT_1):
        # Not assumed: the UI shows a toggle instead of separate on/off buttons.
        assert "assumed_state" not in hass.states.get(entity_id).attributes
    assert fake_listener.take() == []  # setting up must not change the lights


async def test_setup_port_in_use_raises_repair_issue(hass, fake_listener):
    MockConfigEntry(domain="govee_light_local").add_to_hass(hass)
    with patch(
        "custom_components.govee_segments.async_get_listener",
        AsyncMock(side_effect=OSError(98, "Address in use")),
    ):
        entry = await setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "port_conflict")
    assert issue.translation_key == "port_conflict_govee_light_local"


async def test_setup_follows_device_to_new_ip(hass, fake_listener):
    fake_listener.devices = {"192.0.2.77": fake_listener.devices[HOST]}
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_HOST] == "192.0.2.77"


async def test_setup_not_ready_when_unreachable(hass, fake_listener):
    fake_listener.online = False
    entry = await setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


# --- migration ---------------------------------------------------------------


async def test_migrates_v1_entry_keeping_entity_ids(hass, fake_listener):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Govee H7094", version=1, unique_id=HOST, data={CONF_HOST: HOST, CONF_SKU: "H7094"}
    )
    entry.add_to_hass(hass)
    dev_reg, ent_reg = dr.async_get(hass), er.async_get(hass)
    device = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={(DOMAIN, HOST)})
    old = ent_reg.async_get_or_create(
        LIGHT_DOMAIN, DOMAIN, f"{HOST}_segment_1", config_entry=entry, device_id=device.id,
        suggested_object_id="govee_h7094_light_2",
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.unique_id == DEVICE_ID
    assert entry.data[CONF_DEVICE_ID] == DEVICE_ID
    migrated = ent_reg.async_get(old.entity_id)
    assert migrated.entity_id == LIGHT_2
    assert migrated.unique_id == f"{DEVICE_ID}_segment_1"
    assert dev_reg.async_get(device.id).identifiers == {(DOMAIN, DEVICE_ID)}
    assert hass.states.get(LIGHT_2) is not None


async def test_migration_fails_cleanly_when_unreachable(hass, fake_listener):
    fake_listener.online = False
    entry = MockConfigEntry(
        domain=DOMAIN, title="Govee H7094", version=1, unique_id=HOST, data={CONF_HOST: HOST, CONF_SKU: "H7094"}
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    assert entry.version == 1


# --- per-light commands ------------------------------------------------------


async def test_light_on_while_fixture_on_sends_only_that_light(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_on", LIGHT_2, **{ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 128})
    assert fake_listener.take() == [("frames", [color_frame([1], (255, 0, 0)), brightness_frame([1], 50)])]
    state = hass.states.get(LIGHT_2)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (255, 0, 0)


async def test_light_on_while_fixture_off_darkens_the_others(hass, fake_listener):
    fake_listener.status = {**STATUS_ON, "onOff": 0}
    await setup(hass)
    assert hass.states.get(GROUP).state == STATE_OFF
    await call(hass, "turn_on", LIGHT_2, **{ATTR_RGB_COLOR: (0, 0, 255)})
    assert fake_listener.take() == [
        TURN_ON,
        ("frames", [color_frame([0, 2, 3], (0, 0, 0)), color_frame([1], (0, 0, 255)), brightness_frame([1], 100)]),
    ]
    assert hass.states.get(LIGHT_2).state == STATE_ON
    assert hass.states.get(LIGHT_1).state == STATE_OFF
    assert hass.states.get(GROUP).state == STATE_ON


async def test_last_light_off_powers_fixture_down(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_on", GROUP)
    for n in (1, 2, 3):
        await call(hass, "turn_off", f"light.govee_h7094_light_{n}")
    fake_listener.take()
    await call(hass, "turn_off", "light.govee_h7094_light_4")
    # The light goes dark before the fixture powers off.
    assert fake_listener.take() == [("frames", [off_frame([3])]), TURN_OFF]
    assert hass.states.get(GROUP).state == STATE_OFF


# --- group -------------------------------------------------------------------


async def test_group_lists_members(hass, fake_listener):
    await setup(hass)
    state = hass.states.get(GROUP)
    assert state.attributes[ATTR_ENTITY_ID] == [f"light.govee_h7094_light_{n}" for n in range(1, 5)]


async def test_group_off_is_a_real_power_off(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_off", GROUP)
    assert fake_listener.take() == [TURN_OFF]
    assert hass.states.get(GROUP).state == STATE_OFF
    assert hass.states.get(LIGHT_1).state == STATE_OFF


async def test_group_on_restores_each_lights_own_colour(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_on", LIGHT_1, **{ATTR_RGB_COLOR: (255, 0, 0)})
    await call(hass, "turn_on", LIGHT_2, **{ATTR_RGB_COLOR: (0, 0, 255), ATTR_BRIGHTNESS: 128})
    await call(hass, "turn_off", GROUP)
    fake_listener.take()
    await call(hass, "turn_on", GROUP)
    assert fake_listener.take() == [
        TURN_ON,
        (
            "frames",
            [
                color_frame([0], (255, 0, 0)),
                color_frame([1], (0, 0, 255)),
                color_frame([2, 3], (255, 255, 255)),
                brightness_frame([0, 2, 3], 100),
                brightness_frame([1], 50),
            ],
        ),
    ]
    assert hass.states.get(LIGHT_2).attributes[ATTR_RGB_COLOR] == (0, 0, 255)


async def test_group_colour_applies_to_all(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_on", GROUP, **{ATTR_RGB_COLOR: (0, 255, 0), ATTR_BRIGHTNESS: 255})
    assert fake_listener.take() == [("frames", [color_frame(ALL, (0, 255, 0)), brightness_frame(ALL, 100)])]
    for n in range(1, 5):
        assert hass.states.get(f"light.govee_h7094_light_{n}").attributes[ATTR_RGB_COLOR] == (0, 255, 0)


async def test_group_colour_temperature(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_on", GROUP, **{ATTR_COLOR_TEMP_KELVIN: 2700, ATTR_BRIGHTNESS: 128})
    assert fake_listener.take() == [
        ("colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 2700}),
        ("brightness", {"value": 50}),
    ]
    state = hass.states.get(GROUP)
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 2700
    # A single light changing colour takes the fixture out of white mode, so
    # every light gets its colour sent again.
    await call(hass, "turn_on", LIGHT_1, **{ATTR_RGB_COLOR: (255, 0, 0)})
    frames = fake_listener.take()[0][1]
    assert color_frame([0], (255, 0, 0)) in frames
    assert len([f for f in frames if f[3] == 0x01]) >= 2  # others re-sent too
    assert hass.states.get(GROUP).attributes[ATTR_COLOR_MODE] == ColorMode.RGB


async def test_group_effect_sends_scene(hass, fake_listener):
    await setup(hass)
    scenes = await hass.async_add_executor_job(load_scenes, "H7094")
    state = hass.states.get(GROUP)
    assert "Aurora" in state.attributes[ATTR_EFFECT_LIST]
    await call(hass, "turn_on", GROUP, **{ATTR_EFFECT: "Aurora"})
    assert fake_listener.take() == [("frames", scene_frames(*scenes["Aurora"]))]
    assert hass.states.get(GROUP).attributes[ATTR_EFFECT] == "Aurora"
    # Brightness during a scene goes fixture-wide and keeps the scene running.
    await call(hass, "turn_on", GROUP, **{ATTR_BRIGHTNESS: 51})
    assert fake_listener.take() == [("brightness", {"value": 20})]
    assert hass.states.get(GROUP).attributes[ATTR_EFFECT] == "Aurora"


async def test_group_follows_members(hass, fake_listener):
    await setup(hass)
    await call(hass, "turn_off", GROUP)
    await call(hass, "turn_on", LIGHT_2, **{ATTR_RGB_COLOR: (255, 0, 0), ATTR_BRIGHTNESS: 100})
    state = hass.states.get(GROUP)
    assert state.state == STATE_ON
    assert state.attributes[ATTR_RGB_COLOR] == (255, 0, 0)  # only lit members count
    await call(hass, "turn_on", LIGHT_1, **{ATTR_RGB_COLOR: (0, 0, 255), ATTR_BRIGHTNESS: 200})
    state = hass.states.get(GROUP)
    assert state.attributes[ATTR_RGB_COLOR] == (128, 0, 128)
    assert state.attributes[ATTR_BRIGHTNESS] == 150


# --- reconciling with devStatus ---------------------------------------------


async def test_external_power_off_and_on(hass, fake_listener, no_quiet_window):
    entry = await setup(hass)
    await call(hass, "turn_on", GROUP, **{ATTR_RGB_COLOR: (0, 0, 255)})
    await poll(hass, entry)
    fake_listener.status = {**fake_listener.status, "onOff": 0}
    await poll(hass, entry)
    assert hass.states.get(GROUP).state == STATE_OFF
    fake_listener.status = {**fake_listener.status, "onOff": 1}
    await poll(hass, entry)
    assert hass.states.get(GROUP).state == STATE_ON
    assert hass.states.get(LIGHT_1).attributes[ATTR_RGB_COLOR] == (0, 0, 255)  # kept


async def test_external_colour_and_brightness_change(hass, fake_listener, no_quiet_window):
    entry = await setup(hass)
    fake_listener.status = {**STATUS_ON, "color": {"r": 0, "g": 255, "b": 0}, "brightness": 40}
    await poll(hass, entry)
    for n in range(1, 5):
        state = hass.states.get(f"light.govee_h7094_light_{n}")
        assert state.attributes[ATTR_RGB_COLOR] == (0, 255, 0)
        assert state.attributes[ATTR_BRIGHTNESS] == 102
    fake_listener.status = {**STATUS_ON, "color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": 6500}
    await poll(hass, entry)
    state = hass.states.get(GROUP)
    assert state.attributes[ATTR_COLOR_MODE] == ColorMode.COLOR_TEMP
    assert state.attributes[ATTR_COLOR_TEMP_KELVIN] == 6500


async def test_changes_right_after_our_commands_are_ours(hass, fake_listener):
    entry = await setup(hass)
    await call(hass, "turn_on", LIGHT_1, **{ATTR_RGB_COLOR: (0, 0, 255)})
    # devStatus picking up a colour late must not repaint every light.
    fake_listener.status = {**STATUS_ON, "color": {"r": 0, "g": 0, "b": 255}}
    await poll(hass, entry)
    assert hass.states.get(LIGHT_2).attributes[ATTR_RGB_COLOR] == (255, 255, 255)


async def test_unavailable_after_two_missed_polls(hass, fake_listener):
    entry = await setup(hass)
    fake_listener.online = False
    await poll(hass, entry)
    assert hass.states.get(GROUP).state != STATE_UNAVAILABLE  # one miss is tolerated
    await poll(hass, entry)
    assert hass.states.get(GROUP).state == STATE_UNAVAILABLE
    assert hass.states.get(LIGHT_1).state == STATE_UNAVAILABLE
    fake_listener.online = True
    await poll(hass, entry)
    assert hass.states.get(GROUP).state == STATE_ON


async def test_poll_follows_device_to_new_ip(hass, fake_listener):
    entry = await setup(hass)
    fake_listener.devices = {"192.0.2.88": fake_listener.devices[HOST]}
    await poll(hass, entry)
    await poll(hass, entry)
    assert entry.data[CONF_HOST] == "192.0.2.88"
    await poll(hass, entry)
    assert hass.states.get(GROUP).state == STATE_ON


# --- diagnostics -------------------------------------------------------------


async def test_diagnostics_redacts_identifiers(hass, fake_listener):
    entry = await setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"][CONF_HOST] == "**REDACTED**"
    assert diag["scan"]["device"] == "**REDACTED**"
    assert diag["scan"]["wifiVersionSoft"] == "1.06.24"
    assert diag["last_dev_status"] == STATUS_ON
    assert len(diag["store"]["segments"]) == 4
    assert diag["scenes"] == 70
