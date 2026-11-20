"""Preserve the installed cloud-canvas bridge while updating older plugins."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

import test_gateway as fixture


class CloudGatewayTests(unittest.TestCase):
    def test_first_password_setup_stays_on_the_authenticated_website(self):
        self.client.responses[("GET", "/api/account")] = {"profile": {"id": "alice", "hasPassword": False, "canSetPassword": True}}
        response = self.gateway.handle("GET", "/api/account")
        profile = json.loads(response[2])["profile"]
        self.assertFalse(profile["canSetPassword"])
        self.assertTrue(profile["passwordSetupOnWeb"])

    def test_browser_login_capability_is_advertised_without_forwarding_embedded_google_redirects(self):
        self.client.responses[("GET", "/api/auth/session")] = {"enabled": True, "user": self.client.user, "providers": {"google": True}}
        response = self.gateway.handle("GET", "/api/auth/session")
        self.assertEqual(json.loads(response[2])["providers"], {"google": False, "browserLogin": True})
        with self.assertRaises(fixture.GatewayError):
            self.gateway.handle("GET", "/api/auth/google/start")

    def test_browser_status_uses_private_local_routes_with_attempt_scoped_cancellation(self):
        attempt = "browser-attempt-123456"
        from types import SimpleNamespace
        cancelled = []
        self.gateway._browser_login = SimpleNamespace(
            start=lambda value: {"attemptId": value, "status": "starting"},
            status=lambda value: {"attemptId": value, "status": "pending"},
            cancel=lambda value=None: cancelled.append(value))
        for action in ("start", "status", "cancel"):
            response = self.gateway.handle("POST", "/krita/auth/browser/" + action, json.dumps({"attemptId": attempt}).encode())
            self.assertEqual(response[0], 200)
        self.assertEqual(cancelled, [attempt])
        self.assertEqual(self.client.calls, [])
        for action in ("start", "status", "cancel"):
            with self.assertRaises(fixture.GatewayError):
                self.gateway.handle("GET", "/krita/auth/browser/" + action)

    def setUp(self):
        self.fixture = fixture.GatewayTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.gateway = self.fixture.gateway
        self.client = self.fixture.client

    def test_state_reads_and_revision_saves_go_to_cloud(self):
        self.client.responses[("GET", "/api/state")] = {"revision": 7, "nodes": [], "edges": []}
        response = self.gateway.handle("GET", "/api/state", browser_canvas="11111111-1111-4111-8111-111111111111")
        self.assertEqual(json.loads(response[2])["revision"], 7)
        self.assertEqual(self.client.canvas_calls[-1], ("GET", "/api/state", "11111111-1111-4111-8111-111111111111"))
        self.assertIn("11111111-1111-4111-8111-111111111111", self.gateway.cloud_canvas_ids)
        payload = {"expectedRevision": 7, "nodes": [], "edges": []}
        self.client.responses[("PUT", "/api/state")] = {"revision": 8}
        response = self.gateway.handle("PUT", "/api/state", json.dumps(payload).encode(), browser_canvas="11111111-1111-4111-8111-111111111111")
        self.assertEqual(json.loads(response[2])["revision"], 8)
        self.assertEqual(self.client.calls[-1][2], payload)

    def test_revision_conflict_is_returned_to_webpage(self):
        self.client.responses[("PUT", "/api/state")] = (409, {"error": "版本冲突"})
        response = self.gateway.handle("PUT", "/api/state", b'{"expectedRevision":1}')
        self.assertEqual(response[0], 409)
        self.assertEqual(json.loads(response[2])["error"], "版本冲突")
        self.assertEqual(len(self.client.calls), 1)

    def test_cloud_library_registers_remote_documents(self):
        self.client.responses[("GET", "/api/canvases")] = {"canvases": [{"id": "22222222-2222-4222-8222-222222222222"}]}
        with mock.patch.object(self.gateway, "_migrate_legacy") as migrate:
            response = self.gateway.handle("GET", "/api/canvases")
        self.assertEqual(json.loads(response[2])["canvases"][0]["id"], "22222222-2222-4222-8222-222222222222")
        self.assertIn("22222222-2222-4222-8222-222222222222", self.gateway.cloud_canvas_ids)
        migrate.assert_called_once()

    def test_account_switch_removes_previous_cloud_document_access(self):
        self.gateway.cloud_canvas_ids.add("alice-private")
        self.client.user_id = "bob"
        self.client.user = {"id": "bob"}
        self.gateway._sync_identity()
        self.assertNotIn("alice-private", self.gateway.cloud_canvas_ids)
        with self.assertRaises(fixture.GatewayError):
            self.gateway.handle("GET", "/krita/events", browser_canvas="alice-private")

    def test_wrong_state_methods_are_rejected_before_request(self):
        for path in ("/api/state", "/api/canvases"):
            with self.assertRaises(fixture.GatewayError):
                self.gateway.handle("DELETE", path)
        self.assertEqual(self.client.calls, [])

    def test_legacy_migration_is_restartable_and_never_overwrites_default(self):
        spec = importlib.util.spec_from_file_location("mori_migration_fixture",
            Path(__file__).resolve().parents[1] / "mori_krita/canvas_sync.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        key = self.gateway._account_key()
        local = {"name": "Legacy", "nodes": [{"id": "one", "type": "image", "position": {"x": 0, "y": 0},
            "data": {"label": "preserve"}}], "edges": [], "expectedRevision": 0}
        self.gateway.store.put(key, local)
        original = self.gateway.store._path(key).read_bytes()
        calls = []
        def request(method, path, value, canvas_id):
            calls.append((method, path, value, canvas_id))
            if path == "/api/canvases":
                return {"id": "new-cloud-copy"}
            if method == "GET":
                return {"revision": 0, "nodes": [], "edges": []}
            self.assertEqual(canvas_id, "new-cloud-copy")
            self.assertEqual(value["expectedRevision"], 0)
            return {"revision": 1}
        migration = module.LegacyCanvasMigration(self.gateway.store)
        self.assertEqual(migration.run(key, request, lambda: None), 1)
        self.assertEqual(migration.run(key, request, lambda: None), 0)
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.gateway.store._path(key).read_bytes(), original)
