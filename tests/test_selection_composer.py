"""Selection composer signals and native rendering; no network or user data."""
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest

try:
    from PyQt5 import sip
    from PyQt5.QtCore import QAbstractAnimation, QEvent, QPointF, Qt
    from PyQt5.QtGui import QHideEvent, QKeyEvent, QMouseEvent, QResizeEvent
    from PyQt5.QtWidgets import QApplication, QWidget
except ImportError:
    QApplication = None


MODULE_PATH = Path(__file__).resolve().parents[1] / "mori_krita" / "selection_composer.py"


def key_click(widget, key, modifiers=Qt.NoModifier if QApplication is not None else None):
    text = "\r" if key in (Qt.Key_Return, Qt.Key_Enter) else ""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, modifiers, text))
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyRelease, key, modifiers, text))


def load_composer():
    package_name = "mori_composer_fixture"
    name = package_name + ".selection_composer"
    if name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(MODULE_PATH.parent)]
        sys.modules[package_name] = package
        spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].SelectionComposer


@unittest.skipIf(QApplication is None, "Native Qt tests run inside Krita's official runner")
class SelectionComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        cls.composer_class = load_composer()

    def setUp(self):
        self.parent = QWidget()
        self.parent.resize(800, 600)
        self.widget = self.composer_class(self.parent)
        self.widget.resize(530, 128)
        self.payloads = []
        self.widget.submitted.connect(self.payloads.append)

    def tearDown(self):
        self.widget.close()
        self.parent.close()
        self.widget.deleteLater()
        self.parent.deleteLater()
        self.application.processEvents()

    def test_payload_contains_current_draft_and_settings(self):
        self.widget.set_message("  修改这个产品的配色为红色  ")
        self.widget.set_settings({"aspect": "3:4", "quality": "high", "mode": "chat", "count": 2})
        self.widget._submit()
        self.assertEqual(len(self.payloads), 1)
        self.assertEqual(self.payloads[0]["message"], "修改这个产品的配色为红色")
        self.assertEqual(self.payloads[0]["aspect"], "3:4")
        self.assertEqual(self.payloads[0]["mode"], "chat")
        self.assertEqual(self.payloads[0]["count"], 2)

    def test_empty_and_oversized_drafts_never_submit(self):
        self.widget._submit()
        self.assertEqual(self.payloads, [])
        self.assertTrue(self.widget._error)
        self.widget.set_message("字" * 32001)
        self.widget._submit()
        self.assertEqual(self.payloads, [])

    def test_collapse_preserves_draft_and_reports_state(self):
        changes = []
        self.widget.collapsedChanged.connect(changes.append)
        self.widget.set_message("保留这份未发送的草稿")
        self.widget.set_collapsed(True)
        self.assertEqual(self.widget.message(), "保留这份未发送的草稿")
        self.assertEqual(self.widget.desired_size(800).height(), 42)
        self.assertEqual(self.widget.height(), 42)
        self.widget.set_collapsed(False)
        self.assertEqual(self.widget.message(), "保留这份未发送的草稿")
        self.assertEqual(changes, [True, False])

    def test_local_shortcuts_submit_or_collapse_without_clearing(self):
        self.widget.set_message("修改选区")
        shortcut = QKeyEvent(QEvent.ShortcutOverride, Qt.Key_Return, Qt.ControlModifier)
        shortcut.ignore()
        QApplication.sendEvent(self.widget.editor, shortcut)
        self.assertTrue(shortcut.isAccepted())
        key_click(self.widget.editor, Qt.Key_Return, Qt.ControlModifier)
        self.assertEqual(len(self.payloads), 1)
        key_click(self.widget.editor, Qt.Key_Escape)
        self.assertTrue(self.widget.is_collapsed())
        self.assertEqual(self.widget.message(), "修改选区")

    def test_normal_enter_is_a_newline(self):
        self.widget.set_message("第一行")
        cursor = self.widget.editor.textCursor()
        cursor.movePosition(cursor.End)
        self.widget.editor.setTextCursor(cursor)
        key_click(self.widget.editor, Qt.Key_Return)
        self.assertEqual(self.payloads, [])
        self.assertIn("\n", self.widget.message())

    def test_busy_blocks_double_submit_and_cancel_fires_once(self):
        cancelled = []
        self.widget.cancelRequested.connect(lambda: cancelled.append(True))
        self.widget.set_message("修改选区")
        self.widget.set_busy(True, "正在生成")
        self.widget._submit()
        self.assertEqual(self.payloads, [])
        self.assertFalse(self.widget.submit_button.isEnabled())
        self.widget._cancel()
        self.widget._cancel()
        self.assertEqual(cancelled, [True])
        self.widget.set_busy(False)
        self.widget._submit()
        self.assertEqual(len(self.payloads), 1)

    def test_agent_render_is_explicitly_an_image_request(self):
        self.widget.set_settings({"mode": "chat"})
        self.widget.set_message("将选区材质改成银色金属")
        self.assertEqual(self.widget.submit_button.text(), "智能体改图")
        self.widget._submit()
        self.assertEqual(self.payloads[-1]["mode"], "chat")
        self.assertIs(self.payloads[-1]["expect_image"], True)
        self.widget.set_busy(True)
        self.assertEqual(self.widget.submit_button.text(), "处理中")
        self.widget.set_busy(False)
        self.assertEqual(self.widget.submit_button.text(), "智能体改图")
        self.widget.set_settings({"mode": "edit"})
        self.assertEqual(self.widget.submit_button.text(), "渲染")

    def test_phase_timings_are_visible_without_changing_overlay_height(self):
        self.widget.set_status("结果已写入图层 · 共 23.0 秒")
        before = self.widget.height()
        self.widget.set_timing_details("上传参考图：0.50 秒\n服务器/智能体等待：22.10 秒")
        self.assertIn("服务器/智能体等待：22.10 秒", self.widget._status_label.toolTip())
        self.assertEqual(self.widget.height(), before)

    def test_width_adapts_and_quote_is_never_invented(self):
        self.assertEqual(self.widget._quote, "")
        self.assertEqual(self.widget.desired_size(380).width(), 360)
        self.assertEqual(self.widget.desired_size(1000).width(), 530)
        self.widget.set_quote("本次预计 0.4 积分 · 可用 10.5 积分")
        self.assertEqual(self.widget._quote_label.text(), "本次预计 0.4 积分 · 可用 10.5 积分")

    def test_models_and_parameter_menu_use_real_configuration(self):
        self.widget.set_models({"models": [{"id": "test-model", "name": "测试模型", "provider": "test"}], "configured": {"test": True}})
        self.assertEqual(self.widget.settings()["model"], "test-model")
        self.widget._populate_models()
        self.assertEqual(self.widget._model_menu.actions()[0].text(), "测试模型")
        self.widget._populate_parameters()
        self.assertIn("交互方式", [action.text() for action in self.widget._parameter_menu.actions()])

    def test_reveal_only_clips_painting_and_keeps_layout_and_draft(self):
        self.widget._animations_enabled = lambda: True
        self.widget.set_message("继续修改这一选区")
        self.widget.hide()
        bounds = self.widget.geometry()
        self.widget.reveal()
        self.assertFalse(self.widget.isHidden())
        self.assertEqual(self.widget._reveal_animation.state(), QAbstractAnimation.Running)
        self.widget._reveal_animation.setCurrentTime(60)
        self.assertGreater(self.widget.mask().boundingRect().height(), 8)
        self.assertLess(self.widget.mask().boundingRect().height(), self.widget.height())
        self.assertEqual(self.widget.geometry(), bounds)
        self.assertEqual(self.widget.message(), "继续修改这一选区")
        self.widget._reveal_animation.setCurrentTime(140)
        self.assertTrue(self.widget.mask().isEmpty())
        self.assertEqual(self.widget.geometry(), bounds)

    def test_native_effect_setting_and_reveal_work_without_a_test_override(self):
        # Exercise the real Qt enum/API as well as the forced-animation paths.
        self.widget.hide()
        enabled = self.widget._animations_enabled()
        self.assertIsInstance(enabled, bool)
        self.widget.reveal()
        self.assertFalse(self.widget.isHidden())
        if enabled:
            self.assertEqual(self.widget._reveal_animation.state(), QAbstractAnimation.Running)
            self.widget._reveal_animation.setCurrentTime(140)
        self.assertTrue(self.widget.mask().isEmpty())

    def test_reveal_does_not_restart_for_an_already_visible_card(self):
        self.widget._animations_enabled = lambda: True
        self.widget.hide()
        self.widget.reveal()
        self.widget._reveal_animation.setCurrentTime(60)
        before = self.widget._reveal_animation.currentTime()
        self.widget.reveal()
        self.assertEqual(self.widget._reveal_animation.currentTime(), before)

    def test_hide_cancels_animation_and_next_reveal_opens_cleanly(self):
        self.widget._animations_enabled = lambda: True
        self.parent.show()
        self.widget.hide()
        self.widget.reveal()
        self.widget._reveal_animation.setCurrentTime(40)
        self.widget.hide()
        self.assertEqual(self.widget._reveal_animation.state(), QAbstractAnimation.Stopped)
        self.assertTrue(self.widget.mask().isEmpty())
        self.widget.reveal()
        self.assertEqual(self.widget._reveal_animation.currentTime(), 0)

    def test_reveal_preserves_folded_state_and_does_not_take_focus(self):
        self.widget._animations_enabled = lambda: True
        self.widget.set_message("保留折叠草稿")
        self.widget.set_collapsed(True)
        self.widget.hide()
        focused = self.application.focusWidget()
        self.widget.reveal()
        self.widget._reveal_animation.setCurrentTime(140)
        self.assertTrue(self.widget.is_collapsed())
        self.assertEqual(self.widget.height(), 42)
        self.assertEqual(self.widget.message(), "保留折叠草稿")
        self.assertIs(self.application.focusWidget(), focused)

    def test_disabled_system_effects_show_without_an_animation(self):
        self.widget._animations_enabled = lambda: False
        self.widget.hide()
        self.widget.reveal()
        self.assertFalse(self.widget.isHidden())
        self.assertEqual(self.widget._reveal_animation.state(), QAbstractAnimation.Stopped)
        self.assertTrue(self.widget.mask().isEmpty())

    def test_blank_card_mouse_press_does_not_propagate_into_canvas(self):
        received = []

        class Canvas(QWidget):
            def mousePressEvent(self, event):
                received.append(event.button())
                event.accept()

        canvas = Canvas()
        composer = self.composer_class(canvas)
        try:
            event = QMouseEvent(QEvent.MouseButtonPress, QPointF(1, 1),
                                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            QApplication.sendEvent(composer._card, event)
            self.assertEqual(received, [])
        finally:
            composer.close()
            canvas.close()
            canvas.deleteLater()

    def test_deleted_animation_does_not_break_cancel_hide_resize_or_reveal(self):
        sip.delete(self.widget._reveal_animation)
        self.assertTrue(sip.isdeleted(self.widget._reveal_animation))
        self.widget.cancel_reveal()
        self.widget.hideEvent(QHideEvent())
        self.widget.resizeEvent(QResizeEvent(self.widget.size(), self.widget.size()))
        self.widget._animations_enabled = lambda: True
        self.widget.hide()
        self.widget.reveal()
        self.assertFalse(self.widget.isHidden())
        self.assertTrue(self.widget.mask().isEmpty())

    def test_cancel_on_deleted_composer_does_not_touch_native_widgets(self):
        composer = self.composer_class()
        sip.delete(composer)
        self.assertTrue(sip.isdeleted(composer))
        composer.cancel_reveal()
        composer.reveal()


def main(arguments):
    """Official kritarunner entry: run Qt tests and save synthetic panel renders."""
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(SelectionComposerTests)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors)}
    (output / "composer-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    composer = load_composer()()
    composer.set_message("修改这个产品的配色为红色")
    composer.set_settings({"resolution": "1K", "aspect": "3:4", "quality": "high", "count": 1})
    composer.set_quote("本次预计 0.4 积分 · 可用 10.5 积分")
    composer.resize(composer.desired_size(550))
    composer.ensurePolished()
    composer.layout().activate()
    application.processEvents()
    composer.grab().save(str(output / "composer-expanded.png"), "PNG")
    composer.set_quote("")
    composer.resize(composer.desired_size(550))
    composer.layout().activate()
    application.processEvents()
    composer.grab().save(str(output / "composer-delivery.png"), "PNG")
    composer.set_collapsed(True)
    composer.resize(composer.desired_size(550))
    composer.layout().activate()
    application.processEvents()
    composer.grab().save(str(output / "composer-collapsed.png"), "PNG")
    composer.close()
    (output / "composer-qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    unittest.main()
