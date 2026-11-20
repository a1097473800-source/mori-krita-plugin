"""Real HTTP/cookie tests for native browser login; all identities are synthetic."""
import base64
import hashlib
import importlib
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

package = ModuleType('mori_browser_fixture')
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'mori_krita')]
sys.modules[package.__name__] = package
api = importlib.import_module(package.__name__ + '.api')
BrowserLogin = importlib.import_module(package.__name__ + '.browser_login').BrowserLogin


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or '{}')
        self.server.calls.append((self.path, value))
        status = 200
        cookie = None
        if self.path == '/api/auth/desktop/start':
            self.server.challenge = value['challenge']
            result = {'requestId': 'R' * 43, 'verificationPath': self.server.verification_path, 'interval': 2, 'expiresIn': 30}
        elif self.path == '/api/auth/desktop/poll':
            actual = base64.urlsafe_b64encode(hashlib.sha256(value['verifier'].encode()).digest()).decode().rstrip('=')
            if actual != self.server.challenge:
                status, result = 403, {'error': 'invalid verifier'}
            elif self.server.denied:
                status, result = 403, {'error': '已取消插件登录', 'code': 'DESKTOP_DENIED'}
            elif not self.server.approved:
                result = {'status': 'pending'}
            else:
                self.server.poll_entered.set()
                self.server.release_poll.wait(5)
                result = {'status': 'complete', 'user': {'id': 'google-user', 'email': 'qa@example.invalid', 'name': 'QA'}}
                cookie = 'mori_session=google-fixture; HttpOnly; Path=/; SameSite=Lax'
        elif self.path == '/api/auth/desktop/cancel':
            self.server.cancelled.set()
            result = {'ok': True}
        elif self.path == '/api/auth/logout':
            self.server.logged_out.set()
            result = {'ok': True}
            cookie = 'mori_session=; Max-Age=0; Path=/'
        elif self.path == '/api/auth/login':
            result = {'user': {'id': 'password-user', 'email': value['email'], 'name': 'Other'}}
            cookie = 'mori_session=password-fixture; HttpOnly; Path=/'
        else:
            status, result = 404, {'error': 'not found'}
        data = json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(data)


class BrowserLoginTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.calls = []
        self.server.verification_path = '/?desktop_login=' + 'R' * 43
        self.server.denied = False
        self.server.approved = True
        self.server.poll_entered = threading.Event()
        self.server.release_poll = threading.Event()
        self.server.release_poll.set()
        self.server.cancelled = threading.Event()
        self.server.logged_out = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = api.MoriClient('http://127.0.0.1:%s' % self.server.server_port, _proxy_config={})
        self.opened = []
        self.completed = []
        self.manager = BrowserLogin(self.client, lambda: self.completed.append(True), lambda url: self.opened.append(url) or True)

    def tearDown(self):
        self.manager.cancel()
        self.server.release_poll.set()
        self.server.shutdown()
        self.server.server_close()
        self.client.close()
        self.thread.join()

    def wait_status(self, attempt, *states):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            value = self.manager.status(attempt)
            if value['status'] in states:
                return value
            time.sleep(.01)
        self.fail('login did not reach %r: %r' % (states, value))

    def test_browser_consent_adopts_the_original_account_without_exposing_credentials(self):
        value = self.manager.start()
        complete = self.wait_status(value['attemptId'], 'complete', 'error')
        self.assertEqual(complete['status'], 'complete', complete)
        self.assertEqual(self.client.user_id, 'google-user')
        self.assertEqual(len(self.completed), 1)
        self.assertEqual(self.opened, [self.client.base_url + self.server.verification_path])
        self.assertEqual(set(complete), {'attemptId', 'status', 'message'})
        self.assertEqual([c.value for c in self.client._cookies], ['google-fixture'])
        verifier = next(value['verifier'] for route, value in self.server.calls if route.endswith('/poll'))
        self.assertNotIn(verifier, self.opened[0])
        self.assertNotIn('google-fixture', json.dumps(complete))

    def test_user_denial_keeps_the_plugin_logged_out(self):
        self.server.denied = True
        attempt = self.manager.start()['attemptId']
        self.assertEqual(self.wait_status(attempt, 'error')['message'], '已取消插件登录')
        self.assertIsNone(self.client.user_id)

    def test_cancel_before_start_and_unsafe_server_urls_never_open_a_browser(self):
        attempt = 'cancel-before-start-123'
        self.manager.cancel(attempt)
        self.assertEqual(self.manager.start(attempt)['status'], 'cancelled')
        self.assertEqual(self.server.calls, [])
        self.server.verification_path = 'https://attacker.invalid/login'
        attempt = self.manager.start()['attemptId']
        self.wait_status(attempt, 'error')
        self.assertEqual(self.opened, [])

    def test_cancel_after_session_redemption_revokes_unused_session(self):
        self.server.release_poll.clear()
        attempt = self.manager.start()['attemptId']
        self.assertTrue(self.server.poll_entered.wait(4))
        self.manager.cancel(attempt)
        self.server.release_poll.set()
        self.assertTrue(self.server.logged_out.wait(3))
        self.assertIsNone(self.client.user_id)
        self.assertEqual(self.manager.status(attempt)['status'], 'cancelled')

    def test_account_switch_during_browser_login_cannot_overwrite_the_new_account(self):
        self.server.release_poll.clear()
        attempt = self.manager.start()['attemptId']
        self.assertTrue(self.server.poll_entered.wait(4))
        self.client.login('other@example.invalid', 'test-password')
        self.server.release_poll.set()
        self.wait_status(attempt, 'error')
        self.assertEqual(self.client.user_id, 'password-user')
        self.assertTrue(self.server.logged_out.wait(3))

    def test_pending_cancel_cleans_up_remote_request_and_stale_cancel_does_not_cancel_new_attempt(self):
        self.server.approved = False
        attempt = self.manager.start()['attemptId']
        self.wait_status(attempt, 'pending')
        self.manager.cancel(attempt)
        self.assertTrue(self.server.cancelled.wait(3))
        new_attempt = self.manager.start()['attemptId']
        self.manager.cancel(attempt)
        self.assertIn(self.manager.status(new_attempt)['status'], ('starting', 'pending'))
        self.manager.cancel(new_attempt)

    def test_session_adoption_rejects_a_stale_epoch_and_different_server(self):
        source = self.client.new_login_transport()
        source.login('qa@example.invalid', 'test-password')
        version = self.client.session_version
        self.client.clear_session()
        with self.assertRaises(api.MoriApiError):
            self.client.adopt_login(source, version)
        different = api.MoriClient('http://127.0.0.1:1', _proxy_config={})
        with self.assertRaises(api.MoriApiError):
            different.adopt_login(source, different.session_version)
        source.close()
        different.close()
