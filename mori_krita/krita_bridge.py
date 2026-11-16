"""Krita's GUI-thread image bridge. No network or server credentials live here.

Targets hold a document identity frozen at submission, plus the original region.
Image results are always inserted into a new paint layer. Existing layers and the
document's colour space are never changed.
"""

from __future__ import annotations

import base64
import ctypes
from dataclasses import dataclass, field
from functools import lru_cache
import math
import os
from pathlib import Path
import tempfile
import sys
from time import perf_counter
from typing import Any
from uuid import uuid4

try:
    from PyQt5.QtCore import QByteArray, QBuffer, QIODevice, QRect, QRectF, Qt, QThread, QUuid
    from PyQt5.QtGui import QColorSpace, QImage, QImageReader, QImageWriter, QPainter
    from PyQt5.QtWidgets import QApplication
except ImportError:  # Permit the mapping/target tests to run outside Krita.
    QByteArray = QBuffer = QIODevice = QRect = QRectF = Qt = QThread = QUuid = None
    QColorSpace = QImage = QImageReader = QImageWriter = QPainter = QApplication = None
try:
    from krita import InfoObject, Krita
except ImportError:
    InfoObject = Krita = None


MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_BYTES = 80 * 1024 * 1024
SRGB_PROFILE = "sRGB-elle-V2-srgbtrc.icc"
VALID_SOURCES = frozenset(("canvas", "layer", "selection"))
CAPTURE_SOURCES = VALID_SOURCES | {"auto"}
_UNSET = object()
FAST_REFERENCE_MAX_EDGE = 2048
FAST_REFERENCE_MAX_PIXELS = 4_000_000


class BridgeError(RuntimeError):
    """A user-readable capture/import error."""

    def __init__(self, message: str, *, stage: str = "") -> None:
        self.stage = stage
        super().__init__(f"{message} [{stage}]" if stage else message)


def clip_rect(x: int, y: int, width: int, height: int,
              document_width: int, document_height: int) -> tuple[int, int, int, int]:
    """Clip capture coordinates to the original document, retaining the offset."""
    left, top = max(0, x), max(0, y)
    right, bottom = min(document_width, x + width), min(document_height, y + height)
    return left, top, max(0, right - left), max(0, bottom - top)


def strip_argb32_padding(raw: bytes, width: int, height: int, stride: int) -> bytes:
    """Krita wants tight four-byte pixels; QImage rows may include padding."""
    row_size = width * 4
    if width <= 0 or height <= 0 or stride < row_size or len(raw) < stride * height:
        raise BridgeError("图片像素数据不完整")
    if stride == row_size:
        return raw[:row_size * height]
    return b"".join(raw[row * stride:row * stride + row_size] for row in range(height))


def apply_alpha_mask(bgra: bytes, mask: bytes) -> bytes:
    """Multiply original alpha by a frozen 8-bit selection, including feathering."""
    if len(bgra) != len(mask) * 4:
        raise BridgeError("选区蒙版尺寸与图片不一致")
    # Rectangular selections are overwhelmingly common. Avoid millions of
    # Python multiplications when every selected pixel is fully included.
    if mask.count(255) == len(mask):
        return bgra
    result = bytearray(bgra)
    if mask.count(0) == len(mask):
        result[3::4] = bytes(len(mask))
        return bytes(result)
    alpha = bgra[3::4]
    if alpha.count(255) == len(alpha):
        # Most generated images are opaque. Multiplication by 255 is the
        # original selection mask itself, including every feathering level.
        result[3::4] = mask
        return bytes(result)
    result[3::4] = bytes((alpha * selected + 127) // 255
                        for alpha, selected in zip(alpha, mask))
    return bytes(result)


@dataclass(frozen=True)
class Target:
    document_key: str
    x: int
    y: int
    width: int
    height: int
    source: str = "canvas"
    document_name: str = ""
    layer_id: str = ""
    # The original selection is frozen; edits while a job runs cannot move it.
    selection_mask: bytes = field(default=b"", repr=False, compare=False)

    def as_dict(self, include_mask: bool = False) -> dict[str, Any]:
        result = {"document_key": self.document_key, "x": self.x, "y": self.y,
                  "width": self.width, "height": self.height, "source": self.source,
                  "document_name": self.document_name, "layer_id": self.layer_id}
        if include_mask and self.selection_mask:
            result["selection_mask"] = base64.b64encode(self.selection_mask).decode("ascii")
        return result

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Target":
        if not isinstance(value, dict):
            raise BridgeError("画布目标无效")
        try:
            target = cls(document_key=str(value["document_key"]),
                         x=int(value["x"]), y=int(value["y"]),
                         width=int(value["width"]), height=int(value["height"]),
                         source=str(value.get("source", "canvas")),
                         document_name=str(value.get("document_name", "")),
                         layer_id=str(value.get("layer_id", "")),
                         selection_mask=base64.b64decode(value.get("selection_mask", ""), validate=True))
        except (ValueError, TypeError, KeyError) as exc:
            raise BridgeError("画布目标无效") from exc
        _validate_target(target)
        return target


