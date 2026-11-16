"""Release regressions in official kritarunner; no real model or account calls."""
import importlib
import io
import json
from pathlib import Path
import unittest


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in ("test_realtime", "test_realtime_layout", "test_docker_lifecycle",
                 "test_webview_lifecycle", "test_startup_visibility"):
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
        (output / (name + ".txt")).write_text(stream.getvalue(), encoding="utf-8")
        report[name] = {"passed": result.wasSuccessful(), "tests": result.testsRun,
                        "failures": len(result.failures), "errors": len(result.errors),
                        "skipped": len(result.skipped)}
    report["passed"] = all(item["passed"] and not item["skipped"] for item in report.values())
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
