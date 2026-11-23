"""Exercise the packaged native installer in isolated Mac user directories."""
import argparse
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    executable = args.stage / 'disk/安装 Mori.app/Contents/MacOS/MoriInstaller'
    resources = executable.parents[1] / 'Resources'
    subprocess.run([str(executable), '--render-preview', str((args.output / 'installer-preview.png').resolve())], check=True, timeout=20)
    with zipfile.ZipFile(resources / 'plugins.zip') as archive:
        manifest = [line.split('\t', 1) for line in archive.read('files.sha256').decode().splitlines()]
    reports = []
    for name in ('clean', 'upgrade-中文 测试'):
        target = (args.output / name).resolve()
        assert not (target / 'profile/Applications').exists()
        profile = target / 'profile'
        resource = profile / 'Library/Application Support/krita'
        config = profile / 'Library/Preferences/kritarc'
        protected = {}
        if name.startswith('upgrade'):
            fixtures = {
                resource / 'brushes/student.gbr': b'preserve student brush',
                resource / 'account-and-canvas.json': b'{"fixture":"keep"}',
                resource / 'pykrita/another_plugin/personal.py': b'# keep unrelated plugin',
                resource / 'pykrita/mori_krita/version.py': b'VERSION = "0.4.11"',
                resource / 'pykrita/mori_krita/__pycache__/stale.pyc': b'stale fixture',
                config: b'[General]\nKeepPreference=yes\n\n[python]\nenable_mori_krita=false\nenable_unrelated=true\n',
            }
            for path, data in fixtures.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                if path != config and '/mori_krita/' not in path.as_posix():
                    protected[path] = sha(path)
        result = subprocess.run([str(executable), '--self-test', str(target)], capture_output=True, timeout=180)
        (args.output / (name + '.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            print((result.stdout + result.stderr).decode('utf-8', 'replace')[-6000:])
        assert result.returncode == 0
        installed = profile / 'Applications/MoriKrita/Krita-5.3.4.1-Mori-0.4.12.app'
        assert (installed / 'Contents/MacOS/krita').is_file()
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(installed)], check=True)
        count = 0
        for digest, relative in manifest:
            if relative.startswith('plugins/'):
                path = resource / 'pykrita' / relative[8:]
            elif relative.startswith('actions/'):
                path = resource / relative
            else:
                path = profile / 'Applications/MoriKrita/Notices-0.4.12' / relative
            assert path.is_file() and sha(path) == digest, relative
            count += 1
        for path, digest in protected.items():
            assert sha(path) == digest
        text = config.read_text()
        assert 'enable_mori_krita=true' in text and 'enable_ai_diffusion=true' in text
        if protected:
            assert 'KeepPreference=yes' in text and 'enable_unrelated=true' in text
        assert not (resource / 'pykrita/mori_krita/__pycache__/stale.pyc').exists()
        report = {'passed': True, 'case': name, 'payloadFilesVerified': count,
                  'officialKritaSignatureVerified': True, 'protectedFilesVerified': len(protected)}
        reports.append(report)
        print(json.dumps(report))
    # Use the unmodified import logic shipped in the verified official Krita app.
    source = next(installed.rglob('plugin_importer.py'))
    spec = importlib.util.spec_from_file_location('mori_official_importer_qa', source)
    importer_module = importlib.util.module_from_spec(spec)
    original_i18n = getattr(builtins, 'i18n', None)
    builtins.i18n = lambda text: text
    try:
        spec.loader.exec_module(importer_module)
        destination = args.output / 'plugin-import-中文'
        destination.mkdir()
        package = next((args.stage / 'disk').glob('*-macOS-Plugin.zip'))
        imported = importer_module.PluginImporter(str(package), str(destination), lambda _: True).import_all()
        assert {plugin['name'] for plugin in imported} == {'mori_krita', 'ai_diffusion'}
        assert (destination / 'pykrita/mori_krita/macos/libMoriMac.dylib').is_file()
        assert (destination / 'actions/ai_diffusion.action').is_file()
        for digest, relative in manifest:
            if relative.startswith('plugins/'):
                assert sha(destination / 'pykrita' / relative[8:]) == digest, relative
    finally:
        if original_i18n is None:
            del builtins.i18n
        else:
            builtins.i18n = original_i18n
    (args.output / 'summary.json').write_text(json.dumps({'passed': True, 'tests': reports,
        'officialPluginImporter': {'passed': True, 'plugins': sorted(plugin['name'] for plugin in imported),
            'nativeBridgePreserved': True, 'actionFileImported': True}}, indent=2))


if __name__ == '__main__':
    main()
