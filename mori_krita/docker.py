"""Krita dock: embedded Mori UI and a small native image bridge toolbar."""
import concurrent.futures
from pathlib import Path
import sys
import threading

from PyQt5.QtCore import (QObject, PYQT_VERSION_STR, QT_VERSION_STR, QSettings, QSize,
                          QStandardPaths, QThread, QTimer, Qt, pyqtSignal)
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QInputDialog,
                            QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
                            QStackedWidget, QToolButton, QMenu, QAction, QActionGroup,
                            QSizePolicy, QTabBar)
from krita import DockWidget, Krita

from .api import MoriClient
from .gateway import MoriGateway
from .krita_bridge import KritaBridge, prepare_reference
from .webview import WebCanvas
from .qt_lifetime import is_alive
from .session_store import WindowsSessionStore
from .version import VERSION
from .web_updates import WebUpdateManager
from .realtime_panel import (RealtimePanel, MORI_STYLE, set_dark_surface, icon_button,
                             ui_icon, _CompactComboBox)
from .realtime_controller import RealtimeController


class GuiDispatch(QObject):
    requested = pyqtSignal(object)
    message = pyqtSignal(str)

    def __init__(self, handler, parent=None):
        super().__init__(parent)
        self.handler = handler
        self._closed = False
        self._pending = {}
        self._pending_lock = threading.RLock()
        self.requested.connect(self._execute)
        self.destroyed.connect(lambda: self.shutdown())

    def _execute(self, task):
        try:
            if task.get("abandoned"):
                return
            if self._closed:
                raise RuntimeError("Krita 插件正在关闭")
            task["result"] = self.handler(task["operation"], task["data"])
        except Exception as error:
            task["error"] = error
        finally:
            task["event"].set()

    def invoke(self, operation, data):
        if self._closed or not is_alive(self):
            raise RuntimeError("Krita 插件正在关闭")
        if QThread.currentThread() == self.thread():
            return self.handler(operation, data)
        task = {"operation": operation, "data": data, "event": threading.Event()}
        with self._pending_lock:
            if self._closed:
                raise RuntimeError("Krita 插件正在关闭")
            self._pending[id(task)] = task
        try:
            self.requested.emit(task)
            if not task["event"].wait(25):
                task["abandoned"] = True
                raise RuntimeError("Krita 正在忙，请关闭模态对话框后重试。")
        finally:
            with self._pending_lock:
                self._pending.pop(id(task), None)
        if "error" in task:
            raise task["error"]
        return task.get("result")

    def shutdown(self):
        """Release workers even if Qt has already removed queued GUI calls."""
        with self._pending_lock:
            self._closed = True
            for task in self._pending.values():
                task["abandoned"] = True
                task["error"] = RuntimeError("Krita 插件正在关闭")
                task["event"].set()


