"""Exercise the packaged native installer in isolated Mac user directories."""
import argparse
import hashlib
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
                continue
            assert path.is_file() and sha(path) == digest, relative
            count += 1
        for path, digest in protected.items():
            assert sha(path) == digest
        text = config.read_text()
        assert 'enable_mori_krita=true' in text and 'enable_ai_diffusion=true' in text
        if protected:
            assert 'KeepPreference=yes' in text and 'enable_unrelated=true' in text
        assert not (resource / 'pykrita/mori_krita/__pycache__/stale.pyc').exists()
        report = {'passed': True, 'case': name, 'pluginFilesVerified': count,
                  'officialKritaSignatureVerified': True, 'protectedFilesVerified': len(protected)}
        reports.append(report)
        print(json.dumps(report))
    (args.output / 'summary.json').write_text(json.dumps({'passed': True, 'tests': reports}, indent=2))


if __name__ == '__main__':
    main()
