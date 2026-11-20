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
from .branding import MoriLogo


STYLE = UPDATE_STYLE + """
QWidget { font-family:'Microsoft YaHei UI'; font-size:12px; color:#e8e8e8; }
QDialog#MoriSettings { background:transparent; }
QFrame#SettingsWindow { background:#222222; border:1px solid #414141; border-radius:14px; }
QFrame#SettingsHeader { background:transparent; border:0; border-bottom:1px solid #353535; }
QLabel#SettingsBrand { font-family:'Segoe UI'; font-size:16px; font-weight:700; letter-spacing:2px; color:#f9f9f9; }
QLabel#SettingsWindowTitle { color:#b3b3b3; font-size:13px; }
QLabel#SettingsVersion { font-family:'Segoe UI'; font-size:11px; color:#898989; }
QFrame#SettingsSidebar { background:#1d1d1d; border:0; border-bottom-left-radius:13px; }
QLabel#SettingsEyebrow { color:#757575; font-size:10px; }
QPushButton#SettingsNav { background:transparent; border:1px solid transparent; border-radius:7px;
    color:#a7a7a7; text-align:left; padding:0 12px; min-height:41px; font-size:12px; }
QPushButton#SettingsNav:hover { background:#282828; color:#eaeaea; }
QPushButton#SettingsNav:checked { background:#2f2f2f; border-color:#3e3e3e; color:#d0d0d0; }
QPushButton#SettingsNav:focus { border-color:#7f7f7f; }
QLabel#SettingsSideNote { color:#6d6d6d; font-size:10px; }
QStackedWidget#SettingsPages, QWidget#SettingsPage, QScrollArea#SettingsScroll { background:transparent; border:0; }
QScrollArea#SettingsScroll > QWidget > QWidget { background:#222222; }
QLabel#SettingsPageTitle { font-size:22px; font-weight:600; color:#efefef; }
QLabel#SettingsPageSubtitle { color:#8c8c8c; font-size:12px; }
QLabel#SettingsSection { color:#8d8d8d; font-size:11px; }
QFrame#SettingsCard { background:#292929; border:1px solid #353535; border-radius:10px; }
QFrame#SettingsRow { background:transparent; border:0; }
QFrame#SettingsDivider { background:#393939; border:0; }
QLabel#SettingsRowTitle { color:#dedede; font-size:12px; font-weight:500; }
QLabel#SettingsRowHint { color:#8d8d8d; font-size:11px; }
QLabel#SettingsTip { color:#8a8a8a; font-size:11px; }
QComboBox { background:#323232; border:1px solid #424242; border-radius:6px; padding:7px 23px 7px 10px; color:#dadada; min-height:18px; }
QComboBox:hover { border-color:#696969; }
QComboBox:focus { border-color:#a9a9a9; }
QComboBox:disabled { color:#808080; background:#2a2a2a; border-color:#373737; }
QComboBox::drop-down { border:0; width:22px; }
QComboBox QAbstractItemView { background:#272727; color:#e7e7e7; selection-background-color:#434343; border:1px solid #4b4b4b; padding:4px; }
QPushButton#SettingsChoice { background:#292929; border:1px solid #383838; border-radius:9px; padding:15px; min-height:65px; text-align:left; }
QPushButton#SettingsChoice:hover { background:#2e2e2e; border-color:#6c6c6c; }
QPushButton#SettingsChoice:checked { background:#303030; border:1px solid #8a8a8a; }
QLabel#SettingsChoiceTitle { color:#e4e4e4; font-size:13px; font-weight:500; }
QLabel#SettingsChoiceHint { color:#8e8e8e; font-size:11px; }
QLabel#SettingsRecommended { color:#b3b3b3; font-size:10px; }
QFrame#SettingsSegment { background:#232323; border:1px solid #3a3a3a; border-radius:7px; }
QPushButton#SettingsSegmentButton { background:transparent; border:0; border-radius:5px; padding:0 9px; min-height:30px; color:#989898; font-size:11px; }
QPushButton#SettingsSegmentButton:checked { background:#434343; color:#e1e1e1; }
QPushButton#SettingsSegmentButton:focus { border:1px solid #9e9e9e; }
QFrame#SettingsFooter { background:transparent; border:0; border-top:1px solid #323232; }
QPushButton#SettingsReset { background:transparent; border:0; padding:0; min-height:32px; color:#979797; font-size:11px; }
QPushButton#SettingsReset:hover { color:#c8c8c8; }
QLabel#SettingsSaved { color:#878787; font-size:11px; }
QPushButton#SettingsDone { background:#6845ff; color:#ffffff; border:0; border-radius:7px; min-height:34px; padding:0 24px; font-size:12px; font-weight:600; }
QPushButton#SettingsDone:hover { background:#795bff; }
QPushButton#SettingsDone:focus { border:1px solid #b3a3ff; }
QPushButton#SettingsAction { background:#6845ff; color:#ffffff; border:0; border-radius:6px; min-height:32px; padding:0 14px; }
QPushButton#SettingsAction:hover { background:#795bff; }
QPushButton#SettingsAction:disabled { background:#3a3a3a; color:#858585; }
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
    recovery_requested = pyqtSignal(str)
    PAGES = (("canvas", "画布与图层", "layers"),
             ("realtime", "实时与选区", "sliders"),
             ("connection", "连接与恢复", "refresh"),
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
        row.addWidget(MoriLogo(20))
        row.addWidget(label("/   插件设置", "SettingsWindowTitle", False))
        row.addStretch()
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
            button.setIcon(ui_icon(icon, "#b4b4b4"))
            button.setIconSize(QSize(17, 17))
            button.clicked.connect(lambda checked, key=page: self.set_page(key))
            self.nav_group.addButton(button)
            self.navigation[page] = button
            side.addWidget(button)
        side.addStretch()
        side_note = label("Mori for Krita\nv" + VERSION, "SettingsVersion")
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
        self._build_connection()
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
        for index, (title, hint) in enumerate((("快速参考", "缩小并压缩上传副本，节省时间。\n细节可能减少，保留透明度。"),
                                                ("原图无损", "原始尺寸 · 无损 PNG。\n保留线稿、文字与纹理细节。"))):
            card = ChoiceCard(title, hint, index == 1)
            self.reference_group.addButton(card, index)
            self.reference_cards.append(card)
            quality_row.addWidget(card, 1)
        layout.addLayout(quality_row)
        layout.addWidget(label("用于工作台、实时与选区的参考图上传；生成分辨率在生成面板中单独选择。", "SettingsTip"))
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

    def _build_connection(self):
        layout = self._page("connection", "连接与恢复", "页面空白或连接异常时，可以在这里恢复。")
        layout.addWidget(label("工作台", "SettingsSection"))
        card, rows = self._card()
        self.recovery_buttons = {}
        actions = (("page", "刷新工作台页面", "重新载入当前页面，适用于空白、卡住或显示不完整。", "刷新页面"),
                   ("reconnect", "重新连接工作台", "重启工作台连接。刷新页面后仍未恢复时使用。", "重新连接"))
        for index, (key, title, hint, caption) in enumerate(actions):
            button = self._recovery_button(key, caption)
            self._row(rows, title, hint, button, bool(index))
        layout.addWidget(card)
        layout.addWidget(label("画面与账号", "SettingsSection"))
        card, rows = self._card()
        for index, (key, title, hint, caption) in enumerate((
                ("canvas", "刷新画面", "重新绘制 Krita 画布与插件面板，更新图层列表。", "刷新画面"),
                ("account", "刷新账号与积分", "重新获取当前账号的连接状态与积分余额。", "刷新账号"))):
            button = self._recovery_button(key, caption)
            self._row(rows, title, hint, button, bool(index))
        layout.addWidget(card)
        self.recovery_status = label("建议先刷新页面，仍未恢复时再重新连接工作台。", "SettingsTip")
        layout.addWidget(self.recovery_status)
        layout.addStretch()

    def _recovery_button(self, key, caption):
        button = QPushButton(caption)
        button.setObjectName("SettingsAction")
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumWidth(104)
        button.clicked.connect(lambda checked, action=key: self.recovery_requested.emit(action))
        self.recovery_buttons[key] = button
        return button

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
        self.reset.setVisible(key in ("canvas", "realtime"))
        self.updates.checked_at.setVisible(key == "updates")
        self.saved.setVisible(key in ("canvas", "realtime"))
        self.update_actions.setVisible(key == "updates")
        self.current_page = key
        self.page_changed.emit(key)

    def set_update_available(self, available):
        self.navigation["updates"].setText("更新与版本  ·" if available else "更新与版本")
        self.navigation["updates"].setAccessibleDescription("有可用更新" if available else "检查版本和更新记录")

    def reset_current_page(self):
        dock, panel = self.docker, self.docker._realtime_panel
        if self.current_page == "canvas":
            dock._reference_mode.setCurrentIndex(1)
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
