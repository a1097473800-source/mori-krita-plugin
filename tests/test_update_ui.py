"""Real Qt update entry/dialog and worker lifecycle; all requests are offline."""
import importlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import test_selection_controller as fixture

docker_module = None
if fixture.QWidget is not None:
    from PyQt5 import sip
    docker_module = importlib.import_module("mori_controller_fixture.docker")


@unittest.skipIf(docker_module is None, "Use Krita's Python/Qt runtime")
class UpdateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SelectionControllerTests.setUpClass()

    def setUp(self):
        self.window = fixture.QWidget()
        with mock.patch.object(docker_module, "QSettings", lambda *_: fixture.MemorySettings()):
            self.dock = docker_module.MoriDocker()
        self.dock.setParent(self.window)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.dock.shutdown()
        if docker_module.is_alive(self.window):
            sip.delete(self.window)

    def result(self, version="0.5.0"):
        module = importlib.import_module("mori_controller_fixture.plugin_updates")
        release = module.PluginRelease(version,
            "https://github.com/" + module.REPOSITORY + "/releases/tag/v" + version,
            "https://github.com/" + module.REPOSITORY + "/releases/download/v" + version + "/update.zip",
            "改进更新提示")
        return module.PluginUpdateResult(release, "发现插件新版本 " + version, True, True)

    def capture_workers(self):
        self.workers = []
        def background(function, done=None):
            self.workers.append((function, done))
        self.gateway = mock.Mock()
        self.gateway.client.base_url = "https://qa.example.invalid"
        self.gateway.web_root = Path("bundled")
        self.dock._gateway = self.gateway
        patch = mock.patch.object(self.dock, "_background", side_effect=background)
        patch.start()
        self.addCleanup(patch.stop)

    def test_update_entry_is_available_in_both_modes(self):
        for index in (0, 1):
            with mock.patch.object(self.dock, "_start"), mock.patch.object(self.dock._realtime, "set_active"):
                self.dock._set_mode(index)
            self.assertFalse(self.dock._settings_entry.isHidden())
        self.assertEqual([action.text() for action in self.dock._mode_button.menu().actions()],
                         ["实时模式", "工作台模式"])
        self.assertEqual(self.dock._settings_entry.text(), "设置")

    def test_background_check_in_realtime_does_not_open_workbench_or_dialog(self):
        self.capture_workers()
        with mock.patch.object(self.dock._web, "start") as start:
            self.dock._automatic_update_check()
            self.assertEqual(len(self.workers), 2)
            self.assertIsNone(self.dock._settings_dialog)
            start.assert_not_called()
        self.assertEqual(self.dock._update_timer.interval(), 6 * 60 * 60 * 1000)

    def test_automatic_checks_can_be_disabled_but_manual_check_remains(self):
        self.dock._settings.setValue("automaticUpdates", False)
        with mock.patch.object(self.dock, "_check_all_updates") as check:
            self.dock._automatic_update_check()
            check.assert_not_called()
        self.capture_workers()
        self.dock._check_all_updates()
        self.assertEqual(len(self.workers), 2)

    def test_new_release_sets_persistent_entry_without_using_task_status(self):
        self.capture_workers()
        with mock.patch.object(self.dock, "_status") as status:
            self.dock._check_all_updates()
            with mock.patch.object(docker_module, "PluginUpdateManager") as manager:
                manager.return_value.check.return_value = self.result()
                function, done = self.workers[0]
                function()
                done()
            self.assertEqual(self.dock._settings_entry.text(), "设置")
            self.assertTrue(self.dock._settings_entry.available)
            status.assert_not_called()

    def test_cancelled_native_worker_cannot_show_old_release(self):
        self.capture_workers()
        self.dock._check_all_updates()
        with mock.patch.object(docker_module, "PluginUpdateManager") as manager:
            manager.return_value.check.return_value = self.result()
            function, done = self.workers[0]
            function()
        self.dock._plugin_update_cancel.set()
        done()
        self.assertIsNone(self.dock._plugin_release)

    def test_shutdown_discards_late_callbacks_and_stops_update_timer(self):
        self.capture_workers()
        self.dock._check_all_updates()
        function, done = self.workers[0]
        with mock.patch.object(docker_module, "PluginUpdateManager") as manager:
            manager.return_value.check.return_value = self.result()
            function()
        self.dock.shutdown()
        sip.delete(self.window)
        done()
        self.assertTrue(self.dock._plugin_update_cancel.is_set())

    def test_switching_to_workbench_during_automatic_check_opens_when_ready(self):
        self.capture_workers()
        self.dock._check_all_updates()
        self.dock._begin_web_update(self.gateway, open_after=True)
        function, done = self.workers[1]
        with mock.patch.object(docker_module, "WebUpdateManager") as manager:
            manager.return_value.check.return_value = SimpleNamespace(
                release=SimpleNamespace(root=Path("verified"), ui_version="20261120-verified"),
                message="网页已更新", updated=True)
            function()
        with mock.patch.object(self.dock._web, "start") as start:
            done()
            start.assert_called_once_with(self.gateway.url)
        self.assertEqual(self.gateway.web_root, Path("verified"))

    def test_active_workbench_keeps_its_current_files_until_restart(self):
        self.capture_workers()
        self.dock._web_started = True
        self.dock._check_all_updates()
        function, done = self.workers[1]
        with mock.patch.object(docker_module, "WebUpdateManager") as manager:
            manager.return_value.check.return_value = SimpleNamespace(
                release=SimpleNamespace(root=Path("next"), ui_version="20261120-next"),
                message="网页已更新", updated=True)
            function()
        with mock.patch.object(self.dock._web, "start") as start:
            done()
            start.assert_not_called()
        self.assertEqual(self.gateway.web_root, Path("bundled"))
        self.assertTrue(self.dock._web_update_available)
        self.assertIn("重开 Krita", self.dock._web_update_message)

    def test_dialog_uses_plain_text_and_downloads_only_when_newer(self):
        module = importlib.import_module("mori_controller_fixture.update_dialog")
        dialog = module.UpdatePage(docker_module.VERSION, True, self.window)
        result = self.result()
        dialog.render(result, result.release, "20261120-current", "网页已是最新兼容版本", False, "2026-11-20 15:00")
        self.assertTrue(dialog.download.isEnabled())
        self.assertIn("0.5.0", dialog.latest.text())
        self.assertEqual(dialog.notes.toPlainText(), "改进更新提示")
        self.assertEqual(dialog.native_status.textFormat(), docker_module.Qt.PlainText)
        dialog.render(None, None, "内置网页", "离线", True, "")
        self.assertFalse(dialog.download.isEnabled())
        self.assertFalse(dialog.check.isEnabled())