@dataclass(frozen=True)
class Capture:
    png_bytes: bytes
    width: int
    height: int
    x: int
    y: int
    target: Target
    source: str
    document_name: str
    layer_name: str = ""

    @property
    def document_key(self) -> str:
        return self.target.document_key

    def as_dict(self, include_image: bool = True) -> dict[str, Any]:
        result = {"width": self.width, "height": self.height, "x": self.x, "y": self.y,
                  "target": self.target.as_dict(), "source": self.source,
                  "document_key": self.document_key, "document_name": self.document_name,
                  "layer_name": self.layer_name}
        if include_image:
            result["dataUrl"] = "data:image/png;base64," + base64.b64encode(self.png_bytes).decode("ascii")
        return result


@dataclass(frozen=True)
class Snapshot:
    """Detached pixels safe to hand to a worker; never contains a live document."""
    image: Any = field(repr=False, compare=False)
    target: Target
    source: str
    document_name: str = ""
    layer_name: str = ""
    read_ms: float = 0.0
    fallback: bool = False
    read_timings: dict[str, float] = field(default_factory=dict, compare=False)
    # Optional unmasked context around a selection, in original image pixels.
    # Target remains the exact selection to which a result may be written.
    context_rect: tuple[int, int, int, int] | None = None

    @property
    def width(self) -> int:
        return self.image.width()

    @property
    def height(self) -> int:
        return self.image.height()


@dataclass(frozen=True)
class Reference(Capture):
    """Encoded upload. png_bytes is retained as a legacy alias for image_bytes."""
    mime: str = "image/png"
    extension: str = "png"
    metrics: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def image_bytes(self) -> bytes:
        return self.png_bytes

    @property
    def name(self) -> str:
        # Deliberately generic: diagnostic/upload names need no document title.
        return "reference." + self.extension

    def as_dict(self, include_image: bool = True) -> dict[str, Any]:
        result = super().as_dict(include_image=False)
        result.update({"mime": self.mime, "name": self.name, "metrics": dict(self.metrics)})
        if include_image:
            result["dataUrl"] = "data:" + self.mime + ";base64," + base64.b64encode(self.image_bytes).decode("ascii")
        return result


def reference_dimensions(width: int, height: int, mode: str = "fast") -> tuple[int, int]:
    _validate_size(width, height)
    if mode not in ("fast", "original"):
        raise BridgeError("请选择快速参考或原图参考")
    if mode == "original":
        return width, height
    scale = min(1.0, FAST_REFERENCE_MAX_EDGE / max(width, height),
                math.sqrt(FAST_REFERENCE_MAX_PIXELS / (width * height)))
    return max(1, int(width * scale)), max(1, int(height * scale))


def _has_transparency(image: Any) -> bool:
    if not image.hasAlphaChannel():
        return False
    argb = image.convertToFormat(QImage.Format_ARGB32)
    # bytes slicing/count are C loops. No Python per-pixel walk is performed.
    alpha = argb.constBits().asstring(argb.byteCount())[3::4]
    return alpha.count(255) != len(alpha)


@lru_cache(maxsize=1)
def _webp_encoder() -> Any:
    """Find Krita's own codec, never a DLL from the working directory/PATH.

    Qt 5's WebP writer sets alpha_quality to the colour quality. libwebp's
    stable simple encoding API instead retains alpha_quality=100 from its
    default preset, while colour quality can remain 90. Missing codecs retain
    the PNG path; no dependency installation is required.
    """
    if QApplication is None or QImageWriter is None or os.name != "nt" or sys.byteorder != "little":
        return None
    if b"webp" not in [bytes(item).lower() for item in QImageWriter.supportedImageFormats()]:
        return None
    location = Path(QApplication.applicationDirPath()) / "libwebp.dll"
    if not location.is_file():
        return None
    try:
        library = ctypes.CDLL(str(location))
        library.WebPEncodeBGRA.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_float,
                                          ctypes.POINTER(ctypes.c_void_p)]
        library.WebPEncodeBGRA.restype = ctypes.c_size_t
        library.WebPFree.argtypes = [ctypes.c_void_p]
        library.WebPFree.restype = None
        return library
    except (OSError, AttributeError):
        return None


