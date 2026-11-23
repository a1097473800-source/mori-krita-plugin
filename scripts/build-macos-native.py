"""Compile the universal WebKit/Keychain bridge with Apple's system SDK."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'mori_krita/macos')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise RuntimeError('Compile this bridge on a macOS runner.')
    args.output.mkdir(parents=True, exist_ok=True)
    library = args.output / 'libMoriMac.dylib'
    common = ['xcrun', 'clang++', '-std=c++17', '-fobjc-arc', '-O2', '-mmacosx-version-min=12.0',
              '-framework', 'AppKit', '-framework', 'WebKit', '-framework', 'Security']
    subprocess.run(common + ['-arch', 'arm64', '-arch', 'x86_64', '-dynamiclib',
        '-Wl,-install_name,@rpath/libMoriMac.dylib', str(ROOT / 'macos/MoriMac.mm'), '-o', str(library)], check=True)
    subprocess.run(['codesign', '--force', '--sign', '-', str(library)], check=True)
    smoke = args.output / 'MoriMacSmoke'
    subprocess.run(common + [str(ROOT / 'macos/MoriMacSmoke.mm'), '-o', str(smoke)], check=True)
    report = {'abi': 1, 'minimumMacOS': '12.0', 'architectures': ['arm64', 'x86_64'],
              'sha256': hashlib.sha256(library.read_bytes()).hexdigest()}
    (args.output / 'host-release.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
