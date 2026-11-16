"""Detached, uniform image mapping for edits of a frozen Krita selection.

The context image, uploaded source, model output and final selection have distinct
coordinate systems. This module records their relationship rather than stretching
the complete model result into the selected rectangle.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import sys
from time import perf_counter
from typing import Any

try:
    from PyQt5.QtCore import QRectF, Qt
    from PyQt5.QtGui import QImage, QPainter
except ImportError:
    QRectF = Qt = QImage = QPainter = None


def _bridge_for(value):
    # Snapshot/Target can come from a standalone official kritarunner loader.
    # Use that same module's classes and errors rather than importing a duplicate.
    module = sys.modules.get(type(value).__module__)
    if module is None or not hasattr(module, "BridgeError") or not hasattr(module, "Reference"):
        raise RuntimeError("选区修改需要有效的 Krita 图像快照")
    return module


def _mask_digest(target):
    return hashlib.sha256(target.selection_mask).hexdigest()


@dataclass(frozen=True)
class SelectionEditMapping:
    document_key: str
    target_rect: tuple[int, int, int, int]
    mask_sha256: str
    context_rect: tuple[int, int, int, int]
    output_width: int
    output_height: int
    input_width: int
    input_height: int
    scale: float
    padding_x: float
    padding_y: float
    selection_rect_in_frame: tuple[float, float, float, float]

    @property
    def input_selection_rect(self):
        """The editable bounds in uploaded-reference pixels, for prompts/UI."""
        return self.selection_rect_in_frame

    def assert_target(self, target):
        bridge = _bridge_for(target)
        if (target.document_key != self.document_key or
                (target.x, target.y, target.width, target.height) != self.target_rect or
                _mask_digest(target) != self.mask_sha256):
            raise bridge.BridgeError("结果与提交时的原选区不匹配，图片仍保留在 Mori")


@dataclass(frozen=True)
class SelectionEditReference:
    reference: Any
    mapping: SelectionEditMapping
    mask_bytes: bytes


def _output_size(aspect, resolution):
    """Match the existing server's imageSize, including JS positive rounding."""
    first, second = str(aspect).split(":")
    first, second = float(first), float(second)
    if not all(math.isfinite(value) and value > 0 for value in (first, second)):
        raise ValueError("Invalid aspect")
    if resolution not in ("1K", "2K", "4K"):
        raise ValueError("Invalid resolution")
    edge = {"1K": 1024, "2K": 2560, "4K": 3840}[resolution]
    width = max(16, math.floor((edge if first >= second else edge * first / second) / 16 + .5) * 16)
    height = max(16, math.floor((edge if second >= first else edge * second / first) / 16 + .5) * 16)
    if width * height > 8_294_400:
        shrink = math.sqrt(8_294_400 / (width * height))
        width = max(16, math.floor(width * shrink / 16) * 16)
        height = max(16, math.floor(height * shrink / 16) * 16)
    return width, height


def _frame_size(output_width, output_height, context_width, context_height, mode, bridge):
    # Preserve the *actual* requested ratio exactly, including the server's
    # 16-pixel rounding. Independent integer rounding of width/height would make
    # the server's masked compositor stretch the source by different factors.
    divisor = math.gcd(output_width, output_height)
    unit_width, unit_height = output_width // divisor, output_height // divisor
    needed = math.ceil(max(context_width / unit_width, context_height / unit_height))
    if mode == "fast":
        multiplier = min(divisor, needed,
                         bridge.FAST_REFERENCE_MAX_EDGE // max(unit_width, unit_height),
                         math.floor(math.sqrt(bridge.FAST_REFERENCE_MAX_PIXELS / (unit_width * unit_height))))
    elif mode == "original":
        multiplier = min(needed,
                         math.floor(math.sqrt(bridge.MAX_IMAGE_PIXELS / (unit_width * unit_height))))
    else:
        raise bridge.BridgeError("请选择快速参考或原图参考")
    if multiplier < 1:
        raise bridge.BridgeError("选区参考画幅过大，请调整选区或图像比例")
    width, height = unit_width * multiplier, unit_height * multiplier
    # The existing server's masked compositor accepts at most 20M source pixels.
    # Fail while preparing, before uploading or submitting a billable request.
    if width * height > 20_000_000:
        raise bridge.BridgeError("原图修改参考包含上下文后超过 2000 万像素，请使用快速参考或缩小选区")
    return width, height


