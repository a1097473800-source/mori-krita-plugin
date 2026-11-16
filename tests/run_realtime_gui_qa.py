"""Real Qt dock + libkis document, using an explicit headless view adapter.

Runs in the official runner without opening the user's Krita configuration.
"""
import importlib
import json
from pathlib import Path
import sys
import time
import traceback
from types import ModuleType, SimpleNamespace


def main(arguments):
    from PyQt5.QtCore import QEventLoop, QTimer, Qt, QByteArray
    from PyQt5.QtGui import QImage, QPixmap
    from PyQt5.QtWidgets import QMainWindow, QLabel
    from krita import Krita
    from realtime_gui import configure
    from mock_client import pattern_png
    source = Path(__file__).resolve().parents[1] / "mori_krita"
    package = ModuleType("mori_krita")
    package.__path__ = [str(source)]
    package.__file__ = str(source / "__init__.py")
    sys.modules[package.__name__] = package
    docker = importlib.import_module("mori_krita.docker")
    configure(docker)
    bridge = importlib.import_module("mori_krita.krita_bridge")
    layers = importlib.import_module("mori_krita.realtime_bridge")
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    native = Krita.instance()
    doc = native.createDocument(1024, 768, "Realtime QA synthetic", "RGBA", "U8", bridge.SRGB_PROFILE, 72.)
    subject = doc.createNode("主体 · 产品草图", "paintlayer")
    image = QImage.fromData(pattern_png(0, 1024, 768)).convertToFormat(QImage.Format_ARGB32)
    subject.setPixelData(QByteArray(bridge._image_bytes(image)), 0, 0, 1024, 768)
    doc.rootNode().addChildNode(subject, None)
    reference = doc.createNode("参考 · 金属材质", "paintlayer")
    ref_image = QImage.fromData(pattern_png(1, 160, 120)).convertToFormat(QImage.Format_ARGB32)
    reference.setPixelData(QByteArray(bridge._image_bytes(ref_image)), 0, 0, 160, 120)
    doc.rootNode().addChildNode(reference, None)
    reference.setVisible(False)
    doc.refreshProjection()
    doc.waitForDone()
    selected = SimpleNamespace(activeDocument=lambda: doc, documents=native.documents)
    main = QMainWindow()
    main.setAttribute(Qt.WA_DeleteOnClose)
    main.resize(1200, 1000)
    main.setWindowTitle("Mori · 实时模式隔离验收")
    main.setStyleSheet("QMainWindow { background:#333338; }")
    canvas = QLabel()
    canvas.setAlignment(Qt.AlignCenter)
    canvas.setPixmap(QPixmap.fromImage(image).scaled(700, 700, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    main.setCentralWidget(canvas)
    dock = docker.MoriDocker()
    dock._bridge = bridge.KritaBridge(selected)
    main.addDockWidget(Qt.RightDockWidgetArea, dock)
    main.show()
    main.resizeDocks([dock], [420], Qt.Horizontal)
    loop = QEventLoop()
    main.destroyed.connect(loop.quit)
    report = {"passed": False, "real_krita_runtime": native.version(),
              "headless_active_document_adapter": True, "real_qt_dock": True,
              "external_model_calls": 0, "real_credits_spent": 0}
    state = {"stage": "account", "started": time.monotonic()}
    panel = dock._realtime_panel

    def finish():
        timer.stop()
        panel.live.setChecked(False)
        doc.setModified(False)
        report["dock_width"] = dock.width()
        report["preview_height"] = panel.preview.height()
        report["panel_height"] = panel.height()
        report["layers"] = [node.name() for node in doc.rootNode().childNodes()]
        report["passed"] = bool(state["stage"] == "complete" and not report.get("error"))
        main.grab().save(str(output / "realtime-window.png"), "PNG")
        dock.grab().save(str(output / "realtime-panel.png"), "PNG")
        from PyQt5.QtWidgets import QApplication
        QApplication.processEvents()
        panel.references_box.grab().save(str(output / "reference-picker.png"), "PNG")
        from mori_krita.login_dialog import MoriLoginDialog
        login = MoriLoginDialog()
        login.ensurePolished()
        login.adjustSize()
        login.grab().save(str(output / "login-dialog.png"), "PNG")
        login.deleteLater()
        (output / "integration.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        QTimer.singleShot(200, loop.quit)

    def tick():
        try:
            if time.monotonic() - state["started"] > 50:
                raise AssertionError("Timed out at " + state["stage"] + ": " + panel.status.text())
            controller = dock._realtime
            if state["stage"] == "account":
                if not controller._authenticated:
                    return
                assert not dock._web_started
                assert not dock.testAttribute(Qt.WA_NativeWindow)
                assert not dock._web._host.testAttribute(Qt.WA_NativeWindow)
                report["webview_lazy"] = True
                panel.prompt.setPlainText("保持草图的构图与轮廓，参考金属材质，生成精致的产品设计。")
                controller.refresh()
                assert panel.source_ids()[0] == "canvas"
                report["default_canvas_input"] = True
                panel.add_reference("material", reference.uniqueId().toString())
                panel.writeback.setChecked(True)
                panel.live.setChecked(True)
                state["stage"] = "first"
            elif state["stage"] in ("first", "second"):
                expected = 1 if state["stage"] == "first" else 2
                outputs = [node for node in doc.rootNode().childNodes() if node.name().startswith(layers.OUTPUT_PREFIX)]
                if len(outputs) != expected or controller._task is not None:
                    return
                assert dock._gateway.client.generation_calls == expected
                if expected == 1:
                    assert bytes(subject.pixelData(0, 0, 1024, 768)) == bridge._image_bytes(image)
                    report["input_pixels_preserved"] = True
                    report["automatic_generation_and_writeback"] = True
                    subject.setPixelData(QByteArray(bytes([90, 140, 210, 255]) * 48 * 48), 300, 250, 48, 48)
                    doc.refreshProjection()
                    doc.waitForDone()
                    state["stage"] = "second"
                else:
                    assert sum(node.visible() for node in outputs) == 1
                    state["idle"] = time.monotonic()
                    state["stage"] = "stable"
            elif state["stage"] == "stable":
                if time.monotonic() - state["idle"] < 5:
                    return
                assert dock._gateway.client.generation_calls == 2
                report["no_feedback_loop"] = True
                report["automatic_generation_calls"] = 2
                panel.submit.click()
                assert not panel.live.isChecked()
                state["stage"] = "single"
            elif state["stage"] in ("single", "repeat_single"):
                expected = 3 if state["stage"] == "single" else 4
                outputs = [node for node in doc.rootNode().childNodes() if node.name().startswith(layers.OUTPUT_PREFIX)]
                if len(outputs) != expected or controller._task is not None:
                    return
                assert not panel.live.isChecked()
                assert panel.submit.isEnabled()
                assert dock._gateway.client.generation_calls == expected
                state["idle"] = time.monotonic()
                state["stage"] = "single_stable" if expected == 3 else "repeat_stable"
            elif state["stage"] in ("single_stable", "repeat_stable"):
                if time.monotonic() - state["idle"] < 3:
                    return
                expected = 3 if state["stage"] == "single_stable" else 4
                assert dock._gateway.client.generation_calls == expected
                if expected == 3:
                    report["single_submit_stops_automatic_generation"] = True
                    panel.submit.click()
                    state["stage"] = "repeat_single"
                    return
                report["same_parameters_can_be_submitted_again"] = True
                report["no_automatic_followup_after_single_submit"] = True
                report["mock_account_generation_calls"] = 4
                report["reference_count"] = len(dock._gateway.client.jobs()[-1]["references"])
                dock._web.ready.connect(lambda: report.update(workbench_webview_ready=True))
                dock._set_mode(1)
                state["stage"] = "webview"
            elif state["stage"] == "webview" and report.get("workbench_webview_ready"):
                from unittest import mock
                from PyQt5.QtWidgets import QApplication
                QApplication.processEvents()
                toolbar = dock._workbench_toolbar
                report["workbench_toolbar_height"] = toolbar.height()
                assert toolbar.height() < 50
                assert dock._source.parentWidget() is toolbar
                assert dock._capture_button.parentWidget() is toolbar
                assert dock._import_button.parentWidget() is toolbar
                assert not dock._reference_mode.isVisible()
                dock._reference_actions[1].trigger()
                assert dock._reference_mode.currentData() == "original"
                assert dock._settings.value("referenceMode") == "original"
                dock._source.setCurrentIndex(dock._source.findData("canvas"))

                def synchronous(function, done=None):
                    function()
                    if done:
                        done()

                with mock.patch.object(dock, "_background", side_effect=synchronous), \
                     mock.patch.object(dock._gateway, "capture") as capture, \
                     mock.patch.object(dock._gateway, "import_selected") as receive:
                    dock._capture_button.click()
                    dock._import_button.click()
                    assert capture.call_args.args == ("canvas",)
                    assert capture.call_args.kwargs["mode"] == "original"
                    assert capture.call_args.kwargs["display"] is True
                    receive.assert_called_once_with()
                report["workbench_transfer_actions_preserved"] = True
                dock._reference_actions[0].trigger()
                report["workbench_quality_setting_preserved"] = True
                dock._status("Mori 工作台")
                QApplication.processEvents()
                toolbar.grab().save(str(output / "workbench-toolbar.png"), "PNG")
                dock.grab().save(str(output / "workbench-panel.png"), "PNG")
                main.resize(1700, 1000)
                main.resizeDocks([dock], [760], Qt.Horizontal)
                for _ in range(3):
                    QApplication.processEvents()
                assert dock._toolbar_inline
                assert toolbar.parentWidget() is dock.titleBarWidget()
                assert toolbar.isVisible()
                titlebar = dock.titleBarWidget()
                assert titlebar.height() == 56
                for button in (dock._capture_button, dock._import_button):
                    top = button.mapTo(titlebar, button.rect().topLeft()).y()
                    assert top >= 10, (top, titlebar.height())
                    assert titlebar.height() - top - button.height() >= 10
                    assert button.visibleRegion().boundingRect().contains(button.rect())
                assert dock.widget().y() >= titlebar.geometry().bottom() + 1
                report["workbench_buttons_have_vertical_padding"] = True
                dock.titleBarWidget().grab().save(str(output / "workbench-single-row.png"), "PNG")
                report["workbench_single_row_at_wide_width"] = True
                report["workbench_wide_width"] = dock.width()
                main.resizeDocks([dock], [340], Qt.Horizontal)
                for _ in range(3):
                    QApplication.processEvents()
                assert not dock._toolbar_inline
                assert toolbar.parentWidget() is dock._pages.currentWidget()
                for control in (dock._source, dock._capture_button, dock._import_button, dock._workbench_options):
                    assert control.isVisible()
                    assert control.visibleRegion().boundingRect().contains(control.rect())
                report["workbench_narrow_controls_not_clipped"] = True
                dock.grab().save(str(output / "workbench-narrow.png"), "PNG")
                main.resizeDocks([dock], [420], Qt.Horizontal)
                main.resize(1200, 1000)
                QApplication.processEvents()
                assert not dock.testAttribute(Qt.WA_NativeWindow)
                assert not dock._pages.testAttribute(Qt.WA_NativeWindow)
                report["native_webview_does_not_promote_dock"] = True
                dock._set_mode(0)
                report["preview_has_no_height_cap"] = panel.preview.height() > 500
                state["stage"] = "complete"
                QTimer.singleShot(500, finish)
                timer.stop()
        except Exception:
            report["error"] = traceback.format_exc()
            finish()

    timer = QTimer()
    timer.setInterval(350)
    timer.timeout.connect(tick)
    timer.start()
    QTimer.singleShot(60000, loop.quit)
    loop.exec_()
    dock.shutdown()
    doc.setModified(False)
    doc.close()
