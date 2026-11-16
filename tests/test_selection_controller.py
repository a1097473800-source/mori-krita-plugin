"""Native selection-controller regressions using Qt widgets and offline doubles.

The fake canvas stays inside a hidden parent: tests never create a desktop
window, read a user document or send a request to Mori.
"""
import importlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
try:
    from PyQt5 import sip
    from PyQt5.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
    from PyQt5.QtGui import QKeyEvent, QMouseEvent
    from PyQt5.QtWidgets import QApplication, QMenu, QWidget
except ImportError:
    QRectF = QApplication = QWidget = None

controller_module = None
if QWidget is not None:
    package = ModuleType("mori_controller_fixture")
    package.__path__ = [str(PROJECT / "mori_krita")]
    sys.modules[package.__name__] = package
    controller_module = importlib.import_module(package.__name__ + ".selection_controller")


class MemorySettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self.values[key] = value


class FakeClient:
    def __init__(self):
        self.user_id = "qa-user"
        self.auth_enabled = True
        self.reads = []
        self.uploads = []

    def session(self):
        self.reads.append("session")
        return {"enabled": True, "user": {"id": self.user_id}}

    def fork_session(self):
        return self

    def config(self):
        self.reads.append("config")
        return {"models": [{"id": "gpt-image-2.5", "name": "GPT Image 2.5", "provider": "openai",
                            "aspects": ["1:1", "3:2", "4:3"], "qualities": ["high"]}],
                "configured": {"openai": True}}

    def quote(self, settings):
        self.reads.append("quote")
        return {"enforcement": True, "totalPoints": 0, "available": 1000}


class FakeGateway:
    def __init__(self):
        self.client = FakeClient()
        self.epoch = 7
        self.session_lock = threading.RLock()

    def _channel(self):
        return self.client

    def _sync_identity(self):
        pass


class FakeBridge:
    def __init__(self):
        self.snapshots = []
        self.context_requests = []
        self.imports = []
        self.import_mappings = []
        self.target = SimpleNamespace(document_key="document-a", x=128, y=80, width=192, height=128,
                                      selection_mask=b"original mask")

    def snapshot(self, source, *, context_padding=False):
        self.snapshots.append(source)
        self.context_requests.append(context_padding)
        return SimpleNamespace(target=self.target)

    def import_png(self, data, name, target, *, selection_mapping=None):
        self.imports.append((data, name, target))
        self.import_mappings.append(selection_mapping)
        return SimpleNamespace(name=lambda: name, uniqueId=lambda: SimpleNamespace(toString=lambda: "new-layer"))


if QWidget is not None:
    class FakeCanvas(QWidget):
        def inherits(self, name):
            return name in ("KisOpenGLCanvas2", "KisQPainterCanvas") or super().inherits(name)

        def isVisible(self):
            return True  # Parent remains hidden, so no OS window is created.

        def mousePressEvent(self, event):
            self.canvas_presses = getattr(self, "canvas_presses", 0) + 1
            event.accept()

    class FakeDocker(QWidget):
        def __init__(self, parent):
            super().__init__(parent)
            self._settings = MemorySettings()
            self._bridge = FakeBridge()
            self._gateway = FakeGateway()
            self._web_started = False
            self._dispatch = SimpleNamespace(requested=SimpleNamespace(emit=lambda task: task["data"]["callback"]()))

        def _ensure_gateway(self):
            return self._gateway


