"""Compact native composer attached to Krita's current selection.

This widget owns only local draft/settings state. The controller supplies model
configuration and real quotes, and performs all capture/network/document work.
It installs no application-wide shortcuts or input filters.
"""
from __future__ import annotations
from pathlib import Path

from PyQt5.QtCore import QEvent, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QFrame,
                            QHBoxLayout, QLabel, QMenu, QPlainTextEdit,
                            QSizePolicy, QStyle, QToolButton, QVBoxLayout, QWidget)

from .qt_lifetime import is_alive


DEFAULT_SETTINGS = {"model": "gpt-image-2.5", "aspect": "auto", "resolution": "1K",
                    "quality": "high", "count": 1, "mode": "edit"}
QUALITY_LABELS = {"low": "低", "medium": "中", "high": "高", "xhigh": "超高", "max": "极致"}
ASPECTS = ("auto", "1:1", "3:4", "4:3", "2:3", "3:2", "9:16", "16:9")


class _PromptEdit(QPlainTextEdit):
    submitRequested = pyqtSignal()
    collapseRequested = pyqtSignal()

    def event(self, event):
        if event.type() == QEvent.ShortcutOverride:
            if event.key() == Qt.Key_Escape or (event.key() in (Qt.Key_Return, Qt.Key_Enter)
                                               and event.modifiers() & Qt.ControlModifier):
                event.accept()
                return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() & Qt.ControlModifier:
            event.accept()
            self.submitRequested.emit()
            return
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.collapseRequested.emit()
            return
        super().keyPressEvent(event)


class _SummaryLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            event.accept()
            self.clicked.emit()
            return
        super().mousePressEvent(event)


