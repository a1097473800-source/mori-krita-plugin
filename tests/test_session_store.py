"""Encrypted sign-in persistence using synthetic cookies, never a real account."""
import copy
from email.message import Message
import http.cookiejar
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


PROJECT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, PROJECT / "mori_krita" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store_module = load_module("mori_session_store_fixture", "session_store.py")
api = load_module("mori_persistent_api_fixture", "api.py")
ORIGIN = "https://morinet.chat"
TOKEN = "synthetic-session-token-never-real"


def cookie(value=TOKEN, *, domain="morinet.chat", expires=None, name="mori_session"):
    return http.cookiejar.Cookie(0, name, value, None, False, domain, False, False,
                                 "/", True, True, expires, expires is None, None, None,
                                 {"HttpOnly": None}, False)


@unittest.skipUnless(os.name == "nt", "Windows DPAPI requires the current Windows user")
class WindowsSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = store_module.WindowsSessionStore(self.temporary.name)

    def test_dpapi_roundtrip_saves_only_ciphertext_and_preserves_cookie_expiry(self):
        expires = int(time.time()) + 300
        self.assertTrue(self.store.save(ORIGIN, [cookie(expires=expires)]))
        files = list(Path(self.temporary.name).iterdir())
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].suffix, ".dpapi")
        self.assertNotIn(TOKEN.encode(), files[0].read_bytes())
        self.assertNotIn(b"morinet.chat", files[0].read_bytes())
        restored = self.store.load(ORIGIN)
        self.assertEqual([(item.name, item.value, item.expires) for item in restored],
                         [("mori_session", TOKEN, expires)])
        self.assertTrue(restored[0].secure)

    def test_origin_is_canonical_but_never_shared_with_another_server_or_port(self):
        self.assertTrue(self.store.save("https://MORINET.chat:443/", [cookie()]))
        self.assertEqual(self.store.load(ORIGIN)[0].value, TOKEN)
        self.assertEqual(self.store.load("https://other.invalid"), [])
        self.assertEqual(self.store.load("https://morinet.chat:444"), [])
        original, _ = self.store._path(ORIGIN)
        other, _ = self.store._path("https://other.invalid")
        other.write_bytes(original.read_bytes())
        self.assertEqual(self.store.load("https://other.invalid"), [])
        self.assertFalse(other.exists())
        self.assertEqual(self.store.load(ORIGIN)[0].value, TOKEN)

    def test_corrupt_or_expired_session_is_removed(self):
        self.store.save(ORIGIN, [cookie()])
        path, _ = self.store._path(ORIGIN)
        path.write_bytes(b"damaged-data")
        self.assertEqual(self.store.load(ORIGIN), [])
        self.assertFalse(path.exists())
        with mock.patch.object(store_module.time, "time", return_value=100):
            self.assertTrue(self.store.save(ORIGIN, [cookie(expires=200)]))
        with mock.patch.object(store_module.time, "time", return_value=300):
            self.assertEqual(self.store.load(ORIGIN), [])
        self.assertFalse(path.exists())

    def test_only_matching_session_cookie_is_saved_and_logout_removes_it(self):
        cookies = [cookie(), cookie(name="analytics"), cookie(domain="evil.invalid")]
        self.assertTrue(self.store.save(ORIGIN, cookies))
        self.assertEqual(len(self.store.load(ORIGIN)), 1)
        self.store.clear(ORIGIN)
        self.assertEqual(self.store.load(ORIGIN), [])
        self.assertFalse(self.store.save(ORIGIN, [cookie(name="analytics")]))

    def test_http_and_unsafe_urls_never_create_files(self):
        for origin in ("http://127.0.0.1", "https://u:p@morinet.chat", "https://morinet.chat/path"):
            with self.subTest(origin=origin):
                self.assertFalse(self.store.save(origin, [cookie()]))
                self.assertEqual(self.store.load(origin), [])
        self.assertEqual(list(Path(self.temporary.name).iterdir()), [])


class MemoryStore:
    """Offline stand-in: production persistence is tested with real DPAPI above."""
    def __init__(self):
        self.values = {}
        self.saves = 0

    def load(self, origin):
        return copy.deepcopy(self.values.get(origin, []))

    def save(self, origin, cookies):
        self.values[origin] = copy.deepcopy(list(cookies))
        self.saves += 1

    def clear(self, origin):
        self.values.pop(origin, None)


class FakeResponse(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def getcode(self):
        return self.status


class SessionPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.client = api.MoriClient(ORIGIN, session_store=self.store)
        self.addCleanup(self.client.close)

    def response(self, client, payload, status=200, *, session_cookie=False):
        def opening(request, timeout):
            if session_cookie:
                client._cookies.set_cookie(cookie())
            return FakeResponse(payload, status)
        client._opener.open = opening

    def test_raw_web_login_and_native_login_share_persistence_hook_without_passwords(self):
        self.response(self.client, {"user": {"id": "user-a"}}, session_cookie=True)
        self.client.raw_request("POST", "/api/auth/login", b'{"email":"fixture","password":"never-save"}', "application/json")
        self.assertEqual(self.store.values[ORIGIN][0].value, TOKEN)
        self.assertNotIn("never-save", repr(vars(self.store)))
        self.client.login("fixture", "another-password")
        self.assertEqual(self.store.values[ORIGIN][0].value, TOKEN)

    def test_web_registration_persists_only_session_and_restores_after_restart(self):
        self.response(self.client, {"user": {"id": "registered-user"}}, 201, session_cookie=True)
        self.client.raw_request("POST", "/api/auth/register", b'{"email":"fixture","password":"never-save"}', "application/json")
        self.assertEqual(self.client.user_id, "registered-user")
        self.assertEqual(self.store.values[ORIGIN][0].value, TOKEN)
        self.assertNotIn("never-save", repr(vars(self.store)))
        self.client.close()
        restarted = api.MoriClient(ORIGIN, session_store=self.store)
        self.addCleanup(restarted.close)
        self.assertTrue(restarted.session_restored)
        self.assertIsNone(restarted.user_id)
        self.response(restarted, {"enabled": True, "user": {"id": "registered-user"}})
        restarted.session()
        self.assertEqual(restarted.user_id, "registered-user")

    def test_restart_restores_cookie_but_requires_server_validation_for_identity(self):
        self.store.values[ORIGIN] = [cookie()]
        restarted = api.MoriClient("https://MORINET.chat:443", session_store=self.store)
        self.addCleanup(restarted.close)
        self.assertTrue(restarted.session_restored)
        self.assertIsNone(restarted.user_id)
        self.response(restarted, {"enabled": True, "user": {"id": "user-a"}})
        restarted.session()
        self.assertFalse(restarted.session_restored)
        self.assertEqual(restarted.user_id, "user-a")
        self.assertEqual(self.store.values[ORIGIN][0].value, TOKEN)

    def test_shutdown_preserves_sign_in_but_explicit_logout_clears_it(self):
        self.response(self.client, {"user": {"id": "user-a"}}, session_cookie=True)
        self.client.login("fixture", "password")
        fork = self.client.fork_session()
        self.client.close()
        self.assertEqual(self.store.values[ORIGIN][0].value, TOKEN)
        with self.assertRaises(api.MoriApiError):
            fork.assert_session_current()
        restarted = api.MoriClient(ORIGIN, session_store=self.store)
        self.addCleanup(restarted.close)
        self.response(restarted, {"ok": True})
        restarted.raw_request("POST", "/api/auth/logout", b"{}", "application/json")
        self.assertNotIn(ORIGIN, self.store.values)

    def test_expired_session_200_without_user_or_401_removes_saved_cookie(self):
        for payload, status in (({"enabled": True, "user": None}, 200), ({"error": "expired"}, 401)):
            with self.subTest(status=status):
                self.store.values[ORIGIN] = [cookie()]
                restarted = api.MoriClient(ORIGIN, session_store=self.store)
                self.response(restarted, payload, status)
                restarted.raw_request("GET", "/api/auth/session")
                self.assertIsNone(restarted.user_id)
                self.assertFalse(restarted.session_restored)
                self.assertEqual(list(restarted._cookies), [])
                self.assertNotIn(ORIGIN, self.store.values)

    def test_fork_cannot_load_or_write_saved_cookie_or_resurrect_logout(self):
        self.response(self.client, {"user": {"id": "user-a"}}, session_cookie=True)
        self.client.login("fixture", "password")
        fork = self.client.fork_session()
        self.assertIsNone(fork._session_store)
        self.assertFalse(fork.session_restored)
        self.client.clear_session()
        with self.assertRaises(api.MoriApiError):
            fork.assert_session_current()
        self.assertNotIn(ORIGIN, self.store.values)

    def test_http_client_cannot_load_or_save_persistent_credentials(self):
        self.store.values["http://127.0.0.1"] = [cookie(domain="127.0.0.1")]
        client = api.MoriClient("http://127.0.0.1", session_store=self.store)
        self.assertIsNone(client._session_store)
        self.assertEqual(list(client._cookies), [])
        client.close()

    def test_storage_errors_do_not_block_server_authentication(self):
        broken = mock.Mock()
        broken.load.side_effect = OSError("unavailable")
        broken.save.side_effect = OSError("unavailable")
        broken.clear.side_effect = OSError("unavailable")
        client = api.MoriClient(ORIGIN, session_store=broken)
        self.response(client, {"user": {"id": "user-a"}}, session_cookie=True)
        client.login("fixture", "password")
        self.assertEqual(client.user_id, "user-a")
        client.clear_session()
        self.assertIsNone(client.user_id)

    def test_raw_logout_forgets_saved_session_even_when_server_is_unreachable(self):
        self.store.values[ORIGIN] = [cookie()]
        self.client._opener.open = mock.Mock(side_effect=OSError("offline"))
        with self.assertRaises(api.MoriApiError):
            self.client.raw_request("POST", "/api/auth/logout", b"{}", "application/json")
        self.assertNotIn(ORIGIN, self.store.values)


if __name__ == "__main__":
    unittest.main()
