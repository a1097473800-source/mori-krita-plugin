"""Cross-platform checks for Mac platform selection, sessions and host cleanup."""
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
package = ModuleType('mori_mac_unit_fixture')
package.__path__ = [str(ROOT / 'mori_krita')]
sys.modules[package.__name__] = package
store = importlib.import_module(package.__name__ + '.mac_session_store')
native = importlib.import_module(package.__name__ + '.macos_native')
updates = importlib.import_module(package.__name__ + '.plugin_updates')


class MacIntegrationTests(unittest.TestCase):
    def test_mac_update_channel_never_offers_windows_packages(self):
        manager = updates.PluginUpdateManager('https://morinet.chat', '0.4.12', platform='darwin')
        self.assertTrue(manager.manifest_url.endswith('/macos-manifest.json'))
        value = {'schemaVersion': 1, 'version': '0.4.13', 'platform': 'darwin',
            'releaseUrl': updates.RELEASES_URL + '/tag/v0.4.13',
            'package': {'url': updates.RELEASES_URL + '/download/v0.4.13/Mori-Krita-0.4.13-macOS-Plugin.zip'}}
        self.assertTrue(manager._manifest(value).download_url.endswith('-macOS-Plugin.zip'))
        value['package']['url'] = value['package']['url'].replace('macOS-Plugin', 'Compatibility-Update')
        with self.assertRaises(ValueError):
            manager._manifest(value)
        with self.assertRaises(ValueError):
            manager._github({'draft': False, 'prerelease': False, 'tag_name': 'v0.4.13',
                             'html_url': value['releaseUrl'], 'assets': []})

    def test_keychain_store_roundtrip_rejects_cross_origin_and_clears_logout(self):
        from test_session_store import cookie, ORIGIN
        vault = {}
        def write(origin, payload):
            vault[origin] = payload
            return True
        with mock.patch.object(store, 'keychain_write', side_effect=write), \
             mock.patch.object(store, 'keychain_read', side_effect=lambda origin: vault.get(origin)), \
             mock.patch.object(store, 'keychain_delete', side_effect=lambda origin: vault.pop(origin, None)):
            session = store.MacSessionStore()
            self.assertTrue(session.save(ORIGIN, [cookie()]))
            self.assertEqual(session.load(ORIGIN)[0].value, cookie().value)
            self.assertFalse(session.save('http://127.0.0.1', [cookie()]))
            self.assertEqual(session.load('https://another.invalid'), [])
            vault['https://another.invalid'] = vault[ORIGIN]
            self.assertEqual(session.load('https://another.invalid'), [])
            self.assertNotIn('https://another.invalid', vault)
            session.clear(ORIGIN)
            self.assertEqual(vault, {})

    def test_mac_update_fallback_uses_its_own_manifest(self):
        manager = updates.PluginUpdateManager('https://morinet.chat', '0.4.12', platform='darwin')
        manifest = {'schemaVersion': 1, 'version': '0.4.13', 'platform': 'darwin',
            'releaseUrl': updates.RELEASES_URL + '/tag/v0.4.13',
            'package': {'url': updates.RELEASES_URL + '/download/v0.4.13/Mori-Krita-0.4.13-macOS-Plugin.zip'}}
        with mock.patch.object(manager, '_read', side_effect=[OSError('offline'), manifest]) as read:
            result = manager.check()
        self.assertTrue(result.checked and result.available)
        self.assertEqual(read.call_args.args[0], updates.MAC_MANIFEST_FALLBACK)

    def test_keychain_unavailable_does_not_block_sign_in(self):
        from test_session_store import cookie, ORIGIN
        with mock.patch.object(store, 'keychain_read', side_effect=OSError('locked')), \
             mock.patch.object(store, 'keychain_write', side_effect=OSError('locked')):
            session = store.MacSessionStore()
            self.assertEqual(session.load(ORIGIN), [])
            self.assertFalse(session.save(ORIGIN, [cookie()]))

    def test_mac_host_detaches_before_releasing_callback_and_ignores_late_events(self):
        library = mock.Mock()
        library.mori_web_create.return_value = 123
        messages = []
        with mock.patch.object(native, 'native_library', return_value=library):
            host = native.MacWebHost(456, 'http://127.0.0.1:1234/connect/test', lambda *args: messages.append(args))
            callback = host.callback
            callback(b'ready', b'connected')
            host.close()
            host.close()
            callback(b'error', b'late')
            self.assertEqual(messages, [('ready', 'connected')])
            library.mori_web_close.assert_called_once_with(123)
