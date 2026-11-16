"""Official kritarunner entry point for real libkis pixel tests.

Example: kritarunner --script run_krita_smoke --function main QA_DIRECTORY
Set PYTHONPATH to this tests directory. The runner has no native canvas window,
so only its active-document/view selection is supplied by a tiny adapter; all
documents, nodes, conversions, selection masks and pixel buffers are real Krita.
"""

import importlib.util
import importlib
import json
from pathlib import Path
import sys
import traceback
from types import ModuleType


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    try:
        from PyQt5.QtCore import QBuffer, QIODevice, QByteArray
        from PyQt5.QtGui import QImage, QColor
        from krita import Krita, Selection

        package = ModuleType("mori_bridge_runtime_qa")
        package.__path__ = [str(Path(__file__).resolve().parents[1] / "mori_krita")]
        sys.modules[package.__name__] = package
        module = importlib.import_module(package.__name__ + ".krita_bridge")
        selection_edit = importlib.import_module(package.__name__ + ".selection_edit")
        native = Krita.instance()

        class HeadlessDocumentSelection:
            active = None

            def activeDocument(self):
                return self.active

            def documents(self):
                return native.documents()

            def createDocument(self, *args):
                return native.createDocument(*args)

            def activeWindow(self):
                return self

            def addView(self, document):
                self.active = document

        selected = HeadlessDocumentSelection()
        bridge = module.KritaBridge(selected)
        original = native.createDocument(64, 48, "Mori bridge CLI QA", "RGBA", "U8", module.SRGB_PROFILE, 72.0)
        selected.active = original
        target = bridge.active_target("canvas")
        assert target.document_key
        image = QImage(4, 4, QImage.Format_ARGB32)
        image.fill(QColor(220, 40, 10, 255))
        image.setPixelColor(0, 0, QColor(10, 40, 220, 128))
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        assert image.save(buffer, "PNG")
        encoded = bytes(buffer.data())
        node = bridge.import_png(encoded, "Synthetic generated result", target)
        assert bytes(node.pixelData(55, 47, 1, 1)) == bytes([10, 40, 220, 255])
        assert bytes(node.pixelData(0, 0, 1, 1))[3] == 0
        assert bytes(node.pixelData(63, 47, 1, 1))[3] == 0
        first_pixel = bytes(node.pixelData(8, 0, 1, 1))
        # Smooth scaling premultiplies then unpremultiplies alpha, with a 1-unit
        # channel rounding allowance; alpha itself must remain exact.
        assert first_pixel[3] == 128 and all(abs(a - b) <= 1 for a, b in zip(first_pixel[:3], [220, 40, 10])), repr(first_pixel)
        selection = Selection()
        selection.select(10, 12, 8, 6, 255)
        selection.setPixelData(QByteArray(bytes([0, 128] + [255] * 46)), 10, 12, 8, 6)
        original.setSelection(selection)
        capture = bridge.capture("selection")
        assert (capture.x, capture.y, capture.width, capture.height) == (10, 12, 8, 6)
        (output / "captured-selection.png").write_bytes(capture.png_bytes)
        frozen = capture.target
        selection.move(30, 30)
        original.setSelection(selection)
        other = native.createDocument(16, 16, "Mori bridge second CLI QA", "RGBA", "U8", module.SRGB_PROFILE, 72.0)
        other_layer_ids = [child.uniqueId().toString() for child in other.rootNode().childNodes()]
        selected.active = other
        solid_image = QImage(4, 4, QImage.Format_ARGB32)
        solid_image.fill(QColor(220, 40, 10, 255))
        solid_buffer = QBuffer()
        solid_buffer.open(QIODevice.WriteOnly)
        assert solid_image.save(solid_buffer, "PNG")
        placed = bridge.import_png(bytes(solid_buffer.data()), "Frozen selection result", frozen)
        assert any(child.uniqueId() == placed.uniqueId() for child in original.rootNode().childNodes())
        assert bytes(placed.pixelData(10, 12, 1, 1))[3] == 0
        assert bytes(placed.pixelData(11, 12, 1, 1))[3] == 128
        assert bytes(placed.pixelData(30, 30, 1, 1))[3] == 0
        assert [child.uniqueId().toString() for child in other.rootNode().childNodes()] == other_layer_ids
        standalone = bridge.import_png(encoded, "New standalone result", force_new_document=True)
        assert bytes(standalone.pixelData(0, 0, 1, 1)) == bytes([220, 40, 10, 128])
        created_target = bridge.last_import_target
        assert created_target.document_key not in (target.document_key, frozen.document_key)
        closed = bridge._resolve(created_target.document_key)
        closed.setModified(False)
        closed.close()
        rejected = False
        try:
            bridge.import_png(encoded, "Must not appear", created_target)
        except module.BridgeError:
            rejected = True
        assert rejected
        # Also exercise conversion of the newly created layer into a 16-bit image.
        high_depth = native.createDocument(8, 8, "Mori bridge U16 CLI QA", "RGBA", "U16", module.SRGB_PROFILE, 72.0)
        selected.active = high_depth
        high_target = bridge.active_target("canvas")
        high_node = bridge.import_png(encoded, "16-bit result", high_target)
        assert high_node.colorDepth() == "U16"
        assert len(bytes(high_node.pixelData(0, 0, 1, 1))) == 8
        high_capture = bridge.capture("canvas")
        decoded_high = QImage.fromData(high_capture.png_bytes, "PNG")
        assert decoded_high.pixelColor(7, 7).red() == 220
        assert decoded_high.pixelColor(7, 7).blue() == 10
        # Geometry round trip through the real 5.2/5.3 native APIs. Returning the
        # prepared frame unchanged is a fake model that edits no geometry; a
        # plugin mapping error is therefore measurable without a paid request.
        from test_selection_edit import geometry_image, SelectionEditTests
        geometric = native.createDocument(360, 280, "Selection geometry CLI QA", "RGBA", "U8", module.SRGB_PROFILE, 72.0)
        selected.active = geometric
        source_image = geometry_image(360, 280)
        base = geometric.createNode("Synthetic cylinder and circle", "paintlayer")
        base.setPixelData(QByteArray(module._image_bytes(source_image)), 0, 0, 360, 280)
        assert geometric.rootNode().addChildNode(base, None)
        geometric.setActiveNode(base)
        geometric.waitForDone()
        local_mask = bytearray([255]) * (151 * 89)
        for y in range(10, 20):
            for x in range(10, 20):
                local_mask[y * 151 + x] = 0
        for y in range(30, 40):
            for x in range(30, 40):
                local_mask[y * 151 + x] = 128
        geometric_selection = Selection()
        geometric_selection.select(81, 77, 151, 89, 255)
        geometric_selection.setPixelData(QByteArray(bytes(local_mask)), 81, 77, 151, 89)
        geometric.setSelection(geometric_selection)
        geometric_snapshot = bridge.snapshot("selection", context_padding=True)
        assert geometric_snapshot.context_rect is not None
        prepared = selection_edit.prepare_selection_edit(geometric_snapshot, aspect="16:9", resolution="1K", mode="original")
        geometric_selection.move(0, 0)
        geometric.setSelection(geometric_selection)
        selected.active = other
        other_before_geometry = [child.uniqueId().toString() for child in other.rootNode().childNodes()]
        mapped = bridge.import_png(prepared.reference.image_bytes, "Identity frame geometry result", geometric_snapshot.target,
                                   selection_mapping=prepared.mapping)
        assert any(child.uniqueId() == mapped.uniqueId() for child in geometric.rootNode().childNodes())
        actual_geometry = module._image_from_bgra(bytes(mapped.pixelData(81, 77, 151, 89)), 151, 89)
        expected_geometry = source_image.copy(81, 77, 151, 89)
        SelectionEditTests().assert_landmarks(actual_geometry, expected_geometry)
        assert actual_geometry.pixelColor(15, 15).alpha() == 0
        assert actual_geometry.pixelColor(35, 35).alpha() == 128
        assert actual_geometry.pixelColor(70, 70).alpha() == 255
        assert bytes(mapped.pixelData(80, 77, 1, 1))[3] == 0
        assert bytes(mapped.pixelData(81, 76, 1, 1))[3] == 0
        assert bytes(mapped.pixelData(232, 165, 1, 1))[3] == 0
        assert [child.uniqueId().toString() for child in other.rootNode().childNodes()] == other_before_geometry
        assert actual_geometry.save(str(output / "selection-geometry.png"), "PNG")
        assert original.projection(0, 0, original.width(), original.height()).save(str(output / "bridge-qa.png"), "PNG")
        report = {"passed": True, "runtime": native.version(), "runner": "official kritarunner",
                  "headless_active_document_adapter": True,
                  "bgra_and_alpha": True, "selection_mask_and_offset": True,
                  "switch_document_keeps_original_target": True,
                  "closed_target_rejected": True, "force_new_document": True,
                  "rgba_u16_conversion_and_capture": True,
                  "selection_context_circle_and_boundary_edges": True,
                  "selection_context_mask_and_original_document": True}
    except Exception:
        report = {"passed": False, "traceback": traceback.format_exc()}
    (output / "bridge-qa.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