def _encode_webp(image: Any) -> bytes:
    library = _webp_encoder()
    if library is None:
        raise BridgeError("当前 Krita 没有可用的无损透明通道 WebP 编码器")
    # On the Windows little-endian runtime, straight ARGB32 memory is BGRA.
    # Holding `argb` throughout the native call keeps the input buffer alive.
    argb = image.convertToFormat(QImage.Format_ARGB32)
    output = ctypes.c_void_p()
    size = library.WebPEncodeBGRA(int(argb.constBits()), argb.width(), argb.height(),
                                 argb.bytesPerLine(), ctypes.c_float(90), ctypes.byref(output))
    if not size or not output.value:
        raise BridgeError("WebP 参考图片编码失败")
    try:
        if size > MAX_IMAGE_BYTES:
            raise BridgeError("参考图片超过 80 MB，请缩小选区")
        return ctypes.string_at(output, size)
    finally:
        library.WebPFree(output)


def _encode_reference(image: Any, mime: str, fast: bool) -> bytes:
    if mime == "image/webp":
        return _encode_webp(image)
    buffer = QBuffer()
    if not buffer.open(QIODevice.WriteOnly):
        raise BridgeError("无法准备参考图片")
    writer = QImageWriter(buffer, b"jpeg" if mime == "image/jpeg" else b"png")
    if mime == "image/jpeg":
        writer.setQuality(90)
    elif fast:
        # Qt 5 maps its 0..100 compression scale to zlib 0..9. Setting 11
        # selects zlib level 1; setting 1 would upload an uncompressed PNG.
        writer.setCompression(11)
    if not writer.write(image):
        message = writer.errorString()
        buffer.close()
        raise BridgeError("参考图片编码失败：" + message)
    result = bytes(buffer.data())
    buffer.close()
    if len(result) > MAX_IMAGE_BYTES:
        raise BridgeError("参考图片超过 80 MB，请使用快速参考或缩小选区")
    return result


def prepare_reference(snapshot: Snapshot, mode: str = "fast") -> Reference:
    """Resize/mask/encode detached pixels in a worker. No Krita API calls.

    Fast mode is a reference proxy, while Target retains the original region for
    full-size result placement. Original mode is a lossless, full-resolution PNG.
    QImage and QImageWriter are reentrant; each call owns its image and writer.
    """
    if QImage is None or QImageWriter is None:
        raise BridgeError("参考图片处理需要 Krita 的 Qt 图像环境")
    if not isinstance(snapshot, Snapshot) or snapshot.image.isNull():
        raise BridgeError("参考图片快照为空")
    started = perf_counter()
    source_width, source_height = snapshot.width, snapshot.height
    width, height = reference_dimensions(source_width, source_height, mode)
    if (source_width, source_height) != (snapshot.target.width, snapshot.target.height):
        raise BridgeError("参考图片快照尺寸与原画布区域不一致")
    image = snapshot.image
    if (width, height) != (source_width, source_height):
        image = image.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    resize_ms = (perf_counter() - started) * 1000
    mask_started = perf_counter()
    mask = snapshot.target.selection_mask
    if mask:
        if len(mask) != source_width * source_height:
            raise BridgeError("参考选区蒙版尺寸不正确")
        if mode == "original":
            # Preserve exact straight RGB and alpha, including hidden RGB.
            # The Python loop is deliberately confined to this worker-only mode.
            image = _image_from_bgra(apply_alpha_mask(_image_bytes(image), mask), width, height)
        else:
            mask_image = QImage(mask, source_width, source_height, source_width, QImage.Format_Alpha8)
            if (width, height) != (source_width, source_height):
                mask_image = mask_image.scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            # Qt's smooth scaler may promote Alpha8 to ARGB32. Normalize back
            # so setAlphaChannel reads alpha, not the promoted image's black RGB.
            mask_image = mask_image.convertToFormat(QImage.Format_Alpha8)
            image = image.copy()
            # Native Qt multiplication retains original alpha and feathering.
            image.setAlphaChannel(mask_image)
    transparent = _has_transparency(image)
    mask_ms = (perf_counter() - mask_started) * 1000
    webp_available = mode == "fast" and transparent and _webp_encoder() is not None
    mime = ("image/png" if mode == "original" else
            "image/webp" if webp_available else "image/png" if transparent else "image/jpeg")
    encode_started = perf_counter()
    codec_fallback = mode == "fast" and transparent and not webp_available
    try:
        encoded = _encode_reference(image, mime, mode == "fast")
    except BridgeError:
        if mime != "image/webp":
            raise
        # A codec runtime failure must not prevent adding a reference or flatten
        # its transparency. The established PNG encoder is the safe fallback.
        mime = "image/png"
        codec_fallback = True
        encoded = _encode_reference(image, mime, True)
    encode_ms = (perf_counter() - encode_started) * 1000
    metrics = {"mode": mode, "source_width": source_width, "source_height": source_height,
               "width": width, "height": height, "bytes": len(encoded), "mime": mime,
               "read_ms": round(snapshot.read_ms, 3), "resize_ms": round(resize_ms, 3),
               "mask_ms": round(mask_ms, 3), "encode_ms": round(encode_ms, 3),
               "total_ms": round(snapshot.read_ms + (perf_counter() - started) * 1000, 3),
               "fallback": snapshot.fallback, "codec_fallback": codec_fallback,
               "alpha_lossless": transparent, "webp_available": webp_available}
    metrics.update(snapshot.read_timings)
    return Reference(encoded, width, height, snapshot.target.x, snapshot.target.y,
                     snapshot.target, snapshot.source, snapshot.document_name,
                     snapshot.layer_name, mime,
                     {"image/jpeg": "jpg", "image/webp": "webp", "image/png": "png"}[mime], metrics)


