"""Private loopback transport for the bundled Mori UI.

Remote authentication stays private to the native client. Canvas documents, assets,
generation and conversations share the website server and its revision checks.
No route runs arbitrary Python or exposes filesystem access.
"""
import concurrent.futures
from collections import OrderedDict
import base64
import copy
import hashlib
import http.cookies
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote, parse_qs, urlencode, urlunsplit

MAX_BODY = 40 * 1024 * 1024
ID = r"[A-Za-z0-9_-]{1,150}"
CANVAS_ID = r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"


def _canvas_id(value):
    value = "default" if value is None else value
    if not isinstance(value, str) or not re.fullmatch(r"default|" + CANVAS_ID, value):
        raise GatewayError("画布标识无效")
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class GatewayError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class CanvasStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.RLock()
        self.cache = {}

    def _path(self, key):
        name = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.directory / (name + ".json")

    def _registry_path(self, key):
        return self._path(key).with_suffix(".documents.json")

    def _registry(self, key):
        try:
            value = json.loads(self._registry_path(key).read_text("utf-8"))
        except FileNotFoundError:
            return {"version": 1, "documents": []}
        except (OSError, ValueError, TypeError):
            raise GatewayError("本机画布目录无法读取，未覆盖原资料。", 503)
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("documents"), list)
                or any(not isinstance(item, dict) or not re.fullmatch(CANVAS_ID, str(item.get("id", "")))
                       for item in value["documents"])):
            raise GatewayError("本机画布目录无效，未覆盖原资料。", 503)
        return value

    def _assert_canvas(self, key, canvas_id):
        _canvas_id(canvas_id)
        if canvas_id != "default" and not any(item["id"] == canvas_id for item in self._registry(key)["documents"]):
            raise GatewayError("画布不存在或不属于当前账号。", 404)

    def _document_key(self, key, canvas_id):
        return key if canvas_id == "default" else key + ":canvas:" + canvas_id

    def get(self, key, canvas_id="default"):
        with self.lock:
            self._assert_canvas(key, canvas_id)
            document_key = self._document_key(key, canvas_id)
            if document_key not in self.cache:
                try:
                    value = json.loads(self._path(document_key).read_text("utf-8"))
                    if (not isinstance(value, dict) or not isinstance(value.get("nodes"), list)
                            or not isinstance(value.get("edges", []), list)
                            or type(value.get("revision", 0)) is not int or value.get("revision", 0) < 0):
                        raise ValueError("Invalid canvas")
                except FileNotFoundError:
                    if canvas_id != "default":
                        raise GatewayError("本机画布无法读取，未覆盖原资料。", 503)
                    value = {"name": "Krita 创作画布", "nodes": [], "edges": [],
                             "appliedCommandIds": [], "viewport": {"x": 0, "y": 0, "zoom": 1}, "revision": 0}
                except (OSError, ValueError, TypeError):
                    raise GatewayError("本机画布无法读取，未覆盖原资料，请检查备份后重试。", 503)
                self.cache[document_key] = value
            return {**copy.deepcopy(self.cache[document_key]), "id": canvas_id}

    def _write(self, path, value):
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(str(temp), str(path))

    def put(self, key, value, canvas_id="default"):
        with self.lock:
            if not isinstance(value, dict):
                raise GatewayError("画布数据无效")
            current = self.get(key, canvas_id)
            if any(value.get(field, canvas_id) != canvas_id for field in ("id", "canvasId")):
                raise GatewayError("当前画布与保存目标不一致。", 409)
            if type(value.get("expectedRevision")) is not int or value["expectedRevision"] != current.get("revision", 0):
                raise GatewayError("画布已在另一个插件页面更新，请刷新后继续。", 409)
            if (not isinstance(value.get("nodes"), list) or not isinstance(value.get("edges"), list)
                    or len(value["nodes"]) > 5000):
                raise GatewayError("画布数据无效")
            clean = {k: v for k, v in value.items() if k != "expectedRevision"}
            clean.setdefault("name", current.get("name", "未命名画布"))
            clean["revision"] = current.get("revision", 0) + 1
            clean.update(id=canvas_id, updatedAt=_now())
            document_key = self._document_key(key, canvas_id)
            self._write(self._path(document_key), clean)
            self.cache[document_key] = clean
            return {"saved": True, "revision": clean["revision"]}

    def summary(self, key, canvas_id, assets=None):
        value = self.get(key, canvas_id)
        assets = assets or {}
        cover = value.get("coverAssetId")
        asset = assets.get(cover, {})
        return {"id": canvas_id, "name": value.get("name", "未命名画布"),
                "coverAssetId": cover, "coverUrl": asset.get("url"),
                "createdAt": value.get("createdAt", ""), "updatedAt": value.get("updatedAt", ""),
                "assetCount": len({n["data"]["assetId"] for n in value["nodes"]
                                   if isinstance(n, dict) and isinstance(n.get("data"), dict)
                                   and isinstance(n["data"].get("assetId"), str) and n["data"]["assetId"]}),
                "nodeCount": len(value["nodes"])}

    def list(self, key, assets=None):
        with self.lock:
            identifiers = ["default"] + [item["id"] for item in self._registry(key)["documents"]]
            values = [self.summary(key, identifier, assets) for identifier in identifiers]
            return {"canvases": sorted(values, key=lambda item: item["updatedAt"], reverse=True), "defaultCanvasId": "default"}

    def created(self, key, request_id, name):
        with self.lock:
            entry = next((item for item in self._registry(key)["documents"] if item.get("clientRequestId") == request_id), None)
            if entry and entry.get("name") != name:
                raise GatewayError("请求标识已用于另一份画布。", 409)
            return self.summary(key, entry["id"]) if entry else None

    def create(self, key, remote, request_id, name):
        with self.lock:
            previous = self.created(key, request_id, name)
            if previous:
                return previous
            identifier = remote.get("id") if isinstance(remote, dict) else None
            if not isinstance(identifier, str) or not re.fullmatch(CANVAS_ID, identifier):
                raise GatewayError("服务器返回的画布标识无效。", 502)
            registry = self._registry(key)
            if any(item["id"] == identifier for item in registry["documents"]):
                raise GatewayError("画布标识已存在，未覆盖本机画布。", 409)
            created = remote.get("createdAt") or _now()
            value = {"id": identifier, "version": 1, "name": name, "nodes": [], "edges": [],
                     "viewport": {"x": 0, "y": 0, "zoom": 1}, "appliedCommandIds": [],
                     "revision": 0, "coverAssetId": None, "createdAt": created, "updatedAt": created}
            document_key = self._document_key(key, identifier)
            self._write(self._path(document_key), value)
            self._write(self._registry_path(key), {"version": 1, "documents": registry["documents"] + [
                {"id": identifier, "name": name, "clientRequestId": request_id}]})
            self.cache[document_key] = value
            return self.summary(key, identifier)


