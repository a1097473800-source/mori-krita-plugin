"""Loading presentation and cross-process visibility regressions in real Qt."""
import importlib
import json
import unittest
from unittest import mock

import test_webview_lifecycle as fixture
from test_webview_lifecycle import QProcess


class LoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.WebviewLifecycleTests.setUpClass()
        cls.application = fixture.WebviewLifecycleTests.application
        cls.module = fixture.WebviewLifecycleTests.module

    def setUp(self):
        fixture.WebviewLifecycleTests.setUp(self)

    def tearDown(self):
        fixture.WebviewLifecycleTests.tearDown(self)

    def process(self, event):
        process = mock.Mock()
        process.state.return_value = QProcess.Running
        process.readAllStandardOutput.return_value = (json.dumps(event) + '\n').encode()
        self.canvas._process = process
        self.canvas._read_output()
        return process

    def expose(self):
        self.parent.resize(640, 500)
        self.canvas.resize(640, 500)
        self.canvas.show()
        self.parent.show()
        self.application.processEvents()

    def test_host_stays_hidden_through_loading_and_is_revealed_only_after_ready(self):
        self.expose()
        self.canvas.begin_loading()
        self.assertTrue(self.canvas._host_parent.isHidden())
        self.assertTrue(self.canvas._host.isHidden())
        self.assertFalse(self.canvas._loading.isHidden())
        self.assertTrue(self.canvas._loading.message.isHidden())
        self.assertTrue(self.canvas._loading.logo._timer.isActive())
        process = self.process({'type': 'ready'})
        self.assertTrue(self.canvas._was_ready)
        self.assertFalse(self.canvas._host_parent.isHidden())
        self.assertTrue(self.canvas._loading.isHidden())
        self.assertFalse(self.canvas._loading.logo._timer.isActive())
        self.assertIn(b'{"op": "visibility", "visible": true}\n', [call.args[0] for call in process.write.call_args_list])

    def test_ready_received_on_another_mode_cannot_expose_the_browser(self):
        self.expose()
        self.canvas.begin_loading()
        self.canvas.set_active(False)
        self.process({'type': 'ready'})
        self.assertTrue(self.canvas._host_parent.isHidden())
        self.assertTrue(self.canvas._host.isHidden())
        self.canvas.set_active(True)
        self.assertFalse(self.canvas._host_parent.isHidden())
        self.assertFalse(self.canvas._host.isHidden())

    def test_new_navigation_restores_loading_without_exposing_the_old_page(self):
        self.expose()
        self.process({'type': 'ready'})
        self.process({'type': 'loading'})
        self.assertFalse(self.canvas._was_ready)
        self.assertTrue(self.canvas._host.isHidden())
        self.assertFalse(self.canvas._loading.isHidden())
        self.assertTrue(self.canvas._timeout.isActive())

    def test_failure_stops_animation_and_retry_uses_the_original_private_url(self):
        self.expose()
        self.canvas.begin_loading()
        self.canvas._last_url = 'http://127.0.0.1:14333/connect/synthetic'
        self.canvas._fail('Synthetic timeout')
        self.assertTrue(self.canvas._host.isHidden())
        self.assertFalse(self.canvas._loading.logo._timer.isActive())
        self.assertFalse(self.canvas._loading.retry.isHidden())
        self.assertFalse(self.canvas._loading.message.isHidden())
        self.canvas.set_active(False)
        self.canvas.set_active(True)
        self.assertFalse(self.canvas._loading.logo._timer.isActive())
        with mock.patch.object(self.canvas, 'start') as start:
            self.canvas._loading.retry.click()
        start.assert_called_once_with(self.canvas._last_url)

    def test_svg_animation_changes_over_time_and_stops_when_hidden(self):
        self.expose()
        self.canvas.begin_loading()
        logo = self.canvas._loading.logo
        first = logo.grab().toImage()
        from PyQt5.QtCore import QEventLoop, QTimer
        loop = QEventLoop()
        QTimer.singleShot(170, loop.quit)
        loop.exec_()
        second = logo.grab().toImage()
        self.assertNotEqual(first, second)
        self.canvas.hide()
        self.application.processEvents()
        self.assertFalse(logo._timer.isActive())

    def test_reload_hides_old_page_and_restarts_readiness_timeout(self):
        self.expose()
        process = self.process({'type': 'ready'})
        self.assertTrue(self.canvas.reload())
        self.assertFalse(self.canvas._was_ready)
        self.assertTrue(self.canvas._loading.isVisible())
        self.assertTrue(self.canvas._host.isHidden())
        self.assertTrue(self.canvas._timeout.isActive())
        self.assertIn(b'{"op": "reload"}\n', [call.args[0] for call in process.write.call_args_list])

    def test_reload_declines_stopped_and_closed_hosts_for_docker_fallback(self):
        self.assertFalse(self.canvas.reload())
        process = self.process({'type': 'ready'})
        self.canvas.stop()
        self.assertFalse(self.canvas.reload())
        self.canvas.shutdown()
        self.assertFalse(self.canvas.reload())
