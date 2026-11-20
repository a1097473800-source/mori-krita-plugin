"""Update content and controls embedded in the Mori settings window."""
import re

from PyQt5.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                            QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget)


STYLE = """
QDialog { background:transparent; }
QFrame#UpdateWindow { background:#1d1d21; border:1px solid #38383f; border-radius:16px; }
QLabel { background:transparent; color:#eeeef2; font-family:'Microsoft YaHei UI'; font-size:13px; border:0; }
QLabel#UpdateBrand { color:#f2f0f6; font-size:12px; font-weight:600; }
QLabel#UpdateEyebrow { color:#86848f; font-size:12px; }
QLabel#UpdateHeading { font-size:19px; font-weight:600; color:#f5f3f8; }
QLabel#UpdateMuted { color:#96949f; font-size:12px; }
QLabel#UpdateVersionCaption { color:#9c99a7; font-size:12px; }
QLabel#UpdateVersion { font-family:'Segoe UI'; font-size:23px; font-weight:600; }
QLabel#UpdateArrow { color:#64616e; font-size:22px; }
QLabel#UpdateSectionTitle { color:#e5e2ec; font-size:13px; font-weight:600; }
QLabel#UpdateTag { color:#c2b0ef; font-family:'Segoe UI'; font-size:11px; }
QLabel#UpdateWebBadge { color:#bdb0dc; background:#37303f; padding:4px 9px; border-radius:9px; font-size:11px; }
QLabel#UpdateInstructions { color:#aaa5b6; font-size:12px; }
QFrame#UpdateVersionCard, QFrame#UpdateNotesCard, QFrame#UpdateWebCard {
  background:#26262b; border:1px solid #303036; border-radius:11px;
}
QFrame#UpdateDivider { background:#323238; border:0; }
QTextBrowser { background:transparent; color:#bcb8c7; border:0; padding:0;
  font-family:'Microsoft YaHei UI'; font-size:13px; selection-background-color:#655283; }
QScrollBar:vertical { background:transparent; width:5px; margin:2px 0; }
QScrollBar::handle:vertical { background:#504a5d; border-radius:2px; min-height:24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QPushButton { color:#dad5e4; background:#303036; border:1px solid #414049; border-radius:8px;
  padding:0 18px; min-height:38px; font-family:'Microsoft YaHei UI'; font-size:13px; }
QPushButton:hover { background:#3a3841; border-color:#55505f; }
QPushButton[variant='primary'] { background:#bdacef; color:#25202f; border:1px solid #bdacef; font-weight:600; }
QPushButton[variant='primary']:hover { background:#cdbdf9; border-color:#cdbdf9; }
QPushButton:disabled { color:#81798f; background:#302c39; border-color:#3c3647; }
QToolButton#UpdateClose { border:0; border-radius:7px; background:transparent; padding:0; }
QToolButton#UpdateClose:hover { background:#343238; }
QToolButton#UpdateRecordLink { color:#b6a5dc; background:transparent; border:0;
  font-family:'Microsoft YaHei UI'; font-size:12px; padding:0; }
QToolButton#UpdateRecordLink:hover { color:#d7c5ff; }
"""


def small_icon(kind, color="#aaa5b5"):
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    if kind == "close":
        painter.drawLine(4, 4, 12, 12)
        painter.drawLine(12, 4, 4, 12)
    else:
        painter.drawLine(4, 12, 12, 4)
        painter.drawLine(5, 4, 12, 4)
        painter.drawLine(12, 4, 12, 11)
    painter.end()
    return QIcon(pixmap)


