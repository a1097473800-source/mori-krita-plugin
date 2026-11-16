"""Actual Qt deletion regressions; no WebView host, user documents or network."""
import importlib
import io
import json
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest import mock

try:
    from PyQt5 import sip
    from PyQt5.QtCore import QProcess, Qt
    from PyQt5.QtWidgets import QApplication, QWidget
except ImportError:
    QApplication = None

PROJECT = Path(__file__).resolve().parents[1]


class FakeProcess:
    def __init__(self):
        self.running = True
        self.writes = []
        self.waits = []
        self.kills = 0
        self.channel_closed = False

    def state(self):
        return QProcess.Running if self.running else QProcess.NotRunning

    def write(self, data):
        self.writes.append(data)

    def closeWriteChannel(self):
        self.channel_closed = True

    def waitForFinished(self, timeout):
        self.waits.append(timeout)
        return not self.running

    def kill(self):
        self.kills += 1
        self.running = False


@unittest.skipIf(QApplication is None, "Run native lifecycle tests inside Krita")
class WebviewLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        package = ModuleType("mori_webview_lifecycle_fixture")
        package.__path__ = [str(PROJECT / "mori_krita")]
        sys.modules[package.__name__] = package
        cls.module = importlib.import_module(package.__name__ + ".webview")

    def setUp(self):
        self.parent = QWidget()
        self.canvas = self.module.WebCanvas(self.parent)

    def tearDown(self):
        self.canvas.shutdown()
        if not sip.isdeleted(self.parent):
            sip.delete(self.parent)

    def test_host_notice_reports_status_without_replacing_the_canvas(self):
        messages = []
        self.canvas.error.connect(messages.append)
        before = self.canvas._status.text()
        process = mock.Mock()
        process.readAllStandardOutput.return_value = b'{"type":"notice","message":"Export cancelled safely"}\n'
        process.state.return_value = QProcess.NotRunning
        self.canvas._process = process
        self.canvas._read_output()
        self.assertEqual(messages, ["Export cancelled safely"])
        self.assertEqual(self.canvas._status.text(), before)
        self.assertFalse(self.canvas._closed)

    def test_stop_shutdown_and_finished_tolerate_an_already_deleted_web_widget(self):
        sip.delete(self.parent)
        self.assertTrue(sip.isdeleted(self.canvas))
        self.canvas.stop()
        self.canvas._finished()
        self.canvas.shutdown()
        self.canvas.shutdown()
        self.assertTrue(self.canvas._closed)

    def test_deleted_timeout_and_process_are_not_called_during_teardown(self):
        process = QProcess(self.canvas)
        self.canvas._process = process
        sip.delete(self.canvas._timeout)
        sip.delete(process)
        self.canvas.stop()
        self.canvas._kill_if_current(process)
        self.canvas.shutdown()
        self.canvas._finished()
        self.assertIsNone(self.canvas._process)

    def test_terminal_shutdown_closes_then_kills_a_host_that_does_not_exit(self):
        process = FakeProcess()
        self.canvas._process = process
        self.canvas._pending_url = "http://127.0.0.1:12345/connect/fixture"
        self.canvas.shutdown()
        self.canvas._shutdown()  # aboutToQuit and docker cleanup may both run.
        self.assertTrue(self.canvas._closed)
        self.assertIsNone(self.canvas._pending_url)
        self.assertEqual(process.writes, [b'{"op": "close"}\n'])
        self.assertTrue(process.channel_closed)
        self.assertEqual(process.waits, [1200, 500])
        self.assertEqual(process.kills, 1)

    def test_regular_stop_keeps_reconnect_available(self):
        self.canvas.stop()
        self.assertFalse(self.canvas._closed)
        self.assertTrue(self.canvas._stopping)

    def test_hidden_workbench_does_not_create_native_ancestors(self):
        self.parent.show()
        self.application.processEvents()
        self.assertFalse(self.canvas._host.testAttribute(Qt.WA_NativeWindow))
        self.assertTrue(self.canvas._host.testAttribute(Qt.WA_DontCreateNativeAncestors))
        self.canvas._host.setAttribute(Qt.WA_NativeWindow)
        self.canvas._host.winId()
        self.assertFalse(self.canvas._host_parent.testAttribute(Qt.WA_NativeWindow))
        self.assertFalse(self.canvas.testAttribute(Qt.WA_NativeWindow))
        self.assertFalse(self.parent.testAttribute(Qt.WA_NativeWindow))

    def test_queued_restart_is_ignored_if_shutdown_happens_first(self):
        self.canvas._pending_url = "http://127.0.0.1:12345/connect/fixture"
        with mock.patch.object(self.module.QTimer, "singleShot") as schedule:
            self.canvas._finished()
        self.assertEqual(schedule.call_count, 1)
        restart = schedule.call_args.args[1]
        self.canvas.shutdown()
        with mock.patch.object(self.module.tempfile, "mkdtemp", side_effect=AssertionError("Host restarted")):
            restart()
        self.assertIsNone(self.canvas._process)

    def test_late_host_signals_do_not_update_deleted_controls_or_emit_errors(self):
        errors = []
        self.canvas.error.connect(errors.append)
        sip.delete(self.parent)
        self.canvas._read_output()
        self.canvas._process_error(QProcess.Crashed)
        self.canvas._fail("late failure")
        self.canvas.shutdown()
        self.canvas._load_timeout()
        self.assertEqual(errors, [])

    def test_delayed_kill_does_not_kill_a_replacement_host(self):
        old = FakeProcess()
        replacement = FakeProcess()
        self.canvas._process = replacement
        self.canvas._kill_if_current(old)
        self.assertEqual(old.kills, 0)
        self.assertEqual(replacement.kills, 0)

    def test_finished_after_shutdown_drops_pending_restarts(self):
        self.canvas.shutdown()
        self.canvas._pending_url = "http://127.0.0.1:12345/connect/fixture"
        with mock.patch.object(self.module.QTimer, "singleShot") as schedule:
            self.canvas._finished()
        schedule.assert_not_called()
        self.assertIsNone(self.canvas._pending_url)


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(WebviewLifecycleTests))
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "webview_hosts_started": 0, "user_document_reads": 0}
    (output / "webview-lifecycle-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "webview-lifecycle-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    return report
