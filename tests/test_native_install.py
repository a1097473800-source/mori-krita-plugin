"""Native packages are committed in one step and loaded on the next import."""
import concurrent.futures
import hashlib
import importlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "mori_krita"
fixture = types.ModuleType("native_install_fixture")
fixture.__path__ = [str(SOURCE)]
sys.modules[fixture.__name__] = fixture
install = importlib.import_module(fixture.__name__ + ".native_install")
loader = importlib.import_module(fixture.__name__ + ".native_loader")
updates = importlib.import_module(fixture.__name__ + ".plugin_updates")


class NativeInstallTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "mori_krita"
        self.root.mkdir()
        for name in ("__init__.py", "native_loader.py"):
            shutil.copyfile(SOURCE / name, self.root / name)
        (self.root / "version.py").write_text('VERSION = "0.4.17"\n', encoding="utf-8")
        (self.root / "startup.py").write_text("from .version import VERSION\n", encoding="utf-8")
        self.version = "0.4.18"
        self.cancel = threading.Event()
        self.manager = updates.PluginUpdateManager("https://morinet.chat", "0.4.17")
        self.messages = []
        self.archive = self.package()

    def package(self, omit=None, declared_version=None):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            for name in loader.REQUIRED:
                if name == omit:
                    continue
                data = b""
                if name == "startup.py":
                    data = b"from .version import VERSION\n"
                elif name == "version.py":
                    data = ('VERSION = "' + (declared_version or self.version) + '"\n').encode()
                prefix = "Mori-Krita-" + self.version + "-Compatibility-Update-20261127-100000/"
                zipped.writestr(prefix + "mori_krita/" + name, data)
            zipped.writestr(prefix + "update-installed.ps1", "offline installer, not part of the plugin")
        return stream.getvalue()

    def run_install(self, response=None, cancel=None):
        name = "Mori-Krita-" + self.version + "-Compatibility-Update.zip"
        digest = hashlib.sha256(self.archive).hexdigest()
        manifest = {"schemaVersion": 1, "version": self.version, "releaseUrl": updates.RELEASES_URL + "/tag/v" + self.version,
                    "package": {"url": "https://morinet.chat/krita-plugin/packages/" + name,
                                "sha256": digest, "size": len(self.archive)}}
        opener = mock.Mock()
        opener.open.side_effect = lambda *a, **kw: io.BytesIO(response if response is not None else self.archive)
        manager = updates.PluginUpdateManager("https://morinet.chat", "0.4.17")
        with mock.patch.object(manager, "_read", return_value=manifest):
            install.install_update(manager, self.version, self.messages.append, cancel or self.cancel,
                                   bundled=self.root, opener=opener)
        return opener

    def loaded_version(self):
        code = "import mori_krita; print(mori_krita.startup.VERSION)"
        return subprocess.check_output([sys.executable, "-B", "-c", code], cwd=self.root.parent,
                                       text=True).strip()

    def test_successful_install_uses_normal_plugin_entry_in_fresh_process(self):
        self.assertEqual(self.loaded_version(), "0.4.17")
        self.run_install()
        self.assertEqual(loader.package_version(self.root), "0.4.17")
        self.assertEqual(self.loaded_version(), self.version)
        self.assertTrue(any("100%" in text for text in self.messages))
        self.assertFalse((loader.active_package(self.root) / "update-installed.ps1").exists())
        self.run_install().open.assert_not_called()

    def test_bad_download_then_retry(self):
        with self.assertRaisesRegex(ValueError, "校验失败"):
            self.run_install(b"!" + self.archive[1:])
        self.assertEqual(self.loaded_version(), "0.4.17")
        self.assertEqual(list(loader.release_store(self.root).iterdir()), [])
        self.run_install()
        self.assertEqual(self.loaded_version(), self.version)

    def test_missing_components_and_wrong_version_do_not_activate(self):
        for archive in (self.package(omit="startup.py"), self.package(declared_version="0.4.19")):
            self.archive = archive
            with self.assertRaises(ValueError):
                self.run_install()
            self.assertEqual(self.loaded_version(), "0.4.17")

    def test_cancel_then_retry(self):
        def report(text):
            if "100%" in text:
                self.cancel.set()
        with mock.patch.object(self, "messages") as sink:
            sink.append.side_effect = report
            with self.assertRaisesRegex(ValueError, "已停止"):
                self.run_install()
        self.assertEqual(loader.active_package(self.root), self.root)
        self.cancel.clear()
        self.run_install()

    def test_concurrent_install_does_not_replace_running_files(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(len(list(executor.map(lambda _: self.run_install(), range(2)))), 2)
        self.assertEqual(self.loaded_version(), self.version)
        self.assertEqual([p.name for p in loader.release_store(self.root).iterdir()], [self.version])

    def test_github_tag_can_supply_official_digest_when_manifest_is_older(self):
        name = "Mori-Krita-" + self.version + "-Compatibility-Update.zip"
        url = updates.RELEASES_URL + "/download/v" + self.version + "/" + name
        gh = {"tag_name": "v" + self.version, "draft": False, "prerelease": False,
              "html_url": updates.RELEASES_URL + "/tag/v" + self.version,
              "assets": [{"name": name, "browser_download_url": url, "size": len(self.archive),
                          "digest": "sha256:" + hashlib.sha256(self.archive).hexdigest()}]}
        with mock.patch.object(self.manager, "_read", side_effect=[{}, gh]) as read:
            release = self.manager.installation_release(self.version, self.cancel)
        self.assertEqual(release.download_url, url)
        self.assertEqual(read.call_args_list[0].args[0], updates.OFFICIAL_MANIFEST)

    def test_no_digest_from_either_official_source_cannot_start_install(self):
        with mock.patch.object(self.manager, "_read", return_value={}):
            with self.assertRaisesRegex(ValueError, "校验信息"):
                self.manager.installation_release(self.version, self.cancel)
