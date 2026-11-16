"""Mori's Krita entry point and managed workspace docking."""
from PyQt5.QtCore import QEvent, QObject, QTimer, Qt
from PyQt5.QtWidgets import QApplication, QDockWidget
from krita import Krita, DockWidgetFactory, DockWidgetFactoryBase
from .docker import MoriDocker
from .qt_lifetime import is_alive

Krita.instance().addDockWidgetFactory(
    DockWidgetFactory("mori_krita", DockWidgetFactoryBase.DockRight, MoriDocker)
)


class _StartupVisibility(QObject):
    """Show Mori after layout restore, without reopening a user-closed panel."""
    def __init__(self, application):
        super().__init__(QApplication.instance())
        self._application = application
        self._qt_application = QApplication.instance()
        self._windows = {}
        self._pending = False
        self._closed = False
        # AI Diffusion can register its dock after Krita restores the first
        # document and can also restore its floating state when the user
        # switches workspace pages.  Keep a lightweight guard running for
        # the lifetime of Krita so a late state change cannot leave a second
        # top-level window over every other plugin.
        self._dock_repair_timer = QTimer(self)
        self._dock_repair_timer.setInterval(300)
        self._dock_repair_timer.timeout.connect(self._normalise_all)
        self._dock_repair_timer.start()
        if self._qt_application is not None:
            self._qt_application.installEventFilter(self)
        notifier = application.notifier()
        notifier.setActive(True)
        notifier.windowCreated.connect(self._schedule)
        notifier.viewCreated.connect(self._schedule)
        view_closed = getattr(notifier, "viewClosed", None)
        if view_closed is not None:
            view_closed.connect(self._schedule)
        notifier.applicationClosing.connect(self._shutdown)
        self._schedule()

    def _schedule(self, *unused):
        if self._closed or self._pending:
            return
        self._pending = True
        # Window/view notifications can precede Krita's workspace restoration.
        # A single deferred pass also coalesces several views opened together.
        QTimer.singleShot(150, self._after_layout)

    def _after_layout(self):
        if self._closed or not is_alive(self):
            return
        self._pending = False
        for window in self._application.windows():
            try:
                main = window.qwindow()
                if not is_alive(main):
                    continue
                dock = next((item for item in window.dockers()
                             if is_alive(item) and item.objectName() == "mori_krita"), None)
                if dock is None:
                    if window.views():
                        # The Mori factory itself may be late while another
                        # plugin has already created its dock.
                        self._start_dock_repair()
                    continue
                key = id(main)
                state = self._windows.get(key)
                if state is None:
                    state = {"main": main, "dock": dock, "shown": False,
                             "first_view": False, "user_closed": False}
                    self._windows[key] = state
                    main.destroyed.connect(lambda _=None, key=key: self._windows.pop(key, None))
                    dock.installEventFilter(self)
                    dock.toggleViewAction().triggered.connect(
                        lambda checked, key=key: self._user_toggle(key, checked))
                has_view = bool(window.views())
                if not has_view:
                    # The welcome page should retain Krita's normal layout.
                    # hide() is not a user Close event or menu toggle.
                    dock.hide()
                    state["first_view"] = False
                    continue
                if state["user_closed"] or state["first_view"]:
                    self._start_dock_repair()
                    continue
                state["shown"] = True
                state["first_view"] = has_view
                dock.show()
                dock.toggleViewAction().setChecked(True)
                dock.raise_()
                prepare = getattr(dock, "prepare_document_layout", None)
                if prepare:
                    prepare()
                self._normalise_plugin_docks(main, window.dockers())
                self._start_dock_repair()
            except (RuntimeError, AttributeError):
                # Main windows can close while the deferred startup pass waits.
                continue

    def _normalise_plugin_docks(self, main, dockers):
        """Keep Mori and AI Diffusion inside Krita's normal dock stack.

        Krita remembers a dock's floating state in ``kritarc``.  Once AI
        Diffusion has been undocked, that state can make its page appear over
        every other plugin after the next restart.  Normalize only these two
        plugin docks and leave all other user layout choices untouched.
        """
        alive = [dock for dock in dockers if is_alive(dock)]
        mori = next((dock for dock in alive if dock.objectName() == "mori_krita"), None)
        diffusion = next((dock for dock in alive if dock.objectName() == "imageDiffusion"), None)
        if diffusion is None:
            return False

        # These signals cover the moments when the AI plugin itself restores
        # a layout after switching tabs or completing its startup.  The
        # periodic guard remains as a fallback for plugin versions that do
        # not emit a useful signal during their restore sequence.
        self._watch_dock(diffusion)
        if mori is not None:
            self._watch_dock(mori)

        # A floating dock is a separate top-level window, which is the source
        # of the "always over other plugin pages" behaviour.
        was_visible = diffusion.isVisible()
        was_floating = diffusion.isFloating()
        if was_floating:
            diffusion.setFloating(False)
        if diffusion.windowFlags() & Qt.WindowStaysOnTopHint:
            diffusion.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        diffusion.setAttribute(Qt.WA_AlwaysOnTop, False)

        # AI Diffusion stays a regular dock after the first repair.  The user
        # can still close it, but cannot accidentally create a second window.
        features = diffusion.features()
        if features & QDockWidget.DockWidgetFloatable:
            diffusion.setFeatures(features & ~QDockWidget.DockWidgetFloatable)

        area = main.dockWidgetArea(mori) if mori is not None else main.dockWidgetArea(diffusion)
        if area == Qt.NoDockWidgetArea:
            area = Qt.RightDockWidgetArea
        allowed = Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        if diffusion.allowedAreas() != allowed:
            diffusion.setAllowedAreas(allowed)

        if main.dockWidgetArea(diffusion) != area or was_floating or diffusion.isFloating():
            # Re-adding explicitly clears a stale floating parent/area that
            # Krita may restore from kritarc before the factory is complete.
            main.removeDockWidget(diffusion)
            main.addDockWidget(area, diffusion)
        if mori is not None and diffusion not in main.tabifiedDockWidgets(mori):
            main.tabifyDockWidget(mori, diffusion)
        if was_visible:
            diffusion.show()
        if mori is None:
            return False
        return diffusion in main.tabifiedDockWidgets(mori)

    def _watch_dock(self, dock):
        """Trigger an immediate repair when a managed dock changes state."""
        if not is_alive(dock):
            return
        try:
            if dock.property("_mori_dock_guard_connected"):
                return
            dock.setProperty("_mori_dock_guard_connected", True)
            for signal_name in ("topLevelChanged", "dockLocationChanged", "visibilityChanged"):
                signal = getattr(dock, signal_name, None)
                if signal is not None:
                    signal.connect(self._start_dock_repair)
        except (RuntimeError, AttributeError, TypeError):
            # A plugin can destroy/recreate its dock while emitting a signal.
            # The periodic pass will pick up the replacement.
            return

    def _start_dock_repair(self, *unused):
        if self._closed or not is_alive(self):
            return
        if not self._dock_repair_timer.isActive():
            self._dock_repair_timer.start()

    def _normalise_all(self):
        if self._closed or not is_alive(self):
            return
        for window in self._application.windows():
            try:
                main = window.qwindow()
                if is_alive(main):
                    self._normalise_plugin_docks(main, window.dockers())
            except (RuntimeError, AttributeError):
                continue

    def _user_toggle(self, key, checked):
        if not checked and key in self._windows:
            self._windows[key]["user_closed"] = True

    def eventFilter(self, watched, event):
        if event.type() == QEvent.ChildAdded:
            try:
                child = event.child()
                if child is not None and child.objectName() == "imageDiffusion":
                    self._start_dock_repair()
            except (RuntimeError, AttributeError):
                pass
        elif event.type() in (QEvent.Show, QEvent.WindowActivate, QEvent.WindowStateChange,
                              QEvent.ParentChange):
            try:
                if watched.objectName() == "imageDiffusion":
                    self._start_dock_repair()
            except (RuntimeError, AttributeError):
                pass
        if event.type() == QEvent.Close:
            for state in self._windows.values():
                if watched is state["dock"]:
                    state["user_closed"] = True
                    break
        return False

    def _shutdown(self):
        self._closed = True
        if is_alive(self._dock_repair_timer):
            self._dock_repair_timer.stop()
        if self._qt_application is not None:
            try:
                self._qt_application.removeEventFilter(self)
            except (RuntimeError, AttributeError):
                pass
        self._windows.clear()


_startup_visibility = _StartupVisibility(Krita.instance())
# The deferred pass also covers installations where the AI Diffusion factory
# is imported after the initial workspace restoration; the lifetime guard then
# keeps the two plugin docks together when the workspace changes later.
QTimer.singleShot(700, _startup_visibility._normalise_all)

