"""Selection anchoring tests: no user canvas or network access."""
import importlib.util
from contextlib import ExitStack
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

_SPEC = importlib.util.spec_from_file_location(
    "mori_selection_geometry", Path(__file__).resolve().parents[1] / "mori_krita" / "selection_geometry.py")
geometry = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = geometry
_SPEC.loader.exec_module(geometry)


class GeometryLookupCacheTests(unittest.TestCase):
    class Canvas:
        def __init__(self):
            self.visible = True
            self.scans = 0
            self.child_count = 0

        def inherits(self, name):
            return name == "KisOpenGLCanvas2"

        def isVisible(self): return self.visible
        def width(self): return 800
        def height(self): return 600
        def children(self): return [object()] * self.child_count
        def findChildren(self, _):
            self.scans += 1
            return []

    class Scope:
        def __init__(self, canvas):
            self.canvas = canvas
            self.scans = 0

        def findChildren(self, _):
            self.scans += 1
            return [self.canvas]

    class Area:
        def __init__(self, scope): self.scope = scope
        def activeSubWindow(self): return self.scope

    class Main:
        def __init__(self, area):
            self.area = area
            self.scans = 0
        def findChildren(self, _):
            self.scans += 1
            return [self.area]

    def setUp(self):
        geometry.clear_geometry_cache()

    def test_canvas_tree_is_scanned_once_until_active_view_changes(self):
        first = self.Canvas()
        first_scope = self.Scope(first)
        area = self.Area(first_scope)
        window = self.Main(area)
        for _ in range(12):
            self.assertIs(geometry._find_canvas(window), first)
        self.assertEqual(window.scans, 1)
        self.assertEqual(first_scope.scans, 1)
        second = self.Canvas()
        second_scope = self.Scope(second)
        area.scope = second_scope
        self.assertIs(geometry._find_canvas(window), second)
        self.assertEqual(window.scans, 1)
        self.assertEqual(second_scope.scans, 1)

    def test_toolbar_tree_is_rescanned_only_after_children_change(self):
        canvas = self.Canvas()
        for _ in range(12):
            self.assertIsNone(geometry._toolbar_rect(canvas))
        self.assertEqual(canvas.scans, 1)
        canvas.child_count = 1
        geometry._toolbar_rect(canvas)
        self.assertEqual(canvas.scans, 2)

    def test_public_canvas_lookup_uses_only_the_widget_tree(self):
        canvas = self.Canvas()
        scope = self.Scope(canvas)
        window = self.Main(self.Area(scope))
        window.document = mock.Mock(side_effect=AssertionError("Canvas rebinding read a document"))
        for _ in range(3):
            self.assertIs(geometry.find_canvas_for_window(window), canvas)
        window.document.assert_not_called()
        self.assertEqual(scope.scans, 1)
        self.assertIsNone(geometry.find_canvas_for_window(None))

    def test_public_canvas_lookup_tolerates_deleted_native_widgets(self):
        window = mock.Mock()
        window.findChildren.side_effect = RuntimeError("wrapped C++ object has been deleted")
        self.assertIsNone(geometry.find_canvas_for_window(window))


class PanelPlacementTests(unittest.TestCase):
    def test_below_selection_reserves_native_toolbar(self):
        self.assertEqual(geometry.place_panel((100, 100, 200, 100), 800, 700, 530, 130),
                         (8, 256, 530, 130))

    def test_bottom_selection_uses_upper_edge(self):
        x, y, width, height = geometry.place_panel((250, 550, 160, 80), 800, 700, 530, 130)
        self.assertEqual((x, y, width, height), (65, 408, 530, 130))
        self.assertLess(y + height, 550)

    def test_horizontal_clamping_and_narrow_viewport(self):
        self.assertEqual(geometry.place_panel((900, 40, 100, 100), 600, 700, 530, 130)[0], 62)
        self.assertEqual(geometry.place_panel((10, 50, 100, 100), 280, 500, 530, 130)[2], 264)

    def test_collapsed_panel_uses_the_same_anchor(self):
        self.assertEqual(geometry.place_panel((200, 180, 200, 100), 800, 700, 250, 34),
                         (175, 336, 250, 34))

    def test_exact_native_toolbar_collision_is_avoided(self):
        box = geometry.place_panel((250, 100, 200, 100), 800, 700, 530, 130,
                                   toolbar_margin=0, obstacles=[(220, 210, 280, 40)])
        self.assertEqual(box[1], 262)

    def test_pinned_bottom_toolbar_is_avoided(self):
        box = geometry.place_panel((200, 650, 200, 40), 800, 700, 530, 130,
                                   obstacles=[(100, 520, 500, 45)])
        self.assertEqual(box[1], 378)

    def test_oversized_or_offscreen_selection_keeps_panel_reachable(self):
        for selection in ((-100, -100, 2000, 2000), (100, -400, 50, 50), (100, 1000, 50, 50)):
            with self.subTest(selection=selection):
                x, y, width, height = geometry.place_panel(selection, 700, 600, 530, 130)
                self.assertGreaterEqual(x, 8)
                self.assertGreaterEqual(y, 8)
                self.assertLessEqual(x + width, 692)
                self.assertLessEqual(y + height, 592)

    def test_tiny_or_empty_viewport_and_invalid_values(self):
        self.assertEqual(geometry.place_panel((0, 0, 1, 1), 0, 0, 530, 130), (0, 0, 0, 0))
        self.assertEqual(geometry.place_panel((0, 0, 1, 1), 3, 2, 530, 130)[2:], (3, 2))
        with self.assertRaises(ValueError):
            geometry.place_panel((math.nan, 0, 1, 1), 800, 700, 530, 130)


