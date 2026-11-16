"""Deterministic, network-free Mori protocol fixture for the real Krita GUI QA.

This module is never imported by the production plugin. No API keys, user data,
network calls or real billing are used. Images are synthetic Qt QImage patterns.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid


SETTINGS = {"model": "gpt-image-2.5", "aspect": "4:3", "resolution": "1K",
            "quality": "high", "count": 1, "prompt": ""}
USER = {"id": "mori-qa-user", "name": "Mori 插件测试", "email": "qa@example.invalid"}


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def pattern_png(variant=0, width=320, height=240):
    from PyQt5.QtCore import QBuffer, QIODevice
    from PyQt5.QtGui import QColor, QImage, QPainter
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor("#24252b" if not variant % 2 else "#292635"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QColor("#43414e"))
    for x in range(0, width, 32):
        painter.drawLine(x, 0, x, height)
    for y in range(0, height, 32):
        painter.drawLine(0, y, width, y)
    painter.setPen(QColor("#c5bcff"))
    painter.setBrush(QColor("#8b78e8" if not variant % 2 else "#dbac70"))
    painter.drawRoundedRect(width // 5, height // 4, width * 3 // 5, height // 2, 24, 24)
    painter.setBrush(QColor("#e7e2ff"))
    painter.drawEllipse(width // 2 - 25, height // 2 - 25, 50, 50)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


class FixtureError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status
        self.code = "MOCK_QA_ONLY"


class FakeMoriClient:
    """The synchronous MoriClient contract with an in-memory mock transport."""

    instances = []

    def __init__(self, base_url="https://qa.example.invalid", timeout=30):
        self.base_url = "https://qa.example.invalid"
        self.timeout = timeout
        self.user = dict(USER)
        self.user_id = USER["id"]
        self.auth_enabled = True
        self.lock = threading.RLock()
        self.requests = []
        self._counter = 0
        self._assets = []
        self._bytes = {}
        self._jobs = []
        self._due = {}
        self._commands = []
        self._conversations = {}
        self._events = {}
        self._turn_inputs = {}
        self.add_asset(pattern_png(0), "Krita 集成测试 · 参考图")
        type(self).instances.append(self)

    def _id(self, kind):
        self._counter += 1
        return str(uuid.uuid5(uuid.NAMESPACE_DNS, "mori-krita-qa:%s:%s" % (kind, self._counter)))

    def add_asset(self, encoded, name, **extra):
        from PyQt5.QtGui import QImage
        image = QImage.fromData(encoded)
        if image.isNull():
            raise FixtureError("测试素材不是图片")
        identifier = self._id("asset")
        asset = {"id": identifier, "filename": identifier + ".png", "url": "/media/" + identifier + ".png",
                 "name": name, "width": image.width(), "height": image.height(), "size": len(encoded),
                 "createdAt": now(), **extra}
        self._assets.append(asset)
        self._bytes[asset["url"]] = bytes(encoded)
        return copy.deepcopy(asset)

    def _advance(self):
        clock = time.monotonic()
        for job in self._jobs:
            due = self._due[job["id"]]
            if job["status"] in ("completed", "cancelled", "failed"):
                continue
            if clock >= due:
                for index in range(job["count"]):
                    asset = self.add_asset(pattern_png(index + len(self._jobs)),
                                           "模拟生成结果 · " + job["prompt"][:16],
                                           model=job["model"], prompt=job["prompt"], jobId=job["id"])
                    job["assetIds"].append(asset["id"])
                job.update(status="completed", completed=len(job["assetIds"]), finishedAt=now())
            elif clock >= due - .55:
                job["status"] = "running"
        for conversation in self._conversations.values():
            for turn in conversation["turns"]:
                if turn["status"] not in ("queued", "running"):
                    continue
                linked = [j for j in self._jobs if j["id"] in turn["jobIds"]]
                if linked and all(j["status"] == "completed" for j in linked):
                    turn["status"] = "completed"
                    message = {"id": self._id("message"), "role": "assistant", "turnId": turn["id"],
                               "content": "测试图片已生成，并交由 Krita 插件写入图层。此为隔离模拟，没有调用付费模型。", "createdAt": now()}
                    conversation["messages"].append(message)
                    events = self._events[conversation["id"]]
                    events.append({"seq": len(events) + 1, "type": "message", "turnId": turn["id"], "message": message})
                    events.append({"seq": len(events) + 1, "type": "turn_completed", "turnId": turn["id"]})
                else:
                    turn["status"] = "running"

    def session(self):
        return {"enabled": True, "user": copy.deepcopy(self.user), "providers": {"google": False}}

    def login(self, email, password):
        self.user = dict(USER)
        self.user_id = USER["id"]
        return {"user": dict(self.user)}

    def logout(self):
        self.clear_session()
        return {"ok": True}

    def clear_session(self):
        self.user_id = None
        self.user = None

    def config(self):
        return {"models": [{"id": "gpt-image-2.5", "name": "GPT Image 2.5", "sub": "隔离测试模型",
                            "provider": "openai", "badge": "QA", "color": "#8b78e8",
                            "aspects": ["1:1", "4:3", "3:4"], "qualities": ["high"]}],
                "configured": {"openai": True}, "connection": {"openai": {"state": "ok"}},
                "baseUrl": "隔离测试 · 没有外部模型调用"}

    def credits(self):
        return {"config": {"version": 1, "enforcement": True, "signupBonus": 0, "pointsPerYuan": 1,
                           "prices": [{"model": "gpt-image-2.5", "resolution": "1K", "quality": "high", "points": 0}], "updatedAt": now()},
                "balance": {"userId": USER["id"], "available": 1000, "frozen": 0, "total": 1000,
                            "createdAt": now(), "updatedAt": now()},
                "ledger": {"items": [], "nextCursor": None}, "isAdmin": False,
                "models": [{"id": "gpt-image-2.5", "name": "GPT Image 2.5"}],
                "usage": {"consumed": 0, "refunded": 0, "granted": 1000, "deducted": 0}}

    def assets(self):
        with self.lock:
            self._advance()
            return copy.deepcopy(self._assets)

    def jobs(self):
        with self.lock:
            self._advance()
            return copy.deepcopy(self._jobs)

    def upload_image(self, png_bytes, name, mime=None, progress=None):
        with self.lock:
            return self.add_asset(png_bytes, name)

    def generate(self, settings, node_id, references, request_id, origin=None):
        with self.lock:
            existing = next((j for j in self._jobs if j.get("clientRequestId") == request_id), None)
            if existing:
                return copy.deepcopy(existing)
            job = {**SETTINGS, **settings, "id": self._id("job"), "nodeId": node_id, "references": list(references),
                   "clientRequestId": request_id, "status": "queued", "assetIds": [], "completed": 0,
                   "createdAt": now()}
            if origin:
                job["origin"] = origin
            self._jobs.append(job)
            self._due[job["id"]] = time.monotonic() + .9
            self._commands.append({"id": self._id("command"), "type": "generation", "jobId": job["id"],
                                   "nodeId": node_id, "references": list(references), "settings": {**SETTINGS, **settings},
                                   "label": "集成模拟生成", "conversationId": (origin or {}).get("conversationId"), "createdAt": now()})
            return copy.deepcopy(job)

    def create_conversation(self, title):
        with self.lock:
            conversation = {"id": self._id("conversation"), "title": title, "messages": [], "turns": [],
                            "createdAt": now(), "updatedAt": now()}
            self._conversations[conversation["id"]] = conversation
            self._events[conversation["id"]] = []
            return copy.deepcopy(conversation)

    def send_message(self, conversation_id, message, references, image_settings, agent_model, context, request_id):
        with self.lock:
            conversation = self._conversations[conversation_id]
            existing = next((t for t in conversation["turns"] if self._turn_inputs[t["id"]] == request_id), None)
            if existing:
                return copy.deepcopy(existing)
            turn = {"id": self._id("turn"), "conversationId": conversation_id, "status": "queued", "jobIds": [], "createdAt": now()}
            self._turn_inputs[turn["id"]] = request_id
            conversation["turns"].append(turn)
            conversation["messages"].append({"id": self._id("message"), "role": "user", "content": message,
                                             "references": references, "turnId": turn["id"], "createdAt": now()})
            job = self.generate({**image_settings, "prompt": message}, self._id("node"), references,
                                "agent-" + request_id, {"turnId": turn["id"], "conversationId": conversation_id, "type": "creator"})
            turn["jobIds"].append(job["id"])
            events = self._events[conversation_id]
            events.extend([{"seq": 1, "type": "tool_started", "turnId": turn["id"], "name": "read_canvas", "toolCallId": "qa-read"},
                           {"seq": 2, "type": "tool_completed", "turnId": turn["id"], "toolCallId": "qa-read"},
                           {"seq": 3, "type": "tool_started", "turnId": turn["id"], "name": "generate_image", "toolCallId": "qa-gen"}])
            return copy.deepcopy(turn)

    def conversation(self, conversation_id):
        with self.lock:
            self._advance()
            return copy.deepcopy(self._conversations[conversation_id])

    def events(self, conversation_id, after=0):
        with self.lock:
            self._advance()
            return copy.deepcopy([e for e in self._events[conversation_id] if e["seq"] > after])

    def assistant_commands(self):
        with self.lock:
            self._advance()
            return copy.deepcopy(self._commands)

    def download_asset(self, asset_url):
        with self.lock:
            if asset_url not in self._bytes:
                raise FixtureError("模拟素材不存在", 404)
            return self._bytes[asset_url]

    def cancel_job(self, job_id):
        with self.lock:
            job = next(j for j in self._jobs if j["id"] == job_id)
            job["status"] = "cancelled"
            return copy.deepcopy(job)

    def cancel_turn(self, turn_id):
        with self.lock:
            for conversation in self._conversations.values():
                for turn in conversation["turns"]:
                    if turn["id"] == turn_id:
                        turn["status"] = "cancelled"
                        for job_id in turn["jobIds"]:
                            self.cancel_job(job_id)
                        return copy.deepcopy(turn)
        raise FixtureError("模拟轮次不存在", 404)

    def request(self, method, path, payload=None, request_id=None):
        status, _, data = self.raw_request(method, path, json.dumps(payload).encode("utf-8") if payload is not None else None)
        value = json.loads(data)
        if status >= 400:
            raise FixtureError(value.get("error", "模拟接口错误"), status)
        return value

    def raw_request(self, method, path, body=None, content_type=None):
        with self.lock:
            self.requests.append((method, urlsplit(path).path))
            self._advance()
            payload = json.loads(body or b"{}")
            route = urlsplit(path)
            parts = route.path.strip("/").split("/")
            status = 200
            if route.path.startswith("/media/"):
                return 200, {"content-type": "image/png"}, self.download_asset(route.path)
            if route.path == "/api/auth/session": value = self.session()
            elif route.path == "/api/auth/login": value = self.login(payload.get("email"), payload.get("password"))
            elif route.path == "/api/auth/logout": value = self.logout()
            elif route.path == "/api/config": value = self.config()
            elif route.path == "/api/account":
                value = {"profile": {**USER, "avatar": None, "hasPassword": True},
                         "preferences": {"language": "zh-CN", "creatorLanguage": "auto", "shortcutPreset": "win", "shortcuts": {}}}
            elif route.path == "/api/credits": value = self.credits()
            elif route.path == "/api/credits/quote": value = {"enforcement": True, "unitPoints": 0, "totalPoints": 0, "available": 1000, "count": payload.get("count", 1), "configVersion": 1}
            elif route.path == "/api/assets":
                value = self.assets() if method == "GET" else self.upload_image(base64.b64decode(payload["dataUrl"].split(",", 1)[1]), payload.get("name", "测试上传"))
            elif route.path == "/api/jobs": value = self.jobs()
            elif route.path == "/api/generate":
                value = self.generate(payload, payload["nodeId"], payload.get("references", []), payload["clientRequestId"])
                status = 202
            elif route.path == "/api/assistant/commands": value = self.assistant_commands()
            elif route.path == "/api/creator/config": value = {"models": [{"id": "qa-chat", "name": "Mori 测试智能体", "provider": "chat"}], "configured": {"chat": True}, "connection": {"state": "ok"}, "defaultModel": "qa-chat"}
            elif route.path == "/api/creator/conversations":
                value = copy.deepcopy(list(self._conversations.values())) if method == "GET" else self.create_conversation(payload.get("title", "集成测试"))
            elif len(parts) == 4 and parts[:3] == ["api", "creator", "conversations"]: value = self.conversation(parts[3])
            elif len(parts) == 5 and parts[:3] == ["api", "creator", "conversations"] and parts[4] == "messages":
                value = self.send_message(parts[3], payload["message"], payload.get("references", []), payload.get("imageSettings", SETTINGS), payload.get("agentModel", "qa-chat"), payload.get("context", {}), payload["clientRequestId"])
                status = 202
            elif len(parts) == 5 and parts[:3] == ["api", "creator", "conversations"] and parts[4] == "events": value = self.events(parts[3], int(parse_qs(route.query).get("after", ["0"])[0]))
            elif len(parts) == 5 and parts[:3] == ["api", "creator", "turns"] and parts[4] == "cancel": value = self.cancel_turn(parts[3])
            elif len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel": value = self.cancel_job(parts[2])
            elif route.path == "/api/tool-library": value = {"tools": [], "categories": []}
            elif route.path == "/api/tool-runs": value = []
            elif route.path == "/api/check": value = {"openai": {"state": "ok"}}
            else:
                value, status = {"error": "模拟接口未实现", "code": "MOCK_QA_ONLY"}, 404
            return status, {"content-type": "application/json; charset=utf-8"}, json.dumps(value, ensure_ascii=False).encode("utf-8")
