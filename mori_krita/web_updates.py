"""Verified, account-free updates for the Krita-specific web workbench.

Only public files from the selected Mori origin are downloaded. A release is
immutable once selected by a gateway; opening a new workbench selects again.
Nothing in this module reads sessions, canvases, or Krita documents.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import stat
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile


MAX_BUNDLE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 96 * 1024 * 1024
MAX_FILES = 256
MAX_MANIFEST_BYTES = 64 * 1024
ALLOWED_SUFFIXES = frozenset((".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg",
                              ".webp", ".woff", ".woff2", ".json", ".ico", ".map", ".txt"))
_UI_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_SHA256 = re.compile(r"[a-fA-F0-9]{64}\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_locks = {}
_locks_guard = threading.Lock()


class WebUpdateError(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_arguments, **_keywords):
        raise WebUpdateError("网页更新地址发生跳转，已保留当前版本。")


@dataclass(frozen=True)
class WebRelease:
    root: Path
    ui_version: str
    source: str


@dataclass(frozen=True)
class WebUpdateResult:
    release: WebRelease
    message: str
    updated: bool = False


def _version(value):
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise WebUpdateError("网页更新的插件兼容版本无效。")
    return tuple(int(part) for part in value.split("."))


def _metadata(value, bridge_version):
    if (not isinstance(value, dict) or type(value.get("schemaVersion")) is not int
            or value["schemaVersion"] != 1 or not isinstance(value.get("uiVersion"), str)
            or not _UI_VERSION.fullmatch(value["uiVersion"])):
        raise WebUpdateError("网页更新版本信息无效。")
    if _version(value.get("bridgeMinVersion")) > _version(bridge_version):
        raise WebUpdateError("新版网页需要更新 Mori 插件，已保留兼容版本。")
    return {key: value[key] for key in ("schemaVersion", "uiVersion", "bridgeMinVersion")}


def _path_name(name):
    if (not isinstance(name, str) or not name or "\\" in name or "\x00" in name
            or name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/"))
            or any(":" in part or part.endswith((" ", ".")) for part in name.split("/"))):
        raise WebUpdateError("网页更新包含无效资源路径。")
    path = PurePosixPath(name)
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise WebUpdateError("网页更新包含不支持的文件。")
    return path


def _json(data):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise WebUpdateError("网页更新信息无法读取。") from error


def _linked_path(path, anchor):
    """Reject links and Windows junctions inside the managed cache tree."""
    try:
        path.relative_to(anchor)
    except ValueError:
        return True
    while path != anchor:
        try:
            info = path.lstat()
            if (stat.S_ISLNK(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
                return True
        except FileNotFoundError:
            pass
        path = path.parent
    return False


class WebUpdateManager:
    def __init__(self, base_url, bundled_root, cache_root, bridge_version):
        parsed = urllib.parse.urlsplit(base_url)
        try:
            port = parsed.port
        except ValueError as error:
            raise WebUpdateError("网页更新服务器地址无效。") from error
        if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
                or not parsed.hostname or parsed.scheme not in ("http", "https")
                or (parsed.scheme == "http" and parsed.hostname.lower() not in ("127.0.0.1", "localhost", "::1"))):
            raise WebUpdateError("网页更新仅支持 HTTPS 服务器。")
        hostname = parsed.hostname.lower()
        authority = "[" + hostname + "]" if ":" in hostname else hostname
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            authority += ":" + str(port)
        self.base_url = urllib.parse.urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
        self.origin = urllib.parse.urlunsplit((parsed.scheme, authority, "", "", ""))
        self.server_key = hashlib.sha256(self.base_url.encode("utf-8")).hexdigest()
        self.cache_root = Path(cache_root).resolve() / self.server_key
        self.bundled_root = Path(bundled_root).resolve()
        self.bridge_version = bridge_version
        _version(bridge_version)
        self._opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        with _locks_guard:
            self._lock = _locks.setdefault(str(self.cache_root), threading.Lock())

    @staticmethod
    def _cancelled(cancel_event, deadline=None):
        if cancel_event is not None and cancel_event.is_set():
            raise WebUpdateError("网页更新检查已停止。")
        if deadline is not None and time.monotonic() > deadline:
            raise WebUpdateError("网页更新检查超时，已使用本机版本。")

    def _url(self, value):
        # Publisher file names are ASCII and need no percent escapes. Refusing
        # escapes also avoids differences between proxy/origin double decoding.
        if not isinstance(value, str) or not re.fullmatch(r"/krita-ui/[A-Za-z0-9_./-]+", value):
            raise WebUpdateError("网页更新资源地址无效。")
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in value:
            raise WebUpdateError("网页更新资源地址无效。")
        # Encoded path segments must not turn this public download into another
        # endpoint on the same origin.
        decoded = urllib.parse.unquote(parsed.path)
        if any(part in (".", "..", "") for part in decoded.lstrip("/").split("/")) or "\\" in decoded:
            raise WebUpdateError("网页更新资源地址无效。")
        return self.origin + value

    def _download(self, url, maximum, cancel_event, deadline, expected_size=None):
        self._cancelled(cancel_event, deadline)
        request = urllib.request.Request(url, headers={"Accept": "application/json, application/zip, application/octet-stream",
                                                      "Accept-Encoding": "identity", "User-Agent": "Mori-Krita-WebUpdate/1"})
        with self._opener.open(request, timeout=3) as response:
            if response.status != 200 or response.geturl() != url:
                raise WebUpdateError("网页更新下载未完成。")
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    length = int(length)
                except ValueError as error:
                    raise WebUpdateError("网页更新大小无效。") from error
                if length < 0 or length > maximum or (expected_size is not None and length != expected_size):
                    raise WebUpdateError("网页更新大小校验失败。")
            chunks, size = [], 0
            # HTTPResponse.read(n) can wait for all n bytes while a peer keeps
            # trickling data. read1 returns after one underlying read so the
            # total deadline and shutdown cancellation are checked in between.
            read = getattr(response, "read1", response.read)
            while True:
                self._cancelled(cancel_event, deadline)
                chunk = read(min(64 * 1024, maximum + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum:
                    raise WebUpdateError("网页更新资源超过大小限制。")
            if expected_size is not None and size != expected_size:
                raise WebUpdateError("网页更新大小校验失败。")
            return b"".join(chunks)

    def _bundled(self):
        version = "随插件安装"
        try:
            metadata_path = self.bundled_root / "ui-release.json"
            if metadata_path.stat().st_size > MAX_MANIFEST_BYTES:
                return WebRelease(self.bundled_root, version, "bundled")
            data = metadata_path.read_bytes()
            if len(data) <= MAX_MANIFEST_BYTES:
                version = _metadata(_json(data), self.bridge_version)["uiVersion"]
        except (OSError, WebUpdateError):
            pass
        return WebRelease(self.bundled_root, version, "bundled")

    def _cached(self):
        try:
            pointer_path = self.cache_root / "current.json"
            if _linked_path(pointer_path, self.cache_root.parent):
                return None
            if pointer_path.stat().st_size > MAX_MANIFEST_BYTES:
                return None
            raw = pointer_path.read_bytes()
            if len(raw) > MAX_MANIFEST_BYTES:
                return None
            pointer = _json(raw)
            meta = _metadata(pointer, self.bridge_version)
            digest = pointer.get("bundleSha256")
            inventory = pointer.get("files")
            if (not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                    or not isinstance(inventory, dict) or not 2 <= len(inventory) <= MAX_FILES
                    or "index.html" not in inventory or "ui-release.json" not in inventory):
                return None
            root = self.cache_root / "releases" / digest.lower()
            total, found = 0, set()
            for name, description in inventory.items():
                _path_name(name)
                if (not isinstance(description, dict) or type(description.get("size")) is not int
                        or not 0 <= description["size"] <= MAX_EXPANDED_BYTES
                        or not isinstance(description.get("sha256"), str) or not _SHA256.fullmatch(description["sha256"])):
                    return None
                target = root.joinpath(*PurePosixPath(name).parts)
                if _linked_path(target, self.cache_root.parent):
                    return None
                if total + description["size"] > MAX_EXPANDED_BYTES or target.stat().st_size != description["size"]:
                    return None
                data = target.read_bytes()
                total += len(data)
                if (total > MAX_EXPANDED_BYTES or len(data) != description["size"]
                        or hashlib.sha256(data).hexdigest() != description["sha256"].lower()):
                    return None
                found.add(name.casefold())
            if len(found) != len(inventory):
                return None
            if _metadata(_json((root / "ui-release.json").read_bytes()), self.bridge_version) != meta:
                return None
            return WebRelease(root, meta["uiVersion"], "cache")
        except (OSError, WebUpdateError, TypeError, AttributeError):
            return None

    def selected(self):
        return self._cached() or self._bundled()

    def _stage(self, data, manifest, cancel_event, deadline):
        metadata = _metadata(manifest, self.bridge_version)
        if _linked_path(self.cache_root, self.cache_root.parent):
            raise WebUpdateError("网页更新缓存路径无效，已使用内置版本。")
        self.cache_root.mkdir(parents=True, exist_ok=True)
        release_root = self.cache_root / "releases"
        if _linked_path(release_root, self.cache_root.parent):
            raise WebUpdateError("网页更新缓存路径无效，已使用内置版本。")
        release_root.mkdir(exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="stage-", dir=str(self.cache_root)))
        inventory = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                files = archive.infolist()
                if not files or len(files) > MAX_FILES:
                    raise WebUpdateError("网页更新资源数量无效。")
                total, names = 0, set()
                for entry in files:
                    self._cancelled(cancel_event, deadline)
                    if entry.is_dir():
                        raise WebUpdateError("网页更新包仅应包含资源文件。")
                    # ZipInfo normalizes backslashes on Windows; inspect the
                    # original member string before allowing that conversion.
                    name = entry.orig_filename
                    relative = _path_name(name)
                    mode = entry.external_attr >> 16
                    if (stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                            or entry.flag_bits & 1 or name.casefold() in names):
                        raise WebUpdateError("网页更新资源格式无效。")
                    names.add(name.casefold())
                    total += entry.file_size
                    if entry.file_size < 0 or total > MAX_EXPANDED_BYTES:
                        raise WebUpdateError("网页更新展开后超过大小限制。")
                    body = archive.read(entry)
                    if len(body) != entry.file_size:
                        raise WebUpdateError("网页更新资源大小无效。")
                    target = stage.joinpath(*relative.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    inventory[name] = {"size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
            if "index.html" not in inventory or "ui-release.json" not in inventory:
                raise WebUpdateError("网页更新缺少入口或版本文件。")
            release_metadata = (stage / "ui-release.json").read_bytes()
            if len(release_metadata) > MAX_MANIFEST_BYTES or _metadata(_json(release_metadata), self.bridge_version) != metadata:
                raise WebUpdateError("网页更新包与发布版本不一致。")
            self._cancelled(cancel_event, deadline)
            digest = manifest["bundle"]["sha256"].lower()
            target = release_root / digest
            if target.exists():
                # A previously downloaded release remains immutable for any old
                # gateway still serving it. Validate it against this exact zip.
                for name, description in inventory.items():
                    path = target.joinpath(*PurePosixPath(name).parts)
                    if (_linked_path(path, self.cache_root.parent) or not path.is_file()
                            or path.stat().st_size != description["size"]
                            or hashlib.sha256(path.read_bytes()).hexdigest() != description["sha256"]):
                        raise WebUpdateError("已缓存的网页资源校验失败，已使用内置版本。")
            else:
                os.replace(str(stage), str(target))
            self._cancelled(cancel_event, deadline)
            pointer = {**metadata, "bundleSha256": digest, "files": inventory}
            pointer_data = json.dumps(pointer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(pointer_data) > MAX_MANIFEST_BYTES:
                raise WebUpdateError("网页更新资源清单过大。")
            descriptor, pointer_path = tempfile.mkstemp(prefix="current-", suffix=".json", dir=str(self.cache_root))
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(pointer_data)
                os.replace(pointer_path, str(self.cache_root / "current.json"))
            finally:
                if os.path.exists(pointer_path):
                    os.unlink(pointer_path)
            return WebRelease(target, metadata["uiVersion"], "cache")
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def check(self, cancel_event=None):
        """Fetch once in a worker; failed checks keep the last verified release."""
        with self._lock:
            previous = self.selected()
            deadline = time.monotonic() + 12
            try:
                self._cancelled(cancel_event, deadline)
                manifest = _json(self._download(self._url("/krita-ui/manifest.json"), MAX_MANIFEST_BYTES,
                                               cancel_event, deadline))
                metadata = _metadata(manifest, self.bridge_version)
                bundle = manifest.get("bundle")
                if (not isinstance(bundle, dict) or not isinstance(bundle.get("sha256"), str)
                        or not _SHA256.fullmatch(bundle["sha256"]) or type(bundle.get("size")) is not int
                        or not 1 <= bundle["size"] <= MAX_BUNDLE_BYTES):
                    raise WebUpdateError("网页更新资源清单无效。")
                url = self._url(bundle.get("url"))
                if not urllib.parse.urlsplit(url).path.endswith(".zip"):
                    raise WebUpdateError("网页更新包地址无效。")
                if previous.ui_version == metadata["uiVersion"]:
                    # Same release was fully validated by selected(). Never
                    # replace files under a workbench already using this root.
                    return WebUpdateResult(previous, "网页已是最新兼容版本 · " + previous.ui_version)
                data = self._download(url, MAX_BUNDLE_BYTES, cancel_event, deadline, expected_size=bundle["size"])
                if hashlib.sha256(data).hexdigest() != bundle["sha256"].lower():
                    raise WebUpdateError("网页更新校验失败，已保留当前版本。")
                release = self._stage(data, manifest, cancel_event, deadline)
                return WebUpdateResult(release, "网页已更新 · " + release.ui_version, True)
            except WebUpdateError as error:
                return WebUpdateResult(previous, str(error))
            except Exception:
                # Do not disclose URLs, proxies, local paths, or server bodies.
                return WebUpdateResult(previous, "暂时无法检查网页更新，已使用本机兼容版本。")
