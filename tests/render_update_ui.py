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
        settings = dock._settings_dialog
        dialog = settings.updates
        updates = importlib.import_module("mori_controller_fixture.plugin_updates")
        release = updates.PluginRelease(module.VERSION, updates.RELEASES_URL + "/tag/v0.4.10", notes="新增独立设置窗口，集中管理画布、实时与选区、更新。\n右上角齿轮统一打开设置，有新版时显示小圆点。\n常用偏好自动保存。")
        result = updates.PluginUpdateResult(release, "插件已是最新版本。", False, True)
        dock._plugin_update_result = result
        for page, _, _ in settings.PAGES:
            settings.set_page(page)
            fixture.QApplication.processEvents()
            settings.grab().save(str(output / ("settings-" + page + ".png")))
        dialog.render(result, release, "20261120-cloud-sync", "网页已是最新兼容版本。", False, "2026-11-20")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        settings.grab().save(str(output / "update-dialog.png"))
        available = updates.PluginUpdateResult(release, "新版现已准备好，更新后即可使用。", True, True)
        dialog.current_version = "0.4.9"
        release = updates.PluginRelease(module.VERSION, updates.RELEASES_URL + "/tag/v0.4.10",
            updates.RELEASES_URL + "/download/v0.4.10/Mori-Krita-0.4.10-Compatibility-Update.zip",
            "新增统一更新页，实时模式和工作台均可打开。\n启动时自动检查，有新版本时显示提醒。\n修复网页更新兼容判断，保留云端同步。")
        available = updates.PluginUpdateResult(release, "新版现已准备好，更新后即可使用。", True, True)
        dialog.render(available, release, "20261120-cloud-sync", "网页已更新，下次打开工作台后生效。", False, "2026-11-20 15:00")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        settings.grab().save(str(output / "update-available.png"))
        dialog.render(None, None, "20261120-cloud-sync", "正在检查工作台网页更新…", True, "")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        settings.grab().save(str(output / "update-checking.png"))
        failed = updates.PluginUpdateResult(None, "暂时无法检查插件更新，请检查网络后重试；也可打开发布页。")
        dialog.render(failed, None, "20261120-cloud-sync", "暂时无法检查网页更新，已使用本机兼容版本。", False, "2026-11-20 15:00")
        dialog.adjustSize()
        fixture.QApplication.processEvents()
        settings.grab().save(str(output / "update-offline.png"))
        settings.resize(740, 520)
        report = []
        for page in ("canvas", "realtime", "updates"):
            settings.set_page(page)
            fixture.QApplication.processEvents()
            settings.grab().save(str(output / ("settings-small-" + page + ".png")))
            report.append({"page": page, "width": settings.width(), "height": settings.height(),
                "horizontalScroll": settings._page_widgets[page].horizontalScrollBar().maximum(),
                "doneVisible": settings.done.visibleRegion().boundingRect().contains(settings.done.rect())})
        settings.hide()
        for mode in (0, 1):
            with mock.patch.object(dock, "_start"), mock.patch.object(dock._realtime, "set_active"):
                dock._set_mode(mode)
            for width in (340, 400, 620, 700, 900):
                dock.resize(width, 520)
                dock.ensurePolished()
                dock.grab().save(str(output / ("dock-" + str(mode) + "-" + str(width) + ".png")))
                title = dock._titlebar
                controls = (dock._settings_entry,)
                if mode == 1:
                    controls += (dock._source, dock._capture_button, dock._import_button)
                positions = []
                for control in controls:
                    parent = control.parentWidget()
                    x = control.mapTo(parent, control.rect().topLeft()).x()
                    positions.append({"name": control.accessibleName(), "x": x,
                        "right": x + control.width(), "parentWidth": parent.width()})
                report.append({"mode": mode, "requestedWidth": width, "actualWidth": dock.width(),
                    "titleWidth": title.width(), "controls": positions})
        passed = all(row.get("horizontalScroll", 0) == 0 and row.get("doneVisible", True) for row in report)
        passed = passed and all(c["x"] >= 0 and c["right"] <= c["parentWidth"] for row in report for c in row.get("controls", []))
        (output / "summary.json").write_text(json.dumps({"passed": passed, "geometry": report}, indent=2))
    finally:
        dock.shutdown()
