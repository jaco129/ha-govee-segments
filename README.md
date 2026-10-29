# Govee LAN Segments

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/jaco129/ha-govee-segments/actions/workflows/validate.yml/badge.svg)](https://github.com/jaco129/ha-govee-segments/actions/workflows/validate.yml)
[![Tests](https://github.com/jaco129/ha-govee-segments/actions/workflows/tests.yml/badge.svg)](https://github.com/jaco129/ha-govee-segments/actions/workflows/tests.yml)

A Home Assistant custom integration that makes each light of a Govee multi-light fixture its own
`light` entity, over Govee's local LAN API. There's no cloud account or API key, and it doesn't use
Bluetooth.

Supported models (confirmed on real hardware):

| Model | Lights | Notes |
|---|---|---|
| H7094 Outdoor Spotlights 2 (4-pack) | 4 | Tested on Wi-Fi firmware 1.06.24 |

Each light gets on/off, RGB colour and brightness. Colour temperature isn't supported yet.

## How it works

The integration sends Govee's undocumented `ptReal` LAN command, which wraps the device's Bluetooth
segment frames, to UDP port 4003 on the device. Light 1 is the one nearest the control box.

It only sends and never listens. The device doesn't report per-light state anyway, so each entity is
**optimistic**: it shows what it last sent and restores that after a restart. Because it never binds
UDP 4002, it can run alongside Home Assistant's built-in **Govee lights local** integration. Keep that
integration for whole-fixture control and scenes.

Caveats:
- If the whole fixture changes (the Govee app, a scene, or the built-in integration), the per-light
  entities don't know and will show stale state until you next set them.
- Turning a light off sets its colour to black. The fixture itself stays on.
- Brightness is the light's own level and may be scaled by the fixture's overall brightness.

## Install

1. In the Govee Home app, enable **LAN Control** for the device. Reserve its IP on your router.
2. In HACS: **⋮ → Custom repositories**, add `https://github.com/jaco129/ha-govee-segments` with type **Integration**, then
   download **Govee LAN Segments** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Govee LAN Segments**, then enter the IP and
   pick the model.

To install without HACS, copy `custom_components/govee_segments` into your Home Assistant
`config/custom_components/` folder and restart.

## Development

```
python3 -m unittest discover -s tests -p test_protocol.py   # frame encoder, no HA needed
pip install -r requirements_test.txt && pytest tests
```

## Releasing

HACS installs from GitHub releases. Bump `version` in `custom_components/govee_segments/manifest.json`,
commit, then tag and publish a release with the same version (for example `v0.1.0`).

## License

MIT, see [LICENSE](LICENSE).
