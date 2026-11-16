"""Account-billed HTTP protocol, real libkis layers and live-mode race regressions."""
import base64
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType("mori_realtime_fixture")
package.__path__ = [str(ROOT / "mori_krita")]
sys.modules[package.__name__] = package
task = importlib.import_module(package.__name__ + ".realtime_task")
api = importlib.import_module(package.__name__ + ".api")
gateway_module = importlib.import_module(package.__name__ + ".gateway")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII=")
SETTINGS = {"model": "gemini-nano-banana-2.1", "resolution": "1K", "aspect": "1:1",
            "quality": "low", "prompt": "磨砂金属鼠标", "count": 1}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, value, *, raw=False):
        data = value if raw else json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "image/png" if raw else "application/json")
        self.send_header("Content-Length", str(len(data)))
        if self.path == "/api/auth/login":
            self.send_header("Set-Cookie", "mori_session=fixture-session; Path=/; HttpOnly")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.calls.append((self.command, self.path, dict(self.headers), None))
        if self.path == "/api/assets":
            self.reply([{"id": "generated", "url": "/media/result.png", "mime": "image/png"}])
        elif self.path == "/api/jobs":
            self.reply([self.server.job])
        elif self.path.startswith("/media/"):
            self.reply(PNG, raw=True)
        else:
            self.reply({})

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.calls.append((self.command, self.path, dict(self.headers), value))
        if self.path == "/api/auth/login":
            self.reply({"user": {"id": "account-a", "name": "QA"}})
        elif self.path == "/api/credits/quote":
            self.reply({"enforcement": True, "totalPoints": 5, "available": self.server.balance})
        elif self.path == "/api/assets":
            self.reply({"id": "input-" + str(len(self.server.calls))})
        elif self.path == "/api/generate":
            self.server.balance -= 5
            self.server.job = {"id": "job-a", "status": "queued" if self.server.slow else "completed",
                               "assetIds": ["generated"]}
            if self.server.drop:
                self.close_connection = True
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.reply(self.server.job)
        else:
            self.reply({})


class RealtimeProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.worker.join()

    def setUp(self):
        self.server.calls, self.server.balance = [], 30
        self.server.slow = self.server.drop = False
        self.temp = tempfile.TemporaryDirectory()
        self.client = api.MoriClient("http://127.0.0.1:%d" % self.server.server_port, _proxy_config={})
        self.client.login("test@example.invalid", "test-only")
        self.gateway = gateway_module.MoriGateway(self.client, Path(self.temp.name), Path(self.temp.name), lambda *_: None)
        self.gateway._sync_identity()
        self.identity = (self.gateway.epoch, self.client.user_id)
        self.snapshot = SimpleNamespace(target=object())

    def tearDown(self):
        self.gateway.close()
        self.temp.cleanup()

    def run_task(self, **kwargs):
        return task.run_realtime_task(self.gateway, [self.snapshot], SETTINGS,
                                      expected_identity=self.identity,
                                      prepare=lambda *args, **kw: SimpleNamespace(image_bytes=PNG, mime="image/png"),
                                      poll_interval=.01, **kwargs)

    def requests(self, path):
        return [call for call in self.server.calls if call[1] == path]

    def test_generation_uses_account_cookie_quote_and_one_request(self):
        result = self.run_task()
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["bytes"], PNG)
        self.assertIs(result["target"], self.snapshot.target)
        self.assertEqual(self.server.balance, 25)
        calls = self.requests("/api/generate")
        self.assertEqual(len(calls), 1)
        self.assertIn("mori_session=fixture-session", calls[0][2]["Cookie"])
        self.assertEqual(calls[0][2]["X-Mori-User"], "account-a")
        self.assertEqual(calls[0][3]["count"], 1)
        self.assertEqual(len(calls[0][3]["references"]), 1)
        self.assertTrue(calls[0][3]["clientRequestId"])
        self.assertEqual(self.gateway.job_targets, {})  # Workbench cannot auto-import it twice.

    def test_status_follows_actual_queue_generation_and_download(self):
        self.server.slow = True
        stages = []

        def progress(state, message):
            stages.append(state)
            self.assertTrue(message)
            if state == "queued":
                self.server.job["status"] = "running"
            elif state == "generating":
                self.server.job["status"] = "completed"

        result = self.run_task(notify=progress)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(stages, ["preparing", "uploading", "submitting", "queued", "generating", "downloading"])
        self.assertEqual(len(self.requests("/api/generate")), 1)

    def test_server_cancel_is_reported_as_stopped(self):
        self.server.slow = True

        def progress(state, message):
            if state == "queued":
                self.server.job.update(status="cancelled", error="任务已取消")

        result = self.run_task(notify=progress)
        self.assertEqual(result["error_state"], "stopped")
        self.assertEqual(result["error"], "任务已取消")

    def test_unchanged_snapshot_costs_nothing_and_manual_request_can_repeat(self):
        first = self.run_task()
        second = self.run_task(previous_signature=first["signature"])
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(len(self.requests("/api/generate")), 1)
        self.assertEqual(len(self.requests("/api/credits/quote")), 1)
        self.run_task()
        self.assertEqual(len(self.requests("/api/assets")), 3)  # One upload, two result listings.
        self.assertEqual(self.server.balance, 20)

    def test_insufficient_credits_neither_uploads_nor_submits(self):
        self.server.balance = 4
        result = self.run_task()
        self.assertEqual(result["status"], "failed")
        self.assertIn("积分不足", result["error"])
        self.assertEqual(self.requests("/api/assets"), [])
        self.assertEqual(self.requests("/api/generate"), [])

    def test_changed_account_stops_before_pixels_leave(self):
        self.client.clear_session()
        result = self.run_task()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(self.requests("/api/assets"), [])
        self.assertEqual(self.requests("/api/generate"), [])

    def test_disconnect_after_billing_is_uncertain_and_never_replayed(self):
        self.server.drop = True
        result = self.run_task()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["error_state"], "uncertain")
        self.assertEqual(len(self.requests("/api/generate")), 1)
        self.assertEqual(self.server.balance, 25)

    def test_timeout_keeps_history_job_and_never_resubmits(self):
        self.server.slow = True
        result = self.run_task(timeout=.55)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["job_id"], "job-a")
        self.assertEqual(result["error_state"], "timeout")
        self.assertEqual(len(self.requests("/api/generate")), 1)

    def test_cancelled_before_start_makes_no_paid_request(self):
        cancel = threading.Event()
        cancel.set()
        self.assertEqual(self.run_task(cancel=cancel)["status"], "failed")
        self.assertEqual(self.requests("/api/generate"), [])

    def test_reference_roles_are_mapped_to_uploaded_image_numbers(self):
        snapshots = [SimpleNamespace(target=SimpleNamespace(layer_id="")),
                     SimpleNamespace(target=SimpleNamespace(layer_id="hidden-style"))]
        settings = {**SETTINGS, "reference_bindings": [
            {"layer_id": "hidden-style", "role": "style"},
            {"layer_id": "hidden-style", "role": "palette"}]}
        prompt = task.reference_prompt(settings, snapshots, [1, 2])
        self.assertIn("Image 2 — 风格参考", prompt)
        self.assertIn("Image 2 — 配色参考", prompt)
        self.assertIn("do not copy its subject or composition", prompt)
        self.assertIn(SETTINGS["prompt"], prompt)

    def test_deduplicated_reference_keeps_its_actual_image_number(self):
        snapshots = [SimpleNamespace(target=SimpleNamespace(layer_id="main")),
                     SimpleNamespace(target=SimpleNamespace(layer_id="material"))]
        settings = {**SETTINGS, "reference_bindings": [{"layer_id": "material", "role": "material"}]}
        prompt = task.reference_prompt(settings, snapshots, [1, 1])
        self.assertIn("Image 1 — 材质参考", prompt)
        self.assertNotIn("Image 2", prompt)

    def test_explicit_composition_reference_can_override_default_framing(self):
        snapshots = [SimpleNamespace(target=SimpleNamespace(layer_id="layout"))]
        settings = {**SETTINGS, "reference_bindings": [{"layer_id": "layout", "role": "composition"}]}
        self.assertIn("assigned composition reference for framing", task.reference_prompt(settings, snapshots, [1]))

    def test_full_variation_uploads_only_explicit_references_with_correct_numbering(self):
        main = SimpleNamespace(target=SimpleNamespace(layer_id="main"))
        reference = SimpleNamespace(target=SimpleNamespace(layer_id="style"))
        settings = {**SETTINGS, "variation": 100, "reference_bindings": [
            {"layer_id": "style", "role": "style"}]}
        prepared = []

        def prepare(snapshot, **kwargs):
            prepared.append(snapshot)
            return SimpleNamespace(image_bytes=PNG, mime="image/png")

        result = task.run_realtime_task(self.gateway, [main, reference], settings,
                                       expected_identity=self.identity, prepare=prepare)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(prepared, [reference])
        uploads = [call for call in self.requests("/api/assets") if call[0] == "POST"]
        self.assertEqual(len(uploads), 1)
        request = self.requests("/api/generate")[0][3]
        self.assertEqual(len(request["references"]), 1)
        self.assertIn("Image 1 — 风格参考", request["prompt"])
        self.assertIn("canvas is not provided", request["prompt"])
        self.assertNotIn("It is the main drawing", request["prompt"])
        self.assertIs(result["target"], main.target)

    def test_full_variation_without_valid_references_never_prepares_canvas(self):
        main = SimpleNamespace(target=SimpleNamespace(layer_id=None))
        settings = {**SETTINGS, "variation": 100, "reference_bindings": [
            {"layer_id": None, "role": "style"}, {"layer_id": "", "role": "palette"}]}
        prepare = mock.Mock(side_effect=AssertionError("Canvas must not be prepared"))
        result = task.run_realtime_task(self.gateway, [main], settings,
                                       expected_identity=self.identity, prepare=prepare)
        self.assertEqual(result["status"], "completed", result)
        prepare.assert_not_called()
        self.assertEqual([call for call in self.requests("/api/assets") if call[0] == "POST"], [])
        self.assertEqual(self.requests("/api/generate")[0][3]["references"], [])

    def test_canvas_changes_at_full_variation_do_not_repeat_billing(self):
        settings = {**SETTINGS, "variation": 100}
        first = task.run_realtime_task(self.gateway, [SimpleNamespace(target="old canvas")], settings,
                                       expected_identity=self.identity)
        second = task.run_realtime_task(self.gateway, [SimpleNamespace(target="edited canvas")], settings,
                                        expected_identity=self.identity, previous_signature=first["signature"])
        self.assertEqual(second["status"], "unchanged", second)
        self.assertEqual(len(self.requests("/api/generate")), 1)
        self.assertEqual(self.server.balance, 25)

    def test_middle_variation_keeps_drawing_and_reference_and_instructs_shape_changes(self):
        main = SimpleNamespace(target=SimpleNamespace(layer_id="main"))
        reference = SimpleNamespace(target=SimpleNamespace(layer_id="material"))
        settings = {**SETTINGS, "variation": 50, "reference_bindings": [
            {"layer_id": "material", "role": "material"}]}
        prepare = mock.Mock(side_effect=[SimpleNamespace(image_bytes=PNG, mime="image/png"),
                                         SimpleNamespace(image_bytes=PNG + b"reference", mime="image/png")])
        result = task.run_realtime_task(self.gateway, [main, reference], settings,
                                       expected_identity=self.identity, prepare=prepare)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual([call.args[0] for call in prepare.call_args_list], [main, reference])
        request = self.requests("/api/generate")[0][3]
        self.assertEqual(len(request["references"]), 2)
        self.assertIn("Requested change amount: 50/100", request["prompt"])
        self.assertIn("product shape", request["prompt"])
        self.assertIn("Image 2 — 材质参考", request["prompt"])
        self.assertNotIn("variation", request)


