"""Geometry landmarks survive reference preparation and selection import.

The fake model returns the prepared image unchanged (or uniformly enlarged).
This isolates plugin coordinate mapping from a model changing an object's shape.
"""
import importlib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

try:
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QColor, QImage, QPainter, QPen
except ImportError:
    QImage = None

bridge = editing = None
if QImage is not None:
    package = ModuleType("mori_selection_edit_fixture")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "mori_krita")]
    sys.modules[package.__name__] = package
    bridge = importlib.import_module(package.__name__ + ".krita_bridge")
    editing = importlib.import_module(package.__name__ + ".selection_edit")


def geometry_image(width, height):
    """Coordinate grid, a circle and cylinder edges crossing the edit boundary."""
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor(245, 245, 245, 255))
    painter = QPainter(image)
    painter.setPen(QPen(QColor(210, 210, 210), 1))
    for x in range(0, width, 17):
        painter.drawLine(x, 0, x, height - 1)
    for y in range(0, height, 19):
        painter.drawLine(0, y, width - 1, y)
    # All geometry is fixed in original document coordinates.
    painter.fillRect(106, 0, 5, height, QColor(0, 0, 0))
    painter.fillRect(198, 0, 5, height, QColor(0, 0, 0))
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0))
    painter.drawEllipse(135, 103, 34, 34)
    painter.fillRect(116, 143, 18, 14, QColor(220, 30, 40))
    painter.fillRect(177, 84, 12, 14, QColor(20, 70, 230))
    painter.end()
    return image


