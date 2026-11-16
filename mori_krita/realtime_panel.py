"""Compact prompt-first workspace with a large, persistent live preview."""
from PyQt5.QtCore import (Qt, QPoint, QPointF, QRectF, QSize, QTimer, pyqtSignal,
                          QVariantAnimation, QAbstractAnimation, QEasingCurve)
from PyQt5.QtGui import (QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF,
                         QLinearGradient, QPainterPath)
from PyQt5.QtWidgets import (QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                            QPushButton, QToolButton, QComboBox, QPlainTextEdit,
                            QCheckBox, QScrollArea,
                            QSizePolicy, QGridLayout, QDialog, QApplication, QSlider,
                            QStyleOptionSlider, QStyle)
from .reference_presets import CANVAS_SOURCE, REFERENCE_PRESETS


MORI_STYLE = """
QWidget { font-family:'Microsoft YaHei UI'; font-size:12px; }
QWidget#MoriSurface, QWidget#RealtimePanel, QWidget#MoriControls { background:#222225; color:#eeedf2; }
QWidget#MoriSurface { font-family:'Microsoft YaHei UI'; font-size:12px; }
QFrame#MoriCard { background:#242429; border:1px solid #35353d; border-radius:8px; }
QLabel { background:transparent; color:#e9e9ef; border:0; }
QLabel[role='muted'] { color:#a4a1ae; font-size:11px; }
QLabel[role='section'] { font-size:12px; font-weight:600; color:#e3e3eb; }
QPushButton, QToolButton { background:#2a2a2f; border:1px solid #39383e; border-radius:5px; padding:5px 8px; color:#dedee8; }
QPushButton:hover, QToolButton:hover { background:#37343e; border-color:#777083; }
QPushButton:disabled, QToolButton:disabled { background:#25252b; color:#666674; border-color:#303037; }
QPushButton[role='primary'] { background:#baa6fa; color:#241d36; border:0; font-weight:500; padding:8px 10px; }
QPushButton[role='primary']:hover { background:#cbbfff; }
QPushButton[role='primary']:disabled { background:#3c354f; color:#8c809f; }
QToolButton[role='ghost'], QToolButton[role='window'], QToolButton[role='mode'] { background:transparent; border:0; color:#a4a1ae; padding:3px 6px; }
QToolButton[role='window'] { padding:0; }
QToolButton[role='mode'] { color:#eeedf2; padding:3px 14px 3px 5px; }
QToolButton[role='ghost']:hover, QToolButton[role='window']:hover, QToolButton[role='mode']:hover { background:#37343e; }
QToolButton[role='ghost']:checked { background:#373041; color:#d2c5ff; }
QToolButton[role='ghost']:disabled, QToolButton[role='window']:disabled { background:transparent; color:#666674; }
QToolButton[role='mode']::menu-indicator { subcontrol-origin:padding; subcontrol-position:right center; right:3px; width:9px; }
QToolButton:checked { background:#40384f; border-color:#8b78b2; color:#e3d6ff; }
QComboBox { background:#2a2a2f; color:#e4e4ed; border:1px solid #39383e; border-radius:5px; padding:4px 7px; min-height:18px; }
QComboBox[role='layer'] { background:transparent; border:0; color:#a4a1ae; font-size:11px; padding:3px 5px; }
QComboBox:focus { border-color:#b8a8ff; }
QComboBox::drop-down { border:0; width:18px; }
QComboBox#MoriCompactCombo::down-arrow { image:none; }
QComboBox QAbstractItemView { background:#26262d; color:#eeeeF5; selection-background-color:#454052; border:1px solid #4a4658; }
QPlainTextEdit { background:#2a2a2f; color:#eeedf2; border:1px solid #39383e; border-radius:6px; padding:10px; selection-background-color:#645489; font-size:13px; }
QPlainTextEdit:focus { border-color:#b8a8ff; }
QCheckBox { color:#c5c5d1; spacing:6px; background:transparent; }
QCheckBox::indicator { width:14px; height:14px; border-radius:3px; border:1px solid #6b657b; background:#17171c; }
QCheckBox::indicator:checked { background:#b8a8ff; border-color:#b8a8ff; }
QCheckBox#MoriLiveSwitch::indicator { width:27px; height:15px; border-radius:8px; }
QListWidget { background:#17171c; color:#c9c9d6; border:1px solid #3b3b44; border-radius:5px; padding:3px; }
QListWidget::item { padding:3px; border-radius:3px; }
QListWidget::item:selected { background:#34303f; }
QScrollArea { background:#222225; border:0; }
QScrollBar:vertical { background:transparent; width:7px; margin:2px; }
QScrollBar::handle:vertical { background:#45434f; min-height:26px; border-radius:3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QSplitter::handle:vertical { background:#222225; height:4px; }
QSplitter::handle:vertical:hover { background:#8b78b2; }
QMenu { background:#24242b; color:#ededf3; border:1px solid #46434f; padding:5px; }
QMenu::item { padding:7px 20px; border-radius:5px; }
QMenu::item:selected { background:#3a344b; }
QFrame#MoriPopover { background:#2a2a2f; border:1px solid #46434f; border-radius:7px; }
QWidget#MoriPreviewSurface { background:#17171a; color:#eeedf2; }
/* The live editor keeps one quiet prompt surface and a single primary action. */
QFrame#MoriLivePrompt { background:#2a2a2f; border:0; border-radius:8px; }
QPlainTextEdit#MoriLivePromptEdit, QPlainTextEdit#MoriLivePromptEdit:focus {
    background:transparent; border:0; padding:7px; border-radius:5px;
}
QComboBox[role='live-model'] { background:transparent; border:0; padding:3px 16px 3px 3px; color:#bdbdc7; font-size:11px; }
QComboBox[role='reference'] { background:#2a2a2f; border:0; padding:5px 20px 5px 9px; color:#d8d8df; }
QComboBox[role='reference']:focus { border:0; }
QPushButton#MoriLiveStart { min-width:48px; padding:4px 11px; font-size:12px; }
QPushButton#MoriLiveStart[live='true'] { background:#3c334b; color:#d3c0f5; }
QPushButton#MoriLiveApply { background:transparent; border:0; padding:5px 0; color:#c0bec9; }
QPushButton#MoriLiveApply:disabled { background:transparent; color:#74747e; }
QToolButton#MoriLiveAddReference { background:transparent; border:0; padding:3px 4px; color:#bdbdc7; font-size:11px; }
QLabel#MoriBeta { background:#312d3b; color:#b8a9d7; border:0; border-radius:3px; padding:1px 5px; font-size:10px; }
QSlider#MoriVariation { background:transparent; }
QSlider#MoriVariation::groove:horizontal { height:8px; background:transparent; border:0; border-radius:4px; }
QSlider#MoriVariation::handle:horizontal { background:transparent; border:0; width:20px; margin:-6px 0; border-radius:10px; }
QLabel#MoriVariationValue { background:#2b2b30; color:#e5e1ee; border-radius:5px; padding:2px 5px; font-size:11px; }
QWidget#MoriWorkbenchToolbar { background:#222225; }
QComboBox[role='workbench-source'] { background:transparent; border:0; padding:4px 5px; color:#c4c1cc; font-size:11px; }
QComboBox[role='workbench-source']:focus { border:0; }
QToolButton#MoriTransferOptions::menu-indicator { image:none; width:0; }
QPushButton[role='transfer'], QPushButton[role='transfer-secondary'] { background:#303035; color:#eeedf2; border:0; padding:4px 12px; border-radius:9px; font-size:11px; }
QPushButton[role='transfer'] { background:#6845ff; color:#ffffff; }
QPushButton[role='transfer']:hover { background:#7858ff; }
QPushButton[role='transfer']:pressed { background:#5836e8; }
QPushButton[role='transfer-secondary']:hover { background:#3c3b42; }
QPushButton[role='transfer-secondary']:pressed { background:#29282e; }
QPushButton[role='transfer']:disabled, QPushButton[role='transfer-secondary']:disabled { background:#2b2931; color:#77727f; }
"""


