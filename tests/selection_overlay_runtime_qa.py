"""Opt-in real-Krita GUI preview using synthetic artwork and an offline client.

Call run(output_directory) on Krita's main thread. --prepare builds an isolated
resource tree for the parent agent to launch; this file never launches Krita.
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import traceback

_runner = None


def run(output_directory=None):
    global _runner
    if _runner is not None:
        return _runner
    from PyQt5.QtCore import QObject, QTimer, Qt
    from PyQt5.QtGui import QImage
    from krita import Krita, Selection
    import mori_krita.docker as docker_module
    from mori_krita.selection_geometry import locate_selection
    from mock_client import FakeMoriClient, pattern_png

    output = Path(output_directory or Path(__file__).resolve().parents[1] / "qa" / "selection-overlay")
    output.mkdir(parents=True, exist_ok=True)

    class MemorySettings:
        def __init__(self):
            self.values = {"serverUrl": "https://qa.example.invalid", "selectionAssistant": True,
                           "selectionCollapsed": False, "selectionSettings": "{}", "autoImport": True}

        def value(self, key, default=None, type=None):
            value = self.values.get(key, default)
            return type(value) if type is not None else value

        def setValue(self, key, value):
            self.values[key] = value

    class OfflineClient(FakeMoriClient):
        def fork_session(self):
            return self

        def quote(self, settings):
            return {"enforcement": True, "totalPoints": 0, "available": 1000}

    class Runner(QObject):
        def __init__(self):
            super().__init__()
            self.dock = None
            self.document = None
            self.window = None
            self.attempts = 0
            self.report = {"passed": False, "mode": "real Krita GUI / offline synthetic fixture",
                           "external_requests": 0, "paid_model_calls": 0}

        def start(self):
            try:
                native = Krita.instance()
                active = native.activeWindow()
                if active is None:
                    QTimer.singleShot(300, self.start)
                    return
                self.window = active.qwindow()
                original_gateway = docker_module.MoriGateway

                def isolated_gateway(client, web_root, storage_dir, invoke, notify=None):
                    return original_gateway(client, web_root, output / "workspace", invoke, notify)

                docker_module.MoriClient = OfflineClient
                docker_module.MoriGateway = isolated_gateway
                self.document = native.createDocument(512, 384, "Mori 选区浮栏验收 · 合成素材", "RGBA", "U8",
                                                      "sRGB-elle-V2-srgbtrc.icc", 72.0)
                active.addView(self.document)
                image = QImage.fromData(pattern_png(0, 512, 384)).convertToFormat(QImage.Format_ARGB32)
                layer = self.document.createNode("QA 合成参考层", "paintlayer")
                self.document.rootNode().addChildNode(layer, None)
                layer.setPixelData(image.constBits().asstring(image.byteCount()), 0, 0, 512, 384)
                self.document.setActiveNode(layer)
                selection = Selection()
                selection.select(128, 80, 192, 128, 255)
                self.document.setSelection(selection)
                self.document.refreshProjection()
                self.document.waitForDone()
                self.document.setModified(False)
                active.activeView().canvas().setZoomLevel(1.0)
                self.dock = docker_module.MoriDocker()
                # Suppress showEvent's webpage startup; the native composer uses
                # the same offline gateway even while the large dock is hidden.
                self.dock._started = True
                self.dock._settings = MemorySettings()
                self.dock._selection.enabled = True
                self.window.addDockWidget(Qt.RightDockWidgetArea, self.dock)
                self.dock.hide()
                self.report["runtime"] = native.version()
                QTimer.singleShot(400, self.inspect)
            except Exception:
                self.finish_error()

        def inspect(self):
            try:
                self.attempts += 1
                controller = self.dock._selection
                controller.refresh()
                anchor = locate_selection()
                panel = controller.panel
                if anchor is None or panel is None:
                    if self.attempts < 40:
                        QTimer.singleShot(250, self.inspect)
                        return
                    raise AssertionError("No selection anchor/composer: " + str(getattr(controller, "last_error", "")))
                panel.set_message("修改这个产品的配色为红色")
                panel.set_collapsed(False)
                controller.refresh()
                QTimer.singleShot(700, self.capture)
            except Exception:
                self.finish_error()

        def capture(self):
            try:
                controller = self.dock._selection
                anchor, panel = controller.anchor, controller.panel
                assert anchor is not None and panel is not None
                parent = panel.parentWidget()
                self.report.update(
                    canvas_class=parent.metaObject().className(),
                    parent_is_krita_canvas=parent.inherits("KisOpenGLCanvas2") or parent.inherits("KisQPainterCanvas"),
                    panel_visible=panel.isVisible(),
                    panel_collapsed=panel.is_collapsed(),
                    viewport=[parent.width(), parent.height()],
                    selection_image_rect=self.rect(anchor.selection_rect),
                    selection_canvas_rect=self.rect(anchor.rect),
                    toolbar_canvas_rect=self.rect(anchor.toolbar_rect) if anchor.toolbar_rect is not None else None,
                    panel_canvas_rect=self.rect(panel.geometry()),
                    panel_is_canvas_child=parent is anchor.parent,
                    controller_error=getattr(controller, "last_error", None),
                    webview_started=self.dock._web_started,
                )
                assert self.report["parent_is_krita_canvas"]
                assert self.report["panel_visible"] and self.report["panel_is_canvas_child"]
                assert panel.x() >= 0 and panel.y() >= 0
                assert panel.x() + panel.width() <= parent.width()
                assert panel.y() + panel.height() <= parent.height()
                assert panel.grab().save(str(output / "selection-composer.png"), "PNG")
                assert self.window.grab().save(str(output / "selection-window.png"), "PNG")
                expanded_message = panel.message()
                panel.set_collapsed(True)
                controller.refresh()
                self.report["collapsed_height"] = panel.height()
                self.report["collapse_preserves_draft"] = panel.message() == expanded_message
                self.report["collapsed_visible"] = panel.isVisible()
                assert self.report["collapse_preserves_draft"] and self.report["collapsed_visible"]
                assert panel.grab().save(str(output / "selection-composer-collapsed.png"), "PNG")
                panel.set_collapsed(False)
                controller.refresh()
                self.document.setModified(False)
                self.report["passed"] = True
                (output / "selection-overlay-qa.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False), encoding="utf-8")
            except Exception:
                self.finish_error()

        @staticmethod
        def rect(value):
            return [value.x(), value.y(), value.width(), value.height()]

        def finish_error(self):
            self.report["traceback"] = traceback.format_exc()
            if self.document:
                self.document.setModified(False)
            if self.window:
                self.window.grab().save(str(output / "selection-window-error.png"), "PNG")
            (output / "selection-overlay-qa.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False), encoding="utf-8")

    _runner = Runner()
    QTimer.singleShot(0, _runner.start)
    return _runner


def prepare(directory):
    directory = Path(directory).resolve()
    project = Path(__file__).resolve().parents[1]
    resources = directory / "resources"
    package = resources / "pykrita" / "mori_krita"
    if package.exists():
        raise RuntimeError("Choose a new empty QA directory")
    package.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(project / "mori_krita", package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copy2(project / "mori_krita.desktop", package.parent / "mori_krita.desktop")
    startup = ("from PyQt5.QtCore import QTimer\nimport sys\n"
               "sys.path.insert(0, %r)\nfrom selection_overlay_runtime_qa import run\n"
               "QTimer.singleShot(1800, lambda: run(%r))\n" % (str(project / "tests"), str(directory / "output")))
    (package / "__init__.py").write_text(startup, encoding="utf-8")
    instructions = {"resource_directory": str(resources), "output_directory": str(directory / "output"),
                    "command": '"C:\\Program Files\\Krita (x64)\\bin\\krita.exe" --nosplash --resource-location "%s"' % resources,
                    "requires": "Close normal Krita first; mori_krita must already be enabled. All networking uses an offline fixture."}
    (directory / "launch-instructions.json").write_text(json.dumps(instructions, indent=2, ensure_ascii=False), encoding="utf-8")
    return instructions


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.prepare), indent=2, ensure_ascii=False))