try:
    from PyQt5.QtCore import QByteArray
    from PyQt5.QtGui import QImage, QColor
    from PyQt5.QtWidgets import QWidget
    from PyQt5 import sip
    from krita import Krita
    bridge_module = importlib.import_module(package.__name__ + ".krita_bridge")
    layers_module = importlib.import_module(package.__name__ + ".realtime_bridge")
    panel_module = importlib.import_module(package.__name__ + ".realtime_panel")
    controller_module = importlib.import_module(package.__name__ + ".realtime_controller")
except ImportError:
    QWidget = None


@unittest.skipIf(QWidget is None, "Real Qt/libkis needs official kritarunner")
class RealtimeNativeTests(unittest.TestCase):
    def setUp(self):
        native = Krita.instance()
        self.doc = native.createDocument(96, 64, "Realtime synthetic test", "RGBA", "U8", bridge_module.SRGB_PROFILE, 72.)
        self.application = SimpleNamespace(activeDocument=lambda: self.doc, documents=native.documents)
        self.bridge = bridge_module.KritaBridge(self.application)
        self.subject = self.add_layer("主体", "#9074dd", 0, 0, 96, 64)
        self.reference = self.add_layer("参考", "#ddaa55", 12, 8, 32, 24)
        self.doc.setActiveNode(self.subject)
        self.doc.refreshProjection()
        self.doc.waitForDone()
        self.active_before = self.doc.activeNode()  # Headless runner has no view selection.

    def tearDown(self):
        self.doc.setModified(False)
        self.doc.close()

    def add_layer(self, name, color, x, y, w, h):
        layer = self.doc.createNode(name, "paintlayer")
        image = QImage(w, h, QImage.Format_ARGB32)
        image.fill(QColor(color))
        layer.setPixelData(QByteArray(bridge_module._image_bytes(image)), x, y, w, h)
        self.doc.rootNode().addChildNode(layer, None)
        self.doc.waitForDone()
        return layer

    def ids(self):
        return self.subject.uniqueId().toString(), [self.reference.uniqueId().toString()]

    def test_snapshots_preserve_subject_canvas_and_crop_references(self):
        snapshots = layers_module.capture_layers(self.bridge, *self.ids())
        self.assertEqual((snapshots[0].width, snapshots[0].height), (96, 64))
        self.assertEqual((snapshots[1].width, snapshots[1].height), (32, 24))
        self.assertEqual(snapshots[0].image.pixelColor(0, 0).name(), "#9074dd")
        self.assertEqual(snapshots[1].image.pixelColor(0, 0).name(), "#ddaa55")
        self.assertEqual(self.doc.activeNode(), self.active_before)

    def test_zero_variation_is_lossless_without_account_prompt_or_preparation(self):
        snapshots = layers_module.capture_layers(self.bridge, "canvas", [])
        prepare = mock.Mock(side_effect=AssertionError("Zero must not prepare or upload"))
        result = task.run_realtime_task(None, snapshots, {"variation": 0, "prompt": ""},
                                       expected_identity=None, prepare=prepare)
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["local_original"])
        decoded = QImage.fromData(result["bytes"]).convertToFormat(QImage.Format_ARGB32)
        expected = snapshots[0].image.convertToFormat(QImage.Format_ARGB32)
        self.assertEqual(decoded.size(), expected.size())
        self.assertEqual(bridge_module._image_bytes(decoded), bridge_module._image_bytes(expected))
        self.assertIs(result["target"], snapshots[0].target)
        prepare.assert_not_called()
        unchanged = task.run_realtime_task(None, snapshots, {"variation": 0}, expected_identity=None,
                                          previous_signature=result["signature"])
        self.assertEqual(unchanged["status"], "unchanged")

    def test_cancelled_zero_variation_returns_without_encoding(self):
        cancel = threading.Event()
        cancel.set()
        result = task.run_realtime_task(None, [], {"variation": 0}, expected_identity=None, cancel=cancel)
        self.assertEqual(result["status"], "failed")
        self.assertIn("暂停", result["error"])

    def test_generated_layer_import_does_not_feed_back_and_preserves_source(self):
        from mock_client import pattern_png
        before = layers_module.source_signature(self.bridge, *self.ids())
        choices_before = {item["id"] for item in layers_module.layer_choices(self.bridge)[1]}
        source = bytes(self.subject.pixelData(0, 0, 96, 64))
        target = layers_module.capture_layers(self.bridge, *self.ids())[0].target
        result = {"bytes": pattern_png(), "target": target}
        first = layers_module.import_result(self.bridge, result)
        second = layers_module.import_result(self.bridge, result)
        self.doc.waitForDone()
        self.assertFalse(first.visible())
        self.assertTrue(second.visible())
        self.assertEqual(bytes(self.subject.pixelData(0, 0, 96, 64)), source)
        self.assertEqual(self.doc.activeNode(), self.active_before)
        self.assertEqual(layers_module.source_signature(self.bridge, *self.ids()), before)
        self.assertEqual({item["id"] for item in layers_module.layer_choices(self.bridge)[1]}, choices_before)

    def test_real_pixels_change_signature(self):
        before = layers_module.source_signature(self.bridge, *self.ids())
        self.subject.setPixelData(QByteArray(bytes([0, 0, 255, 255]) * 20 * 20), 0, 0, 20, 20)
        self.doc.refreshProjection()
        self.doc.waitForDone()
        self.assertNotEqual(layers_module.source_signature(self.bridge, *self.ids()), before)

    def test_default_canvas_is_visible_composite_and_hidden_reference_is_readable(self):
        self.reference.setVisible(False)
        self.doc.refreshProjection()
        self.doc.waitForDone()
        snapshots = layers_module.capture_layers(self.bridge, "canvas", self.ids()[1])
        self.assertEqual(snapshots[0].target.source, "canvas")
        self.assertEqual((snapshots[0].width, snapshots[0].height), (96, 64))
        self.assertEqual(snapshots[0].image.pixelColor(12, 8).name(), "#9074dd")
        self.assertEqual(snapshots[1].image.pixelColor(0, 0).name(), "#ddaa55")
        self.assertFalse(self.reference.visible())
        self.reference.setVisible(True)
        self.doc.refreshProjection()
        self.doc.waitForDone()
        visible = layers_module.capture_layers(self.bridge, "canvas", [])[0]
        self.assertEqual(visible.image.pixelColor(12, 8).name(), "#ddaa55")

    def test_canvas_ignores_unassigned_hidden_layers_but_tracks_assigned_ones(self):
        self.reference.setVisible(False)
        self.doc.refreshProjection()
        self.doc.waitForDone()
        original = layers_module.source_signature(self.bridge, "canvas", [])
        assigned = layers_module.source_signature(self.bridge, "canvas", self.ids()[1])
        self.reference.setOpacity(120)
        self.doc.refreshProjection()
        self.doc.waitForDone()
        self.assertEqual(original, layers_module.source_signature(self.bridge, "canvas", []))
        self.assertNotEqual(assigned, layers_module.source_signature(self.bridge, "canvas", self.ids()[1]))

    def test_canvas_overlay_never_changes_inputs_or_causes_feedback(self):
        from mock_client import pattern_png
        before = layers_module.capture_layers(self.bridge, "canvas", [])[0]
        signature = layers_module.source_signature(self.bridge, "canvas", [])
        layer = layers_module.import_result(self.bridge, {"bytes": pattern_png(), "target": before.target})
        self.doc.refreshProjection()
        self.doc.waitForDone()
        modified = self.doc.modified()
        after = layers_module.capture_layers(self.bridge, "canvas", [])[0]
        self.assertEqual(layers_module.source_signature(self.bridge, "canvas", []), signature)
        self.assertEqual(bridge_module._image_bytes(before.image), bridge_module._image_bytes(after.image))
        self.assertTrue(layer.visible())
        self.assertEqual(self.doc.modified(), modified)
        self.assertEqual(self.doc.activeNode(), self.active_before)


