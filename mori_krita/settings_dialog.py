"""One native preferences window for both Mori modes."""
from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame,
                            QHBoxLayout, QLabel, QPushButton, QScrollArea,
                            QSizeGrip, QSizePolicy, QStackedWidget, QToolButton,
                            QVBoxLayout, QWidget)

from .realtime_panel import _CompactComboBox, ui_icon
from .update_dialog import STYLE as UPDATE_STYLE, UpdatePage, UpdateSwitch, WindowHeader, small_icon
from .version import VERSION


STYLE = UPDATE_STYLE + """
QWidget { font-family:'Microsoft YaHei UI'; font-size:12px; color:#eae7f0; }
QDialog#MoriSettings { background:transparent; }
QFrame#SettingsWindow { background:#222225; border:1px solid #414047; border-radius:14px; }
QFrame#SettingsHeader { background:transparent; border:0; border-bottom:1px solid #34343b; }
QLabel#SettingsBrand { font-family:'Segoe UI'; font-size:16px; font-weight:700; letter-spacing:2px; color:#faf8ff; }
QLabel#SettingsWindowTitle { color:#b6b1c2; font-size:13px; }
QLabel#SettingsVersion { font-family:'Segoe UI'; font-size:11px; color:#8b8797; }
QFrame#SettingsSidebar { background:#1d1d21; border:0; border-bottom-left-radius:13px; }
QLabel#SettingsEyebrow { color:#787381; font-size:10px; }
QPushButton#SettingsNav { background:transparent; border:1px solid transparent; border-radius:7px;
    color:#aaa5b5; text-align:left; padding:0 12px; min-height:41px; font-size:12px; }
QPushButton#SettingsNav:hover { background:#29272f; color:#ede7f8; }
QPushButton#SettingsNav:checked { background:#332c42; border-color:#463955; color:#d9c9fb; }
QPushButton#SettingsNav:focus { border-color:#8c76b7; }
QLabel#SettingsSideNote { color:#706b7c; font-size:10px; }
QStackedWidget#SettingsPages, QWidget#SettingsPage, QScrollArea#SettingsScroll { background:transparent; border:0; }
QScrollArea#SettingsScroll > QWidget > QWidget { background:#222225; }
QLabel#SettingsPageTitle { font-size:22px; font-weight:600; color:#f1edf7; }
QLabel#SettingsPageSubtitle { color:#8f899d; font-size:12px; }
QLabel#SettingsSection { color:#928a9f; font-size:11px; }
QFrame#SettingsCard { background:#29292e; border:1px solid #34343b; border-radius:10px; }
QFrame#SettingsRow { background:transparent; border:0; }
QFrame#SettingsDivider { background:#3a383f; border:0; }
QLabel#SettingsRowTitle { color:#e1dce9; font-size:12px; font-weight:500; }
QLabel#SettingsRowHint { color:#918a9d; font-size:11px; }
QLabel#SettingsTip { color:#8e879c; font-size:11px; }
QComboBox { background:#333139; border:1px solid #44404d; border-radius:6px; padding:7px 23px 7px 10px; color:#dfd6ee; min-height:18px; }
QComboBox:hover { border-color:#736480; }
QComboBox:focus { border-color:#b3a0df; }
QComboBox:disabled { color:#847d90; background:#2b2930; border-color:#39353e; }
QComboBox::drop-down { border:0; width:22px; }
QComboBox QAbstractItemView { background:#29262f; color:#eae4f5; selection-background-color:#484052; border:1px solid #514859; padding:4px; }
QPushButton#SettingsChoice { background:#29292e; border:1px solid #3a373f; border-radius:9px; padding:15px; min-height:65px; text-align:left; }
QPushButton#SettingsChoice:hover { background:#302d37; border-color:#77668d; }
QPushButton#SettingsChoice:checked { background:#342d42; border:1px solid #9881bd; }
QLabel#SettingsChoiceTitle { color:#e9e1f5; font-size:13px; font-weight:500; }
QLabel#SettingsChoiceHint { color:#938b9f; font-size:11px; }
QLabel#SettingsRecommended { color:#c2a8ed; font-size:10px; }
QFrame#SettingsSegment { background:#242229; border:1px solid #3e3847; border-radius:7px; }
QPushButton#SettingsSegmentButton { background:transparent; border:0; border-radius:5px; padding:0 9px; min-height:30px; color:#9d94ac; font-size:11px; }
QPushButton#SettingsSegmentButton:checked { background:#494052; color:#e9dcfa; }
QPushButton#SettingsSegmentButton:focus { border:1px solid #ad95d1; }
QFrame#SettingsFooter { background:transparent; border:0; border-top:1px solid #34313a; }
QPushButton#SettingsReset { background:transparent; border:0; padding:0; min-height:32px; color:#9c93aa; font-size:11px; }
QPushButton#SettingsReset:hover { color:#d3c1ef; }
QLabel#SettingsSaved { color:#8c8399; font-size:11px; }
QPushButton#SettingsDone { background:#bba5e9; color:#2a2235; border:0; border-radius:7px; min-height:34px; padding:0 24px; font-size:12px; font-weight:600; }
QPushButton#SettingsDone:hover { background:#cbb8f4; }
QPushButton#SettingsDone:focus { border:1px solid #eee0ff; }
QSizeGrip { background:transparent; width:12px; height:12px; }
"""


