"""Run saved offline suites in the actual official Krita Mac Python runtime."""
import argparse
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--krita', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--suite', default='run_update_qa')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    bootstrap = Path.home() / 'Library/Application Support/kritarunner'
    bootstrap.mkdir(parents=True, exist_ok=True)
    name = 'mori_mac_build_qa'
    script = bootstrap / (name + '.py')
    script.write_text('import sys\nsys.path.insert(0, ' + repr(str(ROOT / 'tests')) + ')\nfrom ' + args.suite + ' import main\n', encoding='utf-8')
    try:
        env = dict(os.environ, PYTHONPATH=str(bootstrap))
        result = subprocess.run([str(args.krita / 'Contents/MacOS/kritarunner'), '--script', name,
            '--function', 'main', str(args.output.resolve())], env=env, capture_output=True, timeout=180)
        (args.output / 'stdout.log').write_bytes(result.stdout)
        (args.output / 'stderr.log').write_bytes(result.stderr)
        report = args.output / 'summary.json'
        if not report.exists():
            print(result.stderr.decode('utf-8', 'replace')[-8000:])
            raise RuntimeError('The official Krita runtime did not produce a QA report.')
        data = json.loads(report.read_text(encoding='utf-8'))
        print(json.dumps(data))
        if result.returncode or not data.get('passed'):
            raise RuntimeError('Native Mac QA failed; see the saved diagnostics.')
    finally:
        script.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
