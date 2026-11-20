"""Mori's existing HTTP protocol, with optional user-bound session storage.

Call this synchronous client from a worker. It never retries requests: a caller
must retain clientRequestId when resolving an uncertain generation submission.
"""

from __future__ import annotations

import base64
import copy
import functools
import http.client
import http.cookiejar
import json
import re
import socket
import ssl
import threading
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_REQUEST_BYTES = 40 * 1024 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,100}$")
_RESPONSE_HEADERS = frozenset((
    "content-type", "cache-control", "retry-after", "content-disposition",
    "etag", "last-modified", "content-language",
))


@functools.lru_cache(maxsize=1)
def _https_context():
    # Loading the Windows trust store for every small poll is expensive. Share
    # the verified context while leaving urllib's system proxy handling intact.
    context = ssl.create_default_context()
    if ssl.HAS_ALPN:
        context.set_alpn_protocols(["http/1.1"])
    return context


class _ConnectionPool:
    """Small session-owned HTTP/1.1 pool. A failed request is never replayed.

    Connections are leased exclusively, and only complete responses are reusable.
    The four-second idle limit is shorter than Node's common keep-alive timeout.
    Login/logout replace the pool; in-flight old-account leases close on return.
    """
    def __init__(self, idle_seconds=4.0, max_idle=8):
        self.idle_seconds = idle_seconds
        self.max_idle = max_idle
        self._lock = threading.Lock()
        self._idle = {}
        self.closed = False

    def acquire(self, key, factory, timeout):
        with self._lock:
            if self.closed:
                raise OSError("Mori transport session ended")
            now = time.monotonic()
            for existing in list(self._idle):
                fresh = []
                for returned, connection in self._idle[existing]:
                    if now - returned < self.idle_seconds and connection.sock is not None:
                        fresh.append((returned, connection))
                    else:
                        connection.close()
                if fresh:
                    self._idle[existing] = fresh
                else:
                    self._idle.pop(existing, None)
            available = self._idle.get(key)
            if available:
                _, connection = available.pop()
                if not available:
                    self._idle.pop(key, None)
                connection.timeout = timeout
                if connection.sock is not None:
                    connection.sock.settimeout(timeout)
                return connection, False
        return factory(), True

    def release(self, key, connection):
        with self._lock:
            count = sum(len(value) for value in self._idle.values())
            if self.closed or connection.sock is None or count >= self.max_idle:
                connection.close()
            else:
                self._idle.setdefault(key, []).append((time.monotonic(), connection))

    def close(self):
        with self._lock:
            self.closed = True
            for entries in self._idle.values():
                for _, connection in entries:
                    connection.close()
            self._idle.clear()


class _ResponseLease:
    """urllib-compatible response which returns its socket after a full read."""
    def __init__(self, response, connection, pool, key):
        self._response = response
        self._connection = connection
        self._pool = pool
        self._key = key
        self._closed = False

    def __getattr__(self, name):
        return getattr(self._response, name)

    def read(self, amount=None):
        try:
            return self._response.read(amount)
        except Exception:
            self._connection.close()
            raise

    def close(self):
        if self._closed:
            return
        self._closed = True
        complete = self._response.isclosed() or self._response.length == 0
        reusable = complete and not self._response.will_close and self._connection.sock is not None
        self._response.close()
        if reusable:
            self._pool.release(self._key, self._connection)
        else:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        self.close()