class MoriDocker(DockWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("mori_krita")
        self.setProperty("ShowOnWelcomePage", False)
        self.setWindowTitle("Mori 画布 · " + VERSION)
        self.setMinimumWidth(340)
        set_dark_surface(self)
        self._gateway = None
        self._closing = False
        self._started = False
        self._web_started = False
        self._capturing = False
        self._web_update_cancel = None
        self._web_update_pending = False
        self._web_update_message = ""
        self._web_ui_version = "随插件安装"
        self._settings = QSettings("Mori", "KritaPlugin")
        self._bridge = KritaBridge()
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="Mori toolbar")
        self._dispatch = GuiDispatch(self._ui_operation, self)
        self._dispatch.message.connect(self._status)

        main = QWidget(self)
        main.setObjectName("MoriSurface")
        set_dark_surface(main)
        self.setWidget(main)
        self.setStyleSheet("QDockWidget#mori_krita { background:#222225; border:0; }\n" + MORI_STYLE)

        # A native titlebar matches the panel and removes the old white strip.
        titlebar = QWidget(self)
        self._titlebar = titlebar
        titlebar.setObjectName("MoriSurface")
        set_dark_surface(titlebar)
        titlebar.setFixedHeight(38)
        title_layout = QHBoxLayout(titlebar)
        self._title_layout = title_layout
        title_layout.setContentsMargins(10, 3, 8, 3)
        title_layout.setSpacing(7)
        self._brand = QLabel("<span style='font-size:13px;font-weight:500;'>MORI</span>")
        self._brand.setToolTip("Mori for Krita " + VERSION)
        title_layout.addWidget(self._brand)
        separator = QLabel("│")
        self._title_separator = separator
        separator.setProperty("role", "muted")
        title_layout.addWidget(separator)
        self._mode_button = QToolButton(titlebar)
        self._mode_button.setProperty("role", "mode")
        self._mode_button.setText("实时模式")
        self._mode_button.setToolTip("切换实时模式 / 工作台模式")
        self._mode_button.setAccessibleName("切换 Mori 模式")
        self._mode_button.setPopupMode(QToolButton.InstantPopup)
        mode_menu = QMenu(self._mode_button)
        self._mode_button.setMenu(mode_menu)
        self._mode_group = QActionGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_actions = []
        for index, label in enumerate(("实时模式", "工作台模式")):
            action = mode_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(index == 0)
            self._mode_group.addAction(action)
            action.triggered.connect(lambda checked, selected=index: self._set_mode(selected))
            self._mode_actions.append(action)
        mode_menu.addSeparator()
        menu = mode_menu.addMenu("设置")
        menu.addAction("实时参数…", lambda: self._realtime_panel.show_options(self._mode_button))
        menu.addAction("刷新图层与连接", lambda: self._realtime_panel.refresh.click())
        menu.addAction("登录 Mori…", lambda: self._realtime_panel.login.click())
        menu.addSeparator()
        title_layout.addWidget(self._mode_button)
        self._beta_label = QLabel("BETA")
        self._beta_label.setObjectName("MoriBeta")
        self._beta_label.setToolTip("实时模式 · BETA 测试")
        title_layout.addWidget(self._beta_label)
        title_layout.addStretch()
        undock = icon_button("float", "浮动 / 停靠面板", titlebar)
        self._undock_button = undock
        undock.setProperty("role", "window")
        undock.setFixedSize(24, 24)
        undock.clicked.connect(lambda: self.setFloating(not self.isFloating()))
        close = icon_button("close", "关闭 Mori 面板", titlebar)
        close.setProperty("role", "window")
        close.setFixedSize(24, 24)
        close.clicked.connect(self.close)
        title_layout.addWidget(undock)
        title_layout.addWidget(close)
        self.setTitleBarWidget(titlebar)

        layout = QVBoxLayout(main)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self._auto = QCheckBox("工作台生成后加入图层", main)
        self._auto.hide()
        self._auto.setChecked(self._settings.value("autoImport", True, type=bool))
        self._auto.toggled.connect(self._auto_changed)
        for checkbox in (self._auto,):
            action = menu.addAction(checkbox.text())
            action.setCheckable(True)
            action.setChecked(checkbox.isChecked())
            action.toggled.connect(checkbox.setChecked)
            checkbox.toggled.connect(action.setChecked)
        menu.addSeparator()
        menu.addAction("服务器地址…", self._change_server)
        self._update_button = QAction("检查工作台更新", menu)
        self._update_button.setToolTip("检查 Krita 工作台网页更新；插件原生功能通过安装包升级")
        self._update_button.triggered.connect(self._check_web_updates)
        menu.addAction(self._update_button)
        menu.addAction("重新连接", self._reconnect)

        self._pages = QStackedWidget(main)
        set_dark_surface(self._pages)
        self._realtime_panel = RealtimePanel(self._pages)
        self._pages.addWidget(self._realtime_panel)
        workbench = QWidget(self._pages)
        workbench.setObjectName("MoriSurface")
        set_dark_surface(workbench)
        workbench_layout = QVBoxLayout(workbench)
        self._workbench_layout = workbench_layout
        self._toolbar_inline = False
        workbench_layout.setContentsMargins(0, 0, 0, 0)
        workbench_layout.setSpacing(0)
        self._workbench_toolbar = QWidget()
        self._workbench_toolbar.setObjectName("MoriWorkbenchToolbar")
        self._workbench_toolbar.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._workbench_toolbar.setMinimumWidth(0)
        actions = QHBoxLayout(self._workbench_toolbar)
        actions.setContentsMargins(12, 8, 12, 8)
        actions.setSpacing(8)
        self._source = _CompactComboBox()
        self._source.setProperty("role", "workbench-source")
        self._source.setFixedWidth(90)
        self._source.setFixedHeight(32)
        self._source.setAccessibleName("发送范围")
        self._source.addItem("自动识别", "auto")
        self._source.addItem("Krita 选区", "selection")
        self._source.addItem("当前图层", "layer")
        self._source.addItem("整个画布", "canvas")
        self._source.setToolTip("自动识别：优先选区，其次当前有内容的图层，最后整个画布。导入图片会直接显示在 Mori 画布。")
        actions.addWidget(self._source)
        actions.addStretch()
        self._reference_mode = QComboBox(workbench)
        self._reference_mode.addItem("快速参考", "fast")
        self._reference_mode.addItem("原图无损", "original")
        self._reference_mode.setToolTip("快速参考缩小上传副本，最长边 2048、最多 400 万像素；透明度保留。Krita 原图和结果图层尺寸不变。")
        saved_mode = self._settings.value("referenceMode", "fast", type=str)
        self._reference_mode.setCurrentIndex(1 if saved_mode == "original" else 0)
        self._reference_mode.currentIndexChanged.connect(
            lambda: self._settings.setValue("referenceMode", self._reference_mode.currentData()))
        self._reference_mode.hide()
        self._capture_button = QPushButton("发送")
        self._capture_button.setProperty("role", "transfer")
        self._capture_button.setFixedHeight(32)
        self._capture_button.setIcon(ui_icon("send", "#ffffff"))
        self._capture_button.setIconSize(QSize(13, 13))
        self._capture_button.setAccessibleName("发送到 Mori 工作台")
        self._capture_button.setToolTip("将左侧选择的 Krita 区域发送到 Mori 工作台")
        self._capture_button.clicked.connect(self._capture)
        actions.addWidget(self._capture_button)
        self._import_button = QPushButton("放入画布")
        self._import_button.setProperty("role", "transfer-secondary")
        self._import_button.setFixedHeight(32)
        self._import_button.setIcon(ui_icon("apply", "#e5e2ef"))
        self._import_button.setIconSize(QSize(13, 13))
        self._import_button.setAccessibleName("将 Mori 图片放入 Krita 画布")
        self._import_button.setToolTip("将 Mori 画布选中的图片放入当前 Krita 文档的新图层")
        self._import_button.clicked.connect(self._import_selected)
        actions.addWidget(self._import_button)
        self._workbench_options = icon_button("more", "更多选项", workbench)
        self._workbench_options.setObjectName("MoriTransferOptions")
        self._workbench_options.setPopupMode(QToolButton.InstantPopup)
        quality_menu = QMenu(self._workbench_options)
        quality_group = QActionGroup(self)
        quality_group.setExclusive(True)
        self._reference_actions = []
        for index in range(self._reference_mode.count()):
            action = quality_menu.addAction(self._reference_mode.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == self._reference_mode.currentIndex())
            quality_group.addAction(action)
            action.triggered.connect(lambda checked, selected=index: self._reference_mode.setCurrentIndex(selected))
            self._reference_actions.append(action)
        self._reference_mode.currentIndexChanged.connect(
            lambda index: self._reference_actions[index].setChecked(True))
        quality_menu.addSeparator()
        quality_menu.addAction("浮动 / 停靠面板", lambda: self.setFloating(not self.isFloating()))
        self._workbench_options.setMenu(quality_menu)
        actions.addWidget(self._workbench_options)
        workbench_layout.addWidget(self._workbench_toolbar)

        self._web = WebCanvas(workbench)
        self._web.error.connect(self._status)
        self._web.ready.connect(self._web_ready)
        workbench_layout.addWidget(self._web, 1)
        self._pages.addWidget(workbench)
        layout.addWidget(self._pages, 1)
        self._status_label = QLabel("实时预览已暂停")
        self._status_label.setProperty("role", "muted")
        self._status_label.setContentsMargins(12, 6, 12, 8)
        self._status_label.setWordWrap(True)
        self._status_label.setTextFormat(Qt.PlainText)
        self._status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._status_label)
        self._status_label.hide()
        # The automatic selection overlay has been removed. In particular, do
        # not construct its timers or install Python input filters on the canvas.
        # Explicit selection capture remains available from the workbench.
        self._realtime = RealtimeController(self, self._realtime_panel)
        self._surface_timer = QTimer(self)
        self._surface_timer.setSingleShot(True)
        self._surface_timer.timeout.connect(self._settle_surface)
        # A QDockWidget can stay visible while another tabified dock becomes
        # the current tab.  Qt then does not reliably send hideEvent to this
        # dock, while the WebView2 child HWND remains visible because it lives
        # in another process.  Reconcile the effective tab visibility at a
        # low frequency so the native child cannot cover a sibling docker.
        self._visibility_guard = QTimer(self)
        self._visibility_guard.setInterval(150)
        self._visibility_guard.timeout.connect(self._sync_embedded_visibility)
        self._visibility_guard.start()
        self.topLevelChanged.connect(self._schedule_surface)
        self.dockLocationChanged.connect(self._schedule_surface)
        self.topLevelChanged.connect(self._sync_embedded_visibility)
        self.dockLocationChanged.connect(self._sync_embedded_visibility)
        self.visibilityChanged.connect(self._sync_embedded_visibility)
        self.visibilityChanged.connect(self._schedule_surface)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.destroyed.connect(lambda: self.shutdown())

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_surface()
        if hasattr(self, "_web"):
            self._sync_embedded_visibility()
        if not self._started:
            self._started = True
            QTimer.singleShot(0, self._start)
        elif hasattr(self, "_realtime"):
            QTimer.singleShot(0, lambda: self._realtime.set_active(self._pages.currentIndex() == 0))

    def hideEvent(self, event):
        if hasattr(self, "_realtime"):
            self._realtime.set_active(False)
        if hasattr(self, "_web"):
            self._web.set_active(False)
        super().hideEvent(event)

    def _is_current_dock_tab(self):
        """Return whether this dock is the selected tab in a tabified stack."""
        if self.isFloating():
            return True
        try:
            main = self.window()
            title = self.windowTitle()
            for tabbar in main.findChildren(QTabBar):
                for index in range(tabbar.count()):
                    text = tabbar.tabText(index).replace("&", "").strip()
                    if text == title or text.startswith("Mori 画布"):
                        return tabbar.currentIndex() == index
        except (RuntimeError, AttributeError):
            pass
        # A non-tabified dock has no QTabBar and is therefore current.
        return True

    def _sync_embedded_visibility(self, *unused):
        """Mirror the selected dock tab into the external WebView2 host."""
        if self._closing or not hasattr(self, "_web"):
            return
        active = (self.isVisible()
                  and self._pages.currentIndex() == 1
                  and self._is_current_dock_tab())
        self._web.set_active(active)

    def _set_mode(self, index):
        self._pages.setCurrentIndex(index)
        self._sync_embedded_visibility()
        self._mode_actions[index].setChecked(True)
        self._mode_button.setText("实时模式" if index == 0 else "工作台")
        self._beta_label.setVisible(index == 0)
        self._title_separator.setVisible(index == 0)
        self._undock_button.setVisible(index == 0)
        self._arrange_workbench_toolbar()
        self._status_label.setVisible(index == 1)
        self._schedule_surface()
        self._realtime.set_active(index == 0 and self.isVisible())
        if index == 1:
            self._start()
            self._status("Mori 工作台")
        else:
            self._status("实时预览已暂停")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_workbench_options"):
            self._arrange_workbench_toolbar()

    def _arrange_workbench_toolbar(self):
        """Share the native title row when wide; keep usable targets in narrow docks."""
        if self._closing:
            return
        workbench = self._pages.currentIndex() == 1
        inline = workbench and self.width() >= 620
        toolbar = self._workbench_toolbar
        if inline != self._toolbar_inline:
            self._toolbar_inline = inline
            if inline:
                self._workbench_layout.removeWidget(toolbar)
                self._title_layout.insertWidget(self._title_layout.count() - 3, toolbar, 1)
                toolbar.layout().setContentsMargins(12, 0, 4, 0)
            else:
                self._title_layout.removeWidget(toolbar)
                self._workbench_layout.insertWidget(0, toolbar)
                toolbar.layout().setContentsMargins(12, 8, 12, 8)
        self._title_layout.setStretch(self._title_layout.count() - 3, 0 if inline else 1)
        # QDockWidget reserves its title height from sizeHint(), not just the
        # widget's fixedHeight. Include the breathing room in the real layout
        # hint so a tabbed native dock cannot paint the webview over the buttons.
        self._title_layout.setContentsMargins(10, 12 if inline else 3, 8, 12 if inline else 3)
        self._titlebar.setFixedHeight(56 if inline else 38)
        toolbar.setVisible(workbench)

    def _schedule_surface(self, *unused):
        timer = getattr(self, "_surface_timer", None)
        if not self._closing and is_alive(timer):
            timer.start(0)

    def _settle_surface(self):
        if self._closing or not is_alive(self) or not self.isVisible():
            return
        # Let Qt finish its docking/stack layout before painting newly exposed
        # pixels. No synthetic move/resize or interference with the canvas.
        for surface in (self, self.widget(), self._pages, self._pages.currentWidget()):
            if surface.layout():
                surface.layout().activate()
            surface.update()

    def prepare_document_layout(self):
        if self._closing or self._settings.value("nativePanelLayoutVersion", 0, type=int) >= 1:
            return
        main = self.window()
        if not self.isFloating() and hasattr(main, "resizeDocks"):
            main.resizeDocks([self], [400], Qt.Horizontal)
        self._settings.setValue("nativePanelLayoutVersion", 1)

    def canvasChanged(self, canvas):
        if hasattr(self, "_realtime"):
            self._realtime.refresh()

    def _status(self, message):
        if not self._closing and is_alive(self._status_label):
            self._status_label.setText(str(message))
            if hasattr(self, "_pages") and self._pages.currentIndex() == 0 and hasattr(self, "_realtime_panel"):
                self._realtime_panel.set_status(message)

    def _ensure_gateway(self):
        if self._closing or not is_alive(self):
            raise RuntimeError("插件正在关闭")
        if self._gateway is None:
            base = self._settings.value("serverUrl", "https://morinet.chat", type=str)
            client = MoriClient(base, session_store=WindowsSessionStore())
            storage = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "mori-krita"
            gateway = MoriGateway(client, Path(__file__).with_name("web"), storage,
                                  lambda op, data: self._dispatch.invoke(op, {**data, "gateway_id": id(gateway)}),
                                  self._dispatch.message.emit)
            gateway.auto_import = self._auto.isChecked()
            self._gateway = gateway
            gateway.start()
        return self._gateway

    def _start(self):
        if self._closing:
            return
        try:
            gateway = self._ensure_gateway()
            if self._pages.currentIndex() == 0:
                self._realtime.set_active(self.isVisible())
            elif not self._web_started and not self._web_update_pending:
                self._begin_web_update(gateway, open_after=True)
        except Exception as error:
            self._status("连接未完成：" + str(error))

    def _web_ready(self):
        self._status(self._web_update_message or "画布已打开。账号、积分和生成使用 Mori 服务器。")

    def _check_web_updates(self):
        if self._closing or self._web_update_pending:
            return
        try:
            gateway = self._ensure_gateway()
            self._begin_web_update(gateway, open_after=not self._web_started)
        except Exception as error:
            self._status("网页更新检查未完成：" + str(error))

    def _begin_web_update(self, gateway, open_after=False):
        # A native selection submission can create the gateway without opening
        # the workbench. Only this explicit workbench path checks for updates.
        if self._closing or self._web_update_pending or gateway is not self._gateway:
            return
        cancel = threading.Event()
        self._web_update_cancel = cancel
        self._web_update_pending = True
        self._update_button.setEnabled(False)
        self._status("正在检查工作台网页更新…")
        base = gateway.client.base_url
        bundled = Path(__file__).with_name("web")
        storage = Path(QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)) / "mori-krita" / "web-updates"
        result = {}

        def check():
            try:
                # Workbench-first launches need the same read-only connection
                # probe as live mode before the embedded page sends any POST.
                gateway.client.session()
                gateway._sync_identity()
            except Exception:
                pass
            try:
                manager = WebUpdateManager(base, bundled, storage, VERSION)
                result["value"] = manager.check(cancel)
            except Exception:
                # A damaged update cache must never prevent the bundled UI
                # from opening, and no user data is removed during recovery.
                result["value"] = None

        def done():
            if (self._closing or cancel.is_set() or gateway is not self._gateway
                    or self._web_update_cancel is not cancel):
                return
            self._web_update_pending = False
            self._update_button.setEnabled(True)
            value = result.get("value")
            message = "暂时无法检查网页更新，已使用随插件安装的版本。"
            if value is not None:
                message = value.message
                if open_after and not self._web_started:
                    # No page has loaded from this gateway yet, so static
                    # resources can be selected once without mixing releases.
                    gateway.web_root = value.release.root
                    self._web_ui_version = value.release.ui_version
                elif value.updated or value.release.root != gateway.web_root:
                    message = "新版网页已准备好，点击「重连」后生效 · " + value.release.ui_version
            self._web_update_message = message
            self._brand.setToolTip("Mori 插件 " + VERSION + "\n工作台网页 " + self._web_ui_version)
            self._status(message)
            if open_after and not self._web_started:
                self._web_started = True
                self._web.start(gateway.url)

        self._background(check, done)

    def _auto_changed(self, checked):
        self._settings.setValue("autoImport", checked)
        if self._gateway:
            self._gateway.auto_import = checked
            if checked:
                self._gateway._schedule_results()

    def _change_server(self):
        current = self._settings.value("serverUrl", "https://morinet.chat", type=str)
        value, accepted = QInputDialog.getText(self, "Mori 服务器", "账号、积分和生成服务地址", text=current)
        if not accepted or value.strip() == current:
            return
        try:
            normalized = MoriClient(value.strip()).base_url
        except Exception as error:
            QMessageBox.warning(self, "服务器地址无效", str(error))
            return
        self._settings.setValue("serverUrl", normalized)
        self._reconnect()

    def _reconnect(self):
        self._realtime.connection_changed()
        if self._web_update_cancel is not None:
            self._web_update_cancel.set()
        self._web_update_pending = False
        self._web_update_message = ""
        self._update_button.setEnabled(True)
        self._web.stop()
        self._web_started = False
        old = self._gateway
        self._gateway = None
        if old:
            self._pool.submit(old.close)
        QTimer.singleShot(100, self._start)

    def _target(self):
        # Without an active selection, normal generation targets the whole canvas.
        source = self._source.currentData()
        if source == "selection":
            try:
                return self._bridge.active_target("selection")
            except Exception:
                return self._bridge.active_target("canvas")
        return self._bridge.active_target(source)

    @staticmethod
    def _import_failure(error):
        # Use the loaded versions, rather than files that may have been updated
        # while this Krita process was open. Never collect accounts or documents.
        try:
            runtime = Krita.instance().version()
        except Exception:
            runtime = "未知"
        python = ".".join(str(value) for value in sys.version_info[:3])
        return RuntimeError("%s\n[Mori %s; Krita %s; Python %s; Qt %s; PyQt %s]" %
                            (error, VERSION, runtime, python, QT_VERSION_STR, PYQT_VERSION_STR))

    def _ui_operation(self, operation, data):
        if self._closing or not is_alive(self):
            raise RuntimeError("插件正在关闭")
        if operation == "callback":
            data["callback"]()
            return None
        if data.get("gateway_id") != id(self._gateway):
            raise RuntimeError("插件连接已改变，已停止旧连接操作")
        if operation == "target":
            return self._target()
        if operation == "capture":
            return self._bridge.capture(data.get("source", "selection"))
        if operation == "snapshot":
            return self._bridge.snapshot(data.get("source", "selection"))
        if operation == "import":
            try:
                gateway = self._gateway
                if "epoch" in data and (gateway is None or data["epoch"] != gateway.epoch):
                    raise RuntimeError("账号会话已改变，已停止自动导入")
                self._bridge.import_png(data["bytes"], data["name"], data.get("target"),
                                        force_new_document=data.get("force_new", False))
                return self._bridge.last_import_target
            except Exception as error:
                raise self._import_failure(error) from error
        raise RuntimeError("不支持此 Krita 操作")

    def _background(self, function, done=None):
        future = self._pool.submit(function)

        def finished(result):
            try:
                result.result()
            except Exception as error:
                if not self._closing and is_alive(self._dispatch):
                    try:
                        self._dispatch.message.emit(str(error))
                    except RuntimeError:
                        if not self._closing and is_alive(self._dispatch):
                            raise
            finally:
                if done and not self._closing and is_alive(self._dispatch):
                    try:
                        self._dispatch.requested.emit({"operation": "callback", "data": {"callback": done},
                                                       "event": threading.Event()})
                    except RuntimeError:
                        if not self._closing and is_alive(self._dispatch):
                            raise
        future.add_done_callback(finished)

    def _capture(self):
        if self._capturing:
            return
        gateway = self._ensure_gateway()
        self._capturing = True
        self._capture_button.setEnabled(False)
        source = self._source.currentData()
        mode = self._reference_mode.currentData()
        self._status("正在读取 Krita 参考区域…")

        def done():
            self._capturing = False
            self._capture_button.setEnabled(True)
        self._background(lambda: gateway.capture(source, mode=mode, prepare=prepare_reference, display=True), done)

    def _import_selected(self):
        if self._gateway:
            gateway = self._gateway
            self._status("正在将所选图片写入 Krita 图层…")
            self._background(gateway.import_selected)

    def shutdown(self):
        if self._closing:
            return
        self._closing = True
        if is_alive(getattr(self, "_visibility_guard", None)):
            self._visibility_guard.stop()
        if self._web_update_cancel is not None:
            self._web_update_cancel.set()
        self._dispatch.shutdown()
        try:
            self._realtime.shutdown()
        finally:
            try:
                self._web.shutdown()
            finally:
                if self._gateway:
                    gateway, self._gateway = self._gateway, None
                    threading.Thread(target=gateway.close, daemon=True).start()
                self._pool.shutdown(wait=False, cancel_futures=True)
