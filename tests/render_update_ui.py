import importlib
import json
from pathlib import Path
from unittest import mock
import test_selection_controller as fixture


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    fixture.SelectionControllerTests.setUpClass()
    module = importlib.import_module("mori_controller_fixture.docker")
    with mock.patch.object(module, "QSettings", lambda *_: fixture.MemorySettings()):
        dock = module.MoriDocker()
    try:
        with mock.patch.object(dock, "_check_all_updates"):
            dock._show_updates()
        dialog = dock._update_dialog
        updates = importlib.import_module("mori_controller_fixture.plugin_updates")
        release = updates.PluginRelease("0.4.9", updates.RELEASES_URL + "/tag/v0.4.9", notes="新增统一更新页、后台检查与更新提示。\n修复网页更新兼容判断，保留云端同步。")
        result = updates.PluginUpdateResult(release, "插件已是最新版本。", False, True)
        dialog.render(result, release, "20261120-cloud-sync", "网页已是最新兼容版本。", False, "2026-11-20")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        dialog.grab().save(str(output / "update-dialog.png"))
        available = updates.PluginUpdateResult(release, "新版现已准备好，更新后即可使用。", True, True)
        dialog.current_version = "0.4.8"
        release = updates.PluginRelease("0.4.9", updates.RELEASES_URL + "/tag/v0.4.9",
            updates.RELEASES_URL + "/download/v0.4.9/Mori-Krita-0.4.9-Compatibility-Update.zip",
            "新增统一更新页，实时模式和工作台均可打开。\n启动时自动检查，有新版本时显示提醒。\n修复网页更新兼容判断，保留云端同步。")
        available = updates.PluginUpdateResult(release, "新版现已准备好，更新后即可使用。", True, True)
        dialog.render(available, release, "20261120-cloud-sync", "网页已更新，下次打开工作台后生效。", False, "2026-11-20 15:00")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        dialog.grab().save(str(output / "update-available.png"))
        dialog.render(None, None, "20261120-cloud-sync", "正在检查工作台网页更新…", True, "")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        dialog.grab().save(str(output / "update-checking.png"))
        failed = updates.PluginUpdateResult(None, "暂时无法检查插件更新，请检查网络后重试；也可打开发布页。")
        dialog.render(failed, None, "20261120-cloud-sync", "暂时无法检查网页更新，已使用本机兼容版本。", False, "2026-11-20 15:00")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        dialog.grab().save(str(output / "update-offline.png"))
        dialog.hide()
        report = []
        for width in (340, 400, 700):
            dock.resize(width, 520)
            dock.ensurePolished()
            dock.grab().save(str(output / ("dock-" + str(width) + ".png")))
            entry = dock._updates_entry
            report.append({"requestedWidth": width, "actualWidth": dock.width(),
                "titleWidth": dock._titlebar.width(), "entryX": entry.x(),
                "entryRight": entry.x() + entry.width()})
        (output / "summary.json").write_text(json.dumps({"passed": True, "geometry": report}, indent=2))
    finally:
        dock.shutdown()