@unittest.skipIf(QImage is None, "Run selection geometry checks in Krita's Qt runtime")
class SelectionEditTests(unittest.TestCase):
    def snapshot(self, *, rect=(81, 77, 151, 89), context=(41, 37, 231, 169), mask=None):
        image = geometry_image(360, 280)
        x, y, width, height = rect
        if mask is None:
            mask = bytes([255]) * (width * height)
        target = bridge.Target("frozen-geometry-document", x, y, width, height,
                               "selection", "", "", mask)
        snapshot = bridge.Snapshot(image.copy(*context), target, "selection", context_rect=context)
        return image, snapshot

    def import_reference(self, snapshot, prepared, *, scale=1):
        generated = QImage.fromData(prepared.reference.image_bytes)
        self.assertFalse(generated.isNull())
        if scale != 1:
            generated = generated.scaled(generated.width() * scale, generated.height() * scale,
                                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
        node = Mock()
        node.colorModel.return_value = "RGBA"
        node.colorDepth.return_value = "U8"
        node.colorProfile.return_value = bridge.SRGB_PROFILE
        node.setPixelData.return_value = None  # Real Krita 5.2 binding behavior.
        document = Mock()
        document.width.return_value = 360
        document.height.return_value = 280
        document.colorModel.return_value = "RGBA"
        document.colorDepth.return_value = "U8"
        document.colorProfile.return_value = bridge.SRGB_PROFILE
        document.createNode.return_value = node
        document.rootNode.return_value.addChildNode.return_value = True
        client = bridge.KritaBridge()
        client._gui = lambda: Mock()
        client._resolve = lambda identifier: document
        returned = client.import_png(bridge._encode_png(generated), "Synthetic material edit", snapshot.target,
                                     selection_mapping=prepared.mapping)
        self.assertIs(returned, node)
        payload, x, y, width, height = node.setPixelData.call_args.args
        self.assertEqual((x, y, width, height),
                         (snapshot.target.x, snapshot.target.y, snapshot.target.width, snapshot.target.height))
        self.assertEqual(len(bytes(payload)), width * height * 4)
        return bridge._image_from_bgra(bytes(payload), width, height), node

    @staticmethod
    def dark_positions(image, *, horizontal, fixed, start, end):
        positions = []
        for moving in range(start, end):
            pixel = image.pixelColor(moving, fixed) if horizontal else image.pixelColor(fixed, moving)
            if max(pixel.red(), pixel.green(), pixel.blue()) < 100:
                positions.append(moving)
        return positions

    def assert_landmarks(self, actual, expected):
        # Compare the x coordinates of both cylinder edges on rows near each
        # selection boundary, not just the final image width and height.
        for y in (3, expected.height() - 4):
            actual_x = self.dark_positions(actual, horizontal=True, fixed=y, start=0, end=actual.width())
            expected_x = self.dark_positions(expected, horizontal=True, fixed=y, start=0, end=expected.width())
            self.assertTrue(actual_x and expected_x)
            self.assertLessEqual(abs(min(actual_x) - min(expected_x)), 1)
            self.assertLessEqual(abs(max(actual_x) - max(expected_x)), 1)
        # A circle catches unequal x/y scaling even when the rectangle is right.
        cx, cy = 152 - 81, 120 - 77
        widths = []
        for horizontal, fixed, start, end in ((True, cy, cx - 23, cx + 23),
                                               (False, cx, cy - 23, cy + 23)):
            positions = self.dark_positions(actual, horizontal=horizontal, fixed=fixed, start=start, end=end)
            original = self.dark_positions(expected, horizontal=horizontal, fixed=fixed, start=start, end=end)
            self.assertTrue(positions and original)
            self.assertLessEqual(abs(min(positions) - min(original)), 1)
            self.assertLessEqual(abs(max(positions) - max(original)), 1)
            widths.append(max(positions) - min(positions) + 1)
        self.assertLessEqual(abs(widths[0] - widths[1]), 1)
        red = actual.pixelColor(124 - 81, 150 - 77)
        blue = actual.pixelColor(182 - 81, 90 - 77)
        self.assertGreater(red.red(), 180)
        self.assertLess(red.blue(), 80)
        self.assertGreater(blue.blue(), 180)
        self.assertLess(blue.red(), 80)

    def test_context_round_trip_preserves_circle_and_cross_boundary_edges(self):
        original, snapshot = self.snapshot()
        for aspect, scale, mode in (("16:9", 1, "original"), ("1:1", 2, "original"),
                                    ("3:2", 1, "original"), ("16:9", 1, "fast"),
                                    ("3:2", 1, "fast")):
            with self.subTest(aspect=aspect, provider_scale=scale, reference_mode=mode):
                prepared = editing.prepare_selection_edit(snapshot, aspect=aspect, resolution="1K", mode=mode)
                actual, node = self.import_reference(snapshot, prepared, scale=scale)
                self.assert_landmarks(actual, original.copy(81, 77, 151, 89))
                node.pixelData.assert_not_called()

    def test_mask_holes_and_feathering_are_applied_only_at_final_import(self):
        mask = bytearray([255]) * (151 * 89)
        for y in range(10, 20):
            for x in range(10, 20):
                mask[y * 151 + x] = 0
        for y in range(30, 40):
            for x in range(30, 40):
                mask[y * 151 + x] = 128
        _, snapshot = self.snapshot(mask=bytes(mask))
        prepared = editing.prepare_selection_edit(snapshot, aspect="16:9", resolution="1K", mode="original")
        reference = QImage.fromData(prepared.reference.image_bytes)
        # The full context remains visible to the fake model; selected alpha is
        # sent separately as an inverse PNG mask with the very same frame.
        mapping = prepared.mapping
        sx, sy, _, _ = mapping.selection_rect_in_frame
        hole = (round(sx + 15 * mapping.scale), round(sy + 15 * mapping.scale))
        feather = (round(sx + 35 * mapping.scale), round(sy + 35 * mapping.scale))
        self.assertEqual(reference.pixelColor(*hole).alpha(), 255)
        self.assertEqual(reference.pixelColor(*feather).alpha(), 255)
        edit_mask = QImage.fromData(prepared.mask_bytes, "PNG")
        self.assertEqual((edit_mask.width(), edit_mask.height()), (reference.width(), reference.height()))
        self.assertEqual(edit_mask.pixelColor(*hole).alpha(), 255)  # protected
        # The server composites the provider mask before local clipping. Keep
        # this mask binary so original 128 feathering is not applied twice.
        self.assertEqual(edit_mask.pixelColor(*feather).alpha(), 0)
        self.assertEqual(set(bridge._image_bytes(edit_mask)[3::4]), {0, 255})
        actual, _ = self.import_reference(snapshot, prepared)
        self.assertEqual(actual.pixelColor(15, 15).alpha(), 0)
        self.assertEqual(actual.pixelColor(35, 35).alpha(), 128)
        self.assertEqual(actual.pixelColor(70, 70).alpha(), 255)

    def test_clipped_context_at_canvas_edge_keeps_selection_offset(self):
        original, snapshot = self.snapshot(rect=(0, 0, 151, 89), context=(0, 0, 191, 129))
        prepared = editing.prepare_selection_edit(snapshot, aspect="16:9", resolution="1K", mode="original")
        actual, _ = self.import_reference(snapshot, prepared)
        expected = original.copy(0, 0, 151, 89)
        for y in (4, 84):
            actual_x = self.dark_positions(actual, horizontal=True, fixed=y, start=0, end=151)
            expected_x = self.dark_positions(expected, horizontal=True, fixed=y, start=0, end=151)
            self.assertTrue(actual_x and expected_x)
            self.assertLessEqual(abs(min(actual_x) - min(expected_x)), 1)
            self.assertLessEqual(abs(max(actual_x) - max(expected_x)), 1)

    def test_original_source_alpha_and_feathering_multiply_once(self):
        mask = bytes([128]) * (151 * 89)
        _, snapshot = self.snapshot(mask=mask)
        painter = QPainter(snapshot.image)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(90, 60, 30, 30, QColor(120, 90, 60, 128))
        painter.end()
        prepared = editing.prepare_selection_edit(snapshot, aspect="16:9", resolution="1K", mode="original")
        actual, _ = self.import_reference(snapshot, prepared)
        # Global patch (131..160,97..126), local selected sample (64,35).
        self.assertEqual(actual.pixelColor(64, 35).alpha(), 64)

    def test_wrong_provider_aspect_is_rejected_before_blind_stretch(self):
        _, snapshot = self.snapshot()
        prepared = editing.prepare_selection_edit(snapshot, aspect="16:9", resolution="1K", mode="original")
        wrong = QImage(1024, 1024, QImage.Format_ARGB32)
        wrong.fill(QColor(20, 80, 180))
        with self.assertRaises(bridge.BridgeError):
            editing.map_selection_result(wrong, prepared.mapping, snapshot.target)

    def test_mapping_is_immutable_and_rejects_a_different_target_or_mask(self):
        _, snapshot = self.snapshot()
        prepared = editing.prepare_selection_edit(snapshot, aspect="3:2", resolution="1K", mode="original")
        with self.assertRaises(FrozenInstanceError):
            prepared.mapping.scale = prepared.mapping.scale * 2
        for target in (replace(snapshot.target, x=snapshot.target.x + 1),
                       replace(snapshot.target, document_key="another-document"),
                       replace(snapshot.target, selection_mask=bytes([128]) * (151 * 89))):
            with self.subTest(target=(target.document_key, target.x)), self.assertRaises(bridge.BridgeError):
                prepared.mapping.assert_target(target)

    def test_output_dimensions_use_actual_server_rounding(self):
        _, snapshot = self.snapshot()
        prepared = editing.prepare_selection_edit(snapshot, aspect="3:2", resolution="1K", mode="fast")
        mapping = prepared.mapping
        self.assertEqual((mapping.output_width, mapping.output_height), (1024, 688))
        self.assertEqual(mapping.input_width * mapping.output_height,
                         mapping.input_height * mapping.output_width)
        reference = QImage.fromData(prepared.reference.image_bytes)
        self.assertEqual((reference.width(), reference.height()), (mapping.input_width, mapping.input_height))

    def test_native_task_default_builder_uploads_aligned_context_and_mask(self):
        from test_selection_tasks import FakeGateway
        tasks = importlib.import_module("mori_selection_edit_fixture.selection_tasks")
        _, snapshot = self.snapshot()
        gateway = FakeGateway()
        original_upload = gateway.client.upload_image

        def upload(image, name, mime=None, progress=None):
            value = original_upload(image, name, mime=mime, progress=progress)
            if "蒙版" in name:
                value = {**value, "id": "real-shaped-mask", "url": "/media/real-shaped-mask.png"}
            return value
        gateway.client.upload_image = upload
        summary = tasks.run_selection_task(gateway, snapshot, {
            "message": "只修改材质，保留轮廓", "model": "gpt-image-2.5", "aspect": "16:9",
            "resolution": "1K", "quality": "high", "count": 1,
            "mode": "edit", "reference_mode": "original"}, poll_interval=.001, timeout=.5)
        self.assertEqual(summary["status"], "completed")
        uploads = [call for call in gateway.client.calls if call[0] == "upload"]
        self.assertEqual(len(uploads), 2)
        reference, mask = [QImage.fromData(call[1]) for call in uploads]
        self.assertFalse(reference.isNull() or mask.isNull())
        self.assertEqual(reference.size(), mask.size())
        mapping = gateway.mappings[0]["selection_mapping"]
        self.assertIs(gateway.results[0]["selection_mapping"], mapping)
        generated = [call for call in gateway.client.calls if call[0] == "generate"][0]
        self.assertEqual(generated[1]["edit"], {"size": "1024x576", "maskAssetId": "real-shaped-mask"})
        self.assertIs(gateway.results[0]["target"], snapshot.target)

    def test_oversized_original_frame_is_rejected_before_allocation_or_encoding(self):
        # The source canvas is under Krita's 40 MP capture limit, while its
        # aspect-preserving padded frame exceeds the server's 20 MP limit.
        image = Mock()
        image.isNull.return_value = False
        image.width.return_value, image.height.return_value = 5000, 4500
        target = bridge.Target("large-frozen-document", 81, 77, 151, 89,
                               "selection", "", "", bytes([255]) * (151 * 89))
        snapshot = bridge.Snapshot(image, target, "selection", context_rect=(0, 0, 5000, 4500))
        with patch.object(editing, "QImage") as allocate, patch.object(bridge, "_encode_reference") as encode:
            with self.assertRaises(bridge.BridgeError) as caught:
                editing.prepare_selection_edit(snapshot, aspect="16:9", resolution="1K", mode="original")
        self.assertIn("快速", str(caught.exception))
        allocate.assert_not_called()
        encode.assert_not_called()


if __name__ == "__main__":
    unittest.main()
