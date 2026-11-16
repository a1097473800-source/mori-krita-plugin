"""Measure actual Qt event dispatch, without displaying or touching user art."""
import importlib
import json
from pathlib import Path
import sys
from time import perf_counter
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

import test_selection_controller as fixture

METRICS = {}
BASELINE = Path(__file__).resolve().parents[1] / "qa" / "student-0.3.5" / "baseline"


@unittest.skipIf(fixture.QWidget is None, "Actual Qt dispatch runs inside Krita")
class SelectionInputRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SelectionControllerTests.setUpClass()

    def make_controller(self, module):
        class CountingController(module.SelectionController):
            def __init__(self, docker):
                self.input_count = 0
                super().__init__(docker)

            def eventFilter(self, watched, event):
                self.input_count += 1
                return super().eventFilter(watched, event)

        window = fixture.QWidget()
        window.resize(1000, 800)
        canvas = fixture.FakeCanvas(window)
        canvas.resize(800, 650)
        docker = fixture.FakeDocker(window)
        anchor = SimpleNamespace(parent=canvas, rect=fixture.QRectF(128, 80, 192, 128),
                                 selection_rect=fixture.QRectF(128, 80, 192, 128),
                                 document=object(), document_key="perf-qa", toolbar_rect=None)
        locate = mock.patch.object(module, "locate_selection", return_value=anchor)
        locate.start()
        self.addCleanup(locate.stop)
        controller = CountingController(docker)
        controller._work = lambda function, callback: None
        controller._timer.stop()
        controller._quote_timer.stop()
        controller.refresh()
        self.assertIsNotNone(controller.panel)
        controller._timer.stop()
        controller._quote_timer.stop()
        self.addCleanup(window.deleteLater)
        self.addCleanup(controller.shutdown)
        return controller, window, canvas

    def measure_unrelated(self, controller, samples):
        from PyQt5.QtCore import QEvent
        unrelated = fixture.QWidget()
        event = QEvent(QEvent.User)
        controller.input_count = 0
        started = perf_counter()
        for _ in range(samples):
            fixture.QApplication.sendEvent(unrelated, event)
        elapsed = (perf_counter() - started) * 1000
        count = controller.input_count
        unrelated.deleteLater()
        return {"events": samples, "python_filter_calls": count, "elapsed_ms": round(elapsed, 3)}

    def test_unrelated_qt_events_do_not_enter_the_plugin_filter(self):
        samples = 10000
        if (BASELINE / "selection_controller.py").is_file():
            package = ModuleType("mori_input_baseline")
            package.__path__ = [str(BASELINE), str(Path(__file__).resolve().parents[1] / "mori_krita")]
            sys.modules[package.__name__] = package
            baseline_module = importlib.import_module(package.__name__ + ".selection_controller")
            baseline, _, _ = self.make_controller(baseline_module)
            METRICS["baseline_0.3.4"] = self.measure_unrelated(baseline, samples)
            self.assertGreaterEqual(METRICS["baseline_0.3.4"]["python_filter_calls"], samples)
            baseline.shutdown()
        fixed, _, canvas = self.make_controller(fixture.controller_module)
        METRICS["fixed"] = self.measure_unrelated(fixed, samples)
        self.assertEqual(METRICS["fixed"]["python_filter_calls"], 0)
        # Verify the observer is active on the real Qt receiver, rather than
        # obtaining zero by accidentally disconnecting every input filter.
        from PyQt5.QtCore import QEvent
        fixed.input_count = 0
        fixture.QApplication.sendEvent(canvas, QEvent(QEvent.User))
        self.assertGreater(fixed.input_count, 0)

    def test_real_canvas_dispatch_pauses_before_held_move_samples(self):
        from PyQt5.QtCore import QEvent, QPoint, QPointF, Qt
        from PyQt5.QtGui import QMouseEvent
        controller, _, canvas = self.make_controller(fixture.controller_module)
        point = canvas.mapToGlobal(QPoint(20, 20))

        def mouse(kind, buttons):
            return QMouseEvent(kind, QPointF(20, 20), QPointF(point), Qt.LeftButton, buttons, Qt.NoModifier)

        fixture.QApplication.sendEvent(canvas, mouse(QEvent.MouseButtonPress, Qt.LeftButton))
        self.assertTrue(controller._pointer_down)
        self.assertFalse(controller._timer.isActive())
        move = mouse(QEvent.MouseMove, Qt.LeftButton)
        with mock.patch.object(fixture.controller_module, "locate_selection", side_effect=AssertionError("geometry during drag")), \
                mock.patch.object(controller.panel, "setGeometry", side_effect=AssertionError("layout during drag")), \
                mock.patch.object(controller.panel, "reveal", side_effect=AssertionError("show during drag")):
            for _ in range(1000):
                fixture.QApplication.sendEvent(canvas, move)
                controller.refresh()
        fixture.QApplication.sendEvent(canvas, mouse(QEvent.MouseButtonRelease, Qt.NoButton))
        self.assertFalse(controller._pointer_down)
        self.assertTrue(controller._settle_timer.isActive())
        METRICS["canvas_drag"] = {"held_moves": 1000, "geometry_reads": 0, "layout_updates": 0}