LIVE_STATES = {
    state: (title, color)
    for title, color, states in (
        ("未开始", "#a4a1ae", ("idle", "no_document", "connecting", "login", "needs_prompt", "blocked", "ready", "waiting", "watching")),
        ("生成中", "#b8a8ff", ("preparing", "uploading", "submitting", "queued", "generating", "downloading")),
        ("已完成", "#92d6b0", ("completed", "applied", "unchanged")),
        ("已暂停", "#a4a1ae", ("paused", "stopped")),
        ("出错", "#f0a6ae", ("disconnected", "timeout", "uncertain", "error")),
    )
    for state in states
}


def set_dark_surface(widget, color="#222225"):
    # Cover native-window erase/resize before Qt's first styled paint.
    palette = widget.palette()
    palette.setColor(palette.Window, QColor(color))
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)
    widget.setAttribute(Qt.WA_StyledBackground, True)


def _label(text, role=None):
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    return label


def ui_icon(name, color="#a4a1ae"):
    """Small scalable outline controls, without platform bitmap decorations."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(color), 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.setBrush(Qt.NoBrush)

    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))

    if name == "close":
        line(4, 4, 12, 12); line(12, 4, 4, 12)
    elif name in ("float", "expand", "fit"):
        if name == "float":
            painter.drawRect(QRectF(2.5, 5.5, 8, 8))
            line(7, 2.5, 13.5, 2.5); line(13.5, 2.5, 13.5, 9); line(7, 9, 13.5, 2.5)
        elif name == "fit":
            painter.drawRect(QRectF(3.5, 4, 9, 8))
            line(1, 8, 3, 8); line(13, 8, 15, 8)
            line(8, 1, 8, 3); line(8, 13, 8, 15)
        else:
            for x, y, dx, dy in ((2.5, 2.5, 4, 4), (13.5, 2.5, -4, 4),
                                  (2.5, 13.5, 4, -4), (13.5, 13.5, -4, -4)):
                line(x, y, x + dx, y); line(x, y, x, y + dy)
    elif name == "refresh":
        painter.drawArc(QRectF(3, 3, 10, 10), 40 * 16, 285 * 16)
        line(13, 2.5, 13, 6); line(13, 6, 9.5, 6)
    elif name == "play":
        painter.drawPolygon(QPolygonF([QPointF(5, 3), QPointF(13, 8), QPointF(5, 13)]))
    elif name == "pause":
        line(5.5, 3.5, 5.5, 12.5); line(10.5, 3.5, 10.5, 12.5)
    elif name == "plus":
        line(3.5, 8, 12.5, 8); line(8, 3.5, 8, 12.5)
    elif name == "apply":
        line(12.5, 3, 12.5, 8); line(12.5, 8, 3, 8)
        line(3, 8, 6.5, 4.5); line(3, 8, 6.5, 11.5)
    elif name == "send":
        line(4, 12, 12, 4); line(5, 4, 12, 4); line(12, 4, 12, 11)
    elif name == "more":
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        for x in (3.5, 8, 12.5):
            painter.drawEllipse(QPointF(x, 8), .9, .9)
    elif name == "settings":
        for y, x in ((4, 6), (8, 11), (12, 5)):
            line(2.5, y, x - 1.5, y); line(x + 1.5, y, 13.5, y)
            painter.drawEllipse(QPointF(x, y), 1.5, 1.5)
    elif name == "reference":
        painter.drawRoundedRect(QRectF(2.5, 3.5, 11, 10), 1, 1)
        painter.drawEllipse(QPointF(5.5, 6.5), .8, .8)
        line(3, 12, 6.5, 8.5); line(6.5, 8.5, 9, 11); line(9, 11, 12.5, 7.5)
    elif name == "compare":
        painter.drawRoundedRect(QRectF(2, 3, 12, 10), 1, 1)
        line(8, 3, 8, 13)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def icon_button(name, tooltip, parent=None):
    button = QToolButton(parent)
    button.setProperty("role", "ghost")
    button.setIcon(ui_icon(name))
    button.setIconSize(QSize(14, 14))
    button.setFixedSize(26, 26)
    button.setToolTip(tooltip)
    button.setAccessibleName(tooltip)
    return button


class _CompactComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MoriCompactCombo")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor("#a4a1ae" if self.isEnabled() else "#666674"),
                            1.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        x, y = self.width() - 10, self.height() / 2
        painter.drawPolyline(QPolygonF([QPointF(x - 3, y - 1.5), QPointF(x, y + 1.5),
                                        QPointF(x + 3, y - 1.5)]))


class _VariationSlider(QSlider):
    """Native five-percent stops with a painted rail and finite interaction glow."""
    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setRange(0, 100)
        self.setSingleStep(5)
        self.setPageStep(5)
        self.setTracking(False)
        self.setMinimumHeight(28)
        self.setMouseTracking(True)
        self._flow_phase = 1.0
        self._motion_enabled = self._native_motion_enabled()
        self._flow_animation = QVariantAnimation(self)
        self._flow_animation.setStartValue(0.0)
        self._flow_animation.setEndValue(1.0)
        self._flow_animation.setDuration(1100)
        self._flow_animation.setEasingCurve(QEasingCurve.InOutSine)
        self._flow_animation.valueChanged.connect(self._paint_flow)
        self.sliderMoved.connect(self._snap_drag)
        self.sliderMoved.connect(self._play_flow)
        self.actionTriggered.connect(lambda action: self.setSliderPosition(self.sliderPosition()))
        self.valueChanged.connect(self._snap_value)
        self.valueChanged.connect(self._play_flow)

    @staticmethod
    def _native_motion_enabled():
        # Read the OS animation preference; never change a Windows setting.
        import sys
        if sys.platform == "win32":
            try:
                import ctypes
                enabled = ctypes.c_int(1)
                if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                    return bool(enabled.value)
            except (AttributeError, OSError):
                pass
        return True

    def _paint_flow(self, phase):
        self._flow_phase = float(phase)
        self.update()

    def _play_flow(self, *unused):
        app = QApplication.instance()
        if (not self._motion_enabled or not self.isVisible() or not self.isEnabled()
                or self.sliderPosition() == 0 or (app and app.property("moriReduceMotion"))):
            return
        if self._flow_animation.state() != QAbstractAnimation.Running:
            self._flow_animation.start()

    def enterEvent(self, event):
        super().enterEvent(event)
        self._play_flow()
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def hideEvent(self, event):
        self._flow_animation.stop()
        self._flow_phase = 1.0
        super().hideEvent(event)

    def paintEvent(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        center = QRectF(handle).center()
        track = QRectF(handle.width() / 2, self.height() / 2 - 4,
                       max(1, self.width() - handle.width()), 8)
        active = QRectF(track)
        if option.upsideDown:
            active.setLeft(center.x())
        else:
            active.setRight(center.x())
        if self.sliderPosition() == self.minimum():
            active.setWidth(0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#3a3a3f"))
        painter.drawRoundedRect(track, 4, 4)
        if active.width() > 0:
            gradient = QLinearGradient(active.topLeft(), active.topRight())
            gradient.setColorAt(0, QColor("#a175f5" if self.isEnabled() else "#55515e"))
            gradient.setColorAt(1, QColor("#6845ff" if self.isEnabled() else "#55515e"))
            painter.setBrush(gradient)
            painter.drawRoundedRect(active, 4, 4)
            if self.isEnabled():
                path = QPainterPath()
                path.addRoundedRect(active, 4, 4)
                painter.save()
                painter.setClipPath(path)
                phase = self._flow_phase
                envelope = 4 * phase * (1 - phase)
                if self._flow_animation.state() == QAbstractAnimation.Running:
                    beam = active.left() + active.width() * (-.2 + 1.4 * phase)
                    glow = QLinearGradient(beam - 20, 0, beam + 20, 0)
                    glow.setColorAt(0, QColor(255, 255, 255, 0))
                    glow.setColorAt(.5, QColor(255, 255, 255, int(55 * envelope)))
                    glow.setColorAt(1, QColor(255, 255, 255, 0))
                    painter.fillRect(active, glow)
                for index, fraction in enumerate((.22, .46, .69, .85)):
                    x = active.left() + active.width() * fraction + envelope * 7
                    y = track.center().y() + (1 if index % 2 else -1)
                    painter.setBrush(QColor(255, 255, 255, int(95 + envelope * 95)))
                    painter.drawEllipse(QPointF(x, y), .8 if index % 2 else 1.1, .8 if index % 2 else 1.1)
                painter.restore()
        radius = 10 if self.isSliderDown() or self.underMouse() else 9
        painter.setBrush(QColor(0, 0, 0, 30))
        painter.drawEllipse(center + QPointF(0, 1), radius + .8, radius + .8)
        painter.setBrush(QColor("#ffffff" if self.isEnabled() else "#9a98a2"))
        painter.drawEllipse(center, radius, radius)
        if self.hasFocus() and option.state & QStyle.State_KeyboardFocusChange:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor("#b9a0ff"), 1))
            painter.drawEllipse(center, radius + 2, radius + 2)

    @staticmethod
    def snapped(value):
        return max(0, min(100, ((int(value) + 2) // 5) * 5))

    def setValue(self, value):
        super().setValue(self.snapped(value))

    def setSliderPosition(self, position):
        super().setSliderPosition(self.snapped(position))

    def _snap_drag(self, position):
        if position != self.snapped(position):
            blocked = self.blockSignals(True)
            self.setSliderPosition(position)
            self.blockSignals(blocked)

    def _snap_value(self, value):
        # Accessibility setters can call the C++ method directly.
        if value != self.snapped(value):
            blocked = self.blockSignals(True)
            self.setValue(value)
            self.blockSignals(blocked)


class _PopupFrame(QFrame):
    """Transient controls never take height away from the image preview."""
    def __init__(self, anchor):
        super().__init__(anchor, Qt.Popup | Qt.FramelessWindowHint)
        self.anchor = anchor
        self.setObjectName("MoriPopover")
        self.setAttribute(Qt.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WA_NoMouseReplay, True)
        set_dark_surface(self, "#2a2a2f")
        self.setStyleSheet(MORI_STYLE)

    def open(self):
        self.adjustSize()
        available = QApplication.desktop().availableGeometry(self.anchor)
        position = self.anchor.mapToGlobal(QPoint(self.anchor.width() - self.width(), self.anchor.height() + 5))
        x = max(available.left(), min(position.x(), available.right() - self.width() + 1))
        y = position.y()
        if y + self.height() > available.bottom():
            y = self.anchor.mapToGlobal(QPoint(0, -self.height() - 5)).y()
        self.move(x, max(available.top(), y))
        self.show()
        self.raise_()

    def hideEvent(self, event):
        self.anchor.blockSignals(True)
        self.anchor.setChecked(False)
        self.anchor.blockSignals(False)
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.anchor.setFocus()
            event.accept()
        else:
            super().keyPressEvent(event)


class ImagePreview(QWidget):
    """Paint the original image at the current viewport size."""
    open_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MoriPreview")
        self.setMinimumSize(100, 100)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip("滚轮缩放 · 拖动查看 · 双击放大预览")
        self._image = None
        self._zoom = 1.0
        self._pan = QPointF()
        self._drag = None

    def sizeHint(self):
        return QSize(400, 600)

    def set_image(self, image, reset=False):
        self._image = image
        if reset or image is None:
            self.reset_view()
        self.update()

    def reset_view(self):
        self._zoom = 1.0
        self._pan = QPointF()
        self._drag = None
        self.update()

    def image_rect(self):
        if self._image is None or self._image.isNull():
            return QRectF()
        scale = min(self.width() / self._image.width(), self.height() / self._image.height()) * self._zoom
        width, height = self._image.width() * scale, self._image.height() * scale
        return QRectF((self.width() - width) / 2 + self._pan.x(),
                      (self.height() - height) / 2 + self._pan.y(), width, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#17171a"))
        if self._image is None:
            # An empty image area needs no repeated instructions or heading.
            ui_icon("reference", "#414148").paint(painter,
                                                   int(self.width() / 2 - 12),
                                                   int(self.height() / 2 - 12), 24, 24)
        else:
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(self.image_rect(), self._image)

    def wheelEvent(self, event):
        if self._image is None:
            return super().wheelEvent(event)
        before = self._zoom
        self._zoom = max(0.25, min(16.0, before * 1.2 ** (event.angleDelta().y() / 120.0)))
        ratio = self._zoom / before
        anchor = QPointF(event.pos()) - QPointF(self.width() / 2, self.height() / 2)
        self._pan = anchor - (anchor - self._pan) * ratio
        self.update()
        event.accept()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._image is not None:
            self._drag = QPointF(event.pos())
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self._pan += QPointF(event.pos()) - self._drag
            self._drag = QPointF(event.pos())
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self._image is not None:
            self._drag = None
            self.unsetCursor()
            self.open_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class RealtimePanel(QWidget):
    parameters_changed = pyqtSignal()
    sources_changed = pyqtSignal()
    auto_changed = pyqtSignal(bool)
    generate_requested = pyqtSignal()
    apply_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    login_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RealtimePanel")
        set_dark_surface(self)
        self._image = self._source_image = None
        self._busy = self._available = False
        self._models = []
        self._large_dialog = None
        self._layer_choices = []
        self.reference_rows = []
        self._status_error = False
        self.status_state = "idle"
        self._pending = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.controls_scroll = QScrollArea(self)
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setFrameShape(QFrame.NoFrame)
        self.controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        set_dark_surface(self.controls_scroll.viewport())
        self.controls_content = QWidget()
        self.controls_content.setObjectName("MoriControls")
        set_dark_surface(self.controls_content)
        self.controls_scroll.setWidget(self.controls_content)
        layout = QVBoxLayout(self.controls_content)
        layout.setContentsMargins(12, 4, 12, 12)
        layout.setSpacing(8)
        outer.addWidget(self.controls_scroll)

        # Controller-owned state stays available without adding chrome.
        self.account, self.balance, self.cost = _label(""), _label(""), _label("")
        self.login = QPushButton("登录 Mori", self)
        self.login.clicked.connect(self.login_requested)
        self.refresh = QToolButton(self)
        self.refresh.clicked.connect(self.refresh_requested)
        self.live = QCheckBox(self)
        self.live.toggled.connect(self.auto_changed)
        for widget in (self.account, self.balance, self.cost, self.login, self.refresh, self.live):
            widget.setParent(self)
            widget.hide()

        self.prompt_card = QFrame()
        self.prompt_card.setObjectName("MoriLivePrompt")
        card = QVBoxLayout(self.prompt_card)
        card.setContentsMargins(5, 5, 7, 7)
        card.setSpacing(1)
        self.prompt = QPlainTextEdit()
        self.prompt.setObjectName("MoriLivePromptEdit")
        self.prompt.setAccessibleName("提示词")
        self.prompt.setPlaceholderText("描述你想要的效果…")
        self.prompt.setFixedHeight(84)
        self.prompt.textChanged.connect(self.parameters_changed)
        card.addWidget(self.prompt)
        tools = QHBoxLayout()
        tools.setSpacing(5)
        self.model = _CompactComboBox()
        self.model.setProperty("role", "live-model")
        self.model.setMinimumWidth(75)
        self.model.setMaximumWidth(180)
        self.model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(7)
        self.model.setToolTip("生成模型")
        self.model.setAccessibleName("生成模型")
        self.model.addItem("Banana 2.1", "gemini-nano-banana-2.1")
        tools.addWidget(self.model, 1)
        self.resolution = _CompactComboBox()
        self.resolution.setProperty("role", "live-model")
        self.resolution.setFixedWidth(58)
        self.resolution.setToolTip("输出精度")
        self.resolution.setAccessibleName("输出精度")
        for item in ("1K", "2K", "4K"):
            self.resolution.addItem(item, item)
        tools.addWidget(self.resolution)
        tools.addStretch()
        self.references_toggle = QToolButton()
        self.references_toggle.setObjectName("MoriLiveAddReference")
        self.references_toggle.setIcon(ui_icon("plus"))
        self.references_toggle.setIconSize(QSize(13, 13))
        self.references_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.references_toggle.setText("参考")
        self.references_toggle.setAccessibleName("添加参考")
        self.references_toggle.setToolTip("添加参考用途与来源图层；默认读取当前画布。")
        self.references_toggle.setMinimumHeight(30)
        self.references_toggle.clicked.connect(lambda: self.add_reference())
        tools.addWidget(self.references_toggle)
        self.generate = QPushButton("开始")
        self.generate.setObjectName("MoriLiveStart")
        self.generate.setProperty("role", "primary")
        self.generate.setMinimumHeight(32)
        self.generate.setIconSize(QSize(13, 13))
        self.generate.setIcon(ui_icon("play", "#241d36"))
        self.generate.setToolTip("开始实时：停笔后自动更新预览")
        self.generate.setAccessibleName("开始实时")
        self.generate.clicked.connect(lambda: self.live.setChecked(not self.live.isChecked()))
        self.generate.setEnabled(False)
        tools.addWidget(self.generate)
        self.submit = QPushButton("提交")
        self.submit.setObjectName("MoriLiveSubmit")
        self.submit.setMinimumHeight(32)
        self.submit.setToolTip("按当前参数只生成一张，完成后停止")
        self.submit.setAccessibleName("提交生成一次")
        self.submit.clicked.connect(self.generate_requested)
        self.submit.setEnabled(False)
        tools.addWidget(self.submit)
        card.addLayout(tools)
        layout.addWidget(self.prompt_card)

        # Change amount is committed on release, so dragging cannot enqueue jobs.
        variation_row = QVBoxLayout()
        variation_row.setContentsMargins(2, 0, 1, 0)
        variation_row.setSpacing(0)
        variation_heading = QHBoxLayout()
        variation_heading.addWidget(_label("变化幅度", "muted"))
        variation_heading.addStretch()
        self.variation = _VariationSlider()
        self.variation.setObjectName("MoriVariation")
        self.variation.setValue(50)
        self.variation.setAccessibleName("变化幅度，0保持原图，100只按关键词与参考发散")
        self.variation.setToolTip("每格 5%\n0% 原图，不调用 AI\n50% 基于画布调整材质与造型\n100% 仅按关键词与额外参考发散，不上传画布\n中间数值控制生成倾向，不是精确相似度")
        self.variation_value = _label("50%", "muted")
        self.variation_value.setObjectName("MoriVariationValue")
        self.variation_value.setFixedWidth(46)
        self.variation_value.setAlignment(Qt.AlignCenter)
        variation_heading.addWidget(self.variation_value)
        variation_row.addLayout(variation_heading)
        variation_row.addWidget(self.variation)
        self.variation.sliderMoved.connect(lambda value: self.variation_value.setText("%d%%" % self.variation.sliderPosition()))
        self.variation.valueChanged.connect(self._variation_changed)
        layout.addLayout(variation_row)

        self.references_box = QWidget()
        self.references_layout = QVBoxLayout(self.references_box)
        self.references_layout.setContentsMargins(0, 0, 0, 0)
        self.references_layout.setSpacing(6)
        self.references_box.hide()
        layout.addWidget(self.references_box)

        # Less-used controls remain reachable from the titlebar settings menu.
        self.options_toggle = QToolButton(self)
        self.options_toggle.setCheckable(True)
        self.options_toggle.hide()
        self.options_box = _PopupFrame(self.generate)
        self.options_box.setFixedWidth(290)
        grid = QGridLayout(self.options_box)
        grid.setContentsMargins(12, 12, 12, 12)
        grid.setHorizontalSpacing(9)
        grid.setVerticalSpacing(9)
        self.subject = _CompactComboBox()
        self.subject.setAccessibleName("画面来源")
        self.subject.setMinimumWidth(0)
        self.subject.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.subject.addItem("当前画布", CANVAS_SOURCE)
        self.subject.currentIndexChanged.connect(self._sources_updated)
        self.quality = _CompactComboBox()
        for label, value in (("快速", "low"), ("标准", "medium"), ("精细", "high")):
            self.quality.addItem(label, value)
        self.delay = _CompactComboBox()
        for label, value in (("停笔 1 秒", 1.0), ("停笔 2 秒", 2.0), ("停笔 4 秒", 4.0)):
            self.delay.addItem(label, value)
        self.delay.setCurrentIndex(1)
        grid.addWidget(_label("画面来源", "muted"), 0, 0)
        grid.addWidget(self.subject, 0, 1)
        grid.addWidget(_label("生成质量", "muted"), 1, 0)
        grid.addWidget(self.quality, 1, 1)
        grid.addWidget(_label("停笔后更新", "muted"), 2, 0)
        grid.addWidget(self.delay, 2, 1)
        self.writeback = QCheckBox("新结果自动放入画布")
        self.writeback.setToolTip("默认先预览，满意后手动放入；结果写入独立图层。")
        grid.addWidget(self.writeback, 3, 0, 1, 2)
        self.document = _label("尚未打开画布", "muted")
        self.document.setWordWrap(True)
        grid.addWidget(self.document, 4, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        self.options_box.hide()
        self.options_toggle.toggled.connect(lambda checked: self.show_options() if checked else self.options_box.hide())

        self.result_surface = QWidget(self)
        self.result_surface.setObjectName("MoriPreviewSurface")
        set_dark_surface(self.result_surface, "#17171a")
        result_layout = QVBoxLayout(self.result_surface)
        result_layout.setContentsMargins(12, 0, 12, 8)
        result_layout.setSpacing(0)
        outer.addWidget(self.result_surface, 1)
        self.preview = ImagePreview(self.result_surface)
        self.preview.open_requested.connect(self.open_large_preview)
        result_layout.addWidget(self.preview, 1)

        # Outside the settings scroll and preview: status is always in view.
        self.status_row = QFrame(self)
        self.status_row.setObjectName("MoriLiveStatus")
        self.status_row.setStyleSheet("QFrame#MoriLiveStatus { background:#222225; border:0; }")
        information = QHBoxLayout(self.status_row)
        information.setContentsMargins(12, 2, 12, 4)
        information.setSpacing(8)
        self.status_badge = _label("", "muted")
        self.status_badge.setAccessibleName("当前生成状态")
        information.addWidget(self.status_badge)
        information.addStretch()
        # Keep full details for controller consumers and the hover tooltip.
        self.status = _label("点击开始，停笔后自动生成预览。", "muted")
        self.status.setParent(self.status_row)
        self.status.setTextFormat(Qt.PlainText)
        self.status.hide()
        self.connection_action = QToolButton()
        self.connection_action.setProperty("role", "ghost")
        self.connection_action.setText("重新连接")
        self.connection_action.clicked.connect(self._connection_action)
        self.connection_action.hide()
        information.addWidget(self.connection_action)
        outer.insertWidget(1, self.status_row)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 2, 0, 0)
        footer.setSpacing(4)
        self.preview_tools = QWidget()
        preview_tools = QHBoxLayout(self.preview_tools)
        preview_tools.setContentsMargins(0, 0, 0, 0)
        preview_tools.setSpacing(2)
        self.fit = icon_button("fit", "适应预览区域，重置缩放和位置")
        self.fit.clicked.connect(self.preview.reset_view)
        preview_tools.addWidget(self.fit)
        self.compare = icon_button("compare", "对照原图")
        self.compare.setCheckable(True)
        self.compare.setEnabled(False)
        self.compare.toggled.connect(self._compare_source)
        preview_tools.addWidget(self.compare)
        self.expand = icon_button("expand", "打开大图预览；也可以双击图片")
        self.expand.setEnabled(False)
        self.expand.clicked.connect(self.open_large_preview)
        preview_tools.addWidget(self.expand)
        self.regenerate = icon_button("refresh", "生成一次")
        self.regenerate.setEnabled(False)
        self.regenerate.clicked.connect(self.generate_requested)
        preview_tools.addWidget(self.regenerate)
        self.preview_tools.hide()
        footer.addWidget(self.preview_tools)
        self.result_info = _label("", "muted")
        self.result_info.hide()
        footer.addStretch()
        self.apply = QPushButton("放入画布")
        self.apply.setObjectName("MoriLiveApply")
        self.apply.setMinimumHeight(32)
        self.apply.setIconSize(QSize(14, 14))
        self.apply.setIcon(ui_icon("apply"))
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self.apply_requested)
        footer.addWidget(self.apply)
        result_layout.addLayout(footer)
        self.live.toggled.connect(self._update_actions)
        for widget in (self.model, self.resolution, self.quality, self.delay):
            widget.currentIndexChanged.connect(self.parameters_changed)
        self.model.currentIndexChanged.connect(self._model_changed)
        self._model_changed()
        self.set_status(self.status.text(), state="idle")
        self._update_status_context()
        self._fit_controls()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._fit_controls)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "controls_scroll"):
            QTimer.singleShot(0, self._fit_controls)

    def _fit_controls(self):
        self.controls_content.layout().activate()
        height = self.controls_content.sizeHint().height()
        limit = max(170, min(390, int(self.height() * .44)))
        self.controls_scroll.setFixedHeight(min(height, limit))

    def show_options(self, anchor=None):
        self.options_box.anchor = anchor or self.generate
        self.options_box.open()

    def _variation_changed(self, value):
        self.variation_value.setText("%d%%" % self.variation.value())
        self.parameters_changed.emit()

    def _sources_updated(self, *unused):
        self.sources_changed.emit()

    def _fill_reference_layers(self, selector, selected=""):
        selector.blockSignals(True)
        selector.clear()
        selector.addItem("选择图层", "")
        for layer in self._layer_choices:
            selector.addItem(layer["name"] + (" · 隐藏" if not layer.get("visible", True) else ""), layer["id"])
        selector.setCurrentIndex(max(0, selector.findData(selected)))
        selector.blockSignals(False)

    def add_reference(self, role=None, layer_id="", *, notify=True):
        if len(self.reference_rows) >= 6:
            return None
        presets = sorted(REFERENCE_PRESETS, key=lambda item: (item[0] != "identity", list(REFERENCE_PRESETS).index(item)))
        used = {row["purpose"].currentData() for row in self.reference_rows}
        role = role or next((item[0] for item in presets if item[0] not in used), presets[0][0])
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        purpose = _CompactComboBox()
        purpose.setProperty("role", "reference")
        purpose.setAccessibleName("参考用途")
        purpose.setMinimumWidth(0)
        purpose.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        for identifier, label, hint, instruction in presets:
            purpose.addItem("主体参考" if identifier == "identity" else label, identifier)
            purpose.setItemData(purpose.count() - 1, hint, Qt.ToolTipRole)
        purpose.setCurrentIndex(max(0, purpose.findData(role)))
        selector = _CompactComboBox()
        selector.setProperty("role", "reference")
        selector.setAccessibleName("参考来源图层")
        selector.setMinimumWidth(0)
        selector.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        selector.setToolTip("可选择隐藏图层，不会改变图层可见性")
        self._fill_reference_layers(selector, layer_id)
        remove = icon_button("close", "移除此参考")
        layout.addWidget(purpose, 3)
        layout.addWidget(selector, 5)
        layout.addWidget(remove)
        row = {"widget": widget, "purpose": purpose, "layer": selector, "remove": remove}
        purpose.currentIndexChanged.connect(self._sources_updated)
        selector.currentIndexChanged.connect(self._sources_updated)
        remove.clicked.connect(lambda: self.remove_reference(row))
        self.reference_rows.append(row)
        self.references_layout.addWidget(widget)
        self.references_box.show()
        self.references_toggle.setEnabled(len(self.reference_rows) < 6)
        self._fit_controls()
        QTimer.singleShot(0, self._fit_controls)
        if notify:
            purpose.setFocus()
            self.sources_changed.emit()
        return row

    def remove_reference(self, row, *, notify=True):
        if row not in self.reference_rows:
            return
        self.reference_rows.remove(row)
        self.references_layout.removeWidget(row["widget"])
        row["widget"].hide()
        row["widget"].deleteLater()
        self.references_box.setVisible(bool(self.reference_rows))
        self.references_toggle.setEnabled(True)
        self._fit_controls()
        if notify:
            self.references_toggle.setFocus()
            self.sources_changed.emit()

    def _connection_action(self):
        if self.connection_action.property("action") == "login":
            self.login_requested.emit()
        else:
            self.refresh_requested.emit()

    def set_connection_action(self, action=None):
        self.connection_action.setProperty("action", action)
        self.connection_action.setText("登录 Mori" if action == "login" else "重新连接")
        self.connection_action.setVisible(bool(action))
        self.status_row.show()

    def _model_changed(self):
        identifier = self.model.currentData() or ""
        model = next((model for model in self._models if model.get("id") == identifier), {})
        values = model.get("qualities", ["low", "medium", "high"] if identifier.startswith("gpt-image-") else [])
        current = self.quality.currentData()
        self.quality.blockSignals(True)
        self.quality.clear()
        labels = {"low": "快速", "medium": "标准", "high": "精细", "xhigh": "超精细", "max": "最高"}
        for value in values or ["low"]:
            self.quality.addItem(labels.get(value, value), value)
        index = self.quality.findData(current)
        self.quality.setCurrentIndex(max(0, index))
        self.quality.setEnabled(bool(values))
        self.quality.blockSignals(False)

    def set_models(self, models, preferred=None):
        current = preferred or self.model.currentData()
        self.model.blockSignals(True)
        self.model.clear()
        self._models = list(models)
        for model in models:
            self.model.addItem(model.get("name", model["id"]), model["id"])
        index = self.model.findData(current)
        if index < 0:
            index = self.model.findData("gemini-nano-banana-2.1")
        if index >= 0:
            self.model.setCurrentIndex(index)
        self.model.blockSignals(False)
        self._model_changed()

    def set_layers(self, layers, subject_id=CANVAS_SOURCE, reference_ids=None, *, reset=False):
        bindings = self.reference_bindings()
        replacement = None
        if reference_ids is not None:
            if reference_ids and isinstance(reference_ids[0], dict):
                replacement = reference_ids if reference_ids != bindings else None
            elif reset or set(reference_ids) != {item["layer_id"] for item in bindings}:
                replacement = [{"role": preset[0], "layer_id": identifier}
                               for preset, identifier in zip(REFERENCE_PRESETS, reference_ids)]
        if reset and replacement is None:
            replacement = []
        self._layer_choices = list(layers)
        self.subject.blockSignals(True)
        self.subject.clear()
        self.subject.addItem("当前画布", CANVAS_SOURCE)
        for layer in layers:
            self.subject.addItem("仅图层 · " + layer["name"], layer["id"])
        index = self.subject.findData(subject_id)
        self.subject.setCurrentIndex(max(0, index))
        self.subject.blockSignals(False)
        self.subject.setToolTip(self.subject.currentText())
        if replacement is not None:
            for row in list(self.reference_rows):
                self.remove_reference(row, notify=False)
            for binding in replacement[:6]:
                self.add_reference(binding["role"], binding["layer_id"], notify=False)
        else:
            for row in self.reference_rows:
                self._fill_reference_layers(row["layer"], row["layer"].currentData())
        self._fit_controls()

    def source_ids(self):
        return (self.subject.currentData() or CANVAS_SOURCE,
                list(dict.fromkeys(item["layer_id"] for item in self.reference_bindings())))

    def reference_bindings(self):
        return [{"role": row["purpose"].currentData(), "layer_id": row["layer"].currentData()}
                for row in self.reference_rows if row["layer"].currentData()]

    def settings(self):
        return {"model": self.model.currentData(), "prompt": self.prompt.toPlainText().strip(),
                "resolution": self.resolution.currentData(), "quality": self.quality.currentData(), "count": 1,
                "variation": self.variation.value(),
                "reference_bindings": self.reference_bindings()}

    def set_status(self, text, error=False, *, state=None):
        # Generic workbench notices cannot replace an active task's progress.
        if self._busy and state is None and not error:
            return
        self.status_state = "error" if error and state is None else (state or self.status_state)
        self.status.setText(str(text))
        self._status_error = bool(error)
        self._update_status_context()
        self.status_row.show()

    def set_pending(self, pending):
        self._pending = bool(pending)
        self._update_status_context()

    def _update_status_context(self):
        title, color = LIVE_STATES.get(self.status_state, LIVE_STATES["idle"])
        self.status_badge.setText("●  " + title)
        self.status_badge.setStyleSheet("color:%s;" % color)
        detail = self.status.text()
        if self._busy and not self.live.isChecked():
            detail += "\n本次任务仍在处理，完成后停止。"
        self.status_badge.setToolTip(detail)
        self.status_badge.setAccessibleDescription(detail)

    def set_available(self, available):
        self._available = bool(available)
        self._update_actions()

    def set_busy(self, busy):
        self._busy = bool(busy)
        self._update_actions()

    def _update_actions(self, *unused):
        live = self.live.isChecked()
        self._update_status_context()
        self.generate.setText("暂停" if live else "开始")
        self.generate.setAccessibleName("暂停实时" if live else "开始实时")
        self.generate.setToolTip("暂停后续自动生成，当前任务会继续完成" if live else "开始实时：停笔后自动更新预览")
        self.generate.setIcon(ui_icon("pause" if live else "play", "#d3c0f5" if live else "#241d36"))
        self.generate.setProperty("live", live)
        self.generate.style().unpolish(self.generate)
        self.generate.style().polish(self.generate)
        # Pausing a running stream must stay possible during a request.
        self.generate.setEnabled(live or (self._available and not self._busy))
        self.regenerate.setEnabled(self._available and not self._busy)
        self.submit.setEnabled(self._available and not self._busy)

    def set_source_preview(self, image):
        self._source_image = image.copy()
        self.compare.setEnabled(self._image is not None)
        if self.compare.isChecked():
            self._compare_source(True)

    def _compare_source(self, checked):
        image = self._source_image if checked and self._source_image is not None else self._image
        self.preview.set_image(image)
        self.compare.setToolTip("返回效果" if checked else "对照原图")
        self.compare.setAccessibleName("返回效果" if checked else "对照原图")

    def set_result(self, data, seconds):
        image = QImage.fromData(data)
        if image.isNull():
            raise ValueError("服务器返回的图片无法显示。")
        first = self._image is None
        self._image = image
        self.result_info.setText("%.1f 秒 · %d×%d" % (seconds, image.width(), image.height()))
        self.result_info.setToolTip(self.result_info.text())
        self.apply.setEnabled(True)
        self.expand.setEnabled(True)
        self.preview_tools.show()
        self.apply.setToolTip(self.result_info.text())
        self.compare.setEnabled(self._source_image is not None)
        self.preview.set_image(self._source_image if self.compare.isChecked() else image, reset=first)
        if self._large_dialog is not None:
            self._large_preview.set_image(image)
            self._large_info.setText(self.result_info.text())

    def clear_result(self):
        self._image = None
        self._source_image = None
        self.compare.setChecked(False)
        self.compare.setEnabled(False)
        self.preview.set_image(None)
        self.result_info.setText("")
        self.apply.setEnabled(False)
        self.expand.setEnabled(False)
        self.preview_tools.hide()
        if self._large_dialog is not None:
            self._large_preview.set_image(None)
            self._large_info.setText("等待生成")

    def open_large_preview(self):
        if self._image is None:
            return
        if self._large_dialog is None:
            dialog = QDialog(self.window())
            dialog.setWindowTitle("Mori · 效果预览")
            dialog.setStyleSheet(MORI_STYLE)
            set_dark_surface(dialog)
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(8, 8, 8, 8)
            row = QHBoxLayout()
            self._large_info = _label(self.result_info.text(), "muted")
            row.addWidget(self._large_info, 1)
            row.addWidget(_label("滚轮缩放 · 拖动查看", "muted"))
            fit = QPushButton("适应窗口")
            close = QPushButton("关闭 · Esc")
            row.addWidget(fit)
            row.addWidget(close)
            layout.addLayout(row)
            self._large_preview = ImagePreview(dialog)
            self._large_preview.setToolTip("滚轮缩放 · 拖动查看 · 双击适应窗口")
            self._large_preview.open_requested.connect(self._large_preview.reset_view)
            fit.clicked.connect(self._large_preview.reset_view)
            close.clicked.connect(dialog.reject)
            layout.addWidget(self._large_preview, 1)
            self._large_dialog = dialog
        self._large_preview.set_image(self._image)
        self._large_dialog.showMaximized()
        self._large_dialog.raise_()
        self._large_dialog.activateWindow()
