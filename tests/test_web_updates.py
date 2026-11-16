"""Real public HTTP + zip update checks; no accounts, model calls, or Qt."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock
import zipfile


spec = importlib.util.spec_from_file_location("mori_web_updates_fixture", Path(__file__).resolve().parents[1] / "mori_krita" / "web_updates.py")
updates = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = updates
spec.loader.exec_module(updates)


def metadata(version="20261106-new-a1b2", minimum="0.3.7"):
    return {"schemaVersion": 1, "uiVersion": version, "bridgeMinVersion": minimum}


def bundle(meta=None, extra=None, exclude=()):
    meta = meta or metadata()
    files = {"index.html": b'<script src="/assets/app.js"></script>',
             "ui-release.json": json.dumps(meta).encode(), "assets/app.js": b"window.synthetic=true;"}
    files.update(extra or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            if name not in exclude:
                archive.writestr(name, data)
    return output.getvalue()


class WebUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes = {}
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                cls.requests.append((self.path, dict(self.headers)))
                status, headers, data = cls.routes.get(self.path, (404, {}, b"missing"))
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundled = self.root / "bundled"
        self.bundled.mkdir()
        (self.bundled / "index.html").write_text("Bundled page")
        (self.bundled / "ui-release.json").write_text(json.dumps(metadata("20261105-old-1234")))
        self.manager = updates.WebUpdateManager(self.url, self.bundled, self.root / "cache", "0.3.7")
        self.routes.clear()
        self.requests.clear()

    def publish(self, payload=None, meta=None, manifest_changes=None):
        meta = meta or metadata()
        payload = payload if payload is not None else bundle(meta)
        path = "/krita-ui/bundles/" + meta["uiVersion"] + ".zip"
        manifest = {**meta, "bundle": {"url": path, "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}}
        manifest.update(manifest_changes or {})
        self.routes["/krita-ui/manifest.json"] = (200, {}, json.dumps(manifest).encode())
        self.routes[path] = (200, {}, payload)
        return manifest

    def test_verified_release_is_cached_and_reused_without_redownloading(self):
        self.publish(bundle(extra={"fonts/NOTICE.txt": b"Synthetic font license notice"}))
        result = self.manager.check()
        self.assertTrue(result.updated)
        self.assertEqual(result.release.ui_version, metadata()["uiVersion"])
        self.assertEqual((result.release.root / "assets/app.js").read_bytes(), b"window.synthetic=true;")
        self.assertEqual((result.release.root / "fonts/NOTICE.txt").read_bytes(), b"Synthetic font license notice")
        self.assertTrue((self.manager.cache_root / "current.json").exists())
        self.assertEqual(self.manager.selected(), result.release)
        before = len(self.requests)
        again = self.manager.check()
        self.assertFalse(again.updated)
        self.assertEqual(again.release, result.release)
        self.assertEqual(len(self.requests), before + 1)

    def test_requests_have_no_account_or_cookie_headers(self):
        self.publish()
        self.manager.check()
        self.assertEqual(len(self.requests), 2)
        for _path, headers in self.requests:
            lowered = {key.lower() for key in headers}
            self.assertFalse(lowered.intersection(("cookie", "authorization", "x-mori-user", "x-mori-canvas")))

    def test_missing_channel_uses_bundled(self):
        result = self.manager.check()
        self.assertEqual(result.release.root, self.bundled)
        self.assertFalse(result.updated)

    def test_failed_check_keeps_previous_verified_cache(self):
        self.publish()
        previous = self.manager.check().release
        self.routes.clear()
        result = self.manager.check()
        self.assertEqual(result.release, previous)
        self.assertEqual(self.manager.selected(), previous)

    def test_hash_mismatch_keeps_previous_pointer_and_release(self):
        self.publish()
        previous = self.manager.check().release
        pointer = (self.manager.cache_root / "current.json").read_bytes()
        manifest = self.publish(meta=metadata("20261106-newer"))
        manifest["bundle"]["sha256"] = "0" * 64
        self.routes["/krita-ui/manifest.json"] = (200, {}, json.dumps(manifest).encode())
        result = self.manager.check()
        self.assertEqual(result.release, previous)
        self.assertEqual((self.manager.cache_root / "current.json").read_bytes(), pointer)

    def test_incompatible_minimum_uses_old_release_without_downloading(self):
        self.publish(meta=metadata(minimum="0.4.0"))
        result = self.manager.check()
        self.assertEqual(result.release.root, self.bundled)
        self.assertIn("需要更新 Mori 插件", result.message)
        self.assertEqual(len(self.requests), 1)

    def test_bad_schema_and_versions_are_rejected(self):
        for change in ({"schemaVersion": True}, {"schemaVersion": 2}, {"uiVersion": "../unsafe"}, {"bridgeMinVersion": "broken"}):
            with self.subTest(change=change):
                self.publish(manifest_changes=change)
                self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_cross_origin_and_nonpublic_urls_are_not_requested(self):
        for url in ("https://other.example/krita-ui/new.zip", "//other.example/a.zip", "/api/assets/a.zip",
                    "/krita-ui/../api/a.zip", "/krita-ui/%2e%2e/api/a.zip", "/krita-ui/new.zip?token=secret",
                    "/krita-ui/%252e%252e/api/a.zip", "/krita-ui/a\nb.zip", "/krita-ui/new file.zip",
                    "/krita-ui/%2fapi/a.zip", "/krita-ui/a%00.zip"):
            with self.subTest(url=url):
                manifest = self.publish()
                manifest["bundle"]["url"] = url
                self.routes["/krita-ui/manifest.json"] = (200, {}, json.dumps(manifest).encode())
                before = len(self.requests)
                self.assertEqual(self.manager.check().release.root, self.bundled)
                self.assertEqual(len(self.requests), before + 1)

    def test_redirect_is_not_followed(self):
        self.routes["/krita-ui/manifest.json"] = (302, {"Location": self.url + "/unexpected"}, b"")
        result = self.manager.check()
        self.assertEqual(result.release.root, self.bundled)
        self.assertEqual([path for path, _headers in self.requests], ["/krita-ui/manifest.json"])

    def test_length_mismatch_is_rejected(self):
        manifest = self.publish()
        manifest["bundle"]["size"] += 1
        self.routes["/krita-ui/manifest.json"] = (200, {}, json.dumps(manifest).encode())
        self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_invalid_bundle_sizes_are_rejected_before_download(self):
        for size in (True, 0, -1, updates.MAX_BUNDLE_BYTES + 1, "12"):
            with self.subTest(size=size):
                manifest = self.publish()
                manifest["bundle"]["size"] = size
                self.routes["/krita-ui/manifest.json"] = (200, {}, json.dumps(manifest).encode())
                before = len(self.requests)
                self.assertEqual(self.manager.check().release.root, self.bundled)
                self.assertEqual(len(self.requests), before + 1)

    def test_traversal_windows_paths_and_executables_are_rejected(self):
        for name in ("../escaped.js", "/escaped.js", "assets\\escaped.js", "C:/escaped.js", "assets/run.exe",
                     "assets/./app.js", "assets/space ./app.js", "assets/trailing./app.js"):
            with self.subTest(name=name):
                payload = bundle(extra={name: b"bad"})
                if "\\" in name:
                    payload = payload.replace(name.replace("\\", "/").encode(), name.encode())
                self.publish(payload)
                self.assertEqual(self.manager.check().release.root, self.bundled)
                self.assertFalse((self.root / "escaped.js").exists())

    def test_symlink_member_is_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(bundle())) as original, zipfile.ZipFile(output, "w") as archive:
            for item in original.infolist():
                archive.writestr(item.filename, original.read(item))
            link = zipfile.ZipInfo("assets/link.js")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "../../outside")
        self.publish(output.getvalue())
        self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_case_insensitive_duplicate_is_rejected(self):
        self.publish(bundle(extra={"ASSETS/APP.JS": b"collision"}))
        self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_missing_entry_or_metadata_is_rejected(self):
        for name in ("index.html", "ui-release.json"):
            with self.subTest(name=name):
                self.publish(bundle(exclude=(name,)))
                self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_bundle_metadata_must_match_manifest(self):
        self.publish(bundle(metadata("different-release")))
        self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_expanded_size_and_file_count_are_limited(self):
        with mock.patch.object(updates, "MAX_EXPANDED_BYTES", 500):
            self.publish(bundle(extra={"assets/big.js": b"x" * 1000}))
            self.assertEqual(self.manager.check().release.root, self.bundled)
        with mock.patch.object(updates, "MAX_FILES", 3):
            self.publish(bundle(extra={"assets/one.js": b"x"}))
            self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_corrupted_zip_is_rejected(self):
        self.publish(b"not a zip")
        self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_cancel_before_check_does_not_request_or_replace(self):
        self.publish()
        cancel = threading.Event()
        cancel.set()
        result = self.manager.check(cancel)
        self.assertEqual(result.release.root, self.bundled)
        self.assertEqual(self.requests, [])

    def test_cancel_after_download_does_not_commit_pointer(self):
        self.publish()
        cancel = threading.Event()
        original = self.manager._download

        def download(*args, **kwargs):
            result = original(*args, **kwargs)
            if args[0].endswith(".zip"):
                cancel.set()
            return result

        with mock.patch.object(self.manager, "_download", side_effect=download):
            result = self.manager.check(cancel)
        self.assertEqual(result.release.root, self.bundled)
        self.assertFalse((self.manager.cache_root / "current.json").exists())

    def test_tampered_cache_falls_back_to_bundle(self):
        self.publish()
        release = self.manager.check().release
        (release.root / "assets/app.js").write_bytes(b"modified")
        self.assertEqual(self.manager.selected().root, self.bundled)
        self.routes.clear()
        self.assertEqual(self.manager.check().release.root, self.bundled)

    def test_cache_reparse_points_are_rejected_before_reading(self):
        self.publish()
        release = self.manager.check().release
        original = updates.Path.lstat

        def lstat(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == release.root:
                return type("Reparse", (), {"st_mode": info.st_mode, "st_file_attributes": 1024})()
            return info

        with mock.patch.object(updates.Path, "lstat", lstat):
            self.assertEqual(self.manager.selected().root, self.bundled)

    def test_origins_and_base_paths_have_separate_cache_keys(self):
        one = updates.WebUpdateManager("https://example.com/", self.bundled, self.root, "0.3.7")
        two = updates.WebUpdateManager("https://EXAMPLE.COM:443", self.bundled, self.root, "0.3.7")
        other = updates.WebUpdateManager("https://example.com/other", self.bundled, self.root, "0.3.7")
        self.assertEqual(one.cache_root, two.cache_root)
        self.assertNotEqual(one.cache_root, other.cache_root)

    def test_remote_http_credentials_and_fragments_are_rejected(self):
        for url in ("http://example.com", "https://user:secret@example.com", "https://example.com/#token", "https://example.com/?token=x"):
            with self.subTest(url=url), self.assertRaises(updates.WebUpdateError):
                updates.WebUpdateManager(url, self.bundled, self.root, "0.3.7")

    def test_original_canvas_and_account_files_are_untouched(self):
        user_data = self.root / "user-data"
        user_data.mkdir()
        for filename in ("canvas.json", "session.dat"):
            (user_data / filename).write_bytes(b"do not replace")
        self.publish()
        self.manager.check()
        self.assertEqual([path.read_bytes() for path in sorted(user_data.iterdir())], [b"do not replace"] * 2)
        self.assertEqual((self.bundled / "index.html").read_text(), "Bundled page")


if __name__ == "__main__":
    unittest.main()
