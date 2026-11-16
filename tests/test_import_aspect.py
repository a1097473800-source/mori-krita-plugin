"""Aspect, transparency and frozen-target regressions with synthetic results."""
import importlib.util
from pathlib import Path
import sys
import unittest

spec = importlib.util.spec_from_file_location(
    "mori_import_aspect_qa", Path(__file__).resolve().parents[1] / "mori_krita/krita_bridge.py")
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)

try:
    from PyQt5.QtGui import QColor, QPainter
    from krita import Krita, Selection
except ImportError:
    Krita = None


def sample(width, height, alpha=255):
    image = bridge.QImage(width, height, bridge.QImage.Format_ARGB32)
    image.fill(QColor(230, 235, 245, alpha))
    painter = QPainter(image)
    painter.setPen(bridge.Qt.NoPen)
    painter.setBrush(QColor(20, 20, 20, alpha))
    diameter = min(width, height) // 2
    painter.drawEllipse((width - diameter) // 2, (height - diameter) // 2, diameter, diameter)
    painter.fillRect(0, 0, 4, 4, QColor(240, 30, 40, alpha))
    painter.fillRect(width - 4, height - 4, 4, 4, QColor(20, 60, 230, alpha))
    painter.end()
    return image


def opaque_bounds(image, predicate=lambda color: color.alpha() > 127):
    points = [(x, y) for y in range(image.height()) for x in range(image.width())
              if predicate(image.pixelColor(x, y))]
    if not points:
        raise AssertionError("No matching pixels")
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


@unittest.skipIf(bridge.QImage is None, "Run in Krita's actual Qt runtime")
class ImportAspectQtTests(unittest.TestCase):
    def test_wide_and_tall_images_keep_complete_content_and_transparent_margins(self):
        for source, expected in (((120, 60), (0, 20, 80, 60)),
                                 ((60, 120), (20, 0, 60, 80)),
                                 ((80, 80), (0, 0, 80, 80))):
            with self.subTest(source=source):
                result = bridge._fit_import_image(sample(*source), 80, 80)
                self.assertEqual(opaque_bounds(result), expected)
                colors = [result.pixelColor(x, y) for y in range(80) for x in range(80)]
                self.assertTrue(any(c.alpha() and c.red() > 200 and c.blue() < 100 for c in colors))
                self.assertTrue(any(c.alpha() and c.blue() > 200 and c.red() < 100 for c in colors))

    def test_circles_remain_round_in_square_wide_and_tall_targets(self):
        for source in ((120, 60), (60, 120), (80, 80), (160, 90), (90, 160)):
            for target in ((80, 80), (120, 60), (60, 120)):
                with self.subTest(source=source, target=target):
                    result = bridge._fit_import_image(sample(*source), *target)
                    x0, y0, x1, y1 = opaque_bounds(result, lambda c: c.alpha() > 127 and max(c.red(), c.green(), c.blue()) < 80)
                    self.assertLessEqual(abs((x1 - x0) - (y1 - y0)), 1)
                    l, t, r, b = opaque_bounds(result)
                    self.assertLessEqual(abs(l - (target[0] - r)), 1)
                    self.assertLessEqual(abs(t - (target[1] - b)), 1)

    def test_padding_keeps_source_alpha_rgb_and_mask_in_target_coordinates(self):
        image = bridge.QImage(4, 2, bridge.QImage.Format_ARGB32)
        image.fill(QColor(40, 80, 120, 128))
        result = bridge._fit_import_image(image, 4, 4)
        self.assertEqual(result.pixelColor(0, 1), QColor(40, 80, 120, 128))
        self.assertEqual(result.pixelColor(0, 0).alpha(), 0)
        mask = bytes([255] * 4 + [128, 0, 255, 255] + [255] * 8)
        masked = bridge._image_from_bgra(bridge._import_pixels(result, mask), 4, 4)
        self.assertEqual(masked.pixelColor(0, 1).alpha(), 64)
        self.assertEqual(masked.pixelColor(1, 1).alpha(), 0)
        self.assertEqual(masked.pixelColor(0, 0).alpha(), 0)

    def test_identical_size_does_not_resample_mapped_selection_pixels(self):
        image = sample(31, 17, 128)
        self.assertIs(bridge._fit_import_image(image, 31, 17), image)

    def test_odd_dimensions_stay_centred_and_extreme_aspects_do_not_fail(self):
        result = bridge._fit_import_image(sample(120, 60), 81, 80)
        left, top, right, bottom = opaque_bounds(result)
        self.assertLessEqual(abs(left - (81 - right)), 1)
        self.assertLessEqual(abs(top - (80 - bottom)), 1)
        for source in ((40000, 1), (1, 40000)):
            image = bridge.QImage(*source, bridge.QImage.Format_ARGB32)
            image.fill(QColor(1, 2, 3, 255))
            self.assertFalse(bridge._fit_import_image(image, 8, 8).isNull())


@unittest.skipIf(Krita is None, "Run through official kritarunner")
class ImportAspectNativeTests(unittest.TestCase):
    def setUp(self):
        native = Krita.instance()
        self.documents = []
        self.native = native
        test = self

        class SelectedDocuments:
            active = None

            def documents(self):
                return native.documents()

            def activeDocument(self):
                return self.active

            def createDocument(self, *args):
                doc = native.createDocument(*args)
                test.documents.append(doc)
                return doc

            def activeWindow(self):
                return self

            def addView(self, doc):
                self.active = doc

        self.selected = SelectedDocuments()
        self.client = bridge.KritaBridge(self.selected)

    def document(self, width, height):
        doc = self.selected.createDocument(width, height, "Synthetic aspect QA", "RGBA", "U8", bridge.SRGB_PROFILE, 72.0)
        self.selected.active = doc
        return doc

    def tearDown(self):
        for doc in reversed(self.documents):
            doc.setModified(False)
            doc.close()

    def test_native_layers_keep_shape_and_original_layers_for_every_target_ratio(self):
        for width, height in ((80, 80), (120, 60), (60, 120)):
            doc = self.document(width, height)
            base = doc.createNode("Original artwork", "paintlayer")
            pixels = bytes([45, 65, 85, 255]) * (width * height)
            base.setPixelData(bridge.QByteArray(pixels), 0, 0, width, height)
            doc.rootNode().addChildNode(base, None)
            target = self.client.active_target("canvas")
            for source in ((120, 60), (60, 120), (80, 80)):
                with self.subTest(source=source, target=(width, height)):
                    node = self.client.import_png(bridge._encode_png(sample(*source)), "Aspect result", target)
                    actual = bridge._image_from_bgra(bytes(node.pixelData(0, 0, width, height)), width, height)
                    l, t, r, b = opaque_bounds(actual, lambda c: c.alpha() > 127 and max(c.red(), c.green(), c.blue()) < 80)
                    self.assertLessEqual(abs((r - l) - (b - t)), 1)
                    self.assertEqual((doc.width(), doc.height()), (width, height))
                    self.assertEqual(bytes(base.pixelData(0, 0, width, height)), pixels)

    def test_frozen_selection_position_alpha_and_document_survive_switch(self):
        original = self.document(100, 100)
        selection = Selection()
        mask = bytes([128] * 40 + [255] * 1560)
        selection.select(17, 23, 40, 40, 255)
        selection.setPixelData(bridge.QByteArray(mask), 17, 23, 40, 40)
        original.setSelection(selection)
        target = self.client.active_target("selection")
        other = self.document(50, 50)
        before = len(other.rootNode().childNodes())
        image = bridge.QImage(20, 40, bridge.QImage.Format_ARGB32)
        image.fill(QColor(40, 80, 120, 255))
        node = self.client.import_png(bridge._encode_png(image), "Selection aspect result", target)
        self.assertTrue(any(child.uniqueId() == node.uniqueId() for child in original.rootNode().childNodes()))
        self.assertEqual(len(other.rootNode().childNodes()), before)
        self.assertEqual(bytes(node.pixelData(17, 23, 1, 1))[3], 0)
        self.assertEqual(bytes(node.pixelData(27, 23, 1, 1))[3], 128)
        self.assertEqual(bytes(node.pixelData(27, 24, 1, 1))[3], 255)
        self.assertEqual(bytes(node.pixelData(57, 24, 1, 1))[3], 0)

    def test_new_document_uses_result_dimensions_and_pixels_unchanged(self):
        self.document(80, 80)
        source = sample(120, 60, 128)
        node = self.client.import_png(bridge._encode_png(source), force_new_document=True)
        doc = self.selected.active
        self.assertEqual((doc.width(), doc.height()), (120, 60))
        self.assertEqual(bytes(node.pixelData(0, 0, 120, 60)), bridge._image_bytes(source))