def prepare_selection_edit(snapshot, *, aspect, resolution, mode="fast"):
    bridge = _bridge_for(snapshot)
    if QImage is None or QPainter is None or snapshot.image.isNull():
        raise bridge.BridgeError("无法准备选区修改参考图")
    started = perf_counter()
    target = snapshot.target
    bridge._validate_target(target)
    context = tuple(snapshot.context_rect or (target.x, target.y, target.width, target.height))
    context_x, context_y, context_width, context_height = context
    bridge._validate_size(context_width, context_height)
    if (snapshot.width, snapshot.height) != (context_width, context_height):
        raise bridge.BridgeError("上下文快照尺寸与原画布区域不一致")
    if (target.x < context_x or target.y < context_y or
            target.x + target.width > context_x + context_width or
            target.y + target.height > context_y + context_height):
        raise bridge.BridgeError("原选区超出修改参考范围")
    try:
        output_width, output_height = _output_size(aspect, resolution)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as error:
        raise bridge.BridgeError("选区修改的输出比例或分辨率无效") from error
    input_width, input_height = _frame_size(output_width, output_height, context_width, context_height, mode, bridge)
    scale = min(1.0, input_width / context_width, input_height / context_height)
    padding_x = math.floor((input_width - context_width * scale) / 2)
    padding_y = math.floor((input_height - context_height * scale) / 2)
    selected = (padding_x + (target.x - context_x) * scale,
                padding_y + (target.y - context_y) * scale,
                target.width * scale, target.height * scale)
    mapping = SelectionEditMapping(target.document_key,
                                   (target.x, target.y, target.width, target.height), _mask_digest(target),
                                   context, output_width, output_height, input_width, input_height,
                                   scale, padding_x, padding_y, selected)
    source = QImage(input_width, input_height, QImage.Format_ARGB32)
    source.fill(Qt.transparent)
    painter = QPainter(source)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawImage(QRectF(padding_x, padding_y, context_width * scale, context_height * scale), snapshot.image)
    painter.end()

    # Provider masks use the inverse alpha convention: 0 edits, 255 preserves.
    # Send binary support only. The server may composite using this mask before
    # returning its result; sending feathering here and again at final import
    # would multiply the edit contribution twice. Target retains the original
    # full-resolution mask and applies its feathering exactly once locally.
    mask = QImage(input_width, input_height, QImage.Format_ARGB32)
    mask.fill(Qt.white)
    selected_alpha = target.selection_mask or bytes([255]) * (target.width * target.height)
    selected_alpha = selected_alpha.translate(bytes([0]) + bytes([255]) * 255)
    selection = QImage(selected_alpha, target.width, target.height, target.width, QImage.Format_Alpha8)
    painter = QPainter(mask)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationOut)
    painter.drawImage(QRectF(*selected), selection)
    painter.end()
    # Fractional draw bounds can yield a partially covered raster edge even with
    # nearest sampling. A final native bytes translation keeps the provider mask
    # binary while final original-resolution clipping enforces the exact shape.
    mask_alpha = bridge._image_bytes(mask)[3::4].translate(bytes([0]) * 255 + bytes([255]))
    mask = QImage(mask_alpha, input_width, input_height, input_width, QImage.Format_Alpha8).copy().convertToFormat(QImage.Format_ARGB32)
    mask_bytes = bridge._encode_png(mask)

    transparent = bridge._has_transparency(source)
    mime = "image/png" if mode == "original" else "image/webp" if transparent and bridge._webp_encoder() is not None else "image/png" if transparent else "image/jpeg"
    try:
        encoded = bridge._encode_reference(source, mime, mode == "fast")
    except bridge.BridgeError:
        if mime != "image/webp":
            raise
        mime = "image/png"
        encoded = bridge._encode_reference(source, mime, True)
    reference = bridge.Reference(encoded, input_width, input_height, target.x, target.y, target,
                                 snapshot.source, snapshot.document_name, snapshot.layer_name, mime,
                                 {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[mime],
                                 {"mode": mode, "source_width": context_width, "source_height": context_height,
                                  "width": input_width, "height": input_height,
                                  "output_width": output_width, "output_height": output_height,
                                  "bytes": len(encoded), "mask_bytes": len(mask_bytes), "mime": mime,
                                  "read_ms": round(snapshot.read_ms, 3),
                                  "total_ms": round(snapshot.read_ms + (perf_counter() - started) * 1000, 3)})
    return SelectionEditReference(reference, mapping, mask_bytes)


def map_selection_result(image, mapping, target):
    """Recover just the frozen selection using a uniform inverse transform."""
    bridge = _bridge_for(target)
    if not isinstance(mapping, SelectionEditMapping):
        raise bridge.BridgeError("选区结果坐标映射无效，图片仍保留在 Mori")
    mapping.assert_target(target)
    if image.isNull() or min(image.width(), image.height()) <= 0:
        raise bridge.BridgeError("生成结果图像为空，图片仍保留在 Mori")
    if (min(mapping.input_width, mapping.input_height, mapping.output_width, mapping.output_height) <= 0 or
            mapping.input_width * mapping.output_height != mapping.input_height * mapping.output_width or
            not math.isfinite(mapping.scale) or mapping.scale <= 0):
        raise bridge.BridgeError("选区结果坐标映射无效，图片仍保留在 Mori")
    expected_ratio = mapping.output_width / mapping.output_height
    if abs((image.width() / image.height()) / expected_ratio - 1) > .02:
        raise bridge.BridgeError("生成结果的比例与选区参考画幅不一致，已停止自动回填；图片仍保留在 Mori")
    # Accept globally rescaled output without assuming a particular resolution.
    # A very small provider rounding difference is handled as centered padding,
    # never by stretching horizontal and vertical axes independently.
    result_scale = min(image.width() / mapping.input_width, image.height() / mapping.input_height)
    extra_x = (image.width() - mapping.input_width * result_scale) / 2
    extra_y = (image.height() - mapping.input_height * result_scale) / 2
    x, y, width, height = mapping.selection_rect_in_frame
    source_rect = QRectF(extra_x + x * result_scale, extra_y + y * result_scale,
                         width * result_scale, height * result_scale)
    if (not all(math.isfinite(value) for value in (x, y, width, height)) or min(width, height) <= 0 or
            source_rect.left() < -.001 or source_rect.top() < -.001 or
            source_rect.right() > image.width() + .001 or source_rect.bottom() > image.height() + .001):
        raise bridge.BridgeError("选区结果超出原参考画幅，图片仍保留在 Mori")
    result = QImage(target.width, target.height, QImage.Format_ARGB32)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    painter.drawImage(QRectF(0, 0, target.width, target.height), image, source_rect)
    painter.end()
    return result
