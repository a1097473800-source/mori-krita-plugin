"""Run run(output_directory) in Krita's main thread; uses synthetic pixels only.

This script does not access the website, credentials, or user documents. It creates
QA documents and leaves them open so callers can inspect or save screenshots.
"""

import json
from pathlib import Path

from PyQt5.QtCore import QBuffer, QIODevice, QByteArray
from PyQt5.QtGui import QImage, QColor
from krita import Krita, Selection

from mori_krita.krita_bridge import BridgeError, KritaBridge, SRGB_PROFILE


def png(image):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def run(output_directory):
    directory = Path(output_directory)
    directory.mkdir(parents=True, exist_ok=True)
    app = Krita.instance()
    bridge = KritaBridge()
    original = app.createDocument(64, 48, "Mori bridge QA", "RGBA", "U8", SRGB_PROFILE, 72.0)
    app.activeWindow().addView(original)
    base = original.createNode("Synthetic base", "paintlayer")
    original.rootNode().addChildNode(base, None)
    original.setActiveNode(base)
    original.refreshProjection()
    original.waitForDone()
    image = QImage(4, 4, QImage.Format_ARGB32)
    image.fill(QColor(220, 40, 10, 255))
    image.setPixelColor(0, 0, QColor(10, 40, 220, 128))
    encoded = png(image)
    target = bridge.active_target("canvas")
    node = bridge.import_png(encoded, "Synthetic generated result", target)
    original.waitForDone()
    pixel = bytes(node.pixelData(55, 47, 1, 1))
    assert pixel == bytes([10, 40, 220, 255]), repr(pixel)
    assert bytes(node.pixelData(0, 0, 1, 1))[3] == 0
    assert bytes(node.pixelData(63, 47, 1, 1))[3] == 0
    first = bytes(node.pixelData(8, 0, 1, 1))
    assert first[3] == 128 and all(abs(a - b) <= 1 for a, b in zip(first[:3], [220, 40, 10])), repr(first)
    selection = Selection()
    selection.select(10, 12, 8, 6, 255)
    selection.setPixelData(QByteArray(bytes([0, 128] + [255] * 46)), 10, 12, 8, 6)
    original.setSelection(selection)
    capture = bridge.capture("selection")
    assert (capture.x, capture.y, capture.width, capture.height) == (10, 12, 8, 6)
    frozen = capture.target
    selection.move(30, 30)
    original.setSelection(selection)
    other = app.createDocument(16, 16, "Mori bridge second QA", "RGBA", "U8", SRGB_PROFILE, 72.0)
    other_layer_ids = [child.uniqueId().toString() for child in other.rootNode().childNodes()]
    app.activeWindow().addView(other)
    solid_image = QImage(4, 4, QImage.Format_ARGB32)
    solid_image.fill(QColor(220, 40, 10, 255))
    placed = bridge.import_png(png(solid_image), "Frozen selection result", frozen)
    assert original.activeNode().uniqueId() == placed.uniqueId()
    assert bytes(placed.pixelData(10, 12, 1, 1))[3] == 0
    assert bytes(placed.pixelData(11, 12, 1, 1))[3] == 128
    assert bytes(placed.pixelData(30, 30, 1, 1))[3] == 0
    assert [child.uniqueId().toString() for child in other.rootNode().childNodes()] == other_layer_ids
    standalone = bridge.import_png(encoded, "New standalone result", force_new_document=True)
    assert bytes(standalone.pixelData(0, 0, 1, 1)) == bytes([220, 40, 10, 128])
    created_target = bridge.last_import_target
    assert created_target.document_key not in (target.document_key, frozen.document_key)
    # A closed, explicitly captured target must not silently fall back to `other`.
    closed = bridge._resolve(created_target.document_key)
    closed.setModified(False)
    closed.close()
    closed_rejected = False
    try:
        bridge.import_png(encoded, "Must not appear", created_target)
    except BridgeError:
        closed_rejected = True
    assert closed_rejected
    assert original.projection(0, 0, original.width(), original.height()).save(str(directory / "bridge-qa.png"), "PNG")
    report = {"passed": True, "bgra_and_alpha": True, "selection_mask_and_offset": True,
              "switch_document_keeps_original_target": True, "closed_target_rejected": True,
              "force_new_document": True}
    (directory / "bridge-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
