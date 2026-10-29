"""Frame encoder tests. Expected bytes are the frames that worked on a real H7094.

Run with: python3 -m unittest discover -s tests
"""
import base64
import importlib.util
import json
import pathlib
import unittest

# Load protocol.py on its own so the tests don't need Home Assistant installed.
_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "govee_segments" / "protocol.py"
_spec = importlib.util.spec_from_file_location("protocol", _PATH)
protocol = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(protocol)


def hexframe(s: str) -> bytes:
    return bytes.fromhex(s.replace(" ", ""))


class FrameTests(unittest.TestCase):
    def test_color_light1_green(self):
        # 2026-10-29 11:09:07: light 1 went green
        self.assertEqual(
            protocol.color_frame([0], (0, 255, 0)),
            hexframe("33 05 15 01 00 ff 00 00 00 00 00 00 01 00 00 00 00 00 00 dc"),
        )

    def test_color_light4_purple(self):
        self.assertEqual(
            protocol.color_frame([3], (255, 0, 255)),
            hexframe("33 05 15 01 ff 00 ff 00 00 00 00 00 08 00 00 00 00 00 00 2a"),
        )

    def test_brightness_light2(self):
        # 2026-10-29 11:15 blink test: light 2 to 5% and back to 100%
        self.assertEqual(
            protocol.brightness_frame([1], 5),
            hexframe("33 05 15 02 05 02 00 00 00 00 00 00 00 00 00 00 00 00 00 26"),
        )
        self.assertEqual(
            protocol.brightness_frame([1], 100),
            hexframe("33 05 15 02 64 02 00 00 00 00 00 00 00 00 00 00 00 00 00 47"),
        )

    def test_off_light2(self):
        # 2026-10-29 11:24:53: light 2 went fully dark
        self.assertEqual(
            protocol.off_frame([1]),
            hexframe("33 05 15 01 00 00 00 00 00 00 00 00 02 00 00 00 00 00 00 20"),
        )

    def test_multi_segment_mask(self):
        frame = protocol.color_frame([0, 1, 2, 3], (255, 0, 0))
        self.assertEqual(frame[12:14], b"\x0f\x00")

    def test_ptreal_message(self):
        msg = json.loads(protocol.ptreal_message([protocol.off_frame([1])]))
        self.assertEqual(msg["msg"]["cmd"], "ptReal")
        self.assertEqual(msg["msg"]["data"]["command"], ["MwUVAQAAAAAAAAAAAgAAAAAAACA="])
        self.assertEqual(base64.b64decode(msg["msg"]["data"]["command"][0]), protocol.off_frame([1]))

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            protocol.brightness_frame([0], 101)
        with self.assertRaises(ValueError):
            protocol.color_frame([15], (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
