import importlib
import io
import json
from pathlib import Path
import unittest


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in ('test_workbench_loading', 'test_webview_lifecycle', 'test_docker_lifecycle', 'test_startup_visibility', 'test_settings_ui'):
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
        (output / (name + '.txt')).write_text(stream.getvalue(), encoding='utf-8')
        report[name] = {'passed': result.wasSuccessful(), 'tests': result.testsRun,
                        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}
    fixture = importlib.import_module('test_workbench_loading')
    canvas = fixture.LoadingTests.module.WebCanvas()
    canvas.resize(660, 620)
    canvas.begin_loading()
    canvas.show()
    fixture.LoadingTests.application.processEvents()
    canvas.grab().save(str(output / 'Mori-0.4.13-工作台加载.png'), 'PNG')
    canvas.shutdown()
    canvas.close()
    report['passed'] = all(value['passed'] and not value['skipped'] for value in report.values())
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
