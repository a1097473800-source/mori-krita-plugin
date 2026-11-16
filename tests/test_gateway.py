"""Loopback/identity/result-delivery tests without Qt or a live Mori server."""

import concurrent.futures
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
from urllib.parse import urlsplit


_SPEC = importlib.util.spec_from_file_location("mori_gateway", Path(__file__).resolve().parents[1] / "mori_krita" / "gateway.py")
gateway_module = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gateway_module)
MoriGateway = gateway_module.MoriGateway
GatewayError = gateway_module.GatewayError
CanvasStore = gateway_module.CanvasStore

PNG = b"\x89PNG\r\n\x1a\nfixture"


class ManualExecutor:
    """Deterministic delivery; catches functions just as a real Executor does."""
    def __init__(self):
        self.queue = []

    def submit(self, function, *args):
        future = concurrent.futures.Future()
        self.queue.append((future, function, args))
        return future

    def drain(self):
        while self.queue:
            future, function, args = self.queue.pop(0)
            try:
                future.set_result(function(*args))
            except Exception as error:
                future.set_exception(error)

    def shutdown(self, **kwargs):
        pass


class FakeClient:
    base_url = "https://morinet.chat"

    def __init__(self):
        self.user_id = "alice"
        self.user = {"id": "alice", "name": "Alice"}
        self.auth_enabled = True
        self.responses = {}
        self.calls = []
        self.canvas_calls = []
        self.downloads = []
        self.uploads = []
        self.raw_hook = None
        self.download_hook = None
        self.upload_hook = None

    def raw_request(self, method, path, body=None, content_type=None):
        self.calls.append((method, path, json.loads(body) if body else None))
        self.canvas_calls.append((method, path, getattr(self, "_canvas_id", None)))
        if self.raw_hook:
            self.raw_hook(method, path)
        result = self.responses.get((method, path), [])
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = result()
        status = 200
        if isinstance(result, tuple):
            status, result = result
        return status, {"content-type": "application/json"}, json.dumps(result).encode()

    def download_asset(self, path):
        self.downloads.append(path)
        if self.download_hook:
            self.download_hook()
        return PNG

    def upload_image(self, data, name):
        self.uploads.append((data, name))
        if self.upload_hook:
            self.upload_hook()
        return {"id": "captured-asset", "url": "/media/captured.png", "name": name}

    def clear_session(self):
        self.user_id = None
        self.user = None


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>Mori</title>", encoding="utf-8")
        (self.root / "secret.txt").write_text("PRIVATE", encoding="utf-8")
        self.client = FakeClient()
        self.target = "document-A"
        self.imports = []
        self.created_documents = []
        self.notices = []
        self.import_failure = None
        self.capture_hook = None
        self.gateway = MoriGateway(self.client, self.web, self.root / "state", self.invoke, self.notices.append)
        self.gateway.executor.shutdown(wait=True)
        self.executor = ManualExecutor()
        self.gateway.executor = self.executor
        self.gateway._sync_identity()

    def tearDown(self):
        self.gateway.close()
        self.temp.cleanup()

    def test_latest_website_routes_keep_explicit_methods_and_query(self):
        routes = (("GET", "/api/community?kind=style"),
                  ("GET", "/api/community/mine?kind=tool&sourceId=tool-1"),
                  ("GET", "/api/community/items/item-1?token=fixture-token"),
                  ("POST", "/api/community/preview"), ("POST", "/api/community/publish"),
                  ("POST", "/api/community/share"), ("POST", "/api/community/items/item-1/import"),
                  ("POST", "/api/community/items/item-1/revoke"),
                  ("POST", "/api/aesthetic/styles/style-1/copy"),
                  ("POST", "/api/tool-library/preview"),
                  ("POST", "/api/tool-library/drafts/draft-1/restore"),
                  ("POST", "/api/tool-library/tools/tool-1/preview"))
        for method, path in routes:
            with self.subTest(method=method, path=path):
                self.gateway.handle(method, path, b"{}" if method == "POST" else None,
                                    browser_user="alice", enforce_user=True)
                self.assertEqual(self.client.calls[-1][:2], (method, path))
                wrong = "POST" if method == "GET" else "GET"
                before = len(self.client.calls)
                with self.assertRaises(GatewayError):
                    self.gateway.handle(wrong, path, b"{}" if wrong == "POST" else None,
                                        browser_user="alice", enforce_user=True)
                self.assertEqual(len(self.client.calls), before)
        with self.assertRaises(GatewayError):
            self.gateway.handle("POST", "/api/community/admin", b"{}")

    def test_public_site_config_only_returns_server_address(self):
        status, _, data = self.gateway.handle("GET", "/krita/config")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), {"serverUrl": "https://morinet.chat"})
        with self.assertRaises(GatewayError):
            self.gateway.handle("POST", "/krita/config", b"{}")

    def test_smart_studio_endpoints_forward_with_exact_methods_and_account_headers(self):
        analysis_id = "00000000-0000-4000-8000-000000000001"
        routes = [("GET", "/api/tool-library/capabilities"),
                  ("POST", "/api/community/items/item-1/tool-preview"),
                  ("POST", "/api/community/items/item-1/run")]
        for kind in ("cmf-analysis", "design-analysis"):
            root = "/api/tool-library/" + kind
            routes.extend((("POST", root), ("GET", root + "/" + analysis_id),
                           ("POST", root + "/" + analysis_id + "/cancel")))
        for method, path in routes:
            with self.subTest(method=method, path=path):
                payload = b'{"expectedVersion":1,"clientRequestId":"studio-request"}' if method == "POST" else None
                self.gateway.handle(method, path, payload, browser_user="alice", enforce_user=True)
                self.assertEqual(self.client.calls[-1], (method, path, json.loads(payload) if payload else None))
                before = len(self.client.calls)
                for wrong in ({"GET", "POST", "PATCH", "DELETE"} - {method}):
                    with self.assertRaises(GatewayError):
                        self.gateway.handle(wrong, path, b"{}", browser_user="alice", enforce_user=True)
                with self.assertRaises(GatewayError):
                    self.gateway.handle(method, path, payload, browser_user="bob", enforce_user=True)
                self.assertEqual(len(self.client.calls), before)
        for path in ("/api/tool-library/design-analysis/not-a-task",
                     "/api/tool-library/cmf-analysis/" + analysis_id + "/delete",
                     "/api/community/items/item-1/run/extra", "/api/admin/community"):
            with self.subTest(path=path), self.assertRaises(GatewayError):
                self.gateway.handle("POST", path, b"{}")

    def test_community_tool_generation_keeps_frozen_krita_target_and_preview_is_read_only(self):
        preview = "/api/community/items/item-1/tool-preview"
        run = "/api/community/items/item-1/run"
        self.gateway.handle("POST", preview, b'{"clientRequestId":"community-preview"}')
        self.assertEqual(self.gateway.request_targets, {})
        self.client.responses[("POST", run)] = {"id": "tool-run", "jobId": "community-job"}
        self.gateway.handle("POST", run, b'{"clientRequestId":"community-request"}')
        self.assertEqual(self.gateway.job_targets["community-job"], "document-A")
        self.target = "document-B"
        self.gateway.handle("POST", run, b'{"clientRequestId":"community-request"}')
        self.assertEqual(self.gateway.job_targets["community-job"], "document-A")
        self.deliver(job_id="community-job", request_id="community-request")
        self.executor.drain()
        self.assertEqual(len(self.imports), 1)
        self.assertEqual(self.imports[0]["target"], "document-A")

    def test_community_and_share_load_pinned_spa_only(self):
        original = self.gateway.handle("GET", "/")[2]
        for path in ("/community", "/share/item-1?token=fixture"):
            self.assertEqual(self.gateway.handle("GET", path)[2], original)
        for path in ("/share/item-1/extra", "/unexpected", "/assets/missing.js"):
            with self.assertRaises(GatewayError):
                self.gateway.handle("GET", path)

    def test_gif_svg_and_safe_svg_preview_media_routes(self):
        for path in ("/media/0123abcd.gif", "/media/0123abcd.svg", "/media/0123abcd-preview.png"):
            self.gateway._validate_remote("GET", path)
            with self.assertRaises(GatewayError):
                self.gateway._validate_remote("POST", path)
        for path in ("/media/untrusted.svg", "/media/0123abcd.js", "/media/../../secret.svg"):
            with self.assertRaises(GatewayError):
                self.gateway._validate_remote("GET", path)

    def test_gateway_shutdown_closes_runtime_without_forgetting_sign_in(self):
        closed = []
        self.client.close = lambda: closed.append(True)

        def forget():
            raise AssertionError("Window close must not explicitly log out")

        self.client.clear_session = forget
        self.gateway.close()
        self.assertEqual(closed, [True])

    def invoke(self, operation, data):
        if operation == "target":
            return self.target
        if operation == "capture":
            if self.capture_hook:
                self.capture_hook()
            return SimpleNamespace(png_bytes=PNG, layer_name="Layer", document_name="Document")
        if operation == "import":
            if self.import_failure:
                raise self.import_failure
            self.imports.append(dict(data))
            target = data.get("target")
            if data.get("force_new") or target is None:
                target = "created-document-%s" % (len(self.created_documents) + 1)
                self.created_documents.append(target)
            return target
        raise AssertionError("Unexpected operation " + operation)

    def post_generation(self, job_id="job-1", request_id="request-123456"):
        payload = {"clientRequestId": request_id, "nodeId": "node-1"}
        self.client.responses[("POST", "/api/generate")] = {"id": job_id, **payload}
        return self.gateway.handle("POST", "/api/generate", json.dumps(payload).encode())

    def deliver(self, job_id="job-1", asset_ids=("asset-1",), request_id="request-123456"):
        assets = [{"id": asset_id, "url": "/media/%s.png" % asset_id, "name": asset_id} for asset_id in asset_ids]
        jobs = [{"id": job_id, "clientRequestId": request_id, "status": "completed", "assetIds": list(asset_ids)}]
        self.client.responses[("GET", "/api/assets")] = assets
        self.client.responses[("GET", "/api/jobs")] = jobs
        self.gateway.handle("GET", "/api/assets")
        self.gateway.handle("GET", "/api/jobs")

    def test_static_files_cannot_escape_web_root(self):
        for route in ("/../secret.txt", "/%2e%2e/secret.txt", "/%2e%2e%2fsecret.txt"):
            with self.subTest(route=route), self.assertRaises(GatewayError):
                self.gateway.handle("GET", route)
        status, _, data = self.gateway.handle("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Mori", data)

    def test_dangerous_remote_routes_are_not_forwarded(self):
        for route in ("/api/admin/credits", "/api/auth/register", "/api/anything", "/media/../secret"):
            with self.subTest(route=route), self.assertRaises(GatewayError):
                self.gateway.handle("GET", route)
        self.assertEqual(self.client.calls, [])

    def test_canvas_state_is_account_and_server_scoped(self):
        payload = {"expectedRevision": 0, "name": "Alice", "nodes": [], "edges": []}
        self.gateway.handle("PUT", "/api/state", json.dumps(payload).encode())
        self.client.user_id = "bob"
        status, _, data = self.gateway.handle("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["revision"], 0)
        self.client.user_id = "alice"
        _, _, data = self.gateway.handle("GET", "/api/state")
        self.assertEqual(json.loads(data)["name"], "Alice")
        self.assertEqual(self.client.calls, [])

    def test_canvas_revision_prevents_stale_overwrite(self):
        value = {"expectedRevision": 0, "nodes": [], "edges": []}
        self.gateway.handle("PUT", "/api/state", json.dumps(value).encode())
        with self.assertRaises(GatewayError) as raised:
            self.gateway.handle("PUT", "/api/state", json.dumps(value).encode())
        self.assertEqual(raised.exception.status, 409)

    def test_corrupt_saved_state_is_not_replaced_with_an_empty_autosave(self):
        store = CanvasStore(self.root / "broken")
        store.directory.mkdir()
        store._path("account").write_text("[]", encoding="utf-8")
        for operation in (lambda: store.get("account"),
                          lambda: store.put("account", {"expectedRevision": 0, "nodes": [], "edges": []})):
            with self.assertRaises(GatewayError) as raised:
                operation()
            self.assertEqual(raised.exception.status, 503)
        self.assertEqual(store._path("account").read_text("utf-8"), "[]")

    def test_unreadable_default_canvas_does_not_become_an_empty_document(self):
        store = CanvasStore(self.root / "unreadable")
        store.directory.mkdir()
        target = store._path("account")
        original = '{"nodes":[{"id":"keep"}],"edges":[],"revision":1}'
        target.write_text(original, encoding="utf-8")
        read_text = Path.read_text

        def read(path, *args, **kwargs):
            if path == target:
                raise PermissionError("fixture inaccessible")
            return read_text(path, *args, **kwargs)

        with mock.patch.object(Path, "read_text", read):
            with self.assertRaises(GatewayError) as raised:
                store.put("account", {"expectedRevision": 0, "nodes": [], "edges": []})
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(target.read_text("utf-8"), original)

    def create_canvas(self, name="Second", request_id="new-canvas-request", identifier="11111111-1111-4111-8111-111111111111"):
        self.client.responses[("POST", "/api/canvases")] = {"id": identifier, "name": name, "createdAt": "2026-11-04T00:00:00Z"}
        payload = json.dumps({"name": name, "clientRequestId": request_id}).encode()
        status, _, raw = self.gateway.handle("POST", "/api/canvases", payload)
        self.assertIn(status, (200, 201))
        return json.loads(raw)

    def test_canvas_library_migrates_legacy_default_without_rewriting_its_bytes(self):
        key = self.gateway._account_key()
        path = self.gateway.store._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        original = b'{"name":"Existing", "nodes":[{"id":"kept","data":{}}],"edges":[],"revision":4}'
        path.write_bytes(original)
        _, _, raw = self.gateway.handle("GET", "/api/canvases")
        self.assertEqual(json.loads(raw)["canvases"][0]["nodeCount"], 1)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(len(list(path.parent.iterdir())), 1)

    def test_explicit_new_canvas_reserves_server_id_but_never_writes_website_state(self):
        created = self.create_canvas()
        self.assertEqual(created["id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(self.client.calls[0][:2], ("POST", "/api/canvases"))
        self.create_canvas()
        self.assertEqual(len(self.client.calls), 1)
        self.gateway.handle("PUT", "/api/state", json.dumps({"id": created["id"], "expectedRevision": 0,
            "name": "Edited", "nodes": [{"id": "new"}], "edges": []}).encode(), browser_canvas=created["id"])
        _, _, raw = self.gateway.handle("GET", "/api/state")
        self.assertEqual(json.loads(raw)["nodes"], [])
        self.assertEqual(len(self.client.calls), 1)
        restarted = CanvasStore(self.gateway.store.directory)
        self.assertEqual(restarted.get(self.gateway._account_key(), created["id"])["name"], "Edited")
        self.assertEqual(len(restarted.list(self.gateway._account_key())["canvases"]), 2)

    def test_canvas_revisions_mismatch_account_and_create_receipts_are_isolated(self):
        identifier = self.create_canvas()["id"]
        payload = {"id": identifier, "expectedRevision": 0, "name": "Second", "nodes": [], "edges": []}
        self.gateway.handle("PUT", "/api/state", json.dumps(payload).encode(), browser_canvas=identifier)
        with self.assertRaises(GatewayError) as conflict:
            self.gateway.handle("PUT", "/api/state", json.dumps(payload).encode(), browser_canvas=identifier)
        self.assertEqual(conflict.exception.status, 409)
        with self.assertRaises(GatewayError):
            self.gateway.handle("PUT", "/api/state", json.dumps({**payload, "expectedRevision": 0}).encode())
        with self.assertRaises(GatewayError):
            self.create_canvas(name="Different")
        self.client.user_id = "bob"
        with self.assertRaises(GatewayError) as missing:
            self.gateway.handle("GET", "/api/state", browser_canvas=identifier)
        self.assertEqual(missing.exception.status, 404)

    def test_remote_requests_and_read_caches_use_the_page_canvas(self):
        identifier = self.create_canvas()["id"]
        self.client.responses[("GET", "/api/jobs?scope=all")] = [
            {"id": "default-job"}, {"id": "second-job", "canvasId": identifier}]
        _, _, raw = self.gateway.handle("GET", "/api/jobs", browser_canvas=identifier)
        self.assertEqual([job["id"] for job in json.loads(raw)], ["second-job"])
        self.assertEqual(self.client.canvas_calls[-1][2], identifier)
        _, _, raw = self.gateway.handle("GET", "/api/jobs")
        self.assertEqual([job["id"] for job in json.loads(raw)], ["default-job"])
        _, _, raw = self.gateway.handle("GET", "/api/jobs?scope=all", browser_canvas=identifier)
        self.assertEqual(len(json.loads(raw)), 2)
        self.assertIn("second-job", self.gateway.jobs_by_id)
        with self.assertRaises(GatewayError):
            self.gateway.handle("GET", "/api/config", browser_canvas="22222222-2222-4222-8222-222222222222")

    def test_capture_events_stay_on_original_canvas_after_page_switch(self):
        identifier = self.create_canvas()["id"]
        self.gateway.handle("GET", "/krita/events", browser_canvas=identifier)
        self.client.upload_hook = lambda: self.gateway.handle("GET", "/krita/events")
        self.gateway.capture(display=True)
        _, _, raw = self.gateway.handle("GET", "/krita/events", browser_canvas=identifier)
        self.assertEqual([item["type"] for item in json.loads(raw)], ["capture_pending", "asset"])
        self.assertTrue(all(item["canvasId"] == identifier for item in json.loads(raw)))
        _, _, raw = self.gateway.handle("GET", "/krita/events")
        self.assertEqual(json.loads(raw), [])

    def test_switching_canvas_still_imports_an_accepted_background_job_to_its_original_document(self):
        identifier = self.create_canvas()["id"]
        self.post_generation()
        self.target = "document-B"
        self.client.responses[("GET", "/api/assets")] = [{"id": "asset-1", "url": "/media/asset-1.png"}]
        self.client.responses[("GET", "/api/jobs?scope=all")] = [
            {"id": "job-1", "canvasId": "default", "clientRequestId": "request-123456", "assetIds": ["asset-1"]}]
        self.gateway.handle("GET", "/api/assets", browser_canvas=identifier)
        _, _, raw = self.gateway.handle("GET", "/api/jobs", browser_canvas=identifier)
        self.assertEqual(json.loads(raw), [])
        self.executor.drain()
        self.assertEqual(self.imports[0]["target"], "document-A")

    def test_failed_server_canvas_reservation_does_not_create_a_local_empty_document(self):
        self.client.responses[("POST", "/api/canvases")] = (503, {"error": "offline"})
        status, _, _ = self.gateway.handle("POST", "/api/canvases", b'{"clientRequestId":"new-canvas-request"}')
        self.assertEqual(status, 503)
        self.assertFalse(self.gateway.store.directory.exists())
        _, _, raw = self.gateway.handle("GET", "/api/canvases")
        self.assertEqual([item["id"] for item in json.loads(raw)["canvases"]], ["default"])

    def test_new_image_actions_and_aesthetic_preview_import_to_frozen_krita_target(self):
        for path, response in (("/api/image-actions", {"id": "edit-job"}),
                               ("/api/aesthetic/styles/style-1/preview", {"preview": {"jobId": "preview-job"}})):
            self.client.responses[("POST", path)] = response
            self.gateway.handle("POST", path, b'{"clientRequestId":"updated-action"}')
            job = response.get("id") or response["preview"]["jobId"]
            self.assertEqual(self.gateway.job_targets[job], "document-A")
        self.target = "document-B"
        self.deliver(job_id="edit-job", request_id="updated-action")
        self.executor.drain()
        self.assertEqual(self.imports[0]["target"], "document-A")

    def test_updated_feature_routes_are_allowed_but_admin_and_unknown_routes_stay_blocked(self):
        for method, route in (("GET", "/api/inspiration/public"), ("POST", "/api/inspiration/public/import"),
                              ("PATCH", "/api/inspiration/item-1"), ("DELETE", "/api/inspiration/item-1"),
                              ("POST", "/api/object-analysis"), ("GET", "/api/object-analysis/analysis-1"),
                              ("POST", "/api/object-analysis/analysis-1/cancel"),
                              ("GET", "/api/aesthetic"), ("PATCH", "/api/aesthetic/styles/style-1"),
                              ("POST", "/api/aesthetic/styles/style-1/analysis/cancel"),
                              ("POST", "/api/aesthetic/previews/preview-1/cancel"),
                              ("POST", "/api/tool-library/tools/tool-1/cover"), ("GET", "/api/chat-usage")):
            with self.subTest(route=route):
                self.gateway._validate_remote(method, route)
        for route in ("/api/admin/credits", "/api/object-analysis/analysis-1/files", "/api/aesthetic/delete-all"):
            with self.subTest(route=route), self.assertRaises(GatewayError):
                self.gateway._validate_remote("POST", route)

    def test_historical_jobs_are_never_automatically_imported(self):
        self.deliver(request_id="old-browser-request")
        self.executor.drain()
        self.assertEqual(self.imports, [])
        self.assertEqual(self.client.downloads, [])

    def test_current_session_results_import_once(self):
        self.post_generation()
        self.deliver()
        self.executor.drain()
        self.deliver()
        self.executor.drain()
        self.assertEqual(len(self.imports), 1)
        self.assertEqual(self.imports[0]["target"], "document-A")
        self.assertEqual(len(self.client.downloads), 1)

    def test_target_is_frozen_when_user_switches_documents(self):
        self.post_generation()
        self.target = "document-B"
        self.deliver()
        self.executor.drain()
        self.assertEqual(self.imports[0]["target"], "document-A")

    def test_first_submission_synchronizes_identity_before_tracking(self):
        self.gateway.account_id = None
        self.post_generation()
        self.assertIn("request-123456", self.gateway.request_targets)

    def test_uncertain_submission_can_be_recovered_by_request_id(self):
        self.client.responses[("POST", "/api/generate")] = RuntimeError("uncertain response")
        payload = {"clientRequestId": "request-123456", "nodeId": "node-1"}
        with self.assertRaises(RuntimeError):
            self.gateway.handle("POST", "/api/generate", json.dumps(payload).encode())
        self.target = "document-B"
        self.deliver()
        self.executor.drain()
        self.assertEqual(self.imports[0]["target"], "document-A")

    def test_same_request_id_keeps_original_document(self):
        self.post_generation()
        self.target = "document-B"
        self.post_generation()
        self.deliver()
        self.executor.drain()
        self.assertEqual(self.imports[0]["target"], "document-A")

    def test_switching_accounts_discards_pending_delivery(self):
        self.post_generation()
        self.deliver()
        self.client.user_id = "bob"
        self.gateway._sync_identity()
        self.executor.drain()
        self.assertEqual(self.imports, [])
        self.assertEqual(self.gateway.job_targets, {})
        self.assertEqual(self.gateway.assets_by_id, {})

    def test_identity_change_during_submission_discards_tracking(self):
        def switch(method, path):
            if path == "/api/generate":
                self.client.user_id = "bob"
        self.client.raw_hook = switch
        try:
            self.post_generation()
        except GatewayError as error:
            self.assertEqual(error.status, 401)
        self.assertNotIn("job-1", self.gateway.job_targets)

    def test_old_assets_response_never_populates_new_account_cache(self):
        self.client.responses[("GET", "/api/assets")] = [{"id": "alice-private", "url": "/media/alice.png"}]
        self.client.raw_hook = lambda method, path: setattr(self.client, "user_id", "bob")
        try:
            self.gateway.handle("GET", "/api/assets")
        except GatewayError as error:
            self.assertEqual(error.status, 401)
        self.assertNotIn("alice-private", self.gateway.assets_by_id)

    def test_batch_without_original_document_uses_one_new_document(self):
        self.target = None
        self.post_generation()
        self.deliver(asset_ids=("asset-1", "asset-2", "asset-3"))
        self.executor.drain()
        self.assertEqual(len(self.imports), 3)
        self.assertEqual(len(self.created_documents), 1)
        self.assertEqual([row["target"] for row in self.imports[1:]], ["created-document-1"] * 2)

    def test_failed_import_is_not_retried_on_every_poll(self):
        self.import_failure = RuntimeError("Original document closed")
        self.post_generation()
        self.deliver()
        self.executor.drain()
        self.deliver()
        self.executor.drain()
        self.assertEqual(len(self.client.downloads), 1)
        self.assertEqual(len(self.gateway.failed), 1)

    def test_auto_import_toggle_defers_delivery(self):
        self.gateway.auto_import = False
        self.post_generation()
        self.deliver()
        self.executor.drain()
        self.assertEqual(self.imports, [])
        self.gateway.auto_import = True
        self.gateway._schedule_results()
        self.executor.drain()
        self.assertEqual(len(self.imports), 1)

    def test_manual_import_carries_identity_epoch(self):
        self.gateway.assets_by_id["selected"] = {"id": "selected", "url": "/media/selected.png"}
        self.gateway.selected_asset = "selected"
        self.gateway.import_selected()
        self.assertEqual(self.imports[0].get("epoch"), self.gateway.epoch)

    def test_manual_import_drops_result_if_account_changes_during_download(self):
        self.gateway.assets_by_id["selected"] = {"id": "selected", "url": "/media/selected.png"}
        self.gateway.selected_asset = "selected"
        def switch():
            self.client.user_id = "bob"
            self.gateway._sync_identity()
        self.client.download_hook = switch
        try:
            self.gateway.import_selected()
        except GatewayError:
            pass
        self.assertEqual(self.imports, [])

    def test_capture_drops_old_account_asset_after_upload(self):
        def switch():
            self.client.user_id = "bob"
            self.gateway._sync_identity()
        self.client.upload_hook = switch
        self.gateway.capture("selection")
        self.assertNotIn("captured-asset", self.gateway.assets_by_id)
        self.assertEqual(self.gateway.events, [])

    def test_capture_checks_account_again_before_uploading_pixels(self):
        self.capture_hook = lambda: setattr(self.client, "user_id", "bob")
        self.gateway.capture("selection")
        self.assertEqual(self.client.uploads, [])


class HttpGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.web = Path(cls.temp.name) / "web"
        cls.web.mkdir()
        (cls.web / "index.html").write_text("Mori", encoding="utf-8")
        cls.gateway = MoriGateway(FakeClient(), cls.web, Path(cls.temp.name) / "storage", lambda *args: None)
        cls.gateway.start()

    @classmethod
    def tearDownClass(cls):
        cls.gateway.close()
        cls.temp.cleanup()

    def call(self, method="GET", path="/", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.httpd.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def cookie(self):
        return "mori_krita_bridge=" + self.gateway.token

    def test_pairing_requires_secret_and_sets_http_only_strict_cookie(self):
        status, _, _ = self.call(path="/connect/wrong")
        self.assertEqual(status, 403)
        status, headers, _ = self.call(path="/connect/" + self.gateway.token)
        self.assertEqual(status, 302)
        self.assertEqual(headers["Location"], "/")
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")

    def test_cookie_is_required_for_page_and_api(self):
        for path in ("/", "/api/auth/session", "/krita/config"):
            with self.subTest(path=path):
                self.assertEqual(self.call(path=path)[0], 403)
        self.assertEqual(self.call(headers={"Cookie": self.cookie()})[0], 200)

    def test_host_validation_prevents_dns_rebinding(self):
        status, _, _ = self.call(headers={"Host": "attacker.invalid", "Cookie": self.cookie()})
        self.assertEqual(status, 403)

    def test_cross_site_origin_and_fetch_metadata_are_rejected(self):
        for extra in ({"Origin": "https://attacker.invalid"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=extra):
                self.assertEqual(self.call(headers={"Cookie": self.cookie(), **extra})[0], 403)

    def test_mutations_require_exact_loopback_origin(self):
        body = b'{"assetId":"x"}'
        self.assertEqual(self.call("POST", "/krita/selection", {"Cookie": self.cookie()}, body)[0], 403)
        status, _, _ = self.call("POST", "/krita/selection", {"Cookie": self.cookie(), "Origin": self.gateway.origin,
                                                                "X-Mori-User": self.gateway.client.user_id}, body)
        self.assertEqual(status, 200)

    def test_stale_browser_account_cannot_submit_to_new_account(self):
        self.gateway.client.calls.clear()
        status, _, _ = self.call("POST", "/api/generate", {
            "Cookie": self.cookie(), "Origin": self.gateway.origin, "X-Mori-User": "previous-account"
        }, b'{"clientRequestId":"request-123456"}')
        self.assertEqual(status, 401)
        self.assertEqual(self.gateway.client.calls, [])

    def test_authenticated_api_requires_browser_account_header(self):
        status, _, _ = self.call(path="/api/credits", headers={"Cookie": self.cookie()})
        self.assertEqual(status, 401)
        status, _, _ = self.call(path="/api/credits", headers={
            "Cookie": self.cookie(), "X-Mori-User": "alice"
        })
        self.assertEqual(status, 200)

    def test_public_community_images_need_pairing_but_no_account_header(self):
        path = "/api/community/items/01234567-0123-4567-89ab-0123456789ab/media/a0?token=fixture"
        self.assertEqual(self.call(path=path)[0], 403)
        self.assertEqual(self.call(path=path, headers={"Cookie": self.cookie()})[0], 200)
        for private in ("/api/community/mine", "/api/community/items/item-1"):
            self.assertEqual(self.call(path=private, headers={"Cookie": self.cookie()})[0], 401)
        self.assertEqual(self.call("POST", path, {"Cookie": self.cookie(), "Origin": self.gateway.origin}, b"{}")[0], 401)

    def test_svg_original_is_download_only_inside_paired_bridge(self):
        with mock.patch.object(self.gateway.client, "raw_request", return_value=(200, {"content-type": "image/svg+xml"}, b"<svg/>")):
            status, headers, data = self.call(path="/media/abcd0123.svg", headers={"Cookie": self.cookie(), "X-Mori-User": "alice"})
        self.assertEqual(status, 200)
        self.assertEqual(data, b"<svg/>")
        self.assertEqual(headers["Content-Disposition"], "attachment")
        self.assertIn("sandbox", headers["Content-Security-Policy"])

    def test_stale_browser_account_cannot_log_out_current_account(self):
        status, _, _ = self.call("POST", "/api/auth/logout", {
            "Cookie": self.cookie(), "Origin": self.gateway.origin, "X-Mori-User": "previous-account"
        }, b"{}")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
