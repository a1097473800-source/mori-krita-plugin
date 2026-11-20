"""One account-billed image request over Mori's existing API, never a model key.

Inputs are frozen before leaving the GUI thread. An uncertain submission is not
replayed; all generated assets remain in the ordinary Mori creation history.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from uuid import uuid4
from .reference_presets import PRESET_BY_ID


class RealtimeTaskError(Exception):
    def __init__(self, message, state="error"):
        super().__init__(message)
        self.state = state


def variation_value(settings):
    return max(0, min(100, int(settings.get("variation", 50))))


def reference_prompt(settings, snapshots, image_positions):
    purposes = []
    for binding in settings.get("reference_bindings", []):
        preset = PRESET_BY_ID.get(binding.get("role"))
        if preset is None:
            continue
        for index, snapshot in enumerate(snapshots):
            if (image_positions[index] is not None
                    and getattr(snapshot.target, "layer_id", "") == binding.get("layer_id")):
                purposes.append((preset[0], "Image %d — %s: %s" %
                                 (image_positions[index], preset[1], preset[3])))
                break
    composition = any(role == "composition" for role, _ in purposes)
    variation = variation_value(settings)
    if variation == 100:
        drawing = (
            "Create a new image from the user's keywords and the explicitly assigned reference images only. "
            "The drawing canvas is not provided as an input image. "
            "No uploaded image is the main drawing: every supplied image is an explicit reference "
            "with only the purpose assigned below. Explore new forms and compositions freely, "
            "while respecting those reference purposes and the user's instructions. ")
    else:
        if variation <= 20:
            freedom = "Make very small refinements. Preserve the main drawing's silhouette, viewpoint, object placement and proportions. "
        elif variation < 50:
            freedom = "Keep the main drawing's recognizable shape, viewpoint and layout. Refine materials and details; allow only restrained shape changes. "
        elif variation <= 70:
            freedom = "Use the main drawing as the starting point. Allow intentional changes to materials, finish, details and product shape while keeping the core subject recognizable. "
        else:
            freedom = "Treat the main drawing as loose inspiration. Explore substantial changes to its shape, materials and composition, guided by the user's keywords and explicit references. "
        drawing = (
        "This is a Krita live drawing task. Image 1 is the current visible canvas "
        "or the explicitly selected drawing layer. It is the main drawing. "
        + ("Use the explicitly assigned composition reference for framing. " if composition else "")
        + "Requested change amount: %d/100 (an instruction, not a measured similarity score). " % variation
        + freedom
        + "Return one complete image with the same aspect ratio as the main drawing. "
        "Keep the subject's identity unless the user assigns an identity reference or asks to change it. "
        "Additional images have only the purposes assigned below; a style, color, lighting or material "
        "reference must not replace the main subject or composition.\n")
    return (drawing
        + ("\nReference assignments:\n" + "\n".join(line for _, line in purposes) if purposes else "")
        + "\n\nUser instructions:\n" + settings["prompt"])


def _unchanged_canvas(snapshots, settings, expected_identity, previous_signature, cancel):
    """Zero change means lossless pixels, never a paid approximation."""
    from PyQt5.QtCore import QBuffer, QIODevice
    if cancel.is_set():
        raise RealtimeTaskError("已暂停原图预览。")
    if not snapshots:
        raise RealtimeTaskError("请先打开画布。")
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    if not snapshots[0].image.save(buffer, "PNG"):
        raise RealtimeTaskError("无法读取当前画布。")
    data = bytes(buffer.data())
    signature = "original:" + hashlib.sha256(data).hexdigest()
    if signature == previous_signature:
        return {"status": "unchanged", "signature": signature}
    return {"status": "completed", "bytes": data, "signature": signature,
            "target": snapshots[0].target, "identity": expected_identity,
            "elapsed_seconds": 0., "local_original": True}


def run_realtime_task(gateway, snapshots, settings, *, expected_identity,
                      cancel=None, notify=None, previous_signature=None,
                      prepare=None, poll_interval=1.2, timeout=900):
    cancel = cancel or threading.Event()
    notify = notify or (lambda state, message: None)
    started = time.monotonic()
    submitted = False
    job_id = None
    signature = None
    request_id = str(uuid4())
    epoch, user_id = expected_identity or (None, None)

    def check():
        if cancel.is_set():
            raise RealtimeTaskError("已暂停本机等待；已提交的任务可在 Mori 创作记录中查看。", "paused")
        if gateway.closed:
            raise RealtimeTaskError("Mori 连接已关闭。", "disconnected")
        gateway._sync_identity()
        if (gateway.epoch, gateway.client.user_id) != (epoch, user_id):
            raise RealtimeTaskError("账号已改变，已停止旧账号的实时任务。")
        if time.monotonic() - started > timeout:
            raise RealtimeTaskError("等待超时，请查看 Mori 创作记录；本次没有自动重发。", "timeout")
        if client is not None:
            assertion = getattr(client, "assert_session_current", None)
            if assertion:
                assertion()

    client = None
    try:
        variation = variation_value(settings)
        if variation == 0:
            return _unchanged_canvas(snapshots, settings, expected_identity, previous_signature, cancel)
        check()
        if not snapshots:
            raise RealtimeTaskError("请先打开画布。")
        if not str(settings.get("prompt", "")).strip():
            raise RealtimeTaskError("请先填写提示词。")
        if getattr(gateway.client, "auth_enabled", True) and not user_id:
            raise RealtimeTaskError("请先登录 Mori，实时模式使用该账号的积分。")
        with gateway.session_lock:
            check()
            client = gateway._channel()
        if prepare is None:
            from .krita_bridge import prepare_reference
            prepare = prepare_reference
        notify("preparing", "正在准备画面与参考图层…")
        assigned = {binding["layer_id"] for binding in settings.get("reference_bindings", [])
                    if binding.get("layer_id") and binding.get("role") in PRESET_BY_ID}
        # At 100 the main drawing is never prepared, hashed or uploaded, unless
        # the user explicitly assigns that very layer as a separate reference.
        conditioned = [(index, snapshot) for index, snapshot in enumerate(snapshots)
                       if variation < 100 or getattr(snapshot.target, "layer_id", None) in assigned]
        prepared = []
        reference_mode = settings.get("reference_mode", "original")
        for index, snapshot in conditioned:
            check()
            prepared.append(prepare(snapshot, mode=reference_mode))
        digests = [hashlib.sha256(item.image_bytes).hexdigest() for item in prepared]
        identity_settings = {**settings, "count": 1}
        signature = hashlib.sha256(json.dumps(
            {"settings": identity_settings, "images": digests},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if signature == previous_signature:
            return {"status": "unchanged", "signature": signature,
                    "elapsed_seconds": time.monotonic() - started}
        check()
        api_settings = {key: value for key, value in identity_settings.items() if key not in ("variation", "reference_mode")}
        estimate = client.quote(api_settings)
        check()
        if estimate.get("enforcement"):
            available, required = estimate.get("available"), estimate.get("totalPoints")
            if isinstance(available, (int, float)) and isinstance(required, (int, float)) and available < required:
                raise RealtimeTaskError("Mori 账号积分不足，实时模式已暂停。")
        references = []
        image_positions = [None] * len(snapshots)
        for (index, snapshot), reference, digest in zip(conditioned, prepared, digests):
            check()
            with gateway.lock:
                asset = gateway.reference_assets.get(digest)
            if asset is None:
                notify("uploading", "正在上传%s…" % ("当前画面" if index == 0 else "参考图层 %d" % index))
                asset = client.upload_image(reference.image_bytes,
                                            "实时模式 · " + ("主体" if index == 0 else "参考 %d" % index),
                                            mime=reference.mime)
                check()
                if not isinstance(asset, dict) or not asset.get("id"):
                    raise RealtimeTaskError("图层上传未返回有效素材。")
                with gateway.session_lock:
                    check()
                    with gateway.lock:
                        gateway.reference_assets[digest] = asset
            if asset["id"] not in references:
                references.append(asset["id"])
            image_positions[index] = references.index(asset["id"]) + 1
        check()
        generation_settings = dict(api_settings)
        generation_settings["prompt"] = reference_prompt(settings, snapshots, image_positions)
        notify("submitting", "正在提交 · 使用 Mori 账号积分…")
        submitted = True
        job = client.generate(generation_settings, "realtime-" + request_id, references, request_id)
        if not isinstance(job, dict) or not job.get("id"):
            raise RealtimeTaskError("无法确认生成任务是否已提交，请查看创作记录；没有自动补发。")
        job_id = job["id"]
        gateway._invalidate_reads()
        terminal = {"failed", "cancelled", "interrupted"}
        while True:
            check()
            status = job.get("status")
            if status in terminal:
                raise RealtimeTaskError(str(job.get("error") or "生成任务已停止，请查看创作记录。"),
                                        "error" if status == "failed" else "stopped")
            if status == "completed":
                identifiers = job.get("assetIds") or []
                if not identifiers:
                    raise RealtimeTaskError("任务完成但未返回图片，请查看 Mori 创作记录。")
                assets = {asset["id"]: asset for asset in client.assets()
                          if isinstance(asset, dict) and asset.get("id")}
                asset = assets.get(identifiers[0])
                if asset and asset.get("url"):
                    check()
                    notify("downloading", "正在下载生成结果…")
                    cached = gateway._get_media(asset["url"])
                    image = cached[1] if cached else client.download_asset(asset["url"])
                    check()
                    gateway._cache_media(asset["url"], image, asset.get("mime", "image/png"), epoch)
                    gateway._invalidate_reads()
                    return {"status": "completed", "bytes": image, "asset": asset,
                            "job_id": job_id, "request_id": request_id, "signature": signature,
                            "quote": estimate, "identity": (epoch, user_id),
                            "target": snapshots[0].target,
                            "elapsed_seconds": time.monotonic() - started}
            notify("queued" if status == "queued" else "downloading" if status == "completed" else "generating",
                   ("任务已提交，等待服务开始处理。" if status == "queued" else "正在生成，完成后会自动显示预览。")
                   if status != "completed" else "任务已完成，正在等待结果图片。")
            if cancel.wait(poll_interval):
                check()
            check()
            found = next((item for item in client.jobs()
                          if isinstance(item, dict) and item.get("id") == job_id), None)
            if found:
                job = found
    except Exception as error:
        uncertain = submitted and job_id is None
        return {"status": "uncertain" if uncertain else "failed",
                "error_state": "uncertain" if uncertain else getattr(error, "state", "error"),
                "error": str(error), "job_id": job_id, "request_id": request_id,
                "signature": signature, "elapsed_seconds": time.monotonic() - started}
    finally:
        if client is not None and client is not gateway.client:
            close = getattr(client, "close", None)
            if close:
                try:
                    close()
                except Exception:
                    pass
