"""Install an official compatibility ZIP beside the running copy of the plugin."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile

from .native_loader import BUNDLED_ROOT, REQUIRED, active_package, package_version, ready, release_store
from .plugin_updates import RELEASES_URL, version_tuple

_MAX_EXPANDED = 256 * 1024 * 1024


class _PackageRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        hosts = ("release-assets.githubusercontent.com", "objects.githubusercontent.com",
                 "github-releases.githubusercontent.com")
        if (urllib.parse.urlsplit(req.full_url).hostname not in ("github.com", *hosts)
                or target.scheme != "https" or target.netloc not in hosts):
            raise ValueError("更新包下载地址无效。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _running(cancel, deadline):
    if cancel.is_set():
        raise ValueError("更新已停止。")
    if time.monotonic() >= deadline:
        raise TimeoutError("下载更新超时，请重试。")


def _download(opener, url, release, destination, progress, cancel, deadline):
    req = urllib.request.Request(url, headers={"User-Agent": "Mori-Krita-Updater", "Accept": "application/octet-stream"})
    digest, total, last_progress = hashlib.sha256(), 0, -1
    with opener.open(req, timeout=20) as response, destination.open("wb") as output:
        read = getattr(response, "read1", response.read)
        while True:
            _running(cancel, deadline)
            chunk = read(256 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > release.size:
                raise ValueError("下载的更新包大小不符。")
            output.write(chunk)
            digest.update(chunk)
            percent = 100 * total // release.size
            if percent != last_progress:
                progress("正在下载更新 " + str(percent) + "%")
                last_progress = percent
    if total != release.size or digest.hexdigest() != release.sha256:
        raise ValueError("更新包校验失败，请重试。")


def _unpack(archive, target, version, cancel, deadline):
    prefix = re.compile(r"Mori-Krita-" + re.escape(version) + r"-Compatibility-Update(?:-[0-9]{8}-[0-9]{6})?")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) > 2048 or sum(item.file_size for item in entries) > _MAX_EXPANDED:
            raise ValueError("更新包展开后过大。")
        seen, roots = set(), set()
        for item in entries:
            _running(cancel, deadline)
            parts = item.filename.rstrip("/").split("/")
            if not prefix.fullmatch(parts[0]):
                raise ValueError("更新包目录或版本不符。")
            roots.add(parts[0])
            if len(parts) < 3 or parts[1] != "mori_krita":
                continue
            names = parts[2:]
            if any(not name or name in (".", "..") or name.endswith((" ", ".")) or
                   re.search(r'[\\:<>"|?*\x00-\x1f]', name) or
                   re.fullmatch(r"CON|NUL|PRN|AUX|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0].rstrip(), re.I)
                   for name in names):
                raise ValueError("更新包包含无效文件名。")
            key = "/".join(names).casefold()
            mode = stat.S_IFMT(item.external_attr >> 16)
            if key in seen or mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("更新包包含重复或不支持的文件。")
            seen.add(key)
            out = target.joinpath(*names)
            if item.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with package.open(item) as source, out.open("xb") as output:
                    shutil.copyfileobj(source, output, length=256 * 1024)
        if len(roots) != 1 or not all((target / name).is_file() for name in REQUIRED):
            raise ValueError("更新包缺少插件组件。")
    if package_version(target) != version:
        raise ValueError("更新包与发布版本不一致。")
    for module in target.glob("*.py"):
        compile(module.read_bytes(), module.name, "exec")


def install_update(manager, version, progress, cancel, *, bundled=BUNDLED_ROOT, opener=None):
    progress("正在校验官方发布信息…")
    release = manager.installation_release(version, cancel)
    store = release_store(bundled)
    store.mkdir(exist_ok=True)
    destination = store / release.version
    if ready(destination, version, release.sha256):
        return
    if version_tuple(package_version(active_package(bundled))) >= version_tuple(version):
        raise ValueError("本机已安装此版本或更新的版本。")
    deadline = time.monotonic() + 300
    opener = opener or urllib.request.build_opener(_PackageRedirect())
    filename = "Mori-Krita-" + version + "-Compatibility-Update.zip"
    github = RELEASES_URL + "/download/v" + version + "/" + filename
    with tempfile.TemporaryDirectory(prefix=".install-", dir=store) as temporary:
        scratch = Path(temporary)
        archive = scratch / "package.zip"
        for url in dict.fromkeys((release.download_url, github)):
            _running(cancel, deadline)
            try:
                _download(opener, url, release, archive, progress, cancel, deadline)
                break
            except (OSError, ValueError):
                if url == github or cancel.is_set():
                    raise
                progress("正在从 GitHub 下载更新…")
        progress("校验通过，正在安装…")
        staged = scratch / "plugin"
        staged.mkdir()
        _unpack(archive, staged, version, cancel, deadline)
        (staged / ".installed.json").write_text(json.dumps({"version": version, "sha256": release.sha256}), encoding="utf-8")
        _running(cancel, deadline)
        try:
            os.rename(staged, destination)
        except OSError:
            if not ready(destination, version, release.sha256):
                raise
