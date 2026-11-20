"""Visible workspace regressions in real Qt; no model calls or user documents."""
import importlib
from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    from PyQt5 import sip
    from PyQt5.QtCore import QBuffer, QIODevice, QPoint, QPointF, Qt, QEvent, QAbstractAnimation
    from PyQt5.QtGui import QImage, QColor, QWheelEvent, QMouseEvent
    from PyQt5.QtWidgets import QApplication, QAbstractSlider, QSlider, QStyleOptionSlider, QStyle
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "Run layout checks in official kritarunner")
class RealtimeLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        package = ModuleType("mori_layout_fixture")
        package.__path__ = [str(Path(__file__).resolve().parents[1] / "mori_krita")]
        sys.modules[package.__name__] = package
        cls.module = importlib.import_module(package.__name__ + ".realtime_panel")

    def setUp(self):
        self.panel = self.module.RealtimePanel()
        self.panel.setStyleSheet(self.module.MORI_STYLE)
        self.panel.resize(420, 920)
        self.panel.show()
        self.pump()

    def tearDown(self):
        if self.panel._large_dialog is not None:
            sip.delete(self.panel._large_dialog)
        sip.delete(self.panel)
        self.pump()

    def pump(self):
        for _ in range(3):
            self.app.processEvents()

    def result(self, width=1024, height=1536, color="#986bd8"):
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(QColor(color))
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        image.save(buffer, "PNG")
        self.panel.set_result(bytes(buffer.data()), 2.5)
        self.pump()

    def test_preview_owns_most_height_and_stays_visible_at_small_sizes(self):
        self.assertGreater(self.panel.preview.height(), self.panel.height() * .60)
        self.assertFalse(self.panel.controls_scroll.isAncestorOf(self.panel.preview))
        self.assertEqual(self.panel.controls_scroll.verticalScrollBar().maximum(), 0)
        self.panel.resize(360, 600)
        self.pump()
        self.assertGreater(self.panel.preview.height(), 240)
        self.assertTrue(self.panel.preview.visibleRegion().boundingRect().contains(self.panel.preview.rect()))
        self.assertTrue(self.panel.generate.isVisible())

    def test_portrait_and_landscape_fill_available_space_after_resize(self):
        for width, height in ((1024, 1536), (1536, 1024)):
            self.result(width, height)
            for dock_width, dock_height in ((420, 920), (550, 1080), (360, 650)):
                self.panel.resize(dock_width, dock_height)
                self.pump()
                view = self.panel.preview
                rect = view.image_rect()
                self.assertAlmostEqual(rect.width() / rect.height(), width / height)
                self.assertTrue(abs(rect.width() - view.width()) < 1 or abs(rect.height() - view.height()) < 1)
                self.assertGreaterEqual(rect.left(), -1)
                self.assertGreaterEqual(rect.top(), -1)

    def test_reference_rows_and_options_preserve_sources_and_non_native_parent(self):
        self.panel.set_layers([{"id": "a", "name": "主体"}, {"id": "b", "name": "参考"}], "a", ["b"])
        self.pump()
        self.assertEqual(len(self.panel.reference_rows), 1)
        before = self.panel.preview.height()
        requests = []
        self.panel.settings_requested.connect(lambda: requests.append(True))
        self.panel.show_options()
        self.pump()
        self.assertEqual(requests, [True])
        self.assertEqual(self.panel.preview.height(), before)
        self.assertEqual(self.panel.source_ids(), ("a", ["b"]))
        self.assertFalse(self.panel.testAttribute(Qt.WA_NativeWindow))

    def test_roles_survive_layer_refresh_and_allow_one_layer_for_multiple_purposes(self):
        self.panel.set_layers([{"id": "hidden", "name": "风格图", "visible": False}])
        self.assertEqual(self.panel.source_ids(), ("canvas", []))
        for role in ("style", "palette"):
            selector = self.panel.add_reference(role, "hidden")["layer"]
            self.assertIn("隐藏", selector.currentText())
        self.assertEqual(self.panel.source_ids(), ("canvas", ["hidden"]))
        self.panel.set_layers([{"id": "hidden", "name": "重命名", "visible": False}], "canvas", ["hidden"])
        self.assertEqual({item["role"] for item in self.panel.reference_bindings()}, {"style", "palette"})
        self.assertEqual(len(self.panel.settings()["reference_bindings"]), 2)
        self.panel.set_layers([], "canvas", ["hidden"])
        self.assertEqual(self.panel.reference_bindings(), [])

    def test_start_model_and_add_reference_are_in_prompt_card_above_preview(self):
        model_position = self.panel.model.mapTo(self.panel, QPoint(0, 0))
        prompt_position = self.panel.prompt.mapTo(self.panel, QPoint(0, 0))
        self.assertGreater(model_position.y(), prompt_position.y())
        for widget in (self.panel.model, self.panel.resolution, self.panel.references_toggle, self.panel.generate, self.panel.submit):
            self.assertTrue(self.panel.prompt_card.isAncestorOf(widget))
            self.assertLessEqual(abs(widget.mapTo(self.panel, widget.rect().center()).y()
                                     - self.panel.model.mapTo(self.panel, self.panel.model.rect().center()).y()), 1)
        self.assertLess(self.panel.generate.mapTo(self.panel, QPoint()).y(),
                        self.panel.preview.mapTo(self.panel, QPoint()).y())

    def test_references_are_added_on_demand_and_removed_without_remaining_bindings(self):
        self.panel.set_layers([{"id": "a", "name": "参考"}])
        self.assertFalse(self.panel.references_box.isVisible())
        self.panel.references_toggle.click()
        self.assertEqual(len(self.panel.reference_rows), 1)
        row = self.panel.reference_rows[0]
        self.assertEqual(row["purpose"].currentData(), "identity")
        self.assertEqual(self.panel.reference_bindings(), [])
        row["layer"].setCurrentIndex(row["layer"].findData("a"))
        self.assertEqual(self.panel.reference_bindings(), [{"role": "identity", "layer_id": "a"}])
        row["remove"].click()
        self.assertEqual(self.panel.reference_bindings(), [])
        self.assertFalse(self.panel.references_box.isVisible())

    def test_refresh_keeps_unselected_rows_but_new_document_clears_them(self):
        self.panel.add_reference("style")
        self.panel.set_layers([{"id": "a", "name": "图层"}], reference_ids=[])
        self.assertEqual(len(self.panel.reference_rows), 1)
        self.assertEqual(self.panel.reference_rows[0]["purpose"].currentData(), "style")
        self.panel.set_layers([{"id": "b", "name": "新文档图层"}], reference_ids=[], reset=True)
        self.assertEqual(self.panel.reference_rows, [])

    def test_six_references_scroll_without_collapsing_preview_and_can_be_removed(self):
        self.panel.resize(360, 600)
        for _ in range(6):
            self.panel.references_toggle.click()
        self.pump()
        self.assertEqual(len(self.panel.reference_rows), 6)
        self.assertFalse(self.panel.references_toggle.isEnabled())
        self.assertGreater(self.panel.preview.height(), 240)
        self.assertGreater(self.panel.controls_scroll.verticalScrollBar().maximum(), 0)
        self.panel.remove_reference(self.panel.reference_rows[-1])
        self.assertTrue(self.panel.references_toggle.isEnabled())

    def test_variation_changes_only_commit_on_release(self):
        changes = []
        self.panel.parameters_changed.connect(lambda: changes.append(self.panel.settings()["variation"]))
        self.assertEqual(self.panel.settings()["variation"], 50)
        self.assertFalse(self.panel.variation.hasTracking())
        self.panel.variation.setSliderDown(True)
        self.panel.variation.setSliderPosition(100)
        self.assertEqual(self.panel.variation_value.text(), "100%")
        self.assertEqual(self.panel.settings()["variation"], 50)
        self.assertEqual(changes, [])
        self.panel.variation.setSliderDown(False)
        self.assertEqual(changes, [100])
        self.panel.variation.setValue(0)
        self.assertEqual(changes, [100, 0])
        self.assertEqual(self.panel.variation_value.text(), "0%")

    def test_status_and_connection_actions_stay_visible(self):
        self.panel.set_status("已生成", state="completed")
        self.assertTrue(self.panel.status_row.isVisible())
        self.assertFalse(self.panel.status.isVisible())
        self.assertIn("已完成", self.panel.status_badge.text())
        self.panel.set_status("积分不足", error=True)
        self.assertEqual(self.panel.status_badge.text(), "●  出错")
        self.assertIn("积分不足", self.panel.status_badge.toolTip())
        self.assertEqual(self.panel.status_state, "error")
        self.panel.set_status("已连接", state="ready")
        self.assertFalse(self.panel.status.isVisible())
        self.panel.set_connection_action("login")
        self.assertTrue(self.panel.connection_action.isVisible())
        self.assertTrue(self.panel.status_row.isVisible())
        self.panel.set_connection_action(None)
        self.assertTrue(self.panel.status_row.isVisible())

    def test_status_is_one_line_and_pause_does_not_claim_task_stopped(self):
        self.panel.live.setChecked(True)
        self.panel.set_busy(True)
        self.panel.set_status("云端生成中", state="generating")
        self.panel.set_status("工作台后台通知")
        self.assertEqual(self.panel.status.text(), "云端生成中")
        self.panel.set_pending(True)
        self.panel.live.setChecked(False)
        self.pump()
        self.assertEqual(self.panel.status_badge.text(), "●  生成中")
        self.assertIn("仍在处理", self.panel.status_badge.toolTip())
        self.assertFalse(self.panel.status.isVisible())
        self.assertLessEqual(self.panel.status_row.height(), 34)
        self.assertFalse(hasattr(self.panel, "status_progress"))
        self.assertFalse(hasattr(self.panel, "status_elapsed"))
        self.panel.set_busy(False)
        self.panel.set_status("完成", state="completed")
        self.assertEqual(self.panel.status_badge.text(), "●  已完成")

    def test_detailed_phases_collapse_to_five_simple_labels(self):
        self.assertEqual({label for label, color in self.module.LIVE_STATES.values()},
                         {"未开始", "生成中", "已完成", "已暂停", "出错"})
        for phase in ("preparing", "uploading", "submitting", "queued", "generating", "downloading"):
            self.panel.set_status("详细处理进度", state=phase)
            self.assertEqual(self.panel.status_badge.text(), "●  生成中")

    def test_submit_and_start_fit_narrow_panel_and_disable_during_task(self):
        self.panel.resize(360, 600)
        self.panel.set_available(True)
        self.pump()
        for button in (self.panel.generate, self.panel.submit):
            self.assertTrue(button.visibleRegion().boundingRect().contains(button.rect()))
            self.assertTrue(button.isEnabled())
        self.panel.live.setChecked(True)
        self.panel.set_busy(True)
        self.assertTrue(self.panel.generate.isEnabled())
        self.assertFalse(self.panel.submit.isEnabled())

    def test_status_is_pinned_when_reference_settings_scroll(self):
        self.panel.resize(360, 600)
        for _ in range(6):
            self.panel.add_reference()
        self.panel.set_busy(True)
        self.panel.set_status("正在生成，完成后会自动显示预览。", state="generating")
        self.panel.controls_scroll.verticalScrollBar().setValue(10000)
        self.pump()
        self.assertTrue(self.panel.status_row.visibleRegion().boundingRect().contains(self.panel.status_row.rect()))
        self.assertFalse(self.panel.controls_scroll.isAncestorOf(self.panel.status_row))
        self.assertLess(self.panel.status_row.mapTo(self.panel, QPoint()).y(),
                        self.panel.preview.mapTo(self.panel, QPoint()).y())
        self.assertGreater(self.panel.preview.height(), 200)

    def test_variation_keyboard_and_accessibility_setters_use_five_percent_stops(self):
        slider = self.panel.variation
        self.assertEqual(slider.singleStep(), 5)
        self.assertEqual(slider.pageStep(), 5)
        slider.triggerAction(QAbstractSlider.SliderSingleStepAdd)
        self.assertEqual(self.panel.settings()["variation"], 55)
        slider.triggerAction(QAbstractSlider.SliderPageStepSub)
        self.assertEqual(self.panel.settings()["variation"], 50)
        slider.setValue(42)
        self.assertEqual(self.panel.settings()["variation"], 40)
        QSlider.setValue(slider, 43)
        self.assertEqual(self.panel.settings()["variation"], 45)
        self.assertEqual(self.panel.variation_value.text(), "45%")

    def test_mouse_drag_snaps_to_five_percent_without_mid_drag_submissions(self):
        slider = self.panel.variation
        option = QStyleOptionSlider()
        slider.initStyleOption(option)
        handle = slider.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, slider)
        start = QPointF(handle.center())
        changes = []
        self.panel.parameters_changed.connect(lambda: changes.append(self.panel.settings()["variation"]))
        slider.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, start, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
        for x in (slider.width() * .63, slider.width() * .74, slider.width() * .89):
            end = QPointF(x, start.y())
            slider.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, end, Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
            self.assertEqual(slider.sliderPosition() % 5, 0)
            self.assertEqual(int(self.panel.variation_value.text().rstrip("%")) % 5, 0)
            self.assertEqual(self.panel.settings()["variation"], 50)
            self.assertEqual(changes, [])
        slider.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, end, Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
        self.assertEqual(len(changes), 1)
        self.assertGreater(changes[0], 75)

    def test_slider_motion_never_submits_parameters_and_finishes_without_a_loop(self):
        slider = self.panel.variation
        slider._motion_enabled = True
        changes = []
        self.panel.parameters_changed.connect(lambda: changes.append(True))
        slider._play_flow()
        self.assertEqual(slider._flow_animation.state(), QAbstractAnimation.Running)
        slider._flow_animation.setCurrentTime(550)
        self.pump()
        self.assertEqual(changes, [])
        self.assertEqual(self.panel.settings()["variation"], 50)
        slider._flow_animation.setCurrentTime(slider._flow_animation.duration())
        self.assertEqual(slider._flow_animation.state(), QAbstractAnimation.Stopped)
        self.assertEqual(changes, [])

    def test_hidden_slider_stops_motion_and_reduced_motion_prevents_it(self):
        slider = self.panel.variation
        slider._motion_enabled = True
        slider._play_flow()
        self.panel.hide()
        self.pump()
        self.assertEqual(slider._flow_animation.state(), QAbstractAnimation.Stopped)
        self.panel.show()
        self.pump()
        slider._flow_animation.stop()
        self.app.setProperty("moriReduceMotion", True)
        try:
            slider._play_flow()
            self.assertEqual(slider._flow_animation.state(), QAbstractAnimation.Stopped)
        finally:
            self.app.setProperty("moriReduceMotion", None)

    def test_start_pause_and_single_generation_are_distinct_even_during_a_request(self):
        auto, single = [], []
        self.panel.auto_changed.connect(auto.append)
        self.panel.generate_requested.connect(lambda: single.append(True))
        self.panel.set_available(True)
        self.panel.generate.click()
        self.assertTrue(self.panel.live.isChecked())
        self.assertEqual(auto, [True])
        self.assertEqual(single, [])
        self.panel.set_busy(True)
        self.assertTrue(self.panel.generate.isEnabled())
        self.assertFalse(self.panel.regenerate.isEnabled())
        self.panel.generate.click()
        self.assertFalse(self.panel.live.isChecked())
        self.assertEqual(auto, [True, False])
        self.assertFalse(self.panel.generate.isEnabled())
        self.panel.set_busy(False)
        self.panel.regenerate.click()
        self.assertEqual(single, [True])
        self.assertFalse(self.panel.live.isChecked())

    def test_source_comparison_preserves_the_latest_result(self):
        source = QImage(1024, 1536, QImage.Format_ARGB32)
        source.fill(QColor("#d9d5c8"))
        self.panel.set_source_preview(source)
        self.result()
        self.panel.compare.setChecked(True)
        self.assertEqual(self.panel.preview._image.pixelColor(0, 0).name(), "#d9d5c8")
        self.result(color="#35ae95")
        self.assertEqual(self.panel.preview._image.pixelColor(0, 0).name(), "#d9d5c8")
        self.panel.compare.setChecked(False)
        self.assertEqual(self.panel.preview._image.pixelColor(0, 0).name(), "#35ae95")
        self.panel.clear_result()
        self.assertFalse(self.panel.compare.isEnabled())
        self.assertIsNone(self.panel._source_image)

    def test_zoom_is_preserved_across_live_results_and_reset_fits(self):
        self.result()
        view = self.panel.preview
        wheel = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(), QPoint(0, 120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        view.wheelEvent(wheel)
        self.assertGreater(view._zoom, 1)
        zoom = view._zoom
        self.result(color="#35ae95")
        self.assertEqual(view._zoom, zoom)
        view.reset_view()
        self.assertEqual(view._zoom, 1)
        self.assertEqual(view._pan, QPointF())

    def test_large_view_receives_new_results_and_clears_with_document(self):
        self.result()
        self.panel.open_large_preview()
        self.pump()
        self.assertFalse(self.panel._large_dialog.isModal())
        self.assertGreater(self.panel._large_preview.height(), self.panel.preview.height())
        self.result(color="#35ae95")
        self.assertEqual(self.panel._large_preview._image.pixelColor(0, 0).name(), "#35ae95")
        self.panel.clear_result()
        self.assertIsNone(self.panel._large_preview._image)
        self.assertFalse(self.panel.expand.isEnabled())

    def test_empty_view_and_edges_are_dark_before_any_result_or_movement(self):
        image = self.panel.grab().toImage()
        for x, y in ((0, 0), (image.width() - 1, 0), (0, image.height() - 1),
                     (image.width() - 1, image.height() - 1)):
            self.assertLess(image.pixelColor(x, y).lightness(), 70)
        preview = self.panel.preview.grab().toImage()
        self.assertEqual(preview.pixelColor(0, 0).name(), "#17171a")