class _PersistentOpen:
    """Retain urllib proxy/CONNECT/cookie semantics while avoiding close-per-GET.

    Based on CPython AbstractHTTPHandler.do_open. It deliberately has no retry
    branch, including when an idle socket was closed by the remote peer.
    """
    def __init__(self, pool_getter, *arguments, **keywords):
        self._pool_getter = pool_getter
        super().__init__(*arguments, **keywords)

    def do_open(self, http_class, req, **connection_options):
        host = req.host
        if not host:
            raise urllib.error.URLError("no host given")
        headers = dict(req.unredirected_hdrs)
        headers.update({key: value for key, value in req.headers.items() if key not in headers})
        headers = {name.title(): value for name, value in headers.items()}
        headers["Connection"] = "keep-alive"
        proxy_auth = headers.get("Proxy-Authorization")
        tunnel = getattr(req, "_tunnel_host", None)
        # Key includes proxy destination and credentials, never printed or saved.
        key = (http_class, host, tunnel, proxy_auth, id(connection_options.get("context")))
        pool = self._pool_getter()
        connection, fresh = pool.acquire(key, lambda: http_class(host, timeout=req.timeout, **connection_options), req.timeout)
        connection.set_debuglevel(0)
        if tunnel:
            tunnel_headers = {}
            if proxy_auth:
                tunnel_headers["Proxy-Authorization"] = proxy_auth
                del headers["Proxy-Authorization"]
            if fresh:
                connection.set_tunnel(tunnel, headers=tunnel_headers)
        try:
            connection.request(req.get_method(), req.selector, req.data, headers,
                               encode_chunked=req.has_header("Transfer-encoding"))
            response = connection.getresponse()
        except Exception as error:
            connection.close()
            if isinstance(error, (OSError, http.client.HTTPException)):
                raise urllib.error.URLError(error) from error
            raise
        response.url = req.get_full_url()
        response.msg = response.reason
        return _ResponseLease(response, connection, pool, key)


class _PersistentHTTPHandler(_PersistentOpen, urllib.request.HTTPHandler):
    pass


class _PersistentHTTPSHandler(_PersistentOpen, urllib.request.HTTPSHandler):
    pass


class _ProgressBody:
    """File-like body with a known Content-Length and bounded temporary chunks."""

    def __init__(self, data, callback):
        self.data = memoryview(data)
        self.callback = callback
        self.offset = 0
        self.reported = -1
        self.last_report = float("-inf")

    def _report(self, final=False):
        now = time.monotonic()
        if self.reported == self.offset or (not final and now - self.last_report < 0.15):
            return
        self.reported = self.offset
        self.last_report = now
        try:
            self.callback(self.offset, len(self.data))
        except Exception:
            # A UI callback must never interrupt/replay an in-flight request.
            pass

    def read(self, size=8192):
        # http.client asks for the next chunk only after sending the preceding
        # chunk. Report that completed amount, rather than bytes merely read.
        final = self.offset == len(self.data)
        self._report(final=final)
        if final:
            return b""
        if size is None or size < 0:
            size = 65536
        end = min(self.offset + min(size, 65536), len(self.data))
        result = self.data[self.offset:end].tobytes()
        self.offset = end
        return result


class MoriApiError(Exception):
    """A displayable error. No request body, password or Cookie is retained."""

    def __init__(self, message: str, status: int = 0, code: str = ""):
        super().__init__(message)
        self.status = status
        self.code = code


# Alternate spelling for consumers which conventionally capitalize API.
MoriAPIError = MoriApiError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Even a same-origin 307 can resend a paid POST. Surface every redirect.
        return None


def _origin(parts):
    try:
        port = parts.port
    except ValueError as error:
        raise MoriApiError("服务器端口无效", code="UNSAFE_URL") from error
    if port is not None and port < 1:
        raise MoriApiError("服务器端口无效", code="UNSAFE_URL")
    return parts.scheme.lower(), (parts.hostname or "").lower(), port or (
        443 if parts.scheme.lower() == "https" else 80
    )


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise MoriApiError("任务或对话标识无效", code="INVALID_ID")
    return value


def _request_id(value: str) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise MoriApiError("请求标识需要 8–100 位字母、数字、下划线或连字符", code="INVALID_REQUEST_ID")
    return value