def _validate_size(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        raise BridgeError("当前区域没有可读取的像素")
    if width * height > MAX_IMAGE_PIXELS:
        raise BridgeError("图片区域过大，请缩小到 4000 万像素以内")


def _validate_target(target: Target) -> None:
    _validate_size(target.width, target.height)
    if not target.document_key or target.source not in VALID_SOURCES:
        raise BridgeError("画布目标无效")
    if target.selection_mask and len(target.selection_mask) != target.width * target.height:
        raise BridgeError("原选区蒙版已失效，请重新读取选区")


def _image_bytes(image: Any) -> bytes:
    image = image.convertToFormat(QImage.Format_ARGB32)
    pointer = image.constBits()
    raw = pointer.asstring(image.byteCount())
    return strip_argb32_padding(raw, image.width(), image.height(), image.bytesPerLine())


def _image_from_bgra(raw: bytes, width: int, height: int) -> Any:
    return QImage(raw, width, height, width * 4, QImage.Format_ARGB32).copy()


def _fit_import_image(image: Any, width: int, height: int) -> Any:
    """Contain the complete result, centred, without changing its proportions.

    Transparent padding keeps the frozen target/mask in document coordinates.
    A single uniform transform also handles very thin images without rounding
    one scaled dimension to zero. Mapped selection edits already have the
    target size and pass through untouched.
    """
    _validate_size(width, height)
    _validate_size(image.width(), image.height())
    if image.width() == width and image.height() == height:
        return image
    scale = min(width / image.width(), height / image.height())
    fitted_width, fitted_height = image.width() * scale, image.height() * scale
    result = QImage(width, height, QImage.Format_ARGB32)
    if result.isNull():
        raise BridgeError("无法为生成结果分配图像空间")
    result.fill(Qt.transparent)
    result.setColorSpace(image.colorSpace())
    painter = QPainter(result)
    try:
        if not painter.isActive():
            raise BridgeError("无法等比放置生成结果")
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.drawImage(QRectF((width - fitted_width) / 2, (height - fitted_height) / 2,
                                fitted_width, fitted_height), image)
    finally:
        painter.end()
    return result


def _import_pixels(image: Any, mask: bytes = b"") -> bytes:
    """Skip rectangular mask work and retain exact straight RGB/feathering."""
    raw = _image_bytes(image)
    if not mask:
        return raw
    if len(mask) != image.width() * image.height():
        raise BridgeError("原选区蒙版尺寸与结果不一致")
    if mask.count(255) == len(mask):
        return raw
    if mask.count(0) == len(mask):
        result = bytearray(raw)
        result[3::4] = bytes(len(mask))
        return bytes(result)
    # Qt's native DestinationIn may differ by one alpha level for some high
    # values. The helper also shortcuts opaque results to a native byte-slice
    # assignment; only truly translucent source × feathering uses its exact loop.
    return apply_alpha_mask(raw, mask)


def _encode_png(image: Any) -> bytes:
    buffer = QBuffer()
    if not buffer.open(QIODevice.WriteOnly) or not image.save(buffer, "PNG"):
        raise BridgeError("无法将画布编码为 PNG")
    result = bytes(buffer.data())
    buffer.close()
    if len(result) > MAX_IMAGE_BYTES:
        raise BridgeError("图片文件过大，请缩小选区后重试")
    return result


def _decode_image(encoded: bytes) -> Any:
    if not isinstance(encoded, (bytes, bytearray)) or not encoded or len(encoded) > MAX_IMAGE_BYTES:
        raise BridgeError("生成图片为空或超过 80 MB")
    buffer = QBuffer()
    buffer.setData(QByteArray(encoded))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in (b"png", b"jpeg", b"jpg", b"webp"):
        raise BridgeError("生成结果不是有效的 PNG、JPEG 或 WebP 图片")
    size = reader.size()
    _validate_size(size.width(), size.height())
    image = reader.read()
    buffer.close()
    if image.isNull():
        raise BridgeError("无法解码生成图片")
    color_space = image.colorSpace()
    if color_space.isValid():
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.SRgb))
    return image.convertToFormat(QImage.Format_ARGB32)


