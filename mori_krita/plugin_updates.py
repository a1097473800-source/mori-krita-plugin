"""Public native release checks, independent of login and the web workbench."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


REPOSITORY = "a1097473800-source/mori-krita-plugin"
RELEASES_URL = "https://github.com/" + REPOSITORY + "/releases"
LATEST_API = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
OFFICIAL_MANIFEST = "https://morinet.chat/krita-plugin/manifest.json"
MAX_METADATA_BYTES = 64 * 1024
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def version_tuple(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise ValueError("更新版本信息无效。")
    return tuple(int(part) for part in value.split("."))


def release_url(value, *, package=False):
    """Only open release pages or ZIPs belonging to this plugin repository."""
    if not isinstance(value, str) or re.search(r"[\x00-\x20\\]", value):
        raise ValueError("更新下载地址无效。")
    parsed = urllib.parse.urlsplit(value)
    prefix = "/" + REPOSITORY + "/releases"
    if (package and parsed.scheme == "https" and parsed.netloc == "morinet.chat"
            and not parsed.query and not parsed.fragment
            and re.fullmatch(r"/krita-plugin/packages/Mori-Krita-[0-9]+\.[0-9]+\.[0-9]+-Compatibility-Update\.zip", parsed.path)):
        return value
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query
            or parsed.fragment or not parsed.path.startswith(prefix + "/")
            or any(part in (".", "..") for part in parsed.path.split("/"))
            or "%" in parsed.path):
        raise ValueError("更新下载地址无效。")
    if package:
        if not parsed.path.startswith(prefix + "/download/") or not parsed.path.endswith(".zip"):
            raise ValueError("更新下载地址无效。")
    elif not (parsed.path == prefix + "/latest" or parsed.path.startswith(prefix + "/tag/")):
        raise ValueError("更新发布页无效。")
    return value


@dataclass(frozen=True)
class PluginRelease:
    version: str
    page_url: str
    download_url: str = ""
    notes: str = ""
    sha256: str = ""
    size: int = 0


@dataclass(frozen=True)
class PluginUpdateResult:
    release: PluginRelease | None
    message: str
    available: bool = False
    checked: bool = False


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("更新地址发生跳转，请稍后重试。")


class PluginUpdateManager:
    def __init__(self, base_url, current_version):
        self.current_version = current_version
        version_tuple(current_version)
        parsed = urllib.parse.urlsplit(base_url)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ("", "/") or re.search(r"[\x00-\x20\\]", base_url)
                or (parsed.scheme != "https" and not (parsed.scheme == "http"
                    and parsed.hostname in ("127.0.0.1", "localhost", "::1")))):
            raise ValueError("更新服务器地址无效。")
        self.manifest_url = base_url.rstrip("/") + "/krita-plugin/manifest.json"
        self._opener = urllib.request.build_opener(_NoRedirect(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def _read(self, url, cancel, deadline):
        if cancel is not None and cancel.is_set():
            raise ValueError("更新检查已停止。")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError()
        request = urllib.request.Request(url, headers={"User-Agent": "Mori-Krita/" + self.current_version,
            "Accept": "application/json", "Cache-Control": "no-cache"})
        with self._opener.open(request, timeout=min(6, remaining)) as response:
            chunks, count = [], 0
            read = getattr(response, "read1", response.read)
            while True:
                if (cancel is not None and cancel.is_set()) or time.monotonic() > deadline:
                    raise TimeoutError()
                chunk = read(min(16384, MAX_METADATA_BYTES + 1 - count))
                if not chunk:
                    break
                count += len(chunk)
                if count > MAX_METADATA_BYTES:
                    raise ValueError("更新信息过大。")
                chunks.append(chunk)
        value = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("更新信息无效。")
        return value

    @staticmethod
    def _manifest(value):
        if type(value.get("schemaVersion")) is not int or value["schemaVersion"] != 1:
            raise ValueError("更新信息无效。")
        version = value.get("version")
        version_tuple(version)
        page = release_url(value.get("releaseUrl"))
        package = value.get("package") or {}
        download = release_url(package.get("url"), package=True) if package else ""
        notes = value.get("notes", "")
        if not isinstance(notes, str):
            raise ValueError("更新说明无效。")
        return PluginRelease(version, page, download, notes[:6000], package.get("sha256", ""), package.get("size", 0))

    @staticmethod
    def _github(value):
        if value.get("draft") is not False or value.get("prerelease") is not False:
            raise ValueError("尚无正式发布版本。")
        tag = value.get("tag_name", "")
        version = tag[1:] if isinstance(tag, str) and tag.startswith("v") else tag
        version_tuple(version)
        page = release_url(value.get("html_url"))
        download = ""
        digest, size = "", 0
        for asset in value.get("assets", []):
            if isinstance(asset, dict) and asset.get("name") == "Mori-Krita-" + version + "-Compatibility-Update.zip":
                download = release_url(asset.get("browser_download_url"), package=True)
                digest = (asset.get("digest") or "").removeprefix("sha256:")
                size = asset.get("size", 0)
                break
        notes = value.get("body") or ""
        if not isinstance(notes, str):
            raise ValueError("更新说明无效。")
        return PluginRelease(version, page, download, notes[:6000], digest, size)

    def installation_release(self, version, cancel):
        """Executable packages always use metadata from the official channels."""
        if version_tuple(version) <= version_tuple(self.current_version):
            raise ValueError("只能安装比当前版本更新的插件。")
        tag_api = LATEST_API.rsplit("/", 1)[0] + "/tags/v" + version
        deadline = time.monotonic() + 16
        filename = "Mori-Krita-" + version + "-Compatibility-Update.zip"
        allowed = {"https://morinet.chat/krita-plugin/packages/" + filename,
                   RELEASES_URL + "/download/v" + version + "/" + filename}
        for url, parse in ((OFFICIAL_MANIFEST, self._manifest), (tag_api, self._github)):
            try:
                release = parse(self._read(url, cancel, deadline))
                if (release.version == version and release.download_url in allowed
                        and isinstance(release.sha256, str) and re.fullmatch(r"[0-9a-f]{64}", release.sha256)
                        and type(release.size) is int and 0 < release.size <= 128 * 1024 * 1024):
                    return release
            except (ValueError, OSError, TypeError, AttributeError):
                pass
            if cancel.is_set():
                break
        raise ValueError("未取得此版本的官方更新包校验信息，请稍后重试。")

    def check(self, cancel=None):
        deadline = time.monotonic() + 12
        for url, parse in ((self.manifest_url, self._manifest), (LATEST_API, self._github)):
            try:
                latest = parse(self._read(url, cancel, deadline))
                available = version_tuple(latest.version) > version_tuple(self.current_version)
                if available:
                    message = "发现插件新版本 " + latest.version
                elif latest.version == self.current_version:
                    message = "插件已是最新版本。"
                else:
                    message = "当前版本较新；公开发布版本为 " + latest.version + "。"
                return PluginUpdateResult(latest, message, available, True)
            except Exception:
                if cancel is not None and cancel.is_set():
                    return PluginUpdateResult(None, "更新检查已停止。")
        return PluginUpdateResult(None, "暂时无法检查插件更新，请检查网络后重试；也可打开发布页。")
