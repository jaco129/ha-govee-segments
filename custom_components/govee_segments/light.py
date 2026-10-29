"""One optimistic light entity per segment.

The device never reports per-segment state, so every entity assumes its last
command took effect and restores that state across restarts.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
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
    async_add_entities(
        GoveeSegmentLight(entry.runtime_data, entry.unique_id, device, seg)
        for seg in range(SEGMENTS_BY_SKU[sku])
    )


def _to_percent(brightness: int) -> int:
    # Segment brightness 0 is a dim glow rather than off, so keep "on" at >= 1%.
    return max(1, round(brightness * 100 / 255))


class GoveeSegmentLight(LightEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.RGB
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_should_poll = False

    def __init__(self, sender: GoveeSender, entry_id: str, device: DeviceInfo, segment: int) -> None:
        self._sender = sender
        self._segment = segment
        self._attr_unique_id = f"{entry_id}_segment_{segment}"
        self._attr_name = f"Light {segment + 1}"
        self._attr_device_info = device
        self._attr_is_on = False
        self._attr_rgb_color = DEFAULT_RGB
        self._attr_brightness = DEFAULT_BRIGHTNESS

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is None:
            return
        self._attr_is_on = last.state == STATE_ON
        if (rgb := last.attributes.get(ATTR_RGB_COLOR)) is not None:
            self._attr_rgb_color = tuple(rgb)
        if (brightness := last.attributes.get(ATTR_BRIGHTNESS)) is not None:
            self._attr_brightness = brightness

    async def async_turn_on(self, **kwargs: Any) -> None:
        if ATTR_RGB_COLOR in kwargs:
            self._attr_rgb_color = kwargs[ATTR_RGB_COLOR]
        if ATTR_BRIGHTNESS in kwargs:
            self._attr_brightness = kwargs[ATTR_BRIGHTNESS]
        segs = [self._segment]
        self._sender.send_frames(
            [
                color_frame(segs, self._attr_rgb_color),
                brightness_frame(segs, _to_percent(self._attr_brightness)),
            ]
        )
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._sender.send_frames([off_frame([self._segment])])
        self._attr_is_on = False
        self.async_write_ha_state()
