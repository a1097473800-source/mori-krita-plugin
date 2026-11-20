"""Native vector loading screen, visible before the browser process has a window."""
import re
from xml.etree import ElementTree

from PyQt5.QtCore import QElapsedTimer, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .branding import WORDMARK_SVG
from .realtime_panel import set_dark_surface


def _wordmark_contours():
    """Read the approved SVG's cubic outlines without rasterizing the logo."""
    element = ElementTree.fromstring(WORDMARK_SVG).find('{http://www.w3.org/2000/svg}path')
    tokens = re.findall(r'[MCLZ]|-?(?:\d*\.\d+|\d+)(?:[eE][+-]?\d+)?', element.attrib['d'])
    contours = []
    path = None
    command = None
    index = 0
    while index < len(tokens):
        if tokens[index] in ('M', 'C', 'L', 'Z'):
            command = tokens[index]
            index += 1
        if command == 'Z':
            path.closeSubpath()
            command = None
            continue
        count = 6 if command == 'C' else 2
        values = [float(token) for token in tokens[index:index + count]]
        index += count
        if command == 'M':
            if path is not None:
                path.closeSubpath()
                contours.append(path)
            path = QPainterPath()
            path.moveTo(*values)
            command = 'L'
        elif command == 'C':
            path.cubicTo(*values)
        elif command == 'L':
            path.lineTo(*values)
    if path is not None:
        path.closeSubpath()
        contours.append(path)
    return [(path, path.length()) for path in contours]


class AnimatedMoriLogo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(208, 90)
        self.setAccessibleName("Mori 正在加载")
        self._renderer = QSvgRenderer(WORDMARK_SVG, self)
        self._contours = _wordmark_contours()
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._running = False
        self._timer = QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self.update)

    def set_running(self, running):
        self._running = bool(running)
        if self._running and self.isVisible():
            if not self._timer.isActive():
                self._elapsed.restart()
                self._timer.start()
        else:
            self._timer.stop()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.set_running(self._running)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        target = QRectF(22, 20, 164, 164 * 435 / 1541)
        phase = (self._elapsed.elapsed() % 2800) / 2800.0 if self._running else 1.0
        if self._running:
            progress = min(1.0, phase / 0.62)
            progress = progress * progress * (3 - 2 * progress)
            fill = max(0.0, min(1.0, (phase - 0.62) / 0.20))
            fade = min(1.0, (1.0 - phase) / 0.12)
            painter.save()
            painter.translate(target.left(), target.top())
            painter.scale(target.width() / 1541, target.height() / 435)
            painter.setBrush(Qt.NoBrush)
            for path, length in self._contours:
                guide = QPen(QColor('#555555'), 7.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                painter.setPen(guide)
                painter.setOpacity(0.30)
                painter.drawPath(path)
                if progress > 0:
                    pen = QPen(QColor('#eeeeee'), 9.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
                    units = length / pen.widthF()
                    pen.setDashPattern([units, units])
                    pen.setDashOffset((1.0 - progress) * units)
                    painter.setPen(pen)
                    painter.setOpacity((1.0 - fill) * fade)
                    painter.drawPath(path)
            painter.restore()
            painter.setOpacity(fill * fade)
            self._renderer.render(painter, target)
        else:
            painter.setOpacity(0.75)
            self._renderer.render(painter, target)
        painter.end()


class MoriLoadingScreen(QWidget):
    retry_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MoriLoadingScreen")
        set_dark_surface(self, "#222222")
        self.setStyleSheet("QWidget#MoriLoadingScreen { background:#222222; border:0; }"
                           "QLabel { color:#a6a6a6; background:transparent; font-size:12px; }"
                           "QPushButton { background:#303035; color:#eeeeee; border:1px solid #424242; border-radius:6px; padding:8px 18px; }"
                           "QPushButton:hover { background:#3a3a3f; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 24, 26, 24)
        layout.setSpacing(12)
        layout.addStretch()
        self.logo = AnimatedMoriLogo(self)
        layout.addWidget(self.logo, 0, Qt.AlignHCenter)
        self.message = QLabel("打开工作台后，将在此显示 Mori 创作空间。", self)
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setTextFormat(Qt.PlainText)
        self.message.hide()
        layout.addWidget(self.message)
        self.retry = QPushButton("重新加载", self)
        self.retry.clicked.connect(self.retry_requested)
        self.retry.hide()
        layout.addWidget(self.retry, 0, Qt.AlignHCenter)
        layout.addStretch()

    def set_loading(self, message):
        self.message.setText(message)
        self.message.hide()
        self.retry.hide()
        self.logo.set_running(True)

    def set_error(self, message):
        self.message.setText(message)
        self.message.show()
        self.retry.show()
        self.logo.set_running(False)

    def pause(self):
        self.logo.set_running(False)
