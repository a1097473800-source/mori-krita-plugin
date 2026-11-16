"""User-bound Windows storage for Mori's existing server session cookie.

Passwords are never passed here. DPAPI uses the current Windows user's key
(not LOCAL_MACHINE); the normalized HTTPS origin is also authenticated entropy.
An unavailable key/store simply requires the usual server sign-in again.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit


_MAX_BLOB = 65536
_COOKIE_NAME = "mori_session"


def _canonical_origin(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.path not in ("", "/")
                or parts.query or parts.fragment or re.search(r"[\x00-\x20\\]", value)):
            return None
        host = parts.hostname.lower().encode("idna").decode("ascii")
        port = parts.port if parts.port is not None else 443
        if not 1 <= port <= 65535:
            return None
        authority = "[%s]" % host if ":" in host else host
        return "https://" + authority + (":%s" % port if port != 443 else "")
    except (AttributeError, TypeError, ValueError, UnicodeError):
        return None


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, entropy, *, decrypt=False):
    """CryptProtectData/UnprotectData without any credential dialog."""
    if os.name != "nt":
        raise OSError("Windows DPAPI is unavailable")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    data_buffer = ctypes.create_string_buffer(data)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    source = _DataBlob(len(data), ctypes.cast(data_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    extra = _DataBlob(len(entropy), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _DataBlob()
    if decrypt:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                             ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    else:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                             ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                             ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN. Omit LOCAL_MACHINE to bind the current user.
    if not function(ctypes.byref(source), None, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32.LocalFree(output.data)


def _cookie_record(cookie, origin):
    host = urlsplit(origin).hostname
    domain = cookie.domain.lstrip(".").lower()
    if (cookie.name != _COOKIE_NAME or cookie.path != "/" or not cookie.value
            or len(cookie.value) > 8192 or re.search(r"[\x00-\x20\x7f;,]", cookie.value)
            or not domain or not (host == domain or (cookie.domain_specified and host.endswith("." + domain)))
            or cookie.is_expired()):
        return None
    return {"value": cookie.value, "domain": cookie.domain,
            "domain_specified": bool(cookie.domain_specified),
            "domain_initial_dot": bool(cookie.domain_initial_dot),
            "expires": cookie.expires, "secure": bool(cookie.secure)}


class WindowsSessionStore:
    """One encrypted, atomically replaced cookie file per HTTPS origin.

    The directory inherits the current user's LocalAppData profile ACL. No
    credentials are persisted on non-Windows platforms or for HTTP test hosts.
    """
    def __init__(self, root=None):
        local = os.environ.get("LOCALAPPDATA")
        self.root = Path(root) if root is not None else (
            Path(local) / "Mori" / "KritaPlugin" / "sessions" if local else None)

    def _path(self, origin):
        normalized = _canonical_origin(origin)
        if normalized is None or self.root is None or os.name != "nt":
            return None, None
        return self.root / (hashlib.sha256(normalized.encode("utf-8")).hexdigest() + ".dpapi"), normalized

    @staticmethod
    def _entropy(origin):
        return ("Mori-Krita/session/v1\0" + origin).encode("utf-8")

    def clear(self, origin):
        path, _ = self._path(origin)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def load(self, origin):
        path, origin = self._path(origin)
        if path is None:
            return []
        try:
            with path.open("rb") as stream:
                encrypted = stream.read(_MAX_BLOB + 1)
            if not encrypted or len(encrypted) > _MAX_BLOB:
                raise ValueError("Invalid session record")
            payload = json.loads(_dpapi(encrypted, self._entropy(origin), decrypt=True))
            if (not isinstance(payload, dict) or payload.get("version") != 1
                    or payload.get("origin") != origin or not isinstance(payload.get("cookies"), list)
                    or len(payload["cookies"]) != 1):
                raise ValueError("Invalid session record")
            record = payload["cookies"][0]
            if not isinstance(record, dict):
                raise ValueError("Invalid session record")
            if (not isinstance(record.get("value"), str) or not isinstance(record.get("domain"), str)
                    or any(type(record.get(flag)) is not bool
                           for flag in ("domain_specified", "domain_initial_dot", "secure"))):
                raise ValueError("Invalid session cookie fields")
            expires = record.get("expires")
            if expires is not None and (type(expires) is not int or expires <= time.time()):
                raise ValueError("Expired session")
            cookie = http.cookiejar.Cookie(
                0, _COOKIE_NAME, record["value"], None, False, record["domain"],
                record["domain_specified"], record["domain_initial_dot"], "/", True,
                record["secure"], expires, expires is None, None, None,
                {"HttpOnly": None, "SameSite": "Lax"}, False)
            if _cookie_record(cookie, origin) is None:
                raise ValueError("Invalid session cookie")
            return [cookie]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
            self.clear(origin)
            return []

    def save(self, origin, cookies):
        path, origin = self._path(origin)
        if path is None:
            return False
        records = [record for cookie in cookies if (record := _cookie_record(cookie, origin)) is not None]
        if len(records) != 1:
            self.clear(origin)
            return False
        temporary = None
        try:
            payload = json.dumps({"version": 1, "origin": origin, "cookies": records},
                                 separators=(",", ":")).encode("utf-8")
            encrypted = _dpapi(payload, self._entropy(origin))
            if len(encrypted) > _MAX_BLOB:
                return False
            path.parent.mkdir(parents=True, exist_ok=True)
            # mkstemp creates an exclusive file; only encrypted bytes touch disk.
            descriptor, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            return True
        except (OSError, ValueError, TypeError):
            return False
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass
