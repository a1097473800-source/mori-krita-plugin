import importlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

try:
    from PyQt5.QtCore import QEventLoop, QTimer, Qt
    from PyQt5.QtWidgets import QApplication, QWidget, QDialog
except ImportError:
    QWidget = None

module = None
if QWidget is not None:
    package = ModuleType('mori_browser_ui_fixture')
    package.__path__ = [str(Path(__file__).resolve().parents[1] / 'mori_krita')]
    sys.modules[package.__name__] = package
    module = importlib.import_module(package.__name__ + '.selection_controller')


@unittest.skipIf(QWidget is None, 'Run inside the official Krita runner')
class BrowserLoginUiTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(QApplication.instance())
        self.window = QWidget()

    def tearDown(self):
        self.window.deleteLater()

    def context(self, status='complete'):
        manager = SimpleNamespace(
            start=mock.Mock(return_value={'attemptId': 'native-ui-attempt-123', 'status': 'pending', 'message': '请在浏览器确认登录'}),
            status=mock.Mock(return_value={'status': status, 'message': '已完成' if status == 'complete' else '已取消'}),
            cancel=mock.Mock())
        gateway = SimpleNamespace(client=SimpleNamespace(base_url='https://mori.example'), _browser_login=manager,
                                  browser_login=lambda: manager, _sync_identity=mock.Mock())
        controller = SimpleNamespace(_login_dialog=None, anchor=None,
            docker=SimpleNamespace(window=lambda: self.window, _web_started=False),
            _conversations={'old': []}, _config_gateway='old', _work=mock.Mock())
        return controller, gateway, manager

    def test_browser_confirmation_accepts_native_login_and_invalidates_old_account_caches(self):
        controller, gateway, manager = self.context()
        original_execute = module.MoriLoginDialog.exec_
        def execute(dialog):
            dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
            QTimer.singleShot(0, dialog.browser_submit.click)
            watchdog = QTimer(dialog)
            watchdog.setSingleShot(True)
            watchdog.timeout.connect(dialog.reject)
            watchdog.start(2000)
            return original_execute(dialog)
        with mock.patch.object(module.MoriLoginDialog, 'exec_', execute):
            accepted = module.SelectionController._login(controller, gateway)
        self.assertTrue(accepted)
        self.assertIsNone(controller._login_dialog)
        self.assertEqual(controller._conversations, {})
        self.assertIsNone(controller._config_gateway)
        gateway._sync_identity.assert_called_once()
        controller._work.assert_not_called()
        manager.cancel.assert_called_once_with('native-ui-attempt-123')

    def test_cancel_keeps_dialog_open_and_restores_email_password_sign_in(self):
        controller, gateway, manager = self.context('cancelled')
        captured = []
        def execute(dialog):
            dialog.password.setText('synthetic-unused-password')
            dialog.browser_submit.click()
            dialog.browser_submit.click()
            captured.append(dialog)
            self.assertTrue(dialog.email.isEnabled())
            self.assertTrue(dialog.password.isEnabled())
            self.assertEqual(dialog.browser_submit.text(), '通过浏览器登录（支持 Google）')
            dialog.reject()
            return QDialog.Rejected
        with mock.patch.object(module.MoriLoginDialog, 'exec_', execute):
            accepted = module.SelectionController._login(controller, gateway)
        self.assertFalse(accepted)
        self.assertEqual(captured[0].password.text(), '')
        self.assertEqual(controller._conversations, {'old': []})
        manager.cancel.assert_called_once_with('native-ui-attempt-123')
        gateway._sync_identity.assert_not_called()
