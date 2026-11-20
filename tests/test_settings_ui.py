"""Preferences exercise the actual Qt controls and persistent plugin behavior."""
import importlib
import unittest
from unittest import mock

import test_selection_controller as fixture

docker_module = None
if fixture.QWidget is not None:
    from PyQt5 import sip
    docker_module = importlib.import_module("mori_controller_fixture.docker")


@unittest.skipIf(docker_module is None, "Use Krita's Python/Qt runtime")
class SettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SelectionControllerTests.setUpClass()

    def setUp(self):
        self.window = fixture.QWidget()
        self.settings = fixture.MemorySettings()
        with mock.patch.object(docker_module, "QSettings", lambda *_: self.settings):
            self.dock = docker_module.MoriDocker()
        self.dock.setParent(self.window)
        self.dock._settings_entry.click()
        self.dialog = self.dock._settings_dialog
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.dock.shutdown()
        if docker_module.is_alive(self.window):
            sip.delete(self.window)

    def test_gear_opens_independent_reusable_window_with_three_categories(self):
        self.assertTrue(self.dialog.isWindow())
        self.assertTrue(self.dialog.isVisible())
        self.assertEqual(list(self.dialog.navigation), ["canvas", "realtime", "updates"])
        self.dialog.done.click()
        self.assertFalse(self.dialog.isVisible())
        self.dock._settings_entry.click()
        self.assertIs(self.dock._settings_dialog, self.dialog)

    def test_settings_has_no_server_or_account_controls_and_mode_menu_only_switches_modes(self):
        self.assertEqual([action.text() for action in self.dock._mode_button.menu().actions()],
                         ["实时模式", "工作台模式"])
        texts = [item.text() for item in self.dialog.findChildren(docker_module.QLabel)]
        self.assertFalse(any("服务器" in text or "登录" in text for text in texts))

    def test_canvas_preferences_drive_transfer_and_persist(self):
        gateway = mock.Mock()
        self.dock._gateway = gateway
        self.dialog.reference_cards[1].click()
        self.dialog.transfer_source.setCurrentIndex(self.dialog.transfer_source.findData("selection"))
        self.dialog.auto_import.click()
        self.assertEqual(self.dock._reference_mode.currentData(), "original")
        self.assertEqual(self.dock._source.currentData(), "selection")
        self.assertFalse(self.dock._auto.isChecked())
        self.assertFalse(gateway.auto_import)
        self.assertEqual(self.settings.value("referenceMode"), "original")
        self.assertEqual(self.settings.value("transferSource"), "selection")
        self.assertFalse(self.settings.value("autoImport"))

    def test_toolbar_and_document_layer_changes_refresh_the_open_settings(self):
        self.dock._source.setCurrentIndex(3)
        self.assertEqual(self.dialog.transfer_source.currentData(), "canvas")
        panel = self.dock._realtime_panel
        panel.set_layers([{"id": "ink", "name": "线稿"}], "ink")
        self.assertEqual(self.dialog.subject.currentData(), "ink")
        panel.set_layers([], reset=True)
        self.assertEqual(self.dialog.subject.currentData(), "canvas")
        self.assertEqual(self.dialog.subject.count(), 1)

    def test_quality_tracks_supported_model_options_and_recovers_saved_preference(self):
        panel = self.dock._realtime_panel
        panel.set_models([{"id": "gpt-image-test", "name": "Image", "qualities": ["low", "medium", "high"]}])
        self.assertTrue(self.dialog.quality.isEnabled())
        self.dialog.quality.setCurrentIndex(2)
        self.assertEqual(panel.settings()["quality"], "high")
        self.assertEqual(self.settings.value("realtimeQuality"), "high")
        panel.set_models([{"id": "banana", "name": "Banana"}])
        self.assertFalse(self.dialog.quality.isEnabled())
        self.assertEqual(self.dialog.quality.currentText(), "模型默认")
        panel.set_models([{"id": "gpt-image-test", "name": "Image"}])
        self.assertEqual(self.dialog.quality.currentData(), "high")

    def test_delay_writeback_and_selection_are_live_and_restored_on_next_launch(self):
        self.dialog.delay_buttons[2].click()
        self.dialog.writeback.click()
        self.dialog.selection.click()
        self.assertEqual(self.dock._realtime_panel.delay.currentData(), 4.0)
        self.assertTrue(self.dock._realtime_panel.writeback.isChecked())
        self.assertFalse(self.dock._selection.enabled)
        self.assertFalse(self.dock._realtime_panel.live.isChecked())
        with mock.patch.object(docker_module, "QSettings", lambda *_: self.settings):
            second = docker_module.MoriDocker()
        try:
            self.assertEqual(second._realtime_panel.delay.currentData(), 4.0)
            self.assertTrue(second._realtime_panel.writeback.isChecked())
            self.assertFalse(second._selection.enabled)
        finally:
            second.shutdown()
            sip.delete(second)

    def test_reset_is_limited_to_the_current_page(self):
        self.dialog.reference_cards[1].click()
        self.dialog.writeback.click()
        self.dialog.delay_buttons[2].click()
        self.dialog.set_page("realtime")
        self.dialog.reset.click()
        self.assertEqual(self.dock._reference_mode.currentData(), "original")
        self.assertFalse(self.dialog.writeback.isChecked())
        self.assertTrue(self.dialog.delay_buttons[1].isChecked())

    def test_updates_share_settings_and_background_callbacks_keep_the_gear_usable(self):
        with mock.patch.object(self.dock, "_check_all_updates") as check:
            self.dialog.navigation["updates"].click()
            check.assert_called_once()
            self.assertIs(self.dock._settings_dialog, self.dialog)
        self.dock._plugin_update_pending = True
        self.dock._render_updates()
        self.assertTrue(self.dock._settings_entry.isEnabled())
        self.assertFalse(self.dialog.updates.check.isEnabled())
        self.dialog.updates.automatic.click()
        self.assertFalse(self.settings.value("automaticUpdates"))

    def test_hiding_or_deleting_settings_during_update_is_safe(self):
        self.dialog.close()
        self.dock._render_updates()
        self.assertFalse(self.dialog.isVisible())
        sip.delete(self.dialog)
        self.dock._render_updates()
        self.dock._show_settings()
        self.assertIsNot(self.dock._settings_dialog, self.dialog)
        self.assertTrue(self.dock._settings_dialog.isVisible())
