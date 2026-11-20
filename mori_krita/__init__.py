"""Mori startup visibility; user docking and other plugin layouts stay editable."""
from PyQt5.QtCore import QEvent, QObject, QTimer
from PyQt5.QtWidgets import QApplication
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
        # Each owned dock gets its Close filter in _after_layout. A global
        # filter would send every paint/input event through Python, including
        # the native WebView's events once the workbench has opened.
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
                    continue
                state["shown"] = True
                state["first_view"] = has_view
                dock.show()
                dock.toggleViewAction().setChecked(True)
                dock.raise_()
                prepare = getattr(dock, "prepare_document_layout", None)
                if prepare:
                    prepare()
            except (RuntimeError, AttributeError):
                # Main windows can close while the deferred startup pass waits.
                continue

    def _user_toggle(self, key, checked):
        if not checked and key in self._windows:
            self._windows[key]["user_closed"] = True

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Close:
            for state in self._windows.values():
                if watched is state["dock"]:
                    state["user_closed"] = True
                    break
        return False

    def _shutdown(self):
        self._closed = True
        for state in self._windows.values():
            if is_alive(state["dock"]):
                state["dock"].removeEventFilter(self)
        self._windows.clear()


_startup_visibility = _StartupVisibility(Krita.instance())
