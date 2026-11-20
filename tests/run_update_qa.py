import importlib
import io
import json
from pathlib import Path
import unittest


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in ("test_update_ui", "test_settings_ui", "test_docker_lifecycle", "test_startup_visibility", "test_realtime", "test_realtime_layout", "test_webview_lifecycle"):
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
        (output / (name + ".txt")).write_text(stream.getvalue(), encoding="utf-8")
        report[name] = {"passed": result.wasSuccessful(), "tests": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
    report["passed"] = all(value["passed"] and not value["skipped"] for value in report.values())
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
