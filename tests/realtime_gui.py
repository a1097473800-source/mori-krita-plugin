"""Opt-in live-mode integration in an isolated real Krita window; mock billing."""
import json
from pathlib import Path
import sys
import time
import traceback
from types import SimpleNamespace

_runner = None


def configure(docker):
    from mock_client import FakeMoriClient

    class MemorySettings:
        def __init__(self, *args):
            self.values = {"serverUrl": "https://qa.example.invalid", "selectionAssistant": False}

        def value(self, key, default=None, type=None):
            result = self.values.get(key, default)
            return type(result) if type else result

        def setValue(self, key, value):
            self.values[key] = value

    class OfflineClient(FakeMoriClient):
        def __init__(self, *args, **kwargs):
            super().__init__()
            self.session_version = 1
            self.generation_calls = 0
            self.quote_calls = 0

        def fork_session(self):
            return self

        def quote(self, settings):
            self.quote_calls += 1
            return {"enforcement": True, "totalPoints": 5, "available": 1000 - 5 * self.generation_calls}

        def config(self):
            return {"models": [
                {"id": "gemini-nano-banana-2.1", "name": "Banana 2.1", "provider": "banana",
                 "aspects": ["1:1", "4:3", "3:4"], "qualities": []},
                {"id": "gpt-image-2.5", "name": "Image 2.5", "provider": "openai",
                 "aspects": ["1:1", "4:3", "3:4"], "qualities": ["low", "medium", "high"]}],
                    "configured": {"openai": True, "banana": True}}

        def generate(self, *args, **kwargs):
            self.generation_calls += 1
            return super().generate(*args, **kwargs)

        def close(self):
            pass

    class OfflineUpdates:
        def __init__(self, base, bundled, storage, version):
            self.bundled = bundled

        def check(self, cancel):
            return SimpleNamespace(message="隔离界面验收 · 不连接真实模型", updated=False,
                                   release=SimpleNamespace(root=self.bundled, ui_version="offline-qa"))

    docker.MoriClient = OfflineClient
    docker.QSettings = MemorySettings
    docker.WindowsSessionStore = lambda: None
    docker.WebUpdateManager = OfflineUpdates