class KritaBridge:
    def __init__(self, application: Any = None) -> None:
        self._app = application
        self._documents: dict[str, Any] = {}
        self.last_import_target: Target | None = None
        self.last_import_metrics: dict[str, float] = {}
        self.last_import_stage = ""

    def _gui(self) -> Any:
        if Krita is None or QApplication is None:
            raise BridgeError("此功能需要在 Krita 的 Python 插件环境中运行")
        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            raise BridgeError("画布读写必须在 Krita 主线程执行")
        return self._app or Krita.instance()

    @staticmethod
    def _same_document(first: Any, second: Any) -> bool:
        try:
            # libkis Document::operator== compares the underlying KisDocument.
            return first == second
        except RuntimeError:
            return False

    def _register(self, document: Any) -> str:
        for key, existing in self._documents.items():
            if self._same_document(existing, document):
                return key
        key = uuid4().hex
        self._documents[key] = document
        return key

    def _resolve(self, key: str) -> Any:
        app = self._gui()
        existing = self._documents.get(key)
        if existing is not None:
            for document in app.documents():
                if self._same_document(existing, document):
                    return document
        raise BridgeError("生成任务对应的原画布已关闭，请打开画布后手动导入结果")

    @staticmethod
    def _document_name(document: Any) -> str:
        return document.name() or Path(document.fileName()).name or "未命名画布"

    def list_documents(self) -> list[Target]:
        app = self._gui()
        result = []
        for document in app.documents():
            result.append(self._target(document, "canvas"))
        return result

    def active_target(self, source: str = "canvas") -> Target | None:
        app = self._gui()
        if source not in CAPTURE_SOURCES:
            raise BridgeError("请选择画布、当前图层或选区")
        document = app.activeDocument()
        if document is None:
            return None
        if source == "auto":
            source, selection, node, bounds = self._detect_context(document)
            return self._target(document, source, selection=selection, node=node, layer_bounds=bounds,
                                selection_rect=bounds if source == "selection" else _UNSET)
        return self._target(document, source)

    def _detect_context(self, document: Any, selection: Any = _UNSET, node: Any = _UNSET):
        """Capture one native Selection wrapper, not once per decision branch."""
        if selection is _UNSET:
            selection = document.selection()
        if node is _UNSET:
            node = document.activeNode()
        if selection is not None:
            selected = clip_rect(selection.x(), selection.y(), selection.width(), selection.height(),
                                 document.width(), document.height())
            if selected[2] > 0 and selected[3] > 0:
                return "selection", selection, node, selected
        if node is not None and not node.type().endswith("mask"):
            bounds = node.bounds()
            clipped = clip_rect(bounds.x(), bounds.y(), bounds.width(), bounds.height(),
                                document.width(), document.height())
            if clipped[2] > 0 and clipped[3] > 0:
                return "layer", selection, node, bounds
        return "canvas", selection, node, _UNSET

    def detect_source(self) -> str:
        """Choose selection, usable layer/group, or canvas for a user import."""
        document = self._gui().activeDocument()
        if document is None:
            raise BridgeError("请先在 Krita 中打开或新建画布")
        return self._detect_context(document)[0]

    def _target(self, document: Any, source: str, *, selection: Any = _UNSET,
                node: Any = _UNSET, layer_bounds: Any = _UNSET,
                selection_rect: Any = _UNSET) -> Target:
        if node is _UNSET:
            node = document.activeNode()
        x, y, width, height = 0, 0, document.width(), document.height()
        mask = b""
        if source == "selection":
            if selection is _UNSET:
                selection = document.selection()
            if selection is None:
                raise BridgeError("请先在 Krita 画布中建立选区")
            if selection_rect is _UNSET:
                selection_rect = selection.x(), selection.y(), selection.width(), selection.height()
            x, y, width, height = clip_rect(*selection_rect, width, height)
            _validate_size(width, height)
            mask = bytes(selection.pixelData(x, y, width, height))
            if len(mask) != width * height or mask.count(0) == len(mask):
                raise BridgeError("当前选区为空，请重新选择")
        elif source == "layer":
            if node is None or node.type().endswith("mask"):
                raise BridgeError("请先选择一个图像图层或图层组")
            bounds = node.bounds() if layer_bounds is _UNSET else layer_bounds
            x, y, width, height = clip_rect(bounds.x(), bounds.y(), bounds.width(),
                                           bounds.height(), width, height)
        _validate_size(width, height)
        return Target(self._register(document), x, y, width, height, source,
                      self._document_name(document), node.uniqueId().toString() if node else "", mask)

    def snapshot(self, source: str = "auto", *, context_padding: bool = False) -> Snapshot:
        """Freeze image pixels on the GUI thread; encode them later in a worker.

        Mainstream RGBA/sRGB layers and all canvas/selection snapshots perform no
        PNG encoding here. Unusual layer colour spaces retain the established
        native PNG-export fallback to avoid regressing colour correctness; the
        metrics explicitly identify that slower compatibility path.
        """
        started = perf_counter()
        app = self._gui()
        if source not in CAPTURE_SOURCES:
            raise BridgeError("请选择画布、当前图层或选区")
        document = app.activeDocument()
        if document is None:
            raise BridgeError("请先在 Krita 中打开或新建画布")
        node = document.activeNode()
        selection_started = perf_counter()
        selection, bounds = _UNSET, _UNSET
        if source == "auto":
            source, selection, node, bounds = self._detect_context(document, node=node)
        elif source == "selection":
            selection = document.selection()
        selection_ms = (perf_counter() - selection_started) * 1000
        fallback = False
        if source == "layer":
            fallback = (node.colorModel(), node.colorDepth(), node.colorProfile()) != ("RGBA", "U8", SRGB_PROFILE)
        wait_started = perf_counter()
        locked = False
        # A stable idle canvas can be read immediately. waitForDone() also
        # forces every delayed node to update; keep that expensive fallback only
        # for a busy image or unusual native-export colour-space paths.
        barrier = getattr(document, "tryBarrierLock", None)
        if not fallback and callable(barrier):
            locked = bool(barrier())
        if not locked:
            document.waitForDone()
        wait_ms = (perf_counter() - wait_started) * 1000
        try:
            target_started = perf_counter()
            target = self._target(document, source, selection=selection, node=node, layer_bounds=bounds,
                                  selection_rect=bounds if source == "selection" else _UNSET)
            selection_ms += (perf_counter() - target_started) * 1000
            context_rect = None
            if context_padding and source == "selection":
                # Read context only for an explicit submitted edit. This never
                # runs while locating/repainting the floating selection panel.
                padding = min(512, max(32, math.ceil(max(target.width, target.height) * .25)))

                def expanded(amount):
                    left, top = max(0, target.x - amount), max(0, target.y - amount)
                    right = min(document.width(), target.x + target.width + amount)
                    bottom = min(document.height(), target.y + target.height + amount)
                    return left, top, right - left, bottom - top

                context_rect = expanded(padding)
                if context_rect[2] * context_rect[3] > MAX_IMAGE_PIXELS:
                    lower, upper = 0, padding
                    while lower < upper:
                        middle = (lower + upper + 1) // 2
                        candidate = expanded(middle)
                        if candidate[2] * candidate[3] <= MAX_IMAGE_PIXELS:
                            lower = middle
                        else:
                            upper = middle - 1
                    context_rect = expanded(lower)
            projection_started = perf_counter()
            image = (self._capture_layer(document, node, target) if source == "layer" else
                     document.projection(*(context_rect or (target.x, target.y, target.width, target.height))))
            projection_ms = (perf_counter() - projection_started) * 1000
            expected_width, expected_height = context_rect[2:] if context_rect else (target.width, target.height)
            if image.isNull() or image.width() != expected_width or image.height() != expected_height:
                raise BridgeError("无法读取当前画布区域")
            copy_started = perf_counter()
            image = image.copy()
            copy_ms = (perf_counter() - copy_started) * 1000
        finally:
            if locked:
                document.unlock()
        return Snapshot(image, target, source, target.document_name, node.name() if node else "",
                        (perf_counter() - started) * 1000, fallback,
                        {"selection_ms": round(selection_ms, 3), "wait_ms": round(wait_ms, 3),
                         "projection_ms": round(projection_ms, 3), "copy_ms": round(copy_ms, 3)}, context_rect)

    def capture(self, source: str = "selection") -> Capture:
        if source == "auto":
            reference = prepare_reference(self.snapshot("auto"), mode="original")
            return Capture(reference.image_bytes, reference.width, reference.height, reference.x, reference.y,
                           reference.target, reference.source, reference.document_name, reference.layer_name)
        app = self._gui()
        if source not in VALID_SOURCES:
            raise BridgeError("请选择画布、当前图层或选区")
        document = app.activeDocument()
        if document is None:
            raise BridgeError("请先在 Krita 中打开或新建画布")
        document.waitForDone()
        target = self._target(document, source)
        node = document.activeNode()
        if source == "layer":
            image = self._capture_layer(document, node, target)
        else:
            # Krita performs its native colour conversion into QImage here.
            image = document.projection(target.x, target.y, target.width, target.height)
        if image.isNull() or image.width() != target.width or image.height() != target.height:
            raise BridgeError("无法读取当前画布区域")
        if target.selection_mask:
            image = _image_from_bgra(apply_alpha_mask(_image_bytes(image), target.selection_mask),
                                     target.width, target.height)
        return Capture(_encode_png(image), target.width, target.height, target.x, target.y,
                       target, source, target.document_name, node.name() if node else "")

    def _capture_layer(self, document: Any, node: Any, target: Target) -> Any:
        if node.colorModel() == "RGBA" and node.colorDepth() == "U8" and node.colorProfile() == SRGB_PROFILE:
            raw = bytes(node.projectionPixelData(target.x, target.y, target.width, target.height))
            if len(raw) != target.width * target.height * 4:
                raise BridgeError("当前图层像素数据不完整")
            return _image_from_bgra(raw, target.width, target.height)
        # Layer.save delegates depth/profile conversion to Krita's PNG exporter.
        # This changes neither the source layer nor its visibility/colour space.
        with tempfile.TemporaryDirectory(prefix="mori-krita-layer-") as directory:
            path = os.path.join(directory, "layer.png")
            options = InfoObject()
            options.setProperty("alpha", True)
            options.setProperty("forceSRGB", True)
            options.setProperty("saveSRGBProfile", True)
            node.save(path, document.xRes() / 72.0, document.yRes() / 72.0, options,
                      QRect(target.x, target.y, target.width, target.height))
            if not os.path.isfile(path):
                raise BridgeError("当前图层导出失败，请尝试读取整个画布")
            with open(path, "rb") as stream:
                return _decode_image(stream.read(MAX_IMAGE_BYTES + 1))

    def import_png(self, png_bytes: bytes, name: str = "Mori 生成结果",
                   target: Target | dict[str, Any] | None = None,
                   force_new_document: bool = False, *, selection_mapping: Any = None) -> Any:
        # Keep diagnostics independent of document titles, paths and image data.
        # No pixel read-back is needed for the legacy void setter's success path.
        self.last_import_stage = "GUI_THREAD"
        try:
            return self._import_png(png_bytes, name, target, force_new_document, selection_mapping)
        except BridgeError as error:
            if error.stage:
                raise
            raise BridgeError(str(error), stage=self.last_import_stage) from error
        except Exception as error:
            # Native binding exceptions can include private paths in their text.
            raise BridgeError(f"结果导入失败（{type(error).__name__}）",
                              stage=self.last_import_stage) from error

    def _import_png(self, png_bytes: bytes, name: str,
                    target: Target | dict[str, Any] | None,
                    force_new_document: bool, selection_mapping: Any = None) -> Any:
        started = perf_counter()
        app = self._gui()
        self.last_import_stage = "DECODE_IMAGE"
        image = _decode_image(png_bytes)
        decode_ms = (perf_counter() - started) * 1000
        self.last_import_stage = "RESOLVE_TARGET"
        if isinstance(target, dict):
            target = Target.from_dict(target)
        if selection_mapping is not None:
            self.last_import_stage = "MAP_SELECTION"
            if target is None or force_new_document:
                raise BridgeError("选区结果缺少提交时的原画布位置，图片仍保留在 Mori")
            # The immutable mapping is constructed by this local module. Resolve
            # its existing module so standalone Krita QA loaders need no package
            # imports and cannot accidentally create a second mapping class.
            module = sys.modules.get(type(selection_mapping).__module__)
            mapper = getattr(module, "map_selection_result", None)
            module_path = getattr(module, "__file__", "")
            if not callable(mapper) or not module_path or Path(module_path).resolve() != Path(__file__).with_name("selection_edit.py").resolve():
                raise BridgeError("选区结果的坐标映射无效，图片仍保留在 Mori")
            image = mapper(image, selection_mapping, target)
            self.last_import_stage = "RESOLVE_TARGET"
        created = False
        if force_new_document:
            document = None
            target = None
        elif target is not None:
            _validate_target(target)
            document = self._resolve(target.document_key)
            # A resized/cropped document no longer describes the same region.
            if target.x < 0 or target.y < 0 or target.x + target.width > document.width() or target.y + target.height > document.height():
                raise BridgeError("原画布尺寸已改变，请手动选择导入位置")
        else:
            document = app.activeDocument()
            target = self._target(document, "canvas") if document is not None else None
        if document is None:
            self.last_import_stage = "CREATE_DOCUMENT"
            document = app.createDocument(image.width(), image.height(), "Mori 创作", "RGBA", "U8", SRGB_PROFILE, 72.0)
            if document is None:
                raise BridgeError("无法创建 Krita 画布")
            created = True
            target = self._target(document, "canvas")
        if target is None:
            raise BridgeError("生成结果没有可用的导入位置")
        self.last_import_stage = "SCALE_IMAGE"
        scale_started = perf_counter()
        image = _fit_import_image(image, target.width, target.height)
        scale_ms = (perf_counter() - scale_started) * 1000
        self.last_import_stage = "PREPARE_PIXELS"
        mask_started = perf_counter()
        raw = _import_pixels(image, target.selection_mask)
        mask_ms = (perf_counter() - mask_started) * 1000
        self.last_import_stage = "CREATE_LAYER"
        attach_started = perf_counter()
        node = document.createNode((name or "Mori 生成结果")[:160], "paintlayer")
        if node is None:
            raise BridgeError("无法新建结果图层")
        try:
            self.last_import_stage = "SET_SRGB"
            if ((node.colorModel(), node.colorDepth(), node.colorProfile()) != ("RGBA", "U8", SRGB_PROFILE)
                    and not node.setColorSpace("RGBA", "U8", SRGB_PROFILE)):
                raise BridgeError("无法为结果图层设置 sRGB 色彩空间")
            # Krita 5.2's SIP binding declares this setter void, even though
            # the C++ method returns bool. None is therefore a normal return
            # on 5.2; newer bindings expose False when the write fails.
            self.last_import_stage = "WRITE_PIXELS"
            written = node.setPixelData(QByteArray(raw), target.x, target.y, target.width, target.height)
            if written is False:
                raise BridgeError(f"生成图片写入图层失败（{target.width}×{target.height}，{len(raw)} 字节）")
            self.last_import_stage = "CONVERT_COLOR"
            desired = (document.colorModel(), document.colorDepth(), document.colorProfile())
            if desired != ("RGBA", "U8", SRGB_PROFILE) and not node.setColorSpace(*desired):
                raise BridgeError("无法将结果转换为原画布的色彩空间")
            self.last_import_stage = "ATTACH_LAYER"
            root = document.rootNode()
            if root is None or not root.addChildNode(node, None):
                raise BridgeError("无法将结果图层加入原画布")
            self.last_import_stage = "ACTIVATE_LAYER"
            document.setActiveNode(node)
            # addChildNode() schedules the layer-add command and waits for its
            # projection updates itself. A subsequent refreshProjection() would
            # synchronously recompute the *entire* image, then wait once again.
            self.last_import_target = target
            self.last_import_metrics = {"decode_ms": round(decode_ms, 3), "scale_ms": round(scale_ms, 3),
                                        "mask_ms": round(mask_ms, 3),
                                        "attach_ms": round((perf_counter()-attach_started)*1000, 3),
                                        "total_ms": round((perf_counter()-started)*1000, 3)}
            if created:
                self.last_import_stage = "SHOW_DOCUMENT"
                window = app.activeWindow()
                if window is None:
                    raise BridgeError("结果画布已创建，但当前没有可显示它的 Krita 窗口")
                window.addView(document)
            self.last_import_stage = "DONE"
            return node
        except Exception:
            # Only the newly created node is removed on failure, never user layers.
            try:
                node.remove()
            except RuntimeError:
                pass
            raise
