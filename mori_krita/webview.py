"""Windows WebView2 child-process container; no Qt WebEngine dependency."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
from urllib.parse import urlsplit

from PyQt5.QtCore import QEvent, QProcess, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from .qt_lifetime import is_alive
from .realtime_panel import set_dark_surface


class WebCanvas(QWidget):
    """Embed the bundled WinForms WebView2 host in a native Qt child window."""

    ready = pyqtSignal()
    error = pyqtSignal(str)
    stopped = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        set_dark_surface(self)
        self.setStyleSheet("background:#1c1c20; border:0;")
        self._closed = False
        self._process = None
        self._profile = None
        self._buffer = b""
        self._config = None
        self._stopping = False
        self._was_ready = False
        # The native WebView2 host is parented to a Win32 child HWND.  Qt's
        # stacked pages do not reliably propagate hide/show to that external
        # child, so keep its intended page visibility explicitly.
        self._active = False
        self._pending_url = None
        self._status = QLabel("打开画布后，将在此显示 Mori 节点工作区。", self)
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setMargin(16)
        # Give the HWND a parent with no widget siblings: Qt also makes native
        # siblings when creating a child HWND, which would otherwise turn the
        # status label (and then the whole dock) into native windows.
        self._host_parent = QWidget(self)
        set_dark_surface(self._host_parent, "#101112")
        host_layout = QVBoxLayout(self._host_parent)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        self._host = QWidget(self._host_parent)
        set_dark_surface(self._host, "#101112")
        self._host.setStyleSheet("background:#101112; border:0; margin:0; padding:0;")
        # Do not promote the hidden stacked page, dock and Krita's other docks
        # to HWNDs. Native ancestors leave stale/unpainted strips after layout
        # restore on Windows. Create only this host, when the workbench opens.
        self._host.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self._host.setFocusPolicy(Qt.StrongFocus)
        self._host.installEventFilter(self)
        self._host.setMinimumSize(240, 220)
        self._host.hide()
        host_layout.addWidget(self._host)
        self._host_parent.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._status)
        layout.addWidget(self._host_parent, 1)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self._load_timeout)
        application = QApplication.instance()
        if application:
            application.aboutToQuit.connect(self._shutdown)

    def start(self, url: str):
        """Start one host for a URL returned by the plugin's local bridge."""
        if self._closed or not is_alive(self):
            return False
        try:
            parsed = urlsplit(url)
            valid_url = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                         and parsed.port and not parsed.username and not parsed.password)
        except (ValueError, TypeError):
            valid_url = False
        if not valid_url:
            self._fail("画布只能连接插件提供的本机桥接地址。")
            return False
        if sys.platform != "win32":
            self._fail("当前插件版本仅支持 Windows x64。")
            return False
        executable = Path(__file__).with_name("webview-host") / "MoriWebHost.exe"
        if not executable.is_file():
            self._fail("缺少 Mori 画布引擎，请重新安装完整的 Windows 插件包。")
            return False
        if is_alive(self._process) and self._process.state() != QProcess.NotRunning:
            self._pending_url = url
            self._stop(clear_pending=False)
            return True
        self._active = True
        self._stopping = False
        self._was_ready = False
        self._buffer = b""
        self._profile = tempfile.mkdtemp(prefix="mori-krita-webview-")
        self._host.setAttribute(Qt.WA_NativeWindow)
        self._host_parent.show()
        self._host.show()
        self._status.setText("正在启动 Mori 画布…")
        self._status.show()
        self._config = {"url": url, "parent": int(self._host.winId()), "profile": self._profile}
        process = QProcess(self)
        self._process = process
        process.setProgram(str(executable))
        process.setWorkingDirectory(str(executable.parent))
        process.setProcessChannelMode(QProcess.SeparateChannels)
        process.started.connect(self._started)
        process.readyReadStandardOutput.connect(self._read_output)
        process.readyReadStandardError.connect(lambda: process.readAllStandardError())
        process.errorOccurred.connect(self._process_error)
        process.finished.connect(self._finished)
        process.start()
        self._timeout.start(30000)
        return True

    def set_active(self, active: bool):
        """Keep the external WebView host behind Krita's current page.

        WebView2 is hosted by a separate WinForms process and reparented to a
        native Qt child HWND.  Hiding a QStackedWidget page alone can leave
        that child HWND visible over the next plugin page on Windows.  Mirror
        the page state in both Qt and the host process.
        """
        self._active = bool(active)
        if self._closed:
            return
        if is_alive(self._host_parent):
            self._host_parent.setVisible(self._active)
        if is_alive(self._host):
            self._host.setVisible(self._active)
        if is_alive(self._process) and self._process.state() == QProcess.Running:
            self._send({"op": "visibility", "visible": self._active})

    def _started(self):
        if self._closed:
            self._stop()
            return
        self._send(self._config)
        self._config = None
        # The page can be switched while the host process is starting.
        self._send({"op": "visibility", "visible": self._active})

    def _send(self, data):
        if self._closed and (not isinstance(data, dict) or data.get("op") != "close"):
            return
        if is_alive(self._process) and self._process.state() == QProcess.Running:
            self._process.write((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))

    def _read_output(self):
        if self._closed or not is_alive(self) or not is_alive(self._process):
            return
        self._buffer += bytes(self._process.readAllStandardOutput())
        if len(self._buffer) > 65536:
            self._buffer = b""
            self._fail("画布引擎返回了无效状态。")
            return
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                data = json.loads(line.decode("utf-8-sig"))
            except (ValueError, UnicodeError):
                continue
            if self._stopping:
                continue
            if data.get("type") == "ready":
                self._timeout.stop()
                self._was_ready = True
                self._status.hide()
                self.ready.emit()
            elif data.get("type") == "error":
                self._timeout.stop()
                self._fail(str(data.get("message", "画布引擎启动失败。")))
            elif data.get("type") == "notice":
                # Export/link errors belong in Krita's status line; they do
                # not replace a working canvas with an engine failure screen.
                self.error.emit(str(data.get("message", "操作未完成，请重试。")))

    def _fail(self, message):
        if self._closed or not is_alive(self):
            return
        self._status.setText(message)
        self._status.show()
        self.error.emit(message)

    def _process_error(self, error):
        if not self._closed and not self._stopping:
            if is_alive(self._timeout):
                self._timeout.stop()
            if error == QProcess.FailedToStart:
                self._fail("无法启动画布引擎，请确认插件包完整且系统允许运行 MoriWebHost。")
            elif error == QProcess.Crashed:
                self._fail("画布引擎已退出，请重新打开画布。")

    def _load_timeout(self):
        if not self._closed and not self._was_ready and not self._stopping:
            self._fail("画布启动超时，请检查本机桥接服务和 WebView2 Runtime。")
            self.stop()

    def _finished(self, exit_code=0, exit_status=QProcess.NormalExit):
        if is_alive(self._timeout):
            self._timeout.stop()
        process = self._process
        self._process = None
        if is_alive(process):
            process.deleteLater()
        profile, self._profile = self._profile, None
        self._cleanup_profile(profile)
        if not self._closed and not self._stopping and not self._was_ready and exit_code:
            self._fail("画布引擎启动失败，请确认 Microsoft Edge WebView2 Runtime 已安装。")
        elif not self._closed and not self._stopping and self._was_ready:
            self._fail("画布引擎已关闭，请重新打开画布。")
        if is_alive(self._host):
            self._host.hide()
        if is_alive(self._host_parent):
            self._host_parent.hide()
        pending, self._pending_url = self._pending_url, None
        if is_alive(self):
            self.stopped.emit()
        if pending and not self._closed and is_alive(self):
            QTimer.singleShot(0, lambda: self.start(pending))

    def showEvent(self, event):
        super().showEvent(event)
        self.set_active(True)

    def hideEvent(self, event):
        self.set_active(False)
        super().hideEvent(event)

    @staticmethod
    def _cleanup_profile(profile, attempt=0):
        if not profile:
            return
        # Only ever remove a directory created by this wrapper under the temp root.
        path = Path(profile).resolve()
        temp = Path(tempfile.gettempdir()).resolve()
        if path.parent != temp or not path.name.startswith("mori-krita-webview-"):
            return
        try:
            shutil.rmtree(str(path))
        except FileNotFoundError:
            pass
        except OSError:
            if attempt < 4 and QApplication.instance():
                QTimer.singleShot(1000, lambda: WebCanvas._cleanup_profile(str(path), attempt + 1))

    def stop(self):
        self._stop(clear_pending=True)

    def _stop(self, clear_pending=True):
        if clear_pending:
            self._pending_url = None
        self._stopping = True
        if is_alive(self._timeout):
            self._timeout.stop()
        process = self._process
        if is_alive(process) and process.state() != QProcess.NotRunning:
            self._send({"op": "close"})
            process.closeWriteChannel()
            QTimer.singleShot(2500, lambda: self._kill_if_current(process))

    def _kill_if_current(self, process):
        if self._process is process and is_alive(process):
            if process.state() != QProcess.NotRunning:
                process.kill()

    def _shutdown(self):
        self.shutdown()

    def shutdown(self):
        """Terminal, idempotent teardown; unlike stop(), never restart a host."""
        if self._closed:
            return
        self._closed = True
        self.stop()
        process = self._process
        if is_alive(process) and process.state() != QProcess.NotRunning:
            if not process.waitForFinished(1200) and is_alive(process):
                process.kill()
                if is_alive(process):
                    process.waitForFinished(500)
        profile, self._profile = self._profile, None
        self._cleanup_profile(profile)

    def eventFilter(self, watched, event):
        if watched is self._host and event.type() == QEvent.FocusIn:
            self._send({"op": "focus"})
        return super().eventFilter(watched, event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self._send({"op": "focus"})

    def closeEvent(self, event):
        self.stop()
        super().closeEvent(event)