def run(output_directory):
    global _runner
    from PyQt5.QtCore import QObject, QTimer, Qt, QByteArray
    from PyQt5.QtGui import QImage
    from PyQt5.QtWidgets import QApplication
    from krita import Krita
    from mock_client import pattern_png
    from mori_krita.realtime_bridge import OUTPUT_PREFIX

    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)

    class Runner(QObject):
        def __init__(self):
            super().__init__()
            self.report = {"passed": False, "real_krita": Krita.instance().version(),
                           "external_model_calls": 0, "real_credits_spent": 0}
            self.started = time.monotonic()
            self.stage = "welcome"
            self.idle_started = None
            self.poll = QTimer(self)
            self.poll.setInterval(350)
            self.poll.timeout.connect(self.tick)
            self.poll.start()

        def tick(self):
            try:
                if time.monotonic() - self.started > 50:
                    raise AssertionError("GUI integration timed out at " + self.stage)
                native = Krita.instance()
                window = native.activeWindow()
                if not window:
                    return
                self.main = window.qwindow()
                self.dock = next((dock for dock in window.dockers() if dock.objectName() == "mori_krita"), None)
                if not self.dock:
                    return
                if self.stage == "welcome":
                    assert native.activeDocument() is None
                    assert self.dock.isHidden(), "Mori must not appear on the welcome page"
                    self.report["welcome_hidden"] = True
                    self.main.grab().save(str(output / "welcome.png"), "PNG")
                    self.doc = native.createDocument(1024, 768, "Mori 实时模式 · 隔离测试", "RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc", 72.)
                    window.addView(self.doc)
                    self.subject = self.doc.createNode("主体 · 产品草图", "paintlayer")
                    image = QImage.fromData(pattern_png(0, 1024, 768)).convertToFormat(QImage.Format_ARGB32)
                    self.subject.setPixelData(QByteArray(image.constBits().asstring(image.byteCount())), 0, 0, 1024, 768)
                    self.doc.rootNode().addChildNode(self.subject, None)
                    self.ref = self.doc.createNode("参考 · 金属材质", "paintlayer")
                    reference = QImage.fromData(pattern_png(1, 160, 120)).convertToFormat(QImage.Format_ARGB32)
                    self.ref.setPixelData(QByteArray(reference.constBits().asstring(reference.byteCount())), 0, 0, 160, 120)
                    self.doc.rootNode().addChildNode(self.ref, None)
                    self.ref.setVisible(False)
                    self.doc.setActiveNode(self.subject)
                    self.doc.refreshProjection()
                    self.doc.waitForDone()
                    self.original = bytes(self.subject.pixelData(0, 0, 1024, 768))
                    self.stage = "account"
                elif self.stage == "account":
                    controller = self.dock._realtime
                    if self.dock.isHidden() or not controller._authenticated:
                        return
                    self.report["canvas_shown"] = True
                    assert not self.dock._web_started, "Native mode should not load a webview"
                    self.report["webview_lazy"] = True
                    self.panel = self.dock._realtime_panel
                    self.panel.prompt.setPlainText("保持草图构图与产品轮廓，参考金属材质，生成精致的产品设计。")
                    controller.refresh()
                    self.panel.subject.setCurrentIndex(self.panel.subject.findData(self.subject.uniqueId().toString()))
                    self.panel.add_reference("material", self.ref.uniqueId().toString())
                    self.panel.writeback.setChecked(True)
                    self.panel.live.setChecked(True)
                    self.stage = "first-result"
                elif self.stage == "first-result":
                    controller = self.dock._realtime
                    outputs = [node for node in self.doc.rootNode().childNodes() if node.name().startswith(OUTPUT_PREFIX)]
                    if len(outputs) != 1 or controller._task is not None:
                        return
                    assert bytes(self.subject.pixelData(0, 0, 1024, 768)) == self.original
                    assert self.doc.activeNode() == self.subject
                    assert self.dock._gateway.client.generation_calls == 1
                    self.report["automatic_generation_and_writeback"] = True
                    self.report["input_pixels_preserved"] = True
                    self.report["active_subject_preserved"] = True
                    self.subject.setPixelData(QByteArray(bytes([90, 140, 210, 255]) * 48 * 48), 300, 250, 48, 48)
                    self.doc.refreshProjection()
                    self.doc.waitForDone()
                    self.stage = "second-result"
                elif self.stage == "second-result":
                    outputs = [node for node in self.doc.rootNode().childNodes() if node.name().startswith(OUTPUT_PREFIX)]
                    if len(outputs) != 2 or self.dock._realtime._task is not None:
                        return
                    assert sum(node.visible() for node in outputs) == 1
                    self.idle_started = time.monotonic()
                    self.stage = "stable"
                elif self.stage == "stable":
                    if time.monotonic() - self.idle_started < 5:
                        return
                    assert self.dock._gateway.client.generation_calls == 2, "Imported results must not cause a loop"
                    self.report["no_feedback_loop"] = True
                    self.report["mock_account_generation_calls"] = 2
                    self.report["reference_count"] = len(self.dock._gateway.client.jobs()[-1]["references"])
                    self.panel.live.setChecked(False)
                    self.dock._web.ready.connect(self.web_ready)
                    self.dock._set_mode(1)
                    self.stage = "webview"
                elif self.stage == "webview" and self.report.get("workbench_webview_ready"):
                    self.dock._set_mode(0)
                    self.stage = "complete"
                    QTimer.singleShot(500, self.finish)
            except Exception:
                self.report["error"] = traceback.format_exc()
                self.finish()

        def web_ready(self):
            self.report["workbench_webview_ready"] = True

        def finish(self):
            self.poll.stop()
            try:
                self.panel.live.setChecked(False)
                self.doc.setModified(False)
                self.report["dock_width"] = self.dock.width()
                self.report["minimum_width"] = self.dock.minimumWidth()
                self.report["layers"] = [node.name() for node in self.doc.rootNode().childNodes()]
                self.report["passed"] = bool(self.stage == "complete" and not self.report.get("error"))
                self.main.grab().save(str(output / "realtime-window.png"), "PNG")
                self.dock.grab().save(str(output / "realtime-panel.png"), "PNG")
            except Exception:
                self.report["finish_error"] = traceback.format_exc()
            (output / "integration.json").write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding="utf-8")

    _runner = Runner()
    return _runner
