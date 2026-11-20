"""Real Qt dock + external WebView2 integration with a loopback-only fixture."""
import ctypes
import importlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time
from unittest import mock

from PyQt5.QtCore import QEventLoop, QTimer, Qt
from PyQt5.QtWidgets import QApplication, QMainWindow, QWidget

import test_selection_controller as fixture


def main(arguments):
    output = Path(arguments[0])
    output.mkdir(parents=True, exist_ok=True)
    module = importlib.import_module('mori_controller_fixture.docker')
    app = QApplication.instance()
    native = ctypes.windll.user32
    native.IsWindowVisible.argtypes = [ctypes.c_void_p]
    native.GetParent.argtypes = [ctypes.c_void_p]
    native.GetParent.restype = ctypes.c_void_p
    native.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
    native.FindWindowExW.restype = ctypes.c_void_p
    native.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    native.GetWindowLongW.restype = ctypes.c_long
    native.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            page = b"<!doctype html><html><body style='background:#222;color:white'><div id='root'><div class='mori-auth-connection'>Preparing</div></div><script>setTimeout(function(){document.getElementById('root').innerHTML='<main><h1>Mori integration fixture</h1><button>Ready</button></main>';},1200);</script></body></html>"
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            self.wfile.write(page)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    window = QMainWindow()
    window.setWindowTitle('Mori isolated Qt integration QA')
    window.resize(1000, 700)
    window.move(-2500, -2500)
    window.setCentralWidget(QWidget())
    settings = fixture.MemorySettings()
    with mock.patch.object(module, 'QSettings', lambda *_: settings):
        dock = module.MoriDocker()
    dock._started = True
    dock._update_timer.stop()
    window.addDockWidget(Qt.LeftDockWidgetArea, dock)
    dock.resize(660, 600)
    canvas = dock._web
    commands = []
    send = canvas._send
    def record_send(data):
        commands.append({'data': data, 'closed': canvas._closed,
                         'state': int(canvas._process.state()) if canvas._process else None})
        send(data)
        if canvas._process:
            commands[-1]['bytes_queued'] = canvas._process.bytesToWrite()
    canvas._send = record_send
    errors, samples = [], []
    canvas.error.connect(errors.append)
    loop = QEventLoop()
    phase = {'value': 'loading', 'changed': time.monotonic(), 'start': time.monotonic(),
             'captured': False, 'completed': False}

    def sample():
        handle = int(canvas._host.winId())
        child = native.FindWindowExW(handle, None, None, 'Mori Krita 画布')
        chain = []
        ancestor = handle
        while ancestor and len(chain) < 8:
            name = ctypes.create_unicode_buffer(256)
            native.GetClassNameW(ancestor, name, 256)
            chain.append({'handle': ancestor, 'class': name.value,
                          'visible': bool(native.IsWindowVisible(ancestor)),
                          'style': hex(native.GetWindowLongW(ancestor, -16) & 0xffffffff)})
            ancestor = native.GetParent(ancestor)
        samples.append({'phase': phase['value'], 'ready': canvas._was_ready,
                        'active': canvas._active, 'dock_exposed': dock._dock_exposed,
                        'loader': canvas._loading.isVisible(), 'logo': canvas._loading.logo.isVisible(),
                        'timer': canvas._loading.logo._timer.isActive(),
                        'qt_host': canvas._host.isVisible(), 'hwnd_host': bool(native.IsWindowVisible(handle)),
                        'external_found': bool(child), 'external_visible': bool(child and native.IsWindowVisible(child)),
                        'parent_native': canvas._host_parent.testAttribute(Qt.WA_NativeWindow),
                        'native_chain': chain,
                        'main_hwnd': int(window.winId()), 'host_window': int(canvas._host.window().winId()),
                        'main_visible': window.isVisible(), 'main_mapped': window.testAttribute(Qt.WA_Mapped),
                        'main_offscreen': window.testAttribute(Qt.WA_DontShowOnScreen),
                        'bytes_to_write': canvas._process.bytesToWrite() if canvas._process else None,
                        'size': [canvas.width(), canvas.height()]})
        now = time.monotonic()
        elapsed = now - phase['changed']
        state = phase['value']
        if state == 'loading' and not phase['captured'] and elapsed > 0.6:
            canvas.grab().save(str(output / 'loading.png'), 'PNG')
            phase['captured'] = True
        if now - phase['start'] > 30:
            errors.append('Timed out waiting for Qt/native browser presentation')
            loop.quit()
            return
        if state in ('loading', 'reloading', 'restarting') and canvas._was_ready:
            phase['value'] = {'loading': 'ready', 'reloading': 'reloaded', 'restarting': 'restarted'}[state]
        elif state in ('ready', 'returned', 'reloaded', 'restarted') and elapsed > 0.4:
            if not child or not native.IsWindowVisible(child):
                errors.append('Browser not visible at stage ' + state)
            if state == 'ready':
                dock._set_mode(0)
                phase['value'] = 'realtime'
            elif state == 'returned':
                if not canvas.reload():
                    errors.append('Reload was not accepted')
                phase['value'] = 'reloading'
            elif state == 'reloaded':
                canvas.start('http://127.0.0.1:%d/connect/synthetic' % server.server_port)
                phase['value'] = 'restarting'
            else:
                canvas.stop()
                phase['value'] = 'closing'
        elif state == 'realtime' and elapsed > 0.3:
            if child and native.IsWindowVisible(child):
                errors.append('Browser remained visible over realtime mode')
            dock._set_mode(1)
            phase['value'] = 'returned'
        elif state == 'closing' and canvas._process is None:
            phase['completed'] = True
            loop.quit()
        if phase['value'] != state:
            phase['changed'] = now

    with mock.patch.object(dock, '_start'), mock.patch.object(dock._realtime, 'set_active'), \
            mock.patch.object(dock._selection, 'warmup'):
        window.show()
        # The runner itself is launched with SW_HIDE. Consume its first-window
        # startup state, then map the isolated fixture outside the desktop.
        window.hide()
        window.show()
        dock._set_mode(1)
        canvas.start('http://127.0.0.1:%d/connect/synthetic' % server.server_port)
        dock._sync_page_visibility()
        timer = QTimer()
        timer.setInterval(50)
        timer.timeout.connect(sample)
        timer.start()
        loop.exec_()
        timer.stop()
        loading_samples = [row for row in samples if not row['ready']]
        if not phase['completed']:
            errors.append('Did not complete reload, reconnect and graceful pipe shutdown')
        if not loading_samples or not all(row['loader'] and row['logo'] for row in loading_samples if row['phase'] != 'closing'):
            errors.append('Loading surface was hidden inside the Qt dock')
    dock.shutdown()
    window.close()
    server.shutdown()
    server.server_close()
    report = {'passed': not errors, 'errors': errors, 'samples': samples,
              'commands': commands,
              'requests': requests, 'remote_requests': 0, 'model_calls': 0}
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
