"""Offline registration and native image regressions in an actual Krita runtime.

Invoke through an isolated kritarunner profile. No website account is created and
all image/document inputs are synthetic.
"""
import importlib
import io
import json
from pathlib import Path
import sys
import traceback
import unittest


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from krita import Krita
    from PyQt5.QtCore import QT_VERSION_STR, PYQT_VERSION_STR
    reports = {}

    def save():
        summary = {"krita": Krita.instance().version(), "python": sys.version,
                   "qt": QT_VERSION_STR, "pyqt": PYQT_VERSION_STR,
                   "reports": reports,
                   "passed": bool(reports) and all(report.get("passed") for report in reports.values())}
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    for name in ("test_registration", "test_gateway", "test_api", "test_session_store", "test_transport",
                 "test_reference_encoding", "test_auto_source", "test_image_mapping", "test_import_aspect", "test_selection_edit",
                 "test_selection_controller", "test_selection_input_routing", "test_selection_composer", "test_selection_geometry",
                 "test_selection_tasks", "test_docker_lifecycle", "test_native_session_restore",
                 "test_webview_lifecycle", "test_import_preview", "test_startup_visibility", "test_web_updates",
                 "test_realtime", "test_realtime_layout"):
        try:
            module = importlib.import_module(name)
            stream = io.StringIO()
            result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
                unittest.defaultTestLoader.loadTestsFromModule(module))
            reports[name] = {"passed": result.wasSuccessful(), "tests": result.testsRun,
                             "skipped": len(result.skipped),
                             "failures": len(result.failures), "errors": len(result.errors)}
            (output / (name + ".txt")).write_text(stream.getvalue(), encoding="utf-8")
            if getattr(module, "METRICS", None):
                (output / (name + "-metrics.json")).write_text(
                    json.dumps(module.METRICS, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            reports[name] = {"passed": False, "traceback": traceback.format_exc()}
        save()
    try:
        from run_krita_smoke import main as native_bridge
        reports["native_bridge"] = native_bridge([str(output / "native-bridge")])
    except Exception:
        reports["native_bridge"] = {"passed": False, "traceback": traceback.format_exc()}
    save()
    return reports
