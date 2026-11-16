"""Registration through the actual loopback gateway and native HTTP client.

The upstream server is local and disposable; no real accounts or credits exist.
"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "mori_krita" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


api = load("registration_api", "api.py")
gateway_module = load("registration_gateway", "gateway.py")


class RegistrationServer(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, value, status=200, session=None):
        data = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        if session:
            self.send_header("Set-Cookie", "mori_session=%s; HttpOnly; Path=/; SameSite=Lax" % session)
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        self.server.calls.append((self.command, self.path, dict(self.headers), value))
        if self.path == "/api/auth/register":
            if value.get("email") == "exists@example.invalid":
                self.reply({"error": "邮箱已注册，请直接登录。", "code": "EMAIL_EXISTS"}, 409)
            else:
                self.reply({"user": {"id": "new-user", "email": value["email"], "name": value.get("name", "Student")}},
                           201, "registered-session")
        elif self.path == "/api/auth/login":
            self.reply({"user": {"id": "old-user", "name": "Previous user"}}, session="old-session")
        else:
            self.reply({"error": "Unknown fixture route"}, 404)

    def do_GET(self):
        self.server.calls.append((self.command, self.path, dict(self.headers), None))
        if self.path == "/api/auth/session":
            user = {"id": "new-user", "name": "Student"} if "registered-session" in self.headers.get("Cookie", "") else None
            self.reply({"enabled": True, "user": user, "providers": {"google": True}})
        elif self.path == "/api/credits":
            self.reply({"balance": {"available": 8}})
        else:
            self.reply({"error": "Unknown fixture route"}, 404)


class RegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), RegistrationServer)
        cls.server.calls = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.calls.clear()
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        web = root / "web"
        web.mkdir()
        (web / "index.html").write_text("Mori registration fixture", encoding="utf-8")
        self.client = api.MoriClient("http://127.0.0.1:%s" % self.server.server_port, _proxy_config={})
        self.notices = []
        self.gateway = gateway_module.MoriGateway(self.client, web, root / "state",
                                                  lambda *args: None, self.notices.append)
        self.gateway.start()

    def tearDown(self):
        self.gateway.close()
        self.gateway.thread.join()
        self.temp.cleanup()

    def call(self, method, path, value=None, *, user=None, canvas=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.gateway.httpd.server_port, timeout=3)
        request_headers = {"Cookie": "mori_krita_bridge=" + self.gateway.token,
                           "Origin": self.gateway.origin}
        if user is not None:
            request_headers["X-Mori-User"] = user
        if canvas is not None:
            request_headers["X-Mori-Canvas"] = canvas
        request_headers.update(headers or {})
        body = json.dumps(value).encode("utf-8") if value is not None else None
        try:
            connection.request(method, path, body, request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    def register(self, **kwargs):
        return self.call("POST", "/api/auth/register", {
            "email": "student@example.invalid", "password": "fixture-password", "name": "Student",
        }, **kwargs)

    def test_signup_uses_remote_account_cookie_and_credit_service(self):
        status, headers, result = self.register()
        self.assertEqual(status, 201)
        self.assertEqual(result["user"]["id"], "new-user")
        self.assertEqual(self.client.user_id, "new-user")
        self.assertNotIn("Set-Cookie", headers)
        self.assertNotIn("registered-session", json.dumps(result))
        self.assertIn("已登录 Student", self.notices)
        registration = self.server.calls[0]
        self.assertEqual(registration[:2], ("POST", "/api/auth/register"))
        self.assertEqual(registration[3], {"email": "student@example.invalid", "password": "fixture-password", "name": "Student"})
        self.assertEqual(registration[2]["Origin"], self.client.base_url)
        self.assertNotIn("X-Mori-User", registration[2])
        self.assertNotIn("X-Mori-Canvas", registration[2])

        status, _, result = self.call("GET", "/api/auth/session")
        self.assertEqual(status, 200)
        self.assertEqual(result["user"]["id"], "new-user")
        self.assertFalse(result["providers"]["google"])
        status, _, result = self.call("GET", "/api/credits", user="new-user", canvas="default")
        self.assertEqual((status, result["balance"]["available"]), (200, 8))
        self.assertEqual(self.server.calls[-1][2]["X-Mori-User"], "new-user")
        self.assertEqual(self.server.calls[-1][2]["X-Mori-Canvas"], "default")
        self.assertIn("mori_session=registered-session", self.server.calls[-1][2]["Cookie"])

    def test_signup_does_not_inherit_previous_account_cookie_or_canvas(self):
        self.call("POST", "/api/auth/login", {"email": "old@example.invalid", "password": "old-password"})
        old_fork = self.client.fork_session()
        self.call("PUT", "/api/state", {"expectedRevision": 0, "name": "Private old canvas", "nodes": [], "edges": []}, user="old-user")
        self.gateway.assets_by_id["private"] = {"id": "private", "url": "/media/private.png"}
        epoch = self.gateway.epoch

        # Auth UI deliberately sends no account header, even after a stale tab.
        status, _, _ = self.register()
        self.assertEqual(status, 201)
        self.assertGreater(self.gateway.epoch, epoch)
        self.assertEqual(self.gateway.assets_by_id, {})
        self.assertNotIn("Cookie", self.server.calls[-1][2])
        self.assertNotIn("X-Mori-User", self.server.calls[-1][2])
        with self.assertRaises(api.MoriApiError) as raised:
            old_fork.assert_session_current()
        self.assertEqual(raised.exception.code, "SESSION_CHANGED")
        status, _, state = self.call("GET", "/api/state", user="new-user")
        self.assertEqual((status, state["revision"]), (200, 0))
        self.assertNotEqual(state["name"], "Private old canvas")
        status, _, _ = self.call("GET", "/api/credits", user="old-user")
        self.assertEqual(status, 401)

    def test_server_validation_error_reaches_form_without_retry_or_login(self):
        status, _, result = self.call("POST", "/api/auth/register", {
            "email": "exists@example.invalid", "password": "fixture-password",
        })
        self.assertEqual(status, 409)
        self.assertEqual(result, {"error": "邮箱已注册，请直接登录。", "code": "EMAIL_EXISTS"})
        self.assertEqual(len(self.server.calls), 1)
        self.assertIsNone(self.client.user_id)

    def test_registration_requires_exact_route_and_post(self):
        for method, path in (("GET", "/api/auth/register"), ("PUT", "/api/auth/register"),
                             ("DELETE", "/api/auth/register"), ("POST", "/api/auth/register/admin"),
                             ("POST", "/api/auth/register/"), ("POST", "/api/auth/google/link")):
            with self.subTest(method=method, path=path):
                self.assertEqual(self.call(method, path, {})[0], 403)
        self.assertEqual(self.server.calls, [])

    def test_registration_keeps_pairing_and_same_origin_guards(self):
        for headers in ({"Cookie": ""}, {"Origin": "https://other.invalid"},
                        {"Origin": ""}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.register(headers=headers)[0], 403)
        self.assertEqual(self.server.calls, [])


if __name__ == "__main__":
    unittest.main()