def label(text, name, wrap=True):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.PlainText)
    widget.setWordWrap(wrap)
    widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    return widget


class SettingsEntry(QToolButton):
    """Keep the gear stable while a small dot announces an available update."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = False
        self.setObjectName("MoriSettingsEntry")
        self.setProperty("role", "ghost")
        self.setText("设置")
        self.setIconSize(QSize(16, 16))
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setFixedSize(62, 28)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Mori 设置")
        self.set_update_available(False)

    def set_update_available(self, available):
        self.available = bool(available)
        self.setIcon(ui_icon("gear", "#d2b8ff" if available else "#b3adbf"))
        self.setToolTip("Mori 设置 · 有新版本，前往「更新与版本」查看" if available else "Mori 设置")
        self.setAccessibleDescription(self.toolTip())
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.available:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor("#222225"), 1.5))
            painter.setBrush(QColor("#c2a0fc"))
            painter.drawEllipse(QPointF(21, 6), 3, 3)


class ChoiceCard(QPushButton):
    def __init__(self, title, hint, recommended=False, parent=None):
        super().__init__(parent)
        self.setObjectName("SettingsChoice")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)
        self.setAccessibleDescription(hint)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content = QVBoxLayout(self)
        content.setContentsMargins(15, 13, 15, 13)
        content.setSpacing(7)
        top = QHBoxLayout()
        top.setSpacing(6)
        title_label = label(title, "SettingsChoiceTitle", False)
        top.addWidget(title_label)
        top.addStretch()
        if recommended:
            top.addWidget(label("推荐", "SettingsRecommended", False))
        content.addLayout(top)
        content.addWidget(label(hint, "SettingsChoiceHint"))
        for child in self.findChildren(QLabel):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)


class SettingsDialog(QDialog):
    page_changed = pyqtSignal(str)
    PAGES = (("canvas", "画布与图层", "layers"),
             ("realtime", "实时与选区", "sliders"),
             ("updates", "更新与版本", "refresh"))

    def __init__(self, docker):
        super().__init__(docker, Qt.Dialog | Qt.FramelessWindowHint)
        self.docker = docker
        self.setObjectName("MoriSettings")
        self.setWindowTitle("Mori · 设置")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMinimumSize(740, 520)
        available = QApplication.desktop().availableGeometry(docker)
        self.resize(min(900, available.width() - 48), min(690, available.height() - 48))
        self.setStyleSheet(STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        window = QFrame(self)
        window.setObjectName("SettingsWindow")
        root.addWidget(window)
        layout = QVBoxLayout(window)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        header = WindowHeader(window)
        header.setObjectName("SettingsHeader")
        header.setFixedHeight(62)
        row = QHBoxLayout(header)
        row.setContentsMargins(24, 10, 17, 10)
        row.setSpacing(15)
        row.addWidget(label("MORI", "SettingsBrand", False))
        row.addWidget(label("/   插件设置", "SettingsWindowTitle", False))
        row.addStretch()
        row.addWidget(label("v" + VERSION, "SettingsVersion", False))
        self.close_button = QToolButton()
        self.close_button.setObjectName("UpdateClose")
        self.close_button.setFixedSize(28, 28)
        self.close_button.setIcon(small_icon("close"))
        self.close_button.setAccessibleName("关闭设置")
        self.close_button.setCursor(Qt.PointingHandCursor)
        self.close_button.clicked.connect(self.close)
        row.addWidget(self.close_button)
        for item in header.findChildren(QLabel):
            item.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(header)
        body = QHBoxLayout()
        body.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("SettingsSidebar")
        sidebar.setFixedWidth(172)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 24, 12, 19)
        side.setSpacing(7)
        eyebrow = label("偏好设置", "SettingsEyebrow")
        eyebrow.setContentsMargins(12, 0, 0, 8)
        side.addWidget(eyebrow)
        self.navigation = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for page, title, icon in self.PAGES:
            button = QPushButton(title)
            button.setObjectName("SettingsNav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(ui_icon(icon, "#b4a7c7"))
            button.setIconSize(QSize(17, 17))
            button.clicked.connect(lambda checked, key=page: self.set_page(key))
            self.nav_group.addButton(button)
            self.navigation[page] = button
            side.addWidget(button)
        side.addStretch()
        side_note = label("你的创作，\n你的习惯。", "SettingsSideNote")
        side_note.setContentsMargins(12, 0, 0, 1)
        side.addWidget(side_note)
        body.addWidget(sidebar)
        right = QVBoxLayout()
        right.setSpacing(0)
        self.pages = QStackedWidget()
        self.pages.setObjectName("SettingsPages")
        self._page_widgets = {}
        self._build_canvas()
        self._build_realtime()
        update_layout = self._page("updates", "更新与版本", "保持 Mori 插件与工作台同步更新。")
        self.updates = UpdatePage(VERSION, docker._settings.value("automaticUpdates", True, type=bool))
        update_layout.addWidget(self.updates)
        update_layout.addStretch()
        right.addWidget(self.pages, 1)
        footer = QFrame()
        footer.setObjectName("SettingsFooter")
        bottom = QHBoxLayout(footer)
        bottom.setContentsMargins(28, 12, 15, 13)
        bottom.setSpacing(12)
        self.reset = QPushButton("恢复本页默认")
        self.reset.setObjectName("SettingsReset")
        self.reset.setCursor(Qt.PointingHandCursor)
        self.reset.clicked.connect(self.reset_current_page)
        bottom.addWidget(self.reset)
        bottom.addWidget(self.updates.checked_at)
        bottom.addStretch()
        self.saved = label("修改后自动保存", "SettingsSaved", False)
        bottom.addWidget(self.saved)
        self.update_actions = QWidget()
        update_actions = QHBoxLayout(self.update_actions)
        update_actions.setContentsMargins(0, 0, 0, 0)
        update_actions.setSpacing(8)
        update_actions.addWidget(self.updates.check)
        update_actions.addWidget(self.updates.download)
        bottom.addWidget(self.update_actions)
        self.done = QPushButton("完成")
        self.done.setObjectName("SettingsDone")
        self.done.setCursor(Qt.PointingHandCursor)
        self.done.clicked.connect(self.close)
        bottom.addWidget(self.done)
        bottom.addWidget(QSizeGrip(self), 0, Qt.AlignBottom)
        right.addWidget(footer)
        body.addLayout(right, 1)
        layout.addLayout(body, 1)
        self._connect_controls()
        self.sync_controls()
        self.set_page("canvas")

    def _page(self, key, title, subtitle):
        scroll = QScrollArea()
        scroll.setObjectName("SettingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("SettingsPage")
        content.setMinimumWidth(0)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 27, 28, 23)
        layout.setSpacing(14)
        title_area = QVBoxLayout()
        title_area.setSpacing(7)
        title_area.addWidget(label(title, "SettingsPageTitle"))
        title_area.addWidget(label(subtitle, "SettingsPageSubtitle"))
        layout.addLayout(title_area)
        layout.addSpacing(5)
        scroll.setWidget(content)
        self.pages.addWidget(scroll)
        self._page_widgets[key] = scroll
        return layout

    @staticmethod
    def _card():
        card = QFrame()
        card.setObjectName("SettingsCard")
        content = QVBoxLayout(card)
        content.setContentsMargins(17, 3, 17, 3)
        content.setSpacing(0)
        return card, content

    @staticmethod
    def _row(layout, title, hint, control, separator=False):
        if separator:
            divider = QFrame()
            divider.setObjectName("SettingsDivider")
            divider.setFixedHeight(1)
            layout.addWidget(divider)
        item = QFrame()
        item.setObjectName("SettingsRow")
        row = QHBoxLayout(item)
        row.setContentsMargins(0, 15, 0, 15)
        row.setSpacing(20)
        text = QVBoxLayout()
        text.setSpacing(6)
        text.addWidget(label(title, "SettingsRowTitle"))
        text.addWidget(label(hint, "SettingsRowHint"))
        row.addLayout(text, 1)
        control.setAccessibleName(title)
        row.addWidget(control, 0, Qt.AlignVCenter)
        layout.addWidget(item)

    @staticmethod
    def _combo(width=146):
        combo = _CompactComboBox()
        combo.setFixedWidth(width)
        combo.setMinimumContentsLength(4)
        return combo

    def _build_canvas(self):
        layout = self._page("canvas", "画布与图层", "调整图片的发送方式，以及生成结果如何回到 Krita。")
        layout.addWidget(label("发送到 Mori", "SettingsSection"))
        quality_row = QHBoxLayout()
        quality_row.setSpacing(10)
        self.reference_group = QButtonGroup(self)
        self.reference_cards = []
        for index, (title, hint) in enumerate((("快速参考", "压缩上传副本，适合日常创作。\n最长边 2048 px，保留透明度。"),
                                                ("原图无损", "按原始尺寸上传。\n适合细节丰富的图片。"))):
            card = ChoiceCard(title, hint, index == 0)
            self.reference_group.addButton(card, index)
            self.reference_cards.append(card)
            quality_row.addWidget(card, 1)
        layout.addLayout(quality_row)
        card, rows = self._card()
        self.transfer_source = self._combo()
        self._row(rows, "默认发送范围", "在工作台工具栏中也可随时切换。", self.transfer_source)
        layout.addWidget(card)
        layout.addSpacing(3)
        layout.addWidget(label("回到 Krita", "SettingsSection"))
        card, rows = self._card()
        self.auto_import = UpdateSwitch()
        self._row(rows, "工作台结果自动加入图层", "工作台生成完成后，加入当前文档的新图层。", self.auto_import)
        layout.addWidget(card)
        layout.addWidget(label("图片以独立图层放入，方便继续绘制与调整。", "SettingsTip"))
        layout.addStretch()

    def _build_realtime(self):
        layout = self._page("realtime", "实时与选区", "让实时生成跟上绘画节奏，按习惯使用选区助手。")
        layout.addWidget(label("实时生成", "SettingsSection"))
        card, rows = self._card()
        self.subject = self._combo(168)
        self._row(rows, "画面来源", "使用整个画布，或仅使用当前文档中的图层。", self.subject)
        self.quality = self._combo(168)
        self._row(rows, "生成质量", "可选档位跟随实时面板中的当前模型。", self.quality, True)
        segment = QFrame()
        segment.setObjectName("SettingsSegment")
        segment_layout = QHBoxLayout(segment)
        segment_layout.setContentsMargins(3, 3, 3, 3)
        segment_layout.setSpacing(2)
        self.delay_group = QButtonGroup(self)
        self.delay_buttons = []
        for index, caption in enumerate(("1 秒", "2 秒", "4 秒")):
            button = QPushButton(caption)
            button.setObjectName("SettingsSegmentButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setAccessibleName("停笔 " + caption + "后更新")
            self.delay_group.addButton(button, index)
            self.delay_buttons.append(button)
            segment_layout.addWidget(button)
        self._row(rows, "停笔后更新", "连续生成开启时，等待停笔多久再更新预览。", segment, True)
        self.writeback = UpdateSwitch()
        self._row(rows, "实时结果自动放入画布", "关闭时先查看预览，满意后手动放入。", self.writeback, True)
        layout.addWidget(card)
        layout.addSpacing(3)
        layout.addWidget(label("选区助手", "SettingsSection"))
        card, rows = self._card()
        self.selection = UpdateSwitch()
        self._row(rows, "显示选区助手", "建立选区后，在画布旁显示提示词与生成工具。", self.selection)
        layout.addWidget(card)
        layout.addStretch()

    def _connect_controls(self):
        dock, panel = self.docker, self.docker._realtime_panel
        self.reference_group.idClicked.connect(dock._reference_mode.setCurrentIndex)
        dock._reference_mode.currentIndexChanged.connect(self.sync_controls)
        self.auto_import.toggled.connect(dock._auto.setChecked)
        dock._auto.toggled.connect(self.auto_import.setChecked)
        self.transfer_source.currentIndexChanged.connect(lambda index: dock._source.setCurrentIndex(index))
        dock._source.currentIndexChanged.connect(self.sync_controls)
        self.subject.currentIndexChanged.connect(lambda index: panel.subject.setCurrentIndex(index))
        self.quality.currentIndexChanged.connect(lambda index: panel.quality.setCurrentIndex(index))
        self.delay_group.idClicked.connect(panel.delay.setCurrentIndex)
        self.writeback.toggled.connect(panel.writeback.setChecked)
        panel.writeback.toggled.connect(self.writeback.setChecked)
        self.selection.toggled.connect(dock._selection.set_enabled)
        panel.options_changed.connect(self.sync_controls)
        for combo in (panel.subject, panel.quality, panel.delay):
            combo.currentIndexChanged.connect(self.sync_controls)

    @staticmethod
    def _sync_combo(target, source):
        previous = target.blockSignals(True)
        target.clear()
        for index in range(source.count()):
            target.addItem(source.itemText(index), source.itemData(index))
        target.setCurrentIndex(source.currentIndex())
        target.setEnabled(source.isEnabled())
        target.setToolTip(source.currentText())
        target.blockSignals(previous)

    def sync_controls(self, *unused):
        dock, panel = self.docker, self.docker._realtime_panel
        for target, source in ((self.transfer_source, dock._source), (self.subject, panel.subject), (self.quality, panel.quality)):
            self._sync_combo(target, source)
        if not panel.quality.isEnabled():
            self.quality.setItemText(self.quality.currentIndex(), "模型默认")
        self.reference_cards[max(0, dock._reference_mode.currentIndex())].setChecked(True)
        self.delay_buttons[max(0, panel.delay.currentIndex())].setChecked(True)
        for switch, value in ((self.auto_import, dock._auto.isChecked()), (self.writeback, panel.writeback.isChecked()),
                              (self.selection, dock._selection.enabled),
                              (self.updates.automatic, dock._settings.value("automaticUpdates", True, type=bool))):
            blocked = switch.blockSignals(True)
            switch.setChecked(value)
            switch.blockSignals(blocked)

    def set_page(self, key):
        if key not in self._page_widgets:
            key = "canvas"
        self.pages.setCurrentWidget(self._page_widgets[key])
        self.navigation[key].setChecked(True)
        self.reset.setVisible(key != "updates")
        self.updates.checked_at.setVisible(key == "updates")
        self.saved.setVisible(key != "updates")
        self.update_actions.setVisible(key == "updates")
        self.current_page = key
        self.page_changed.emit(key)

    def set_update_available(self, available):
        self.navigation["updates"].setText("更新与版本  ·" if available else "更新与版本")
        self.navigation["updates"].setAccessibleDescription("有可用更新" if available else "检查版本和更新记录")

    def reset_current_page(self):
        dock, panel = self.docker, self.docker._realtime_panel
        if self.current_page == "canvas":
            dock._reference_mode.setCurrentIndex(0)
            dock._source.setCurrentIndex(0)
            dock._auto.setChecked(True)
        elif self.current_page == "realtime":
            panel.subject.setCurrentIndex(0)
            panel._preferred_quality = "low"
            panel.quality.setCurrentIndex(0)
            dock._settings.setValue("realtimeQuality", "low")
            panel.delay.setCurrentIndex(1)
            panel.writeback.setChecked(False)
            dock._selection.set_enabled(True)
        self.sync_controls()

    def showEvent(self, event):
        self.sync_controls()
        super().showEvent(event)
