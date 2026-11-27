"""Startup visibility uses real Qt docks and an offline fake Krita notifier."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock

try:
    from PyQt5.QtCore import QEvent, Qt, QTimer
    from PyQt5.QtWidgets import QApplication, QDockWidget, QMainWindow, QWidget
    from PyQt5 import sip
    import krita
except ImportError:
    QWidget = None


class Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class Notifier:
    def __init__(self):
        self.windowCreated = Signal()
        self.viewCreated = Signal()
        self.viewClosed = Signal()
        self.applicationClosing = Signal()
        self.enabled = False

    def setActive(self, enabled):
        self.enabled = enabled


class Application:
    def __init__(self):
        self.open_windows = []
        self.events = Notifier()
        self.factories = []

    def addDockWidgetFactory(self, factory):
        self.factories.append(factory)

    def notifier(self):
        return self.events

    def windows(self):
        return self.open_windows


@unittest.skipIf(QWidget is None, "Run real Qt visibility tests inside Krita")
class StartupVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.application = Application()
        self.queued = []
        self.timer = mock.patch.object(QTimer, "singleShot", side_effect=lambda delay, callback: self.queued.append(callback))
        self.timer.start()
        self.addCleanup(self.timer.stop)
        package_name = "mori_startup_visibility_fixture"
        docker_module = ModuleType(package_name + ".docker")
        docker_module.MoriDocker = object
        self.stub = mock.patch.dict(sys.modules, {docker_module.__name__: docker_module})
        self.stub.start()
        self.addCleanup(self.stub.stop)
        source = Path(__file__).resolve().parents[1] / "mori_krita" / "__init__.py"
        spec = importlib.util.spec_from_file_location(package_name, source,
                                                      submodule_search_locations=[str(source.parent)])
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = self.module
        with mock.patch.object(krita.Krita, "instance", return_value=self.application), \
                mock.patch.object(krita, "DockWidgetFactory", return_value=object()):
            spec.loader.exec_module(self.module)
        self.helper = self.module.startup._startup_visibility
        self.addCleanup(self.helper._shutdown)
        self.addCleanup(lambda: sip.delete(self.helper) if not sip.isdeleted(self.helper) else None)

    def window(self, views=0):
        main = QMainWindow()
        dock = QDockWidget("Mori", main)
        dock.setObjectName("mori_krita")
        dock.setProperty("ShowOnWelcomePage", False)
        dock.setWidget(QWidget())
        main.addDockWidget(Qt.RightDockWidgetArea, dock)
        dock.hide()
        other = QDockWidget("Other", main)
        other.setObjectName("other")
        main.addDockWidget(Qt.LeftDockWidgetArea, other)
        other.hide()

        view_count = views

        class Window:
            view_list = [object() for _ in range(view_count)]

            def qwindow(self): return main
            def dockers(self): return [dock, other]
            def views(self): return self.view_list

        window = Window()
        self.application.open_windows.append(window)
        self.addCleanup(lambda: sip.delete(main) if not sip.isdeleted(main) else None)
        return window, main, dock, other

    def flush(self):
        pending, self.queued[:] = list(self.queued), []
        for callback in pending:
            callback()

    def test_welcome_page_keeps_mori_hidden(self):
        _, _, dock, other = self.window()
        self.flush()
        self.assertTrue(self.application.events.enabled)
        self.assertTrue(dock.isHidden())
        self.assertFalse(dock.toggleViewAction().isChecked())
        self.assertTrue(other.isHidden())
        self.assertIsNone(other.property("ShowOnWelcomePage"))

    def test_first_view_restoration_gets_one_deferred_show(self):
        window, _, dock, _ = self.window()
        self.flush()
        dock.hide()  # Simulate Krita restoring its pre-welcome workspace.
        window.view_list = [object()]
        self.application.events.viewCreated.emit(object())
        self.application.events.windowCreated.emit()
        self.assertEqual(len(self.queued), 1)
        self.flush()
        self.assertFalse(dock.isHidden())
        dock.hide()
        self.application.events.viewCreated.emit(object())
        self.flush()
        self.assertTrue(dock.isHidden())

    def test_user_closing_dock_does_not_reopen_for_more_views(self):
        window, _, dock, _ = self.window(1)
        self.flush()
        dock.close()
        window.view_list = [object()]
        self.application.events.viewCreated.emit(object())
        self.flush()
        self.assertTrue(dock.isHidden())

    def test_user_menu_uncheck_remains_closed_in_this_process(self):
        window, _, dock, _ = self.window(1)
        self.flush()
        dock.toggleViewAction().trigger()
        self.assertTrue(dock.isHidden())
        window.view_list = [object()]
        self.application.events.viewCreated.emit(object())
        self.flush()
        self.assertTrue(dock.isHidden())

    def test_last_document_closed_hides_panel_without_disabling_next_canvas(self):
        window, _, dock, _ = self.window(1)
        self.flush()
        self.assertFalse(dock.isHidden())
        window.view_list = []
        self.application.events.viewClosed.emit(object())
        self.flush()
        self.assertTrue(dock.isHidden())
        window.view_list = [object()]
        self.application.events.viewCreated.emit(object())
        self.flush()
        self.assertFalse(dock.isHidden())

    def test_each_new_main_window_is_shown_once(self):
        _, _, first, _ = self.window(1)
        self.flush()
        first.close()
        _, _, second, _ = self.window(1)
        self.application.events.windowCreated.emit()
        self.flush()
        self.assertTrue(first.isHidden())
        self.assertFalse(second.isHidden())

    def test_destroyed_window_and_pending_shutdown_are_safe(self):
        _, main, _, _ = self.window()
        sip.delete(main)
        self.flush()
        self.application.events.viewCreated.emit(object())
        self.helper._shutdown()
        self.flush()
        self.assertEqual(self.helper._windows, {})


if __name__ == "__main__":
    unittest.main()
