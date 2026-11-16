"""Reference delivery regressions using synthetic bytes and blocked fake I/O.

The tests never launch Krita, read a real document, or contact a Mori server.
Events make the concurrency checks about independence from network completion,
rather than an arbitrary upload-speed threshold.
"""

import concurrent.futures
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "mori_reference_gateway",
    Path(__file__).resolve().parents[1] / "mori_krita" / "gateway.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
MoriGateway = _MODULE.MoriGateway
GatewayError = _MODULE.GatewayError

PNG = b"\x89PNG\r\n\x1a\nsynthetic-reference"
MEDIA = "/media/11111111-1111-4111-8111-111111111111.png"


class ChannelClient:
    """Independent sessions share only fake server state and test observations."""

    base_url = "https://mori.invalid"

    def __init__(self, shared=None, user_id="alice", channel="main"):
        self.user_id = user_id
        self.user = {"id": user_id, "name": user_id} if user_id else None
        self.auth_enabled = True
        self.channel = channel
        self.shared = shared if shared is not None else {
            "lock": threading.RLock(),
            "calls": [],
            "uploads": [],
            "forks": [],
            "assets": [],
            "jobs": [],
            "block_path": None,
            "entered": threading.Event(),
            "release": threading.Event(),
            "media": b"remote-only-image",
        }

    def fork_session(self, *args, **kwargs):
        with self.shared["lock"]:
            channel = args[0] if args else kwargs.get("channel", "fork-%s" % (len(self.shared["forks"]) + 1))
            clone = type(self)(self.shared, self.user_id, channel)
            self.shared["forks"].append(clone)
        return clone

    def clear_session(self):
        self.user_id = None
        self.user = None

    def raw_request(self, method, path, body=None, content_type=None):
        value = json.loads(body) if body else None
        with self.shared["lock"]:
            self.shared["calls"].append((self.channel, self.user_id, method, path, value))
        if method == "GET" and path == self.shared["block_path"]:
            self.shared["entered"].set()
            if not self.shared["release"].wait(10):
                raise RuntimeError("The test did not release its fake network request")
        with self.shared["lock"]:
            if path.startswith("/media/"):
                return 200, {"content-type": "image/png"}, self.shared["media"]
            if method == "GET" and path == "/api/assets":
                result = list(self.shared["assets"])
            elif method == "GET" and path == "/api/jobs":
                result = list(self.shared["jobs"])
            elif method == "POST" and path == "/api/generate":
                result = {"id": "new-job", "assetIds": [], "status": "queued", **value}
                self.shared["jobs"].append(result)
            elif method == "POST" and path == "/api/assets":
                result = {"id": "browser-upload", "url": MEDIA, "name": "Upload"}
                self.shared["assets"].append(result)
            else:
                result = []
        return 200, {"content-type": "application/json"}, json.dumps(result).encode()

    def upload_image(self, data, name, mime="image/png"):
        with self.shared["lock"]:
            index = len(self.shared["uploads"]) + 1
            self.shared["uploads"].append((self.channel, self.user_id, data, name, mime))
            asset = {"id": "reference-%s" % index,
                     "url": "/media/11111111-1111-4111-8111-%012d.png" % index,
                     "name": name}
            self.shared["assets"].append(asset)
            return dict(asset)

    def download_asset(self, path):
        status, headers, data = self.raw_request("GET", path)
        return data


class ReferencePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        self.web.mkdir()
        (self.web / "index.html").write_text("<!doctype html><title>Fixture</title>", encoding="utf-8")
        self.client = ChannelClient()
        self.capture_bytes = PNG
        self.gateway = MoriGateway(self.client, self.web, self.root / "state", self.invoke)
        self.gateway.auto_import = False
        self.gateway._sync_identity()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def tearDown(self):
        self.client.shared["release"].set()
        self.pool.shutdown(wait=True)
        self.gateway.close()
        self.temp.cleanup()

    def invoke(self, operation, data):
        if operation == "capture":
            return SimpleNamespace(png_bytes=self.capture_bytes, layer_name="Fixture", document_name="Fixture")
        if operation == "target":
            return "fixture-document"
        raise AssertionError("Unexpected GUI operation: " + operation)

    def request(self, method, path, value=None, user="alice"):
        body = None if value is None else json.dumps(value).encode()
        return self.gateway.handle(method, path, body, browser_user=user, enforce_user=True)

    def get_json(self, path, user="alice"):
        status, _, body = self.request("GET", path, user=user)
        self.assertEqual(status, 200)
        return json.loads(body)

    def remote_calls(self, method, path):
        with self.client.shared["lock"]:
            return [call for call in self.client.shared["calls"] if call[2:4] == (method, path)]

    def block_remote(self, path):
        self.client.shared["block_path"] = path
        future = self.pool.submit(self.request, "GET", path)
        self.assertTrue(self.client.shared["entered"].wait(3), "Fake remote request did not start")
        return future

    def test_local_events_state_selection_and_static_do_not_wait_for_remote_io(self):
        remote = self.block_remote("/api/jobs")
        self.gateway.push("status", message="local-event")
        for method, path, value in (
            ("GET", "/krita/events?after=0", None),
            ("GET", "/api/state", None),
            ("POST", "/krita/selection", {"assetId": "reference"}),
            ("GET", "/", None),
        ):
            with self.subTest(path=path):
                local = self.pool.submit(self.request, method, path, value)
                self.assertEqual(local.result(timeout=2)[0], 200)
                self.assertFalse(remote.done(), "Remote I/O unexpectedly completed before local response")
        self.client.shared["release"].set()
        self.assertEqual(remote.result(timeout=3)[0], 200)

    def test_local_account_routes_reject_an_old_browser_account(self):
        self.gateway.push("status", message="alice-only-event")
        for method, path, value in (
            ("GET", "/krita/events?after=0", None),
            ("POST", "/krita/selection", {"assetId": "alice-private"}),
            ("GET", "/api/state", None),
        ):
            with self.subTest(path=path), self.assertRaises(GatewayError) as raised:
                self.request(method, path, value, user="old-account")
            self.assertEqual(raised.exception.status, 401)
        self.assertIsNone(self.gateway.selected_asset)

    def test_uploaded_reference_media_is_served_from_original_local_bytes(self):
        self.gateway.capture("selection")
        asset = self.client.shared["assets"][-1]
        status, headers, data = self.request("GET", asset["url"])
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertEqual(data, PNG)
        self.assertEqual(self.remote_calls("GET", asset["url"]), [])

    def test_reference_deduplication_is_limited_to_the_same_account(self):
        self.gateway.capture("selection")
        self.gateway.capture("selection")
        self.assertEqual(len(self.client.shared["uploads"]), 1)
        self.client.user_id = "bob"
        self.client.user = {"id": "bob"}
        self.gateway._sync_identity()
        self.gateway.capture("selection")
        self.assertEqual(len(self.client.shared["uploads"]), 2)
        self.assertEqual(self.client.shared["uploads"][-1][1], "bob")

    def test_media_cache_cannot_replay_another_accounts_reference(self):
        self.gateway.capture("selection")
        url = self.client.shared["assets"][-1]["url"]
        self.assertEqual(self.request("GET", url)[2], PNG)
        self.client.user_id = "bob"
        self.client.user = {"id": "bob"}
        self.gateway._sync_identity()
        self.assertEqual(self.request("GET", url, user="bob")[2], b"remote-only-image")
        self.assertEqual(len(self.remote_calls("GET", url)), 1)

    def test_media_cache_honors_byte_limit_and_recent_use(self):
        self.gateway.media_cache_limit = 12
        a, b, c = ("/media/%s.png" % value for value in ("aa", "bb", "cc"))
        self.gateway._cache_media(a, b"aaaaa", "image/png", self.gateway.epoch)
        self.gateway._cache_media(b, b"bbbbb", "image/png", self.gateway.epoch)
        self.assertIsNotNone(self.gateway._get_media(a))
        self.gateway._cache_media(c, b"ccccc", "image/png", self.gateway.epoch)
        self.assertIsNone(self.gateway._get_media(b))
        self.assertIsNotNone(self.gateway._get_media(a))
        self.assertIsNotNone(self.gateway._get_media(c))
        self.gateway._cache_media("/media/dd.png", b"x" * 13, "image/png", self.gateway.epoch)
        self.assertIsNone(self.gateway._get_media("/media/dd.png"))

    def test_media_from_an_obsolete_epoch_is_not_cached(self):
        old_epoch = self.gateway.epoch
        self.client.user_id = "bob"
        self.gateway._sync_identity()
        self.gateway._cache_media(MEDIA, PNG, "image/png", old_epoch)
        self.assertIsNone(self.gateway._get_media(MEDIA))

    def test_concurrent_asset_pollers_share_one_remote_fetch_and_short_cache(self):
        first = self.block_remote("/api/assets")
        second_started = threading.Event()

        def second_poll():
            second_started.set()
            return self.request("GET", "/api/assets")

        second = self.pool.submit(second_poll)
        self.assertTrue(second_started.wait(2))
        self.client.shared["release"].set()
        self.assertEqual(first.result(timeout=3)[0], 200)
        self.assertEqual(second.result(timeout=3)[0], 200)
        self.get_json("/api/assets")
        self.assertEqual(len(self.remote_calls("GET", "/api/assets")), 1)

    def test_capture_is_independent_of_a_slow_polling_channel(self):
        remote = self.block_remote("/api/jobs")
        capture = self.pool.submit(self.gateway.capture, "selection")
        capture.result(timeout=2)
        self.assertFalse(remote.done())
        self.assertEqual(len(self.client.shared["uploads"]), 1)
        upload_channel = self.client.shared["uploads"][0][0]
        polling_channel = self.remote_calls("GET", "/api/jobs")[0][0]
        self.assertNotEqual(upload_channel, polling_channel)
        self.client.shared["release"].set()
        remote.result(timeout=3)

    def test_upload_updates_previously_cached_assets(self):
        self.assertEqual(self.get_json("/api/assets"), [])
        self.gateway.capture("selection")
        asset = self.client.shared["assets"][-1]
        self.assertIn(asset["id"], [row["id"] for row in self.get_json("/api/assets")])

    def test_browser_upload_updates_previously_cached_assets(self):
        self.assertEqual(self.get_json("/api/assets"), [])
        self.request("POST", "/api/assets", {"name": "Fixture", "dataUrl": "data:image/png;base64,c3ludGhldGlj"})
        self.assertIn("browser-upload", [row["id"] for row in self.get_json("/api/assets")])

    def test_generation_submission_invalidates_previously_cached_jobs(self):
        self.assertEqual(self.get_json("/api/jobs"), [])
        self.request("POST", "/api/generate", {"nodeId": "fixture-node", "clientRequestId": "fixture-request-123"})
        self.assertIn("new-job", [row["id"] for row in self.get_json("/api/jobs")])


if __name__ == "__main__":
    unittest.main()
