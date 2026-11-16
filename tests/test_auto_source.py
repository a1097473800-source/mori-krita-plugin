"""Automatic import-source and stable-read tests; no user documents or network."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import io
import json
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "mori_krita" / "krita_bridge.py"
spec = importlib.util.spec_from_file_location("mori_auto_source_fixture", path)
bridge = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = bridge
spec.loader.exec_module(bridge)


class Rect:
    def __init__(self, x=2, y=3, width=8, height=6):
        self.values = x, y, width, height

    def x(self): return self.values[0]
    def y(self): return self.values[1]
    def width(self): return self.values[2]
    def height(self): return self.values[3]


class Selection(Rect):
    def pixelData(self, x, y, width, height):
        return bytes([255]) * (width * height)


class Image:
    def __init__(self, width, height):
        self.w, self.h = width, height

    def isNull(self): return False
    def width(self): return self.w
    def height(self): return self.h
    def copy(self): return Image(self.w, self.h)


class Node:
    def __init__(self, kind="paintlayer", bounds=None):
        self.kind, self.rect = kind, bounds or Rect()

    def type(self): return self.kind
    def bounds(self): return self.rect
    def uniqueId(self): return SimpleNamespace(toString=lambda: "layer-1")
    def name(self): return "QA layer"
    def colorModel(self): return "RGBA"
    def colorDepth(self): return "U8"
    def colorProfile(self): return bridge.SRGB_PROFILE


class Document:
    def __init__(self, selection=None, node=None, idle=True):
        self.selection_value, self.node, self.idle = selection, node, idle
        self.selection_reads = self.waits = self.locks = self.unlocks = 0

    def selection(self):
        self.selection_reads += 1
        return self.selection_value

    def activeNode(self): return self.node
    def width(self): return 64
    def height(self): return 48
    def name(self): return "QA document"
    def fileName(self): return ""
    def tryBarrierLock(self):
        self.locks += 1
        return self.idle
    def unlock(self): self.unlocks += 1
    def waitForDone(self): self.waits += 1
    def projection(self, x, y, width, height): return Image(width, height)


class AutoSourceTests(unittest.TestCase):
    def client(self, document):
        result = bridge.KritaBridge()
        result._gui = lambda: SimpleNamespace(activeDocument=lambda: document)
        result._capture_layer = lambda doc, node, target: Image(target.width, target.height)
        return result

    def test_nonempty_selection_has_priority_over_layer(self):
        document = Document(Selection(), Node())
        self.assertEqual(self.client(document).detect_source(), "selection")
        self.assertEqual(document.selection_reads, 1)

    def test_empty_selection_uses_current_layer(self):
        document = Document(Selection(width=0, height=0), Node())
        self.assertEqual(self.client(document).detect_source(), "layer")

    def test_visible_pixel_group_is_a_valid_automatic_layer(self):
        self.assertEqual(self.client(Document(node=Node("grouplayer"))).detect_source(), "layer")

    def test_mask_or_empty_layer_falls_back_to_canvas(self):
        for node in (None, Node("transparencymask"), Node(bounds=Rect(width=0, height=0))):
            with self.subTest(node=node):
                self.assertEqual(self.client(Document(node=node)).detect_source(), "canvas")

    def test_no_document_detect_raises_but_active_target_is_none(self):
        client = self.client(None)
        self.assertIsNone(client.active_target("auto"))
        with self.assertRaises(bridge.BridgeError):
            client.detect_source()

    def test_snapshot_auto_reads_selection_once_and_records_real_source(self):
        document = Document(Selection(), Node())
        snapshot = self.client(document).snapshot("auto")
        self.assertEqual(document.selection_reads, 1)
        self.assertEqual(snapshot.source, "selection")
        self.assertEqual(snapshot.target.source, "selection")
        self.assertEqual((snapshot.width, snapshot.height), (8, 6))
        self.assertEqual(snapshot.target.selection_mask, bytes([255])*48)
        self.assertEqual(document.waits, 0)
        self.assertEqual(document.unlocks, 1)
        self.assertIn("projection_ms", snapshot.read_timings)

    def test_snapshot_without_selection_captures_layer_bounds_once(self):
        document = Document(node=Node())
        snapshot = self.client(document).snapshot()
        self.assertEqual(snapshot.source, "layer")
        self.assertEqual((snapshot.x if hasattr(snapshot, 'x') else snapshot.target.x,
                          snapshot.target.y), (2, 3))
        self.assertEqual(document.selection_reads, 1)

    def test_explicit_canvas_does_not_read_selection(self):
        document = Document(Selection(), Node())
        snapshot = self.client(document).snapshot("canvas")
        self.assertEqual(snapshot.source, "canvas")
        self.assertEqual(document.selection_reads, 0)

    def test_busy_document_preserves_the_synchronization_fallback(self):
        document = Document(idle=False)
        self.client(document).snapshot("canvas")
        self.assertEqual(document.waits, 1)
        self.assertEqual(document.unlocks, 0)

    def test_barrier_is_released_if_projection_fails(self):
        document = Document()
        document.projection = lambda *args: (_ for _ in ()).throw(RuntimeError("QA failure"))
        with self.assertRaises(RuntimeError):
            self.client(document).snapshot("canvas")
        self.assertEqual(document.unlocks, 1)

    def test_full_rectangular_alpha_mask_uses_original_byte_buffer(self):
        pixels = bytes([12, 23, 34, 128])*128
        self.assertIs(bridge.apply_alpha_mask(pixels, bytes([255])*128), pixels)

    def test_opaque_generated_image_uses_exact_feathered_mask_without_pixel_loop(self):
        pixels = bytes([12, 23, 34, 255])*1024
        mask = bytes(range(256))*4
        with patch.object(bridge, "zip", side_effect=AssertionError("per-pixel Python loop"), create=True):
            result = bridge.apply_alpha_mask(pixels, mask)
        self.assertEqual(result[3::4], mask)
        self.assertEqual(result[0::4], pixels[0::4])


@unittest.skipIf(bridge.Krita is None or bridge.QImage is None, "Real Krita API tests run in official runner")
class NativeAutoSourceTests(unittest.TestCase):
    def setUp(self):
        self.native = bridge.Krita.instance()
        self.document = self.native.createDocument(64, 48, "Mori auto-source synthetic QA", "RGBA", "U8", bridge.SRGB_PROFILE, 72.0)
        document = self.document

        class HeadlessSelection:
            def activeDocument(self): return document
            def documents(self): return self_native.documents()
        self_native = self.native
        self.client = bridge.KritaBridge(HeadlessSelection())
        # Official kritarunner has no real active view/node. Only that choice is
        # adapted here, while actual nodes/pixels/masks/image waits are native.
        self.layer = self.document.createNode("Synthetic source", "paintlayer")
        image = bridge.QImage(64, 48, bridge.QImage.Format_ARGB32)
        from PyQt5.QtGui import QColor
        image.fill(QColor(180, 70, 30, 255))
        self.layer.setPixelData(bridge.QByteArray(bridge._image_bytes(image)), 0, 0, 64, 48)
        self.document.rootNode().addChildNode(self.layer, None)
        self.client._detect_context_original = self.client._detect_context
        self.client._detect_context = lambda doc, selection=bridge._UNSET, node=bridge._UNSET: self.client._detect_context_original(doc, selection, self.layer)

    def tearDown(self):
        self.document.setModified(False)
        self.document.close()

    def test_native_auto_selection_and_layer_and_blank_canvas(self):
        from krita import Selection
        selection = Selection()
        selection.select(6, 4, 10, 8, 255)
        self.document.setSelection(selection)
        self.document.waitForDone()
        selected = self.client.snapshot("auto")
        self.assertEqual(selected.source, "selection")
        self.assertEqual(selected.target.selection_mask, bytes([255])*80)
        self.assertEqual((selected.width, selected.height), (10, 8))
        self.document.setSelection(None)
        self.document.waitForDone()
        layer = self.client.snapshot("auto")
        self.assertEqual(layer.source, "layer")
        self.assertEqual(layer.image.pixelColor(63, 47).red(), 180)
        self.layer = self.document.createNode("Blank source", "paintlayer")
        canvas = self.client.snapshot("auto")
        self.assertEqual(canvas.source, "canvas")

    def test_new_layer_projection_is_visible_without_full_refresh(self):
        from PyQt5.QtGui import QColor
        image = bridge.QImage(64, 48, bridge.QImage.Format_ARGB32)
        image.fill(QColor(20, 130, 220, 255))
        encoded = bridge._encode_png(image)
        target = self.client.active_target("canvas")
        self.client.import_png(encoded, "Synthetic imported result", target)
        projected = self.document.projection(0, 0, 64, 48)
        self.assertEqual(projected.pixelColor(63, 47), QColor(20, 130, 220, 255))
        self.assertGreaterEqual(self.client.last_import_metrics["attach_ms"], 0)


def main(arguments):
    directory = Path(arguments[0])
    directory.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "skipped": len(result.skipped), "user_document_reads": 0}
    (directory / "auto-source-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    (directory / "auto-source-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    unittest.main()
