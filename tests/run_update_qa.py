import importlib
import io
import json
from pathlib import Path
import unittest


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    report = {}
    for name in ("test_update_ui", "test_docker_lifecycle", "test_startup_visibility", "test_realtime", "test_realtime_layout", "test_webview_lifecycle"):
        stream = io.StringIO()
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(
            unittest.defaultTestLoader.loadTestsFromModule(importlib.import_module(name)))
        (output / (name + ".txt")).write_text(stream.getvalue(), encoding="utf-8")
        report[name] = {"passed": result.wasSuccessful(), "tests": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped)}
    report["passed"] = all(value["passed"] and not value["skipped"] for value in report.values())
    (output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    module = importlib.import_module("mori_controller_fixture.update_dialog")
    dialog = module.UpdateDialog("0.4.9", True)
    updates = importlib.import_module("mori_controller_fixture.plugin_updates")
    release = updates.PluginRelease("0.4.9", updates.RELEASES_URL + "/tag/v0.4.9", notes="新增统一更新页、后台检查与更新提示。\n修复网页更新兼容判断，保留云端同步。")
    result = updates.PluginUpdateResult(release, "插件已是最新版本。", False, True)
    dialog.render(result, release, "20261120-cloud-sync", "网页已是最新兼容版本。", False, "2026-11-20")
    dialog.ensurePolished()
    dialog.adjustSize()
    dialog.grab().save(str(output / "update-dialog.png"))
    dialog.close()
