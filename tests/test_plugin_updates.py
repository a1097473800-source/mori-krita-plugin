"""Exercise public update discovery against an isolated HTTP server."""
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("plugin_updates_fixture",
    Path(__file__).resolve().parents[1] / "mori_krita/plugin_updates.py")
updates = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = updates
spec.loader.exec_module(updates)


class PluginUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.routes, cls.requests = {}, []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass
            def do_GET(self):
                cls.requests.append((self.path, dict(self.headers)))
                code, data = cls.routes.get(self.path, (404, b"missing"))
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        self.routes.clear()
        self.requests.clear()
        self.manager = updates.PluginUpdateManager(self.base, "0.4.9")
        self.patch = mock.patch.object(updates, "LATEST_API", self.base + "/github/latest")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def publish(self, version="0.4.10", **changes):
        value = {"schemaVersion": 1, "version": version,
            "releaseUrl": "https://github.com/" + updates.REPOSITORY + "/releases/tag/v" + version,
            "package": {"url": "https://github.com/" + updates.REPOSITORY + "/releases/download/v" + version
                + "/Mori-Krita-" + version + "-Compatibility-Update.zip"}, "notes": "修复更新提醒"}
        value.update(changes)
        self.routes["/krita-plugin/manifest.json"] = (200, json.dumps(value).encode())
        return value

    def github(self, version="0.4.10", **changes):
        value = {"tag_name": "v" + version, "draft": False, "prerelease": False,
            "html_url": "https://github.com/" + updates.REPOSITORY + "/releases/tag/v" + version,
            "assets": [{"name": "Mori-Krita-" + version + "-Compatibility-Update.zip",
                "browser_download_url": "https://github.com/" + updates.REPOSITORY + "/releases/download/v"
                    + version + "/Mori-Krita-" + version + "-Compatibility-Update.zip"}], "body": "公开更新"}
        value.update(changes)
        self.routes["/github/latest"] = (200, json.dumps(value).encode())

    def test_numeric_versions_find_0410_after_049_without_login(self):
        self.publish()
        result = self.manager.check()
        self.assertTrue(result.available)
        self.assertTrue(result.checked)
        self.assertEqual(result.release.version, "0.4.10")
        self.assertEqual(len(self.requests), 1)
        for path, headers in self.requests:
            self.assertEqual(path, "/krita-plugin/manifest.json")
            self.assertFalse({h.lower() for h in headers} & {"cookie", "authorization", "x-mori-user"})

    def test_missing_first_party_channel_falls_back_to_github(self):
        self.github()
        result = self.manager.check()
        self.assertTrue(result.available)
        self.assertEqual([path for path, _ in self.requests], ["/krita-plugin/manifest.json", "/github/latest"])

    def test_existing_version_and_newer_local_install_do_not_offer_downgrade(self):
        for version in ("0.4.9", "0.4.7"):
            self.publish(version)
            result = self.manager.check()
            self.assertFalse(result.available)
            self.assertTrue(result.checked)

    def test_unreachable_channels_report_failure_not_latest(self):
        result = self.manager.check()
        self.assertFalse(result.checked)
        self.assertFalse(result.available)
        self.assertIn("无法检查", result.message)
        self.assertNotIn("已是最新", result.message)

    def test_draft_and_prerelease_are_not_announced(self):
        for change in ({"draft": True}, {"prerelease": True}):
            self.github(**change)
            self.assertFalse(self.manager.check().checked)

    def test_invalid_schema_versions_and_foreign_downloads_are_rejected(self):
        for changes in ({"schemaVersion": True}, {"version": "0.4.10-beta"},
            {"releaseUrl": "https://example.com/releases/tag/v0.4.10"},
            {"package": {"url": "https://github.com/attacker/repo/releases/download/v1/evil.zip"}}):
            self.publish(**changes)
            self.assertFalse(self.manager.check().checked)

    def test_cancel_before_start_does_not_request_any_server(self):
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(self.manager.check(cancel).checked)
        self.assertEqual(self.requests, [])

    def test_oversized_metadata_falls_back_without_accepting_it(self):
        self.routes["/krita-plugin/manifest.json"] = (200, b"x" * (updates.MAX_METADATA_BYTES + 1))
        self.github()
        self.assertTrue(self.manager.check().available)

    def test_untrusted_notes_remain_literal_text(self):
        text = '<a href="https://example.com/">更新</a>'
        self.publish(notes=text)
        self.assertEqual(self.manager.check().release.notes, text)

    def test_official_direct_package_works_without_github_downloads(self):
        url = "https://morinet.chat/krita-plugin/packages/Mori-Krita-0.4.10-Compatibility-Update.zip"
        self.publish(package={"url": url})
        self.assertEqual(self.manager.check().release.download_url, url)
        for invalid in (url.replace("https:", "http:"), url.replace("morinet.chat", "example.com"),
                url + "?token=secret", url.replace("packages/", "packages/../")):
            with self.assertRaises(ValueError):
                updates.release_url(invalid, package=True)

    def test_credentials_and_remote_cleartext_origins_are_rejected(self):
        for url in ("http://example.com", "https://user:password@example.com", "https://example.com/?token=secret"):
            with self.assertRaises(ValueError):
                updates.PluginUpdateManager(url, "0.4.9")
