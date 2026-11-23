"""Persist the existing server session in the macOS login Keychain."""
import http.cookiejar
import json
import time

from .macos_native import keychain_delete, keychain_read, keychain_write
from .session_store import _canonical_origin, _cookie_record


class MacSessionStore:
    def clear(self, origin):
        normalized = _canonical_origin(origin)
        if normalized:
            try:
                keychain_delete(normalized)
            except (OSError, AttributeError):
                pass

    def save(self, origin, cookies):
        normalized = _canonical_origin(origin)
        if not normalized:
            return False
        records = [record for cookie in cookies if (record := _cookie_record(cookie, normalized)) is not None]
        if len(records) != 1:
            self.clear(normalized)
            return False
        payload = json.dumps({"version": 1, "origin": normalized, "cookies": records},
                             separators=(",", ":")).encode("utf-8")
        try:
            return keychain_write(normalized, payload)
        except (OSError, AttributeError):
            return False

    def load(self, origin):
        normalized = _canonical_origin(origin)
        if not normalized:
            return []
        try:
            data = keychain_read(normalized)
            if data is None:
                return []
            if len(data) > 65536:
                raise ValueError("Invalid session size")
            payload = json.loads(data)
            if (not isinstance(payload, dict) or payload.get("version") != 1
                    or payload.get("origin") != normalized or not isinstance(payload.get("cookies"), list)
                    or len(payload["cookies"]) != 1):
                raise ValueError("Invalid session record")
            record = payload["cookies"][0]
            if (not isinstance(record, dict) or not isinstance(record.get("value"), str)
                    or not isinstance(record.get("domain"), str)
                    or any(type(record.get(flag)) is not bool
                           for flag in ("domain_specified", "domain_initial_dot", "secure"))):
                raise ValueError("Invalid session cookie")
            expires = record.get("expires")
            if expires is not None and (type(expires) is not int or expires <= time.time()):
                raise ValueError("Expired session")
            cookie = http.cookiejar.Cookie(0, "mori_session", record["value"], None, False,
                record["domain"], record["domain_specified"], record["domain_initial_dot"], "/", True,
                record["secure"], expires, expires is None, None, None,
                {"HttpOnly": None, "SameSite": "Lax"}, False)
            if _cookie_record(cookie, normalized) is None:
                raise ValueError("Wrong session origin")
            return [cookie]
        except (OSError, AttributeError):
            return []
        except (ValueError, TypeError, KeyError, OverflowError):
            self.clear(normalized)
            return []
