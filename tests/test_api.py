"""Protocol/security tests using a loopback fake server; no paid requests."""

import base64
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import threading
import tracemalloc
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_SPEC = importlib.util.spec_from_file_location("mori_api", Path(__file__).resolve().parents[1] / "mori_krita" / "api.py")
api = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(api)

PNG = b"\x89PNG\r\n\x1a\nfixture"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, headers=None):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.calls.append((self.command, self.path, dict(self.headers), None))
        if self.path == "/api/auth/session":
            self.reply({"enabled": True, "user": {"id": "user-1", "name": "Test"}})
        elif self.path == "/api/auth/redirect":
            self.reply({}, 307, {"Location": self.server.redirect_url})
        elif self.path == "/api/large":
            self.send_response(200)
            self.send_header("Content-Length", str(api.MAX_JSON_BYTES + 1))
            self.end_headers()
        elif self.path == "/api/expired":
            self.reply({"error": "请先登录", "code": "AUTH_REQUIRED"}, 401)
        elif self.path == "/api/slow-expired":
            self.server.transfer_entered.set()
            self.server.release_transfer.wait(3)
            self.reply({"error": "请先登录", "code": "AUTH_REQUIRED"}, 401)
        elif self.path == "/media/image.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
        elif self.path == "/api/credits":
            self.reply({"balance": {"available": 25}})
        else:
            self.reply([])

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = json.loads(raw) if raw else None
        self.server.calls.append((self.command, self.path, dict(self.headers), body))
        if self.path == "/api/auth/login":
            second_account = body["email"] == "bob@example.invalid"
            self.reply({"user": {"id": "user-2" if second_account else "user-1", "email": body["email"]}}, headers={"Set-Cookie": "mori_session=%s; HttpOnly; Path=/; SameSite=Lax" % ("fixture-2" if second_account else "fixture")})
        elif self.path == "/api/auth/logout":
            self.reply({"ok": True}, headers={"Set-Cookie": "mori_session=; Max-Age=0; Path=/"})
        elif self.path == "/api/generate":
            self.reply({"id": "job-1", **body}, 202)
        elif self.path == "/api/failed-generation":
            self.reply({"error": "生成服务暂时不可用"}, 503)
        elif self.path == "/api/auth/redirect":
            self.reply({}, 307, {"Location": self.server.redirect_url})
        elif self.path == "/api/assets" and self.server.hold_upload:
            self.server.transfer_entered.set()
            self.server.release_transfer.wait(3)
            self.reply({"id": "uploaded", "name": body.get("name")}, 201)
        else:
            self.reply(body, 201)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.calls = []
        cls.server.redirect_url = "http://127.0.0.1:1/api/leaked"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = "http://127.0.0.1:%s" % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.client = api.MoriClient(self.origin)
        self.server.calls.clear()
        self.server.hold_upload = False
        self.server.transfer_entered = threading.Event()
        self.server.release_transfer = threading.Event()

    def test_cookie_and_current_account_header_stay_in_memory(self):
        result = self.client.login("test@example.invalid", "test-password")
        self.assertEqual(result["user"]["id"], "user-1")
        self.client.credits()
        login, credits = self.server.calls
        self.assertEqual(login[2].get("Origin"), self.origin)
        self.assertNotIn("X-Mori-User", login[2])
        self.assertEqual(credits[2].get("X-Mori-User"), "user-1")
        self.assertIn("mori_session=fixture", credits[2].get("Cookie", ""))
        self.assertNotIn("password", vars(self.client))

    def test_raw_login_updates_session_and_does_not_expose_cookie(self):
        status, headers, _ = self.client.raw_request("POST", "/api/auth/login", b'{"email":"e","password":"p"}', "application/json")
        self.assertEqual(status, 200)
        self.assertNotIn("set-cookie", headers)
        self.assertEqual(self.client.user_id, "user-1")

    def test_canvas_header_is_validated_and_bound_to_one_request(self):
        canvas = "11111111-1111-4111-8111-111111111111"
        self.client.raw_request("GET", "/api/config", canvas_id=canvas)
        self.client.raw_request("GET", "/api/config")
        self.assertEqual(self.server.calls[0][2].get("X-Mori-Canvas"), canvas)
        self.assertNotIn("X-Mori-Canvas", self.server.calls[1][2])
        for invalid in ("../other", "default\r\nX-Mori-User:other", "", 12):
            with self.subTest(value=invalid), self.assertRaises(api.MoriApiError):
                self.client.raw_request("GET", "/api/config", canvas_id=invalid)
        self.assertEqual(len(self.server.calls), 2)

    def test_canvas_context_is_frozen_with_a_fork_and_never_added_to_auth(self):
        self.client._canvas_id = "11111111-1111-4111-8111-111111111111"
        fork = self.client.fork_session()
        self.client._canvas_id = "default"
        fork.raw_request("GET", "/api/config")
        self.assertEqual(self.server.calls[-1][2].get("X-Mori-Canvas"), "11111111-1111-4111-8111-111111111111")
        self.client.raw_request("GET", "/api/auth/session", canvas_id="default")
        self.assertNotIn("X-Mori-Canvas", self.server.calls[-1][2])

    def test_session_and_logout_update_user(self):
        self.client.session()
        self.assertEqual(self.client.user_id, "user-1")
        self.client.logout()
        self.assertIsNone(self.client.user_id)
        self.assertEqual(list(self.client._cookies), [])
        self.assertEqual(self.server.calls[-1][2].get("X-Mori-User"), "user-1")

    def test_expired_session_exposes_status_and_clears_auth(self):
        self.client.login("e", "p")
        with self.assertRaises(api.MoriApiError) as raised:
            self.client.request("GET", "/api/expired")
        self.assertEqual((raised.exception.status, raised.exception.code), (401, "AUTH_REQUIRED"))
        self.assertIsNone(self.client.user_id)

    def test_no_redirect_following_or_post_replay(self):
        self.client.login("e", "p")
        status, _, _ = self.client.raw_request("POST", "/api/auth/redirect", b"{}", "application/json")
        self.assertEqual(status, 307)
        self.assertEqual(len(self.server.calls), 2)

    def test_paid_post_preserves_request_id_and_has_single_attempt(self):
        settings = {"model": "model", "prompt": "test", "aspect": "1:1", "resolution": "1K", "quality": "high", "count": 1}
        result = self.client.generate(settings, "node-1", ["asset-1"], "stable-request-123")
        self.assertEqual(result["clientRequestId"], "stable-request-123")
        self.assertEqual(result["references"], ["asset-1"])
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.server.calls[0][2].get("Origin"), self.origin)

    def test_native_masked_edit_preserves_explicit_size_and_mask_without_extra_post(self):
        settings = {"model": "gpt-image-2.5", "prompt": "keep the full reference frame", "aspect": "16:9",
                    "resolution": "1K", "quality": "high", "count": 1,
                    "edit": {"size": "1024x576", "maskAssetId": "mask-1"}}
        result = self.client.generate(settings, "selection-task", ["context-1"], "selection-request-123")
        self.assertEqual(result["edit"], settings["edit"])
        self.assertEqual(result["references"], ["context-1"])
        self.assertEqual(result["clientRequestId"], "selection-request-123")
        self.assertEqual(len(self.server.calls), 1)
        self.assertEqual(self.server.calls[0][1], "/api/generate")

    def test_failing_post_is_never_retried(self):
        with self.assertRaises(api.MoriApiError) as raised:
            self.client.request("POST", "/api/failed-generation", {"clientRequestId": "stable-123"})
        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(len(self.server.calls), 1)

    def test_upload_matches_existing_mori_protocol(self):
        payload = self.client.upload_image(PNG, "画布")
        self.assertEqual(base64.b64decode(payload["dataUrl"].split(",", 1)[1]), PNG)
        self.assertEqual(payload["name"], "画布")
        self.assertEqual(self.server.calls[0][1], "/api/assets")

    def test_message_contract(self):
        result = self.client.send_message("conversation-1", "draw", ["ref-1"], {"model": "image"}, "agent", {"nodes": []}, "message-1234")
        self.assertEqual(result["clientRequestId"], "message-1234")
        self.assertEqual(result["context"], {"nodes": []})
        self.assertEqual(result["agentModel"], "agent")

    def test_only_same_origin_media_can_be_downloaded(self):
        self.assertEqual(self.client.download_asset(self.origin + "/media/image.png"), PNG)
        for route in ("https://example.invalid/media/x.png", "/api/credits", "file:///secret"):
            with self.subTest(route=route), self.assertRaises(api.MoriApiError):
                self.client.download_asset(route)

    def test_unsafe_urls_are_rejected_before_network(self):
        for route in ("//example.invalid/api/x", "http://example.invalid/api/x", "/api/../secret", "/api/%2e%2e/secret", "/api/%252e%252e/secret", "/api/x\\secret", "/other", "/api/x#secret", "/api/x\r\nCookie: leaked"):
            with self.subTest(route=route), self.assertRaises(api.MoriApiError):
                self.client.request("GET", route)
        self.assertEqual(self.server.calls, [])

    def test_insecure_non_loopback_server_is_rejected(self):
        for origin in ("http://example.invalid", "https://user:secret@example.invalid", "https://example.invalid/nested", "file:///tmp"):
            with self.subTest(origin=origin), self.assertRaises(api.MoriApiError):
                api.MoriClient(origin)

    def test_response_size_limit(self):
        with self.assertRaises(api.MoriApiError) as raised:
            self.client.request("GET", "/api/large")
        self.assertEqual(raised.exception.code, "RESPONSE_TOO_LARGE")

    def test_new_clients_do_not_share_accounts(self):
        self.client.login("e", "p")
        second = api.MoriClient(self.origin)
        second.credits()
        self.assertNotIn("Cookie", self.server.calls[-1][2])
        self.assertNotIn("X-Mori-User", self.server.calls[-1][2])

    def test_api_base_is_read_only(self):
        with self.assertRaises(AttributeError):
            self.client.base_url = "https://other.invalid"

    def test_origin_is_canonical(self):
        self.assertEqual(api.MoriClient("https://MORINET.chat:443/").base_url, "https://morinet.chat")
        with self.assertRaises(api.MoriApiError):
            api.MoriClient("http://localhost:0")

    def test_fork_uses_independent_cookie_jar_and_network_lock(self):
        self.client.login("test@example.invalid", "password")
        fork = self.client.fork_session()
        self.assertIsNot(fork._cookies, self.client._cookies)
        self.assertIsNot(fork._lock, self.client._lock)
        self.assertIsNot(fork._opener, self.client._opener)
        self.assertEqual(fork.session_version, self.client.session_version)
        fork.credits()
        self.assertIn("mori_session=fixture", self.server.calls[-1][2].get("Cookie", ""))

    def test_slow_fork_upload_does_not_block_main_poll(self):
        self.client.login("test@example.invalid", "password")
        fork = self.client.fork_session()
        self.server.hold_upload = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            uploading = executor.submit(fork.upload_image, PNG, "fixture")
            try:
                self.assertTrue(self.server.transfer_entered.wait(1))
                polling = executor.submit(self.client.credits)
                self.assertEqual(polling.result(timeout=1)["balance"]["available"], 25)
                self.assertFalse(uploading.done())
            finally:
                self.server.release_transfer.set()
            self.assertEqual(uploading.result(timeout=2)["id"], "uploaded")

    def test_expired_fork_is_rejected_before_sending(self):
        self.client.login("test@example.invalid", "password")
        fork = self.client.fork_session()
        self.client.clear_session()
        count = len(self.server.calls)
        with self.assertRaises(api.MoriApiError) as raised:
            fork.credits()
        self.assertEqual(raised.exception.code, "SESSION_CHANGED")
        self.assertEqual(len(self.server.calls), count)

    def test_fork_cannot_login_or_logout(self):
        self.client.login("test@example.invalid", "password")
        for path in ("/api/auth/login", "/api/auth/logout", "/api/auth/session"):
            fork = self.client.fork_session()
            with self.subTest(path=path), self.assertRaises(api.MoriApiError) as raised:
                fork.raw_request("POST", path, b"{}", "application/json")
            self.assertEqual(raised.exception.code, "SNAPSHOT_AUTH_BLOCKED")
        self.assertEqual(self.client.user_id, "user-1")

    def test_current_fork_401_invalidates_master_session(self):
        self.client.login("test@example.invalid", "password")
        fork = self.client.fork_session()
        with self.assertRaises(api.MoriApiError):
            fork.request("GET", "/api/expired")
        self.assertIsNone(self.client.user_id)
        with self.assertRaises(api.MoriApiError):
            fork.assert_session_current()

    def test_old_inflight_fork_cannot_invalidate_new_login(self):
        self.client.login("test@example.invalid", "password")
        fork = self.client.fork_session()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            old_request = executor.submit(fork.request, "GET", "/api/slow-expired")
            try:
                self.assertTrue(self.server.transfer_entered.wait(1))
                self.client.login("bob@example.invalid", "password")
            finally:
                self.server.release_transfer.set()
            with self.assertRaises(api.MoriApiError) as raised:
                old_request.result(timeout=2)
            self.assertEqual(raised.exception.code, "SESSION_CHANGED")
        self.assertEqual(self.client.user_id, "user-2")
        self.client.credits()
        self.assertIn("mori_session=fixture-2", self.server.calls[-1][2].get("Cookie", ""))

    def test_inflight_upload_keeps_old_cookie_and_discards_stale_response(self):
        self.client.login("test@example.invalid", "password")
        fork = self.client.fork_session()
        self.server.hold_upload = True
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            uploading = executor.submit(fork.upload_image, PNG, "fixture")
            try:
                self.assertTrue(self.server.transfer_entered.wait(1))
                self.client.login("bob@example.invalid", "password")
            finally:
                self.server.release_transfer.set()
            with self.assertRaises(api.MoriApiError) as raised:
                uploading.result(timeout=2)
            self.assertEqual(raised.exception.code, "SESSION_CHANGED")
        upload_headers = next(call[2] for call in self.server.calls if call[1] == "/api/assets")
        self.assertEqual(upload_headers.get("X-Mori-User"), "user-1")
        self.assertIn("mori_session=fixture", upload_headers.get("Cookie", ""))

    def test_session_version_is_read_only_and_changes_on_relogin(self):
        before = self.client.session_version
        self.client.login("test@example.invalid", "password")
        self.assertGreater(self.client.session_version, before)
        with self.assertRaises(AttributeError):
            self.client.session_version = 123

    def test_verified_https_context_and_proxy_handler_are_preserved(self):
        proxies = {"https": "http://127.0.0.1:9"}
        with mock.patch.object(api.urllib.request, "getproxies", return_value=proxies):
            first = api.MoriClient("https://morinet.chat")
            second = first.fork_session()
        first_https = next(h for h in first._opener.handlers if isinstance(h, api.urllib.request.HTTPSHandler))
        second_https = next(h for h in second._opener.handlers if isinstance(h, api.urllib.request.HTTPSHandler))
        proxy = next(h for h in first._opener.handlers if isinstance(h, api.urllib.request.ProxyHandler))
        self.assertIs(first_https._context, second_https._context)
        self.assertTrue(first_https._context.check_hostname)
        self.assertEqual(first_https._context.verify_mode, api.ssl.CERT_REQUIRED)
        self.assertEqual(proxy.proxies, proxies)

    def test_jpeg_and_webp_uploads_keep_existing_server_protocol(self):
        fixtures = ((b"\xff\xd8\xfffixture", "image/jpeg"), (b"RIFF\x10\x00\x00\x00WEBPfixture", "image/webp"))
        for image, mime in fixtures:
            with self.subTest(mime=mime):
                result = self.client.upload_image(image, '参考 "图片"', mime=mime)
                self.assertTrue(result["dataUrl"].startswith("data:%s;base64," % mime))
                self.assertEqual(base64.b64decode(result["dataUrl"].split(",", 1)[1]), image)
                self.assertEqual(result["name"], '参考 "图片"')
        with self.assertRaises(api.MoriApiError):
            self.client.upload_image(PNG, "fixture", mime="image/jpeg")

    def test_upload_progress_has_known_length_without_chunked_encoding(self):
        progress = []
        self.client.upload_image(PNG + b"p" * (512 * 1024), "fixture", progress=lambda sent, total: progress.append((sent, total)))
        self.assertGreaterEqual(len(progress), 2)
        headers = self.server.calls[-1][2]
        total = int(headers["Content-Length"])
        self.assertEqual(progress[0], (0, total))
        self.assertEqual(progress[-1], (total, total))
        self.assertEqual([sent for sent, _ in progress], sorted(set(sent for sent, _ in progress)))
        self.assertNotIn("Transfer-Encoding", headers)

    def test_progress_callback_error_never_interrupts_or_replays_upload(self):
        def broken(*args):
            raise RuntimeError("UI callback failure")
        self.client.upload_image(PNG, "fixture", progress=broken)
        self.assertEqual(len(self.server.calls), 1)

    def test_oversize_upload_request_is_rejected_before_encoding_or_network(self):
        transport = mock.Mock(return_value=(200, {}, b'{"id":"fixture"}'))
        with mock.patch.object(self.client, "raw_request", transport), mock.patch.object(api, "MAX_UPLOAD_REQUEST_BYTES", 10), mock.patch.object(api.base64, "b64encode") as encoding:
            with self.assertRaises(api.MoriApiError) as raised:
                self.client.upload_image(PNG, "fixture")
        self.assertEqual(raised.exception.code, "REQUEST_TOO_LARGE")
        self.assertIn("请使用快速参考或缩小选区", str(raised.exception))
        encoding.assert_not_called()
        transport.assert_not_called()
        self.assertEqual(self.server.calls, [])

    def test_upload_byte_encoding_reduces_peak_temporary_memory(self):
        image = PNG + b"x" * (8 * 1024 * 1024)
        # No network: measure only the old/new protocol body construction.
        def old_encoding():
            return json.dumps({"name": "参考图", "dataUrl": "data:image/png;base64," + base64.b64encode(image).decode("ascii")}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        tracemalloc.start()
        try:
            old = old_encoding()
            _, old_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        expected_length = len(old)
        del old
        lengths = []
        self.client.raw_request = lambda method, path, body=None, content_type=None, **kwargs: (lengths.append(len(body)) or (200, {}, b'{"id":"fixture"}'))
        tracemalloc.start()
        try:
            self.client.upload_image(image, "参考图")
            _, new_peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(lengths, [expected_length])
        self.assertLess(new_peak, old_peak * 0.85)


if __name__ == "__main__":
    unittest.main()