class UpdateSymbol(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(58, 58)
        self.kind = "idle"
        self.phase = 0
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self.advance)

    def advance(self):
        self.phase = (self.phase + 5) % 360
        self.update()

    def set_state(self, kind):
        self.kind = kind
        if kind == "checking" and self.isVisible():
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if self.kind == "checking":
            self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#302a3c"))
        painter.drawRoundedRect(QRectF(0, 0, 58, 58), 16, 16)
        painter.setPen(QPen(QColor("#c6b3f4"), 2.3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self.kind == "ready":
            path = QPainterPath()
            path.moveTo(18, 29)
            path.lineTo(26, 37)
            path.lineTo(41, 21)
            painter.drawPath(path)
        elif self.kind == "error":
            painter.drawLine(29, 17, 29, 31)
            painter.drawPoint(29, 39)
        elif self.kind == "available":
            painter.drawLine(29, 16, 29, 37)
            painter.drawLine(21, 29, 29, 37)
            painter.drawLine(37, 29, 29, 37)
            painter.drawLine(18, 43, 40, 43)
        else:
            painter.translate(29, 29)
            painter.rotate(self.phase if self.kind == "checking" else -20)
            painter.drawArc(QRectF(-13, -13, 26, 26), 35 * 16, 270 * 16)
            painter.drawLine(11, -8, 11, -16)
            painter.drawLine(11, -8, 3, -8)
        painter.end()


class UpdateSwitch(QCheckBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 22)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("启动时自动检查更新")

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#b7a2eb" if self.isChecked() else "#4b4754"))
        painter.drawRoundedRect(QRectF(0, 1, 36, 20), 10, 10)
        painter.setBrush(QColor("#f5f1fc"))
        painter.drawEllipse(QRectF(19 if self.isChecked() else 3, 4, 14, 14))
        if self.hasFocus():
            painter.setPen(QPen(QColor("#dfcaff"), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(1, 2, 34, 18), 9, 9)
        painter.end()


class WindowHeader(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_offset = event.globalPos() - self.window().frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPos() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_offset = None
        super().mouseReleaseEvent(event)


class UpdatePage(QWidget):
    check_requested = pyqtSignal()
    download_requested = pyqtSignal()
    releases_requested = pyqtSignal()
    automatic_changed = pyqtSignal(bool)

    def __init__(self, version, automatic, parent=None):
        super().__init__(parent)
        self.current_version = version
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hero = QHBoxLayout()
        hero.setSpacing(17)
        self.symbol = UpdateSymbol()
        hero.addWidget(self.symbol, 0, Qt.AlignTop)
        hero_text = QVBoxLayout()
        hero_text.setSpacing(7)
        self.heading = self.label("让创作保持最新", "UpdateHeading")
        self.native_status = self.label("检查 Mori 插件与工作台网页的新版本。", "UpdateMuted")
        hero_text.addWidget(self.heading)
        hero_text.addWidget(self.native_status)
        hero.addLayout(hero_text, 1)
        layout.addLayout(hero)
        layout.addSpacing(14)

        versions = QFrame()
        versions.setObjectName("UpdateVersionCard")
        version_layout = QHBoxLayout(versions)
        version_layout.setContentsMargins(20, 11, 20, 11)
        current = QVBoxLayout()
        current.setSpacing(5)
        current.addWidget(self.label("当前版本", "UpdateVersionCaption"))
        self.current = self.label(version, "UpdateVersion")
        current.addWidget(self.current)
        version_layout.addLayout(current, 1)
        version_layout.addWidget(self.label("→", "UpdateArrow"))
        latest_layout = QVBoxLayout()
        latest_layout.setSpacing(5)
        latest_layout.addWidget(self.label("最新版本", "UpdateVersionCaption"))
        self.latest = self.label("—", "UpdateVersion")
        latest_layout.addWidget(self.latest)
        version_layout.addSpacing(30)
        version_layout.addLayout(latest_layout, 1)
        layout.addWidget(versions)
        layout.addSpacing(12)

        self.notes_card = QFrame()
        self.notes_card.setObjectName("UpdateNotesCard")
        notes_layout = QVBoxLayout(self.notes_card)
        notes_layout.setContentsMargins(18, 11, 18, 10)
        notes_layout.setSpacing(8)
        notes_header = QHBoxLayout()
        notes_header.addWidget(self.label("更新内容", "UpdateSectionTitle"))
        notes_header.addStretch()
        self.note_tag = self.label("", "UpdateTag")
        notes_header.addWidget(self.note_tag)
        notes_layout.addLayout(notes_header)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(False)
        self.notes.setOpenLinks(False)
        self.notes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.notes.setFixedHeight(64)
        notes_layout.addWidget(self.notes)
        self.records = QToolButton()
        self.records.setObjectName("UpdateRecordLink")
        self.records.setText("查看完整更新记录")
        self.records.setIcon(small_icon("external", "#b6a5dc"))
        self.records.setIconSize(QSize(13, 13))
        self.records.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.records.setCursor(Qt.PointingHandCursor)
        self.records.clicked.connect(self.releases_requested.emit)
        notes_layout.addWidget(self.records, 0, Qt.AlignLeft)
        layout.addWidget(self.notes_card)
        layout.addSpacing(12)

        web = QFrame()
        web.setObjectName("UpdateWebCard")
        web_layout = QVBoxLayout(web)
        web_layout.setContentsMargins(18, 13, 18, 13)
        web_layout.setSpacing(7)
        web_header = QHBoxLayout()
        web_header.addWidget(self.label("工作台网页", "UpdateSectionTitle"))
        self.web_version = self.label("随插件安装", "UpdateMuted")
        self.web_version.setWordWrap(False)
        web_header.addWidget(self.web_version)
        web_header.addStretch()
        self.web_badge = self.label("待检查", "UpdateWebBadge")
        web_header.addWidget(self.web_badge)
        web_layout.addLayout(web_header)
        self.web_status = self.label("兼容的网页更新会在后台下载。", "UpdateMuted")
        web_layout.addWidget(self.web_status)
        layout.addWidget(web)
        layout.addSpacing(13)

        automatic_row = QHBoxLayout()
        automatic_text = QVBoxLayout()
        automatic_text.setSpacing(4)
        automatic_text.addWidget(self.label("自动检查更新", "UpdateSectionTitle"))
        automatic_text.addWidget(self.label("启动后在后台检查，有新版本时提醒", "UpdateMuted"))
        automatic_row.addLayout(automatic_text, 1)
        self.automatic = UpdateSwitch()
        self.automatic.setChecked(automatic)
        self.automatic.toggled.connect(self.automatic_changed.emit)
        automatic_row.addWidget(self.automatic)
        layout.addLayout(automatic_row)
        self.instructions = self.label("下载后完整解压，退出 Krita，再运行「更新已安装插件.cmd」。", "UpdateInstructions")
        self.instructions.hide()
        layout.addSpacing(10)
        layout.addWidget(self.instructions)
        self.checked_at = self.label("尚未检查", "UpdateMuted")
        self.checked_at.setWordWrap(False)
        self.check = QPushButton("检查更新")
        self.check.setCursor(Qt.PointingHandCursor)
        self.check.setProperty("variant", "primary")
        self.check.clicked.connect(self.check_requested.emit)
        self.download = QPushButton("下载更新")
        self.download.setCursor(Qt.PointingHandCursor)
        self.download.setProperty("variant", "primary")
        self.download.setEnabled(False)
        self.download.hide()
        self.download.clicked.connect(self.download_requested.emit)
        self.notes.setPlainText("检查后在这里查看本次更新内容。")

    @staticmethod
    def label(text, name=""):
        label = QLabel(text)
        if name:
            label.setObjectName(name)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return label

    def render(self, result, release, web_version, web_message, pending, checked_at):
        available = bool(result and result.available and release)
        failed = bool(result and not result.checked)
        if pending:
            heading, state = "正在检查更新…", "checking"
        elif available:
            heading, state = "发现 Mori 新版本", "available"
        elif failed:
            heading, state = "暂时无法检查更新", "error"
        elif release and release.version == self.current_version:
            heading, state = "Mori 已是最新版本", "ready"
        elif release:
            heading, state = "当前版本较新", "ready"
        else:
            heading, state = "让创作保持最新", "idle"
        self.heading.setText(heading)
        self.symbol.set_state(state)
        message = result.message if result else "正在获取最新版本信息…" if pending else "检查 Mori 插件与工作台网页的新版本。"
        if release and release.version == self.current_version and not available and not failed and not pending:
            message = "插件已更新，继续专注你的下一次创作。"
        self.native_status.setText(message)
        self.current.setText(self.current_version)
        self.latest.setText(release.version if release else "—")
        self.latest.setStyleSheet("color:#c9b5fa;" if available else "")
        self.note_tag.setText("v" + release.version if release else "")
        self.notes.setPlainText(release.notes if release and release.notes else
            "打开完整更新记录，查看功能变化与历史版本。" if release else "检查后在这里查看本次更新内容。")
        self.download.setEnabled(bool(available and release.download_url))
        self.download.setVisible(bool(available and release.download_url))
        self.instructions.setVisible(bool(available and release.download_url))
        self.check.setText("检查中…" if pending else "重新检查" if result else "检查更新")
        self.check.setEnabled(not pending)
        self.check.setProperty("variant", "secondary" if available and release.download_url else "primary")
        self.check.style().unpolish(self.check)
        self.check.style().polish(self.check)
        match = re.match(r"(\d{4})(\d{2})(\d{2})", web_version)
        friendly = ".".join(match.groups()) if match else web_version
        self.web_version.setText("当前网页 · " + friendly)
        self.web_version.setToolTip(web_version)
        self.web_status.setToolTip(web_message)
        friendly_message = web_message.split(" · ", 1)[0]
        self.web_status.setText(friendly_message or "兼容的网页更新会在后台下载。")
        self.web_badge.setText("待重启" if "重开" in web_message or "重连" in web_message else
            "检查中" if "正在检查" in web_message else "已更新" if "已更新" in web_message or "最新" in web_message else
            "暂不可用" if "无法" in web_message or "未完成" in web_message or "尚未发布" in web_message else "待检查")
        self.checked_at.setText("上次检查 " + checked_at if checked_at else "尚未检查")
