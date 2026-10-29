# Govee LAN Segments

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/jaco129/ha-govee-segments/actions/workflows/validate.yml/badge.svg)](https://github.com/jaco129/ha-govee-segments/actions/workflows/validate.yml)
[![Tests](https://github.com/jaco129/ha-govee-segments/actions/workflows/tests.yml/badge.svg)](https://github.com/jaco129/ha-govee-segments/actions/workflows/tests.yml)

A Home Assistant custom integration for Govee multi-light fixtures, over Govee's local LAN API.
Each light becomes its own `light` entity, and the whole fixture is a group light on top, like a
Hue room. There's no cloud account or API key, and it doesn't use Bluetooth.

Supported models (confirmed on real hardware):

| Model | Lights | Notes |
|---|---|---|
| H7094 Outdoor Spotlights 2 (4-pack) | 4 | Tested on Wi-Fi firmware 1.06.24 |

## What you get

A device with five lights:

| Entity | Does |
|---|---|
| `light.govee_h7094` (group) | Real power on/off, colour or brightness for all four, colour temperature (2000–9000 K), and 70 Govee scenes as effects. Lists the four lights as its members. |
| `light.govee_h7094_light_1` to `_4` | On/off, RGB colour and brightness for each light. Light 1 is nearest the control box. |

- **Group on** with no settings turns every light on with its own last colour.
- **Group off** powers the fixture down, so the Govee app agrees.
- **Turning on one light while the fixture is off** lights only that one.
- **Turning off the last lit light** powers the fixture down.

## Staying accurate

The integration polls the fixture every 10 seconds.

- **Power loss or the lights going offline:** all five entities show unavailable within about
  20 seconds, then recover by themselves. The fixture keeps each light's colour through a power
  cut.
- **Changes made elsewhere,** for example power, one colour for all, colour temperature or
  brightness set in the Govee app, are picked up on the next poll.
- **If the fixture's IP address changes,** the integration finds it again with a broadcast scan.

What it can't know:
- The LAN API can't report individual lights, so per-light states are assumed: they show what
  Home Assistant last set, and are restored after a restart.
- A single light changed in the Govee app, or a scene started there, isn't detected.

## Install

> **Remove "Govee lights local" first.** Govee devices send every reply to UDP port 4002, so
> only one Govee LAN integration can run on a Home Assistant host. If it's still installed, this
> integration raises a repair issue and waits.

1. In the Govee Home app, enable **LAN Control** for the device.
2. In HACS: **⋮ → Custom repositories**, add `https://github.com/jaco129/ha-govee-segments` with
   type **Integration**, then download **Govee LAN Segments** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Govee LAN Segments.** Pick the light from
   the list, or enter its IP address.

To change the IP later, use **Reconfigure** on the integration's entry.

### Upgrading from 0.1.x

0.1 identified the fixture by its IP address. 0.2 uses the device's permanent id, so an IP change
no longer breaks it. On the first start after upgrading, the integration asks the fixture for its
id and updates its records. Your entity ids, names and automations stay the same.

1. Remove Govee lights local.
2. Update this integration through HACS.
3. Restart Home Assistant with the lights powered.

If the fixture doesn't answer during that first start, the entry shows a migration error. Power
the lights and restart again.

## How it works

Per-light control uses Govee's undocumented `ptReal` LAN command, which wraps the device's
Bluetooth segment frames, sent to UDP port 4003. Fixture state comes from polling `devStatus`.
Scenes come from Govee's public effect library: run `scripts/fetch_scenes.py H7094` to regenerate
`custom_components/govee_segments/scenes/H7094.json`.

## Development

```
python3 -m unittest discover -s tests -p test_protocol.py   # encoder only, no HA needed
pip install -r requirements_test.txt && pytest tests
```

## Releasing

HACS installs from GitHub releases. Bump `version` in `custom_components/govee_segments/manifest.json`,
commit, then tag and publish a release with the same version (for example `v0.2.0`).

## License

MIT, see [LICENSE](LICENSE).
