"""Native selection work uses fake transports and detached synthetic bytes only."""
import copy
import importlib.util
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "mori_selection_tasks", Path(__file__).resolve().parents[1] / "mori_krita" / "selection_tasks.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MockError(RuntimeError):
    def __init__(self, message, code="NETWORK_ERROR"):
        super().__init__(message)
        self.code = code


class FakeClock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def advance(self, milliseconds):
        self.now += milliseconds / 1000.0

    def wait(self, seconds):
        self.advance(seconds * 1000)
        return False


class FakeClient:
    def __init__(self):
        self.user_id = "alice"
        self.auth_enabled = True
        self.calls = []
        self.jobs_data = []
        self.assets_data = []
        self.generate_hook = None
        self.upload_hook = None
        self.poll_hook = None
        self.conversation_hook = None
        self.turn_status = "completed"
        self.reply = "已修改当前选区。"

    def assert_session_current(self):
        if self.user_id != "alice":
            raise MockError("会话已改变", "SESSION_CHANGED")

    def upload_image(self, image, name, mime=None, progress=None):
        self.calls.append(("upload", image, name, mime))
        if progress:
            progress(len(image), len(image))
        if self.upload_hook:
            self.upload_hook()
        identifier = "mask-1" if image == b"synthetic-edit-mask" else "reference-1"
        return {"id": identifier, "url": "/media/%s.png" % identifier, "mime": mime}

    def generate(self, settings, node_id, references, request_id):
        self.calls.append(("generate", copy.deepcopy(settings), node_id, list(references), request_id))
        if self.generate_hook:
            return self.generate_hook()
        self.jobs_data = [{"id": "job-1", "status": "completed", "assetIds": ["asset-1"], "completed": 1}]
        self.assets_data = [{"id": "asset-1", "url": "/media/result.png", "name": "result"}]
        return {"id": "job-1", "status": "queued", "assetIds": []}

    def jobs(self):
        self.calls.append(("jobs",))
        if self.poll_hook:
            self.poll_hook()
        return copy.deepcopy(self.jobs_data)

    def assets(self):
        self.calls.append(("assets",))
        return copy.deepcopy(self.assets_data)

    def download_asset(self, url):
        self.calls.append(("download", url))
        return b"synthetic-generated-image"

    def creator_config(self):
        self.calls.append(("creator_config",))
        return {"models": [{"id": "agent-1", "provider": "chat"}], "configured": {"chat": True},
                "defaultModel": "agent-1"}

    def create_conversation(self, title):
        self.calls.append(("create_conversation", title))
        return {"id": "conversation-1"}

    def send_message(self, conversation, message, references, settings, model, context, request_id):
        self.calls.append(("send_message", conversation, message, references, copy.deepcopy(settings), model,
                           copy.deepcopy(context), request_id))
        self.jobs_data = [{"id": "job-chat", "status": "completed", "assetIds": ["asset-chat"], "completed": 1}]
        self.assets_data = [{"id": "asset-chat", "url": "/media/chat.png", "name": "agent result"}]
        return {"id": "turn-1", "status": "queued", "jobIds": []}

    def conversation(self, identifier):
        self.calls.append(("conversation", identifier))
        if self.conversation_hook:
            return self.conversation_hook()
        return {"id": identifier,
                "turns": [{"id": "turn-1", "status": self.turn_status, "jobIds": ["job-chat"]}],
                "messages": [{"role": "assistant", "turnId": "turn-1", "content": self.reply}]}

    def cancel_job(self, identifier):
        self.calls.append(("cancel_job", identifier))
        job = next(job for job in self.jobs_data if job["id"] == identifier)
        job["status"] = "cancelled"
        return copy.deepcopy(job)

    def cancel_turn(self, identifier):
        self.calls.append(("cancel_turn", identifier))
        self.turn_status = "cancelled"
        return {"id": identifier, "status": "cancelled", "jobIds": ["job-chat"]}


