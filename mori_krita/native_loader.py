"""Select a completely installed package before importing any Qt code."""
import json
from pathlib import Path
import re

BUNDLED_ROOT = Path(__file__).resolve().parent
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
REQUIRED = ("__init__.py", "startup.py", "native_loader.py", "native_install.py",
            "version.py", "docker.py", "gateway.py", "plugin_updates.py",
            "icons/cpu.svg", "icons/sparkles.svg", "web/index.html", "web/ui-release.json",
            "webview-host/MoriWebHost.exe", "webview-host/host-release.json")


def release_store(bundled):
    return bundled.parent / ".mori-native-updates"


def package_version(root):
    source = (root / "version.py").read_text(encoding="utf-8-sig")
    match = re.search(r'(?m)^VERSION\s*=\s*"(' + _VERSION.pattern + r')"\s*$', source)
    if not match:
        raise ValueError("插件版本文件无效。")
    return match[1]


def ready(root, version, digest=None):
    try:
        record = json.loads((root / ".installed.json").read_text(encoding="utf-8"))
        return (record["version"] == version and isinstance(record["sha256"], str)
                and bool(re.fullmatch(r"[0-9a-f]{64}", record["sha256"]))
                and (digest is None or record["sha256"] == digest)
                and package_version(root) == version
                and all((root / name).is_file() for name in REQUIRED))
    except (KeyError, TypeError, ValueError, OSError):
        return False


def active_package(bundled=BUNDLED_ROOT):
    base_version = tuple(map(int, package_version(bundled).split(".")))
    try:
        releases = list(release_store(bundled).iterdir())
    except OSError:
        return bundled
    candidates = sorted((tuple(map(int, path.name.split("."))), path) for path in releases
                        if len(path.name) <= 32 and _VERSION.fullmatch(path.name))
    for version, root in reversed(candidates):
        if version <= base_version:
            break
        if ready(root, root.name):
            return root
    return bundled