class MoriGateway:
    def __init__(self, client, web_root, storage_dir, invoke, notify=None):
        self.client = client
        self.web_root = Path(web_root).resolve()
        self.store = CanvasStore(Path(storage_dir) / "canvases")
        self.invoke = invoke
        self.notify = notify or (lambda text: None)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.session_lock = threading.RLock()
        self.events = []
        self.event_seq = 0
        self.selected_asset = None
        self.active_canvas = "default"
        self.cloud_canvas_ids = {"default"}
        self._legacy_migration = None
        self.assets_by_id = {}
        self.job_targets = {}
        self.request_targets = {}
        self.turn_targets = {}
        self.conversation_ids = set()
        self.receipts = set()
        self.inflight = set()
        self.failed = set()
        self.jobs_by_id = {}
        self.job_locks = {}
        self.media_cache_limit = 64 * 1024 * 1024
        self.media_cache = OrderedDict()
        self.media_cache_bytes = 0
        self.reference_assets = {}
        self.uploaded_assets = {}
        self.read_cache_ttl = 1.5
        self.read_cache = {}
        self.read_pending = {}
        self.read_revision = 0
        self.last_reference_metrics = {}
        self.pending_captures = {}
        self.auto_import = True
        self.closed = False
        self.account_id = None
        self._browser_login = None
        self.epoch = 0
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="Mori result")
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(30)

            def log_message(self, *_):
                pass  # Neither cookies, token paths nor user content enter logs.

            def do_GET(self):
                self._serve()

            def do_POST(self):
                self._serve()

            def do_PUT(self):
                self._serve()

            def do_DELETE(self):
                self._serve()

            def do_PATCH(self):
                self._serve()

            def _send(self, status, body=b"", content_type="application/json; charset=utf-8", extra=None):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Frame-Options", "DENY")
                if content_type.split(";", 1)[0] == "image/svg+xml":
                    self.send_header("Content-Disposition", "attachment")
                    self.send_header("Content-Security-Policy", "sandbox; default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
                for key, value in (extra or {}).items():
                    self.send_header(key, value)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, value, status=200, extra=None):
                self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), extra=extra)

            def _serve(self):
                try:
                    if self.headers.get("Host") != gateway.host:
                        raise GatewayError("请求主机无效", 403)
                    route = urlsplit(self.path)
                    if route.scheme or route.netloc:
                        raise GatewayError("请求路径无效", 403)
                    if self.command == "GET" and secrets.compare_digest(route.path, "/connect/" + gateway.token):
                        self._send(302, extra={"Location": "/", "Set-Cookie":
                            "mori_krita_bridge=" + gateway.token + "; Path=/; HttpOnly; SameSite=Strict"})
                        return
                    cookie = http.cookies.SimpleCookie()
                    try:
                        cookie.load(self.headers.get("Cookie", ""))
                        value = cookie.get("mori_krita_bridge")
                    except http.cookies.CookieError:
                        value = None
                    if not value or not secrets.compare_digest(value.value, gateway.token):
                        raise GatewayError("插件连接已失效，请在 Krita 面板重新连接。", 403)
                    origin = self.headers.get("Origin")
                    if ((origin and origin != gateway.origin) or
                            (self.command != "GET" and origin != gateway.origin)):
                        raise GatewayError("请求来源无效", 403)
                    if self.headers.get("Sec-Fetch-Site") in ("cross-site",):
                        raise GatewayError("请求来源无效", 403)
                    raw_length = self.headers.get("Content-Length", "0")
                    try:
                        length = int(raw_length)
                    except ValueError:
                        raise GatewayError("请求大小无效")
                    if length < 0 or length > MAX_BODY or self.headers.get("Transfer-Encoding"):
                        raise GatewayError("请求过大", 413)
                    payload = self.rfile.read(length) if length else None
                    status, headers, data = gateway.handle(self.command, self.path, payload,
                        browser_user=self.headers.get("X-Mori-User"), enforce_user=True,
                        browser_canvas=self.headers.get("X-Mori-Canvas"))
                    self._send(status, data, headers.get("content-type", "application/json; charset=utf-8"))
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as error:
                    status = getattr(error, "status", 500) or 500
                    try:
                        self._json({"error": str(error)[:600], "code": getattr(error, "code", "KRITA_BRIDGE_ERROR")}, status)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.host = "127.0.0.1:" + str(self.httpd.server_port)
        self.origin = "http://" + self.host
        self.url = self.origin + "/connect/" + self.token
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="Mori bridge", daemon=True)

    def start(self):
        self.thread.start()
        return self.url

    def close(self):
        self.closed = True
        if self._browser_login is not None:
            self._browser_login.cancel()
        self.epoch += 1
        if self.thread.is_alive():
            self.httpd.shutdown()
        self.httpd.server_close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        # Closing a plugin window revokes in-flight work, while an explicit
        # logout also forgets the encrypted sign-in kept for the next launch.
        getattr(self.client, "close", self.client.clear_session)()
        with self.lock:
            self.media_cache.clear()
            self.media_cache_bytes = 0
            self.reference_assets.clear()
            self.uploaded_assets.clear()
            self.read_cache.clear()
            self.pending_captures.clear()

    def _json(self, value, status=200):
        return status, {"content-type": "application/json; charset=utf-8"}, json.dumps(value, ensure_ascii=False).encode("utf-8")

    def _account_key(self):
        return self.client.base_url + ":" + str(self.client.user_id or "local")

    def push(self, kind, **data):
        with self.lock:
            self.event_seq += 1
            self.events.append({"seq": self.event_seq, "type": kind, **data})
            self.events = self.events[-200:]

    def _sync_identity(self):
        user_id = self.client.user_id
        with self.lock:
            if user_id != self.account_id:
                self.account_id = user_id
                self.epoch += 1
                self.events.clear()
                self.selected_asset = None
                self.active_canvas = "default"
                self.cloud_canvas_ids = {"default"}
                self.assets_by_id.clear()
                self.jobs_by_id.clear()
                self.job_targets.clear()
                self.request_targets.clear()
                self.turn_targets.clear()
                self.conversation_ids.clear()
                self.receipts.clear()
                self.inflight.clear()
                self.failed.clear()
                self.media_cache.clear()
                self.media_cache_bytes = 0
                self.reference_assets.clear()
                self.uploaded_assets.clear()
                self.read_cache.clear()
                self.read_revision += 1
                self.pending_captures.clear()

    def _cache_media(self, url, data, mime="image/png", epoch=None):
        if epoch is not None and epoch != self.epoch:
            return
        path = urlsplit(url).path
        if not path.startswith("/media/") or len(data) > self.media_cache_limit:
            return
        with self.lock:
            if self.closed or (epoch is not None and epoch != self.epoch):
                return
            old = self.media_cache.pop(path, None)
            if old:
                self.media_cache_bytes -= len(old[1])
            self.media_cache[path] = (mime, data)
            self.media_cache_bytes += len(data)
            while self.media_cache and self.media_cache_bytes > self.media_cache_limit:
                _, (_, removed) = self.media_cache.popitem(last=False)
                self.media_cache_bytes -= len(removed)

    def _get_media(self, url):
        with self.lock:
            if self.closed or self.account_id != self.client.user_id:
                return None
            path = urlsplit(url).path
            value = self.media_cache.get(path)
            if value:
                self.media_cache.move_to_end(path)
            return value

    def _invalidate_reads(self):
        with self.lock:
            self.read_cache.clear()
            self.read_revision += 1

    def _channel(self, canvas_id="default"):
        factory = getattr(self.client, "fork_session", None)
        channel = factory() if factory else self.client
        channel._canvas_id = canvas_id
        return channel

    def capture(self, source="auto", mode="fast", prepare=None, display=False):
        if self.closed:
            return
        self._sync_identity()
        epoch = self.epoch
        with self.lock:
            capture_canvas = self.active_canvas
        started = time.perf_counter()
        self.notify("正在读取 Krita 参考区域…")
        read_started = time.perf_counter()
        captured = self.invoke("snapshot" if prepare else "capture", {"source": source})
        read_ms = (time.perf_counter() - read_started) * 1000
        prepare_started = time.perf_counter()
        if prepare:
            self.notify("正在处理参考图（快速 2048）…" if mode == "fast" else "正在无损编码原尺寸参考图…")
            captured = prepare(captured, mode=mode)
        data = getattr(captured, "image_bytes", None)
        if data is None:
            data = captured.png_bytes
        prepare_ms = (time.perf_counter() - prepare_started) * 1000
        mime = getattr(captured, "mime", "image/png")
        digest = hashlib.sha256(data).hexdigest()
        with self.session_lock:
            self._sync_identity()
            if self.closed or self.epoch != epoch:
                return
            client = self._channel(capture_canvas)
            with self.lock:
                asset = self.reference_assets.get(digest)
        upload_started = time.perf_counter()
        reused = asset is not None
        import_id = secrets.token_hex(16) if display else None
        if display and asset is None:
            extension = {"image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
            preview_url = "/media/" + import_id + "." + extension
            preview = {"id": import_id, "name": "Krita · 正在导入", "url": preview_url,
                       "width": getattr(captured, "width", 1), "height": getattr(captured, "height", 1),
                       "size": len(data), "filename": import_id + "." + extension, "createdAt": ""}
            with self.lock:
                if self.closed or self.epoch != epoch:
                    return
                self.pending_captures[import_id] = epoch
                self._cache_media(preview_url, data, mime, epoch)
                self.push("capture_pending", importId=import_id, asset=preview, canvasId=capture_canvas)
        if asset is None:
            size = len(data) / (1024 * 1024)
            self.notify("正在上传参考图 · %.2f MB…" % size)
            name = "Krita · " + (captured.layer_name or captured.document_name)
            try:
                if prepare:
                    def progress(sent, total):
                        if epoch == self.epoch and not self.closed:
                            percent = min(100, int(sent * 100 / total)) if total else 0
                            self.notify("正在上传参考图 · %d%% · %.2f MB" % (percent, size))
                    asset = client.upload_image(data, name, mime=mime, progress=progress)
                else:
                    asset = client.upload_image(data, name)
            except Exception as error:
                with self.lock:
                    self.pending_captures.pop(import_id, None)
                    if import_id and epoch == self.epoch and not self.closed:
                        self.push("capture_failed", importId=import_id, error=str(error)[:500], canvasId=capture_canvas)
                raise
        with self.session_lock:
            self._sync_identity()
            if self.closed or self.epoch != epoch:
                return
            with self.lock:
                self.assets_by_id[asset["id"]] = asset
                self.uploaded_assets[asset["id"]] = asset
                self.reference_assets[digest] = asset
                self.pending_captures.pop(import_id, None)
                self._cache_media(asset["url"], data, mime, epoch)
                self._invalidate_reads()
                self.push("asset", asset=asset, importId=import_id, display=display, canvasId=capture_canvas)
        metrics = dict(getattr(captured, "metrics", {}))
        metrics.update(gui_read_ms=round(read_ms, 1), prepare_ms=round(prepare_ms, 1),
                       upload_ms=round((time.perf_counter()-upload_started)*1000, 1),
                       elapsed_ms=round((time.perf_counter()-started)*1000, 1),
                       bytes=len(data), reused=reused)
        self.last_reference_metrics = metrics
        message = "参考图已添加 · %.2f MB · %.1f 秒" % (len(data)/(1024*1024), (time.perf_counter()-started))
        if reused:
            message += " · 已复用上传"
        self.notify(message)

    def import_selected(self):
        self._sync_identity()
        epoch = self.epoch
        with self.lock:
            asset = self.assets_by_id.get(self.selected_asset)
        if not asset:
            raise GatewayError("请先在 Mori 画布上选中一张图片。")
        target = self.invoke("target", {})
        with self.session_lock:
            self._sync_identity()
            if self.closed or self.epoch != epoch:
                return
            client = self._channel()
            cached = self._get_media(asset["url"])
        data = cached[1] if cached else client.download_asset(asset["url"])
        with self.session_lock:
            self._sync_identity()
            if self.closed or self.epoch != epoch:
                return
            self._cache_media(asset["url"], data, asset.get("mime", "image/png"), epoch)
        self.invoke("import", {"bytes": data, "name": "Mori · " + asset.get("name", "图片"),
                               "target": target, "force_new": target is None, "epoch": epoch})
        self.notify("已将所选图片添加为 Krita 新图层。")

    def _tracking_request(self, method, path, payload):
        if method != "POST":
            return None
        generation = path in ("/api/generate", "/api/image-actions")
        message = re.fullmatch(r"/api/creator/conversations/(" + ID + r")/messages", path)
        tool = path == "/api/tool-runs" or bool(re.fullmatch(
            r"/api/(?:tool-library/tools|community/items)/" + ID + r"/run", path))
        aesthetic = bool(re.fullmatch(r"/api/aesthetic/styles/[^/]+/preview", path))
        if not (generation or message or tool or aesthetic):
            return None
        with self.lock:
            if any(epoch == self.epoch for epoch in self.pending_captures.values()):
                raise GatewayError("参考图片正在后台上传，完成后即可生成。", 409)
        target = self.invoke("target", {})
        request_id = payload.get("clientRequestId")
        with self.lock:
            if request_id:
                self.request_targets.setdefault(request_id, target)
                target = self.request_targets[request_id]
            if message:
                self.conversation_ids.add(message.group(1))
        return {"target": target, "generation": generation, "message": bool(message), "tool": tool,
                "aesthetic": aesthetic, "epoch": self.epoch}

    def _observe(self, method, path, value, tracked):
        if tracked and tracked["epoch"] == self.epoch and isinstance(value, dict):
            with self.lock:
                if tracked["generation"] and value.get("id"):
                    self.job_targets.setdefault(value["id"], tracked["target"])
                elif tracked["message"] and value.get("id"):
                    self.turn_targets[value["id"]] = tracked["target"]
                elif tracked["tool"] and value.get("jobId"):
                    self.job_targets.setdefault(value["jobId"], tracked["target"])
                elif tracked.get("aesthetic") and isinstance(value.get("preview"), dict) and value["preview"].get("jobId"):
                    self.job_targets.setdefault(value["preview"]["jobId"], tracked["target"])
        if path == "/api/assets" and isinstance(value, list):
            with self.lock:
                self.assets_by_id.update({a["id"]: a for a in value if isinstance(a, dict) and a.get("id")})
        elif path == "/api/assets" and isinstance(value, dict) and value.get("id"):
            with self.lock:
                self.assets_by_id[value["id"]] = value
        if path == "/api/jobs" and isinstance(value, list):
            with self.lock:
                self.jobs_by_id.update({j["id"]: j for j in value if isinstance(j, dict) and j.get("id")})
                for job in value:
                    if job.get("clientRequestId") in self.request_targets:
                        self.job_targets.setdefault(job["id"], self.request_targets[job["clientRequestId"]])
                    turn_id = (job.get("origin") or {}).get("turnId")
                    if turn_id in self.turn_targets:
                        self.job_targets.setdefault(job["id"], self.turn_targets[turn_id])
        if path.startswith("/api/creator/conversations/") and isinstance(value, dict):
            with self.lock:
                for turn in value.get("turns", []):
                    if turn["id"] in self.turn_targets:
                        for job_id in turn.get("jobIds", []):
                            self.job_targets.setdefault(job_id, self.turn_targets[turn["id"]])
        self._schedule_results()

    def _schedule_results(self):
        if not self.auto_import or self.closed:
            return
        with self.lock:
            for job_id, target in list(self.job_targets.items()):
                job = self.jobs_by_id.get(job_id, {})
                for asset_id in job.get("assetIds", []):
                    receipt = (self.epoch, job_id, asset_id)
                    asset = self.assets_by_id.get(asset_id)
                    if asset and receipt not in self.receipts | self.inflight | self.failed:
                        self.inflight.add(receipt)
                        self.executor.submit(self._import_result, receipt, asset, target)

    def _import_result(self, receipt, asset, target):
        try:
            if self.closed or receipt[0] != self.epoch:
                return
            with self.session_lock:
                self._sync_identity()
                if self.closed or receipt[0] != self.epoch:
                    return
                client = self._channel()
                cached = self._get_media(asset["url"])
            png = cached[1] if cached else client.download_asset(asset["url"])
            if self.closed or receipt[0] != self.epoch:
                return
            self._cache_media(asset["url"], png, asset.get("mime", "image/png"), receipt[0])
            with self.lock:
                job_lock = self.job_locks.setdefault((receipt[0], receipt[1]), threading.Lock())
            with job_lock:
                if self.closed or receipt[0] != self.epoch:
                    return
                with self.lock:
                    target = self.job_targets.get(receipt[1], target)
                result = self.invoke("import", {"bytes": png, "name": "Mori · " + asset.get("name", "生成结果"),
                                               "target": target, "force_new": target is None,
                                               "epoch": receipt[0]})
                with self.lock:
                    if self.closed or receipt[0] != self.epoch:
                        return
                    self.receipts.add(receipt)
                    if target is None and result is not None:
                        self.job_targets[receipt[1]] = result
                    self.push("status", message="生成结果已加入 Krita 新图层。")
            self.notify("生成结果已加入 Krita 新图层。")
        except Exception as error:
            with self.lock:
                if not self.closed and receipt[0] == self.epoch:
                    self.failed.add(receipt)
            if not self.closed and receipt[0] == self.epoch:
                self.push("status", message="图层导入未完成：" + str(error))
                self.notify("图层导入未完成：" + str(error) + "。图片仍保留在 Mori，可选中后手动导入。")
        finally:
            with self.lock:
                self.inflight.discard(receipt)

    def _authorize(self, pathname, browser_user, enforce_user, method=None):
        # An <img> cannot add the account header. Only this server-public media
        # GET is exempt; the remote publication token still controls access.
        public_image = method == "GET" and re.fullmatch(r"/api/community/items/" + CANVAS_ID + r"/media/a[0-9]+", pathname)
        protected = ((pathname.startswith('/api/') and pathname not in ('/api/auth/session','/api/auth/login','/api/auth/register'))
                     or pathname in ('/krita/events','/krita/selection'))
        if protected and not public_image and enforce_user and (browser_user or None) != (self.client.user_id or None):
            raise GatewayError("账号会话已改变，请刷新插件画布后继续。", 401)

    def handle(self, method, path, body=None, browser_user=None, enforce_user=False, browser_canvas=None):
        route = urlsplit(path)
        pathname = route.path
        canvas_id = _canvas_id(browser_canvas)
        # Local UI work never waits behind TLS, server polling or image uploads.
        local = not pathname.startswith(("/api/", "/media/"))
        if local:
            with self.lock:
                self._sync_identity()
                self._authorize(pathname, browser_user, enforce_user, method)
                epoch, key = self.epoch, self._account_key()
            result = self._local(method, path, body, key, canvas_id)
            with self.lock:
                self._sync_identity()
                if self.epoch != epoch and pathname.startswith(("/krita/", "/api/")) and not pathname.startswith('/krita/auth/browser/'):
                    raise GatewayError("账号会话已改变，已忽略旧请求的响应。", 401)
            return result

        with self.session_lock:
            self._sync_identity()
            self._authorize(pathname, browser_user, enforce_user, method)
            self._validate_remote(method, pathname)
            if method == "GET" and pathname.startswith("/media/"):
                cached = self._get_media(path)
                if cached:
                    return 200, {"content-type": cached[0]}, cached[1]
            value = json.loads(body or b"{}") if body else {}
            tracked = self._tracking_request(method, pathname, value)
            epoch = self.epoch
            if pathname.startswith("/api/auth/"):
                # Only authentication mutates the master CookieJar. Other routes
                # use frozen sessions, so an old request cannot bill a new user.
                self._invalidate_reads()
                return self._remote(self.client, method, path, body, value, tracked, epoch, canvas_id)
            client = self._channel(canvas_id)
        cacheable = method == "GET" and pathname in ("/api/assets", "/api/jobs", "/api/assistant/commands")
        if pathname == "/api/canvases" and method == "GET":
            self._migrate_legacy(client, epoch)
        if cacheable:
            return self._read_once(client, method, path, body, value, tracked, epoch, canvas_id)
        return self._remote(client, method, path, body, value, tracked, epoch, canvas_id)

    def _validate_remote(self, method, pathname):
        canvas_methods = {"/api/state": ("GET", "PUT"), "/api/canvases": ("GET", "POST")}
        if pathname in canvas_methods:
            if method not in canvas_methods[pathname]:
                raise GatewayError("请求方式无效", 405)
            return
        if pathname == "/api/auth/register" and method != "POST":
            raise GatewayError("注册接口只支持提交注册信息。", 403)
        # New website actions retain explicit methods. This does not open an
        # unrestricted proxy to administration or future server endpoints.
        new_routes = (
            (r"/api/community(?:/mine)?", ("GET",)),
            (r"/api/community/items/" + ID, ("GET",)),
            (r"/api/community/items/" + CANVAS_ID + r"/media/a[0-9]+", ("GET",)),
            (r"/api/community/(?:preview|publish|share)", ("POST",)),
            (r"/api/community/items/" + ID + r"/(?:import|revoke)", ("POST",)),
            (r"/api/community/items/" + ID + r"/(?:tool-preview|run)", ("POST",)),
            (r"/api/aesthetic/styles/" + ID + r"/(?:build|copy)", ("POST",)),
            (r"/api/tool-library/capabilities", ("GET",)),
            (r"/api/tool-library/(?:cmf-analysis|design-analysis)", ("POST",)),
            (r"/api/tool-library/(?:cmf-analysis|design-analysis)/" + CANVAS_ID, ("GET",)),
            (r"/api/tool-library/(?:cmf-analysis|design-analysis)/" + CANVAS_ID + r"/cancel", ("POST",)),
            (r"/api/tool-library/preview", ("POST",)),
            (r"/api/tool-library/drafts/" + ID + r"/restore", ("POST",)),
            (r"/api/tool-library/tools/" + ID + r"/preview", ("POST",)),
        )
        for pattern, methods in new_routes:
            if re.fullmatch(pattern, pathname):
                if method not in methods:
                    raise GatewayError("请求方式不在 Krita 插件支持范围内。", 403)
                return
        allowed = re.fullmatch(r"/api/(?:auth/(?:session|login|logout|register)|account(?:/password)?|credits(?:/[a-z-]+)?|chat-usage|config|check|assets|jobs(?:/[^/]+/cancel)?|generate|image-actions|state|assistant/commands|object-analysis(?:/[^/]+(?:/cancel)?)?|inspiration(?:/public(?:/import|/[^/]+/image)?|/[^/]+)?|aesthetic(?:/styles(?:/[^/]+(?:/(?:analyze|refine|restore|analysis/cancel|preview))?)?|/previews/[^/]+/cancel)?|creator/(?:config|conversations(?:/[^/]+(?:/messages|/events)?)?|turns/[^/]+/cancel)|tool-runs(?:/[^/]+(?:/cancel)?)?|tool-library(?:/(?:drafts|tools)(?:/[^/]+(?:/(?:publish|assist|membership|run|cover)(?:/[^/]+/cancel)?)?)?)?)", pathname)
        if not allowed and not (method == "GET" and re.fullmatch(r"/media/[a-f0-9-]+(?:-preview)?\.(?:png|jpg|webp|gif|svg)", pathname)):
            raise GatewayError("此接口不在 Krita 插件支持范围内。", 403)
        if pathname == "/api/config" and method != "GET":
            raise GatewayError("请在服务器管理端配置模型服务。", 403)

    def _read_once(self, client, method, path, body, value, tracked, epoch, canvas_id="default"):
        key = (epoch, canvas_id, path)
        while True:
            with self.lock:
                if self.closed or epoch != self.epoch:
                    raise GatewayError("账号会话已改变", 401)
                cached = self.read_cache.get(key)
                if cached and cached[0] > time.monotonic():
                    return cached[1]
                event = self.read_pending.get(key)
                if event is None:
                    event = threading.Event()
                    self.read_pending[key] = event
                    revision = self.read_revision
                    break
            if not event.wait(60):
                raise GatewayError("读取服务器状态超时", 504)
        try:
            result = self._remote(client, method, path, body, value, tracked, epoch, canvas_id)
            with self.lock:
                if 200 <= result[0] < 300 and epoch == self.epoch and revision == self.read_revision:
                    self.read_cache[key] = (time.monotonic() + self.read_cache_ttl, result)
            return result
        finally:
            with self.lock:
                self.read_pending.pop(key, None)
                event.set()

    def _remote(self, client, method, path, body, value, tracked, epoch, canvas_id="default"):
        pathname = urlsplit(path).path
        remote_path = path
        # Keep watching accepted jobs from other plugin canvases after a page
        # switch, while returning only this page's jobs unless history asks all.
        all_jobs = method == "GET" and pathname == "/api/jobs"
        wants_all = parse_qs(urlsplit(path).query).get("scope") == ["all"]
        if all_jobs and not wants_all:
            parts = urlsplit(path)
            query = parse_qs(parts.query)
            query["scope"] = ["all"]
            remote_path = urlunsplit(("", "", parts.path, urlencode(query, doseq=True), ""))
        status, headers, data = client.raw_request(method, remote_path, body=body,
                                                   content_type="application/json" if body else None)
        with self.session_lock:
            self._sync_identity()
            if self.epoch != epoch and not pathname.startswith('/api/auth/'):
                raise GatewayError("账号会话已改变，已忽略旧请求的响应。", 401)
            if "application/json" not in headers.get("content-type", ""):
                if status == 200 and pathname.startswith("/media/"):
                    self._cache_media(path, data, headers.get("content-type", "image/png"), self.epoch)
                return status, headers, data
            result = json.loads(data)
            if 200 <= status < 300:
                if method != "GET":
                    self._invalidate_reads()
                if pathname == "/api/assets" and isinstance(result, list):
                    ids = {asset.get("id") for asset in result}
                    with self.lock:
                        result = result + [asset for asset_id, asset in self.uploaded_assets.items() if asset_id not in ids]
                self._observe(method, pathname, result, tracked)
                if pathname == "/api/assets" and method == "POST" and isinstance(result, dict) and result.get("url"):
                    match = re.fullmatch(r"data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\r\n]+)", value.get("dataUrl", ""))
                    if match:
                        try:
                            image = base64.b64decode(match.group(2), validate=True)
                            self._cache_media(result["url"], image, match.group(1), epoch)
                            with self.lock:
                                self.uploaded_assets[result["id"]] = result
                        except (ValueError, TypeError):
                            pass
                if pathname == "/api/auth/session" and isinstance(result, dict):
                    result.setdefault("providers", {})["google"] = False
                    result["providers"]["browserLogin"] = True
                if pathname == "/api/account" and isinstance(result, dict) and isinstance(result.get("profile"), dict):
                    if result["profile"].get("canSetPassword"):
                        result["profile"]["canSetPassword"] = False
                        result["profile"]["passwordSetupOnWeb"] = True
                # The server scopes commands to the cloud canvas. Filtering by
                # local jobs would hide changes made in the website.
                if pathname == "/api/canvases" and isinstance(result, dict):
                    identifiers = result.get("canvases", []) if method == "GET" else [result]
                    with self.lock:
                        self.cloud_canvas_ids.update(_canvas_id(item["id"]) for item in identifiers)
                if pathname == "/api/state" and method == "GET" and isinstance(result, dict):
                    with self.lock:
                        self.cloud_canvas_ids.add(canvas_id)
                if pathname in ("/api/auth/session", "/api/auth/login", "/api/auth/register", "/api/auth/logout"):
                    user = (result.get("user") if isinstance(result, dict) else None) or {}
                    self.notify("已登录 " + (user.get("name") or user.get("email") or "Mori") if user else "请使用 Mori 邮箱账号登录。")
                if all_jobs and not wants_all and isinstance(result, list):
                    result = [job for job in result if job.get("canvasId", "default") == canvas_id]
            return self._json(result, status)

    def _assert_cloud_canvas(self, canvas_id):
        if canvas_id not in self.cloud_canvas_ids:
            raise GatewayError("请先读取当前账号的云端画布后继续。", 404)

    def _migrate_legacy(self, client, epoch):
        # Only the library read migrates old local files. Ordinary cloud reads
        # and saves keep working even when an old local file needs recovery.
        from .canvas_sync import LegacyCanvasMigration
        with self.lock:
            if self._legacy_migration is None:
                self._legacy_migration = LegacyCanvasMigration(self.store)
            key = self._account_key()
        def check():
            with self.session_lock:
                self._sync_identity()
                if self.closed or epoch != self.epoch:
                    raise GatewayError("账号会话已改变。", 401)
        def request(method, path, value, canvas_id):
            check()
            # A frozen channel per target retains the original signed-in user.
            factory = getattr(client, "fork_session", None)
            channel = factory() if factory else copy.copy(client)
            channel._canvas_id = _canvas_id(canvas_id)
            response = self._remote(channel, method, path,
                json.dumps(value).encode() if value is not None else None,
                value or {}, None, epoch, canvas_id)
            if not 200 <= response[0] < 300:
                raise GatewayError(json.loads(response[2]).get("error", "旧画布迁移未完成"), response[0])
            return json.loads(response[2])
        try:
            count = self._legacy_migration.run(key, request, check)
            if count:
                self.notify("已将 %d 份旧 Krita 画布保存为云端独立副本。" % count)
        except Exception as error:
            check()
            self.notify("旧 Krita 画布迁移未完成，原文件已保留；重新打开工作空间可重试：" + str(error)[:250])

    def browser_login(self):
        with self.lock:
            if self.closed:
                raise GatewayError("插件已关闭", 410)
            if self._browser_login is None:
                from .browser_login import BrowserLogin
                self._browser_login = BrowserLogin(self.client, self._sync_identity)
            return self._browser_login

    def _local(self, method, path, body, account_key, canvas_id="default"):
        route = urlsplit(path)
        pathname = route.path
        if pathname.startswith('/krita/auth/browser/'):
            if method != 'POST':
                raise GatewayError("请求方式无效", 405)
            value = json.loads(body or b'{}')
            if not isinstance(value, dict):
                raise GatewayError("登录请求无效")
            manager = self.browser_login()
            action = pathname.rsplit('/', 1)[-1]
            attempt_id = value.get('attemptId')
            if action == 'start':
                return self._json(manager.start(attempt_id))
            if not isinstance(attempt_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', attempt_id):
                raise GatewayError("登录标识无效")
            if action == 'status':
                return self._json(manager.status(attempt_id))
            if action == 'cancel':
                manager.cancel(attempt_id)
                return self._json({'ok': True})
            raise GatewayError("登录接口不存在", 404)
        if pathname == "/krita/config" and method == "GET":
            # Public site address only; the paired loopback cookie is still
            # required by Handler. Never return account/session information.
            return self._json({"serverUrl": self.client.base_url})
        if pathname == "/krita/events" and method == "GET":
            from urllib.parse import parse_qs
            after = int(parse_qs(route.query).get("after", ["0"])[0])
            with self.lock:
                self._assert_cloud_canvas(canvas_id)
                self.active_canvas = canvas_id
                return self._json([e for e in self.events if e["seq"] > after
                                   and (e.get("canvasId", "default") == canvas_id or e["type"] == "status")])
        if pathname == "/krita/selection" and method == "POST":
            value = json.loads(body or b"{}")
            with self.lock:
                self._assert_cloud_canvas(canvas_id)
                self.active_canvas = canvas_id
                self.selected_asset = value.get("assetId")
            return self._json({"ok": True})
        if method != "GET":
            raise GatewayError("请求方式无效", 405)
        name = unquote(pathname).lstrip("/") or "index.html"
        if pathname == "/community" or re.fullmatch(r"/share/" + ID, pathname):
            name = "index.html"
        dest = (self.web_root / name).resolve()
        if self.web_root not in dest.parents or not dest.is_file():
            raise GatewayError("页面资源不存在", 404)
        mime = mimetypes.guess_type(str(dest))[0] or "application/octet-stream"
        return 200, {"content-type": mime}, dest.read_bytes()