class FakeGateway:
    def __init__(self):
        self.client = FakeClient()
        self.session_lock = threading.RLock()
        self.lock = threading.RLock()
        self.epoch = 1
        self.account_id = "alice"
        self.closed = False
        self.reference_assets = {}
        self.uploaded_assets = {}
        self.assets_by_id = {}
        self.cache = {}
        self.results = []
        self.mappings = []
        self.invalidations = 0
        self.invoke_hook = None

    def _sync_identity(self):
        if self.client.user_id != self.account_id:
            self.account_id = self.client.user_id
            self.epoch += 1
            self.reference_assets.clear()
            self.cache.clear()

    def _channel(self):
        return self.client

    def _cache_media(self, url, data, mime, epoch):
        if epoch == self.epoch:
            self.cache[url] = (mime, data)

    def _get_media(self, url):
        return self.cache.get(url)

    def _invalidate_reads(self):
        self.invalidations += 1

    def invoke(self, operation, data):
        if operation == "selection_mapping":
            self.mappings.append(dict(data))
            self.client.calls.append(("selection_mapping", data["selection_mapping"]))
            return
        if operation != "selection_result":
            raise AssertionError(operation)
        if self.invoke_hook:
            self.invoke_hook(data)
        self.results.append(dict(data))


class FakeMapping:
    def __init__(self, target):
        self.target = target
        self.selection_rect_in_frame = (100.5, 80.25, 800.0, 600.0)
        self.input_width, self.input_height = 1024, 768
        self.output_width, self.output_height = 1024, 768

    def assert_target(self, target):
        if target is not self.target:
            raise MockError("选区映射不对应原目标", "INVALID_SELECTION_MAPPING")


class SelectionTaskTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.target = SimpleNamespace(document_key="original-document", x=40, y=20, width=800, height=600)
        self.snapshot = SimpleNamespace(target=self.target)
        self.reference = SimpleNamespace(image_bytes=b"synthetic-reference", mime="image/webp")
        self.payload = {"message": "把杯身改成红色", "model": "gpt-image-2.5", "aspect": "auto",
                        "resolution": "1K", "quality": "high", "count": 1, "mode": "edit"}
        self.cancel = threading.Event()
        self.notices = []

    def run_task(self, **overrides):
        options = {"prepare": lambda snapshot, mode="fast": self.reference, "poll_interval": .001,
                   "timeout": .3, "cancel_event": self.cancel, "notify": self.notices.append}
        options.update(overrides)
        return MODULE.run_selection_task(self.gateway, self.snapshot, self.payload, **options)

    def calls(self, name):
        return [call for call in self.gateway.client.calls if call[0] == name]

    def test_edit_round_trip_keeps_original_target_and_sends_one_paid_post(self):
        summary = self.run_task()
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["delivered_asset_ids"], ["asset-1"])
        self.assertEqual(len(self.calls("generate")), 1)
        self.assertEqual(self.calls("generate")[0][1]["aspect"], "4:3")
        self.assertEqual(self.calls("generate")[0][3], ["reference-1"])
        self.assertIs(self.gateway.results[0]["target"], self.target)
        self.assertEqual((self.target.width, self.target.height), (800, 600))
        self.assertEqual(summary["success_message"], "已将 1 张结果加入原选区的新图层。")

    def test_cached_reference_is_not_uploaded_again(self):
        self.assertEqual(self.run_task()["status"], "completed")
        self.assertEqual(self.run_task()["status"], "completed")
        self.assertEqual(len(self.calls("upload")), 1)
        self.assertEqual(len(self.calls("generate")), 2)

    def test_unrelated_historical_jobs_are_never_delivered(self):
        def generate():
            self.gateway.client.jobs_data = [{"id": "history", "status": "completed", "assetIds": ["private-history"]},
                                              {"id": "job-1", "status": "completed", "assetIds": ["asset-1"]}]
            self.gateway.client.assets_data = [{"id": "private-history", "url": "/media/history.png"},
                                                {"id": "asset-1", "url": "/media/result.png"}]
            return {"id": "job-1", "status": "queued", "assetIds": []}
        self.gateway.client.generate_hook = generate
        summary = self.run_task()
        self.assertEqual(summary["delivered_asset_ids"], ["asset-1"])
        self.assertEqual(self.calls("download"), [("download", "/media/result.png")])

    def test_cancellation_before_start_does_not_upload_or_generate(self):
        self.cancel.set()
        self.assertEqual(self.run_task()["status"], "cancelled")
        self.assertEqual(self.gateway.client.calls, [])

    def test_cancellation_after_upload_does_not_submit_a_paid_job(self):
        self.gateway.client.upload_hook = self.cancel.set
        self.assertEqual(self.run_task()["status"], "cancelled")
        self.assertEqual(len(self.calls("upload")), 1)
        self.assertEqual(self.calls("generate"), [])

    def test_cancellation_keeps_partial_results_and_cancels_once(self):
        def generate():
            self.gateway.client.jobs_data = [{"id": "job-1", "status": "running", "assetIds": ["asset-1"]}]
            self.gateway.client.assets_data = [{"id": "asset-1", "url": "/media/result.png"}]
            self.cancel.set()
            return copy.deepcopy(self.gateway.client.jobs_data[0])
        self.gateway.client.generate_hook = generate
        summary = self.run_task()
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(summary["delivered_asset_ids"], ["asset-1"])
        self.assertEqual(self.calls("cancel_job"), [("cancel_job", "job-1")])

    def test_upload_account_switch_discards_result_before_generation(self):
        self.gateway.client.upload_hook = lambda: setattr(self.gateway.client, "user_id", "bob")
        summary = self.run_task()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "SESSION_CHANGED")
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(self.gateway.results, [])

    def test_poll_account_switch_never_writes_a_new_accounts_document(self):
        self.gateway.client.poll_hook = lambda: setattr(self.gateway.client, "user_id", "bob")
        summary = self.run_task()
        self.assertEqual(summary["error_code"], "SESSION_CHANGED")
        self.assertEqual(self.gateway.results, [])

    def test_closed_original_target_error_is_returned_without_repeated_import(self):
        def fail(data):
            raise RuntimeError("原画布已关闭")
        self.gateway.invoke_hook = fail
        summary = self.run_task()
        self.assertEqual(summary["status"], "failed")
        self.assertIn("原画布已关闭", summary["error"])
        self.assertEqual(len(self.calls("download")), 1)
        self.assertEqual(len(self.calls("generate")), 1)

    def test_uncertain_paid_submission_is_not_retried(self):
        self.gateway.client.generate_hook = lambda: (_ for _ in ()).throw(MockError("网络中断"))
        summary = self.run_task()
        self.assertEqual(summary["status"], "uncertain")
        self.assertEqual(len(self.calls("generate")), 1)
        self.assertIn("没有自动重发", summary["success_message"])

    def test_timeout_returns_job_record_without_resubmission(self):
        self.gateway.client.generate_hook = lambda: {"id": "job-slow", "status": "running", "assetIds": []}
        summary = self.run_task(timeout=.01)
        self.assertEqual(summary["status"], "timeout")
        self.assertEqual(summary["job_ids"], ["job-slow"])
        self.assertEqual(len(self.calls("generate")), 1)

    def test_login_is_required_before_preparing_or_uploading(self):
        self.gateway.client.user_id = None
        summary = self.run_task()
        self.assertEqual(summary["error_code"], "AUTH_REQUIRED")
        self.assertEqual(self.gateway.client.calls, [])

    def test_account_changed_before_worker_starts_never_prepares_or_uploads(self):
        self.payload.update(expected_epoch=1, expected_user_id="alice")
        self.gateway.client.user_id = "bob"
        def prepare(snapshot, mode="fast"):
            self.fail("An old account's snapshot must not be processed")
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "SESSION_CHANGED")
        self.assertEqual(self.gateway.client.calls, [])
        self.assertEqual(self.gateway.results, [])

    def test_explicit_expected_anonymous_account_is_compared_before_upload(self):
        self.payload["expected_user_id"] = None
        summary = self.run_task()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "SESSION_CHANGED")
        self.assertEqual(self.gateway.client.calls, [])

    def test_chat_generation_uses_selected_reference_and_delivers_only_its_turn(self):
        self.payload["mode"] = "chat"
        summary = self.run_task()
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["conversation_id"], "conversation-1")
        self.assertEqual(summary["turn_id"], "turn-1")
        self.assertEqual(summary["delivered_asset_ids"], ["asset-chat"])
        sent = self.calls("send_message")[0]
        self.assertEqual(sent[3], ["reference-1"])
        self.assertEqual(sent[5], "agent-1")
        self.assertEqual(sent[6]["nodes"][0]["assetId"], "reference-1")
        self.assertIs(self.gateway.results[0]["target"], self.target)
        self.assertEqual(len(self.calls("send_message")), 1)

    def test_chat_can_reuse_existing_conversation(self):
        self.payload["mode"] = "chat"
        summary = self.run_task(conversation_id="existing-conversation")
        self.assertEqual(self.calls("create_conversation"), [])
        self.assertEqual(self.calls("send_message")[0][1], "existing-conversation")
        self.assertEqual(summary["conversation_id"], "existing-conversation")

    def test_plain_chat_answer_does_not_create_a_result_layer(self):
        self.payload["mode"] = "chat"
        self.gateway.client.conversation_hook = lambda: {
            "turns": [{"id": "turn-1", "status": "completed", "jobIds": []}],
            "messages": [{"role": "assistant", "turnId": "turn-1", "content": "建议先调整明度。"}]}
        summary = self.run_task()
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["message"], "建议先调整明度。")
        self.assertEqual(summary["delivered_asset_ids"], [])
        self.assertEqual(self.gateway.results, [])
        self.assertTrue(summary["success_message"].startswith("智能体已回复，本轮未生成图片。"))
        self.assertIn("直接修改", summary["success_message"])
        self.assertIn("建议先调整明度。", summary["success_message"])
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(len(self.calls("send_message")), 1)

    def test_timing_stages_measure_real_work_without_overlap_or_extra_posts(self):
        clock = FakeClock()
        self.snapshot.read_ms = 12.0
        prepared, _ = self.mapped_reference()

        def prepare(snapshot, mode="fast"):
            clock.advance(10)
            return prepared

        def channel():
            clock.advance(13)
            return self.gateway.client
        self.gateway._channel = channel
        self.gateway.client.upload_hook = lambda: clock.advance(
            20 if "蒙版" in self.calls("upload")[-1][2] else 30)

        def generate():
            clock.advance(40)
            self.gateway.client.jobs_data = [{"id": "job-1", "status": "completed", "assetIds": ["asset-1"]}]
            self.gateway.client.assets_data = [{"id": "asset-1", "url": "/media/result.png", "name": "result"}]
            return {"id": "job-1", "status": "queued", "assetIds": []}
        self.gateway.client.generate_hook = generate
        self.gateway.client.poll_hook = lambda: clock.advance(50)
        original_assets = self.gateway.client.assets
        original_download = self.gateway.client.download_asset

        def assets():
            clock.advance(5)
            return original_assets()

        def download(url):
            clock.advance(100)
            return original_download(url)
        self.gateway.client.assets = assets
        self.gateway.client.download_asset = download
        self.gateway.invoke_hook = lambda data: clock.advance(7)
        with mock.patch.object(MODULE.time, "monotonic", clock.monotonic), \
                mock.patch.object(self.cancel, "wait", clock.wait):
            summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["timings_ms"], {
            "read_ms": 12.0, "session_ms": 13.0, "prepare_ms": 10.0,
            "reference_upload_ms": 30.0, "mask_upload_ms": 20.0,
            "agent_setup_ms": 0.0, "submit_ms": 40.0, "wait_ms": 51.0,
            "download_ms": 105.0, "import_ms": 7.0, "total_ms": 288.0})
        self.assertEqual(len(self.calls("generate")), 1)
        self.assertEqual(len(self.calls("jobs")), 1)
        self.assertEqual(len(self.calls("assets")), 1)
        self.assertIn("正在下载生成结果…", self.notices)
        self.assertIn("正在写入 Krita 图层…", self.notices)

    def test_cached_uploads_have_zero_transfer_time_on_the_next_task(self):
        clock = FakeClock()
        _, prepare = self.mapped_reference()
        self.gateway.client.upload_hook = lambda: clock.advance(25)
        with mock.patch.object(MODULE.time, "monotonic", clock.monotonic), \
                mock.patch.object(self.cancel, "wait", clock.wait):
            first = self.run_task(prepare=prepare)
            second = self.run_task(prepare=prepare)
        self.assertEqual((first["status"], second["status"]), ("completed", "completed"))
        self.assertEqual(first["timings_ms"]["reference_upload_ms"], 25.0)
        self.assertEqual(first["timings_ms"]["mask_upload_ms"], 25.0)
        self.assertEqual(second["timings_ms"]["reference_upload_ms"], 0.0)
        self.assertEqual(second["timings_ms"]["mask_upload_ms"], 0.0)
        self.assertEqual(len(self.calls("upload")), 2)
        self.assertEqual(len(self.calls("generate")), 2)

    def test_submission_failure_keeps_elapsed_time_without_retry(self):
        clock = FakeClock()

        def uncertain():
            clock.advance(80)
            raise MockError("connection interrupted", "NETWORK_ERROR")
        self.gateway.client.generate_hook = uncertain
        with mock.patch.object(MODULE.time, "monotonic", clock.monotonic):
            summary = self.run_task()
        self.assertEqual(summary["status"], "uncertain")
        self.assertEqual(summary["timings_ms"]["submit_ms"], 80.0)
        self.assertEqual(summary["timings_ms"]["wait_ms"], 0.0)
        self.assertEqual(summary["timings_ms"]["total_ms"], 80.0)
        self.assertEqual(len(self.calls("generate")), 1)
        self.assertEqual(self.gateway.results, [])

    def test_wait_progress_distinguishes_queue_generation_and_elapsed_seconds(self):
        clock = FakeClock()
        self.gateway.client.generate_hook = lambda: {"id": "job-1", "status": "queued", "assetIds": []}

        def poll():
            if len(self.calls("jobs")) == 1:
                self.gateway.client.jobs_data = [{"id": "job-1", "status": "running", "assetIds": []}]
            else:
                self.gateway.client.jobs_data = [{"id": "job-1", "status": "completed", "assetIds": ["asset-1"]}]
                self.gateway.client.assets_data = [{"id": "asset-1", "url": "/media/result.png", "name": "result"}]
        self.gateway.client.poll_hook = poll
        with mock.patch.object(MODULE.time, "monotonic", clock.monotonic), \
                mock.patch.object(self.cancel, "wait", clock.wait):
            summary = self.run_task(poll_interval=2, timeout=20)
        self.assertEqual(summary["status"], "completed")
        self.assertIn("生成任务正在排队 · 已等待 0 秒", self.notices)
        self.assertIn("服务器正在生成选区修改结果 · 已等待 2 秒", self.notices)
        self.assertEqual(summary["timings_ms"]["wait_ms"], 4000.0)
        self.assertEqual(len(self.calls("jobs")), 2)
        self.assertEqual(len(self.calls("generate")), 1)

    def expect_image_after_text_only_turn(self):
        self.payload.update(mode="chat", expect_image=True)
        self.gateway.client.conversation_hook = lambda: {
            "turns": [{"id": "turn-1", "status": "completed", "jobIds": []}],
            "messages": [{"role": "assistant", "turnId": "turn-1", "content": "我会把选区改为银色。"}]}

    def test_image_intent_finishes_a_text_plan_with_one_masked_image_request(self):
        self.expect_image_after_text_only_turn()
        prepared, prepare = self.mapped_reference()
        self.payload["client_request_id"] = "explicit-render-123"
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "completed")
        self.assertTrue(summary["agent_image_fallback"])
        self.assertEqual(summary["delivered_asset_ids"], ["asset-1"])
        self.assertEqual(len(self.calls("conversation")), 2)
        self.assertEqual(len(self.calls("send_message")), 1)
        self.assertEqual(len(self.calls("generate")), 1)
        sent, generated = self.calls("send_message")[0], self.calls("generate")[0]
        self.assertNotIn("edit", sent[4])
        self.assertEqual(generated[1]["edit"], {"size": "1024x768", "maskAssetId": "mask-1"})
        self.assertEqual(generated[3], ["reference-1"])
        self.assertEqual((sent[7], generated[4]), ("explicit-render-123", "explicit-render-123"))
        self.assertEqual((generated[1]["model"], generated[1]["resolution"], generated[1]["quality"], generated[1]["count"]),
                         ("gpt-image-2.5", "1K", "high", 1))
        names = [call[0] for call in self.gateway.client.calls]
        self.assertGreater(names.index("generate"), names.index("jobs"))
        self.assertGreater(names.index("upload", names.index("send_message")), names.index("jobs"))
        self.assertIs(self.gateway.results[0]["selection_mapping"], prepared.mapping)
        self.assertIn("智能体已完成理解，正在生成修改图片…", self.notices)

    def test_existing_turn_job_is_recovered_without_a_second_image_post(self):
        self.expect_image_after_text_only_turn()
        original = self.gateway.client.conversation_hook

        def conversation():
            self.gateway.client.jobs_data = [{"id": "job-chat", "status": "completed", "assetIds": ["asset-chat"],
                                              "origin": {"turnId": "turn-1", "conversationId": "conversation-1"}}]
            return original()
        self.gateway.client.conversation_hook = conversation
        summary = self.run_task()
        self.assertEqual(summary["status"], "completed")
        self.assertFalse(summary["agent_image_fallback"])
        self.assertEqual(summary["delivered_asset_ids"], ["asset-chat"])
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(len(self.calls("send_message")), 1)

    def test_existing_failed_turn_job_is_not_automatically_retried(self):
        self.expect_image_after_text_only_turn()
        original = self.gateway.client.conversation_hook

        def conversation():
            self.gateway.client.jobs_data = [{"id": "job-failed", "status": "failed", "assetIds": [],
                                              "error": "Image service failed", "origin": {"turnId": "turn-1"}}]
            return original()
        self.gateway.client.conversation_hook = conversation
        summary = self.run_task()
        self.assertEqual(summary["status"], "failed")
        self.assertFalse(summary["agent_image_fallback"])
        self.assertEqual(summary["job_ids"], ["job-failed"])
        self.assertEqual(self.calls("generate"), [])

    def test_fresh_turn_job_ids_prevent_a_fallback_even_before_job_metadata_arrives(self):
        self.expect_image_after_text_only_turn()
        original = self.gateway.client.conversation_hook

        def conversation():
            value = original()
            if len(self.calls("conversation")) > 1:
                value["turns"][0]["jobIds"] = ["job-chat"]
            return value
        self.gateway.client.conversation_hook = conversation
        summary = self.run_task()
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(summary["delivered_asset_ids"], ["asset-chat"])
        self.assertEqual(self.calls("generate"), [])

    def test_non_completed_agent_turns_never_admit_a_fallback(self):
        for status in ("failed", "interrupted", "cancelled"):
            with self.subTest(status=status):
                self.setUp()
                self.expect_image_after_text_only_turn()
                self.gateway.client.conversation_hook = lambda status=status: {
                    "turns": [{"id": "turn-1", "status": status, "jobIds": [], "error": "Stopped"}], "messages": []}
                summary = self.run_task()
                self.assertEqual(summary["status"], status)
                self.assertEqual(self.calls("generate"), [])
                self.assertEqual(self.gateway.results, [])

    def test_cannot_confirm_completed_agent_turn_does_not_submit_an_image(self):
        self.expect_image_after_text_only_turn()
        original = self.gateway.client.conversation_hook

        def conversation():
            if len(self.calls("conversation")) == 2:
                raise MockError("Status response timed out", "TIMEOUT")
            return original()
        self.gateway.client.conversation_hook = conversation
        summary = self.run_task()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "TIMEOUT")
        self.assertEqual(self.calls("generate"), [])

    def test_cancellation_and_account_change_during_confirmation_stop_before_fallback(self):
        for change in ("cancel", "account"):
            with self.subTest(change=change):
                self.setUp()
                self.expect_image_after_text_only_turn()
                _, prepare = self.mapped_reference()
                if change == "cancel":
                    self.gateway.client.poll_hook = self.cancel.set
                else:
                    self.gateway.client.poll_hook = lambda: setattr(self.gateway.client, "user_id", "bob")
                summary = self.run_task(prepare=prepare)
                self.assertEqual(summary["status"], "cancelled" if change == "cancel" else "failed")
                if change == "account":
                    self.assertEqual(summary["error_code"], "SESSION_CHANGED")
                self.assertEqual(len(self.calls("upload")), 1)
                self.assertEqual(self.calls("generate"), [])
                self.assertEqual(self.gateway.results, [])

    def test_cancellation_during_lazy_mask_upload_does_not_admit_an_image(self):
        self.expect_image_after_text_only_turn()
        _, prepare = self.mapped_reference()
        self.gateway.client.upload_hook = lambda: self.cancel.set() if len(self.calls("upload")) == 2 else None
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(len(self.calls("upload")), 2)
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(self.gateway.results, [])

    def test_uncertain_fallback_post_is_not_replayed_even_with_an_existing_turn_id(self):
        self.expect_image_after_text_only_turn()
        self.gateway.client.generate_hook = lambda: (_ for _ in ()).throw(MockError("Request outcome unknown", "NETWORK_ERROR"))
        summary = self.run_task()
        self.assertEqual(summary["status"], "uncertain")
        self.assertEqual(summary["turn_id"], "turn-1")
        self.assertEqual(len(self.calls("generate")), 1)
        self.assertEqual(len(self.calls("send_message")), 1)
        self.assertEqual(self.gateway.results, [])

    def test_confirmation_crossing_the_deadline_never_admits_an_image(self):
        self.expect_image_after_text_only_turn()
        clock = FakeClock()
        self.gateway.client.poll_hook = lambda: clock.advance(400)
        with mock.patch.object(MODULE.time, "monotonic", clock.monotonic), \
                mock.patch.object(self.cancel, "wait", clock.wait):
            summary = self.run_task(timeout=.3)
        self.assertEqual(summary["status"], "timeout")
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(self.gateway.results, [])

    def test_cancel_after_fallback_submission_cancels_the_image_job(self):
        self.expect_image_after_text_only_turn()

        def generate():
            self.gateway.client.jobs_data = [{"id": "fallback-job", "status": "running", "assetIds": []}]
            self.cancel.set()
            return {"id": "fallback-job", "status": "running", "assetIds": []}
        self.gateway.client.generate_hook = generate
        summary = self.run_task()
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(self.calls("cancel_turn"), [])
        self.assertEqual(self.calls("cancel_job"), [("cancel_job", "fallback-job")])
        self.assertEqual(len(self.calls("generate")), 1)

    def test_invalid_draft_or_settings_never_touch_network(self):
        for patch in ({"message": " "}, {"count": 5}, {"resolution": "8K"}, {"mode": "unknown"}):
            with self.subTest(patch=patch):
                self.payload.update(patch)
                self.assertEqual(self.run_task()["status"], "failed")
                self.assertEqual(self.gateway.client.calls, [])
                self.setUp()

    def mapped_reference(self):
        self.snapshot.context_rect = (0, 0, 1024, 768)
        mapping = FakeMapping(self.target)
        prepared = SimpleNamespace(reference=self.reference, mapping=mapping, mask_bytes=b"synthetic-edit-mask")
        return prepared, lambda snapshot, mode="fast": prepared

    def test_context_edit_uploads_separate_mask_and_registers_mapping_before_paid_post(self):
        prepared, prepare = self.mapped_reference()
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "completed")
        uploads = self.calls("upload")
        self.assertEqual(len(uploads), 2)
        self.assertEqual((uploads[0][1], uploads[0][3]), (b"synthetic-reference", "image/webp"))
        self.assertEqual((uploads[1][1], uploads[1][3]), (b"synthetic-edit-mask", "image/png"))
        generated = self.calls("generate")[0]
        self.assertEqual(generated[3], ["reference-1"])
        self.assertEqual(generated[1]["edit"], {"size": "1024x768", "maskAssetId": "mask-1"})
        self.assertIn(self.payload["message"], generated[1]["prompt"])
        self.assertIn("完整画幅", generated[1]["prompt"])
        names = [call[0] for call in self.gateway.client.calls]
        self.assertLess(names.index("selection_mapping"), names.index("generate"))
        self.assertIs(self.gateway.mappings[0]["selection_mapping"], prepared.mapping)
        self.assertIs(self.gateway.results[0]["selection_mapping"], prepared.mapping)
        self.assertIs(self.gateway.results[0]["target"], self.target)
        self.assertEqual(len(self.calls("generate")), 1)

    def test_context_and_mask_cache_independently_without_reuploading(self):
        _, prepare = self.mapped_reference()
        self.assertEqual(self.run_task(prepare=prepare)["status"], "completed")
        self.assertEqual(self.run_task(prepare=prepare)["status"], "completed")
        self.assertEqual(len(self.calls("upload")), 2)
        self.assertEqual(len(self.calls("generate")), 2)
        self.assertEqual(len(self.gateway.reference_assets), 2)

    def test_context_snapshot_without_mapping_is_rejected_before_upload(self):
        self.snapshot.context_rect = (0, 0, 1024, 768)
        summary = self.run_task()
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "INVALID_REFERENCE")
        self.assertEqual(self.gateway.client.calls, [])

    def test_mapping_for_different_selection_is_rejected_before_upload(self):
        prepared, prepare = self.mapped_reference()
        prepared.mapping.target = SimpleNamespace(document_key="other-document")
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "INVALID_SELECTION_MAPPING")
        self.assertEqual(self.gateway.client.calls, [])

    def test_banana_keeps_context_mapping_but_does_not_send_openai_mask_fields(self):
        prepared, prepare = self.mapped_reference()
        self.payload["model"] = "gemini-nano-banana-2.1"
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(len(self.calls("upload")), 1)
        self.assertNotIn("edit", self.calls("generate")[0][1])
        self.assertIs(self.gateway.results[0]["selection_mapping"], prepared.mapping)

    def test_agent_keeps_context_mapping_without_invalid_openai_settings(self):
        prepared, prepare = self.mapped_reference()
        self.payload["mode"] = "chat"
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "completed")
        self.assertEqual(len(self.calls("upload")), 1)
        sent = self.calls("send_message")[0]
        self.assertNotIn("edit", sent[4])
        self.assertIn("完整画幅", sent[2])
        self.assertIs(self.gateway.results[0]["selection_mapping"], prepared.mapping)

    def test_cancelling_after_mask_upload_does_not_submit_a_paid_job(self):
        _, prepare = self.mapped_reference()
        self.gateway.client.upload_hook = lambda: self.cancel.set() if len(self.calls("upload")) == 2 else None
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(len(self.calls("upload")), 2)
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(self.gateway.mappings, [])

    def test_account_change_during_mask_upload_never_bills_or_imports(self):
        _, prepare = self.mapped_reference()
        def switch_after_mask():
            if len(self.calls("upload")) == 2:
                self.gateway.client.user_id = "bob"
        self.gateway.client.upload_hook = switch_after_mask
        summary = self.run_task(prepare=prepare)
        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["error_code"], "SESSION_CHANGED")
        self.assertEqual(self.calls("generate"), [])
        self.assertEqual(self.gateway.mappings, [])
        self.assertEqual(self.gateway.results, [])


if __name__ == "__main__":
    unittest.main()