@unittest.skipIf(geometry.QRectF is None, "Qt transform runtime is available inside Krita")
class CanvasTransformTests(unittest.TestCase):
    def setUp(self):
        from PyQt5.QtGui import QTransform
        self.QTransform = QTransform

    def view(self, image_to_flake, flake_to_canvas):
        class FakeView:
            def flakeToImageTransform(self):
                return image_to_flake.inverted()[0]

            def flakeToCanvasTransform(self):
                return flake_to_canvas
        return FakeView()

    def assertRect(self, actual, expected):
        for value, target in zip(geometry._rect_values(actual), geometry._rect_values(expected)):
            self.assertAlmostEqual(value, target, places=6)

    def test_public_transforms_include_zoom_and_pan(self):
        scale = self.QTransform.fromScale(.5, .5)
        offset = self.QTransform.fromTranslate(80, 120)
        rect = geometry.QRectF(100, 40, 200, 80)
        actual = geometry.image_to_canvas_rect(self.view(scale, offset), rect)
        self.assertRect(actual, geometry.QRectF(130, 140, 100, 40))

    def test_rotation_and_mirror_are_not_applied_twice(self):
        scale = self.QTransform.fromScale(.75, .75)
        widget = self.QTransform()
        widget.scale(-1, 1)
        widget.rotate(37)
        widget.translate(50, 80)
        rect = geometry.QRectF(12, 18, 160, 90)
        expected = (scale * widget).mapRect(rect)
        actual = geometry.image_to_canvas_rect(self.view(scale, widget), rect)
        self.assertRect(actual, expected)

    def test_high_dpi_uses_widget_logical_pixels(self):
        # The canvas transform already describes logical pixels. There is no
        # extra screen-ratio factor in an overlay's QWidget coordinates.
        scale = self.QTransform.fromScale(.25, .25)
        widget = self.QTransform.fromTranslate(13.5, 22.5)
        actual = geometry.image_to_canvas_rect(self.view(scale, widget), geometry.QRectF(4, 8, 400, 200))
        self.assertRect(actual, geometry.QRectF(14.5, 24.5, 100, 50))

    def test_singular_transform_hides_anchor(self):
        class BrokenView:
            def flakeToImageTransform(self):
                return self.QTransform(0, 0, 0, 0, 0, 0)
        view = BrokenView()
        view.QTransform = self.QTransform
        self.assertIsNone(geometry.image_to_canvas_rect(view, geometry.QRectF(0, 0, 10, 10)))


