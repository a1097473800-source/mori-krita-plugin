"""Build a Windows x64 offline bundle from clean, verified distributions."""
import argparse
import ast
from datetime import date
import re
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import urllib.request
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
MAGIC = b'MORI-KRITA-SETUP1'
AI_VERSION = '1.49.0'
AI_SHA256 = 'a29cadbbdc6d7af06c56ac59615f98c81d974412be5ca0a94fce84a2e4fcebd1'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def extract_plugin(archive, destination, allowed):
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            relative = PurePosixPath(entry.filename)
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                    or relative.parts[0] not in allowed):
                raise ValueError('Unexpected plugin archive entry: ' + entry.filename)
            if entry.is_dir():
                continue
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(entry))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ai', type=Path, required=True)
    parser.add_argument('--vision', type=Path, required=True)
    parser.add_argument('--build-date', default=date.today().strftime('%Y%m%d'))
    args = parser.parse_args()
    stage, output = args.stage.resolve(), args.output.resolve()
    version = re.search(r'^VERSION = \"([0-9.]+)\"$', (SOURCE / 'mori_krita/version.py').read_text(encoding='utf-8'), re.M).group(1)
    if not re.fullmatch(r'[0-9]{8}', args.build_date):
        raise ValueError('Build date must use YYYYMMDD.')
    if sha(args.ai) != AI_SHA256:
        raise ValueError('AI Diffusion checksum mismatch.')
    if not (stage / 'Krita/bin/krita.exe').is_file():
        raise ValueError('First extract the official signed Krita 5.3.4.1 installer to stage/Krita.')
    expected_vision = '326f736cb45c6503ed5e0d6ebd117be730c284ad5ad5bbb8dbbed1bfc32e6837'
    if sha(args.vision) != expected_vision:
        raise ValueError('Vision Tools checksum mismatch.')
    output.mkdir(parents=True, exist_ok=True)
    plugins = stage / 'plugins'
    print('Preparing clean plugins', flush=True)
    for item in (SOURCE / 'mori_krita').iterdir():
        if item.is_file() and item.suffix in ('.py', '.html'):
            if item.suffix == '.py':
                ast.parse(item.read_text(encoding='utf-8'))
            copy(item, plugins / 'mori_krita' / item.name)
    for folder in ('web', 'webview-host', 'icons'):
        for item in (SOURCE / 'mori_krita' / folder).rglob('*'):
            if item.is_file() and '__pycache__' not in item.parts and item.suffix != '.pyc':
                copy(item, plugins / 'mori_krita' / folder / item.relative_to(SOURCE / 'mori_krita' / folder))
    copy(SOURCE / 'mori_krita.desktop', plugins / 'mori_krita.desktop')
    # Window layouts are Krita resources rather than Python plugin files.  Keep
    # the preset alongside the bundled Krita resources so it appears in the
    # user's Window Layouts chooser after installation.
    for item in (SOURCE / 'windowlayouts').glob('*.kwl'):
        copy(item, stage / 'Krita/share/krita/windowlayouts' / item.name)
    extract_plugin(args.ai, plugins, {'ai_diffusion', 'ai_diffusion.desktop'})
    actual_ai = re.search(r'^__version__\s*=\s*"([0-9.]+)"',
        (plugins / 'ai_diffusion/__init__.py').read_text(encoding='utf-8'), re.M)
    if not actual_ai or actual_ai.group(1) != AI_VERSION:
        raise ValueError('AI Diffusion version does not match the verified release.')
    extract_plugin(args.vision, plugins, {'vision_tools', 'vision_tools.desktop'})
    for name in ('ai_diffusion', 'vision_tools'):
        for action in (plugins / name).glob('*.action'):
            copy(action, stage / 'actions' / action.name)
    for item in (SOURCE / 'prerequisites').iterdir():
        if item.is_file():
            copy(item, stage / 'prerequisites' / item.name)
    copy(SOURCE / 'installer-support.ps1', stage / 'installer-support.ps1')
    install_source = (SOURCE / 'bundle/install-bundle.ps1').read_text(encoding='utf-8-sig')
    (stage / 'install-bundle.ps1').write_text(install_source, encoding='utf-8-sig')
    for item in (SOURCE / 'licenses').iterdir():
        if item.is_file():
            copy(item, stage / 'licenses/Mori' / item.name)
    copy(SOURCE / 'THIRD-PARTY.md', stage / 'licenses/Mori/THIRD-PARTY.md')
    copy(plugins / 'ai_diffusion/LICENSE', stage / 'licenses/AI-Diffusion-LICENSE.txt')
    vision_license = stage / 'licenses/Vision-Tools-LICENSE.txt'
    if not vision_license.exists():
        request = urllib.request.Request(
            'https://raw.githubusercontent.com/Acly/krita-vision-tools/v2.2.0/LICENSE',
            headers={'User-Agent': 'Mori-Krita-Bundle/' + version})
        vision_license.write_bytes(urllib.request.urlopen(request, timeout=30).read())
    notice = '''Mori for Krita Windows x64 集成安装包

组件：Krita 5.3.4.1、Mori {VERSION}、AI Diffusion {AI_VERSION}、Vision Tools 2.2.0。
包含 Microsoft WebView2 x64 离线运行组件。

双击安装程序，关闭正在运行的 Krita 后点击“安装”。完成后从桌面“Krita”打开。
三个插件会自动启用。原有画笔、作品、账号与其他插件不会打包给其他电脑。
安装包包含“Krita/share/krita/windowlayouts/Mori 工作台.kwl”布局预设；在 Krita 的“窗口 → 工作区/窗口布局”中选择“Mori 工作台”即可恢复 Mori 与 AI Diffusion 的并列停靠布局。
包含原生选区助手；也可在工作台选择“Krita 选区”后点击“发送”。
保留云端画布同步与版本冲突保护。
实时滑块为 0–100%，每步 5%，采用紫色轨道、白色圆点与短暂流光。

安装软件与插件可离线完成。Mori 生成服务需要联网并登录自己的账号。
AI Diffusion 的本地生成模型与 ComfyUI 服务需要在其设置中另行配置；安装包不含扩散模型。
Vision Tools 自带的分割与修补模型已随其官方插件一起包含。

上游与对应源码：
Krita：https://download.kde.org/stable/krita/5.3.4.1/
Krita source：https://invent.kde.org/graphics/krita/-/tree/v5.3.4.1
AI Diffusion：https://github.com/Acly/krita-ai-diffusion/releases/tag/v{AI_VERSION}
Vision Tools：https://github.com/Acly/krita-vision-tools/releases/tag/v2.2.0
Vision Tools 2.2.0 面向 Krita 5.3；3.0.0 不用于此集成包。
许可证与第三方声明保留在软件内的 bundle-licenses 及 Krita 自带许可目录中。
'''
    notice = notice.replace('{VERSION}', version).replace('{AI_VERSION}', AI_VERSION)
    notice += '''
更新方式：
点击 Mori 面板右上角齿轮“设置”，打开独立的 Mori 设置窗口。
左侧分为“画布与图层”“实时与选区”“更新与版本”，常用偏好自动保存。
在“更新与版本”查看当前版本、最新版本、更新说明和下载入口。
启动后自动在后台检查，发现新版本时在齿轮上持续显示小圆点，也可手动重新检查。
工作台网页通过官网校验后下载；网页已打开时，保存作品并重开 Krita 后生效。
Mori 插件：在更新页下载新版 Compatibility-Update.zip，退出 Krita 后完整解压，双击“更新已安装插件.cmd”，无需重装整个 Krita。
https://github.com/a1097473800-source/mori-krita-plugin/releases
官网更新与下载：https://morinet.chat/krita-plugin/manifest.json

实时画布：
“开始”开启连续实时交互；“提交”按当前参数只生成一张，完成即停止。
生成期间禁用重复提交；暂停只停止后续生成，已提交的本次任务仍会完成。
'''
    (stage / 'licenses/说明.txt').write_text(notice, encoding='utf-8-sig')
    build = version + '-' + args.build_date + '-' + sha(SOURCE / 'mori_krita/__init__.py')[:8]
    manifest = {
        'product': 'MoriKrita', 'build': build,
        'versions': {'krita': '5.3.4.1', 'mori': version, 'aiDiffusion': AI_VERSION, 'visionTools': '2.2.0'},
        'archives': {'aiDiffusion': sha(args.ai), 'visionTools': sha(args.vision)},
        'selectionOverlayRemoved': False,
        'selectionAssistantEnabled': True,
        'updates': {'workbench': 'verified-online-manifest', 'nativePlugin': 'startup-check-with-manual-install',
                    'pluginManifest': 'https://morinet.chat/krita-plugin/manifest.json',
                    'releasePage': 'https://github.com/a1097473800-source/mori-krita-plugin/releases'},
        'windowLayoutPreset': 'Mori 工作台.kwl',
        'files': [],
    }
    allowed_krita = {'bin', 'lib', 'python', 'share', 'shellex'}
    files = []
    for path in sorted(stage.rglob('*')):
        if not path.is_file():
            continue
        relative = path.relative_to(stage)
        if relative.parts[0] == 'Krita' and (len(relative.parts) < 2 or relative.parts[1] not in allowed_krita):
            continue
        if relative.parts[0] not in {'Krita', 'plugins', 'actions', 'prerequisites', 'licenses',
                                     'installer-support.ps1', 'install-bundle.ps1'}:
            continue
        if path.suffix in {'.pyc', '.log', '.db', '.sqlite'} or '__pycache__' in relative.parts:
            raise ValueError('Non-distribution data found: ' + str(relative))
        files.append(path)
        manifest['files'].append({'path': relative.as_posix(), 'sha256': sha(path), 'bytes': path.stat().st_size})
    manifest_path = stage / 'bundle-manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Compressing %d files (%d bytes)' % (len(files), sum(p.stat().st_size for p in files)), flush=True)
    payload = stage.parent / 'mori-bundle-payload.zip'
    with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in files + [manifest_path]:
            package.write(path, path.relative_to(stage).as_posix())
    digest = sha(payload)
    source_cs = (SOURCE / 'bundle/Setup.cs').read_text(encoding='utf-8').replace('__PAYLOAD_SHA256__', digest)
    source_cs = source_cs.replace('__MORI_VERSION__', version).replace('__AI_VERSION__', AI_VERSION)
    build_cs = stage.parent / 'MoriKritaSetup.generated.cs'
    build_cs.write_text(source_cs, encoding='utf-8-sig')
    stub = stage.parent / 'MoriKritaSetup.stub.exe'
    compiler = Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')
    subprocess.run([str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
                    '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll',
                    '/r:System.IO.Compression.dll', '/r:System.IO.Compression.FileSystem.dll',
                    '/out:' + str(stub), str(build_cs)], check=True)
    setup = output / ('Mori-Krita-' + version + '-Windows-x64-Setup.exe')
    with setup.open('wb') as target:
        for part in (stub, payload):
            with part.open('rb') as stream:
                shutil.copyfileobj(stream, target)
        target.write(struct.pack('<q', payload.stat().st_size))
        target.write(MAGIC)
    (output / '使用说明.txt').write_text(notice, encoding='utf-8-sig')
    (output / '更新方式.txt').write_text(notice[notice.index('更新方式：'):], encoding='utf-8-sig')
    report = {key: value for key, value in manifest.items() if key != 'files'}
    report.update(setup=setup.name, sha256=sha(setup), bytes=setup.stat().st_size,
                  payloadSha256=digest, files=len(files),
                  bundledUncompressedBytes=sum(p.stat().st_size for p in files))
    (output / '安装包清单.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'SHA256.txt').write_text(report['sha256'] + '  ' + setup.name + '\n', encoding='utf-8-sig')
    with zipfile.ZipFile(output / '安装器维护源码.zip', 'w', zipfile.ZIP_DEFLATED) as package:
        maintenance = [SOURCE / 'bundle/Setup.cs', SOURCE / 'bundle/install-bundle.ps1', SOURCE / 'installer-support.ps1', Path(__file__)]
        maintenance.extend(sorted((SOURCE / 'prerequisites').glob('*.json')))
        maintenance.extend(sorted((SOURCE / 'prerequisites').glob('*.html')))
        maintenance.extend(sorted((SOURCE / 'prerequisites').glob('*.txt')))
        maintenance.extend(sorted((SOURCE / 'windowlayouts').glob('*.kwl')))
        for path in maintenance:
            package.write(path, path.relative_to(SOURCE).as_posix())
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
