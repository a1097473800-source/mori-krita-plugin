"""A native composer attached to the active Krita selection.

Polling reads geometry only. Pixels leave Krita only after the user submits.
Network work runs in workers and keeps the original document/selection target.
"""
import concurrent.futures
import json
import threading
import time
from uuid import uuid4

from PyQt5.QtCore import QEvent, QObject, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFormLayout,
                            QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from .selection_composer import SelectionComposer
from .selection_geometry import (SelectionBusy, find_canvas_for_window,
                                 locate_selection, place_panel)
from .selection_tasks import run_selection_task, selection_aspect
from .qt_lifetime import is_alive
from .login_dialog import MoriLoginDialog


class SelectionController(QObject):
    progress = pyqtSignal(str)

    def __init__(self, docker):
        super().__init__(docker)
        self.docker = docker
        self._panel = None
        self._panel_token = None
        self.anchor = None
        self.enabled = docker._settings.value("selectionAssistant", True, type=bool)
        self._closed = False
        self._config_gateway = None
        self._config_user = None
        self._config_loading = False
        self._config_request = None
        self._config = {}
        self._task_id = None
        self._cancel = None
        self._task_gateway = None
        self._task_document = None
        self._task_key = None
        self._task_target = None
        self._task_mapping = None
        self._task_needs_mapping = False
        self.last_error = None
        self._conversations = {}
        self._drafts = {}
        self._document_key = None
        self._owner_ref = None
        self._quote_version = 0
        self._login_dialog = None
        self._restoring_session = False
        self._pointer_down = False
        self._tablet_down = False
        self._touch_down = False
        self._panel_pointer_down = False
        self._panel_tablet_down = False
        self._panel_touch_down = False
        self._panel_key_release = None
        self._settle_until = 0.0
        self._settle_retry_until = 0.0
        self._drag_paused = False
        self._drag_origin = None
        self._drag_distance = QApplication.startDragDistance()
        self._last_context = None
        self._last_bounds = None
        self._selection_key = None
        self._pending_progress = None
        self._pending_config = None
        self._pending_quote = None
        self._pending_completion = None
        self._quote_requested = False
        self._input_canvas = None
        self._input_targets = []
        self._panel_input_targets = []
        # Some Qt/PyQt builds (including Krita 5.2 on Windows) do not
        # expose NativeGesture. Wheel navigation still works on those builds.
        native_gesture = getattr(QEvent, "NativeGesture", None)
        self._viewport_events = frozenset(
            event for event in (QEvent.Wheel, native_gesture) if event is not None)
        self._interaction_events = frozenset((
            QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.MouseButtonRelease, QEvent.MouseMove,
            QEvent.TabletPress, QEvent.TabletMove, QEvent.TabletRelease,
            QEvent.TouchBegin, QEvent.TouchUpdate, QEvent.TouchEnd, QEvent.TouchCancel,
            QEvent.KeyRelease)) | self._viewport_events
        self._settle_timer = QTimer(self)
        self._settle_timer.setSingleShot(True)
        self._settle_timer.timeout.connect(self._settled)
        application = QApplication.instance()
        if application is not None:
            application.applicationStateChanged.connect(self._application_state_changed)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="Mori selection")
        self.progress.connect(self._progress)
        self._quote_timer = QTimer(self)
        self._quote_timer.setSingleShot(True)
        self._quote_timer.timeout.connect(self._quote)
        self._timer = QTimer(self)
        # Fallback for selection changes made by other scripts/actions. Pointer
        # releases are the normal trigger; this timer is stopped during strokes.
        self._timer.setInterval(900)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self._bind_input_scope()
        # QObject parents can disappear without an explicit plugin close. The
        # Python cleanup also works once this QObject's C++ half is gone.
        self.destroyed.connect(lambda _=None: self.shutdown())

    @property
    def panel(self):
        panel = self._panel
        if panel is not None and not is_alive(panel):
            self._panel_destroyed(self._panel_token)
        return self._panel

    @panel.setter
    def panel(self, panel):
        self._clear_panel_input_scope()
        self._panel = panel
        token = self._panel_token = object() if panel is not None else None
        if panel is not None:
            panel.destroyed.connect(lambda _=None, token=token: self._panel_destroyed(token))
            # Editors and menus can propagate an ignored key to the canvas.
            # Observe only this small subtree rather than every widget in Krita.
            self._panel_input_targets = [panel] + panel.findChildren(QWidget)
            for target in self._panel_input_targets:
                target.installEventFilter(self)

    def _clear_panel_input_scope(self):
        targets, self._panel_input_targets = getattr(self, "_panel_input_targets", []), []
        for target in targets:
            if is_alive(target) and is_alive(self):
                target.removeEventFilter(self)

    def _panel_destroyed(self, token):
        # destroyed() may run after the canvas has already deleted every child;
        # forget Python references without making another call into Qt.
        if token is not self._panel_token:
            return
        self._clear_panel_input_scope()
        self._panel = self._panel_token = None
        self.anchor = None
        self._last_context = self._last_bounds = None
        self._panel_pointer_down = self._panel_tablet_down = self._panel_touch_down = False
        self._restoring_session = False
        self._quote_version += 1
        self._config_gateway = None
        self._config_loading = False
        self._config_request = None
        self._pending_config = self._pending_quote = self._pending_completion = None
        self._quote_requested = False

    def _owner_window(self):
        # A floating QDockWidget.window() is the dock itself. Its QWidget parent
        # still identifies the Krita main window which owns its canvas.
        parent = self.docker.parentWidget()
        return parent.window() if parent is not None else self.docker.window()

    def _owner_active(self):
        """Reject another main window before querying any native selection API."""
        try:
            owner = self._owner_window()
            active = QApplication.activeWindow()
            return (active is None or active is owner or active.window() is owner
                    or active is self.docker.window())
        except RuntimeError:
            return False

    def _unbind_input_scope(self):
        targets, self._input_targets = self._input_targets, []
        self._input_canvas = None
        for target in targets:
            if is_alive(target) and is_alive(self):
                target.removeEventFilter(self)

    def _bind_input_scope(self):
        """Observe this canvas/window, never the application's Paint/MetaCall stream."""
        if self._closed or not self.enabled or self._pointer_down or self._tablet_down or self._touch_down:
            return
        try:
            owner = self._owner_window()
            canvas = find_canvas_for_window(owner)
            targets = []
            if canvas is not None:
                for target in (canvas, owner, owner.windowHandle()):
                    if is_alive(target) and target not in targets:
                        targets.append(target)
            if canvas is self._input_canvas and targets == self._input_targets:
                return
            self._unbind_input_scope()
            self._input_canvas = canvas
            self._input_targets = targets
            for target in targets:
                target.installEventFilter(self)
        except (RuntimeError, TypeError):
            self._unbind_input_scope()

    def _end_interaction(self):
        active = self._drag_paused or self._pointer_down or self._tablet_down or self._touch_down
        self._pointer_down = self._tablet_down = self._touch_down = False
        self._panel_pointer_down = self._panel_tablet_down = self._panel_touch_down = False
        if active:
            self._after_interaction()

    def _application_state_changed(self, state):
        if not self._closed and state != Qt.ApplicationActive:
            self._end_interaction()

    def _hide_panel(self):
        try:
            if self.panel is not None and not self.panel.isHidden():
                self.panel.hide()
        except RuntimeError:
            self.panel = None
            self._last_context = self._last_bounds = None

    def _begin_interaction(self, hide=True):
        if self._drag_paused:
            return
        self._drag_paused = True
        self._timer.stop()
        self._settle_timer.stop()
        self._settle_until = 0.0
        if hide:
            self._hide_panel()

    def _after_interaction(self):
        if self._pointer_down or self._tablet_down or self._touch_down:
            return
        self._drag_paused = False
        self._drag_origin = None
        self._timer.stop()
        now = time.monotonic()
        self._settle_until = now + 0.04
        self._settle_retry_until = now + 0.4
        self._settle_timer.start(40)

    def _settled(self):
        if self._closed or not self.enabled:
            return
        if self._pointer_down or self._tablet_down or self._touch_down:
            return
        self._settle_timer.stop()
        remaining = self._settle_until - time.monotonic()
        if remaining > 0:
            self._settle_timer.start(max(1, int(remaining * 1000) + 1))
            return
        self.refresh()
        if (not self._closed and self.enabled and not self._drag_paused
                and not self._settle_timer.isActive()):
            self._timer.start()

    def _prepare_panel(self, canvas):
        if self.panel is not None:
            return
        self.panel = SelectionComposer(canvas)
        self.panel.hide()
        self._last_context = None
        self._last_bounds = None
        self.panel.submitted.connect(self.submit)
        self.panel.cancelRequested.connect(self.cancel)
        self.panel.collapsedChanged.connect(self._collapsed)
        self.panel.settingsChanged.connect(self._settings_changed)
        try:
            self.panel.set_settings(json.loads(self.docker._settings.value("selectionSettings", "{}", type=str)))
        except (ValueError, TypeError):
            pass
        self.panel.ensurePolished()
        self.panel.layout().activate()

    def warmup(self):
        """Prepare the hidden editor while idle, without reading image data."""
        if self._closed or not self.enabled or self._interaction_busy() or not self._owner_active():
            return
        self._bind_input_scope()
        if is_alive(self._input_canvas):
            self._prepare_panel(self._input_canvas)

    def _panel_input_owner(self, watched, event):
        """Return receiver ownership (1) or a canvas-forwarded panel hit (2)."""
        panel = self.panel
        if panel is None or panel.isHidden():
            return 0
        parent = panel.parentWidget()
        if parent is None or not parent.isVisible():
            return 0
        def belongs(widget):
            # QMenu is a top-level window but retains its owning button as a
            # parent. isAncestorOf alone can stop at that popup boundary.
            while isinstance(widget, QWidget):
                if widget is panel:
                    return True
                widget = widget.parentWidget()
            return False

        if belongs(watched):
            return 1
        if isinstance(watched, QWidget) and watched.window() != panel.window():
            return 0
        if (getattr(watched, "isWindowType", lambda: False)()
                and watched != panel.window().windowHandle()):
            return 0
        popup = QApplication.activePopupWidget()
        own_popup = popup is not None and belongs(popup)
        if own_popup and (watched is popup or (isinstance(watched, QWidget) and popup.isAncestorOf(watched))):
            return 1
        position = getattr(event, "globalPos", lambda: None)()
        if position is None:
            points = getattr(event, "touchPoints", lambda: [])()
            if points:
                position = points[0].screenPos().toPoint()
        if position is not None:
            if panel.rect().contains(panel.mapFromGlobal(position)):
                return 2
            if own_popup and popup.rect().contains(popup.mapFromGlobal(position)):
                return 2
        return 0

    def _canvas_input(self, watched, event):
        """Limit global input observation to this active canvas."""
        canvas = self._input_canvas or (self.anchor.parent if self.anchor is not None else None)
        if not is_alive(canvas):
            canvas = None
        widget = watched
        while isinstance(widget, QWidget):
            if widget is canvas:
                return True
            if canvas is None and (widget.inherits("KisOpenGLCanvas2") or widget.inherits("KisQPainterCanvas")):
                return widget.window() == self._owner_window()
            widget = widget.parentWidget()
        if canvas is None:
            return False
        # Native QWindow/main-window input precedes QWidget dispatch. Only its
        # portion over the canvas belongs to drawing, not toolbars and docks.
        if isinstance(watched, QWidget):
            if watched.window() != canvas.window() or not watched.isAncestorOf(canvas):
                return False
        elif watched != canvas.window().windowHandle():
            return False
        position = getattr(event, "globalPos", lambda: None)()
        return position is not None and canvas.rect().contains(canvas.mapFromGlobal(position))

    def eventFilter(self, watched, event):
        """Pause once on press; do no Qt widget/timer work on held moves."""
        if self._closed or not self.enabled:
            return False
        kind = event.type()
        if kind in (QEvent.ApplicationDeactivate, QEvent.WindowDeactivate, QEvent.UngrabMouse):
            self._end_interaction()
            return False
        if kind == QEvent.TabletLeaveProximity:
            if self._tablet_down:
                self._tablet_down = False
                self._after_interaction()
            return False
        if kind not in self._interaction_events:
            return False
        key_release = None
        if kind == QEvent.KeyRelease:
            key_release = (getattr(event, "key", lambda: None)(),
                           getattr(event, "timestamp", lambda: None)())
            # A key release ignored by QPlainTextEdit can propagate to its
            # canvas ancestors. It remains the same input, not a canvas action.
            if self._panel_key_release == key_release:
                return False
        # These high-frequency paths must run before panel ancestry/focus tests.
        # In particular, do not restart a single-shot timer for every pen sample.
        if kind == QEvent.MouseMove:
            if self._panel_pointer_down:
                return False
            buttons = event.buttons()
            if self._pointer_down:
                if not buttons:  # Recover if a release went to a native popup.
                    self._pointer_down = False
                    self._after_interaction()
                elif self._drag_origin is not None:
                    position = getattr(event, "globalPos", lambda: None)()
                    if position is None or abs(position.x() - self._drag_origin[0]) + abs(position.y() - self._drag_origin[1]) >= self._drag_distance:
                        self._drag_origin = None
                        self._hide_panel()
                return False
            if not buttons or self._tablet_down or self._touch_down:
                return False
        elif kind == QEvent.TabletMove:
            if self._panel_tablet_down:
                return False
            if self._tablet_down or self._pointer_down or self._touch_down:
                return False
            if not getattr(event, "pressure", lambda: 0.0)():
                return False
        elif kind == QEvent.TouchUpdate:
            if self._panel_touch_down or self._touch_down:
                return False
        elif kind == QEvent.MouseButtonRelease and self._panel_pointer_down:
            self._panel_pointer_down = bool(event.buttons())
            return False
        elif kind == QEvent.TabletRelease and self._panel_tablet_down:
            self._panel_tablet_down = False
            return False
        elif kind in (QEvent.TouchEnd, QEvent.TouchCancel) and self._panel_touch_down:
            self._panel_touch_down = False
            return False
        elif kind == QEvent.MouseButtonRelease and self._pointer_down:
            self._pointer_down = bool(event.buttons())
            self._after_interaction()
            return False
        elif kind == QEvent.TabletRelease and self._tablet_down:
            self._tablet_down = False
            self._after_interaction()
            return False
        elif kind in (QEvent.TouchEnd, QEvent.TouchCancel) and self._touch_down:
            self._touch_down = False
            self._after_interaction()
            return False
        # The selection card owns its editor, menus and the whole padded card.
        # Native canvas receivers can also see propagated/forwarded mouse input;
        # use global coordinates for that path instead of treating it as a drag.
        try:
            owner = self._panel_input_owner(watched, event)
            if owner:
                if kind == QEvent.KeyRelease:
                    self._panel_key_release = key_release
                    QTimer.singleShot(0, lambda: setattr(self, "_panel_key_release", None))
                if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                    self._panel_pointer_down = bool(event.buttons())
                elif kind == QEvent.TabletPress:
                    self._panel_tablet_down = True
                elif kind == QEvent.TouchBegin:
                    self._panel_touch_down = True
                # Consume only a QWidget canvas receiver. A QWindow event must
                # continue so Qt can deliver its original event to the editor.
                return bool(owner == 2 and isinstance(watched, QWidget))
        except (TypeError, RuntimeError):
            pass
        if not self._canvas_input(watched, event):
            return False
        if kind in (QEvent.TabletPress, QEvent.TabletMove):
            self._tablet_down = True
        elif kind in (QEvent.TouchBegin, QEvent.TouchUpdate):
            self._touch_down = True
        elif kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick, QEvent.MouseMove):
            # Qt may synthesize mouse events from tablet/touch input. Its own
            # release is not needed to end the real tablet/touch interaction.
            if self._tablet_down or self._touch_down:
                return False
            self._pointer_down = bool(event.buttons())
            if kind in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick) and not self._drag_paused:
                position = getattr(event, "globalPos", lambda: None)()
                self._drag_origin = (position.x(), position.y()) if position is not None else None
        else:
            if kind in self._viewport_events:
                self._begin_interaction()
            self._after_interaction()
            return False
        self._begin_interaction(hide=self._drag_origin is None)
        return False

    def _interaction_busy(self):
        if self._pointer_down or self._tablet_down or self._touch_down:
            return True
        if self._panel_pointer_down or self._panel_tablet_down or self._panel_touch_down:
            return time.monotonic() < self._settle_until
        if QApplication.mouseButtons() == Qt.NoButton:
            return time.monotonic() < self._settle_until
        try:
            panel_input = self.panel is not None and self.panel.is_interacting()
        except RuntimeError:
            self.panel = None
            self._last_context = self._last_bounds = None
            panel_input = False
        if not panel_input:
            # An unobserved/native press may be in a dock or another window.
            # Skip this read while held, without hiding or rearming the panel.
            return True
        return time.monotonic() < self._settle_until

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self._pointer_down = self._tablet_down = self._touch_down = False
            self._panel_pointer_down = self._panel_tablet_down = self._panel_touch_down = False
            self._drag_paused = False
            self._drag_origin = None
            self._settle_until = 0.0
            self._settle_timer.stop()
            self._timer.stop()
            self._unbind_input_scope()
        elif not self._timer.isActive():
            self._timer.start()
        self.docker._settings.setValue("selectionAssistant", self.enabled)
        self.refresh()

    def _post(self, callback):
        if self._closed or not is_alive(self) or not is_alive(self.docker):
            return
        dispatch = self.docker._dispatch
        if not is_alive(dispatch):
            return
        try:
            dispatch.requested.emit({"operation": "callback", "data": {"callback": callback},
                                      "event": threading.Event()})
        except RuntimeError:
            # A worker can finish between the lifetime check and signal emit.
            if is_alive(self) and is_alive(self.docker) and is_alive(dispatch):
                raise

    def _work(self, function, callback):
        future = self._pool.submit(function)

        def finished(result):
            try:
                value, error = result.result(), None
            except Exception as failure:
                value, error = None, str(failure)
            self._post(lambda: callback(value, error) if not self._closed else None)
        future.add_done_callback(finished)

    def refresh(self):
        if self._closed:
            return
        if not self.enabled:
            self._hide_panel()
            return
        # selectedExactRect() competes with Krita's selection/paint updates.
        # Keep every API call, widget scan and service initialization out of an
        # active drag/stroke, and wait briefly after release before querying it.
        if self._interaction_busy():
            return
        if not self._owner_active():
            return
        self._bind_input_scope()
        try:
            anchor = locate_selection()
            if anchor is None or not anchor.parent.isVisible():
                self._selection_key = None
                self.anchor = None
                self._hide_panel()
                return
            # Each window owns its own dock and composer.
            owner = self.docker.parentWidget()
            if owner is not None and anchor.parent.window() != owner.window():
                self._hide_panel()
                return
            if owner is not None and owner is not self._owner_ref:
                self._owner_ref = owner
                owner.destroyed.connect(getattr(self.docker, "shutdown", self.shutdown))
            self.anchor = anchor
            if self._task_id and self._task_document is not None and anchor.document != self._task_document:
                self._hide_panel()
                return
            self._apply_pending_ui()
            if self.panel is None:
                self._prepare_panel(anchor.parent)
            elif self.panel.parentWidget() is not anchor.parent:
                self.panel.setParent(anchor.parent)
                self._last_bounds = None
            if anchor.document_key != self._document_key and not self._task_id:
                if self._document_key:
                    self._drafts[self._document_key] = self.panel.message()
                self._document_key = anchor.document_key
                self.panel.set_message(self._drafts.get(self._document_key, ""))
            rect = anchor.selection_rect
            selection_key = (anchor.document_key, rect.x(), rect.y(), rect.width(), rect.height())
            if selection_key != self._selection_key:
                self._selection_key = selection_key
                # Collapse is temporary for this selection, never a global
                # preference that conceals the next region's editing controls.
                self.panel.set_collapsed(False)
            if self._pending_progress is not None:
                self.panel.set_status(self._pending_progress)
                self._pending_progress = None
            context = "当前选区 · %d × %d" % (rect.width(), rect.height())
            if context != self._last_context:
                self.panel.set_context(context)
                self._last_context = context
            size = self.panel.desired_size(anchor.parent.width())
            bounds = place_panel(anchor.rect, anchor.parent.width(), anchor.parent.height(),
                                 size.width(), size.height(), toolbar_margin=0,
                                 obstacles=[anchor.toolbar_rect] if anchor.toolbar_rect is not None else [])
            if bounds[2] < 240 or bounds[3] < 40:
                self._hide_panel()
                return
            moved = bounds != self._last_bounds
            if moved:
                self.panel.setGeometry(*bounds)
                self._last_bounds = bounds
            showing = self.panel.isHidden()
            if showing:
                self.panel.reveal()
            if moved or showing:
                self.panel.raise_()
            # Hovering/drawing must not construct SSLContext or start services.
            # User actions (submit/workbench) initialize the shared gateway.
            gateway = getattr(self.docker, "_gateway", None)
            if gateway is not None and (gateway is not self._config_gateway or gateway.client.user_id != self._config_user) and not self._config_loading:
                self._load_config(gateway)
        except SelectionBusy:
            # Retry shortly after release instead of adding a whole 900 ms idle
            # interval. Never wait on Krita; a new press cancels this retry.
            if time.monotonic() < self._settle_retry_until:
                self._settle_timer.start(32)
            return
        except Exception as error:
            # Native windows can disappear between an active-view query and a
            # repaint. Wait for the next tick rather than interrupting painting.
            self.anchor = None
            self.last_error = str(error)
            if self.panel:
                try:
                    self.panel.hide()
                except RuntimeError:
                    self.panel = None

    def _collapsed(self, collapsed):
        QTimer.singleShot(0, self.refresh)

    def _settings_changed(self, settings):
        self.docker._settings.setValue("selectionSettings", json.dumps(settings, ensure_ascii=False))
        self._quote_version += 1
        self._quote_timer.start(450)
        QTimer.singleShot(0, self.refresh)

    @staticmethod
    def _session_token(gateway):
        return (gateway.client.user_id, getattr(gateway.client, "session_version", None),
                getattr(gateway, "epoch", None))

    def _defer_ui(self, name, valid, apply):
        if self._closed:
            return
        if self._interaction_busy() or not self._owner_active():
            setattr(self, name, (valid, apply))
        elif valid():
            apply()

    def _apply_pending_ui(self):
        # Called only after the idle guard and a successful native selection
        # lookup. Responses may arrive during a stroke; applying only the latest
        # one here avoids layout invalidation and mask changes while dragging.
        for name in ("_pending_config", "_pending_quote", "_pending_completion"):
            pending = getattr(self, name)
            setattr(self, name, None)
            if pending is not None and pending[0]():
                pending[1]()
        if self._quote_requested:
            self._quote_requested = False
            self._quote_timer.stop()
            self._quote()

    def _load_config(self, gateway):
        if self._interaction_busy():
            return
        self._config_gateway = gateway
        requested_user = gateway.client.user_id
        self._config_user = requested_user
        self._config_loading = True
        request = self._config_request = object()
        panel = self.panel
        response = {"identity": self._session_token(gateway)}
        self._quote_version += 1
        if panel:
            panel.set_quote("")

        def load():
            session = gateway.client.session()
            response["identity"] = self._session_token(gateway)
            if session.get("enabled", True) and not session.get("user"):
                return None
            with gateway.session_lock:
                client = gateway._channel()
            return client.config()

        def loaded(config, error):
            if request is not self._config_request:
                return
            def valid():
                current = (request is self._config_request and gateway is self.docker._gateway
                           and response["identity"] == self._session_token(gateway)
                           and self.panel is panel)
                if request is self._config_request:
                    self._config_loading = False
                return current

            def apply():
                if config:
                    self._config_user = gateway.client.user_id
                    self._config = config
                    panel.set_models(config)
                    if not self._task_id:
                        panel.set_status("")
                    self._quote_timer.start(100)
                elif not self._task_id:
                    panel.set_status(error or "输入修改要求；发送时使用 Mori 账号登录。", error=bool(error))
            self._defer_ui("_pending_config", valid, apply)
        self._work(load, loaded)

    def _quote(self):
        if self._closed:
            return
        if self._interaction_busy():
            self._quote_requested = True
            return
        if self._closed or not self.panel or self.anchor is None or self._task_id:
            return
        gateway = self.docker._gateway
        if not gateway or (gateway.client.auth_enabled and not gateway.client.user_id):
            self.panel.set_quote("")
            return
        settings = self.panel.settings()
        rect = self.anchor.selection_rect
        model = next((item for item in self._config.get("models", []) if item["id"] == settings["model"]), {})
        if settings.get("aspect") == "auto":
            settings["aspect"] = selection_aspect(rect.width(), rect.height(), model.get("aspects"))
        settings.pop("mode", None)
        token = self._quote_version
        identity = self._session_token(gateway)
        panel = self.panel

        def quote():
            with gateway.session_lock:
                client = gateway._channel()
            return client.quote(settings)

        def received(value, error):
            def valid():
                return (token == self._quote_version and gateway is self.docker._gateway
                        and identity == self._session_token(gateway) and self.panel is panel)

            def apply():
                text = ""
                if value and value.get("enforcement"):
                    amount = value.get("totalPoints")
                    available = value.get("available")
                    if amount is not None and available is not None:
                        text = "本次预计 %g 积分 · 可用 %g 积分" % (amount, available)
                        if available < amount:
                            text += " · 余额不足"
                elif value:
                    text = "积分扣费尚未启用"
                panel.set_quote(text)
            self._defer_ui("_pending_quote", valid, apply)
            if not self._interaction_busy():
                self.refresh()
        self._work(quote, received)

    def _login(self, gateway):
        if self._login_dialog is not None:
            return False
        parent = self.anchor.parent.window() if self.anchor is not None else self.docker.window()
        dialog = MoriLoginDialog(parent, gateway.client.base_url)
        self._login_dialog = dialog
        email, password = dialog.email, dialog.password
        login = dialog.submit

        def submit_login():
            address, secret = email.text().strip(), password.text()
            if not address or not secret:
                dialog.set_status("请输入邮箱和密码。", error=True)
                return
            if not login.isEnabled():
                return
            dialog.set_busy(True)
            dialog.set_status("正在连接 Mori…")

            def logged(value, error):
                try:
                    password.clear()
                    if self._login_dialog is not dialog or not dialog.isVisible():
                        return
                    if error:
                        dialog.set_status(error, error=True)
                        dialog.set_busy(False)
                    else:
                        gateway._sync_identity()
                        dialog.accept()
                except RuntimeError:
                    pass
            def sign_in():
                gateway.client.session()  # Resolve a broken proxy before the POST.
                return gateway.client.login(address, secret)
            self._work(sign_in, logged)
        login.clicked.connect(submit_login)
        password.returnPressed.connect(submit_login)
        browser_attempt = [None]
        browser_timer = QTimer(dialog)
        browser_timer.setInterval(500)

        def finish_browser():
            if not browser_attempt[0]:
                return
            value = gateway.browser_login().status(browser_attempt[0])
            dialog.set_status(value.get("message", "请在浏览器中确认登录。"), error=value["status"] == "error")
            if value["status"] == "complete":
                browser_timer.stop()
                gateway._sync_identity()
                dialog.accept()
            elif value["status"] in ("error", "cancelled"):
                browser_timer.stop()
                browser_attempt[0] = None
                dialog.set_busy(False)
                dialog.browser_submit.setText("通过浏览器登录（支持 Google）")

        browser_timer.timeout.connect(finish_browser)

        def browser_login():
            manager = gateway.browser_login()
            if browser_attempt[0]:
                manager.cancel(browser_attempt[0])
                finish_browser()
                return
            try:
                value = manager.start()
                browser_attempt[0] = value["attemptId"]
                dialog.set_busy(True)
                dialog.browser_submit.setEnabled(True)
                dialog.browser_submit.setText("取消浏览器登录")
                dialog.set_status(value["message"])
                browser_timer.start()
            except Exception as error:
                dialog.set_status(str(error), error=True)

        dialog.browser_submit.clicked.connect(browser_login)
        accepted = dialog.exec_() == QDialog.Accepted
        browser_timer.stop()
        if browser_attempt[0] and gateway._browser_login is not None:
            gateway._browser_login.cancel(browser_attempt[0])
        password.clear()
        self._login_dialog = None
        dialog.deleteLater()
        if accepted:
            self._config_gateway = None
            self._conversations.clear()
            if self.docker._web_started:
                self.docker._web.start(gateway.url)
        return accepted

    def _restore_login(self, gateway, payload):
        """Validate a saved cookie in a worker before offering sign-in."""
        marker = object()
        self._restoring_session = marker
        panel = self.panel
        expected_document = self._document_key
        expected_rect = self.anchor.selection_rect if self.anchor is not None else None
        panel.set_busy(True, "正在恢复登录…")

        def validate():
            try:
                gateway.client.session()
            except Exception as error:
                # An expired cookie is an ordinary sign-in, while a network
                # failure retains the saved session for another attempt.
                if getattr(error, "status", 0) != 401:
                    raise
            return gateway.client.session_version

        def restored(version, error):
            if self._restoring_session is not marker:
                return
            self._restoring_session = False
            if self.panel is panel:
                panel.set_busy(False)
            if self._closed or self.docker._gateway is not gateway or self.panel is not panel:
                return
            if error:
                panel.set_status("恢复登录未完成：" + str(error), error=True)
                return
            if gateway.client.session_version != version:
                panel.set_status("账号状态已改变，请重新发送。", error=True)
                return
            if gateway.client.session_restored:
                panel.set_status("暂时无法确认登录状态，请稍后重试。", error=True)
                return
            # Authentication must not turn a click on document A into a paid
            # edit on a selection made while the server was being contacted.
            try:
                anchor = None if self._interaction_busy() else locate_selection()
            except SelectionBusy:
                anchor = None
            if (anchor is None or anchor.document_key != expected_document
                    or anchor.selection_rect != expected_rect):
                panel.set_status("登录验证已完成，请确认当前选区后再发送。")
                return
            self.submit(payload)

        self._work(validate, restored)

    def submit(self, payload):
        if self._closed or self._task_id or self._restoring_session or self.panel is None:
            return
        gateway = self.docker._ensure_gateway()
        if not gateway.client.user_id and getattr(gateway.client, "session_restored", False):
            self._restore_login(gateway, dict(payload))
            return
        if gateway.client.auth_enabled and not gateway.client.user_id:
            if not self._login(gateway):
                return
        try:
            anchor = locate_selection()
            if anchor is None:
                raise RuntimeError("请先在 Krita 中建立选区。")
            owner = self.docker.parentWidget()
            if owner is not None and anchor.parent.window() != owner.window():
                raise RuntimeError("当前选区属于另一个 Krita 窗口，请在对应窗口发送。")
            if self._document_key is not None and anchor.document_key != self._document_key:
                self.refresh()
                raise RuntimeError("当前文档已切换，请确认选区后再发送。")
            snapshot = self.docker._bridge.snapshot("selection", context_padding=True)
            gateway._sync_identity()
            model = next((item for item in self._config.get("models", []) if item["id"] == payload.get("model")), {})
            payload = dict(payload, available_aspects=model.get("aspects"),
                           expected_epoch=gateway.epoch, expected_user_id=gateway.client.user_id,
                           reference_mode=self.docker._settings.value("referenceMode", "original", type=str))
        except Exception as error:
            self.panel.set_status(str(error), error=True)
            return
        task_id = uuid4().hex
        self._task_id = task_id
        self._cancel = threading.Event()
        self._task_gateway = gateway
        self._task_document = anchor.document
        target = snapshot.target
        self._task_target = target
        self._task_mapping = None
        self._task_needs_mapping = getattr(snapshot, "context_rect", None) is not None
        self._task_key = (gateway.epoch, target.document_key, target.x, target.y, target.width, target.height)
        task_identity = self._session_token(gateway)
        conversation = self._conversations.get(self._task_key)
        self.panel.set_busy(True, "正在准备当前选区…")
        self.refresh()

        def run():
            return run_selection_task(gateway, snapshot, payload, notify=self.progress.emit,
                                      cancel_event=self._cancel, task_id=task_id, conversation_id=conversation)

        def finished(summary, error):
            if self._task_id != task_id:
                return
            if summary and summary.get("conversation_id"):
                self._conversations[self._task_key] = summary["conversation_id"]
            self._task_id = None
            self._task_document = None
            self._task_target = None
            self._task_mapping = None
            self._task_needs_mapping = False
            self._cancel = None
            self._pending_progress = None
            panel = self.panel
            identity = task_identity

            def valid():
                return panel is not None and self.panel is panel and self._task_id is None

            def apply():
                if gateway is not self.docker._gateway or identity != self._session_token(gateway):
                    # Release the old busy UI without showing another account's
                    # response or changing a newer task's controls.
                    panel.set_busy(False, "账号状态已改变，请确认选区后重新发送。")
                    return
                text = error or (summary or {}).get("success_message", "本轮操作未完成。")
                timings = (summary or {}).get("timings_ms") or {}
                total = timings.get("total_ms", 0)
                if isinstance(total, (int, float)) and 0 < total < 86_400_000:
                    text += " · 共 %.1f 秒" % (total / 1000)
                panel.set_busy(False, text)
                panel.set_status(text, error=bool(error or (summary or {}).get("status") in ("failed", "uncertain", "timeout")))
                timing_lines = []
                for key, label in (("read_ms", "读取选区"), ("session_ms", "准备会话"),
                                   ("prepare_ms", "处理参考图"), ("reference_upload_ms", "上传参考图"),
                                   ("mask_upload_ms", "上传蒙版"), ("agent_setup_ms", "准备智能体"),
                                   ("submit_ms", "提交请求"), ("wait_ms", "服务器/智能体等待"),
                                   ("download_ms", "下载结果"), ("import_ms", "写入图层")):
                    value = timings.get(key)
                    if isinstance(value, (int, float)) and 0 <= value < 86_400_000:
                        timing_lines.append("%s：%.2f 秒" % (label, value / 1000))
                panel.set_timing_details("\n".join(timing_lines))
                if summary and summary.get("message"):
                    panel.setToolTip(summary["message"])
                self._quote_timer.start(300)
            self._defer_ui("_pending_completion", valid, apply)
            self.refresh()
        self._work(run, finished)

    def _result_target(self, data):
        if self._closed or data.get("task_id") != self._task_id or self._task_gateway is not self.docker._gateway:
            raise RuntimeError("选区任务已失效，结果可在 Mori 创作记录查看。")
        gateway = self._task_gateway
        gateway._sync_identity()
        if data.get("epoch") != gateway.epoch or self._task_key is None or data.get("epoch") != self._task_key[0]:
            raise RuntimeError("账号会话已改变，已停止导入此轮结果。")
        original = self._task_target
        supplied = data.get("target")
        fields = ("document_key", "x", "y", "width", "height")
        if original is None or supplied is None or any(getattr(original, name) != getattr(supplied, name) for name in fields):
            raise RuntimeError("结果与提交时的原选区不匹配，已停止自动导入。")
        return original

    def register_mapping(self, data):
        """Bind worker geometry once, before the task submits a paid request."""
        original = self._result_target(data)
        mapping = data.get("selection_mapping")
        if mapping is None:
            raise RuntimeError("选区参考图缺少坐标映射，已停止生成。")
        mapping.assert_target(original)
        if self._task_mapping is not None and self._task_mapping is not mapping:
            raise RuntimeError("选区任务的参考图映射已改变，已停止生成。")
        self._task_mapping = mapping

    def deliver(self, data):
        original = self._result_target(data)
        mapping = data.get("selection_mapping")
        if (self._task_needs_mapping or self._task_mapping is not None or mapping is not None) and (
                self._task_mapping is None or mapping is not self._task_mapping):
            raise RuntimeError("结果与提交时的参考图映射不匹配，已停止自动导入。")
        asset = data["asset"]
        options = {"selection_mapping": mapping} if mapping is not None else {}
        node = self.docker._bridge.import_png(data["bytes"], "Mori · " + asset.get("name", "选区修改"), original, **options)
        return {"layer_id": node.uniqueId().toString(), "name": node.name()}

    def _progress(self, message):
        if not self._closed and self.panel and self._task_id:
            if self._interaction_busy() or not self._owner_active():
                self._pending_progress = message
                return
            self.panel.set_status(message)
            self.refresh()

    def cancel(self):
        if self._cancel:
            self._cancel.set()
            if self.panel:
                self.panel.set_status("正在停止，已完成的结果会保留…")

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        try:
            for timer in (self._timer, self._quote_timer, self._settle_timer):
                if is_alive(timer):
                    timer.stop()
            self._unbind_input_scope()
            application = QApplication.instance()
            if is_alive(application):
                try:
                    application.applicationStateChanged.disconnect(self._application_state_changed)
                except (RuntimeError, TypeError):
                    pass
            if self._cancel:
                self._cancel.set()
            panel = self.panel
            self.panel = None
            if panel is not None:
                panel.hide()
        finally:
            self.anchor = None
            self._pending_config = self._pending_quote = self._pending_completion = None
            self._quote_requested = False
            self._restoring_session = False
            self._pool.shutdown(wait=False, cancel_futures=True)
