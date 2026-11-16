"""Focused native regressions while developing the live mode."""
import io
import json
from pathlib import Path
import unittest


def main(arguments):
    import test_realtime
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(test_realtime))
    (output / "test_realtime.txt").write_text(stream.getvalue(), encoding="utf-8")
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
