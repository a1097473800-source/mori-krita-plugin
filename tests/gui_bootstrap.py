"""Opt-in, real-Krita GUI integration runner using only mock_client.

In Krita's Scripter, add this tests directory to sys.path then call run().
Or run this file with --prepare to create an isolated resource tree. The normal
plugin id must already be enabled in Krita, as after the production installer.
This script never launches Krita and never changes the user's Krita settings.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import http.cookiejar
import json
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback
import urllib.request

_active_runner = None


class MemorySettings:
    def __init__(self):
        self.values = {"serverUrl": "https://qa.example.invalid", "autoImport": True}

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type is not None else value

    def setValue(self, key, value):
        self.values[key] = value


def run(output_directory=None):
    """Create a QA-only dock/document and write integration.json on completion."""
    global _active_runner
    from PyQt5.QtCore import QObject, QTimer, Qt, pyqtSignal
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QImage
    from krita import Krita, Selection
    import mori_krita.docker as docker_module
    from mock_client import FakeMoriClient, SETTINGS

    if _active_runner is not None:
        return _active_runner
    output = Path(output_directory or Path(__file__).resolve().parents[1] / "qa-output")
    output.mkdir(parents=True, exist_ok=True)

    class Runner(QObject):
        completed = pyqtSignal(object)

        def __init__(self):
            super().__init__()
            self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="Mori GUI QA")
            self.completed.connect(self.finish)
            self.report = {"passed": False, "mode": "isolated mock client / real Krita GUI",
                           "external_requests": 0, "paid_model_calls": 0, "runtime": Krita.instance().version()}
            self.finished = False
            self.dock = None
            self.window = None
            self.document = None
            self.timeout = QTimer(self)
            self.timeout.setSingleShot(True)
            self.timeout.timeout.connect(lambda: self.finish({"error": "GUI QA exceeded 60 seconds"}))
            self.ready_seen = False

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
                    gateway = original_gateway(client, web_root, output / "gui-workspace", invoke, notify)
                    asset = client.assets()[0]
                    snapshot = {"name": "Krita 插件 · 隔离验收", "edges": [], "appliedCommandIds": [],
                                "viewport": {"x": 25, "y": 25, "zoom": .65}, "expectedRevision": 0,
                                "nodes": [
                                    {"id": "qa-reference", "type": "image", "position": {"x": 40, "y": 80},
                                     "style": {"width": 320, "height": 240}, "data": {"label": asset["name"], "settings": SETTINGS,
                                     "assetId": asset["id"], "url": asset["url"], "assetWidth": 320, "assetHeight": 240, "status": "completed"}},
                                    {"id": "qa-creator", "type": "creator", "position": {"x": 470, "y": 80},
                                     "style": {"width": 500, "height": 500}, "selected": True,
                                     "data": {"label": "Mori 测试创作家", "settings": SETTINGS, "agentModel": "qa-chat"}}
                                ]}
                    gateway.store.put(gateway._account_key(), snapshot)
                    return gateway

                docker_module.MoriClient = FakeMoriClient
                docker_module.MoriGateway = isolated_gateway
                self.document = native.createDocument(512, 384, "Mori 插件验收 · 合成素材", "RGBA", "U8", "sRGB-elle-V2-srgbtrc.icc", 72.0)
                active.addView(self.document)
                from mock_client import pattern_png
                image = QImage.fromData(pattern_png(0, 512, 384)).convertToFormat(QImage.Format_ARGB32)
                layer = self.document.createNode("QA 合成参考层", "paintlayer")
                self.document.rootNode().addChildNode(layer, None)
                layer.setPixelData(image.constBits().asstring(image.byteCount()), 0, 0, 512, 384)
                self.document.setActiveNode(layer)
                selection = Selection()
                selection.select(32, 40, 128, 96, 255)
                self.document.setSelection(selection)
                self.document.refreshProjection()
                self.document.waitForDone()
                self.dock = docker_module.MoriDocker()
                self.dock._settings = MemorySettings()
                self.dock._auto.setChecked(True)
                self.dock._web.ready.connect(self.ready)
                self.dock._web.error.connect(lambda message: self.report.update(webview_error=message))
                self.window.addDockWidget(Qt.RightDockWidgetArea, self.dock)
                self.dock.show()
                self.window.resizeDocks([self.dock], [880], Qt.Horizontal)
                self.timeout.start(60000)
            except Exception:
                self.finish({"traceback": traceback.format_exc()})

        def ready(self):
            if self.ready_seen:
                return
            self.ready_seen = True
            self.report["webview_ready"] = True
            future = self.pool.submit(self.pipeline)
            future.add_done_callback(lambda result: self.completed.emit(result.result()))

        def pipeline(self):
            try:
                gateway = self.dock._gateway
                opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
                opener.open(gateway.url, timeout=10).read()

                def request(path, payload=None):
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
                    req = urllib.request.Request(gateway.origin + path, data=body,
                                                 headers={"Origin": gateway.origin, "X-Mori-User": gateway.client.user_id or "",
                                                          "Content-Type": "application/json"})
                    with opener.open(req, timeout=20) as response:
                        return json.loads(response.read())

                def wait_for_receipts(count):
                    deadline = time.monotonic() + 12
                    while time.monotonic() < deadline:
                        request("/api/jobs")
                        request("/api/assets")
                        with gateway.lock:
                            if len(gateway.receipts) >= count:
                                return
                        time.sleep(.15)
                    raise AssertionError("Result import did not reach %s receipts; failed=%s" % (count, gateway.failed))

                session = request("/api/auth/session")
                assert session["user"]["id"] == "mori-qa-user"
                assert request("/api/config")["configured"]["openai"] is True
                assert request("/api/credits")["balance"]["available"] == 1000
                gateway.capture("selection")
                captured = gateway.client.assets()[-1]
                assert (captured["width"], captured["height"]) == (128, 96)
                (output / "gui-captured-selection.png").write_bytes(gateway.client.download_asset(captured["url"]))
                settings = {**SETTINGS, "prompt": "隔离测试：生成一个紫色产品图"}
                first = request("/api/generate", {**settings, "nodeId": "qa-direct-image", "references": [captured["id"]], "clientRequestId": "qa-direct-0001"})
                wait_for_receipts(1)
                conversation = request("/api/creator/conversations", {"title": "Krita 智能体集成测试"})
                context = {"nodes": [{"id": "qa-reference", "label": "Krita 选区", "assetId": captured["id"], "position": {"x": 0, "y": 0}}], "edges": [], "selectedIds": ["qa-reference"]}
                turn = request("/api/creator/conversations/" + conversation["id"] + "/messages",
                               {"message": "参考选区生成一张暖色产品图", "references": [captured["id"]], "imageSettings": SETTINGS,
                                "agentModel": "qa-chat", "context": context, "clientRequestId": "qa-agent-0001"})
                wait_for_receipts(2)
                full = request("/api/creator/conversations/" + conversation["id"])
                assert full["turns"][-1]["status"] == "completed"
                commands = request("/api/assistant/commands")
                assert len(commands) >= 2
                for _ in range(6):
                    request("/api/jobs")
                    request("/api/assets")
                assert len(gateway.receipts) == 2
                assert len(gateway.client.jobs()) == 2
                return {"account_session": True, "credits_mock_protocol": True, "selection_capture": True,
                        "generation_to_layer": True, "agent_to_layer": True, "duplicate_poll_no_reimport": True,
                        "generation_job_id": first["id"], "agent_turn_id": turn["id"], "receipts": 2,
                        "fixture_requests": len(gateway.client.requests)}
            except Exception:
                return {"traceback": traceback.format_exc()}

        def finish(self, result):
            if self.finished:
                return
            self.finished = True
            self.timeout.stop()
            self.report.update(result)
            try:
                if self.document:
                    children = self.document.rootNode().childNodes()
                    names = [node.name() for node in children]
                    self.report["krita_layer_names"] = names
                    assert len(children) == 3, "Expected original + generated + agent result layers"
                    assert sum(name.startswith("Mori · ") for name in names) == 2
                    self.document.refreshProjection()
                    self.document.waitForDone()
                    self.document.projection().save(str(output / "gui-document.png"), "PNG")
                    self.document.setModified(False)
                    self.report["actual_krita_layers"] = True
                if self.window:
                    self.window.grab().save(str(output / "gui-window-qt.png"), "PNG")
                self.report["passed"] = bool(result.get("generation_to_layer") and result.get("agent_to_layer")
                                              and self.report.get("actual_krita_layers") and self.report.get("webview_ready"))
            except Exception:
                self.report["verification_traceback"] = traceback.format_exc()
            (output / "integration.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False), encoding="utf-8")
            if self.dock:
                self.dock._status("隔离验收通过：生成与智能体结果均已进入 Krita 图层。" if self.report["passed"] else "隔离验收未完成，请查看 integration.json。")
            self.pool.shutdown(wait=False)

    _active_runner = Runner()
    QTimer.singleShot(0, _active_runner.start)
    return _active_runner


def prepare(directory):
    """Create test resources only; never start an app or alter user config."""
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
               "sys.path.insert(0, %r)\nfrom gui_bootstrap import run\n"
               "QTimer.singleShot(1800, lambda: run(%r))\n" % (str(project / "tests"), str(directory / "output")))
    (package / "__init__.py").write_text(startup, encoding="utf-8")
    instructions = {"resource_directory": str(resources), "output_directory": str(directory / "output"),
                    "command": '"C:\\Program Files\\Krita (x64)\\bin\\krita.exe" --nosplash --resource-location "%s"' % resources,
                    "requires": "Close the normal Krita instance first; plugin id mori_krita must already be enabled. No user configuration is changed.",
                    "alternative": "In an existing Krita Scripter, insert the tests directory in sys.path, import gui_bootstrap and call gui_bootstrap.run()."}
    (directory / "launch-instructions.json").write_text(json.dumps(instructions, indent=2, ensure_ascii=False), encoding="utf-8")
    return instructions


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(prepare(arguments.prepare), indent=2, ensure_ascii=False))
