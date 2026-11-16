"""Persistent transport tests using local HTTP/1.1 and deterministic doubles."""
import http.client
import concurrent.futures
import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location("transport_api_fixture", Path(__file__).resolve().parents[1] / "mori_krita/api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def get_request(self):
        pair = super().get_request()
        self.connections += 1
        return pair


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def respond(self, payload, status=200):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.server.calls.append((self.command, self.path, dict(self.headers)))
        if self.path == "/api/slow":
            self.server.slow_entered.set()
            self.server.slow_release.wait(3)
        if self.path == "/api/too-large":
            self.send_response(200)
            self.send_header("Content-Length", str(api.MAX_JSON_BYTES + 1))
            self.end_headers()
            return
        self.respond({"ok": True}, 503 if self.path == "/api/failure" else 200)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.calls.append((self.command, self.path, dict(self.headers)))
        if self.path == "/api/auth/login":
            self.respond({"user": {"id": "qa-user"}})
        elif self.path == "/api/drop":
            self.close_connection = True
        else:
            self.respond({"ok": True}, 503)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.calls = []
        self.server.connections = 0
        self.server.slow_entered = threading.Event()
        self.server.slow_release = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = api.MoriClient("http://127.0.0.1:%d" % self.server.server_port)

    def tearDown(self):
        self.server.slow_release.set()
        self.client.clear_session()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_complete_http11_responses_reuse_one_connection(self):
        self.client.request("GET", "/api/ping")
        self.client.request("GET", "/api/ping")
        self.assertEqual(self.server.connections, 1)
        self.assertEqual(self.server.calls[0][2]["Connection"], "keep-alive")

    def test_independent_forks_share_idle_socket_but_send_fresh_headers(self):
        self.client.login("qa@example.invalid", "fixture")
        first = self.client.fork_session()
        first.request("GET", "/api/ping")
        second = self.client.fork_session()
        second.request("GET", "/api/ping")
        self.assertEqual(self.server.connections, 2)  # login generation + shared fork generation
        self.assertIsNot(first._lock, second._lock)
        self.assertEqual(self.server.calls[-1][2]["X-Mori-User"], "qa-user")

    def test_inflight_socket_is_not_shared_between_parallel_forks(self):
        first, second = self.client.fork_session(), self.client.fork_session()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            slow = executor.submit(first.request, "GET", "/api/slow")
            self.assertTrue(self.server.slow_entered.wait(2))
            quick = executor.submit(second.request, "GET", "/api/ping")
            self.assertTrue(quick.result(timeout=2)["ok"])
            self.assertFalse(slow.done())
            self.assertEqual(self.server.connections, 2)
            self.server.slow_release.set()
            self.assertTrue(slow.result(timeout=2)["ok"])

    def test_login_generation_closes_old_idle_connections(self):
        self.client.request("GET", "/api/ping")
        old_pool = self.client._transport_pool
        self.client.clear_session()
        self.assertTrue(old_pool.closed)
        self.client.request("GET", "/api/ping")
        self.assertEqual(self.server.connections, 2)

    def test_unread_oversize_response_is_discarded(self):
        with self.assertRaises(api.MoriApiError) as raised:
            self.client.request("GET", "/api/too-large")
        self.assertEqual(raised.exception.code, "RESPONSE_TOO_LARGE")
        self.client.request("GET", "/api/ping")
        self.assertEqual(self.server.connections, 2)

    def test_idle_expiry_does_not_reuse_old_socket(self):
        self.client._transport_pool.idle_seconds = 0
        self.client.request("GET", "/api/ping")
        self.client.request("GET", "/api/ping")
        self.assertEqual(self.server.connections, 2)

    def test_error_body_is_consumed_and_socket_remains_reusable(self):
        with self.assertRaises(api.MoriApiError):
            self.client.request("GET", "/api/failure")
        self.client.request("GET", "/api/ping")
        self.assertEqual(self.server.connections, 1)

    def test_post_disconnect_is_not_replayed(self):
        self.client.request("GET", "/api/ping")
        with self.assertRaises(api.MoriApiError):
            self.client.request("POST", "/api/drop", {"clientRequestId": "stable-request"})
        self.assertEqual(len([call for call in self.server.calls if call[:2] == ("POST", "/api/drop")]), 1)

    def test_failed_post_is_not_retried_on_another_connection(self):
        self.client.request("GET", "/api/ping")
        with self.assertRaises(api.MoriApiError):
            self.client.request("POST", "/api/failure", {"clientRequestId": "stable-request"})
        self.assertEqual(len([call for call in self.server.calls if call[0] == "POST"]), 1)
        self.assertEqual(self.server.connections, 1)

    def test_proxy_connect_authorization_never_reaches_origin_headers(self):
        records = []

        class Connection:
            def __init__(self, host, **kwargs):
                self.sock = SimpleNamespace(settimeout=lambda timeout: None)
                records.append(("new", host))

            def close(self):
                self.sock = None

            def set_debuglevel(self, level):
                self.assert_no_debug = level

            def set_tunnel(self, host, headers):
                records.append(("tunnel", host, headers))

            def request(self, method, selector, body, headers, **kwargs):
                records.append(("request", method, selector, headers))

            def getresponse(self):
                return SimpleNamespace(reason="OK", length=0, will_close=False,
                                       isclosed=lambda: True, close=lambda: None)

        pool = api._ConnectionPool()
        handler = api._PersistentHTTPHandler(lambda: pool)
        try:
            request = urllib.request.Request("https://example.invalid/api/generate", data=b"{}",
                                             headers={"Proxy-Authorization": "Basic fixture"}, method="POST")
            request.set_proxy("127.0.0.1:9999", "http")
            request.timeout = 3
            handler.do_open(Connection, request).close()
            handler.do_open(Connection, request).close()
            tunnels = [record for record in records if record[0] == "tunnel"]
            sent = [record for record in records if record[0] == "request"]
            self.assertEqual(len(tunnels), 1)
            self.assertEqual(tunnels[0][2], {"Proxy-Authorization": "Basic fixture"})
            self.assertEqual(len([record for record in records if record[0] == "new"]), 1)
            self.assertEqual(len(sent), 2)
            self.assertNotIn("Proxy-Authorization", sent[0][3])
        finally:
            pool.close()


class SessionRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = api.MoriClient("https://example.invalid", _proxy_config={"https": "http://127.0.0.1:9"})
        self.addCleanup(self.client.close)

    def test_only_session_probe_falls_back_and_forks_keep_working_route(self):
        failure = api.MoriApiError("proxy failed", code="NETWORK_ERROR")
        with mock.patch.object(self.client, "request", side_effect=[failure, {"enabled": True}]) as request:
            self.assertEqual(self.client.session(), {"enabled": True})
        self.assertEqual(request.call_args_list, [mock.call("GET", "/api/auth/session")] * 2)
        self.assertEqual(self.client.connection_route, "direct_fallback")
        self.assertEqual(self.client.fork_session()._proxy_config, {})

    def test_failed_direct_probe_restores_proxy_and_retains_original_error(self):
        original = self.client._opener
        failure = api.MoriApiError("proxy failed", code="NETWORK_ERROR")
        with mock.patch.object(self.client, "request", side_effect=[failure, OSError("direct failed")]):
            with self.assertRaises(api.MoriApiError) as raised:
                self.client.session()
        self.assertIs(raised.exception, failure)
        self.assertIs(self.client._opener, original)
        self.assertTrue(self.client._proxy_config)

    def test_http_auth_error_never_triggers_another_route(self):
        with mock.patch.object(self.client, "request", side_effect=api.MoriApiError("expired", 401, "AUTH_REQUIRED")) as request:
            with self.assertRaises(api.MoriApiError):
                self.client.session()
        request.assert_called_once_with("GET", "/api/auth/session")

    def test_paid_post_network_failure_never_tries_direct(self):
        with mock.patch.object(self.client._opener, "open", side_effect=urllib.error.URLError(OSError("broken proxy"))) as opened:
            with self.assertRaises(api.MoriApiError):
                self.client.generate({"model": "fixture", "prompt": "test"}, "fixture-node", [], "fixture-1234")
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(self.client._proxy_config)


if __name__ == "__main__":
    unittest.main()
