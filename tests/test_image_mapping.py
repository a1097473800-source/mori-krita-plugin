"""Pure image/target checks; the real Krita API path is in krita_smoke.py."""

import importlib.util
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "mori_krita" / "krita_bridge.py"
SPEC = importlib.util.spec_from_file_location("mori_image_mapping_under_test", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class ImageMappingTests(unittest.TestCase):
    def test_selection_is_clipped_without_losing_its_position(self):
        self.assertEqual(bridge.clip_rect(-5, 8, 12, 9, 20, 10), (0, 8, 7, 2))
        self.assertEqual(bridge.clip_rect(30, 0, 5, 5, 20, 10), (30, 0, 0, 5))

    def test_padded_scanlines_do_not_enter_krita_pixel_buffer(self):
        first, second = bytes([10, 20, 30, 255]), bytes([40, 50, 60, 128])
        self.assertEqual(bridge.strip_argb32_padding(first + b"xx" + second + b"xx", 1, 2, 6), first + second)

    def test_selection_preserves_source_alpha_and_feathering(self):
        pixels = bytes([10, 20, 30, 255, 40, 50, 60, 128, 70, 80, 90, 255])
        result = bridge.apply_alpha_mask(pixels, bytes([255, 128, 0]))
        self.assertEqual(result, bytes([10, 20, 30, 255, 40, 50, 60, 64, 70, 80, 90, 0]))

    def test_bad_pixel_or_mask_dimensions_are_rejected(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.strip_argb32_padding(b"short", 2, 1, 8)
        with self.assertRaises(bridge.BridgeError):
            bridge.apply_alpha_mask(bytes(8), bytes(1))

    def test_target_serialization_retains_original_document_and_region(self):
        target = bridge.Target("document-one", 30, 40, 2, 1, "selection", "drawing.kra", "layer-one", bytes([255, 128]))
        value = target.as_dict(include_mask=True)
        restored = bridge.Target.from_dict(value)
        self.assertEqual(restored, target)
        self.assertEqual(restored.selection_mask, bytes([255, 128]))
        self.assertNotIn("selection_mask", target.as_dict())

    def test_invalid_targets_are_rejected(self):
        for patch in ({"width": 0}, {"source": "arbitrary"}, {"selection_mask": "!invalid!"}):
            value = bridge.Target("document-one", 0, 0, 2, 1).as_dict()
            value.update(patch)
            with self.assertRaises(bridge.BridgeError):
                bridge.Target.from_dict(value)


if __name__ == "__main__":
    unittest.main()