@unittest.skipIf(geometry.QRectF is None, "Qt anchoring runtime is available inside Krita")
class NonBlockingSelectionTests(unittest.TestCase):
    def setUp(self):
        from PyQt5.QtGui import QTransform
        if geometry.QApplication.instance() is None:
            self.skipTest("Run this suite with Krita's QApplication")
        self.locked = False
        self.available = True
        self.order = []
        self.selection = mock.Mock()

        def selected_value(name, value):
            def read():
                self.assertTrue(self.locked, "Selection getter must hold the image barrier")
                self.order.append(name)
                return value
            return read

        for name, value in (("width", 120), ("height", 80), ("x", 30), ("y", 40)):
            getattr(self.selection, name).side_effect = selected_value(name, value)

        def acquire():
            self.order.append("tryBarrierLock")
            if self.available:
                self.locked = True
            return self.available

        def release():
            self.assertTrue(self.locked, "Only the acquired barrier may be released")
            self.locked = False
            self.order.append("unlock")

        def read_selection():
            self.assertTrue(self.locked)
            self.order.append("selection")
            return self.selection

        root = SimpleNamespace(uniqueId=lambda: SimpleNamespace(toString=lambda: "document-one"))
        self.document = SimpleNamespace(
            tryBarrierLock=mock.Mock(side_effect=acquire),
            unlock=mock.Mock(side_effect=release),
            selection=mock.Mock(side_effect=read_selection),
            width=lambda: 800, height=lambda: 600, rootNode=lambda: root,
            name=lambda: "Synthetic canvas", fileName=lambda: "",
            waitForDone=mock.Mock(side_effect=AssertionError("Blocking image wait")))
        self.view = SimpleNamespace(visible=lambda: True, document=lambda: self.document,
                                    flakeToImageTransform=lambda: QTransform(),
                                    flakeToCanvasTransform=lambda: QTransform())
        self.main = SimpleNamespace(isMinimized=lambda: False)
        self.window = SimpleNamespace(activeView=lambda: self.view, qwindow=lambda: self.main)
        self.application = SimpleNamespace(activeWindow=lambda: self.window)
        self.canvas = SimpleNamespace(rect=lambda: geometry.QRectF(0, 0, 800, 600))
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.lookup = patches.enter_context(mock.patch.object(geometry, "find_canvas_for_window", return_value=self.canvas))
        patches.enter_context(mock.patch.object(geometry, "_toolbar_rect", return_value=None))
        patches.enter_context(mock.patch.object(geometry.QApplication, "activeModalWidget", return_value=None))

    def test_busy_image_skips_selection_getters_without_waiting_or_unlocking(self):
        self.available = False
        with self.assertRaises(geometry.SelectionBusy):
            geometry.locate_selection(self.application)
        self.document.selection.assert_not_called()
        self.selection.width.assert_not_called()
        self.selection.height.assert_not_called()
        self.selection.x.assert_not_called()
        self.selection.y.assert_not_called()
        self.document.waitForDone.assert_not_called()
        self.document.unlock.assert_not_called()
        self.assertEqual(self.order, ["tryBarrierLock"])

    def test_success_releases_barrier_before_canvas_transform_and_toolbar(self):
        original = geometry.image_to_canvas_rect

        def unlocked_transform(view, rect):
            self.assertFalse(self.locked)
            self.order.append("transform")
            return original(view, rect)

        with mock.patch.object(geometry, "image_to_canvas_rect", side_effect=unlocked_transform):
            anchor = geometry.locate_selection(self.application)
        self.assertEqual(geometry._rect_values(anchor.selection_rect), (30, 40, 120, 80))
        self.assertIs(anchor.parent, self.canvas)
        self.document.unlock.assert_called_once_with()
        self.document.waitForDone.assert_not_called()
        self.assertLess(self.order.index("unlock"), self.order.index("transform"))

    def test_no_selection_and_empty_selection_still_release_the_acquired_barrier(self):
        for selected in (None, self.selection):
            with self.subTest(no_selection=selected is None):
                self.document.selection.side_effect = None
                self.document.selection.return_value = selected
                self.selection.width.side_effect = lambda: 0
                self.document.unlock.reset_mock()
                self.assertIsNone(geometry.locate_selection(self.application))
                self.assertFalse(self.locked)
                self.document.unlock.assert_called_once_with()

    def test_deleted_selection_or_invalid_transform_cannot_leave_the_image_locked(self):
        self.selection.width.side_effect = RuntimeError("wrapped C++ selection was deleted")
        self.assertIsNone(geometry.locate_selection(self.application))
        self.assertFalse(self.locked)
        self.document.unlock.assert_called_once_with()
        self.selection.width.side_effect = lambda: 120
        self.document.unlock.reset_mock()
        with mock.patch.object(geometry, "image_to_canvas_rect", return_value=None):
            self.assertIsNone(geometry.locate_selection(self.application))
        self.assertFalse(self.locked)
        self.document.unlock.assert_called_once_with()

    def test_changed_active_view_reads_only_its_current_document_and_canvas(self):
        first = geometry.locate_selection(self.application)
        other_canvas = SimpleNamespace(rect=lambda: geometry.QRectF(0, 0, 800, 600))
        self.lookup.return_value = other_canvas
        self.view = SimpleNamespace(visible=lambda: True, document=lambda: self.document,
                                    flakeToImageTransform=first.view.flakeToImageTransform,
                                    flakeToCanvasTransform=first.view.flakeToCanvasTransform)
        second = geometry.locate_selection(self.application)
        self.assertIsNot(second.view, first.view)
        self.assertIs(second.parent, other_canvas)
        self.assertFalse(self.locked)
        self.assertEqual(self.document.unlock.call_count, 2)

    def test_view_closing_before_probe_never_acquires_the_image_barrier(self):
        self.window.activeView = lambda: None
        self.assertIsNone(geometry.locate_selection(self.application))
        self.document.tryBarrierLock.assert_not_called()
        self.document.selection.assert_not_called()
        self.document.unlock.assert_not_called()

    def test_missing_nonblocking_api_never_falls_back_to_unsafe_selection_getters(self):
        self.document.tryBarrierLock = None
        with self.assertRaises(geometry.SelectionBusy):
            geometry.locate_selection(self.application)
        self.document.selection.assert_not_called()
        self.document.unlock.assert_not_called()
        self.document.waitForDone.assert_not_called()


if __name__ == "__main__":
    unittest.main()
