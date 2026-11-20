import importlib
import io
import json
import os
from pathlib import Path
import sys
from types import ModuleType
import unittest


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    baseline = os.environ.get('MORI_AUTH_QA_BASELINE')
    if baseline:
        sys.path.insert(0, str(Path(baseline) / 'tests'))
    names = ('test_selection_controller', 'test_native_session_restore') if baseline else ('test_browser_login_ui', 'test_selection_controller', 'test_native_session_restore')
    for name in names:
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
        (output / (name + '.txt')).write_text(stream.getvalue(), encoding='utf-8')
        report[name] = {'passed': result.wasSuccessful(), 'tests': result.testsRun,
                        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}
    package = ModuleType('mori_login_capture')
    package.__path__ = [str(Path(__file__).resolve().parents[1] / 'mori_krita')]
    sys.modules[package.__name__] = package
    Dialog = importlib.import_module(package.__name__ + '.login_dialog').MoriLoginDialog
    dialog = Dialog()
    dialog.ensurePolished()
    dialog.adjustSize()
    dialog.grab().save(str(output / 'Mori-Krita-0.4.12-登录窗口.png'), 'PNG')
    dialog.deleteLater()
    report['passed'] = all(value['passed'] and not value['skipped'] for value in report.values())
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