@unittest.skipIf(QWidget is None, "Native Qt controller tests run inside Krita")
class SelectionControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance()
        if cls.application is None:
            raise unittest.SkipTest("Run native tests using Krita's QApplication")

    def setUp(self):
        self.window = QWidget()
        self.window.resize(1000, 800)
        self.canvas = FakeCanvas(self.window)
        self.canvas.resize(800, 650)
        self.docker = FakeDocker(self.window)
        self.document = object()
        self.anchor = self.make_anchor(self.canvas, self.document, "a")
        self.original_locate = controller_module.locate_selection
        controller_module.locate_selection = lambda: self.anchor
        self.controller = controller_module.SelectionController(self.docker)
        self.controller._timer.stop()
        self.controller._quote_timer.stop()
        self.work = []
        self.controller._work = lambda function, callback: self.work.append((function, callback))

    def make_anchor(self, canvas, document, key):
        return SimpleNamespace(parent=canvas, rect=QRectF(128, 80, 192, 128),
                               selection_rect=QRectF(128, 80, 192, 128),
                               document=document, document_key=key, toolbar_rect=None)

    def tearDown(self):
        self.controller.shutdown()
        controller_module.locate_selection = self.original_locate
        self.window.deleteLater()

    def show_panel(self):
        self.controller.refresh()
        self.assertIsNotNone(self.controller.panel)
        return self.controller.panel

    def payload(self):
        return {"message": "修改这个产品的配色为红色", "model": "gpt-image-2.5", "mode": "edit",
                "aspect": "auto", "resolution": "1K", "quality": "high", "count": 1}

    def mouse_event(self, widget, kind, position=None):
        point = position or widget.mapToGlobal(widget.rect().center())
        buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
        return QMouseEvent(kind, QPointF(widget.mapFromGlobal(point)), QPointF(point),
                           Qt.LeftButton, buttons, Qt.NoModifier)

    def begin_task(self):
        panel = self.show_panel()
        self.controller.submit(self.payload())
        self.assertTrue(self.controller._task_id)
        self.assertTrue(panel.is_busy())
        return {"task_id": self.controller._task_id, "epoch": self.docker._gateway.epoch,
                "target": self.docker._bridge.target, "bytes": b"offline-image",
                "asset": {"id": "asset-1", "name": "QA result"}}

    def test_panel_is_a_canvas_child_and_within_viewport(self):
        panel = self.show_panel()
        self.assertIs(panel.parentWidget(), self.canvas)
        self.assertFalse(panel.isHidden())
        self.assertGreaterEqual(panel.x(), 0)
        self.assertGreaterEqual(panel.y(), 0)
        self.assertLessEqual(panel.x() + panel.width(), self.canvas.width())
        self.assertLessEqual(panel.y() + panel.height(), self.canvas.height())

    def test_missing_native_gesture_does_not_break_creation_or_canvas_input(self):
        class EventsWithoutNativeGesture:
            def __getattr__(self, name):
                if name == "NativeGesture":
                    raise AttributeError(name)
                return getattr(QEvent, name)

        self.controller.shutdown()
        with mock.patch.object(controller_module, "QEvent", EventsWithoutNativeGesture()):
            self.controller = controller_module.SelectionController(self.docker)
            self.controller._timer.stop()
            self.controller._work = lambda function, callback: None
            panel = self.show_panel()
            self.assertEqual(self.controller._viewport_events, frozenset((QEvent.Wheel,)))
            origin = self.canvas.mapToGlobal(QPoint(1, 1))
            for kind, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                  (QEvent.MouseButtonRelease, Qt.NoButton)):
                self.controller.eventFilter(self.canvas, SimpleNamespace(
                    type=lambda kind=kind: kind, buttons=lambda buttons=buttons: buttons,
                    globalPos=lambda: origin))
            self.assertFalse(self.controller._pointer_down)
            self.controller._settle_until = 0
            self.controller.refresh()
            self.controller.eventFilter(self.canvas, SimpleNamespace(
                type=lambda: QEvent.Wheel, globalPos=lambda: origin))
            self.assertTrue(panel.isHidden())
            self.assertTrue(self.controller._settle_timer.isActive())
            self.controller._settle_until = 0
            self.controller.refresh()
            self.controller.eventFilter(self.canvas, SimpleNamespace(
                type=lambda: QEvent.KeyRelease, key=lambda: Qt.Key_A, timestamp=lambda: 1001))
            self.assertTrue(self.controller._settle_timer.isActive())
            self.assertFalse(panel.isHidden())

    def test_available_native_gesture_keeps_canvas_navigation_behavior(self):
        kind = getattr(QEvent, "NativeGesture", None)
        if kind is None:
            self.skipTest("This Qt build does not expose native gestures")
        panel = self.show_panel()
        self.assertIn(kind, self.controller._interaction_events)
        self.assertIn(kind, self.controller._viewport_events)
        origin = self.canvas.mapToGlobal(QPoint(1, 1))
        self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: kind, globalPos=lambda: origin))
        self.assertTrue(panel.isHidden())
        self.assertTrue(self.controller._settle_timer.isActive())

    def test_mouse_drag_does_not_query_selection_or_initialize_service(self):
        self.docker._gateway = None
        locate = mock.Mock(return_value=self.anchor)
        original = controller_module.locate_selection
        controller_module.locate_selection = locate
        self.docker._ensure_gateway = mock.Mock(side_effect=AssertionError("service initialized while dragging"))
        try:
            event = SimpleNamespace(type=lambda: QEvent.MouseButtonPress, buttons=lambda: Qt.LeftButton)
            self.controller.eventFilter(self.canvas, event)
            for _ in range(10):
                self.controller.refresh()
            self.assertEqual(locate.call_count, 0)
            self.assertEqual(self.docker._ensure_gateway.call_count, 0)
            self.assertIsNone(self.controller.panel)
        finally:
            controller_module.locate_selection = original

    def test_tablet_drag_never_queries_selection_even_without_mouse_buttons(self):
        locate = mock.Mock(return_value=self.anchor)
        original = controller_module.locate_selection
        controller_module.locate_selection = locate
        try:
            self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.TabletPress))
            for _ in range(8):
                self.controller.refresh()
            self.assertEqual(locate.call_count, 0)
        finally:
            controller_module.locate_selection = original

    def test_held_mouse_samples_do_no_widget_timer_or_geometry_work(self):
        panel = self.show_panel()
        self.controller._timer.start()
        self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.MouseButtonPress,
                                                                  buttons=lambda: Qt.LeftButton))
        self.assertTrue(panel.isHidden())
        self.assertFalse(self.controller._timer.isActive())
        self.assertFalse(self.controller._settle_timer.isActive())
        event = SimpleNamespace(type=lambda: QEvent.MouseMove, buttons=lambda: Qt.LeftButton)
        with mock.patch.object(panel, "isAncestorOf", side_effect=AssertionError("widget query during drag")), \
                mock.patch.object(panel, "is_interacting", side_effect=AssertionError("focus query during drag")), \
                mock.patch.object(panel, "hide", side_effect=AssertionError("repeat hide during drag")), \
                mock.patch.object(self.controller._settle_timer, "start", side_effect=AssertionError("timer restart during drag")), \
                mock.patch.object(self.controller._settle_timer, "stop", side_effect=AssertionError("repeat settle stop during drag")), \
                mock.patch.object(self.controller._timer, "start", side_effect=AssertionError("idle timer restart during drag")), \
                mock.patch.object(self.controller._timer, "stop", side_effect=AssertionError("repeat timer stop during drag")), \
                mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("selection query during drag")):
            for _ in range(1000):
                self.controller.eventFilter(self.canvas, event)
                self.controller.refresh()

    def test_held_tablet_and_touch_samples_have_the_same_zero_work_path(self):
        panel = self.show_panel()
        for begin, update, end in ((QEvent.TabletPress, QEvent.TabletMove, QEvent.TabletRelease),
                                   (QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd)):
            with self.subTest(input=begin):
                self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: begin))
                with mock.patch.object(panel, "isAncestorOf", side_effect=AssertionError("widget query during stroke")), \
                        mock.patch.object(panel, "is_interacting", side_effect=AssertionError("focus query during stroke")), \
                        mock.patch.object(panel, "hide", side_effect=AssertionError("repeat hide during stroke")), \
                        mock.patch.object(self.controller._settle_timer, "start", side_effect=AssertionError("timer restart during stroke")), \
                        mock.patch.object(self.controller._settle_timer, "stop", side_effect=AssertionError("repeat settle stop during stroke")), \
                        mock.patch.object(self.controller._timer, "start", side_effect=AssertionError("idle timer restart during stroke")), \
                        mock.patch.object(self.controller._timer, "stop", side_effect=AssertionError("repeat idle stop during stroke")), \
                        mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("selection query during stroke")):
                    for _ in range(1000):
                        self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: update))
                        self.controller.refresh()
                self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: end))

    def test_release_reveals_only_after_settle_then_resumes_idle_fallback(self):
        panel = self.show_panel()
        with mock.patch.object(controller_module.time, "monotonic", return_value=100.0):
            self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.MouseButtonPress,
                                                                      buttons=lambda: Qt.LeftButton))
            self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.MouseButtonRelease,
                                                                      buttons=lambda: Qt.NoButton))
            self.assertFalse(self.controller._timer.isActive())
            self.assertTrue(self.controller._settle_timer.isActive())
        with mock.patch.object(panel, "reveal", wraps=panel.reveal) as reveal:
            with mock.patch.object(controller_module.time, "monotonic", return_value=100.1):
                self.controller._settled()
                self.assertTrue(panel.isHidden())
                self.assertEqual(reveal.call_count, 0)
            with mock.patch.object(controller_module.time, "monotonic", return_value=100.181):
                self.controller._settled()
                self.assertFalse(panel.isHidden(), self.controller.last_error)
                self.assertEqual(reveal.call_count, 1)
                self.assertTrue(self.controller._timer.isActive())

    def test_new_press_cancels_pending_reveal_without_reading_selection(self):
        self.show_panel()
        press = SimpleNamespace(type=lambda: QEvent.MouseButtonPress, buttons=lambda: Qt.LeftButton)
        release = SimpleNamespace(type=lambda: QEvent.MouseButtonRelease, buttons=lambda: Qt.NoButton)
        self.controller.eventFilter(self.canvas, press)
        self.controller.eventFilter(self.canvas, release)
        self.assertTrue(self.controller._settle_timer.isActive())
        self.controller.eventFilter(self.canvas, press)
        self.assertFalse(self.controller._settle_timer.isActive())
        with mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("early selection query")):
            self.controller._settled()
            self.controller.refresh()
        self.assertTrue(self.controller.panel.isHidden())

    def test_drag_entering_canvas_with_press_missed_still_pauses(self):
        self.show_panel()
        self.controller._timer.start()
        self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.MouseMove,
                                                                  buttons=lambda: Qt.LeftButton))
        self.assertTrue(self.controller._pointer_down)
        self.assertFalse(self.controller._timer.isActive())
        self.assertTrue(self.controller.panel.isHidden())

    def test_touch_cancel_and_tablet_proximity_loss_finish_interrupted_drag(self):
        for begin, end in ((QEvent.TouchBegin, QEvent.TouchCancel),
                           (QEvent.TabletPress, QEvent.TabletLeaveProximity)):
            with self.subTest(input=begin):
                self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: begin))
                self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: end))
                self.assertFalse(self.controller._touch_down)
                self.assertFalse(self.controller._tablet_down)
                self.assertTrue(self.controller._settle_timer.isActive())

    def test_composer_input_does_not_hide_or_pause_the_composer(self):
        panel = self.show_panel()
        self.controller.eventFilter(panel.editor, SimpleNamespace(type=lambda: QEvent.MouseButtonPress,
                                                                    buttons=lambda: Qt.LeftButton))
        self.controller.eventFilter(panel.editor, SimpleNamespace(type=lambda: QEvent.KeyRelease))
        self.assertFalse(panel.isHidden(), self.controller.last_error)
        self.assertFalse(self.controller._pointer_down)
        self.assertFalse(self.controller._settle_timer.isActive())

    def test_real_editor_viewport_click_and_typing_keep_composer_open(self):
        panel = self.show_panel()
        panel.cancel_reveal()
        panel.layout().activate()
        viewport = panel.editor.viewport()
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            QApplication.sendEvent(viewport, self.mouse_event(viewport, kind))
        QApplication.sendEvent(panel.editor, QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier, "a"))
        QApplication.sendEvent(panel.editor, QKeyEvent(QEvent.KeyRelease, Qt.Key_A, Qt.NoModifier, "a"))
        self.assertEqual(panel.message(), "a")
        self.assertFalse(panel.isHidden())
        self.assertFalse(self.controller._pointer_down)
        self.assertFalse(self.controller._settle_timer.isActive())
        self.assertEqual(getattr(self.canvas, "canvas_presses", 0), 0)

    def test_padded_card_click_does_not_reach_canvas_or_hide_composer(self):
        panel = self.show_panel()
        panel.cancel_reveal()
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            QApplication.sendEvent(panel._card, self.mouse_event(panel._card, kind))
        self.assertFalse(panel.isHidden())
        self.assertEqual(getattr(self.canvas, "canvas_presses", 0), 0)
        self.assertFalse(self.controller._pointer_down)

    def test_canvas_forwarded_press_over_composer_is_consumed(self):
        panel = self.show_panel()
        point = panel.mapToGlobal(panel.rect().center())
        QApplication.sendEvent(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonPress, point))
        QApplication.sendEvent(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonRelease, point))
        self.assertFalse(panel.isHidden())
        self.assertEqual(getattr(self.canvas, "canvas_presses", 0), 0)
        self.assertFalse(self.controller._pointer_down)
        self.assertFalse(self.controller._settle_timer.isActive())

    def test_global_panel_hit_does_not_capture_another_krita_window(self):
        panel = self.show_panel()
        other_window = QWidget()
        try:
            point = panel.mapToGlobal(panel.rect().center())
            event = self.mouse_event(other_window, QEvent.MouseButtonPress, point)
            self.assertEqual(self.controller._panel_input_owner(other_window, event), 0)
        finally:
            other_window.deleteLater()

    def test_parameter_and_popup_input_belong_to_composer(self):
        panel = self.show_panel()
        panel.cancel_reveal()
        press = self.mouse_event(panel.parameter_button, QEvent.MouseButtonPress)
        # Inspect the real press before QToolButton opens its modal popup loop.
        self.assertFalse(self.controller.eventFilter(panel.parameter_button, press))
        release = self.mouse_event(panel.parameter_button, QEvent.MouseButtonRelease)
        QApplication.sendEvent(panel.parameter_button, release)
        menu = QMenu(panel.parameter_button)
        try:
            menu.resize(160, 30)
            for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                QApplication.sendEvent(menu, self.mouse_event(menu, kind))
            self.assertFalse(panel.isHidden())
            self.assertFalse(self.controller._pointer_down)
            self.assertFalse(self.controller._settle_timer.isActive())
        finally:
            menu.deleteLater()

    def test_real_render_button_click_emits_once_and_stays_visible(self):
        panel = self.show_panel()
        panel.cancel_reveal()
        panel.set_message("把颜色改成红色")
        panel.submitted.disconnect(self.controller.submit)
        emitted = []
        panel.submitted.connect(emitted.append)
        for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
            QApplication.sendEvent(panel.submit_button, self.mouse_event(panel.submit_button, kind))
        self.assertEqual(len(emitted), 1)
        self.assertEqual(emitted[0]["message"], "把颜色改成红色")
        self.assertFalse(panel.isHidden())
        self.assertFalse(self.controller._pointer_down)
        self.assertFalse(self.controller._settle_timer.isActive())

    def test_keyboard_selection_action_schedules_a_single_settled_refresh(self):
        panel = self.show_panel()
        self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.KeyRelease))
        self.assertFalse(panel.isHidden())
        self.assertTrue(self.controller._settle_timer.isActive())
        self.assertFalse(self.controller._timer.isActive())

    def test_outside_toolbar_click_and_key_release_keep_open_composer_stable(self):
        panel = self.show_panel()
        panel.cancel_reveal()
        outside = QWidget(self.window)
        outside.setGeometry(850, 10, 80, 40)
        with mock.patch.object(panel, "hide", wraps=panel.hide) as hide, \
                mock.patch.object(panel, "reveal", wraps=panel.reveal) as reveal:
            for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
                QApplication.sendEvent(outside, self.mouse_event(outside, kind))
            QApplication.sendEvent(outside, QKeyEvent(QEvent.KeyRelease, Qt.Key_A, Qt.NoModifier))
            self.controller.refresh()
        self.assertFalse(panel.isHidden())
        self.assertFalse(self.controller._settle_timer.isActive())
        self.assertFalse(self.controller._pointer_down)
        self.assertEqual(hide.call_count, 0)
        self.assertEqual(reveal.call_count, 0)

    def test_canvas_click_without_moving_does_not_hide_or_reveal_same_selection(self):
        panel = self.show_panel()
        panel.cancel_reveal()
        point = self.canvas.mapToGlobal(QPoint(20, 20))
        with mock.patch.object(panel, "hide", wraps=panel.hide) as hide, \
                mock.patch.object(panel, "reveal", wraps=panel.reveal) as reveal:
            self.controller.eventFilter(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonPress, point))
            self.assertFalse(panel.isHidden())
            self.controller.eventFilter(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonRelease, point))
            with mock.patch.object(controller_module.time, "monotonic", return_value=self.controller._settle_until + 0.001):
                self.controller._settled()
        self.assertFalse(panel.isHidden())
        self.assertEqual(hide.call_count, 0)
        self.assertEqual(reveal.call_count, 0)

    def test_canvas_drag_hides_once_after_threshold_and_reveals_after_release(self):
        panel = self.show_panel()
        point = self.canvas.mapToGlobal(QPoint(20, 20))
        self.controller.eventFilter(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonPress, point))
        self.assertFalse(panel.isHidden())
        self.assertFalse(self.controller._timer.isActive())
        far = point + QPoint(self.controller._drag_distance + 1, 0)
        self.controller.eventFilter(self.canvas, self.mouse_event(self.canvas, QEvent.MouseMove, far))
        self.assertTrue(panel.isHidden())
        with mock.patch.object(panel, "hide", side_effect=AssertionError("repeat hide")), \
                mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("drag read")):
            for _ in range(1000):
                self.controller.eventFilter(self.canvas, self.mouse_event(self.canvas, QEvent.MouseMove, far))
                self.controller.refresh()
        self.controller.eventFilter(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonRelease, far))
        with mock.patch.object(controller_module.time, "monotonic", return_value=self.controller._settle_until + 0.001):
            self.controller._settled()
        self.assertFalse(panel.isHidden())

    def test_global_held_button_fallback_never_hides_for_an_untracked_ui_press(self):
        panel = self.show_panel()
        with mock.patch.object(controller_module.QApplication, "mouseButtons", return_value=Qt.LeftButton), \
                mock.patch.object(panel, "is_interacting", return_value=False), \
                mock.patch.object(panel, "hide", side_effect=AssertionError("outside press hides")), \
                mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("held read")):
            self.controller.refresh()
        self.assertFalse(panel.isHidden())
        self.assertFalse(self.controller._pointer_down)

    def test_release_waits_for_settle_before_querying_selection(self):
        locate = mock.Mock(return_value=self.anchor)
        original = controller_module.locate_selection
        controller_module.locate_selection = locate
        try:
            with mock.patch.object(controller_module.time, "monotonic", return_value=100.0):
                self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.MouseButtonPress,
                                                                         buttons=lambda: Qt.LeftButton))
                self.controller.eventFilter(self.canvas, SimpleNamespace(type=lambda: QEvent.MouseButtonRelease,
                                                                         buttons=lambda: Qt.NoButton))
                self.controller.refresh()
                self.assertEqual(locate.call_count, 0)
            with mock.patch.object(controller_module.time, "monotonic", return_value=100.23):
                self.controller.refresh()
                self.assertEqual(locate.call_count, 1)
        finally:
            controller_module.locate_selection = original

    def test_idle_unchanged_does_not_relayout_show_or_raise_panel(self):
        panel = self.show_panel()
        for name in ("set_context", "setGeometry", "show", "raise_"):
            setattr(panel, name, mock.Mock(wraps=getattr(panel, name)))
        for _ in range(10):
            self.controller.refresh()
        for name in ("set_context", "setGeometry", "show", "raise_"):
            self.assertEqual(getattr(panel, name).call_count, 0, name)

    def test_idle_display_does_not_create_gateway(self):
        self.docker._gateway = None
        self.docker._ensure_gateway = mock.Mock(side_effect=AssertionError("unexpected SSL initialization"))
        panel = self.show_panel()
        self.assertFalse(panel.isHidden())
        self.assertEqual(self.docker._ensure_gateway.call_count, 0)
        self.assertEqual(self.work, [])

    def test_reenabling_after_an_interrupted_drag_clears_pointer_state(self):
        self.controller._pointer_down = True
        self.controller._tablet_down = True
        self.controller._settle_until = 1000000000.0
        self.controller.set_enabled(False)
        self.controller.set_enabled(True)
        self.assertFalse(self.controller._pointer_down)
        self.assertFalse(self.controller._tablet_down)
        self.assertFalse(self.controller.panel.isHidden())

    def test_submit_rejects_another_windows_selection_before_reading_pixels(self):
        self.show_panel()
        other_window = QWidget()
        other_canvas = FakeCanvas(other_window)
        other_canvas.resize(800, 650)
        self.anchor = self.make_anchor(other_canvas, object(), "other")
        self.controller.submit(self.payload())
        self.assertIsNone(self.controller._task_id)
        self.assertEqual(self.docker._bridge.snapshots, [])
        other_window.deleteLater()

    def test_submit_rejects_a_new_document_until_context_has_refreshed(self):
        self.show_panel()
        self.anchor = self.make_anchor(self.canvas, object(), "b")
        self.controller.submit(self.payload())
        self.assertIsNone(self.controller._task_id)
        self.assertEqual(self.docker._bridge.snapshots, [])

    def test_missing_selection_hides_even_focused_panel_and_preserves_draft(self):
        panel = self.show_panel()
        panel.set_message("尚未发送的草稿")
        panel.is_interacting = lambda: True
        original = self.anchor
        self.anchor = None
        self.controller.refresh()
        self.assertTrue(panel.isHidden())
        self.anchor = original
        self.controller.refresh()
        self.assertEqual(panel.message(), "尚未发送的草稿")
        self.assertFalse(panel.isHidden())

    def test_switching_documents_keeps_separate_drafts(self):
        panel = self.show_panel()
        panel.set_message("文档A的草稿")
        original = self.anchor
        self.anchor = self.make_anchor(self.canvas, object(), "b")
        self.controller.refresh()
        self.assertEqual(panel.message(), "")
        panel.set_message("文档B的草稿")
        self.anchor = original
        self.controller.refresh()
        self.assertEqual(panel.message(), "文档A的草稿")

    def test_collapse_is_persisted_and_does_not_hide_the_small_bar(self):
        panel = self.show_panel()
        panel.set_message("保留折叠草稿")
        expanded_height = panel.height()
        panel.set_collapsed(True)
        self.controller.refresh()
        self.assertTrue(panel.is_collapsed())
        self.assertFalse(panel.isHidden())
        self.assertLess(panel.height(), expanded_height)
        self.assertEqual(panel.message(), "保留折叠草稿")
        self.assertTrue(self.docker._settings.values["selectionCollapsed"])

    def test_other_window_cannot_receive_this_docks_panel(self):
        panel = self.show_panel()
        other_window = QWidget()
        other_canvas = FakeCanvas(other_window)
        other_canvas.resize(800, 650)
        self.anchor = self.make_anchor(other_canvas, object(), "other")
        self.controller.refresh()
        self.assertIs(panel.parentWidget(), self.canvas)
        self.assertTrue(panel.isHidden())
        other_window.deleteLater()

    def test_configuration_load_is_read_only_and_does_not_capture_or_upload(self):
        self.show_panel()
        function, callback = self.work.pop(0)
        callback(function(), None)
        self.controller._quote_timer.stop()
        self.assertEqual(self.docker._gateway.client.reads, ["session", "config"])
        self.assertEqual(self.docker._bridge.snapshots, [])
        self.assertEqual(self.docker._gateway.client.uploads, [])

    def test_unrelated_widget_events_never_enter_the_controller_filter(self):
        outside = QWidget()
        try:
            with mock.patch.object(self.controller, "eventFilter", wraps=self.controller.eventFilter) as filtered:
                for _ in range(100):
                    QApplication.sendEvent(outside, QEvent(QEvent.User))
                self.assertEqual(filtered.call_count, 0)
        finally:
            outside.deleteLater()

    def test_scoped_canvas_filter_observes_the_first_stroke_without_a_selection(self):
        self.anchor = None
        self.controller.refresh()
        self.assertIs(self.controller._input_canvas, self.canvas)
        with mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("held native read")):
            QApplication.sendEvent(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonPress))
            self.assertTrue(self.controller._pointer_down)
            self.assertFalse(self.controller._timer.isActive())
            self.controller.refresh()
            QApplication.sendEvent(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonRelease))
        self.assertFalse(self.controller._pointer_down)
        self.assertTrue(self.controller._settle_timer.isActive())

    def test_scoped_filters_rebind_when_the_canvas_is_replaced(self):
        self.show_panel()
        old_canvas = self.canvas
        old_canvas.hide()
        old_canvas.isVisible = lambda: False
        # The fake visibility override keeps a hidden fixture visible; remove it
        # from this main window so the real pure-Qt finder sees one replacement.
        old_canvas.setParent(None)
        self.canvas = FakeCanvas(self.window)
        self.canvas.resize(800, 650)
        self.anchor = self.make_anchor(self.canvas, object(), "replacement")
        self.controller.refresh()
        self.assertIs(self.controller._input_canvas, self.canvas)
        with mock.patch.object(self.controller, "eventFilter", wraps=self.controller.eventFilter) as filtered:
            QApplication.sendEvent(old_canvas, QEvent(QEvent.User))
            self.assertEqual(filtered.call_count, 0)
            QApplication.sendEvent(self.canvas, self.mouse_event(self.canvas, QEvent.MouseButtonPress))
            self.assertTrue(filtered.called)
        old_canvas.deleteLater()

    def test_inactive_owner_does_not_query_the_native_selection(self):
        self.show_panel()
        other_window = QWidget()
        try:
            with mock.patch.object(controller_module.QApplication, "activeWindow", return_value=other_window), \
                    mock.patch.object(controller_module, "locate_selection", side_effect=AssertionError("inactive native read")), \
                    mock.patch.object(controller_module, "find_canvas_for_window", side_effect=AssertionError("inactive widget scan")):
                self.controller.refresh()
        finally:
            other_window.deleteLater()

    def test_busy_native_document_keeps_the_last_panel_and_anchor(self):
        panel = self.show_panel()
        anchor = self.controller.anchor
        with mock.patch.object(controller_module, "locate_selection", side_effect=controller_module.SelectionBusy()), \
                mock.patch.object(panel, "hide", side_effect=AssertionError("busy document hid panel")):
            self.controller.refresh()
        self.assertIs(self.controller.anchor, anchor)
        self.assertFalse(panel.isHidden())

    def test_config_response_during_a_stroke_changes_no_widgets_until_idle(self):
        panel = self.show_panel()
        function, callback = self.work.pop(0)
        value = function()
        self.controller._pointer_down = True
        with mock.patch.object(panel, "set_models", wraps=panel.set_models) as models, \
                mock.patch.object(panel, "set_status", wraps=panel.set_status) as status, \
                mock.patch.object(panel, "set_quote", wraps=panel.set_quote) as quote:
            callback(value, None)
            self.assertEqual((models.call_count, status.call_count, quote.call_count), (0, 0, 0))
            self.assertIsNotNone(self.controller._pending_config)
            self.controller._pointer_down = False
            self.controller.refresh()
            self.assertEqual((models.call_count, status.call_count), (1, 1))
            self.assertIsNone(self.controller._pending_config)
            self.controller.refresh()
            self.assertEqual(models.call_count, 1)

    def test_deferred_configuration_is_rejected_after_account_change(self):
        panel = self.show_panel()
        function, callback = self.work.pop(0)
        value = function()
        self.controller._pointer_down = True
        callback(value, None)
        self.docker._gateway.client.user_id = "different-user"
        self.controller._pointer_down = False
        with mock.patch.object(panel, "set_models", side_effect=AssertionError("stale account config")):
            self.controller.refresh()
        self.assertIsNone(self.controller._pending_config)

    def test_quote_start_during_a_stroke_defers_even_settings_and_aspect_reads(self):
        panel = self.show_panel()
        count = len(self.work)
        self.controller._pointer_down = True
        with mock.patch.object(panel, "settings", side_effect=AssertionError("settings read during stroke")), \
                mock.patch.object(controller_module, "selection_aspect", side_effect=AssertionError("aspect during stroke")), \
                mock.patch.object(panel, "set_quote", side_effect=AssertionError("quote UI during stroke")):
            self.controller._quote()
        self.assertTrue(self.controller._quote_requested)
        self.assertEqual(len(self.work), count)

    def test_latest_quote_response_is_applied_once_after_drag_and_stale_one_is_dropped(self):
        panel = self.show_panel()
        function, callback = self.work.pop(0)
        callback(function(), None)
        self.controller._quote_timer.stop()
        self.controller._quote()
        _, received = self.work.pop(0)
        self.controller._pointer_down = True
        with mock.patch.object(panel, "set_quote", wraps=panel.set_quote) as quote:
            received({"enforcement": True, "totalPoints": 1, "available": 100}, None)
            received({"enforcement": True, "totalPoints": 9, "available": 100}, None)
            quote.assert_not_called()
            self.controller._pointer_down = False
            self.controller.refresh()
            quote.assert_called_once()
            self.assertIn("9 积分", quote.call_args.args[0])
            self.controller._pointer_down = True
            received({"enforcement": True, "totalPoints": 99, "available": 100}, None)
            self.controller._quote_version += 1
            self.controller._pointer_down = False
            self.controller.refresh()
            self.assertEqual(quote.call_count, 1)

    def test_application_deactivation_and_window_ungrab_release_a_stroke(self):
        self.controller._pointer_down = True
        self.controller._application_state_changed(Qt.ApplicationInactive)
        self.assertFalse(self.controller._pointer_down)
        self.assertTrue(self.controller._settle_timer.isActive())
        self.controller._tablet_down = True
        self.controller.eventFilter(self.canvas, QEvent(QEvent.UngrabMouse))
        self.assertFalse(self.controller._tablet_down)

    def test_floating_dock_uses_its_parent_main_window_for_canvas_binding(self):
        with mock.patch.object(self.docker, "window", return_value=self.docker):
            self.controller._bind_input_scope()
            self.assertIs(self.controller._owner_window(), self.window)
            self.assertIs(self.controller._input_canvas, self.canvas)

    def test_task_completion_during_drag_defers_widget_changes_until_idle(self):
        self.begin_task()
        _, finished = self.work[-1]
        panel = self.controller.panel
        self.controller._pointer_down = True
        with mock.patch.object(panel, "set_busy", wraps=panel.set_busy) as busy, \
                mock.patch.object(panel, "set_status", wraps=panel.set_status) as status:
            finished({"success_message": "Finished offline", "status": "completed"}, None)
            self.assertIsNone(self.controller._task_id)
            self.assertIsNotNone(self.controller._pending_completion)
            busy.assert_not_called()
            status.assert_not_called()
            self.controller._pointer_down = False
            self.controller.refresh()
            busy.assert_called_once_with(False, "Finished offline")
            self.assertFalse(panel.is_busy())

    def test_task_completion_does_not_show_stale_account_response(self):
        self.begin_task()
        _, finished = self.work[-1]
        panel = self.controller.panel
        self.controller._pointer_down = True
        finished({"success_message": "Private response", "message": "Private answer", "status": "completed"}, None)
        self.docker._gateway.client.user_id = "new-user"
        self.controller._pointer_down = False
        self.controller.refresh()
        self.assertFalse(panel.is_busy())
        self.assertNotIn("Private", panel._status_text)

    def test_login_restore_busy_document_does_not_raise_or_submit(self):
        self.show_panel()
        self.controller._restore_login(self.docker._gateway, self.payload())
        _, restored = self.work[-1]
        self.docker._gateway.client.session_version = 1
        self.docker._gateway.client.session_restored = False
        with mock.patch.object(controller_module, "locate_selection", side_effect=controller_module.SelectionBusy()):
            restored(1, None)
        self.assertIsNone(self.controller._task_id)
        self.assertEqual(self.docker._bridge.snapshots, [])

    def test_delivery_requires_current_task_gateway_and_epoch(self):
        data = self.begin_task()
        for changed in ({"task_id": "another-task"}, {"epoch": 999}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.controller.deliver({**data, **changed})
        original = self.docker._gateway
        self.docker._gateway = FakeGateway()
        with self.assertRaises(RuntimeError):
            self.controller.deliver(data)
        self.docker._gateway = original
        self.assertEqual(self.docker._bridge.imports, [])

    def test_submit_reads_surrounding_context_only_after_the_send_action(self):
        self.show_panel()
        self.assertEqual(self.docker._bridge.context_requests, [])
        self.controller.submit(self.payload())
        self.assertEqual(self.docker._bridge.context_requests, [True])

    def test_context_delivery_requires_the_registered_mapping_object(self):
        data = self.begin_task()
        self.controller._task_needs_mapping = True
        mapping = SimpleNamespace(assert_target=mock.Mock())
        with self.assertRaisesRegex(RuntimeError, "映射不匹配"):
            self.controller.deliver(data)
        self.controller.register_mapping({**data, "selection_mapping": mapping})
        mapping.assert_target.assert_called_once_with(self.docker._bridge.target)
        with self.assertRaisesRegex(RuntimeError, "映射不匹配"):
            self.controller.deliver({**data, "selection_mapping": SimpleNamespace()})
        with self.assertRaisesRegex(RuntimeError, "映射已改变"):
            self.controller.register_mapping({**data, "selection_mapping": SimpleNamespace(assert_target=mock.Mock())})
        self.controller.deliver({**data, "selection_mapping": mapping})
        self.assertIs(self.docker._bridge.import_mappings[0], mapping)
        self.assertIs(self.docker._bridge.imports[0][2], self.docker._bridge.target)

    def test_stale_mapping_cannot_be_registered_to_another_task_or_region(self):
        data = self.begin_task()
        mapping = SimpleNamespace(assert_target=mock.Mock())
        replacement = SimpleNamespace(**{**vars(self.docker._bridge.target), "x": 999})
        for changed in ({"task_id": "stale-task"}, {"epoch": 999}, {"target": replacement}):
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.controller.register_mapping({**data, **changed, "selection_mapping": mapping})
        mapping.assert_target.assert_not_called()
        self.assertIsNone(self.controller._task_mapping)

    def test_context_delivery_keeps_mapping_when_selection_and_document_change(self):
        data = self.begin_task()
        self.controller._task_needs_mapping = True
        mapping = SimpleNamespace(assert_target=mock.Mock())
        self.controller.register_mapping({**data, "selection_mapping": mapping})
        self.anchor = self.make_anchor(self.canvas, object(), "another-document")
        self.controller.refresh()
        self.controller.deliver({**data, "selection_mapping": mapping})
        self.assertIs(self.docker._bridge.import_mappings[0], mapping)
        self.assertIs(self.docker._bridge.imports[0][2], self.docker._bridge.target)

    def test_unregistered_mapping_is_rejected_even_for_a_legacy_snapshot(self):
        data = self.begin_task()
        with self.assertRaisesRegex(RuntimeError, "映射不匹配"):
            self.controller.deliver({**data, "selection_mapping": SimpleNamespace()})
        self.assertEqual(self.docker._bridge.imports, [])

    def test_delivery_rejects_another_document_or_region(self):
        data = self.begin_task()
        target = self.docker._bridge.target
        for changed in ({"document_key": "wrong-document"}, {"x": 999}, {"height": 777}):
            replacement = SimpleNamespace(**{**vars(target), **changed})
            with self.subTest(changed=changed), self.assertRaises(RuntimeError):
                self.controller.deliver({**data, "target": replacement})
        self.assertEqual(self.docker._bridge.imports, [])

    def test_delivery_uses_original_mask_even_if_worker_replaces_it(self):
        data = self.begin_task()
        target = self.docker._bridge.target
        replacement = SimpleNamespace(**{**vars(target), "selection_mask": b"worker replacement"})
        result = self.controller.deliver({**data, "target": replacement})
        self.assertEqual(result["layer_id"], "new-layer")
        self.assertIs(self.docker._bridge.imports[0][2], target)

    def test_running_task_hides_when_switching_documents_and_imports_to_original(self):
        data = self.begin_task()
        panel = self.controller.panel
        self.anchor = self.make_anchor(self.canvas, object(), "b")
        self.controller.refresh()
        self.assertTrue(panel.isHidden())
        self.controller.deliver(data)
        self.assertIs(self.docker._bridge.imports[0][2], self.docker._bridge.target)

    def test_stale_worker_completion_cannot_finish_a_newer_task(self):
        self.begin_task()
        _, callback = self.work[-1]
        self.controller._task_id = "newer-task"
        callback({"conversation_id": "old-conversation", "success_message": "Old result"}, None)
        self.assertEqual(self.controller._task_id, "newer-task")
        self.assertTrue(self.controller.panel.is_busy())
        self.assertNotIn("old-conversation", self.controller._conversations.values())

    def test_shutdown_after_composer_cpp_deletion_still_cancels_and_stops_pool(self):
        panel = self.show_panel()
        cancelled = self.controller._cancel = threading.Event()
        sip.delete(panel)
        self.assertIsNone(self.controller.panel)
        with mock.patch.object(self.controller._pool, "shutdown", wraps=self.controller._pool.shutdown) as stop:
            self.controller.shutdown()
            self.controller.shutdown()
        self.assertTrue(cancelled.is_set())
        stop.assert_called_once_with(wait=False, cancel_futures=True)
        self.assertFalse(self.controller._timer.isActive())
        self.assertFalse(self.controller._quote_timer.isActive())
        self.assertFalse(self.controller._settle_timer.isActive())

    def test_canvas_cpp_deletion_forgets_panel_and_allows_another_canvas(self):
        old_panel = self.show_panel()
        sip.delete(self.canvas)
        self.assertTrue(sip.isdeleted(old_panel))
        self.assertIsNone(self.controller.panel)
        self.assertIsNone(self.controller.anchor)
        self.assertFalse(self.controller._closed)
        self.canvas = FakeCanvas(self.window)
        self.canvas.resize(800, 650)
        self.anchor = self.make_anchor(self.canvas, object(), "b")
        replacement = self.show_panel()
        self.assertIsNot(replacement, old_panel)
        self.assertIs(replacement.parentWidget(), self.canvas)

    def test_queued_config_callback_ignores_an_already_deleted_composer(self):
        panel = self.show_panel()
        function, callback = self.work.pop(0)
        sip.delete(panel)
        callback(function(), None)
        self.assertIsNone(self.controller.panel)
        self.assertFalse(self.controller._config_loading)

    def test_generation_completion_and_progress_after_panel_deletion_are_safe(self):
        self.begin_task()
        _, callback = self.work[-1]
        cancelled = self.controller._cancel
        sip.delete(self.controller.panel)
        self.anchor = None
        self.controller._progress("late progress")
        self.controller.cancel()
        self.assertTrue(cancelled.is_set())
        callback({"success_message": "Finished offline"}, None)
        self.assertIsNone(self.controller._task_id)
        self.assertIsNone(self.controller.panel)

    def test_restore_callback_cannot_touch_deleted_composer_or_submit(self):
        panel = self.show_panel()
        self.controller._restore_login(self.docker._gateway, self.payload())
        _, callback = self.work[-1]
        sip.delete(panel)
        callback(1, None)
        self.assertFalse(self.controller._restoring_session)
        self.assertIsNone(self.controller._task_id)
        self.assertEqual(self.docker._bridge.snapshots, [])

    def test_deleting_controller_cpp_object_performs_python_cleanup_and_drops_posts(self):
        self.show_panel()
        cancelled = self.controller._cancel = threading.Event()
        pool = self.controller._pool
        sip.delete(self.controller)
        self.assertTrue(self.controller._closed)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(pool._shutdown)
        called = []
        self.controller._post(lambda: called.append(True))
        self.controller._progress("too late")
        self.controller.shutdown()
        self.assertEqual(called, [])

    def test_post_does_not_emit_to_a_deleted_qt_dispatcher(self):
        dispatcher = QWidget()
        self.docker._dispatch = dispatcher
        sip.delete(dispatcher)
        self.controller._post(lambda: self.fail("Callback emitted to deleted QObject"))

    def test_old_composer_destroyed_signal_cannot_forget_its_replacement(self):
        old_panel = self.show_panel()
        replacement = controller_module.SelectionComposer(self.canvas)
        self.controller.panel = replacement
        sip.delete(old_panel)
        self.assertIs(self.controller.panel, replacement)


def main(arguments):
    """Run from official kritarunner without opening a GUI window."""
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(SelectionControllerTests))
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "user_document_reads": 0, "external_requests": 0}
    (output / "selection-controller-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "selection-controller-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    return report


if __name__ == "__main__":
    unittest.main()
