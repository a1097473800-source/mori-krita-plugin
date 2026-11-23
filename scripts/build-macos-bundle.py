"""Package a universal native installer around the unmodified official Krita DMG."""
import argparse
import ast
import hashlib
import json
import plistlib
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
KRITA_HASH = '531e261da1fd8d5c468cfcd4eebd16e9edf6ab320e89406d2875f2e43c1148de'
AI_HASH = 'a29cadbbdc6d7af06c56ac59615f98c81d974412be5ca0a94fce84a2e4fcebd1'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def archive_folder(root, destination):
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--krita-dmg', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert sys.platform == 'darwin'
    assert sha(args.krita_dmg) == KRITA_HASH
    version = re.search(r'^VERSION = "([0-9.]+)"', (ROOT / 'mori_krita/version.py').read_text(), re.M).group(1)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output.parent / 'mac-package-stage'
    assert not stage.exists(), 'Use a fresh build directory.'
    plugin = stage / 'payload/plugins/mori_krita'
    for source in (ROOT / 'mori_krita').rglob('*'):
        if not source.is_file():
            continue
        relative = source.relative_to(ROOT / 'mori_krita')
        if '__pycache__' in relative.parts or source.suffix == '.pyc' or relative.parts[0] == 'webview-host':
            continue
        if relative.parts[0] == 'macos' and source.name not in ('libMoriMac.dylib', 'host-release.json'):
            continue
        if source.suffix == '.py':
            ast.parse(source.read_text(encoding='utf-8'))
        copy(source, plugin / relative)
    copy(ROOT / 'mori_krita.desktop', stage / 'payload/plugins/mori_krita.desktop')
    binary = plugin / 'macos/libMoriMac.dylib'
    assert binary.exists()
    native = json.loads((plugin / 'macos/host-release.json').read_text())
    assert native['sha256'] == sha(binary)
    subprocess.run(['lipo', str(binary), '-verify_arch', 'arm64', 'x86_64'], check=True)
    subprocess.run(['codesign', '--verify', '--strict', str(binary)], check=True)
    ai = output.parent / 'ai-diffusion.zip'
    if not ai.exists():
        urllib.request.urlretrieve('https://github.com/Acly/krita-ai-diffusion/releases/download/v1.49.0/krita_ai_diffusion-1.49.0.zip', ai)
    assert sha(ai) == AI_HASH
    with zipfile.ZipFile(ai) as archive:
        for entry in archive.infolist():
            relative = Path(entry.filename)
            assert not relative.is_absolute() and '..' not in relative.parts
            assert relative.parts[0] in ('ai_diffusion', 'ai_diffusion.desktop')
            if not entry.is_dir():
                target = stage / 'payload/plugins' / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(entry))
    for source in (stage / 'payload/plugins/ai_diffusion').glob('*.action'):
        copy(source, stage / 'payload/actions' / source.name)
    (stage / 'payload/actions').mkdir(exist_ok=True)
    copy(ROOT / 'THIRD-PARTY.md', stage / 'payload/THIRD-PARTY.md')
    for source in (ROOT / 'licenses').iterdir():
        if source.is_file():
            copy(source, stage / 'payload/licenses' / source.name)
    copy(stage / 'payload/plugins/ai_diffusion/LICENSE', stage / 'payload/licenses/AI-Diffusion-LICENSE.txt')
    files = []
    for source in sorted((stage / 'payload').rglob('*')):
        if source.is_file():
            name = source.relative_to(stage / 'payload').as_posix()
            files.append({'path': name, 'bytes': source.stat().st_size, 'sha256': sha(source)})
    (stage / 'payload/files.sha256').write_text(''.join(f['sha256'] + '\t' + f['path'] + '\n' for f in files), encoding='utf-8')
    plugin_zip = output / ('Mori-Krita-' + version + '-macOS-Plugin.zip')
    archive_folder(stage / 'payload/plugins', plugin_zip)
    contents = stage / 'disk/安装 Mori.app/Contents'
    resources = contents / 'Resources'
    resources.mkdir(parents=True)
    (contents / 'MacOS').mkdir()
    archive_folder(stage / 'payload', resources / 'plugins.zip')
    copy(args.krita_dmg, resources / 'krita.dmg')
    script = (ROOT / 'macos/install.sh').read_text(encoding='utf-8').replace('__MORI_VERSION__', version)
    script = script.replace('__PLUGINS_SHA256__', sha(resources / 'plugins.zip'))
    (resources / 'install.sh').write_text(script, encoding='utf-8', newline='\n')
    (contents / 'Info.plist').write_bytes(plistlib.dumps({
        'CFBundleExecutable': 'MoriInstaller', 'CFBundleIdentifier': 'chat.morinet.krita.installer',
        'CFBundleName': '安装 Mori', 'CFBundleDisplayName': '安装 Mori',
        'CFBundleVersion': version, 'CFBundleShortVersionString': version,
        'CFBundlePackageType': 'APPL', 'LSMinimumSystemVersion': '12.0',
        'NSHighResolutionCapable': True, 'NSPrincipalClass': 'NSApplication',
    }))
    generated = stage / 'Installer.mm'
    generated.write_text((ROOT / 'macos/Installer.mm').read_text(encoding='utf-8').replace('__MORI_VERSION__', version), encoding='utf-8')
    subprocess.run(['xcrun', 'clang++', '-std=c++17', '-fobjc-arc', '-O2', '-mmacosx-version-min=12.0',
        '-arch', 'arm64', '-arch', 'x86_64', '-framework', 'AppKit', str(generated),
        '-o', str(contents / 'MacOS/MoriInstaller')], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(contents.parent)], check=True)
    notice = f'''Mori for Krita {version} · macOS 测试版

适用于 macOS 12 或更新版本，Apple 芯片与 Intel 共用此安装包。
包含官方 Krita 5.3.4.1、Mori {version}、AI Diffusion 1.49.0。

安装：
1. 保存作品并完全退出 Krita。
2. 双击「安装 Mori.app」，点击安装。
3. 完成后点击「打开 Krita」，打开画布后即可使用 Mori。
安装到当前用户的 Applications/MoriKrita，无需管理员权限。
已有 Mori 与 AI Diffusion 会先备份到 Krita 资源目录的 bundle-backups。
保留画笔、作品、账号和其他插件。AI Diffusion 的模型与后端需在其设置中配置。

已有 Krita：可导入 Mori-Krita-{version}-macOS-Plugin.zip，再在 Python 插件管理器启用 Mori 并重开。
Mac 工作台使用系统 WebKit；登录会话存入 Mac 钥匙串，原图无损参考为默认。
Vision Tools 2.2.0 官方没有 Mac 安装包，因此未包含。

Mori 安装器与桥接组件使用本地签名，尚未经过 Apple Developer ID 签名与公证。
首次打开如提示开发者无法验证，确认文件来自 Mori 的发布页后，
在「系统设置 → 隐私与安全性」找到本安装器并点「仍要打开」。
macOS 12 对应「系统偏好设置 → 安全性与隐私 → 通用」。
Apple 操作说明：https://support.apple.com/zh-cn/102445
Krita 使用原封不动的官方签名磁盘映像，安装时验证其 SHA256 和应用签名。
发布前已在云端 Mac 执行编译与自动测试；学员电脑上的数位板、显示缩放仍需实机验收。

上游：
https://download.kde.org/stable/krita/5.3.4.1/
https://github.com/Acly/krita-ai-diffusion/releases/tag/v1.49.0
https://github.com/a1097473800-source/mori-krita-plugin
'''
    (stage / 'disk/安装说明.txt').write_text(notice, encoding='utf-8')
    (output / '安装说明.txt').write_text(notice, encoding='utf-8')
    copy(plugin_zip, stage / 'disk' / plugin_zip.name)
    manifest = {'version': version, 'platform': 'darwin', 'minimumMacOS': '12.0',
        'architectures': ['arm64', 'x86_64'], 'kritaVersion': '5.3.4.1', 'kritaSha256': KRITA_HASH,
        'aiDiffusionVersion': '1.49.0', 'aiDiffusionSha256': AI_HASH,
        'visionToolsIncluded': False, 'developerIdSigned': False, 'appleNotarized': False,
        'pluginFiles': files}
    (output / 'bundle-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    dmg = output / ('Mori-Krita-' + version + '-macOS-Setup.dmg')
    subprocess.run(['hdiutil', 'create', '-quiet', '-volname', 'Mori for Krita ' + version,
        '-srcfolder', str(stage / 'disk'), '-format', 'UDZO', str(dmg)], check=True)
    (output / 'SHA256SUMS.txt').write_text(''.join(sha(p) + '  ' + p.name + '\n' for p in (dmg, plugin_zip)), encoding='utf-8')
    print(json.dumps({'installer': str(dmg), 'bytes': dmg.stat().st_size, 'sha256': sha(dmg),
        'plugin': plugin_zip.name, 'packagedFiles': len(files)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