class MoriClient:
    """Account-isolated memory CookieJar with an optional encrypted session store."""

    def __init__(self, base_url: str = "https://morinet.chat", timeout: float = 30, *,
                 session_store=None, _transport_pool=None, _proxy_config=None):
        if not isinstance(base_url, str) or re.search(r"[\x00-\x20\\]", base_url):
            raise MoriApiError("服务器地址无效", code="UNSAFE_URL")
        try:
            parts = urllib.parse.urlsplit(base_url)
            hostname = parts.hostname
        except ValueError as error:
            raise MoriApiError("服务器地址无效", code="UNSAFE_URL") from error
        if (
            parts.scheme not in ("http", "https") or not hostname
            or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment
            or (parts.scheme == "http" and hostname.lower() not in ("127.0.0.1", "localhost", "::1"))
        ):
            raise MoriApiError("请使用 HTTPS 服务器地址；HTTP 仅用于本机测试", code="UNSAFE_URL")
        self._origin = _origin(parts)
        scheme, normalized_host, port = self._origin
        normalized_host = normalized_host.encode("idna").decode("ascii")
        authority = "[%s]" % normalized_host if ":" in normalized_host else normalized_host
        if port != (443 if scheme == "https" else 80):
            authority += ":%s" % port
        self._base_url = urllib.parse.urlunsplit((scheme, authority, "", "", ""))
        self.timeout = max(1.0, min(float(timeout), 120.0))
        self._cookies = http.cookiejar.CookieJar()
        self._transport_pool = _transport_pool or _ConnectionPool()
        self._proxy_config = dict(urllib.request.getproxies() if _proxy_config is None else _proxy_config)
        self.connection_route = "system" if self._proxy_config else "direct"
        self._opener = self._build_opener(self._proxy_config)
        self._lock = threading.RLock()
        self._session_generation = 0
        self._snapshot_owner = None
        self._snapshot_generation = None
        self._snapshot_user = None
        self._snapshot_revoked = False
        self.user_id = None
        self.user = None
        self.auth_enabled = True
        self._canvas_id = None
        self._session_store = session_store if scheme == "https" else None
        self._session_restored = False
        if self._session_store is not None:
            try:
                for cookie in self._session_store.load(self._base_url):
                    self._cookies.set_cookie(cookie)
                self._session_restored = bool(list(self._cookies))
            except Exception:
                # A damaged/unavailable Windows key must not prevent sign-in.
                pass

    @property
    def base_url(self) -> str:
        return self._base_url

    def _build_opener(self, proxies):
        pool_getter = lambda: self._transport_pool
        handlers = [urllib.request.ProxyHandler(proxies),
                    urllib.request.HTTPCookieProcessor(self._cookies), _NoRedirect(),
                    _PersistentHTTPHandler(pool_getter)]
        if self._origin[0] == "https":
            handlers.append(_PersistentHTTPSHandler(pool_getter, context=_https_context()))
        return urllib.request.build_opener(*handlers)

    @property
    def session_version(self):
        """Current master version, or the immutable version frozen in a fork."""
        # This read is used by GUI timers. The request lock can be held during
        # a slow network read; reading an immutable integer must never wait on
        # it. Authorization and snapshot validation still take their locks.
        return self._snapshot_generation if self._snapshot_owner is not None else self._session_generation

    @property
    def session_restored(self):
        """A saved cookie awaits validation by the existing /auth/session route."""
        return self._session_restored

    def assert_session_current(self):
        with self._lock:
            self._check_snapshot()
            return self.session_version

    def clear_session(self, *, forget=True):
        with self._lock:
            self._session_generation += 1
            if self._snapshot_owner is not None:
                self._snapshot_revoked = True
            else:
                self._transport_pool.close()
                self._transport_pool = _ConnectionPool()
            self._cookies.clear()
            self.user_id = None
            self.user = None
            self._session_restored = False
            if forget and self._session_store is not None:
                try:
                    self._session_store.clear(self._base_url)
                except Exception:
                    pass

    def close(self):
        """Revoke this runtime and its forks while retaining the saved sign-in."""
        self.clear_session(forget=False)

    def _persist_session(self):
        if self._session_store is not None and self._snapshot_owner is None:
            try:
                self._session_store.save(self._base_url, self._cookies)
            except Exception:
                pass

    def _check_snapshot(self):
        owner = self._snapshot_owner
        if owner is not None:
            with owner._lock:
                if (self._snapshot_revoked or owner._session_generation != self._snapshot_generation
                        or owner.user_id != self._snapshot_user):
                    raise MoriApiError("账号会话已改变，已停止旧请求", 401, "SESSION_CHANGED")

    def _invalidate_owner_if_current(self):
        owner = self._snapshot_owner
        if owner is not None:
            with owner._lock:
                if (owner._session_generation == self._snapshot_generation
                        and owner.user_id == self._snapshot_user):
                    owner.clear_session()

    def fork_session(self):
        """Return an independently locked transport with a revocable snapshot.

        Use a fork for media or image uploads so a slow transfer does not hold
        the main client's networking lock. The caller must still freeze its
        Krita document/account epoch before starting an operation and validate
        it when applying results. Already in-flight requests use only their old
        account's copied Cookie; a fork never adopts a newer account's Cookie.
        Login/logout are deliberately unavailable through snapshot transports.
        """
        with self._lock:
            self._check_snapshot()
            owner = self._snapshot_owner or self
            with owner._lock:
                fork = MoriClient(self._base_url, self.timeout,
                                  _transport_pool=owner._transport_pool, _proxy_config=self._proxy_config)
                fork._canvas_id = self._canvas_id
                for cookie in self._cookies:
                    fork._cookies.set_cookie(copy.deepcopy(cookie))
                fork.user_id = self.user_id
                fork.user = copy.deepcopy(self.user)
                fork.auth_enabled = self.auth_enabled
                fork._snapshot_owner = owner
                fork._snapshot_generation = owner._session_generation
                fork._snapshot_user = owner.user_id
                return fork

    def _url(self, path: str, media_only: bool = False) -> str:
        if not isinstance(path, str) or re.search(r"[\x00-\x20\\]", path):
            raise MoriApiError("请求地址无效", code="UNSAFE_URL")
        try:
            parts = urllib.parse.urlsplit(path)
        except ValueError as error:
            raise MoriApiError("请求地址无效", code="UNSAFE_URL") from error
        if parts.netloc or parts.scheme:
            if not parts.scheme or not parts.netloc or _origin(parts) != self._origin:
                raise MoriApiError("只允许连接当前 Mori 服务器", code="UNSAFE_URL")
        if parts.username is not None or parts.password is not None or parts.fragment:
            raise MoriApiError("请求地址无效", code="UNSAFE_URL")
        route = parts.path
        # Check decoded path too; never let percent-encoded traversal cross out
        # of /api or /media after a proxy/server decodes it.
        decoded = route
        for _ in range(4):
            next_path = urllib.parse.unquote(decoded)
            if next_path == decoded:
                break
            decoded = next_path
        if (
            not route.startswith("/") or route.startswith("//")
            or re.search(r"[\x00-\x20\\]", decoded) or "%" in decoded
            or any(segment in (".", "..") for segment in decoded.split("/"))
            or not decoded.startswith("/media/" if media_only else ("/api/", "/media/"))
        ):
            raise MoriApiError("只允许访问 Mori 接口和图片", code="UNSAFE_URL")
        return self._base_url + urllib.parse.urlunsplit(("", "", route, parts.query, ""))

    def _read_limited(self, response, limit: int) -> bytes:
        length = response.headers.get("Content-Length")
        if length:
            try:
                if int(length) > limit:
                    raise MoriApiError("服务器返回的数据过大", code="RESPONSE_TOO_LARGE")
            except ValueError:
                pass
        result = response.read(limit + 1)
        if len(result) > limit:
            raise MoriApiError("服务器返回的数据过大", code="RESPONSE_TOO_LARGE")
        return result

    def _observe_session(self, route: str, status: int, data: bytes):
        if route in ("/api/auth/login", "/api/auth/register", "/api/auth/session") and 200 <= status < 300:
            try:
                payload = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                return
            if isinstance(payload, dict):
                user = payload.get("user")
                next_user_id = user.get("id") if isinstance(user, dict) else None
                if next_user_id != self.user_id:
                    self._session_generation += 1
                    self._transport_pool.close()
                    self._transport_pool = _ConnectionPool()
                self.user = user if isinstance(user, dict) else None
                self.user_id = self.user.get("id") if self.user else None
                self._session_restored = False
                if route == "/api/auth/session":
                    self.auth_enabled = payload.get("enabled", True) is not False
                if self.user_id:
                    self._persist_session()
                else:
                    self.clear_session()
        elif route == "/api/auth/logout":
            self.clear_session()
        elif status == 401 and (route == "/api/auth/session" or not route.startswith("/api/auth/")):
            self.clear_session()

    def raw_request(self, method: str, path: str, body: bytes | None = None,
                    content_type: str | None = None, progress=None, *, canvas_id=None) -> tuple[int, dict[str, str], bytes]:
        """Return HTTP status/selected headers/body, including HTTP errors.

        Cookie and Set-Cookie stay private to this client. This entry point is
        suitable for a loopback bridge; its caller must separately authenticate
        loopback callers and restrict the routes it chooses to expose.
        """
        method = str(method).upper()
        if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
            raise MoriApiError("不支持的请求方法", code="INVALID_METHOD")
        url = self._url(path)
        route = urllib.parse.urlsplit(url).path
        if body is not None and not isinstance(body, bytes):
            raise MoriApiError("请求内容必须是字节数据", code="INVALID_BODY")
        if body is not None and len(body) > MAX_UPLOAD_BYTES * 4 // 3 + 65536:
            raise MoriApiError("上传内容过大", code="REQUEST_TOO_LARGE")
        if content_type and re.search(r"[\r\n]", content_type):
            raise MoriApiError("请求类型无效", code="INVALID_BODY")
        headers = {"Accept": "application/json, image/*;q=0.9", "User-Agent": "Mori-Krita/0.1"}
        canvas_id = self._canvas_id if canvas_id is None else canvas_id
        if canvas_id is not None:
            if not isinstance(canvas_id, str) or not re.fullmatch(r"default|[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", canvas_id):
                raise MoriApiError("画布标识无效", code="INVALID_CANVAS")
            if route.startswith("/api/") and not route.startswith("/api/auth/"):
                headers["X-Mori-Canvas"] = canvas_id
        if method not in ("GET", "HEAD", "OPTIONS"):
            headers["Origin"] = self._base_url
        if content_type:
            headers["Content-Type"] = content_type
        request_body = body
        if progress is not None and body is not None:
            if not callable(progress):
                raise MoriApiError("上传进度回调无效", code="INVALID_BODY")
            headers["Content-Length"] = str(len(body))
            request_body = _ProgressBody(body, progress)
        with self._lock:
            self._check_snapshot()
            if self._snapshot_owner is not None and route.startswith("/api/auth/"):
                raise MoriApiError("请在主画布中登录或退出账号", 403, "SNAPSHOT_AUTH_BLOCKED")
            if route in ("/api/auth/login", "/api/auth/register") and method == "POST":
                self.clear_session()
            if self.user_id and (not route.startswith("/api/auth/") or route == "/api/auth/logout"):
                headers["X-Mori-User"] = str(self.user_id)
            req = urllib.request.Request(url, data=request_body, headers=headers, method=method)
            try:
                try:
                    response = self._opener.open(req, timeout=self.timeout)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    status = response.getcode()
                    limit = MAX_IMAGE_BYTES if route.startswith("/media/") else MAX_JSON_BYTES
                    data = self._read_limited(response, limit)
                    response_headers = {
                        key.lower(): value for key, value in response.headers.items()
                        if key.lower() in _RESPONSE_HEADERS
                    }
            except MoriApiError:
                if route == "/api/auth/logout":
                    self.clear_session()
                raise
            except (TimeoutError, socket.timeout) as error:
                if route == "/api/auth/logout":
                    self.clear_session()
                self._check_snapshot()
                raise MoriApiError("连接 Mori 超时，请查询任务状态后再决定是否重试", code="TIMEOUT") from error
            except (urllib.error.URLError, OSError) as error:
                if route == "/api/auth/logout":
                    self.clear_session()
                self._check_snapshot()
                # Show useful causes without exposing proxy credentials or URLs.
                reason = error.reason if isinstance(error, urllib.error.URLError) else error
                if isinstance(reason, ssl.SSLCertVerificationError):
                    message = "Mori 服务器证书校验失败，请检查系统时间和证书。"
                elif isinstance(reason, ssl.SSLError):
                    message = "Mori 安全连接被中断，请重新连接或检查代理。"
                elif isinstance(reason, socket.gaierror):
                    message = "无法解析 Mori 服务器地址，请检查网络或服务器地址。"
                elif isinstance(reason, ConnectionRefusedError):
                    message = "Mori 连接被拒绝，请检查代理或服务器是否在线。"
                else:
                    message = "无法连接 Mori 服务器，请重新连接或检查网络。"
                raise MoriApiError(message, code="NETWORK_ERROR") from error
            self._check_snapshot()
            if status == 401 and not route.startswith("/api/auth/"):
                self._invalidate_owner_if_current()
            self._observe_session(route, status, data)
            return status, response_headers, data

    request_raw = raw_request

    def request(self, method: str, path: str, payload: Any = None,
                request_id: str | None = None):
        if request_id is not None:
            if not isinstance(payload, dict):
                raise MoriApiError("带请求标识的内容必须是对象", code="INVALID_BODY")
            payload = dict(payload, clientRequestId=_request_id(request_id))
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        status, _, data = self.raw_request(method, path, body, "application/json" if body is not None else None)
        return self._decode_json(status, data)

    def _decode_json(self, status, data):
        try:
            value = json.loads(data) if data else None
        except (ValueError, UnicodeDecodeError) as error:
            if 300 <= status < 400:
                raise MoriApiError("服务器返回了跳转，请使用最终的 Mori 服务器地址", status, "REDIRECT_BLOCKED") from error
            raise MoriApiError("服务器返回的内容不是有效 JSON", status, "INVALID_RESPONSE") from error
        if not 200 <= status < 300:
            message = value.get("error") if isinstance(value, dict) else None
            code = value.get("code", "") if isinstance(value, dict) else ""
            if not isinstance(message, str) or not message:
                message = "服务器返回跳转，已停止请求" if 300 <= status < 400 else "Mori 请求失败（HTTP %s）" % status
            message = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", message)[:500]
            raise MoriApiError(message, status, str(code or ("REDIRECT_BLOCKED" if 300 <= status < 400 else "HTTP_ERROR")))
        return value

    def login(self, email: str, password: str):
        # Discard the old account before starting a new sign-in attempt.
        self.clear_session()
        return self.request("POST", "/api/auth/login", {"email": email, "password": password})

    def session(self):
        # Probe only this read-only route. Paid submissions and uploads are
        # never retried or replayed when a transport fails.
        with self._lock:
            try:
                return self.request("GET", "/api/auth/session")
            except MoriApiError as error:
                if (error.code not in ("NETWORK_ERROR", "TIMEOUT")
                        or self._origin[0] != "https"
                        or not any(self._proxy_config.get(key) for key in ("http", "https", "all"))
                        or self._snapshot_owner is not None):
                    raise
                original = error
            old_opener = self._opener
            self._opener = self._build_opener({})
            try:
                result = self.request("GET", "/api/auth/session")
            except Exception:
                self._opener = old_opener
                raise original
            self._proxy_config = {}
            self.connection_route = "direct_fallback"
            return result

    def logout(self):
        try:
            return self.request("POST", "/api/auth/logout", {})
        finally:
            self.clear_session()

    def config(self):
        return self.request("GET", "/api/config")

    def credits(self):
        return self.request("GET", "/api/credits")

    def account(self):
        return self.request("GET", "/api/account")

    def quote(self, settings):
        return self.request("POST", "/api/credits/quote", settings)

    def assets(self):
        return self.request("GET", "/api/assets")

    def jobs(self):
        return self.request("GET", "/api/jobs")

    def upload_image(self, png_bytes: bytes, name: str = "Krita 画布", mime=None, progress=None):
        """Upload PNG/JPEG/WebP; progress receives sent HTTP-body bytes/total."""
        detected = None
        if isinstance(png_bytes, bytes):
            if png_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
                detected = "image/png"
            elif png_bytes.startswith(b"\xff\xd8\xff"):
                detected = "image/jpeg"
            elif png_bytes.startswith(b"RIFF") and png_bytes[8:12] == b"WEBP":
                detected = "image/webp"
        if detected is None or (mime is not None and mime != detected):
            raise MoriApiError("请选择有效的 PNG、JPEG 或 WebP 图片", code="INVALID_IMAGE")
        if len(png_bytes) > MAX_UPLOAD_BYTES:
            raise MoriApiError("图片大于 50 MB", code="REQUEST_TOO_LARGE")
        # Base64 is already JSON-safe ASCII. Build bytes directly instead of
        # allocating base64 text, a full Unicode JSON string, and then UTF-8.
        encoded_name = json.dumps(str(name)[:200], ensure_ascii=False).encode("utf-8")
        prefix = b',"dataUrl":"data:' + detected.encode("ascii") + b';base64,'
        request_size = len(b'{"name":') + len(encoded_name) + len(prefix) + 4 * ((len(png_bytes) + 2) // 3) + len(b'"}')
        if request_size > MAX_UPLOAD_REQUEST_BYTES:
            raise MoriApiError("参考图超过服务器单次上传上限，请使用快速参考或缩小选区", code="REQUEST_TOO_LARGE")
        body = b"".join((b'{"name":', encoded_name, prefix,
                         base64.b64encode(png_bytes), b'"}'))
        status, _, data = self.raw_request("POST", "/api/assets", body, "application/json", progress=progress)
        return self._decode_json(status, data)

    def generate(self, settings, node_id: str, references, request_id: str):
        payload = {key: settings[key] for key in ("model", "prompt", "aspect", "resolution", "quality", "count", "edit") if key in settings}
        payload.update(nodeId=_identifier(node_id), references=list(references))
        return self.request("POST", "/api/generate", payload, request_id)

    def creator_config(self):
        return self.request("GET", "/api/creator/config")

    def conversations(self):
        return self.request("GET", "/api/creator/conversations")

    def create_conversation(self, title: str):
        return self.request("POST", "/api/creator/conversations", {"title": str(title)[:80]})

    def send_message(self, conversation_id: str, message: str, references,
                     image_settings, agent_model: str, context, request_id: str):
        return self.request("POST", "/api/creator/conversations/%s/messages" % _identifier(conversation_id), {
            "message": message, "references": list(references), "imageSettings": image_settings,
            "agentModel": agent_model, "context": context,
        }, request_id)

    def conversation(self, conversation_id: str):
        return self.request("GET", "/api/creator/conversations/%s" % _identifier(conversation_id))

    def events(self, conversation_id: str, after: int = 0):
        if type(after) is not int or after < 0:
            raise MoriApiError("事件序号无效", code="INVALID_ID")
        return self.request("GET", "/api/creator/conversations/%s/events?after=%s" % (_identifier(conversation_id), after))

    def assistant_commands(self):
        return self.request("GET", "/api/assistant/commands")

    def cancel_job(self, job_id: str):
        return self.request("POST", "/api/jobs/%s/cancel" % _identifier(job_id), {})

    def cancel_turn(self, turn_id: str):
        return self.request("POST", "/api/creator/turns/%s/cancel" % _identifier(turn_id), {})

    def download_asset(self, asset_url: str) -> bytes:
        self._url(asset_url, media_only=True)
        status, headers, data = self.raw_request("GET", asset_url)
        if status != 200:
            raise MoriApiError("无法下载生成图片（HTTP %s）" % status, status, "DOWNLOAD_FAILED")
        if not headers.get("content-type", "").split(";", 1)[0].startswith("image/"):
            raise MoriApiError("服务器返回的内容不是图片", status, "INVALID_IMAGE")
        return data
