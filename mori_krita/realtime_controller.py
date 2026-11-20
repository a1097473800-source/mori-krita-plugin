"""Native live drawing: GUI snapshots, one paid request, latest edit wins."""
from __future__ import annotations

import concurrent.futures
import threading
import time

from PyQt5.QtCore import QObject, QEvent, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication

from .qt_lifetime import is_alive
from .realtime_bridge import (RealtimeBusy, layer_choices, document_key,
                              source_signature, capture_layers, import_result)
from .realtime_task import run_realtime_task, variation_value
from .reference_presets import CANVAS_SOURCE
from .selection_geometry import find_canvas_for_window
from .selection_tasks import selection_aspect


class RealtimeController(QObject):
    completed = pyqtSignal(object, object, object)
    progress = pyqtSignal(object, str, str)

    def __init__(self, docker, panel):
        super().__init__(docker)
        self.docker, self.panel = docker, panel
        self._closed = False
        self._active = False
        self._loading = False
        self._authenticated = False
        self._identity = None
        self._config = {}
        self._document = None
        self._document_id = None
        self._layers = None
        self._canvas = None
        self._pressed = False
        self._dirty = False
        self._last_edit = time.monotonic()
        self._observed = None
        self._previous = None
        self._result = None
        self._task = None
        self._cancel = None
        self._account_request = None
        self._quote_version = 0
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="Mori realtime")
        self.completed.connect(self._finished_work)
        self.progress.connect(self._progress)
        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self.refresh)
        self._quote_timer = QTimer(self)
        self._quote_timer.setSingleShot(True)
        self._quote_timer.timeout.connect(self._quote)
        panel.parameters_changed.connect(self._parameters_changed)
        panel.sources_changed.connect(self._sources_changed)
        panel.auto_changed.connect(self._auto_changed)
        panel.generate_requested.connect(self.submit_once)
        panel.apply_requested.connect(self.apply)
        panel.refresh_requested.connect(self.refresh_account)
        panel.login_requested.connect(self.login)

    def _work(self, function, callback):
        if self._closed:
            return
        future = self._pool.submit(function)

        def done(value):
            result, error = None, None
            try:
                result = value.result()
            except Exception as failure:
                error = str(failure)
            if not self._closed and is_alive(self):
                try:
                    self.completed.emit(callback, result, error)
                except RuntimeError:
                    pass  # A deleted Qt parent invalidates all queued UI work.
        future.add_done_callback(done)

    def _finished_work(self, callback, result, error):
        if not self._closed and is_alive(self.panel):
            callback(result, error)

    def _progress(self, task, state, message):
        if not self._closed and task is self._task and is_alive(self.panel):
            self.panel.set_status(message, state=state)

    @staticmethod
    def _session(gateway):
        gateway._sync_identity()
        return gateway.epoch, gateway.client.user_id

    def set_active(self, active):
        if self._closed:
            return
        self._active = bool(active)
        if not active:
            self.panel.live.setChecked(False)
            self._timer.stop()
            self._quote_timer.stop()
            self._pressed = False
            return
        self._timer.start()
        self.refresh()
        if self._identity is None and not self._loading:
            self.refresh_account()

    def connection_changed(self):
        if self._closed:
            return
        self.panel.live.setChecked(False)
        if self._cancel:
            self._cancel.set()
        self._task = None
        self._account_request = None
        self._loading = False
        self._authenticated = False
        self._identity = None
        self._result = None
        self._previous = None
        self.panel.clear_result()
        self.panel.set_busy(False)
        self.panel.set_pending(False)
        self.panel.set_available(False)
        self.panel.set_connection_action("retry")
        self.panel.set_status("连接已改变，请重新连接 Mori。", state="disconnected")

    def refresh_account(self):
        if self._closed or self._loading:
            return
        self.refresh()
        try:
            gateway = self.docker._ensure_gateway()
        except Exception as error:
            self.panel.set_status(str(error), error=True, state="disconnected")
            return
        self._loading = True
        if self._task is None:
            self.panel.set_status("正在连接 Mori…", state="connecting")
        marker = self._account_request = object()
        self.panel.refresh.setEnabled(False)
        self.panel.connection_action.setEnabled(False)

        def load():
            session = gateway.client.session()
            identity = self._session(gateway)
            if session.get("enabled", True) and not session.get("user"):
                return {"session": session, "identity": identity}
            client = gateway._channel()
            try:
                config = client.config()
                credits = client.credits()
                return {"session": session, "identity": identity, "config": config, "credits": credits}
            finally:
                if client is not gateway.client:
                    client.close()

        def loaded(value, error):
            if marker is not self._account_request:
                return
            self._loading = False
            self.panel.refresh.setEnabled(True)
            self.panel.connection_action.setEnabled(True)
            if gateway is not self.docker._gateway:
                return
            if error:
                self._authenticated = False
                self.panel.account.setText("连接未完成")
                self._pause(error, state="disconnected")
                self.panel.set_connection_action("retry")
                self._available()
                return
            if value["identity"] != self._session(gateway):
                self._pause("账号已改变，请刷新后重新开始。")
                return
            identity = value["identity"]
            if self._identity is not None and identity != self._identity:
                self.connection_changed()
            self._identity = identity
            session = value["session"]
            user = session.get("user") or {}
            self._authenticated = bool(user or not session.get("enabled", True))
            self.panel.login.setText("切换账号" if user else "登录")
            label = str(user.get("name") or user.get("email") or ("本地账号" if self._authenticated else "未登录 Mori"))
            self.panel.account.setText(label[:15] + ("…" if len(label) > 15 else ""))
            self.panel.account.setToolTip(label)
            self.panel.balance.setText("积分 —")
            if self._authenticated:
                self.panel.set_connection_action()
                self._config = value.get("config", {})
                configured = self._config.get("configured", {})
                models = [model for model in self._config.get("models", [])
                          if not configured or configured.get(model.get("provider"))]
                self.panel.set_models(models)
                balance = value.get("credits", {}).get("balance", {}).get("available")
                if isinstance(balance, (float, int)):
                    self.panel.balance.setText("%g 积分" % balance)
                self._quote_version += 1
                self._quote_timer.start(150)
                if self._task is None:
                    self._idle_status()
            else:
                self.panel.set_status("服务已连接，请登录 Mori 后开始。", state="login")
                self.panel.set_connection_action("login")
            self._available()
        self._work(load, loaded)

    def login(self):
        if self._closed:
            return
        self.panel.live.setChecked(False)
        try:
            gateway = self.docker._ensure_gateway()
            if self.docker._selection._login(gateway):
                self.connection_changed()
                self.refresh_account()
        except Exception as error:
            self.panel.set_status(str(error), error=True)

    def _settings(self):
        settings = self.panel.settings()
        model = next((model for model in self._config.get("models", [])
                      if model.get("id") == settings["model"]), {})
        width = self._document.width() if self._document else 1
        height = self._document.height() if self._document else 1
        settings["aspect"] = selection_aspect(width, height, model.get("aspects"))
        preferences = getattr(self.docker, "_settings", None)
        settings["reference_mode"] = preferences.value("referenceMode", "original", type=str) if preferences else "original"
        return settings

    def _quote(self):
        if variation_value(self.panel.settings()) == 0:
            return
        gateway = self.docker._gateway
        if self._closed or not self._authenticated or not gateway or not self.panel.model.currentData():
            return
        settings = self._settings()
        settings.pop("variation", None)
        settings.pop("reference_mode", None)
        identity, version = self._session(gateway), self._quote_version

        def quote():
            client = gateway._channel()
            try:
                return client.quote(settings)
            finally:
                if client is not gateway.client:
                    client.close()

        def received(value, error):
            if (version != self._quote_version or gateway is not self.docker._gateway
                    or identity != self._session(gateway)):
                return
            if value and value.get("enforcement"):
                amount, balance = value.get("totalPoints"), value.get("available")
                if isinstance(amount, (int, float)) and isinstance(balance, (int, float)):
                    self.panel.balance.setText("%g 积分" % balance)
                    self.panel.cost.setText("每次预计 %g 积分 · 构图比例 %s%s" %
                                            (amount, settings["aspect"], " · 积分不足" if balance < amount else ""))
            elif value:
                self.panel.cost.setText("服务器未启用积分扣费 · 构图比例 " + settings["aspect"])
            elif error:
                self.panel.cost.setText("暂时无法获取报价，提交前会再次核对积分。")
        self._work(quote, received)

    def _mark_dirty(self):
        self._dirty = True
        self._last_edit = time.monotonic()
        if self.panel.live.isChecked():
            if self._task is not None:
                self.panel.set_pending(True)
            else:
                self.panel.set_status("检测到修改，停笔 %g 秒后更新。" % float(self.panel.delay.currentData()),
                                      state="waiting")

    def _idle_status(self):
        if self._task is not None:
            return
        if self._document is None:
            self.panel.set_status("新建或打开画布后即可开始。", state="no_document")
        elif variation_value(self.panel.settings()) != 0 and not self._authenticated:
            self.panel.set_status("请连接并登录 Mori 后开始。", state="connecting" if self._loading else "login")
        elif variation_value(self.panel.settings()) != 0 and not self.panel.model.currentData():
            self.panel.set_status("暂无可用生成模型，请刷新 Mori 连接。", state="blocked")
        elif variation_value(self.panel.settings()) != 0 and not self.panel.settings()["prompt"]:
            self.panel.set_status("填写你想要的效果后，点击开始。", state="needs_prompt")
        elif self.panel.live.isChecked() and self._dirty:
            self.panel.set_status("检测到修改，停笔 %g 秒后更新。" % float(self.panel.delay.currentData()), state="waiting")
        elif self.panel.live.isChecked():
            self.panel.set_status("实时已开启，等待画布或提示词变化。", state="watching")
        else:
            self.panel.set_status("准备就绪，点击开始，停笔后自动生成预览。", state="ready")

    def _parameters_changed(self):
        if self._closed:
            return
        self._quote_version += 1
        self._quote_timer.start(550)
        self._mark_dirty()
        self._available()

    def _sources_changed(self):
        if self._closed:
            return
        self._observed = None
        self._mark_dirty()
        self._available()

    def _auto_changed(self, enabled):
        if self._closed:
            return
        if enabled:
            original = variation_value(self.panel.settings()) == 0
            if (not self.panel.source_ids()[0] or self._document is None
                    or (not original and (not self._authenticated or not self.panel.settings()["prompt"]))):
                self.panel.live.setChecked(False)
                self.panel.set_status("请先打开画布、登录并填写提示词，默认参考当前画布。", error=True,
                                      state="no_document" if self._document is None else "login" if not self._authenticated and not original else "needs_prompt")
                return
            self._mark_dirty()
        else:
            self._dirty = False
            self.panel.set_pending(False)
            if self._task is None:
                self.panel.set_status("自动生成已暂停，点击开始可继续。", state="paused")

    def _pause(self, message, *, state="error"):
        self.panel.live.setChecked(False)
        self.panel.set_status(message, error=True, state=state)

    def _available(self):
        settings = self.panel.settings()
        original = variation_value(settings) == 0
        self.panel.set_available(self._document is not None and bool(self.panel.source_ids()[0])
                                 and (original or (self._authenticated and bool(settings.get("model")))))

    def _bind_canvas(self):
        canvas = find_canvas_for_window(self.docker.window())
        if canvas is self._canvas:
            return
        if is_alive(self._canvas):
            self._canvas.removeEventFilter(self)
        self._canvas = canvas
        self._pressed = False
        if is_alive(canvas):
            canvas.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self._canvas:
            kind = event.type()
            if kind in (QEvent.MouseButtonPress, QEvent.TabletPress):
                self._pressed = True
            elif kind in (QEvent.MouseButtonRelease, QEvent.TabletRelease):
                self._pressed = False
                self._mark_dirty()
        return False

    def refresh(self):
        if self._closed or not is_alive(self.panel):
            return
        try:
            document, layers = layer_choices(self.docker._bridge)
            key = document_key(self.docker._bridge, document) if document else None
            changed = key != self._document_id
            if changed:
                self.panel.live.setChecked(False)
                self._document, self._document_id = document, key
                self._observed = self._previous = self._result = self._layers = None
                self.panel.clear_result()
                self._dirty = False
                self.panel.set_pending(False)
            subject, references = self.panel.source_ids()
            if changed:
                subject = CANVAS_SOURCE
                references = []
            fingerprint = tuple((item["id"], item["name"], item.get("visible", True)) for item in layers)
            if fingerprint != self._layers:
                removed = bool(subject and subject != CANVAS_SOURCE and subject not in {layer["id"] for layer in layers})
                self.panel.set_layers(layers, subject, references, reset=changed)
                self._layers = fingerprint
                if removed:
                    self._pause("指定图层已移除，已恢复参考当前画布。")
            if document:
                self.panel.document.setText("%s · %d × %d" %
                                            (self.docker._bridge._document_name(document), document.width(), document.height()))
            else:
                self.panel.document.setText("新建或打开画布后，自动参考画布可见内容。")
            self._available()
            if changed:
                self._idle_status()
            if not self._active:
                return
            self._bind_canvas()
            gateway = self.docker._gateway
            if gateway and self._identity is not None and self._session(gateway) != self._identity:
                self.connection_changed()
                self.refresh_account()
                return
            if (not document or self._pressed or QApplication.mouseButtons() != Qt.NoButton
                    or QApplication.activeModalWidget() is not None):
                return
            subject, references = self.panel.source_ids()
            if not subject or not self.panel.live.isChecked():
                return
            signature = source_signature(self.docker._bridge, subject, references)
            if signature != self._observed:
                self._observed = signature
                self._mark_dirty()
            if (self._dirty and self._task is None
                    and time.monotonic() - self._last_edit >= float(self.panel.delay.currentData())):
                self.generate(automatic=True)
        except RealtimeBusy:
            self._last_edit = time.monotonic()
        except Exception as error:
            self._pause(str(error))

    def submit_once(self, checked=False):
        if self._closed or self._task is not None:
            return
        self.panel.live.setChecked(False)
        self._dirty = False
        self.generate()

    def generate(self, checked=False, *, automatic=False):
        if self._closed or self._task is not None:
            return
        original = variation_value(self.panel.settings()) == 0
        if not original and not self._authenticated:
            self._pause("请先登录 Mori 账号。")
            return
        try:
            if not original and not self.panel.settings()["prompt"]:
                raise ValueError("请先填写提示词。")
            gateway = None if original else self.docker._ensure_gateway()
            identity = None if original else self._session(gateway)
            if not original and identity != self._identity:
                raise ValueError("账号已改变，请刷新后重新生成。")
            subject, references = self.panel.source_ids()
            observed = source_signature(self.docker._bridge, subject, references)
            snapshots = capture_layers(self.docker._bridge, subject, [] if original else references)
            settings = self._settings()
        except RealtimeBusy:
            self._last_edit = time.monotonic()
            self.panel.set_status("正在绘制，停笔后再生成。", state="waiting")
            return
        except Exception as error:
            self._pause(str(error))
            return
        marker = self._task = object()
        cancel = self._cancel = threading.Event()
        document_id = self._document_id
        self._dirty = False
        self._observed = observed
        previous = self._previous if automatic else None
        self.panel.set_source_preview(snapshots[0].image)
        self.panel.set_busy(True)
        self.panel.set_pending(False)
        self.panel.set_status("正在读取画面与参考图层…", state="preparing")

        def notify(state, message):
            if not self._closed and is_alive(self):
                try:
                    self.progress.emit(marker, state, message)
                except RuntimeError:
                    pass

        def done(value, error):
            if marker is not self._task:
                return
            self._task = self._cancel = None
            self.panel.set_busy(False)
            self.panel.set_pending(False)
            if not original and (gateway is not self.docker._gateway or identity != self._session(gateway)):
                self.connection_changed()
                self._pause("账号已改变，旧账号的结果未导入。")
                return
            if error or not value or value.get("status") not in ("completed", "unchanged"):
                self._pause(error or (value or {}).get("error") or "生成未完成，请查看 Mori 创作记录。",
                            state=(value or {}).get("error_state", "uncertain" if (value or {}).get("status") == "uncertain" else "error"))
                self._quote_timer.start(150)
                return
            if document_id != self._document_id:
                self.panel.set_status("上一画布的任务已完成，可在 Mori 工作台的创作记录中查看。", state="completed")
                return
            self._previous = value.get("signature")
            if value["status"] == "unchanged":
                self.panel.set_status("输入内容没有变化，已跳过本次生成，未消耗积分。", state="unchanged")
                return
            try:
                self._result = value
                self.panel.set_result(value["bytes"], value["elapsed_seconds"])
                current = source_signature(self.docker._bridge, *self.panel.source_ids())
                fresh = current == observed and settings == self._settings()
                if self.panel.writeback.isChecked() and self._active and fresh and not self._pressed:
                    self.apply()
                else:
                    self.panel.set_status("已生成，可预览后放入画布。" if fresh else
                                          "已生成上一版；画布已有新修改，结果保留在预览中。", state="completed")
                if self.panel.live.isChecked() and not fresh:
                    self._mark_dirty()
            except RealtimeBusy:
                self.panel.set_status("已生成，画布正在绘制；可停笔后手动放入。", state="completed")
                if self.panel.live.isChecked():
                    self._mark_dirty()
            except Exception as failure:
                self._pause(str(failure))
            self._quote_version += 1
            self._quote_timer.start(150)
        self._work(lambda: run_realtime_task(gateway, snapshots, settings, expected_identity=identity,
                                            cancel=cancel, notify=notify, previous_signature=previous), done)

    def apply(self):
        if self._closed or self._result is None:
            return
        try:
            gateway = self.docker._gateway
            if not self._result.get("local_original") and (
                    gateway is None or tuple(self._result["identity"]) != self._session(gateway)):
                raise ValueError("账号已改变，请重新生成。")
            if self._result["target"].document_key != self._document_id:
                raise ValueError("请切回生成时的原画布。")
            import_result(self.docker._bridge, self._result)
            self.panel.apply.setEnabled(False)
            self.panel.set_status("已放入独立结果图层 · 原始主体与参考图层保留。", state="applied")
            self._result = None  # One click cannot accidentally duplicate an import.
        except Exception as error:
            self.panel.set_status(str(error), error=True)

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        if is_alive(self.panel):
            self.panel.set_busy(False)
            self.panel.set_pending(False)
        if self._cancel:
            self._cancel.set()
        for timer in (self._timer, self._quote_timer):
            if is_alive(timer):
                timer.stop()
        if is_alive(self._canvas):
            self._canvas.removeEventFilter(self)
        self._pool.shutdown(wait=False, cancel_futures=True)