@unittest.skipIf(QWidget is None, "Native controller needs official kritarunner")
class RealtimeControllerTests(RealtimeNativeTests):
    def setUp(self):
        super().setUp()
        self.window = QWidget()
        self.window._bridge = self.bridge
        self.gateway = SimpleNamespace(epoch=1, client=SimpleNamespace(user_id="user-a"), _sync_identity=lambda: None)
        self.window._gateway = self.gateway
        self.window._ensure_gateway = lambda: self.gateway
        self.panel = panel_module.RealtimePanel(self.window)
        self.controller = controller_module.RealtimeController(self.window, self.panel)
        self.controller._identity = (1, "user-a")
        self.controller._authenticated = True
        self.controller.refresh()
        self.panel.subject.setCurrentIndex(self.panel.subject.findData(self.ids()[0]))
        self.controller._active = True
        self.panel.prompt.setPlainText("产品设计")
        self.work = []
        self.controller._work = lambda function, callback: self.work.append((function, callback))

    def tearDown(self):
        self.controller.shutdown()
        sip.delete(self.window)
        super().tearDown()

    def test_pause_preserves_inflight_progress_and_clears_pending_edits(self):
        self.panel.live.setChecked(True)
        self.controller.generate()
        marker = self.controller._task
        self.controller._progress(marker, "generating", "正在生成")
        self.panel.prompt.setPlainText("修改后的产品")
        self.assertTrue(self.panel._pending)
        self.panel.live.setChecked(False)
        self.assertEqual(self.panel.status_state, "generating")
        self.assertIs(self.controller._task, marker)
        self.assertFalse(self.controller._cancel.is_set())
        self.assertFalse(self.panel._pending)
        self.assertFalse(self.controller._dirty)
        self.controller._progress(marker, "downloading", "接收结果")
        self.assertEqual(self.panel.status_state, "downloading")
        self.assertIn("仍在处理", self.panel.status_badge.toolTip())

    def test_submit_generates_once_and_repeated_clicks_can_repeat_same_parameters(self):
        from mock_client import pattern_png
        self.panel.live.setChecked(True)
        self.panel.submit.click()
        self.assertFalse(self.panel.live.isChecked())
        self.assertEqual(len(self.work), 1)
        self.assertFalse(self.panel.submit.isEnabled())
        self.panel.submit.click()
        self.assertEqual(len(self.work), 1)
        with mock.patch.object(controller_module, "run_realtime_task") as request:
            self.work[0][0]()
            self.assertIsNone(request.call_args.kwargs["previous_signature"])
            self.assertEqual(request.call_args.args[2]["count"], 1)
        target = layers_module.capture_layers(self.bridge, *self.ids())[0].target
        result = {"status": "completed", "identity": (1, "user-a"), "bytes": pattern_png(),
                  "elapsed_seconds": 1., "signature": "same-input", "target": target}
        self.work[0][1](result, None)
        self.assertFalse(self.panel.live.isChecked())
        self.assertTrue(self.panel.submit.isEnabled())
        self.controller.refresh()
        self.assertEqual(len(self.work), 1)
        self.panel.submit.click()
        self.assertEqual(len(self.work), 2)
        with mock.patch.object(controller_module, "run_realtime_task") as request:
            self.work[1][0]()
            self.assertIsNone(request.call_args.kwargs["previous_signature"])

    def test_late_progress_cannot_replace_terminal_error_status(self):
        self.panel.live.setChecked(True)
        self.controller.generate()
        marker = self.controller._task
        self.work[0][1]({"status": "failed", "error": "等待超时，请查看记录", "error_state": "timeout"}, None)
        self.controller._progress(marker, "queued", "过期排队消息")
        self.assertEqual(self.panel.status_state, "timeout")
        self.assertIn("超时", self.panel.status.text())
        self.assertFalse(self.panel.live.isChecked())
        self.assertFalse(self.panel._busy)

    def test_idle_pause_and_waiting_for_edits_have_distinct_states(self):
        self.controller._idle_status()
        self.assertEqual(self.panel.status_state, "ready")
        self.panel.live.setChecked(True)
        self.assertEqual(self.panel.status_state, "waiting")
        self.panel.live.setChecked(False)
        self.assertEqual(self.panel.status_state, "paused")

    def test_multiple_edits_keep_a_single_inflight_task_and_stale_preview(self):
        from mock_client import pattern_png
        self.panel.writeback.setChecked(True)
        self.controller.generate()
        self.controller.generate()
        self.assertEqual(len(self.work), 1)
        self.panel.prompt.setPlainText("新修改")
        target = layers_module.capture_layers(self.bridge, *self.ids())[0].target
        result = {"status": "completed", "identity": (1, "user-a"), "bytes": pattern_png(),
                  "elapsed_seconds": 1., "signature": "first", "target": target}
        with mock.patch.object(controller_module, "import_result") as write:
            self.work[0][1](result, None)
            write.assert_not_called()
        self.assertIsNotNone(self.controller._result)
        self.assertIsNone(self.controller._task)
        self.assertIn("上一版", self.panel.status.text())

    def test_switching_account_discards_old_result_before_import(self):
        self.controller.generate()
        self.gateway.client.user_id = "user-b"
        self.work[0][1]({"status": "completed", "bytes": PNG}, None)
        self.assertIsNone(self.controller._result)
        self.assertFalse(self.panel.apply.isEnabled())
        self.assertFalse(self.panel.live.isChecked())

    def test_hiding_mode_pauses_and_deleted_subject_requires_new_choice(self):
        self.panel.live.setChecked(True)
        self.assertTrue(self.panel.live.isChecked())
        self.controller.set_active(False)
        self.assertFalse(self.panel.live.isChecked())
        self.subject.remove()
        self.doc.waitForDone()
        self.controller.refresh()
        self.assertEqual(self.panel.source_ids()[0], "canvas")
        self.assertFalse(self.panel.live.isChecked())
        self.assertTrue(self.panel.generate.isEnabled())

    def test_new_document_uses_canvas_without_manually_selecting_any_layer(self):
        self.controller._document_id = None
        self.controller.refresh()
        self.assertEqual(self.panel.source_ids(), ("canvas", []))
        self.assertTrue(self.panel.generate.isEnabled())

    def test_models_honor_server_quality_and_no_model_disables_submission(self):
        self.panel.set_models([{"id": "gpt-image-2.5", "name": "Image 2.5", "qualities": ["high"]}])
        self.assertEqual(self.panel.settings()["quality"], "high")
        self.panel.set_models([])
        self.controller._available()
        self.assertFalse(self.panel.generate.isEnabled())

    def test_zero_variation_previews_and_imports_without_login_prompt_or_model(self):
        self.controller._authenticated = False
        self.controller._identity = None
        self.window._gateway = None
        self.window._ensure_gateway = mock.Mock(side_effect=AssertionError("Local original needs no connection"))
        self.panel.set_models([])
        self.panel.prompt.clear()
        self.panel.variation.setValue(0)
        self.controller._available()
        self.assertTrue(self.panel.generate.isEnabled())
        self.panel.live.setChecked(True)
        self.assertTrue(self.panel.live.isChecked())
        self.controller._quote()
        self.assertEqual(self.work, [])
        self.controller.generate()
        self.assertEqual(len(self.work), 1)
        result = self.work[0][0]()
        self.work[0][1](result, None)
        self.assertTrue(self.panel.apply.isEnabled(), self.panel.status.text())
        source = bytes(self.subject.pixelData(0, 0, 96, 64))
        self.controller.apply()
        self.assertIsNone(self.controller._result)
        self.assertFalse(self.panel.apply.isEnabled())
        self.assertEqual(bytes(self.subject.pixelData(0, 0, 96, 64)), source)
        outputs = [node for node in self.doc.rootNode().childNodes() if node.name().startswith(layers_module.OUTPUT_PREFIX)]
        self.assertEqual(len(outputs), 1)
        self.window._ensure_gateway.assert_not_called()

    def test_new_document_clears_pending_reference_rows(self):
        self.panel.add_reference("style")
        self.assertEqual(len(self.panel.reference_rows), 1)
        self.controller._document_id = None
        self.controller.refresh()
        self.assertEqual(self.panel.reference_rows, [])


if __name__ == "__main__":
    unittest.main()
