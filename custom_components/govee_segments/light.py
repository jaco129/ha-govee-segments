"""Light entities: one per segment, plus a group light for the whole fixture.

The device never reports per-segment state, so all entities share one optimistic
SegmentStore. Every command updates the store and every entity re-renders from it,
which keeps the group and its members in sync (like a Hue room and its bulbs).
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import GoveeSegmentsConfigEntry, GoveeSender
from .const import CONF_SKU, DOMAIN
from .protocol import SEGMENTS_BY_SKU, brightness_frame, color_frame, off_frame

DEFAULT_RGB = (255, 255, 255)
DEFAULT_BRIGHTNESS = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GoveeSegmentsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    sku = entry.data[CONF_SKU]
    device = DeviceInfo(
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer="Govee",
        model=sku,
        name=entry.title,
    )
    store = SegmentStore(entry.runtime_data, SEGMENTS_BY_SKU[sku])
    members = [GoveeSegmentLight(store, entry.unique_id, device, seg) for seg in range(store.count)]
    async_add_entities([*members, GoveeGroupLight(store, entry.unique_id, device, members)])


def _to_percent(brightness: int) -> int:
    # Segment brightness 0 is a dim glow rather than off, so keep "on" at >= 1%.
    return max(1, round(brightness * 100 / 255))


def _group_by(segments: Iterable[int], key: Callable[[int], Any]) -> dict[Any, list[int]]:
    groups: dict[Any, list[int]] = {}
    for seg in segments:
        groups.setdefault(key(seg), []).append(seg)
    return groups


@dataclass
class SegmentState:
    is_on: bool = False
    rgb: tuple[int, int, int] = DEFAULT_RGB
    brightness: int = DEFAULT_BRIGHTNESS


class SegmentStore:
    """Assumed state of every segment, and the only place that sends commands."""

    def __init__(self, sender: GoveeSender, count: int) -> None:
        self._sender = sender
        self.count = count
        self.segments = [SegmentState() for _ in range(count)]
        self._listeners: list[Callable[[], None]] = []

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def restore(self, seg: int, state: SegmentState) -> None:
        """Load a restored state without sending anything."""
        self.segments[seg] = state
        self._notify()

    def turn_on(
        self,
        segs: list[int],
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
    ) -> None:
        for seg in segs:
            state = self.segments[seg]
            state.is_on = True
            if rgb is not None:
                state.rgb = tuple(rgb)
            if brightness is not None:
                state.brightness = brightness
        # One frame per distinct value, each with a mask covering every segment
        # that shares it, so a group change stays a single short message.
        frames = [
            color_frame(group, value)
            for value, group in _group_by(segs, lambda s: self.segments[s].rgb).items()
        ]
        frames += [
            brightness_frame(group, value)
            for value, group in _group_by(
                segs, lambda s: _to_percent(self.segments[s].brightness)
            ).items()
        ]
        self._sender.send_frames(frames)
        self._notify()

    def turn_off(self, segs: list[int]) -> None:
        for seg in segs:
            self.segments[seg].is_on = False
        self._sender.send_frames([off_frame(segs)])
        self._notify()


class _StoreLight(LightEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_should_poll = False

    def __init__(self, store: SegmentStore, device: DeviceInfo) -> None:
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

    def __init__(self, store: SegmentStore, entry_id: str, device: DeviceInfo, segment: int) -> None:
        super().__init__(store, device)
        self._segment = segment
        self._attr_unique_id = f"{entry_id}_segment_{segment}"
        self._attr_name = f"Light {segment + 1}"

    @property
    def _state(self) -> SegmentState:
        return self._store.segments[self._segment]

    @property
    def is_on(self) -> bool:
        return self._state.is_on

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
        self._store.turn_on(
            [self._segment], kwargs.get(ATTR_RGB_COLOR), kwargs.get(ATTR_BRIGHTNESS)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._store.turn_off([self._segment])


class GoveeGroupLight(_StoreLight):
    """The whole fixture as one light, with the segment lights as members.

    On if any member is on. Turning it on with no colour or brightness brings each
    light back to its own last setting; with a colour or brightness, all lights get it.
    Colour and brightness shown are the mean of the lights that are on, as HA's own
    light groups do.
    """

    _attr_name = None  # takes the device name, e.g. light.govee_h7094

    def __init__(
        self,
        store: SegmentStore,
        entry_id: str,
        device: DeviceInfo,
        members: list[GoveeSegmentLight],
    ) -> None:
        super().__init__(store, device)
        self._members = members
        self._attr_unique_id = f"{entry_id}_group"

    @property
    def _lit(self) -> list[SegmentState]:
        return [s for s in self._store.segments if s.is_on] or self._store.segments

    @property
    def is_on(self) -> bool:
        return any(s.is_on for s in self._store.segments)

    @property
    def rgb_color(self) -> tuple[int, int, int]:
        lit = self._lit
        return tuple(round(sum(s.rgb[i] for s in lit) / len(lit)) for i in range(3))

    @property
    def brightness(self) -> int:
        lit = self._lit
        return round(sum(s.brightness for s in lit) / len(lit))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # The same attribute HA light groups use; the UI lists these as members.
        return {ATTR_ENTITY_ID: [m.entity_id for m in self._members if m.entity_id]}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._store.turn_on(
            list(range(self._store.count)),
            kwargs.get(ATTR_RGB_COLOR),
            kwargs.get(ATTR_BRIGHTNESS),
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._store.turn_off(list(range(self._store.count)))
