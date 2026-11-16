"""Reference proxy tests. Qt cases run with Krita's official script runner."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "mori_krita" / "krita_bridge.py"
SPEC = importlib.util.spec_from_file_location("mori_reference_encoding_under_test", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bridge
SPEC.loader.exec_module(bridge)


class ReferenceDimensionTests(unittest.TestCase):
    def test_fast_limits_edge_and_area_without_upscaling(self):
        self.assertEqual(bridge.reference_dimensions(4096, 4096), (2000, 2000))
        self.assertEqual(bridge.reference_dimensions(4096, 2048), (2048, 1024))
        self.assertEqual(bridge.reference_dimensions(13, 7), (13, 7))
        width, height = bridge.reference_dimensions(15000, 1000)
        self.assertLessEqual(max(width, height), 2048)
        self.assertLessEqual(width * height, 4_000_000)

    def test_original_retains_dimensions_and_rejects_invalid_mode(self):
        self.assertEqual(bridge.reference_dimensions(4096, 4096, "original"), (4096, 4096))
        with self.assertRaises(bridge.BridgeError):
            bridge.reference_dimensions(10, 10, "unknown")


@unittest.skipIf(bridge.QImage is None, "Qt image runtime is available inside Krita")
class ReferenceQtTests(unittest.TestCase):
    @staticmethod
    def image(width, height, rgba=(40, 80, 120, 255)):
        from PyQt5.QtGui import QColor
        image = bridge.QImage(width, height, bridge.QImage.Format_ARGB32)
        image.fill(QColor(*rgba))
        return image

    @staticmethod
    def snapshot(image, mask=b"", read_ms=1.25):
        target = bridge.Target("frozen-document", 30, 40, image.width(), image.height(),
                               "selection" if mask else "canvas", "private-document-name", "", mask)
        return bridge.Snapshot(image, target, target.source, "private-document-name", "private-layer-name", read_ms)

    def pixel_import_fixture(self, write_result):
        image = self.image(8, 4)
        target = bridge.Target("frozen-document", 0, 0, 8, 4, "canvas", "QA document", "", b"")
        node = Mock()
        node.colorModel.return_value = "RGBA"
        node.colorDepth.return_value = "U8"
        node.colorProfile.return_value = bridge.SRGB_PROFILE
        node.setPixelData.return_value = write_result
        document = Mock()
        document.createNode.return_value = node
        document.width.return_value, document.height.return_value = 8, 4
        document.colorModel.return_value = "RGBA"
        document.colorDepth.return_value = "U8"
        document.colorProfile.return_value = bridge.SRGB_PROFILE
        document.rootNode.return_value.addChildNode.return_value = True
        client = bridge.KritaBridge()
        client._gui = lambda: Mock()
        client._resolve = lambda key: document
        return client, document, node, target, bridge._encode_png(image)

    def test_legacy_void_pixel_setter_is_a_success_without_a_second_pixel_read(self):
        client, document, node, target, encoded = self.pixel_import_fixture(None)
        self.assertIs(client.import_png(encoded, "Legacy result", target), node)
        payload, x, y, width, height = node.setPixelData.call_args.args
        self.assertEqual((x, y, width, height), (0, 0, 8, 4))
        self.assertEqual(len(bytes(payload)), 8 * 4 * 4)
        document.rootNode.return_value.addChildNode.assert_called_once_with(node, None)
        node.pixelData.assert_not_called()
        self.assertEqual(client.last_import_stage, "DONE")

    def test_modern_true_pixel_setter_keeps_the_same_fast_path(self):
        client, document, node, target, encoded = self.pixel_import_fixture(True)
        self.assertIs(client.import_png(encoded, "Modern result", target), node)
        document.rootNode.return_value.addChildNode.assert_called_once_with(node, None)
        node.pixelData.assert_not_called()

    def test_explicit_false_pixel_write_is_rejected_and_not_attached(self):
        client, document, node, target, encoded = self.pixel_import_fixture(False)
        with self.assertRaisesRegex(bridge.BridgeError, "写入图层失败") as caught:
            client.import_png(encoded, "Failed result", target)
        self.assertEqual(caught.exception.stage, "WRITE_PIXELS")
        self.assertIn("8×4，128 字节", str(caught.exception))
        document.rootNode.return_value.addChildNode.assert_not_called()
        node.remove.assert_called_once()

    def test_native_write_exception_has_a_safe_stage_and_removes_only_new_layer(self):
        client, document, node, target, encoded = self.pixel_import_fixture(True)
        node.setPixelData.side_effect = RuntimeError("C:/private/student/image.png secret")
        with self.assertRaises(bridge.BridgeError) as caught:
            client.import_png(encoded, "Private document title", target)
        self.assertEqual(caught.exception.stage, "WRITE_PIXELS")
        self.assertIn("RuntimeError", str(caught.exception))
        self.assertNotIn("private", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        document.rootNode.return_value.addChildNode.assert_not_called()
        document.remove.assert_not_called()
        node.remove.assert_called_once()

    def test_attach_failure_is_distinguished_from_pixel_write_failure(self):
        client, document, node, target, encoded = self.pixel_import_fixture(None)
        document.rootNode.return_value.addChildNode.return_value = False
        with self.assertRaises(bridge.BridgeError) as caught:
            client.import_png(encoded, "Result", target)
        self.assertEqual(caught.exception.stage, "ATTACH_LAYER")
        self.assertIn("加入原画布", str(caught.exception))
        document.setActiveNode.assert_not_called()
        node.remove.assert_called_once()

    def test_invalid_image_is_rejected_before_any_layer_is_created(self):
        client, document, node, target, _ = self.pixel_import_fixture(True)
        with self.assertRaises(bridge.BridgeError) as caught:
            client.import_png(b"not an image", "Result", target)
        self.assertEqual(caught.exception.stage, "DECODE_IMAGE")
        document.createNode.assert_not_called()

    def test_non_rgba_canvas_conversion_failure_has_its_own_stage(self):
        client, document, node, target, encoded = self.pixel_import_fixture(None)
        document.colorDepth.return_value = "U16"
        node.setColorSpace.return_value = False
        with self.assertRaises(bridge.BridgeError) as caught:
            client.import_png(encoded, "Result", target)
        self.assertEqual(caught.exception.stage, "CONVERT_COLOR")
        document.rootNode.return_value.addChildNode.assert_not_called()
        node.remove.assert_called_once()

    def test_opaque_argb_uses_jpeg_and_retains_original_placement(self):
        snapshot = self.snapshot(self.image(2560, 1280))
        result = bridge.prepare_reference(snapshot)
        self.assertEqual((result.width, result.height), (2048, 1024))
        self.assertEqual((result.target.width, result.target.height), (2560, 1280))
        self.assertEqual((result.x, result.y), (30, 40))
        self.assertEqual(result.mime, "image/jpeg")
        self.assertEqual(result.name, "reference.jpg")
        self.assertTrue(result.image_bytes.startswith(b"\xff\xd8"))
        self.assertEqual(result.image_bytes, result.png_bytes)
        self.assertTrue(result.as_dict()["dataUrl"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("private", repr(result.metrics))
        self.assertEqual(result.metrics["read_ms"], 1.25)

    def test_transparent_pixels_remain_transparent_in_fast_reference(self):
        image = self.image(20, 10, (80, 120, 160, 128))
        result = bridge.prepare_reference(self.snapshot(image))
        decoded = bridge.QImage.fromData(result.image_bytes)
        self.assertIn(result.mime, ("image/webp", "image/png"))
        self.assertEqual(decoded.pixelColor(1, 1).alpha(), 128)
        if result.mime == "image/png":
            self.assertEqual(decoded.pixelColor(1, 1).red(), 80)

    def test_webp_keeps_all_256_alpha_values_exact(self):
        from PyQt5.QtGui import QColor
        if bridge._webp_encoder() is None:
            self.skipTest("Krita's optional libwebp codec is not installed")
        image = self.image(256, 4)
        for column in range(256):
            for row in range(4):
                image.setPixelColor(column, row, QColor(40 + column % 80, 90, 180, column))
        result = bridge.prepare_reference(self.snapshot(image))
        decoded = bridge.QImage.fromData(result.image_bytes)
        self.assertEqual(result.mime, "image/webp")
        self.assertEqual(result.name, "reference.webp")
        self.assertTrue(result.image_bytes.startswith(b"RIFF"))
        self.assertEqual(bridge._image_bytes(decoded)[3::4], bridge._image_bytes(image)[3::4])
        self.assertTrue(result.metrics["alpha_lossless"])

    def test_missing_webp_codec_falls_back_to_png_with_alpha(self):
        image = self.image(20, 10, (80, 120, 160, 128))
        with patch.object(bridge, "_webp_encoder", return_value=None):
            result = bridge.prepare_reference(self.snapshot(image))
        self.assertEqual(result.mime, "image/png")
        self.assertTrue(result.metrics["codec_fallback"])
        self.assertEqual(bridge.QImage.fromData(result.image_bytes).pixelColor(1, 1).alpha(), 128)

    def test_fast_png_fallback_is_compressed_and_keeps_exact_alpha(self):
        image = self.image(512, 512, (80, 120, 160, 128))
        with patch.object(bridge, "_webp_encoder", return_value=None):
            result = bridge.prepare_reference(self.snapshot(image))
        self.assertEqual(result.mime, "image/png")
        # A level-zero PNG of this image is about one MiB. Low compression
        # should retain exact pixels without uploading that uncompressed copy.
        self.assertLess(len(result.image_bytes), 16_384)
        decoded = bridge.QImage.fromData(result.image_bytes, "PNG")
        self.assertEqual(bridge._image_bytes(decoded), bridge._image_bytes(image))

    def test_failed_webp_encoder_falls_back_to_png_with_alpha(self):
        image = self.image(20, 10, (80, 120, 160, 128))
        with patch.object(bridge, "_webp_encoder", return_value=object()), \
                patch.object(bridge, "_encode_webp", side_effect=bridge.BridgeError("codec failed")):
            result = bridge.prepare_reference(self.snapshot(image))
        self.assertEqual(result.mime, "image/png")
        self.assertTrue(result.metrics["codec_fallback"])
        self.assertEqual(bridge.QImage.fromData(result.image_bytes).pixelColor(1, 1).alpha(), 128)

    def test_fast_native_mask_keeps_holes_feathering_and_source_alpha(self):
        image = self.image(4096, 2, (60, 100, 160, 128))
        row = bytes(1024) + bytes([128]) * 2048 + bytes([255]) * 1024
        snapshot = self.snapshot(image, row * 2)
        with patch.object(bridge, "apply_alpha_mask", side_effect=AssertionError("Python alpha loop in fast mode")):
            result = bridge.prepare_reference(snapshot)
        decoded = bridge.QImage.fromData(result.image_bytes)
        self.assertEqual((decoded.width(), decoded.height()), (2048, 1))
        self.assertEqual(decoded.pixelColor(100, 0).alpha(), 0)
        self.assertEqual(decoded.pixelColor(1024, 0).alpha(), 64)
        self.assertEqual(decoded.pixelColor(2000, 0).alpha(), 128)
        self.assertEqual(image.pixelColor(0, 0).alpha(), 128)
        self.assertEqual(result.target.selection_mask, row * 2)

    def test_original_png_preserves_pixels_and_exact_alpha_product(self):
        from PyQt5.QtGui import QColor
        image = self.image(4, 1)
        for column, alpha in enumerate((0, 64, 128, 255)):
            image.setPixelColor(column, 0, QColor(40, 80, 120, alpha))
        original = bridge.prepare_reference(self.snapshot(image), "original")
        decoded_original = bridge.QImage.fromData(original.image_bytes, "PNG")
        self.assertEqual((original.width, original.height), (4, 1))
        self.assertEqual(original.mime, "image/png")
        for column in range(4):
            self.assertEqual(decoded_original.pixelColor(column, 0), image.pixelColor(column, 0))
        masked = bridge.prepare_reference(self.snapshot(image, bytes([255, 128, 128, 0])), "original")
        decoded = bridge.QImage.fromData(masked.image_bytes, "PNG")
        self.assertEqual([decoded.pixelColor(column, 0).alpha() for column in range(4)], [0, 32, 64, 0])
        self.assertEqual([decoded.pixelColor(column, 0).red() for column in range(4)], [40] * 4)

    def test_import_mask_matches_all_alpha_pairs_and_preserves_rgb(self):
        from PyQt5.QtGui import QColor
        image = self.image(256, 256)
        for x in range(256):
            for y in range(256):
                image.setPixelColor(x, y, QColor(41, 83, 127, x))
        mask = b"".join(bytes([y])*256 for y in range(256))
        expected = bridge.apply_alpha_mask(bridge._image_bytes(image), mask)
        self.assertEqual(bridge._import_pixels(image, mask), expected)

    def test_import_opaque_rectangle_skips_per_pixel_python_masking(self):
        image = self.image(100, 80, (40, 80, 120, 128))
        with patch.object(bridge, "apply_alpha_mask", side_effect=AssertionError("per-pixel Python mask")):
            self.assertEqual(bridge._import_pixels(image, bytes([255])*8000), bridge._image_bytes(image))

    def test_preparation_runs_in_worker_without_gui_or_live_document(self):
        snapshot = self.snapshot(self.image(100, 80))
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(bridge.prepare_reference, snapshot).result(timeout=10)
        self.assertEqual(result.mime, "image/jpeg")
        self.assertGreater(result.metrics["bytes"], 0)

    def test_gui_snapshot_detaches_pixels_and_defers_mask_and_encoding(self):
        image = self.image(8, 6)
        target = bridge.Target("snapshot-only-document", 0, 0, 8, 6, "selection", selection_mask=bytes(48))

        class Document:
            def waitForDone(self):
                pass

            def activeNode(self):
                return None

            def selection(self):
                return None

            def projection(self, *args):
                return image

        class Application:
            def activeDocument(self):
                return Document()

        client = bridge.KritaBridge()
        client._gui = lambda: Application()
        client._target = lambda document, source, **context: target
        with patch.object(bridge, "_encode_png", side_effect=AssertionError("GUI PNG encoding")), \
                patch.object(bridge, "apply_alpha_mask", side_effect=AssertionError("GUI alpha loop")):
            snapshot = client.snapshot("selection")
        image.fill(0)
        self.assertEqual(snapshot.image.pixelColor(0, 0).alpha(), 255)
        self.assertEqual(snapshot.target.selection_mask, bytes(48))
        self.assertGreaterEqual(snapshot.read_ms, 0)


if __name__ == "__main__":
    unittest.main()
