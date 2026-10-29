"""Light entities: one per segment, plus a group light for the whole fixture.

The device can't report per-segment state, so all entities share one SegmentStore
holding the assumed state of each light. Commands go through the store, and every
entity re-renders from it, which keeps the group and its members in sync (like a
Hue room and its bulbs). The coordinator's devStatus polls feed whole-fixture
changes made elsewhere (the Govee app, a power cut) back into the store.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util.color import color_temperature_to_rgb

from . import GoveeSegmentsConfigEntry
from .const import CONF_SKU, DOMAIN
from .coordinator import GoveeCoordinator, GoveeDevice
from .protocol import (
    KELVIN_RANGE,
    SEGMENTS_BY_SKU,
    brightness_frame,
    brightness_message,
    color_frame,
    kelvin_message,
    off_frame,
    scene_frames,
    turn_message,
)

DEFAULT_RGB = (255, 255, 255)
DEFAULT_BRIGHTNESS = 255
# devStatus can lag our own commands, so changes within this long of one are ours.
QUIET_SECONDS = 15.0
SCENES_DIR = Path(__file__).parent / "scenes"


def load_scenes(sku: str) -> dict[str, tuple[int, bytes]]:
    """Scene name -> (code, param) from the bundled effect library. Blocking I/O."""
    path = SCENES_DIR / f"{sku}.json"
    if not path.exists():
        return {}
    return {
        s["name"]: (s["code"], base64.b64decode(s["param"]))
        for s in json.loads(path.read_text(encoding="utf-8"))
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeSegmentsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = entry.runtime_data
    sku = entry.data[CONF_SKU]
    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer="Govee",
        model=sku,
        name=entry.title,
        sw_version=data.scan.get("wifiVersionSoft"),
        hw_version=data.scan.get("wifiVersionHard"),
    )
    scenes = await hass.async_add_executor_job(load_scenes, sku)
    store = SegmentStore(data.device, SEGMENTS_BY_SKU[sku], scenes)
    store.handle_status(data.coordinator.data)
    # Registered before the entities' own listeners, so they render the reconciled state.
    entry.async_on_unload(
        data.coordinator.async_add_listener(lambda: store.handle_status(data.coordinator.data))
    )
    data.store = store  # for diagnostics
    members = [
        GoveeSegmentLight(data.coordinator, store, entry.unique_id, device_info, seg)
        for seg in range(store.count)
    ]
    async_add_entities(
        [*members, GoveeGroupLight(data.coordinator, store, entry.unique_id, device_info, members)]
    )


def _to_percent(brightness: int) -> int:
    # Segment brightness 0 is a dim glow rather than off, so keep "on" at >= 1%.
    return max(1, round(brightness * 100 / 255))


def _kelvin_rgb(kelvin: int) -> tuple[int, int, int]:
    return tuple(round(c) for c in color_temperature_to_rgb(kelvin))


def _group_by(segments: Iterable[int], key: Callable[[int], Any]) -> dict[Any, list[int]]:
    groups: dict[Any, list[int]] = {}
    for seg in segments:
        groups.setdefault(key(seg), []).append(seg)
    return groups


@dataclass
class SegmentState:
    # Without a saved state, assume a light is lit whenever the fixture is: that's
    # what a fixture that was set up in the Govee app looks like.
    is_on: bool = True
    rgb: tuple[int, int, int] = DEFAULT_RGB
    brightness: int = DEFAULT_BRIGHTNESS


class SegmentStore:
    """Assumed state of the fixture and each segment; the only place that sends commands.

    A segment shows as on only while the fixture is powered. `kelvin` is set while
    the whole fixture is in white (colour temperature) mode, `effect` while a scene runs.
    """

    def __init__(self, device: GoveeDevice, count: int, scenes: dict[str, tuple[int, bytes]]) -> None:
        self._device = device
        self.count = count
        self.scenes = scenes
        self.segments = [SegmentState() for _ in range(count)]
        self.fixture_on = False
        self.kelvin: int | None = None
        self.effect: str | None = None
        self._listeners: list[Callable[[], None]] = []
        self._last_status: dict[str, Any] | None = None
        self._last_command = float("-inf")

    @property
    def all_segments(self) -> list[int]:
        return list(range(self.count))

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture_on": self.fixture_on,
            "kelvin": self.kelvin,
            "effect": self.effect,
            "segments": [asdict(s) for s in self.segments],
        }

    # --- sending -------------------------------------------------------------

    def _send(self, payloads: list[bytes], frames: list[bytes], after: list[bytes] = ()) -> None:
        """Send payloads, then frames in one ptReal message, then `after` payloads."""
        self._last_command = time.monotonic()
        for payload in payloads:
            self._device.send(payload)
        if frames:
            self._device.send_frames(frames)
        for payload in after:
            self._device.send(payload)

    def _segment_frames(self, segs: list[int]) -> list[bytes]:
        # One frame per distinct value, each with a mask covering every segment
        # that shares it, so a group change stays a single short message.
        frames = []
        for rgb, group in _group_by(
            segs, lambda s: self.segments[s].rgb if self.segments[s].is_on else (0, 0, 0)
        ).items():
            frames.append(color_frame(group, rgb))
        lit = [s for s in segs if self.segments[s].is_on]
        for pct, group in _group_by(lit, lambda s: _to_percent(self.segments[s].brightness)).items():
            frames.append(brightness_frame(group, pct))
        return frames

    # --- commands ------------------------------------------------------------

    def turn_on_segments(
        self,
        segs: list[int],
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
    ) -> None:
        payloads = []
        resend = segs
        if not self.fixture_on:
            # Powering on brings back the fixture's last mode, so the others go dark
            # explicitly: only the requested lights should come on.
            payloads.append(turn_message(True))
            self.fixture_on = True
            for seg in self.all_segments:
                self.segments[seg].is_on = False
            resend = self.all_segments
        elif self.kelvin is not None or self.effect is not None:
            # Leaving white mode or a scene: every segment needs its own colour again.
            resend = self.all_segments
        self.kelvin = None
        self.effect = None
        for seg in segs:
            state = self.segments[seg]
            state.is_on = True
            if rgb is not None:
                state.rgb = tuple(rgb)
            if brightness is not None:
                state.brightness = brightness
        self._send(payloads, self._segment_frames(resend))
        self._notify()

    def turn_off_segments(self, segs: list[int]) -> None:
        for seg in segs:
            self.segments[seg].is_on = False
        if not any(s.is_on for s in self.segments):
            # The last light went off: power the fixture down too, so the Govee app
            # and devStatus agree that it's off.
            self.fixture_on = False
            self._send([], [off_frame(segs)], after=[turn_message(False)])
        else:
            self._send([], [off_frame(segs)])
        self._notify()

    def turn_on_group(
        self,
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
        kelvin: int | None = None,
        effect: str | None = None,
    ) -> None:
        payloads = [] if self.fixture_on else [turn_message(True)]
        self.fixture_on = True
        for state in self.segments:
            state.is_on = True
            if brightness is not None:
                state.brightness = brightness
        frames: list[bytes] = []
        if effect is not None and effect in self.scenes:
            code, param = self.scenes[effect]
            self.effect, self.kelvin = effect, None
            frames = scene_frames(code, param)
            if brightness is not None:
                payloads.append(brightness_message(_to_percent(brightness)))
        elif self.effect is not None and rgb is None and kelvin is None:
            # Powering back on resumes the scene by itself; brightness is fixture-wide.
            if brightness is not None:
                payloads.append(brightness_message(_to_percent(brightness)))
        elif kelvin is not None or (self.kelvin is not None and rgb is None):
            # White mode is fixture-wide (colorwc); brightness then goes fixture-wide too.
            self.kelvin = kelvin if kelvin is not None else self.kelvin
            self.effect = None
            for state in self.segments:
                state.rgb = _kelvin_rgb(self.kelvin)
            payloads.append(kelvin_message(self.kelvin))
            payloads.append(brightness_message(_to_percent(self.segments[0].brightness)))
        else:
            self.kelvin = self.effect = None
            if rgb is not None:
                for state in self.segments:
                    state.rgb = tuple(rgb)
            frames = self._segment_frames(self.all_segments)
        self._send(payloads, frames)
        self._notify()

    def turn_off_group(self) -> None:
        self.fixture_on = False
        self._send([turn_message(False)], [])
        self._notify()

    def restore(self, seg: int, state: SegmentState) -> None:
        """Load a restored state without sending anything."""
        self.segments[seg] = state
        self._notify()

    # --- reconciling with devStatus -------------------------------------------

    def handle_status(self, status: dict[str, Any] | None) -> None:
        """Apply whole-fixture changes made outside Home Assistant.

        Only changes that turn up while we've been quiet count: devStatus can pick
        up our own commands a poll or two late.
        """
        if not status:
            return
        previous, self._last_status = self._last_status, status
        if previous is None:
            self.fixture_on = bool(status.get("onOff"))
            self._notify()
            return
        if status == previous or time.monotonic() - self._last_command < QUIET_SECONDS:
            return
        on = bool(status.get("onOff"))
        if on and not self.fixture_on and not any(s.is_on for s in self.segments):
            for state in self.segments:
                state.is_on = True
        self.fixture_on = on
        color = status.get("color") or {}
        rgb = (color.get("r", 0), color.get("g", 0), color.get("b", 0))
        kelvin = status.get("colorTemInKelvin") or 0
        if (rgb, kelvin) != _status_color(previous):
            self.effect = None
            if kelvin:
                self.kelvin = kelvin
                rgb = _kelvin_rgb(kelvin)
            else:
                self.kelvin = None
            for state in self.segments:
                state.is_on = True
                state.rgb = rgb
        if (pct := status.get("brightness")) != previous.get("brightness") and pct:
            for state in self.segments:
                state.brightness = round(pct * 255 / 100)
        self._notify()


def _status_color(status: dict[str, Any]) -> tuple[tuple[int, int, int], int]:
    color = status.get("color") or {}
    return (color.get("r", 0), color.get("g", 0), color.get("b", 0)), status.get("colorTemInKelvin") or 0


class _StoreLight(CoordinatorEntity[GoveeCoordinator], LightEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = True  # per-light state can't be read back over LAN

    def __init__(self, coordinator: GoveeCoordinator, store: SegmentStore, device: DeviceInfo) -> None:
        super().__init__(coordinator)
        self._store = store
        self._attr_device_info = device

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._store.add_listener(self._handle_store_update))

    @callback
    def _handle_store_update(self) -> None:
        self.async_write_ha_state()


class GoveeSegmentLight(_StoreLight, RestoreEntity):
    """One physical light (segment)."""

    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        store: SegmentStore,
        device_key: str,
        device: DeviceInfo,
        segment: int,
    ) -> None:
        super().__init__(coordinator, store, device)
        self._segment = segment
        self._attr_unique_id = f"{device_key}_segment_{segment}"
        self._attr_name = f"Light {segment + 1}"

    @property
    def _state(self) -> SegmentState:
        return self._store.segments[self._segment]

    @property
    def is_on(self) -> bool:
        return self._store.fixture_on and self._state.is_on

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        return self._state.rgb

    @property
    def brightness(self) -> int:
        return self._state.brightness

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            return
        state = SegmentState(is_on=last.state == STATE_ON)
        if (rgb := last.attributes.get(ATTR_RGB_COLOR)) is not None:
            state.rgb = tuple(rgb)
        if (brightness := last.attributes.get(ATTR_BRIGHTNESS)) is not None:
            state.brightness = brightness
        self._store.restore(self._segment, state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._store.turn_on_segments(
            [self._segment], kwargs.get(ATTR_RGB_COLOR), kwargs.get(ATTR_BRIGHTNESS)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._store.turn_off_segments([self._segment])


class GoveeGroupLight(_StoreLight):
    """The whole fixture as one light, with the segment lights as members.

    On if the fixture is on and any member is. Turning it on with no settings
    turns every light on with its own last colour; a colour or brightness goes to
    all of them. Colour and brightness shown are the mean of the lights that are on,
    as HA's own light groups do. Colour temperature and scenes are fixture-wide.
    """

    _attr_name = None  # takes the device name, e.g. light.govee_h7094
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.RGB}
    _attr_min_color_temp_kelvin, _attr_max_color_temp_kelvin = KELVIN_RANGE

    def __init__(
        self,
        coordinator: GoveeCoordinator,
        store: SegmentStore,
        device_key: str,
        device: DeviceInfo,
        members: list[GoveeSegmentLight],
    ) -> None:
        super().__init__(coordinator, store, device)
        self._members = members
        self._attr_unique_id = f"{device_key}_group"
        if store.scenes:
            self._attr_supported_features = LightEntityFeature.EFFECT
            self._attr_effect_list = list(store.scenes)

    @property
    def _lit(self) -> list[SegmentState]:
        return [s for s in self._store.segments if s.is_on] or self._store.segments

    @property
    def is_on(self) -> bool:
        return self._store.fixture_on and any(s.is_on for s in self._store.segments)

    @property
    def color_mode(self) -> ColorMode:
        return ColorMode.COLOR_TEMP if self._store.kelvin is not None else ColorMode.RGB

    @property
    def color_temp_kelvin(self) -> int | None:
        return self._store.kelvin

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        lit = self._lit
        return tuple(round(sum(s.rgb[i] for s in lit) / len(lit)) for i in range(3))

    @property
    def brightness(self) -> int:
        lit = self._lit
        return round(sum(s.brightness for s in lit) / len(lit))

    @property
    def effect(self) -> str | None:
        return self._store.effect

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # The same attribute HA light groups use; the UI lists these as members.
        return {ATTR_ENTITY_ID: [m.entity_id for m in self._members if m.entity_id]}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._store.turn_on_group(
            rgb=kwargs.get(ATTR_RGB_COLOR),
            brightness=kwargs.get(ATTR_BRIGHTNESS),
            kelvin=kwargs.get(ATTR_COLOR_TEMP_KELVIN),
            effect=kwargs.get(ATTR_EFFECT),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._store.turn_off_group()
