"""Browser consent with a native-only S256 verifier and revocable session adoption."""
import hashlib
import base64
import re
import secrets
import threading
import time
import webbrowser


class BrowserLogin:
    def __init__(self, client, on_complete=lambda: None, open_browser=None):
        self.client = client
        self.on_complete = on_complete
        self.open_browser = open_browser or webbrowser.open
        self.lock = threading.RLock()
        self.current = None
        self.cancelled = set()

    def start(self, attempt_id=None):
        attempt_id = attempt_id or secrets.token_hex(16)
        if not isinstance(attempt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", attempt_id):
            raise ValueError("浏览器登录标识无效")
        with self.lock:
            if attempt_id in self.cancelled:
                return {"attemptId": attempt_id, "status": "cancelled", "message": "已取消浏览器登录。"}
            if self.current and self.current["status"] in ("starting", "pending"):
                if self.current["attemptId"] == attempt_id:
                    return self._public(self.current)
                raise ValueError("已有浏览器登录进行中，请先取消或完成。")
            item = {"attemptId": attempt_id, "status": "starting", "message": "正在打开浏览器…",
                    "cancel": threading.Event(), "version": self.client.session_version}
            self.current = item
            threading.Thread(target=self._run, args=(item,), daemon=True, name="Mori browser login").start()
            return self._public(item)

    @staticmethod
    def _public(item):
        return {key: item[key] for key in ("attemptId", "status", "message")}

    def status(self, attempt_id):
        with self.lock:
            if not self.current or self.current["attemptId"] != attempt_id:
                return {"attemptId": attempt_id, "status": "cancelled", "message": "这次登录已结束，请重新发起。"}
            return self._public(self.current)

    def cancel(self, attempt_id=None):
        with self.lock:
            if attempt_id:
                if len(self.cancelled) >= 128:
                    self.cancelled.clear()
                self.cancelled.add(attempt_id)
            item = self.current
            if item and (attempt_id is None or item["attemptId"] == attempt_id) and item["status"] != "complete":
                item["cancel"].set()
                item.update(status="cancelled", message="已取消浏览器登录。")

    def _run(self, item):
        channel = None
        request_id = None
        verifier = secrets.token_urlsafe(32)
        adopted = False
        try:
            channel = self.client.new_login_transport()
            challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
            flow = channel.request("POST", "/api/auth/desktop/start", {"challenge": challenge})
            request_id = flow.get("requestId")
            if (not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", request_id)
                    or flow.get("verificationPath") != "/?desktop_login=" + request_id):
                raise ValueError("服务器返回的浏览器登录地址无效。")
            if item["cancel"].is_set():
                return
            # No credential is placed in this URL; it points only at the configured server.
            if not self.open_browser(channel.base_url + flow["verificationPath"]):
                raise ValueError("无法打开系统浏览器，请检查默认浏览器设置后重试。")
            with self.lock:
                if item["cancel"].is_set():
                    return
                item.update(status="pending", message="请在浏览器登录并确认连接，然后返回 Krita。")
            interval = max(2, min(float(flow.get("interval", 2)), 10))
            expires = time.monotonic() + min(max(float(flow.get("expiresIn", 600)), 1), 600)
            while not item["cancel"].wait(interval):
                if self.client.session_version != item["version"]:
                    raise ValueError("插件账号已切换，已停止这次浏览器登录。")
                if time.monotonic() >= expires:
                    raise ValueError("浏览器登录已过期，请重新发起。")
                try:
                    result = channel.request("POST", "/api/auth/desktop/poll", {"requestId": request_id, "verifier": verifier})
                except Exception as error:
                    if getattr(error, "status", None) == 429:
                        interval = min(interval + 2, 10)
                        continue
                    raise
                if result.get("status") == "pending":
                    continue
                if result.get("status") != "complete" or not channel.user_id:
                    raise ValueError("服务器返回的登录结果无效。")
                with self.lock:
                    if item["cancel"].is_set():
                        return
                    self.client.adopt_login(channel, item["version"])
                    adopted = True
                    item.update(status="complete", message="已登录 Mori，可以继续创作。")
                self.on_complete()
                return
        except Exception as error:
            with self.lock:
                if not adopted and not item["cancel"].is_set():
                    code = getattr(error, "status", None)
                    message = "服务器尚未支持浏览器登录，请更新 Mori 服务端。" if code == 404 else str(error)
                    item.update(status="error", message=message[:250] or "浏览器登录失败，请重试。")
        finally:
            if channel:
                try:
                    if not adopted and channel.user_id:
                        channel.logout()
                    elif request_id and not adopted:
                        channel.request("POST", "/api/auth/desktop/cancel", {"requestId": request_id, "verifier": verifier})
                except Exception:
                    pass
                channel.close()