class SelectionComposer(QFrame):
    """Screenshot-sized, collapsible Qt editor for one selected image region."""

    submitted = pyqtSignal(dict)
    collapsedChanged = pyqtSignal(bool)
    cancelRequested = pyqtSignal()
    settingsChanged = pyqtSignal(dict)
    draftChanged = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("moriSelectionComposer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Ignored clicks on card padding must stop here, rather than bubbling
        # into Krita's canvas and beginning a selection/paint interaction.
        self.setAttribute(Qt.WA_NoMousePropagation, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setMinimumWidth(240)
        self.setMaximumWidth(530)
        self._settings = dict(DEFAULT_SETTINGS)
        self._models = []
        self._configured = {}
        self._busy = False
        self._collapsed = False
        self._context = "当前选区"
        self._quote = ""
        self._status_text = ""
        self._error = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(3)
        self._card = QFrame(self)
        self._card.setObjectName("moriComposerCard")
        outer.addWidget(self._card)
        card = QVBoxLayout(self._card)
        card.setContentsMargins(10, 9, 10, 9)
        card.setSpacing(6)

        self._expanded = QWidget(self._card)
        expanded = QVBoxLayout(self._expanded)
        expanded.setContentsMargins(0, 0, 0, 0)
        expanded.setSpacing(6)
        editor_row = QHBoxLayout()
        editor_row.setContentsMargins(0, 0, 0, 0)
        editor_row.setSpacing(2)
        self.editor = _PromptEdit(self._expanded)
        self.editor.setObjectName("moriComposerPrompt")
        self.editor.setAccessibleName("选区修改要求")
        self.editor.setPlaceholderText("描述你想怎样修改这块选区…")
        self.editor.setToolTip("Ctrl+Enter 生成；Enter 换行；Esc 折叠并保留草稿")
        self.editor.setFixedHeight(61)
        self.editor.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.editor.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.editor.setTabChangesFocus(True)
        self.editor.submitRequested.connect(self._submit)
        self.editor.collapseRequested.connect(lambda: self.set_collapsed(True))
        self.editor.textChanged.connect(self._draft_changed)
        editor_row.addWidget(self.editor, 1)
        self._collapse_button = self._tool("折叠修改栏，保留草稿")
        self._collapse_button.setArrowType(Qt.UpArrow)
        self._collapse_button.setObjectName("moriComposerCollapse")
        self._collapse_button.setFixedSize(20, 20)
        self._collapse_button.clicked.connect(lambda: self.set_collapsed(True))
        editor_row.addWidget(self._collapse_button, 0, Qt.AlignTop)
        expanded.addLayout(editor_row)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.parameter_button = self._tool("图像参数与交互方式")
        self.parameter_button.setObjectName("moriComposerParameters")
        self.parameter_button.setPopupMode(QToolButton.InstantPopup)
        self.parameter_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._parameter_menu = QMenu(self.parameter_button)
        self._parameter_menu.aboutToShow.connect(self._populate_parameters)
        self.parameter_button.setMenu(self._parameter_menu)
        controls.addWidget(self.parameter_button)
        controls.addStretch(1)
        self.model_button = self._tool("选择图像模型", QStyle.SP_ComputerIcon)
        self.model_button.setObjectName("moriComposerModel")
        self.model_button.setPopupMode(QToolButton.InstantPopup)
        self.model_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.model_button.setIconSize(QSize(14, 14))
        cpu_icon = QIcon(str(Path(__file__).with_name("icons") / "cpu.svg"))
        if not cpu_icon.isNull():
            self.model_button.setIcon(cpu_icon)
        self._model_menu = QMenu(self.model_button)
        self._model_menu.aboutToShow.connect(self._populate_models)
        self.model_button.setMenu(self._model_menu)
        controls.addWidget(self.model_button)
        self.submit_button = self._tool("使用当前选区生成修改（Ctrl+Enter）")
        self.submit_button.setObjectName("moriComposerSubmit")
        self.submit_button.setText("渲染")
        self.submit_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.submit_button.setIcon(self._theme_icon("tools-wizard", QStyle.SP_MediaPlay))
        sparkles = QIcon(str(Path(__file__).with_name("icons") / "sparkles.svg"))
        if not sparkles.isNull():
            self.submit_button.setIcon(sparkles)
        self.submit_button.setIconSize(QSize(14, 14))
        self.submit_button.setMinimumWidth(61)
        self.submit_button.clicked.connect(self._submit)
        controls.addWidget(self.submit_button)
        self.cancel_button = self._tool("停止当前任务", QStyle.SP_MediaStop)
        self.cancel_button.setObjectName("moriComposerCancel")
        self.cancel_button.setFixedWidth(28)
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        controls.addWidget(self.cancel_button)
        expanded.addLayout(controls)
        card.addWidget(self._expanded)

        self._compact = QWidget(self._card)
        compact = QHBoxLayout(self._compact)
        compact.setContentsMargins(0, 0, 0, 0)
        compact.setSpacing(6)
        self._compact_label = _SummaryLabel(self._compact)
        self._compact_label.setObjectName("moriComposerSummary")
        self._compact_label.setCursor(Qt.PointingHandCursor)
        self._compact_label.setMinimumWidth(0)
        self._compact_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._compact_label.clicked.connect(lambda: self.set_collapsed(False))
        compact.addWidget(self._compact_label, 1)
        self._expand_button = self._tool("展开修改栏")
        self._expand_button.setArrowType(Qt.DownArrow)
        self._expand_button.setObjectName("moriComposerExpand")
        self._expand_button.setFixedSize(22, 22)
        self._expand_button.clicked.connect(lambda: self.set_collapsed(False))
        compact.addWidget(self._expand_button)
        card.addWidget(self._compact)
        self._compact.hide()

        self._status_label = QLabel(self._card)
        self._status_label.setObjectName("moriComposerStatus")
        self._status_label.setTextFormat(Qt.PlainText)
        self._status_label.setMinimumWidth(0)
        self._status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._status_label.setFixedHeight(16)
        self._status_label.hide()
        card.addWidget(self._status_label)
        self._quote_label = QLabel(self)
        self._quote_label.setObjectName("moriComposerQuote")
        self._quote_label.setAlignment(Qt.AlignRight)
        self._quote_label.setTextFormat(Qt.PlainText)
        self._quote_label.setFixedHeight(14)
        self._quote_label.hide()
        outer.addWidget(self._quote_label)
        self.setStyleSheet("""
            QFrame#moriSelectionComposer { background: transparent; border: none; }
            QFrame#moriComposerCard { background: #262626; border: 1px solid #454545; border-radius: 14px; }
            QWidget { color: #eeeeef; font-family: 'Microsoft YaHei UI', 'Segoe UI', sans-serif; }
            QPlainTextEdit#moriComposerPrompt { background: transparent; border: none; padding: 1px 3px; font-size: 13px; selection-background-color: #6047bb; }
            QToolButton { min-height: 26px; background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 0px 4px; font-size: 10px; }
            QToolButton:hover { background: #363636; }
            QToolButton:focus { border-color: #aa96ff; }
            QToolButton:disabled { color: #85858b; }
            QToolButton#moriComposerParameters { background: #303030; border-color: #484848; padding: 0 7px; }
            QToolButton#moriComposerSubmit { background: #6942ff; color: white; border: none; border-radius: 9px; font-size: 12px; padding: 0 7px; }
            QToolButton#moriComposerSubmit:hover { background: #7958ff; }
            QToolButton#moriComposerSubmit:pressed { background: #5936df; }
            QToolButton#moriComposerSubmit:disabled { background: #4d4277; color: #c2b9de; }
            QToolButton#moriComposerCollapse { min-height: 18px; padding: 0; }
            QToolButton#moriComposerExpand { min-height: 20px; padding: 0; }
            QLabel#moriComposerSummary { font-size: 12px; color: #dddddf; }
            QLabel#moriComposerStatus { color: #b3b3bd; font-size: 10px; }
            QLabel#moriComposerStatus[error='true'] { color: #f7a9a9; }
            QLabel#moriComposerQuote { color: #919199; font-size: 10px; padding-right: 7px; }
            QMenu { background: #292929; color: #eeeeef; border: 1px solid #505050; padding: 4px; }
            QMenu::item { padding: 5px 24px 5px 22px; }
            QMenu::item:selected { background: #6046a8; }
            QMenu::item:disabled { color: #818187; }
        """)
        self._refresh_labels()
        self.resize(self.sizeHint())

    def _theme_icon(self, name, fallback):
        icon = QIcon.fromTheme(name)
        if icon.isNull():
            try:
                from krita import Krita
                icon = Krita.instance().icon(name)
            except (ImportError, RuntimeError, AttributeError):
                pass
        return icon if not icon.isNull() else self.style().standardIcon(fallback)

    def _tool(self, label, fallback=None):
        button = QToolButton(self)
        button.setAutoRaise(False)
        button.setFocusPolicy(Qt.StrongFocus)
        button.setAccessibleName(label)
        button.setToolTip(label)
        if fallback is not None:
            button.setIcon(self.style().standardIcon(fallback))
        return button

    def settings(self):
        return dict(self._settings)

    def message(self):
        return self.editor.toPlainText()

    def payload(self):
        result = self.settings()
        result["message"] = self.message().strip()
        return result

    def set_message(self, text):
        self.editor.setPlainText(str(text))

    def is_collapsed(self):
        return self._collapsed

    def is_busy(self):
        return self._busy

    def reveal(self):
        """Show the complete card immediately, without animating a canvas mask."""
        if not is_alive(self) or not self.isHidden():
            return
        self.cancel_reveal()
        self.show()

    def cancel_reveal(self):
        """Clear any clipping before hiding or changing the card dimensions."""
        if is_alive(self):
            self.clearMask()

    def is_interacting(self):
        focused = QApplication.focusWidget()
        popup = QApplication.activePopupWidget()
        return bool((focused is not None and (focused is self or self.isAncestorOf(focused)))
                    or (popup is not None and self.isAncestorOf(popup)))

    def set_models(self, config):
        models = config.get("models", []) if isinstance(config, dict) else config
        self._models = [dict(model) for model in (models or []) if isinstance(model, dict) and model.get("id")]
        self._configured = dict(config.get("configured", {})) if isinstance(config, dict) else {}
        if self._models and self._settings["model"] not in {model["id"] for model in self._models}:
            usable = [model for model in self._models if self._configured.get(model.get("provider"), True)]
            self._settings["model"] = (usable or self._models)[0]["id"]
        self._refresh_labels()

    def set_settings(self, settings):
        if not isinstance(settings, dict):
            return
        for key in DEFAULT_SETTINGS:
            if key in settings:
                self._settings[key] = settings[key]
        if self._settings["mode"] not in ("edit", "chat"):
            self._settings["mode"] = "edit"
        try:
            self._settings["count"] = max(1, min(self._count_limit(), int(self._settings["count"])))
        except (TypeError, ValueError):
            self._settings["count"] = 1
        if self._settings["quality"] not in QUALITY_LABELS:
            self._settings["quality"] = "high"
        self._refresh_labels()

    def set_context(self, label):
        self._context = str(label or "当前选区")
        self.editor.setAccessibleDescription(self._context)
        self._refresh_labels()

    def set_quote(self, text):
        self._quote = str(text or "")
        self._quote_label.setText(self._quote)
        self._quote_label.setToolTip(self._quote)
        self._quote_label.setVisible(bool(self._quote) and not self._collapsed)
        self._sync_height()

    def set_status(self, text, error=False):
        self._status_text = str(text or "")
        self._error = bool(error)
        self._status_label.setProperty("error", self._error)
        self._status_label.style().unpolish(self._status_label)
        self._status_label.style().polish(self._status_label)
        self._status_label.setToolTip(self._status_text)
        self._status_label.setVisible(bool(self._status_text) and not self._collapsed)
        self._refresh_labels()
        self._sync_height()

    def set_timing_details(self, details):
        """Keep phase timings available without expanding the canvas overlay."""
        text = str(details or "")
        self._status_label.setToolTip(self._status_text + ("\n\n" + text if text else ""))

    def set_busy(self, busy, status=""):
        self._busy = bool(busy)
        self.submit_button.setEnabled(not self._busy)
        self.parameter_button.setEnabled(not self._busy)
        self.model_button.setEnabled(not self._busy)
        self.cancel_button.setVisible(self._busy)
        self.cancel_button.setEnabled(self._busy)
        self.submit_button.setText("处理中" if self._busy else
                                   "智能体改图" if self._settings.get("mode") == "chat" else "渲染")
        if status or not self._busy:
            self.set_status(status)
        self._refresh_labels()

    def set_collapsed(self, collapsed):
        collapsed = bool(collapsed)
        if collapsed == self._collapsed:
            return
        self.cancel_reveal()
        self._collapsed = collapsed
        self._expanded.setVisible(not collapsed)
        self._compact.setVisible(collapsed)
        self._quote_label.setVisible(bool(self._quote) and not collapsed)
        self._status_label.setVisible(bool(self._status_text) and not collapsed)
        if collapsed:
            self.editor.clearFocus()
        self._refresh_labels()
        self._sync_height()
        self.collapsedChanged.emit(collapsed)

    def expanded_height(self):
        return 128 + (22 if self._status_text else 0) + (17 if self._quote else 0)

    def desired_size(self, viewport_width):
        width = max(240, min(530, int(viewport_width) - 20))
        return QSize(width, 42 if self._collapsed else self.expanded_height())

    def sizeHint(self):
        return QSize(530, 42 if self._collapsed else self.expanded_height())

    def minimumSizeHint(self):
        return QSize(240, self.sizeHint().height())

    def _sync_height(self):
        # A hidden canvas child does not always receive layout-request events
        # before the controller repositions it. Freeze the visible card height
        # explicitly so collapsing cannot retain the expanded layout minimum.
        self.cancel_reveal()
        self._card.layout().invalidate()
        self.layout().invalidate()
        self.setFixedHeight(self.sizeHint().height())
        self.updateGeometry()

    def _count_limit(self):
        return 50 if self._settings.get("model") == "gemini-nano-banana-2.1" else 4

    def _refresh_labels(self):
        if not hasattr(self, "_compact_label"):
            return
        settings = self._settings
        aspect = "随选区" if settings["aspect"] == "auto" else str(settings["aspect"])
        quality = QUALITY_LABELS.get(settings["quality"], "高")
        full = "%s · %s · %s · %s张" % (settings["resolution"], aspect, quality, settings["count"])
        text = full if self.width() >= 430 else "%s · %s · %s张" % (settings["resolution"], quality, settings["count"])
        if self.width() < 340:
            text = str(settings["resolution"])
        self.parameter_button.setText(text)
        mode_label = "智能体" if settings["mode"] == "chat" else "直接修改"
        self.parameter_button.setToolTip(full + " · " + mode_label)
        if not self._busy:
            self.submit_button.setText("智能体改图" if settings["mode"] == "chat" else "渲染")
        self.submit_button.setToolTip(
            "由智能体理解修改要求并生成图片（Ctrl+Enter）" if settings["mode"] == "chat" else
            "直接生成当前选区的修改结果（Ctrl+Enter）")
        model = next((model for model in self._models if model["id"] == settings["model"]), None)
        name = str(model.get("name", settings["model"])) if model else str(settings["model"])
        if not model and name == "gpt-image-2.5":
            name = "GPT Image 2.5"
        self.model_button.setText(self.fontMetrics().elidedText(name, Qt.ElideRight, 140 if self.width() >= 430 else 88))
        self.model_button.setToolTip("图像模型：" + name)
        summary = self.message().strip().replace("\n", " ") or ("Mori · " + mode_label + " · " + self._context)
        if self._busy:
            summary = "正在处理 · " + summary
        self._compact_label.setText(self.fontMetrics().elidedText(summary, Qt.ElideRight, max(80, self.width() - 58)))
        self._compact_label.setToolTip(summary + "\n点击展开")
        if hasattr(self, "_status_label"):
            self._status_label.setText(self.fontMetrics().elidedText(self._status_text, Qt.ElideRight, max(80, self.width() - 24)))

    def _draft_changed(self):
        self._refresh_labels()
        self.draftChanged.emit(self.message())

    def _apply(self, key, value):
        self.set_settings({key: value})
        self.settingsChanged.emit(self.settings())

    def _choices(self, parent, title, key, choices):
        menu = parent.addMenu(title)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for value, label in choices:
            action = QAction(label, menu)
            action.setCheckable(True)
            action.setChecked(self._settings[key] == value)
            action.triggered.connect(lambda checked=False, k=key, v=value: self._apply(k, v))
            group.addAction(action)
            menu.addAction(action)
        return menu

    def _populate_parameters(self):
        menu = self._parameter_menu
        menu.clear()
        self._choices(menu, "交互方式", "mode", (("edit", "直接修改选区"), ("chat", "智能体理解并改图")))
        menu.addSeparator()
        self._choices(menu, "分辨率", "resolution", ((value, value) for value in ("1K", "2K", "4K")))
        self._choices(menu, "比例", "aspect", ((value, "跟随当前选区" if value == "auto" else value) for value in ASPECTS))
        model = next((model for model in self._models if model["id"] == self._settings["model"]), {})
        qualities = model.get("qualities") or QUALITY_LABELS
        self._choices(menu, "质量", "quality", ((value, QUALITY_LABELS[value]) for value in QUALITY_LABELS if value in qualities))
        counts = (1, 2, 4, 8, 16, 50) if self._count_limit() == 50 else (1, 2, 4)
        self._choices(menu, "数量", "count", ((value, "%s 张" % value) for value in counts))
        menu.addSeparator()
        hint = menu.addAction("Ctrl+Enter 生成 · Esc 折叠")
        hint.setEnabled(False)

    def _populate_models(self):
        menu = self._model_menu
        menu.clear()
        group = QActionGroup(menu)
        group.setExclusive(True)
        for model in self._models:
            action = QAction(str(model.get("name", model["id"])), menu)
            action.setCheckable(True)
            action.setChecked(model["id"] == self._settings["model"])
            action.setEnabled(self._configured.get(model.get("provider"), True))
            action.triggered.connect(lambda checked=False, value=model["id"]: self._apply("model", value))
            group.addAction(action)
            menu.addAction(action)
        if not self._models:
            action = menu.addAction("登录后读取可用模型")
            action.setEnabled(False)

    def _submit(self):
        if self._busy:
            return
        message = self.message().strip()
        if not message:
            self.set_status("先写下想怎样修改这块选区。", error=True)
            return
        if len(message) > 32000:
            self.set_status("修改要求请控制在 32000 字以内。", error=True)
            return
        payload = self.settings()
        payload["message"] = message
        # This native card is an explicit image-edit action. Plain agent chat
        # remains available through the workspace and non-render task callers.
        payload["expect_image"] = True
        self.submitted.emit(payload)

    def _cancel(self):
        if self._busy and self.cancel_button.isEnabled():
            self.cancel_button.setEnabled(False)
            self.cancelRequested.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_labels()

    def hideEvent(self, event):
        self.cancel_reveal()
        super().hideEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.set_collapsed(True)
            event.accept()
            return
        super().keyPressEvent(event)
