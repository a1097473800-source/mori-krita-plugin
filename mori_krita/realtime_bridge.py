"""GUI-thread layer snapshots for realtime; generated layers never become inputs."""
from __future__ import annotations

import hashlib
import json
from time import perf_counter

from PyQt5.QtCore import Qt
from .krita_bridge import Snapshot, Target, BridgeError, _image_bytes, _validate_size
from .reference_presets import CANVAS_SOURCE

OUTPUT_PREFIX = "Mori 实时 · "


class RealtimeBusy(Exception):
    pass


def layer_choices(bridge):
    application = bridge._gui()
    document = application.activeDocument()
    if document is None:
        return None, []
    result = []

    def includes_output(node):
        return node.name().startswith(OUTPUT_PREFIX) or any(includes_output(child) for child in node.childNodes())

    def visit(node, prefix=""):
        if node.name().startswith(OUTPUT_PREFIX):
            return
        children = node.childNodes()
        contains_output = any(includes_output(child) for child in children)
        if node.type() in ("paintlayer", "grouplayer", "vectorlayer", "filelayer", "clonelayer") and not contains_output:
            result.append({"id": node.uniqueId().toString(), "name": prefix + node.name(),
                           "visible": node.visible(), "node": node})
        for child in reversed(children):
            visit(child, prefix + node.name() + " / ")

    for node in reversed(document.rootNode().childNodes()):
        visit(node)
    return document, result


def document_key(bridge, document):
    return bridge._register(document)


def _nodes(bridge, subject_id, reference_ids):
    document, choices = layer_choices(bridge)
    if document is None:
        raise BridgeError("请先新建或打开 Krita 画布。")
    nodes = {item["id"]: item["node"] for item in choices}
    identifiers = list(dict.fromkeys(([subject_id] if subject_id != CANVAS_SOURCE else []) + list(reference_ids)))
    if not subject_id or any(identifier not in nodes for identifier in identifiers):
        raise BridgeError("输入图层已改变，请重新选择主体与参考图层。")
    return document, [nodes[identifier] for identifier in identifiers]


def _visible_inputs(document):
    """Hash actual drawing inputs, excluding generated overlays and hidden art."""
    result = []

    def visit(node, parent_visible=True):
        if node.name().startswith(OUTPUT_PREFIX):
            return
        visible = parent_visible and node.visible()
        if not visible:
            return
        children = node.childNodes()
        result.append((node, not children))
        for child in children:
            visit(child, visible)

    for node in document.rootNode().childNodes():
        visit(node)
    return result


def _outputs(document):
    result = []

    def visit(node):
        if node.name().startswith(OUTPUT_PREFIX):
            if node.visible():
                result.append(node)
            return
        for child in node.childNodes():
            visit(child)
    visit(document.rootNode())
    return result


def _canvas_copy(document):
    # Keep blend modes, masks, native colour conversion and visibility intact.
    # A clone is needed only when a generated overlay is visible; the original
    # document is never toggled, dirtied or changed just to capture an input.
    duplicate = document.clone()
    if duplicate is None:
        raise BridgeError("暂时无法读取画布，请停笔后重试。")
    try:
        for node in _outputs(duplicate):
            node.setVisible(False)
        duplicate.refreshProjection()
        duplicate.waitForDone()
        return duplicate.projection(0, 0, duplicate.width(), duplicate.height()).copy()
    finally:
        duplicate.setModified(False)
        duplicate.close()


def source_signature(bridge, subject_id, reference_ids):
    document, nodes = _nodes(bridge, subject_id, reference_ids)
    barrier = getattr(document, "tryBarrierLock", None)
    if not callable(barrier) or not barrier():
        raise RealtimeBusy()
    try:
        digest = hashlib.sha256()
        digest.update((document_key(bridge, document) + ":%dx%d" % (document.width(), document.height())).encode())
        inputs = (_visible_inputs(document) if subject_id == CANVAS_SOURCE else [])
        inputs += [(node, True) for node in nodes]
        digest.update(subject_id.encode())
        for node, include_pixels in inputs:
            bounds = node.bounds()
            rect = [bounds.x(), bounds.y(), bounds.width(), bounds.height()] if include_pixels else []
            digest.update(json.dumps([node.uniqueId().toString(), rect, node.opacity(), node.visible(),
                                      node.blendingMode(), node.type()]).encode())
            if include_pixels:
                image = node.thumbnail(192, 192).convertToFormat(5)  # QImage.Format_ARGB32
                digest.update(_image_bytes(image))
        return digest.hexdigest()
    finally:
        document.unlock()


def capture_layers(bridge, subject_id, reference_ids):
    started = perf_counter()
    document, nodes = _nodes(bridge, subject_id, reference_ids)
    width, height = document.width(), document.height()
    _validate_size(width, height)
    barrier = getattr(document, "tryBarrierLock", None)
    if not callable(barrier) or not barrier():
        raise RealtimeBusy()
    locked = True
    try:
        key = document_key(bridge, document)
        name = bridge._document_name(document)
        snapshots = []
        if subject_id == CANVAS_SOURCE:
            if _outputs(document):
                # Krita clone() obtains its own locks. Never clone while holding
                # the source barrier, which would deadlock the GUI thread.
                document.unlock()
                locked = False
                image = _canvas_copy(document)
                if not barrier():
                    raise RealtimeBusy()
                locked = True
            else:
                image = document.projection(0, 0, width, height).copy()
            if image.isNull():
                raise BridgeError("无法读取当前画布。")
            target = Target(key, 0, 0, width, height, "canvas", name)
            snapshots.append(Snapshot(image, target, "canvas", name, "当前画布",
                                      (perf_counter() - started) * 1000))
        for index, node in enumerate(nodes):
            # The primary layer retains the complete document coordinate frame.
            # Supporting images are cropped to their contents to avoid empty margins.
            bounds = node.bounds()
            x, y, w, h = (0, 0, width, height) if index == 0 and subject_id != CANVAS_SOURCE else (
                max(0, bounds.x()), max(0, bounds.y()),
                min(width, bounds.x() + bounds.width()) - max(0, bounds.x()),
                min(height, bounds.y() + bounds.height()) - max(0, bounds.y()))
            if w <= 0 or h <= 0:
                raise BridgeError("参考图层“%s”没有可读取的像素。" % node.name())
            target = Target(key, x, y, w, h, "layer", name, node.uniqueId().toString())
            image = bridge._capture_layer(document, node, target).copy()
            if image.isNull():
                raise BridgeError("无法读取图层“%s”。" % node.name())
            snapshots.append(Snapshot(image, target, "layer", name, node.name(),
                                      (perf_counter() - started) * 1000))
        return snapshots
    finally:
        if locked:
            document.unlock()


def import_result(bridge, result):
    target = result["target"]
    document = bridge._resolve(target.document_key)
    current = bridge._gui().activeDocument()
    if current != document:
        raise BridgeError("请切回原画布后放入结果。")
    active = document.activeNode()
    old = [node for node in document.rootNode().childNodes() if node.name().startswith(OUTPUT_PREFIX)]
    from datetime import datetime
    name = OUTPUT_PREFIX + datetime.now().strftime("%H:%M:%S")
    try:
        node = bridge.import_png(result["bytes"], name, target)
        for previous in old:
            previous.setVisible(False)
        document.refreshProjection()
        return node
    finally:
        # Keep drawing on the chosen input, rather than feeding an AI result back.
        if active is not None:
            document.setActiveNode(active)
