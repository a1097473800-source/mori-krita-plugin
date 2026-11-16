"""Worker-only generation for the native selection composer.

The owner takes a detached Krita snapshot on the GUI thread before constructing
the task. This module never finds the current document and never retries a paid
POST. All result writes go through the gateway's GUI-thread dispatcher using the
snapshot's original target.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
from uuid import uuid4


ACTIVE = frozenset(("queued", "running"))
TERMINAL = frozenset(("completed", "partial", "failed", "cancelled", "interrupted"))
DEFAULT_ASPECTS = ("1:1", "2:1", "3:2", "4:3", "5:4", "16:9", "21:9",
                   "1:2", "2:3", "3:4", "4:5", "9:16", "9:21")


class SelectionTaskError(RuntimeError):
    def __init__(self, message, code="SELECTION_TASK_ERROR", status=0):
        super().__init__(message)
        self.code = code
        self.status = status


def selection_aspect(width, height, available=None):
    """Choose a supported ratio without changing the frozen result placement."""
    if width <= 0 or height <= 0:
        raise SelectionTaskError("当前选区为空，请重新选择。", "EMPTY_SELECTION")
    candidates = []
    for aspect in available or DEFAULT_ASPECTS:
        try:
            first, second = str(aspect).split(":")
            ratio = float(first) / float(second)
            if ratio > 0 and math.isfinite(ratio):
                candidates.append((abs(math.log(ratio / (width / height))), str(aspect)))
        except (ValueError, ZeroDivisionError, TypeError):
            continue
    if not candidates:
        raise SelectionTaskError("当前模型没有可用的图像比例。", "INVALID_ASPECT")
    return min(candidates, key=lambda item: item[0])[1]


def selection_settings(snapshot, payload):
    message = str(payload.get("message", "")).strip()
    if not message or len(message) > 16000:
        raise SelectionTaskError("请输入 1–16000 字的修改要求。", "INVALID_MESSAGE")
    model = str(payload.get("model") or "gpt-image-2.5")
    try:
        count = int(payload.get("count", 1))
    except (TypeError, ValueError):
        raise SelectionTaskError("生成数量无效。", "INVALID_SETTINGS")
    maximum = 50 if model == "gemini-nano-banana-2.1" else 4
    if isinstance(payload.get("count"), bool) or not 1 <= count <= maximum:
        raise SelectionTaskError("当前模型一次最多生成 %d 张。" % maximum, "INVALID_SETTINGS")
    quality = str(payload.get("quality") or "high")
    resolution = str(payload.get("resolution") or "1K")
    if quality not in ("low", "medium", "high", "xhigh", "max") or resolution not in ("1K", "2K", "4K"):
        raise SelectionTaskError("图像质量或分辨率无效。", "INVALID_SETTINGS")
    aspect = str(payload.get("aspect") or "auto")
    if aspect == "auto":
        target = snapshot.target
        aspect = selection_aspect(target.width, target.height, payload.get("available_aspects"))
    elif aspect not in DEFAULT_ASPECTS and aspect not in (payload.get("available_aspects") or []):
        raise SelectionTaskError("图像比例无效。", "INVALID_SETTINGS")
    return {"model": model, "prompt": message, "aspect": aspect, "resolution": resolution,
            "quality": quality, "count": count}


class SelectionTask:
    def __init__(self, gateway, snapshot, payload, notify=None, cancel_event=None, *, prepare=None,
                 poll_interval=1.2, timeout=900, conversation_id=None, task_id=None):
        self.gateway = gateway
        self.snapshot = snapshot
        self.payload = dict(payload)
        self.notify = notify or (lambda message: None)
        self.cancel_event = cancel_event or threading.Event()
        self.prepare = prepare
        self.poll_interval = max(0.001, min(float(poll_interval), 10.0))
        self.timeout = max(0.01, float(timeout))
        self.conversation_id = conversation_id or self.payload.get("conversation_id")
        self.task_id = task_id or uuid4().hex
        self.request_id = self.payload.get("client_request_id") or uuid4().hex
        self.node_id = "selection-" + self.task_id
        self.turn_id = None
        self.client = None
        self.epoch = None
        self.user_id = None
        self.job_ids = []
        self.jobs = {}
        self.asset_ids = []
        self.delivered = []
        self.reference_id = None
        self.selection_mapping = None
        self.mask_asset_id = None
        self._mask_bytes = None
        self._agent_image_fallback = False
        self._image_submission_attempted = False
        self.started = None
        self.submission_attempted = False
        self.cancel_sent = set()
        self.last_answer = ""
        self.last_status = None
        self._submitted_at = None
        self._timings = dict.fromkeys(("prepare_ms", "reference_upload_ms", "mask_upload_ms",
                                      "session_ms", "agent_setup_ms", "submit_ms", "wait_ms",
                                      "download_ms", "import_ms"), 0.0)

    def _timed_call(self, stage, function, *arguments, **keywords):
        """Measure only work performed here; never infer a remote model duration."""
        started = time.monotonic()
        try:
            return function(*arguments, **keywords)
        finally:
            self._timings[stage] += max(0.0, time.monotonic() - started) * 1000

    def _timing_summary(self):
        read_ms = max(0.0, float(getattr(self.snapshot, "read_ms", 0.0)))
        elapsed_ms = max(0.0, time.monotonic() - self.started) * 1000 if self.started is not None else 0.0
        return {**{key: round(value, 3) for key, value in self._timings.items()},
                "read_ms": round(read_ms, 3), "total_ms": round(read_ms + elapsed_ms, 3)}

    def _emit_wait_status(self, agent=False):
        statuses = {self.jobs.get(identifier, {}).get("status") for identifier in self.job_ids}
        if "running" in statuses:
            message = "服务器正在生成选区修改结果"
        elif "queued" in statuses:
            message = "生成任务正在排队"
        elif self.job_ids and statuses and statuses <= TERMINAL:
            message = "生成任务已结束，正在等待结果同步"
        elif agent and not self.job_ids:
            message = "智能体正在回复"
        else:
            message = "正在等待服务器生成结果"
        beginning = self._submitted_at if self._submitted_at is not None else self.started
        waited = max(0, int(time.monotonic() - beginning)) if beginning is not None else 0
        self._emit("%s · 已等待 %d 秒" % (message, waited))

    def _emit(self, message):
        try:
            self.notify(str(message))
        except Exception:
            pass  # A UI callback must never cause a paid request to be replayed.

    def _check(self):
        gateway = self.gateway
        if getattr(gateway, "closed", False):
            raise SelectionTaskError("插件连接已关闭，任务结果仍可在创作记录中查看。", "CONNECTION_CLOSED")
        gateway._sync_identity()
        if gateway.epoch != self.epoch or gateway.client.user_id != self.user_id:
            raise SelectionTaskError("账号会话已改变，已停止此轮选区操作。", "SESSION_CHANGED", 401)
        checker = getattr(self.client, "assert_session_current", None)
        if checker:
            checker()

    def _summary(self, status, error=None):
        if self.delivered:
            success = "已将 %d 张结果加入原选区的新图层。" % len(self.delivered)
            if status != "completed":
                success = "已保留 %d 张完成结果。" % len(self.delivered)
        elif status == "completed":
            if self.payload.get("mode", "edit") == "chat" and not self.job_ids:
                success = "智能体已回复，本轮未生成图片。需要改图请切换「直接修改」。"
                if self.last_answer:
                    success += "\n" + self.last_answer
            else:
                success = self.last_answer or "本轮对话已完成。"
        elif status == "cancelled":
            success = "已停止本轮操作，已完成的图片会保留。"
        elif status == "timeout":
            success = "等待时间较长，请从创作记录查看任务状态；没有重新提交。"
        elif status == "uncertain":
            success = "请求可能已提交，请先查看创作记录；没有自动重发。"
        else:
            success = error or "本轮操作未完成。"
        return {"status": status, "task_id": self.task_id, "request_id": self.request_id,
                "node_id": self.node_id, "job_ids": list(self.job_ids),
                "asset_ids": list(self.asset_ids), "delivered_asset_ids": list(self.delivered),
                "reference_id": self.reference_id, "conversation_id": self.conversation_id,
                "turn_id": self.turn_id, "message": self.last_answer,
                "success_message": success, "error": error,
                "epoch": self.epoch, "target": self.snapshot.target,
                "timings_ms": self._timing_summary(),
                "agent_image_fallback": self._agent_image_fallback}

    def _remember_job(self, job):
        if not isinstance(job, dict) or not job.get("id"):
            return
        identifier = job["id"]
        if identifier not in self.job_ids:
            self.job_ids.append(identifier)
        self.jobs[identifier] = job

    def _frame_instructions(self, mapping):
        x, y, width, height = mapping.selection_rect_in_frame
        return (
            "这是 Krita 的局部修改任务。第一张参考图是包含选区周围上下文的完整画幅，"
            "请返回同一完整画幅，保持物体的位置、原有比例、视角和与周围边缘的衔接，"
            "不要把选区裁成新构图，不要缩放、平移或重新居中整个物体。"
            "除非用户明确要求改变形状，否则保持原轮廓。"
            "参考画幅为 %d×%d 像素；本次可修改区域的边界为 "
            "x=%.3f、y=%.3f、宽=%.3f、高=%.3f（左上角为原点，像素坐标）。"
            "如提供编辑蒙版，仅修改蒙版透明区域；选区外内容只作为定位参考并须保留。"
            "请按 %d×%d 的完整画幅比例输出。\n\n用户修改要求：\n" %
            (mapping.input_width, mapping.input_height, x, y, width, height,
             mapping.output_width, mapping.output_height))

    def _upload_reference(self, settings):
        self._emit("正在准备选区参考图…")
        prepare = self.prepare
        context = getattr(self.snapshot, "context_rect", None)
        if prepare is None and context is not None:
            from .selection_edit import prepare_selection_edit
            prepared = self._timed_call("prepare_ms", prepare_selection_edit, self.snapshot,
                                        aspect=settings["aspect"], resolution=settings["resolution"],
                                        mode=self.payload.get("reference_mode", "fast"))
        else:
            if prepare is None:
                from .krita_bridge import prepare_reference
                prepare = prepare_reference
            prepared = self._timed_call("prepare_ms", prepare, self.snapshot,
                                        mode=self.payload.get("reference_mode", "fast"))
        mapping = getattr(prepared, "mapping", None)
        if mapping is not None:
            mapping.assert_target(self.snapshot.target)
            self.selection_mapping = mapping
            self._mask_bytes = prepared.mask_bytes
            reference = prepared.reference
            settings["prompt"] = self._frame_instructions(mapping) + settings["prompt"]
            if self.payload.get("mode", "edit") == "chat" and len(settings["prompt"]) > 16000:
                raise SelectionTaskError("修改要求加上选区定位说明后超过 16000 字，请缩短修改要求。", "INVALID_MESSAGE")
        elif context is not None:
            raise SelectionTaskError("选区上下文缺少对应的坐标映射，已停止生成。", "INVALID_REFERENCE")
        else:
            reference = prepared
        self._check()
        if self.cancel_event.is_set():
            return None
        data = getattr(reference, "image_bytes", None)
        if data is None:
            data = reference.png_bytes
        mime = getattr(reference, "mime", "image/png")
        asset = self._upload_reference_bytes(data, mime, "Krita 选区参考")
        self.reference_id = asset["id"]
        if (mapping is not None and self.payload.get("mode", "edit") == "edit"
                and settings["model"].startswith("gpt-image-")):
            if not self._attach_image_mask(settings):
                return None
        if mapping is not None:
            self._check()
            if self.cancel_event.is_set():
                return None
            self.gateway.invoke("selection_mapping", {
                "selection_mapping": mapping, "target": self.snapshot.target,
                "task_id": self.task_id, "epoch": self.epoch})
            self._check()
        return asset

    def _attach_image_mask(self, settings):
        """Attach a direct image edit lazily; agent settings never receive it."""
        mapping = self.selection_mapping
        if mapping is None or not settings["model"].startswith("gpt-image-"):
            return True
        self._check()
        if self.cancel_event.is_set():
            return False
        if self.mask_asset_id is None:
            if not self._mask_bytes:
                raise SelectionTaskError("选区修改缺少对应的编辑蒙版。", "INVALID_REFERENCE")
            mask = self._upload_reference_bytes(self._mask_bytes, "image/png", "Krita 选区编辑蒙版",
                                                timing_stage="mask_upload_ms")
            self.mask_asset_id = mask["id"]
        self._check()
        if self.cancel_event.is_set():
            return False
        settings["edit"] = {"size": "%dx%d" % (mapping.output_width, mapping.output_height),
                            "maskAssetId": self.mask_asset_id}
        return True

    def _upload_reference_bytes(self, data, mime, name, *, timing_stage="reference_upload_ms"):
        """Upload/cache context and mask independently, with the same session fence."""
        self._check()
        digest = hashlib.sha256(data).hexdigest()
        gateway = self.gateway
        with gateway.lock:
            asset = getattr(gateway, "reference_assets", {}).get(digest)
        if asset is None:
            def progress(sent, total):
                if total and not self.cancel_event.is_set():
                    label = "正在上传选区编辑蒙版" if timing_stage == "mask_upload_ms" else "正在上传选区参考"
                    self._emit(label + " · %d%%" % min(100, int(sent * 100 / total)))
            self._emit("正在上传选区编辑蒙版…" if timing_stage == "mask_upload_ms" else "正在上传选区参考图…")
            asset = self._timed_call(timing_stage, self.client.upload_image,
                                     data, name, mime=mime, progress=progress)
        self._check()
        if not isinstance(asset, dict) or not asset.get("id"):
            raise SelectionTaskError("参考图片上传未返回素材标识。", "INVALID_REFERENCE")
        with gateway.session_lock:
            self._check()
            with gateway.lock:
                if hasattr(gateway, "reference_assets"):
                    gateway.reference_assets[digest] = asset
                if hasattr(gateway, "uploaded_assets"):
                    gateway.uploaded_assets[asset["id"]] = asset
                if hasattr(gateway, "assets_by_id"):
                    gateway.assets_by_id[asset["id"]] = asset
                if asset.get("url"):
                    gateway._cache_media(asset["url"], data, mime, self.epoch)
                gateway._invalidate_reads()
        return asset

    def _deliver(self):
        wanted = []
        for job_id in self.job_ids:
            for asset_id in self.jobs.get(job_id, {}).get("assetIds", []):
                if asset_id not in self.asset_ids:
                    self.asset_ids.append(asset_id)
                if asset_id not in self.delivered:
                    wanted.append(asset_id)
        wanted = list(dict.fromkeys(wanted))
        if not wanted:
            return
        self._check()
        self._emit("正在下载生成结果…")
        assets = {item["id"]: item for item in self._timed_call("download_ms", self.client.assets)
                  if isinstance(item, dict) and item.get("id")}
        self._check()
        for asset_id in wanted:
            asset = assets.get(asset_id)
            if not asset or not asset.get("url"):
                continue  # Assets and jobs can be independently timed snapshots.
            cached = self.gateway._get_media(asset["url"])
            image = cached[1] if cached else self._timed_call("download_ms", self.client.download_asset, asset["url"])
            self._check()
            self.gateway._cache_media(asset["url"], image, asset.get("mime", "image/png"), self.epoch)
            self._emit("正在写入 Krita 图层…")
            self._timed_call("import_ms", self.gateway.invoke, "selection_result", {
                "bytes": image, "asset": asset, "target": self.snapshot.target,
                "task_id": self.task_id, "epoch": self.epoch,
                "conversation_id": self.conversation_id,
                "selection_mapping": self.selection_mapping})
            self._check()
            self.delivered.append(asset_id)
            self._emit("已将 %d 张结果加入原选区。" % len(self.delivered))

    def _refresh_jobs(self):
        self._check()
        allowed = set(self.job_ids)
        for job in self._timed_call("wait_ms", self.client.jobs):
            if isinstance(job, dict) and job.get("id") in allowed:
                self._remember_job(job)
        self._check()

    def _cancel(self):
        self._check()
        self._emit("正在停止本轮操作…")
        if self.turn_id and not self._agent_image_fallback and self.turn_id not in self.cancel_sent:
            self.cancel_sent.add(self.turn_id)
            result = self.client.cancel_turn(self.turn_id)
            for job_id in (result or {}).get("jobIds", []):
                if job_id not in self.job_ids:
                    self.job_ids.append(job_id)
        elif not self.turn_id or self._agent_image_fallback:
            for job_id in self.job_ids:
                if job_id not in self.cancel_sent and self.jobs.get(job_id, {}).get("status") in ACTIVE:
                    self.cancel_sent.add(job_id)
                    self._remember_job(self.client.cancel_job(job_id))
        self._check()
        if self.job_ids:
            self._refresh_jobs()
            self._deliver()
        return self._summary("cancelled")

    def _terminal_status(self):
        if not self.job_ids or any(self.jobs.get(identifier, {}).get("status") not in TERMINAL for identifier in self.job_ids):
            return None
        if any(asset not in self.delivered for identifier in self.job_ids
               for asset in self.jobs.get(identifier, {}).get("assetIds", [])):
            return None
        statuses = {self.jobs[identifier].get("status") for identifier in self.job_ids}
        if statuses == {"completed"}:
            return "completed"
        if self.delivered:
            return "partial"
        if statuses == {"cancelled"}:
            return "cancelled"
        return "failed"

    def _wait(self):
        return self._timed_call("wait_ms", self.cancel_event.wait, self.poll_interval)

    def _expired(self):
        return time.monotonic() - self.started >= self.timeout

    def _run_edit(self, settings, reference):
        self._check()
        if self.cancel_event.is_set():
            return self._summary("cancelled")
        self._emit("智能体已完成理解，正在生成修改图片…" if self._agent_image_fallback else "正在提交选区修改…")
        self.submission_attempted = True
        self._image_submission_attempted = True
        self._remember_job(self._timed_call("submit_ms", self.client.generate,
                                            settings, self.node_id, [reference["id"]], self.request_id))
        self._submitted_at = time.monotonic()
        self._check()
        if not self.job_ids:
            raise SelectionTaskError("生成请求没有返回任务标识，请先查看创作记录。", "INVALID_JOB")
        self.gateway._invalidate_reads()
        while not self._expired():
            if self.cancel_event.is_set():
                return self._cancel()
            self._deliver()
            terminal = self._terminal_status()
            if terminal:
                error = next((job.get("error") for job in self.jobs.values() if job.get("error")), None)
                return self._summary(terminal, error)
            self._emit_wait_status()
            self._wait()
            self._refresh_jobs()
        return self._summary("timeout")

    def _agent_model(self):
        config = self.client.creator_config()
        self._check()
        models = config.get("models", [])
        requested = self.payload.get("agent_model") or config.get("defaultModel")
        configured = config.get("configured", {})
        available = [model for model in models if not configured or configured.get(model.get("provider"))]
        selected = next((model for model in available if model.get("id") == requested), None)
        if selected is None and self.payload.get("agent_model"):
            raise SelectionTaskError("选中的智能体模型不可用，请重新选择。", "INVALID_AGENT_MODEL")
        selected = selected or (available[0] if available else None)
        if selected is None:
            raise SelectionTaskError(config.get("connection", {}).get("error") or "服务器尚未配置可用的智能体模型。", "NO_AGENT_MODEL")
        return selected["id"]

    def _run_chat(self, settings, reference):
        agent_model = self._timed_call("agent_setup_ms", self._agent_model)
        if self.cancel_event.is_set():
            return self._summary("cancelled")
        if not self.conversation_id:
            self._check()
            conversation = self._timed_call("agent_setup_ms", self.client.create_conversation,
                                             str(self.payload.get("message", settings["prompt"]))[:36])
            self._check()
            self.conversation_id = conversation.get("id")
        if not self.conversation_id:
            raise SelectionTaskError("未能建立本轮对话。", "INVALID_CONVERSATION")
        target = self.snapshot.target
        context = {"nodes": [{"id": self.node_id, "label": "Krita 当前选区", "assetId": reference["id"],
                               "position": {"x": target.x, "y": target.y}}],
                   "edges": [], "selectedIds": [self.node_id]}
        self._check()
        if self.cancel_event.is_set():
            return self._summary("cancelled")
        self._emit("智能体正在理解选区修改要求…")
        self.submission_attempted = True
        turn = self._timed_call("submit_ms", self.client.send_message,
                                self.conversation_id, settings["prompt"], [reference["id"]],
                                settings, agent_model, context, self.request_id)
        self._submitted_at = time.monotonic()
        self._check()
        self.turn_id = turn.get("id")
        for job_id in turn.get("jobIds", []):
            if job_id not in self.job_ids:
                self.job_ids.append(job_id)
        if not self.turn_id:
            raise SelectionTaskError("智能体没有返回任务标识，请先查看对话记录。", "INVALID_TURN")
        self.gateway._invalidate_reads()
        while not self._expired():
            self._check()
            if self.cancel_event.is_set():
                return self._cancel()
            conversation = self._timed_call("wait_ms", self.client.conversation, self.conversation_id)
            self._check()
            current = next((item for item in conversation.get("turns", []) if item.get("id") == self.turn_id), turn)
            for job_id in current.get("jobIds", []):
                if job_id not in self.job_ids:
                    self.job_ids.append(job_id)
            answers = [item.get("content", "") for item in conversation.get("messages", [])
                       if item.get("role") == "assistant" and item.get("turnId") == self.turn_id]
            if answers:
                self.last_answer = str(answers[-1])
            if self.job_ids:
                self._refresh_jobs()
                self._deliver()
            turn_status = current.get("status")
            job_status = self._terminal_status()
            if turn_status in TERMINAL and (not self.job_ids or job_status):
                if (turn_status == "completed" and not self.job_ids and not self.delivered
                        and self.payload.get("expect_image") is True):
                    resolution = self._finish_expected_image(settings, reference)
                    if resolution is not None:
                        return resolution
                    self._emit_wait_status(agent=True)
                    self._wait()
                    continue
                if turn_status == "completed":
                    status = job_status or "completed"
                else:
                    status = "partial" if self.delivered else turn_status
                return self._summary(status, current.get("error"))
            self._emit_wait_status(agent=True)
            self._wait()
        return self._summary("timeout")

    def _finish_expected_image(self, settings, reference):
        """Resolve a completed text-only agent turn before admitting one image.

        The server persists an image job before adding it to turn.jobIds. Its
        own cancellation route also checks origin.turnId to recover that gap.
        Follow the same association check so an existing or uncertain image is
        never mistaken for an agent that only returned a textual plan.
        """
        self._check()
        if self.cancel_event.is_set():
            return self._summary("cancelled")
        if self._expired():
            return self._summary("timeout")
        confirmed = self._timed_call("wait_ms", self.client.conversation, self.conversation_id)
        self._check()
        current = next((turn for turn in confirmed.get("turns", []) if turn.get("id") == self.turn_id), None)
        if current is None:
            raise SelectionTaskError("暂时无法确认智能体任务状态；没有自动补发图片请求，请查看创作记录。", "INVALID_TURN")
        for job_id in current.get("jobIds", []):
            if job_id not in self.job_ids:
                self.job_ids.append(job_id)
        if self.cancel_event.is_set():
            return self._cancel() if self.job_ids else self._summary("cancelled")
        related = self._timed_call("wait_ms", self.client.jobs)
        self._check()
        if not isinstance(related, list):
            raise SelectionTaskError("暂时无法核实图像任务；没有自动补发图片请求，请查看创作记录。", "INVALID_JOB")
        for job in related:
            if not isinstance(job, dict):
                continue
            belongs = ((job.get("origin") or {}).get("turnId") == self.turn_id or
                       str(job.get("clientRequestId", "")).startswith("creator-" + self.turn_id + "-"))
            if belongs and not job.get("id"):
                raise SelectionTaskError("本轮可能已有图像任务；没有自动补发图片请求，请查看创作记录。", "INVALID_JOB")
            if belongs or job.get("id") in self.job_ids:
                self._remember_job(job)
        if self.cancel_event.is_set():
            return self._cancel() if self.job_ids else self._summary("cancelled")
        if self._expired():
            return self._summary("timeout")
        if self.job_ids:
            return None  # Track every discovered job, including failed ones.
        status = current.get("status")
        if status != "completed":
            return self._summary(status, current.get("error")) if status in TERMINAL else None
        if current.get("error"):
            return self._summary("failed", current["error"])
        if self._agent_image_fallback:
            raise SelectionTaskError("本轮图像请求已经处理，没有重复提交；请查看创作记录。", "INVALID_JOB")
        self._agent_image_fallback = True
        image_settings = dict(settings)
        if not self._attach_image_mask(image_settings):
            return self._summary("cancelled")
        self._check()
        if self._expired():
            return self._summary("timeout")
        return self._run_edit(image_settings, reference)

    def _open_channel(self):
        with self.gateway.session_lock:
            self.gateway._sync_identity()
            self.epoch = self.gateway.epoch
            self.user_id = self.gateway.client.user_id
            # The GUI freezes these when the user presses Submit. A worker
            # can start later, after a queued task or account change; never
            # let that older click upload its snapshot to a newer account.
            if (("expected_epoch" in self.payload and self.payload["expected_epoch"] != self.epoch)
                    or ("expected_user_id" in self.payload and self.payload["expected_user_id"] != self.user_id)):
                raise SelectionTaskError("账号会话已改变，请重新确认选区后提交。", "SESSION_CHANGED", 401)
            if getattr(self.gateway.client, "auth_enabled", True) and not self.user_id:
                raise SelectionTaskError("请先在 Mori 面板登录账号，再修改选区。", "AUTH_REQUIRED", 401)
            self.client = self.gateway._channel()

    def run(self):
        try:
            self.started = time.monotonic()
            if self.cancel_event.is_set():
                return self._summary("cancelled")
            settings = selection_settings(self.snapshot, self.payload)
            mode = self.payload.get("mode", "edit")
            if mode not in ("edit", "chat"):
                raise SelectionTaskError("请选择直接修改或智能体模式。", "INVALID_MODE")
            self._timed_call("session_ms", self._open_channel)
            self._check()
            reference = self._upload_reference(settings)
            if reference is None or self.cancel_event.is_set():
                return self._summary("cancelled")
            return self._run_chat(settings, reference) if mode == "chat" else self._run_edit(settings, reference)
        except Exception as error:
            code = getattr(error, "code", "")
            uncertain = (self.submission_attempted and not self.job_ids
                         and (not self.turn_id or self._image_submission_attempted)
                         and code in ("TIMEOUT", "NETWORK_ERROR"))
            result = self._summary("uncertain" if uncertain else "failed", str(error)[:600])
            result["error_code"] = code or "SELECTION_TASK_ERROR"
            self._emit(result["success_message"] if uncertain else result["error"])
            return result


def run_selection_task(gateway, snapshot, payload, notify=None, cancel_event=None, *, prepare=None,
                       poll_interval=1.2, timeout=900, conversation_id=None, task_id=None):
    """Run once in a worker and return a serializable-status summary plus Target."""
    return SelectionTask(gateway, snapshot, payload, notify, cancel_event, prepare=prepare,
                         poll_interval=poll_interval, timeout=timeout,
                         conversation_id=conversation_id, task_id=task_id).run()
