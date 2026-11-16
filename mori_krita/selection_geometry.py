"""Locate a Krita selection in its actual canvas widget, without pixel reads.

Krita's public View transforms compose as image -> flake -> widget. The
flakeToCanvasTransform implementation returns flakeToWidgetTransform, including
pan, rotation and mirroring (the older public header description is misleading).
Coordinates are Qt logical widget pixels; applying devicePixelRatio again would
double-scale an overlay on a high-DPI display.

Source: KDE/krita libs/libkis/View.cpp, flakeToCanvasTransform and
flakeToImageTransform; libs/ui/canvas/kis_coordinates_converter.cpp.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import weakref
from typing import Any

try:
    from PyQt5.QtCore import QRectF, Qt, QThread
    from PyQt5.QtWidgets import QApplication, QMdiArea, QWidget
except ImportError:
    QRectF = Qt = QThread = QApplication = QMdiArea = QWidget = None
try:
    from krita import Krita
except ImportError:
    Krita = None


# Finding hundreds of Qt children on every idle tick is avoidable. Keep only
# widget references here; no image, selection or document pixels are cached.
_CANVAS_CACHE = weakref.WeakKeyDictionary()
_TOOLBAR_CACHE = weakref.WeakKeyDictionary()


class SelectionBusy(RuntimeError):
    """Selection bounds cannot be read without competing with an image job."""


def clear_geometry_cache():
    _CANVAS_CACHE.clear()
    _TOOLBAR_CACHE.clear()


@dataclass(frozen=True)
class SelectionAnchor:
    parent: Any = field(repr=False, compare=False)
    rect: Any
    document: Any = field(repr=False, compare=False)
    view: Any = field(repr=False, compare=False)
    document_name: str
    document_key: str
    selection_rect: Any
    toolbar_rect: Any = None


def _rect_values(rect):
    """Accept QRect/F or an (x, y, width, height) tuple for pure tests."""
    if isinstance(rect, (tuple, list)) and len(rect) == 4:
        values = tuple(float(value) for value in rect)
    else:
        values = (float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Rectangle coordinates must be finite")
    x, y, width, height = values
    if width < 0:
        x, width = x + width, -width
    if height < 0:
        y, height = y + height, -height
    return x, y, width, height


def _overlaps(first, second):
    x, y, width, height = first
    ox, oy, ow, oh = second
    return x < ox + ow and x + width > ox and y < oy + oh and y + height > oy


def place_panel(anchor_rect, viewport_width, viewport_height, panel_width, panel_height,
                gap=12, toolbar_margin=44, obstacles=(), margin=8):
    """Return a clamped (x, y, width, height) in canvas logical pixels.

    Prefer the selection's lower edge, reserving space for Krita's own selection
    bar. If there is not enough room, use its upper edge. Native toolbar widget
    rectangles may be supplied in ``obstacles`` for exact collision avoidance.
    All dimensions remain independent of display scale.
    """
    numeric = (viewport_width, viewport_height, panel_width, panel_height,
               gap, toolbar_margin, margin)
    if not all(math.isfinite(float(value)) for value in numeric):
        raise ValueError("Panel dimensions must be finite")
    vw, vh = max(0, int(viewport_width)), max(0, int(viewport_height))
    if vw == 0 or vh == 0:
        return 0, 0, 0, 0
    margin = max(0, min(int(margin), (vw - 1) // 2, (vh - 1) // 2))
    width = min(max(1, int(panel_width)), vw - 2 * margin)
    height = min(max(1, int(panel_height)), vh - 2 * margin)
    gap, toolbar_margin = max(0, int(gap)), max(0, int(toolbar_margin))
    ax, ay, aw, ah = _rect_values(anchor_rect)
    x = int(round(ax + aw / 2 - width / 2))
    x = max(margin, min(x, vw - margin - width))
    blocked = [_rect_values(rect) for rect in obstacles if rect is not None]

    below = ay + ah + gap + toolbar_margin
    above = ay - gap - height

    def clear_y(y, direction):
        # A few native selection buttons may describe the same toolbar. Moving
        # past each collision converges after at most the number of obstacles.
        for _ in range(len(blocked) + 1):
            collisions = [rect for rect in blocked if _overlaps((x, y, width, height), rect)]
            if not collisions:
                break
            if direction > 0:
                y = max(rect[1] + rect[3] + gap for rect in collisions)
            else:
                y = min(rect[1] - gap - height for rect in collisions)
        return y

    below = clear_y(below, 1)
    above = clear_y(above, -1)
    if margin <= below and below + height <= vh - margin:
        y = below
    elif margin <= above and above + height <= vh - margin:
        y = above
    else:
        # A very large/offscreen selection can leave neither side available.
        # Keep the controls reachable rather than placing them outside Krita.
        lower_space = vh - margin - (ay + ah)
        upper_space = ay - margin
        y = below if lower_space >= upper_space else above
        y = max(margin, min(y, vh - margin - height))
        if any(_overlaps((x, y, width, height), rect) for rect in blocked):
            alternatives = (margin, vh - margin - height)
            for candidate in alternatives:
                if not any(_overlaps((x, candidate, width, height), rect) for rect in blocked):
                    y = candidate
                    break
    y = max(margin, min(int(round(y)), vh - margin - height))
    return x, y, width, height


def image_to_canvas_rect(view, image_rect):
    """Map through Krita's exact transforms, preserving zoom/rotation/mirror."""
    image_to_flake, invertible = view.flakeToImageTransform().inverted()
    if not invertible:
        return None
    image_to_canvas = image_to_flake * view.flakeToCanvasTransform()
    result = image_to_canvas.mapRect(QRectF(image_rect)).normalized()
    if not all(math.isfinite(value) for value in _rect_values(result)):
        return None
    return result


def _is_canvas(widget):
    return any(widget.inherits(name) for name in ("KisOpenGLCanvas2", "KisQPainterCanvas"))


def _find_canvas(main_window):
    cached = _CANVAS_CACHE.get(main_window)
    area = scope = None
    if cached is not None:
        try:
            area, previous_scope, canvas = cached
            scope = area.activeSubWindow() if area is not None else main_window
            if scope is previous_scope and canvas.isVisible() and canvas.width() > 0 and canvas.height() > 0:
                return canvas
        except RuntimeError:
            area = scope = None
    areas = [area] if area is not None else main_window.findChildren(QMdiArea)
    scope = None
    for area in areas:
        active = area.activeSubWindow()
        if active is not None:
            scope = active
            break
    if scope is None:
        scope = main_window
    candidates = [widget for widget in scope.findChildren(QWidget)
                  if _is_canvas(widget) and widget.isVisible() and widget.width() > 0 and widget.height() > 0]
    # Never guess between visible canvases from different split views.
    if len(candidates) != 1:
        _CANVAS_CACHE.pop(main_window, None)
        return None
    result = candidates[0]
    _CANVAS_CACHE[main_window] = (area if areas else None, scope, result)
    return result


def find_canvas_for_window(main_window):
    """Find the active canvas from Qt widgets only, without image API reads."""
    if main_window is None:
        return None
    try:
        return _find_canvas(main_window)
    except (RuntimeError, AttributeError, TypeError):
        # An MDI tab or its native widgets can disappear while rebinding input.
        return None


def _toolbar_rect(canvas):
    result = None
    # Krita parents its selection toolbar buttons directly to the canvas.
    # A changed child count invalidates the short list (including lazy creation).
    child_count = len(canvas.children())
    cached = _TOOLBAR_CACHE.get(canvas)
    if cached is None or cached[0] != child_count:
        widgets = [widget for widget in canvas.findChildren(QWidget)
                   if widget.metaObject().className().startswith("KisSelectionActionsPanel")]
        _TOOLBAR_CACHE[canvas] = (child_count, widgets)
    else:
        widgets = cached[1]
    for widget in widgets:
        try:
            visible = widget.isVisible() and widget.width() > 0 and widget.height() > 0
        except RuntimeError:
            _TOOLBAR_CACHE.pop(canvas, None)
            return None
        if visible:
            # Source currently parents these buttons directly to the canvas.
            # Mapping also handles future versions which introduce a wrapper.
            point = widget.mapTo(canvas, widget.rect().topLeft())
            rect = QRectF(float(point.x()), float(point.y()), float(widget.width()), float(widget.height()))
            result = rect if result is None else result.united(rect)
    return result


def locate_selection(application=None):
    """Return the active selection anchor or None. GUI-thread, metadata only.

    Selection getters call Krita's exact-bounds cache, so query them only after
    a non-blocking image barrier succeeds. SelectionBusy means the last anchor
    should be retained until a later settled/idle update; None means no anchor.
    Does not wait for image work, capture pixels, upload content or change tools.
    """
    if QRectF is None or QApplication is None:
        return None
    qt_app = QApplication.instance()
    if qt_app is None or QThread.currentThread() != qt_app.thread():
        return None
    if QApplication.activeModalWidget() is not None:
        return None
    app = application or (Krita.instance() if Krita is not None else None)
    if app is None:
        return None
    try:
        window = app.activeWindow()
        view = window.activeView() if window is not None else None
        if view is None or not view.visible():
            return None
        document = view.document()
        if document is None:
            return None
        main_window = window.qwindow()
        if main_window is None or main_window.isMinimized():
            return None
        canvas = find_canvas_for_window(main_window)
        if canvas is None:
            return None
        barrier = getattr(document, "tryBarrierLock", None)
        unlock = getattr(document, "unlock", None)
        if not callable(barrier) or not callable(unlock):
            # The supported 5.2/5.3 runtimes expose these methods. Do not use a
            # blocking wait or unsafe exact-bounds fallback on other bindings.
            raise SelectionBusy("当前 Krita 暂不能安全读取选区，请稍后重试")
        if not barrier():
            raise SelectionBusy("Krita 正在更新画布，稍后重新定位选区")
        try:
            selection = document.selection()
            if selection is None:
                return None
            width, height = selection.width(), selection.height()
            if width <= 0 or height <= 0:
                return None
            image_rect = QRectF(selection.x(), selection.y(), width, height)
            image_rect = image_rect.intersected(QRectF(0, 0, document.width(), document.height()))
            if image_rect.isEmpty():
                return None
            root = document.rootNode()
            key = root.uniqueId().toString() if root is not None else document.fileName() or document.name()
            document_name = document.name() or "未命名画布"
        finally:
            unlock()
        # Widget traversal and transforms need no image barrier. Keep its hold
        # short so the next native selection/paint stroke can start immediately.
        rect = image_to_canvas_rect(view, image_rect)
        if rect is None or rect.isEmpty() or not rect.intersects(QRectF(canvas.rect())):
            return None
        return SelectionAnchor(parent=canvas, rect=rect, document=document, view=view,
                               document_name=document_name, document_key=key,
                               selection_rect=image_rect, toolbar_rect=_toolbar_rect(canvas))
    except SelectionBusy:
        raise
    except (RuntimeError, AttributeError, TypeError, ValueError):
        # A tab can close or switch while the caller's timer is queued.
        return None
