"""Official kritarunner entry point. No native window or user document needed."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest


def main(arguments):
    from krita import Krita, View
    from PyQt5.QtCore import QT_VERSION_STR
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    path = Path(__file__).with_name("test_selection_geometry.py")
    spec = importlib.util.spec_from_file_location("mori_selection_geometry_runtime_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(module))
    methods = {name: hasattr(View, name) for name in (
        "flakeToImageTransform", "flakeToCanvasTransform", "document", "canvas", "visible")}
    report = {"passed": result.wasSuccessful() and all(methods.values()),
              "runtime": Krita.instance().version(), "qt_version": QT_VERSION_STR,
              "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
              "public_view_methods": methods, "user_document_reads": 0, "gui_windows_created": 0}
    (output / "selection-geometry-qa.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "selection-geometry-tests.txt").write_text(stream.getvalue(), encoding="utf-8")
    return report
