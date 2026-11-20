import importlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest


def main(arguments):
    from PyQt5.QtWidgets import QApplication
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in ('test_browser_login_ui', 'test_settings_ui', 'test_update_ui', 'test_native_session_restore'):
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
        (output / (name + '.txt')).write_text(stream.getvalue(), encoding='utf-8')
        report[name] = {'passed': result.wasSuccessful(), 'tests': result.testsRun,
                        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}
    fixture = importlib.import_module('test_settings_ui')
    instance = fixture.SettingsUiTests(methodName='runTest')
    instance.setUp()
    try:
        dialog = instance.dialog
        dialog.resize(900, 690)
        dialog.updates.render(SimpleNamespace(checked=False, available=False, message='暂时无法检查插件更新，请检查网络后重试；也可打开发布页。'),
                              None, '20261120', '网页已是最新兼容版本', False, '2026-11-20 20:00')
        for page, title in (('canvas', '画布与图层'), ('realtime', '实时与选区'), ('updates', '更新与版本')):
            dialog.set_page(page)
            QApplication.processEvents()
            dialog.grab().save(str(output / ('Mori-0.4.12-设置-' + title + '.png')), 'PNG')
        instance.dock._brand.grab().save(str(output / 'Mori-0.4.12-工具栏标志.png'), 'PNG')
        Login = importlib.import_module('mori_controller_fixture.login_dialog').MoriLoginDialog
        login = Login()
        login.ensurePolished()
        login.adjustSize()
        login.grab().save(str(output / 'Mori-0.4.12-登录窗口.png'), 'PNG')
        login.deleteLater()
    finally:
        instance.cleanup()
    report['passed'] = all(value['passed'] and not value['skipped'] for value in report.values())
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
