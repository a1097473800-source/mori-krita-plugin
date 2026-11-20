"""Real Qt destruction with offline workers; no user windows or documents."""
import concurrent.futures
import importlib
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import test_selection_controller as fixture

docker_module = None
if fixture.QWidget is not None:
    from PyQt5 import sip
    from PyQt5.QtCore import Qt
    docker_module = importlib.import_module("mori_controller_fixture.docker")


class ManualPool:
    def __init__(self):
        self.future = concurrent.futures.Future()
        self.closed = False

    def submit(self, function):
        return self.future

    def shutdown(self, **kwargs):
        self.closed = True


@unittest.skipIf(docker_module is None, "Run native destruction tests inside Krita")
class DockerLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.SelectionControllerTests.setUpClass()

    def setUp(self):
        self.window = fixture.QWidget()
        with mock.patch.object(docker_module, "QSettings", lambda *args: fixture.MemorySettings()):
            self.docker = docker_module.MoriDocker()
        self.docker.setParent(self.window)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.docker.shutdown()
        if docker_module.is_alive(self.window):
            sip.delete(self.window)

    def attach_gateway(self):
        closed = threading.Event()
        gateway = mock.Mock()
        gateway.close.side_effect = closed.set
        self.docker._gateway = gateway
        return gateway, closed

    def test_installed_selection_assistant_remains_available_after_update(self):
        self.assertTrue(hasattr(self.docker, "_selection"))
        self.assertTrue(self.docker.findChildren(fixture.controller_module.SelectionController))
        self.docker._show_settings("realtime")
        self.assertEqual(self.docker._settings_dialog.selection.isChecked(), self.docker._selection.enabled)

    def test_deleted_realtime_panel_does_not_interrupt_remaining_shutdown(self):
        controller = self.docker._realtime
        sip.delete(self.docker._realtime_panel)
        _, closed = self.attach_gateway()
        self.docker.shutdown()
        self.docker.shutdown()
        self.assertTrue(closed.wait(1))
        self.assertTrue(self.docker._pool._shutdown)
        self.assertTrue(self.docker._web._closed)
        self.assertTrue(controller._closed)

    def test_dock_cpp_destruction_also_closes_non_qt_resources(self):
        _, closed = self.attach_gateway()
        pool = self.docker._pool
        controller = self.docker._realtime
        sip.delete(self.docker)
        self.assertTrue(self.docker._closing)
        self.assertTrue(controller._closed)
        self.assertTrue(pool._shutdown)
        self.assertTrue(closed.wait(1))
        self.docker.shutdown()

    def test_other_resources_close_even_if_a_cleanup_stage_fails(self):
        _, closed = self.attach_gateway()
        with mock.patch.object(self.docker._realtime, "shutdown", side_effect=RuntimeError("stage failure")):
            with self.assertRaisesRegex(RuntimeError, "stage failure"):
                self.docker.shutdown()
        self.assertTrue(self.docker._web._closed)
        self.assertTrue(self.docker._pool._shutdown)
        self.assertTrue(closed.wait(1))

    def manual_background(self):
        self.docker._pool.shutdown(wait=False)
        pool = ManualPool()
        self.docker._pool = pool
        completed = mock.Mock()
        self.docker._background(lambda: None, completed)
        return pool, completed

    def test_late_background_error_after_shutdown_does_not_emit_or_update_controls(self):
        messages = []
        self.docker._dispatch.message.connect(messages.append)
        pool, completed = self.manual_background()
        self.docker.shutdown()
        with self.assertNoLogs("concurrent.futures", level="ERROR"):
            pool.future.set_exception(RuntimeError("late offline request"))
        self.assertTrue(pool.closed)
        self.assertEqual(messages, [])
        completed.assert_not_called()

    def test_late_background_completion_with_deleted_dispatcher_is_dropped(self):
        pool, completed = self.manual_background()
        sip.delete(self.docker._dispatch)
        with self.assertNoLogs("concurrent.futures", level="ERROR"):
            pool.future.set_result(None)
        completed.assert_not_called()

    def test_dispatch_shutdown_or_cpp_delete_releases_waiting_worker(self):
        for destroy in (False, True):
            with self.subTest(destroy=destroy):
                handler = mock.Mock()
                dispatch = docker_module.GuiDispatch(handler, self.window)
                queued = threading.Event()
                dispatch.requested.connect(lambda task: queued.set(), Qt.DirectConnection)
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as workers:
                    waiting = workers.submit(dispatch.invoke, "snapshot", {})
                    self.assertTrue(queued.wait(1))
                    if destroy:
                        sip.delete(dispatch)
                    else:
                        dispatch.shutdown()
                    with self.assertRaisesRegex(RuntimeError, "正在关闭"):
                        waiting.result(timeout=1)
                fixture.QApplication.processEvents()
                handler.assert_not_called()
                if docker_module.is_alive(dispatch):
                    sip.delete(dispatch)

    def test_dispatch_invoke_after_cpp_delete_reports_closing_without_using_qt(self):
        dispatch = docker_module.GuiDispatch(mock.Mock(), self.window)
        sip.delete(dispatch)
        with self.assertRaisesRegex(RuntimeError, "正在关闭"):
            dispatch.invoke("snapshot", {})

    def test_layer_import_failure_reports_loaded_runtime_for_other_computers(self):
        gateway, _ = self.attach_gateway()
        bridge = mock.Mock()
        bridge.import_png.side_effect = RuntimeError("生成图片写入图层失败 [WRITE_PIXELS]")
        self.docker._bridge = bridge
        with mock.patch.object(docker_module.Krita, "instance") as native:
            native.return_value.version.return_value = "5.2.11 (QA)"
            with self.assertRaises(RuntimeError) as raised:
                self.docker._ui_operation("import", {
                    "gateway_id": id(gateway), "bytes": b"synthetic", "name": "result"})
        message = str(raised.exception)
        self.assertIn("WRITE_PIXELS", message)
        self.assertIn("Mori " + docker_module.VERSION, message)
        self.assertIn("Krita 5.2.11 (QA)", message)
        self.assertIn("Python ", message)
        self.assertIn("Qt ", message)
        self.assertIn("PyQt ", message)
        bridge.import_png.assert_called_once()
        bridge.capture.assert_not_called()
        bridge.snapshot.assert_not_called()

    def test_selection_assistant_delivery_keeps_existing_bridge_routes(self):
        gateway, _ = self.attach_gateway()
        self.docker._bridge = mock.Mock()
        data = {"gateway_id": id(gateway)}
        with mock.patch.object(self.docker._selection, "register_mapping") as register, \
                mock.patch.object(self.docker._selection, "deliver") as deliver:
            self.docker._ui_operation("selection_mapping", data)
            self.docker._ui_operation("selection_result", data)
            register.assert_called_once_with(data)
            deliver.assert_called_once_with(data)
        self.docker._bridge.import_png.assert_not_called()

    def test_manual_selection_capture_remains_available(self):
        gateway, _ = self.attach_gateway()
        self.docker._bridge = mock.Mock()
        self.docker._source.setCurrentIndex(self.docker._source.findData("selection"))
        self.docker._ui_operation("capture", {"gateway_id": id(gateway), "source": "selection"})
        self.docker._bridge.capture.assert_called_once_with("selection")
        self.assertIs(self.docker._target(), self.docker._bridge.active_target.return_value)
        self.docker._bridge.active_target.assert_called_once_with("selection")

    def test_successful_layer_import_preserves_the_original_target(self):
        gateway, _ = self.attach_gateway()
        bridge = mock.Mock()
        bridge.last_import_target = object()
        self.docker._bridge = bridge
        original = object()
        returned = self.docker._ui_operation("import", {
            "gateway_id": id(gateway), "bytes": b"synthetic", "name": "result", "target": original})
        self.assertIs(returned, bridge.last_import_target)
        bridge.import_png.assert_called_once_with(b"synthetic", "result", original, force_new_document=False)

    def run_web_check(self, open_after=True):
        gateway, _ = self.attach_gateway()
        gateway.client.base_url = "https://qa.example.invalid"
        gateway.web_root = Path("old-web")
        worker = {}

        def background(function, done):
            worker.update(function=function, done=done)

        with mock.patch.object(self.docker, "_background", side_effect=background):
            self.docker._begin_web_update(gateway, open_after=open_after)
        return gateway, worker

    def web_result(self):
        return SimpleNamespace(release=SimpleNamespace(root=Path("new-web"), ui_version="20261106-new"),
                               updated=True, message="网页已更新")

    def test_first_web_page_waits_for_worker_without_blocking_gui(self):
        gateway, worker = self.run_web_check()
        with mock.patch.object(self.docker._web, "start") as start:
            start.assert_not_called()
            self.assertTrue(self.docker._web_update_pending)
            self.assertTrue(self.docker._settings_entry.isEnabled())
            with mock.patch.object(docker_module, "WebUpdateManager") as manager:
                manager.return_value.check.return_value = self.web_result()
                worker["function"]()
            start.assert_not_called()
            worker["done"]()
            start.assert_called_once_with(gateway.url)
        self.assertEqual(gateway.web_root, Path("new-web"))
        self.assertIn("20261106-new", self.docker._brand.toolTip())
        self.assertTrue(self.docker._settings_entry.isEnabled())

    def test_manual_check_keeps_open_page_pinned_to_its_release(self):
        self.docker._web_started = True
        gateway, worker = self.run_web_check(open_after=False)
        with mock.patch.object(self.docker._web, "start") as start, mock.patch.object(docker_module, "WebUpdateManager") as manager:
            manager.return_value.check.return_value = self.web_result()
            worker["function"]()
            worker["done"]()
            start.assert_not_called()
        self.assertEqual(gateway.web_root, Path("old-web"))
        self.assertIn("重开 Krita", self.docker._web_update_message)

    def test_update_worker_after_connection_change_cannot_open_old_gateway(self):
        gateway, worker = self.run_web_check()
        with mock.patch.object(docker_module, "WebUpdateManager") as manager:
            manager.return_value.check.return_value = self.web_result()
            worker["function"]()
        other = mock.Mock()
        self.docker._gateway = other
        self.docker._web_update_cancel.set()
        with mock.patch.object(self.docker._web, "start") as start:
            worker["done"]()
            start.assert_not_called()
        self.assertEqual(gateway.web_root, Path("old-web"))

    def test_update_worker_after_shutdown_cannot_touch_deleted_controls(self):
        _gateway, worker = self.run_web_check()
        with mock.patch.object(docker_module, "WebUpdateManager") as manager:
            manager.return_value.check.return_value = self.web_result()
            worker["function"]()
        cancel = self.docker._web_update_cancel
        self.docker.shutdown()
        self.assertTrue(cancel.is_set())
        sip.delete(self.window)
        worker["done"]()

    def test_update_failure_still_opens_bundled_page(self):
        gateway, worker = self.run_web_check()
        with mock.patch.object(docker_module, "WebUpdateManager", side_effect=RuntimeError("offline")):
            worker["function"]()
        with mock.patch.object(self.docker._web, "start") as start:
            worker["done"]()
            start.assert_called_once_with(gateway.url)
        self.assertEqual(gateway.web_root, Path("old-web"))
        self.assertIn("随插件安装", self.docker._web_update_message)


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(DockerLifecycleTests))
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors)}
    (output / "docker-lifecycle-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "docker-lifecycle-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    return report
